"""The brain's filing rules: 01_Personal / 02_Work, numbered categories, three levels, clean names — and a
reorganisation that proposes first, asks when unsure, and never loses links, pins or projects."""
import json
from pathlib import Path

import pytest

from nova import context, taxonomy as tx
from nova.tools import REGISTRY


def answer_with(**kw):
    """A fake model that files by a few obvious words."""
    def complete(prompt, **_):
        if "File this note" not in prompt:
            return "summary"
        body = prompt.split("Title:")[-1].lower()
        title = body.split("\n")[0]
        for part in (title, body):                       # the title decides; the text only when the title doesn't
            for words, out in kw.get("rules", RULES):
                if any(w in part for w in words):
                    return json.dumps(out)
        return json.dumps({"domain": "work", "category": "04_Resources_&_Reference", "subfolder": "", "confidence": 0.4,
                           "reason": "not sure"})
    return complete


RULES = [
    (["xuv", "service the car"], {"domain": "personal", "category": "01_Life_Admin", "confidence": 0.95, "reason": "vehicle"}),
    (["gym", "blood pressure"], {"domain": "personal", "category": "02_Health_&_Fitness", "confidence": 0.9, "reason": "health"}),
    (["fishing"], {"domain": "personal", "category": "03_Interests_&_Projects", "subfolder": "Hobbies", "confidence": 0.9,
                   "reason": "hobby"}),
    (["axiz", "juniper"], {"domain": "work", "category": "02_Clients_&_Partners", "subfolder": "Active_Accounts",
                           "confidence": 0.92, "reason": "client"}),
    (["laptop"], {"domain": "work", "category": "04_Resources_&_Reference", "confidence": 0.5,
                  "reason": "could be personal or work"}),
]


def test_names_are_pascal_snake_case():
    assert tx.clean_name("vehicle maintenance log!") == "Vehicle_Maintenance_Log"
    assert tx.clean_name("Q4 QBR notes for the EX4400 deal") == "Q4_QBR_Notes_for_the_EX4400_Deal"
    assert tx.file_like("2026-09-12 Axiz mist pricing (2).md") == "2026-09-12_Axiz_Mist_Pricing_2.md"
    assert tx.file_like("2026-10-02.md") == "2026-10-02.md" and tx.file_like("2026-W40.md") == "2026-W40.md"
    assert tx.category_named("health") == ("01_Personal", "02_Health_&_Fitness")
    assert tx.category_named("Clients") == ("02_Work", "02_Clients_&_Partners")
    assert tx.category_named("nonsense") is None


def test_new_notes_are_filed_by_the_rules(nova):
    context.llm.complete = answer_with()
    v = Path(nova[0]["brain"]["vault_dir"])
    p = tx.place("Axiz Mist pricing", "Juniper quote for Axiz")
    assert p.relative_to(v).parts[:3] == ("02_Work", "02_Clients_&_Partners", "Active_Accounts")
    assert p.name.endswith("_Axiz_Mist_Pricing.md")
    p = tx.place("Service the car", "XUV500 60 000 km service", dated=False)
    assert p.relative_to(v).as_posix() == "01_Personal/01_Life_Admin/Service_the_Car.md"
    assert tx.place("Today", "", hint="journal", dated=False).parent.relative_to(v).as_posix() == "01_Personal/05_Journal"
    assert tx.place("x", "", hint="Finance").parent.name == "04_Finance_&_Budgets"
    # unsure → still filed, but a question is kept for the user
    p = tx.place("New laptop", "which laptop to buy")
    p.write_text("x")
    checks = tx.load_plan()["checks"]
    assert checks and checks[-1]["rel"] == p.relative_to(v).as_posix() and checks[-1]["confidence"] == 0.5
    # the agent's own note tool goes through the same door
    out = REGISTRY["write_note"].run({"title": "Gym plan", "content": "gym three times a week", "folder": "Notes"})
    assert "01_Personal/02_Health_&_Fitness" in out and (v / "01_Personal/02_Health_&_Fitness/Gym_Plan.md").exists()
    assert sorted(d.name for d in v.iterdir() if d.is_dir()) == ["01_Personal", "02_Work"]


