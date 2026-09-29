"""Screen watcher (fake screen, fake OCR, temp folders — no desktop needed)."""
import sys
import time

from PIL import Image

from nova import context
from nova.watcher import WatchManager, contains, read_text


def _mgr(monkeypatch, screen_text=None, win=None):
    m = WatchManager()
    m.start = lambda: None                      # tests drive check() by hand
    state = {"text": screen_text or [""], "img": Image.new("RGB", (400, 300), "white"), "wins": win or []}
    m.grab = lambda box=None: state["img"]
    m.read_text = lambda img: state["text"][0]
    m.windows = lambda: [{"title": t, "box": (0, 0, 400, 300)} for t in state["wins"]]
    said, pushed = [], []
    monkeypatch.setattr(context, "announce", said.append)
    monkeypatch.setattr(context, "push", lambda text, files=None: pushed.append((text, files or [])))
    return m, state, said, pushed


def test_contains_is_forgiving():
    assert contains("EXPORT  COMPLETE\n100%", "export complete")
    assert contains("Exportcomplete", "export complete")
    assert not contains("Exporting…", "export complete")


def test_text_appears_fires_once_with_screenshot(nova, monkeypatch):
    m, st, said, pushed = _mgr(monkeypatch, ["Rendering 45%"])
    w = m.add("text_appears", "Export complete")
    assert not m.check(w)
    st["text"][0] = "Export complete - 100%"
    assert m.check(w)
    assert said == ["'Export complete' is on the screen now."]
    assert pushed[0][1] and pushed[0][1][0].endswith(".jpg")
    assert w.id not in m.watches
    row = context.store.db.execute("SELECT status, note FROM watchers WHERE id=?", (w.id,)).fetchone()
    assert row["status"] == "done"


def test_text_disappears_needs_to_have_been_seen(nova, monkeypatch):
    m, st, said, _ = _mgr(monkeypatch, ["Uploading 45%"])
    w = m.add("text_disappears", "Uploading")
    assert not m.check(w) and not m.check(w)
    st["text"][0] = "Upload finished"
    assert m.check(w) and "has gone" in said[0]


def test_stops_changing_after_movement(nova, monkeypatch):
    m, st, said, _ = _mgr(monkeypatch)
    w = m.add("stops_changing", window="")
    w.state["stable_seconds"] = 10
    t = 1000.0
    for i in range(4):                       # progress bar moving
        st["img"] = Image.new("RGB", (400, 300), (0, i * 70, 0))
        assert not m.check(w, t)
        t += 3
    for _ in range(3):                       # still for 6 s — not yet
        assert not m.check(w, t)
        t += 3
    t += 6
    assert m.check(w, t) and "stopped changing" in said[0]


def test_changes_inside_a_window_only(nova, monkeypatch):
    m, st, said, _ = _mgr(monkeypatch, win=["Inbox - Outlook"])
    w = m.add("changes", window="outlook")
    assert not m.check(w)
    st["img"] = Image.new("RGB", (400, 300), "black")
    assert m.check(w) and "outlook" in said[0].lower()
    m2, st2, _, _ = _mgr(monkeypatch, win=["Notepad"])
    w2 = m2.add("changes", window="outlook")
    st2["img"] = Image.new("RGB", (400, 300), "red")
    assert not m2.check(w2)                   # window not open -> no false alarm


def test_window_closes_and_opens(nova, monkeypatch):
    m, st, said, _ = _mgr(monkeypatch, win=["Setup - Zoom Installer"])
    w = m.add("window_closes", "installer")
    assert not m.check(w)
    st["wins"] = []
    assert m.check(w) and said[-1] == "The 'installer' window has closed."
    w2 = m.add("window_opens", "Teams")
    assert not m.check(w2)
    st["wins"] = ["Meeting | Microsoft Teams"]
    assert m.check(w2)


