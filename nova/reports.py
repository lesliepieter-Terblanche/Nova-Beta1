"""Weekly report (what moved at work, what's stuck, what's due next week) and the weekly activity review (what Nova
did for you, what failed, what you never use). Both can run by themselves once a week — Settings → Reports.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path

from . import context
from .config import resolve

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WRITE_PROMPT = """Write {owner}'s weekly work report from these facts. Plain, businesslike, no jokes, no filler, no
invented details — only what is listed. Three short sections with these exact headings:
## What moved
## Stuck or waiting
## Next week
Use short bullet points. If a section has nothing, write "Nothing to report."

FACTS (week ending {day}):
{facts}"""


def cfg() -> dict:
    return dict(((context.cfg or {}).get("reports") or {}))


# ── weekly report ─────────────────────────────────────────
def week_facts(days: int = 7, now: dt.datetime | None = None) -> dict:
    """What happened on the work side in the last week — from tracking, the brain's work folders and the calendar."""
    from . import taxonomy
    s = context.store
    now = now or dt.datetime.now()
    since = (now - dt.timedelta(days=days)).isoformat(timespec="seconds")
    facts: dict = {"done": [], "doing": [], "waiting": [], "meetings": [], "new_notes": [], "next_week": [], "todo": 0}

    def name_of(item: str) -> tuple[str, str]:
        if item.startswith("note:"):
            p = Path(item[5:])
            return (p.stem.replace("_", " "), taxonomy.domain_of(p)) if p.exists() else ("", "")
        if item.startswith("memory:"):
            with s.lock:
                row = s.db.execute("SELECT text FROM memories WHERE id=? AND superseded_by IS NULL", (item[7:],)).fetchone()
            return (row["text"][:110], taxonomy._guess_domain(row["text"])) if row else ("", "")
        return "", ""
    for t in s.tracked():
        title, domain = name_of(t["item"])
        if not title or domain != taxonomy.WORK:
            continue
        fresh = (t.get("updated") or "") >= since
        if t["status"] == "done" and fresh:
            facts["done"].append(title)
        elif t["status"] == "doing":
            facts["doing"].append(title)
        elif t["status"] == "waiting":
            facts["waiting"].append(title + (f" — {t['note']}" if t.get("note") else ""))
        elif t["status"] == "todo":
            facts["todo"] += 1
    work = taxonomy.vault() / taxonomy.WORK
    cutoff = (now - dt.timedelta(days=days)).timestamp()
    if work.exists():
        for p in sorted(work.rglob("*.md"), key=lambda x: -x.stat().st_mtime):
            if p.stat().st_mtime < cutoff or p.name == "Status_Board.md" or "Weekly_Reports" in p.parts:
                continue
            (facts["meetings"] if "Meetings" in p.parts else facts["new_notes"]).append(p.stem.replace("_", " "))
    try:
        from .tools import REGISTRY
        if "calendar_events" in REGISTRY:
            ev = REGISTRY["calendar_events"].run({"when": "tomorrow", "days": 7})
            if isinstance(ev, list):
                facts["next_week"] = [f"{str(e.get('start', ''))[:16].replace('T', ' ')} {e.get('title', '')}" for e in ev[:15]]
    except Exception as e:
        print(f"[reports] calendar skipped: {e}")
    return facts


def facts_text(f: dict) -> str:
    def block(title, items, limit=12):
        return f"{title}:\n" + ("\n".join(f"- {i}" for i in items[:limit]) if items else "- (none)")
    return "\n\n".join([block("Completed this week", f["done"]), block("In progress", f["doing"]),
                        block("Waiting on me / blocked", f["waiting"]), block("Meetings held", f["meetings"]),
                        block("New work notes and documents", f["new_notes"], 8),
                        f"Backlog items not started: {f['todo']}", block("Calendar, next 7 days", f["next_week"], 15)])


