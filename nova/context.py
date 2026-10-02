"""Shared runtime state that skills can reach without passing objects around."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

cfg = None              # nova.config.Config
llm = None              # nova.llm.LLM
store = None            # nova.store.Store  (permanent memory + activity log)
speech = None           # nova.speech.Speech
mcp = None              # nova.mcp_client.MCPManager
agent = None            # nova.agent.Agent
voice = None            # nova.voice.Voice (when the microphone loop runs)
# speak_now(text) says something immediately (announce() may hold it while you're away from the desk)
speak_now: Callable[[str], None] | None = None
# announce(text) says something out loud if the voice loop is running (set by main.py)
announce: Callable[[str], None] | None = None
# notify(text, files) pushes a message to the owner (Telegram and/or speaker).
notify: Callable[[str, list[str]], None] | None = None

telegram_on = False      # True when the Telegram bot is running (set by main.py)

_local = threading.local()


def begin_turn(session: str = "") -> None:
    _local.files = []
    _local.session = session
    _local.counts = {}


def count(name: str) -> int:
    """How many times `name` has now happened in this request (1 the first time)."""
    c = getattr(_local, "counts", None)
    if c is None:
        c = _local.counts = {}
    c[name] = c.get(name, 0) + 1
    return c[name]


def session() -> str:
    """Where the request being handled came from: voice, dashboard, telegram, phone…"""
    return getattr(_local, "session", "")


def attach(path: str | Path) -> None:
    """A tool calls this to send a file back with the reply (e.g. to Telegram)."""
    if not hasattr(_local, "files"):
        _local.files = []
    _local.files.append(str(path))


def record(kind: str, title: str, location: str | Path, detail: str = "") -> int | None:
    """Register something Nova made/touched so it shows up in the 2nd-brain dashboard. Returns its id."""
    if store:
        try:
            return store.add_artifact(kind, title, str(location), detail)
        except Exception as e:
            print(f"[store] could not record artifact: {e}")
    return None


def attachments() -> list[str]:
    return list(getattr(_local, "files", []))


def push(text: str, files: list[str] | None = None) -> None:
    try:                                  # shown on the dashboard too (a banner), not only on Telegram
        if store is not None:
            store.log("notice", "system", text[:200], text, turn=0)
    except Exception:
        pass
    if notify:
        notify(text, files or [])
    else:
        print(f"[notify] {text} {files or ''}")
