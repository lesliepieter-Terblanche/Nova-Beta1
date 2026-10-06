"""Business mode: Nova runs the day-to-day of your businesses and you only step in to approve or decide.

  cockpit        per business: sales this week, leads, open tasks, money owed, what's waiting on you
  approval queue everything Nova wants to send or do for a business lines up here — nothing leaves without your yes
  lead inbox     enquiries (told to her, or found in the business's mailbox or Gmail) are logged, scored and get a drafted reply to approve
  money watch    sales, who owes you, what you owe, what is overdue
  follow-ups     promises and open threads; she drafts the message when it's due
  standing       rules she runs by herself inside limits you set ("answer every new enquiry", "chase invoices after
  instructions   7 days") — they put drafts in the queue, or send by themselves only if you said so, capped per day
  creating       an idea validator (demand, competitors, pricing → go / no-go), a launch kit (brand, landing page
                 brief, first week of posts, launch email) and an offer tester
  weekly review  per business: what moved, what stalled, the three things to do next

Everything is kept in Nova's own database. The page is the Business dashboard (/business), linked from the main one.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from pathlib import Path

from . import context
from .config import resolve

_SCHEMA = """
CREATE TABLE IF NOT EXISTS biz(id TEXT PRIMARY KEY, name TEXT, url TEXT, about TEXT, inbox TEXT DEFAULT '',
  currency TEXT DEFAULT 'R', created TEXT);
