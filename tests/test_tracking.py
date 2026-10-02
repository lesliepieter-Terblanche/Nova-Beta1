"""v1.5: model auto-switching, per-request tracking, topics, skills and the busy-with card."""
import json
import time

import pytest

from nova import context
from nova.agent import Agent
from nova.llm import LLMReply, Provider, ToolCall, _model_missing, pick_model


# ── models ────────────────────────────────────────────────
def test_pick_model_skips_retired_and_non_chat_models():
    ids = ["whisper-large-v3", "meta-llama/llama-guard-4-12b", "openai/gpt-oss-120b", "llama-3.1-8b-instant",
           "playai-tts"]
    assert pick_model(ids, "groq", "llama-3.3-70b-versatile") == "openai/gpt-oss-120b"
    assert pick_model(ids + ["llama-3.3-70b-versatile"], "groq", "llama-3.3-70b-versatile") == "llama-3.3-70b-versatile"
    assert pick_model(["grok-3", "grok-4-fast-non-reasoning", "grok-2-image"], "xai", "auto") == "grok-4-fast-non-reasoning"
    assert pick_model(["models/gemini-2.5-flash", "models/embedding-001"], "gemini", "auto") == "gemini-2.5-flash"


def test_model_missing_detection():
    assert _model_missing(Exception("Error code: 404 - The model `llama-3.3-70b-versatile` does not exist or you "
                                    "do not have access to it. model_not_found"))
    assert _model_missing(Exception("The model `x` has been decommissioned and is no longer supported"))
    assert not _model_missing(Exception("Invalid API Key"))


