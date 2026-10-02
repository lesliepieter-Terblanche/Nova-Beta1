"""v2.6: people cards, filing into the brain (Telegram forwarding), ask-the-brain and the digest."""
import json
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from nova import answers, context, inbox, people


def scripted(nova, monkeypatch, people_json=None, summary=None, answer="Sam Dlamini at Northwind wants Mist pricing [1][2]."):
    """Route the fake model's answers by prompt."""
    def complete(prompt, system="", prefer_smart=True, temperature=0.5):
        if "List the real, named people" in prompt:
            return json.dumps({"people": people_json or []})
        if "You file things into a personal knowledge base" in prompt:
            return json.dumps(summary or {"title": "Mist deal update", "summary": ["Northwind wants pricing", "Due Friday"],
                                          "type": "message"})
        if "Answer his question using ONLY the numbered sources" in prompt:
            return answer
        if "weekly" in prompt:
            return "- Busy week with Northwind."
        if "long-term memory" in prompt:
            return '{"memories": []}'
        return "summary"
    monkeypatch.setattr(context.llm, "complete", complete)


# ── people cards ──────────────────────────────────────────
def test_cards_merge_names_and_fill_details(nova):
    pid, created = people.upsert("Sam", company="Northwind")
    assert created
    pid2, created2 = people.upsert("Sam Dlamini", company="Northwind", role="Juniper BDM", email="Sam@Northwind.co.za",
                                   note="Owns the Mist refresh")
    assert pid2 == pid and not created2
    p = people.get(pid)
    assert p["name"] == "Sam Dlamini" and p["aliases"] == ["Sam"] and p["email"] == "sam@northwind.co.za"
    assert "Owns the Mist refresh" in p["notes"]
    people.upsert("Sam Dlamini", note="Owns the Mist refresh")          # no duplicate notes
    assert people.get(pid)["notes"].count("Mist refresh") == 1
    assert people.find("sam")["id"] == pid                            # unique first name works
    people.upsert("Sam Botha", company="Dimension Data")
    assert people.find("Sam") is None                                  # now ambiguous
    tid = people.from_line("Thandi Nkosi – Channel Manager, Nokia")
    t = people.get(tid)
    assert (t["role"], t["company"]) == ("Channel Manager", "Nokia")
    assert people.get(people.from_line("Alex Venter – Northwind"))["company"] == "Northwind"
    keep = people.merge(pid, people.find("Sam Botha")["id"])
    assert "Sam Botha" in keep["aliases"] and len(people.all_people()) == 3


def test_absorb_from_text_skips_the_owner(nova, monkeypatch):
    nova[0]["assistant"]["owner"] = "Alex"
    scripted(nova, monkeypatch, people_json=[
        {"name": "Alex", "company": "Westcon"},
        {"name": "Lerato Mokoena", "company": "Avaya", "role": "Partner manager", "fact": "Runs the SADC partner program"}])
    ids = people.absorb("Alex met Lerato Mokoena from Avaya about the SADC partner program.")
    assert len(ids) == 1 and people.get(ids[0])["role"] == "Partner manager"
    assert people.absorb("nothing here at all") == []                # no names → no model call needed


def test_mentions_deals_and_summary(nova):
    s = context.store
    pid, _ = people.upsert("Sam Dlamini", company="Northwind", role="BDM")
    s.add_memory("Sam Dlamini asked for Juniper Mist pricing on the Standard Bank deal", "person")
    s.add_memory("Sam Dlamini prefers WhatsApp over email", "preference")
    s.add_memory("The office Wi-Fi password changes monthly", "fact")
    c = people.card(pid)
    assert len(c["mentions"]) == 2 and sum(m["deal"] for m in c["mentions"]) == 1
    txt = people.summary(people.get(pid))
    assert "Sam Dlamini — BDM, Northwind" in txt and "Deals & money" in txt and "WhatsApp" in txt


