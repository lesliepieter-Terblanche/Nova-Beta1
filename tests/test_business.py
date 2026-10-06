"""v2.36: business mode — cockpit, approval queue, leads, money, follow-ups, standing instructions, creating tools."""
import datetime as dt
import json

import pytest

from nova import business as biz
from nova import context
from nova.tools import REGISTRY, Tool


@pytest.fixture()
def shop(nova, monkeypatch):
    """Two made-up businesses, a fake Gmail, and a model that writes whatever is asked."""
    cfg, tmp = nova
    cfg["assistant"]["owner"] = "Alex"
    monkeypatch.setattr(context, "llm", None)                 # no model unless a test brings one: the plain wording
    monkeypatch.setattr(biz, "resolve", lambda p: tmp / p)
    sent, inbox = [], []

    def fake(name, fn):
        monkeypatch.setitem(REGISTRY, name, Tool(name, name, fn, {"type": "object", "properties": {}}, "google", True))
    fake("gmail_send", lambda to, subject, body: sent.append(("send", to, subject, body)) or f"Email sent to {to}.")
    fake("gmail_reply", lambda message_id, body: sent.append(("reply", message_id, body)) or "Reply sent.")
    fake("gmail_draft", lambda to, subject, body: sent.append(("draft", to, subject, body)) or "Draft saved (id d1).")
    fake("gmail_search", lambda query="", max_results=5: list(inbox))
    cfg["business"] = {"health": False, "seo_weekly": False, "rivals": False, "calendar": False, "win_back": False}
    h = biz.add_business("Harbour Homes", "www.harbourhomes.example", "Property sales and rentals on the coast.")
    r = biz.add_business("Riverbend", "https://riverbend.example", "A fishing app with a gear shop.")
    assert h["url"] == "https://www.harbourhomes.example" and r["id"] == "riverbend"
    return {"sent": sent, "inbox": inbox, "tmp": tmp, "cfg": cfg}


def test_businesses_are_found_by_how_you_say_them(shop):
    assert biz.find("harbour homes")["id"] == "harbour-homes" and biz.find("Harbour")["id"] == "harbour-homes"
    assert biz.find("harbourhomes")["id"] == "harbour-homes" and biz.find("riverbend.example")["id"] == "riverbend"
    assert biz.find("") is None and biz.find("orchard") is None                 # two businesses: say which
    with pytest.raises(ValueError, match="which business"):
        biz.need("")
    biz.add_business("Harbour Homes", about="Coastal property, sales only.")     # updating keeps the rest
    b = biz.find("harbour")
    assert b["about"] == "Coastal property, sales only." and b["url"].endswith("harbourhomes.example")
    assert len(biz.businesses()) == 2


def test_an_enquiry_is_scored_drafted_and_only_sent_on_your_yes(shop):
    from nova.skills.business import add_enquiry, approval_queue, approve_and_send
    assert biz.score_lead("hi") == 1
    assert biz.score_lead("How much is the listing package? Please call me on 082 555 0100 today") == 5
    out = add_enquiry("harbour", "Sam Carter", "What does it cost to list my house? I'd like to sign up.", "sam@example.com")
    assert "Logged Sam Carter's enquiry" in out and "waiting for your yes" in out
    [q] = biz.pending("harbour-homes")
    assert q["kind"] == "reply" and q["tool"] == "gmail_send" and json.loads(q["args"])["to"] == "sam@example.com"
    assert "Harbour Homes" in q["body"] and not shop["sent"]                     # drafted, nothing sent
    assert biz.cockpit(biz.find("harbour"))["leads"][0]["status"] == "drafted"
    assert f"#{q['id']} [Harbour Homes] Reply to Sam Carter" in approval_queue()
    from nova.agent import needs_yes
    assert needs_yes(REGISTRY["approve_and_send"], {}) and not needs_yes(REGISTRY["approval_queue"], {})
    out = approve_and_send("sam", "Hi Sam — the price list is attached. Shall I call you tomorrow?")
    assert out.startswith("Done — Reply to Sam Carter: Email sent to sam@example.com")
    assert shop["sent"] == [("send", "sam@example.com", "Your enquiry — Harbour Homes",
                             "Hi Sam — the price list is attached. Shall I call you tomorrow?")]     # your wording went out
    assert not biz.pending() and biz.cockpit(biz.find("harbour"))["leads"][0]["status"] == "replied"
    assert "ERROR" in approve_and_send("sam")                                   # nothing left to approve twice


def test_dropping_a_draft_sends_nothing_and_keeps_the_lead(shop):
    from nova.skills.business import drop_queue_item
    lead = biz.add_lead("riverbend", "Jo", "0825550100", "Do you ship rods to Durban?")
    qid = biz.queue_reply(lead)
    q = biz.queue_item(str(qid))
    assert q["tool"] == "" and "0825550100" in q["title"]                       # no email address: yours to send
    assert "Nothing was sent" in drop_queue_item(f"#{qid}")
    assert not shop["sent"] and not biz.pending()
    assert biz.cockpit(biz.find("riverbend"))["leads"][0]["status"] == "new"
    assert biz.run_rules() == {"queued": 0, "sent": 0}                           # …and it isn't drafted all over again


def test_gmail_enquiries_are_logged_once_and_replied_in_thread(shop):
    shop["inbox"] += [{"id": "m1", "from": "Pat Lee <pat@example.com>", "subject": "Riverbend order", "snippet": "Is the reel in stock?"}]
    biz.add_rule("riverbend", "new lead", "Mention free delivery over R500.")
    assert biz.scan_inbox("riverbend") == 1 and biz.scan_inbox("riverbend") == 0          # not twice
    assert biz.run_rules() == {"queued": 1, "sent": 0}
    [q] = biz.pending("riverbend")
    assert q["tool"] == "gmail_reply" and json.loads(q["args"])["message_id"] == "m1"
    assert biz.act({"action": "approve", "id": q["id"], "body": "Yes, it is — free delivery over R500."})["ok"]
    assert shop["sent"] == [("reply", "m1", "Yes, it is — free delivery over R500.")]


def test_standing_instruction_may_send_by_itself_but_only_within_its_limit(shop):
    r = biz.add_rule("riverbend", "new_lead", "Send the price list.", auto=True, max_per_day=2)
    assert "sends by herself (max 2 a day)" in biz.describe_rule(r)
    for i in range(4):
        biz.add_lead("riverbend", f"Lead {i}", f"lead{i}@example.com", "How much is the starter kit?")
    assert biz.run_rules() == {"queued": 2, "sent": 2}                           # two sent, the rest wait for you
    assert len(shop["sent"]) == 2 and len(biz.pending("riverbend")) == 2
    assert biz.add_rule("riverbend", "weekly", "Draft three post ideas.", auto=True)["auto"] == 0     # never auto for those
    with pytest.raises(ValueError, match="when must be"):
        biz.add_rule("riverbend", "at full moon", "howl")


