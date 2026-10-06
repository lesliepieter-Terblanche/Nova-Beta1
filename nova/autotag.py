"""Nova tags her own work: every command and every project carries one of the four statuses, without you asking.

  command   IN-PROGRESS while she works on it · WAITING-ON-USER while she waits for your yes/no (or it failed) ·
            COMPLETED when she has answered
  project   IN-PROGRESS from the moment she does real work for it (builds, writes, sends, files — not just looks
            something up) · WAITING-ON-USER while one of its commands waits on you · COMPLETED when you say it's done
            ("mark Harbour Homes as completed") — only you know when a project is finished

A status you set by hand always stays; the one exception is Backlog, which only means "not started yet".
`flow()` describes the work in progress step by step (request → brain → thinking → tools → reply) for the dashboard's
live work-flow view.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from . import context

TURN_TAG = {"running": "IN-PROGRESS", "waiting": "WAITING-ON-USER", "error": "WAITING-ON-USER"}
TURN_TRACK = {"running": "doing", "waiting": "waiting", "error": "waiting"}      # the same, as a tracking status
FRESH_HOURS = 24                      # a failed command is listed as a bottleneck for a day, then it is just history

# tools that only look something up — using one of these alone doesn't mean a project is being worked on
READ_ONLY = re.compile(
    r"^(get|list|read|recall|find|look|show|what|where|check|scrape|ask|recent|planes)_"
    r"|^(recall|spending|screen_time|time_spent_on|person_card|brain_structure|brain_digest|focus_now|held_actions|"
    r"last_dream|undo_list|calendar_events|web_search|set_status|work_status)$"
    r"|_(search|read|status|look|list|events|notifications|messages|missed|health|scheduled|report)$")

WORDS = {"todo": "backlog", "doing": "in progress", "waiting": "waiting on you", "done": "completed"}
SAY = {"backlog": "todo", "todo": "todo", "to do": "todo", "not started": "todo", "later": "todo", "someday": "todo",
       "in progress": "doing", "in-progress": "doing", "doing": "doing", "started": "doing", "busy": "doing",
       "active": "doing", "working on it": "doing", "ongoing": "doing",
       "waiting": "waiting", "waiting on me": "waiting", "waiting on user": "waiting", "waiting-on-user": "waiting",
       "blocked": "waiting", "stuck": "waiting", "on hold": "waiting",
       "completed": "done", "complete": "done", "done": "done", "finished": "done", "closed": "done",
       "shipped": "done", "none": "", "clear": "", "untag": ""}


def tag_of_turn(status: str) -> str:
    """The four-status tag of a command, from how the request went."""
    return TURN_TAG.get(status or "done", "COMPLETED")


def turn_tracking(tid: int, status: str, saved: dict | None = None) -> dict:
    """The tracking of a command as the dashboard shows it: the status you gave it by hand if you did, else the
    tag Nova gives it from how the request went (auto=1) — so a command is never left on "None"."""
    cur = dict(saved) if saved is not None else context.store.get_tracking(f"turn:{tid}")
    cur.setdefault("auto", 0)
    if not cur.get("status"):
        cur["status"] = TURN_TRACK.get(status or "done", "done")
        cur["auto"] = 1
    return cur


def is_fresh(ts: str, hours: int = FRESH_HOURS) -> bool:
    try:
        return dt.datetime.now() - dt.datetime.fromisoformat(ts) < dt.timedelta(hours=hours)
    except (TypeError, ValueError):
        return False


def did_work(tools_used: list[str]) -> bool:
    return any(not READ_ONLY.search(t) for t in tools_used or [])


# ── which project is this about? ──────────────────────────
def _projects(s) -> list[dict]:
    with s.lock:
        rows = s.db.execute("SELECT id,text,created,source FROM memories WHERE superseded_by IS NULL AND kind='project' "
                            "ORDER BY id").fetchall()
    return [{"id": f"memory:{r['id']}", "text": r["text"], "vec": None, "created": r["created"], "source": r["source"]}
            for r in rows]


def _owner() -> str:
    try:
        return str(context.cfg.assistant.owner)
    except Exception:
        return ""


def project_of(item: str, text: str) -> tuple[str, dict]:
    """(project id or "", {project: parent project}) — by the project's name in the text, or your own link."""
    s = context.store
    if s is None:
        return "", {}
    from .dashboard.organize import organize
    with s.lock:
        s.db.execute("CREATE TABLE IF NOT EXISTS links(item TEXT PRIMARY KEY, project TEXT)")
        row = s.db.execute("SELECT project FROM links WHERE item=?", (item,)).fetchone()
    res = organize(_projects(s), [{"id": item, "text": text, "vec": None}], {item: row["project"]} if row else {},
                   owner=_owner())
    return res["item_project"].get(item, ""), res["parents"]


