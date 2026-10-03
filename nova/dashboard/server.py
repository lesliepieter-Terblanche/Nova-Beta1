"""Local dashboard server (http://localhost:8765). Standard library only.

  /                 the 3D 2nd-brain dashboard
  /api/graph        everything Nova knows/made, as a graph
  /api/activity     live activity feed + Nova's status (polled)
  /api/item?id=     details for one node: what it is, where it came from, its timeline, your tracking
  /api/topic?kind=  every item in a topic (People, Projects, Actions, … or memories/notes/creations/tracked)
  /api/now          what Nova is busy with right now (current request and its steps)
  /api/flow         the live work flow: everything in progress, as steps the data moves through
  /api/track        set your status / pin / note on an item (POST)
  /api/open         open an item on the PC (POST {"id": ...})
  /api/ask          talk to Nova by typing (POST {"text": ..., "voice": true = spoken reply as audio for this device})
  /api/brain/plan   the 01_Personal / 02_Work filing plan: review, answer questions, apply (see nova/taxonomy.py)
  /api/upload       file anything into the brain (POST the file; ?name=…&note=…)
  /api/voice        talk to Nova by voice from any browser (POST the recording; reply text + audio)
  /api/tts/<token>  a spoken reply: streamed from ElevenLabs, or made into one MP3 (kept for 30 minutes)
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
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import numpy as np

from .. import context, settings
from ..config import resolve

HERE = Path(__file__).parent

# One clear colour per category. Each family also has its own shape on the brain (see the dashboard's colour key):
# memories ● spheres · notes ◆ diamonds · creations ■ cubes · activity ▲ pyramids.
HUBS = {
    "person": ("People", "#ff7a45"), "project": ("Projects", "#ffd23f"), "preference": ("Preferences", "#ff5fa2"),
    "fact": ("Facts", "#38bdf8"), "goal": ("Goals", "#34d399"), "decision": ("Decisions", "#a3e635"),
    "routine": ("Routines", "#6366f1"), "event": ("Events", "#e879f9"),
    "note": ("Notes", "#a78bfa"),
    "file": ("Files", "#94a3b8"), "site": ("Websites", "#22d3ee"), "video": ("Videos", "#ef4444"),
    "ad": ("Ads", "#fb923c"), "image": ("Images", "#fda4af"), "email": ("Email", "#fef08a"),
    "doc": ("Google Docs", "#2dd4bf"), "scrape": ("Web research", "#60a5fa"), "audio": ("Audio", "#c084fc"),
    "meeting": ("Meetings", "#4ade80"),
    "action": ("Actions", "#fde047"), "mission": ("Missions", "#d946ef"),
}
MEMORY_HUBS = ("person", "project", "preference", "fact", "goal", "decision", "routine", "event")
GROUPS = {"memories": "Memories", "notes": "Notes", "creations": "Creations", "actions": "Actions",
          "tracked": "Tracked", "doing": "In progress"}
SESSION_LABEL = {"voice": "by voice", "telegram": "on Telegram", "dashboard": "on the dashboard",
                 "text": "typed", "routine": "by a routine", "system": "automatically"}
STEP_LABEL = {"user": "You asked", "reply": "Nova replied", "tool": "Action", "memory": "Memory",
              "artifact": "Created", "timing": "Timing", "open": "Opened", "track": "Tracking"}


SKILL_TITLES = {"system": "PC control", "memory": "Second brain", "files": "Files & folders", "web": "Web & websites",
                "browser": "Browser control", "google_ws": "Google Workspace", "media": "Video & media",
                "camera_ads": "Webcam & ads", "meetings": "Meeting recorder", "weather": "Weather",
                "maintenance": "Updates & upkeep", "currency": "Currency", "globe": "God's Eye View globe"}


def _dream_state() -> dict:
    from ..dreaming import _dreamer
    return {"running": bool(_dreamer and _dreamer.running), "step": _dreamer.step if _dreamer else ""}


def _how(session: str) -> str:
    if (session or "").startswith("mission:"):
        return f"by mission #{session.split(':', 1)[1]}"
    return SESSION_LABEL.get(session, session)


def _json_or(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


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
            turns = s.db.execute("SELECT id,ts,title,status,session FROM activity WHERE kind='user' AND turn=id "
                                 "ORDER BY id DESC LIMIT 40").fetchall()
            track = {r["item"]: dict(r) for r in s.db.execute("SELECT * FROM tracking")}

        from .. import taxonomy as tx
        strict = tx.enabled()
        nodes = [{"id": "core", "label": self.cfg.assistant.name, "kind": "core", "val": 30, "color": "#ffffff"}]
        links = []
        used_hubs = set()                      # (domain, kind)
        vault = resolve(self.cfg.brain.vault_dir)
        guessed = self.__dict__.setdefault("_domain_cache", {})

        placed = tx._placements()

        def domain_for(nid, text):
            """Which side of the brain: Personal (left) or Work (right) — where you put it, else by keywords."""
            if nid in placed:
                return placed[nid][0]
            key = (nid, text[:80])
            if key not in guessed:
                if len(guessed) > 6000:
                    guessed.clear()
                guessed[key] = tx._guess_domain(text)
            return guessed[key]

        def add(nid, label, kind, val, created, vec=None, extra=None, domain=None, parent=None, status=None):
            t = track.get(nid) or {}
            domain = domain or domain_for(nid, label)
            n = {"id": nid, "label": label[:120], "kind": kind, "val": val * (1.3 if t.get("pinned") else 1),
                 "color": HUBS[kind][1], "created": created, "hot": (nid in recent and kind != "action") or t.get("status") == "doing",
                 "track": t.get("status", ""), "pinned": bool(t.get("pinned")), "domain": domain,
                 "auto": bool(t.get("auto")),         # Nova tagged it herself
                 "status": tx.TRACK_TO_STATUS.get(t.get("status", ""), "") or status or ""}
            if extra:
                n.update(extra)
            if nid in placed:
                domain = n["domain"] = placed[nid][0]
                n["cat"] = placed[nid][1]
            if not parent:
                parent = f"hub:{domain}:{kind}"
                used_hubs.add((domain, kind))
            n["parent_hub"] = parent
            nodes.append(n)
            links.append({"source": parent, "target": nid, "type": "hub"})

        for m in mems:
            add(f"memory:{m['id']}", m["text"], m["kind"] if m["kind"] in HUBS else "fact",
                2 + 2 * m["importance"] + min(m["uses"], 10) * 0.4, m["created"])
        titles = {}
        for n in notes:
            p = Path(n["path"])
            titles[p.stem.lower()] = f"note:{n['path']}"
            import datetime as _dt
            rel = p.relative_to(vault).parts if vault in p.parents else ()
            filed = strict and len(rel) >= 3 and rel[0] in tx.TREE and rel[1] in tx.TREE[rel[0]]
            add(f"note:{n['path']}", p.stem, "note", 5,
                _dt.datetime.fromtimestamp(n["m"]).isoformat(timespec="seconds") if n["m"] else "", None,
                {"folder": "/".join(rel[:-1]), "filed": bool(filed)},
                domain=rel[0] if filed else None, parent=f"cat:{rel[0]}/{rel[1]}" if filed else None,
                status="COMPLETED" if filed else "")
        for a in arts:
            add(f"artifact:{a['id']}", a["title"] or Path(a["location"]).name, _kind_of_artifact(a["kind"]), 4, a["ts"])

        from ..missions import missions as _missions
        m_status = {"running": "IN-PROGRESS", "failed": "WAITING-ON-USER", "stuck": "WAITING-ON-USER",
                    "scheduled": "BACKLOG", "paused": "BACKLOG", "done": "COMPLETED"}
        for ms in _missions().all():
            add(f"mission:{ms['id']}", ms["title"], "mission", 7, ms["created"], None,
                {"status": m_status.get(ms["status"], ""), "mstatus": ms["status"], "progress": ms.get("progress", 0)})
            if ms["status"] == "running":
                nodes[-1]["hot"] = True
        from .. import autotag
        for t in turns:                                  # every command is tagged from how it went
            tstatus = t["status"] or "done"
            add(f"turn:{t['id']}", t["title"], "action", 3.2, t["ts"], None,
                {"session": t["session"], "tstatus": tstatus, "fresh": autotag.is_fresh(t["ts"])},
                status=autotag.tag_of_turn(tstatus))
            if not nodes[-1]["track"]:
                nodes[-1]["auto"] = True
            if tstatus == "running":
                nodes[-1]["hot"] = True

        # people cards: each person linked to everything that mentions them
        from .. import people as _people
        ids_now = {n["id"] for n in nodes}
        for pr in _people.all_people():
            ms = _people.mentions(pr, limit=60)
            add(f"person:{pr['id']}", pr["name"], "person", 5 + min(len(ms), 20) * 0.35, pr["created"], None,
                {"isPerson": True, "company": pr["company"], "mentions": len(ms)},
                domain=tx.WORK if pr["company"] else domain_for(f"person:{pr['id']}", f"{pr['name']} {pr['role']} {pr['notes']}"))
            for m in ms:
                if m["id"] in ids_now:
                    links.append({"source": f"person:{pr['id']}", "target": m["id"], "type": "mention"})

        # [[wiki links]] between notes
        for n in notes:
            for target in set(re.findall(r"\[\[([^\]|#]+)", n["t"] or "")):
                t = titles.get(target.strip().lower())
                if t:
                    links.append({"source": f"note:{n['path']}", "target": t, "type": "wiki"})

        # the two halves of the brain, their numbered categories, and a hub per kind of thing on each side
        for d in (tx.PERSONAL, tx.WORK):
            nodes.append({"id": f"dom:{d}", "label": d, "kind": "domain", "domain": d, "val": 20,
                          "color": "#f0a6ff" if d == tx.PERSONAL else "#7cc4ff"})
            links.append({"source": "core", "target": f"dom:{d}", "type": "core"})
            if strict:
                for c in tx.TREE[d]:
                    nodes.append({"id": f"cat:{d}/{c}", "label": c, "kind": "category", "domain": d, "val": 12,
                                  "color": HUBS["note"][1]})
                    links.append({"source": f"dom:{d}", "target": f"cat:{d}/{c}", "type": "branch"})
        for d, k in sorted(used_hubs):
            label, color = HUBS[k]
            nodes.append({"id": f"hub:{d}:{k}", "label": label if (k != "note" or not strict) else "Notes to file",
                          "kind": "hub", "hubKind": k, "domain": d, "val": 14, "color": color})
            links.append({"source": f"dom:{d}", "target": f"hub:{d}:{k}", "type": "branch"})

        # projects: each project is the centre of a cluster of everything that belongs to it
        org = self.organized()
        ids = {n["id"] for n in nodes}
        by_id = {n["id"]: n for n in nodes}
        for pid in org["projects"]:
            if pid in by_id:
                if not by_id[pid].get("status"):         # no project without a status: not started yet = backlog
                    by_id[pid]["status"] = "BACKLOG"
                by_id[pid].update(isProject=True, short=org["short"].get(pid, ""), pcolor=org["color"].get(pid),
                                  parent=org["parents"].get(pid, ""), val=max(by_id[pid]["val"], 9))
        for child, parent in org["parents"].items():
            if child in ids and parent in ids:
                by_id[child]["project"] = parent             # a sub-project sits with its parent, not the category
                links.append({"source": parent, "target": child, "type": "subproject", "color": org["color"].get(parent)})
        for item, pid in org["item_project"].items():
            if item in ids and pid in ids and item != pid:
                by_id[item]["project"] = pid
                links.append({"source": pid, "target": item, "type": "project", "color": org["color"].get(pid)})
        # live: a project is in progress while Nova works on one of its commands or missions, and waits on you
        # while one of them does — whatever its saved tag says
        rank = {"WAITING-ON-USER": 2, "IN-PROGRESS": 1}
        for n in list(nodes):
            pid = n.get("project")
            if not pid or n["kind"] not in ("action", "mission"):
                continue
            if n["kind"] == "action":                    # a command sits with its project on the map
                n["domain"] = by_id[pid]["domain"]
                if by_id[pid].get("cat"):
                    n["cat"] = by_id[pid]["cat"]
                if n.get("tstatus") not in ("running", "waiting"):
                    continue
            if n.get("status") not in rank:
                continue
            for _ in range(4):
                p_ = by_id.get(pid)
                if not p_:
                    break
                if rank[n["status"]] > rank.get(p_.get("live") or "", 0):
                    p_.update(status=n["status"], live=n["status"], hot=True)
                pid = org["parents"].get(pid, "")
        try:
            waiting = tx.bottlenecks()
        except Exception as e:
            print(f"[dashboard] bottlenecks: {e}")
            waiting = []
        seen = {w["id"] for w in waiting}
        for n in nodes:                                  # anything on the map that waits on you is listed too
            if n.get("status") == "WAITING-ON-USER" and n["id"] not in seen and not n.get("live"):
                why = ""
                if n["kind"] == "action" and not n["track"]:
                    if n.get("tstatus") == "error" and not n.get("fresh"):
                        continue                         # an old failure is history, not a bottleneck
                    why = "say yes or no" if n.get("tstatus") == "waiting" else \
                        "it failed — ask again, or mark it completed"
                waiting.append({"id": n["id"], "text": n["label"], "domain": n["domain"], "why": why})
        try:
            audit = tx.plan_summary()
        except Exception:
            audit = {}
        return {"nodes": nodes, "links": links, "bottlenecks": waiting, "strict": strict, "audit": audit,
                "tree": {d: list(tx.TREE[d]) for d in tx.TREE}}

    def graph_rev(self) -> str:
        """Changes whenever a status, pin or note changes — the dashboard redraws the map when it does."""
        s = context.store
        with s.lock:
            t = s.db.execute("SELECT COUNT(*) c, COALESCE(MAX(updated), '') u, COALESCE(SUM(LENGTH(status) * 7 + pinned), 0) x "
                             "FROM tracking").fetchone()
            c = s.db.execute("SELECT COUNT(DISTINCT path) c, COALESCE(MAX(mtime), 0) m FROM chunks").fetchone()
            a = s.db.execute("SELECT COUNT(*) c, COALESCE(SUM(id), 0) x FROM activity WHERE kind='user' "
                             "AND status IN ('running','waiting')").fetchone()     # commands change tag as they go
        return f"{t['c']}.{t['u']}.{t['x']}.{c['c']}.{int(c['m'])}.{a['c']}.{a['x']}"

    # ── projects: what belongs together ───────────────────
    def organized(self, force: bool = False) -> dict:
        """Which project each item belongs to (cached a few seconds — the graph and panels both ask)."""
        import time as _time

        from .organize import organize, palette
        cache = getattr(self, "_org_cache", None)
        if cache and not force and _time.time() - cache[0] < 4:
            return cache[1]
        s = context.store
        with s.lock:
            s.db.execute("CREATE TABLE IF NOT EXISTS links(item TEXT PRIMARY KEY, project TEXT)")
            projs = s.db.execute("SELECT id,text,created,source,embedding FROM memories WHERE superseded_by IS NULL "
                                 "AND kind='project' ORDER BY id").fetchall()
            mems = s.db.execute("SELECT id,text,embedding FROM memories WHERE superseded_by IS NULL AND kind!='project' "
                                "ORDER BY id DESC LIMIT 900").fetchall()
            arts = s.db.execute("SELECT id,title,detail,location,embedding FROM artifacts WHERE kind!='note' "
                                "ORDER BY id DESC LIMIT 700").fetchall()
            notes = s.db.execute("SELECT path, GROUP_CONCAT(text, ' ') t, (SELECT embedding FROM chunks c2 WHERE "
                                 "c2.path=chunks.path AND c2.idx=0) e FROM chunks GROUP BY path LIMIT 500").fetchall()
            turns = s.db.execute("SELECT id,title FROM activity WHERE kind='user' AND turn=id "
                                 "AND session NOT LIKE 'mission:%' ORDER BY id DESC LIMIT 40").fetchall()
            manual = {r["item"]: r["project"] for r in s.db.execute("SELECT * FROM links")}
            try:
                mis = s.db.execute("SELECT id,title,goal FROM missions WHERE status!='deleted'").fetchall()
            except Exception:
                mis = []
        vec = lambda b: np.frombuffer(b, dtype=np.float32) if b else None    # noqa: E731
        projects = [{"id": f"memory:{p['id']}", "text": p["text"], "vec": vec(p["embedding"]), "created": p["created"],
                     "source": p["source"]} for p in projs]
        items = [{"id": f"memory:{m['id']}", "text": m["text"], "vec": vec(m["embedding"])} for m in mems]
        items += [{"id": f"artifact:{a['id']}", "text": f"{a['title']} {a['detail'] or ''} {Path(a['location']).name}",
                   "vec": vec(a["embedding"])} for a in arts]
        items += [{"id": f"note:{n['path']}", "text": f"{Path(n['path']).stem} {(n['t'] or '')[:3000]}", "vec": vec(n["e"])}
                  for n in notes]
        items += [{"id": f"mission:{m['id']}", "text": f"{m['title']} {m['goal']}", "vec": None} for m in mis]
        items += [{"id": f"turn:{t['id']}", "text": t["title"], "vec": None} for t in turns]
        from .. import people as _people
        items += [{"id": f"person:{p['id']}", "text": f"{p['name']} {p['company']} {p['role']} {p['notes']}", "vec": None}
                  for p in _people.all_people()]
        res = organize(projects, items, manual, owner=self.cfg.assistant.owner)
        roots = [p["id"] for p in projects if p["id"] not in res["parents"]]
        colors = dict(zip(roots, palette(len(roots))))
        for pid in [p["id"] for p in projects]:
            top = pid
            for _ in range(5):
                top = res["parents"].get(top, top) if top in res["parents"] else top
            colors[pid] = colors.get(top, colors.get(pid, "#ffd166"))
        res["color"] = colors
        res["projects"] = [p["id"] for p in projects]
        self._org_cache = (_time.time(), res)
        return res

    def set_link(self, item: str, project: str | None) -> dict:
        """You decide: link an item to a project ("memory:12"), to none (""), or back to automatic (None)."""
        s = context.store
        with s.lock:
            s.db.execute("CREATE TABLE IF NOT EXISTS links(item TEXT PRIMARY KEY, project TEXT)")
            if project is None:
                s.db.execute("DELETE FROM links WHERE item=?", (item,))
            else:
                s.db.execute("INSERT OR REPLACE INTO links(item, project) VALUES(?,?)", (item, project))
            s.db.commit()
        s.log("track", "dashboard", "Linked to a project" if project else "Unlinked from its project" if project == ""
              else "Project link set back to automatic", item, item, turn=0)
        org = self.organized(force=True)
        return {"project": org["item_project"].get(item, ""), "why": org["why"].get(item, "")}

    def stats(self) -> dict:
        s = context.store
        with s.lock:
            q = lambda sql: s.db.execute(sql).fetchone()[0]
            return {
                "memories": q("SELECT COUNT(*) FROM memories WHERE superseded_by IS NULL"),
                "notes": q("SELECT COUNT(DISTINCT path) FROM chunks"),
                "artifacts": q("SELECT COUNT(*) FROM artifacts"),
                "actions": q("SELECT COUNT(*) FROM activity WHERE kind='user' AND turn=id")
                           or q("SELECT COUNT(*) FROM activity WHERE kind='tool'"),
                "tracked": q("SELECT COUNT(*) FROM tracking"),
                "projects": self.active_projects(),
                "skills": self._skill_count(),
                "doing": q("SELECT COUNT(*) FROM tracking WHERE status='doing'"),
                "status": s.status, "theme": settings.theme(), "name": self.cfg.assistant.name, "owner": self.cfg.assistant.owner,
                "models": {"local": self.cfg.llm.providers.ollama.model, "primary": getattr(context.llm, "primary", ""),
                           "smart": context.llm.smart if context.llm else []},
            }

    def _skill_count(self) -> int:
        try:
            from ..tools import REGISTRY
            return len({t.func.__module__ for t in REGISTRY.values()})
        except Exception:
            return 0

    # ── items ─────────────────────────────────────────────
    def item(self, nid: str) -> dict:
        out = self._item(nid)
        if "error" in out:
            return out
        org = self.organized()
        if nid in org["projects"]:
            linked = {}
            members = [i for i, p in org["item_project"].items() if p == nid]
            members += [c for c, p in org["parents"].items() if p == nid]
            for m in members:
                kind = m.split(":")[0]
                label = {"memory": "Memories", "note": "Notes", "artifact": "Creations", "mission": "Missions",
                         "turn": "Actions", "person": "People"}.get(kind, kind)
                if m in org["projects"]:
                    label = "Sub-projects"
                linked.setdefault(label, []).append({"id": m, "title": self.title_of(m) or m,
                                                     "why": org["why"].get(m, "sub-project")})
            out["linked"] = linked
            out["parent_project"] = org["parents"].get(nid, "")
        else:
            pid = org["item_project"].get(nid, "")
            out["project"] = {"id": pid, "title": org["short"].get(pid, "") if pid else "",
                              "why": org["why"].get(nid, ""), "color": org["color"].get(pid)}
        out["projects"] = [{"id": p, "title": org["short"].get(p, "")} for p in org["projects"] if p != nid]
        if not nid.startswith("turn:"):
            out["tracking"] = context.store.get_tracking(nid)
            out["timeline"] = self.timeline(nid, out)
            if out.get("turn"):
                out["origin"] = self.turn_brief(out["turn"])
        return out

    # ── turns: one request and everything Nova did for it ──
    def turn_brief(self, tid: int) -> dict | None:
        s = context.store
        with s.lock:
            r = s.db.execute("SELECT * FROM activity WHERE id=? AND kind='user'", (tid,)).fetchone()
        if not r:
            return None
        return {"id": f"turn:{tid}", "request": r["detail"] or r["title"], "ts": r["ts"],
                "how": _how(r["session"]), "status": r["status"] or "done", "ms": r["ms"]}

    def turn(self, tid: int) -> dict:
        s = context.store
        with s.lock:
            r = s.db.execute("SELECT * FROM activity WHERE id=? AND kind='user'", (tid,)).fetchone()
            if not r:
                return {"error": "not found"}
            rows = s.db.execute("SELECT * FROM activity WHERE turn=? AND id!=? ORDER BY id", (tid, tid)).fetchall()
            mems = s.db.execute("SELECT id,kind,text FROM memories WHERE turn=?", (tid,)).fetchall()
            arts = s.db.execute("SELECT id,kind,title,location FROM artifacts WHERE turn=?", (tid,)).fetchall()
            used = s.db.execute("SELECT DISTINCT item FROM uses WHERE turn=?", (tid,)).fetchall()
        steps, reply, timing = [], "", None
        for a in rows:
            if a["kind"] == "reply":
                reply = a["detail"] or a["title"]
            if a["kind"] == "timing":
                timing = _json_or(a["detail"])
                continue
            d = _json_or(a["detail"] or "")
            steps.append({"kind": a["kind"], "label": STEP_LABEL.get(a["kind"], a["kind"]), "title": a["title"],
                          "ts": a["ts"], "status": a["status"] or "", "ms": a["ms"], "ref": a["ref"],
                          "args": d.get("args", d) if isinstance(d, dict) else None,
                          "result": d.get("result") if isinstance(d, dict) else None,
                          "text": None if isinstance(d, dict) else (a["detail"] or "")[:1500]})
        made = [{"id": f"memory:{m['id']}", "title": m["text"], "kind": m["kind"]} for m in mems]
        made += [{"id": f"artifact:{a['id']}", "title": a["title"] or Path(a["location"]).name, "kind": a["kind"]}
                 for a in arts]
        recalled = [{"id": u["item"], "title": self.title_of(u["item"])} for u in used]
        req = r["detail"] or r["title"]
        status = r["status"] or "done"
        return {"id": f"turn:{tid}", "type": "action", "title": req, "created": r["ts"], "status": status,
                "how": _how(r["session"]), "ms": r["ms"], "reply": reply,
                "steps": steps, "made": made, "recalled": [x for x in recalled if x["title"]], "timing": timing,
                "tracking": s.get_tracking(f"turn:{tid}"),
                "meta": {"channel": r["session"], "status": status,
                         "took": f"{r['ms'] / 1000:.1f} s" if r["ms"] else "—"}}

    def mission(self, mid: int) -> dict:
        from ..missions import missions as _missions
        m = _missions().get(mid)
        if not m or m["status"] == "deleted":
            return {"error": "not found"}
        runs = _missions().runs(mid, 8)
        cur = runs[0] if runs else None
        out_runs = []
        for r in runs:
            out_runs.append({"started": r["started"], "finished": r["finished"], "status": r["status"],
                             "summary": r["summary"] or "", "report": f"note:{r['report']}" if r["report"] else "",
                             "steps": len(r["steps"])})
        plan = [{"title": p.get("title", ""), "do": p.get("do", ""), "status": "", "result": ""}
                for p in (cur["plan"] if cur else [])]
        for i, st in enumerate(cur["steps"] if cur else []):
            if i < len(plan):
                plan[i].update(status="ok", result=st.get("result", "")[:1500], ms=st.get("ms"))
        if m["status"] == "running":
            nxt = next((p for p in plan if not p["status"]), None)
            if nxt:
                nxt["status"] = "running"
        return {"id": f"mission:{mid}", "type": "mission", "title": m["title"], "body": m["goal"],
                "created": m["created"], "turn": m.get("turn"), "status": m["status"], "when": m["when"],
                "next_run": m.get("next_run"), "last_run": m.get("last_run"), "runs_count": m["runs"],
                "progress": m.get("progress") or 0, "step": m.get("step") or "", "plan": plan, "runs": out_runs,
                "meta": {"schedule": m["when"], "runs": m["runs"],
                         "next run": (m.get("next_run") or "—")[:16].replace("T", " ")}}

    def person(self, pid: int) -> dict:
        from .. import people as _people
        c = _people.card(pid)
        if not c:
            return {"error": "not found"}
        groups: dict[str, list] = {}
        label = {"memory": "What Nova knows", "note": "Notes & meetings", "creation": "Creations", "action": "Requests"}
        for m in c["mentions"]:
            if m["deal"]:
                groups.setdefault("Deals & money", []).append(m)
            groups.setdefault(label.get(m["kind"], m["kind"]), []).append(m)
        return {"id": f"person:{pid}", "type": "person", "title": c["name"], "created": c["created"],
                "person": {k: c[k] for k in ("name", "company", "role", "email", "phone", "notes", "aliases",
                                             "last_contact", "last_seen", "source")},
                "mentions": {k: [{"id": m["id"], "title": m["title"], "ts": m["ts"]} for m in v[:40]]
                             for k, v in groups.items()},
                "mention_count": len(c["mentions"]),
                "others": [{"id": f"person:{p['id']}", "name": p["name"]} for p in _people.all_people()
                           if p["id"] != pid],
                "meta": {}}

    def title_of(self, nid: str) -> str:
        s = context.store
        kind, _, key = nid.partition(":")
        with s.lock:
            if kind == "memory" and key.isdigit():
                r = s.db.execute("SELECT text FROM memories WHERE id=?", (int(key),)).fetchone()
                return r["text"] if r else ""
            if kind == "artifact" and key.isdigit():
                r = s.db.execute("SELECT title, location FROM artifacts WHERE id=?", (int(key),)).fetchone()
                return (r["title"] or Path(r["location"]).name) if r else ""
            if kind == "turn" and key.isdigit():
                r = s.db.execute("SELECT title FROM activity WHERE id=?", (int(key),)).fetchone()
                return r["title"] if r else ""
            if kind == "mission" and key.isdigit():
                r = s.db.execute("SELECT title FROM missions WHERE id=?", (int(key),)).fetchone()
                return r["title"] if r else ""
        if kind == "person" and key.isdigit():
            from .. import people as _people
            pr = _people.get(int(key))
            return pr["name"] if pr else ""
        if kind == "note":
            return Path(key).stem
        return ""

    def timeline(self, nid: str, it: dict) -> list:
        """Everything that happened to one item, newest first."""
        s = context.store
        ev = []
        if it.get("created"):
            ev.append({"ts": it["created"], "what": "Created", "icon": "create"})
        for h in it.get("history") or []:
            ev.append({"ts": h["created"], "what": f"Earlier version: {h['text'][:140]}", "icon": "edit"})
        with s.lock:
            rows = s.db.execute("SELECT ts,kind,title,session FROM activity WHERE ref=? ORDER BY id DESC LIMIT 60",
                                (nid,)).fetchall()
            uses = s.db.execute("SELECT u.ts, u.turn, a.title FROM uses u LEFT JOIN activity a ON a.id=u.turn "
                                "WHERE u.item=? ORDER BY u.rowid DESC LIMIT 40", (nid,)).fetchall()
        for r in rows:
            if r["kind"] in ("memory", "artifact") and abs(len(ev)) and r["ts"][:16] == (it.get("created") or "")[:16]:
                continue         # same moment as "Created"
            icon = {"open": "open", "track": "track", "memory": "edit", "artifact": "create"}.get(r["kind"], "dot")
            ev.append({"ts": r["ts"], "what": r["title"], "icon": icon})
        for u in uses:
            ev.append({"ts": u["ts"], "what": f"Recalled while answering “{(u['title'] or '')[:90]}”", "icon": "recall",
                       "ref": f"turn:{u['turn']}"})
        ev.sort(key=lambda e: e["ts"] or "", reverse=True)
        return ev[:80]

    def now(self) -> dict:
        """What Nova is doing right now, for the 'Busy with' card."""
        s = context.store
        tid = s.current_turn
        with s.lock:
            if tid is None:
                last = s.db.execute("SELECT id FROM activity WHERE kind='user' AND turn=id AND session NOT LIKE 'mission:%' "
                                    "ORDER BY id DESC LIMIT 1").fetchone()
                tid_last = last["id"] if last else None
            waiting = s.db.execute("SELECT id,title,ts FROM activity WHERE kind='tool' AND status='waiting' "
                                   "ORDER BY id DESC LIMIT 5").fetchall()
            doing = s.db.execute("SELECT item, note FROM tracking WHERE status='doing' ORDER BY updated DESC LIMIT 8").fetchall()
        from ..presence import _presence
        pres = _presence.status() if _presence else {"enabled": False}
        from ..watcher import _mgr
        from ..missions import _missions
        running = [{"id": f"mission:{k}", "title": (_missions.get(k) or {}).get("title", ""),
                    "progress": v.get("progress", 0), "step": v.get("step", "")}
                   for k, v in (_missions.running.items() if _missions else [])]
        watching = [{"id": w.id, "label": w.label(), "left_min": max(0, int((w.until - time.time()) / 60))}
                    for w in (_mgr.active() if _mgr else [])]
        out = {"status": s.status, "busy": tid is not None, "presence": pres, "watching": watching, "missions": running,
               "dreaming": _dream_state(),
               "waiting": [dict(w) for w in waiting],
               "doing": [{"id": d["item"], "title": self.title_of(d["item"]) or d["item"], "note": d["note"]} for d in doing]}
        t = self.turn(tid if tid is not None else tid_last) if (tid is not None or tid_last) else None
        if t and "error" not in t:
            out["turn"] = {k: t[k] for k in ("id", "title", "created", "status", "how", "ms")}
            out["turn"]["steps"] = [{"label": x["label"], "title": x["title"], "status": x["status"], "ms": x["ms"]}
                                    for x in t["steps"] if x["kind"] in ("tool", "artifact", "memory")][-6:]
        return out

    def topic(self, kind: str, limit: int = 300) -> dict:
        """Every item in one topic, with when it was made, last touched, times recalled and your tracking."""
        s = context.store
        with s.lock:
            track = {r["item"]: dict(r) for r in s.db.execute("SELECT * FROM tracking")}
            uses = {r["item"]: (r["n"], r["last"]) for r in s.db.execute(
                "SELECT item, COUNT(*) n, MAX(ts) last FROM uses GROUP BY item")}
            touched = {r["ref"]: r["last"] for r in s.db.execute(
                "SELECT ref, MAX(ts) last FROM activity WHERE ref!='' GROUP BY ref")}
            items = []

            def push(nid, title, k, created, extra=None):
                t = track.get(nid) or {}
                n_use, last_use = uses.get(nid, (0, None))
                last = max([x for x in (created, last_use, touched.get(nid), t.get("updated")) if x] or [""])
                row = {"id": nid, "title": (title or "")[:200], "kind": k, "created": created, "last": last,
                       "recalled": n_use, "track": t.get("status", ""), "pinned": bool(t.get("pinned")),
                       "note": t.get("note", ""), "color": HUBS.get(k, ("", "#8f97bf"))[1]}
                if extra:
                    row.update(extra)
                items.append(row)

            if kind in ("person", "people", "tracked", "doing"):
                from .. import people as _people
                for pr in _people.all_people():
                    push(f"person:{pr['id']}", pr["name"], "person", pr["created"],
                         {"sub": ", ".join(x for x in (pr["role"], pr["company"]) if x) or "person card",
                          "card": True})
            if kind == "people":
                kind = "person"
            if kind in MEMORY_HUBS or kind in ("memories", "tracked", "doing"):
                kinds = MEMORY_HUBS if kind in ("memories", "tracked", "doing") else (kind,)
                q = ",".join("?" * len(kinds))
                for m in s.db.execute(f"SELECT id,kind,text,created,importance,source,turn FROM memories "
                                      f"WHERE superseded_by IS NULL AND kind IN ({q}) ORDER BY id DESC LIMIT ?",
                                      (*kinds, limit)):
                    push(f"memory:{m['id']}", m["text"], m["kind"], m["created"],
                         {"sub": f"{m['source']} · importance {m['importance']}"})
            folder = ""
            if kind.startswith("cat:"):                     # one numbered category of the brain, e.g. cat:02_Work/02_Clients_&_Partners
                folder = str(resolve(self.cfg.brain.vault_dir) / kind[4:]) + os.sep
            if kind in ("note", "notes", "tracked", "doing") or folder:
                for n in s.db.execute("SELECT path, MAX(mtime) m FROM chunks GROUP BY path ORDER BY m DESC LIMIT ?",
                                      (limit if not folder else 5000,)):
                    import datetime as _dt
                    if folder and not str(Path(n["path"])).startswith(folder):
                        continue
                    push(f"note:{n['path']}", Path(n["path"]).stem, "note",
                         _dt.datetime.fromtimestamp(n["m"]).isoformat(timespec="seconds"))
            art_kinds = [k for k in HUBS if k not in MEMORY_HUBS and k not in ("note", "action")]
            if kind in art_kinds or kind in ("creations", "tracked", "doing"):
                kinds = art_kinds if kind in ("creations", "tracked", "doing") else [kind]
                q = ",".join("?" * len(kinds))
                raw = s.db.execute("SELECT id,ts,kind,title,location FROM artifacts WHERE kind != 'note' "
                                   "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
                for a in raw:
                    k = _kind_of_artifact(a["kind"])
                    if k in kinds:
                        push(f"artifact:{a['id']}", a["title"] or Path(a["location"]).name, k, a["ts"],
                             {"sub": a["location"]})
            if kind in ("action", "actions", "tracked", "doing"):
                for t in s.db.execute("SELECT a.id,a.ts,a.title,a.status,a.ms,a.session, "
                                      "(SELECT COUNT(*) FROM activity b WHERE b.turn=a.id AND b.kind='tool') tools "
                                      "FROM activity a WHERE a.kind='user' AND a.turn=a.id ORDER BY a.id DESC LIMIT ?",
                                      (limit,)):
                    push(f"turn:{t['id']}", t["title"], "action", t["ts"],
                         {"status": t["status"] or "done", "ms": t["ms"], "tools": t["tools"],
                          "sub": _how(t["session"])})
        if kind in ("mission", "missions", "tracked", "doing"):
            from ..missions import missions as _missions
            for ms in _missions().all():
                pct = int((ms.get("progress") or 0) * 100)
                push(f"mission:{ms['id']}", ms["title"], "mission", ms["created"],
                     {"status": ms["status"], "sub": ms["when"] + (f" · {pct}% {ms.get('step') or ''}"
                                                                   if ms["status"] == "running" else "")})
                if ms.get("last_run") and ms["last_run"] > (items[-1]["last"] or ""):
                    items[-1]["last"] = ms["last_run"]
        if kind == "tracked":
            items = [i for i in items if i["track"] or i["pinned"] or i["note"]]
        if kind == "doing":
            items = [i for i in items if i["track"] == "doing"]
        items.sort(key=lambda i: (not i["pinned"], "" if not i["last"] else "~", i["last"] or ""), reverse=False)
        items.sort(key=lambda i: i["last"] or "", reverse=True)
        items.sort(key=lambda i: not i["pinned"])
        label = HUBS[kind][0] if kind in HUBS else GROUPS.get(kind, kind.title())
        if kind.startswith("cat:"):
            label = kind[4:].replace("/", " › ")
        color = HUBS[kind][1] if kind in HUBS else "#8b7bff"
        counts = {st: sum(1 for i in items if i["track"] == st) for st in ("todo", "doing", "waiting", "done")}
        counts["pinned"] = sum(1 for i in items if i["pinned"])
        return {"kind": kind, "label": label, "color": color, "items": items[:limit], "total": len(items),
                "counts": counts}

    # ── projects & skills (header tiles) ──────────────────
    def active_projects(self) -> dict:
        """Projects Nova knows about that you haven't marked done."""
        t = self.topic("project")
        live = [i for i in t["items"] if i["track"] != "done"]
        return {"active": len(live), "doing": sum(1 for i in live if i["track"] == "doing"),
                "done": sum(1 for i in t["items"] if i["track"] == "done")}

    def skills(self) -> dict:
        """Every skill (and plugin / connector) with a short description, its tools and how often it's used."""
        import ast
        from ..skills import SKILLS
        from ..tools import REGISTRY
        off = settings.disabled("skills")
        with context.store.lock:
            used = {r["title"]: (r["n"], r["last"]) for r in context.store.db.execute(
                "SELECT title, COUNT(*) n, MAX(ts) last FROM activity WHERE kind='tool' GROUP BY title")}
        by_mod: dict[str, list] = {}
        for t in REGISTRY.values():
            by_mod.setdefault(t.func.__module__, []).append(t)

        def doc_of(path: Path) -> str:
            try:
                d = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
            except Exception:
                return ""
            first = d.split("\n\n")[0].replace("\n", " ").strip()
            return first if len(first) <= 240 else first[:237].rsplit(" ", 1)[0] + "…"

        def entry(name, kind, module, path, enabled=True, core=False, desc=None):
            tools = sorted(by_mod.get(module, []), key=lambda t: t.name)
            n_used, last = 0, None
            for t in tools:
                u = used.get(t.name.replace("_", " "))
                if u:
                    n_used += u[0]
                    last = max(last or "", u[1])
            return {"name": name, "title": SKILL_TITLES.get(name, name.replace("_", " ").title()), "kind": kind,
                    "description": desc if desc is not None else doc_of(path), "enabled": enabled,
                    "loaded": bool(tools), "core": core, "uses": n_used, "last": last,
                    "tools": [{"name": t.name, "about": (t.description or "").split("\n")[0][:140],
                               "confirm": t.confirm} for t in tools]}

        here = Path(__file__).resolve().parents[1]
        out = [entry(n, "skill", f"nova.skills.{n}", here / "skills" / f"{n}.py", n not in off,
                     n in ("system", "memory", "maintenance")) for n in SKILLS]
        root = here.parent
        poff = settings.disabled("plugins")
        for f in sorted((root / "plugins").glob("*.py")):
            if not f.name.startswith("_"):
                out.append(entry(f.stem, "plugin", f"plugins.{f.stem}", f, f.stem not in poff))
        known = {e["name"] for e in out}
        mods = {m for m in by_mod if m not in {f"nova.skills.{n}" for n in SKILLS}
                and not m.startswith("plugins.")}
        for m in sorted(mods):
            name = m.rsplit(".", 1)[-1]
            if name in known:
                continue
            out.append(entry(name, "connector", m, Path("-"), True, False,
                             "Tools from connected MCP servers and other add-ons."))
        active = [e for e in out if e["enabled"] and e["loaded"]]
        return {"skills": out, "active": len(active), "tools": sum(len(e["tools"]) for e in active)}

    def _item(self, nid: str) -> dict:
        s = context.store
        kind, _, key = nid.partition(":")
        if kind == "turn" and key.isdigit():
            return self.turn(int(key))
        if kind == "mission" and key.isdigit():
            return self.mission(int(key))
        if kind == "person" and key.isdigit():
            return self.person(int(key))
        if kind == "memory":
            with s.lock:
                r = s.db.execute("SELECT * FROM memories WHERE id=?", (int(key),)).fetchone()
                history = s.db.execute("SELECT id,text,created FROM memories WHERE superseded_by=?", (int(key),)).fetchall()
            if not r:
                return {"error": "not found"}
            return {"id": nid, "type": r["kind"], "title": r["text"], "body": r["text"], "created": r["created"],
                    "turn": r["turn"], "superseded": bool(r["superseded_by"]),
                    "meta": {"source": r["source"], "importance": r["importance"], "times recalled": r["uses"],
                             "last changed": (r["updated"] or "")[:16].replace("T", " ")},
                    "history": [dict(h) for h in history], "related": self._related(r["text"], nid)}
        if kind == "note":
            p = Path(key)
            body = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else "(file missing)"
            import datetime as _dt
            with s.lock:
                a = s.db.execute("SELECT turn, ts FROM artifacts WHERE location=? ORDER BY id LIMIT 1", (key,)).fetchone()
            st = p.stat() if p.exists() else None
            return {"id": nid, "type": "note", "title": p.stem, "body": body[:20000], "location": str(p),
                    "created": a["ts"] if a else (_dt.datetime.fromtimestamp(st.st_ctime).isoformat(timespec="seconds")
                                                  if st else None),
                    "turn": a["turn"] if a else None, "openable": True,
                    "meta": {"words": len(body.split()),
                             "last edited": _dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                             if st else "—"},
                    "related": self._related(body[:1000], nid)}
        if kind == "artifact":
            with s.lock:
                r = s.db.execute("SELECT * FROM artifacts WHERE id=?", (int(key),)).fetchone()
            if not r:
                return {"error": "not found"}
            loc = r["location"]
            out = {"id": nid, "type": r["kind"], "title": r["title"], "body": r["detail"], "created": r["ts"],
                   "turn": r["turn"], "location": loc, "openable": True,
                   "related": self._related(f"{r['title']} {r['detail']}", nid)}
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
        context.store.log("open", "dashboard", f"Opened {Path(loc).name or loc}", loc, nid, turn=0)
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
                if ctype.startswith("text/html"):
                    self.send_header("Cache-Control", "no-cache")      # always show the newest dashboard/settings
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

            def _mjpeg(self):
                """Live webcam preview with the hand skeleton drawn on. When there's no picture it shows why
                (camera off, starting, blocked, black) instead of a blank box."""
                import time as _t

                from .. import camera
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                off_since = None
                try:
                    while True:
                        h = camera._hub
                        jpg = h.preview_jpeg() if h is not None and h.running else None
                        if jpg is None:
                            h = h or camera.CameraHub()
                            jpg = h.placeholder_jpeg()
                            off_since = off_since or _t.time()
                            if not h.running and _t.time() - off_since > 60:
                                break
                        else:
                            off_since = None
                        if jpg:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                             + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                            self.wfile.flush()
                        _t.sleep(0.12 if off_since is None else 0.5)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    pass

            def _host_ok(self) -> bool:
                """Blocks DNS-rebinding: only answer requests addressed to this PC's dashboard."""
                host = (self.headers.get("Host") or "").lower()
                allowed = {f"localhost:{dash.port}", f"127.0.0.1:{dash.port}", "localhost", "127.0.0.1"}
                allowed |= {h.lower() for h in (dash.cfg.get("dashboard") or {}).get("allowed_hosts", [])}
                if host in allowed:
                    return True
                from .. import remote                  # your PC's own Tailscale name (phone access)
                return remote.allowed_host(host)

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if not self._host_ok():
                    return self.send_error(403)
                try:
                    if u.path in ("/", "/index.html"):
                        return self._file(HERE / "index.html")
                    if u.path in ("/weather", "/weather.html"):
                        return self._file(HERE / "weather.html")
                    if u.path == "/api/weather":
                        from ..skills import weather
                        if q.get("place"):
                            return self._json(weather.fetch(q["place"]))
                        latest = resolve("workspace/weather/latest.json")
                        if not latest.exists():
                            return self._json(weather.fetch(""))
                        cached = json.loads(latest.read_text(encoding="utf-8"))
                        return self._json(weather.fetch(cached["place"]) if q.get("refresh") else cached)
                    if u.path in ("/globe", "/globe.html"):
                        return self._file(HERE / "globe.html")
                    if u.path == "/api/gesture":
                        from ..gestures import engine
                        e = engine()
                        return self._json({**e.status(), "events": e.recent(int(q.get("since", 0) or 0))})
                    if u.path.startswith("/api/tts/"):              # 🔊 a spoken reply, made on request
                        from .. import phone_voice
                        token = u.path.rsplit("/", 1)[-1]
                        if phone_voice.pending_text(token) is None:
                            if not (phone_voice.wants_live(token) and phone_voice.can_stream(self.headers.get("User-Agent", ""))):
                                return self.send_error(404)
                            chunks = phone_voice.live_stream(token)      # 🔊 spoken while the answer is written
                            try:
                                first = next(chunks)
                            except StopIteration:
                                return self.send_error(404)
                            except Exception as e:
                                print(f"[phone voice] live voice failed: {e}")
                                return self.send_error(503)
                            self.send_response(200)
                            self.send_header("Content-Type", "audio/mpeg")
                            self.send_header("Cache-Control", "no-store")
                            self.send_header("Connection", "close")
                            self.end_headers()
                            try:
                                self.wfile.write(first)
                                for c in chunks:
                                    self.wfile.write(c)
                                    self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                                pass
                            except Exception as e:
                                print(f"[phone voice] live voice stopped: {e}")
                            self.close_connection = True
                            return
                        if not self.headers.get("Range") and phone_voice.can_stream(self.headers.get("User-Agent", "")):
                            chunks = None
                            try:
                                chunks = phone_voice.stream(token)
                                first = next(chunks)
                            except Exception as e:
                                print(f"[phone voice] streaming failed, making a file instead: {e}")
                                first = None
                            if first is not None:
                                self.send_response(200)
                                self.send_header("Content-Type", "audio/mpeg")
                                self.send_header("Cache-Control", "no-store")
                                self.send_header("Connection", "close")
                                self.end_headers()
                                try:
                                    self.wfile.write(first)
                                    for c in chunks:
                                        self.wfile.write(c)
                                        self.wfile.flush()
                                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                                    pass
                                self.close_connection = True
                                return
                        f = phone_voice.render(token)
                        return self._file(f) if f else self.send_error(500)
                    if u.path.startswith("/api/audio/"):
                        from .. import phone_voice
                        f = phone_voice.audio_file(u.path.rsplit("/", 1)[-1])
                        return self._file(f) if f else self.send_error(404)
                    if u.path == "/api/camera.mjpg":
                        return self._mjpeg()
                    if u.path == "/api/remote":
                        from .. import remote
                        st = remote.finish_setup() if q.get("fresh") else remote.status()
                        return self._json({**st, "qr": remote.qr_svg(st["url"]) if st["url"] else "",
                                           "here": self.headers.get("Host", "")})
                    if u.path == "/api/globe/status":
                        from ..skills import globe
                        return self._json(globe.status())
                    if u.path in ("/settings", "/settings.html"):
                        return self._file(HERE / "settings.html")
                    if u.path == "/api/settings":
                        return self._json(settings.snapshot())
                    if u.path == "/api/theme":
                        return self._json(settings.theme())
                    if u.path.startswith("/vendor/"):
                        return self._file(HERE / "vendor" / Path(u.path).name)
                    if u.path == "/api/graph":
                        return self._json(dash.graph())
                    if u.path == "/api/phone":                   # the phone card: battery, storage, Wi-Fi, routines
                        from .. import phone_watch
                        return self._json(phone_watch.snapshot())
                    if u.path == "/api/stats":
                        from .. import phone_voice
                        live = bool((dash.cfg.get("voice") or {}).get("live_speech", True)) and \
                            phone_voice.can_stream(self.headers.get("User-Agent", ""))
                        return self._json({**dash.stats(), "live_voice": live})
                    if u.path == "/api/activity":
                        from .. import cards
                        return self._json({"events": context.store.activity(int(q.get("since", 0))),
                                           "status": context.store.status,
                                           "cards": cards.recent(int(q.get("cards", 0))) if "cards" in q else [],
                                           "card_seq": cards.latest_id(), "rev": dash.graph_rev()})
                    if u.path == "/api/brain/plan":
                        from .. import taxonomy
                        plan = taxonomy.load_plan()
                        return self._json({"summary": taxonomy.plan_summary(plan), "items": plan.get("items", [])[:1500],
                                           "checks": plan.get("checks", []), "backup": plan.get("last_backup", ""),
                                           "tree": {d: {c: m["subs"] for c, m in cats.items()}
                                                    for d, cats in taxonomy.TREE.items()}})
                    if u.path == "/api/pcstats":
                        from .. import cards
                        return self._json(cards.pc_stats())
                    if u.path == "/api/item":
                        return self._json(dash.item(q.get("id", "")))
                    if u.path == "/api/topic":
                        return self._json(dash.topic(q.get("kind", "memories")))
                    if u.path == "/api/skills":
                        return self._json(dash.skills())
                    if u.path == "/api/now":
                        return self._json(dash.now())
                    if u.path == "/api/flow":                    # the live work flow: what is in progress, step by step
                        from .. import autotag
                        return self._json(autotag.flow())
                    if u.path == "/api/focus":
                        from .. import focus, wellbeing
                        return self._json({**focus.state(), "wb": wellbeing.state()})
                    if u.path == "/api/screen_time":
                        from .. import activity
                        if not activity.running():
                            return self._json({"running": False})
                        try:
                            return self._json({"running": True, **activity.summary(q.get("period", "today"))})
                        except Exception as e:
                            return self._json({"running": True, "error": str(e)})
                    if u.path == "/api/wellbeing/trends":
                        from .. import wellbeing
                        days = min(120, int(q.get("days", "45")))
                        return self._json({"checkins": wellbeing.checkins(days=days), "signals": wellbeing.signals()})
                    if u.path == "/wellbeing/report":
                        from .. import wellbeing
                        body = wellbeing.report(min(120, int(q.get("days", "30"))),
                                                include_notes=q.get("notes", "0") == "1").encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        return self.wfile.write(body)
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
                origin = self.headers.get("Origin", "").split("//")[-1].lower()
                host = self.headers.get("Host", "").lower()
                if not self._host_ok() or origin not in ("", host, host.split(":")[0]):
                    self.rfile.read(length)
                    return self._json({"error": "forbidden"}, 403)
                if self.path.startswith("/api/upload"):          # 📎 a file dropped on the dashboard → the brain
                    from .. import inbox
                    q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                    limit = int((dash.cfg.get("dashboard") or {}).get("max_upload_mb", 500)) * 1024 * 1024
                    if length > limit:
                        self.close_connection = True
                        return self._json({"error": f"That file is bigger than {limit // (1024 * 1024)} MB."}, 413)
                    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(q.get("name", "upload")).name).strip(". ")
                    dest = resolve("workspace/inbox/uploads") / (name[:150] or "upload")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    n = 2
                    while dest.exists():
                        dest = dest.with_name(f"{Path(name).stem} ({n}){Path(name).suffix}")
                        n += 1
                    left = length
                    with open(dest, "wb") as f:
                        while left > 0:
                            chunk = self.rfile.read(min(1 << 20, left))
                            if not chunk:
                                break
                            f.write(chunk)
                            left -= len(chunk)
                    try:
                        if context.store:
                            context.store.set_status("thinking")
                        r = inbox.file_document(dest, source="dashboard", note=q.get("note", "")[:500])
                        return self._json({**r, "message": inbox.reply_text(r)})
                    except Exception as e:
                        return self._json({"error": f"I saved {dest.name} but couldn't file it: {e}"}, 500)
                    finally:
                        if context.store:
                            context.store.set_status("idle")
                if self.path.split("?")[0] == "/api/voice":      # 🎤 a recording from the phone / browser
                    from .. import phone_voice
                    if length > phone_voice.MAX_BYTES:
                        return self._json({"error": "That recording is too long."}, 413)
                    data = self.rfile.read(length)
                    try:
                        if context.store:
                            context.store.set_status("thinking")
                        tok = (parse_qs(urlparse(self.path).query).get("live") or [""])[0]
                        return self._json(phone_voice.converse(dash.agent, data,
                                                               self.headers.get("Content-Type", ""), tok))
                    except Exception as e:
                        return self._json({"error": str(e)}, 500)
                    finally:
                        if context.store:
                            context.store.set_status("idle")
                body = json.loads(self.rfile.read(length) or b"{}")
                try:
                    if self.path == "/api/settings":
                        try:
                            return self._json(settings.apply(body))
                        except Exception as e:
                            import traceback
                            traceback.print_exc()
                            return self._json({"saved": [], "errors": {"_": f"{type(e).__name__}: {e}"}})
                    if self.path == "/api/settings/test":
                        return self._json(settings.run_test(str(body.get("kind", ""))))
                    if self.path == "/api/restart":
                        settings.restart_soon()
                        return self._json({"message": "Restarting…"})
                    if self.path == "/api/mission":
                        from ..missions import missions as _missions
                        mid, act = int(body.get("id", 0)), str(body.get("action", ""))
                        fn = {"pause": _missions().pause, "resume": _missions().resume, "run": _missions().run_now,
                              "delete": _missions().delete}.get(act)
                        return self._json({"message": fn(mid) if fn else "unknown action"})
                    if self.path == "/api/watch/cancel":
                        from ..watcher import manager
                        n = manager().cancel(int(body["id"]) if body.get("id") else None)
                        return self._json({"stopped": n})
                    if self.path == "/api/gesture":
                        from ..gestures import engine
                        on = body.get("on")
                        msg = engine().start() if on else engine().stop()
                        return self._json({"message": msg, **engine().status()})
                    if self.path == "/api/link":
                        nid = str(body.get("id", ""))
                        if not re.fullmatch(r"(memory|artifact|turn|mission):\d+|note:.+", nid):
                            return self._json({"error": "unknown item"}, 400)
                        proj = body.get("project")
                        if proj not in (None, "") and not re.fullmatch(r"memory:\d+", str(proj)):
                            return self._json({"error": "unknown project"}, 400)
                        return self._json(dash.set_link(nid, proj))
                    if self.path == "/api/remote":
                        from .. import remote
                        if not self.headers.get("Host", "").lower().startswith(("localhost", "127.0.0.1")):
                            return self._json({"ok": False, "message": "Change remote access from the PC itself."}, 403)
                        return self._json(remote.enable() if body.get("on") else remote.disable())
                    if self.path == "/api/globe/start":
                        from ..skills import globe
                        msg = globe.start(wait=90)
                        return self._json({"message": msg, "running": globe.running()})
                    if self.path == "/api/globe/install":
                        from ..skills import globe
                        return self._json({"message": globe.install()})
                    if self.path == "/api/phone/do":             # the phone card's buttons
                        from ..tools import REGISTRY
                        what = str(body.get("do", ""))
                        calls = {"ring": ("phone_ring", {}), "reconnect": ("phone_reconnect", {}),
                                 "screenshot": ("phone_screenshot", {}), "health": ("phone_health", {}),
                                 "locate": ("phone_locate", {}),
                                 "mirror": ("phone_show_screen", {}), "unmirror": ("phone_hide_screen", {}),
                                 "photo": ("phone_take_photo", {}),
                                 "routine": ("phone_run_routine", {"name": str(body.get("name", ""))})}
                        if what not in calls or calls[what][0] not in REGISTRY:
                            return self._json({"error": "I don't know that phone action."}, 400)
                        name, args = calls[what]
                        context.begin_turn("dashboard")
                        if what == "routine":                    # can take a while: run it and report as a banner
                            def _go():
                                context.begin_turn("dashboard")
                                out = REGISTRY[name].run(args)
                                context.store.log("notice", "phone", out[:200], out, turn=0)
                            threading.Thread(target=_go, daemon=True).start()
                            return self._json({"message": f"Running {args['name']}…"})
                        out = str(REGISTRY[name].run(args))
                        link = re.search(r"https://www\.google\.com/maps\S+", out)
                        return self._json({"message": out.removeprefix("ERROR: "), "ok": not out.startswith("ERROR"),
                                           "url": link.group(0) if link else ""})
                    if self.path == "/api/send_file":            # "Send to Telegram" under a picture on the dashboard
                        loc = dash.location_of(str(body.get("id", "")))
                        if not loc or not Path(loc).is_file():
                            return self._json({"error": "That file isn't there any more."}, 404)
                        from ..tools import REGISTRY
                        context.begin_turn("dashboard")
                        return self._json({"message": REGISTRY["send_to_phone"].run({"path": loc})})
                    if self.path in ("/api/brain/move", "/api/brain/delete"):
                        from .. import taxonomy
                        nid = str(body.get("id", ""))
                        if not re.fullmatch(r"(memory|mission):\d+|note:.+", nid):
                            return self._json({"error": "That item can't be moved or deleted from here."}, 400)
                        try:
                            if self.path.endswith("/move"):
                                return self._json(taxonomy.move_item(nid, str(body.get("domain", "")),
                                                                     str(body.get("category", "")), str(body.get("sub", ""))))
                            return self._json(taxonomy.delete_item(nid))
                        except (ValueError, KeyError) as e:
                            return self._json({"error": str(e)}, 400)
                    if self.path == "/api/track":
                        nid = str(body.get("id", ""))
                        if not re.fullmatch(r"(memory|artifact|turn|mission):\d+|note:.+", nid):
                            return self._json({"error": "unknown item"}, 400)
                        try:
                            return self._json(context.store.set_tracking(
                                nid, body.get("status"), body.get("pinned"), body.get("note")))
                        except ValueError as e:
                            return self._json({"error": str(e)}, 400)
                    if self.path == "/api/open":
                        return self._json({"message": dash.open_item(body.get("id", ""))})
                    if self.path == "/api/google/client":
                        from ..config import ROOT as _ROOT
                        from ..skills import google_ws
                        try:
                            cid = google_ws.save_client_json(str(body.get("json", "")), _ROOT / "secrets" / "credentials.json")
                            return self._json({"ok": True, "message": f"Saved your Google client ({cid[:18]}…). Now click Connect Google."})
                        except ValueError as e:
                            return self._json({"ok": False, "message": str(e)})
                    if self.path == "/api/focus":
                        from .. import focus
                        a, tid = body.get("action"), int(body.get("id") or 0)
                        if a == "add":
                            focus.add(str(body.get("text", "")), body.get("where", "now"), body.get("source", ""))
                        elif a == "dump":
                            focus.brain_dump(str(body.get("text", "")))
                        elif a == "breakdown":
                            focus.breakdown(tid)
                        elif a in ("done", "later", "open-next", "open-now", "delete"):
                            focus.set_status(tid, a)
                        return self._json({"ok": True})
                    if self.path == "/api/wellbeing":
                        from .. import wellbeing
                        a = body.get("action")
                        if a == "checkin":
                            wellbeing.checkin(body.get("energy"), body.get("mood"), body.get("sleep"), body.get("note", ""))
                        elif a == "anchor":
                            wellbeing.tick_anchor(str(body.get("name", "")), bool(body.get("done", True)))
                        elif a == "dismiss":
                            wellbeing.dismiss_headsup()
                        elif a == "low":
                            wellbeing.set_low_energy(bool(body.get("on")))
                        elif a in ("send", "drop"):
                            return self._json({"ok": True, "message": wellbeing.release(int(body.get("id", 0)), a)})
                        return self._json({"ok": True})
                    if self.path == "/api/ask_brain":
                        from .. import answers
                        return self._json(answers.ask(str(body.get("question", ""))[:1000]))
                    if self.path == "/api/person":
                        from .. import people as _people
                        pid = int(str(body.get("id", "")).removeprefix("person:") or 0)
                        if body.get("delete"):
                            _people.delete(pid)
                            return self._json({"ok": True})
                        if body.get("merge_into"):
                            keep = int(str(body["merge_into"]).removeprefix("person:"))
                            _people.merge(keep, pid)
                            return self._json({"ok": True, "id": f"person:{keep}"})
                        if body.get("new"):
                            new_id, _ = _people.upsert(str(body.get("name", "")), str(body.get("company", "")),
                                                       str(body.get("role", "")), str(body.get("email", "")),
                                                       str(body.get("phone", "")), source="dashboard")
                            return self._json({"ok": True, "id": f"person:{new_id}"})
                        fields = {k: body[k] for k in ("name", "company", "role", "email", "phone", "notes", "aliases")
                                  if k in body}
                        return self._json({"ok": True, "person": _people.update(pid, **fields)})
                    if self.path == "/api/brain/plan":
                        from .. import taxonomy
                        act = body.get("action")
                        if act == "build":
                            if not taxonomy._build["running"]:
                                threading.Thread(target=taxonomy.build_plan, daemon=True, name="brain-plan").start()
                            return self._json({"ok": True, "message": "Working out where everything belongs…"})
                        if act == "answer":
                            return self._json(taxonomy.answer(str(body.get("rel", "")), str(body.get("domain", "")),
                                                              str(body.get("category", "")), str(body.get("sub", "")),
                                                              str(body.get("name", ""))))
                        if act == "keep":                       # "yes, that's the right place"
                            plan = taxonomy.load_plan()
                            plan["checks"] = [c for c in plan.get("checks", []) if c["rel"] != body.get("rel")]
                            taxonomy.save_plan(plan)
                            return self._json({"ok": True})
                        if act == "apply":
                            return self._json({"ok": True, **taxonomy.apply_plan(bool(body.get("include_unanswered")))})
                        return self._json({"error": "unknown action"}, 400)
                    if self.path == "/api/ask":
                        # the voice starts with the first sentence, while the rest is still being written
                        speaker = feed = None
                        live_ok = bool((self_cfg_voice := (dash.cfg.get("voice") or {})).get("live_speech", True))
                        if body.get("speak") and context.speech and live_ok:       # out loud on the PC's speakers
                            from ..live_speech import LiveSpeaker
                            speaker = LiveSpeaker(context.speech, int(self_cfg_voice.get("max_spoken_chars", 450)))
                            feed = speaker.feed
                        elif body.get("voice") and body.get("live") and live_ok:   # on the device you're using
                            from .. import phone_voice
                            feed = phone_voice.open_live(str(body.get("live")))
                        try:
                            reply = dash.agent.handle(body.get("text", ""), session="dashboard",
                                                      **({"on_delta": feed} if feed else {}))
                        except Exception:
                            if feed:
                                feed.finish()
                            raise
                        if feed:
                            feed.finish(reply.text)
                        elif body.get("speak") and context.speech:
                            threading.Thread(target=context.speech.speak, args=(reply.text,), daemon=True).start()
                        audio = None
                        if body.get("voice"):                              # a finished file too, in case live didn't play
                            from .. import phone_voice
                            audio = phone_voice.reply_audio(reply.text)
                        return self._json({"text": reply.text, "files": reply.files, "audio": audio,
                                           "live": bool(feed and not speaker)})
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
