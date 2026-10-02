"""Speak while the answer is still being written.

The model's reply is streamed; as soon as the first sentence is complete it goes to the voice, and the following
sentences are spoken as they arrive. So the voice starts after the first sentence instead of after the whole answer.

  SentenceFeed   turns a stream of text pieces into whole sentences (it is the agent's `on_delta` callback)
  LiveSpeaker    says those sentences on the PC's speakers as they come
(for a phone / browser the same feed is turned into one continuous MP3 — see phone_voice.open_live)
"""
from __future__ import annotations

import queue
import re
import threading

from .speech import clean_for_speech, trim_for_speech

END = re.compile(r"([.!?…]+[\"')\]]*|\n)(\s+|$)")
ODD_START = ("{", "[", "```", "<")            # a tool call written out as text, never an answer to read aloud


class SentenceFeed:
    def __init__(self, limit: int = 450, first_min: int = 12):
        self.q: queue.Queue = queue.Queue()
        self.buf = ""
        self.limit, self.first_min = int(limit), first_min
        self.sent = 0                          # characters handed to the voice so far
        self.trimmed = False
        self.closed = False
        self.lock = threading.Lock()

    # the agent calls this with each new piece of text
    def __call__(self, piece: str) -> None:
        if not piece or self.closed:
            return
        with self.lock:
            self.buf += piece
            self._emit(final=False)

    def _emit(self, final: bool) -> None:
        if self.buf.lstrip().startswith(ODD_START):
            if final:
                self.buf = ""
            return
        while True:
            cut = None
            for m in END.finditer(self.buf):
                if m.end() >= (self.first_min if not self.sent else 3) and (m.group(2) or final):
                    cut = m.end()
                    break
            if cut is None:
                if not final or not self.buf.strip():
                    return
                cut = len(self.buf)
            piece, self.buf = self.buf[:cut], self.buf[cut:]
            self._say(piece)
            if final and not self.buf.strip():
                return

    def _say(self, piece: str) -> None:
        text = clean_for_speech(piece)
        if not text or self.trimmed:
            return
        if self.sent + len(text) > self.limit and self.sent:
            self.trimmed = True                 # long answers: the rest is on screen
            return
        self.sent += len(text)
        self.q.put(text)

    def finish(self, final_text: str = "") -> None:
        """The turn is over. If nothing could be streamed (a tool-only turn, a yes/no question, an older model),
        the whole answer is spoken now."""
        with self.lock:
            if self.closed:
                return
            self._emit(final=True)
            if not self.sent and final_text:
                spoken, self.trimmed = trim_for_speech(final_text, self.limit)
                spoken = clean_for_speech(spoken)
                if spoken:
                    self.sent = len(spoken)
                    self.q.put(spoken)
            self.closed = True
            self.q.put(None)

    def batches(self, timeout: float = 120.0):
        """Sentences as they become ready — whatever has piled up is joined, so the voice gets fewer, longer requests."""
        while True:
            try:
                item = self.q.get(timeout=timeout)
            except queue.Empty:
                return
            if item is None:
                return
            parts, done = [item], False
            while True:
                try:
                    more = self.q.get_nowait()
                except queue.Empty:
                    break
                if more is None:
                    done = True
                    break
                parts.append(more)
            yield " ".join(parts)
            if done:
                return


class LiveSpeaker:
    """Says a SentenceFeed on the PC's speakers. Starts with the first sentence; stop() cuts it off."""

    def __init__(self, speech, limit: int = 450):
        self.speech = speech
        self.feed = SentenceFeed(limit)
        self.stopped = False
        self.started = threading.Event()         # set when the first words are being spoken
        self.thread = threading.Thread(target=self._run, daemon=True, name="live-speech")
        self.thread.start()

    def _run(self) -> None:
        for text in self.feed.batches():
            if self.stopped:
                continue
            self.started.set()
            try:
                if self.speech.speak(text) is False:
                    self.stopped = True
            except Exception as e:
                print(f"[voice] couldn't speak: {e}")

    def finish(self, final_text: str = "") -> None:
        self.feed.finish(final_text)

    def stop(self) -> None:
        self.stopped = True
        try:
            self.speech.stop()
        except Exception:
            pass

    def alive(self) -> bool:
        return self.thread.is_alive()

    def wait(self, timeout: float | None = None) -> None:
        self.thread.join(timeout)
