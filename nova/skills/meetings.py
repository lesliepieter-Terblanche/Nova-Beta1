"""Meeting recorder: record your mic + whatever the PC is playing (Teams, Zoom, Meet, phone-on-speaker),
then transcribe locally, summarise, and file the notes, decisions and action items into the 2nd brain.

"Nova, record this meeting"  ->  "Nova, stop recording"  ->  notes arrive a few minutes later.
Also: "summarise this recording C:\\...\\call.mp4" for existing recordings.

Recording a conversation may need the other people's consent where you live — tell them first.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np

from .. import context, ffmpeg
from ..config import resolve
from ..tools import register_group, tool

register_group("meetings", ["record", "recording", "meeting notes", "minutes", "transcribe", "transcript",
                            "teams call", "zoom", "google meet", "this call", "this meeting", "summarise the call",
                            "summarize the call", "stop recording"])

RATE = 16000
CHUNK = RATE // 2          # 0.5 s


def _cfg():
    return context.cfg.get("meetings") or {}


def _folder() -> Path:
    p = resolve(_cfg().get("folder", "workspace/meetings"))
    p.mkdir(parents=True, exist_ok=True)
    return p


class _Track(threading.Thread):
    """Records one source to a raw int16 file, padding gaps with silence so tracks stay in sync."""

    def __init__(self, name: str, mic, path: Path, t0: float, stop: threading.Event):
        super().__init__(daemon=True, name=f"rec-{name}")
        self.mic, self.path, self.t0, self.stop_evt = mic, path, t0, stop
        self.written = 0
        self.error: Exception | None = None

    def run(self):
        try:
            with open(self.path, "wb") as f, self.mic.recorder(samplerate=RATE, channels=1, blocksize=CHUNK) as rec:
                while not self.stop_evt.is_set():
                    data = rec.record(numframes=CHUNK)
                    pcm = (np.clip(data.reshape(-1), -1, 1) * 32767).astype(np.int16)
                    expected = int((time.monotonic() - self.t0) * RATE) - len(pcm)
                    gap = expected - self.written
                    if gap > RATE // 5:                      # source went quiet (e.g. nothing playing)
                        f.write(np.zeros(gap, np.int16).tobytes())
                        self.written += gap
                    f.write(pcm.tobytes())
                    self.written += len(pcm)
        except Exception as e:
            self.error = e
            print(f"[meeting] {self.name} stopped: {e}")


class _Session:
    def __init__(self, title: str, pc_audio: bool):
        import soundcard as sc
        self.title = title or f"Meeting {dt.datetime.now():%Y-%m-%d %H:%M}"
        self.started = dt.datetime.now()
        stamp = self.started.strftime("%Y%m%d_%H%M")
        slug = re.sub(r"[^\w]+", "_", self.title)[:40].strip("_")
        self.base = _folder() / f"{stamp}_{slug}"
        self.stop = threading.Event()
        t0 = time.monotonic()
        self.tracks = [_Track("mic", sc.default_microphone(), self.base.with_suffix(".mic.raw"), t0, self.stop)]
        if pc_audio:
            try:
                loop = sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)
                self.tracks.append(_Track("pc", loop, self.base.with_suffix(".pc.raw"), t0, self.stop))
            except Exception as e:
                print(f"[meeting] PC audio capture unavailable ({e}); recording mic only")
        for t in self.tracks:
            t.start()

    def finish(self) -> Path:
        self.stop.set()
        for t in self.tracks:
            t.join(timeout=5)
        out = self.base.with_suffix(".wav")
        mix_tracks([t.path for t in self.tracks], out)
        for t in self.tracks:
            t.path.unlink(missing_ok=True)
        return out


def mix_tracks(raw_paths: list[Path], out: Path) -> Path:
    """Mix raw int16 mono tracks (same rate) into one WAV, streaming 10 s at a time."""
    files = [open(p, "rb") for p in raw_paths if p.exists()]
    block = RATE * 10
    try:
        with wave.open(str(out), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(RATE)
            while True:
                chunks = [np.frombuffer(f.read(block * 2), np.int16).astype(np.int32) for f in files]
                n = max((len(c) for c in chunks), default=0)
                if n == 0:
                    break
                acc = np.zeros(n, np.int32)
                for c in chunks:
                    acc[: len(c)] += c
                wf.writeframes(np.clip(acc, -32768, 32767).astype(np.int16).tobytes())
    finally:
        for f in files:
            f.close()
    return out


_session: _Session | None = None
_lock = threading.Lock()


@tool(group="meetings")
def start_meeting_recording(title: str = "", include_pc_audio: bool = True) -> str:
    """Start recording a meeting or call: your microphone plus the PC's sound (Teams, Zoom, Meet…).
    Args:
        title: optional meeting name, e.g. "Axiz QBR"
        include_pc_audio: also record what the PC plays (the other people on the call)
    """
    global _session
    with _lock:
        if _session:
            return f"Already recording '{_session.title}' since {_session.started:%H:%M}."
        _session = _Session(title, include_pc_audio)
    if context.store:
        context.store.set_status("listening")
    srcs = "your mic and PC audio" if len(_session.tracks) > 1 else "your mic"
    return (f"Recording '{_session.title}' ({srcs}). Say 'stop recording' when you're done. "
            "Remember to let the others know it's being recorded.")


@tool(group="meetings")
def stop_meeting_recording() -> str:
    """Stop the meeting recording, then transcribe and write up the notes in the background."""
    global _session
    with _lock:
        s, _session = _session, None
    if not s:
        return "Nothing is being recorded."
    wav = s.finish()
    minutes = ffmpeg.duration(wav) / 60 if wav.exists() else 0
    if context.store:
        context.store.set_status("idle")
    threading.Thread(target=process_recording, args=(wav, s.title, s.started), daemon=True).start()
    return (f"Stopped. {minutes:.0f} minutes recorded. I'm transcribing it now and will let you know "
            "when the notes and action items are ready.")


@tool(group="meetings")
def meeting_recording_status() -> str:
    """Is a meeting being recorded, and for how long."""
    if not _session:
        return "Not recording."
    mins = (dt.datetime.now() - _session.started).seconds // 60
    return f"Recording '{_session.title}' for {mins} minutes."


@tool(group="meetings")
def summarise_recording(path: str, title: str = "") -> str:
    """Transcribe and summarise an existing recording (audio or video file, e.g. a Teams recording).
    Runs in the background; notes arrive when done.
    Args:
        path: full path to the recording
        title: optional meeting name
    """
    from .files import safe
    p = safe(path)
    wav = _folder() / f"{p.stem}_16k.wav"
    ffmpeg.to_wav16k(p, wav)
    threading.Thread(target=process_recording,
                     args=(wav, title or p.stem, dt.datetime.fromtimestamp(p.stat().st_mtime)), daemon=True).start()
    return f"Transcribing '{title or p.stem}' in the background. I'll let you know when the notes are ready."


# ── processing pipeline ───────────────────────────────────
SUMMARY_PROMPT = """You are an excellent executive assistant. Below is the transcript of a meeting called "{title}"
held on {date}. The person you work for is {owner} (the transcript may not label speakers).

