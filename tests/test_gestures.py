"""Gesture control (synthetic hands — no webcam needed) and the roadmap → Projects sync."""
import time

import pytest

from nova import context
from nova.agent import Agent
from nova.gestures import GestureEngine, Tracker, classify
from nova.llm import LLMReply, ToolCall

EXT = {"pip": 0.50, "dip": 0.44, "tip": 0.38}
FOLD = {"pip": 0.52, "dip": 0.58, "tip": 0.64}
XS = {"index": 0.44, "middle": 0.50, "ring": 0.56, "pinky": 0.61}
THUMBS = {
    "out": [(0.42, 0.75), (0.37, 0.70), (0.33, 0.66), (0.29, 0.62)],
    "in": [(0.44, 0.74), (0.45, 0.70), (0.46, 0.68), (0.46, 0.66)],
    "up": [(0.43, 0.72), (0.42, 0.60), (0.41, 0.50), (0.40, 0.42)],
    "down": [(0.43, 0.78), (0.42, 0.84), (0.41, 0.88), (0.40, 0.95)],
}


def hand(ext=(), thumb="in", dx=0.0, dy=0.0, pinch=False):
    """21 landmarks for a hand facing the camera, fingers up. ext = names of stretched fingers."""
    pts = [(0.5, 0.8)] + THUMBS[thumb]
    for name in ("index", "middle", "ring", "pinky"):
        x = XS[name]
        f = EXT if name in ext else FOLD
        pts += [(x, 0.60), (x, f["pip"]), (x, f["dip"]), (x, f["tip"])]
    if pinch:                              # index tip meets the thumb tip
        pts[8] = (pts[4][0] + 0.02, pts[4][1] - 0.01)
    return [(x + dx, y + dy) for x, y in pts]


ALL = ("index", "middle", "ring", "pinky")


@pytest.mark.parametrize("lm,name", [
    (hand(ALL, "out"), "palm"),
    (hand((), "in"), "fist"),
    (hand((), "up"), "thumbs_up"),
    (hand((), "down"), "thumbs_down"),
    (hand(("index",), "in"), "point"),
    (hand(("index", "middle"), "in"), "victory"),
    (hand(ALL, "in"), "other"),
])
def test_classify_shapes(lm, name):
    assert classify(lm) == name


def test_classify_is_position_independent():
    assert classify(hand(ALL, "out", dx=-0.2, dy=-0.1)) == "palm"
    assert classify(hand((), "up", dx=0.25)) == "thumbs_up"


def _feed(tr, frames, t0=0.0, fps=12):
    out = []
    for i, lm in enumerate(frames):
        out += tr.update(lm, t0 + i / fps)
    return out


def test_hold_fires_once_and_needs_the_full_hold():
    tr = Tracker()
    ev = _feed(tr, [hand((), "up")] * 4)                     # 0.25 s — too short
    assert ("gesture", "thumbs_up") not in ev
    ev = _feed(Tracker(), [hand((), "up")] * 20)             # 1.6 s — fires exactly once
    assert ev.count(("gesture", "thumbs_up")) == 1


def test_gesture_fires_again_after_letting_go():
    tr = Tracker()
    ev = _feed(tr, [hand((), "in")] * 10 + [None] * 3 + [hand((), "in")] * 10)
    assert ev.count(("gesture", "fist")) == 2


def test_swipe_right_and_left_without_palm_stop():
    tr = Tracker()
    frames = [hand(ALL, "out", dx=-0.2 + i * 0.1) for i in range(5)]      # fast move to the right
    ev = _feed(tr, frames)
    assert ("gesture", "swipe_right") in ev and ("gesture", "palm") not in ev
    tr2 = Tracker()
    ev = _feed(tr2, [hand(ALL, "out", dx=0.2 - i * 0.1) for i in range(5)], t0=5)
    assert ("gesture", "swipe_left") in ev


def test_moving_palm_does_not_trigger_stop():
    tr = Tracker()
    frames = [hand(ALL, "out", dx=0.05 * (i % 4)) for i in range(12)]    # waving slowly
    assert ("gesture", "palm") not in _feed(tr, frames)


