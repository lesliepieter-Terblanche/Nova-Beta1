"""Local dashboard server (http://localhost:8765). Standard library only.

  /                 the 3D 2nd-brain dashboard
  /api/graph        everything Nova knows/made, as a graph
  /api/activity     live activity feed + Nova's status (polled)
  /api/item?id=     details for one node
  /api/open         open an item on the PC (POST {"id": ...})
  /api/ask          talk to Nova by typing (POST {"text": ...})
  /media?id=        stream an image/video for previews
  /sites/<name>/    previews of websites Nova built
"""
from __future__ import annotations

import json
import mimetypes
import os
import platform
import re
import subprocess
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import numpy as np

from .. import context
from ..config import resolve

HERE = Path(__file__).parent

HUBS = {
    "person": ("People", "#ff8a5c"), "project": ("Projects", "#ffd166"), "preference": ("Preferences", "#f78fb3"),
    "fact": ("Facts", "#7bdff2"), "goal": ("Goals", "#b8f2e6"), "decision": ("Decisions", "#caffbf"),
    "routine": ("Routines", "#a0c4ff"), "event": ("Events", "#bdb2ff"),
    "note": ("Notes", "#9b8cff"), "file": ("Files", "#8ecae6"), "site": ("Websites", "#4cc9f0"),
    "video": ("Videos", "#ff4d6d"), "ad": ("Ads", "#ff9f1c"), "image": ("Images", "#ffb4a2"),
    "email": ("Email", "#90e0ef"), "doc": ("Google Docs", "#80ed99"), "scrape": ("Web research", "#48cae4"),
    "audio": ("Audio", "#e0aaff"), "meeting": ("Meetings", "#f4a261"),
}


def _kind_of_artifact(k: str) -> str:
    return k if k in HUBS else "file"


