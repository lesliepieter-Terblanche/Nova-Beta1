"""Business mode by voice: "how is TrueHome doing?", "new enquiry for TackleTrail from Sam…", "what's waiting for my
yes?", "validate this idea…", "make a launch kit for…". The engine is nova/business.py; the page is /business."""
from __future__ import annotations

from .. import business as biz
from .. import context
from ..tools import register_group, tool

WORDS = ["business", "businesses", "cockpit", "enquiry", "enquiries", "inquiry", "new lead", "leads", "made a sale",
         "sold a", "invoice", "owes me", "overdue", "mark as paid", "has paid", "approval", "approve number",
         "approve item", "approve the reply", "approve and send", "waiting for my yes", "standing instruction",
         "follow up with", "follow-up with", "launch kit", "launch plan", "business idea", "validate this idea",
         "go or no-go", "offer test", "test this offer", "business review", "make money", "website up", "website down",
         "site down", "website health", "broken links", "seo", "google check", "competitor", "competitors", "rival",
         "sales target", "monthly target", "on track", "plan posts", "week of posts", "content calendar",
         "customer list", "my customers", "repeat customers"]


def refresh_keywords() -> None:
    """A business's own name calls these tools up too ("how is Harbour Homes doing?")."""
    names = []
    try:
        for b in biz.businesses():
            names += [b["name"].lower(), b["id"].replace("-", " "), b["id"].replace("-", "")]
    except Exception:
        pass
    register_group("business", WORDS + sorted(set(n for n in names if len(n) >= 4)))


refresh_keywords()


def _err(e: Exception) -> str:
    return f"ERROR: {e}"


@tool(group="business")
def business_overview(business: str = "") -> str:
    """How a business is doing right now: sales this week, open enquiries, open tasks, money owed and what is
    waiting for the user's yes. With no name, every business.
    Args:
        business: the business name ("" = all of them)
    """
    all_ = biz.businesses()
    if not all_:
        return "No businesses are set up yet. Say 'add a business called …' with its website."
    if business:
        try:
            return biz.overview_text(biz.need(business))
        except ValueError as e:
            return _err(e)
    return "\n".join(biz.overview_text(b) for b in all_) + "\nThe full picture is on the Business dashboard."


@tool(group="business")
def add_business(name: str, website: str = "", about: str = "", enquiry_email: str = "") -> str:
    """Add a business for Nova to help run, or update one — e.g. set the email address its enquiries arrive at
    ("Harbour Homes enquiries go to hello@harbourhomes.example").
    Args:
        name: the business name
        website: its web address
        about: one or two sentences on what it sells and to whom
        enquiry_email: the email address its website forms and customers write to; Nova watches it for enquiries
    """
    if enquiry_email and not biz.EMAIL.fullmatch(enquiry_email.strip()):
        return f"ERROR: '{enquiry_email}' doesn't look like an email address. Nothing was changed."
    try:
        b = biz.find(name) if not (website or about) else None      # only setting the address: don't rename anything
        b = b or biz.add_business(name, website, about)
        if enquiry_email:
            b = biz.set_up(b["id"], email=enquiry_email)
            refresh_keywords()
            return f"Saved — I'll watch {b['email']} for {b['name']} enquiries."
    except ValueError as e:
        return _err(e)
    refresh_keywords()
    return f"{b['name']} is set up" + (f" ({b['url']})" if b["url"] else "") + ". It has its own tab on the Business dashboard."


@tool(group="business")
def validate_business_idea(idea: str, market: str = "South Africa") -> str:
    """Research a business idea — demand, competitors, pricing, risks — and give a one-page GO / NO-GO with the
    sources. Takes a minute.
    Args:
        idea: the idea, in a sentence or two
        market: where it would sell
    """
    r = biz.validate_idea(idea, market)
    context.attach(r["path"])
    return f"Verdict: {r['verdict']} (from {r['sources']} search results). Saved at {r['path']}.\n\n{r['text'][:1800]}"