def test_pointer_and_pinch_events():
    tr = Tracker(smooth=1.0)
    ev = _feed(tr, [hand(("index",), "in")] * 3)
    ptr = [e for e in ev if e[0] == "pointer"]
    assert ptr and 0 <= ptr[-1][1] <= 1 and 0 <= ptr[-1][2] <= 1
    ev = _feed(tr, [hand(("index",), "out", pinch=True)] * 4 + [hand(("index",), "out")] * 4, t0=1)
    kinds = [e[0] for e in ev]
    assert kinds.index("pinch_down") < kinds.index("pinch_up")
    assert tr.current != "pinch"


class FakeMouse:
    def __init__(self):
        self.position, self.log = (0, 0), []

    def press(self, b):
        self.log.append("down")

    def release(self, b):
        self.log.append("up")


def test_engine_moves_mouse_and_clicks(nova):
    nova[0]["gestures"] = {"style": "finger"}
    e = GestureEngine(nova[0])
    e._mouse, e._screen = FakeMouse(), (1920, 1080)
    for i in range(3):
        e.process(hand(("index",), "in"), i / 12)
    assert e._mouse.position != (0, 0)
    for i in range(3):
        e.process(hand(("index",), "out", pinch=True), 1 + i / 12)
    for i in range(3):
        e.process(hand(("index",), "out"), 1.3 + i / 12)
    assert e._mouse.log == ["down", "up"]
    assert e.recent()[-1]["gesture"] == "pinch"


def test_mouse_can_be_switched_off(nova):
    cfg = nova[0]
    cfg["gestures"] = {"mouse": False, "style": "finger"}
    e = GestureEngine(cfg)
    e._mouse, e._screen = FakeMouse(), (1920, 1080)
    for i in range(4):
        e.process(hand(("index",), "in"), i / 12)
    assert e._mouse.position == (0, 0)


def test_palm_stops_speech_and_thumbs_up_answers_the_question(nova, monkeypatch):
    cfg, tmp = nova
    stopped = []
    monkeypatch.setattr(context.speech, "stop", lambda: stopped.append(1), raising=False)
    said = []
    monkeypatch.setattr(context, "announce", said.append)
    junk = tmp / "files" / "old.txt"
    junk.write_text("x")
    agent = Agent(cfg, context.llm)
    monkeypatch.setattr(context, "agent", agent)
    context.llm.queue = [LLMReply("", [ToolCall("d", "delete_path", {"path": str(junk)})])]
    assert "Shall I go ahead" in agent.handle("delete old.txt", "voice").text
    assert agent.waiting_session() == "voice"

    cfg["gestures"] = {"style": "finger"}
    e = GestureEngine(cfg)
    for i in range(10):
        e.process(hand(ALL, "out"), i / 12)
    assert stopped and e.recent()[-1]["action"] == "stop"

    context.llm.queue = [LLMReply("Deleted it.")]
    for i in range(12):
        e.process(hand((), "up"), 5 + i / 12)
    for _ in range(40):
        if said:
            break
        time.sleep(0.05)
    assert said == ["Deleted it."] and not junk.exists()
    assert agent.waiting_session() is None
    assert e.recent()[-1]["result"] == "answered yes"


def test_yes_no_from_yaml_booleans(nova):
    cfg = nova[0]
    cfg["gestures"] = {"style": "finger", "actions": {"thumbs_up": True, "thumbs_down": False, "fist": "none"}}
    a = GestureEngine(cfg).actions()
    assert a["thumbs_up"] == "yes" and a["thumbs_down"] == "no" and a["fist"] == "none" and a["palm"] == "stop"


def test_gesture_tool_and_help(nova):
    from nova.tools import REGISTRY, select_tools
    assert "gesture_control" in {t.name for t in select_tools("turn on gesture control")}
    out = REGISTRY["gesture_control"].run({"action": "help"})
    assert "Move your hand to move the cursor" in out and "👍 Thumbs up: press Enter" in out
    assert "off" in REGISTRY["gesture_control"].run({"action": "status"})


def test_dashboard_gesture_api(nova):
    from nova.dashboard.server import Dashboard  # noqa: F401  (routes import the engine lazily)
    from nova.gestures import engine
    st = engine().status()
    assert st["enabled"] is False and "events" not in st and st["mouse"] in (True, False)