def test_money_watch_and_chasing_late_invoices(shop):
    from nova.skills.business import mark_as_paid, record_money
    today = dt.date.today()
    assert "Sale recorded: R1 500" in record_money("harbour", "sale", "Listing fee — Carter", 1500)
    late = (today - dt.timedelta(days=10)).isoformat()
    biz.add_money("harbour", "invoice", "Coastline Agency", 4200, late, "accounts@coastline.example")
    biz.add_money("harbour", "owed_to_me", "Dune Realty", 900, (today + dt.timedelta(days=5)).isoformat())
    biz.add_money("harbour", "bill", "Hosting", 350)
    k = biz.cockpit(biz.find("harbour"))["kpi"]
    assert (k["sales_week"], k["owed"], k["overdue"], k["overdue_n"], k["i_owe"]) == (1500, 5100, 4200, 1, 350)
    with pytest.raises(ValueError):
        biz.add_money("harbour", "gift", "x", 5)
    biz.add_rule("harbour", "invoice_overdue", "Polite and firm.", after_days=7)
    assert biz.run_rules(today) == {"queued": 1, "sent": 0}
    [q] = biz.pending("harbour-homes")
    assert q["kind"] == "chase" and "Coastline Agency for R4 200 (10 days late)" in q["title"]
    assert json.loads(q["args"])["to"] == "accounts@coastline.example" and "R4 200" in q["body"]
    assert biz.run_rules(today) == {"queued": 0, "sent": 0}                      # one reminder, not one every round
    assert "Marked as paid: R4 200" in mark_as_paid("harbour", "coastline")
    k = biz.cockpit(biz.find("harbour"))["kpi"]
    assert k["owed"] == 900 and k["overdue"] == 0 and k["sales_week"] == 5700    # a paid invoice is a sale
    assert "ERROR" in mark_as_paid("harbour", "nobody")


def test_follow_ups_and_tasks(shop):
    from nova.skills.business import business_task, follow_up
    assert "I'll draft a follow-up to Dana" in follow_up("riverbend", "Dana", "the bulk order quote", "2026-01-05", "dana@example.com")
    biz.add_followup("riverbend", "Eli", "the sponsorship", "2099-01-01")
    assert biz.due_followups() == 1 and biz.due_followups() == 0
    [q] = biz.pending("riverbend")
    assert q["kind"] == "follow-up" and "Dana" in q["title"] and q["tool"] == "gmail_send"
    biz.approve(q["id"])
    assert shop["sent"][0][1] == "dana@example.com"
    assert [f["who"] for f in biz.cockpit(biz.find("riverbend"))["followups"]] == ["Eli"]
    assert "Task added for Riverbend: Photograph the new rods" in business_task("riverbend", "Photograph the new rods")
    assert "Ticked off: Photograph the new rods" in business_task("riverbend", "the new rods", done=True)
    assert "ERROR" in business_task("riverbend", "walk the dog", done=True)
    assert biz.cockpit(biz.find("riverbend"))["kpi"]["tasks_open"] == 0


class Model:
    """Answers with whatever each prompt asks for."""
    def __init__(self):
        self.prompts = []

    def complete(self, prompt, system="", prefer_smart=True, temperature=0.5):
        self.prompts.append(prompt)
        if "launch kit" in prompt:
            return json.dumps({"tagline": "Find your place", "audience": "Coastal buyers", "promise": "Listings that are real",
                               "colour": "#101a3a", "accent": "#e8b84b", "landing_page": "One page: hero, three benefits, sign-up form.",
                               "posts": [f"Post {i}" for i in range(1, 8)], "reel": ["Wide shot", "Close-up"],
                               "email_subject": "We are live", "email_body": "Harbour Homes is open."})
        if "different versions of this offer" in prompt:
            return json.dumps({"variants": [{"label": "Save money", "text": "List for less."},
                                            {"label": "Save time", "text": "List in five minutes."}]})
        if "business idea" in prompt:
            return "## Verdict\nGO WITH CHANGES — demand is there, pricing is unclear.\n## Demand\n- found (https://x.example)"
        if "weekly review" in prompt:
            return "## What moved\n- Sales.\n## What stalled\n- Nothing.\n## Do next\n1. A\n2. B\n3. C"
        return "Hi there,\n\nThanks for your message — I'll confirm the details.\n\nAlex, Harbour Homes"


def test_launch_kit_puts_every_sendable_piece_in_the_queue(shop, monkeypatch):
    from nova import cinema
    from nova.skills.business import launch_kit
    shop["cfg"]["media"]["brands_file"] = str(shop["tmp"] / "brands.json")
    monkeypatch.setattr(context, "llm", Model())
    built = []
    monkeypatch.setitem(REGISTRY, "build_website", Tool("build_website", "", lambda name, description: built.append((name, description)) or "Built.",
                                                         {"type": "object", "properties": {}}, "web"))
    out = launch_kit("harbour", "Launch to coastal estate agents")
    assert 'tagline: "Find your place"' in out and "7 posts" in out and "9 pieces are waiting for your yes" in out
    kinds = [q["kind"] for q in biz.pending("harbour-homes")]
    assert kinds == ["page"] + ["post"] * 7 + ["email"] and not built and not shop["sent"]        # nothing done yet
    assert cinema.brand("Harbour Homes")["accent"] == "#e8b84b"                                   # the reels' brand kit
    page = biz.pending("harbour-homes")[0]
    assert biz.approve(page["id"])["ok"] and built == [("harbour-homes-launch", "One page: hero, three benefits, sign-up form.")]
    post = biz.pending("harbour-homes")[0]
    assert biz.approve(post["id"])["message"] == "Marked as done."                                # a post is yours to post
    doc = next((shop["tmp"] / "workspace" / "business" / "launch").glob("*.md")).read_text()
    assert "## First week of posts" in doc and "7. Post 7" in doc


