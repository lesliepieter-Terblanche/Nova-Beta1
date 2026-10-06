"""Growing each business (v2.39): targets and trends, the customer list, win-back messages, the content calendar.

  targets    a monthly sales target per business, how far along you are and where the month is heading, and eight
             weeks of sales and enquiries as a small chart
  customers  everyone who enquired or bought, per business, built from the enquiries and sales Nova already has:
             what they spent, how often, when last — repeat customers stand out
  win-back   a customer who has gone quiet gets a "we haven't heard from you" message drafted for your yes
             (never sent by itself, a few per week at most, each person once)
  calendar   a week of posts per business, drafted in its own voice, to copy and post yourself
"""
from __future__ import annotations

import datetime as dt
import json
import re

from . import business as biz
from . import context

_SCHEMA = """
CREATE TABLE IF NOT EXISTS biz_posts(id INTEGER PRIMARY KEY, biz TEXT, ts TEXT, day TEXT, platform TEXT, text TEXT,
  idea TEXT DEFAULT '', status TEXT DEFAULT 'planned');
CREATE TABLE IF NOT EXISTS biz_nudges(id INTEGER PRIMARY KEY, biz TEXT, who TEXT, ts TEXT);
"""
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
PLAN = [("Mon", "Facebook", "a helpful tip your customers would save"), ("Tue", "Instagram", "a behind-the-scenes look"),
        ("Wed", "Facebook", "one product, listing or feature, and who it is for"),
        ("Thu", "Instagram", "a question that gets people commenting"), ("Fri", "Facebook", "this weekend's offer or reminder")]


def _ready() -> None:
    s = biz._db()
    if not getattr(s, "_bizplan_ready", False):
        with s.lock:
            s.db.executescript(_SCHEMA)
            have = {r["name"] for r in s.db.execute("PRAGMA table_info(biz)")}
            if "target" not in have:
                s.db.execute("ALTER TABLE biz ADD COLUMN target REAL DEFAULT 0")
            s.db.commit()
        s._bizplan_ready = True


def _q(sql: str, args: tuple = ()) -> list[dict]:
    _ready()
    return biz._q(sql, args)


def _x(sql: str, args: tuple = ()) -> int:
    _ready()
    return biz._x(sql, args)


# ── 8. targets and trends ─────────────────────────────────
def set_target(name: str, amount: float) -> dict:
    b = biz.need(name)
    amount = max(0.0, float(amount or 0))
    _x("UPDATE biz SET target=? WHERE id=?", (amount, b["id"]))
    return {**b, "target": amount}


def trend(b: dict, today: dt.date | None = None) -> dict:
    """The month against its target, and the last eight weeks of sales and enquiries."""
    today = today or dt.date.today()
    row = _q("SELECT target FROM biz WHERE id=?", (b["id"],))
    target = float(row[0]["target"] or 0) if row else 0.0
    sales = _q("SELECT ts, amount FROM biz_money WHERE biz=? AND kind='sale'", (b["id"],))
    leads = _q("SELECT ts FROM biz_leads WHERE biz=?", (b["id"],))
    monday = today - dt.timedelta(days=today.weekday())
    weeks = []
    for i in range(7, -1, -1):
        a = monday - dt.timedelta(weeks=i)
        z = (a + dt.timedelta(days=7)).isoformat()
        weeks.append({"label": f"{a.day} {a.strftime('%b')}", "now": i == 0,
                      "sales": sum(m["amount"] for m in sales if a.isoformat() <= m["ts"][:10] < z),
                      "leads": sum(1 for x in leads if a.isoformat() <= x["ts"][:10] < z)})
    first = today.replace(day=1)
    days_in = ((first + dt.timedelta(days=32)).replace(day=1) - first).days
    month = sum(m["amount"] for m in sales if m["ts"][:10] >= first.isoformat())
    pace = month / today.day * days_in
    return {"target": target, "month": month, "pace": pace, "day": today.day, "days": days_in,
            "pct": round(100 * month / target) if target else None, "weeks": weeks,
            "on_track": bool(target) and pace >= target}


# ── 7. customers ──────────────────────────────────────────
def _key(name: str, contact: str) -> tuple[str, str, str]:
    """(who they are for matching, email, phone)."""
    e = biz.EMAIL.search(contact or "")
    p = biz.PHONE.search(contact or "")
    email = e.group(0).lower() if e else ""
    phone = re.sub(r"\D", "", p.group(0)) if p else ""
    clean = re.sub(r"\s*\(order [^)]*\)|^(listing fee|featured listing|sale)\s*[—-]\s*", "", name or "", flags=re.I).strip()
    return email or re.sub(r"[^a-z0-9]+", " ", clean.lower()).strip(), email, phone


