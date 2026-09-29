"""Drop-in extensions — no core code changes needed.

plugins/*.py        Python files with @tool functions -> new abilities
playbooks/*.md      Instruction files (like Claude "skills"): when a request matches a
                    playbook's triggers, its instructions are added to Nova's prompt, so Nova
                    follows your procedure using the tools it already has.

Playbook format:
    ---
    name: meeting-prep
    description: Prepare me for a meeting
    triggers: [prep me, meeting prep, before my meeting]
    tool_groups: [google, brain, web]     # optional: tool groups this playbook needs
    ---
    1. Look up the meeting in the calendar ...
"""
from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import ROOT


@dataclass
class Playbook:
    name: str
    description: str
    triggers: list[str]
    groups: list[str]
    body: str
    path: Path


_PLAYBOOKS: list[Playbook] = []


def load_plugins(folder: Path = ROOT / "plugins") -> list[str]:
    loaded = []
    if not folder.is_dir():
        return loaded
    for f in sorted(folder.glob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"nova_plugins.{f.stem}", f)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            loaded.append(f.stem)
        except Exception as e:
            print(f"[plugins] {f.name} failed: {e}")
    return loaded


def load_playbooks(folder: Path = ROOT / "playbooks") -> list[Playbook]:
    _PLAYBOOKS.clear()
    if not folder.is_dir():
        return _PLAYBOOKS
    for f in sorted(folder.rglob("*.md")):
        if f.name.lower() == "readme.md":
            continue
        text = f.read_text(encoding="utf-8")
        meta, body = {}, text
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
        if m:
            meta, body = (yaml.safe_load(m.group(1)) or {}), m.group(2)
        _PLAYBOOKS.append(Playbook(
            name=str(meta.get("name", f.stem)),
            description=str(meta.get("description", "")),
            triggers=[str(t).lower() for t in meta.get("triggers", [])],
            groups=[str(g) for g in meta.get("tool_groups", [])],
            body=body.strip(),
            path=f,
        ))
    return _PLAYBOOKS


def matching_playbooks(text: str, limit: int = 2) -> list[Playbook]:
    low = text.lower()
    return [p for p in _PLAYBOOKS if any(t in low for t in p.triggers)][:limit]


def playbooks() -> list[Playbook]:
    return list(_PLAYBOOKS)