@tool(group="business")
def launch_kit(business: str, brief: str = "") -> str:
    """Make a launch kit for a business: tagline and positioning, brand colours, a landing-page brief, a week of
    posts, a 20-second reel storyboard and a launch email. Everything sendable goes to the approval queue.
    Args:
        business: which business
        brief: what is being launched and to whom
    """
    try:
        r = biz.launch_kit(business, brief)
    except ValueError as e:
        return _err(e)
    if not r["ok"]:
        return "ERROR: " + r["message"]
    context.attach(r["path"])
    return (f"Launch kit ready — tagline: \"{r['tagline']}\". {r['posts']} posts, a reel storyboard, a landing-page "
            f"brief and a launch email. {r['queued']} pieces are waiting for your yes on the Business dashboard. "
            f"The whole kit is saved at {r['path']}.")


@tool(group="business")
def offer_test(business: str, offer: str) -> str:
    """Write a few different versions of an offer to try against each other. Responses are then counted per version
    with count_offer_response.
    Args:
        business: which business
        offer: the offer in plain words, with its price if it has one
    """
    try:
        r = biz.offer_test(business, offer)
    except ValueError as e:
        return _err(e)
    return ("Offer test started. Try each version, and tell me which one each response came from:\n"
            + "\n".join(f"{i}. {v['label']}: {v['text']}" for i, v in enumerate(r["variants"], 1)))


@tool(group="business")
def count_offer_response(business: str, version: str, count: int = 1) -> str:
    """Count a response (a reply, a sign-up, a sale) for one version of the running offer test, and say which leads.
    Args:
        business: which business
        version: the version's label or number
        count: how many responses
    """
    try:
        r = biz.offer_response(business, version, count)
    except ValueError as e:
        return _err(e)
    if r is None:
        return "There's no offer test running for that business. Start one with 'test this offer…'."
    if r.get("error"):
        return "ERROR: I don't know that version. The versions are: " + ", ".join(v["label"] for v in r["variants"])
    tally = ", ".join(f"{v['label']} {v['responses']}" for v in r["variants"])
    return f"Counted. So far: {tally}. \"{r['leader']}\" is ahead."


@tool(group="business")
def add_enquiry(business: str, name: str, message: str, contact: str = "") -> str:
    """Log a new enquiry or lead for a business. Nova scores it and drafts a reply for the approval queue.
    Args:
        business: which business
        name: who it is from
        message: what they asked or said
        contact: their email address or phone number
    """
    try:
        lead = biz.add_lead(business, name, contact, message)
        rule = next((r for r in biz.rules(lead["biz"]) if r["trigger"] == "new_lead" and r["enabled"]), None)
        qid = biz.queue_reply(lead, rule["text"] if rule else "")
    except ValueError as e:
        return _err(e)
    heat = {5: "hot", 4: "warm", 3: "worth a reply"}.get(lead["score"], "cool")
    return (f"Logged {name}'s enquiry ({heat}, {lead['score']} out of 5). A reply is drafted and waiting for your yes "
            f"— item {qid} in the approval queue.")


@tool(group="business", confirm=True)
def business_send_email(business: str, to: str, subject: str, body: str, in_reply_to: str = "") -> str:
    """Send an email from a business's own mailbox (the one connected on the Business dashboard), so it comes from
    the business's address. Use only when the user clearly says to send it.
    Args:
        business: which business
        to: the recipient's email address
        subject: the subject line
        body: the message, plain text
        in_reply_to: leave empty (Nova fills it in when answering an enquiry)
    """
    from .. import mailbox
    try:
        b = biz.need(business)
        return mailbox.send(b["id"], to, subject, body, in_reply_to, name=b["name"])
    except (ValueError, mailbox.MailError) as e:
        return _err(e)


@tool(group="business")
def check_enquiries(business: str = "") -> str:
    """Look in each business's mailbox (or Gmail) for new enquiries, log them and draft replies for approval.
    Args:
        business: which business ("" = all)
    """
    try:
        n = biz.scan_inbox(business)
    except ValueError as e:
        return _err(e)
    r = biz.run_rules()
    if not n:
        return "No new enquiries in the inbox." + (f" {len(biz.pending())} things are waiting for your yes." if biz.pending() else "")
    return f"{n} new enquir{'y' if n == 1 else 'ies'} logged; {r['queued']} replies drafted for your yes" + \
        (f", {r['sent']} sent under your standing instructions." if r["sent"] else ".")


