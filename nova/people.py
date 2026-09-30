"""People cards: everyone you deal with — partners, resellers, vendor contacts, family — on one card each.

A card holds name, company, role, email, phone and short notes. Cards are made and kept up to date from:
  - what you tell Nova ("Sam Dlamini at Axiz is the Juniper BDM"),
  - meeting notes (the People list), business cards and anything forwarded on Telegram,
  - the nightly dream (catches up on every person-memory it hasn't turned into a card yet).
Everything that mentions the person (memories, notes, creations, requests) is linked to their card in the 2nd brain.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading

from . import context

_SCHEMA = """CREATE TABLE IF NOT EXISTS people(
  id INTEGER PRIMARY KEY, name TEXT, aliases TEXT DEFAULT '[]', company TEXT DEFAULT '', role TEXT DEFAULT '',
  email TEXT DEFAULT '', phone TEXT DEFAULT '', notes TEXT DEFAULT '', created TEXT, updated TEXT,
  last_contact TEXT, source TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS people_seen(item TEXT PRIMARY KEY, ts TEXT);"""
FIELDS = ("company", "role", "email", "phone")
DEAL_WORDS = re.compile(r"\b(deal|deals|quote|quotation|pricing|price|opportunit\w*|pipeline|order|PO|renewal|tender|RFP|"
                        r"rfq|forecast|registration|budget|R\s?\d[\d\s,.]*[mk]?)\b", re.I)
_lock = threading.Lock()
ON_CHANGE: list = []            # callbacks when cards are added / renamed (the people skill refreshes its trigger words)


def _changed() -> None:
    for cb in list(ON_CHANGE):
        try:
            cb()
        except Exception:
            pass

EXTRACT_PROMPT = """List the real, named people in this text (not {owner} himself, not companies, not famous people
who are only mentioned in passing). For each give what the text says about them.

Text:
{text}

Reply with JSON only: {{"people": [{{"name": "full name as written", "company": "", "role": "", "email": "",
"phone": "", "fact": "one short sentence about them from the text, or empty"}}]}}
Return {{"people": []}} if there are none."""


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _db():
    s = context.store
    with s.lock:
        s.db.executescript(_SCHEMA)
    return s


def _row(r) -> dict:
    d = dict(r)
    d["aliases"] = json.loads(d.get("aliases") or "[]")
    return d


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'\-]", " ", name or "")).strip()


def all_people() -> list[dict]:
    s = _db()
    with s.lock:
        return [_row(r) for r in s.db.execute("SELECT * FROM people ORDER BY name COLLATE NOCASE")]


def get(pid: int) -> dict | None:
    s = _db()
    with s.lock:
        r = s.db.execute("SELECT * FROM people WHERE id=?", (int(pid),)).fetchone()
    return _row(r) if r else None


def find(name: str, company: str = "") -> dict | None:
    """Exact name or alias (any case); a lone first name only when exactly one card fits (and the company agrees)."""
    name = _norm(name).lower()
    if not name:
        return None
    people = all_people()
    for p in people:
        if name == p["name"].lower():
            return p
    first = [p for p in people if p["name"].lower().split()[0] == name.split()[0]]
    by_alias = [p for p in people if name in [a.lower() for a in p["aliases"]]]
    if len(by_alias) == 1 and (len(name.split()) > 1 or len(first) <= 1):
        return by_alias[0]                     # a lone first name alias only counts while it's unambiguous
    if len(name.split()) == 1 and len(first) == 1:
        return first[0]
    if len(name.split()) == 1 and company:
        same = [p for p in first if p["company"] and p["company"].lower() in company.lower()]
        if len(same) == 1:
            return same[0]
    if len(name.split()) > 1:            # "Sam" card + now we learn "Sam Dlamini" → same person if company agrees
        solo = [p for p in first if len(p["name"].split()) == 1
                and (not company or not p["company"] or p["company"].lower() == company.lower())]
        if len(solo) == 1:
            return solo[0]
    return None


def upsert(name: str, company: str = "", role: str = "", email: str = "", phone: str = "", note: str = "",
           source: str = "", contact: str | None = None) -> tuple[int, bool]:
    """Create or fill in a card. Returns (id, created). Never overwrites what's there with blanks."""
    name = _norm(name)
    if len(name) < 2:
        raise ValueError("A person needs a name.")
    vals = {"company": company.strip(), "role": role.strip(), "email": email.strip().lower(), "phone": phone.strip()}
    s = _db()
    with _lock:
        p = find(name, company)
        ts = now()
        with s.lock:
            if not p:
                pid = s.db.execute("INSERT INTO people(name,company,role,email,phone,notes,created,updated,last_contact,"
                                   "source) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                   (name, *vals.values(), f"- {note.strip()}" if note.strip() else "", ts, ts, contact,
                                    source)).lastrowid
                s.db.commit()
                created = True
            else:
                pid, created = p["id"], False
                upd = {k: v for k, v in vals.items() if v and not p[k]}
                if vals["role"] and p["role"] and vals["role"].lower() != p["role"].lower():
                    upd["role"] = vals["role"]                       # roles change — newest wins
                notes = p["notes"] or ""
                if note.strip() and note.strip().lower() not in notes.lower():
                    notes = (notes + "\n" if notes else "") + f"- {note.strip()}"
                aliases = p["aliases"]
                if len(name.split()) > len(p["name"].split()):      # we learned the full name
                    aliases = sorted(set(aliases + [p["name"]]))
                    upd["name"] = name
                elif name.lower() != p["name"].lower() and name.lower() not in [a.lower() for a in aliases]:
                    aliases = sorted(set(aliases + [name]))
                upd.update(notes=notes, aliases=json.dumps(aliases), updated=ts)
                if contact and (not p["last_contact"] or contact > p["last_contact"]):
                    upd["last_contact"] = contact
                sets = ", ".join(f"{k}=?" for k in upd)
                s.db.execute(f"UPDATE people SET {sets} WHERE id=?", (*upd.values(), pid))
                s.db.commit()
    if created:
        _changed()
        s.log("memory", "system", f"👤 New person: {name}" + (f" ({vals['company']})" if vals["company"] else ""),
              note, f"person:{pid}", turn=0)
    return pid, created


