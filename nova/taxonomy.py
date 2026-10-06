"""The second brain's filing rules — enforced on every note Nova creates, indexes or moves.

1. Root: exactly two folders, `01_Personal/` and `02_Work/`. Nothing else lives at the top.
2. Domains: Personal = life admin, health, hobbies, travel, own finances. Work = role, clients & partners, projects &
   strategy, reference material.
3. Structure: numeric prefixes on the first two levels (`01_`, `02_`…), never more than three folders deep
   (domain / category / one sub-folder), names in Pascal_Snake_Case with clear specifiers.
4. Integrity: everything is classified the moment it's created; things that blur personal and work go where they are
   mainly used.

`place()` decides where a new note goes, `audit()` lists what breaks the rules, `enforce()` fixes it (after a backup).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path

from . import context
from .config import resolve

PERSONAL, WORK = "01_Personal", "02_Work"
MAX_DEPTH = 3                                    # folders below the vault: domain / category / sub-folder

# domain → category → {"about": for the classifier, "subs": allowed fixed sub-folders, "words": keyword fallback}
TREE: dict[str, dict[str, dict]] = {
    PERSONAL: {
        "01_Life_Admin": {"about": "IDs, passports, licences, vehicle papers and maintenance, property, insurance, "
                                   "household admin, family paperwork",
                          "subs": [], "words": ["passport", "id document", "licence", "license", "vehicle", "car service",
                                                "mahindra", "xuv", "insurance", "property", "deed", "municipal",
                                                "rates", "household", "warranty", "school", "family"]},
        "02_Health_&_Fitness": {"about": "medical records, doctors, medication, workouts, nutrition, sleep, wellbeing",
                                "subs": [], "words": ["medical", "doctor", "medication", "prescription", "workout", "gym",
                                                      "nutrition", "diet", "sleep", "therapy", "health", "fitness",
                                                      "clinic", "blood"]},
        "03_Interests_&_Projects": {"about": "hobbies, personal travel, personal side projects and ideas, learning for fun",
                                    "subs": ["Hobbies", "Travel"],
                                    "words": ["hobby", "fishing", "woodwork", "travel", "holiday", "itinerary", "trip",
                                              "flight", "camping", "braai", "recipe", "game", "movie", "youtube channel",
                                              "side project", "nova", "hookpoint", "tackletrail"]},
        "04_Finance_&_Budgets": {"about": "personal bank statements, tax returns, budgets, personal investments, bills",
                                 "subs": [], "words": ["bank statement", "tax return", "sars", "budget", "salary slip",
                                                       "payslip", "investment", "retirement", "medical aid bill",
                                                       "personal loan", "bond repayment"]},
        "05_Journal": {"about": "daily journal, reflections, Nova's nightly dreams and memory export",
                       "subs": ["Dreams"], "words": ["journal", "diary", "reflection", "dream"]},
    },
    WORK: {
        "01_Role_&_Responsibilities": {"about": "the job itself: position, KPIs and targets, procedures, workflows, HR",
                                       "subs": ["Standard_Operating_Procedures"],
                                       "words": ["kpi", "target", "job description", "sop", "procedure", "workflow",
                                                 "performance review", "responsibilit", "onboarding", "policy"]},
        "02_Clients_&_Partners": {"about": "customers, resellers, partners, vendors and distributors: accounts, contacts, "
                                           "quotes, pricing sent, correspondence",
                                  "subs": ["Active_Accounts", "Partner_Network"],
                                  "words": ["client", "customer", "partner", "reseller", "vendor", "account", "quote",
                                            "pricing", "purchase order", "deal registration", "northwind", "juniper", "avaya",
                                            "nokia", "sonar", "westcon", "distributor"]},
        "03_Projects_&_Strategy": {"about": "active work projects (one sub-folder per project), plans, goals, forecasts, "
                                            "competitor and market research",
                                   "subs": ["Strategic_Growth_Plans"],
                                   "words": ["project", "strategy", "plan", "roadmap", "forecast", "pipeline", "goal",
                                             "competitor", "market analysis", "launch", "growth", "budget fy"]},
        "04_Resources_&_Reference": {"about": "technical documentation, product specs, datasheets, company policies, "
                                              "industry reports, training material, articles saved for work",
                                     "subs": ["Technical_Documentation"],
                                     "words": ["datasheet", "spec", "documentation", "manual", "api", "schema", "report",
                                               "whitepaper", "training", "article", "reference", "how to", "guide"]},
        "05_Meetings_&_Reports": {"about": "meeting notes and recordings, Nova's weekly digests and mission reports",
                                  "subs": ["Meetings", "Digests", "Missions"],
                                  "words": ["meeting", "minutes", "qbr", "call notes", "digest", "mission report"]},
    },
}
# Nova's own fixed destinations (what used to be top-level folders)
SYSTEM = {
    "journal": (PERSONAL, "05_Journal", ""), "dreams": (PERSONAL, "05_Journal", "Dreams"),
    "memory": (PERSONAL, "05_Journal", ""),
    "meetings": (WORK, "05_Meetings_&_Reports", "Meetings"), "digests": (WORK, "05_Meetings_&_Reports", "Digests"),
    "missions": (WORK, "05_Meetings_&_Reports", "Missions"),
}
OLD_FOLDERS = {"journal": "journal", "dreams": "dreams", "meetings": "meetings", "digests": "digests",
               "missions": "missions"}
ATTACHMENTS = "Attachments"
ASK_BELOW = 0.7                                  # less sure than this = ask the user
PREFIX = re.compile(r"^\d{2}_")
SMALL = {"and", "or", "of", "the", "a", "an", "to", "for", "in", "on", "at", "by", "with", "vs"}

CLASSIFY_PROMPT = """File this note into {owner}'s second brain. Reply with JSON only:
{{"domain": "personal" or "work", "category": "<one category name exactly as listed>", "subfolder": "<one of that
category's sub-folders, a short project or account name, or empty>", "confidence": 0.0-1.0,
"reason": "<a few words: why it belongs there>"}}
Use a confidence below 0.7 when it could reasonably belong in another category or domain.

PERSONAL categories:
{personal}
WORK categories:
{work}

Rules: personal = life admin, health, hobbies, travel, own money. Work = the job, clients/partners, work projects,
strategy, reference. If it blurs both, choose where it is mainly USED. For a work project use category
03_Projects_&_Strategy with the project's name as sub-folder; for a specific client or partner use
02_Clients_&_Partners with sub-folder Active_Accounts or Partner_Network.

