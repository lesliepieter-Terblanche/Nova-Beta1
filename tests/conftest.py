"""Shared test setup: a fake LLM, a fake voice and an isolated memory store."""
import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nova import context  # noqa: E402
from nova.config import load_config  # noqa: E402
from nova.llm import LLMReply, ToolCall  # noqa: E402,F401
from nova.store import Store  # noqa: E402


class FakeLLM:
    """Scripted model. Queue LLMReply objects (or callables taking messages) in .queue."""
    smart = ["fake"]

    def __init__(self):
        self.queue = []

    def should_escalate(self, text):
        return False

    def chat(self, messages, tools=None, prefer_smart=False, temperature=0.3, on_delta=None):
        r = self.queue.pop(0)
        r = r(messages) if callable(r) else r
        if on_delta and r.content and not r.tool_calls:          # "streamed": a few words at a time
            words = r.content.split(" ")
            for i in range(0, len(words), 3):
                on_delta(" ".join(words[i:i + 3]) + (" " if i + 3 < len(words) else ""))
        return r

    def complete(self, prompt, system="", prefer_smart=True, temperature=0.5):
        if "executive assistant" in prompt:
            return json.dumps({"summary": "We agreed the Q4 plan.", "decisions": ["Push Mist bundle in Q4"],
                               "action_items": [{"owner": "me", "task": "Send pricing to Axiz", "due": "Friday"},
                                                {"owner": "Sam", "task": "Book demo", "due": ""}],
                               "people": ["Sam – Axiz"], "open_questions": [], "follow_up_email": "Hi all…"})
        if "long-term memory" in prompt:
            return '{"memories": []}'
        if "video script" in prompt:
            return json.dumps({"title": "Test video", "slides": [
                {"heading": "One", "text": "first", "narration": "One."},
                {"heading": "Two", "text": "second", "narration": "Two."}]})
        if "copywriter" in prompt:
            return json.dumps({"product_name": "Flask", "headline": "Cold for 24 hours", "subline": "Steel flask",
                               "cta": "Shop now", "caption": "Go outside.", "hashtags": "#test"})
        if "website" in prompt.lower():
            return "```html\n<!doctype html><html><body><h1>Test site</h1></body></html>\n```"
        return "summary"

    def see(self, path, question):
        return "A black steel flask."


class FakeSpeech:
    def synth_wav(self, text, out):
        rate = 22050
        t = np.linspace(0, 0.6, int(rate * 0.6), False)
        pcm = (np.sin(2 * np.pi * 220 * t) * 3000).astype(np.int16)
        with wave.open(str(out), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(pcm.tobytes())
        return Path(out)

    def speak(self, text):
        pass

    def transcribe_segments(self, audio, model=None):
        return [(0.0, 2.0, "Welcome to the QBR."), (2.0, 5.0, "Pieter will send pricing to Axiz by Friday.")]


@pytest.fixture()
def nova(tmp_path):
    cfg = load_config(ROOT / "config.example.yaml")
    cfg["brain"]["db_file"] = str(tmp_path / "nova.db")
    cfg["brain"]["vault_dir"] = str(tmp_path / "brain")
    cfg["media"]["output_dir"] = str(tmp_path / "videos")
    cfg["web"]["sites_dir"] = str(tmp_path / "sites")
    cfg["files"]["allowed_roots"] = [str(tmp_path / "files"), "./workspace"]
    cfg["mcp_servers"] = {}
    (tmp_path / "files").mkdir()
    context.cfg = cfg
    context.store = Store(Path(cfg["brain"]["db_file"]))
    context.store._embed_ok = False          # no Ollama in tests -> keyword search
    context.store.vault = Path(cfg["brain"]["vault_dir"])
    context.llm = FakeLLM()
    context.speech = FakeSpeech()
    from nova.skills import load_all
    load_all()
    return cfg, tmp_path
