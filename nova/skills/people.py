"""People cards, ask-the-brain and filing things into the brain."""
from __future__ import annotations

from .. import answers, context, inbox, people
from ..tools import GROUP_KEYWORDS, register_group, tool

BASE = ["who is", "who's", "contact", "contacts", "person", "people", "card", "phone number", "cell number",
        "email address", "works at", "work at", "colleague", "partner contact", "rep at", "bdm", "account manager",
        "business card", "introduce", "met "]
register_group("people", BASE)
register_group("askbrain", ["what do i know", "what do we know", "what have i", "according to my", "ask my brain",
                            "ask the brain", "in my brain", "from my brain", "my notes say", "everything about",
                            "history with", "brain digest", "what's new in my brain", "whats new in my brain",
                            "weekly digest", "remind me what", "catch me up", "what did", "when did i", "summary of"])
register_group("inbox", ["save this", "file this", "add this to my brain", "save to my brain", "into my brain",
                         "keep this", "file it", "save it to"])


def refresh_keywords() -> None:
    """Saying a person's name ("what did Sipho say…") brings in the people tools."""
    try:
        names = set()
        ppl = people.all_people()
        firsts = [p["name"].split()[0].lower() for p in ppl]
        names |= {f for f in firsts if len(f) >= 4 and firsts.count(f) == 1}      # "Sipho" alone is enough
        for p in ppl:
            for n in [p["name"], *p["aliases"]]:
                n = n.lower().strip()
                if len(n) >= 4:
                    names.add(n)
        GROUP_KEYWORDS["people"] = BASE + sorted(names)
        GROUP_KEYWORDS["askbrain"] = [k for k in GROUP_KEYWORDS.get("askbrain", [])] + sorted(names)
    except Exception:
        pass


people.ON_CHANGE.append(refresh_keywords)
if context.store is not None:
    refresh_keywords()


@tool(group="people")
def person_card(name: str) -> str:
    """Everything about one person: company, role, contact details, notes, what you know, deals and when they were last
    mentioned.
    Args:
        name: the person's name (first name is fine if it's unique)
    """
    p = people.find(name)
    if not p:
        hits = [x for x in people.all_people() if name.lower() in x["name"].lower()]
        if len(hits) == 1:
            p = hits[0]
        elif hits:
            return "Which one? " + "; ".join(f"{h['name']} ({h['company'] or 'no company'})" for h in hits[:8])
        else:
            return f"I don't have a card for {name} yet. Tell me about them and I'll make one."
    context.store.note_use(f"person:{p['id']}")
    return people.summary(p)


@tool(group="people")
def save_person(name: str, company: str = "", role: str = "", email: str = "", phone: str = "", note: str = "") -> str:
    """Create or update someone's people card (only the details you give are changed).
    Args:
        name: full name
        company: company they work for
        role: job title / role, e.g. Juniper BDM
        email: email address
        phone: phone number
        note: one short fact about them to add to the card
    """
    pid, created = people.upsert(name, company, role, email, phone, note, source="told")
    p = people.get(pid)
    return f"{'Made a new card' if created else 'Updated the card'} for {p['name']}" + \
        (f" ({', '.join(x for x in (p['role'], p['company']) if x)})" if p["role"] or p["company"] else "") + "."


@tool(group="people")
def list_people(company: str = "", limit: int = 40) -> str:
    """List people cards, optionally only those at one company.
    Args:
        company: e.g. Northwind (empty = everyone)
        limit: how many
    """
    ppl = [p for p in people.all_people() if not company or company.lower() in (p["company"] or "").lower()]
    if not ppl:
        return "No people cards yet." if not company else f"No one at {company} yet."
    return f"{len(ppl)} {'person' if len(ppl) == 1 else 'people'}: " + "; ".join(
        p["name"] + (f" — {', '.join(x for x in (p['role'], p['company']) if x)}" if p["role"] or p["company"] else "")
        for p in ppl[:limit])


@tool(group="people")
def merge_people(keep_name: str, duplicate_name: str) -> str:
    """Merge two cards that are the same person (e.g. "Sam" and "Samuel Dlamini").
    Args:
        keep_name: the card to keep
        duplicate_name: the card to fold into it
    """
    a, b = people.find(keep_name), people.find(duplicate_name)
    if not (a and b) or a["id"] == b["id"]:
        return "I need two different existing cards."
    p = people.merge(a["id"], b["id"])
    return f"Merged — {p['name']} now also goes by {', '.join(p['aliases']) or '—'}."


@tool(group="people")
def build_people_cards() -> str:
    """Read through everything Nova remembers about people and make / update their cards (runs nightly too)."""
    n = people.catch_up(limit=200)
    return f"Done — {len(people.all_people())} people cards ({n} made or updated just now)."


@tool(group="askbrain")
def ask_brain(question: str) -> str:
    """Answer a question ONLY from the user's own 2nd brain (memories, notes, people cards, creations), with numbered
    sources. Use for "what do I know about…", "what did Sam say about…", "remind me what we decided…".
    Args:
        question: the question, in full
    """
    return answers.as_text(answers.ask(question))


@tool(group="askbrain")
def brain_digest(days: int = 7) -> str:
    """What's new in the 2nd brain lately: new memories, notes, people and creations, summarised.
    Args:
        days: how far back
    """
    return answers.digest(int(days or 7))


@tool(group="inbox")
def file_to_brain(link_or_text: str, note: str = "") -> str:
    """File a link, a file on the PC or a piece of text into the 2nd brain (summarised, people and project linked).
    Args:
        link_or_text: a URL, a full file path, or the text itself
        note: optional note from the user about why it matters
    """
    from pathlib import Path
    x = link_or_text.strip()
    if inbox.URL_RE.fullmatch(x):
        r = inbox.file_url(x, "voice", note)
    elif len(x) < 400 and Path(x).expanduser().is_file():
        r = inbox.file_document(Path(x).expanduser(), "voice", note)
    else:
        r = inbox.file_text(x, "voice", note)
    return inbox.reply_text(r)