def test_idea_validator_offer_test_and_weekly_review(shop, monkeypatch):
    from nova.skills.business import business_review, count_offer_response, offer_test, validate_business_idea
    m = Model()
    monkeypatch.setattr(context, "llm", m)
    monkeypatch.setattr(biz, "_search", lambda q, n=4: [{"title": "Market report", "snippet": "growing 8%", "url": "https://x.example"}])
    out = validate_business_idea("a marketplace for second-hand fishing gear")
    assert out.startswith("Verdict: GO WITH CHANGES (from 3 search results)")
    assert "growing 8%" in m.prompts[-1] and "not found — needs checking" in m.prompts[-1]
    assert "Save money" in offer_test("riverbend", "Starter kit R499") and "2. Save time" in offer_test("harbour", "List free in October")
    assert 'Save money 0, Save time 2. "Save time" is ahead.' in count_offer_response("riverbend", "2", 2)
    assert "ERROR" in count_offer_response("riverbend", "nine")
    biz.add_money("riverbend", "sale", "Starter kit", 499)
    text = business_review("riverbend")
    assert text.startswith("Riverbend\n## What moved") and "Sales this week: R499" in m.prompts[-1]
    assert biz.weekly().count("💼") == 2
    shop["cfg"]["business"] = {"enabled": False}
    assert biz.weekly() == "" and biz.tick() == {"leads": 0, "queued": 0, "sent": 0, "followups": 0, "sales": 0, "alerts": 0}


def test_without_a_model_everything_still_works_plainly(shop, monkeypatch):
    class Down:
        def complete(self, *a, **k):
            raise RuntimeError("all models failed")
    monkeypatch.setattr(context, "llm", Down())
    lead = biz.add_lead("harbour", "Sam Carter", "sam@example.com", "Price list please")
    biz.queue_reply(lead)
    assert "Hi Sam," in biz.pending()[0]["body"] and "Kind regards,\nAlex" in biz.pending()[0]["body"]
    assert biz.offer_test("harbour", "List free in October.")["variants"][1]["text"] == "List free in October. This week only."
    assert "## Do next" in biz.review(biz.find("harbour"))
    assert biz.launch_kit("harbour")["ok"] is False


def test_the_round_tells_you_once_and_the_dashboard_shows_it(shop, monkeypatch):
    from nova.dashboard.server import Dashboard
    from nova.taxonomy import bottlenecks
    told = []
    monkeypatch.setattr(context, "push", lambda text, files=None: told.append(text))
    biz.add_rule("harbour", "new_lead", "Be brief.")
    shop["inbox"] += [{"id": "m9", "from": "kim@example.com", "subject": "Harbour Homes listing", "snippet": "How much to list?"}]
    assert biz.tick() == {"leads": 1, "queued": 1, "sent": 0, "followups": 0, "sales": 0, "alerts": 0}
    assert told == ["💼 1 new thing waiting for your yes on the Business dashboard (1 new enquiry)."]
    assert biz.tick()["queued"] == 0 and len(told) == 1
    assert [b for b in bottlenecks() if b["id"] == "business"][0]["text"] == "Harbour Homes: Reply to kim"
    st = biz.state()
    assert [b["name"] for b in st["businesses"]] == ["Harbour Homes", "Riverbend"] and st["businesses"][0]["kpi"]["waiting"] == 1
    assert json.loads(json.dumps(st))                                           # the page gets plain JSON
    qid = st["businesses"][0]["queue"][0]["id"]
    assert biz.act({"action": "reject", "id": qid})["ok"] and biz.act({"action": "reject", "id": qid})["ok"] is False
    assert biz.act({"biz": "riverbend", "action": "money_add", "kind": "sale", "party": "Rod", "amount": 800})["message"] == "Recorded R800."
    assert biz.act({"biz": "riverbend", "action": "task_add", "text": "Restock reels"})["ok"]
    tid = biz.cockpit(biz.find("riverbend"))["tasks"][0]["id"]
    assert biz.act({"biz": "riverbend", "action": "task_done", "id": tid})["message"] == "Ticked off."
    assert biz.act({"biz": "nowhere", "action": "money_add", "amount": 5})["ok"] is False
    assert biz.act({"action": "fly"})["message"] == "I don't know that button."
    assert Dashboard is not None


def test_business_names_call_up_the_tools(shop):
    from nova.skills import business as skill
    from nova.tools import select_tools
    skill.refresh_keywords()
    assert "business_overview" in {t.name for t in select_tools("how is Harbour Homes doing?")}
    assert "add_enquiry" in {t.name for t in select_tools("new enquiry for riverbend from Sam")}
    assert "business_overview" not in {t.name for t in select_tools("what's the weather tomorrow")}
    out = REGISTRY["business_overview"].run({})
    assert "Harbour Homes: R0 in sales this week" in out and "Riverbend:" in out
    assert REGISTRY["business_overview"].run({"business": "orchard"}).startswith("ERROR: which business? I have: Harbour Homes, Riverbend")