@pytest.fixture()
def old_brain(nova):
    """A brain laid out the old way, with everything that must survive the move."""
    context.llm.complete = answer_with()
    v = Path(nova[0]["brain"]["vault_dir"])
    files = {
        "Inbox/2026-09-12 Axiz Mist pricing.md": "# Axiz Mist pricing\n\nJuniper quote for Axiz. See [[Fishing trip Vaal]] "
                                                 "and [[2026-09-30|that journal]].\n\n![card](Inbox/attachments/card 1.png)\n",
        "Inbox/attachments/card 1.png": "PNG",
        "Inbox/attachments/orphan.png": "PNG",
        "Notes/Fishing trip Vaal.md": "# Fishing trip\n\nfishing at the Vaal with [[2026-09-12 Axiz Mist pricing]] and "
                                      "[[Journal/2026-09-30]]\n\n![[permit scan.pdf]]\n[map](pics/vaal map.png)\n"
                                      "[site](https://example.com/a b)\n",
        "Notes/pics/vaal map.png": "PNG",
        "Inbox/attachments/permit scan.pdf": "PDF",
        "01_Personal/01_Life_Admin/Licence_Renewal.md": "# Licence\n\n![card](Inbox/attachments/card%201.png) and "
                                                        "[[New laptop]]\n",
        "Notes/New laptop.md": "# New laptop\n\nwhich laptop should I get\n",
        "Journal/2026-09-30.md": "# Journal\n\nGood day.\n",
        "Meetings/2026-09-29 Axiz QBR.md": "# Axiz QBR\n\nminutes\n",
        "Projects/Mist rollout/deep/Plan.md": "# Plan\n\naxiz rollout plan\n",
        "_Nova Memory.md": "# What Nova knows\n",
    }
    for rel, text in files.items():
        f = v / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    s = context.store
    s.sync_vault(v)
    note = str(v / "Inbox/2026-09-12 Axiz Mist pricing.md")
    s.set_tracking(f"note:{note}", status="doing", pinned=True, note="chase Sam")
    s.db.execute("CREATE TABLE IF NOT EXISTS links(item TEXT PRIMARY KEY, project TEXT)")
    s.db.execute("INSERT INTO links(item, project) VALUES(?, 'memory:1')", (f"note:{note}",))
    s.db.commit()
    return v, note


def test_plan_moves_nothing_and_asks_when_unsure(old_brain):
    v, note = old_brain
    before = sorted(p.relative_to(v).as_posix() for p in v.rglob("*") if p.is_file())
    plan = tx.build_plan()
    assert sorted(p.relative_to(v).as_posix() for p in v.rglob("*") if p.is_file()) == before      # nothing moved
    by = {i["rel"]: i for i in plan["items"]}
    assert by["Inbox/2026-09-12 Axiz Mist pricing.md"]["target"] == \
        "02_Work/02_Clients_&_Partners/Active_Accounts/2026-09-12_Axiz_Mist_Pricing.md"
    assert by["Journal/2026-09-30.md"]["target"] == "01_Personal/05_Journal/2026-09-30.md"
    assert by["Meetings/2026-09-29 Axiz QBR.md"]["target"] == "02_Work/05_Meetings_&_Reports/Meetings/2026-09-29_Axiz_QBR.md"
    assert by["_Nova Memory.md"]["target"] == "01_Personal/05_Journal/Nova_Memory.md"
    assert by["Projects/Mist rollout/deep/Plan.md"]["target"] == \
        "02_Work/03_Projects_&_Strategy/Mist_Rollout/Deep_Plan.md"                                # three deep, project kept
    q = by["Notes/New laptop.md"]
    assert q["question"] and not q["answered"] and q["confidence"] == 0.5
    s = tx.plan_summary()
    assert s["questions"] == 1 and s["moves"] == len(plan["items"]) and s["state"] == "ready"


