"""Undo: Nova keeps a short journal of what she changed, so "undo that" can put it back.

Things that reached someone else (a sent message, an email) or left the PC can't be taken back; those are recorded
too, so she can say so plainly instead of pretending.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

from . import context

KEEP_HOURS = 48
_SCHEMA = ("CREATE TABLE IF NOT EXISTS undo_log(id INTEGER PRIMARY KEY, ts TEXT, session TEXT, label TEXT, kind TEXT, "
           "data TEXT, state TEXT DEFAULT 'open')")


def _db():
    s = context.store
    if s is None:
        return None
    with s.lock:
        s.db.execute(_SCHEMA)
    return s


def record(label: str, kind: str, **data) -> int | None:
    """Note something that was just done. kind 'none' = it can't be undone (data['why'] says why)."""
    s = _db()
    if s is None:
        return None
    try:
        with s.lock:
            cur = s.db.execute("INSERT INTO undo_log(ts, session, label, kind, data) VALUES(?,?,?,?,?)",
                               (dt.datetime.now().isoformat(timespec="seconds"), context.session(), label[:200], kind,
                                json.dumps(data, default=str)))
            s.db.commit()
            return cur.lastrowid
    except Exception as e:
        print(f"[undo] couldn't note '{label}': {e}")
        return None


def recent(limit: int = 8) -> list[dict]:
    s = _db()
    if s is None:
        return []
    since = (dt.datetime.now() - dt.timedelta(hours=KEEP_HOURS)).isoformat(timespec="seconds")
    with s.lock:
        rows = s.db.execute("SELECT * FROM undo_log WHERE state='open' AND ts>=? ORDER BY id DESC LIMIT ?",
                            (since, limit)).fetchall()
    return [{**dict(r), "data": json.loads(r["data"] or "{}")} for r in rows]


def _mark(uid: int, state: str) -> None:
    s = _db()
    with s.lock:
        s.db.execute("UPDATE undo_log SET state=? WHERE id=?", (state, uid))
        s.db.commit()


def undo_last() -> str:
    items = recent(1)
    if not items:
        return "There's nothing recent to undo."
    e = items[0]
    d = e["data"]
    if e["kind"] == "none":
        _mark(e["id"], "fixed")
        return f"I can't undo '{e['label']}' — {d.get('why', 'it has already left my hands')}."
    try:
        out = HANDLERS[e["kind"]](d)
    except KeyError:
        return f"I don't know how to undo '{e['label']}'."
    except Exception as ex:
        return f"I couldn't undo '{e['label']}': {ex}"
    _mark(e["id"], "undone")
    if context.store:
        context.store.log("memory", context.session() or "system", f"↩ Undone: {e['label'][:80]}", turn=0)
    return f"Undone: {e['label']}. {out}".strip()


# ── how each kind is put back ─────────────────────────────
def _file_move(d: dict) -> str:
    src, dst = Path(d["src"]), Path(d["dst"])
    if not dst.exists():
        raise RuntimeError("it isn't where I put it any more")
    if src.exists():
        raise RuntimeError(f"something else is at {src} now")
    src.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dst), str(src))
    return f"It's back at {src}."


def _file_write(d: dict) -> str:
    p = Path(d["path"])
    if d.get("backup") and Path(d["backup"]).exists():
        shutil.copy2(d["backup"], p)
        return "The earlier version is back."
    if d.get("created") and p.exists():
        p.unlink()
        return "The file is gone again."
    raise RuntimeError("there's no earlier version to go back to")


def _file_copy(d: dict) -> str:
    p = Path(d["path"])
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()
    return "The copy is removed."


def _folder(d: dict) -> str:
    p = Path(d["path"])
    if p.is_dir() and not any(p.iterdir()):
        p.rmdir()
        return "The folder is removed."
    raise RuntimeError("the folder isn't empty any more")


def _note_move(d: dict) -> str:
    from . import taxonomy
    new, old = Path(d["new"]), Path(d["old"])
    if not new.exists():
        raise RuntimeError("the note isn't where I moved it any more")
    taxonomy.move_note(new, old)
    taxonomy.write_status_boards()
    return "The note is back where it was."


def _note_delete(d: dict) -> str:
    kept, old = Path(d["kept"]), Path(d["old"])
    if not kept.exists():
        raise RuntimeError("the kept copy is gone")
    old.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(kept), str(old))
    if context.store:
        try:
            context.store.index_note(old)
        except Exception:
            pass
    return "The note is back in the brain."


def _memory(d: dict) -> str:
    s = context.store
    with s.lock:
        s.db.execute("UPDATE memories SET superseded_by=id WHERE id=?", (int(d["id"]),))
        s.db.commit()
    return "I've forgotten it again."


def _placement(d: dict) -> str:
    s = context.store
    with s.lock:
        if d.get("was"):
            s.db.execute("INSERT OR REPLACE INTO placement(item, domain, category) VALUES(?,?,?)",
                         (d["item"], d["was"][0], d["was"][1]))
        else:
            s.db.execute("DELETE FROM placement WHERE item=?", (d["item"],))
        s.db.commit()
    return "It's back where it was."


def _reminder(d: dict) -> str:
    from .skills import system
    with system._rem_lock:
        system._save_rem([r for r in system._load_rem() if r.get("id") != d["id"]])
    return "The reminder is cancelled."


def _expense(d: dict) -> str:
    from . import budget
    budget.delete(int(d["id"]))
    return "The expense is removed from your budget."


def _setting(d: dict) -> str:
    from . import settings
    settings.apply({"values": {d["path"]: d["was"]}})
    return "It's back to how it was."


HANDLERS = {"file_move": _file_move, "file_write": _file_write, "file_copy": _file_copy, "folder": _folder,
            "note_move": _note_move, "note_delete": _note_delete, "memory": _memory, "placement": _placement,
            "reminder": _reminder, "expense": _expense, "setting": _setting}