@tool(group="business")
def record_money(business: str, kind: str, party: str, amount: float, due: str = "", note: str = "") -> str:
    """Record money for a business: a sale that came in, an invoice someone owes, or a bill to pay.
    Args:
        business: which business
        kind: sale (money received), owed_to_me (an invoice you sent) or i_owe (a bill you must pay)
        party: the customer or supplier
        amount: the amount, as a number
        due: when it is due, for invoices and bills (e.g. "Friday", "2026-10-20")
        note: anything to remember, e.g. their email address for reminders
    """
    try:
        m = biz.add_money(business, kind, party, amount, due, note)
        b = biz.need(business)
    except ValueError as e:
        return _err(e)
    what = {"sale": "Sale recorded", "owed_to_me": "Invoice recorded", "i_owe": "Bill recorded"}[m["kind"]]
    return f"{what}: {biz.money_text(b, m['amount'])} — {m['party']}" + (f", due {m['due']}" if m["due"] else "") + \
        f". {biz.overview_text(b)}"


@tool(group="business")
def mark_as_paid(business: str, party: str) -> str:
    """Mark an open invoice or bill as paid. A paid invoice is counted as a sale.
    Args:
        business: which business
        party: the customer or supplier (or the entry's number)
    """
    try:
        m = biz.mark_paid(business, party)
        b = biz.need(business)
    except ValueError as e:
        return _err(e)
    if not m:
        return f"ERROR: I found no open invoice or bill for {party}."
    return f"Marked as paid: {biz.money_text(b, m['amount'])} — {m['party']}."


@tool(group="business")
def business_task(business: str, task: str, done: bool = False, due: str = "") -> str:
    """Add a task for a business, or tick one off.
    Args:
        business: which business
        task: the task (to tick off: its number or a few words from it)
        done: true to mark it done
        due: optional due date for a new task
    """
    try:
        if done:
            t = biz.done_task(business, task)
            return f"Ticked off: {t['text']}" if t else f"ERROR: I found no open task like '{task}'."
        t = biz.add_task(business, task, due)
    except ValueError as e:
        return _err(e)
    return f"Task added for {biz.need(business)['name']}: {t['text']}" + (f" (due {t['due']})" if t["due"] else "") + "."


@tool(group="business")
def approval_queue(business: str = "") -> str:
    """What is waiting for the user's yes: replies, emails, follow-ups, payment reminders, posts and pages Nova has
    prepared for the businesses.
    Args:
        business: which business ("" = all)
    """
    try:
        rows = biz.pending(biz.need(business)["id"] if business else "")
    except ValueError as e:
        return _err(e)
    if not rows:
        return "Nothing is waiting for your yes."
    names = {b["id"]: b["name"] for b in biz.businesses()}
    return (f"{len(rows)} waiting for your yes:\n" + "\n".join(
        f"#{r['id']} [{names.get(r['biz'], r['biz'])}] {r['title']} — {' '.join(r['body'].split())[:110]}" for r in rows[:12])
        + "\nSay 'approve number N', 'drop number N', or open the Business dashboard.")


@tool(group="business", confirm=True)
def approve_and_send(item: str = "", edited_text: str = "") -> str:
    """Approve an item from the approval queue: Nova then sends or does it (an email, a reply, a page…). Use only when
    the user clearly says to approve it.
    Args:
        item: the item's number, or words from its title (empty = the latest)
        edited_text: the user's changed wording, if they want it changed before it goes
    """
    it = biz.queue_item(item)
    if not it:
        return "ERROR: I found no such item waiting. Ask 'what's waiting for my yes?'"
    r = biz.approve(it["id"], edited_text)
    return (f"Done — {it['title']}: {r['message']}" if r["ok"] else "ERROR: " + r["message"])


@tool(group="business")
def drop_queue_item(item: str = "") -> str:
    """Drop an item from the approval queue without sending it.
    Args:
        item: the item's number, or words from its title (empty = the latest)
    """
    it = biz.queue_item(item)
    if not it:
        return "ERROR: I found no such item waiting."
    biz.reject(it["id"])
    return f"Dropped: {it['title']}. Nothing was sent."


