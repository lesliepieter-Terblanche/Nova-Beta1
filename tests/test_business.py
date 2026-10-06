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
