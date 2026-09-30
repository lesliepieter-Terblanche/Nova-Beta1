"""Focus mode: one thing now, two next, everything else "later" — no guilt, no overdue piles.

  Now   the one task that matters (with a tiny first step if you asked Nova to break it down)
  Next  the two after it
  Later everything else, including brain-dump captures; nothing here ever turns red
Wins today = tasks done + routine anchors ticked + your check-in. No streaks that can break.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time

from . import context

_SCHEMA = """CREATE TABLE IF NOT EXISTS focus_tasks(
  id INTEGER PRIMARY KEY, text TEXT, status TEXT DEFAULT 'open', pos REAL, parent INTEGER, source TEXT DEFAULT '',
  created TEXT, updated TEXT, started TEXT, done_at TEXT);"""
BREAKDOWN_PROMPT = """Someone with ADHD is stuck on this task: "{task}".
Give the very first step that takes under two minutes and needs no decisions (e.g. "Open the Avaya QBR deck"),
then up to three more small steps. Plain, concrete, kind. JSON only: {{"steps": ["...", "..."]}}"""


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _db():
    s = context.store
    with s.lock:
        s.db.executescript(_SCHEMA)
    return s


def _rows(sql: str, args=()) -> list[dict]:
    s = _db()
    with s.lock:
        return [dict(r) for r in s.db.execute(sql, args)]


def open_tasks() -> list[dict]:
    return _rows("SELECT * FROM focus_tasks WHERE status='open' ORDER BY pos, id")


def later_tasks() -> list[dict]:
    return _rows("SELECT * FROM focus_tasks WHERE status='later' ORDER BY updated DESC LIMIT 60")


def get(tid: int) -> dict | None:
    r = _rows("SELECT * FROM focus_tasks WHERE id=?", (int(tid),))
    return r[0] if r else None


def _max_pos() -> float:
    r = _rows("SELECT MAX(pos) m FROM focus_tasks WHERE status='open'")
    return float(r[0]["m"] or 0)


def add(text: str, where: str = "later", source: str = "", parent: int | None = None) -> dict:
    """where: 'now' (top), 'next' (right after Now), 'end' (bottom of the open list) or 'later' (brain dump)."""
    text = re.sub(r"\s+", " ", text or "").strip()[:300]
    if not text:
        raise ValueError("Nothing to add.")
    s = _db()
    opens = open_tasks()
    if where == "now":
        pos, status = (opens[0]["pos"] - 1) if opens else 1.0, "open"
    elif where == "next":
        pos = ((opens[0]["pos"] + opens[1]["pos"]) / 2) if len(opens) > 1 else (_max_pos() + 1)
        status = "open"
    elif where == "end":
        pos, status = _max_pos() + 1, "open"
    else:
        pos, status = 0, "later"
    with s.lock:
        tid = s.db.execute("INSERT INTO focus_tasks(text,status,pos,parent,source,created,updated) VALUES(?,?,?,?,?,?,?)",
                           (text, status, pos, parent, source, now(), now())).lastrowid
        s.db.commit()
    _mark_started()
    return get(tid)


def brain_dump(text: str) -> list[dict]:
    """Several lines → several 'later' items, instantly (no model involved)."""
    items = [x.strip(" -•*\t") for x in re.split(r"[\n;]+", text or "") if x.strip(" -•*\t")]
    return [add(x, "later", "brain dump") for x in items[:20]]


def set_status(tid: int, status: str) -> dict | None:
    """done | later (not now) | open-next (do it next) | open-now (do it now) | delete"""
    s = _db()
    t = get(tid)
    if not t:
        return None
    with s.lock:
        if status == "delete":
            s.db.execute("DELETE FROM focus_tasks WHERE id=?", (tid,))
        elif status == "done":
            s.db.execute("UPDATE focus_tasks SET status='done', done_at=?, updated=? WHERE id=?", (now(), now(), tid))
            if t["source"].startswith("item:"):
                try:
                    context.store.set_tracking(t["source"][5:], status="done")
                except Exception:
                    pass
        elif status == "later":
            s.db.execute("UPDATE focus_tasks SET status='later', started=NULL, updated=? WHERE id=?", (now(), tid))
        elif status in ("open-next", "open-now"):
            opens = [o for o in open_tasks() if o["id"] != tid]
            if status == "open-now":
                pos = (opens[0]["pos"] - 1) if opens else 1.0
            else:
                pos = ((opens[0]["pos"] + opens[1]["pos"]) / 2) if len(opens) > 1 else (_max_pos() + 1)
            s.db.execute("UPDATE focus_tasks SET status='open', pos=?, updated=? WHERE id=?", (pos, now(), tid))
        s.db.commit()
    if status == "done":
        context.store.log("track", "focus", f"✓ Done: {t['text'][:80]}", "", turn=0)
    _mark_started()
    return get(tid)


def _mark_started() -> None:
    """The Now task's clock starts when it becomes Now (used for the gentle hyperfocus nudge)."""
    opens = open_tasks()
    if opens and not opens[0]["started"]:
        s = context.store
        with s.lock:
            s.db.execute("UPDATE focus_tasks SET started=? WHERE id=?", (now(), opens[0]["id"]))
            s.db.commit()


