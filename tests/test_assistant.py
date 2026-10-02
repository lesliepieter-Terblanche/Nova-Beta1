"""Budget and slips, undo, learning from corrections, weekly report, activity review, personality."""
import datetime as dt
import json

import pytest

from nova import budget, context, lessons, reports, taxonomy, undo
from nova.agent import Agent
from nova.tools import REGISTRY

SLIP = {"is_slip": True, "merchant": "Checkers Sandton", "date": "2026-10-02", "total": 452.5, "currency": "R",
        "category": "Groceries", "items": ["Milk", "Bread", "Coffee"]}


@pytest.fixture()
def home(nova, monkeypatch):
    cfg, tmp = nova
    cfg["brain"]["structure"] = "strict"
    monkeypatch.setattr(context.llm, "see", lambda path, q: json.dumps(SLIP), raising=False)
    pic = tmp / "files" / "IMG_2041.jpg"
    pic.write_bytes(b"\xff\xd8jpeg")
    context.begin_turn("test")
    return cfg, tmp, pic


def test_a_slip_photo_is_read_filed_under_personal_and_added_to_the_month(home):
    cfg, tmp, pic = home
    out = REGISTRY["add_slip"].run({"path": str(pic)})
    assert out.startswith("Slip filed: R452.50 at Checkers Sandton on 2026-10-02 (Groceries).")
    folder = taxonomy.vault() / "01_Personal" / "04_Finance_&_Budgets" / "Slips"
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["2026-10-02_Checkers_Sandton_R452.jpg", "2026-10-02_Checkers_Sandton_R452.md"]
    note = (folder / names[1]).read_text()
    assert note.startswith("[LABEL: DOMAIN: PERSONAL]\n[LABEL: STATUS: COMPLETED]") and "![[2026-10-02_Checkers_Sandton_R452.jpg]]" in note
    assert "Items: Milk, Bread, Coffee" in note
    REGISTRY["add_expense"].run({"amount": 800, "merchant": "Engen", "category": "petrol", "date": "2026-10-03"})
    assert REGISTRY["set_budget"].run({"category": "groceries", "amount": 6000}) == "Budget for Groceries: R6 000.00 a month."
    REGISTRY["set_budget"].run({"category": "total", "amount": 20000})
    text = REGISTRY["spending"].run({"month": "2026-10"})
    assert text.splitlines()[0] == "October 2026: R1 252.50 spent of your R20 000.00 budget."
    assert "- Fuel: R800.00" in text and "- Groceries: R452.50 of R6 000.00 — R5 547.50 left" in text
    REGISTRY["set_budget"].run({"category": "fuel", "amount": 500})
    assert "- Fuel: R800.00 of R500.00 — R300.00 OVER" in REGISTRY["spending"].run({"month": "2026-10"})
    assert "categories are" in REGISTRY["set_budget"].run({"category": "llamas", "amount": 5})


def test_a_picture_that_is_not_a_slip_is_not_guessed(home, monkeypatch):
    cfg, tmp, pic = home
    monkeypatch.setattr(context.llm, "see", lambda path, q: '{"is_slip": false, "total": 0}', raising=False)
    assert "couldn't find a total" in REGISTRY["add_slip"].run({"path": str(pic)})
    assert budget.summary("2026-10")["total"] == 0


def test_claims_are_tracked_until_paid_and_exported(home):
    cfg, tmp, pic = home
    out = REGISTRY["add_slip"].run({"path": str(pic), "claim_for": "work", "note": "client lunch"})
    assert "to claim from work" in out
    note = taxonomy.vault() / "01_Personal" / "04_Finance_&_Budgets" / "Claims" / "2026-10-02_Checkers_Sandton_R452.md"
    assert "[LABEL: STATUS: WAITING-ON-USER]" in note.read_text() and "Claim: work — to be claimed" in note.read_text()
    assert budget.summary("2026-10")["total"] == 0                               # a claim isn't your own spending
    assert "Still to claim: R452.50 (1 slips)." in REGISTRY["spending"].run({"month": "2026-10"})
    listed = REGISTRY["list_claims"].run({})
    assert "Checkers Sandton R452.50 — work (to claim)" in listed and "Still to claim: R452.50." in listed
    pack = REGISTRY["export_claims"].run({"claim_for": "work"})
    folder = __import__("pathlib").Path(pack.split(" is in ")[1].split(": claims.csv")[0])
    rows = (folder / "claims.csv").read_text(encoding="utf-8-sig").splitlines()
    assert rows[1].startswith("2026-10-02,Checkers Sandton,452.50,Groceries,work,client lunch,2026-10-02_Checkers")
    assert rows[-1] == ",TOTAL,452.50" and (folder / "2026-10-02_Checkers_Sandton_R452.jpg").exists()
    assert REGISTRY["update_claim"].run({"which": "work", "state": "claimed"}) == "Marked 1 claim (R452.50) as submitted."
    assert "[LABEL: STATUS: IN-PROGRESS]" in note.read_text()
    REGISTRY["update_claim"].run({"which": "all", "state": "paid"})
    assert "[LABEL: STATUS: COMPLETED]" in note.read_text() and REGISTRY["list_claims"].run({}) == "Nothing is waiting to be claimed."


