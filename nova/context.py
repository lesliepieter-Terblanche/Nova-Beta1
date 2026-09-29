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
# announce(text) says something out loud if the voice loop is running (set by main.py)
announce: Callable[[str], None] | None = None
# notify(text, files) pushes a message to the owner (Telegram and/or speaker).
notify: Callable[[str, list[str]], None] | None = None

_local = threading.local()


def begin_turn() -> None:
    _local.files = []


def attach(path: str | Path) -> None:
    """A tool calls this to send a file back with the reply (e.g. to Telegram)."""
    if not hasattr(_local, "files"):
        _local.files = []
    _local.files.append(str(path))


def record(kind: str, title: str, location: str | Path, detail: str = "") -> None:
    """Register something Nova made/touched so it shows up in the 2nd-brain dashboard."""
    if store:
        try:
            store.add_artifact(kind, title, str(location), detail)
        except Exception as e:
            print(f"[store] could not record artifact: {e}")


def attachments() -> list[str]:
    return list(getattr(_local, "files", []))


def push(text: str, files: list[str] | None = None) -> None:
    if notify:
        notify(text, files or [])
    else:
        print(f"[notify] {text} {files or ''}")
