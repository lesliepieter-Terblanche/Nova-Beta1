"""The always-on voice loop: wake word -> record -> transcribe -> agent -> speak."""
from __future__ import annotations

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
        frames, started, silent_for, waited, total = [], False, 0.0, 0.0, 0.0
        block_s = BLOCK / RATE
        while total < self.v.max_record_seconds:
            block = stream.read(BLOCK)[0][:, 0].copy()
            total += block_s
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
        oww = self._load_wakeword()
        name = self.cfg.assistant.name
        print(f"[voice] {name} is listening for '{self.v.wake_word}'…")
        self.speech.speak(f"{name} is online.")
        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK) as stream:
            self._drain(stream)
            while self.running:
                block = stream.read(BLOCK)[0][:, 0]
                self.noise.append(self._rms(block))
                scores = oww.predict(block)
                if max(scores.values(), default=0) >= self.v.wake_threshold:
                    oww.reset()
                    self._conversation(stream)
                    self._drain(stream)
                    oww.reset()

    def _conversation(self, stream) -> None:
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
            reply = self.agent.handle(text, session="voice")
            print(f"🤖 {reply.text}\n")
            spoken, trimmed = trim_for_speech(reply.text, int(self.v.get("max_spoken_chars", 450)))
            if trimmed or reply.files:
                context.push(reply.text, reply.files)       # full answer + files to Telegram
            if store:
                store.set_status("speaking")
            self.speech.speak(spoken)
            self._drain(stream)
            if re_goodbye(text):
                break
            timeout = float(self.v.get("follow_up_seconds", 6))   # follow-up without wake word
            time.sleep(0.15)
        if store:
            store.set_status("idle")


def re_goodbye(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in ("thank you", "thanks", "that's all", "goodbye", "bye", "stop listening"))
