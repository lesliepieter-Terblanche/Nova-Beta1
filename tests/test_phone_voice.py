"""Voice from the phone (Tailscale) and the 'ask me before…' policy."""
import json
import urllib.request

import pytest

from nova import context
from nova.agent import Agent, needs_yes
from nova.llm import LLMReply, ToolCall
from nova.tools import REGISTRY


# ── spoken replies for the device you're using ────────────
def test_reply_audio_is_a_small_mp3_and_expires(nova, monkeypatch):
    from nova import phone_voice
    url = phone_voice.reply_audio("Your next meeting is at ten with Axiz.")
    assert url.startswith("/api/audio/") and url.endswith(".mp3")
    f = phone_voice.audio_file(url.rsplit("/", 1)[-1])
    assert f and f.read_bytes()[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    assert phone_voice.audio_file("../../config.yaml") is None and phone_voice.audio_file("x.mp3") is None
    monkeypatch.setattr(phone_voice, "KEEP_MINUTES", -1)
    phone_voice.reply_audio("again")
    assert not f.exists()                                        # old replies are tidied away


def test_phone_conversation_turn(nova, monkeypatch):
    from nova import phone_voice
    got = {}

    def transcribe(path):
        got["path"] = path
        return "What's on my calendar? Thanks, bye"
    context.speech.transcribe = transcribe
    agent = Agent(nova[0], context.llm)
    context.llm.queue = [LLMReply("You have a QBR at ten.")]
    r = phone_voice.converse(agent, b"\x1aE\xdf\xa3 not really webm", "audio/webm;codecs=opus")
    assert r["heard"].startswith("What's on my calendar") and r["text"] == "You have a QBR at ten."
    assert r["audio"].startswith("/api/audio/") and r["end"] is True          # "bye" ends the hands-free loop
    context.speech.transcribe = lambda p: ""
    assert phone_voice.converse(agent, b"...", "audio/mp4")["heard"] == ""
    assert "too long" in phone_voice.converse(agent, b"x" * (phone_voice.MAX_BYTES + 1), "audio/mp4")["error"]


@pytest.fixture()
def dash(nova):
    from nova.dashboard.server import Dashboard
    cfg = nova[0]
    cfg["dashboard"]["open_on_start"] = False
    cfg["dashboard"]["port"] = 8797
    d = Dashboard(cfg, Agent(cfg, context.llm))
    d.start()
    return d


def _post(path, data, ctype):
    req = urllib.request.Request(f"http://127.0.0.1:8797{path}", data=data, method="POST",
                                 headers={"Content-Type": ctype, "Origin": "http://127.0.0.1:8797"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def test_dashboard_voice_endpoints(dash, nova):
    context.speech.transcribe = lambda p: "remind me what Sam wanted"
    context.llm.queue = [LLMReply("Sam wants the Mist quote."), LLMReply("Typed answer.")]
    r = _post("/api/voice", b"fake recording", "audio/mp4")
    assert r["heard"] == "remind me what Sam wanted" and r["text"] == "Sam wants the Mist quote." and r["audio"]
    with urllib.request.urlopen(f"http://127.0.0.1:8797{r['audio']}", timeout=10) as a:
        assert a.headers["Content-Type"] == "audio/mpeg" and len(a.read()) > 500
    r = _post("/api/ask", json.dumps({"text": "hi", "voice": True}).encode(), "application/json")
    assert r["text"] == "Typed answer." and r["audio"].endswith(".mp3")
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen("http://127.0.0.1:8797/api/audio/..%2F..%2Fconfig.yaml", timeout=5)


# ── ask me before… ────────────────────────────────────────
def test_commands_just_run_but_irreversible_things_still_ask(nova):
    t = {n: REGISTRY[n] for n in ("run_shell", "move_path", "delete_path", "gmail_send", "restart_nova")}
    assert not needs_yes(t["run_shell"], {"command": "winget install VideoLAN.VLC"})
    assert not needs_yes(t["move_path"], {}) and not needs_yes(t["restart_nova"], {})
    assert needs_yes(t["delete_path"], {}) and needs_yes(t["gmail_send"], {})
    assert needs_yes(t["run_shell"], {"command": "Remove-Item C:\\Users\\Pieter\\Documents -Recurse -Force"})
    assert needs_yes(t["run_shell"], {"command": "format D: /q"})
    nova[0]["system"]["confirm"] = "all"
    assert needs_yes(t["run_shell"], {"command": "dir"}) and needs_yes(t["move_path"], {})
    nova[0]["system"]["confirm"] = "never"
    assert not needs_yes(t["delete_path"], {}) and needs_yes(t["run_shell"], {"command": "diskpart"})


def test_agent_runs_a_command_without_asking(nova, monkeypatch):
    import nova.skills.system as system
    ran = []

    class P:
        returncode, stdout, stderr = 0, "VLC installed", ""
    monkeypatch.setattr(system.subprocess, "run", lambda args, **kw: ran.append(args) or P())
    agent = Agent(nova[0], context.llm)
    context.llm.queue = [LLMReply("", [ToolCall("c1", "run_shell", {"command": "winget install VideoLAN.VLC"})]),
                         LLMReply("Done — VLC is installed.")]
    out = agent.handle("install vlc", "voice").text
    assert out == "Done — VLC is installed." and ran and agent.waiting_session() is None