def update(pid: int, **fields) -> dict:
    s = _db()
    allowed = {k: str(v).strip() for k, v in fields.items() if k in (*FIELDS, "name", "notes") and v is not None}
    if "aliases" in fields and fields["aliases"] is not None:
        al = fields["aliases"]
        al = [a.strip() for a in (al.split(",") if isinstance(al, str) else al) if a.strip()]
        allowed["aliases"] = json.dumps(al)
    if not allowed:
        return get(pid) or {}
    allowed["updated"] = now()
    with s.lock:
        s.db.execute(f"UPDATE people SET {', '.join(f'{k}=?' for k in allowed)} WHERE id=?", (*allowed.values(), pid))
        s.db.commit()
    s.log("track", "dashboard", "Person card updated", "", f"person:{pid}", turn=0)
    _changed()
    return get(pid) or {}


def merge(keep: int, drop: int) -> dict:
    a, b = get(keep), get(drop)
    if not (a and b):
        raise ValueError("Both people must exist.")
    upd = {k: a[k] or b[k] for k in FIELDS}
    upd["notes"] = "\n".join(x for x in (a["notes"], b["notes"]) if x)
    upd["aliases"] = sorted(set(a["aliases"] + b["aliases"] + [b["name"]]) - {a["name"]})
    update(keep, **upd)
    s = _db()
    with s.lock:
        s.db.execute("DELETE FROM people WHERE id=?", (drop,))
        s.db.execute("UPDATE OR IGNORE tracking SET item=? WHERE item=?", (f"person:{keep}", f"person:{drop}"))
        s.db.commit()
    return get(keep)


def delete(pid: int) -> None:
    s = _db()
    with s.lock:
        s.db.execute("DELETE FROM people WHERE id=?", (pid,))
        s.db.commit()
    _changed()


# ── learning people from text ─────────────────────────────
def from_line(line: str, source: str = "") -> int | None:
    """Meeting-note style 'Sam Dlamini – BDM, Axiz' → card (no model needed)."""
    parts = re.split(r"\s+[–—-]\s+|\s*\|\s*", line.strip(), maxsplit=1)
    name = _norm(parts[0])
    if not name or len(name) < 2 or not name[0].isupper():
        return None
    rest = parts[1] if len(parts) > 1 else ""
    company = role = ""
    if rest:
        bits = [b.strip() for b in re.split(r",|/| at | @ ", rest) if b.strip()]
        if len(bits) >= 2:
            role, company = bits[0], bits[-1]
        elif bits:
            if bits[0][:1].isupper() and len(bits[0].split()) <= 3 and not re.search(r"(?i)manager|director|head|"
                                                                                      r"engineer|bdm|ceo|cto|sales", bits[0]):
                company = bits[0]
            else:
                role = bits[0]
    return upsert(name, company=company, role=role, source=source)[0]


def absorb(text: str, source: str = "", llm=None, contact: str | None = None) -> list[int]:
    """Find people in any text (a memory, a note, a forwarded message) and create / update their cards."""
    text = (text or "").strip()
    if len(text) < 6 or (not re.search(r"\b[A-Z][a-z]{2,}", text[1:]) and "@" not in text):
        return []
    llm = llm or context.llm
    if not llm:
        return []
    owner = (context.cfg.assistant.owner if context.cfg else "") or "the user"
    try:
        raw = llm.complete(EXTRACT_PROMPT.format(owner=owner, text=text[:6000]),
                           prefer_smart=bool(getattr(llm, "smart", None)), temperature=0.1)
        m = re.search(r"\{.*\}", raw or "", re.S)
        found = json.loads(m.group(0)).get("people", []) if m else []
    except Exception as e:
        print(f"[people] couldn't read people from text: {e}")
        return []
    ids = []
    owner_words = {w.lower() for w in owner.split()}
    for p in found[:12]:
        name = _norm(str(p.get("name", "")))
        if len(name) < 2 or {w.lower() for w in name.split()} <= owner_words:
            continue
        try:
            pid, _ = upsert(name, str(p.get("company") or ""), str(p.get("role") or ""), str(p.get("email") or ""),
                            str(p.get("phone") or ""), str(p.get("fact") or ""), source=source, contact=contact)
            ids.append(pid)
        except ValueError:
            continue
    return ids


