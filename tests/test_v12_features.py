"""Tests for v1.2 features: voice engines, captions/B-roll, meetings, search, vision fallback, barge-in."""
import threading
import time
import wave

import numpy as np

from nova import context, ffmpeg


# ── voice engines ─────────────────────────────────────────
def test_engine_order_skips_unavailable(nova, monkeypatch):
    from nova.speech import Speech
    cfg, _ = nova
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    s = Speech(cfg)
    s.el_key = ""
    monkeypatch.setattr(s, "_available", lambda e: e in ("kokoro", "windows"))
    assert s.engine_order() == ["kokoro", "windows"]
    s.el_key = "x"
    monkeypatch.setattr(s, "_available", lambda e: True)
    assert s.engine_order()[0] == "elevenlabs"


def test_split_sentences_starts_short():
    from nova.speech import split_sentences
    parts = split_sentences("Hi. Your first meeting is at ten. Then lunch with Sam at one, and a call at three.")
    assert parts[0] == "Hi. Your first meeting is at ten."
    assert "".join(parts).replace(" ", "") == "Hi.Yourfirstmeetingisatten.ThenlunchwithSamatone,andacallatthree."


def test_synth_falls_back(nova, monkeypatch):
    from nova.speech import Speech
    s = Speech(nova[0])
    monkeypatch.setattr(s, "_synth_kokoro", lambda t: (_ for _ in ()).throw(RuntimeError("no model")))
    monkeypatch.setattr(s, "_synth_windows", lambda t: (np.zeros(100, np.int16), 16000))
    pcm, rate, engine = s.synth("hello", engines=["kokoro", "windows"])
    assert engine == "windows" and rate == 16000


# ── captions & B-roll ─────────────────────────────────────
def test_caption_chunks_cover_the_audio():
    from nova.skills.media import caption_chunks
    chunks = caption_chunks("one two three four five six seven eight nine ten eleven", 6.0, 4)
    assert [c[2] for c in chunks] == ["one two three four", "five six seven eight", "nine ten eleven"]
    assert all(a < b for a, b, _ in chunks) and chunks[-1][1] <= 6.0
    assert all(chunks[i][1] <= chunks[i + 1][0] + 1e-6 for i in range(len(chunks) - 1))


def test_video_with_captions_and_broll(nova, monkeypatch):
    cfg, tmp = nova
    from nova.skills import media
    clip = tmp / "broll.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=3", clip])
    monkeypatch.setattr(media, "fetch_broll", lambda q, fmt, d: clip if q == "b" else None)
    out = media.build_video("Test", [
        {"heading": "Scene one", "text": "gradient", "narration": "First scene words here.", "broll": "a"},
        {"heading": "Scene two", "text": "stock video", "narration": "Second scene over footage.", "broll": "b"},
    ], "vertical")
    assert out.exists() and ffmpeg.duration(out) > 1
    assert out.with_suffix(".jpg").exists()


def test_fetch_broll_without_key(monkeypatch):
    from nova.skills.media import fetch_broll
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    assert fetch_broll("office", "landscape") is None


# ── meetings ──────────────────────────────────────────────
def test_mix_tracks(tmp_path):
    from nova.skills.meetings import RATE, mix_tracks
    a, b = tmp_path / "a.raw", tmp_path / "b.raw"
    a.write_bytes((np.ones(RATE, np.int16) * 1000).tobytes())
    b.write_bytes((np.ones(RATE // 2, np.int16) * 500).tobytes())
    out = mix_tracks([a, b], tmp_path / "m.wav")
    with wave.open(str(out)) as wf:
        data = np.frombuffer(wf.readframes(wf.getnframes()), np.int16)
    assert len(data) == RATE and data[0] == 1500 and data[-1] == 1000


def test_meeting_processing_files_notes_and_memories(nova, monkeypatch):
    cfg, tmp = nova
    from nova.skills import meetings
    wav = tmp / "meeting.wav"
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(np.zeros(16000 * 3, np.int16).tobytes())
    pushed = []
    monkeypatch.setattr(context, "notify", lambda text, files: pushed.append((text, files)))
    cfg["meetings"] = {"create_tasks": False, "folder": str(tmp / "meet")}
    import datetime as dt
    note = meetings.process_recording(wav, "Northwind QBR", dt.datetime(2026, 9, 29, 10, 0))
    body = note.read_text()
    for section in ("## Summary", "## Decisions", "## Action items", "## Transcript", "Send pricing to Northwind"):
        assert section in body
    mems = [r["text"] for r in context.store.db.execute("SELECT text FROM memories")]
    assert any("Push Mist bundle" in m for m in mems) and any("Send pricing to Northwind" in m for m in mems)
    assert pushed and "Notes ready" in pushed[0][0]


# ── search & vision fallbacks ─────────────────────────────
def test_searxng_falls_back_to_duckduckgo(nova, monkeypatch):
    from nova.skills import web
    nova[0]["web"]["searxng_url"] = "http://localhost:1"
    monkeypatch.setattr(web, "_searxng", lambda *a: (_ for _ in ()).throw(ConnectionError("down")))
    monkeypatch.setattr(web, "_duckduckgo", lambda q, n: [{"title": "ddg", "url": "u", "snippet": "s"}])
    assert web.web_search("test")[0]["title"] == "ddg"


def test_vision_falls_back_to_local(nova, tmp_path):
    from PIL import Image
    from nova.llm import LLM, LLMReply
    llm = LLM(nova[0])

    class Broken:
        def chat(self, msgs, *a, **k):
            raise RuntimeError("quota")

    class Local:
        def chat(self, msgs, *a, **k):
            assert msgs[0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
            return LLMReply("a flask")
    llm.providers = {"gemini": Broken(), "ollama_vision": Local()}
    llm.vision_order = ["gemini", "ollama_vision"]
    img = tmp_path / "x.png"
    Image.new("RGB", (3000, 2000), "red").save(img)
    assert llm.see(str(img), "what is it?") == "a flask"


# ── barge-in ──────────────────────────────────────────────
def test_barge_in_stops_speech(nova):
    from nova.voice import VoiceLoop
    cfg, _ = nova

    class Speech:
        def __init__(self):
            self.stopped = threading.Event()

        def speak(self, text):
            self.stopped.wait(5)
            return False

        def stop(self):
            self.stopped.set()

    class Stream:
        def read(self, n):
            time.sleep(0.01)
            return (np.zeros((n, 1), np.int16), False)

    class WakeModel:
        calls = 0

        def predict(self, block):
            WakeModel.calls += 1
            return {"hey_jarvis": 0.99 if WakeModel.calls > 5 else 0.0}

        def reset(self):
            pass

    sp = Speech()
    loop = VoiceLoop(cfg, agent=None, speech=sp)
    t0 = time.time()
    assert loop._speak(Stream(), WakeModel(), "a long answer") is True
    assert sp.stopped.is_set() and time.time() - t0 < 3