def after_turn(turn: int, text: str, tools_used: list[str], status: str) -> str:
    """A command has ended: if Nova did real work for a project, the project (and the one it belongs to) is now
    in progress. Returns the project it tagged, or ""."""
    s = context.store
    if s is None or not turn or status == "error" or not did_work(tools_used):
        return ""
    pid, parents = project_of(f"turn:{turn}", text)
    tagged = ""
    for _ in range(4):
        if not pid:
            break
        if s.auto_status(pid, "doing") and not tagged:
            tagged = pid
        pid = parents.get(pid, "")
    return tagged


# ── by voice: "mark Harbour Homes as completed" ───────────
def find_item(name: str) -> tuple[str, str]:
    """(item id, title) of the project — else mission or tracked item — that best matches a spoken name."""
    s = context.store
    low = re.sub(r"\b(the|my|our|project|mission)\b", " ", (name or "").lower())
    want = set(re.findall(r"[a-z0-9]{3,}", low))
    if s is None or not want:
        return "", ""
    with s.lock:
        cands = [(f"memory:{r['id']}", r["text"], 2) for r in s.db.execute(
            "SELECT id,text FROM memories WHERE superseded_by IS NULL AND kind='project'")]
        cands += [(f"memory:{r['id']}", r["text"], 1) for r in s.db.execute(
            "SELECT m.id, m.text FROM memories m JOIN tracking t ON t.item='memory:'||m.id "
            "WHERE m.superseded_by IS NULL AND m.kind!='project'")]
        try:
            cands += [(f"mission:{r['id']}", r["title"], 1) for r in s.db.execute(
                "SELECT id,title FROM missions WHERE status!='deleted'")]
        except Exception:
            pass
    best, score = ("", ""), 0.0
    for iid, text, weight in cands:
        have = set(re.findall(r"[a-z0-9]{3,}", text.lower()))
        hit = len(want & have)
        if not hit:
            continue
        sc = hit / len(want) + 0.2 * hit / max(len(have), 1) + 0.05 * weight
        if sc > score:
            best, score = (iid, text), sc
    return best if score >= 0.5 else ("", "")


# ── the live work flow ────────────────────────────────────
_CHANNEL = {"voice": ("🎤", "You said"), "dashboard": ("⌨️", "You typed"), "telegram": ("✈️", "You sent"),
            "phone": ("📱", "You said"), "gesture": ("✋", "You signalled")}
_TOOL_ICON = [(r"^phone_|_phone$", "📱"), (r"gmail|email", "✉️"), (r"calendar", "📅"), (r"^browser_|web_agent", "🌐"),
              (r"web_search|scrape", "🔎"), (r"website", "🌐"), (r"file|folder|path|zip|document|tables", "📄"),
              (r"note|remember|recall|brain|memory|journal", "🧠"), (r"video|photo|image|voiceover|media|ad$", "🎬"),
              (r"shell|app|window|click|type|keys|pc", "🖥️"), (r"mission", "🚀"), (r"slip|expense|budget|claim", "💰"),
              (r"drive|docs|sheets|slides|tasks", "📂"), (r"weather", "⛅")]


def _icon(tool: str) -> str:
    for pat, ic in _TOOL_ICON:
        if re.search(pat, tool):
            return ic
    return "⚙️"


def _short(v, n: int = 70) -> str:
    t = " ".join(str(v).split())
    return t if len(t) <= n else t[:n - 1] + "…"


