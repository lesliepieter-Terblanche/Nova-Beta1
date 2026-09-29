"""Presence awareness by voice: switch it on/off and ask whether Nova thinks you're at your desk."""
from __future__ import annotations

from ..tools import register_group, tool

register_group("presence", ["presence", "away", "at my desk", "walk away", "lock my pc", "lock the pc",
                            "greet me", "sit down", "i'm back", "notice me"])


@tool(group="presence")
def presence_awareness(action: str = "status") -> str:
    """Turn presence awareness on/off (webcam notices when you sit down or walk away), or report its status.
    Args:
        action: on, off or status
    """
    from ..presence import presence
    p = presence()
    a = (action or "status").lower().strip()
    if a in ("on", "start", "enable"):
        return p.start()
    if a in ("off", "stop", "disable"):
        return p.stop()
    s = p.status()
    if not s["enabled"]:
        return "Presence awareness is off." + (f" Last problem: {s['error']}" if s["error"] else "")
    where = {True: "at your desk", False: "away", None: "not sure yet"}[s["present"]]
    held = f" I'm holding {s['held']} message(s) for when you're back." if s["held"] else ""
    return f"Presence awareness is on — you're {where} (for {p._human(s['for_seconds'])}).{held}"


@tool(group="presence", confirm=True)
def lock_computer() -> str:
    """Lock the Windows PC now (like Windows+L)."""
    from ..presence import lock_pc
    return "Locked." if lock_pc() else "I can only lock a Windows PC."
