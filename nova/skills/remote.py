"""Remote access by voice: open Nova on your phone via Tailscale (private, your devices only)."""
from __future__ import annotations

from ..tools import register_group, tool

register_group("remote", ["tailscale", "remote access", "on my phone", "from my phone", "phone access",
                          "access nova remotely", "outside the house", "away from home"])


@tool(group="remote")
def remote_access(action: str = "status") -> str:
    """Tailscale remote access to Nova's dashboard (and the globe) from the user's phone.
    Args:
        action: status, on (set up) or off
    """
    from .. import remote
    a = (action or "status").lower().strip()
    if a in ("on", "setup", "set up", "enable", "start"):
        return remote.enable()["message"]
    if a in ("off", "disable", "stop"):
        return remote.disable()["message"]
    st = remote.status(fresh=True)
    if st["url"]:
        extra = f" The globe is at {st['globe_url']}." if st["globe_url"] else ""
        return f"Remote access is on: open {st['url']} on your phone (with Tailscale switched on).{extra}"
    return st["message"] or "Remote access is off."