# ── roadmap → Projects ────────────────────────────────────
def test_roadmap_sync_creates_and_moves_projects(nova, tmp_path):
    from nova import roadmap
    s = context.store
    f = tmp_path / "roadmap.yaml"
    f.write_text("- {id: a, title: 'Nova upgrade: A', status: doing}\n- {id: b, title: 'Nova upgrade: B', status: todo}\n")
    assert roadmap.sync(s, f) == {"added": 2, "moved": 0}
    rows = {r["text"]: r["id"] for r in s.db.execute("SELECT id, text FROM memories WHERE kind='project'")}
    a, b = rows["Nova upgrade: A"], rows["Nova upgrade: B"]
    assert s.get_tracking(f"memory:{a}")["status"] == "doing" and s.get_tracking(f"memory:{a}")["pinned"]
    assert s.get_tracking(f"memory:{b}")["status"] == "todo"

    s.set_tracking(f"memory:{b}", status="waiting", note="mine")          # your own change survives a re-sync
    assert roadmap.sync(s, f) == {"added": 0, "moved": 0}
    assert s.get_tracking(f"memory:{b}")["status"] == "waiting"

    f.write_text("- {id: a, title: 'Nova upgrade: A', status: done}\n- {id: b, title: 'Nova upgrade: B', status: doing}\n")
    assert roadmap.sync(s, f) == {"added": 0, "moved": 2}
    ta, tb = s.get_tracking(f"memory:{a}"), s.get_tracking(f"memory:{b}")
    assert ta["status"] == "done" and not ta["pinned"]
    assert tb["status"] == "doing" and tb["note"] == "mine"
    assert s.db.execute("SELECT COUNT(*) FROM memories WHERE kind='project'").fetchone()[0] == 2


def test_real_roadmap_file_is_valid():
    from nova import roadmap
    items = roadmap.load()
    assert [i["id"] for i in items][:5] == ["gesture-control", "presence-awareness", "screen-watcher", "missions",
                                            "nightly-dreaming"]
    assert all(i["status"] in ("todo", "doing", "done") for i in items)


# ── camera + MediaPipe plumbing (fake webcam and fake model) ──
class P:
    def __init__(self, x, y):
        self.x, self.y, self.z = x, y, 0.0


def test_on_frame_runs_mediapipe_image_path(nova):
    import numpy as np
    e = GestureEngine(nova[0])
    seen = []

    class FakeLandmarker:
        def detect_for_video(self, image, ms):
            seen.append((image.width, image.height, ms))
            return type("R", (), {"hand_landmarks": [[P(x, y) for x, y in hand((), "up")]]})()

    e.landmarker = FakeLandmarker()
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    for i in range(12):
        e.on_frame(frame, 100 + i / 10)
    assert seen[0][:2] == (480, 270) and all(b[2] > a[2] for a, b in zip(seen, seen[1:]))
    assert e.hand and e.gesture == "thumbs_up" and e.fps > 0


def test_camera_hub_shares_frames_and_stops_when_unused(nova, monkeypatch):
    import numpy as np

    from nova.camera import CameraHub

    class Cap:
        released = False

        def isOpened(self):
            return True

        def read(self):
            f = np.zeros((72, 128, 3), dtype=np.uint8)
            f[:, :10] = 255                       # white stripe on the left
            return True, f

        def release(self):
            Cap.released = True

    h = CameraHub(fps=50, mirror=True)
    monkeypatch.setattr(h, "_open", lambda: Cap())
    got = []
    h.subscribe("a", lambda f, t: got.append(f[0, -1, 0]))
    for _ in range(50):
        if len(got) > 3:
            break
        time.sleep(0.02)
    assert got and got[-1] == 255                 # mirrored: the stripe is now on the right
    assert h.latest() is not None and h.preview_jpeg()[:2] == b"\xff\xd8"
    snap = h.snapshot(nova[1] / "snap.jpg")
    assert snap and snap.exists()
    h.unsubscribe("a")
    for _ in range(50):
        if not h.running:
            break
        time.sleep(0.02)
    assert not h.running and Cap.released


# ── hand mode: whole hand = cursor, ✊ = click, swipes, push/pull = zoom, 👍 Enter, 👎 Delete ──
from nova.gestures import HandTracker  # noqa: E402