Write JSON only:
{{"summary": "4-6 sentence summary",
  "decisions": ["decision", ...],
  "action_items": [{{"owner": "name or 'me' if it's {owner}'s", "task": "...", "due": "date/timeframe or ''"}}],
  "people": ["Name – role/company if mentioned", ...],
  "open_questions": ["...", ...],
  "follow_up_email": "a short, friendly follow-up email {owner} could send to the attendees"}}

TRANSCRIPT:
{transcript}"""


def _fmt_ts(s: float) -> str:
    return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{int(s % 60):02d}"


def _summarise(transcript: str, title: str, date: str) -> dict:
    owner = context.cfg.assistant.owner
    limit = 120_000
    if len(transcript) > limit:                  # very long meeting: summarise in parts first
        parts = [transcript[i:i + limit] for i in range(0, len(transcript), limit)]
        partial = [context.llm.complete(f"Summarise this part of a meeting transcript in detail, keeping all "
                                        f"decisions, numbers, names and action items:\n\n{p}") for p in parts]
        transcript = "\n\n".join(partial)
    raw = context.llm.complete(SUMMARY_PROMPT.format(title=title, date=date, owner=owner, transcript=transcript),
                               prefer_smart=True, temperature=0.2)
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0)) if m else {"summary": raw}


def build_note(title: str, when: dt.datetime, minutes: float, data: dict, segments: list) -> str:
    L = [f"# {title}", "", f"_{when:%A %d %B %Y, %H:%M} · {minutes:.0f} min · recorded by Nova_", "",
         "## Summary", data.get("summary", ""), ""]
    if data.get("decisions"):
        L += ["## Decisions", *[f"- {d}" for d in data["decisions"]], ""]
    if data.get("action_items"):
        L += ["## Action items"]
        for a in data["action_items"]:
            due = f" _(due {a['due']})_" if a.get("due") else ""
            L.append(f"- [ ] **{a.get('owner', '?')}** — {a.get('task', '')}{due}")
        L.append("")
    if data.get("people"):
        L += ["## People", *[f"- {p}" for p in data["people"]], ""]
    if data.get("open_questions"):
        L += ["## Open questions", *[f"- {q}" for q in data["open_questions"]], ""]
    if data.get("follow_up_email"):
        L += ["## Draft follow-up email", "", data["follow_up_email"], ""]
    L += ["## Transcript", ""] + [f"`{_fmt_ts(s)}` {t}" for s, _, t in segments]
    return "\n".join(L) + "\n"


def process_recording(wav: Path, title: str, when: dt.datetime) -> Path | None:
    try:
        model = None
        name = _cfg().get("stt_model")
        if name and name != context.cfg.voice.stt_model:
            from faster_whisper import WhisperModel
            model = WhisperModel(name, device="cpu", compute_type="int8")
        segments = context.speech.transcribe_segments(str(wav), model=model)
        minutes = ffmpeg.duration(wav) / 60
        if not segments:
            context.push(f"🎙 '{title}': I couldn't hear any speech in the recording.")
            return None
        transcript = "\n".join(f"[{_fmt_ts(s)}] {t}" for s, _, t in segments)
        data = _summarise(transcript, title, f"{when:%d %B %Y}")

        from .memory import vault
        safe_title = re.sub(r"[^\w\- ]", "", title)[:70]
        from .. import taxonomy
        if taxonomy.enabled():
            note = taxonomy.unique(taxonomy.system_folder("meetings") / taxonomy.file_name(safe_title, when.date()))
        else:
            note = vault() / "Meetings" / f"{when:%Y-%m-%d} {safe_title}.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(build_note(title, when, minutes, data, segments), encoding="utf-8")
        context.store.index_note(note)
        context.record("meeting", title, note, data.get("summary", "")[:300])
        context.record("audio", f"{title} (recording)", wav, f"{minutes:.0f} min")
        try:                                   # everyone in the meeting gets / updates a people card
            from .. import people
            for p in data.get("people", [])[:20]:
                pid = people.from_line(str(p), source=f"meeting:{title}")
                if pid:
                    people.upsert(people.get(pid)["name"], note=f"In meeting '{title}' ({when:%d %b %Y})",
                                  contact=when.isoformat(timespec="seconds"))
        except Exception as e:
            print(f"[meetings] people cards: {e}")

        # teach the permanent memory
        for d in data.get("decisions", [])[:8]:
            context.store.add_memory(f"Decision in '{title}' ({when:%d %b %Y}): {d}", "decision", "meeting")
        mine = [a for a in data.get("action_items", []) if str(a.get("owner", "")).lower() in
                ("me", context.cfg.assistant.owner.lower())]
        for a in mine[:10]:
            context.store.add_memory(f"Action from '{title}': {a.get('task')}" +
                                     (f" (due {a['due']})" if a.get("due") else ""), "event", "meeting")
        if _cfg().get("create_tasks", True) and mine:
            try:
                from .google_ws import tasks_add
                for a in mine[:10]:
                    tasks_add(a.get("task", ""), a.get("due", "") or "", f"From meeting: {title}")
            except Exception as e:
                print(f"[meeting] couldn't add Google Tasks: {e}")

        msg = (f"📝 Notes ready: {title}\n\n{data.get('summary', '')}\n\n"
               + ("Your actions:\n" + "\n".join(f"• {a.get('task')}" for a in mine) if mine else ""))
        context.push(msg, [str(note)])
        if context.announce:
            context.announce(f"Your notes for {title} are ready." +
                             (f" You have {len(mine)} action items." if mine else ""))
        return note
    except Exception as e:
        print(f"[meeting] processing failed: {e}")
        context.push(f"⚠️ Meeting '{title}' was recorded ({wav}) but processing failed: {e}")
        return None