def test_business_dashboard_is_served_and_linked_from_the_main_one(shop):
    import threading
    import time
    import urllib.request

    from nova.dashboard.server import Dashboard
    shop["cfg"]["dashboard"]["port"] = 8794
    biz.add_task("riverbend", "Restock reels")
    threading.Thread(target=Dashboard(shop["cfg"], None).start, daemon=True).start()
    time.sleep(1.2)

    def get(path, data=None):
        req = urllib.request.Request(f"http://127.0.0.1:8794{path}", data=json.dumps(data).encode() if data else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.read().decode()
    assert "Nova · Business" in get("/business") and 'href="/"' in get("/business")
    assert 'href="/business"' in get("/")                                        # the 💼 button on the main dashboard
    st = json.loads(get("/api/business"))
    assert [b["name"] for b in st["businesses"]] == ["Harbour Homes", "Riverbend"]
    tid = st["businesses"][1]["tasks"][0]["id"]
    assert json.loads(get("/api/business/act", {"biz": "riverbend", "action": "task_done", "id": tid}))["message"] == "Ticked off."
    assert "## Do next" in json.loads(get("/api/business/review?biz=riverbend"))["text"]


def test_a_place_for_each_business_enquiry_address(shop):
    """v2.36.1: set where enquiries arrive (page or voice); until then Nova looks for mail that mentions the name."""
    from nova.skills.business import add_business
    seen = []
    REGISTRY["gmail_search"].func = lambda query="", max_results=5: seen.append(query) or []
    h = biz.cockpit(biz.find("harbour"))
    assert h["email"] == "" and h["gmail"] is False and '"Harbour Homes" OR harbourhomes.example' in h["watching"]
    assert biz.act({"biz": "harbour-homes", "action": "setup", "email": "not an address"}) == \
        {"ok": False, "message": "'not an address' doesn't look like an email address"}
    r = biz.act({"biz": "harbour-homes", "action": "setup", "email": " Hello@HarbourHomes.example ", "inbox": ""})
    assert r == {"ok": True, "message": "Saved — I'll watch hello@harbourhomes.example for Harbour Homes enquiries."}
    assert add_business("Riverbend", enquiry_email="shop@riverbend.example") == \
        "Saved — I'll watch shop@riverbend.example for Riverbend enquiries."
    assert biz.find("riverbend")["about"] == "A fishing app with a gear shop."            # nothing else was touched
    biz.scan_inbox()
    assert seen == ["is:unread newer_than:14d (to:hello@harbourhomes.example OR deliveredto:hello@harbourhomes.example OR cc:hello@harbourhomes.example)",
                    "is:unread newer_than:14d (to:shop@riverbend.example OR deliveredto:shop@riverbend.example OR cc:shop@riverbend.example)"]
    biz.act({"biz": "riverbend", "action": "setup", "email": "shop@riverbend.example", "inbox": "label:website-forms is:unread"})
    assert biz.cockpit(biz.find("riverbend"))["watching"] == "label:website-forms is:unread"     # your own search wins
    assert "No enquiry address yet" in biz.act({"biz": "harbour-homes", "action": "setup", "email": "", "inbox": ""})["message"]
    assert "ERROR" in add_business("Orchard", enquiry_email="nope") and biz.find("orchard") is None
    (shop["tmp"] / "token.json").write_text("{}")                                # Google gets connected
    shop["cfg"]["google"]["token_file"] = "token.json"
    assert biz.cockpit(biz.find("harbour"))["gmail"] is True


# ── v2.37: a business's own mailbox, signed in to directly ─────────────────
class FakeMail:
    """One pretend mail host: an IMAP inbox and an SMTP server that accept one username and password."""

    def __init__(self, host="mail.harbourhomes.example", user="hello@harbourhomes.example", pw="s3cret"):
        self.host, self.user, self.pw = host, user, pw
        self.inbox, self.sent, self.filed, self.smtp_ports = [], [], [], (465,)

    def add(self, uid, raw, flags="", when="06-Oct-2026 09:00:00 +0200"):
        self.inbox.append((uid, flags, when, raw.replace("\n", "\r\n").encode()))

    def imap(self, host, port=993, ssl_context=None, timeout=None):
        if host != self.host:
            raise OSError("no such host")
        outer = self

        class C:
            def login(self, user, pw):
                if (user, pw) != (outer.user, outer.pw):
                    raise __import__("imaplib").IMAP4.error("AUTHENTICATIONFAILED")

            def select(self, box, readonly=False):
                assert readonly                                        # nothing is ever marked as read
                return "OK", [b"1"]

            def uid(self, cmd, *a):
                if cmd == "search":
                    return "OK", [b" ".join(str(u).encode() for u, *_ in outer.inbox)]
                want = a[0].decode().split(",") if isinstance(a[0], bytes) else [a[0]]
                out = []
                for u, flags, when, raw in outer.inbox:
                    if str(u) not in want:
                        continue
                    part = raw.split(b"\r\n\r\n")[0] + b"\r\n\r\n" if "HEADER" in a[1] else raw
                    out += [(f'{u} (UID {u} FLAGS ({flags}) INTERNALDATE "{when}" BODY[] {{{len(part)}}}'.encode(), part), b")"]
                return "OK", out

            def list(self):
                return "OK", [b'(\\HasNoChildren) "." "INBOX"', b'(\\HasNoChildren \\Sent) "." "INBOX.Sent"']

            def append(self, folder, flags, when, raw):
                outer.filed.append((folder, raw))

            def logout(self):
                pass

            def shutdown(self):
                pass
        return C()

    def smtp(self, host, port=465, timeout=None, context=None):
        if host != self.host or port not in self.smtp_ports:
            raise OSError("connection refused")
        outer = self

        class S:
            def starttls(self, context=None):
                pass

            def login(self, user, pw):
                if (user, pw) != (outer.user, outer.pw):
                    raise __import__("smtplib").SMTPAuthenticationError(535, b"no")

            def send_message(self, msg):
                outer.sent.append(msg)

            def quit(self):
                pass

            def close(self):
                pass
        return S()


@pytest.fixture()
def post(shop, monkeypatch):
    from nova import mailbox, settings
    monkeypatch.setattr(settings, "ENV", shop["tmp"] / ".env")
    monkeypatch.setattr(settings, "ENV_TEMPLATE", shop["tmp"] / "none")
    monkeypatch.delenv("BIZ_MAIL_HARBOUR_HOMES", raising=False)
    monkeypatch.setattr(mailbox, "mx_hosts", lambda domain: [])
    host = FakeMail()
    monkeypatch.setattr(mailbox.imaplib, "IMAP4_SSL", host.imap)
    monkeypatch.setattr(mailbox.smtplib, "SMTP_SSL", host.smtp)
    monkeypatch.setattr(mailbox.smtplib, "SMTP", host.smtp)
    return host


ENQUIRY = """From: Sam Carter <sam@example.com>
To: hello@harbourhomes.example
Subject: Listing my house
Message-ID: <abc123@example.com>
Content-Type: text/plain; charset=utf-8

Hi, how much does it cost to list my house? Please call me on 082 555 0100.
"""
NEWSLETTER = """From: Deals <news@shop.example>
Subject: 50% off
Message-ID: <news1@shop.example>
List-Unsubscribe: <mailto:off@shop.example>

Buy now
"""
WEB_FORM = """From: Website <noreply@harbourhomes.example>
Reply-To: Jo Mills <jo@example.com>
Subject: New website enquiry
Message-ID: <form9@harbourhomes.example>
Content-Type: text/html; charset=utf-8

<html><head><style>p{color:red}</style></head><body><p>Is the flat in Bay Road still available?</p><p>Jo</p></body></html>
"""


def test_mail_servers_are_worked_out_from_the_address():
    from nova import mailbox
    assert mailbox.servers("info@riverbend.example", [])[0] == ("mail.riverbend.example", "mail.riverbend.example", 465)
    assert mailbox.servers("info@riverbend.example", ["aspmx.l.google.com"]) == [("imap.gmail.com", "smtp.gmail.com", 465)]
    assert mailbox.servers("me@riverbend.example", ["riverbend-example.mail.protection.outlook.com"])[0][1] == "smtp.office365.com"
    assert ("mx1.host.example", "mx1.host.example", 465) in mailbox.servers("a@riverbend.example", ["mx1.host.example"])
    assert mailbox.env_key("harbour-homes") == "BIZ_MAIL_HARBOUR_HOMES"


def test_a_business_mailbox_is_signed_in_to_directly(shop, post):
    from nova import mailbox
    from nova.agent import needs_yes
    bid = "harbour-homes"
    r = biz.act({"biz": bid, "action": "mailbox_connect", "email": "hello@harbourhomes.example", "password": "wrong"})
    assert r["ok"] is False and "refused that username and password" in r["message"] and "app password" not in r["message"]
    assert biz.act({"biz": bid, "action": "mailbox_connect", "email": "hello@harbourhomes.example"}) == \
        {"ok": False, "message": "Type the mailbox's password too"}
    assert biz.cockpit(biz.find("harbour"))["mailbox"] == {"connected": False}
    r = biz.act({"biz": bid, "action": "mailbox_connect", "email": " Hello@HarbourHomes.example", "password": "s3cret"})
    assert r == {"ok": True, "message": "Connected to hello@harbourhomes.example. I read its enquiries and send approved replies from it."}
    c = biz.cockpit(biz.find("harbour"))
    assert c["email"] == "hello@harbourhomes.example" and c["mailbox"]["can_send"] and c["mailbox"]["imap_host"] == post.host
    assert "s3cret" not in json.dumps(biz.state())                               # the password never goes to the page
    assert "BIZ_MAIL_HARBOUR_HOMES=s3cret" in (shop["tmp"] / ".env").read_text() and "pass" not in json.dumps(mailbox.box(bid))

    post.add(1, ENQUIRY)
    post.add(2, NEWSLETTER)                                                      # automatic mail is not an enquiry
    post.add(3, WEB_FORM)                                                        # a website form: answer the Reply-To
    post.add(4, ENQUIRY.replace("abc123", "old1"), flags="\\Seen", when="01-Jan-2020 09:00:00 +0200")   # read before
    post.add(5, ENQUIRY.replace("abc123", "done1"), flags="\\Answered")                                  # answered by you
    shop["inbox"].append({"id": "g1", "from": "x@example.com", "subject": "Harbour Homes", "snippet": "hi"})
    assert biz.scan_inbox("harbour") == 2 and biz.scan_inbox("harbour") == 0      # each mail once; its Gmail search unused
    assert biz.scan_inbox() == 1 and biz.find("riverbend") and biz.cockpit(biz.find("riverbend"))["leads"][0]["source"] == "gmail"
    form, sam = biz.cockpit(biz.find("harbour"))["leads"]
    assert (sam["name"], sam["contact"], sam["source"], sam["score"]) == ("Sam Carter", "sam@example.com", "mailbox", 5)
    assert (form["name"], form["contact"]) == ("Jo Mills", "jo@example.com")
    assert form["message"] == "New website enquiry\nIs the flat in Bay Road still available?\nJo"
    biz._x("DELETE FROM biz_leads WHERE biz='riverbend'")
    assert biz.run_rules() == {"queued": 0, "sent": 0}                           # no standing instruction: nothing drafted
    biz.add_rule(bid, "new_lead", "Offer a call.")
    assert biz.run_rules() == {"queued": 2, "sent": 0} and not post.sent
    q = next(x for x in biz.pending(bid) if "Sam" in x["title"])
    assert q["tool"] == "business_send_email" and needs_yes(REGISTRY["business_send_email"], {})
    assert json.loads(q["args"])["subject"] == "Re: Listing my house"
    r = biz.act({"action": "approve", "id": q["id"], "body": "Hi Sam, I'll call you at 3."})
    assert r == {"ok": True, "message": "Approved — Email sent to sam@example.com from hello@harbourhomes.example."}
    [m] = post.sent
    assert m["From"] == "Harbour Homes <hello@harbourhomes.example>" and m["To"] == "sam@example.com"
    assert m["In-Reply-To"] == "<abc123@example.com>" and m.get_content().strip() == "Hi Sam, I'll call you at 3."
    assert post.filed[0][0] == '"INBOX.Sent"' and not shop["sent"]               # copy in Sent; Gmail untouched

    biz.add_followup(bid, "Dana Reed", "the valuation", "today", "dana@example.com")       # other mail goes out the same way
    biz.due_followups()
    assert biz.pending(bid)[-1]["tool"] == "business_send_email"
    assert biz.pending("riverbend") == [] and biz.mail_tool(biz.find("riverbend"), "a@b.example", "s", "b")[0] == "gmail_send"

    assert "Disconnected" in biz.act({"biz": bid, "action": "mailbox_disconnect"})["message"]
    assert biz.cockpit(biz.find("harbour"))["mailbox"] == {"connected": False}
    assert "s3cret" not in (shop["tmp"] / ".env").read_text()


def test_a_mailbox_that_can_be_read_but_not_sent_from(shop, post):
    from nova import mailbox
    post.smtp_ports = ()
    r = biz.act({"biz": "harbour-homes", "action": "mailbox_connect", "email": "hello@harbourhomes.example", "password": "s3cret"})
    assert r["ok"] and "can't send from it yet" in r["message"]
    post.add(1, ENQUIRY)
    assert biz.scan_inbox("harbour") == 1
    qid = biz.queue_reply(biz._q("SELECT * FROM biz_leads")[0])
    assert biz._q("SELECT tool FROM biz_queue WHERE id=?", (qid,))[0]["tool"] == ""        # you send this one yourself
    assert REGISTRY["business_send_email"].func("harbour", "sam@example.com", "Hi", "x").startswith("ERROR: I can read")
    post.smtp_ports = (587,)                                                      # typed in under Advanced
    r = biz.act({"biz": "harbour-homes", "action": "mailbox_connect", "email": "hello@harbourhomes.example",
                 "imap_host": post.host, "smtp_host": post.host})                  # no password: the saved one is used
    assert r["ok"] and mailbox.box("harbour-homes")["smtp_port"] == 587
    with pytest.raises(mailbox.MailError, match="couldn't reach the mail server"):
        mailbox.connect("riverbend", "shop@riverbend.example", "pw")


# ── v2.38: website health watch, and sales read from order emails ──────────
HOME = """<html><head><title>Harbour Homes — coastal property for sale and to rent</title>
<meta name="viewport" content="width=device-width"><meta name="description" content="Homes on the coast."></head>
<body><h1>Find your place by the sea</h1><p>{words}</p><a href="/listings">Listings</a> <a href="/gone">Old page</a>
<a href="mailto:hello@harbourhomes.example">Email us</a><img src="a.jpg"><img src="b.jpg" alt="Beach house"></body></html>"""


@pytest.fixture()
def web(shop, monkeypatch):
    """A pretend internet: {web address: (status, page)}; anything else can't be reached."""
    from nova import sitewatch
    pages = {"https://www.harbourhomes.example": (200, HOME.format(words="sea view " * 120)),
             "https://www.harbourhomes.example/listings": (200, "<html><head><title>Listings</title></head><body><p>Two homes.</p></body></html>"),
             "https://www.harbourhomes.example/gone": (404, "gone")}

    def get(url, timeout=20):
        if url.rstrip("/") not in pages:
            raise OSError("[Errno -2] Name or service not known")
        st, text = pages[url.rstrip("/")]
        if st == 0:
            raise TimeoutError("timed out")
        return st, text, 0.42, url
    monkeypatch.setattr(sitewatch, "_get", get)
    monkeypatch.setattr(sitewatch, "_cert_days", lambda host: 80)
    shop["cfg"]["business"] = {"recheck_seconds": 0}
    return pages


def test_a_website_is_watched_and_you_hear_when_it_goes_down(shop, web, monkeypatch):
    from nova import sitewatch
    h = biz.find("harbour")
    assert sitewatch.health("harbour-homes") == {"state": "unknown"}
    assert sitewatch.watch([h]) == ["🔗 Harbour Homes: 1 broken link(s) on the home page — see the Business dashboard."]
    s = biz.cockpit(h)["site"]["health"]
    assert (s["state"], s["ms"], s["ssl_days"], s["uptime"], s["links"], s["contact"]) == ("up", 420, 80, 100.0, 2, True)
    assert s["broken"] == [{"url": "https://www.harbourhomes.example/gone", "why": "error 404"}]
    assert sitewatch.watch([h]) == []                                             # the daily look isn't repeated every round
    web["https://www.harbourhomes.example"] = (0, "")
    assert sitewatch.watch([h]) == [] and sitewatch.health("harbour-homes")["state"] == "up"   # the PC itself is offline: no alarm
    web["https://www.google.com/generate_204"] = (204, "")
    assert sitewatch.watch([h]) == ["🔴 The Harbour Homes website is DOWN — the site didn't answer in time. (https://www.harbourhomes.example)"]
    assert sitewatch.watch([h]) == []                                             # told once, not every round
    down = sitewatch.health("harbour-homes")
    assert down["state"] == "down" and down["down_since"] and down["broken"]      # the last full look is kept
    web["https://www.harbourhomes.example"] = (200, HOME.format(words="x"))
    [back] = sitewatch.watch([h])
    assert back.startswith("🟢 The Harbour Homes website is back up")
    monkeypatch.setattr(sitewatch, "_cert_days", lambda host: 9)
    sitewatch._x("UPDATE biz_health SET ts='2020-01-01T00:00:00'")                  # a day later: the full look again
    assert "🔒 The Harbour Homes website's security certificate runs out in 9 days" in sitewatch.watch([h])[0]
    web["https://www.harbourhomes.example"] = (403, "Just a moment...")           # a bot wall is not an outage
    row = sitewatch.check(h, deep=True)
    assert row["up"] == 1 and "turns automatic checks away" in row["error"] and row["links"] == 0
    r = biz.act({"biz": "riverbend", "action": "health_check"})
    assert r["message"] == "Riverbend is DOWN — the web address can't be found (DNS)."
    from nova.skills.business import website_health
    web["https://www.harbourhomes.example"] = (200, HOME.format(words="x"))
    assert website_health("harbour").startswith("Harbour Homes: up, answered in 420 ms, security certificate has 9 days left")


ORDER = """From: Harbour Homes <noreply@harbourhomes.example>
To: hello@harbourhomes.example
Subject: [Harbour Homes] New order #1042
Message-ID: <order1042@harbourhomes.example>
Auto-Submitted: auto-generated
Content-Type: text/html; charset=utf-8

<p>You've received the following order from Thabo Nkosi:</p>
<table><tr><td>Featured listing</td><td>1</td><td>R 450.00</td></tr><tr><td>Subtotal:</td><td>R450.00</td></tr>
<tr><td>Shipping:</td><td>R99.00</td></tr><tr><td>Total:</td><td>R549.00</td></tr></table>
"""
PAYMENT = """From: PayGate <noreply@paygate.example>
Subject: Payment received
Message-ID: <pay77@paygate.example>

You have received a payment of R549.00 for Harbour Homes.
"""


def test_order_and_payment_emails_become_sales(shop, post, monkeypatch):
    from nova import mailbox
    assert mailbox.order_of("[Shop] New order #1042", "order from Thabo Nkosi:\nSubtotal: R450.00\nTotal: R549.00") == \
        {"amount": 549.0, "order": "1042", "party": "Thabo Nkosi (order 1042)"}
    assert mailbox.order_of("Payment received", "You have received a payment of R1 250,00 from Jo Mills.")["amount"] == 1250.0
    assert mailbox.order_of("Your order has shipped", "Total: R50.00") is None    # something you bought
    assert mailbox.order_of("Refund for order #7", "Total: R50.00") is None and mailbox.order_of("New order", "thanks") is None
    assert biz.act({"biz": "harbour-homes", "action": "mailbox_connect", "email": "hello@harbourhomes.example", "password": "s3cret"})["ok"]
    post.add(1, ORDER)
    post.add(2, PAYMENT)                                                         # the payment provider, about the same sale
    post.add(3, ENQUIRY)
    post.add(4, NEWSLETTER)
    pushed = []
    monkeypatch.setattr(context, "push", lambda text, files=None: pushed.append(text))
    out = biz.tick()
    assert out["sales"] == 1 and out["leads"] == 1
    assert pushed[0] == "💰 Sale recorded for Harbour Homes: R549 — Thabo Nkosi (order 1042)."
    c = biz.cockpit(biz.find("harbour"))
    assert c["kpi"]["sales_week"] == 549 and c["kpi"]["leads_open"] == 1
    [sale] = [m for m in c["money"] if m["kind"] == "sale"]
    assert sale["ref"] == "mbox:<order1042@harbourhomes.example>" and "order 1042" in sale["note"]
    assert biz.scan("harbour") == {"leads": 0, "sales": []}                       # nothing is counted twice
    post.add(5, ORDER.replace("1042", "1043"))                                   # the same amount, another order number
    assert [m["party"] for m in biz.scan("harbour")["sales"]] == ["Thabo Nkosi (order 1043)"]
    assert biz.act({"biz": "harbour-homes", "action": "money_delete", "id": sale["id"]}) == {"ok": True, "message": "Removed."}
    assert biz.cockpit(biz.find("harbour"))["kpi"]["sales_week"] == 549           # "Not a sale" took the first one off
    shop["cfg"]["business"]["sales_from_email"] = False
    post.add(6, ORDER.replace("1042", "1050"))
    assert biz.scan("harbour") == {"leads": 0, "sales": []}


# ── v2.39–2.41: targets, customers, posts, the Google check, competitors, visitor numbers ─────────
def test_target_trend_and_customers(shop):
    from nova import bizplan
    from nova.skills.business import customer_list, sales_target
    today = dt.date.today()
    biz.add_money("harbour", "sale", "Sam Carter", 1500)
    biz.add_money("harbour", "sale", "Sam Carter", 500)
    biz.add_money("harbour", "sale", "Dune Realty", 900, note="accounts@dune.example")
    biz.add_lead("harbour", "Sam Carter", "sam@example.com", "How much to list?")
    biz.add_lead("harbour", "Priya Naidoo", "082 555 0100", "Rentals?")
    assert sales_target("harbour") == "Harbour Homes has no target. Sales this month: R2 900."
    assert sales_target("harbour", 10000) == "Target for Harbour Homes: R10 000 a month."
    t = biz.cockpit(biz.find("harbour"))["trend"]
    assert (t["target"], t["month"], t["pct"]) == (10000, 2900, 29) and len(t["weeks"]) == 8
    assert t["weeks"][-1]["now"] and t["weeks"][-1]["sales"] == 2900 and t["weeks"][-1]["leads"] == 2
    assert t["pace"] == pytest.approx(2900 / today.day * t["days"]) and "R2 900 of R10 000 (29%)" in sales_target("harbour")
    assert biz.act({"biz": "harbour-homes", "action": "target_set", "amount": 0})["message"] == "Target removed."
    people = {p["name"]: p for p in bizplan.customers(biz.find("harbour"))}
    assert set(people) == {"Sam Carter", "Dune Realty", "Priya Naidoo"}
    sam = people["Sam Carter"]
    assert (sam["email"], sam["orders"], sam["spent"], sam["enquiries"], sam["repeat"]) == ("sam@example.com", 2, 2000, 1, True)
    assert people["Dune Realty"]["email"] == "accounts@dune.example" and people["Priya Naidoo"]["phone"] == "0825550100"
    c = biz.cockpit(biz.find("harbour"))["customers"]
    assert (c["total"], c["repeat"]) == (3, 1)
    assert "3 people on file, 1 bought more than once, 0 gone quiet" in customer_list("harbour")
    assert bizplan.win_back() == 0                                                # nobody is quiet yet
    later = today + dt.timedelta(days=70)
    assert bizplan.win_back(later) == 2 and bizplan.win_back(later) == 0          # each person once
    q = {x["title"]: x for x in biz.pending("harbour-homes")}
    assert set(q) == {"Win back Sam Carter — quiet for 70 days", "Win back Dune Realty — quiet for 70 days"}   # Priya never bought
    assert q["Win back Sam Carter — quiet for 70 days"]["tool"] == "gmail_send" and not shop["sent"]
    assert "Customers on file: 3, of whom 1 bought more than once." in biz.growth_facts(biz.find("harbour"), biz.cockpit(biz.find("harbour")))


def test_a_week_of_posts_is_drafted_to_post_yourself(shop, monkeypatch):
    from nova import bizplan
    monday = dt.date(2026, 10, 5)
    rows = bizplan.plan_week(biz.find("harbour"), monday)                         # no model: slots to fill in yourself
    assert [p["day"] for p in rows] == ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
    assert rows[0]["platform"] == "Instagram" and "Harbour Homes · www.harbourhomes.example" in rows[0]["text"]

    class Model:
        def complete(self, prompt, **k):
            assert "adults only" in prompt and "Property sales and rentals on the coast." in prompt and "spring special" in prompt
            return json.dumps({"posts": [{"day": d, "platform": "Facebook", "text": f"Post for {d} #coast", "idea": "A drone shot"}
                                         for d in ("2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2031-01-01")]})
    monkeypatch.setattr(context, "llm", Model())
    r = biz.act({"biz": "harbour-homes", "action": "plan_week", "brief": "spring special"})
    assert r["message"].endswith("posts drafted for the coming week.")
    saturday = dt.date(2026, 10, 10)
    rows = bizplan.plan_week(biz.find("harbour"), saturday, "spring special")     # at the weekend: next Monday to Friday
    assert [p["day"] for p in rows] == ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
    assert rows[0]["text"] == "Post for 2026-10-12 #coast" and rows[0]["idea"] == "A drone shot"
    assert biz.act({"action": "post_status", "id": rows[0]["id"], "status": "posted"})["message"] == "Marked as posted."
    assert biz.act({"action": "post_status", "id": rows[1]["id"], "status": "skipped"})["message"] == "Skipped."
    left = bizplan.posts("harbour-homes", saturday)
    assert [p["status"] for p in left] == ["posted", "planned", "planned", "planned"]
    bizplan.plan_week(biz.find("harbour"), saturday)                              # planning again keeps what was posted
    assert [p["status"] for p in bizplan.posts("harbour-homes", saturday)].count("posted") == 1
    assert bizplan.plan_due(saturday) == 1 and bizplan.plan_due(saturday) == 0     # Riverbend had nothing; then all are planned
    assert [p["day"] for p in bizplan.plan_week(biz.find("riverbend"), dt.date(2026, 10, 7))] == ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]