def big(lm, k):
    """The same hand k times bigger (closer to the camera)."""
    cx, cy = 0.5, 0.7
    return [(cx + (x - cx) * k, cy + (y - cy) * k) for x, y in lm]


OPEN = hand(ALL, "in")                     # a relaxed open hand ("other"), like most people hold it


def kinds(ev):
    return [e[0] if e[0] != "gesture" else e[1] for e in ev]


def test_whole_hand_moves_the_pointer():
    tr = HandTracker(smooth=1.0)
    ev = _feed(tr, [OPEN, hand(ALL, "out"), hand(ALL, "in", dx=0.05), hand(ALL, "in", dx=0.05, dy=0.05)])
    ptr = [e for e in ev if e[0] == "pointer"]
    assert len(ptr) == 4
    assert ptr[2][1] > ptr[1][1] and ptr[3][2] > ptr[2][2]               # right, then down


def test_close_hand_clicks_at_the_aimed_spot_and_opening_lets_go():
    tr = HandTracker(smooth=1.0)
    _feed(tr, [OPEN] * 3)
    aimed = tr.pointer
    ev = _feed(tr, [hand((), "in")] * 4, t0=1)
    assert kinds(ev).count("click") == 1 and "pointer" not in kinds(ev)   # cursor holds still for the click
    assert tr.closed and tr.pointer == aimed
    ev = _feed(tr, [OPEN] * 3, t0=2)
    assert "click" not in kinds(ev) and not tr.closed
    ev = _feed(tr, [hand((), "in")] * 3, t0=3)                            # close again = another click
    assert kinds(ev).count("click") == 1


def test_close_and_move_drags():
    tr = HandTracker(smooth=1.0)
    _feed(tr, [OPEN] * 3)
    ev = _feed(tr, [hand((), "in", dx=0.02 * i) for i in range(8)], t0=1)
    k = kinds(ev)
    assert k.index("click") < k.index("drag_start") < len(k) and "pointer" in k[k.index("drag_start"):]
    ev = _feed(tr, [OPEN] * 3, t0=2)
    assert "drag_end" in kinds(ev)


def test_one_flickering_fist_frame_is_not_a_click():
    tr = HandTracker(smooth=1.0)
    ev = _feed(tr, [OPEN, hand((), "in"), OPEN, OPEN])
    assert "click" not in kinds(ev)


def test_swipes_and_cursor_goes_back():
    tr = HandTracker(smooth=1.0)
    _feed(tr, [OPEN] * 4)
    before = tr.pointer
    ev = _feed(tr, [hand(ALL, "in", dx=0.1 * i) for i in range(4)], t0=1)
    assert "swipe_right" in kinds(ev)
    restore = [e for e in ev if e[0] == "restore"][0]
    assert (restore[1], restore[2]) == before
    tr2 = HandTracker(smooth=1.0)
    ev = _feed(tr2, [hand(ALL, "out", dx=0.2 - 0.1 * i) for i in range(4)])
    assert "swipe_left" in kinds(ev)


def test_slow_movement_is_not_a_swipe():
    tr = HandTracker(smooth=1.0)
    ev = _feed(tr, [hand(ALL, "in", dx=0.02 * i) for i in range(15)])
    assert "swipe_right" not in kinds(ev) and "swipe_left" not in kinds(ev)


def test_push_forward_zooms_in_pull_back_zooms_out():
    tr = HandTracker(smooth=1.0)
    ev = _feed(tr, [OPEN] * 8)
    assert "zoom" not in kinds(ev)
    ev = _feed(tr, [big(OPEN, 1.3)] * 3, t0=1)
    assert [e for e in ev if e[0] == "zoom"] == [("zoom", 1)]
    ev = _feed(tr, [big(OPEN, 1.3)] * 8, t0=2)                           # holding there = no more zoom
    assert "zoom" not in kinds(ev)
    ev = _feed(tr, [OPEN] * 3, t0=3)
    assert ("zoom", -1) in ev