class Dashboard:
    def __init__(self, cfg, agent):
        self.cfg, self.agent = cfg, agent
        self.port = int(cfg.dashboard.get("port", 8765))
        self._graph_cache = (0, None)

    # ── graph ─────────────────────────────────────────────
    def graph(self) -> dict:
        s = context.store
        with s.lock:
            mems = s.db.execute("SELECT id,kind,text,created,importance,uses,embedding FROM memories "
                                "WHERE superseded_by IS NULL ORDER BY id DESC LIMIT 900").fetchall()
            arts = s.db.execute("SELECT id,ts,kind,title,location,detail,embedding FROM artifacts "
                                "WHERE kind != 'note' ORDER BY id DESC LIMIT 700").fetchall()
            notes = s.db.execute("SELECT path, MIN(idx) i, MAX(mtime) m, GROUP_CONCAT(text, ' ') t, "
                                 "(SELECT embedding FROM chunks c2 WHERE c2.path=chunks.path AND c2.idx=0) e "
                                 "FROM chunks GROUP BY path ORDER BY m DESC LIMIT 500").fetchall()
            recent = {r["ref"] for r in s.db.execute("SELECT ref FROM activity WHERE ref!='' ORDER BY id DESC LIMIT 25")}

        nodes = [{"id": "core", "label": self.cfg.assistant.name, "kind": "core", "val": 30, "color": "#ffffff"}]
        links, vecs = [], []
        used_hubs = set()

        def add(nid, label, kind, val, created, vec, extra=None):
            hub = f"hub:{kind}"
            used_hubs.add(kind)
            n = {"id": nid, "label": label[:120], "kind": kind, "val": val, "color": HUBS[kind][1],
                 "created": created, "hot": nid in recent}
            if extra:
                n.update(extra)
            nodes.append(n)
            links.append({"source": hub, "target": nid, "type": "hub"})
            if vec is not None:
                vecs.append((nid, np.frombuffer(vec, dtype=np.float32)))

        for m in mems:
            add(f"memory:{m['id']}", m["text"], m["kind"] if m["kind"] in HUBS else "fact",
                2 + 2 * m["importance"] + min(m["uses"], 10) * 0.4, m["created"], m["embedding"])
        vault = resolve(self.cfg.brain.vault_dir)
        titles = {}
        for n in notes:
            p = Path(n["path"])
            titles[p.stem.lower()] = f"note:{n['path']}"
            add(f"note:{n['path']}", p.stem, "note", 5, "", n["e"],
                {"folder": str(p.parent.relative_to(vault)) if vault in p.parents else ""})
        for a in arts:
            add(f"artifact:{a['id']}", a["title"] or Path(a["location"]).name, _kind_of_artifact(a["kind"]), 4,
                a["ts"], a["embedding"])

        # [[wiki links]] between notes
        for n in notes:
            for target in set(re.findall(r"\[\[([^\]|#]+)", n["t"] or "")):
                t = titles.get(target.strip().lower())
                if t:
                    links.append({"source": f"note:{n['path']}", "target": t, "type": "wiki"})

        # semantic links: each node to its 2 closest neighbours
        if len(vecs) > 2:
            dims = {}
            for nid, v in vecs:
                dims.setdefault(len(v), []).append((nid, v))
            for group in dims.values():
                if len(group) < 3:
                    continue
                ids = [g[0] for g in group]
                mat = np.stack([g[1] for g in group])
                mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
                sim = mat @ mat.T
                np.fill_diagonal(sim, 0)
                seen = set()
                for i, row in enumerate(sim):
                    for j in np.argsort(row)[-2:]:
                        if row[j] > 0.62:
                            key = tuple(sorted((i, int(j))))
                            if key not in seen:
                                seen.add(key)
                                links.append({"source": ids[i], "target": ids[int(j)], "type": "semantic",
                                              "strength": float(row[j])})
        for k in used_hubs:
            label, color = HUBS[k]
            nodes.append({"id": f"hub:{k}", "label": label, "kind": "hub", "hubKind": k, "val": 14, "color": color})
            links.append({"source": "core", "target": f"hub:{k}", "type": "core"})
        return {"nodes": nodes, "links": links}

    def stats(self) -> dict:
        s = context.store
        with s.lock:
            q = lambda sql: s.db.execute(sql).fetchone()[0]
            return {
                "memories": q("SELECT COUNT(*) FROM memories WHERE superseded_by IS NULL"),
                "notes": q("SELECT COUNT(DISTINCT path) FROM chunks"),
                "artifacts": q("SELECT COUNT(*) FROM artifacts"),
                "actions": q("SELECT COUNT(*) FROM activity WHERE kind='tool'"),
                "status": s.status, "name": self.cfg.assistant.name, "owner": self.cfg.assistant.owner,
                "models": {"local": self.cfg.llm.providers.ollama.model, "smart": context.llm.smart if context.llm else []},
            }

    # ── items ─────────────────────────────────────────────
    def item(self, nid: str) -> dict:
        s = context.store
        kind, _, key = nid.partition(":")
        if kind == "memory":
            with s.lock:
                r = s.db.execute("SELECT * FROM memories WHERE id=?", (int(key),)).fetchone()
                history = s.db.execute("SELECT id,text,created FROM memories WHERE superseded_by=?", (int(key),)).fetchall()
            if not r:
                return {"error": "not found"}
            return {"id": nid, "type": r["kind"], "title": r["text"], "body": r["text"], "created": r["created"],
                    "meta": {"source": r["source"], "importance": r["importance"], "times recalled": r["uses"]},
                    "history": [dict(h) for h in history], "related": self._related(r["text"], nid)}
        if kind == "note":
            p = Path(key)
            body = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else "(file missing)"
            return {"id": nid, "type": "note", "title": p.stem, "body": body[:20000], "location": str(p),
                    "openable": True, "related": self._related(body[:1000], nid)}
        if kind == "artifact":
            with s.lock:
                r = s.db.execute("SELECT * FROM artifacts WHERE id=?", (int(key),)).fetchone()
            if not r:
                return {"error": "not found"}
            loc = r["location"]
            out = {"id": nid, "type": r["kind"], "title": r["title"], "body": r["detail"], "created": r["ts"],
                   "location": loc, "openable": True, "related": self._related(f"{r['title']} {r['detail']}", nid)}
            p = Path(loc)
            if not loc.startswith("http") and p.exists():
                ext = p.suffix.lower()
                if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                    out["preview"] = {"type": "image", "src": f"/media?id={nid}"}
                elif ext in (".mp4", ".webm", ".mov"):
                    out["preview"] = {"type": "video", "src": f"/media?id={nid}"}
                elif ext in (".mp3", ".wav", ".m4a", ".ogg"):
                    out["preview"] = {"type": "audio", "src": f"/media?id={nid}"}
                elif ext == ".html" and r["kind"] == "site":
                    out["preview"] = {"type": "site", "src": f"/sites/{p.parent.name}/"}
                elif ext in (".md", ".txt", ".csv", ".json") and p.stat().st_size < 400000:
                    out["body"] = p.read_text(encoding="utf-8", errors="ignore")[:20000]
                elif p.is_dir():
                    imgs = sorted(x for x in p.iterdir() if x.suffix.lower() in (".jpg", ".png"))
                    out["gallery"] = [f"/media?id={nid}&file={x.name}" for x in imgs[:8]]
                    cap = p / "caption.txt"
                    if cap.exists():
                        out["body"] = cap.read_text(encoding="utf-8")
            return out
        return {"error": "unknown id"}

    def _related(self, text: str, exclude: str) -> list:
        s = context.store
        out = []
        for score, r in s.recall(text, k=5):
            nid = f"memory:{r['id']}"
            if nid != exclude:
                out.append({"id": nid, "title": r["text"][:90], "score": round(score, 2)})
        for score, c in s.search_notes(text, k=3):
            nid = f"note:{c['path']}"
            if nid != exclude and all(o["id"] != nid for o in out):
                out.append({"id": nid, "title": Path(c["path"]).stem, "score": round(score, 2)})
        return out[:7]

    def location_of(self, nid: str) -> str | None:
        kind, _, key = nid.partition(":")
        if kind == "note":
            return key
        if kind == "artifact":
            with context.store.lock:
                r = context.store.db.execute("SELECT location FROM artifacts WHERE id=?", (int(key),)).fetchone()
            return r["location"] if r else None
        return None

    def open_item(self, nid: str) -> str:
        loc = self.location_of(nid)
        if not loc:
            return "Nothing to open for this item."
        if loc.startswith("http"):
            webbrowser.open(loc)
        elif platform.system() == "Windows":
            os.startfile(loc)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", loc])
        return f"Opened {loc}"

    # ── server ────────────────────────────────────────────
    def start(self) -> None:
        dash = self

        class H(SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                data = json.dumps(obj, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _file(self, path: Path):
                if not path.exists() or not path.is_file():
                    return self.send_error(404)
                ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
                size = path.stat().st_size
                rng = self.headers.get("Range")
                start, end = 0, size - 1
                if rng and rng.startswith("bytes="):
                    a, _, b = rng[6:].partition("-")
                    start = int(a) if a else 0
                    end = int(b) if b else size - 1
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                else:
                    self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                with open(path, "rb") as f:
                    f.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        chunk = f.read(min(1 << 16, remaining))
                        if not chunk:
                            break
                        try:
                            self.wfile.write(chunk)
                        except (BrokenPipeError, ConnectionResetError):
                            return
                        remaining -= len(chunk)

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                try:
                    if u.path in ("/", "/index.html"):
                        return self._file(HERE / "index.html")
                    if u.path.startswith("/vendor/"):
                        return self._file(HERE / "vendor" / Path(u.path).name)
                    if u.path == "/api/graph":
                        return self._json(dash.graph())
                    if u.path == "/api/stats":
                        return self._json(dash.stats())
                    if u.path == "/api/activity":
                        return self._json({"events": context.store.activity(int(q.get("since", 0))),
                                           "status": context.store.status})
                    if u.path == "/api/item":
                        return self._json(dash.item(q.get("id", "")))
                    if u.path == "/media":
                        loc = dash.location_of(q.get("id", ""))
                        if not loc:
                            return self.send_error(404)
                        p = Path(loc)
                        if p.is_dir() and q.get("file"):
                            p = p / Path(q["file"]).name
                        return self._file(p)
                    if u.path.startswith("/sites/"):
                        rel = unquote(u.path[len("/sites/"):])
                        base = resolve(dash.cfg.web.sites_dir).resolve()
                        target = (base / rel).resolve()
                        if base not in target.parents and target != base:
                            return self.send_error(403)
                        if target.is_dir():
                            target = target / "index.html"
                        return self._file(target)
                    self.send_error(404)
                except Exception as e:
                    self._json({"error": str(e)}, 500)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.headers.get("Origin", "").split("//")[-1] not in ("", f"localhost:{dash.port}", f"127.0.0.1:{dash.port}"):
                    return self._json({"error": "forbidden"}, 403)
                try:
                    if self.path == "/api/open":
                        return self._json({"message": dash.open_item(body.get("id", ""))})
                    if self.path == "/api/ask":
                        reply = dash.agent.handle(body.get("text", ""), session="dashboard")
                        if body.get("speak") and context.speech:
                            threading.Thread(target=context.speech.speak, args=(reply.text,), daemon=True).start()
                        return self._json({"text": reply.text, "files": reply.files})
                    self.send_error(404)
                except Exception as e:
                    self._json({"error": str(e)}, 500)

        try:
            server = ThreadingHTTPServer(("127.0.0.1", self.port), H)   # localhost only
        except OSError:
            print(f"[dashboard] port {self.port} is busy — is Nova already running? Dashboard not started.")
            return
        threading.Thread(target=server.serve_forever, daemon=True, name="dashboard").start()
        print(f"[dashboard] http://localhost:{self.port}")
        if self.cfg.dashboard.get("open_on_start", True):
            webbrowser.open(f"http://localhost:{self.port}")
