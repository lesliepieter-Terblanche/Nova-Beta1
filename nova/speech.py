"""Speech in and out.

Listening: faster-whisper, fully local on the CPU (keeps the 4 GB GPU for the LLM).
Speaking:  a chain of engines, tried in order until one works:
             elevenlabs  – cloud, streamed, starts talking in ~300 ms (your subscription)
             kokoro      – local neural voice (Kokoro-82M), natural, runs on CPU
             piper       – local, very fast, more robotic
             windows     – the built-in Windows voice (always there)
"""
from __future__ import annotations

import os
import queue
import re
import threading
import wave
from pathlib import Path

import httpx
import numpy as np

from .config import resolve

EL_RATE = 22050
ENGINES = ("elevenlabs", "kokoro", "piper", "windows")


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


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    out, buf = [], ""
    for p in parts:                       # merge very short fragments so the voice flows
        buf = f"{buf} {p}".strip()
        if len(buf) > (15 if not out else 60):     # short first chunk = voice starts sooner
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def to_int16(samples: np.ndarray) -> np.ndarray:
    if samples.dtype == np.int16:
        return samples
    return (np.clip(samples, -1, 1) * 32767).astype(np.int16)


def write_wav(path: Path, pcm: np.ndarray, rate: int) -> Path:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(to_int16(pcm).tobytes())
    return Path(path)


