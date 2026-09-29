"""Screen watcher tools: "tell me when…" for things on your screen, windows, programs and downloads."""
from __future__ import annotations

import time

from ..tools import register_group, tool

register_group("watch", ["tell me when", "let me know when", "notify me when", "watch the screen", "watch my screen",
                         "keep an eye", "watch for", "when it's done", "when it finishes", "when the download",
                         "when the render", "when the upload", "stop watching", "watching", "watchers"])


@tool(group="watch")
def watch_screen(until: str, what: str = "", window: str = "", check_every_seconds: float = 0,
                 give_up_after_minutes: float = 120, then: str = "") -> str:
    """Keep an eye on the screen / a window / a program / Downloads, and tell the user when something happens.
    Args:
        until: text_appears | text_disappears | stops_changing | changes | window_opens | window_closes |
               process_exits | file_appears | looks_like
        what: the text to look for, window title, program name (e.g. ffmpeg), file name part or Downloads folder,
              or for looks_like a yes/no question about the screen (e.g. "Has the build failed?")
        window: only look inside the window whose title contains this (optional)
        check_every_seconds: 0 = sensible default
        give_up_after_minutes: stop watching after this long
        then: optional follow-up to do when it happens, e.g. "close that window" or "email the file to Sam"
    """
    from ..watcher import manager
    try:
        w = manager().add(until.strip().lower(), what, window, check_every_seconds, give_up_after_minutes, then)
    except ValueError as e:
        return f"ERROR: {e}"
    extra = f" Then I'll {then}." if then else ""
    return f"Okay — I'll tell you when {w.label()} (watch #{w.id}).{extra}"


@tool(group="watch")
def list_watchers() -> str:
    """What Nova is currently watching the screen for."""
    from ..watcher import manager
    ws = manager().active()
    if not ws:
        return "I'm not watching for anything right now."
    return "I'm watching for: " + "; ".join(
        f"#{w.id} {w.label()} (gives up in {max(0, int((w.until - time.time()) / 60))} min)" for w in ws) + "."


@tool(group="watch")
def stop_watching(watch_id: int = 0) -> str:
    """Stop one screen watch by its number, or all of them (0).
    Args:
        watch_id: the watch number, or 0 for all
    """
    from ..watcher import manager
    n = manager().cancel(int(watch_id) if watch_id else None)
    return f"Stopped {n} watch{'es' if n != 1 else ''}." if n else "There was nothing like that to stop."
