"""The skills library by voice: "install the skills library", "what skills do you have from the library?"."""
from __future__ import annotations

from .. import skill_library
from ..tools import register_group, tool

register_group("library", ["skills library", "skill library", "library of skills", "anthropic skills",
                           "install skills", "install the skills", "update the skills", "library skills"])


@tool(group="library")
def install_skill_library() -> str:
    """Download (or update) the public skills library from github.com/anthropics/skills and turn its open-source
    skills into Nova playbooks: web design, internal comms, themes, posters, generative art, MCP servers and more."""
    try:
        res = skill_library.install()
    except Exception as e:
        return f"ERROR: I couldn't download the skills library ({type(e).__name__}: {e}). Is the internet up?"
    on = [n for n in res["added"] if f"lib-{n}" not in res["off"]]
    msg = f"Installed {len(res['added'])} skills from the library. Ready to use: {', '.join(on)}."
    if res["off"]:
        msg += (f" Installed but switched off, because they were written for Claude's own apps: "
                f"{', '.join(n.removeprefix('lib-') for n in res['off'])} (Settings → Extensions to switch one on).")
    if res["skipped"]:
        msg += " Left out: " + "; ".join(f"{n} — {why}" for n, why in res["skipped"].items()) + "."
    return msg


@tool(group="library")
def skill_library_status() -> str:
    """Which skills from the public skills library are installed, and how to call one up."""
    info = skill_library.installed()
    if not info.get("skills"):
        return "The skills library isn't installed yet. Say 'install the skills library'."
    from ..extensions import playbooks
    live = {p.name: p for p in playbooks()}
    lines = []
    for n in info["skills"]:
        p = live.get(f"lib-{n}")
        lines.append(f"- {n}: " + (f"on — say e.g. \"{p.triggers[1] if len(p.triggers) > 1 else p.triggers[0]}\""
                                   if p else "off"))
    return f"{len(info['skills'])} library skills installed:\n" + "\n".join(lines)
