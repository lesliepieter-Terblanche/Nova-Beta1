"""Nova gets better at her job by herself — in two safe ways, once a week.

  self-review   she looks back at the week: what failed, what she had to say "I can't" to, what was slow, what you
                corrected — and says plainly what she struggled with and what she would change.
  playbooks     when you have asked for the same kind of job several times, she writes the steps down as a playbook
                (plain instructions, never code) and asks you to approve it. Approved playbooks live in
                playbooks/mine/ and are followed from then on; a draft you drop is never proposed again.

Nothing here changes Nova's own code, and nothing takes effect without your yes.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter
from pathlib import Path

import yaml

from . import context
from .config import ROOT, resolve

STOP = set("""a an the and or but for with from this that these those to of in on at by as is are was were be been it
its my me our your you i we us do does did done can could would should will shall please nova hey ok okay now then
just also so if into out up about again some any all new make made get got give show tell what whats which who how
when where why not no yes thanks thank today tomorrow yesterday week month""".split())
CANT = re.compile(r"\b(i can'?t|i cannot|can'?t (do|yet)|couldn'?t|i'?m not able|not able to|no tool|don'?t have a (tool|way)|"
                  r"isn'?t (set up|connected|installed)|not (set up|connected|installed) yet)\b", re.I)
SKIP_SESSIONS = ("mission:", "routine", "test", "system")
NUMBERED = re.compile(r"^\d+[.)]\s*")           # a step that already carries its number
REVIEW_PROMPT = """You are {name}, {owner}'s assistant, looking back honestly at your own week. From the facts below
write three short sections with these exact headings, two to four plain bullet points each, no jokes, no filler, no
invented details — only what the facts show:
## Went well
## I struggled with
## What I'd change
In "What I'd change" be concrete: a setting to switch, a key or add-on that is missing, a kind of request to phrase
differently, or a playbook worth writing. If a section has nothing, write "Nothing this week."

FACTS:
{facts}"""
DRAFT_PROMPT = """{owner} has asked for the same kind of job {n} times. Write a reusable playbook for it: plain numbered
steps an assistant follows with the tools named below. No code.

The requests:
{examples}

Tools that were used for them, in order: {tools}

Reply with JSON only:
{{"name": "short-kebab-case-name", "description": "one sentence: what this playbook does",
  "triggers": ["2 to 4 short lowercase phrases, each copied word for word from the requests above, that mark this job"],
  "steps": ["step 1 …", "step 2 …"]}}