def weekly_report(llm=None, now: dt.datetime | None = None) -> dict:
    """Write the report, save it under 02_Work / Meetings & Reports / Weekly_Reports. Returns {"text", "path"}."""
    from . import taxonomy
    now = now or dt.datetime.now()
    f = week_facts(now=now)
    owner = (context.cfg.assistant.owner if context.cfg else "") or "the user"
    body = ""
    llm = llm if llm is not None else context.llm
    if llm and any(f[k] for k in ("done", "doing", "waiting", "meetings", "new_notes", "next_week")):
        try:
            body = (llm.complete(WRITE_PROMPT.format(owner=owner, day=f"{now:%A %d %B %Y}", facts=facts_text(f)),
                                 prefer_smart=True, temperature=0.2) or "").strip()
        except Exception as e:
            print(f"[reports] the model couldn't write it, using the plain facts: {e}")
    if "## What moved" not in body:
        body = ("## What moved\n" + ("\n".join(f"- {x}" for x in f["done"] + f["meetings"]) or "Nothing to report.")
                + "\n\n## Stuck or waiting\n" + ("\n".join(f"- {x}" for x in f["waiting"]) or "Nothing to report.")
                + "\n\n## Next week\n" + ("\n".join(f"- {x}" for x in f["doing"] + f["next_week"]) or "Nothing to report."))
    title = f"Weekly report — week ending {now:%d %B %Y}"
    folder = taxonomy.folder_for(taxonomy.WORK, "05_Meetings_&_Reports", "Weekly_Reports")
    path = folder / f"Weekly_Report_{now:%Y-%m-%d}.md"
    path.write_text(f"{taxonomy.header(taxonomy.WORK, 'COMPLETED')}\n\n# {title}\n\n{body}\n", encoding="utf-8")
    try:
        context.store.index_note(path)
    except Exception:
        pass
    context.record("document", title, path, "weekly report")
    return {"title": title, "text": body, "path": str(path), "facts": f}


# ── activity review ───────────────────────────────────────
def activity_review(days: int = 7, now: dt.datetime | None = None) -> dict:
    s = context.store
    now = now or dt.datetime.now()
    since = (now - dt.timedelta(days=days)).isoformat(timespec="seconds")
    month = (now - dt.timedelta(days=30)).isoformat(timespec="seconds")
    with s.lock:
        q = s.db.execute
        asks = [dict(r) for r in q("SELECT session, COUNT(*) n, AVG(ms) ms FROM activity WHERE kind='user' AND turn=id "
                                   "AND ts>=? GROUP BY session ORDER BY n DESC", (since,))]
        tools = [dict(r) for r in q("SELECT title, COUNT(*) n, SUM(status='error') bad FROM activity WHERE kind='tool' "
                                    "AND ts>=? GROUP BY title ORDER BY n DESC", (since,))]
        used = {r["title"].replace(" (waiting for your yes/no)", "") for r in
                q("SELECT DISTINCT title FROM activity WHERE kind='tool' AND ts>=?", (month,))}
        made = q("SELECT COUNT(*) FROM artifacts WHERE ts>=?", (since,)).fetchone()[0]
        learned = q("SELECT COUNT(*) FROM memories WHERE created>=? AND superseded_by IS NULL", (since,)).fetchone()[0]
        try:
            undone = q("SELECT COUNT(*) FROM undo_log WHERE state='undone' AND ts>=?", (since,)).fetchone()[0]
            lessons = q("SELECT COUNT(*) FROM lessons WHERE ts>=?", (since,)).fetchone()[0]
        except Exception:
            undone = lessons = 0
    total = sum(a["n"] for a in asks)
    ms = [a["ms"] for a in asks if a["ms"]]
    idle = []
    try:
        from .tools import REGISTRY
        groups: dict[str, list[str]] = {}
        for t in REGISTRY.values():
            groups.setdefault(t.group, []).append(t.name.replace("_", " "))
        idle = sorted(g for g, names in groups.items() if g not in ("system", "maintenance") and not (set(names) & used))
    except Exception:
        pass
    return {"days": days, "requests": total, "by_session": {a["session"].split(":")[0]: a["n"] for a in asks},
            "avg_seconds": round(sum(ms) / len(ms) / 1000, 1) if ms else None,
            "top_tools": [(t["title"], t["n"]) for t in tools[:6]],
            "failing": [(t["title"], int(t["bad"] or 0), t["n"]) for t in tools if (t["bad"] or 0) > 0][:5],
            "made": made, "learned": learned, "undone": undone, "lessons": lessons, "unused_groups": idle}