def absorb_in_background(text: str, source: str = "") -> None:
    threading.Thread(target=absorb, args=(text, source), daemon=True, name="people").start()


def catch_up(limit: int = 60) -> int:
    """Turn person-memories and meeting notes that haven't been read yet into cards (nightly dream step)."""
    s = _db()
    with s.lock:
        seen = {r["item"] for r in s.db.execute("SELECT item FROM people_seen")}
        mems = s.db.execute("SELECT id, text, created FROM memories WHERE superseded_by IS NULL AND kind='person' "
                            "ORDER BY id DESC LIMIT 400").fetchall()
    todo = [m for m in mems if f"memory:{m['id']}" not in seen][:limit]
    n = 0
    for i in range(0, len(todo), 10):                     # ten memories per model call
        batch = todo[i:i + 10]
        n += len(absorb("\n".join(f"- {m['text']}" for m in batch), source="memories"))
        with s.lock:
            s.db.executemany("INSERT OR IGNORE INTO people_seen(item, ts) VALUES(?,?)",
                             [(f"memory:{m['id']}", now()) for m in batch])
            s.db.commit()
    return n


# ── what mentions a person ────────────────────────────────
def patterns(p: dict) -> re.Pattern | None:
    names = [p["name"]] + [a for a in p["aliases"] if len(a) >= 3]
    if p["email"]:
        names.append(p["email"])
    names = [n for n in names if n]
    if not names:
        return None
    return re.compile(r"(?<![\w@.])(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
                      + r")(?![\w@])", re.I)


def mentions(p: dict, limit: int = 200) -> list[dict]:
    """[{id, title, kind, ts, deal}] newest first — memories, notes, creations and requests that name this person."""
    pat = patterns(p)
    if not pat:
        return []
    s = context.store
    out = []
    with s.lock:
        for m in s.db.execute("SELECT id, kind, text, created FROM memories WHERE superseded_by IS NULL"):
            if pat.search(m["text"]):
                out.append({"id": f"memory:{m['id']}", "title": m["text"], "kind": "memory", "ts": m["created"]})
        for a in s.db.execute("SELECT id, kind, title, detail, ts FROM artifacts WHERE kind != 'note'"):
            if pat.search(f"{a['title']} {a['detail'] or ''}"):
                out.append({"id": f"artifact:{a['id']}", "title": a["title"], "kind": "creation", "ts": a["ts"]})
        for c in s.db.execute("SELECT path, GROUP_CONCAT(text, ' ') t, MAX(mtime) m FROM chunks GROUP BY path"):
            if pat.search(c["t"] or ""):
                from pathlib import Path
                out.append({"id": f"note:{c['path']}", "title": Path(c["path"]).stem, "kind": "note",
                            "ts": dt.datetime.fromtimestamp(c["m"] or 0).isoformat(timespec="seconds"),
                            "text": c["t"][:4000]})
        for t in s.db.execute("SELECT id, title, detail, ts FROM activity WHERE kind='user' AND turn=id "
                              "ORDER BY id DESC LIMIT 2000"):
            if pat.search(t["detail"] or t["title"] or ""):
                out.append({"id": f"turn:{t['id']}", "title": t["title"], "kind": "action", "ts": t["ts"]})
    for o in out:
        o["deal"] = bool(DEAL_WORDS.search(o.pop("text", None) or o["title"] or ""))
    out.sort(key=lambda o: o["ts"] or "", reverse=True)
    return out[:limit]


def card(pid: int) -> dict | None:
    p = get(pid)
    if not p:
        return None
    ms = mentions(p)
    last = max([x for x in [p["last_contact"]] + [m["ts"] for m in ms[:1]] if x] or [""]) or None
    return {**p, "mentions": ms, "last_seen": last}


def summary(p: dict, ms: list[dict] | None = None) -> str:
    ms = mentions(p) if ms is None else ms
    bits = [p["name"]]
    if p["role"] or p["company"]:
        bits.append(", ".join(x for x in (p["role"], p["company"]) if x))
    line = " — ".join(bits)
    extra = []
    if p["email"]:
        extra.append(p["email"])
    if p["phone"]:
        extra.append(p["phone"])
    if extra:
        line += f" ({' · '.join(extra)})"
    out = [line + "."]
    if p["notes"]:
        out.append("Notes: " + " ".join(n.lstrip("- ").strip() for n in p["notes"].splitlines() if n.strip())[:600])
    facts = [m for m in ms if m["kind"] == "memory"][:6]
    if facts:
        out.append("What I know: " + " | ".join(m["title"][:160] for m in facts))
    deals = [m for m in ms if m["deal"]][:5]
    if deals:
        out.append("Deals & money: " + " | ".join(m["title"][:120] for m in deals))
    if ms:
        out.append(f"Mentioned {len(ms)} time{'s' if len(ms) != 1 else ''}; last on {ms[0]['ts'][:10]}.")
    return "\n".join(out)
