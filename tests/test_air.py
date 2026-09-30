"""Air mouse upgrade: smooth pointer, pinch = click / double-click / hold = right-click / move = drag, ✌️ scroll,
hover labels for files, folders, links and buttons."""
import random

from nova import air
from nova.gestures import GestureEngine, Tracker
from test_gestures import ALL, _feed, hand


def test_one_euro_is_steady_when_still_and_quick_when_moving():
    f = air.OneEuro()
    random.seed(1)
    out = [f(0.5 + random.uniform(-0.01, 0.01), i / 12) for i in range(40)]
    assert max(out[10:]) - min(out[10:]) < 0.012                    # jitter damped
    g = air.OneEuro()
    for i in range(10):
        g(0.2, i / 12)
    vals = [g(0.8, (10 + i) / 12) for i in range(4)]
    assert vals[-1] > 0.7                                            # catches up within a third of a second


def pinch_frames(n, dx=0.0):
    return [hand(("index",), "out", pinch=True, dx=dx)] * n


def point(n, dx=0.0):
    return [hand(("index",), "in", dx=dx)] * n


def test_quick_pinch_clicks_and_two_pinches_double_click(nova):
    tr = Tracker(smooth=1.0)
    ev = _feed(tr, point(3) + pinch_frames(3) + point(3) + pinch_frames(3) + point(3))
    assert [e[0] for e in ev].count("click") == 2 and "drag_start" not in [e[0] for e in ev]
    nova[0]["gestures"] = {"style": "finger"}
    e = GestureEngine(nova[0])
    e._mouse = Mouse()
    e._screen = (1920, 1080)
    for i, lm in enumerate(point(3) + pinch_frames(3) + point(3) + pinch_frames(3) + point(3)):
        e.process(lm, i / 12)
    acts = [x["action"] for x in e.recent()]
    assert acts == ["click", "double-click"]


def test_hold_is_right_click_and_move_is_drag():
    tr = Tracker(smooth=1.0)
    ev = _feed(tr, point(3) + pinch_frames(14) + point(3))           # ~1.2 s still
    kinds = [e[0] for e in ev]
    assert kinds.count("right_click") == 1 and "click" not in kinds
    tr = Tracker(smooth=1.0)
    moving = [hand(("index",), "out", pinch=True, dx=i * 0.03) for i in range(8)]
    ev = _feed(tr, point(3) + moving + point(3, dx=0.24))
    kinds = [e[0] for e in ev]
    assert kinds.index("drag_start") < kinds.index("drag_end") and "click" not in kinds and "right_click" not in kinds


def test_pinch_does_not_jump_the_cursor():
    tr = Tracker(smooth=1.0)
    ev = _feed(tr, point(4))
    before = [e for e in ev if e[0] == "pointer"][-1][1:]
    _feed(tr, pinch_frames(4), t0=1)
    assert abs(tr.pointer[0] - before[0]) < 0.02 and abs(tr.pointer[1] - before[1]) < 0.02


def test_victory_scrolls_and_does_not_start_listening():
    tr = Tracker(smooth=1.0)
    frames = [hand(("index", "middle"), "in", dy=i * 0.02) for i in range(12)]
    ev = _feed(tr, frames)
    scrolls = [e[1] for e in ev if e[0] == "scroll"]
    assert scrolls and all(s < 0 for s in scrolls)                   # hand moving down = scroll down
    assert ("gesture", "victory") not in ev
    ev = _feed(Tracker(smooth=1.0), [hand(("index", "middle"), "in")] * 12)
    assert ("gesture", "victory") in ev                             # held still = listen, as before


class Mouse:
    def __init__(self):
        self.position, self.log = (0, 0), []

    def press(self, b):
        self.log.append(("down", str(b)))

    def release(self, b):
        self.log.append(("up", str(b)))

    def click(self, b, n=1):
        self.log.append(("click", str(b), n))

    def scroll(self, dx, dy):
        self.log.append(("scroll", dy))


