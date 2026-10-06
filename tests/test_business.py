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
    assert biz.weekly() == "" and biz.tick() == {"leads": 0, "queued": 0, "sent": 0, "followups": 0}


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
    assert biz.tick() == {"leads": 1, "queued": 1, "sent": 0, "followups": 0}
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