# ── filing (Telegram forwarding) ──────────────────────────
def test_file_forwarded_message(nova, monkeypatch):
    cfg, tmp = nova
    scripted(nova, monkeypatch, people_json=[{"name": "Sam Dlamini", "company": "Northwind", "fact": "Needs Mist pricing"}],
             summary={"title": "Northwind Mist pricing request", "summary": ["Needs pricing by Friday"], "type": "message"})
    context.store.add_memory("TrueHome is Alex's property platform project", "project")
    r = inbox.file_text("Hi Alex, please send TrueHome listing pricing and the Mist quote by Friday. Sam",
                        from_who="Sam Dlamini")
    p = Path(r["path"])
    assert p.exists() and p.parent.name == "02_Clients_&_Partners" and p.parent.parent.name == "02_Work"
    assert p.name.endswith("_Northwind_Mist_Pricing_Request.md") and "Northwind Mist pricing request" in p.read_text()
    assert "From: Sam Dlamini" in p.read_text()
    assert r["people"] == ["Sam Dlamini"] and people.find("Sam Dlamini")["last_contact"]
    assert r["project"].startswith("TrueHome")
    text = inbox.reply_text(r)
    assert "📥 Filed in your brain: Northwind Mist pricing request" in text and "📁 Project: TrueHome" in text
    assert context.store.search_notes("Mist quote")


def test_file_business_card_photo(nova, monkeypatch, tmp_path):
    scripted(nova, monkeypatch)
    img = tmp_path / "card.jpg"
    from PIL import Image
    Image.new("RGB", (60, 40), "white").save(img)
    monkeypatch.setattr(context.llm, "see", lambda path, q: json.dumps({"business_card": {
        "name": "Johan Pretorius", "company": "Nokia", "role": "Account Manager", "email": "johan@nokia.com",
        "phone": "+27 82 555 0101", "website": ""}}))
    r = inbox.file_document(img)
    p = people.find("Johan Pretorius")
    assert p and p["phone"] == "+27 82 555 0101" and r["person"]["created"]
    assert "New card: Johan Pretorius" in inbox.reply_text(r)
    assert any((Path(r["path"]).parent / "Attachments").iterdir())          # picture kept beside its category


def test_file_whiteboard_and_link(nova, monkeypatch, tmp_path):
    scripted(nova, monkeypatch, summary={"title": "Q4 whiteboard", "summary": ["Plan"], "type": "whiteboard"})
    img = tmp_path / "wb.png"
    from PIL import Image
    Image.new("RGB", (60, 40), "white").save(img)
    monkeypatch.setattr(context.llm, "see", lambda path, q: "TYPE: whiteboard\nQ4 plan -> Mist bundle\n- Avaya renewals")
    r = inbox.file_image(img)
    assert "Mist bundle" in Path(r["path"]).read_text() and "![" in Path(r["path"]).read_text()
    from nova.skills import web
    monkeypatch.setattr(web, "fetch_text", lambda url: ("Juniper launches new Mist AI features for SMEs. " * 5, "Juniper news"))
    r2 = inbox.file_url("https://example.com/juniper")
    assert "Source: https://example.com/juniper" in Path(r2["path"]).read_text()


def test_telegram_helpers():
    from nova.telegram_bot import TelegramBot
    assert TelegramBot.only_links("https://a.co/x") and TelegramBot.only_links(" https://a.co https://b.co ")
    assert not TelegramBot.only_links("summarise https://a.co please") and not TelegramBot.only_links("hello")
    assert TelegramBot._forwarded(SimpleNamespace(forward_origin=None)) == ""
    fo = SimpleNamespace(sender_user=SimpleNamespace(first_name="Sam", last_name="Dlamini"))
    assert TelegramBot._forwarded(SimpleNamespace(forward_origin=fo)) == "Sam Dlamini"
    ch = SimpleNamespace(chat=SimpleNamespace(first_name=None, last_name=None, title="MyBroadband"))
    assert TelegramBot._forwarded(SimpleNamespace(forward_origin=ch)) == "MyBroadband"