def customers(b: dict, today: dt.date | None = None) -> list[dict]:
    """Everyone who enquired or bought, newest activity first."""
    today = today or dt.date.today()
    people: dict[str, dict] = {}

    def person(name, contact, ts):
        key, email, phone = _key(name, contact)
        if not key or key in ("someone", "customer", "online payment") or key.startswith("online order"):
            return None
        p = people.setdefault(key, {"key": key, "name": "", "email": "", "phone": "", "enquiries": 0, "orders": 0,
                                    "spent": 0.0, "first": ts, "last": ts, "won": False})
        shown = re.sub(r"\s*\(order [^)]*\)", "", name or "").strip()
        if shown and (not p["name"] or "@" in p["name"]):
            p["name"] = shown
        p["email"], p["phone"] = p["email"] or email, p["phone"] or phone
        p["first"], p["last"] = min(p["first"], ts), max(p["last"], ts)
        return p
    by_name: dict[str, str] = {}
    for x in _q("SELECT * FROM biz_leads WHERE biz=? ORDER BY id", (b["id"],)):
        p = person(x["name"], x["contact"], x["ts"])
        if p:
            p["enquiries"] += 1
            p["won"] = p["won"] or x["status"] == "won"
            by_name[re.sub(r"[^a-z0-9]+", " ", (x["name"] or "").lower()).strip()] = p["key"]
    for m in _q("SELECT * FROM biz_money WHERE biz=? AND kind='sale' ORDER BY id", (b["id"],)):
        key, email, _ = _key(m["party"], m["note"] or "")
        if not email and key in by_name:                       # the same person who enquired by email
            p = people[by_name[key]]
            p["last"] = max(p["last"], m["ts"])
        else:
            p = person(m["party"], m["note"] or "", m["ts"])
        if p:
            p["orders"] += 1
            p["spent"] += m["amount"]
    out = []
    for p in people.values():
        p["quiet_days"] = (today - dt.date.fromisoformat(p["last"][:10])).days
        p["repeat"] = p["orders"] >= 2
        p["name"] = p["name"] or p["email"] or p["key"].title()
        out.append(p)
    return sorted(out, key=lambda p: p["last"], reverse=True)


def win_back(today: dt.date | None = None) -> int:
    """Draft a friendly message for customers who bought (or were won) and have gone quiet. Returns how many."""
    today = today or dt.date.today()
    c = biz.cfg()
    quiet, cap = int(c.get("quiet_days", 60)), int(c.get("win_back_per_week", 3))
    n = 0
    for b in biz.businesses():
        week = (dt.datetime.combine(today, dt.time()) - dt.timedelta(days=7)).isoformat()
        room = cap - len(_q("SELECT id FROM biz_nudges WHERE biz=? AND ts>=?", (b["id"], week)))
        for p in customers(b, today):
            if room <= 0:
                break
            if not p["email"] or not (p["orders"] or p["won"]) or p["quiet_days"] < quiet:
                continue
            if _q("SELECT id FROM biz_nudges WHERE biz=? AND who=?", (b["id"], p["key"])):
                continue
            first = p["name"].split()[0] if p["name"] and "@" not in p["name"] else "there"
            fallback = (f"Hi {first},\n\nIt's been a while since we last heard from you at {b['name']}, so I wanted to "
                        f"check in. Is there anything we can help you with?\n\nKind regards,\n{biz.owner()}\n{b['name']} — {b['url']}")
            body = biz._write(
                f"Write a short, warm check-in email from {biz.owner()} of {b['name']} ({b['about']}) to {p['name']}, a "
                f"past customer we haven't heard from in {p['quiet_days']} days. No discount unless one is in the facts, "
                "no pressure, one easy question to answer. Under 70 words, plain text, no subject line, nothing invented.",
                fallback)
            tool, args = biz.mail_tool(b, p["email"], f"Checking in — {b['name']}", body)
            biz.enqueue(b["id"], "follow-up", f"Win back {p['name']} — quiet for {p['quiet_days']} days", body, tool, args,
                        f"winback:{p['key']}")
            _x("INSERT INTO biz_nudges(biz,who,ts) VALUES(?,?,?)", (b["id"], p["key"], biz.now()))
            room -= 1
            n += 1
    return n


