"""Gesture control: steer Nova with your hands through the webcam.

  ✋ open palm (hold)      stop talking
  👍 thumbs up (hold)      "yes" to the question Nova is waiting on
  👎 thumbs down (hold)    "no"
  ✌️ victory (hold)        start listening (like the push-to-talk hotkey)
  ✊ fist (hold)           Escape (close a panel / leave full screen)
  👋 swipe left / right   turn the 3D brain (or a key you choose in Settings)
  👉 point                move the mouse pointer
  🤏 pinch                click — pinch and move to drag (spin the brain or the globe)

Hand tracking runs locally with Google MediaPipe (CPU, a few % load). Nothing leaves the PC.
"""
from __future__ import annotations

import collections
import math
import platform
import threading
import time
from dataclasses import dataclass, field

from . import context

EMOJI = {"palm": "✋", "thumbs_up": "👍", "thumbs_down": "👎", "victory": "✌️", "fist": "✊", "point": "👉",
         "pinch": "🤏", "swipe_left": "👈", "swipe_right": "👉", "none": "", "other": "🖐"}
LABEL = {"palm": "Open palm", "thumbs_up": "Thumbs up", "thumbs_down": "Thumbs down", "victory": "Victory",
         "fist": "Fist", "point": "Point", "pinch": "Pinch", "swipe_left": "Swipe left", "swipe_right": "Swipe right"}
DEFAULT_ACTIONS = {"palm": "stop", "thumbs_up": "yes", "thumbs_down": "no", "victory": "listen", "fist": "escape",
                   "swipe_left": "dashboard", "swipe_right": "dashboard"}
ACTION_CHOICES = ["stop", "yes", "no", "listen", "escape", "dashboard", "none", "key:alt+left", "key:alt+right",
                  "key:space", "key:media_play_pause", "key:media_next", "key:media_previous"]
HOLD = {"palm": 0.5, "thumbs_up": 0.6, "thumbs_down": 0.6, "victory": 0.6, "fist": 0.6}

WRIST, THUMB_IP, THUMB_TIP = 0, 3, 4
INDEX = (5, 6, 8)      # mcp, pip, tip
MIDDLE = (9, 10, 12)
RING = (13, 14, 16)
PINKY = (17, 18, 20)


