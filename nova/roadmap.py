"""Nova's upgrade roadmap (roadmap.yaml) → Projects on the dashboard.

Each roadmap item becomes a project memory with a tracking status (To do / In progress / Done).
The status is only changed when the roadmap's status changes, so anything you set by hand stays.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
_SCHEMA = "CREATE TABLE IF NOT EXISTS roadmap_state(id TEXT PRIMARY KEY, memory_id INTEGER, status TEXT)"


def load(path: Path | None = None) -> list[dict]:
    p = path or ROOT / "roadmap.yaml"
    if not p.exists():
        return []
    return [i for i in (yaml.safe_load(p.read_text(encoding="utf-8")) or []) if isinstance(i, dict) and i.get("id")]


def sync(store, path: Path | None = None) -> dict:
    """Create/update the roadmap projects. Returns counts for the log."""
    items = load(path)
    out = {"added": 0, "moved": 0}
    if not items:
        return out
    with store.lock:
        store.db.execute(_SCHEMA)
        state = {r["id"]: dict(r) for r in store.db.execute("SELECT * FROM roadmap_state")}
    for it in items:
        rid, title = str(it["id"]), str(it.get("title") or it["id"]).strip()
        status = str(it.get("status") or "todo").strip()
        status = status if status in ("todo", "doing", "waiting", "done") else "todo"
        st = state.get(rid)
        mid = st["memory_id"] if st else None
        with store.lock:
            row = store.db.execute("SELECT id, text, superseded_by FROM memories WHERE id=?", (mid,)).fetchone() \
                if mid else None
            while row and row["superseded_by"]:                 # follow corrections to the live version
                row = store.db.execute("SELECT id, text, superseded_by FROM memories WHERE id=?",
                                       (row["superseded_by"],)).fetchone()
        if row is None:
            now = _now()
            with store.lock:
                mid = store.db.execute(
                    "INSERT INTO memories(kind,text,created,updated,source,importance) VALUES('project',?,?,?,?,2)",
                    (title, now, now, f"roadmap:{rid}")).lastrowid
                store.db.commit()
            store.log("memory", "system", f"Roadmap: {title[:80]}", title, f"memory:{mid}", turn=0)
            out["added"] += 1
        else:
            mid = row["id"]
            if row["text"] != title:
                with store.lock:
                    store.db.execute("UPDATE memories SET text=?, updated=? WHERE id=?", (title, _now(), mid))
                    store.db.commit()
        if not st or st["status"] != status or st["memory_id"] != mid:
            cur = store.get_tracking(f"memory:{mid}")
            if not st or st["status"] != status:
                pin = True if status == "doing" else (False if status == "done" and cur.get("pinned") else None)
                store.set_tracking(f"memory:{mid}", status=status, pinned=pin)
                out["moved"] += bool(st)
            with store.lock:
                store.db.execute("INSERT OR REPLACE INTO roadmap_state(id, memory_id, status) VALUES(?,?,?)",
                                 (rid, mid, status))
                store.db.commit()
    return out


def _now() -> str:
    import datetime as dt
    return dt.datetime.now().isoformat(timespec="seconds")
