"""Gesture control: steer Nova and the PC with your hand through the webcam.

Hand mode (the default):
  🖐 move your hand        the cursor follows it — a label shows what's under it (folder, file, link, button…)
  ✊ close your hand       click (close twice quickly = open / double-click)
  ✊ close + move          drag · open the hand to let go
  👈 swipe left           back (browser, Explorer, …)
  👉 swipe right          show the Nova dashboard
  🔍 hand towards camera  zoom in · pull it back = zoom out
  👍 thumbs up (hold)      Enter ("yes" when Nova is waiting for an answer)
  👎 thumbs down (hold)    Delete ("no" when Nova is waiting for an answer)
  ✌️ victory (hold)        start listening · ✌️ moving up/down = scroll

Finger mode (gestures.style: finger — the older scheme):
  ✋ open palm (hold)      stop talking
  👍 thumbs up (hold)      "yes" to the question Nova is waiting on
  👎 thumbs down (hold)    "no"
  ✌️ victory (hold)        start listening (like the push-to-talk hotkey)
  ✊ fist (hold)           Escape (close a panel / leave full screen)
  👋 swipe left / right   turn the 3D brain (or a key you choose in Settings)
  👉 point                move the mouse pointer — a label shows what's under it (folder, file, link, button…)
  🤏 pinch                click · 🤏🤏 two quick pinches = double-click (open a file / folder)
  🤏 pinch and hold       right-click (the menu for that file, link…)
  🤏 pinch and move       drag (move files, scroll bars, sliders)
  ✌️ two fingers up/down  scroll
  👍 on a file or folder  open it (when Nova isn't waiting for a yes)

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

EMOJI = {"grab": "✊", "zoom_in": "🔍", "zoom_out": "🔎", "palm": "✋", "thumbs_up": "👍", "thumbs_down": "👎", "victory": "✌️", "fist": "✊", "point": "👉",
         "pinch": "🤏", "swipe_left": "👈", "swipe_right": "👉", "none": "", "other": "🖐"}
LABEL = {"grab": "Grab", "zoom_in": "Zoom in", "zoom_out": "Zoom out", "palm": "Open palm", "thumbs_up": "Thumbs up", "thumbs_down": "Thumbs down", "victory": "Victory",
         "fist": "Fist", "point": "Point", "pinch": "Pinch", "swipe_left": "Swipe left", "swipe_right": "Swipe right"}
DEFAULT_ACTIONS = {"palm": "stop", "thumbs_up": "yes", "thumbs_down": "no", "victory": "listen", "fist": "escape",
                   "swipe_left": "auto", "swipe_right": "auto"}
HAND_ACTIONS = {"thumbs_up": "enter", "thumbs_down": "delete", "swipe_left": "back", "swipe_right": "open_dashboard",
                "victory": "listen", "palm": "none"}
ACTION_CHOICES = ["enter", "delete", "back", "forward", "open_dashboard", "stop", "yes", "no", "listen", "escape",
                  "dashboard", "auto", "none", "key:alt+left", "key:alt+right", "key:space", "key:media_play_pause",
                  "key:media_next", "key:media_previous"]
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
    smooth: float = 0.35                          # 1.0 = raw (tests); otherwise the One Euro filter smooths
    hold_right: float = 0.7                       # pinch held this long without moving = right-click
    drag_start: float = 0.03                      # pinch moved this far (share of the screen) = drag
    offset: tuple = (0.0, 0.0)                    # index tip relative to the knuckles (keeps pinches from jumping)
    pinch_t0: float = 0.0
    anchor: tuple | None = None
    dragging: bool = False
    consumed: bool = False
    scroll_y: float | None = None
    scrolled: bool = False
    fx: object = None
    fy: object = None
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
                if self.dragging:
                    events.append(("drag_end",))
            self.dragging, self.anchor, self.scroll_y = False, None, None
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
                self.pinch_t0, self.anchor, self.dragging, self.consumed = t, self.pointer, False, False
                events.append(("pinch_down",))
        elif self.pinching and r > 0.5:
            self.pinch_frames -= 1
            if self.pinch_frames <= 0:
                self.pinching = False
                self.pinch_frames = 0
                events.append(("pinch_up",))
                if self.dragging:
                    events.append(("drag_end",))
                elif not self.consumed:
                    events.append(("click",))
                self.dragging, self.anchor = False, None
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
            if (moved < 0.07 or g not in ("palm", "victory")) and not (g == "victory" and self.scrolled):
                self.fired = g
                events.append(("gesture", g))

        # ✌️ two fingers moving up/down = scroll
        if g == "victory":
            if self.scroll_y is None:
                self.scroll_y, self.scrolled = cy, False
            elif abs(cy - self.scroll_y) > 0.035:
                steps = int((cy - self.scroll_y) / 0.035)
                self.scroll_y += steps * 0.035
                self.scrolled = True
                events.append(("scroll", -steps))            # hand down = page down
        else:
            self.scroll_y, self.scrolled = None, False

        # pointer follows the index finger; while pinching it follows the knuckles + the last offset, so the
        # cursor doesn't jump when the fingertip bends to pinch
        knuckle = ((lm[INDEX[0]][0] + lm[MIDDLE[0]][0]) / 2, (lm[INDEX[0]][1] + lm[MIDDLE[0]][1]) / 2)
        if g in ("point", "pinch") or self.pinching:
            if self.pinching:
                px, py = knuckle[0] + self.offset[0], knuckle[1] + self.offset[1]
            else:
                px, py = lm[INDEX[2]]
                self.offset = (px - knuckle[0], py - knuckle[1])
            x0, y0, x1, y1 = self.box
            nx = min(1.0, max(0.0, (px - x0) / (x1 - x0)))
            ny = min(1.0, max(0.0, (py - y0) / (y1 - y0)))
            if self.smooth < 1.0:
                from .air import OneEuro
                if self.fx is None:
                    self.fx, self.fy = OneEuro(), OneEuro()
                nx, ny = self.fx(nx, t), self.fy(ny, t)
            if self.pinching and not self.dragging:
                if self.anchor is None:
                    self.anchor = (nx, ny)
                start = self.anchor
                if _d((nx, ny), start) > self.drag_start and not self.consumed:
                    self.dragging = True
                    events.append(("drag_start",))
                elif not self.consumed and t - self.pinch_t0 >= self.hold_right:
                    self.consumed = True
                    events.append(("right_click",))
                if not self.dragging:
                    self.pointer = start                     # hold still so the click lands where you aimed
                    return events
            self.pointer = (nx, ny)
            events.append(("pointer", nx, ny))
        else:
            self.pointer = None
            if self.fx is not None:
                self.fx.reset()
                self.fy.reset()
        return events


def palm_centre(lm) -> tuple[float, float]:
    """Wrist + the four knuckles: stays put when the fingers curl, so closing the hand doesn't move the cursor."""
    return (sum(lm[i][0] for i in (0, 5, 9, 13, 17)) / 5, sum(lm[i][1] for i in (0, 5, 9, 13, 17)) / 5)


