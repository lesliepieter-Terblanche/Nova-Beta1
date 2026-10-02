"""Nova looking after herself: undo, what she has learned, her personality, the weekly report and activity review."""
from __future__ import annotations

from .. import context, lessons, reports, undo
from ..tools import register_group, tool

register_group("assistant", ["undo", "take that back", "revert", "put it back", "weekly report", "week report",
                             "activity review", "what did you do", "what have you done", "what have you learned",
                             "personality", "witty", "professional", "swear", "be more", "be less", "funny", "jokes",
                             "serious", "forget that lesson", "learned"])
STYLES = {"witty": "quick, dry humour, the odd joke and a mild swear word now and then",
          "warm": "friendly and encouraging, no swearing",
          "professional": "businesslike and to the point, no jokes",
          "minimal": "as few words as possible"}


@tool(group="assistant")
def undo_last() -> str:
    """Undo the last thing Nova changed (a moved or written file, a filed or deleted note, a memory, a reminder, a
    budget entry, a setting). Says plainly when something can't be undone."""
    return undo.undo_last()


@tool(group="assistant")
def undo_list() -> str:
    """The recent things Nova can still undo, newest first."""
    items = undo.recent(8)
    return "\n".join(f"- {e['ts'][11:16]} {e['label']}" + (" (can't be undone)" if e["kind"] == "none" else "")
                     for e in items) if items else "Nothing recent to undo."


@tool(group="assistant")
def what_have_you_learned() -> str:
    """What Nova has learned from the user's corrections: filing rules, shop categories and standing preferences."""
    out = []
    filing = lessons.all_of("filing")
    if filing:
        out.append("Filing: " + "; ".join(f"'{r['key']}' → {r['value'].replace('|', ' / ')}" for r in filing[:8]))
    shops = lessons.all_of("merchant")
    if shops:
        out.append("Shops: " + "; ".join(f"{r['key']} → {r['value']}" for r in shops[:10]))
    prefs = lessons.preferences(8)
    if prefs:
        out.append("How you like things: " + "; ".join(prefs))
    return "\n".join(out) if out else "Nothing from corrections yet — move something I misfiled or tell me how you want it, and I'll keep it."


@tool(group="assistant")
def forget_lesson(which: str) -> str:
    """Forget something Nova learned from a correction.
    Args:
        which: a word from the lesson (a title or a shop name)
    """
    n = lessons.forget(which)
    return f"Forgotten ({n})." if n else "I didn't find a lesson like that."


@tool(group="assistant")
def set_personality(style: str, swearing: str = "") -> str:
    """Change how Nova talks. It stays that way until changed again.
    Args:
        style: witty | warm | professional | minimal
        swearing: "on" or "off" for the occasional mild swear word (only matters for witty); empty = leave as is
    """
    from .. import settings
    style = style.lower().strip()
    if style not in STYLES:
        return "I can be witty, warm, professional or minimal."
    a = (context.cfg or {}).get("assistant") or {}
    values = {"assistant.personality": style}
    if swearing.lower().strip() in ("on", "off", "yes", "no", "true", "false"):
        values["assistant.swearing"] = swearing.lower().strip() in ("on", "yes", "true")
    undo.record(f"changed my personality to {style}", "setting", path="assistant.personality", was=a.get("personality", "witty"))
    try:
        settings.apply({"values": values})
    except Exception as e:
        print(f"[assistant] couldn't save the personality: {e}")
    if context.cfg is not None:
        for k, v in values.items():
            context.cfg["assistant"][k.split(".", 1)[1]] = v
    return f"Done — from now on I'm {style}: {STYLES[style]}."


@tool(group="assistant")
def weekly_report(email_draft_to: str = "") -> str:
    """Write this week's work report (what moved, what's stuck, what's due next week), save it in the brain and show
    it. Optionally also put it in Gmail as a draft — never sent.
    Args:
        email_draft_to: an email address to draft it to; empty = no email draft
    """
    r = reports.weekly_report()
    try:
        from .. import cards
        cards.show("info", r["title"], {"text": r["text"]})
    except Exception:
        pass
    extra = ""
    if email_draft_to.strip():
        extra = " " + reports.draft(r, email_draft_to.strip())
    return f"{r['title']} is saved under Work → Meetings & Reports and is on the dashboard.{extra}\n\n{r['text'][:1800]}"


@tool(group="assistant")
def activity_review(days: int = 7) -> str:
    """What Nova did for the user lately: how many requests, what was used most, what failed, what is never used.
    Args:
        days: how far back (default 7)
    """
    text = reports.review_text(reports.activity_review(max(1, min(90, int(days)))))
    try:
        from .. import cards
        cards.show("info", "My week with you", {"text": text})
    except Exception:
        pass
    return text