Rules: 3 to 7 steps. Name the tool to use in each step. Leave the details that change each time (names, dates,
files) as things to take from the request. End with a step that tells {owner} briefly what was done."""


def cfg() -> dict:
    return dict(((context.cfg or {}).get("evolve") or {}))


def _state_path() -> Path:
    return resolve("data/evolve.json")


def mine_dir() -> Path:
    return ROOT / "playbooks" / "mine"


def state() -> dict:
    try:
        d = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.setdefault("drafts", [])
    return d


def _save(d: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z'-]{2,}", (text or "").lower()) if w not in STOP}


# ── the week, as it really went ───────────────────────────
def turns(days: int, now: dt.datetime | None = None) -> list[dict]:
    """The requests of the last days with how each went: text, status, seconds, tools used, the reply."""
    s = context.store
    if s is None:
        return []
    now = now or dt.datetime.now()
    since = (now - dt.timedelta(days=days)).isoformat(timespec="seconds")
    with s.lock:
        rows = [dict(r) for r in s.db.execute(
            "SELECT id, ts, session, title, detail, status, ms FROM activity WHERE kind='user' AND turn=id AND ts>=? "
            "AND ts<=? ORDER BY id", (since, now.isoformat(timespec="seconds")))]
        out = []
        for r in rows:
            if str(r["session"]).startswith(SKIP_SESSIONS):
                continue
            steps = s.db.execute("SELECT kind, title, detail, status FROM activity WHERE turn=? AND id!=? ORDER BY id",
                                 (r["id"], r["id"])).fetchall()
            tools = [x["title"].replace(" (waiting for your yes/no)", "").replace(" ", "_")
                     for x in steps if x["kind"] == "tool"]
            reply = next((x["detail"] or x["title"] for x in steps if x["kind"] == "reply"), "")
            out.append({"id": r["id"], "day": r["ts"][:10], "text": (r["detail"] or r["title"] or "").strip(),
                        "status": r["status"] or "done", "seconds": (r["ms"] or 0) / 1000, "tools": tools,
                        "tool_errors": sum(1 for x in steps if x["kind"] == "tool" and x["status"] == "error"),
                        "reply": reply or ""})
    return out


def struggles(days: int = 7, now: dt.datetime | None = None) -> dict:
    ts = turns(days, now)
    s = context.store
    since = ((now or dt.datetime.now()) - dt.timedelta(days=days)).isoformat(timespec="seconds")
    corrections = []
    if s is not None:
        with s.lock:
            try:
                corrections = [r["text"] for r in s.db.execute(
                    "SELECT text FROM memories WHERE kind='preference' AND created>=? AND superseded_by IS NULL "
                    "ORDER BY id DESC LIMIT 8", (since,))]
            except Exception:
                corrections = []
    failed = [t for t in ts if t["status"] == "error" or t["tool_errors"]]
    couldnt = [t for t in ts if t["status"] != "error" and not t["tool_errors"] and CANT.search(t["reply"][:400])]
    slow = sorted((t for t in ts if t["seconds"] >= 30), key=lambda t: -t["seconds"])[:3]
    ok = [t for t in ts if t not in failed and t not in couldnt]
    used = Counter(tool for t in ok for tool in dict.fromkeys(t["tools"]))
    return {"days": days, "requests": len(ts), "ok": len(ok), "failed": failed[:6], "couldnt": couldnt[:6],
            "slow": slow, "corrections": corrections, "best_tools": [n for n, _ in used.most_common(5)],
            "repeats": repeated_jobs(max(days, 30), now)}


def facts_text(f: dict) -> str:
    def short(t, n=110):
        return " ".join(t.split())[:n]
    out = [f"Requests in the last {f['days']} days: {f['requests']}; handled without trouble: {f['ok']}."]
    if f["best_tools"]:
        out.append("Used most, and working: " + ", ".join(t.replace("_", " ") for t in f["best_tools"]) + ".")
    for t in f["failed"]:
        out.append(f"FAILED: \"{short(t['text'])}\" — {short(t['reply'], 140) or 'no reply'}")
    for t in f["couldnt"]:
        out.append(f"COULDN'T DO: \"{short(t['text'])}\" — I said: {short(t['reply'], 140)}")
    for t in f["slow"]:
        out.append(f"SLOW ({t['seconds']:.0f} s): \"{short(t['text'])}\"")
    for c in f["corrections"]:
        out.append(f"CORRECTED BY {str((context.cfg or {}).get('assistant', {}).get('owner', 'the user')).upper()}: {short(c, 140)}")
    for r in f["repeats"]:
        out.append(f"ASKED {len(r['turns'])} TIMES, no playbook yet: \"{short(r['turns'][0]['text'])}\"")
    return "\n".join(out)


def review(days: int = 7, now: dt.datetime | None = None, llm=None) -> dict:
    """Nova's own look back at the week. {"text", "facts", "path"} — the text is saved in workspace/evolve/."""
    now = now or dt.datetime.now()
    f = struggles(days, now)
    a = (context.cfg or {}).get("assistant") or {}
    body = ""
    llm = llm if llm is not None else context.llm
    if llm and f["requests"]:
        try:
            body = (llm.complete(REVIEW_PROMPT.format(name=a.get("name", "Nova"), owner=a.get("owner", "the user"),
                                                      facts=facts_text(f)), prefer_smart=True, temperature=0.2)
                    or "").strip()
        except Exception as e:
            print(f"[evolve] the model couldn't write the review, using the plain facts: {e}")
    if "## I struggled with" not in body:
        def lines(items, fmt):
            return "\n".join(f"- {fmt(t)}" for t in items) or "Nothing this week."
        body = (f"## Went well\n- {f['ok']} of {f['requests']} requests handled without trouble."
                + (f"\n- Working reliably: {', '.join(t.replace('_', ' ') for t in f['best_tools'])}." if f["best_tools"] else "")
                + "\n\n## I struggled with\n"
                + lines(f["failed"] + f["couldnt"] + f["slow"],
                        lambda t: f"\"{' '.join(t['text'].split())[:90]}\" — "
                        + ("it failed" if t in f["failed"] else "I couldn't do it" if t in f["couldnt"]
                           else f"it took {t['seconds']:.0f} seconds"))
                + "\n\n## What I'd change\n"
                + lines(f["repeats"], lambda r: f"Write a playbook for \"{' '.join(r['turns'][0]['text'].split())[:80]}\" "
                        f"(asked {len(r['turns'])} times)."))
    path = resolve("workspace/evolve") / f"self_review_{now:%Y-%m-%d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# How my week went — {now:%d %B %Y}\n\n{body}\n", encoding="utf-8")
    return {"text": body, "facts": f, "path": str(path)}