CREATE TABLE IF NOT EXISTS biz_leads(id INTEGER PRIMARY KEY, biz TEXT, ts TEXT, name TEXT, contact TEXT, source TEXT,
  message TEXT, score INTEGER DEFAULT 3, status TEXT DEFAULT 'new', ref TEXT DEFAULT '', note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS biz_money(id INTEGER PRIMARY KEY, biz TEXT, ts TEXT, kind TEXT, party TEXT, amount REAL,
  due TEXT DEFAULT '', status TEXT DEFAULT 'open', note TEXT DEFAULT '', chased TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS biz_tasks(id INTEGER PRIMARY KEY, biz TEXT, ts TEXT, text TEXT, status TEXT DEFAULT 'open',
  due TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS biz_queue(id INTEGER PRIMARY KEY, biz TEXT, ts TEXT, kind TEXT, title TEXT, body TEXT,
  tool TEXT DEFAULT '', args TEXT DEFAULT '{}', status TEXT DEFAULT 'pending', result TEXT DEFAULT '',
  source TEXT DEFAULT '', done TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS biz_rules(id INTEGER PRIMARY KEY, biz TEXT, ts TEXT, trigger TEXT, text TEXT,
  after_days INTEGER DEFAULT 0, auto INTEGER DEFAULT 0, max_per_day INTEGER DEFAULT 5, enabled INTEGER DEFAULT 1,
  last_run TEXT DEFAULT '', runs_day TEXT DEFAULT '', runs_today INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS biz_followups(id INTEGER PRIMARY KEY, biz TEXT, ts TEXT, who TEXT, contact TEXT DEFAULT '',
  what TEXT, due TEXT, status TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS biz_offers(id INTEGER PRIMARY KEY, biz TEXT, ts TEXT, name TEXT, variants TEXT,
  status TEXT DEFAULT 'running');
"""
MONEY_KINDS = {"sale": "sale", "sold": "sale", "income": "sale", "owed_to_me": "owed_to_me", "invoice": "owed_to_me",
               "owed": "owed_to_me", "owes me": "owed_to_me", "receivable": "owed_to_me", "i_owe": "i_owe",
               "bill": "i_owe", "expense": "i_owe", "payable": "i_owe", "i owe": "i_owe"}
TRIGGERS = {"new_lead": "new_lead", "new enquiry": "new_lead", "new lead": "new_lead", "enquiry": "new_lead",
            "lead": "new_lead", "invoice_overdue": "invoice_overdue", "overdue": "invoice_overdue",
            "unpaid": "invoice_overdue", "daily": "daily", "every day": "daily", "weekly": "weekly",
            "every week": "weekly"}
HOT = re.compile(r"\b(price|pricing|cost|how much|quote|buy|order|list my|listing|sign ?up|register|subscribe|book|"
                 r"viewing|interested|available|call me|urgent|today|asap|package|rate card|advertis)\w*", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?:\+?\d[\d\s-]{8,}\d)")


# ── storage ───────────────────────────────────────────────
def _db():
    s = context.store
    if s is None:
        raise RuntimeError("Nova's memory isn't open")
    if not getattr(s, "_biz_ready", False):
        with s.lock:
            s.db.executescript(_SCHEMA)
            have = {r["name"] for r in s.db.execute("PRAGMA table_info(biz)")}
            if "email" not in have:                   # v2.36.1: the address each business's enquiries arrive at
                s.db.execute("ALTER TABLE biz ADD COLUMN email TEXT DEFAULT ''")
            s.db.commit()
        s._biz_ready = True
    return s


def _q(sql: str, args: tuple = ()) -> list[dict]:
    s = _db()
    with s.lock:
        return [dict(r) for r in s.db.execute(sql, args)]


def _x(sql: str, args: tuple = ()) -> int:
    s = _db()
    with s.lock:
        cur = s.db.execute(sql, args)
        s.db.commit()
        return cur.lastrowid


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def cfg() -> dict:
    return dict(((context.cfg or {}).get("business") or {}))


def owner() -> str:
    return str(((context.cfg or {}).get("assistant") or {}).get("owner", "the owner"))


# ── businesses ────────────────────────────────────────────
def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40]


def add_business(name: str, url: str = "", about: str = "", inbox: str = "", currency: str = "R") -> dict:
    bid = slug(name)
    if not bid:
        raise ValueError("a business needs a name")
    url = url.strip()
    if url and not url.startswith("http"):
        url = "https://" + url
    have = _q("SELECT * FROM biz WHERE id=?", (bid,))
    if have:
        _x("UPDATE biz SET name=?, url=COALESCE(NULLIF(?,''),url), about=COALESCE(NULLIF(?,''),about), "
           "inbox=COALESCE(NULLIF(?,''),inbox) WHERE id=?", (name.strip(), url, about.strip(), inbox.strip(), bid))
    else:
        _x("INSERT INTO biz(id,name,url,about,inbox,currency,created) VALUES(?,?,?,?,?,?,?)",
           (bid, name.strip(), url, about.strip(), inbox.strip(), currency, now()))
    return _q("SELECT * FROM biz WHERE id=?", (bid,))[0]


def inbox_query(b: dict) -> str:
    """The Gmail search that finds this business's enquiries: your own search if you gave one, else mail sent to
    its enquiry address, else (nothing set yet) unread mail that mentions the business."""
    if (b.get("inbox") or "").strip():
        return b["inbox"].strip()
    if (b.get("email") or "").strip():
        e = b["email"].strip()
        return f"is:unread newer_than:14d (to:{e} OR deliveredto:{e} OR cc:{e})"
    host = re.sub(r"^https?://(www\.)?", "", b.get("url") or "").split("/")[0]
    return f"is:unread newer_than:7d ({json.dumps(b['name'])}" + (f" OR {host}" if host else "") + ")"


def set_up(biz: str, email: str | None = None, inbox: str | None = None) -> dict:
    """Set where a business's enquiries arrive (its email address) and, optionally, your own Gmail search."""
    b = need(biz)
    if email is not None:
        email = email.strip()
        if email and not EMAIL.fullmatch(email):
            raise ValueError(f"'{email}' doesn't look like an email address")
        _x("UPDATE biz SET email=? WHERE id=?", (email.lower(), b["id"]))
    if inbox is not None:
        _x("UPDATE biz SET inbox=? WHERE id=?", (inbox.strip()[:300], b["id"]))
    return _q("SELECT * FROM biz WHERE id=?", (b["id"],))[0]


def businesses() -> list[dict]:
    return _q("SELECT * FROM biz ORDER BY rowid")


def find(name: str = "") -> dict | None:
    """The business meant by a spoken name. With one business, or no name and one business, that one."""
    all_ = businesses()
    key = slug(name)
    if not key:
        return all_[0] if len(all_) == 1 else None
    for b in all_:
        if b["id"] == key:
            return b
    compact = key.replace("-", "")
    for b in all_:
        bid = b["id"].replace("-", "")
        host = re.sub(r"^https?://(www\.)?", "", b["url"] or "").split("/")[0].replace(".", "").replace("-", "")
        if compact in bid or bid in compact or (host and (compact in host or host.startswith(compact))):
            return b
    return None


def need(name: str) -> dict:
    b = find(name)
    if not b:
        names = ", ".join(x["name"] for x in businesses()) or "none yet"
        raise ValueError(f"which business? I have: {names}")
    return b


def money_text(b: dict, amount: float) -> str:
    return f"{b.get('currency') or 'R'}{amount:,.0f}".replace(",", " ")


# ── the approval queue ────────────────────────────────────
def enqueue(biz: str, kind: str, title: str, body: str, tool: str = "", args: dict | None = None,
            source: str = "") -> int:
    """Line something up for a yes: kind is reply / email / follow-up / chase / post / page / other. With a tool,
    approving runs it (e.g. gmail_send); without, approving just marks it done for you to carry out."""
    return _x("INSERT INTO biz_queue(biz,ts,kind,title,body,tool,args,source) VALUES(?,?,?,?,?,?,?,?)",
              (biz, now(), kind, title[:200], body, tool, json.dumps(args or {}, ensure_ascii=False), source))


def pending(biz: str = "") -> list[dict]:
    rows = _q("SELECT * FROM biz_queue WHERE status='pending' ORDER BY id")
    return [r for r in rows if not biz or r["biz"] == biz]


def queue_item(item) -> dict | None:
    """A pending item by its number, or by words from its title."""
    text = str(item or "").strip().lower()
    rows = pending()
    if not rows:
        return None
    m = re.fullmatch(r"#?\s*(\d+)", text)
    if m:
        return next((r for r in rows if r["id"] == int(m.group(1))), None)
    if text in ("", "it", "that", "the last one", "last", "latest"):
        return rows[-1]
    want = set(re.findall(r"[a-z0-9]{3,}", text))
    best = max(rows, key=lambda r: len(want & set(re.findall(r"[a-z0-9]{3,}", (r["title"] + " " + r["biz"]).lower()))))
    return best if want & set(re.findall(r"[a-z0-9]{3,}", (best["title"] + " " + best["biz"]).lower())) else None


def _body_key(args: dict) -> str:
    return next((k for k in ("body", "text", "content", "description") if k in args), "")


def approve(item_id: int, edit: str = "") -> dict:
    """You said yes: run the item's tool (with your edited text, if any) and close it."""
    rows = _q("SELECT * FROM biz_queue WHERE id=?", (int(item_id),))
    if not rows or rows[0]["status"] != "pending":
        return {"ok": False, "message": "That item isn't waiting any more."}
    it = rows[0]
    body = edit.strip() or it["body"]
    result, ok = "Marked as done.", True
    if it["tool"]:
        from .tools import REGISTRY
        t = REGISTRY.get(it["tool"])
        if t is None:
            result, ok = (f"I can't do this one myself: the '{it['tool'].replace('_', ' ')}' ability isn't available "
                          "(is Google, or the business's mailbox, connected?). It stays in the queue."), False
        else:
            args = json.loads(it["args"] or "{}")
            key = _body_key(args)
            if key:
                args[key] = body
            result = str(t.run(args))
            ok = not result.startswith("ERROR")
    if not ok:
        _x("UPDATE biz_queue SET result=? WHERE id=?", (result[:1000], it["id"]))
        return {"ok": False, "message": result}
    _x("UPDATE biz_queue SET status='approved', body=?, result=?, done=? WHERE id=?",
       (body, result[:1000], now(), it["id"]))
    src = it["source"] or ""
    if src.startswith("lead:"):
        _x("UPDATE biz_leads SET status='replied' WHERE id=? AND status IN ('new','drafted')", (src[5:],))
    elif src.startswith("followup:"):
        _x("UPDATE biz_followups SET status='done' WHERE id=?", (src[9:],))
    elif src.startswith("money:"):
        _x("UPDATE biz_money SET chased=? WHERE id=?", (dt.date.today().isoformat(), src[6:]))
    return {"ok": True, "message": result, "item": {**it, "body": body}}


def reject(item_id: int) -> dict:
    rows = _q("SELECT * FROM biz_queue WHERE id=?", (int(item_id),))
    if not rows or rows[0]["status"] != "pending":
        return {"ok": False, "message": "That item isn't waiting any more."}
    it = rows[0]
    _x("UPDATE biz_queue SET status='rejected', done=? WHERE id=?", (now(), it["id"]))
    src = it["source"] or ""
    if src.startswith("lead:"):                      # the lead stays, you just didn't like the draft
        _x("UPDATE biz_leads SET status='new', note='draft rejected' WHERE id=? AND status='drafted'", (src[5:],))
    elif src.startswith("followup:"):
        _x("UPDATE biz_followups SET status='done' WHERE id=?", (src[9:],))
    return {"ok": True, "message": "Dropped.", "item": it}


# ── which mail a business uses ────────────────────────────
def mail_tool(b: dict, to: str, subject: str, body: str, in_reply_to: str = "") -> tuple[str, dict]:
    """The tool (and its arguments) that sends an email for this business: from its own mailbox when that is
    connected (v2.37), else from the Gmail account Nova is connected to."""
    from . import mailbox
    if mailbox.can_send(b["id"]):
        return "business_send_email", {"business": b["id"], "to": to, "subject": subject, "body": body,
                                       "in_reply_to": in_reply_to}
    return "gmail_send", {"to": to, "subject": subject, "body": body}


# ── leads ─────────────────────────────────────────────────
def score_lead(message: str, contact: str = "") -> int:
    """1 (cold) to 5 (hot), from what they wrote: buying words, a way to reach them, some detail."""
    text = message or ""
    s = 2 + min(2, len(set(m.group(0).lower() for m in HOT.finditer(text))))
    if PHONE.search(text + " " + contact):
        s += 1
    if len(text.split()) < 4:
        s -= 1
    return max(1, min(5, s))


def _write(prompt: str, fallback: str, temperature: float = 0.4) -> str:
    llm = context.llm
    if llm:
        try:
            out = (llm.complete(prompt, prefer_smart=True, temperature=temperature) or "").strip()
            if out:
                return out
        except Exception as e:
            print(f"[business] the model couldn't write it, using the plain version: {e}")
    return fallback


def draft_reply(b: dict, lead: dict, instruction: str = "") -> str:
    first = (lead["name"] or "").split()[0] if lead["name"] else "there"
    fallback = (f"Hi {first},\n\nThank you for getting in touch with {b['name']}. I've got your message and will come "
                f"back to you with the details shortly.\n\nKind regards,\n{owner()}\n{b['name']} — {b['url']}")
    return _write(
        f"Write a short, warm, businesslike email reply from {owner()} of {b['name']} ({b['url']}).\n"
        f"About the business: {b['about'] or 'not given'}\n"
        f"The enquiry, from {lead['name'] or 'someone'}:\n\"\"\"\n{lead['message'][:1500]}\n\"\"\"\n"
        + (f"House rule for these replies: {instruction}\n" if instruction else "")
        + "Answer what they asked, give one clear next step, and never invent prices, dates or promises that are "
          "not in the facts above — say you'll confirm them instead. Under 130 words. Plain text, no subject line, "
          f"sign off as {owner()}, {b['name']}.", fallback)


def add_lead(biz: str, name: str, contact: str = "", message: str = "", source: str = "told", ref: str = "") -> dict:
    b = need(biz)
    if ref and _q("SELECT id FROM biz_leads WHERE ref=?", (ref,)):       # one email is one lead, for one business
        return {}
    lid = _x("INSERT INTO biz_leads(biz,ts,name,contact,source,message,score,ref) VALUES(?,?,?,?,?,?,?,?)",
             (b["id"], now(), name.strip()[:120], contact.strip()[:200], source, message.strip()[:4000],
              score_lead(message, contact), ref))
    return _q("SELECT * FROM biz_leads WHERE id=?", (lid,))[0]


def queue_reply(lead: dict, instruction: str = "", auto: bool = False) -> int:
    """Draft a reply to a lead and line it up (or, with auto, send it straight away). Returns the queue id."""
    b = need(lead["biz"])
    body = draft_reply(b, lead, instruction)
    email = EMAIL.search(lead["contact"] or "")
    tool, args = "", {}
    if lead["ref"] and lead["source"] == "gmail":
        tool, args = "gmail_reply", {"message_id": lead["ref"], "body": body}
    elif lead["source"] == "mailbox":                 # answered from the business's own address, in the same thread
        from . import mailbox
        subject = (lead["message"].splitlines() or [""])[0].strip()
        if email and mailbox.can_send(b["id"]):
            tool, args = mail_tool(b, email.group(0), subject if subject.lower().startswith("re:") else
                                   f"Re: {subject or 'your enquiry'}", body, lead["ref"][5:])
    elif email:
        tool, args = mail_tool(b, email.group(0), f"Your enquiry — {b['name']}", body)
    qid = enqueue(b["id"], "reply", f"Reply to {lead['name'] or 'the enquiry'}"
                  + (f" ({lead['contact']})" if lead["contact"] and not tool else ""), body, tool, args, f"lead:{lead['id']}")
    _x("UPDATE biz_leads SET status='drafted' WHERE id=?", (lead["id"],))
    if auto and tool:
        approve(qid)
    return qid


def _from_parts(sender: str) -> tuple[str, str]:
    m = EMAIL.search(sender or "")
    email = m.group(0) if m else ""
    name = re.sub(r"<.*?>", "", sender or "").strip().strip('"')
    if not name or "@" in name:                       # only an address: the part before the @ will do as a name
        name = email.split("@")[0]
    return name, email


def scan_inbox(biz: str = "") -> int:
    """Look for new enquiries for each business and log them as leads: in its own mailbox when that is connected,
    else in Gmail (its inbox search)."""
    from . import mailbox
    from .tools import REGISTRY
    found = 0
    for b in ([need(biz)] if biz else businesses()):
        if mailbox.box(b["id"]):
            try:
                hits = mailbox.fetch(b["id"], known=lambda ref: bool(_q("SELECT id FROM biz_leads WHERE ref=?", (ref,))))
            except Exception as e:
                print(f"[business] couldn't read the {b['name']} mailbox: {e}")
                continue
            for h in hits:
                name, email = _from_parts(h["from"])
                lead = add_lead(b["id"], name, email, f"{h['subject']}\n{h['snippet']}".strip(), "mailbox", h["id"])
                found += bool(lead)
            continue
        if "gmail_search" not in REGISTRY:
            continue
        try:
            hits = REGISTRY["gmail_search"].func(query=inbox_query(b), max_results=10)
        except Exception as e:
            print(f"[business] couldn't read Gmail for {b['name']}: {e}")
            continue
        for h in hits if isinstance(hits, list) else []:
            name, email = _from_parts(h.get("from", ""))
            lead = add_lead(b["id"], name, email, f"{h.get('subject', '')}\n{h.get('snippet', '')}".strip(), "gmail", h["id"])
            found += bool(lead)
    return found


# ── money ─────────────────────────────────────────────────
def add_money(biz: str, kind: str, party: str, amount: float, due: str = "", note: str = "") -> dict:
    b = need(biz)
    k = MONEY_KINDS.get(re.sub(r"\s+", " ", kind.lower().replace("-", "_")).strip()) or MONEY_KINDS.get(kind.lower().strip())
    if not k:
        raise ValueError("kind must be sale, owed_to_me (an invoice you sent) or i_owe (a bill to pay)")
    amount = float(amount)
    if amount <= 0:
        raise ValueError("the amount must be more than zero")
    when = parse_day(due) if due else ""
    mid = _x("INSERT INTO biz_money(biz,ts,kind,party,amount,due,status,note) VALUES(?,?,?,?,?,?,?,?)",
             (b["id"], now(), k, party.strip()[:120], amount, when, "paid" if k == "sale" else "open", note[:500]))
    return _q("SELECT * FROM biz_money WHERE id=?", (mid,))[0]


def mark_paid(biz: str, party: str) -> dict | None:
    b = need(biz)
    want = set(re.findall(r"[a-z0-9]{2,}", party.lower()))
    rows = _q("SELECT * FROM biz_money WHERE biz=? AND status='open' ORDER BY id", (b["id"],))
    m = re.fullmatch(r"#?\s*(\d+)", party.strip())
    hit = next((r for r in rows if m and r["id"] == int(m.group(1))), None) or \
        next((r for r in rows if want & set(re.findall(r"[a-z0-9]{2,}", r["party"].lower()))), None)
    if not hit:
        return None
    _x("UPDATE biz_money SET status='paid' WHERE id=?", (hit["id"],))
    if hit["kind"] == "owed_to_me":                   # money that came in is a sale of that day
        _x("INSERT INTO biz_money(biz,ts,kind,party,amount,status,note) VALUES(?,?,?,?,?,?,?)",
           (b["id"], now(), "sale", hit["party"], hit["amount"], "paid", f"invoice #{hit['id']} paid"))
    return hit


def parse_day(text: str) -> str:
    """'Friday', 'in 3 days', '2026-10-20' → an ISO date. Raises ValueError when it can't be read."""
    text = (text or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    import dateparser
    t = dateparser.parse(text, settings={"PREFER_DATES_FROM": "future"})
    if t is None:
        raise ValueError(f"I couldn't work out the date '{text}'. Try 'Friday' or '2026-10-20'.")
    return t.date().isoformat()


# ── tasks and follow-ups ──────────────────────────────────
def add_task(biz: str, text: str, due: str = "") -> dict:
    b = need(biz)
    tid = _x("INSERT INTO biz_tasks(biz,ts,text,due) VALUES(?,?,?,?)", (b["id"], now(), text.strip()[:300],
                                                                      parse_day(due) if due else ""))
    return _q("SELECT * FROM biz_tasks WHERE id=?", (tid,))[0]


def done_task(biz: str, text: str) -> dict | None:
    b = need(biz)
    rows = _q("SELECT * FROM biz_tasks WHERE biz=? AND status='open' ORDER BY id", (b["id"],))
    m = re.fullmatch(r"#?\s*(\d+)", text.strip())
    want = set(re.findall(r"[a-z0-9]{3,}", text.lower()))
    hit = next((r for r in rows if m and r["id"] == int(m.group(1))), None) or \
        max(rows, key=lambda r: len(want & set(re.findall(r"[a-z0-9]{3,}", r["text"].lower()))), default=None)
    if not hit or (not m and not want & set(re.findall(r"[a-z0-9]{3,}", hit["text"].lower()))):
        return None
    _x("UPDATE biz_tasks SET status='done' WHERE id=?", (hit["id"],))
    return hit


def add_followup(biz: str, who: str, what: str, when: str = "in 3 days", contact: str = "") -> dict:
    b = need(biz)
    fid = _x("INSERT INTO biz_followups(biz,ts,who,contact,what,due) VALUES(?,?,?,?,?,?)",
             (b["id"], now(), who.strip()[:120], contact.strip()[:200], what.strip()[:500], parse_day(when)))
    return _q("SELECT * FROM biz_followups WHERE id=?", (fid,))[0]


def due_followups(today: dt.date | None = None) -> int:
    """Follow-ups that are due get a drafted message in the queue. Returns how many."""
    today = today or dt.date.today()
    n = 0
    for f in _q("SELECT * FROM biz_followups WHERE status='open' AND due<=? ORDER BY id", (today.isoformat(),)):
        b = find(f["biz"])
        if not b:
            continue
        first = f["who"].split()[0] if f["who"] else "there"
        fallback = (f"Hi {first},\n\nJust following up on {f['what']}. Is there anything you need from me to move this "
                    f"forward?\n\nKind regards,\n{owner()}\n{b['name']}")
        body = _write(f"Write a short, friendly follow-up message from {owner()} of {b['name']} to {f['who']} about: "
                      f"{f['what']}. One clear question or next step. Under 80 words. Plain text, no subject line, "
                      "nothing invented.", fallback)
        email = EMAIL.search(f["contact"] or "")
        tool, args = mail_tool(b, email.group(0), f"Following up — {b['name']}", body) if email else ("", {})
        enqueue(b["id"], "follow-up", f"Follow up with {f['who']}: {f['what'][:60]}", body, tool, args, f"followup:{f['id']}")
        _x("UPDATE biz_followups SET status='queued' WHERE id=?", (f["id"],))
        n += 1
    return n


# ── standing instructions ─────────────────────────────────
def add_rule(biz: str, when: str, text: str, after_days: int = 0, auto: bool = False, max_per_day: int = 5) -> dict:
    b = need(biz)
    trig = TRIGGERS.get(re.sub(r"\s+", " ", when.lower().replace("-", " ")).strip()) or TRIGGERS.get(when.lower().strip())
    if not trig:
        raise ValueError("when must be: new_lead, invoice_overdue, daily or weekly")
    rid = _x("INSERT INTO biz_rules(biz,ts,trigger,text,after_days,auto,max_per_day) VALUES(?,?,?,?,?,?,?)",
             (b["id"], now(), trig, text.strip()[:600], max(0, int(after_days or 0)),
              int(bool(auto) and trig in ("new_lead", "invoice_overdue")), max(1, min(50, int(max_per_day or 5)))))
    return _q("SELECT * FROM biz_rules WHERE id=?", (rid,))[0]


def rules(biz: str = "") -> list[dict]:
    rows = _q("SELECT * FROM biz_rules ORDER BY id")
    return [r for r in rows if not biz or r["biz"] == biz]


def set_rule(rule_id: int, enabled: bool | None = None, delete: bool = False) -> bool:
    if delete:
        _x("DELETE FROM biz_rules WHERE id=?", (int(rule_id),))
        return True
    if enabled is not None:
        _x("UPDATE biz_rules SET enabled=? WHERE id=?", (int(bool(enabled)), int(rule_id)))
    return True


def _room(rule: dict, today: str) -> int:
    """How many more times this rule may act by itself today."""
    used = rule["runs_today"] if rule["runs_day"] == today else 0
    return max(0, rule["max_per_day"] - used)


def _used(rule: dict, today: str, n: int) -> None:
    used = (rule["runs_today"] if rule["runs_day"] == today else 0) + n
    _x("UPDATE biz_rules SET runs_day=?, runs_today=?, last_run=? WHERE id=?", (today, used, now(), rule["id"]))
    rule.update(runs_day=today, runs_today=used)


def describe_rule(r: dict) -> str:
    when = {"new_lead": "When a new enquiry comes in", "daily": "Every day", "weekly": "Every week",
            "invoice_overdue": f"When an invoice is {r['after_days'] or 0}+ days overdue"}[r["trigger"]]
    how = f"sends by herself (max {r['max_per_day']} a day)" if r["auto"] else "drafts it for your yes"
    return f"{when}: {r['text']} — {how}"


def run_rules(today: dt.date | None = None) -> dict:
    """Carry out the standing instructions. Returns {"queued": n, "sent": n}."""
    today = today or dt.date.today()
    day = today.isoformat()
    out = {"queued": 0, "sent": 0}
    for r in [x for x in rules() if x["enabled"]]:
        b = find(r["biz"])
        if not b:
            continue
        if r["trigger"] == "new_lead":
            for lead in _q("SELECT * FROM biz_leads WHERE biz=? AND status='new' AND note!='draft rejected' ORDER BY id",
                           (b["id"],)):
                auto = bool(r["auto"]) and _room(r, day) > 0
                qid = queue_reply(lead, r["text"], auto=auto)
                sent = bool(_q("SELECT id FROM biz_queue WHERE id=? AND status='approved'", (qid,)))
                out["sent" if sent else "queued"] += 1
                if sent:
                    _used(r, day, 1)
        elif r["trigger"] == "invoice_overdue":
            limit = (today - dt.timedelta(days=r["after_days"] or 0)).isoformat()
            for m in _q("SELECT * FROM biz_money WHERE biz=? AND kind='owed_to_me' AND status='open' AND due!='' "
                        "AND due<=? AND chased='' ORDER BY id", (b["id"], limit)):
                late = (today - dt.date.fromisoformat(m["due"])).days
                first = m["party"].split()[0] if m["party"] else "there"
                fallback = (f"Hi {first},\n\nA friendly reminder that {money_text(b, m['amount'])} was due on {m['due']} "
                            f"and is still open on our side. Could you let me know when we can expect payment?\n\n"
                            f"Kind regards,\n{owner()}\n{b['name']}")
                body = _write(f"Write a polite, firm payment reminder from {owner()} of {b['name']} to {m['party']}: "
                              f"{money_text(b, m['amount'])} was due on {m['due']} ({late} days ago). "
                              + (f"House rule: {r['text']}. " if r["text"] else "")
                              + "Under 90 words, plain text, no subject line, nothing invented.", fallback)
                email = EMAIL.search(m["note"] or "") or EMAIL.search(m["party"] or "")
                tool, args = mail_tool(b, email.group(0), f"Payment reminder — {b['name']}", body) if email else ("", {})
                qid = enqueue(b["id"], "chase", f"Chase {m['party']} for {money_text(b, m['amount'])} ({late} days late)",
                              body, tool, args, f"money:{m['id']}")
                _x("UPDATE biz_money SET chased=? WHERE id=?", ("queued", m["id"]))
                if r["auto"] and tool and _room(r, day) > 0 and approve(qid)["ok"]:
                    out["sent"] += 1
                    _used(r, day, 1)
                else:
                    out["queued"] += 1
        elif r["trigger"] in ("daily", "weekly"):
            last = (r["last_run"] or "")[:10]
            gap = 1 if r["trigger"] == "daily" else 7
            if last and (today - dt.date.fromisoformat(last)).days < gap:
                continue
            text = _write(f"You are {owner()}'s business assistant for {b['name']} ({b['url']}): {b['about']}\n"
                          f"Standing instruction: {r['text']}\nDo it now and write the result, ready to use. Plain "
                          "text. Nothing invented about prices, dates or customers.", "")
            _x("UPDATE biz_rules SET last_run=? WHERE id=?", (now(), r["id"]))
            if text:
                enqueue(b["id"], "other", r["text"][:120], text, "", {}, f"rule:{r['id']}")
                out["queued"] += 1
    return out


# ── the cockpit ───────────────────────────────────────────
def cockpit(b: dict, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    week = (today - dt.timedelta(days=6)).isoformat()
    month = today.replace(day=1).isoformat()
    money = _q("SELECT * FROM biz_money WHERE biz=? ORDER BY id DESC", (b["id"],))
    sales_w = sum(m["amount"] for m in money if m["kind"] == "sale" and m["ts"][:10] >= week)
    sales_m = sum(m["amount"] for m in money if m["kind"] == "sale" and m["ts"][:10] >= month)
    owed = [m for m in money if m["kind"] == "owed_to_me" and m["status"] == "open"]
    owe = [m for m in money if m["kind"] == "i_owe" and m["status"] == "open"]
    late = [m for m in owed if m["due"] and m["due"] < today.isoformat()]
    leads = _q("SELECT * FROM biz_leads WHERE biz=? ORDER BY id DESC LIMIT 60", (b["id"],))
    open_leads = [x for x in leads if x["status"] in ("new", "drafted")]
    tasks = _q("SELECT * FROM biz_tasks WHERE biz=? AND status='open' ORDER BY due='' , due, id", (b["id"],))
    fups = _q("SELECT * FROM biz_followups WHERE biz=? AND status IN ('open','queued') ORDER BY due", (b["id"],))
    from . import mailbox
    return {"id": b["id"], "name": b["name"], "url": b["url"], "about": b["about"], "currency": b["currency"] or "R",
            "mailbox": mailbox.status(b["id"]),
            "inbox": b["inbox"], "email": b.get("email") or "", "watching": inbox_query(b),
            "gmail": gmail_connected(),
            "kpi": {"sales_week": sales_w, "sales_month": sales_m, "leads_week": sum(x["ts"][:10] >= week for x in leads),
                    "leads_open": len(open_leads), "tasks_open": len(tasks), "waiting": len(pending(b["id"])),
                    "owed": sum(m["amount"] for m in owed), "overdue": sum(m["amount"] for m in late),
                    "overdue_n": len(late), "i_owe": sum(m["amount"] for m in owe)},
            "queue": pending(b["id"]), "leads": leads[:25], "tasks": tasks[:30], "followups": fups[:20],
            "money": [m for m in money if m["status"] == "open"][:30] + [m for m in money if m["kind"] == "sale"][:10],
            "rules": [{**r, "says": describe_rule(r)} for r in rules(b["id"])],
            "offers": [{**o, "variants": json.loads(o["variants"] or "[]")}
                       for o in _q("SELECT * FROM biz_offers WHERE biz=? ORDER BY id DESC LIMIT 6", (b["id"],))]}


def gmail_connected() -> bool:
    """Has Google been connected (Settings → Google)? Without it Nova can't read or send the businesses' mail."""
    try:
        from .tools import REGISTRY
        token = str(((context.cfg or {}).get("google") or {}).get("token_file") or "secrets/token.json")
        return "gmail_search" in REGISTRY and resolve(token).exists()
    except Exception:
        return False


def overview_text(b: dict) -> str:
    c = cockpit(b)
    k = c["kpi"]
    bits = [f"{money_text(b, k['sales_week'])} in sales this week",
            f"{k['leads_open']} open enquir{'y' if k['leads_open'] == 1 else 'ies'}",
            f"{k['tasks_open']} open task{'' if k['tasks_open'] == 1 else 's'}"]
    if k["owed"]:
        bits.append(f"{money_text(b, k['owed'])} owed to you"
                    + (f" ({money_text(b, k['overdue'])} overdue)" if k["overdue"] else ""))
    wait = f" {k['waiting']} thing{'' if k['waiting'] == 1 else 's'} waiting for your yes." if k["waiting"] else \
        " Nothing is waiting on you."
    return f"{b['name']}: " + ", ".join(bits) + "." + wait


# ── creating: idea validator, launch kit, offer tester ────
def _search(query: str, n: int = 4) -> list[dict]:
    from .tools import REGISTRY
    t = REGISTRY.get("web_search")
    if t is None:
        return []
    try:
        hits = t.func(query=query, max_results=n)
        return hits if isinstance(hits, list) else []
    except Exception as e:
        print(f"[business] search failed for '{query}': {e}")
        return []


def _doc(folder: str, title: str, body: str) -> Path:
    d = resolve(f"workspace/business/{folder}")
    d.mkdir(parents=True, exist_ok=True)
    path = d / (re.sub(r"[^\w]+", "_", title)[:60].strip("_") + f"_{dt.datetime.now():%Y%m%d_%H%M}.md")
    path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    context.record("document", title, path, folder)
    return path


def validate_idea(idea: str, market: str = "South Africa") -> dict:
    """Research an idea (demand, competitors, pricing) and write a one-page go / no-go."""
    found = {k: _search(q) for k, q in (("Demand", f"{idea} demand market size {market}"),
                                        ("Competitors", f"{idea} competitors {market}"),
                                        ("Pricing", f"{idea} price pricing {market}"))}
    facts = "\n".join(f"[{k}] {h.get('title')}: {h.get('snippet')} ({h.get('url')})"
                      for k, hits in found.items() for h in hits)
    n = sum(len(h) for h in found.values())
    fallback = ("## Verdict\nI couldn't reach a model to judge this, so here is only what the search found.\n\n"
                + "\n".join(f"## {k}\n" + ("\n".join(f"- {h.get('title')} — {h.get('url')}" for h in hits)
                                             or "- nothing found") for k, hits in found.items()))
    body = _write(
        f"{owner()} is considering this business idea for {market}: {idea}\n\nSearch results:\n{facts or '(none)'}\n\n"
        "Write a one-page assessment with these exact headings:\n## Verdict\n(GO, GO WITH CHANGES or NO-GO, and one "
        "sentence why)\n## Demand\n## Competitors\n## Pricing\n## Biggest risks\n## Cheapest way to test it this week\n"
        "Use only what the search results support; put the source link after each figure; where the results don't "
        "say, write 'not found — needs checking' instead of guessing. Short bullet points.", fallback, 0.3)
    m = re.search(r"\b(NO-GO|GO WITH CHANGES|GO)\b", body)
    path = _doc("ideas", f"Idea check — {idea[:50]}", body)
    return {"verdict": m.group(1) if m else "UNDECIDED", "text": body, "path": str(path), "sources": n}


def _json_from(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def launch_kit(biz: str, brief: str = "") -> dict:
    """A launch kit for a business: positioning, brand colours, a landing-page brief, a week of posts, a reel
    storyboard and a launch email — saved as one document, with each sendable piece in the approval queue."""
    b = need(biz)
    raw = _write(
        f"Create a launch kit for {b['name']} ({b['url']}). About: {b['about'] or brief}\nBrief from {owner()}: "
        f"{brief or 'launch it to its first customers'}\nReply with JSON only:\n"
        '{"tagline": "...", "audience": "who it is for, one sentence", "promise": "the one thing it does better", '
        '"colour": "#hex main colour", "accent": "#hex accent", "landing_page": "a detailed brief for a one-page '
        'landing site: sections, headline, call to action, tone", "posts": ["7 short social posts, one per day, each '
        'with a hook and a call to action"], "reel": ["5 shots for a 20-second launch reel, one line each"], '
        '"email_subject": "...", "email_body": "launch email, under 150 words"}\n'
        "Invent no prices, dates, numbers or testimonials.", "", 0.6)
    d = _json_from(raw)
    posts = [str(p).strip() for p in (d.get("posts") or []) if str(p).strip()][:7]
    reel = [str(p).strip() for p in (d.get("reel") or []) if str(p).strip()][:6]
    if not d.get("tagline") and not posts:
        return {"ok": False, "message": "I couldn't write the kit just now — no model answered. Try again in a minute."}
    queued = []
    try:                                              # the brand kit feeds the cinematic reels
        from . import cinema
        if not cinema.brand(b["name"]) and d.get("colour"):
            cinema.save_brand(b["name"], handle=re.sub(r"^https?://(www\.)?", "", b["url"]).rstrip("/"),
                              tagline=str(d.get("tagline", ""))[:80], colour=str(d.get("colour", "")),
                              accent=str(d.get("accent", "")))
    except Exception as e:
        print(f"[business] brand kit not saved: {e}")
    if d.get("landing_page"):
        queued.append(enqueue(b["id"], "page", "Build the launch landing page", str(d["landing_page"]), "build_website",
                              {"name": f"{b['id']}-launch", "description": str(d["landing_page"])}, "launch"))
    for i, p in enumerate(posts, 1):
        queued.append(enqueue(b["id"], "post", f"Launch post {i} of {len(posts)}", p, "", {}, "launch"))
    if d.get("email_body"):
        queued.append(enqueue(b["id"], "email", f"Launch email: {str(d.get('email_subject', 'We are live'))[:80]}",
                              str(d["email_body"]), "gmail_draft",
                              {"to": "", "subject": str(d.get("email_subject", f"{b['name']} is live")),
                               "body": str(d["email_body"])}, "launch"))
    body = (f"**Tagline:** {d.get('tagline', '')}\n\n**For:** {d.get('audience', '')}\n\n**Promise:** {d.get('promise', '')}\n\n"
            f"**Colours:** {d.get('colour', '')} / {d.get('accent', '')}\n\n## Landing page\n{d.get('landing_page', '')}\n\n"
            "## First week of posts\n" + "\n".join(f"{i}. {p}" for i, p in enumerate(posts, 1))
            + "\n\n## Launch reel (20 seconds)\n" + "\n".join(f"{i}. {p}" for i, p in enumerate(reel, 1))
            + f"\n\n## Launch email\n**{d.get('email_subject', '')}**\n\n{d.get('email_body', '')}")
    path = _doc("launch", f"Launch kit — {b['name']}", body)
    return {"ok": True, "tagline": d.get("tagline", ""), "posts": len(posts), "queued": len(queued), "path": str(path),
            "text": body}


def offer_test(biz: str, offer: str, n: int = 3) -> dict:
    """Write a few versions of an offer to try against each other; responses are counted per version."""
    b = need(biz)
    raw = _write(f"Write {max(2, min(4, n))} clearly different versions of this offer for {b['name']} ({b['about']}): "
                 f"{offer}\nEach takes a different angle (e.g. saving money, saving time, status, fear of missing "
                 'out). Reply with JSON only: {"variants": [{"label": "one or two words", "text": "the offer, under 40 '
                 'words, with a call to action"}]}\nInvent no prices or numbers that are not in the offer.', "", 0.7)
    vs = [{"label": str(v.get("label", f"Version {i}"))[:30], "text": str(v.get("text", "")).strip(), "responses": 0}
          for i, v in enumerate(_json_from(raw).get("variants") or [], 1) if isinstance(v, dict) and v.get("text")][:4]
    if len(vs) < 2:
        vs = [{"label": "As written", "text": offer.strip(), "responses": 0},
              {"label": "With a deadline", "text": offer.strip().rstrip(".") + ". This week only.", "responses": 0}]
    oid = _x("INSERT INTO biz_offers(biz,ts,name,variants) VALUES(?,?,?,?)", (b["id"], now(), offer.strip()[:120],
                                                                             json.dumps(vs, ensure_ascii=False)))
    return {"id": oid, "name": offer.strip()[:120], "variants": vs}


def offer_response(biz: str, variant: str, count: int = 1) -> dict | None:
    """Count a response for one version of the running offer test. Returns the test with its tallies."""
    b = need(biz)
    rows = _q("SELECT * FROM biz_offers WHERE biz=? AND status='running' ORDER BY id DESC LIMIT 1", (b["id"],))
    if not rows:
        return None
    vs = json.loads(rows[0]["variants"])
    key = variant.strip().lower()
    m = re.search(r"\d+", key)
    idx = next((i for i, v in enumerate(vs) if v["label"].lower() == key or key in v["label"].lower()), None)
    if idx is None and m and 1 <= int(m.group(0)) <= len(vs):
        idx = int(m.group(0)) - 1
    if idx is None:
        return {"error": "no such version", "variants": vs}
    vs[idx]["responses"] += max(1, int(count or 1))
    _x("UPDATE biz_offers SET variants=? WHERE id=?", (json.dumps(vs, ensure_ascii=False), rows[0]["id"]))
    return {"name": rows[0]["name"], "variants": vs, "leader": max(vs, key=lambda v: v["responses"])["label"]}


# ── the weekly business review ────────────────────────────
def review(b: dict, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    week = (today - dt.timedelta(days=6)).isoformat()
    c = cockpit(b, today)
    k = c["kpi"]
    done = _q("SELECT title FROM biz_queue WHERE biz=? AND status='approved' AND done>=?", (b["id"], week))
    won = _q("SELECT name FROM biz_leads WHERE biz=? AND status IN ('replied','won') AND ts>=?", (b["id"], week))
    facts = [f"Sales this week: {money_text(b, k['sales_week'])}; this month: {money_text(b, k['sales_month'])}.",
             f"New enquiries this week: {k['leads_week']}; still open: {k['leads_open']}; replied to: {len(won)}.",
             f"Sent or done with your yes: {len(done)}; waiting for your yes now: {k['waiting']}.",
             f"Owed to you: {money_text(b, k['owed'])}, of which overdue: {money_text(b, k['overdue'])} "
             f"({k['overdue_n']} invoices). You owe: {money_text(b, k['i_owe'])}.",
             "Open tasks: " + ("; ".join(t["text"] for t in c["tasks"][:8]) or "none") + ".",
             "Follow-ups due: " + ("; ".join(f"{f['who']} ({f['due']})" for f in c["followups"][:6]) or "none") + "."]
    fallback = ("## What moved\n- " + facts[0] + "\n- " + facts[2] + "\n\n## What stalled\n- " + facts[1] + "\n- " + facts[3]
                + "\n\n## Do next\n" + ("\n".join(f"{i}. {t['text']}" for i, t in enumerate(c["tasks"][:3], 1))
                                         or "1. Clear the approval queue.\n2. Log this week's sales.\n3. Add the next task."))
    return _write(f"Write {owner()}'s weekly review of the business {b['name']} ({b['about']}) from these facts only. "
                  "Three sections with these exact headings:\n## What moved\n## What stalled\n## Do next\n'Do next' is "
                  "exactly three numbered, concrete actions for the coming week, the one that earns money soonest "
                  "first. Short bullet points, no jokes, nothing invented.\n\nFACTS:\n" + "\n".join(facts), fallback, 0.2)


def weekly() -> str:
    """The weekly review of every business — sent with Nova's weekly review."""
    if not cfg().get("enabled", True):
        return ""
    parts = []
    for b in businesses():
        parts.append(f"💼 {b['name']} — the week\n\n{review(b)}")
    return "\n\n".join(parts)


# ── the worker: enquiries, rules, follow-ups ──────────────
def tick() -> dict:
    """One round: read the inbox, carry out the standing instructions, draft what's due. Returns what happened."""
    out = {"leads": 0, "queued": 0, "sent": 0, "followups": 0}
    if not cfg().get("enabled", True) or not businesses():
        return out
    before = len(pending())
    if cfg().get("scan_inbox", True):
        try:
            out["leads"] = scan_inbox()
        except Exception as e:
            print(f"[business] inbox: {e}")
    r = run_rules()
    out.update(queued=r["queued"], sent=r["sent"], followups=due_followups())
    new = len(pending()) - before
    if new > 0:
        context.push(f"💼 {new} new thing{'' if new == 1 else 's'} waiting for your yes on the Business dashboard"
                     + (f" ({out['leads']} new enquir{'y' if out['leads'] == 1 else 'ies'})" if out["leads"] else "") + ".")
    return out


_started = False


def _worker() -> None:
    time.sleep(120)
    while True:
        try:
            tick()
        except Exception as e:
            print(f"[business] {e}")
        time.sleep(max(5, int(cfg().get("scan_minutes", 15))) * 60)


def start() -> None:
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_worker, daemon=True, name="business").start()


# ── the Business dashboard's buttons ──────────────────────
def state() -> dict:
    return {"businesses": [cockpit(b) for b in businesses()]}


def act(body: dict) -> dict:
    """One click on the Business dashboard. Returns {"ok", "message"}."""
    a, bid = str(body.get("action", "")), str(body.get("biz", ""))
    try:
        if a == "approve":
            r = approve(int(body["id"]), str(body.get("body", "")))
            return {"ok": r["ok"], "message": ("Approved — " + r["message"]) if r["ok"] else r["message"]}
        if a == "reject":
            return reject(int(body["id"]))
        if a == "setup":
            b = set_up(bid, body.get("email"), body.get("inbox"))
            return {"ok": True, "message": (f"Saved — I'll watch {b['email']} for {b['name']} enquiries." if b["email"]
                                            else "Saved. No enquiry address yet, so I'll look for mail that mentions "
                                                 f"{b['name']}.")}
        if a == "mailbox_connect":
            from . import mailbox
            b = need(bid)
            r = mailbox.connect(b["id"], str(body.get("email") or b.get("email") or ""), str(body.get("password") or ""),
                                str(body.get("user") or ""), str(body.get("imap_host") or ""), str(body.get("smtp_host") or ""))
            set_up(b["id"], email=mailbox.box(b["id"])["email"])
            return r
        if a == "mailbox_disconnect":
            from . import mailbox
            b = need(bid)
            mailbox.disconnect(b["id"])
            return {"ok": True, "message": f"Disconnected. The {b['name']} password is deleted from this PC."}
        if a == "scan":
            from . import mailbox
            n = scan_inbox(bid)
            r = run_rules()
            where = "the mailbox" if bid and mailbox.box(need(bid)["id"]) else "the inbox"
            return {"ok": True, "message": f"{n} new enquir{'y' if n == 1 else 'ies'}; {r['queued']} drafts waiting for your yes."
                    if n or r["queued"] else f"No new enquiries in {where}."}
        if a == "lead_add":
            lead = add_lead(bid, str(body.get("name", "")), str(body.get("contact", "")), str(body.get("message", "")))
            rule = next((r for r in rules(lead["biz"]) if r["trigger"] == "new_lead" and r["enabled"]), None)
            queue_reply(lead, rule["text"] if rule else "")
            return {"ok": True, "message": "Enquiry logged — a reply is drafted for your yes."}
        if a == "lead_status":
            if body.get("status") in ("won", "lost", "replied", "new"):
                _x("UPDATE biz_leads SET status=? WHERE id=?", (body["status"], int(body["id"])))
            return {"ok": True, "message": f"Marked as {body.get('status')}."}
        if a == "money_add":
            m = add_money(bid, str(body.get("kind", "sale")), str(body.get("party", "")), float(body.get("amount") or 0),
                          str(body.get("due", "")))
            return {"ok": True, "message": f"Recorded {money_text(need(bid), m['amount'])}."}
        if a == "paid":
            m = mark_paid(bid, f"#{int(body['id'])}")
            return {"ok": bool(m), "message": "Marked as paid." if m else "That entry isn't open any more."}
        if a == "task_add":
            add_task(bid, str(body.get("text", "")))
            return {"ok": True, "message": "Task added."}
        if a == "task_done":
            t = done_task(bid, f"#{int(body['id'])}")
            return {"ok": bool(t), "message": "Ticked off." if t else "That task isn't open any more."}
        if a == "followup_done":
            _x("UPDATE biz_followups SET status='done' WHERE id=?", (int(body["id"]),))
            return {"ok": True, "message": "Follow-up closed."}
        if a == "rule_toggle":
            set_rule(int(body["id"]), enabled=bool(body.get("enabled")))
            return {"ok": True, "message": "Standing instruction " + ("on." if body.get("enabled") else "paused.")}
        if a == "rule_delete":
            set_rule(int(body["id"]), delete=True)
            return {"ok": True, "message": "Standing instruction removed."}
        if a == "offer":
            r = offer_response(bid, str(body.get("version", "")))
            return {"ok": bool(r) and not (r or {}).get("error"), "message": "Counted." if r else "No offer test is running."}
    except (ValueError, KeyError, TypeError) as e:
        return {"ok": False, "message": str(e)[:1].upper() + str(e)[1:]}
    except Exception as e:
        from . import mailbox
        if isinstance(e, mailbox.MailError):
            return {"ok": False, "message": str(e)}
        raise
    return {"ok": False, "message": "I don't know that button."}
