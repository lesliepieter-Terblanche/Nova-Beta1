"""Self-maintenance by voice: updates, rollback, restart, dashboard."""
from __future__ import annotations

import os
import threading
import webbrowser

from .. import context, updater
from ..tools import register_group, tool

register_group("maintenance", ["update", "upgrade", "roll back", "rollback", "revert", "previous version",
                               "version", "restart", "reboot yourself", "dashboard", "github", "mcp", "plugin",
                               "playbook", "routine", "extension", "connector"])


@tool(group="maintenance")
def check_for_updates() -> str:
    """Check GitHub for a newer version of Nova."""
    info = updater.check()
    if "error" in info:
        return info["error"]
    if not info["behind"]:
        return f"Nova is up to date ({info['current']})."
    return f"{info['behind']} update(s) available. Latest: {info['latest']}. Say 'update yourself' to install."


@tool(group="maintenance", confirm=True)
def update_yourself() -> str:
    """Download and install the latest version of Nova from GitHub, then restart."""
    msg = updater.update()
    if "Updated to" in msg:
        _restart_soon()
    return msg


@tool(group="maintenance")
def list_versions() -> list:
    """List recent versions of Nova (for rolling back)."""
    return updater.versions()


@tool(group="maintenance", confirm=True)
def rollback_version(target: str = "previous") -> str:
    """Roll Nova back to an earlier version, then restart.
    Args:
        target: "previous" (the version before the last update), a tag like v1.2.0, or a commit id
    """
    msg = updater.rollback(target)
    if "Rolled back" in msg:
        _restart_soon()
    return msg


@tool(group="maintenance", confirm=True)
def restart_nova() -> str:
    """Restart Nova (e.g. after changing settings)."""
    _restart_soon()
    return "Restarting in a few seconds."


@tool(group="maintenance")
def open_dashboard() -> str:
    """Open the 3D 2nd-brain dashboard in the browser."""
    webbrowser.open(f"http://localhost:{context.cfg.dashboard.port}")
    return "Dashboard opened."


def _restart_soon(delay: float = 4.0) -> None:
    threading.Timer(delay, lambda: os._exit(updater.RESTART_CODE)).start()


@tool(group="maintenance")
def extensions_status() -> dict:
    """List connected MCP servers, loaded playbooks and scheduled routines."""
    from ..extensions import playbooks
    return {
        "mcp_servers": context.mcp.status if context.mcp else {},
        "playbooks": {p.name: p.description for p in playbooks()},
        "routines": [f"{r.get('name')} at {r.get('at')}" for r in (context.cfg.get("routines") or [])],
    }