def breakdown(tid: int) -> list[dict]:
    """Turn a stuck task into a two-minute first step (+ a few small ones) placed right before it."""
    t = get(tid)
    if not t:
        return []
    steps = []
    try:
        raw = context.llm.complete(BREAKDOWN_PROMPT.format(task=t["text"]), prefer_smart=True, temperature=0.3)
        m = re.search(r"\{.*\}", raw or "", re.S)
        steps = [str(x).strip() for x in (json.loads(m.group(0)).get("steps") or [])] if m else []
    except Exception as e:
        print(f"[focus] breakdown failed: {e}")
    steps = [x for x in steps if x][:4] or [f"Open whatever you need for: {t['text'][:80]}"]
    s = _db()
    before = t["pos"]
    opens = [o for o in open_tasks() if o["pos"] < before]
    lo = opens[-1]["pos"] if opens else before - 1
    made = []
    with s.lock:
        for i, x in enumerate(steps):
            pos = lo + (before - lo) * (i + 1) / (len(steps) + 1)
            made.append(s.db.execute("INSERT INTO focus_tasks(text,status,pos,parent,source,created,updated) "
                                     "VALUES(?,?,?,?,?,?,?)", (x, "open", pos, tid, "breakdown", now(), now())).lastrowid)
        if t["status"] != "open":
            s.db.execute("UPDATE focus_tasks SET status='open', pos=? WHERE id=?", (before, tid))
        s.db.execute("UPDATE focus_tasks SET started=NULL WHERE id=?", (tid,))
        s.db.commit()
    _mark_started()
    return [get(i) for i in made]


def from_brain_items() -> list[dict]:
    """Things you marked 'in progress' / 'to do' in the brain, offered as focus candidates."""
    s = context.store
    have = {t["source"] for t in _rows("SELECT source FROM focus_tasks WHERE status IN ('open','later')")}
    out = []
    for r in s.tracked():
        if r["status"] in ("doing", "todo") and f"item:{r['item']}" not in have:
            out.append({"item": r["item"], "status": r["status"], "note": r["note"]})
    return out[:12]


def wins_today() -> dict:
    today = dt.date.today().isoformat()
    done = _rows("SELECT text FROM focus_tasks WHERE status='done' AND done_at>=?", (today,))
    return {"tasks": len(done), "titles": [d["text"] for d in done][-8:]}


# ── next meeting (Google Calendar, if connected) ──────────
_cal = {"t": 0.0, "v": None}


def next_event() -> dict | None:
    if not (context.cfg and (context.cfg.get("google") or {}).get("enabled")):
        return None
    if time.time() - _cal["t"] < 300:
        return _cal["v"]
    val = None
    try:
        from .skills.google_ws import calendar_events
        evs = calendar_events("now", 2)
        nowdt = dt.datetime.now().astimezone()
        for e in evs if isinstance(evs, list) else []:
            if "T" not in e["start"]:
                continue                          # all-day
            st = dt.datetime.fromisoformat(e["start"].replace("Z", "+00:00"))
            if st > nowdt:
                val = {"title": e["title"], "start": st.isoformat(), "where": e.get("where", "")}
                break
    except Exception as e:
        print(f"[focus] calendar: {e}")
    _cal.update(t=time.time(), v=val)
    return val


def state() -> dict:
    opens = open_tasks()
    parents = {t["id"]: t for t in _rows("SELECT id, text FROM focus_tasks WHERE id IN (SELECT DISTINCT parent FROM "
                                         "focus_tasks WHERE parent IS NOT NULL)")}

    def pub(t):
        return {"id": t["id"], "text": t["text"], "started": t["started"], "source": t["source"],
                "for": parents.get(t["parent"], {}).get("text") if t["parent"] else None}
    return {"now": pub(opens[0]) if opens else None, "next": [pub(t) for t in opens[1:3]],
            "more": max(0, len(opens) - 3), "later": [pub(t) for t in later_tasks()],
            "from_brain": from_brain_items(), "wins": wins_today(), "next_event": next_event()}
