"""Speech in and out.

Listening: faster-whisper, fully local on the CPU (keeps the 4 GB GPU for the LLM).
Speaking:  ElevenLabs (streamed, starts talking within ~300 ms) with local Piper
           as automatic backup, and Windows' built-in voice as a last resort.
"""
from __future__ import annotations

import os
import re
import threading
import wave
from pathlib import Path

import httpx
import numpy as np

from .config import resolve

EL_RATE = 22050


def clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " (code sent separately) ", text, flags=re.S)
    text = re.sub(r"https?://\S+", "the link", text)
    text = re.sub(r"[*_#`>|]", "", text)
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


def trim_for_speech(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[: end + 1] if end > 80 else cut) + " The full answer is on your screen and phone.", True


class Speech:
    def __init__(self, cfg):
        self.cfg = cfg
        self._stt = None
        self._piper = None
        self._stop = threading.Event()
        self.speaking = False
        self.el_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()

    # ── STT ────────────────────────────────────────────────
    def stt_model(self):
        if self._stt is None:
            from faster_whisper import WhisperModel
            print(f"[stt] loading whisper '{self.cfg.voice.stt_model}' (first run downloads it)…")
            self._stt = WhisperModel(self.cfg.voice.stt_model, device="cpu", compute_type="int8")
        return self._stt

    def transcribe(self, audio) -> str:
        """audio: float32 numpy array at 16 kHz, or a path to an audio file."""
        lang = self.cfg.voice.get("stt_language") or None
        segments, _ = self.stt_model().transcribe(audio, language=lang, beam_size=1, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()

    # ── TTS: synthesis to a WAV file ───────────────────────
    def _el_request(self, text: str, stream: bool):
        el = self.cfg.tts.elevenlabs
        voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or el.voice_id
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}{'/stream' if stream else ''}"
        body = {
            "text": text,
            "model_id": el.model,
            "voice_settings": {"stability": el.stability, "similarity_boost": el.similarity_boost,
                               "style": el.get("style", 0.0), "speed": el.get("speed", 1.0)},
        }
        return url, body, {"xi-api-key": self.el_key}, {"output_format": f"pcm_{EL_RATE}"}

    def _use_elevenlabs(self) -> bool:
        return self.cfg.tts.engine == "elevenlabs" and bool(self.el_key)

    def synth_wav(self, text: str, out_path: str | Path) -> Path:
        out_path = Path(out_path)
        text = clean_for_speech(text)
        if self._use_elevenlabs():
            try:
                url, body, headers, params = self._el_request(text, stream=False)
                r = httpx.post(url, json=body, headers=headers, params=params, timeout=120)
                r.raise_for_status()
                _write_wav(out_path, r.content, EL_RATE)
                return out_path
            except Exception as e:
                print(f"[tts] ElevenLabs failed ({e}); using Piper")
        return self._piper_wav(text, out_path)

    def _piper_voice(self):
        if self._piper is None:
            from piper import PiperVoice
            self._piper = PiperVoice.load(str(resolve(self.cfg.tts.piper.voice)))
        return self._piper

    def _piper_wav(self, text: str, out_path: Path) -> Path:
        try:
            voice = self._piper_voice()
            with wave.open(str(out_path), "wb") as wf:
                if hasattr(voice, "synthesize_wav"):      # piper-tts >= 1.3
                    voice.synthesize_wav(text, wf)
                else:                                     # piper-tts 1.2
                    voice.synthesize(text, wf)
            return out_path
        except Exception as e:
            print(f"[tts] Piper failed ({e}); using Windows voice")
            import pyttsx3
            eng = pyttsx3.init()
            eng.save_to_file(text, str(out_path))
            eng.runAndWait()
            return out_path

    # ── TTS: speak out loud ────────────────────────────────
    def stop(self) -> None:
        self._stop.set()

    def speak(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
            return
        self._stop.clear()
        self.speaking = True
        try:
            if self._use_elevenlabs():
                try:
                    self._speak_elevenlabs_stream(text)
                    return
                except Exception as e:
                    print(f"[tts] ElevenLabs stream failed ({e}); using Piper")
            tmp = resolve("workspace/.tts_tmp.wav")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            self._piper_wav(text, tmp)
            self.play_wav(tmp)
        finally:
            self.speaking = False

    def _speak_elevenlabs_stream(self, text: str) -> None:
        import sounddevice as sd
        url, body, headers, params = self._el_request(text, stream=True)
        with httpx.stream("POST", url, json=body, headers=headers, params=params, timeout=60) as r:
            if r.status_code != 200:
                r.read()
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            with sd.RawOutputStream(samplerate=EL_RATE, channels=1, dtype="int16") as out:
                leftover = b""
                for chunk in r.iter_bytes(4096):
                    if self._stop.is_set():
                        break
                    chunk = leftover + chunk
                    usable = len(chunk) - (len(chunk) % 2)
                    out.write(chunk[:usable])
                    leftover = chunk[usable:]

    def play_wav(self, path: str | Path) -> None:
        import sounddevice as sd
        with wave.open(str(path), "rb") as wf:
            rate, ch = wf.getframerate(), wf.getnchannels()
            data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        if ch > 1:
            data = data.reshape(-1, ch)
        sd.play(data, rate)
        while sd.get_stream().active:
            if self._stop.wait(0.05):
                sd.stop()
                break

    def beep(self, freq: int = 880, ms: int = 120) -> None:
        try:
            import sounddevice as sd
            t = np.linspace(0, ms / 1000, int(16000 * ms / 1000), False)
            tone = (0.18 * np.sin(2 * np.pi * freq * t) * np.hanning(len(t))).astype(np.float32)
            sd.play(tone, 16000, blocking=True)
        except Exception:
            pass


def _write_wav(path: Path, pcm: bytes, rate: int) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm[: len(pcm) - (len(pcm) % 2)])
