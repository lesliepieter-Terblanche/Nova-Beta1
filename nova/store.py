"""Nova's permanent memory.

Everything lives in one SQLite file (data/nova.db):
  memories   – durable facts Nova has learned (never deleted; corrections supersede)
  chunks     – searchable pieces of the 2nd-brain notes
  artifacts  – everything Nova has made or touched (files, sites, videos, emails…)
  activity   – a log of every request, tool call and reply (grouped into "turns":
               one turn = one request and everything Nova did for it)
  uses       – which memories / notes were recalled in which turn
  tracking   – your own status, pin and note on any item (dashboard → Track)

Semantic search uses local embeddings from Ollama (nomic-embed-text) and falls
back to keyword matching when Ollama isn't available.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import threading
from pathlib import Path

import httpx
import numpy as np

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories(
  id INTEGER PRIMARY KEY, kind TEXT, text TEXT, created TEXT, updated TEXT,
  source TEXT, importance INTEGER DEFAULT 2, uses INTEGER DEFAULT 0,
  superseded_by INTEGER, embedding BLOB);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY, path TEXT, idx INTEGER, text TEXT, mtime REAL, embedding BLOB);
CREATE TABLE IF NOT EXISTS artifacts(
  id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, title TEXT, location TEXT, detail TEXT, embedding BLOB);
CREATE TABLE IF NOT EXISTS activity(
  id INTEGER PRIMARY KEY, ts TEXT, session TEXT, kind TEXT, title TEXT, detail TEXT, ref TEXT);
CREATE INDEX IF NOT EXISTS ix_chunks_path ON chunks(path);
CREATE TABLE IF NOT EXISTS uses(item TEXT, turn INTEGER, ts TEXT);
CREATE INDEX IF NOT EXISTS ix_uses_item ON uses(item);
CREATE TABLE IF NOT EXISTS tracking(
  item TEXT PRIMARY KEY, status TEXT DEFAULT '', pinned INTEGER DEFAULT 0, note TEXT DEFAULT '', updated TEXT);
"""

# columns added after v1.4 — added to existing databases on start-up
_MIGRATIONS = {
    "activity": {"turn": "INTEGER", "ms": "INTEGER", "status": "TEXT DEFAULT ''"},
    "memories": {"turn": "INTEGER"},
    "artifacts": {"turn": "INTEGER"},
}
TRACK_STATUSES = ("", "todo", "doing", "waiting", "done")