def palm_size(lm) -> float:
    """Palm length + width: grows as the hand comes towards the camera, whatever the fingers do."""
    return _d(lm[WRIST], lm[MIDDLE[0]]) + _d(lm[INDEX[0]], lm[PINKY[0]])


HAND_HOLD = {"thumbs_up": 0.6, "thumbs_down": 0.6, "victory": 0.6, "palm": 1.2}


@dataclass
class HandTracker:
    """Whole-hand air mouse:
    move the hand = move the cursor · close it (✊) = click, close + move = drag · swipe = gesture ·
    push towards the camera / pull back = zoom · 👍 / 👎 / ✌️ held = gestures."""
    box: tuple = (0.18, 0.12, 0.82, 0.72)
    smooth: float = 0.35
    holds: dict = field(default_factory=lambda: dict(HAND_HOLD))
    zoom: bool = True
    grab_frames: int = 2                      # frames of ✊ before it counts (stops flicker-clicks)
    drag_start: float = 0.05                  # closed hand moved this far (share of the screen) = drag
    swipe_dist: float = 0.26                  # share of the camera picture…
    swipe_time: float = 0.35                  # …covered this fast = swipe
    zoom_step: float = 0.18                   # hand 18 % bigger / smaller = one zoom step
    current: str = "none"
    since: float = 0.0
    fired: str = ""
    lost_at: float = 0.0
    pointer: tuple | None = None
    closed: bool = False
    close_frames: int = 0
    open_frames: int = 0
    grab_at: tuple | None = None
    dragging: bool = False
    base: float | None = None
    seen_at: float = 0.0
    zoom_t: float = -10.0
    last_swipe: float = -10.0
    quiet_until: float = -10.0
    scroll_y: float | None = None
    scrolled: bool = False
    fx: object = None
    fy: object = None
    trail: collections.deque = field(default_factory=lambda: collections.deque(maxlen=30))
    history: collections.deque = field(default_factory=lambda: collections.deque(maxlen=30))

    @property
    def pinching(self) -> bool:               # the dashboard chip shows "grabbing"
        return self.closed

    def _map(self, cx: float, cy: float, t: float) -> tuple[float, float]:
        x0, y0, x1, y1 = self.box
        nx = min(1.0, max(0.0, (cx - x0) / (x1 - x0)))
        ny = min(1.0, max(0.0, (cy - y0) / (y1 - y0)))
        if self.smooth < 1.0:
            from .air import OneEuro
            if self.fx is None:
                self.fx, self.fy = OneEuro(), OneEuro()
            nx, ny = self.fx(nx, t), self.fy(ny, t)
        return nx, ny

    def _reset(self) -> None:
        self.closed, self.close_frames, self.open_frames, self.dragging, self.grab_at = False, 0, 0, False, None
        self.base, self.scroll_y, self.scrolled, self.pointer = None, None, False, None
        self.trail.clear()
        self.history.clear()
        if self.fx is not None:
            self.fx.reset()
            self.fy.reset()

    def update(self, lm, t: float) -> list[tuple]:
        events: list[tuple] = []
        if lm is None:
            if self.dragging:
                events.append(("drag_end",))
            if self.current != "none":
                self.lost_at = t
            self.current, self.fired = "none", ""
            self._reset()
            return events

        g = classify(lm)
        if g != self.current:
            self.current, self.since = g, t
            if g != self.fired:
                self.fired = ""
        if self.base is None:
            self.seen_at = t
        cx, cy = palm_centre(lm)
        size = palm_size(lm)
        nx, ny = self._map(cx, cy, t)
        self.trail.append((t, cx, cy, g))
        self.history.append((t, nx, ny))

        # ✊ close = click · keep it closed and move = drag · open = let go
        fist = g == "fist"
        if not self.closed:
            self.close_frames = self.close_frames + 1 if fist else 0
            if self.close_frames >= self.grab_frames:
                self.closed, self.open_frames, self.dragging = True, 0, False
                self.grab_at = self.pointer or (nx, ny)
                events.append(("click",))
        else:
            if fist:
                self.open_frames = 0
                if not self.dragging and _d((nx, ny), self.grab_at) > self.drag_start:
                    self.dragging = True
                    events.append(("drag_start",))
            else:
                self.open_frames += 1
                if self.open_frames >= 2:
                    self.closed = False
                    self.close_frames = 0
                    if self.dragging:
                        events.append(("drag_end",))
                    self.dragging = False
                    self.base = None                    # re-measure the hand before zooming again

        # 👋 swipe: an open hand moving fast sideways
        if not self.closed and t - self.last_swipe > 1.0:
            recent = [p for p in self.trail if t - p[0] <= self.swipe_time and p[3] in ("palm", "other", "point")]
            if len(recent) >= 3:
                dx = recent[-1][1] - recent[0][1]
                dy = abs(recent[-1][2] - recent[0][2])
                if abs(dx) > self.swipe_dist and dy < abs(dx) * 0.6:
                    self.last_swipe, self.quiet_until = t, t + 0.6
                    self.fired = self.current
                    self.trail.clear()
                    events.append(("gesture", "swipe_right" if dx > 0 else "swipe_left"))
                    t0 = recent[0][0]
                    before = [h for h in self.history if h[0] < t0] or list(self.history)[:1]
                    self.pointer = (before[-1][1], before[-1][2])
                    events.append(("restore", *self.pointer))      # put the cursor back where it was
                    self.base = None
                    return events

        # held shapes fire once (👍 👎 ✌️ …)
        hold = self.holds.get(g)
        if hold and not self.fired and t - self.since >= hold:
            still = [p for p in self.trail if t - p[0] <= hold]
            moved = max((_d((p[1], p[2]), (cx, cy)) for p in still), default=0)
            if (moved < 0.06 or g not in ("palm", "victory")) and not (g == "victory" and self.scrolled):
                self.fired = g
                events.append(("gesture", g))

        # ✌️ two fingers moving up/down = scroll
        if g == "victory":
            if self.scroll_y is None:
                self.scroll_y, self.scrolled = cy, False
            elif abs(cy - self.scroll_y) > 0.035:
                steps = int((cy - self.scroll_y) / 0.035)
                self.scroll_y += steps * 0.035
                self.scrolled = True
                events.append(("scroll", -steps))
        else:
            self.scroll_y, self.scrolled = None, False

        # 🔍 push the hand towards the camera = zoom in, pull it back = zoom out
        open_hand = g in ("palm", "other", "point") and not self.closed
        if self.zoom and open_hand and t >= self.quiet_until:
            if self.base is None:
                self.base, self.seen_at = size, t
            elif t - self.seen_at >= 0.4 and t - self.zoom_t >= 0.3:
                r = size / self.base
                if r > 1 + self.zoom_step or r < 1 / (1 + self.zoom_step):
                    self.zoom_t, self.base = t, size
                    events.append(("zoom", 1 if r > 1 else -1))
                else:
                    self.base += (size - self.base) * 0.04      # slow drift = no zoom
        elif not open_hand:
            self.base = None

        # the cursor follows the whole hand (frozen while making 👍 👎 ✌️ or a still ✊ click)
        frozen = t < self.quiet_until or g in ("thumbs_up", "thumbs_down", "victory") or \
            (fist and not self.dragging) or (self.closed and not self.dragging)
        if not frozen:
            self.pointer = (nx, ny)
            events.append(("pointer", nx, ny))
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
        self.tracker = self._new_tracker()
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
        self._last_click = -10.0
        self.hover: dict | None = None
        self._hover = None
        self._lock = threading.Lock()

    # settings
    def _g(self) -> dict:
        return dict(((self.cfg or context.cfg or {}).get("gestures") or {}))

    def style(self) -> str:
        return "finger" if str(self._g().get("style", "hand")).lower() == "finger" else "hand"

    def actions(self) -> dict:
        hand = self.style() == "hand"
        key = "hand_actions" if hand else "actions"
        mine = {k: ("yes" if v is True else "no" if v is False else str(v))       # YAML may read yes/no as booleans
                for k, v in (self._g().get(key) or {}).items()}
        return {**(HAND_ACTIONS if hand else DEFAULT_ACTIONS), **mine}

    def _new_tracker(self):
        g = self._g()
        reach = float(g.get("reach", 0.64))                     # smaller = less arm movement
        box = (0.5 - reach / 2, 0.44 - reach * 0.42, 0.5 + reach / 2, 0.44 + reach * 0.42)
        if self.style() == "finger":
            return Tracker(box=box)
        acts = self.actions()
        holds = {k: v for k, v in HAND_HOLD.items() if acts.get(k, "none") != "none"}
        return HandTracker(box=box, holds=holds, zoom=bool(g.get("zoom", True)))

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
        self.tracker = self._new_tracker()
        if self._g().get("hover_labels", True):
            from .air import HoverLabels
            self._hover = HoverLabels(self._cursor, lambda: self.enabled and self.tracker.pointer is not None,
                                      self._on_label)
            self._hover.start()
        from .camera import hub
        h = hub(self.cfg)
        h.subscribe("gestures", self.on_frame)
        h.overlays["gestures"] = self.draw
        self.enabled, self.error = True, ""
        if not h.wait_ready() and (h.error or not h.running):
            self.stop()
            self.error = h.error
            return h.error
        self._log("Gesture control on")
        if self.style() == "hand":
            return ("Gesture control is on. Move your hand = cursor, ✊ close = click, swipe left = back, "
                    "swipe right = dashboard, push/pull = zoom, 👍 = Enter, 👎 = Delete.")
        return "Gesture control is on. ✋ palm = stop, 👍/👎 = yes/no, ✌️ = listen, 👉 point + 🤏 pinch = mouse."

    def stop(self) -> str:
        from .camera import hub
        hub(self.cfg).unsubscribe("gestures")
        if self._hover:
            self._hover.stop()
            self._hover = None
        self.hover = None
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
        cam = _hub.state() if _hub else {"running": False, "error": "", "status": "", "dark": False}
        if _hub and self.enabled and (cam["error"] or cam["status"] or cam["dark"] or not cam["running"]):
            cam["message"] = _hub.message()
        return {"enabled": self.enabled, "camera": cam, "hand": self.hand, "gesture": self.gesture,
                "emoji": EMOJI.get(self.gesture, ""), "label": LABEL.get(self.gesture, ""),
                "fps": round(self.fps, 1), "error": self.error or (_hub.error if _hub else ""),
                "seq": self._seq, "mouse": bool(self._g().get("mouse", True)), "style": self.style(),
                "pointer": self.tracker.pointer, "pinching": self.tracker.pinching,
                "hover": (self.hover or {}).get("text", "") if self.tracker.pointer else ""}

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
        hand = isinstance(self.tracker, HandTracker)
        for ev in events:
            kind = ev[0]
            if kind == "gesture":
                self.fire(ev[1])
            elif kind in ("pointer", "restore") and mouse_on:
                self._move(ev[1], ev[2])
            elif kind == "click" and mouse_on:
                double = ts - self._last_click < 0.6          # two quick clicks = the OS sees a double-click
                self._last_click = -10.0 if double else ts
                self._click()
                self._emit("grab" if hand else "pinch", "double-click" if double else "click", self._hover_name())
            elif kind == "zoom" and mouse_on:
                self._zoom(ev[1])
                self._emit("zoom_in" if ev[1] > 0 else "zoom_out", "zoom")
            elif kind == "right_click" and mouse_on:
                self._click(right=True)
                self._emit("pinch", "right-click", self._hover_name())
            elif kind == "drag_start" and mouse_on:
                self._mouse_down()
            elif kind == "drag_end" and mouse_on:
                self._mouse_up()
                self._emit("grab" if hand else "pinch", "drag")
            elif kind == "scroll" and mouse_on:
                self._ctl().scroll(0, ev[1] * int(self._g().get("scroll_speed", 2)))

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
                result = self._answer(action)
                if result == "nothing to answer" and action == "yes" and self.hover and self.tracker.pointer is not None:
                    self._click(double=True)                     # 👍 on a file / folder / link = open it
                    return f"opened {self.hover.get('name', '')}"
                return result
            if action in ("enter", "delete"):                   # answers Nova first if she's asking
                result = self._answer("yes" if action == "enter" else "no")
                if result != "nothing to answer":
                    return result
                _press(action)
                return action
            if action in ("back", "forward"):
                _press("alt+left" if action == "back" else "alt+right")
                return action
            if action == "open_dashboard":
                return self._open_dashboard()
            if action == "auto":                                 # swipe = back / forward in a browser or Explorer
                if gesture in ("swipe_left", "swipe_right") and self._foreground_is_browser_or_explorer():
                    _press("alt+left" if gesture == "swipe_left" else "alt+right")
                    return "back" if gesture == "swipe_left" else "forward"
                return ""
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
            from .air import virtual_screen
            self._screen = virtual_screen() if platform.system() == "Windows" else _screen_size()
        return self._mouse

    def _move(self, nx: float, ny: float) -> None:
        m = self._ctl()
        scr = self._screen
        left, top, w, h = scr if len(scr) == 4 else (0, 0, *scr)
        m.position = (left + int(nx * (w - 1)), top + int(ny * (h - 1)))

    def _cursor(self):
        try:
            return tuple(self._ctl().position)
        except Exception:
            return None

    def _on_label(self, label: dict | None) -> None:
        self.hover = label
        if label and self._g().get("speak_labels", False) and context.speech:
            threading.Thread(target=context.speech.speak, args=(f"{label['kind']}, {label['name']}",),
                             daemon=True).start()

    def _hover_name(self) -> str:
        return (self.hover or {}).get("name", "") if self.hover else ""

    def _click(self, right: bool = False, double: bool = False) -> None:
        try:
            from pynput.mouse import Button
            b = Button.right if right else Button.left
        except Exception:
            b = "right" if right else "left"
        m = self._ctl()
        if hasattr(m, "click"):
            m.click(b, 2 if double else 1)
        else:
            for _ in range(2 if double else 1):
                m.press(b)
                m.release(b)

    def _zoom(self, step: int) -> None:
        """Ctrl + mouse wheel: zooms browsers, Office, Explorer icons, photos, the dashboard…"""
        try:
            from pynput.keyboard import Controller, Key
            kb = Controller()
            kb.press(Key.ctrl)
            try:
                self._ctl().scroll(0, 1 if step > 0 else -1)
            finally:
                kb.release(Key.ctrl)
        except Exception as e:
            print(f"[gestures] zoom failed: {e}")

    def _open_dashboard(self) -> str:
        """Bring the Nova dashboard to the front, or open it in the browser."""
        if platform.system() == "Windows":
            try:
                import ctypes
                u = ctypes.windll.user32                          # type: ignore[attr-defined]
                found = []

                @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
                def each(hwnd, _):
                    buf = ctypes.create_unicode_buffer(300)
                    u.GetWindowTextW(hwnd, buf, 300)
                    if u.IsWindowVisible(hwnd) and "Nova · Second Brain" in buf.value:
                        found.append(hwnd)
                    return True
                u.EnumWindows(each, 0)
                if found:
                    _press("alt")                                  # lets Windows hand over the foreground
                    u.ShowWindow(found[0], 9)                      # SW_RESTORE
                    u.SetForegroundWindow(found[0])
                    return "dashboard"
            except Exception as e:
                print(f"[gestures] couldn't focus the dashboard: {e}")
        import webbrowser
        port = int((((self.cfg or context.cfg or {}).get("dashboard")) or {}).get("port", 8765))
        webbrowser.open(f"http://localhost:{port}")
        return "dashboard"

    @staticmethod
    def _foreground_is_browser_or_explorer() -> bool:
        if platform.system() != "Windows":
            return False
        try:
            import ctypes
            u = ctypes.windll.user32
            buf = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(u.GetForegroundWindow(), buf, 256)
            return buf.value in ("Chrome_WidgetWin_1", "MozillaWindowClass", "CabinetWClass", "ApplicationFrameWindow")
        except Exception:
            return False

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