def test_slips_sent_by_upload_or_telegram_go_to_the_budget(home, monkeypatch):
    from nova import inbox
    cfg, tmp, pic = home
    r = inbox.file_document(pic, source="telegram", note="slip for my medical aid claim")
    assert r["type"] == "slip" and r["title"] == "Slip — Checkers Sandton R452.50"
    assert r["summary"][0] == "2026-10-02 · Groceries — to claim from medical aid"
    assert inbox.reply_text(r).startswith("📥 Filed in your brain: Slip — Checkers Sandton R452.50")
    # nobody said "slip", but the picture is a receipt: still the budget, not an ordinary photo
    calls = []

    def see(path, q):
        calls.append(q[:20])
        return "TYPE: receipt\nCHECKERS TOTAL 452.50" if "BUSINESS CARD" in q else json.dumps(SLIP)
    monkeypatch.setattr(context.llm, "see", see, raising=False)
    r = inbox.file_document(pic, source="dashboard", note="")
    assert r["type"] == "slip" and len(budget.claims()) == 1 and budget.summary("2026-10")["total"] == 452.5


def test_correcting_a_shops_category_is_remembered(home):
    cfg, tmp, pic = home
    REGISTRY["add_slip"].run({"path": str(pic)})
    out = REGISTRY["fix_expense_category"].run({"which": "last", "category": "eating out"})
    assert out == "Changed to Eating out. Slips from Checkers Sandton will go there from now on."
    e = budget.add_slip(pic)                                                      # the model still says Groceries
    assert e["category"] == "Eating out"                                          # …but you taught otherwise
    assert budget.add(99, "Checkers Sandton")["category"] == "Eating out"         # by hand, no category given
    assert budget.add(20, "Checkers Sandton", "Gifts")["category"] == "Gifts"     # what you say outright still wins
    assert "Checkers Sandton → Eating out" in REGISTRY["what_have_you_learned"].run({})


def test_moving_a_misfiled_note_teaches_the_filing(home, monkeypatch):
    cfg, tmp, _ = home
    src = taxonomy.folder_for(taxonomy.WORK, "04_Resources_&_Reference") / "Mahindra_Service_Quote.md"
    src.write_text("# Mahindra service quote\n\nBrake pads and oil.", encoding="utf-8")
    r = taxonomy.move_item(f"note:{src}", taxonomy.PERSONAL, "01_Life_Admin")
    assert r["where"].startswith("01_Personal/01_Life_Admin/")
    asked = []
    monkeypatch.setattr(context.llm, "complete", lambda prompt, **k: asked.append(prompt) or
                        '{"domain": "work", "category": "04_Resources_&_Reference", "confidence": 0.9}')
    c = taxonomy.classify_full("Mahindra service quote")                           # same thing again: no model needed
    assert (c["domain"], c["category"], asked) == ("01_Personal", "01_Life_Admin", [])
    assert c["reason"] == "you filed something like this here before"
    taxonomy.classify_full("Juniper price list")                                   # unrelated: the model decides,
    assert '"Mahindra Service Quote" belongs in 01_Personal / 01_Life_Admin' in asked[0]   # …but sees the correction
    assert "'Mahindra Service Quote' → 01_Personal / 01_Life_Admin" in REGISTRY["what_have_you_learned"].run({})
    assert REGISTRY["forget_lesson"].run({"which": "mahindra"}) == "Forgotten (1)." and lessons.filing("Mahindra service quote") is None