# ── ask the brain ─────────────────────────────────────────
def test_ask_brain_cites_sources(nova, monkeypatch):
    scripted(nova, monkeypatch)
    s = context.store
    people.upsert("Sam Dlamini", company="Northwind")
    s.add_memory("Sam Dlamini at Northwind wants Juniper Mist pricing", "person")
    s.add_memory("Avaya renewals are due in March", "fact")
    r = answers.ask("What does Sam Dlamini want?")
    assert r["answer"].startswith("Sam Dlamini") and [x["n"] for x in r["sources"]] == [1, 2]
    assert r["sources"][0]["id"].startswith("person:")
    assert "Sources: [1] Sam Dlamini (person)" in answers.as_text(r)
    assert answers.ask("What does Sam want?")["sources"][0]["id"].startswith("person:")    # first name is enough
    empty = answers.ask("What is the capital of Mars colony?")
    assert empty["sources"] == [] or empty["answer"]
    from nova.tools import REGISTRY, select_tools
    assert "ask_brain" in {t.name for t in select_tools("what do I know about the Northwind deal")}
    assert "person_card" in {t.name for t in select_tools("what's Sam Dlamini's number")}   # names are triggers
    assert "Sam Dlamini" in REGISTRY["person_card"].run({"name": "Sam"})


def test_digest(nova, monkeypatch):
    scripted(nova, monkeypatch)
    context.store.add_memory("Signed the Nokia SADC distribution deal", "decision")
    people.upsert("Lerato Mokoena", company="Avaya")
    d = answers.digest(7)
    assert "1 new people" in d and "Busy week" in d
    y, w, _ = __import__("datetime").date.today().isocalendar()
    assert (Path(nova[0]["brain"]["vault_dir"]) / "02_Work" / "05_Meetings_&_Reports" / "Digests"
            / f"{y}-W{w:02d}.md").exists()


def test_catch_up_reads_each_memory_once(nova, monkeypatch):
    calls = []
    scripted(nova, monkeypatch, people_json=[{"name": "Kobus Smit", "company": "SonarSource"}])
    orig = context.llm.complete
    monkeypatch.setattr(context.llm, "complete", lambda p, **k: calls.append(p) or orig(p, **k))
    context.store.add_memory("Kobus Smit is the SonarSource regional lead", "person")
    assert people.catch_up() == 1 and people.find("Kobus Smit")
    n = len(calls)
    people.catch_up()
    assert len(calls) == n


# ── dashboard ─────────────────────────────────────────────
@pytest.fixture()
def dash(nova):
    from nova.dashboard.server import Dashboard
    cfg = nova[0]
    cfg["dashboard"]["open_on_start"] = False
    cfg["dashboard"]["port"] = 8796
    d = Dashboard(cfg, None)
    d.start()
    return d


def call(path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:8796{path}", data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:8796"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def test_dashboard_people_and_ask(dash, nova, monkeypatch):
    scripted(nova, monkeypatch)
    s = context.store
    pid, _ = people.upsert("Sam Dlamini", company="Northwind")
    s.add_memory("Sam Dlamini asked for the Mist quote", "person")
    g = dash.graph()
    node = next(n for n in g["nodes"] if n["id"] == f"person:{pid}")
    assert node["kind"] == "person" and node["mentions"] == 1 and node["created"]
    assert any(ln["type"] == "mention" and ln["source"] == f"person:{pid}" for ln in g["links"])
    assert all(n.get("created") for n in g["nodes"] if n["kind"] == "note")
    it = call(f"/api/item?id=person:{pid}")
    assert it["type"] == "person" and it["person"]["company"] == "Northwind" and "Deals & money" in it["mentions"]
    call("/api/person", {"id": f"person:{pid}", "phone": "082 000 1111", "role": "BDM"})
    assert people.get(pid)["phone"] == "082 000 1111"
    new = call("/api/person", {"new": True, "name": "Nomsa Khumalo", "company": "Nokia"})
    assert people.find("Nomsa Khumalo") and new["id"].startswith("person:")
    t = call("/api/topic?kind=person")
    assert {"Sam Dlamini", "Nomsa Khumalo"} <= {i["title"] for i in t["items"]}
    r = call("/api/ask_brain", {"question": "What did Sam Dlamini ask for?"})
    assert r["sources"] and r["answer"]