def _args_line(detail: str) -> tuple[str, str]:
    """(what went in, what came out) of a tool step, in a few words."""
    try:
        d = json.loads(detail or "")
    except ValueError:
        return _short(detail or ""), ""
    if not isinstance(d, dict):
        return _short(d), ""
    args = d.get("args", d) if isinstance(d.get("args", d), dict) else {}
    first = str(next((v for v in args.values() if v not in ("", None)), ""))
    if re.match(r"^([A-Za-z]:)?[\\/~.]", first) and " " not in first.strip()[:3]:      # a path: the file's name will do
        first = re.split(r"[\\/]", first.rstrip("\\/"))[-1] or first
    return _short(first, 60), _short(d.get("result", ""), 90) if "result" in d else ""


def _lane_for_turn(s, tid: int, live: bool) -> dict | None:
    with s.lock:
        r = s.db.execute("SELECT * FROM activity WHERE id=? AND kind='user'", (tid,)).fetchone()
        if not r:
            return None
        rows = s.db.execute("SELECT * FROM activity WHERE turn=? AND id!=? ORDER BY id", (tid, tid)).fetchall()
        n_recalled = s.db.execute("SELECT COUNT(DISTINCT item) FROM uses WHERE turn=?", (tid,)).fetchone()[0]
        made = [m["text"] for m in s.db.execute("SELECT text FROM memories WHERE turn=?", (tid,))]
        made += [a["title"] or a["location"] for a in s.db.execute("SELECT title, location FROM artifacts WHERE turn=?", (tid,))]
    status = r["status"] or "done"
    session = (r["session"] or "").split(":")[0]
    icon, verb = _CHANNEL.get(session, ("🚀", "Mission step") if session == "mission" else ("💬", "You asked"))
    request = r["detail"] or r["title"]
    nodes = [{"kind": "you", "icon": icon, "title": verb, "sub": _short(r["title"] if session == "mission" else request, 80),
              "state": "done", "out": "your words"}]
    if n_recalled:
        nodes.append({"kind": "brain", "icon": "🧠", "title": "2nd brain",
                      "sub": f"recalled {n_recalled} thing{'s' if n_recalled != 1 else ''} she knows",
                      "state": "done", "out": "what she remembers"})
    tools = [a for a in rows if a["kind"] == "tool"]
    reply = next((a for a in rows if a["kind"] == "reply"), None)
    timing = next((a for a in rows if a["kind"] == "timing"), None)
    model = ""
    if timing:
        try:
            t = json.loads(timing["detail"] or "{}")
            name = str(t.get("model") or "").strip("?")
            model = f"{name + ' · ' if name else ''}{t.get('model_ms', 0) / 1000:.1f} s of thinking"
        except ValueError:
            pass
    busy_tool = any(a["status"] in ("running", "waiting") for a in tools)
    thinking = status == "running" and not busy_tool
    nodes.append({"kind": "think", "icon": "✨", "title": "Nova thinks",
                  "sub": model or ("working out what to do" if thinking and not tools else "decided what to do"),
                  "state": "running" if thinking and not tools else "done", "out": "instructions"})
    for a in tools:
        name = a["title"].replace(" (waiting for your yes/no)", "")
        went_in, came_out = _args_line(a["detail"])
        st = {"ok": "done", "approved": "done", "running": "running", "waiting": "waiting", "error": "error",
              "declined": "skipped", "expired": "skipped", "dropped": "skipped"}.get(a["status"] or "ok", "done")
        nodes.append({"kind": "tool", "icon": _icon(name.replace(" ", "_")), "title": name[:1].upper() + name[1:],
                      "sub": ("needs your yes or no" if st == "waiting" else went_in), "result": came_out,
                      "state": st, "ms": a["ms"], "out": "result" if st in ("done", "running") else ""})
    if made:
        nodes.append({"kind": "brain", "icon": "💾", "title": "Saved in brain",
                      "sub": _short(made[0], 60) + (f" +{len(made) - 1} more" if len(made) > 1 else ""),
                      "state": "done", "out": ""})
    if status == "waiting":
        nodes.append({"kind": "reply", "icon": "❓", "title": "Asked you", "sub": _short(reply["detail"] if reply else "", 80),
                      "state": "waiting"})
    else:
        nodes.append({"kind": "reply", "icon": "💬", "title": "Reply",
                      "sub": _short(reply["detail"] if reply else ("writing the answer…" if thinking and tools else ""), 80),
                      "state": "error" if status == "error" else "done" if reply else
                      "running" if thinking and tools else "todo"})
    pid, _ = project_of(f"turn:{tid}", request)
    project = None
    if pid:
        from .dashboard.organize import short_title
        with s.lock:
            pr = s.db.execute("SELECT text FROM memories WHERE id=?", (pid.split(":")[1],)).fetchone()
        project = {"id": pid, "title": short_title(pr["text"]) if pr else ""}
    return {"id": f"turn:{tid}", "kind": "command", "title": _short(r["title"], 110), "tag": tag_of_turn(status),
            "status": status, "live": live, "channel": session, "started": r["ts"], "ms": r["ms"], "project": project,
            "nodes": nodes}


