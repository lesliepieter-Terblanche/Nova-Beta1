"""Focus tools: one thing now, brain dumps, tiny first steps, held actions and low-energy days.
Check-in data (sleep, energy, mood) is deliberately NOT available to any tool — it never reaches a model."""
from __future__ import annotations

from .. import focus, wellbeing
from ..tools import register_group, tool

register_group("focus", ["focus", "what should i do", "what's next", "whats next", "one thing", "stuck", "brain dump",
                         "dump", "overwhelmed", "too much", "where was i", "to do", "todo", "break it down",
                         "break this down", "first step", "held", "send held", "drop held", "low energy",
                         "running on empty", "flat today", "tired today", "done with", "finished", "i did it", "later",
                         "not now", "focus mode"])


def _fmt() -> str:
    st = focus.state()
    if not st["now"]:
        return "Nothing in focus yet. Tell me the one thing that matters most right now."
    out = f"Now: {st['now']['text']}"
    if st["next"]:
        out += ". Next: " + "; then ".join(t["text"] for t in st["next"])
    return out + "."


@tool(group="focus")
def focus_now() -> str:
    """The user's one thing right now, and the next two — use for "what should I do", "where was I"."""
    return _fmt()


@tool(group="focus")
def focus_add(text: str, when: str = "later") -> str:
    """Add to the focus list. Several items at once (one per line) is a brain dump.
    Args:
        text: the task(s); separate several with new lines or semicolons
        when: now (do it now), next (right after the current thing), later (brain dump, default)
    """
    if when == "later" or ("\n" in text or ";" in text):
        items = focus.brain_dump(text)
        return f"Parked {len(items)} thing{'s' if len(items) != 1 else ''} in Later — out of your head, not lost."
    t = focus.add(text, "now" if when == "now" else "next")
    return f"Okay — '{t['text']}' is {'your one thing now' if when == 'now' else 'next up'}."


@tool(group="focus")
def focus_done(which: str = "now") -> str:
    """Mark the current focus task (or one matching the words) as done. Celebrate briefly.
    Args:
        which: "now" for the current task, or a few words from the task
    """
    opens = focus.open_tasks()
    t = opens[0] if which in ("", "now") and opens else next((o for o in opens if which.lower() in o["text"].lower()), None)
    if not t:
        return "I couldn't find that one in your focus list."
    focus.set_status(t["id"], "done")
    w = focus.wins_today()["tasks"]
    return f"Done: {t['text']}. That's {w} win{'s' if w != 1 else ''} today. " + _fmt()


@tool(group="focus")
def focus_later(which: str = "now") -> str:
    """Move the current task (or one matching the words) to Later — no guilt, it's parked.
    Args:
        which: "now" or a few words from the task
    """
    opens = focus.open_tasks()
    t = opens[0] if which in ("", "now") and opens else next((o for o in opens if which.lower() in o["text"].lower()), None)
    if not t:
        return "I couldn't find that one."
    focus.set_status(t["id"], "later")
    return "Parked for later. " + _fmt()


@tool(group="focus")
def break_it_down(task: str = "") -> str:
    """When the user is stuck: turn the task into a two-minute first step plus a few small ones.
    Args:
        task: the task (empty = the current Now task)
    """
    opens = focus.open_tasks()
    t = next((o for o in opens if task and task.lower() in o["text"].lower()), None) or \
        (focus.add(task, "now") if task else (opens[0] if opens else None))
    if not t:
        return "Tell me what you're stuck on and I'll find a tiny first step."
    steps = focus.breakdown(t["id"])
    return f"Start with just this: {steps[0]['text']}." + (" Then: " + "; ".join(s["text"] for s in steps[1:]) if len(steps) > 1 else "")


@tool(group="focus")
def held_actions() -> str:
    """List actions Nova held back at night or for the buying pause."""
    h = wellbeing.held()
    return "; ".join(f"#{x['id']} {x['label']} (until {x['due'][11:16]} — {x['reason']})" for x in h) or "Nothing is held."


@tool(group="focus", confirm=True)
def send_held(held_id: int) -> str:
    """Do a held action now anyway (the user confirms first).
    Args:
        held_id: the held action number
    """
    return wellbeing.release(int(held_id), "send")


@tool(group="focus")
def drop_held(held_id: int) -> str:
    """Drop a held action — it won't be done.
    Args:
        held_id: the held action number
    """
    return wellbeing.release(int(held_id), "drop")


@tool(group="focus")
def low_energy_mode(on: bool = True) -> str:
    """Turn today's low-energy mode on or off (shorter, gentler replies; the dashboard shows only one thing).
    Args:
        on: true to turn on, false to turn off
    """
    wellbeing.set_low_energy(bool(on))
    return "Low-energy mode is on for today — one small thing at a time." if on else "Back to normal mode."
