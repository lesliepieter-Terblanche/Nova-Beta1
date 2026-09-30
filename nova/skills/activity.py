"""Screen time (ActivityWatch): where the day went, time on one app / site."""
from __future__ import annotations

from .. import activity
from ..tools import register_group, tool

register_group("activity", ["screen time", "where did my day go", "where did my time go", "how long was i",
                            "how much time", "time on", "time spent", "wasted", "distracted", "activitywatch",
                            "what did i do today", "productive", "productivity"])

NOT_RUNNING = ("ActivityWatch isn't running. Install it free from activitywatch.net (it starts with Windows), "
               "then ask again.")


@tool(group="activity")
def screen_time(period: str = "today") -> str:
    """How the user spent their screen time (apps, sites, drift), from ActivityWatch on this PC.
    Args:
        period: today, yesterday, this morning, week, or 'last 2 hours' / 'last 30 minutes'
    """
    if not activity.running():
        return NOT_RUNNING
    return activity.as_text(activity.summary(period))


@tool(group="activity")
def time_spent_on(what: str, period: str = "today") -> str:
    """Time spent on one app, website or document, e.g. 'YouTube', 'Excel', 'Juniper QBR'.
    Args:
        what: app, site or window title to look for
        period: today, yesterday, week, or 'last 2 hours'
    """
    if not activity.running():
        return NOT_RUNNING
    return activity.time_on(what, period)