# ── jobs you keep asking for ──────────────────────────────
def repeated_jobs(days: int = 30, now: dt.datetime | None = None, min_times: int | None = None) -> list[dict]:
    """Groups of similar requests that went well, took real steps, and have no playbook yet:
    [{"key": [...words], "turns": [...], "tools": [...]}], the most repeated first."""
    from .extensions import matching_playbooks
    min_times = int(min_times or cfg().get("repeat_times", 3))
    seen = [set(d["key"]) for d in state()["drafts"]]
    groups: list[dict] = []
    for t in turns(days, now):
        w = words(t["text"])
        if t["status"] != "done" or t["tool_errors"] or not t["tools"] or len(w) < 2 or len(t["text"]) > 400:
            continue
        if matching_playbooks(t["text"]):
            continue
        for g in groups:
            if len(w & g["words"]) / max(1, len(w | g["words"])) >= 0.5:
                g["turns"].append(t)
                g["count"].update(w)
                break
        else:
            groups.append({"words": w, "turns": [t], "count": Counter(w)})
    out = []
    for g in groups:
        n = len(g["turns"])
        if n < min_times or len({t["day"] for t in g["turns"]}) < 2:
            continue
        key = sorted(w for w, c in g["count"].items() if c >= max(2, n - 1))
        if len(key) < 2 or any(len(set(key) & k) / max(1, len(set(key) | k)) >= 0.6 for k in seen):
            continue                                  # too vague, or already drafted / approved / dropped
        seq = Counter(tuple(dict.fromkeys(t["tools"])) for t in g["turns"]).most_common(1)[0][0]
        out.append({"key": key, "turns": g["turns"], "tools": list(seq)})
    return sorted(out, key=lambda g: -len(g["turns"]))


def _common_phrases(texts: list[str]) -> list[str]:
    """Two- to four-word phrases that appear in most of the requests — the safe fallback for triggers."""
    low = [re.sub(r"[^a-z0-9' ]+", " ", t.lower()).split() for t in texts]
    found = Counter()
    for ws in low:
        grams = {" ".join(ws[i:i + n]) for n in (4, 3, 2) for i in range(len(ws) - n + 1)}
        found.update(g for g in grams if len(words(g)) >= 2)
    need = max(2, len(texts) - 1)
    good = sorted((g for g, c in found.items() if c >= need), key=lambda g: (-len(g.split()), g))
    out = []
    for g in good:
        if not any(g in o for o in out):
            out.append(g)
    return out[:3]


def draft_playbook(job: dict, llm=None) -> dict | None:
    """Write a playbook draft for one repeated job and keep it as pending. None when it can't be pinned down."""
    from .tools import REGISTRY
    owner = str(((context.cfg or {}).get("assistant") or {}).get("owner", "the user"))
    texts = [t["text"] for t in job["turns"]]
    lowered = [t.lower() for t in texts]
    data = {}
    llm = llm if llm is not None else context.llm
    if llm:
        try:
            raw = llm.complete(DRAFT_PROMPT.format(owner=owner, n=len(texts),
                                                   examples="\n".join(f"- {t[:200]}" for t in texts[:6]),
                                                   tools=", ".join(job["tools"]) or "none"),
                               prefer_smart=True, temperature=0.2)
            m = re.search(r"\{.*\}", raw or "", re.S)
            data = json.loads(m.group(0)) if m else {}
        except Exception as e:
            print(f"[evolve] the model couldn't draft the playbook, using the plain steps: {e}")
    triggers = [str(t).lower().strip() for t in (data.get("triggers") or [])
                if isinstance(t, str) and len(words(t)) >= 2 and sum(str(t).lower().strip() in x for x in lowered) >= 2]
    triggers = list(dict.fromkeys(triggers))[:4] or _common_phrases(texts)
    if not triggers:
        return None                                   # nothing safe to recognise it by: better no playbook
    steps = [str(s).strip() for s in (data.get("steps") or []) if str(s).strip()][:8]
    if len(steps) < 2:
        steps = [f"Use {t} — take the details from the request." for t in job["tools"]] + \
                [f"Tell {owner} in one or two sentences what was done."]
    name = re.sub(r"[^a-z0-9]+", "-", str(data.get("name") or "-".join(job["key"][:3])).lower()).strip("-")[:40] or "my-job"
    d = state()
    taken = {x["name"] for x in d["drafts"]}
    base, i = name, 2
    while name in taken:
        name, i = f"{base}-{i}", i + 1
    groups = sorted({REGISTRY[t].group for t in job["tools"] if t in REGISTRY})
    draft = {"id": max([x["id"] for x in d["drafts"]] + [0]) + 1, "name": name,
             "description": " ".join(str(data.get("description") or f"What to do for: {texts[0][:80]}").split())[:200],
             "triggers": triggers, "groups": groups, "steps": steps, "examples": texts[:4], "times": len(texts),
             "key": job["key"], "state": "pending", "created": dt.datetime.now().isoformat(timespec="seconds")}
    d["drafts"].append(draft)
    _save(d)
    return draft


