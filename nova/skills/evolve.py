"""Nova improving herself, by voice: "how did you do this week?", "approve the playbook …", "drop the playbook …"."""
from __future__ import annotations

from .. import evolve
from ..tools import register_group, tool

register_group("evolve", ["self review", "self-review", "review yourself", "how did you do", "how are you doing at",
                          "what did you struggle", "struggle with", "improve yourself", "get better", "evolve",
                          "playbook draft", "drafted playbook", "approve the playbook", "approve playbook",
                          "approve that playbook", "drop the playbook", "drop playbook", "drop that playbook",
                          "what playbooks", "your own playbooks", "what have you learned to do"])


@tool(group="evolve")
def self_review(days: int = 7) -> str:
    """Nova's honest look back at her own work: what went well, what failed or she couldn't do, what was slow, what
    the user corrected, and what she would change. Also drafts playbooks for jobs asked several times.
    Args:
        days: how far back to look (default 7)
    """
    days = max(1, min(90, int(days or 7)))
    r = evolve.review(days)
    if not r["facts"]["requests"]:
        return f"There's nothing to review yet: you haven't asked me for anything in the last {days} days."
    out = r["text"]
    new = []
    if evolve.cfg().get("draft_playbooks", True):
        new = [d for d in (evolve.draft_playbook(job) for job in r["facts"]["repeats"][:2]) if d]
    waiting = evolve.pending()
    if waiting:
        out += ("\n\nPlaybooks waiting for your yes" + (f" ({len(new)} new)" if new else "") + ":\n"
                + "\n".join(f"- {evolve.describe(d)}" for d in waiting[-4:])
                + "\nSay \"approve the playbook <name>\" or \"drop the playbook <name>\".")
    try:
        from .. import cards
        cards.show("info", "How my week went", {"text": out})
    except Exception:
        pass
    return out


@tool(group="evolve")
def playbook_drafts() -> str:
    """The playbooks Nova has drafted for repeated jobs and that wait for approval, and the ones already approved."""
    st = evolve.state()["drafts"]
    waiting = [d for d in st if d["state"] == "pending"]
    done = [d["name"] for d in st if d["state"] == "approved"]
    if not waiting and not done:
        return ("I haven't drafted any playbooks yet. I draft one when you've asked for the same kind of job at least "
                "three times on different days.")
    out = []
    if waiting:
        out.append("Waiting for your yes:\n" + "\n".join(f"- {evolve.describe(d)}" for d in waiting))
    if done:
        out.append("Approved and in use: " + ", ".join(done) + ".")
    return "\n\n".join(out)


@tool(group="evolve")
def approve_playbook(name: str = "") -> str:
    """Approve a playbook Nova drafted, so she follows it from now on.
    Args:
        name: the draft's name or a word from it (empty = the latest draft)
    """
    d = evolve.approve(name)
    if not d:
        return "ERROR: there's no playbook draft by that name waiting. Ask 'what playbooks have you drafted?'"
    return (f"Done — “{d['name']}” is now one of my playbooks. I'll follow it when you say things like "
            f"\"{d['triggers'][0]}\". You can switch it off in Settings → Extensions.")


@tool(group="evolve")
def drop_playbook_draft(name: str = "") -> str:
    """Drop a playbook draft the user doesn't want. Nova won't propose that job again.
    Args:
        name: the draft's name or a word from it (empty = the latest draft)
    """
    d = evolve.drop(name)
    if not d:
        return "ERROR: there's no playbook draft by that name waiting."
    return f"Dropped “{d['name']}”. I won't suggest a playbook for that again."