Title: {title}
Content:
{text}"""


# ── labels: every note states its domain and status at the very top ──
STATUSES = ["IN-PROGRESS", "WAITING-ON-USER", "COMPLETED", "BACKLOG"]
TRACK_TO_STATUS = {"doing": "IN-PROGRESS", "waiting": "WAITING-ON-USER", "done": "COMPLETED", "todo": "BACKLOG"}
STATUS_TO_TRACK = {v: k for k, v in TRACK_TO_STATUS.items()}
LABEL_RE = re.compile(r"^\[(?:LABEL: )?(DOMAIN|STATUS): ([A-Z\-]+)\]\s*$")      # "[STATUS: …]" is read too


def domain_label(domain: str) -> str:
    return "PERSONAL" if domain == PERSONAL else "WORK"


def header(domain: str, status: str) -> str:
    """The two lines every note starts with."""
    status = status if status in STATUSES else "COMPLETED"
    return f"[LABEL: DOMAIN: {domain_label(domain)}]\n[LABEL: STATUS: {status}]"


def read_labels(text: str) -> tuple[dict, str]:
    """({"DOMAIN": …, "STATUS": …}, the note without its label lines)."""
    lines = text.split("\n")
    found, i = {}, 0
    while i < len(lines) and i < 6:
        m = LABEL_RE.match(lines[i])
        if m:
            found[m.group(1)] = m.group(2)
        elif lines[i].strip():
            break
        i += 1
    return (found, "\n".join(lines[i:]).lstrip("\n")) if found else ({}, text)


def set_labels(path: Path, status: str | None = None) -> str:
    """Write / correct the label header at the top of a note. Domain comes from where the note lives; status is the
    one given, else the one already there, else the tracked status, else COMPLETED. Returns the status used."""
    path = Path(path)
    domain = domain_of(path)
    if domain not in TREE or path.suffix.lower() != ".md":
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    labels, body = read_labels(text)
    if status not in STATUSES:
        status = labels.get("STATUS") if labels.get("STATUS") in STATUSES else None
    if status is None and context.store is not None:
        try:
            status = TRACK_TO_STATUS.get(context.store.get_tracking(f"note:{path}")["status"])
        except Exception:
            status = None
    status = status or "COMPLETED"
    new = f"{header(domain, status)}\n\n{body}"
    if new != text:
        path.write_text(new, encoding="utf-8")
    return status


def status_of(path: Path) -> str:
    try:
        return read_labels(Path(path).read_text(encoding="utf-8", errors="ignore"))[0].get("STATUS", "")
    except OSError:
        return ""


def on_index(path: Path) -> None:
    """Called whenever a note is created, changed or indexed: keep its labels right, and if the user changed the
    status in the file itself, follow it."""
    path = Path(path)
    if not enabled() or path.suffix.lower() != ".md" or problems_quick(path) or path.name == "Status_Board.md":
        return                                   # (the status board states the overall state; it isn't a task itself)
    s = context.store
    in_file = status_of(path)
    tracked = ""
    if s is not None:
        try:
            tracked = TRACK_TO_STATUS.get(s.get_tracking(f"note:{path}")["status"], "")
        except Exception:
            tracked = ""
    if in_file in STATUSES and in_file != (tracked or "COMPLETED") and s is not None:
        s.set_tracking(f"note:{path}", status=STATUS_TO_TRACK[in_file], _from_file=True)   # edited by hand: the file wins
    set_labels(path, in_file if in_file in STATUSES else None)


def problems_quick(path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(vault().resolve()).parts
    except ValueError:
        return True
    return len(rel) < 3 or rel[0] not in TREE or rel[1] not in TREE[rel[0]]


def vault() -> Path:
    v = resolve(context.cfg.brain.vault_dir) if context.cfg else resolve("brain")
    v.mkdir(parents=True, exist_ok=True)
    return v


def enabled() -> bool:
    return bool(((context.cfg or {}).get("brain") or {}).get("structure", "strict") != "off")


# ── names ─────────────────────────────────────────────────
def clean_name(title: str, limit: int = 70) -> str:
    """'vehicle maintenance log!' → 'Vehicle_Maintenance_Log' (Pascal_Snake_Case, no spaces or odd characters)."""
    t = re.sub(r"[^\w\s&\-.']", " ", str(title or ""), flags=re.UNICODE).replace("'", "")
    words = [w for w in re.split(r"[\s_]+", t) if w]
    out = []
    for i, w in enumerate(words):
        if w == "&" or re.fullmatch(r"[\d\-.]+", w) or (w.isupper() and len(w) <= 6) or any(c.isupper() for c in w[1:]):
            out.append(w)                                  # numbers, dates, acronyms (QBR, EX4400), CamelCase stay
        elif i and w.lower() in SMALL:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:])
    name = "_".join(out).strip("._-")
    return (name[:limit].rstrip("._-")) or "Untitled"


def file_name(title: str, date: dt.date | None = None, ext: str = ".md") -> str:
    """Dated notes sort by day: 2026-10-02_Northwind_Mist_Pricing.md"""
    base = clean_name(title)
    return f"{date:%Y-%m-%d}_{base}{ext}" if date else f"{base}{ext}"


def category_named(text: str) -> tuple[str, str] | None:
    """Find a category from loose words: 'health', 'Clients', '02_Work/03_Projects_&_Strategy'…"""
    want = re.sub(r"[^a-z]", "", str(text or "").lower().split("/")[-1].replace("and", ""))
    if not want:
        return None
    for domain, cats in TREE.items():
        for cat in cats:
            plain = re.sub(r"[^a-z]", "", cat.lower())
            if want == plain or (len(want) >= 5 and (want in plain or plain in want)):
                return domain, cat
    return None


# ── deciding where something goes ─────────────────────────
def _listing(domain: str) -> str:
    return "\n".join(f"- {c}: {m['about']}" + (f" (sub-folders: {', '.join(m['subs'])})" if m["subs"] else "")
                     for c, m in TREE[domain].items())


def classify_full(title: str, text: str = "", llm=None) -> dict:
    """{"domain", "category", "sub", "confidence", "reason"} for a note. AI model first, keywords as fallback."""
    llm = None if llm is False else (llm if llm is not None else context.llm)
    owner = (context.cfg.assistant.owner if context.cfg else "") or "the user"
    taught = ""
    try:
        from . import lessons
        hit = lessons.filing(title)
        if hit and hit[0] in TREE and hit[1] in TREE[hit[0]]:
            return {"domain": hit[0], "category": hit[1], "sub": "", "confidence": 0.9,
                    "reason": "you filed something like this here before"}
        taught = lessons.filing_examples()
    except Exception:
        pass
    if llm:
        try:
            raw = llm.complete(CLASSIFY_PROMPT.format(owner=owner, personal=_listing(PERSONAL), work=_listing(WORK),
                                                      title=title, text=(text or "")[:2500])
                               + (f"\n\n{owner} corrected earlier filing like this — follow the same thinking:\n{taught}"
                                  if taught else ""),
                               prefer_smart=False, temperature=0.0)
            m = re.search(r"\{.*\}", raw or "", re.S)
            d = json.loads(m.group(0)) if m else {}
            said = str(d.get("domain", "")).lower()
            domain = PERSONAL if said.startswith("p") else WORK if said.startswith("w") else ""
            found = category_named(str(d.get("category", "")))
            try:
                conf = max(0.0, min(1.0, float(d.get("confidence", 0.8))))
            except (TypeError, ValueError):
                conf = 0.6
            if found:
                if domain and found[0] != domain:            # the model contradicted itself: ask
                    conf = min(conf, 0.5)
                return {"domain": found[0], "category": found[1], "sub": _sub(found[1], found[0], str(d.get("subfolder", ""))),
                        "confidence": conf, "reason": str(d.get("reason", ""))[:120]}
        except Exception as e:
            print(f"[brain] couldn't classify '{title[:40]}' with the model, using keywords: {e}")
    blob = f"{title}\n{(text or '')[:4000]}".lower()
    scores = sorted(((sum(blob.count(w) for w in meta["words"]), domain, cat)
                     for domain, cats in TREE.items() for cat, meta in cats.items()), reverse=True)
    top, second = scores[0], scores[1]
    if top[0] == 0:
        return {"domain": WORK, "category": "04_Resources_&_Reference", "sub": "", "confidence": 0.3,
                "reason": "nothing in it told me where it belongs"}
    conf = 0.8 if top[0] >= 3 and top[0] >= 2 * max(1, second[0]) else 0.6 if top[0] > second[0] else 0.45
    return {"domain": top[1], "category": top[2], "sub": "", "confidence": conf, "reason": "matched by keywords"}


def classify(title: str, text: str = "", llm=None) -> tuple[str, str, str]:
    c = classify_full(title, text, llm)
    return c["domain"], c["category"], c["sub"]


def _sub(cat: str, domain: str, sub: str) -> str:
    sub = clean_name(sub, 40) if sub and sub.strip() else ""
    if not sub or sub == "Untitled":
        return ""
    for fixed in TREE[domain][cat]["subs"]:
        if re.sub(r"[^a-z]", "", fixed.lower()) == re.sub(r"[^a-z]", "", sub.lower()):
            return fixed
    return sub


def folder_for(domain: str, category: str, sub: str = "") -> Path:
    p = vault() / domain / category
    if sub:
        p = p / sub
    p.mkdir(parents=True, exist_ok=True)
    return p


def system_folder(kind: str) -> Path:
    """Where Nova's own notes go: journal, dreams, meetings, digests, missions, memory."""
    return folder_for(*SYSTEM[kind])


