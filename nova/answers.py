"""Ask the brain: answers built only from what's in your 2nd brain, with numbered links to the sources.

  "What do I know about Westcon's Nokia pipeline?"  → answer + [1] memory, [2] meeting note, [3] Sam's card…
Plus a weekly "what's new in your brain" digest (Sunday's dream sends it to Telegram).
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from . import context, people
from .config import resolve

ANSWER_PROMPT = """You are {name}, {owner}'s assistant. Answer his question using ONLY the numbered sources from his own
knowledge base below. Cite the sources you use like [1] or [2][4] right after the fact. If the sources don't answer
it, say so plainly and say what they do tell you. Be concise (under 150 words unless the question needs a list).
Today is {today}.

Question: {q}

Sources:
{sources}"""

DIGEST_PROMPT = """Write {owner}'s weekly "what's new in your brain" digest from this list of things added in the last
{days} days. 4-7 short bullet points grouped by theme (projects, people, deals, ideas); mention names and numbers;
end with one line suggesting what to follow up. No preamble.

{items}"""


def _sources(question: str, k: int = 12) -> list[dict]:
    s = context.store
    out, seen = [], set()

    def add(sid, kind, title, text, ts, score):
        if sid not in seen:
            seen.add(sid)
            out.append({"id": sid, "kind": kind, "title": title, "text": text, "ts": ts, "score": score})

    # people named in the question: their card first (a first name counts when only one card has it)
    ppl = people.all_people()
    firsts: dict[str, int] = {}
    for p in ppl:
        firsts[p["name"].split()[0].lower()] = firsts.get(p["name"].split()[0].lower(), 0) + 1
    words = set(re.findall(r"[a-z][a-z'\-]+", question.lower()))
    for p in ppl:
        pat = people.patterns(p)
        first = p["name"].split()[0].lower()
        if (pat and pat.search(question)) or (len(first) >= 3 and firsts[first] == 1 and first in words):
            ms = people.mentions(p, limit=30)
            add(f"person:{p['id']}", "person", p["name"], people.summary(p, ms), p["updated"], 1.0)
    for score, r in s.recall(question, k=10, min_score=0.25):
        add(f"memory:{r['id']}", r["kind"], r["text"][:90], r["text"], r["created"], score)
    for score, c in s.search_notes(question, k=6, min_score=0.3):
        add(f"note:{c['path']}", "note", Path(c["path"]).stem, c["text"][:1500], "", score)
    with s.lock:
        arts = s.db.execute("SELECT id,ts,kind,title,detail,embedding FROM artifacts WHERE kind!='note' "
                            "ORDER BY id DESC LIMIT 600").fetchall()
    rows = [{"id": a["id"], "text": f"{a['title']} {a['detail'] or ''}", "embedding": a["embedding"], "ts": a["ts"],
             "kind": a["kind"], "title": a["title"]} for a in arts]
    for score, a in s._rank(question, rows, k=4, min_score=0.35):
        add(f"artifact:{a['id']}", a["kind"], a["title"], a["text"][:800], a["ts"], score)
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:k]


def ask(question: str) -> dict:
    """{"answer": str, "sources": [{"n", "id", "title", "kind"}]}"""
    question = question.strip()
    if not question:
        return {"answer": "Ask me something about your work, people or projects.", "sources": []}
    src = _sources(question)
    if not src:
        return {"answer": "I don't have anything on that in your brain yet. Tell me about it, forward me something on "
                          "Telegram, or say “research …” and I'll file what I find.", "sources": []}
    block = "\n\n".join(f"[{i}] ({x['kind']}{', ' + x['ts'][:10] if x['ts'] else ''}) {x['title']}\n{x['text'][:1500]}"
                        for i, x in enumerate(src, 1))
    cfg = context.cfg
    prompt = ANSWER_PROMPT.format(name=cfg.assistant.name if cfg else "Nova", owner=cfg.assistant.owner if cfg else "the user",
                                  today=dt.date.today().strftime("%A %d %B %Y"), q=question, sources=block)
    try:
        answer = context.llm.complete(prompt, prefer_smart=True, temperature=0.2).strip()
    except Exception as e:
        answer = f"(I couldn't reach a model to write the answer: {e}.) Here's what I found:"
    cited = sorted({int(n) for n in re.findall(r"\[(\d{1,2})\]", answer) if 0 < int(n) <= len(src)})
    keep = cited or list(range(1, len(src) + 1))
    s = context.store
    for i in keep:
        s.note_use(src[i - 1]["id"])
    s.log("reply", "brain", f"🧠 Asked the brain: {question[:120]}", answer[:3000], turn=s.current_turn or 0)
    return {"answer": answer, "sources": [{"n": i, "id": src[i - 1]["id"], "title": src[i - 1]["title"],
                                           "kind": src[i - 1]["kind"]} for i in keep]}


def as_text(r: dict) -> str:
    if not r["sources"]:
        return r["answer"]
    return r["answer"] + "\n\nSources: " + " · ".join(f"[{x['n']}] {x['title'][:60]} ({x['kind']})" for x in r["sources"])


# ── weekly digest ─────────────────────────────────────────
def new_items(days: int = 7) -> dict:
    s = context.store
    since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
    since_ts = (dt.datetime.now() - dt.timedelta(days=days)).timestamp()
    with s.lock:
        mems = s.db.execute("SELECT id,kind,text FROM memories WHERE superseded_by IS NULL AND created>=? "
                            "ORDER BY id DESC", (since,)).fetchall()
        notes = s.db.execute("SELECT path, MAX(mtime) m FROM chunks GROUP BY path HAVING m>=? ORDER BY m DESC",
                             (since_ts,)).fetchall()
        arts = s.db.execute("SELECT id,kind,title FROM artifacts WHERE kind!='note' AND ts>=? ORDER BY id DESC",
                            (since,)).fetchall()
    ppl = [p for p in people.all_people() if (p["created"] or "") >= since]
    return {"memories": [dict(m) for m in mems], "notes": [Path(n["path"]).stem for n in notes],
            "creations": [dict(a) for a in arts], "people": ppl,
            "projects": [m["text"] for m in mems if m["kind"] == "project"]}


def digest(days: int = 7, save: bool = True) -> str:
    n = new_items(days)
    counts = (f"{len(n['memories'])} memories, {len(n['notes'])} notes, {len(n['people'])} new people, "
              f"{len(n['creations'])} creations")
    if not any(n[k] for k in ("memories", "notes", "people", "creations")):
        return f"Quiet week in your brain — nothing new in the last {days} days."
    items = [f"- [{m['kind']}] {m['text']}" for m in n["memories"][:60]]
    items += [f"- [note] {t}" for t in n["notes"][:30]]
    items += [f"- [person] {p['name']}" + (f" ({p['company']})" if p["company"] else "") for p in n["people"][:30]]
    items += [f"- [{a['kind']}] {a['title']}" for a in n["creations"][:30]]
    owner = context.cfg.assistant.owner if context.cfg else "the user"
    try:
        body = context.llm.complete(DIGEST_PROMPT.format(owner=owner, days=days, items="\n".join(items)),
                                    prefer_smart=True, temperature=0.3).strip()
    except Exception:
        body = "\n".join(items[:12])
    text = f"🧠 What's new in your brain this week ({counts}):\n{body}"
    if save:
        vault = resolve(context.cfg.brain.vault_dir) if context.cfg else resolve("brain")
        y, w, _ = dt.date.today().isocalendar()
        path = vault / "Digests" / f"{y}-W{w:02d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Brain digest — week {w}, {y}\n\n_{counts}_\n\n{body}\n", encoding="utf-8")
        context.store.index_note(path)
    return text