MEMORY_KINDS = ["fact", "preference", "person", "project", "decision", "goal", "routine", "event"]


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path, ollama_url: str = "http://localhost:11434", embed_model: str = "nomic-embed-text"):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")      # lets the MCP server read while Nova runs
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(_SCHEMA)
        for table, cols in _MIGRATIONS.items():
            have = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols.items():
                if col not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_activity_turn ON activity(turn)")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_activity_ref ON activity(ref)")
        self.db.commit()
        self.lock = threading.RLock()
        self.current_turn: int | None = None   # the request Nova is working on right now
        self.ollama_url = ollama_url.rstrip("/").removesuffix("/v1")
        self.embed_model = embed_model
        self.keep_alive = "24h"            # keep the embedding model loaded (fast recall)
        self._embed_ok = True
        self.listeners: list = []          # callbacks for live dashboard updates
        self.status = "idle"               # idle | listening | thinking | speaking

    # ── embeddings ────────────────────────────────────────
    def embed(self, texts: list[str]) -> list[np.ndarray | None]:
        if not texts or not self._embed_ok:
            return [None] * len(texts)
        try:
            r = httpx.post(f"{self.ollama_url}/api/embed",
                           json={"model": self.embed_model, "input": [t[:4000] for t in texts],
                                 "keep_alive": self.keep_alive}, timeout=60)
            r.raise_for_status()
            return [np.asarray(v, dtype=np.float32) for v in r.json()["embeddings"]]
        except Exception as e:
            print(f"[memory] embeddings unavailable ({e}); using keyword search")
            self._embed_ok = False
            t = threading.Timer(120, self._reenable)
            t.daemon = True
            t.start()
            return [None] * len(texts)

    def _reenable(self):
        self._embed_ok = True

    @staticmethod
    def _blob(v):
        return None if v is None else v.astype(np.float32).tobytes()

    @staticmethod
    def _vec(b):
        return None if b is None else np.frombuffer(b, dtype=np.float32)

    @staticmethod
    def _cos(a, b) -> float:
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

    @staticmethod
    def _kw(query: str, text: str) -> float:
        q = set(re.findall(r"\w{3,}", query.lower()))
        if not q:
            return 0.0
        t = set(re.findall(r"\w{3,}", text.lower()))
        return len(q & t) / len(q)

    def _rank(self, query: str, rows, text_key="text", k=5, min_score=0.3):
        qv = self.embed([query])[0]
        scored = []
        for r in rows:
            v = self._vec(r["embedding"])
            score = self._cos(qv, v) if (qv is not None and v is not None and len(v) == len(qv)) else self._kw(query, r[text_key])
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [(s, r) for s, r in scored[:k] if s >= min_score]

    # ── memories ──────────────────────────────────────────
    def add_memory(self, text: str, kind: str = "fact", source: str = "conversation", importance: int = 2,
                   turn: int | None = None) -> str:
        text = text.strip()
        if not text:
            return "Nothing to remember."
        kind = kind if kind in MEMORY_KINDS else "fact"
        vec = self.embed([text])[0]
        with self.lock:
            live = self.db.execute("SELECT * FROM memories WHERE superseded_by IS NULL").fetchall()
            best, best_row = 0.0, None
            for r in live:
                v = self._vec(r["embedding"])
                s = self._cos(vec, v) if (vec is not None and v is not None and len(v) == len(vec)) else (
                    1.0 if r["text"].lower() == text.lower() else 0.0)
                if s > best:
                    best, best_row = s, r
            if best >= 0.93:           # already known -> reinforce
                self.db.execute("UPDATE memories SET uses=uses+1, updated=?, importance=MAX(importance,?) WHERE id=?",
                                (now(), importance, best_row["id"]))
                self.db.commit()
                self.note_use(f"memory:{best_row['id']}", turn)
                return f"Already knew that (memory #{best_row['id']})."
            turn = turn if turn is not None else self.current_turn
            cur = self.db.execute(
                "INSERT INTO memories(kind,text,created,updated,source,importance,embedding,turn) VALUES(?,?,?,?,?,?,?,?)",
                (kind, text, now(), now(), source, importance, self._blob(vec), turn))
            new_id = cur.lastrowid
            self.db.commit()
        self.log("memory", "system", f"Learned: {text[:80]}", text, f"memory:{new_id}", turn=turn)
        return f"Remembered (memory #{new_id})."

    def supersede(self, old_id: int, new_text: str, kind: str | None = None, turn: int | None = None) -> str:
        """Correct a memory. The old one is kept for history, marked as replaced."""
        with self.lock:
            old = self.db.execute("SELECT * FROM memories WHERE id=?", (old_id,)).fetchone()
            if not old:
                return f"No memory #{old_id}."
        vec = self.embed([new_text])[0]
        turn = turn if turn is not None else self.current_turn
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO memories(kind,text,created,updated,source,importance,embedding,turn) VALUES(?,?,?,?,?,?,?,?)",
                (kind or old["kind"], new_text, now(), now(), "correction", old["importance"], self._blob(vec), turn))
            self.db.execute("UPDATE memories SET superseded_by=? WHERE id=?", (cur.lastrowid, old_id))
            # your tracking status / pin / note follows the memory to its new version
            self.db.execute("UPDATE OR IGNORE tracking SET item=? WHERE item=?", (f"memory:{cur.lastrowid}", f"memory:{old_id}"))
            self.db.commit()
        self.log("memory", "system", f"Updated memory #{old_id}", new_text, f"memory:{cur.lastrowid}", turn=turn)
        return f"Updated. Memory #{old_id} now superseded by #{cur.lastrowid}."

    def recall(self, query: str, k: int = 6, min_score: float = 0.45, track: bool = False):
        with self.lock:
            rows = self.db.execute("SELECT * FROM memories WHERE superseded_by IS NULL").fetchall()
        hits = self._rank(query, rows, k=k, min_score=min_score)
        if hits:
            with self.lock:
                self.db.executemany("UPDATE memories SET uses=uses+1 WHERE id=?", [(r["id"],) for _, r in hits])
                self.db.commit()
            if track:
                for _, r in hits:
                    self.note_use(f"memory:{r['id']}")
        return hits

    def core_memories(self, limit: int = 12):
        """Most important + most used memories — always in Nova's head."""
        with self.lock:
            return self.db.execute(
                "SELECT * FROM memories WHERE superseded_by IS NULL ORDER BY importance DESC, uses DESC, id DESC LIMIT ?",
                (limit,)).fetchall()

    def context_for(self, query: str) -> str:
        seen, lines = set(), []
        for r in self.core_memories():
            seen.add(r["id"])
            lines.append(f"- [{r['kind']}] {r['text']}")
        for _, r in self.recall(query, track=True):
            if r["id"] not in seen:
                lines.append(f"- [{r['kind']}] {r['text']}")
        for _, c in self.search_notes(query, k=2, min_score=0.55):
            self.note_use(f"note:{c['path']}")
            lines.append(f"- [note {Path(c['path']).stem}] {c['text'][:300]}")
        return "\n".join(lines)

    # ── notes (2nd brain vault) ───────────────────────────
    def index_note(self, path: Path) -> None:
        path = Path(path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        chunks, buf = [], ""
        for p in parts:
            if len(buf) + len(p) > 1200 and buf:
                chunks.append(buf)
                buf = ""
            buf += p + "\n\n"
        if buf.strip():
            chunks.append(buf.strip())
        vecs = self.embed(chunks) if chunks else []
        with self.lock:
            self.db.execute("DELETE FROM chunks WHERE path=?", (str(path),))
            self.db.executemany("INSERT INTO chunks(path,idx,text,mtime,embedding) VALUES(?,?,?,?,?)",
                                [(str(path), i, c, path.stat().st_mtime, self._blob(v)) for i, (c, v) in enumerate(zip(chunks, vecs))])
            self.db.commit()

    def sync_vault(self, vault: Path) -> int:
        vault.mkdir(parents=True, exist_ok=True)
        with self.lock:
            known = {r["path"]: r["m"] for r in self.db.execute("SELECT path, MAX(mtime) m FROM chunks GROUP BY path")}
        n = 0
        present = set()
        for f in vault.rglob("*.md"):
            present.add(str(f))
            if known.get(str(f)) != f.stat().st_mtime:
                self.index_note(f)
                n += 1
        with self.lock:   # note files removed by the user -> drop from search index only
            for p in set(known) - present:
                self.db.execute("DELETE FROM chunks WHERE path=?", (p,))
            self.db.commit()
        return n

    def search_notes(self, query: str, k: int = 5, min_score: float = 0.35):
        with self.lock:
            rows = self.db.execute("SELECT * FROM chunks").fetchall()
        return self._rank(query, rows, k=k, min_score=min_score)

    # ── artifacts & activity ──────────────────────────────
    def add_artifact(self, kind: str, title: str, location: str, detail: str = "") -> int:
        vec = self.embed([f"{title} {detail}"[:1000]])[0]
        with self.lock:
            existing = self.db.execute("SELECT id FROM artifacts WHERE location=?", (location,)).fetchone()
            if existing:
                self.db.execute("UPDATE artifacts SET ts=?, title=?, detail=?, embedding=? WHERE id=?",
                                (now(), title, detail, self._blob(vec), existing["id"]))
                aid = existing["id"]
            else:
                aid = self.db.execute("INSERT INTO artifacts(ts,kind,title,location,detail,embedding,turn) "
                                      "VALUES(?,?,?,?,?,?,?)",
                                      (now(), kind, title, location, detail, self._blob(vec), self.current_turn)).lastrowid
            self.db.commit()
        self.log("artifact", "system", f"{kind.title()}: {title}", location, f"artifact:{aid}")
        return aid

    def log(self, kind: str, session: str, title: str, detail: str = "", ref: str = "", *,
            turn: int | None = None, status: str = "", ms: int | None = None) -> int:
        turn = turn if turn is not None else self.current_turn
        with self.lock:
            cur = self.db.execute("INSERT INTO activity(ts,session,kind,title,detail,ref,turn,status,ms) "
                                  "VALUES(?,?,?,?,?,?,?,?,?)",
                                  (now(), session, kind, title[:200], (detail or "")[:4000], ref, turn, status, ms))
            self.db.commit()
            event = {"id": cur.lastrowid, "ts": now(), "session": session, "kind": kind, "title": title[:200],
                     "ref": ref, "turn": turn, "status": status, "ms": ms}
        for cb in list(self.listeners):
            try:
                cb(event)
            except Exception:
                pass
        return cur.lastrowid

    def finish(self, activity_id: int, status: str, ms: int | None = None, detail: str | None = None) -> None:
        """Mark a logged step as done / error / declined, with how long it took."""
        with self.lock:
            if detail is None:
                self.db.execute("UPDATE activity SET status=?, ms=? WHERE id=?", (status, ms, activity_id))
            else:
                self.db.execute("UPDATE activity SET status=?, ms=?, detail=? WHERE id=?",
                                (status, ms, detail[:4000], activity_id))
            self.db.commit()

    def begin_turn(self, session: str, text: str) -> int:
        """Start a new request. Everything logged until end_turn() belongs to it."""
        tid = self.log("user", session, text[:200], text, status="running", turn=0)
        with self.lock:
            self.db.execute("UPDATE activity SET turn=? WHERE id=?", (tid, tid))
            self.db.commit()
        self.current_turn = tid
        return tid

    def end_turn(self, tid: int, status: str, ms: int) -> None:
        self.finish(tid, status, ms)
        self.current_turn = None

    def note_use(self, item: str, turn: int | None = None) -> None:
        turn = turn if turn is not None else self.current_turn
        if turn is None:
            return
        with self.lock:
            self.db.execute("INSERT INTO uses(item,turn,ts) VALUES(?,?,?)", (item, turn, now()))
            self.db.commit()

    # ── tracking (your own status / pin / note per item) ──
    def get_tracking(self, item: str) -> dict:
        with self.lock:
            r = self.db.execute("SELECT * FROM tracking WHERE item=?", (item,)).fetchone()
        return dict(r) if r else {"item": item, "status": "", "pinned": 0, "note": "", "updated": None}

    def set_tracking(self, item: str, status: str | None = None, pinned: bool | None = None,
                     note: str | None = None) -> dict:
        cur = self.get_tracking(item)
        if status is not None:
            if status not in TRACK_STATUSES:
                raise ValueError(f"status must be one of {', '.join(s or 'none' for s in TRACK_STATUSES)}")
            cur["status"] = status
        if pinned is not None:
            cur["pinned"] = int(bool(pinned))
        if note is not None:
            cur["note"] = note[:4000]
        cur["updated"] = now()
        with self.lock:
            if not cur["status"] and not cur["pinned"] and not cur["note"]:
                self.db.execute("DELETE FROM tracking WHERE item=?", (item,))
            else:
                self.db.execute("INSERT INTO tracking(item,status,pinned,note,updated) VALUES(?,?,?,?,?) "
                                "ON CONFLICT(item) DO UPDATE SET status=excluded.status, pinned=excluded.pinned, "
                                "note=excluded.note, updated=excluded.updated",
                                (item, cur["status"], cur["pinned"], cur["note"], cur["updated"]))
            self.db.commit()
        label = {"todo": "to do", "doing": "in progress", "waiting": "waiting", "done": "done"}.get(cur["status"], "")
        self.log("track", "dashboard", f"Tracking: {label or ('pinned' if cur['pinned'] else 'updated')}", item, item,
                 turn=0)
        return cur

    def tracked(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM tracking ORDER BY pinned DESC, CASE status WHEN 'doing' THEN 0 WHEN 'waiting' THEN 1 "
                "WHEN 'todo' THEN 2 WHEN '' THEN 3 ELSE 4 END, updated DESC")]

    def set_status(self, status: str) -> None:
        self.status = status
        for cb in list(self.listeners):
            try:
                cb({"kind": "status", "status": status})
            except Exception:
                pass

    def activity(self, since_id: int = 0, limit: int = 60):
        with self.lock:
            rows = self.db.execute("SELECT id,ts,session,kind,title,detail,ref,turn,status,ms FROM activity "
                                   "WHERE id>? ORDER BY id DESC LIMIT ?",
                                   (since_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def export_markdown(self, vault: Path) -> Path:
        """Mirror of everything Nova knows, readable in Obsidian / Notepad."""
        with self.lock:
            rows = self.db.execute("SELECT * FROM memories WHERE superseded_by IS NULL ORDER BY kind, id").fetchall()
        out = ["# What Nova knows", "", f"_Auto-generated {now()} — edit memories by telling Nova, not here._", ""]
        kind = None
        for r in rows:
            if r["kind"] != kind:
                kind = r["kind"]
                out += ["", f"## {kind.title()}s"]
            out.append(f"- {r['text']}  <!-- #{r['id']} -->")
        path = vault / "_Nova Memory.md"
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return path


# ── learning: extract durable memories from a finished conversation turn ──
LEARN_PROMPT = """You maintain the long-term memory of a personal assistant for {owner}.
Read this exchange and extract ONLY durable information worth remembering for months:
facts about {owner} (work, family, places, tools), preferences, people he mentions (with their relation),
projects, decisions, goals and routines. Ignore small talk, one-off requests and anything the assistant said.
If something corrects a known memory, include "replaces" with that memory's id.

Known related memories:
{known}

Exchange:
USER: {user}
ASSISTANT: {assistant}
TOOLS USED: {tools}

Reply with JSON only: {{"memories": [{{"kind": "fact|preference|person|project|decision|goal|routine|event", "text": "one clear sentence in third person", "importance": 1-3, "replaces": null}}]}}
Return {{"memories": []}} if nothing is worth keeping."""


# Tools whose use says nothing lasting about you — turns that only used these aren't mined for memories.
QUICK_TOOLS = {"get_time", "get_weather", "system_info", "open_app", "open_url", "screenshot", "set_volume",
               "media_control", "list_files", "search_memory", "recall", "web_search"}


def learn_from_turn(store: Store, llm, owner: str, user: str, assistant: str, tools_used: list[str],
                    turn: int | None = None) -> None:
    if len(user) < 12 and not tools_used:
        return
    if tools_used and set(tools_used) <= QUICK_TOOLS and len(user) < 80:
        return        # "what's the time / weather" — nothing durable to learn, don't tie up the model
    related = store.recall(user, k=8, min_score=0.15)
    known = "\n".join(f"#{r['id']}: {r['text']}" for _, r in related) or "(none)"
    prompt = LEARN_PROMPT.format(owner=owner, known=known, user=user[:2000], assistant=assistant[:1500],
                                 tools=", ".join(tools_used) or "none")
    try:
        # A cloud model does this in the background when available, so the local model stays free for you.
        raw = llm.complete(prompt, prefer_smart=bool(getattr(llm, "smart", None)), temperature=0.1)
        m = re.search(r"\{.*\}", raw, re.S)
        items = json.loads(m.group(0)).get("memories", []) if m else []
    except Exception as e:
        print(f"[memory] learning skipped: {e}")
        return
    valid_ids = {r["id"] for _, r in related}
    for it in items[:6]:
        text = str(it.get("text", "")).strip()
        if len(text) < 8:
            continue
        kind = str(it.get("kind", "fact"))
        imp = int(it.get("importance") or 2)
        rep = it.get("replaces")
        if isinstance(rep, (int, str)) and str(rep).isdigit() and int(rep) in valid_ids:
            store.supersede(int(rep), text, kind, turn=turn)
        else:
            store.add_memory(text, kind, "learned", max(1, min(3, imp)), turn=turn)
    if items and getattr(store, "vault", None):
        store.export_markdown(store.vault)
