"""Nightly dreaming by voice: dream now, what happened last night, back up now."""
from __future__ import annotations

import threading

from ..tools import register_group, tool

register_group("dreaming", ["dream", "dreaming", "backup", "back up", "journal", "consolidate", "last night",
                            "while i slept", "tidy your memory", "clean up your memory"])


@tool(group="dreaming")
def dream_now() -> str:
    """Run Nova's nightly 'dream' now: merge duplicate memories, connect ideas, write the journal, back up."""
    from ..dreaming import dreamer
    d = dreamer()
    if d.running:
        return f"I'm already dreaming ({d.step})."
    threading.Thread(target=d.dream, daemon=True, name="dream-now").start()
    return "Dreaming now — I'll send you the dream report when I'm done."


@tool(group="dreaming")
def last_dream() -> str:
    """What Nova did during its last nightly dream (memories merged, connections, journal, backup)."""
    from ..dreaming import dreamer
    d = dreamer().last()
    if not d:
        return "I haven't dreamt yet — it happens every night at 02:30, or say 'dream now'."
    st = d["stats"]
    bits = [f"{st.get('merged', 0)} memories merged", f"{st.get('connections', 0)} connections",
            "journal written" if st.get("journal") else "no journal (quiet day)"]
    if st.get("backup_mb") is not None:
        bits.append(f"backup {st['backup_mb']} MB")
    return f"Last dream ({d['started'][:16].replace('T', ' ')}): " + ", ".join(bits) + f". Report: {d['note']}"


@tool(group="dreaming")
def backup_now() -> str:
    """Make an encrypted backup of Nova's brain, notes, settings and keys right now."""
    from ..dreaming import backup, passphrase
    info = backup()
    msg = f"Backed up {info['files']} files ({info['size_mb']} MB) to {info['path']}."
    if info.get("drive"):
        msg += f" Also copied to {info['drive']}."
    if info.get("new_passphrase"):
        from .. import context
        context.push("🔑 Your Nova backup passphrase (save it somewhere safe — you need it to restore):\n"
                     + passphrase(create=False)[0], [])
        msg += " I've sent the backup passphrase to your Telegram — save it in a password manager."
    return msg