@tool(group="business")
def standing_instruction(business: str, when: str, instruction: str, after_days: int = 0,
                         send_without_asking: bool = False, max_per_day: int = 5) -> str:
    """Give Nova a standing instruction for a business that she carries out by herself, e.g. "answer every new enquiry
    with the price list", "chase unpaid invoices after 7 days", "every week draft three post ideas".
    Args:
        business: which business
        when: new_lead, invoice_overdue, daily or weekly
        instruction: what to do, in plain words
        after_days: for invoice_overdue: how many days late before chasing
        send_without_asking: true = send emails by herself (only for new_lead and invoice_overdue); false = put a draft in the approval queue
        max_per_day: the most she may send by herself in one day
    """
    try:
        r = biz.add_rule(business, when, instruction, after_days, send_without_asking, max_per_day)
    except ValueError as e:
        return _err(e)
    return f"Standing instruction #{r['id']} saved — {biz.describe_rule(r)}. Switch it off any time on the Business dashboard."


@tool(group="business")
def follow_up(business: str, who: str, about: str, when: str = "in 3 days", contact: str = "") -> str:
    """Remember to follow up with someone. When it is due Nova drafts the message for the approval queue.
    Args:
        business: which business
        who: the person or company
        about: what to follow up on
        when: when, e.g. "Friday", "in 3 days"
        contact: their email address, so the follow-up can be sent on approval
    """
    try:
        f = biz.add_followup(business, who, about, when, contact)
    except ValueError as e:
        return _err(e)
    return f"I'll draft a follow-up to {f['who']} about {f['what']} on {f['due']}."


@tool(group="business")
def business_review(business: str = "") -> str:
    """The weekly review of a business: what moved, what stalled, and the three things to do next.
    Args:
        business: which business ("" = all)
    """
    try:
        which = [biz.need(business)] if business else biz.businesses()
    except ValueError as e:
        return _err(e)
    if not which:
        return "No businesses are set up yet."
    return "\n\n".join(f"{b['name']}\n{biz.review(b)}" for b in which)


# ── v2.38–2.41: websites, targets, customers, posts ──────────────────────
@tool(group="business")
def website_health(business: str = "") -> str:
    """Is a business's website up, how fast is it, any broken links, how does it score with Google.
    Args:
        business: which business ("" = all)
    """
    from .. import sitewatch
    try:
        which = [biz.need(business)] if business else biz.businesses()
    except ValueError as e:
        return _err(e)
    out = []
    for b in which:
        row = sitewatch.check(b, deep=bool(business))
        seo = sitewatch.seo(b["id"])
        out.append(f"{b['name']}: " + (f"up, answered in {row['ms']} ms" if row["up"] else f"DOWN — {row['error']}")
                   + (f", security certificate has {row['ssl_days']} days left" if row["ssl_days"] is not None and row["ssl_days"] <= 30 else "")
                   + (f", {len(row['broken'])} broken links" if row["broken"] else "")
                   + (f"; Google check {seo['score']} out of 100" if seo else "") + ".")
    return "\n".join(out) or "No businesses are set up yet."


@tool(group="business")
def google_check(business: str) -> str:
    """Check how a business's website looks to Google (SEO): a score out of 100 and what to fix. Takes a minute.
    Args:
        business: which business
    """
    from .. import sitewatch
    try:
        b = biz.need(business)
        res = biz.seo_now(b)
    except ValueError as e:
        return _err(e)
    except Exception as e:
        return f"ERROR: I couldn't read the site — {sitewatch._plain(e)}."
    top = "; ".join(f"{i['page']} {i['text']}" for i in res["issues"][:3])
    return f"{b['name']} scores {res['score']} out of 100 ({res['pages']} pages read)." + \
        (f" Biggest problems: {top}. The full list with fixes is in the approval queue." if top else " Nothing to fix.")