def test_preferences_from_corrections_are_always_in_nova_head(home):
    cfg, _, _ = home
    REGISTRY["remember"].run({"text": "Keep emails to three sentences", "kind": "preference"})
    prompt = Agent(cfg, context.llm)._system_prompt("voice", "hi")
    assert "How the user wants things done" in prompt and "- Keep emails to three sentences" in prompt
    assert 'remember(kind="preference")' in prompt


def test_undo_puts_things_back(home):
    cfg, tmp, pic = home
    agent = Agent(cfg, context.llm)                                               # "undo that" needs no model
    f = tmp / "files" / "list.txt"
    REGISTRY["write_file"].run({"path": str(f), "content": "one"})
    REGISTRY["write_file"].run({"path": str(f), "content": "two"})
    assert agent.handle("undo that").text == "Undone: wrote list.txt. The earlier version is back." and f.read_text() == "one"
    assert agent.handle("Nova, undo").text == "Undone: wrote list.txt. The file is gone again." and not f.exists()
    REGISTRY["create_folder"].run({"path": str(tmp / "files" / "Quotes")})
    REGISTRY["move_path"].run({"source": str(pic), "destination": str(tmp / "files" / "Quotes")})
    assert (tmp / "files" / "Quotes" / "IMG_2041.jpg").exists()
    assert "It's back at" in REGISTRY["undo_last"].run({}) and pic.exists()
    assert REGISTRY["undo_last"].run({}) == "Undone: created the folder Quotes. The folder is removed."
    REGISTRY["remember"].run({"text": "My gate code changes monthly", "kind": "fact"})
    assert "gate code" in context.store.context_for("gate code")
    assert "forgotten it again" in REGISTRY["undo_last"].run({}) and "gate code" not in context.store.context_for("gate code")
    REGISTRY["add_expense"].run({"amount": 50, "merchant": "Spar"})
    assert "removed from your budget" in REGISTRY["undo_last"].run({}) and budget.find("spar") == {}
    REGISTRY["set_reminder"].run({"text": "call Sam", "when": "in 20 minutes"})
    assert "reminder is cancelled" in REGISTRY["undo_last"].run({}) and REGISTRY["list_reminders"].run({}) == "No reminders."
    assert REGISTRY["undo_last"].run({}) == "There's nothing recent to undo."


def test_undo_for_brain_notes_and_honesty_about_what_cannot_be_undone(home):
    cfg, tmp, _ = home
    src = taxonomy.folder_for(taxonomy.WORK, "04_Resources_&_Reference") / "Holiday_Plan.md"
    src.write_text("# Holiday plan\n\nDurban in December.", encoding="utf-8")
    moved = taxonomy.move_item(f"note:{src}", taxonomy.PERSONAL, "03_Interests_&_Projects")
    assert not src.exists()
    assert REGISTRY["undo_last"].run({}).endswith("The note is back where it was.") and src.exists()
    taxonomy.delete_item(f"note:{src}")
    assert not src.exists() and REGISTRY["undo_last"].run({}).endswith("The note is back in the brain.") and src.exists()
    assert moved["ok"]
    undo.record("sent Sam a WhatsApp message", "none", why="a sent message can't be unsent from here")
    assert "can't be undone" in REGISTRY["undo_list"].run({})
    assert REGISTRY["undo_last"].run({}) == ("I can't undo 'sent Sam a WhatsApp message' — a sent message can't be "
                                             "unsent from here.")


def test_witty_by_default_and_it_stays_until_changed(home, monkeypatch):
    cfg, _, _ = home
    from nova import settings
    monkeypatch.setattr(settings, "apply", lambda payload: {"saved": []})
    agent = Agent(cfg, context.llm)
    p = agent._system_prompt("voice", "hi")
    assert "quick, dry wit" in p and "occasional mild swear word" in p and "never aimed at" in p
    assert "out of anything written for other people" in p
    assert REGISTRY["set_personality"].run({"style": "witty", "swearing": "off"}).startswith("Done — from now on I'm witty")
    p = agent._system_prompt("voice", "hi")
    assert "quick, dry wit" in p and "No swearing." in p and "occasional mild swear" not in p
    REGISTRY["set_personality"].run({"style": "professional"})
    assert "businesslike and to the point. No jokes" in agent._system_prompt("voice", "hi")
    assert "witty, warm, professional or minimal" in REGISTRY["set_personality"].run({"style": "pirate"})
    assert settings.SCHEMA and any(f["path"] == "assistant.personality" and f["default"] == "witty"
                                   for sec in settings.SCHEMA for f in sec["fields"])


