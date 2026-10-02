"""Presence awareness: Nova notices when you sit down and when you walk away.

  • Sit down  → "Welcome back, Alex" (first time today: your morning briefing), plus anything Nova wanted to
                say while you were away.
  • Walk away → Nova stops talking, holds what it wanted to say until you're back (reminders still reach Telegram),
                and can lock the PC.

Uses the webcam (a face detector running locally on the CPU a couple of times a second — no pictures are kept)
and, on Windows, keyboard/mouse activity, so you count as "here" while typing even if the camera can't see you.
"""
from __future__ import annotations

import datetime as dt
import platform
import threading
import time

from . import context


def seconds_since_input() -> float | None:
    """Seconds since the last keyboard/mouse input (Windows), else None."""
    if platform.system() != "Windows":
        return None
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):     # type: ignore[attr-defined]
            return None
        return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0  # type: ignore[attr-defined]
    except Exception:
        return None


def lock_pc() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.LockWorkStation())                     # type: ignore[attr-defined]
    except Exception:
        return False


class Presence:
    def __init__(self, cfg=None):
        self.cfg = cfg
        self.enabled = False
        self.present: bool | None = None          # None = not sure yet
        self.since = time.time()
        self.last_seen = 0.0
        self.seen_streak_start: float | None = None
        self.faces = 0
        self.error = ""
        self.locked_at = 0.0
        self.held: list[str] = []                 # things Nova wanted to say while you were away
        self.last_arrival_day = ""
        self._detector = None
        self._last_run = 0.0
        self._last_ms = 0
        self._lock = threading.Lock()

    def _c(self) -> dict:
        return dict(((self.cfg or context.cfg or {}).get("presence") or {}))

    # ── on / off ──────────────────────────────────────────
    def start(self) -> str:
        if self.enabled:
            return "Presence awareness is already on."
        try:
            import mediapipe  # noqa: F401
            from mediapipe.tasks.python import BaseOptions, vision

            from .camera import model_path
            opts = vision.FaceDetectorOptions(base_options=BaseOptions(model_asset_path=str(model_path("face"))),
                                              running_mode=vision.RunningMode.VIDEO, min_detection_confidence=0.55)
            self._detector = vision.FaceDetector.create_from_options(opts)
        except ImportError:
            self.error = "MediaPipe isn't installed — run update.bat (or: .venv\\Scripts\\pip install mediapipe)."
            return self.error
        except Exception as e:
            self.error = f"Couldn't load the face-detection model: {e}"
            return self.error
        from .camera import hub
        h = hub(self.cfg)
        h.subscribe("presence", self.on_frame)
        self.enabled, self.error = True, ""
        self.present, self.since, self.last_seen = None, time.time(), time.time()
        if not h.wait_ready() and (h.error or not h.running):
            self.stop()
            self.error = h.error
            return h.error
        self._log("Presence awareness on")
        return "Presence awareness is on — I'll greet you when you sit down and pause when you leave."

    def stop(self) -> str:
        from .camera import hub
        hub(self.cfg).unsubscribe("presence")
        self.enabled = False
        if self._detector:
            try:
                self._detector.close()
            except Exception:
                pass
            self._detector = None
        was_away = self.present is False
        self.present = None
        if was_away:
            self._deliver_held()
        return "Presence awareness is off."

    def status(self) -> dict:
        return {"enabled": self.enabled, "present": self.present, "since": self.since, "faces": self.faces,
                "for_seconds": int(time.time() - self.since), "held": len(self.held), "error": self.error}

    # ── per frame (camera thread) ─────────────────────────
    def on_frame(self, frame, ts: float) -> None:
        if not self._detector or ts - self._last_run < 1.0 / float(self._c().get("checks_per_second", 2)):
            return
        self._last_run = ts
        import cv2
        import mediapipe as mp
        small = cv2.resize(frame, (320, int(frame.shape[0] * 320 / frame.shape[1])))
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        ms = max(self._last_ms + 1, int(ts * 1000))
        self._last_ms = ms
        res = self._detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ms)
        self.update(len(res.detections), ts, seconds_since_input())

    def update(self, faces: int, now: float, idle: float | None = None) -> str | None:
        """Decide present/away from one observation. Returns "arrived" / "left" when that changes."""
        c = self._c()
        away_after = float(c.get("away_seconds", 60))
        arrive_after = float(c.get("arrive_seconds", 2))
        typing = idle is not None and idle < float(c.get("input_counts_seconds", 20))
        self.faces = faces
        here = faces > 0 or typing
        with self._lock:
            if here:
                self.last_seen = now
                if self.seen_streak_start is None:
                    self.seen_streak_start = now
            else:
                self.seen_streak_start = None
            if self.present is not True and here and (typing or now - self.seen_streak_start >= arrive_after):
                first = self.present is None
                away_for = now - self.since
                self.present, self.since = True, now
                if not first:
                    self._arrived(away_for)
                return "arrived"
            if self.present is not False and not here and now - self.last_seen >= away_after:
                self.present, self.since = False, now
                self._left()
                return "left"
            if self.present is False and not here:
                lock_after = float(c.get("lock_after_seconds", 120))
                if c.get("lock_pc", False) and not self.locked_at and now - self.since >= max(0.0, lock_after - away_after):
                    self.locked_at = now
                    if lock_pc():
                        self._log("🔒 Locked the PC (you walked away)")
        return None

    # ── what happens ──────────────────────────────────────
    def _left(self) -> None:
        self.locked_at = 0.0
        self._log("🚶 You left the desk")
        if context.speech:
            try:
                context.speech.stop()
            except Exception:
                pass
        if context.store:
            context.store.set_status("away")

    def _arrived(self, away_for: float) -> None:
        c = self._c()
        owner = (context.cfg or {}).get("assistant", {}).get("owner", "") if context.cfg else ""
        today = dt.datetime.now().date().isoformat()
        first_today = self.last_arrival_day != today
        self.last_arrival_day = today
        self._log(f"👋 You're back (away {self._human(away_for)})")
        if context.store:
            context.store.set_status("idle")
        if not c.get("greet", True):
            self._deliver_held()
            return
        if first_today and c.get("morning_briefing", True) and dt.datetime.now().hour < 12 and context.agent:
            prompt = str(c.get("briefing_prompt") or "I just sat down at my desk. Give me my short morning briefing: "
                         "today's calendar, the weather and anything urgent.")
            threading.Thread(target=self._brief, args=(prompt,), daemon=True, name="presence-brief").start()
            return
        if away_for < float(c.get("greet_after_seconds", 300)):
            self._deliver_held()
            return
        name = f", {owner}" if owner else ""
        extra = ""
        if self.held:
            extra = " While you were away: " + " ".join(self.held[-5:])
            self.held.clear()
        waiting = context.agent.waiting_session() if context.agent else None
        if waiting:
            extra += " I'm still waiting for your yes or no on something."
        self._say(f"Welcome back{name}.{extra}", force=True)

    def _brief(self, prompt: str) -> None:
        try:
            reply = context.agent.handle(prompt, "voice")
            held = (" Also, while you were away: " + " ".join(self.held[-5:])) if self.held else ""
            self.held.clear()
            self._say(reply.text + held, force=True)
        except Exception as e:
            print(f"[presence] briefing failed: {e}")

    def _deliver_held(self) -> None:
        if self.held:
            text = "While you were away: " + " ".join(self.held[-5:])
            self.held.clear()
            self._say(text, force=True)

    def hold_or_say(self, text: str) -> bool:
        """Used for everything Nova says out of the blue. Returns True if it was held because you're away."""
        if self.enabled and self.present is False:
            self.held.append(text.strip())
            del self.held[:-10]             # reminders/routines already go to Telegram as well
            return True
        return False

    def _say(self, text: str, force: bool = False) -> None:
        speak = getattr(context, "speak_now", None)
        if speak:
            speak(text)
        elif context.announce:
            context.announce(text)

    @staticmethod
    def _human(s: float) -> str:
        s = int(s)
        if s < 90:
            return f"{s} s"
        if s < 5400:
            return f"{round(s / 60)} min"
        return f"{s / 3600:.1f} h"

    @staticmethod
    def _log(title: str) -> None:
        if context.store:
            context.store.log("presence", "presence", title, turn=0)


_presence: Presence | None = None


def presence() -> Presence:
    global _presence
    if _presence is None:
        _presence = Presence()
    return _presence