def test_process_exits(nova, monkeypatch):
    m, _, said, _ = _mgr(monkeypatch)
    w = m.add("process_exits", "definitely-not-running-xyz")
    assert m.check(w) and "isn't running" in said[0]
    me = m.add("process_exits", "python" if sys.platform != "win32" else "python.exe")
    assert not m.check(me) and me.state["seen"]


def test_download_finishes(nova, monkeypatch, tmp_path):
    m, _, said, pushed = _mgr(monkeypatch)
    dl = tmp_path / "Downloads"
    dl.mkdir()
    (dl / "old.pdf").write_text("x")
    w = m.add("file_appears", str(dl))
    assert not m.check(w)                     # remembers what was already there
    (dl / "deck.pptx.crdownload").write_text("partial")
    assert not m.check(w)
    (dl / "deck.pptx.crdownload").rename(dl / "deck.pptx")
    assert not m.check(w)                     # first sighting: waits for the size to settle
    assert m.check(w) and said[0] == "deck.pptx has finished downloading."
    assert pushed[0][1] == [str(dl / "deck.pptx")]


def test_expires_and_cancel(nova, monkeypatch):
    m, _, said, _ = _mgr(monkeypatch, ["nothing"])
    w = m.add("text_appears", "Done", minutes=1)
    assert m.check(w, time.time() + 120) and "didn't happen in time" in said[0]
    w2 = m.add("text_appears", "x")
    w3 = m.add("text_appears", "y")
    assert m.cancel(w2.id) == 1 and list(m.watches) == [w3.id]
    assert m.cancel() == 1 and not m.watches


def test_looks_like_uses_vision(nova, monkeypatch):
    m, _, said, _ = _mgr(monkeypatch)
    w = m.add("looks_like", "Has the build failed?")
    assert w.every >= 30
    monkeypatch.setattr(context.llm, "see", lambda p, q: "NO. All green.")
    assert not m.check(w)
    monkeypatch.setattr(context.llm, "see", lambda p, q: "YES. Red error: build failed in step 3.")
    assert m.check(w) and said[0] == "Red error: build failed in step 3."


def test_follow_up_runs_after_firing(nova, monkeypatch):
    from nova.agent import Agent
    from nova.llm import LLMReply
    m, st, said, _ = _mgr(monkeypatch, ["Export complete"])
    agent = Agent(nova[0], context.llm)
    monkeypatch.setattr(context, "agent", agent)
    context.llm.queue = [LLMReply("Done — I've let Sam know.")]
    w = m.add("text_appears", "Export complete", then_do="tell Sam the video is ready")
    assert m.check(w)
    for _ in range(40):
        if len(said) > 1:
            break
        time.sleep(0.05)
    assert said[-1] == "Done — I've let Sam know."


def test_watches_survive_restart_and_tools(nova, monkeypatch):
    from nova.tools import REGISTRY, select_tools
    m, _, _, _ = _mgr(monkeypatch)
    w = m.add("text_appears", "Export complete", window="Premiere")
    m2 = WatchManager()
    m2._thread = type("T", (), {"is_alive": lambda self: True})()      # don't start the loop in the test
    assert m2.load() == 1 and m2.watches[w.id].window == "Premiere"
    names = {t.name for t in select_tools("tell me when the download finishes")}
    assert {"watch_screen", "list_watchers", "stop_watching"} <= names
    out = REGISTRY["watch_screen"].run({"until": "sometime", "what": "x"})
    assert out.startswith("ERROR")


def test_real_ocr_reads_text():
    from PIL import ImageDraw, ImageFont
    img = Image.new("RGB", (700, 120), "white")
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 34)
    except OSError:
        try:
            font = ImageFont.truetype("arial.ttf", 34)
        except OSError:
            font = ImageFont.load_default()
    ImageDraw.Draw(img).text((20, 35), "Export complete 100%", fill="black", font=font)
    assert contains(read_text(img), "export complete")
