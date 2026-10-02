"""Learning from corrections. When you move something Nova misfiled, change a slip's category, or tell her how you
want things done, she keeps the lesson and applies it next time.

  filing    — "a note called X belongs in <domain>/<category>" (from moves you make on the dashboard)
  merchant  — "slips from <shop> are <category>" (from category changes in the budget)
  how-to    — your standing preferences ("shorter emails", "always rands"), kept as preference memories
"""
from __future__ import annotations

import datetime as dt
import re

from . import context

_SCHEMA = ("CREATE TABLE IF NOT EXISTS lessons(id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, key TEXT, value TEXT, "
           "n INTEGER DEFAULT 1)")
STOP = {"the", "and", "for", "with", "from", "this", "that", "note", "notes", "file", "document", "copy", "new", "final",
        "draft", "meeting", "about", "your", "have", "will", "2024", "2025", "2026", "2027"}


def _db():
    s = context.store
    if s is None:
        return None
    with s.lock:
        s.db.execute(_SCHEMA)
    return s


def words(title: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", title.lower()) if len(w) >= 4 and w not in STOP and not w.isdigit()}


def learn(kind: str, key: str, value: str) -> None:
    s = _db()
    key = key.strip()
    if s is None or not key or not value:
        return
    now = dt.datetime.now().isoformat(timespec="seconds")
    with s.lock:
        row = s.db.execute("SELECT id, value, n FROM lessons WHERE kind=? AND lower(key)=lower(?)", (kind, key)).fetchone()
        if row and row["value"] == value:
            s.db.execute("UPDATE lessons SET n=n+1, ts=? WHERE id=?", (now, row["id"]))
        elif row:                                    # you changed your mind: the newest correction wins
            s.db.execute("UPDATE lessons SET value=?, n=1, ts=? WHERE id=?", (value, now, row["id"]))
        else:
            s.db.execute("INSERT INTO lessons(ts, kind, key, value) VALUES(?,?,?,?)", (now, kind, key, value))
        s.db.commit()
    try:
        s.log("memory", "system", f"🎓 Learned: {key[:50]} → {value.replace('|', ' / ')[:60]}", turn=0)
    except Exception:
        pass


def all_of(kind: str = "") -> list[dict]:
    s = _db()
    if s is None:
        return []
    with s.lock:
        rows = s.db.execute("SELECT * FROM lessons" + (" WHERE kind=?" if kind else "") + " ORDER BY ts DESC",
                            (kind,) if kind else ()).fetchall()
    return [dict(r) for r in rows]


def forget(which: str) -> int:
    s = _db()
    if s is None:
        return 0
    w = which.strip().lower()
    with s.lock:
        cur = s.db.execute("DELETE FROM lessons WHERE CAST(id AS TEXT)=? OR lower(key) LIKE ?", (w, f"%{w}%"))
        s.db.commit()
        return cur.rowcount


# ── filing ────────────────────────────────────────────────
def filing(title: str) -> tuple[str, str] | None:
    """(domain, category) you taught for a note like this one — None when nothing you taught applies."""
    mine = words(title)
    if not mine:
        return None
    best, score = None, 0.0
    votes: dict[str, float] = {}
    for les in all_of("filing"):
        theirs = words(les["key"])
        if not theirs:
            continue
        overlap = len(mine & theirs) / len(mine | theirs)
        if overlap > score:
            best, score = les["value"], overlap
        for _ in mine & theirs:
            votes[les["value"]] = votes.get(les["value"], 0) + 1
    if best and score >= 0.6:
        return tuple(best.split("|", 1))             # practically the same title as one you corrected
    top = sorted(votes.items(), key=lambda kv: -kv[1])
    if top and top[0][1] >= 2 and (len(top) == 1 or top[0][1] >= 2 * top[1][1]):
        return tuple(top[0][0].split("|", 1))        # the same telling words, corrected the same way at least twice
    return None


def filing_examples(limit: int = 10) -> str:
    """Recent corrections, for the model that files notes."""
    rows = all_of("filing")[:limit]
    return "\n".join(f"- \"{r['key']}\" belongs in {r['value'].replace('|', ' / ')}" for r in rows)


# ── budget ────────────────────────────────────────────────
def merchant(name: str) -> str:
    n = name.strip().lower()
    for les in all_of("merchant"):
        k = les["key"].lower()
        if k == n or (len(k) >= 4 and (k in n or n in k)):
            return les["value"]
    return ""


# ── how you like things done ──────────────────────────────
def preferences(limit: int = 10) -> list[str]:
    s = context.store
    if s is None:
        return []
    with s.lock:
        rows = s.db.execute("SELECT text FROM memories WHERE kind='preference' AND superseded_by IS NULL "
                            "ORDER BY importance DESC, id DESC LIMIT ?", (limit,)).fetchall()
    return [r["text"] for r in rows]
