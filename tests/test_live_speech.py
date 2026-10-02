"""Live speech: the voice starts with the first sentence, while the rest of the answer is still being written."""
import json
import threading
import time
import urllib.request
from types import SimpleNamespace as NS

import pytest

from nova import context
from nova.agent import Agent
from nova.live_speech import LiveSpeaker, SentenceFeed
from nova.llm import LLMReply, Provider, ToolCall


def drain(feed):
    out = []
    while not feed.q.empty():
        out.append(feed.q.get())
    return out


def test_sentences_come_out_as_soon_as_they_are_complete():
    f = SentenceFeed(limit=200)
    for piece in ["You have ", "a QBR at ten", " with Northwind. ", "After that the R48.5 million ", "forecast call"]:
        f(piece)
    assert drain(f) == ["You have a QBR at ten with Northwind."]              # the first sentence didn't wait for the rest
    f(" is at two.\n- **Bring** the deck")
    assert drain(f) == ["After that the R48.5 million forecast call is at two."]      # "R48.5" isn't a sentence end
    f.finish("ignored: something was already said")
    assert drain(f) == ["Bring the deck", None]                           # the tail, cleaned of markdown, then the end
    # long answers: the voice stops at the limit, the rest is on screen
    g = SentenceFeed(limit=40)
    g("First sentence is here. Second sentence is right here. Third one too. ")
    g.finish()
    assert drain(g) == ["First sentence is here.", None] and g.trimmed
    # a tool call written out as text is never read aloud; with nothing streamed the final answer is spoken
    h = SentenceFeed()
    h('{"name": "get_weather", "arguments": {}} ')
    h.finish("It's 24 degrees and sunny.")
    assert drain(h) == ["It's 24 degrees and sunny.", None]
    assert list(SentenceFeed().batches(timeout=0.05)) == []


def test_batches_join_what_piled_up_while_the_voice_was_busy():
    f = SentenceFeed()
    f("One is done. Two is done. ")
    it = f.batches(timeout=1)
    assert next(it) == "One is done. Two is done."
    f("Three is done. ")
    f.finish()
    assert list(it) == ["Three is done."]


def chunk(content=None, tool=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=[tool] if tool else None))])


def tool(i, cid="", name="", args=""):
    return NS(index=i, id=cid, function=NS(name=name, arguments=args))


class FakeClient:
    def __init__(self, chunks):
        self.chunks = chunks
        self.chat = NS(completions=NS(create=self.create))
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        if not kw.get("stream"):
            return NS(choices=[NS(message=NS(content="Plain answer.", tool_calls=None))])
        if isinstance(self.chunks, Exception):
            raise self.chunks
        return iter(self.chunks)


def provider(chunks):
    p = Provider("fake", "http://localhost:1/v1", "m", "k", 5)
    p.client = FakeClient(chunks)
    return p


def test_streamed_reply_passes_text_on_and_assembles_tool_calls():
    said = []
    p = provider([chunk("It's "), chunk("24 degrees"), chunk(" and sunny.")])
    r = p.chat([{"role": "user", "content": "weather"}], None, 0.3, said.append)
    assert r.content == "It's 24 degrees and sunny." and "".join(said) == r.content and len(said) >= 2
    # a tool call arrives in pieces; nothing is spoken for it
    said.clear()
    schema = [{"type": "function", "function": {"name": "get_weather", "parameters": {}}}]
    p = provider([chunk(tool=tool(0, "c1", "get_weather", '{"ci')), chunk(tool=tool(0, args='ty": "Joburg"}'))])
    r = p.chat([], schema, 0.3, said.append)
    assert r.tool_calls == [ToolCall("c1", "get_weather", {"city": "Joburg"})] and said == []
    # a local model that writes the tool call as text: salvaged, not spoken
    p = provider([chunk('{"name": "get_weather", '), chunk('"arguments": {"city": "Joburg"}}')])
    r = p.chat([], schema, 0.3, said.append)
    assert r.tool_calls and r.tool_calls[0].name == "get_weather" and said == []
    # a provider that can't stream: answered in one piece, and it isn't tried again
    p = provider(RuntimeError("stream not supported"))
    assert p.chat([], None, 0.3, said.append).content == "Plain answer." and p.no_stream
    assert p.chat([], None, 0.3, said.append).content == "Plain answer." and len(p.client.calls) == 3