def test_engine_drag_scroll_and_thumbs_up_opens_hovered_item(nova):
    nova[0]["gestures"] = {"style": "finger"}
    e = GestureEngine(nova[0])
    e._mouse, e._screen = Mouse(), (0, 0, 1920, 1080)
    moving = [hand(("index",), "out", pinch=True, dx=i * 0.03) for i in range(8)]
    for i, lm in enumerate(point(3) + moving + point(3, dx=0.24)):
        e.process(lm, i / 12)
    assert e._mouse.log[0][0] == "down" and e._mouse.log[-1][0] == "up"
    e._mouse.log.clear()
    for i, lm in enumerate([hand(("index", "middle"), "in", dy=j * 0.02) for j in range(10)]):
        e.process(lm, 5 + i / 12)
    assert any(x[0] == "scroll" for x in e._mouse.log)
    e._mouse.log.clear()
    e.tracker.pointer = (0.5, 0.5)
    e.hover = {"kind": "Folder", "name": "Q3 Deals", "text": "📁 Folder · Q3 Deals"}
    assert e.do("yes", "thumbs_up") == "opened Q3 Deals" and e._mouse.log[-1][2] == 2
    assert e.status()["hover"] == "📁 Folder · Q3 Deals"
    assert e.do("auto", "swipe_left") in ("", "back")


def test_describe_files_folders_links_buttons(tmp_path):
    d = tmp_path / "Q3 Deals"
    d.mkdir()
    (d / "a.xlsx").write_bytes(b"x" * 2048)
    f = tmp_path / "Price list.pdf"
    f.write_bytes(b"%PDF" * 1000)
    lab = air.describe({"type": "ListItem", "name": "Q3 Deals", "folder": str(tmp_path)})
    assert lab["emoji"] == "📁" and lab["kind"] == "Folder" and "1 item" in lab["detail"] and "open" in lab["hint"]
    lab = air.describe({"type": "ListItem", "name": "Price list.pdf", "folder": str(tmp_path)})
    assert lab["kind"] == "PDF document" and "3.9 KB" in lab["detail"] and lab["text"].startswith("📄 PDF document · Price list.pdf")
    lab = air.describe({"type": "Hyperlink", "name": "Juniper Mist", "value": "https://mist.com"})
    assert lab["text"] == "🔗 Link · Juniper Mist\nhttps://mist.com"
    assert air.describe({"type": "Button", "name": "Save"})["text"] == "🔘 Button · Save"
    assert air.describe({"type": "Pane", "name": ""}) is None
    assert air.describe({"type": "ListItem", "name": "gone.docx", "folder": str(tmp_path)})["kind"] == "File"


class FakeOverlay:
    def __init__(self):
        self.shown = []

    def show(self, text, x, y):
        self.shown.append(text)

    def hide(self):
        self.shown.append(None)

    def start(self):
        pass


def test_hover_labels_wait_for_the_cursor_to_rest():
    pos = {"p": (100, 100)}
    active = {"on": True}
    seen = []
    ov = FakeOverlay()
    h = air.HoverLabels(lambda: pos["p"], lambda: active["on"], seen.append,
                        inspector=lambda x, y: {"type": "Button", "name": f"B{x}"}, overlay=ov)
    assert h.step(0.0) is None                                       # first sighting
    assert h.step(0.1) is None                                       # not rested long enough
    lab = h.step(0.3)
    assert lab["name"] == "B100" and ov.shown[-1].startswith("🔘 Button · B100")
    assert h.step(0.5) is None                                       # same spot: no repeat
    pos["p"] = (400, 300)
    h.step(0.6)
    assert h.step(0.9)["name"] == "B400"
    active["on"] = False
    h.step(1.0)
    assert ov.shown[-1] is None and seen[-1] is None


def test_all_fingers_open_is_not_pointer():
    tr = Tracker(smooth=1.0)
    ev = _feed(tr, [hand(ALL, "out")] * 4)
    assert not [e for e in ev if e[0] == "pointer"]