def place(title: str, text: str = "", hint: str = "", project: str = "", dated: bool = True, llm=None) -> Path:
    """The full path a new note must be saved at (folder created, name cleaned, never overwriting)."""
    hint_key = re.sub(r"[^a-z]", "", (hint or "").lower())
    if hint_key in SYSTEM:
        folder = system_folder(hint_key)
    else:
        named = category_named(hint) if hint and hint_key not in ("inbox", "notes", "ideas", "") else None
        if named:
            folder = folder_for(*named)
        else:
            c = classify_full(title, text, llm)
            domain, cat, sub = c["domain"], c["category"], c["sub"]
            if project and domain == WORK and cat == "03_Projects_&_Strategy" and not sub:
                sub = clean_name(project, 40)
            folder = folder_for(domain, cat, sub)
            path = unique(folder / file_name(title, dt.date.today() if dated else None))
            if c["confidence"] < ASK_BELOW:                  # filed on a best guess: ask the user to confirm
                _remember_check(path, c)
            return path
    return unique(folder / file_name(title, dt.date.today() if dated else None))


def unique(path: Path) -> Path:
    i, out = 2, path
    while out.exists():
        out = path.with_name(f"{path.stem}_{i}{path.suffix}")
        i += 1
    return out


def attachments_for(note_folder: Path) -> Path:
    """Attachments sit in the category's Attachments folder, so nothing ever goes deeper than three levels."""
    rel = note_folder.resolve().relative_to(vault().resolve()).parts
    d = vault().joinpath(*rel[:2], ATTACHMENTS) if len(rel) >= 2 else note_folder / ATTACHMENTS
    d.mkdir(parents=True, exist_ok=True)
    return d


def domain_of(path: str | Path) -> str:
    try:
        return Path(path).resolve().relative_to(vault().resolve()).parts[0]
    except (ValueError, IndexError):
        return ""


# ── checking and fixing the whole vault ───────────────────
def problems(path: Path) -> list[str]:
    """Why a file breaks the rules ([] = fine)."""
    rel = path.resolve().relative_to(vault().resolve()).parts
    folders, name = rel[:-1], rel[-1]
    out = []
    if not folders or folders[0] not in TREE:
        return ["outside 01_Personal / 02_Work"]
    if len(folders) < 2 or folders[1] not in TREE[folders[0]]:
        out.append("not in a numbered category")
    if len(folders) > MAX_DEPTH:
        out.append("more than three folders deep")
    if path.suffix.lower() == ".md" and (" " in name or name != file_like(name)):
        out.append("name not in Pascal_Snake_Case")
    if any(" " in f for f in folders):
        out.append("folder name has spaces")
    return out


def file_like(name: str) -> str:
    stem, ext = Path(name).stem, Path(name).suffix
    m = re.match(r"^(\d{4}-\d{2}-\d{2}|\d{4}-W\d{2})[ _]?(.*)$", stem)
    if m:
        return m.group(1) + (f"_{clean_name(m.group(2))}" if m.group(2).strip(" _") else "") + ext
    return clean_name(stem) + ext


def audit() -> list[dict]:
    v = vault()
    out = []
    for f in sorted(v.rglob("*")):
        if not f.is_file() or f.name.startswith(".") or ".obsidian" in f.parts or ".trash" in f.parts:
            continue
        why = problems(f)
        if why:
            out.append({"path": str(f), "rel": f.relative_to(v).as_posix(), "why": why})
    return out


