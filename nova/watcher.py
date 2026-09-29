"""Screen watcher: Nova keeps an eye on your screen and tells you when something happens.

  "Tell me when 'Export complete' appears"            → text_appears    (reads the screen, local OCR)
  "Let me know when the upload bar is gone"            → text_disappears
  "Tell me when the render window stops changing"      → stops_changing  (e.g. a progress bar finished)
  "Watch Outlook and tell me when something changes"   → changes
  "Tell me when the installer closes"                  → window_closes / window_opens
  "Tell me when ffmpeg is done"                        → process_exits
  "Tell me when the download finishes"                 → file_appears    (a new finished file in Downloads)
  "Watch the screen and tell me if the build fails"    → looks_like      (a vision model answers yes/no)

When it happens Nova says so (held if you're away), sends it to Telegram with a screenshot, and can then do a
follow-up ("…then close it"). Watches are saved, so they survive a restart.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import context
from .config import resolve

KINDS = ("text_appears", "text_disappears", "stops_changing", "changes", "window_opens", "window_closes",
         "process_exits", "file_appears", "looks_like")
PARTIAL = (".crdownload", ".part", ".tmp", ".download", ".partial", ".opdownload")

_SCHEMA = """CREATE TABLE IF NOT EXISTS watchers(
  id INTEGER PRIMARY KEY, created TEXT, kind TEXT, what TEXT, window TEXT, every REAL, until TEXT, then_do TEXT,
  status TEXT, note TEXT, fired_at TEXT, turn INTEGER, state TEXT)"""


# ── seeing the screen ─────────────────────────────────────
def windows() -> list[dict]:
    """Visible top-level windows: title + rectangle (Windows only)."""
    import platform
    if platform.system() != "Windows":
        return []
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32                                             # type: ignore[attr-defined]
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if u.IsWindowVisible(hwnd) and not u.IsIconic(hwnd):
            n = u.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(hwnd, buf, n + 1)
                r = wintypes.RECT()
                u.GetWindowRect(hwnd, ctypes.byref(r))
                if r.right - r.left > 50 and r.bottom - r.top > 50:
                    out.append({"title": buf.value, "box": (r.left, r.top, r.right, r.bottom)})
        return True
    u.EnumWindows(cb, 0)
    return out


def find_window(title: str) -> dict | None:
    t = (title or "").lower().strip()
    if not t:
        return None
    return next((w for w in windows() if t in w["title"].lower()), None)


def grab(box: tuple | None = None):
    """Screenshot of the whole main screen, or a (left, top, right, bottom) box, as a PIL image."""
    import mss
    from PIL import Image
    with mss.mss() as s:
        mon = s.monitors[1]
        region = mon if not box else {"left": box[0], "top": box[1], "width": max(1, box[2] - box[0]),
                                       "height": max(1, box[3] - box[1])}
        shot = s.grab(region)
        return Image.frombytes("RGB", shot.size, shot.rgb)


_ocr = None
_ocr_lock = threading.Lock()


def read_text(img) -> str:
    """Local OCR (RapidOCR, runs on the CPU, no internet)."""
    global _ocr
    import numpy as np
    with _ocr_lock:
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr = RapidOCR()
        if img.width > 1800:
            img = img.resize((1800, int(img.height * 1800 / img.width)))
        res, _ = _ocr(np.asarray(img)[:, :, ::-1].copy())
    return "\n".join(r[1] for r in res or [])


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def contains(haystack: str, needle: str) -> bool:
    h, n = _norm(haystack), _norm(needle)
    return bool(n) and (n in h or n.replace(" ", "") in h.replace(" ", ""))


def signature(img):
    """Tiny grayscale thumbnail used to see whether the picture changed."""
    import numpy as np
    return np.asarray(img.convert("L").resize((160, 90)), dtype=np.int16)


def changed_fraction(a, b, tolerance: int = 18) -> float:
    import numpy as np
    return float((np.abs(a - b) > tolerance).mean())


# ── one watch ─────────────────────────────────────────────
@dataclass
class Watch:
    id: int
    kind: str
    what: str
    window: str = ""
    every: float = 5.0
    until: float = 0.0               # unix time it gives up
    then_do: str = ""
    status: str = "watching"         # watching | done | expired | cancelled
    note: str = ""
    turn: int | None = None
    created: str = ""
    state: dict = field(default_factory=dict)
    next_at: float = 0.0

    def label(self) -> str:
        where = f" in '{self.window}'" if self.window else ""
        return {
            "text_appears": f"'{self.what}' appears{where}",
            "text_disappears": f"'{self.what}' disappears{where}",
            "stops_changing": f"{self.window or 'the screen'} stops changing",
            "changes": f"{self.window or 'the screen'} changes",
            "window_opens": f"a '{self.what}' window opens",
            "window_closes": f"the '{self.what}' window closes",
            "process_exits": f"{self.what} finishes",
            "file_appears": f"a new file lands in {Path(self.what or '~/Downloads').name or 'Downloads'}",
            "looks_like": f"{self.what}",
        }.get(self.kind, self.what)


class WatchManager:
    def __init__(self):
        self.watches: dict[int, Watch] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.grab = grab                    # replaceable in tests
        self.read_text = read_text
        self.windows = windows

    # storage
    def _db(self):
        s = context.store
        with s.lock:
            s.db.execute(_SCHEMA)
        return s

    def load(self) -> int:
        """Pick up watches that were running before Nova restarted."""
        s = self._db()
        with s.lock:
            rows = s.db.execute("SELECT * FROM watchers WHERE status='watching'").fetchall()
        for r in rows:
            until = dt.datetime.fromisoformat(r["until"]).timestamp() if r["until"] else 0
            w = Watch(r["id"], r["kind"], r["what"] or "", r["window"] or "", r["every"] or 5, until, r["then_do"] or "",
                      "watching", r["note"] or "", r["turn"], r["created"], json.loads(r["state"] or "{}"))
            self.watches[w.id] = w
        if self.watches:
            self.start()
        return len(rows)

    def _save(self, w: Watch) -> None:
        s = context.store
        with s.lock:
            s.db.execute("UPDATE watchers SET status=?, note=?, fired_at=?, state=? WHERE id=?",
                         (w.status, w.note, dt.datetime.now().isoformat(timespec="seconds") if w.status != "watching"
                          else None, json.dumps({k: v for k, v in w.state.items() if k in ("seen", "files")}), w.id))
            s.db.commit()

    # public
    def add(self, kind: str, what: str = "", window: str = "", every: float = 0, minutes: float = 120,
            then_do: str = "") -> Watch:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if kind in ("text_appears", "text_disappears", "window_opens", "window_closes", "process_exits",
                    "looks_like") and not what.strip():
            raise ValueError("Tell me what to watch for.")
        default_every = {"text_appears": 8, "text_disappears": 8, "looks_like": 60, "file_appears": 3,
                         "process_exits": 3, "window_opens": 2, "window_closes": 2}.get(kind, 4)
        every = max(30.0 if kind == "looks_like" else 1.0, float(every or default_every))
        until = time.time() + max(1.0, float(minutes or 120)) * 60
        s = self._db()
        now = dt.datetime.now().isoformat(timespec="seconds")
        with s.lock:
            wid = s.db.execute("INSERT INTO watchers(created,kind,what,window,every,until,then_do,status,turn,state) "
                               "VALUES(?,?,?,?,?,?,?,'watching',?,'{}')",
                               (now, kind, what.strip(), window.strip(), every,
                                dt.datetime.fromtimestamp(until).isoformat(timespec="seconds"), then_do.strip(),
                                s.current_turn)).lastrowid
            s.db.commit()
        w = Watch(wid, kind, what.strip(), window.strip(), every, until, then_do.strip(), turn=s.current_turn,
                  created=now)
        with self._lock:
            self.watches[wid] = w
        s.log("watch", "watcher", f"👀 Watching until {w.label()}", then_do, turn=None)
        self.start()
        return w

    def cancel(self, wid: int | None = None) -> int:
        with self._lock:
            ids = list(self.watches) if wid is None else [wid] if wid in self.watches else []
            for i in ids:
                w = self.watches.pop(i)
                w.status = "cancelled"
                self._save(w)
        return len(ids)

    def active(self) -> list[Watch]:
        return list(self.watches.values())

    # loop
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="watcher")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set() and self.watches:
            now = time.time()
            for w in list(self.watches.values()):
                if now >= w.next_at:
                    w.next_at = now + w.every
                    try:
                        self.check(w, now)
                    except Exception as e:
                        w.state["errors"] = w.state.get("errors", 0) + 1
                        print(f"[watch] #{w.id} check failed: {e}")
                        if w.state["errors"] >= 5:
                            self._finish(w, "expired", f"I had to stop watching ({e}).")
            time.sleep(0.5)

    # the actual checks
    def _box(self, w: Watch):
        if not w.window:
            return None, True
        win = next((x for x in self.windows() if w.window.lower() in x["title"].lower()), None)
        return (win["box"] if win else None), win is not None

    def check(self, w: Watch, now: float | None = None) -> bool:
        """One look. Returns True when the watch fired (or ended)."""
        now = now or time.time()
        if w.until and now > w.until:
            return self._finish(w, "expired", f"I stopped watching for {w.label()} — it didn't happen in time.")
        k = w.kind
        if k in ("window_opens", "window_closes"):
            titles = [x["title"] for x in self.windows()]
            there = any(w.what.lower() in t.lower() for t in titles)
            if k == "window_opens" and there:
                return self._fire(w, f"The '{w.what}' window just opened.")
            if k == "window_closes":
                if there:
                    w.state["seen"] = True
                elif w.state.get("seen") or "first" in w.state:
                    return self._fire(w, f"The '{w.what}' window has closed." if w.state.get("seen")
                                      else f"There's no '{w.what}' window open.")
                w.state["first"] = True
            return False
        if k == "process_exits":
            import psutil
            name = w.what.lower().removesuffix(".exe")
            running = any(name in (p.info.get("name") or "").lower() for p in psutil.process_iter(["name"]))
            if running:
                w.state["seen"] = True
                return False
            return self._fire(w, f"{w.what} has finished." if w.state.get("seen") else f"{w.what} isn't running.")
        if k == "file_appears":
            folder = Path(w.what).expanduser() if w.what and Path(w.what).expanduser().is_dir() \
                else Path.home() / "Downloads"
            pattern = "" if (w.what and Path(w.what).expanduser().is_dir()) else w.what.lower()
            files = {str(p): p.stat().st_size for p in folder.iterdir() if p.is_file()} if folder.exists() else {}
            if "files" not in w.state:
                w.state["files"] = list(files)
                return False
            new = [p for p in files if p not in w.state["files"] and not p.lower().endswith(PARTIAL)
                   and (not pattern or pattern in Path(p).name.lower())]
            sizes = w.state.setdefault("sizes", {})
            for p in new:
                if sizes.get(p) == files[p] and files[p] > 0:          # size stopped growing -> finished
                    return self._fire(w, f"{Path(p).name} has finished downloading.", attach=p)
                sizes[p] = files[p]
            return False

        box, found = self._box(w)
        if w.window and not found:
            if k == "text_disappears" and w.state.get("seen"):
                return self._fire(w, f"The '{w.window}' window is gone, so '{w.what}' is too.")
            return False
        img = self.grab(box)
        if k in ("text_appears", "text_disappears"):
            text = self.read_text(img)
            if k == "text_appears" and contains(text, w.what):
                return self._fire(w, f"'{w.what}' is on the screen now.", img=img)
            if k == "text_disappears":
                if contains(text, w.what):
                    w.state["seen"] = True
                elif w.state.get("seen"):
                    return self._fire(w, f"'{w.what}' has gone from the screen.", img=img)
                elif w.state.get("looks", 0) >= 1:
                    return self._fire(w, f"I don't see '{w.what}' on the screen at all.", img=img)
                w.state["looks"] = w.state.get("looks", 0) + 1
            return False
        if k in ("changes", "stops_changing"):
            sig = signature(img)
            prev = w.state.get("_sig")
            w.state["_sig"] = sig
            if prev is None or prev.shape != sig.shape:
                w.state["still_since"] = now
                return False
            diff = changed_fraction(prev, sig)
            if k == "changes":
                if diff > 0.004:
                    return self._fire(w, f"Something changed on {w.window or 'the screen'}.", img=img)
                return False
            if diff > 0.002:
                w.state["moved"] = True
                w.state["still_since"] = now
                return False
            need = float(w.state.get("stable_seconds", 20))
            if w.state.get("moved") and now - w.state.get("still_since", now) >= need:
                return self._fire(w, f"{w.window or 'The screen'} has stopped changing — looks finished.", img=img)
            return False
        if k == "looks_like":
            path = resolve("workspace/watch") / f"look_{w.id}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            img.convert("RGB").save(path, quality=80)
            ans = context.llm.see(str(path), f"Answer only YES or NO, then one short reason. {w.what}")
            if ans.strip().upper().startswith("YES"):
                return self._fire(w, ans.strip()[3:].lstrip(" .:-,") or w.what, img=img)
            return False
        return False

    def _fire(self, w: Watch, message: str, img=None, attach: str | None = None) -> bool:
        files = []
        if img is not None:
            p = resolve("workspace/watch") / f"watch_{w.id}_{dt.datetime.now():%H%M%S}.jpg"
            p.parent.mkdir(parents=True, exist_ok=True)
            img.convert("RGB").save(p, quality=85)
            files.append(str(p))
        elif attach and Path(attach).stat().st_size < 45_000_000:
            files.append(attach)
        return self._finish(w, "done", message, files)

    def _finish(self, w: Watch, status: str, message: str, files: list | None = None) -> bool:
        with self._lock:
            self.watches.pop(w.id, None)
        w.status, w.note = status, message
        self._save(w)
        if context.store:
            context.store.log("watch", "watcher", ("👀 " if status == "done" else "⏹ ") + message, w.label(), turn=0)
        if context.announce:
            context.announce(message)
        context.push(("👀 " if status == "done" else "⏹ ") + message, files or [])
        if status == "done" and w.then_do and getattr(context, "agent", None):
            threading.Thread(target=self._follow_up, args=(w, message), daemon=True, name="watch-then").start()
        return True

    @staticmethod
    def _follow_up(w: Watch, message: str) -> None:
        reply = context.agent.handle(f"{message} Now, as I asked earlier: {w.then_do}", "voice")
        if context.announce:
            context.announce(reply.text)
        context.push(reply.text, reply.files)


_mgr: WatchManager | None = None


def manager() -> WatchManager:
    global _mgr
    if _mgr is None:
        _mgr = WatchManager()
    return _mgr
