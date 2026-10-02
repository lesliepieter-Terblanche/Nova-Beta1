"""Missions: schedules, a full run (plan → steps → report), safety, and the dashboard views."""
import datetime as dt
import json

import pytest

from nova import context
from nova.agent import Agent
from nova.llm import LLMReply, ToolCall
from nova.missions import Missions, describe, next_run, parse_schedule


@pytest.mark.parametrize("text,expect", [
    ("", {"type": "once"}),
    ("now", {"type": "once"}),
    ("every 6 hours", {"type": "interval", "minutes": 360}),
    ("hourly", {"type": "interval", "minutes": 60}),
    ("daily 07:30", {"type": "daily", "at": "07:30"}),
    ("every morning at 6am", {"type": "daily", "at": "06:00"}),
    ("weekdays 8:15", {"type": "weekly", "days": ["mon", "tue", "wed", "thu", "fri"], "at": "08:15"}),
    ("every Monday at 8", {"type": "weekly", "days": ["mon"], "at": "08:00"}),
    ("mon, thu 4pm", {"type": "weekly", "days": ["mon", "thu"], "at": "16:00"}),
    ("every friday 16h30", {"type": "weekly", "days": ["fri"], "at": "16:30"}),
])
def test_parse_schedule(text, expect):
    assert parse_schedule(text) == expect


def test_next_run_and_describe():
    tue = dt.datetime(2026, 9, 29, 20, 0)                     # a Tuesday evening
    assert next_run(parse_schedule("every monday 08:00"), tue) == dt.datetime(2026, 10, 5, 8, 0)
    assert next_run(parse_schedule("daily 07:30"), tue) == dt.datetime(2026, 9, 30, 7, 30)
    assert next_run(parse_schedule("weekdays 21:00"), tue) == dt.datetime(2026, 9, 29, 21, 0)
    assert next_run(parse_schedule("every 2 hours"), tue) == dt.datetime(2026, 9, 29, 22, 0)
    assert next_run(parse_schedule(""), tue) is None
    assert describe(parse_schedule("weekdays 8:15")) == "weekdays at 08:15"
    assert describe(parse_schedule("every 90 minutes")) == "every 90 min"


def _setup(nova, monkeypatch):
    cfg, tmp = nova
    agent = Agent(cfg, context.llm)
    monkeypatch.setattr(context, "agent", agent)
    pushed, said = [], []
    monkeypatch.setattr(context, "push", lambda text, files=None: pushed.append((text, files or [])))
    monkeypatch.setattr(context, "announce", said.append)
    plan = {"title": "Mist competitors SADC", "steps": [
        {"title": "Search news", "do": "Search the web for Mist competitor news in SADC this week"},
        {"title": "Tidy old notes", "do": "Delete the old competitor file"},
        {"title": "Collect findings", "do": "Summarise what was found"}]}
    report = "Aruba and Cisco Meraki both ran partner promos in Botswana.\n\n## Findings\n- Aruba: 20% off APs"

    def complete(prompt, system="", prefer_smart=True, temperature=0.5):
        if "planning a background mission" in prompt:
            return json.dumps(plan)
        if "Write the mission report" in prompt:
            complete.report_prompt = prompt
            return report
        return '{"memories": []}'
    monkeypatch.setattr(context.llm, "complete", complete)
    old = tmp / "files" / "old.txt"
    old.write_text("x")
    context.llm.queue = [
        LLMReply("Found: Aruba 20% promo in Botswana; Meraki partner event in Gaborone. https://example.com/a"),
        LLMReply("", [ToolCall("d1", "delete_path", {"path": str(old)})]),
        LLMReply("Aruba and Meraki are both pushing SADC partners this week."),
    ]
    return agent, pushed, said, complete, old


