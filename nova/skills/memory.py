"""The 2nd brain: permanent memories + a markdown note vault (Obsidian-compatible)."""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from .. import context
from ..config import resolve
from ..tools import register_group, tool

register_group("brain", ["remember", "memory", "memories", "forget", "note", "notes", "brain", "journal", "recall",
                         "what do you know", "do you know", "did i", "what was", "idea", "save this", "learn",
                         "ingest", "knowledge", "wrong", "correct", "actually"])


def vault() -> Path:
    v = resolve(context.cfg.brain.vault_dir)
    v.mkdir(parents=True, exist_ok=True)
    return v


def _slug(title: str) -> str:
    return re.sub(r"[^\w\- ]", "", title).strip()[:80] or "Untitled"


@tool(group="brain")
def remember(text: str, kind: str = "fact", important: bool = False) -> str:
    """Permanently remember something the user tells you to remember.
    Args:
        text: the thing to remember, as one clear sentence
        kind: fact, preference, person, project, decision, goal, routine or event
        important: true if it should always be kept in mind
    """
    msg = context.store.add_memory(text, kind, "told", 3 if important else 2)
    context.store.export_markdown(vault())
    if kind == "person":
        from .. import people
        people.absorb_in_background(text, "told")
    return msg


@tool(group="brain")
def recall(query: str) -> str:
    """Search permanent memory and the notes vault for anything related to the query.
    Args:
        query: what to look for
    """
    s = context.store
    lines = [f"memory #{r['id']} ({r['kind']}, {r['created'][:10]}): {r['text']}" for _, r in s.recall(query, k=8)]
    lines += [f"note '{Path(c['path']).stem}': {c['text'][:500]}" for _, c in s.search_notes(query, k=4)]
    return "\n".join(lines) or "I don't have anything on that yet."


@tool(group="brain")
def correct_memory(memory_id: int, corrected_text: str) -> str:
    """Correct a memory that is wrong or out of date. The old version is kept in history.
    Args:
        memory_id: the memory number (find it with recall)
        corrected_text: the correct information as one sentence
    """
    msg = context.store.supersede(int(memory_id), corrected_text)
    context.store.export_markdown(vault())
    return msg


@tool(group="brain")
def write_note(title: str, content: str, folder: str = "Notes") -> str:
    """Create or add to a note in the 2nd brain vault.
    Args:
        title: note title
        content: markdown content to add
        folder: optional hint — a category such as Health, Finance, Clients, Projects, Reference (Nova files it
                under 01_Personal or 02_Work by what it is about)
    """
    from .. import taxonomy
    if taxonomy.enabled():
        name = taxonomy.file_name(title)
        existing = [p for p in vault().rglob(name) if not taxonomy.problems(p)]
        path = existing[0] if existing else taxonomy.place(title, content, hint=folder, dated=False)
        folder = path.parent.relative_to(vault()).as_posix()
    else:
        path = vault() / _slug(folder) / f"{_slug(title)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    if path.exists():
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n\n## {stamp}\n{content}\n")
    else:
        path.write_text(f"# {title}\n\n_created {stamp}_\n\n{content}\n", encoding="utf-8")
    context.store.index_note(path)
    context.record("note", title, path, folder)
    return f"Saved to note '{title}' in {folder}."


@tool(group="brain")
def read_note(title: str) -> str:
    """Read a note from the vault by (part of) its title.
    Args:
        title: note title or part of it
    """
    matches = [p for p in vault().rglob("*.md") if title.lower() in p.stem.lower()]
    if not matches:
        return f"No note matching '{title}'."
    p = max(matches, key=lambda x: x.stat().st_mtime)
    return f"{p.relative_to(vault())}\n\n{p.read_text(encoding='utf-8')[:6000]}"


@tool(group="brain")
def list_notes(folder: str = "", limit: int = 15) -> list:
    """List the most recently changed notes.
    Args:
        folder: optional vault folder
        limit: how many
    """
    root = vault() / folder if folder else vault()
    files = sorted(root.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return [str(p.relative_to(vault())) for p in files] or "The vault is empty."


@tool(group="brain")
def journal(entry: str) -> str:
    """Add an entry to today's journal / daily log.
    Args:
        entry: what to log
    """
    today = dt.date.today()
    from .. import taxonomy
    path = (taxonomy.system_folder("journal") if taxonomy.enabled() else vault() / "Journal") / f"{today:%Y-%m-%d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(f"# {today:%A %d %B %Y}\n", encoding="utf-8")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n- {dt.datetime.now():%H:%M} {entry}")
    context.store.index_note(path)
    context.record("note", f"Journal {today}", path, "journal")
    return "Logged."


@tool(group="brain")
def ingest_to_brain(source: str, title: str = "") -> str:
    """Save a web page or document into the 2nd brain so it can be searched later (summarised, with the people in it
    getting cards and the right project linked).
    Args:
        source: a URL or a full file path (pdf, docx, xlsx, txt, md, image)
        title: optional note about it
    """
    from .. import inbox
    if source.startswith("http"):
        r = inbox.file_url(source, "voice", title, folder="Library")
    else:
        r = inbox.file_document(Path(source).expanduser(), "voice", title, folder="Library")
    return inbox.reply_text(r)


@tool(group="brain")
def brain_structure() -> str:
    """How the 2nd brain is organised (01_Personal / 02_Work and their numbered categories), how many notes are in
    each, and whether a reorganisation is waiting for the user's approval."""
    from .. import taxonomy
    s = taxonomy.plan_summary()
    out = taxonomy.tree_text()
    if s["moves"] or s["checks"]:
        out += (f"\n\nWaiting for approval: {s['moves']} notes to move or rename, {s['questions']} open questions, "
                f"{s['checks']} recent notes to confirm. Review them on the dashboard (🗂).")
    return out


@tool(group="brain")
def plan_brain_reorganisation() -> str:
    """Check every note against the filing rules and prepare a plan for anything in the wrong place. Moves nothing."""
    from .. import taxonomy
    s = taxonomy.plan_summary(taxonomy.build_plan())
    if not s["moves"]:
        return "Everything in your brain already follows the filing rules."
    return (f"Plan ready: {s['moves']} notes to move or rename, {s['questions']} I'd like to ask you about. Nothing has "
            "moved — review and approve it on the dashboard (🗂).")


@tool(group="brain", confirm=True)
def apply_brain_plan(include_best_guesses: bool = False) -> str:
    """Carry out the approved reorganisation of the 2nd brain (backup first; links, pins and projects are kept).
    Args:
        include_best_guesses: also move the items the user hasn't answered, using Nova's best guess
    """
    from .. import taxonomy
    r = taxonomy.apply_plan(include_best_guesses)
    if not r["moved"]:
        return "Nothing to move" + (f" — {r['left']} items are waiting for your answer on the dashboard." if r["left"] else ".")
    return (f"Moved {r['moved']} notes into the new structure and fixed the links in {r['links']} notes. "
            + (f"{r['left']} are still waiting for your answer. " if r["left"] else "") + f"Backup: {r['backup']}")