@tool(group="business")
def watch_competitor(business: str, website: str, name: str = "", stop: bool = False) -> str:
    """Watch a competitor's web page for a business (or stop watching): once a week Nova says what changed on it.
    Args:
        business: which business
        website: the competitor page's web address
        name: what to call the competitor
        stop: true to stop watching this one
    """
    from .. import sitewatch
    try:
        b = biz.need(business)
        if stop:
            hit = next((r for r in sitewatch.rivals(b["id"]) if website.lower().strip("/") in r["url"].lower()
                        or (name and name.lower() in r["name"].lower())), None)
            if not hit:
                return "ERROR: I'm not watching that one."
            sitewatch.drop_rival(hit["id"])
            return f"Stopped watching {hit['name']}."
        r = sitewatch.add_rival(b["id"], name, website)
        sitewatch.check_rival(r)
    except ValueError as e:
        return _err(e)
    return f"Watching {r['name']} for {b['name']} — I'll tell you what changes, once a week."


@tool(group="business")
def competitor_changes(business: str = "") -> str:
    """Look at the watched competitor pages now and say what changed since the last look.
    Args:
        business: which business ("" = all)
    """
    from .. import sitewatch
    try:
        bid = biz.need(business)["id"] if business else ""
    except ValueError as e:
        return _err(e)
    if not any(sitewatch.rivals(b["id"]) for b in biz.businesses() if not bid or b["id"] == bid):
        return "No competitors are being watched yet. Say 'watch <web address> for <business>'."
    moved = sitewatch.check_rivals(bid, force=True)
    return "\n".join(moved) if moved else "Nothing changed on the competitor pages since the last look."


@tool(group="business")
def sales_target(business: str, monthly_amount: float = -1) -> str:
    """Set a business's monthly sales target, or (without an amount) say how the month is going against it.
    Args:
        business: which business
        monthly_amount: the target in rand per month (0 removes it; leave out to hear the progress)
    """
    from .. import bizplan
    try:
        b = biz.need(business)
        if monthly_amount >= 0:
            b = bizplan.set_target(b["id"], monthly_amount)
            return f"Target for {b['name']}: {biz.money_text(b, b['target'])} a month." if b["target"] else f"{b['name']} has no target now."
    except ValueError as e:
        return _err(e)
    t = bizplan.trend(b)
    if not t["target"]:
        return f"{b['name']} has no target. Sales this month: {biz.money_text(b, t['month'])}."
    return (f"{b['name']}: {biz.money_text(b, t['month'])} of {biz.money_text(b, t['target'])} ({t['pct']}%) on day {t['day']} of "
            f"{t['days']}. At this pace the month ends on {biz.money_text(b, t['pace'])} — "
            + ("on track." if t["on_track"] else f"{biz.money_text(b, t['target'] - t['pace'])} short."))


@tool(group="business")
def plan_posts(business: str, focus: str = "") -> str:
    """Draft a week of social media posts for a business (to copy and post yourself). They appear on its tab of the
    Business dashboard.
    Args:
        business: which business
        focus: what this week is about, if anything special (a launch, an offer)
    """
    from .. import bizplan
    try:
        b = biz.need(business)
    except ValueError as e:
        return _err(e)
    rows = [p for p in bizplan.plan_week(b, brief=focus) if p["status"] == "planned"]
    return f"{len(rows)} posts drafted for {b['name']}:\n" + "\n".join(
        f"• {p['day']} {p['platform']}: {' '.join(p['text'].split())[:90]}" for p in rows)


@tool(group="business")
def customer_list(business: str) -> str:
    """Who a business's customers are: how many, who bought more than once, who has gone quiet.
    Args:
        business: which business
    """
    from .. import bizplan
    try:
        b = biz.need(business)
    except ValueError as e:
        return _err(e)
    people = bizplan.customers(b)
    if not people:
        return f"No customers on file for {b['name']} yet — they appear as enquiries and sales come in."
    quiet = [p for p in people if (p["orders"] or p["won"]) and p["quiet_days"] >= int(biz.cfg().get("quiet_days", 60))]
    best = sorted(people, key=lambda p: -p["spent"])[:5]
    return (f"{b['name']}: {len(people)} people on file, {sum(p['repeat'] for p in people)} bought more than once, "
            f"{len(quiet)} gone quiet.\nBiggest: " + "; ".join(f"{p['name']} ({biz.money_text(b, p['spent'])}, {p['orders']} orders)"
                                                               for p in best if p["spent"]))
