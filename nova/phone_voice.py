"""Voice for the dashboard when you use it away from the PC (phone / laptop over Tailscale).

  🎤 you speak into the phone → the recording comes to the PC → transcribed locally (Whisper) → Nova answers
  🔊 the answer is spoken with Nova's own voice (ElevenLabs / Kokoro / Piper) → sent back as a small MP3 the phone plays

Nothing plays on the PC's speakers for these, and the audio files are deleted after half an hour.
"""
from __future__ import annotations

import re
import tempfile
import time
import uuid
from pathlib import Path

from . import context
from .config import resolve

NAME = re.compile(r"^[a-f0-9]{32}\.(mp3|wav)$")
EXT = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/aac": ".aac", "audio/x-m4a": ".m4a",
       "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "video/webm": ".webm", "video/mp4": ".m4a"}
MAX_BYTES = 15 * 1024 * 1024
KEEP_MINUTES = 30


def folder() -> Path:
    d = resolve("workspace/tmp/phone_voice")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tidy() -> None:
    cutoff = time.time() - KEEP_MINUTES * 60
    for f in folder().glob("*"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def reply_audio(text: str) -> str | None:
    """Speak `text` into a small MP3 (WAV if ffmpeg can't) and return its URL path, or None."""
    if not (context.speech and text and text.strip()):
        return None
    from .speech import trim_for_speech
    limit = int(((context.cfg or {}).get("voice") or {}).get("max_spoken_chars", 450))
    spoken, _ = trim_for_speech(text, limit)
    _tidy()
    stem = uuid.uuid4().hex
    wav = folder() / f"{stem}.wav"
    try:
        context.speech.synth_wav(spoken, wav)
    except Exception as e:
        print(f"[phone voice] couldn't speak the reply: {e}")
        return None
    try:
        from . import ffmpeg
        mp3 = wav.with_suffix(".mp3")
        ffmpeg.run(["-i", wav, "-ac", "1", "-b:a", "64k", mp3])
        wav.unlink(missing_ok=True)
        return f"/api/audio/{mp3.name}"
    except Exception:
        return f"/api/audio/{wav.name}"


def audio_file(name: str) -> Path | None:
    if not NAME.match(name or ""):
        return None
    p = folder() / name
    return p if p.exists() else None


def transcribe(data: bytes, content_type: str) -> str:
    """Phone recording (webm/opus from Android & Chrome, mp4/aac from iPhone) → text."""
    if not context.speech:
        return ""
    ext = EXT.get((content_type or "").split(";")[0].strip().lower(), ".webm")
    tmp = Path(tempfile.mkdtemp(prefix="nova_mic_"))
    src, wav = tmp / f"in{ext}", tmp / "in.wav"
    src.write_bytes(data)
    try:
        from . import ffmpeg
        ffmpeg.to_wav16k(src, wav)
        path = wav
    except Exception:
        path = src                                       # Whisper can often read it directly
    try:
        return context.speech.transcribe(str(path)).strip()
    finally:
        for f in (src, wav):
            f.unlink(missing_ok=True)
        tmp.rmdir()


def converse(agent, data: bytes, content_type: str) -> dict:
    """One spoken turn from the phone: what Nova heard, her answer (text + audio), and whether you said goodbye."""
    if len(data) > MAX_BYTES:
        return {"error": "That recording is too long."}
    heard = transcribe(data, content_type)
    if len(heard) < 2:
        return {"heard": "", "text": "", "audio": None, "end": False}
    reply = agent.handle(heard, session="phone")
    from .voice import re_goodbye
    return {"heard": heard, "text": reply.text, "files": reply.files, "audio": reply_audio(reply.text),
            "end": re_goodbye(heard)}