def pending() -> list[dict]:
    return [d for d in state()["drafts"] if d["state"] == "pending"]


def playbook_text(d: dict) -> str:
    head = yaml.safe_dump({"name": d["name"], "description": d["description"], "triggers": d["triggers"],
                           "tool_groups": d["groups"], "written_by": "Nova", "approved": dt.date.today().isoformat()},
                          sort_keys=False, allow_unicode=True, width=1000).strip()
    return f"---\n{head}\n---\n" + "\n".join(f"{i}. {NUMBERED.sub('', s)}" for i, s in enumerate(d["steps"], 1)) + "\n"


def find_draft(name: str) -> dict | None:
    want = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    cands = pending()
    if not cands:
        return None
    if not want or want in ("it", "that", "the playbook", "playbook", "the draft", "last", "latest"):
        return cands[-1]
    m = re.fullmatch(r"#?\s*(\d+)", want)
    if m:
        return next((d for d in cands if d["id"] == int(m.group(1))), None)
    for d in cands:
        if want == d["name"].replace("-", " ") or want in d["name"].replace("-", " ") or words(want) & set(d["key"]):
            return d
    return None


def approve(name: str = "") -> dict | None:
    """Make a pending draft a real playbook (playbooks/mine/<name>.md) and start following it."""
    d = find_draft(name)
    if not d:
        return None
    mine_dir().mkdir(parents=True, exist_ok=True)
    (mine_dir() / f"{d['name']}.md").write_text(playbook_text(d), encoding="utf-8")
    st = state()
    for x in st["drafts"]:
        if x["id"] == d["id"]:
            x["state"] = "approved"
    _save(st)
    from .extensions import load_playbooks
    load_playbooks()
    return d


def drop(name: str = "") -> dict | None:
    """Drop a pending draft. It is remembered, so the same job is never proposed again."""
    d = find_draft(name)
    if not d:
        return None
    st = state()
    for x in st["drafts"]:
        if x["id"] == d["id"]:
            x["state"] = "dropped"
    _save(st)
    return d


def describe(d: dict) -> str:
    return (f"“{d['name']}” (asked {d['times']} times, e.g. \"{d['examples'][0][:70]}\"): "
            + " ".join(f"{i}) {NUMBERED.sub('', s)}" for i, s in enumerate(d["steps"], 1)))


# ── once a week ───────────────────────────────────────────
def weekly(now: dt.datetime | None = None, llm=None) -> str:
    """The weekly self-review plus up to two new playbook drafts — the text Nova sends you."""
    c = cfg()
    if not c.get("enabled", True):
        return ""
    r = review(7, now, llm)
    if not r["facts"]["requests"]:
        return ""
    parts = ["🌱 How I did this week, and how I'd get better\n\n" + r["text"]]
    new = []
    if c.get("draft_playbooks", True):
        for job in r["facts"]["repeats"][:2]:
            d = draft_playbook(job, llm)
            if d:
                new.append(d)
    waiting = pending()
    if waiting:
        parts.append("📘 Playbooks I've drafted for jobs you keep asking for"
                     + (f" ({len(new)} new)" if new else "") + ":\n"
                     + "\n".join(f"- {describe(d)}" for d in waiting[-4:])
                     + "\nSay \"approve the playbook <name>\" and I'll follow it from now on, or \"drop the playbook "
                       "<name>\" and I won't suggest it again.")
    return "\n\n".join(parts)