def test_the_google_check_scores_the_site_and_lists_fixes(shop, web):
    from nova import sitewatch
    r = biz.act({"biz": "harbour-homes", "action": "seo_check"})
    assert r["ok"] and r["message"].startswith("Google check: ") and "the list is in the queue" in r["message"]
    seo = biz.cockpit(biz.find("harbour"))["site"]["seo"]
    said = [f"{i['page']} {i['text']}" for i in seo["issues"]]
    assert seo["pages"] == 2 and "/listings is thin (2 words)" in said and "/listings has no description for Google to show under the title" in said
    assert "whole site has no sitemap.xml for Google to find all the pages" in said and "home page has no share picture" in " ".join(said)
    assert not any("home page has no main heading" in s or "home page is thin" in s or "home page isn't marked as phone-friendly" in s for s in said)
    assert seo["issues"][0]["sev"] >= seo["issues"][-1]["sev"] and 5 <= seo["score"] < 100
    [q] = biz.pending("harbour-homes")
    assert q["kind"] == "page" and q["tool"] == "" and f"scores {seo['score']} out of 100" in q["body"] and "Fix:" in q["body"]
    biz.act({"biz": "harbour-homes", "action": "seo_check"})
    assert len(biz.pending("harbour-homes")) == 1 and biz.cockpit(biz.find("harbour"))["site"]["seo"]["before"] == seo["score"]
    spa = sitewatch._page("https://x.example/", "<html><head><title>App</title></head><body><div id=root></div>" + "<script></script>" * 4 + "</body></html>")
    score, issues = sitewatch.audit([spa], True, True)
    assert "almost no text until JavaScript runs" in issues[0]["text"] and score < 80
    assert biz.act({"biz": "riverbend", "action": "seo_check"}) == \
        {"ok": False, "message": "I couldn't read the site: the web address can't be found (DNS)."}