def _proposal(f: Path, llm=None) -> dict | None:
    """Where a file that breaks the rules should go: {"target": Path, "confidence", "reason", ...} or None."""
    v = vault()
    rel = f.relative_to(v).parts
    top = rel[0].lower() if len(rel) > 1 else ""
    name = file_like(f.name)
    sure = {"confidence": 1.0, "question": False}
    if f.name == "_Nova Memory.md":
        return {**sure, "target": system_folder("memory") / "Nova_Memory.md", "reason": "Nova's own memory export"}
    if top in OLD_FOLDERS:
        return {**sure, "target": system_folder(OLD_FOLDERS[top]) / name, "reason": f"all {rel[0]} notes live here"}
    if rel[0] in TREE and len(rel) >= 3 and rel[1] in TREE[rel[0]]:      # right category, wrong depth or name
        sub = clean_name(rel[2], 40) if len(rel) > 3 else ""
        return {**sure, "target": folder_for(rel[0], rel[1], sub) / name, "reason": "same place, tidier name"}
    if f.suffix.lower() != ".md":          # linked attachments travel with their note; loose files are parked
        return {**sure, "target": attachments_for(folder_for(WORK, "04_Resources_&_Reference")) / name.replace(" ", "_"),
                "reason": "a file no note links to"}
    text = f.read_text(encoding="utf-8", errors="ignore")
    title = re.sub(r"^\d{4}-\d{2}-\d{2}[ _]?", "", f.stem)
    where = "/".join(rel[:-1])
    c = classify_full(title, (f"(It is now in the folder: {where})\n" if where else "") + text, llm)
    if top in ("projects", "project") and len(rel) >= 3:        # Projects/<name>/… keeps its project folder
        project = clean_name(rel[1], 40)
        if len(rel) > 3:                                        # deeper folders become part of the file name
            name = file_like(" ".join(rel[2:-1]) + " " + f.name)
        cat = "03_Projects_&_Strategy" if c["domain"] == WORK else "03_Interests_&_Projects"
        return {"target": folder_for(c["domain"], cat, project) / name, "confidence": c["confidence"],
                "question": c["confidence"] < ASK_BELOW,
                "reason": f"project “{rel[1]}” ({'work' if c['domain'] == WORK else 'personal'})"}
    return {"target": folder_for(c["domain"], c["category"], c["sub"]) / name, "confidence": c["confidence"],
            "question": c["confidence"] < ASK_BELOW, "reason": c["reason"]}


MD_LINK = re.compile(r"(\]\()(<[^>\n]+>|[^)\n]+)(\))")
EMBED = re.compile(r"!\[\[([^\]|#\n]+)")


def _inside(p: Path) -> bool:
    return vault().resolve() in p.resolve().parents


def _link_target(raw: str, base: Path, moved: dict[str, str] | None = None) -> Path | None:
    """The file a markdown link points at (vault-relative, relative to the note, or absolute) — also when that
    file has already been moved in this run."""
    from urllib.parse import unquote
    raw = unquote(raw.strip().strip("<>").split("#")[0])
    if not raw or "://" in raw or raw.startswith(("mailto:", "tel:", "data:")):
        return None
    for cand in (vault() / raw, base / raw, Path(raw)):
        try:
            c = cand.resolve()
        except OSError:
            continue
        if (moved and str(c) in moved) or (c.is_file() and _inside(c)):
            return c
    return None


def _find_embed(name: str, base: Path) -> Path | None:
    """![[picture.png]] → the file, wherever in the vault it is."""
    name = name.strip()
    if not Path(name).suffix or name.lower().endswith(".md"):
        return None
    got = _link_target(name, base)
    if got:
        return got
    for f in vault().rglob(Path(name).name):
        if f.is_file() and ".trash" not in f.parts:
            return f.resolve()
    return None


def move_note(src: Path, dst: Path, batch: dict[str, str] | None = None) -> Path:
    """Move one note, take its attachments along, and keep links, the search index, pins and project links intact.
    `batch` collects old path → new path while a whole plan is applied (links are then fixed once, at the end)."""
    moved = batch if batch is not None else {}
    src = src.resolve()
    dst = unique(dst) if dst.resolve() != src else dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".md":
        text = src.read_text(encoding="utf-8", errors="ignore")
        found = [_link_target(m.group(2), src.parent, moved) for m in MD_LINK.finditer(text)]
        found += [_find_embed(m.group(1), src.parent) for m in EMBED.finditer(text)]
        for target in found:
            if not target or str(target) in moved or target.suffix.lower() == ".md" or not target.is_file():
                continue
            if target.parent.name == ATTACHMENTS and not problems(target):
                continue                                    # already in a proper Attachments folder
            new = unique(attachments_for(dst.parent) / file_like(target.name).replace(" ", "_"))
            shutil.move(str(target), str(new))
            moved[str(target)] = str(new.resolve())
    shutil.move(str(src), str(dst))
    moved[str(src)] = str(dst.resolve())
    s = context.store
    if s is not None and hasattr(s, "rename_path"):
        s.rename_path(str(src), str(dst))
    if dst.suffix.lower() == ".md":
        set_labels(dst)
    if batch is None:
        relink(moved)
    elif s is not None and dst.suffix.lower() == ".md":
        try:
            s.index_note(dst)
        except Exception as e:
            print(f"[brain] couldn't re-index {dst.name}: {e}")
    return dst


def relink(moved: dict[str, str]) -> int:
    """After files moved: every link in every note points at the new place. Returns how many notes changed."""
    v = vault().resolve()
    was = {new: old for old, new in moved.items()}
    names = {}
    for old, new in moved.items():
        o, n = Path(old), Path(new)
        if o.suffix.lower() == ".md":
            names[o.stem] = n.stem
        else:
            names[o.name] = n.name
    changed = set()
    for f in vault().rglob("*.md"):
        if ".trash" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        here = str(f.resolve())
        base = Path(was[here]).parent if here in was else f.parent      # links were written from the old place

        def sub(m):
            target = _link_target(m.group(2), base, moved)
            if target is None:
                return m.group(0)
            final = Path(moved.get(str(target), str(target)))
            raw = m.group(2).strip().strip("<>")
            if str(target) not in moved and (v / raw.split("#")[0].replace("%20", " ")).resolve() == final:
                return m.group(0)                                       # untouched and already vault-relative
            if str(target) not in moved and here not in was:
                return m.group(0)                                       # neither end moved
            tail = "#" + raw.split("#", 1)[1] if "#" in raw else ""
            return f"{m.group(1)}{final.relative_to(v).as_posix().replace(' ', '%20')}{tail}{m.group(3)}"
        out = MD_LINK.sub(sub, text)
        if out != text:
            f.write_text(out, encoding="utf-8")
            changed.add(here)
    changed |= rewrite_wikilinks(names)
    if context.store:
        for path in changed | {n for n in moved.values() if n.lower().endswith(".md")}:
            try:
                context.store.index_note(Path(path))
            except Exception as e:
                print(f"[brain] couldn't re-index {Path(path).name}: {e}")
    return len(changed)