def test_mission_run_plans_works_reports_and_stays_safe(nova, monkeypatch):
    agent, pushed, said, complete, old = _setup(nova, monkeypatch)
    ms = Missions()
    ms.start = lambda: None                                  # no background scheduler in tests
    m = ms.create("Research what Juniper Mist competitors did in SADC this week and brief me", "every monday 08:00")
    assert m["status"] == "scheduled" and m["when"] == "Mon at 08:00"
    assert ms.due() == [m["id"]]                             # first run starts straight away
    out = ms.run(m["id"])
    assert out["status"] == "done"
    assert old.exists()                                      # the risky delete was NOT done…
    assert "Needs your OK" in out["steps"][1]["result"] and "delete path" in out["steps"][1]["result"]
    assert agent.waiting_session() is None                   # …and nothing is left hanging
    report = open(out["report"], encoding="utf-8").read()
    assert report.startswith("[LABEL: DOMAIN: WORK]\n[LABEL: STATUS: COMPLETED]\n\n# Mist competitors SADC") and "Aruba" in report and "Search news" in report
    assert out["summary"].startswith("Aruba and Cisco Meraki")
    assert pushed[-1][0].startswith("✅ Mission: Mist competitors SADC") and pushed[-1][1] == [out["report"]]
    assert said and said[-1].startswith("Mission Mist competitors SADC is done.")
    after = ms.get(m["id"])
    assert after["status"] == "scheduled" and after["runs"] == 1 and after["next_run"] > after["last_run"]
    assert ms.due() == []
    # mission steps don't pollute long-term memory
    assert context.store.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    # the note is searchable in the 2nd brain
    assert context.store.search_notes("Aruba promo Botswana", k=1)


def test_second_run_sees_previous_report(nova, monkeypatch):
    agent, pushed, said, complete, old = _setup(nova, monkeypatch)
    ms = Missions()
    ms.start = lambda: None
    m = ms.create("Research Mist competitors in SADC weekly", "every monday 08:00")
    ms.run(m["id"])
    context.llm.queue = [LLMReply("New: HPE bought Juniper deal news"), LLMReply("Nothing to delete"),
                         LLMReply("Summary")]
    ms.run(m["id"])
    assert "Previous run" in complete.report_prompt and "Aruba and Cisco Meraki" in complete.report_prompt
    assert len(ms.runs(m["id"])) == 2


def test_once_mission_ends_done_and_manage(nova, monkeypatch):
    _setup(nova, monkeypatch)
    ms = Missions()
    ms.start = lambda: None
    m = ms.create("Find 10 potential Avaya resellers in Botswana and put them in a note")
    ms.run(m["id"])
    assert ms.get(m["id"])["status"] == "done" and ms.get(m["id"])["next_run"] is None
    assert "Running mission" in ms.run_now(m["id"]) and ms.due() == [m["id"]]
    assert "Paused" in ms.pause(m["id"]) and ms.due() == []
    assert "Deleted" in ms.delete(m["id"]) and ms.all() == []


def test_failed_plan_is_reported(nova, monkeypatch):
    _setup(nova, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("All models failed")
    monkeypatch.setattr(context.llm, "complete", boom)
    ms = Missions()
    ms.start = lambda: None
    m = ms.create("Research something useful for the QBR next week")
    out = ms.run(m["id"])
    assert out["status"] == "failed" and ms.get(m["id"])["status"] == "failed"


def test_mission_tools_and_dashboard(nova, monkeypatch):
    from nova.dashboard.server import Dashboard
    from nova.missions import missions
    from nova.tools import REGISTRY, select_tools
    _setup(nova, monkeypatch)
    monkeypatch.setattr(missions(), "start", lambda: None)
    names = {t.name for t in select_tools("start a mission every monday to research competitors")}
    assert {"start_mission", "list_missions", "mission_report", "manage_mission"} <= names
    out = REGISTRY["start_mission"].run({"goal": "Research Mist competitors in SADC and brief me",
                                         "schedule": "every monday 8am"})
    assert out.startswith("Mission #1") and "Mon at 08:00" in out
    missions().run(1)
    assert "Aruba" in REGISTRY["mission_report"].run({"mission_id": 1})
    assert "#1 Mist competitors SADC — scheduled" in REGISTRY["list_missions"].run({})

    d = Dashboard(nova[0], None)
    it = d.item("mission:1")
    assert it["type"] == "mission" and it["plan"][0]["status"] == "ok" and it["runs"][0]["report"].startswith("note:")
    assert it["tracking"]["status"] == "" and it["timeline"]
    assert d.topic("mission")["items"][0]["id"] == "mission:1"
    assert any(n["id"] == "mission:1" for n in d.graph()["nodes"])
    assert d.now()["missions"] == []
    assert "Paused" in REGISTRY["manage_mission"].run({"mission_id": 1, "action": "pause"})