def test_competitor_pages_are_watched_for_changes(shop, web):
    from nova import sitewatch
    from nova.skills.business import competitor_changes, watch_competitor
    assert competitor_changes() == "No competitors are being watched yet. Say 'watch <web address> for <business>'."
    web["https://coastlist.example/pricing"] = (200, "<html><body><h1>Pricing</h1><p>Basic listing R199</p><p>Agents welcome</p></body></html>")
    assert watch_competitor("harbour", "coastlist.example/pricing", "CoastList") == \
        "Watching CoastList for Harbour Homes — I'll tell you what changes, once a week."
    [r] = biz.cockpit(biz.find("harbour"))["site"]["rivals"]
    assert r["summary"] == "First look saved — changes show from next week." and "text" not in r
    assert sitewatch.check_rivals() == [] and competitor_changes("harbour") == "Nothing changed on the competitor pages since the last look."
    web["https://coastlist.example/pricing"] = (200, "<html><body><h1>Pricing</h1><p>Basic listing R149</p><p>Agents welcome</p><p>Free in October</p></body></html>")
    assert sitewatch.check_rivals() == []                                         # only once a week by itself
    [moved] = sitewatch.check_rivals(force=True)
    assert moved == "CoastList: new prices: R149; prices gone: R199; 2 new line(s), e.g. “Basic listing R149” · “Free in October”"
    c = biz.cockpit(biz.find("harbour"))
    assert any(f.startswith("Competitors this week — CoastList: new prices: R149") for f in biz.growth_facts(biz.find("harbour"), c))
    web["https://coastlist.example/pricing"] = (403, "")
    sitewatch.check_rivals(force=True)
    assert "some big sites block automatic readers" in sitewatch.rivals("harbour-homes")[0]["summary"]
    assert watch_competitor("harbour", "coastlist.example", stop=True) == "Stopped watching CoastList." and not sitewatch.rivals("harbour-homes")