def _data_dir() -> Path:
    db = resolve(context.cfg.brain.db_file) if context.cfg else resolve("data/nova.db")
    return Path(db).resolve().parent


def backup_root() -> Path:
    d = _data_dir()
    return d.parent / "backups" if d.name == "data" else d / "backups"


def backup() -> Path:
    v = vault()
    dest = unique(backup_root() / f"brain-before-reorganise-{dt.datetime.now():%Y%m%d-%H%M%S}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(v, dest, ignore=shutil.ignore_patterns(".obsidian", ".trash"))
    db = resolve(context.cfg.brain.db_file) if context.cfg else None
    if db and Path(db).exists() and context.store:          # the index, pins and links too
        with context.store.lock:
            context.store.db.commit()
            shutil.copy2(db, dest / "_nova_database_backup.db")
    return dest


def rewrite_wikilinks(renamed: dict[str, str]) -> set[str]:
    """[[Old Title]] → [[New_Title|Old Title]] (and ![[old picture.png]] → ![[old_picture.png]]) in every note, so
    links between notes keep working after renames. Returns the notes that changed."""
    changed: set[str] = set()
    if not renamed:
        return changed
    lower = {a.lower(): b for a, b in renamed.items()}
    pat = re.compile(r"\[\[([^\]|#\n]+)([#|][^\]\n]*)?\]\]")
    for f in vault().rglob("*.md"):
        if ".trash" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")

        def sub(m):
            old = m.group(1).strip()
            leaf = old.replace("\\", "/").rsplit("/", 1)[-1]            # [[Notes/Old Title]] is the same note
            leaf = leaf[:-3] if leaf.lower().endswith(".md") else leaf
            new = lower.get(old.lower()) or lower.get(leaf.lower())
            if not new or new == old:
                return m.group(0)
            alias = m.group(2) or ""
            if not alias and leaf != new and not Path(new).suffix:      # keep the readable words on screen
                alias = f"|{leaf}"
            elif alias.startswith("#") and "|" not in alias and leaf != new:
                alias = f"{alias}|{leaf}"
            return f"[[{new}{alias}]]"
        out = pat.sub(sub, text)
        if out != text:
            f.write_text(out, encoding="utf-8")
            changed.add(str(f.resolve()))
    return changed


# ── the reorganisation plan: Nova proposes, you approve ───
_build = {"running": False, "done": 0, "total": 0}


def plan_file() -> Path:
    return _data_dir() / "brain_plan.json"


def load_plan() -> dict:
    try:
        return json.loads(plan_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "none", "items": [], "checks": []}


def save_plan(plan: dict) -> None:
    plan_file().parent.mkdir(parents=True, exist_ok=True)
    plan_file().write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")


def _remember_check(path: Path, c: dict) -> None:
    """A new note was filed on a best guess — keep a question for the user."""
    try:
        plan = load_plan()
        plan.setdefault("checks", []).append({
            "rel": path.relative_to(vault()).as_posix(), "domain": c["domain"], "category": c["category"],
            "sub": c["sub"], "confidence": round(c["confidence"], 2), "reason": c["reason"],
            "when": dt.datetime.now().isoformat(timespec="minutes")})
        plan["checks"] = plan["checks"][-60:]
        save_plan(plan)
    except Exception as e:
        print(f"[brain] {e}")


def build_plan(llm=None) -> dict:
    """Look at everything that breaks the rules and work out where it should go. Moves NOTHING."""
    v = vault()
    bad = [b for b in audit() if Path(b["path"]).exists()]
    _build.update(running=True, done=0, total=len(bad))
    items = []
    try:
        for b in sorted(bad, key=lambda b: b["rel"].lower()):
            f = Path(b["path"])
            try:
                prop = _proposal(f, llm)
            except Exception as e:
                prop = None
                print(f"[brain] {b['rel']}: {e}")
            _build["done"] += 1
            if not prop or prop["target"].resolve() == f.resolve():
                continue
            t = prop["target"].relative_to(v)
            items.append({"rel": b["rel"], "target": t.as_posix(), "domain": t.parts[0], "category": t.parts[1],
                          "sub": t.parts[2] if len(t.parts) > 3 else "", "name": t.name, "why": b["why"],
                          "confidence": round(prop["confidence"], 2), "question": bool(prop["question"]),
                          "reason": prop["reason"], "answered": False})
        _attachments_follow(items)
    finally:
        _build["running"] = False
    old = load_plan()
    plan = {"state": "ready" if items else "clean", "built": dt.datetime.now().isoformat(timespec="minutes"),
            "items": items, "checks": old.get("checks", [])}
    save_plan(plan)
    return plan


def _attachments_follow(items: list[dict]) -> None:
    """A picture or file that a note links to is shown going where that note goes (that's what will happen)."""
    v = vault()
    loose = {str((v / i["rel"]).resolve()): i for i in items if not i["rel"].lower().endswith(".md")}
    if not loose:
        return
    going = {str((v / i["rel"]).resolve()): v / i["target"] for i in items if i["rel"].lower().endswith(".md")}
    notes = [f for f in v.rglob("*.md") if ".trash" not in f.parts]
    for f in sorted(notes, key=lambda f: (str(f.resolve()) not in going, f.relative_to(v).as_posix())):
        text = f.read_text(encoding="utf-8", errors="ignore")       # (notes that move come first, as when applying)
        found = [_link_target(m.group(2), f.parent) for m in MD_LINK.finditer(text)]
        found += [_find_embed(m.group(1), f.parent) for m in EMBED.finditer(text)]
        for t in found:
            it = loose.pop(str(t), None) if t else None
            if not it:
                continue
            home = going.get(str(f.resolve()), f)
            dst = (attachments_for(home.parent) / it["name"]).relative_to(v)
            it.update(target=dst.as_posix(), domain=dst.parts[0], category=dst.parts[1], sub="",
                      reason=f"goes with the note “{f.stem}”")


def plan_summary(plan: dict | None = None) -> dict:
    plan = plan or load_plan()
    items = plan.get("items", [])
    open_q = [i for i in items if i["question"] and not i["answered"]]
    return {"state": plan.get("state", "none"), "moves": len(items), "questions": len(open_q),
            "checks": len(plan.get("checks", [])), "building": _build["running"],
            "progress": f"{_build['done']}/{_build['total']}" if _build["running"] else ""}


def answer(rel: str, domain: str, category: str, sub: str = "", name: str = "") -> dict:
    """You decide where one item goes (a question in the plan, or a recently filed note)."""
    found = category_named(category)
    if domain not in TREE or not found or found[0] != domain:
        raise ValueError(f"{category} isn't a category of {domain}")
    category, sub = found[1], _sub(found[1], domain, sub)
    plan = load_plan()
    for it in plan.get("items", []):
        if it["rel"] == rel:
            nm = file_like(name) if name else it["name"]
            it.update(domain=domain, category=category, sub=sub, name=nm, answered=True, question=True,
                      target="/".join(x for x in (domain, category, sub, nm) if x))
            save_plan(plan)
            return {"ok": True, "target": it["target"]}
    for c in list(plan.get("checks", [])):
        if c["rel"] == rel:
            src = vault() / rel
            plan["checks"].remove(c)
            save_plan(plan)
            if not src.exists():
                return {"ok": True, "target": rel}
            dst = folder_for(domain, category, sub) / (file_like(name) if name else src.name)
            if dst.resolve() == src.resolve():
                return {"ok": True, "target": rel}
            new = move_note(src, dst)
            return {"ok": True, "target": new.relative_to(vault()).as_posix()}
    raise KeyError(rel)


def apply_plan(include_unanswered: bool = False) -> dict:
    """Do the approved moves: backup first, then move, re-link and re-index. Open questions stay where they are
    unless include_unanswered is set (then Nova's best guess is used)."""
    v = vault()
    plan = load_plan()
    todo = [i for i in plan.get("items", []) if include_unanswered or not i["question"] or i["answered"]]
    keep = [i for i in plan.get("items", []) if i not in todo]
    if not todo:
        return {"moved": 0, "left": len(keep), "backup": "", "links": 0}
    saved = backup()
    moved, done, failed = {}, [], []
    for it in sorted(todo, key=lambda i: (not i["rel"].lower().endswith(".md"), i["rel"])):
        src, dst = v / it["rel"], v / it["target"]
        if not src.exists():                               # already went along as an attachment of another note
            continue
        try:
            new = move_note(src, dst, moved)
            done.append((it["rel"], new.relative_to(v).as_posix()))
        except Exception as e:
            failed.append({**it, "error": str(e)})
            print(f"[brain] couldn't move {it['rel']}: {e}")
    links = relink(moved)
    for f in v.rglob("*.md"):                              # every note states its domain and status
        if not problems_quick(f):
            set_labels(f)
    for d in sorted((p for p in v.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):   # empty old folders
        if ".obsidian" in d.parts or ".trash" in d.parts or d.name in (".obsidian", ".trash"):
            continue
        if d.relative_to(v).parts[0] not in TREE and not any(d.iterdir()):
            d.rmdir()
    scaffold()
    write_status_boards()
    plan["items"] = keep + failed
    plan["state"] = "ready" if plan["items"] else "clean"
    plan["applied"] = dt.datetime.now().isoformat(timespec="minutes")
    plan["last_backup"] = str(saved)
    save_plan(plan)
    if context.store:
        context.store.log("memory", "system", f"🗂 Brain reorganised: {len(moved)} moved, {links} notes re-linked",
                          "\n".join(f"{a} → {b}" for a, b in done[:80]), turn=0)
    return {"moved": len(moved), "left": len(plan["items"]), "backup": str(saved), "links": links,
            "failed": len(failed), "examples": done[:8]}


def startup_check() -> None:
    """After an update: if the brain doesn't follow the rules yet, prepare the plan and tell the user. No moves."""
    if not enabled():
        return
    scaffold()
    plan = load_plan()
    if plan.get("state") == "ready" and plan.get("items"):
        return
    if not audit():
        return
    plan = build_plan()
    s = plan_summary(plan)
    if s["moves"]:
        msg = (f"🗂 Your brain's new filing structure is ready to apply: {s['moves']} notes to move or rename"
               + (f", and I have {s['questions']} question{'s' if s['questions'] != 1 else ''} for you" if s["questions"] else "")
               + ". Nothing has been moved yet — open the dashboard and click 🗂 to review and approve.")
        if context.push:
            context.push(msg, [])
        if context.store:
            context.store.log("memory", "system", "🗂 Brain reorganisation plan ready", msg, turn=0)


BOARD = "Status_Board.md"


def _guess_domain(text: str) -> str:
    """Cheap, model-free guess for things that aren't notes (focus tasks, missions)."""
    return classify_full(text, "", llm=False)["domain"] if text else WORK


def board_items() -> dict[str, dict[str, list[str]]]:
    """Everything with a status, per domain: tracked notes and memories, focus tasks, missions, held actions,
    filing questions."""
    out = {d: {st: [] for st in STATUSES} for d in TREE}
    s = context.store
    if s is None:
        return out
    with s.lock:
        tracked = [dict(r) for r in s.db.execute("SELECT item, status, note FROM tracking WHERE status != ''")]
    for t in tracked:
        st = TRACK_TO_STATUS.get(t["status"])
        if not st:
            continue
        extra = f" — {t['note'][:80]}" if t["note"] else ""
        if t["item"].startswith("note:"):
            p = Path(t["item"][5:])
            if p.exists() and domain_of(p) in TREE:
                out[domain_of(p)][st].append(f"[[{p.stem}]]{extra}")
        elif t["item"].startswith("memory:"):
            with s.lock:
                row = s.db.execute("SELECT text FROM memories WHERE id=?", (t["item"][7:],)).fetchone()
            if row:
                out[_guess_domain(row["text"])][st].append(f"{row['text'][:110]}{extra}")
    try:
        from . import focus
        st = focus.state()
        if st.get("now"):
            out[_guess_domain(st["now"]["text"])]["IN-PROGRESS"].append(f"Focus now: {st['now']['text']}")
        for t in (st.get("next") or []) + (st.get("later") or []):
            out[_guess_domain(t["text"])]["BACKLOG"].append(t["text"])
        for title in (st.get("wins") or {}).get("titles", []):
            out[_guess_domain(title)]["COMPLETED"].append(f"{title} (today)")
    except Exception:
        pass
    try:
        from . import wellbeing
        for h in wellbeing.held():
            out[WORK]["WAITING-ON-USER"].append(f"Held action #{h['id']}: {h['label']} — say 'send held #{h['id']}' or drop it")
    except Exception:
        pass
    try:
        with s.lock:
            ms = [dict(r) for r in s.db.execute("SELECT title, status FROM missions")]
        for m in ms:
            st = {"running": "IN-PROGRESS", "active": "IN-PROGRESS", "stuck": "WAITING-ON-USER",
                  "failed": "WAITING-ON-USER", "paused": "BACKLOG", "scheduled": "BACKLOG", "done": "COMPLETED"}.get(m["status"])
            if st:
                out[_guess_domain(m["title"])][st].append(f"Mission: {m['title']}")
    except Exception:
        pass
    plan = load_plan()
    for it in plan.get("items", []):
        if it["question"] and not it["answered"]:
            out[it["domain"]]["WAITING-ON-USER"].append(f"Where should “{it['rel']}” be filed? (dashboard 🗂)")
    for c in plan.get("checks", []):
        out[c["domain"]]["WAITING-ON-USER"].append(f"Is “{Path(c['rel']).name}” in the right place? (dashboard 🗂)")
    if any(not i["question"] or i["answered"] for i in plan.get("items", [])):
        out[WORK]["WAITING-ON-USER"].append("Approve the brain reorganisation (dashboard 🗂)")
    return out


# ── you correct the filing: move an item to the other side / another category, or delete it ──
def _placements() -> dict[str, tuple[str, str]]:
    """Where you put things that aren't files (projects, memories, missions): item → (domain, category)."""
    s = context.store
    if s is None:
        return {}
    with s.lock:
        s.db.execute("CREATE TABLE IF NOT EXISTS placement(item TEXT PRIMARY KEY, domain TEXT, category TEXT)")
        return {r["item"]: (r["domain"], r["category"]) for r in s.db.execute("SELECT * FROM placement")}


def move_item(item: str, domain: str, category: str = "", sub: str = "") -> dict:
    """Put one item where the user says. A note's file is moved (links, pins and status follow); anything else
    remembers its place. Returns {"ok", "where"}."""
    if domain not in TREE:
        raise ValueError("Choose 01_Personal or 02_Work.")
    found = category_named(category) if category else None
    if category and (not found or found[0] != domain):
        raise ValueError(f"{category} isn't a category of {domain}")
    s = context.store
    if item.startswith("note:"):
        src = Path(item[5:])
        if not src.is_file() or not _inside(src):
            raise ValueError("That note isn't in the brain any more.")
        v = vault()
        rel = src.resolve().relative_to(v.resolve()).parts
        if not found:                                   # only the side was chosen: keep the matching kind of category
            same = rel[1][:3] if len(rel) > 2 and rel[0] in TREE else ""
            cat = next((c for c in TREE[domain] if same and c.startswith(same)), None) or classify_in(domain, src)
            found = (domain, cat)
            if not sub and len(rel) > 3 and rel[0] in TREE:
                sub = "" if rel[2] in TREE[rel[0]].get(rel[1], {}).get("subs", []) else rel[2]
        dst = folder_for(domain, found[1], _sub(found[1], domain, sub)) / file_like(src.name)
        if dst.resolve() == src.resolve():
            return {"ok": True, "where": dst.relative_to(v).as_posix(), "id": item}
        old_rel = "/".join(rel)
        new = move_note(src, dst)
        try:                                            # undo, and a lesson: "notes like this belong here"
            from . import lessons, undo
            undo.record(f"moved the note {src.stem.replace('_', ' ')} to {found[1]}", "note_move", old=str(src), new=str(new))
            if len(rel) < 2 or (rel[0], rel[1]) != (domain, found[1]):
                lessons.learn("filing", src.stem.replace("_", " "), f"{domain}|{found[1]}")
        except Exception as e:
            print(f"[brain] couldn't note the correction: {e}")
        plan = load_plan()                              # it no longer needs a question or a check
        plan["items"] = [i for i in plan.get("items", []) if i["rel"] != old_rel]
        plan["checks"] = [c for c in plan.get("checks", []) if c["rel"] != old_rel]
        if not plan["items"] and plan.get("state") == "ready":
            plan["state"] = "clean"
        save_plan(plan)
        write_status_boards()
        if s:
            s.log("memory", "dashboard", f"🗂 Moved {src.name} → {new.relative_to(v).as_posix()}", turn=0)
        return {"ok": True, "where": new.relative_to(v).as_posix(), "id": f"note:{new}"}
    if s is None:
        raise ValueError("The brain isn't ready.")
    was = _placements().get(item)
    with s.lock:
        s.db.execute("INSERT OR REPLACE INTO placement(item, domain, category) VALUES(?,?,?)",
                     (item, domain, found[1] if found else ""))
        s.db.commit()
    try:
        from . import lessons, undo
        undo.record(f"moved an item to {found[1] if found else domain}", "placement", item=item, was=list(was) if was else None)
        if item.startswith("memory:") and found:
            with s.lock:
                row = s.db.execute("SELECT text FROM memories WHERE id=?", (item[7:],)).fetchone()
            if row:
                lessons.learn("filing", row["text"][:80], f"{domain}|{found[1]}")
    except Exception as e:
        print(f"[brain] couldn't note the correction: {e}")
    write_status_boards()
    return {"ok": True, "where": "/".join(x for x in (domain, found[1] if found else "") if x), "id": item}


def classify_in(domain: str, path: Path) -> str:
    """Best category inside one domain for a note (keywords only)."""
    text = (path.stem + " " + path.read_text(encoding="utf-8", errors="ignore")[:1500]).lower()
    best, score = next(iter(TREE[domain])), 0
    for cat, meta in TREE[domain].items():
        n = sum(1 for w in meta["words"] if w in text)
        if n > score:
            best, score = cat, n
    return best


def delete_item(item: str) -> dict:
    """Take one item out of the brain. A note's file is kept in backups/deleted (so it can be put back); a memory
    is retired, not erased."""
    s = context.store
    if item.startswith("note:"):
        src = Path(item[5:])
        if not src.is_file() or not _inside(src):
            raise ValueError("That note isn't in the brain any more.")
        bin_ = backup_root() / "deleted"
        bin_.mkdir(parents=True, exist_ok=True)
        dst = unique(bin_ / f"{dt.datetime.now():%Y%m%d-%H%M%S}_{src.name}")
        rel = src.resolve().relative_to(vault().resolve()).as_posix()
        shutil.move(str(src), str(dst))
        try:
            from . import undo
            undo.record(f"deleted the note {src.stem.replace('_', ' ')}", "note_delete", old=str(src), kept=str(dst))
        except Exception:
            pass
        if s:
            with s.lock:
                s.db.execute("DELETE FROM chunks WHERE path=?", (str(src),))
                s.db.execute("DELETE FROM tracking WHERE item=?", (item,))
                try:
                    s.db.execute("DELETE FROM links WHERE item=?", (item,))
                except Exception:
                    pass
                s.db.commit()
            s.log("memory", "dashboard", f"🗑 Deleted note {src.name}", f"kept in {dst}", turn=0)
        plan = load_plan()
        plan["items"] = [i for i in plan.get("items", []) if i["rel"] != rel]
        plan["checks"] = [c for c in plan.get("checks", []) if c["rel"] != rel]
        save_plan(plan)
        write_status_boards()
        return {"ok": True, "kept": str(dst)}
    if s is None:
        raise ValueError("The brain isn't ready.")
    if item.startswith("memory:"):
        mid = int(item[7:])
        with s.lock:
            row = s.db.execute("SELECT text FROM memories WHERE id=? AND superseded_by IS NULL", (mid,)).fetchone()
            if not row:
                raise ValueError("That memory is already gone.")
            s.db.execute("UPDATE memories SET superseded_by=id WHERE id=?", (mid,))      # retired, still in the database
            s.db.execute("DELETE FROM tracking WHERE item=?", (item,))
            s.db.commit()
        s.log("memory", "dashboard", f"🗑 Forgot: {row['text'][:90]}", turn=0)
        try:
            s.export_markdown(vault())
        except Exception:
            pass
        write_status_boards()
        return {"ok": True, "kept": ""}
    if item.startswith("mission:"):
        from .missions import missions
        missions().delete(int(item[8:]))
        return {"ok": True, "kept": ""}
    raise ValueError("That kind of item can't be deleted from here.")


def bottlenecks() -> list[dict]:
    """Everything that is waiting on the user right now: [{"id", "text", "domain", "why"}] — the dashboard's
    'active bottlenecks' list. `id` opens the item on the map ('filing' opens the filing review)."""
    out = []
    s = context.store
    if s is None:
        return out
    with s.lock:
        rows = [dict(r) for r in s.db.execute("SELECT item, note FROM tracking WHERE status='waiting'")]
    for t in rows:
        if t["item"].startswith("note:"):
            p = Path(t["item"][5:])
            if p.exists():
                out.append({"id": t["item"], "text": p.stem.replace("_", " "), "why": t["note"] or "",
                            "domain": domain_of(p) if domain_of(p) in TREE else _guess_domain(p.stem)})
        elif t["item"].startswith("memory:"):
            with s.lock:
                row = s.db.execute("SELECT text FROM memories WHERE id=?", (t["item"][7:],)).fetchone()
            if row:
                out.append({"id": t["item"], "text": row["text"][:110], "why": t["note"] or "",
                            "domain": _guess_domain(row["text"])})
    try:
        from . import wellbeing
        for h in wellbeing.held():
            out.append({"id": "", "text": f"Held: {h['label']}", "why": f"say “send held #{h['id']}” or drop it",
                        "domain": WORK})
    except Exception:
        pass
    try:
        from . import business
        names = {b["id"]: b["name"] for b in business.businesses()}
        out += [{"id": "business", "text": f"{names.get(q['biz'], q['biz'])}: {q['title']}", "domain": WORK,
                 "why": "approve or drop it on the Business dashboard"} for q in business.pending()[:12]]
    except Exception:
        pass
    try:
        from . import evolve
        out += [{"id": "", "text": f"Playbook draft: {d['name']}", "domain": WORK,
                 "why": f"say “approve the playbook {d['name']}” or “drop the playbook {d['name']}”"}
                for d in evolve.pending()]
    except Exception:
        pass
    try:
        with s.lock:
            ms = [dict(r) for r in s.db.execute("SELECT id, title FROM missions WHERE status IN ('failed','stuck')")]
        out += [{"id": f"mission:{m['id']}", "text": f"Mission: {m['title']}", "why": "it got stuck",
                 "domain": _guess_domain(m["title"])} for m in ms]
    except Exception:
        pass
    plan = load_plan()
    open_q = [i for i in plan.get("items", []) if i["question"] and not i["answered"]]
    if open_q:
        out.append({"id": "filing", "text": f"{len(open_q)} filing question{'s' if len(open_q) != 1 else ''}",
                    "why": "where should these notes go?", "domain": open_q[0]["domain"]})
    elif plan.get("items"):
        out.append({"id": "filing", "text": f"Brain reorganisation: {len(plan['items'])} moves ready",
                    "why": "waiting for your approval", "domain": WORK})
    if plan.get("checks"):
        out.append({"id": "filing", "text": f"{len(plan['checks'])} note(s) filed on a best guess", "why": "is it right?",
                    "domain": plan["checks"][0]["domain"]})
    return out


def write_status_boards() -> list[Path]:
    """One summary log per domain with what's in progress, waiting on you, in the backlog and completed."""
    if not enabled():
        return []
    items = board_items()
    titles = {"IN-PROGRESS": "In progress", "WAITING-ON-USER": "Waiting on you", "BACKLOG": "Backlog",
              "COMPLETED": "Completed"}
    made = []
    for domain, kind in ((PERSONAL, "journal"), (WORK, "meetings")):
        folder = system_folder(kind).parent if kind == "meetings" else system_folder(kind)
        d = items[domain]
        state = "WAITING-ON-USER" if d["WAITING-ON-USER"] else "IN-PROGRESS" if d["IN-PROGRESS"] else \
            "BACKLOG" if d["BACKLOG"] else "COMPLETED"
        lines = [header(domain, state), "", f"# Status board — {domain_label(domain).title()}", "",
                 f"_Updated {dt.datetime.now():%Y-%m-%d %H:%M} by Nova. Change a note's status on the dashboard (Track this) "
                 "or by editing its STATUS label._", ""]
        for st in ("WAITING-ON-USER", "IN-PROGRESS", "BACKLOG", "COMPLETED"):
            rows = d[st][-40:] if st == "COMPLETED" else d[st]
            lines += [f"## [LABEL: STATUS: {st}] {titles[st]} ({len(d[st])})", "", *([f"- {r}" for r in rows] or ["- nothing"]), ""]
        path = folder / BOARD
        text = "\n".join(lines)
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        if re.sub(r"_Updated [^_]*_", "", old) != re.sub(r"_Updated [^_]*_", "", text):
            path.write_text(text, encoding="utf-8")
            if context.store:
                try:
                    context.store.index_note(path)
                except Exception:
                    pass
        made.append(path)
    return made


def scaffold() -> None:
    """Make sure the two domains and their numbered categories exist."""
    for domain, cats in TREE.items():
        for cat, meta in cats.items():
            (vault() / domain / cat).mkdir(parents=True, exist_ok=True)
            for sub in meta["subs"]:
                (vault() / domain / cat / sub).mkdir(exist_ok=True)


def tree_text() -> str:
    v = vault()
    lines = []
    for domain in TREE:
        lines.append(f"{domain}/")
        for cat, meta in TREE[domain].items():
            n = sum(1 for _ in (v / domain / cat).rglob("*.md")) if (v / domain / cat).exists() else 0
            lines.append(f"  {cat}/  ({n} note{'s' if n != 1 else ''})")
            folder = v / domain / cat
            subs = sorted(p.name for p in folder.iterdir() if p.is_dir()) if folder.exists() else meta["subs"]
            lines += [f"    {s}/" for s in subs]
    return "\n".join(lines)