def test_apply_keeps_links_pins_projects_and_attachments(old_brain):
    v, note = old_brain
    tx.build_plan()
    files_before = len([p for p in v.rglob("*") if p.is_file() and p.name != "Status_Board.md"])
    with pytest.raises(ValueError):
        tx.answer("Notes/New laptop.md", "01_Personal", "02_Clients_&_Partners")          # not a personal category
    assert tx.answer("Notes/New laptop.md", "01_Personal", "Life Admin")["target"] == "01_Personal/01_Life_Admin/New_Laptop.md"
    r = tx.apply_plan()
    assert r["moved"] >= 7 and r["left"] == 0 and Path(r["backup"]).is_dir()
    assert (Path(r["backup"]) / "Inbox/2026-09-12 Axiz Mist pricing.md").exists()                  # backup has the old layout
    assert sorted(d.name for d in v.iterdir()) == ["01_Personal", "02_Work"]                       # rule 1
    assert tx.audit() == []                                                                       # rules 2-4
    new = v / "02_Work/02_Clients_&_Partners/Active_Accounts/2026-09-12_Axiz_Mist_Pricing.md"
    text = new.read_text()
    # links between notes follow the renames, with the readable words kept
    assert "[[Fishing_Trip_Vaal|Fishing trip Vaal]]" in text and "[[2026-09-30|that journal]]" in text
    fish = (v / "01_Personal/03_Interests_&_Projects/Hobbies/Fishing_Trip_Vaal.md").read_text()
    assert "[[2026-09-12_Axiz_Mist_Pricing|2026-09-12 Axiz Mist pricing]]" in fish
    # the picture moved to the category's Attachments and the note still points at it
    assert "](02_Work/02_Clients_&_Partners/Attachments/Card_1.png)" in text
    assert (v / "02_Work/02_Clients_&_Partners/Attachments/Card_1.png").exists()
    assert (v / "02_Work/04_Resources_&_Reference/Attachments/Orphan.png").exists()
    # every other way of linking follows too: embeds, links relative to the note, [[Folder/Note]], web links untouched
    hobby = v / "01_Personal/03_Interests_&_Projects"
    assert "![[Permit_Scan.pdf]]" in fish and (hobby / "Attachments/Permit_Scan.pdf").exists()
    assert "[map](01_Personal/03_Interests_&_Projects/Attachments/Vaal_Map.png)" in fish
    assert (hobby / "Attachments/Vaal_Map.png").exists()
    assert "[[2026-09-30]]" in fish and "[site](https://example.com/a b)" in fish
    # a note that was already in the right place still finds the picture and the renamed note
    lic = (v / "01_Personal/01_Life_Admin/Licence_Renewal.md").read_text()
    assert "](02_Work/02_Clients_&_Partners/Attachments/Card_1.png)" in lic and "[[New_Laptop|New laptop]]" in lic
    # nothing was lost: every file that existed before is still in the vault (or was a duplicate name → _2)
    assert len([p for p in v.rglob("*") if p.is_file() and p.name != "Status_Board.md"]) == files_before
    assert (v / "01_Personal/01_Life_Admin/New_Laptop.md").exists()
    # pins, status, project link and the search index point at the new path
    s = context.store
    t = s.get_tracking(f"note:{new}")
    assert t["status"] == "doing" and t["pinned"] and t["note"] == "chase Sam"
    assert s.db.execute("SELECT project FROM links WHERE item=?", (f"note:{new}",)).fetchone()[0] == "memory:1"
    paths = {r["path"] for r in s.db.execute("SELECT DISTINCT path FROM chunks")}
    assert str(new) in paths and note not in paths
    assert tx.build_plan()["state"] == "clean"


def test_unanswered_questions_stay_put(old_brain):
    v, _ = old_brain
    tx.build_plan()
    r = tx.apply_plan()
    assert r["left"] == 1 and (v / "Notes/New laptop.md").exists()                # waited for the user
    r = tx.apply_plan(include_unanswered=True)
    assert r["left"] == 0 and (v / "02_Work/04_Resources_&_Reference/New_Laptop.md").exists()


def test_fixing_a_recent_best_guess_moves_the_note(nova):
    context.llm.complete = answer_with()
    v = Path(nova[0]["brain"]["vault_dir"])
    p = tx.place("New laptop", "which laptop to buy", dated=False)
    p.write_text("# New laptop\n")
    rel = p.relative_to(v).as_posix()
    out = tx.answer(rel, "01_Personal", "04_Finance_&_Budgets")
    assert out["target"] == "01_Personal/04_Finance_&_Budgets/New_Laptop.md" and (v / out["target"]).exists()
    assert not p.exists() and tx.load_plan()["checks"] == []


def test_search_can_stay_on_one_side_of_the_brain(old_brain):
    v, _ = old_brain
    tx.build_plan()
    tx.apply_plan(include_unanswered=True)
    work = context.store.search_notes("axiz", domain="02_Work", min_score=0.01)
    personal = context.store.search_notes("fishing vaal", domain="01_Personal", min_score=0.01)
    assert work and all("/02_Work/" in c["path"] for _, c in work)
    assert personal and all("/01_Personal/" in c["path"] for _, c in personal)


