"""Status tags by voice: "mark Harbour Homes as completed", "what's in progress?".
Nova tags commands and projects herself as she works (nova/autotag.py); these two tools are for the things only
you can know — that a project is finished, parked, or waiting on you."""
from __future__ import annotations

from pathlib import Path

from .. import autotag, context
from ..tools import register_group, tool

register_group("status", ["in progress", "mark ", "tag ", "status of", "status to", "as completed", "as complete",
                          "as done", "is done", "is finished", "is complete", "backlog", "waiting on", "on hold",
                          "what are you working on", "what are you busy with", "outstanding", "bottleneck",
                          "what's open", "whats open", "still open", "park ", "start up", "startup", "start-up",
                          "slow to start", "so long to start", "long to load"])


@tool(group="status")
def set_status(item: str, status: str) -> str:
    """Tag a project (or mission) with a status because the user says so: "mark the Harbour Homes project as
    completed", "Riverbend is on hold", "put Orchard back in the backlog".

    Args:
        item: the project's name as the user said it
        status: backlog, in progress, waiting or completed (none removes the tag)
    """
    key = " ".join(str(status).lower().replace("_", " ").split())
    if key not in autotag.SAY:
        return "ERROR: status must be backlog, in progress, waiting or completed."
    iid, title = autotag.find_item(item)
    if not iid:
        return f"I couldn't find a project called '{item}'. Say its name as it appears on the dashboard."
    if iid.startswith("mission:"):
        return "Missions tag themselves from how their runs go — pause, resume or delete it with manage_mission."
    context.store.set_tracking(iid, status=autotag.SAY[key])
    word = autotag.WORDS.get(autotag.SAY[key], "untagged")
    return f"Tagged '{title[:80]}' as {word}." if autotag.SAY[key] else f"Removed the tag from '{title[:80]}'."


def _title(s, item: str) -> str:
    if item.startswith("note:"):
        return Path(item[5:]).stem.replace("_", " ")
    if item.startswith("memory:"):
        with s.lock:
            r = s.db.execute("SELECT text FROM memories WHERE id=?", (item[7:],)).fetchone()
        from ..dashboard.organize import short_title
        return short_title(r["text"], 8) if r else ""
    return ""


@tool(group="status")
def work_status() -> str:
    """What is in progress right now, what is waiting on the user, and how much is in the backlog or completed.
    Use for "what are you working on", "what's in progress", "what's waiting on me", "what's outstanding"."""
    s = context.store
    live = [ln for ln in autotag.flow()["lanes"] if ln["live"] and ln["id"] != f"turn:{s.current_turn}"]
    with s.lock:
        rows = [dict(r) for r in s.db.execute("SELECT item, status FROM tracking WHERE status!='' ORDER BY updated DESC")]
    by: dict[str, list[str]] = {"doing": [], "waiting": [], "todo": [], "done": []}
    for r in rows:
        t = _title(s, r["item"])
        if t:
            by[r["status"]].append(t)
    from .. import taxonomy
    waiting = [b["text"] for b in taxonomy.bottlenecks()]
    waiting += [ln["title"] for ln in live if ln["tag"] == "WAITING-ON-USER" and ln["title"] not in waiting]
    parts = []
    running = [ln["title"] for ln in live if ln["tag"] == "IN-PROGRESS"]
    if running:
        parts.append("Working on right now: " + "; ".join(running) + ".")
    parts.append(("In progress: " + "; ".join(by["doing"][:8]) + ".") if by["doing"] else "Nothing is tagged in progress.")
    parts.append(("Waiting on you: " + "; ".join(waiting[:8]) + ".") if waiting else "Nothing is waiting on you.")
    parts.append(f"{len(by['todo'])} in the backlog, {len(by['done'])} completed.")
    return " ".join(parts)


@tool(group="status")
def startup_report() -> str:
    """How long Nova took to start and what was slow — for "why do you take so long to start up?"."""
    from .. import startup
    from ..config import resolve
    rows = startup.stages()
    return startup.report(rows if len(rows) >= 3 else startup.last(resolve("data/startup.json")))