# ── 5. the content calendar ───────────────────────────────
def posts(name: str, today: dt.date | None = None) -> list[dict]:
    today = today or dt.date.today()
    since = (today - dt.timedelta(days=2)).isoformat()
    return _q("SELECT * FROM biz_posts WHERE biz=? AND status!='skipped' AND (day>=? OR status='planned') ORDER BY day, id",
              (name, since))[:14]


def plan_week(b: dict, today: dt.date | None = None, brief: str = "") -> list[dict]:
    """Draft the posts for the weekdays ahead: the rest of this week from tomorrow, or all of next week when fewer
    than three weekdays are left. Planned posts that haven't been used yet are replaced."""
    today = today or dt.date.today()
    start = today + dt.timedelta(days=1)
    if start.weekday() >= 5:
        start += dt.timedelta(days=7 - start.weekday())
    days = [start + dt.timedelta(days=i) for i in range(5 - start.weekday())]          # the rest of this week…
    if len(days) < 3:                                                                   # …or, late in the week, all of next
        monday = start + dt.timedelta(days=7 - start.weekday())
        days = [monday + dt.timedelta(days=i) for i in range(5)]
    slots = [(d, *next((p[1:] for p in PLAN if p[0] == DAYS[d.weekday()]), ("Facebook", "something useful"))) for d in days]
    drafted = []
    if context.llm:
        try:
            raw = context.llm.complete(
                f"Plan a week of social media posts for {b['name']} ({b['url']}): {b['about']}\n"
                + (f"This week's focus: {brief}\n" if brief else "")
                + "One post for each of these slots:\n"
                + "\n".join(f"- {d.isoformat()} ({DAYS[d.weekday()]}), {plat}: {angle}" for d, plat, angle in slots)
                + "\nEach post: ready-to-paste text in South African English (2–4 short lines, at most 4 hashtags at the "
                  "end) and a one-line idea for the picture or reel. Never invent prices, stock, dates or promises. If "
                  "the business sells age-restricted products (alcohol, vapes, nicotine), write for adults only, make no "
                  "health claims and never appeal to young people.\n"
                  'Reply with JSON only: {"posts": [{"day": "YYYY-MM-DD", "platform": "…", "text": "…", "idea": "…"}]}',
                prefer_smart=True, temperature=0.7)
            m = re.search(r"\{.*\}", raw or "", re.S)
            for p in (json.loads(m.group(0)).get("posts") or []) if m else []:
                if str(p.get("text", "")).strip():
                    drafted.append({"day": str(p.get("day", ""))[:10], "platform": str(p.get("platform", "Facebook"))[:20],
                                    "text": str(p["text"]).strip()[:1200], "idea": str(p.get("idea", "")).strip()[:300]})
        except Exception as e:
            print(f"[business] plain post ideas instead: {e}")
            drafted = []
    valid = {d.isoformat() for d in days}
    if len([p for p in drafted if p["day"] in valid]) < 3:
        site = (b.get("url") or "").replace("https://", "").replace("http://", "")
        drafted = [{"day": d.isoformat(), "platform": plat, "idea": f"A clear photo or short clip that shows {angle}.",
                    "text": f"[{angle[0].upper() + angle[1:]} — write two or three lines in your own words]\n\n{b['name']} · {site}"}
                   for d, plat, angle in slots]
    _x("DELETE FROM biz_posts WHERE biz=? AND status='planned'", (b["id"],))
    for p in drafted:
        if p["day"] in valid:
            _x("INSERT INTO biz_posts(biz,ts,day,platform,text,idea) VALUES(?,?,?,?,?,?)",
               (b["id"], biz.now(), p["day"], p["platform"], p["text"], p["idea"]))
    return posts(b["id"], today)


def set_post(post_id: int, status: str) -> bool:
    if status not in ("posted", "skipped", "planned"):
        return False
    _x("UPDATE biz_posts SET status=? WHERE id=?", (status, int(post_id)))
    return True


def plan_due(today: dt.date | None = None) -> int:
    """Plan next week for every business that has nothing planned ahead. Returns how many weeks were planned."""
    today = today or dt.date.today()
    n = 0
    for b in biz.businesses():
        ahead = _q("SELECT id FROM biz_posts WHERE biz=? AND day>? AND status!='skipped'", (b["id"], today.isoformat()))
        recent = _q("SELECT ts FROM biz_posts WHERE biz=? ORDER BY id DESC LIMIT 1", (b["id"],))
        if ahead or (recent and (dt.datetime.now() - dt.datetime.fromisoformat(recent[0]["ts"])).days < 3):
            continue
        plan_week(b, today)
        n += 1
    return n