def test_startup_prepares_the_plan_and_tells_you(old_brain, monkeypatch):
    said = []
    monkeypatch.setattr(context, "push", lambda text, files=None: said.append(text))
    tx.startup_check()
    assert said and "Nothing has been moved yet" in said[0] and "1 question" in said[0]
    said.clear()
    tx.startup_check()                                    # plan already waiting: no nagging
    assert said == []
    assert "Waiting for approval" in REGISTRY["brain_structure"].run({})


# ── [LABEL: DOMAIN: …] / [LABEL: STATUS: …] ───────────────
def test_every_note_carries_its_domain_and_status(nova):
    context.llm.complete = answer_with()
    v = Path(nova[0]["brain"]["vault_dir"])
    out = REGISTRY["write_note"].run({"title": "Axiz renewal brief", "content": "Juniper renewal for Axiz"})
    f = next((v / "02_Work").rglob("*Axiz_Renewal_Brief.md"))
    lines = f.read_text().splitlines()
    assert lines[0] == "[LABEL: DOMAIN: WORK]" and lines[1].startswith("[LABEL: STATUS: ") and out
    assert lines[1][len("[LABEL: STATUS: "):-1] in tx.STATUSES
    s = context.store
    # the dashboard's status and the label in the file are one and the same
    s.set_tracking(f"note:{f}", status="waiting", note="need Sam's numbers")
    assert f.read_text().splitlines()[:2] == ["[LABEL: DOMAIN: WORK]", "[LABEL: STATUS: WAITING-ON-USER]"]
    s.set_tracking(f"note:{f}", status="done")
    assert "[LABEL: STATUS: COMPLETED]" in f.read_text() and f.read_text().count("[LABEL: STATUS:") == 1
    # edit the label by hand (Obsidian) → Nova follows the file
    f.write_text(f.read_text().replace("STATUS: COMPLETED", "STATUS: BACKLOG"))
    s.index_note(f)
    assert s.get_tracking(f"note:{f}")["status"] == "todo"
    # a personal note says PERSONAL; indexing twice doesn't stack headers
    g = tx.place("Gym plan", "gym three times a week", dated=False)
    g.write_text("# Gym plan\n\ngym\n")
    s.index_note(g)
    s.index_note(g)
    assert g.read_text().startswith("[LABEL: DOMAIN: PERSONAL]\n[LABEL: STATUS: ") and g.read_text().count("[LABEL:") == 2
    assert "# Gym plan" in g.read_text()


def test_status_boards_show_what_waits_on_you(nova):
    context.llm.complete = answer_with()
    v = Path(nova[0]["brain"]["vault_dir"])
    s = context.store
    a = tx.place("Axiz renewal", "Juniper renewal for Axiz", dated=False)
    a.write_text("# Axiz renewal\n")
    s.index_note(a)
    s.set_tracking(f"note:{a}", status="waiting", note="need the PO number")
    b = tx.place("Gym plan", "gym", dated=False)
    b.write_text("# Gym plan\n")
    s.index_note(b)
    s.set_tracking(f"note:{b}", status="doing")
    tx.write_status_boards()
    work = (v / "02_Work/05_Meetings_&_Reports/Status_Board.md").read_text()
    home = (v / "01_Personal/05_Journal/Status_Board.md").read_text()
    assert work.splitlines()[0] == "[LABEL: DOMAIN: WORK]" and home.splitlines()[0] == "[LABEL: DOMAIN: PERSONAL]"
    top = work.index("[LABEL: STATUS: WAITING-ON-USER]")
    assert "[[Axiz_Renewal]] — need the PO number" in work[top:] and "Axiz_Renewal" not in home
    assert "[[Gym_Plan]]" in home[home.index("[LABEL: STATUS: IN-PROGRESS]"):]
    assert all(f"[LABEL: STATUS: {st}]" in work for st in tx.STATUSES)
    assert tx.audit() == []                                 # the boards themselves obey the rules