class Speech:
    def __init__(self, cfg):
        self.cfg = cfg
        self._stt = None
        self._piper = None
        self._kokoro = None
        self._stop = threading.Event()
        self.speaking = False
        self.el_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        self._http: httpx.Client | None = None

    def http(self) -> httpx.Client:
        """One kept-open connection for ElevenLabs: no new handshake (≈0.3-0.8 s from SA) on every reply."""
        if self._http is None:
            self._http = httpx.Client(timeout=httpx.Timeout(60, connect=10),
                                      limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=600))
        return self._http

    def warm(self) -> None:
        """Get the voice ready at start-up: load Kokoro / Piper, open the ElevenLabs connection."""
        for engine in self.engine_order()[:2]:
            try:
                if engine == "elevenlabs":
                    self.http().get("https://api.elevenlabs.io/v1/models", headers={"xi-api-key": self.el_key})
                elif engine in ("kokoro", "piper"):
                    getattr(self, f"_synth_{engine}")("Ready.")
                print(f"[tts] {engine} voice ready")
            except Exception as e:
                print(f"[tts] couldn't warm up {engine}: {e}")

    # ── STT ────────────────────────────────────────────────
    def stt_model(self):
        if self._stt is None:
            from faster_whisper import WhisperModel
            print(f"[stt] loading whisper '{self.cfg.voice.stt_model}' (first run downloads it)…")
            self._stt = WhisperModel(self.cfg.voice.stt_model, device="cpu", compute_type="int8")
        return self._stt

    def transcribe(self, audio) -> str:
        """audio: float32 numpy array at 16 kHz, or a path to an audio file."""
        return " ".join(t for _, _, t in self.transcribe_segments(audio)).strip()

    def transcribe_segments(self, audio, model=None) -> list[tuple[float, float, str]]:
        """[(start, end, text), …] — used for meetings and captions."""
        lang = self.cfg.voice.get("stt_language") or None
        m = model or self.stt_model()
        segments, _ = m.transcribe(audio, language=lang, beam_size=1, vad_filter=True)
        return [(s.start, s.end, s.text.strip()) for s in segments]

    def transcribe_words(self, audio, model=None) -> list[tuple[float, float, str]]:
        """[(start, end, word), …] with word-level timing — for captions that pop in as you speak."""
        lang = self.cfg.voice.get("stt_language") or None
        m = model or self.stt_model()
        segments, _ = m.transcribe(audio, language=lang, beam_size=1, vad_filter=True, word_timestamps=True)
        return [(w.start, w.end, w.word.strip()) for s in segments for w in (s.words or []) if w.word.strip()]

    # ── engine order ───────────────────────────────────────
    def engine_order(self) -> list[str]:
        t = self.cfg.tts
        order = [t.get("engine", "elevenlabs")] + list(t.get("fallback", ["kokoro", "piper", "windows"]))
        seen, out = set(), []
        for e in order:
            if e in ENGINES and e not in seen and self._available(e):
                seen.add(e)
                out.append(e)
        return out or ["windows"]

    def _available(self, engine: str) -> bool:
        t = self.cfg.tts
        if engine == "elevenlabs":
            return bool(self.el_key)
        if engine == "kokoro":
            k = t.get("kokoro", {})
            return resolve(k.get("model", "models/kokoro-v1.0.int8.onnx")).exists() and \
                resolve(k.get("voices", "models/voices-v1.0.bin")).exists()
        if engine == "piper":
            return resolve(t.get("piper", {}).get("voice", "voices/en_GB-alan-medium.onnx")).exists()
        return True

    # ── engines: text -> (int16 pcm, rate) ─────────────────
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

    def elevenlabs_mp3_stream(self, text: str):
        """MP3 bytes as ElevenLabs makes them — the phone starts playing after the first chunk."""
        url, body, headers, _ = self._el_request(clean_for_speech(text), stream=True)
        with self.http().stream("POST", url, json=body, headers=headers, params={"output_format": "mp3_44100_64"}) as r:
            if r.status_code != 200:
                r.read()
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            yield from r.iter_bytes(4096)

    def _synth_elevenlabs(self, text):
        url, body, headers, params = self._el_request(text, stream=False)
        r = self.http().post(url, json=body, headers=headers, params=params, timeout=120)
        r.raise_for_status()
        pcm = r.content[: len(r.content) - (len(r.content) % 2)]
        return np.frombuffer(pcm, dtype=np.int16), EL_RATE

    def kokoro(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro
            k = self.cfg.tts.get("kokoro", {})
            self._kokoro = Kokoro(str(resolve(k.get("model", "models/kokoro-v1.0.int8.onnx"))),
                                  str(resolve(k.get("voices", "models/voices-v1.0.bin"))))
        return self._kokoro

    def _synth_kokoro(self, text):
        k = self.cfg.tts.get("kokoro", {})
        samples, rate = self.kokoro().create(text, voice=k.get("voice", "bm_george"),
                                             speed=float(k.get("speed", 1.0)), lang=k.get("lang", "en-gb"))
        return to_int16(samples), rate

    def _synth_piper(self, text):
        if self._piper is None:
            from piper import PiperVoice
            self._piper = PiperVoice.load(str(resolve(self.cfg.tts.piper.voice)))
        tmp = resolve("workspace/.piper_tmp.wav")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(tmp), "wb") as wf:
            if hasattr(self._piper, "synthesize_wav"):     # piper-tts >= 1.3
                self._piper.synthesize_wav(text, wf)
            else:                                          # piper-tts 1.2
                self._piper.synthesize(text, wf)
        return _read_wav(tmp)

    def _synth_windows(self, text):
        import pyttsx3
        tmp = resolve("workspace/.sapi_tmp.wav")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        eng = pyttsx3.init()
        eng.save_to_file(text, str(tmp))
        eng.runAndWait()
        return _read_wav(tmp)

    def synth(self, text: str, engines: list[str] | None = None):
        errors = []
        for e in engines or self.engine_order():
            try:
                return (*getattr(self, f"_synth_{e}")(text), e)
            except Exception as ex:
                errors.append(f"{e}: {ex}")
                print(f"[tts] {e} failed ({ex}); trying next voice")
        raise RuntimeError("No voice engine worked: " + " | ".join(errors))

    def synth_wav(self, text: str, out_path: str | Path) -> Path:
        pcm, rate, _ = self.synth(clean_for_speech(text))
        return write_wav(Path(out_path), pcm, rate)

    # ── speaking out loud ──────────────────────────────────
    def stop(self) -> None:
        """Interrupt whatever is being said (barge-in)."""
        self._stop.set()

    def speak(self, text: str) -> bool:
        """Say text. Returns False if it was interrupted."""
        text = clean_for_speech(text)
        if not text:
            return True
        self._stop.clear()
        self.speaking = True
        try:
            for engine in self.engine_order():
                try:
                    if engine == "elevenlabs":
                        self._speak_elevenlabs_stream(text)
                    else:
                        self._speak_pipelined(text, engine)
                    return not self._stop.is_set()
                except Exception as e:
                    if self._stop.is_set():
                        return False
                    print(f"[tts] {engine} failed ({e}); trying next voice")
            return True
        finally:
            self.speaking = False

    def _speak_elevenlabs_stream(self, text: str) -> None:
        import sounddevice as sd
        url, body, headers, params = self._el_request(text, stream=True)
        with self.http().stream("POST", url, json=body, headers=headers, params=params) as r:
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

    def _speak_pipelined(self, text: str, engine: str) -> None:
        """Synthesise sentence N+1 while sentence N plays, so local voices start quickly."""
        q: queue.Queue = queue.Queue(maxsize=2)
        synth = getattr(self, f"_synth_{engine}")
        error: list[Exception] = []

        def producer():
            try:
                for sentence in split_sentences(text):
                    if self._stop.is_set():
                        break
                    q.put(synth(sentence))
            except Exception as e:
                error.append(e)
            finally:
                q.put(None)

        threading.Thread(target=producer, daemon=True).start()
        played = False
        while True:
            item = q.get()
            if item is None:
                break
            if self._stop.is_set():
                continue
            self.play_pcm(*item)
            played = True
        if error and not played:
            raise error[0]

    def play_pcm(self, pcm: np.ndarray, rate: int) -> None:
        import sounddevice as sd
        sd.play(pcm, rate)
        while sd.get_stream().active:
            if self._stop.wait(0.03):
                sd.stop()
                break

    def play_wav(self, path: str | Path) -> None:
        self.play_pcm(*_read_wav(Path(path)))

    def beep(self, freq: int = 880, ms: int = 120) -> None:
        try:
            import sounddevice as sd
            t = np.linspace(0, ms / 1000, int(16000 * ms / 1000), False)
            tone = (0.18 * np.sin(2 * np.pi * freq * t) * np.hanning(len(t))).astype(np.float32)
            sd.play(tone, 16000, blocking=True)
        except Exception:
            pass


def _read_wav(path: Path):
    with wave.open(str(path), "rb") as wf:
        rate, ch = wf.getframerate(), wf.getnchannels()
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return data, rate