class Recorder:
    def __init__(self):
        self.said, self.times = [], []

    def speak(self, text):
        self.said.append(text)
        self.times.append(time.perf_counter())
        return True

    def stop(self):
        pass


def test_the_voice_starts_before_the_answer_is_finished(nova):
    agent = Agent(nova[0], context.llm)
    rec = Recorder()
    done_at = {}

    def slow(messages):                       # the model takes a while over the second sentence
        return LLMReply("")

    class SlowLLM:
        smart = ["fake"]

        def should_escalate(self, text):
            return False

        def chat(self, messages, tools=None, prefer_smart=False, temperature=0.3, on_delta=None):
            on_delta("Your next meeting is the Northwind QBR at ten. ")
            time.sleep(0.4)
            on_delta("Sam asked you to bring the Mist pricing.")
            done_at["t"] = time.perf_counter()
            return LLMReply("Your next meeting is the Northwind QBR at ten. Sam asked you to bring the Mist pricing.")

        def complete(self, *a, **k):
            return '{"memories": []}'

    agent.llm = SlowLLM()
    sp = LiveSpeaker(rec, 450)
    reply = agent.handle("what's next?", "voice", on_delta=sp.feed)
    sp.finish(reply.text)
    sp.wait(3)
    assert rec.said == ["Your next meeting is the Northwind QBR at ten.", "Sam asked you to bring the Mist pricing."]
    assert rec.times[0] < done_at["t"] - 0.25                 # she was already talking while the model was still writing
    assert reply.text.endswith("Mist pricing.") and slow


def test_without_live_speech_nothing_changes(nova):
    agent = Agent(nova[0], context.llm)
    context.llm.queue = [LLMReply("All done.")]
    assert agent.handle("hi", "text").text == "All done."


@pytest.fixture()
def dash(nova):
    from nova.dashboard.server import Dashboard
    cfg = nova[0]
    cfg["dashboard"]["open_on_start"] = False
    cfg["dashboard"]["port"] = 8789
    d = Dashboard(cfg, Agent(cfg, context.llm))
    d.start()
    return d


def test_phone_hears_the_answer_while_it_is_written(dash, nova):
    sp = context.speech
    sp.el_key = "k"
    sp.engine_order = lambda: ["elevenlabs"]
    sp.elevenlabs_mp3_stream = lambda text: iter([b"<" + text.encode() + b">"])
    ua = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/129 Mobile"}
    with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8789/api/stats", headers=ua), timeout=10) as r:
        assert json.loads(r.read())["live_voice"] is True
    token = "ab" * 16
    got = {}

    def listen():                              # the phone asks for the audio first…
        req = urllib.request.Request(f"http://127.0.0.1:8789/api/tts/{token}", headers=ua)
        with urllib.request.urlopen(req, timeout=20) as a:
            got["type"], got["body"] = a.headers["Content-Type"], a.read()

    t = threading.Thread(target=listen)
    t.start()
    time.sleep(0.2)
    context.llm.queue = [LLMReply("Sam wants the Mist quote today. He also asked for the EX4400 lead time.")]
    req = urllib.request.Request("http://127.0.0.1:8789/api/ask", method="POST",
                                 data=json.dumps({"text": "what did Sam want?", "voice": True, "live": token}).encode(),
                                 headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:8789"})
    with urllib.request.urlopen(req, timeout=20) as r:      # …then sends the question
        out = json.loads(r.read())
    t.join(10)
    assert out["live"] is True and out["audio"].startswith("/api/tts/")           # a finished file as a fallback
    assert got["type"] == "audio/mpeg"
    spoken = got["body"].decode().replace("><", " ").strip("<>")
    assert spoken == "Sam wants the Mist quote today. He also asked for the EX4400 lead time."
    # an iPhone can't play a live stream: no live voice offered, and a made-up token gives nothing
    with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8789/api/stats",
                                headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Safari/604.1"}), timeout=10) as r:
        assert json.loads(r.read())["live_voice"] is False
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:8789/api/tts/{'cd' * 16}", headers=ua), timeout=15)