# ── the dashboard's master index gets its data from here ──
def test_index_data_has_domain_status_and_bottlenecks(nova):
    from nova.dashboard.server import Dashboard
    context.llm.complete = answer_with()
    s = context.store
    a = tx.place("Axiz renewal", "Juniper renewal for Axiz", dated=False)
    a.write_text("# Axiz renewal\n")
    s.index_note(a)
    b = tx.place("Gym plan", "gym", dated=False)
    b.write_text("# Gym plan\n")
    s.index_note(b)
    d = Dashboard(nova[0], None)
    rev = d.graph_rev()
    s.set_tracking(f"note:{a}", status="waiting", note="need the PO number")
    assert d.graph_rev() != rev                                   # a status change makes the dashboard redraw
    g = d.graph()
    by = {n["id"]: n for n in g["nodes"]}
    work, home = by[f"note:{a}"], by[f"note:{b}"]
    assert work["domain"] == "02_Work" and work["status"] == "WAITING-ON-USER" and work["filed"]
    assert work["folder"] == "02_Work/02_Clients_&_Partners/Active_Accounts"
    assert home["domain"] == "01_Personal" and home["status"] == "COMPLETED"
    assert list(g["tree"]) == ["01_Personal", "02_Work"] and g["tree"]["02_Work"][0] == "01_Role_&_Responsibilities"
    assert all(n.get("domain") in g["tree"] for n in g["nodes"] if n["kind"] != "core")       # nothing without a domain
    assert g["bottlenecks"] == [{"id": f"note:{a}", "text": "Axiz Renewal", "why": "need the PO number", "domain": "02_Work"}]
    assert g["audit"]["moves"] == 0
    assert d.topic("cat:02_Work/02_Clients_&_Partners")["total"] == 1
    # the short form of the labels is understood too
    b.write_text("[DOMAIN: PERSONAL]\n[STATUS: IN-PROGRESS]\n\n# Gym plan\n")
    s.index_note(b)
    assert s.get_tracking(f"note:{b}")["status"] == "doing" and b.read_text().startswith("[LABEL: DOMAIN: PERSONAL]\n[LABEL: STATUS: IN-PROGRESS]")


# ── right-click on the dashboard: move to the other side / another category, or delete ──
def test_move_and_delete_from_the_dashboard(old_brain):
    from nova.dashboard.server import Dashboard
    v, note = old_brain
    s = context.store
    tx.build_plan()
    # a note that is still in the old layout goes straight where you say — links and its pin follow
    r = tx.move_item(f"note:{note}", "01_Personal", "04_Finance_&_Budgets")
    new = v / "01_Personal/04_Finance_&_Budgets/2026-09-12_Axiz_Mist_Pricing.md"
    assert r["where"] == new.relative_to(v).as_posix() and new.exists() and not Path(note).exists()
    assert new.read_text().startswith("[LABEL: DOMAIN: PERSONAL]") and s.get_tracking(f"note:{new}")["pinned"]
    assert "[[2026-09-12_Axiz_Mist_Pricing|" in (v / "Notes/Fishing trip Vaal.md").read_text()
    assert all(i["rel"] != "Inbox/2026-09-12 Axiz Mist pricing.md" for i in tx.load_plan()["items"])
    # only the side chosen: it keeps the same kind of category on the other side
    r = tx.move_item(f"note:{new}", "02_Work")
    assert r["where"].startswith("02_Work/04_Resources_&_Reference/") and (v / r["where"]).exists()
    with pytest.raises(ValueError):
        tx.move_item(r["id"], "02_Work", "02_Health_&_Fitness")
    # things that aren't files remember where you put them
    s.add_memory("TrueHome is a property platform project", "project")
    mid = f"memory:{s.db.execute('SELECT id FROM memories').fetchone()[0]}"
    tx.move_item(mid, "01_Personal", "Interests")
    d = Dashboard(context.cfg, None)
    node = next(n for n in d.graph()["nodes"] if n["id"] == mid)
    assert node["domain"] == "01_Personal" and node["cat"] == "03_Interests_&_Projects"
    # delete: the note leaves the brain but the file is kept; the memory is retired
    out = tx.delete_item(r["id"])
    assert not (v / r["where"]).exists() and Path(out["kept"]).exists() and "deleted" in out["kept"]
    assert not s.db.execute("SELECT 1 FROM chunks WHERE path=?", (str(v / r["where"]),)).fetchone()
    tx.delete_item(mid)
    assert all(n["id"] != mid for n in d.graph()["nodes"])
    with pytest.raises(ValueError):
        tx.delete_item("artifact:1")
