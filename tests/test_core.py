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


def test_second_brain_items_are_indexed_once_the_embedding_model_is_back(nova, monkeypatch):
    """v2.38.3: what was saved without nomic-embed-text gets its meaning-index afterwards."""
    import numpy as np
    s = context.store
    assert s.embed_model == "nomic-embed-text"
    monkeypatch.setattr(s, "embed", lambda texts: [None] * len(texts))            # the model isn't running
    s.add_memory("The boat is moored at Harbour Bay")
    s.add_artifact("file", "Quote for Dune Realty", "workspace/quote.pdf", "listing package")
    with s.lock:
        s.db.execute("INSERT INTO chunks(path,idx,text,mtime,embedding) VALUES('n.md',0,'tide tables for the estuary',0,?)",
                     (np.ones(3, dtype=np.float32).tobytes(),))                   # indexed by some other model
        s.db.commit()
    assert s.backfill() == 0                                                      # still not running: nothing lost
    asked = []
    monkeypatch.setattr(s, "embed", lambda texts: asked.extend(texts) or [np.ones(8, dtype=np.float32)] * len(texts))
    assert s.backfill() == 3 and s.backfill() == 0
    assert "The boat is moored at Harbour Bay" in asked and "Quote for Dune Realty listing package" in asked
    with s.lock:
        sizes = [r[0] for t in ("memories", "chunks", "artifacts") for r in s.db.execute(f"SELECT length(embedding) FROM {t}")]
    assert sizes == [32, 32, 32]