def test_thumbs_up_and_down_in_hand_mode():
    tr = HandTracker(smooth=1.0)
    ev = _feed(tr, [hand((), "up")] * 12)
    assert kinds(ev).count("thumbs_up") == 1 and "click" not in kinds(ev)
    ev = _feed(HandTracker(smooth=1.0), [hand((), "down")] * 12)
    assert kinds(ev).count("thumbs_down") == 1


class Keys:
    def __init__(self):
        self.log = []


def test_hand_engine_actions(nova, monkeypatch):
    import nova.gestures as G
    cfg = nova[0]
    cfg["gestures"] = {}
    e = GestureEngine(cfg)
    assert e.style() == "hand" and isinstance(e.tracker, HandTracker)
    a = e.actions()
    assert a["thumbs_up"] == "enter" and a["thumbs_down"] == "delete"
    assert a["swipe_left"] == "back" and a["swipe_right"] == "open_dashboard"
    pressed = []
    monkeypatch.setattr(G, "_press", pressed.append)
    opened = []
    monkeypatch.setattr(e, "_open_dashboard", lambda: opened.append(1) or "dashboard")
    zooms = []
    monkeypatch.setattr(e, "_zoom", zooms.append)
    e._mouse, e._screen = FakeMouse(), (1920, 1080)

    t = 0.0
    for lm in [OPEN] * 4:
        e.process(lm, t)
        t += 1 / 12
    assert e._mouse.position != (0, 0)
    for lm in [hand((), "in")] * 3 + [OPEN] * 3:
        e.process(lm, t)
        t += 1 / 12
    assert e._mouse.log == ["down", "up"] and e.recent()[-1]["gesture"] == "grab"
    for lm in [hand((), "up")] * 10:
        e.process(lm, t)
        t += 1 / 12
    for lm in [None, None] + [hand((), "down")] * 10:
        e.process(lm, t)
        t += 1 / 12
    assert pressed == ["enter", "delete"]
    for lm in [None] + [OPEN] * 3 + [hand(ALL, "in", dx=-0.12 * i) for i in range(1, 4)]:
        e.process(lm, t)
        t += 1 / 12
    assert pressed[-1] == "alt+left"
    t += 2
    for lm in [OPEN] * 3 + [hand(ALL, "in", dx=0.12 * i) for i in range(1, 4)]:
        e.process(lm, t)
        t += 1 / 12
    assert opened == [1]
    t += 2
    for lm in [OPEN] * 8 + [big(OPEN, 1.3)] * 3:
        e.process(lm, t)
        t += 1 / 12
    assert zooms == [1]
    assert e.status()["style"] == "hand"


def test_thumbs_up_says_yes_when_nova_is_asking(nova, monkeypatch):
    import nova.gestures as G
    cfg, tmp = nova
    cfg["gestures"] = {}
    pressed = []
    monkeypatch.setattr(G, "_press", pressed.append)
    agent = Agent(cfg, context.llm)
    monkeypatch.setattr(context, "agent", agent)
    monkeypatch.setattr(agent, "waiting_session", lambda: "voice")
    monkeypatch.setattr(agent, "handle", lambda w, s: type("R", (), {"text": w, "files": []})())
    monkeypatch.setattr(context, "announce", lambda t: None)
    e = GestureEngine(cfg)
    assert e.do("enter") == "answered yes" and e.do("delete") == "answered no" and pressed == []


def test_camera_falls_back_to_another_webcam_and_explains_problems(monkeypatch):
    from nova.camera import CameraHub
    h = CameraHub(index=0)
    tried = []

    def fake_try(index, backend):
        tried.append(index)
        return object() if index == 1 else None
    monkeypatch.setattr(h, "_try", fake_try)
    assert h._open() is not None and h.using == 1 and tried[0] == 0
    monkeypatch.setattr(h, "_try", lambda i, b: None)
    assert h._open() is None
    assert "Turn on gestures" in CameraHub().message()
    h.error = "Couldn't get a picture from webcam 0."
    assert h.message() == h.error
    jpg = h.placeholder_jpeg()
    assert jpg[:2] == b"\xff\xd8"


def test_hub_is_rebuilt_when_the_webcam_number_changes(nova):
    from nova import camera
    camera._hub = None
    a = camera.hub({"gestures": {"camera": 0}})
    b = camera.hub({"gestures": {"camera": 1}})
    assert a is not b and b.index == 1
    camera._hub = None
