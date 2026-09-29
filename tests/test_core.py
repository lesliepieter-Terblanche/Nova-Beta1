import os

from nova import context
from nova.agent import Agent
from nova.llm import LLMReply, ToolCall, _salvage_tool_call
from nova.tools import REGISTRY, select_tools


def test_plain_reply(nova):
    cfg, _ = nova
    context.llm.queue = [LLMReply("Hello.")]
    assert Agent(cfg, context.llm).handle("hi", "t").text == "Hello."


def test_tool_call_roundtrip(nova):
    cfg, tmp = nova
    f = tmp / "files" / "report.txt"
    f.write_text("pipeline R4.2m")
    context.llm.queue = [LLMReply("", [ToolCall("a", "read_file", {"path": str(f)})]),
                         lambda m: LLMReply("It says " + m[-1]["content"])]
    assert Agent(cfg, context.llm).handle("read the file report.txt", "t").text == "It says pipeline R4.2m"


def test_confirmation_yes_and_no(nova):
    cfg, tmp = nova
    agent = Agent(cfg, context.llm)
    junk = tmp / "files" / "junk.txt"
    junk.write_text("x")
    context.llm.queue = [LLMReply("", [ToolCall("b", "delete_path", {"path": str(junk)})])]
    assert "Shall I go ahead" in agent.handle("delete junk.txt", "t").text
    context.llm.queue = [LLMReply("Okay, left it.")]
    assert agent.handle("no", "t").text == "Okay, left it."
    assert junk.exists()


def test_files_are_sandboxed(nova):
    outside = "C:/Windows/win.ini" if os.name == "nt" else "/etc/hosts"
    out = REGISTRY["read_file"].run({"path": outside})
    assert "ERROR" in out and "outside" in out


def test_tool_selection_by_keywords(nova):
    assert "google" in {t.group for t in select_tools("check my email")}
    assert "camera" in {t.group for t in select_tools("take a photo of this and make an ad")}
    assert {t.group for t in select_tools("hello there")} >= {"system", "brain", "web"}


def test_salvage_json_tool_call():
    calls = _salvage_tool_call('{"name": "get_time", "arguments": {}}', {"get_time"})
    assert calls and calls[0].name == "get_time"


def test_every_tool_has_a_description(nova):
    assert all(t.description for t in REGISTRY.values())


def test_telegram_stale_messages_are_not_acted_on():
    import datetime as dt

    from nova.telegram_bot import TelegramBot
    now = dt.datetime.now(dt.timezone.utc)
    assert not TelegramBot.is_stale(now - dt.timedelta(minutes=5))
    assert TelegramBot.is_stale(now - dt.timedelta(hours=2))
    assert not TelegramBot.is_stale((now - dt.timedelta(minutes=1)).replace(tzinfo=None))
