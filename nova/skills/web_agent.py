"""The AI web agent (browser-use): hand Nova a whole web task and it works the site in its own Chrome window."""
from __future__ import annotations

from .. import web_agent as wa
from ..tools import register_group, tool

register_group("web_agent", ["web agent", "browser agent", "on the website", "on the site", "portal", "go to the site",
                             "fill in the form", "fill out", "find me the cheapest", "compare prices", "book a",
                             "look up on", "download my invoice", "download the invoice", "check my account",
                             "log in to", "deal registration", "register the deal", "browser-use", "browse for me"])


@tool(group="web_agent")
def web_agent(task: str, allowed: str = "") -> str:
    """Give a whole multi-step website task to the AI web agent (its own Chrome window; runs in the background and
    reports back). Good for portals, forms, searching and comparing across sites, downloading invoices, filling in
    deal registrations. It stops right before paying, buying, sending, submitting, booking or deleting unless the user
    explicitly allowed that action.
    Args:
        task: the full task in plain words, with every detail needed (site, names, numbers, what to report back)
        allowed: final actions the USER explicitly approved in their own words, e.g. "submit" (empty = none)
    """
    return wa.start(task, allowed)


@tool(group="web_agent")
def web_agent_status() -> str:
    """What the web agent is doing right now, or its last result."""
    return wa.status()


@tool(group="web_agent")
def stop_web_agent() -> str:
    """Stop the web agent's current task."""
    return wa.stop()


@tool(group="web_agent")
def web_agent_login(url: str = "") -> str:
    """Open the web agent's own browser so the user can log in to a site once (it remembers the login).
    Args:
        url: the site's login page
    """
    return wa.open_for_login(url)


@tool(group="web_agent")
def install_web_agent() -> str:
    """Install the AI web agent (browser-use) — one-time, a few minutes, in its own separate Python."""
    if wa.installed():
        return "The web agent is already installed."
    return wa.install()