def review_text(r: dict) -> str:
    if not r["requests"] and not r["top_tools"]:
        return f"Nothing to review: you haven't asked me for anything in the last {r['days']} days."
    where = ", ".join(f"{n} by {s}" for s, n in list(r["by_session"].items())[:4])
    out = [f"Last {r['days']} days: {r['requests']} requests ({where})"
           + (f", answered in {r['avg_seconds']} seconds on average." if r["avg_seconds"] else ".")]
    if r["top_tools"]:
        out.append("Most used: " + ", ".join(f"{t} ({n})" for t, n in r["top_tools"]) + ".")
    out.append(f"I made {r['made']} files or documents and learned {r['learned']} new things about you"
               + (f", plus {r['lessons']} corrections" if r["lessons"] else "") + ".")
    if r["failing"]:
        out.append("Not working well: " + ", ".join(f"{t} failed {b} of {n} times" for t, b, n in r["failing"]) + ".")
    if r["undone"]:
        out.append(f"You undid {r['undone']} of my actions.")
    if r["unused_groups"]:
        out.append("Not used in a month: " + ", ".join(r["unused_groups"][:8]) + " — switch off what you don't want "
                   "under Settings → Skills.")
    return "\n".join(out)


# ── once a week, by themselves ────────────────────────────
def _state_path() -> Path:
    p = resolve("data/reports_state.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def due(kind: str, now: dt.datetime, state: dict) -> bool:
    c = cfg()
    on, day, at = {"weekly": (c.get("weekly", True), c.get("weekly_day", "fri"), c.get("weekly_time", "16:00")),
                   "review": (c.get("review", True), c.get("review_day", "sun"), c.get("review_time", "18:00"))}[kind]
    if not on or DAYS[now.weekday()] != str(day)[:3].lower():
        return False
    try:
        hh, mm = map(int, str(at).split(":"))
    except ValueError:
        return False
    start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return start <= now < start + dt.timedelta(hours=3) and state.get(kind) != now.date().isoformat()


def run_due(now: dt.datetime | None = None) -> list[str]:
    now = now or dt.datetime.now()
    path = _state_path()
    try:
        state = json.loads(path.read_text())
    except Exception:
        state = {}
    ran = []
    for kind in ("weekly", "review"):
        if not due(kind, now, state):
            continue
        state[kind] = now.date().isoformat()
        path.write_text(json.dumps(state))
        try:
            if kind == "weekly":
                r = weekly_report(now=now)
                context.push(f"🗓 {r['title']} is ready — saved in your brain under Work → Meetings & Reports.\n\n{r['text'][:1500]}",
                             [r["path"]])
                to = str(cfg().get("manager_email", "")).strip()
                if to:
                    draft(r, to)
            else:
                context.push("🔎 My week with you\n\n" + review_text(activity_review(now=now)))
            ran.append(kind)
        except Exception as e:
            print(f"[reports] {kind} failed: {e}")
    return ran


def draft(r: dict, to: str) -> str:
    """Put the report in Gmail as a DRAFT (never sent)."""
    from .tools import REGISTRY
    if "gmail_draft" not in REGISTRY:
        return "Google isn't connected, so I couldn't make the email draft."
    return str(REGISTRY["gmail_draft"].run({"to": to, "subject": r["title"], "body": r["text"]}))


def worker() -> None:
    time.sleep(90)
    while True:
        try:
            run_due()
        except Exception as e:
            print(f"[reports] {e}")
        time.sleep(120)


_started = False


def start() -> None:
    global _started
    if not _started:
        _started = True
        threading.Thread(target=worker, daemon=True, name="reports").start()