def _d(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ── shape of one hand ─────────────────────────────────────
def fingers(lm) -> dict:
    """Which fingers are stretched out. lm = 21 (x, y) points, image coords (y grows downwards)."""
    w = lm[WRIST]
    out = {}
    for name, (mcp, pip, tip) in (("index", INDEX), ("middle", MIDDLE), ("ring", RING), ("pinky", PINKY)):
        out[name] = _d(lm[tip], w) > _d(lm[pip], w) * 1.12 and _d(lm[tip], w) > _d(lm[mcp], w) * 1.25
    size = _d(w, lm[MIDDLE[0]]) or 1e-6
    out["thumb"] = _d(lm[THUMB_TIP], lm[INDEX[0]]) > 0.55 * size and \
        _d(lm[THUMB_TIP], w) > _d(lm[THUMB_IP], w) * 1.02
    return out


def hand_size(lm) -> float:
    return _d(lm[WRIST], lm[MIDDLE[0]]) or 1e-6


def pinch_ratio(lm) -> float:
    """Thumb-to-index-tip gap relative to hand size. A curled fist (index tip tucked into the palm) never counts."""
    size = hand_size(lm)
    if _d(lm[INDEX[2]], lm[WRIST]) < 1.05 * size:
        return 9.0
    return _d(lm[THUMB_TIP], lm[INDEX[2]]) / size


def classify(lm, pinching: bool = False) -> str:
    """Name the gesture a hand is making."""
    f = fingers(lm)
    size = hand_size(lm)
    four = [f["index"], f["middle"], f["ring"], f["pinky"]]
    if pinching:
        return "pinch"
    if all(four):
        return "palm" if f["thumb"] else "other"
    if not any(four):
        if f["thumb"]:
            tip, w = lm[THUMB_TIP], lm[WRIST]
            knuckles = [lm[i][1] for i in (5, 9, 13, 17)]
            if tip[1] < min(knuckles) - 0.35 * size:
                return "thumbs_up"
            if tip[1] > max(knuckles) + 0.35 * size and tip[1] > w[1]:
                return "thumbs_down"
            return "other"
        return "fist"
    if f["index"] and f["middle"] and not f["ring"] and not f["pinky"]:
        return "victory"
    if f["index"] and not f["middle"] and not f["ring"] and not f["pinky"]:
        return "point"
    return "other"


# ── turning frames into events ────────────────────────────
@dataclass
class Tracker:
    """Debounces shapes into one-off gestures and tracks swipes, pointing and pinching."""
    box: tuple = (0.18, 0.12, 0.82, 0.72)        # hand area mapped to the whole screen (x0, y0, x1, y1)
    smooth: float = 0.35
    current: str = "none"
    since: float = 0.0
    fired: str = ""
    lost_at: float = 0.0
    pinching: bool = False
    pinch_frames: int = 0
    pointer: tuple | None = None
    trail: collections.deque = field(default_factory=lambda: collections.deque(maxlen=20))
    last_swipe: float = -10.0

    def update(self, lm, t: float) -> list[tuple]:
        events: list[tuple] = []
        if lm is None:
            if self.pinching:
                events.append(("pinch_up",))
            if self.current != "none":
                self.lost_at = t
            self.current, self.fired, self.pinching, self.pinch_frames = "none", "", False, 0
            self.trail.clear()
            self.pointer = None
            return events

        # pinch with hysteresis so it doesn't flicker
        r = pinch_ratio(lm)
        if not self.pinching and r < 0.32:
            self.pinch_frames += 1
            if self.pinch_frames >= 2:
                self.pinching = True
                events.append(("pinch_down",))
        elif self.pinching and r > 0.5:
            self.pinch_frames -= 1
            if self.pinch_frames <= 0:
                self.pinching = False
                self.pinch_frames = 0
                events.append(("pinch_up",))
        elif not self.pinching:
            self.pinch_frames = 0

        g = classify(lm, self.pinching)
        if g != self.current:
            self.current, self.since = g, t
            if g != self.fired:
                self.fired = ""

        # swipe: an open hand moving fast sideways
        cx = sum(lm[i][0] for i in (0, 5, 9, 13, 17)) / 5
        cy = sum(lm[i][1] for i in (0, 5, 9, 13, 17)) / 5
        self.trail.append((t, cx, cy, g))
        recent = [p for p in self.trail if t - p[0] <= 0.4 and p[3] in ("palm", "other")]
        if len(recent) >= 3 and t - self.last_swipe > 1.0:
            dx = recent[-1][1] - recent[0][1]
            dy = abs(recent[-1][2] - recent[0][2])
            if abs(dx) > 0.22 and dy < abs(dx) * 0.6:
                self.last_swipe = t
                self.fired = self.current           # don't also fire "palm" at the end of a swipe
                self.trail.clear()
                events.append(("gesture", "swipe_right" if dx > 0 else "swipe_left"))

        # held shapes fire once
        hold = HOLD.get(g)
        if hold and not self.fired and t - self.since >= hold:
            still = [p for p in self.trail if t - p[0] <= hold]
            moved = max((_d((p[1], p[2]), (cx, cy)) for p in still), default=0)
            if moved < 0.07 or g != "palm":
                self.fired = g
                events.append(("gesture", g))

        # pointer follows the index finger tip (or the pinch point)
        if g in ("point", "pinch") or self.pinching:
            px, py = lm[INDEX[2]]
            if self.pinching:
                px, py = (px + lm[THUMB_TIP][0]) / 2, (py + lm[THUMB_TIP][1]) / 2
            x0, y0, x1, y1 = self.box
            nx = min(1.0, max(0.0, (px - x0) / (x1 - x0)))
            ny = min(1.0, max(0.0, (py - y0) / (y1 - y0)))
            if self.pointer:
                a = self.smooth
                nx, ny = self.pointer[0] + (nx - self.pointer[0]) * a, self.pointer[1] + (ny - self.pointer[1]) * a
            self.pointer = (nx, ny)
            events.append(("pointer", nx, ny))
        else:
            self.pointer = None
        return events


# ── doing things ──────────────────────────────────────────
def _screen_size() -> tuple[int, int]:
    if platform.system() == "Windows":
        try:
            import ctypes
            u = ctypes.windll.user32                        # type: ignore[attr-defined]
            try:
                u.SetProcessDPIAware()
            except Exception:
                pass
            return u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        except Exception:
            pass
    return 1920, 1080


def _press(combo: str) -> None:
    from pynput.keyboard import Controller, Key
    kb = Controller()
    keys = []
    for part in combo.lower().split("+"):
        part = part.strip()
        keys.append(getattr(Key, {"esc": "esc", "escape": "esc", "ctrl": "ctrl", "control": "ctrl"}.get(part, part),
                            None) or part)
    for k in keys:
        kb.press(k)
    for k in reversed(keys):
        kb.release(k)


class GestureEngine:
    def __init__(self, cfg=None):
        self.cfg = cfg
        self.tracker = Tracker()
        self.landmarker = None
        self.enabled = False
        self.error = ""
        self.fps = 0.0
        self.hand = False
        self.gesture = "none"
        self.events: collections.deque = collections.deque(maxlen=50)
        self._seq = 0
        self._last_run = 0.0
        self._last_ts_ms = 0
        self._frames = collections.deque(maxlen=30)
        self._lm = None
        self._mouse = None
        self._screen = None
        self._down = False
        self._pinch_at = 0.0
        self._pinch_moved = 0.0
        self._pinch_start = None
        self._lock = threading.Lock()

    # settings
    def _g(self) -> dict:
        return dict(((self.cfg or context.cfg or {}).get("gestures") or {}))

    def actions(self) -> dict:
        mine = {k: ("yes" if v is True else "no" if v is False else str(v))       # YAML may read yes/no as booleans
                for k, v in (self._g().get("actions") or {}).items()}
        return {**DEFAULT_ACTIONS, **mine}

    # ── on / off ──────────────────────────────────────────
    def start(self) -> str:
        if self.enabled:
            return "Gesture control is already on."
        try:
            import mediapipe as mp  # noqa: F401
            from mediapipe.tasks.python import BaseOptions, vision
        except Exception:
            self.error = "MediaPipe isn't installed — run update.bat (or: .venv\\Scripts\\pip install mediapipe)."
            return self.error
        try:
            from .camera import model_path
            opts = vision.HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(model_path("hand"))),
                running_mode=vision.RunningMode.VIDEO, num_hands=1,
                min_hand_detection_confidence=0.6, min_hand_presence_confidence=0.5, min_tracking_confidence=0.5)
            self.landmarker = vision.HandLandmarker.create_from_options(opts)
        except Exception as e:
            self.error = f"Couldn't load the hand-tracking model: {e}"
            return self.error
        from .camera import hub
        h = hub(self.cfg)
        h.subscribe("gestures", self.on_frame)
        h.overlays["gestures"] = self.draw
        self.enabled, self.error = True, ""
        time.sleep(1.5)
        if h.error:
            self.stop()
            self.error = h.error
            return h.error
        self._log("Gesture control on")
        return "Gesture control is on. ✋ palm = stop, 👍/👎 = yes/no, ✌️ = listen, 👉 point + 🤏 pinch = mouse."

    def stop(self) -> str:
        from .camera import hub
        hub(self.cfg).unsubscribe("gestures")
        if self._down:
            self._mouse_up()
        self.enabled, self.hand, self.gesture = False, False, "none"
        if self.landmarker:
            try:
                self.landmarker.close()
            except Exception:
                pass
            self.landmarker = None
        return "Gesture control is off."

    def status(self) -> dict:
        from .camera import _hub
        return {"enabled": self.enabled, "hand": self.hand, "gesture": self.gesture,
                "emoji": EMOJI.get(self.gesture, ""), "label": LABEL.get(self.gesture, ""),
                "fps": round(self.fps, 1), "error": self.error or (_hub.error if _hub else ""),
                "seq": self._seq, "mouse": bool(self._g().get("mouse", True)),
                "pointer": self.tracker.pointer, "pinching": self.tracker.pinching}

    def recent(self, since: int = 0) -> list[dict]:
        return [e for e in list(self.events) if e["id"] > since]

    # ── per frame ─────────────────────────────────────────
    def on_frame(self, frame, ts: float) -> None:
        want = float(self._g().get("fps", 12))
        if ts - self._last_run < 1.0 / max(2.0, want) or not self.landmarker:
            return
        self._last_run = ts
        import cv2
        import mediapipe as mp
        small = cv2.resize(frame, (480, int(frame.shape[0] * 480 / frame.shape[1])))
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        ms = max(self._last_ts_ms + 1, int(ts * 1000))
        self._last_ts_ms = ms
        res = self.landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ms)
        lm = [(p.x, p.y) for p in res.hand_landmarks[0]] if res.hand_landmarks else None
        self._frames.append(ts)
        if len(self._frames) > 2:
            self.fps = (len(self._frames) - 1) / max(1e-3, self._frames[-1] - self._frames[0])
        self.process(lm, ts)

    def process(self, lm, ts: float) -> None:
        """Feed one hand (or None) through the tracker and act on what it sees. Separate for testing."""
        self._lm = lm
        self.hand = lm is not None
        events = self.tracker.update(lm, ts)
        self.gesture = self.tracker.current
        mouse_on = bool(self._g().get("mouse", True))
        for ev in events:
            kind = ev[0]
            if kind == "gesture":
                self.fire(ev[1])
            elif kind == "pointer" and mouse_on:
                self._move(ev[1], ev[2])
            elif kind == "pinch_down" and mouse_on:
                self._pinch_at, self._pinch_moved, self._pinch_start = ts, 0.0, self.tracker.pointer
                self._mouse_down()
            elif kind == "pinch_up" and mouse_on:
                self._mouse_up()
                self._emit("pinch", "click" if ts - self._pinch_at < 0.35 else "drag")

    def fire(self, gesture: str) -> str:
        action = str(self.actions().get(gesture, "none"))
        result = self.do(action, gesture)
        self._emit(gesture, action, result)
        return result

    def _emit(self, gesture: str, action: str, result: str = "") -> None:
        with self._lock:
            self._seq += 1
            self.events.append({"id": self._seq, "t": time.time(), "gesture": gesture,
                                "emoji": EMOJI.get(gesture, ""), "label": LABEL.get(gesture, gesture),
                                "action": action, "result": result})

    def do(self, action: str, gesture: str = "") -> str:
        try:
            if action in ("none", "", "dashboard"):
                return ""
            if action == "stop":
                if context.speech:
                    context.speech.stop()
                self._log(f"{EMOJI.get(gesture, '')} Stop")
                return "stopped"
            if action in ("yes", "no"):
                return self._answer(action)
            if action == "listen":
                v = getattr(context, "voice", None)
                if v is None:
                    return "voice is off"
                v.hotkey.set()
                return "listening"
            if action == "escape":
                _press("esc")
                return "escape"
            if action.startswith("key:"):
                _press(action[4:])
                return action[4:]
        except Exception as e:
            print(f"[gestures] {action} failed: {e}")
            return f"failed: {e}"
        return ""

    def _answer(self, word: str) -> str:
        agent = getattr(context, "agent", None)
        session = agent.waiting_session() if agent else None
        if not session:
            return "nothing to answer"
        self._log(f"{'👍 Yes' if word == 'yes' else '👎 No'} — answered by gesture")

        def run():
            reply = agent.handle(word, session)
            if context.announce:
                context.announce(reply.text)
            if session == "telegram" and context.notify:
                context.notify(reply.text, reply.files)
        threading.Thread(target=run, daemon=True, name="gesture-answer").start()
        return f"answered {word}"

    # ── mouse ─────────────────────────────────────────────
    def _ctl(self):
        if self._mouse is None:
            from pynput.mouse import Controller
            self._mouse = Controller()
            self._screen = _screen_size()
        return self._mouse

    def _move(self, nx: float, ny: float) -> None:
        m = self._ctl()
        w, h = self._screen
        if self._pinch_start and self.tracker.pinching:
            self._pinch_moved = max(self._pinch_moved, _d((nx, ny), self._pinch_start))
        m.position = (int(nx * (w - 1)), int(ny * (h - 1)))

    @staticmethod
    def _left():
        try:
            from pynput.mouse import Button
            return Button.left
        except Exception:
            return "left"

    def _mouse_down(self) -> None:
        self._ctl().press(self._left())
        self._down = True

    def _mouse_up(self) -> None:
        if self._down:
            self._ctl().release(self._left())
        self._down = False

    # ── preview overlay ───────────────────────────────────
    def draw(self, frame) -> None:
        import cv2
        lm = self._lm
        h, w = frame.shape[:2]
        if lm:
            pts = [(int(x * w), int(y * h)) for x, y in lm]
            for a, b in ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
                         (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20),
                         (0, 17)):
                cv2.line(frame, pts[a], pts[b], (240, 200, 76), 2, cv2.LINE_AA)
            for p in pts:
                cv2.circle(frame, p, 4, (255, 123, 139), -1, cv2.LINE_AA)
        x0, y0, x1, y1 = self.tracker.box
        cv2.rectangle(frame, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), (120, 110, 255), 1)
        label = LABEL.get(self.gesture, "")
        if label:
            cv2.putText(frame, label, (16, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)

    @staticmethod
    def _log(title: str) -> None:
        if context.store:
            context.store.log("gesture", "gestures", title, turn=0)


_engine: GestureEngine | None = None


def engine() -> GestureEngine:
    global _engine
    if _engine is None:
        _engine = GestureEngine()
    return _engine
