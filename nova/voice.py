"""The always-on voice loop: wake word -> record -> transcribe -> agent -> speak."""
from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np

from . import context
from .config import resolve
from .speech import trim_for_speech

RATE = 16000
BLOCK = 1280          # 80 ms — the frame size openWakeWord expects


class VoiceLoop:
    def __init__(self, cfg, agent, speech):
        self.cfg, self.agent, self.speech = cfg, agent, speech
        self.v = cfg.voice
        self.noise = deque([300.0] * 20, maxlen=60)
        self.running = True
        self.hotkey = threading.Event()
        self._vad = None
        self._vad_tried = False

    def vad(self):
        """Silero VAD (voice.vad: silero) — or None to judge by loudness (voice.vad: loudness)."""
        if not self._vad_tried:
            self._vad_tried = True
            if str(self.v.get("vad", "silero")).lower() == "silero":
                from .vad import load
                self._vad = load(float(self.v.get("vad_threshold", 0.5)))
        return self._vad

    def _load_wakeword(self):
        import openwakeword
        from openwakeword.model import Model
        ww = str(self.v.wake_word)
        if ww.endswith((".onnx", ".tflite")):          # custom model, e.g. models/hey_nova.onnx
            path = str(resolve(ww))
            return Model(wakeword_models=[path], inference_framework="onnx" if path.endswith(".onnx") else "tflite")
        try:
            openwakeword.utils.download_models(model_names=[ww])
        except Exception as e:
            print(f"[voice] could not download wake-word model: {e}")
        return Model(wakeword_models=[ww], inference_framework="onnx")

    # ── audio helpers ─────────────────────────────────────
    @staticmethod
    def _rms(block: np.ndarray) -> float:
        return float(np.sqrt(np.mean(block.astype(np.float32) ** 2)) + 1e-6)

    def _threshold(self) -> float:
        return max(450.0, float(np.median(self.noise)) * 3.0)

    def _drain(self, stream) -> None:
        n = stream.read_available
        if n:
            stream.read(n)

    def _record(self, stream, start_timeout: float) -> np.ndarray | None:
        """Record until the speaker goes quiet. None if nobody spoke."""
        thr = self._threshold()
        vad = self.vad()
        if vad:
            vad.reset()
        frames, started, silent_for, waited, total = [], False, 0.0, 0.0, 0.0
        block_s = BLOCK / RATE
        while total < self.v.max_record_seconds:
            block = stream.read(BLOCK)[0][:, 0].copy()
            total += block_s
            if vad:                                       # real speech, not fans / typing / the TV
                p = vad.speech_in(block)
                loud = p >= (vad.threshold if not started else max(0.15, vad.threshold - 0.15))
            else:
                loud = self._rms(block) > thr
            if not started:
                frames = (frames + [block])[-4:]          # keep 320 ms of lead-in
                if loud:
                    started = True
                else:
                    waited += block_s
                    if waited > start_timeout:
                        return None
                continue
            frames.append(block)
            silent_for = 0.0 if loud else silent_for + block_s
            if silent_for >= self.v.silence_seconds:
                break
        audio = np.concatenate(frames).astype(np.float32) / 32768.0
        return audio if len(audio) > RATE * 0.4 else None

    # ── main loop ─────────────────────────────────────────
    def run(self) -> None:
        import sounddevice as sd
        from . import startup
        startup.mark("", "voice")
        oww = self._load_wakeword()
        name = self.cfg.assistant.name
        hk = self._start_hotkey()
        startup.mark("listening for the wake word", "voice")
        print(f"[voice] {name} is listening for '{self.v.wake_word}'" + (f" (or press {hk})" if hk else "") + "…")
        self.speech.speak(f"{name} is online.")
        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK) as stream:
            self._drain(stream)
            while self.running:
                block = stream.read(BLOCK)[0][:, 0]
                self.noise.append(self._rms(block))
                scores = oww.predict(block)
                if max(scores.values(), default=0) >= self.v.wake_threshold or self.hotkey.is_set():
                    self.hotkey.clear()
                    oww.reset()
                    self._conversation(stream, oww)
                    self._drain(stream)
                    oww.reset()

    # ── push-to-talk ──────────────────────────────────────
    def _start_hotkey(self) -> str:
        combo = str(self.v.get("hotkey") or "").strip()
        if not combo:
            return ""
        try:
            from pynput import keyboard
            listener = keyboard.GlobalHotKeys({combo: self.hotkey.set})
            listener.daemon = True
            listener.start()
            return combo.replace("<", "").replace(">", "").title()
        except Exception as e:
            print(f"[voice] hotkey {combo} unavailable: {e}")
            return ""

    # ── barge-in: keep listening while speaking ───────────
    def _speak(self, stream, oww, text: str) -> bool:
        """Speak, but stop as soon as the wake word or hotkey is heard. Returns True if interrupted."""
        if not self.v.get("barge_in", True):
            self.speech.speak(text)
            return False
        t = threading.Thread(target=self.speech.speak, args=(text,), daemon=True)
        t.start()
        self.hotkey.clear()
        oww.reset()
        threshold = min(0.95, float(self.v.wake_threshold) + 0.1)   # a bit stricter: the speaker echoes
        interrupted = False
        while t.is_alive():
            block = stream.read(BLOCK)[0][:, 0]
            if self.hotkey.is_set() or max(oww.predict(block).values(), default=0) >= threshold:
                interrupted = True
                self.hotkey.clear()
                self.speech.stop()
                break
        t.join(timeout=3)
        oww.reset()
        return interrupted

    def _answer_live(self, stream, oww, text: str):
        """Think and speak at the same time: the first sentence is spoken while the rest is still being written.
        The wake word / hotkey still cuts in. Returns (reply, interrupted)."""
        from .live_speech import LiveSpeaker
        sp = LiveSpeaker(self.speech, int(self.v.get("max_spoken_chars", 450)))
        box = {}

        def think():
            try:
                box["reply"] = self.agent.handle(text, session="voice", on_delta=sp.feed)
            finally:
                r = box.get("reply")
                sp.finish(r.text if r else "Sorry, something went wrong.")

        t = threading.Thread(target=think, daemon=True)
        t.start()
        self.hotkey.clear()
        oww.reset()
        barge = self.v.get("barge_in", True)
        threshold = min(0.95, float(self.v.wake_threshold) + 0.1)
        interrupted = False
        while t.is_alive() or sp.alive():
            block = stream.read(BLOCK)[0][:, 0]              # keeps the microphone drained while she talks
            if sp.started.is_set() and context.store and context.store.status != "speaking" and not t.is_alive():
                context.store.set_status("speaking")
            if barge and sp.started.is_set() and (self.hotkey.is_set() or max(oww.predict(block).values(), default=0) >= threshold):
                interrupted = True
                self.hotkey.clear()
                sp.stop()
                break
        t.join(timeout=120)
        sp.wait(timeout=3)
        oww.reset()
        return box.get("reply"), interrupted, sp.feed.trimmed

    def _conversation(self, stream, oww) -> None:
        store = context.store
        if self.v.get("chime", True):
            self.speech.beep()
        self._drain(stream)
        timeout = 4.0
        while True:
            if store:
                store.set_status("listening")
            audio = self._record(stream, start_timeout=timeout)
            if audio is None:
                break
            text = self.speech.transcribe(audio)
            if not text or len(text) < 2:
                break
            print(f"\n🗣  {text}")
            if self.v.get("live_speech", True):             # speak the first sentence while the rest is written
                reply, interrupted, trimmed = self._answer_live(stream, oww, text)
                if reply is None:
                    break
                print(f"🤖 {reply.text}\n")
                if trimmed or reply.files:
                    context.push(reply.text, reply.files)   # full answer + files to Telegram
            else:
                reply = self.agent.handle(text, session="voice")
                print(f"🤖 {reply.text}\n")
                spoken, trimmed = trim_for_speech(reply.text, int(self.v.get("max_spoken_chars", 450)))
                if trimmed or reply.files:
                    context.push(reply.text, reply.files)   # full answer + files to Telegram
                if store:
                    store.set_status("speaking")
                interrupted = self._speak(stream, oww, spoken)
            self._drain(stream)
            if interrupted:                                 # you cut in: listen straight away
                self.speech.beep(660)
                timeout = 5.0
                continue
            if re_goodbye(text):
                break
            timeout = float(self.v.get("follow_up_seconds", 6))   # follow-up without wake word
            time.sleep(0.15)
        if store:
            store.set_status("idle")


def re_goodbye(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in ("thank you", "thanks", "that's all", "goodbye", "bye", "stop listening"))