def test_visitor_and_search_numbers_from_google(shop, monkeypatch):
    from nova import sitestats
    monkeypatch.setattr(sitestats, "resolve", lambda p: shop["tmp"] / p)
    assert biz.cockpit(biz.find("harbour"))["stats"] == {"connected": False, "connecting": False, "error": ""}
    assert biz.act({"biz": "harbour-homes", "action": "stats_refresh"}) == {"ok": False, "message": "Press Connect first."}
    (shop["tmp"] / "secrets").mkdir(exist_ok=True)
    sitestats.token_file().write_text("{}")
    off = {"analytics": False}

    class Reply:
        def __init__(self, data, code=200):
            self.status_code, self.text, self._d = code, json.dumps(data), data

        def json(self):
            return self._d

    class Session:
        def request(self, method, url, json=None, timeout=30):
            if "analytics" in url and off["analytics"]:
                return Reply({"error": {"message": "Google Analytics Data API has not been used in project 12 before or it is disabled. "
                                                   "Enable it by visiting https://console.developers.google.com/apis/api/analyticsdata.googleapis.com/overview?project=12 then retry."}}, 403)
            if url.endswith("/webmasters/v3/sites"):
                return Reply({"siteEntry": [{"siteUrl": "sc-domain:harbourhomes.example", "permissionLevel": "siteOwner"},
                                            {"siteUrl": "https://other.example/", "permissionLevel": "siteOwner"}]})
            if "searchAnalytics" in url:
                assert "sc-domain%3Aharbourhomes.example" in url
                if json.get("dimensions"):
                    return Reply({"rows": [{"keys": ["houses for sale ballito"], "clicks": 9, "impressions": 120, "position": 6.42}]})
                return Reply({"rows": [{"clicks": 30, "impressions": 400, "position": 8.0}]} if json["startDate"] > "2026-09-25"
                             else {"rows": [{"clicks": 20, "impressions": 500, "position": 9.0}]})
            if "accountSummaries" in url:
                return Reply({"accountSummaries": [{"propertySummaries": [{"property": "properties/1"}, {"property": "properties/2"}]}]})
            if url.endswith("properties/1/dataStreams"):
                return Reply({"dataStreams": [{"webStreamData": {"defaultUri": "https://other.example"}}]})
            if url.endswith("properties/2/dataStreams"):
                return Reply({"dataStreams": [{"webStreamData": {"defaultUri": "https://www.harbourhomes.example"}}]})
            assert url.endswith("properties/2:runReport")
            if json.get("dimensions"):
                return Reply({"rows": [{"dimensionValues": [{"value": "/listings"}], "metricValues": [{"value": "88"}]}]})
            return Reply({"rows": [{"dimensionValues": [{"value": "date_range_0"}], "metricValues": [{"value": "150"}, {"value": "180"}, {"value": "420"}]},
                                   {"dimensionValues": [{"value": "date_range_1"}], "metricValues": [{"value": "100"}, {"value": "120"}, {"value": "400"}]}]})
    monkeypatch.setattr(sitestats, "_session", lambda: Session())
    d = sitestats.refresh(biz.find("harbour"), dt.date(2026, 10, 6))
    assert d["search"] == {"clicks": 30, "shown": 400, "position": 8.0, "clicks_change": 50, "shown_change": -20,
                           "queries": [{"q": "houses for sale ballito", "clicks": 9, "shown": 120, "position": 6.4}]}
    assert d["visitors"] == {"visitors": 150, "visits": 180, "views": 420, "visitors_change": 50, "views_change": 5,
                             "pages": [{"path": "/listings", "views": 88}]} and d["needs"] == []
    c = biz.cockpit(biz.find("harbour"))
    assert c["stats"]["connected"] and c["stats"]["visitors"]["visitors"] == 150
    facts = " ".join(biz.growth_facts(biz.find("harbour"), c))
    assert "Website visitors last 7 days: 150 (+50% on the week before)" in facts and "top searches: houses for sale ballito" in facts
    assert sitestats.refresh_due(biz.businesses()) == 1                           # Riverbend hadn't been fetched; Harbour is fresh
    r = sitestats.stats("riverbend")
    assert r["search"] is None and "Add riverbend.example to Google Search Console" in r["needs"][0]["text"]
    off["analytics"] = True
    d = sitestats.refresh(biz.find("harbour"), dt.date(2026, 10, 6))
    assert d["search"]["clicks"] == 30 and d["visitors"] is None
    assert d["needs"] == [{"text": "The Google Analytics service is switched off in your Google project. Open the link, press "
                                   "Enable, wait a minute, then press Refresh here.",
                           "link": "https://console.developers.google.com/apis/api/analyticsdata.googleapis.com/overview?project=12"}]
    assert biz.act({"biz": "harbour-homes", "action": "stats_disconnect"})["ok"] and not sitestats.connected()
