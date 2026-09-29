"""Missions by voice: give Nova a goal to work on by itself — now or on a schedule — and check on it."""
from __future__ import annotations

from ..tools import register_group, tool

register_group("missions", ["mission", "missions", "every monday", "every morning", "every week", "every day",
                            "each week", "keep track of", "monitor", "research and brief", "work on this",
                            "in the background", "by yourself", "autonomously"])


def _m():
    from ..missions import missions
    return missions()


@tool(group="missions")
def start_mission(goal: str, schedule: str = "", title: str = "") -> str:
    """Give Nova a goal to work on by itself in the background: it plans the steps, does the research/work with its
    tools, writes a report into the 2nd brain and sends the summary. Can repeat on a schedule.
    Args:
        goal: what to achieve, in full (who/what/where, what the result should be)
        schedule: "" for once now, or e.g. "every monday 08:00", "weekdays 7:30", "daily 18:00", "every 6 hours"
        title: optional short name
    """
    from ..missions import describe
    try:
        m = _m().create(goal, schedule, title)
    except ValueError as e:
        return f"ERROR: {e}"
    when = "I'm starting now" if m["schedule"]["type"] == "once" or not schedule else \
        f"It runs {describe(m['schedule'])} — the first run starts now"
    return f"Mission #{m['id']} '{m['title']}' is set. {when}; I'll send you the report when it's done."


@tool(group="missions")
def list_missions() -> str:
    """List Nova's missions with their status and schedule."""
    ms = _m().all()
    if not ms:
        return "There are no missions yet. Say something like 'start a mission to…'."
    parts = []
    for m in ms[:12]:
        state = m["status"] if m["status"] != "running" else f"running ({int(m.get('progress', 0) * 100)}%: {m['step']})"
        nxt = f", next {m['next_run'][5:16].replace('T', ' ')}" if m.get("next_run") and m["status"] == "scheduled" else ""
        parts.append(f"#{m['id']} {m['title']} — {state}, {m['when']}{nxt}")
    return "; ".join(parts) + "."


@tool(group="missions")
def mission_report(mission_id: int) -> str:
    """The latest result of a mission (summary and where the full report is).
    Args:
        mission_id: the mission number
    """
    m = _m().get(int(mission_id))
    if not m:
        return f"No mission #{mission_id}."
    if m["status"] == "running":
        return f"Mission #{m['id']} is still running — {int(m.get('progress', 0) * 100)}% ({m['step']})."
    runs = _m().runs(m["id"], 1)
    if not runs:
        return f"Mission #{m['id']} hasn't run yet (next: {m.get('next_run') or 'not scheduled'})."
    r = runs[0]
    return f"{r['summary']}\n\nFull report: {r['report']}" if r["report"] else (r["summary"] or "No result yet.")


@tool(group="missions")
def manage_mission(mission_id: int, action: str) -> str:
    """Pause, resume, run now or delete a mission.
    Args:
        mission_id: the mission number
        action: pause, resume, run or delete
    """
    a = (action or "").lower().strip()
    fn = {"pause": _m().pause, "stop": _m().pause, "resume": _m().resume, "start": _m().resume,
          "run": _m().run_now, "run now": _m().run_now, "delete": _m().delete, "remove": _m().delete}.get(a)
    return fn(int(mission_id)) if fn else "Action must be pause, resume, run or delete."