def test_provider_switches_from_retired_model(monkeypatch):
    p = Provider("groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "gsk_test", 5)
    calls = []

    def fake_chat(messages, tools=None, temperature=0.3, on_delta=None):
        calls.append(p.model)
        if p.model == "llama-3.3-70b-versatile":
            raise Exception("Error code: 404 - model_not_found: does not exist")
        return LLMReply("OK", [], "groq")

    class Models:
        @staticmethod
        def list():
            return [type("M", (), {"id": i}) for i in ("whisper-large-v3", "openai/gpt-oss-120b")]

    monkeypatch.setattr(p, "_chat", fake_chat)
    monkeypatch.setattr(p.client, "models", Models)
    assert p.chat([{"role": "user", "content": "hi"}]).content == "OK"
    assert calls == ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"]
    assert p.switched_from == "llama-3.3-70b-versatile"


def test_wrong_key_box_is_explained(nova, monkeypatch):
    from nova import settings
    monkeypatch.setattr(settings, "read_env", lambda: {"GROQ_API_KEY": "xai-abc"})
    r = settings.run_test("groq")
    assert not r["ok"] and "xAI" in r["message"]


# ── turns & tracking ──────────────────────────────────────
def _turn_with_tool(cfg, tmp):
    f = tmp / "files" / "plan.txt"
    f.write_text("Q4 plan")
    context.llm.queue = [LLMReply("", [ToolCall("a", "read_file", {"path": str(f)})]), LLMReply("It's the Q4 plan.")]
    agent = Agent(cfg, context.llm)
    agent.handle("read plan.txt for me please", "dashboard")
    time.sleep(0.2)
    return agent


def test_every_request_becomes_a_tracked_turn(nova):
    cfg, tmp = nova
    _turn_with_tool(cfg, tmp)
    s = context.store
    turn = s.db.execute("SELECT * FROM activity WHERE kind='user'").fetchone()
    assert turn["turn"] == turn["id"] and turn["status"] == "done" and turn["ms"] is not None
    tool = s.db.execute("SELECT * FROM activity WHERE kind='tool'").fetchone()
    assert tool["turn"] == turn["id"] and tool["status"] == "ok" and tool["ms"] is not None
    assert "Q4 plan" in json.loads(tool["detail"])["result"]
    assert s.current_turn is None


def test_waiting_confirmation_is_tracked(nova):
    cfg, tmp = nova
    junk = tmp / "files" / "junk.txt"
    junk.write_text("x")
    agent = Agent(cfg, context.llm)
    context.llm.queue = [LLMReply("", [ToolCall("b", "delete_path", {"path": str(junk)})])]
    agent.handle("delete junk.txt", "t")
    s = context.store
    assert s.db.execute("SELECT status FROM activity WHERE kind='tool'").fetchone()["status"] == "waiting"
    context.llm.queue = [LLMReply("Okay, left it.")]
    agent.handle("no", "t")
    assert s.db.execute("SELECT status FROM activity WHERE kind='tool'").fetchone()["status"] == "declined"


def test_memory_links_to_its_turn_and_recalls_are_counted(nova):
    s = context.store
    tid = s.begin_turn("voice", "remember my wife is Anri")
    s.add_memory("Alex's wife is Anri", "person")
    s.end_turn(tid, "done", 10)
    t2 = s.begin_turn("voice", "who is Anri")
    s.context_for("who is Anri wife")
    s.end_turn(t2, "done", 10)
    m = s.db.execute("SELECT * FROM memories").fetchone()
    assert m["turn"] == tid
    assert s.db.execute("SELECT COUNT(*) FROM uses WHERE item=? AND turn=?", (f"memory:{m['id']}", t2)).fetchone()[0]


def test_tracking_set_clear_and_follows_corrections(nova):
    s = context.store
    s.add_memory("Nova project is a local voice agent", "project")
    mid = s.db.execute("SELECT id FROM memories").fetchone()["id"]
    s.set_tracking(f"memory:{mid}", status="doing", pinned=True, note="finish dashboard")
    assert s.get_tracking(f"memory:{mid}")["status"] == "doing"
    s.supersede(mid, "Nova project is a local-first voice agent with a 3D dashboard")
    new = s.db.execute("SELECT id FROM memories WHERE superseded_by IS NULL").fetchone()["id"]
    assert s.get_tracking(f"memory:{new}")["note"] == "finish dashboard"
    with pytest.raises(ValueError):
        s.set_tracking(f"memory:{new}", status="someday")
    s.set_tracking(f"memory:{new}", status="", pinned=False, note="")
    assert s.tracked() == []


# ── dashboard views ───────────────────────────────────────
def test_dashboard_item_topic_now_and_skills(nova):
    from nova.dashboard.server import Dashboard
    cfg, tmp = nova
    _turn_with_tool(cfg, tmp)
    s = context.store
    tid = s.db.execute("SELECT id FROM activity WHERE kind='user'").fetchone()["id"]
    s.current_turn = tid
    s.add_memory("Westcon QBR deck is a project for Juniper", "project")
    context.record("doc", "QBR deck", str(tmp / "files" / "plan.txt"))
    s.current_turn = None
    d = Dashboard(cfg, None)

    turn = d.item(f"turn:{tid}")
    assert turn["title"].startswith("read plan.txt") and turn["reply"] == "It's the Q4 plan."
    assert any(x["kind"] == "tool" and x["status"] == "ok" for x in turn["steps"])
    assert {m["kind"] for m in turn["made"]} >= {"project", "doc"}

    mid = s.db.execute("SELECT id FROM memories").fetchone()["id"]
    mem = d.item(f"memory:{mid}")
    assert mem["origin"]["id"] == f"turn:{tid}" and mem["timeline"][0]["what"]
    assert mem["tracking"]["status"] == ""

    s.set_tracking(f"memory:{mid}", status="doing")
    topic = d.topic("project")
    assert topic["total"] == 1 and topic["items"][0]["track"] == "doing"
    assert d.topic("actions")["items"][0]["tools"] == 1
    assert d.topic("tracked")["total"] == 1
    assert d.active_projects() == {"active": 1, "doing": 1, "done": 0}

    g = d.graph()
    assert any(n["kind"] == "action" for n in g["nodes"]) and any(n.get("track") == "doing" for n in g["nodes"])
    now = d.now()
    assert now["busy"] is False and now["turn"]["id"] == f"turn:{tid}" and now["doing"]

    sk = d.skills()
    web = next(x for x in sk["skills"] if x["name"] == "files")
    assert web["description"] and web["tools"] and sk["active"] >= 5
    assert next(x for x in sk["skills"] if x["name"] == "files")["uses"] == 1
    assert d.stats()["projects"]["active"] == 1