def _lane_for_mission(mid: int, live: dict) -> dict | None:
    from .missions import missions
    m = missions().get(mid)
    if not m:
        return None
    runs = missions().runs(mid, 1)
    plan = (runs[0]["plan"] if runs else []) or []
    done = len(runs[0]["steps"]) if runs else 0
    nodes = [{"kind": "you", "icon": "🚀", "title": "Mission", "sub": _short(m["goal"] or m["title"], 80), "state": "done",
              "out": "the goal"},
             {"kind": "think", "icon": "✨", "title": "Plan", "sub": f"{len(plan)} steps" if plan else "planning…",
              "state": "done" if plan else "running", "out": "steps"}]
    for i, p in enumerate(plan):
        nodes.append({"kind": "tool", "icon": "⚙️", "title": _short(p.get("title", f"Step {i + 1}"), 40),
                      "sub": _short(p.get("do", ""), 60), "out": "findings",
                      "state": "done" if i < done else "running" if i == done and live.get("status") != "writing" else "todo"})
    nodes.append({"kind": "reply", "icon": "📝", "title": "Report",
                  "sub": "writing the report…" if live.get("status") == "writing" else "",
                  "state": "running" if live.get("status") == "writing" else "todo"})
    return {"id": f"mission:{mid}", "kind": "mission", "title": _short(m["title"], 110), "tag": "IN-PROGRESS",
            "status": "running", "live": True, "channel": "mission", "started": m.get("last_run") or m["created"],
            "ms": None, "project": None, "progress": live.get("progress", 0), "nodes": nodes}


def flow(limit: int = 3) -> dict:
    """What is in progress right now and how the work moves: {"busy", "lanes": [...]}. One lane per thing — the
    command being worked on, commands waiting for your yes/no, running missions. When nothing is, the last command."""
    s = context.store
    if s is None:
        return {"busy": False, "lanes": []}
    lanes = []
    tid = s.current_turn
    with s.lock:
        cur = s.db.execute("SELECT session FROM activity WHERE id=?", (tid,)).fetchone() if tid else None
        waiting = [r["id"] for r in s.db.execute(
            "SELECT id FROM activity WHERE kind='user' AND turn=id AND status='waiting' ORDER BY id DESC LIMIT 3")]
    if tid and cur and not str(cur["session"]).startswith("mission:"):
        lane = _lane_for_turn(s, tid, True)
        if lane:
            lanes.append(lane)
    for w in waiting:
        if w != tid:
            lane = _lane_for_turn(s, w, True)
            if lane:
                lanes.append(lane)
    try:
        from .missions import _missions
        for mid, live in list(_missions.running.items()) if _missions else []:
            lane = _lane_for_mission(mid, live)
            if lane:
                lanes.append(lane)
    except Exception as e:
        print(f"[flow] missions: {e}")
    busy = bool(lanes)
    if not lanes:
        with s.lock:
            last = s.db.execute("SELECT id FROM activity WHERE kind='user' AND turn=id AND session NOT LIKE 'mission:%' "
                                "ORDER BY id DESC LIMIT 1").fetchone()
        if last:
            lane = _lane_for_turn(s, last["id"], False)
            if lane:
                lanes.append(lane)
    return {"busy": busy, "lanes": lanes[:limit]}