def test_weekly_report_from_what_actually_happened(home, monkeypatch):
    cfg, tmp, _ = home
    s = context.store
    work = taxonomy.folder_for(taxonomy.WORK, "03_Projects_&_Strategy", "Northwind_Refresh")
    done, stuck = work / "Northwind_Quote.md", work / "Northwind_Deal_Registration.md"
    for p in (done, stuck):
        p.write_text(f"# {p.stem}\n\ntext", encoding="utf-8")
    s.set_tracking(f"note:{done}", status="done")
    s.set_tracking(f"note:{stuck}", status="waiting", note="vendor approval")
    meet = taxonomy.folder_for(taxonomy.WORK, "05_Meetings_&_Reports", "Meetings") / "Northwind_QBR.md"
    meet.write_text("# QBR", encoding="utf-8")
    private = taxonomy.folder_for(taxonomy.PERSONAL, "01_Life_Admin") / "Car_Licence.md"
    private.write_text("# licence", encoding="utf-8")
    s.set_tracking(f"note:{private}", status="done")
    seen = []
    monkeypatch.setattr(context.llm, "complete", lambda prompt, **k: seen.append(prompt) or
                        "## What moved\n- Northwind quote done\n\n## Stuck or waiting\n- Deal registration\n\n## Next week\n- Nothing to report.")
    out = REGISTRY["weekly_report"].run({})
    facts = seen[0].split("FACTS")[1]
    assert "- Northwind Quote" in facts and "- Northwind Deal Registration — vendor approval" in facts and "- Northwind QBR" in facts
    assert "Car Licence" not in facts and "no jokes" in seen[0]                    # personal items stay out of it
    saved = taxonomy.vault() / "02_Work" / "05_Meetings_&_Reports" / "Weekly_Reports" / f"Weekly_Report_{dt.date.today():%Y-%m-%d}.md"
    assert saved.read_text().startswith("[LABEL: DOMAIN: WORK]\n[LABEL: STATUS: COMPLETED]") and "## Stuck or waiting" in saved.read_text()
    assert "saved under Work → Meetings & Reports" in out and "- Northwind quote done" in out
    monkeypatch.setattr(context.llm, "complete", lambda prompt, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    r = reports.weekly_report()                                                   # no model: the plain facts still go out
    assert "## What moved\n- Northwind Quote" in r["text"] and "vendor approval" in r["text"]


def test_reports_run_once_on_their_day(home, monkeypatch, tmp_path):
    monkeypatch.setattr(reports, "_state_path", lambda: tmp_path / "reports_state.json")
    pushed = []
    monkeypatch.setattr(context, "push", lambda text, files=None: pushed.append(text))
    monkeypatch.setattr(reports, "weekly_report", lambda now=None: {"title": "Weekly report", "text": "body", "path": "x"})
    fri, sun = dt.datetime(2026, 10, 2, 16, 5), dt.datetime(2026, 10, 4, 18, 30)
    assert reports.run_due(dt.datetime(2026, 10, 2, 15, 0)) == []                  # before 16:00
    assert reports.run_due(fri) == ["weekly"] and reports.run_due(fri) == []       # once
    assert reports.run_due(sun) == ["review"] and pushed[0].startswith("🗓 Weekly report is ready")
    assert pushed[1].startswith("🔎 My week with you")
    home[0]["reports"] = {"weekly": False}
    assert reports.run_due(dt.datetime(2026, 10, 9, 16, 5)) == []


def test_activity_review_counts_what_was_used_and_what_failed(home):
    cfg, _, _ = home
    s = context.store
    for said in ("what's the weather", "send the quote", "send it again"):
        t = s.begin_turn("voice", said)
        a = s.log("tool", "voice", "gmail send" if "send" in said else "get weather", status="running")
        s.finish(a, "error" if said == "send it again" else "ok", 900)
        s.end_turn(t, "ok", 2000)
    r = reports.activity_review()
    assert r["requests"] == 3 and r["by_session"] == {"voice": 3} and r["avg_seconds"] == 2.0
    assert ("gmail send", 2) in r["top_tools"] and r["failing"] == [("gmail send", 1, 2)]
    assert "phone" in r["unused_groups"] and "budget" in r["unused_groups"]
    text = REGISTRY["activity_review"].run({})
    assert text.startswith("Last 7 days: 3 requests (3 by voice), answered in 2.0 seconds on average.")
    assert "Not working well: gmail send failed 1 of 2 times." in text and "Not used in a month:" in text
