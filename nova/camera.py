"""One shared webcam for everything that watches you (gestures, presence) plus the ad/photo tools.

Windows lets only one program open a camera at a time, so a single capture thread reads frames and
hands them to every subscriber. The camera is only on while something is subscribed.
"""
from __future__ import annotations

import platform
import threading
import time
from pathlib import Path
from typing import Callable

MODELS = {
    "hand": ("hand_landmarker.task",
             "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/"
             "hand_landmarker.task"),
    "face": ("blaze_face_short_range.tflite",
             "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/"
             "blaze_face_short_range.tflite"),
}


def model_path(name: str) -> Path:
    """Local copy of a small MediaPipe model (downloaded once, a few MB)."""
    from .config import resolve
    fname, url = MODELS[name]
    p = resolve("models/vision") / fname
    if not p.exists() or p.stat().st_size < 10000:
        import httpx
        p.parent.mkdir(parents=True, exist_ok=True)
        print(f"[camera] downloading {fname}…")
        with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
            r.raise_for_status()
            tmp = p.with_suffix(".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
        tmp.replace(p)
    return p


class CameraHub:
    def __init__(self, index: int = 0, width: int = 1280, height: int = 720, fps: float = 15, mirror: bool = True):
        self.index, self.width, self.height, self.fps, self.mirror = index, width, height, fps, mirror
        self.subscribers: dict[str, Callable] = {}
        self._frame = None
        self._ts = 0.0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.error = ""
        self.overlays: dict[str, Callable] = {}      # name -> fn(frame) drawing on a preview copy
        self.status = ""
        self.dark = False
        self.frames = 0
        self.using: int | None = None

    # ── subscribers ───────────────────────────────────────
    def subscribe(self, name: str, fn: Callable) -> None:
        """fn(frame_bgr, timestamp) is called for every frame, from the camera thread."""
        self.subscribers[name] = fn
        self._ensure_running()

    def unsubscribe(self, name: str) -> None:
        self.subscribers.pop(name, None)
        self.overlays.pop(name, None)
        if not self.subscribers:
            self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _ensure_running(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="camera")
        self._thread.start()

    def _try(self, index: int, backend: int | None):
        """Open one camera with one Windows driver and prove it sends a picture."""
        import cv2
        cap = cv2.VideoCapture(index) if backend is None else cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        t0 = time.time()
        while time.time() - t0 < 3.0:                 # some webcams need a moment to wake up
            ok, frame = cap.read()
            if ok and frame is not None and frame.size:
                return cap
            time.sleep(0.1)
        cap.release()
        return None

    def _open(self):
        """Find a webcam that really sends pictures: the chosen one first (DirectShow, then Media Foundation),
        then any other camera on the PC. Returns an opened capture, or None."""
        import cv2
        if platform.system() == "Windows":
            backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, None]
        else:
            backends = [None]
        tried = []
        for index in [self.index] + [i for i in range(4) if i != self.index]:
            for be in backends:
                self.status = f"Opening webcam {index}…"
                cap = self._try(index, be)
                if cap is not None:
                    if index != self.index:
                        print(f"[camera] webcam {self.index} didn't work — using webcam {index}")
                    self.using = index
                    return cap
                tried.append(index)
            if index == self.index and self._stop.is_set():
                break
        return None

    def _loop(self) -> None:
        import cv2
        self.error, self.status, self.dark = "", "Opening the webcam…", False
        cap = self._open()
        if cap is None or not cap.isOpened():
            self.error = (f"Couldn't get a picture from webcam {self.index}. Close Teams / Zoom / the Camera app, "
                          "check the webcam's privacy cover, and in Windows Settings → Privacy & security → Camera "
                          "turn on 'Let desktop apps access your camera'.")
            self.status = ""
            print(f"[camera] {self.error}")
            return
        self.status = ""
        gap = 1.0 / max(1.0, self.fps)
        fails, reopened, dark_since = 0, 0, None
        try:
            while not self._stop.is_set():
                t0 = time.time()
                ok, frame = cap.read()
                if not ok or frame is None:
                    fails += 1
                    if fails > 30:                     # dropped out (USB hiccup, another app grabbed it) — try again
                        cap.release()
                        reopened += 1
                        cap = self._open() if reopened <= 3 else None
                        if cap is None:
                            self.error = "The webcam stopped sending pictures (is another app using it?)."
                            break
                        fails = 0
                    time.sleep(0.1)
                    continue
                fails = 0
                self.frames += 1
                if self.frames % 15 == 1:              # a black picture = privacy cover / Windows camera privacy
                    dark = float(frame.mean()) < 4.0
                    dark_since = (dark_since or t0) if dark else None
                    self.dark = bool(dark_since and t0 - dark_since > 3)
                if self.mirror:
                    frame = cv2.flip(frame, 1)
                with self._lock:
                    self._frame, self._ts = frame, t0
                for name, fn in list(self.subscribers.items()):
                    try:
                        fn(frame, t0)
                    except Exception as e:           # one bad subscriber never stops the camera
                        print(f"[camera] {name}: {e}")
                time.sleep(max(0.0, gap - (time.time() - t0)))
        finally:
            if cap is not None:
                cap.release()

    def wait_ready(self, timeout: float = 12.0) -> bool:
        """Wait for the first picture (or a clear failure)."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._frame is not None and time.time() - self._ts < 2:
                return True
            if self.error or not self.running:
                return False
            time.sleep(0.1)
        return False

    def state(self) -> dict:
        return {"running": self.running, "frames": self.frames, "error": self.error, "status": self.status,
                "dark": self.dark, "index": self.using if self.using is not None else self.index}

    def message(self) -> str:
        """What to show instead of the picture."""
        if self.error:
            return self.error
        if self.status:
            return self.status
        if self.dark:
            return "The picture is black: open the webcam's privacy cover or check Windows camera privacy settings."
        if not self.running:
            return "The camera is off. Turn on gestures to start it."
        return "Waiting for the webcam…"

    # ── frames for others ─────────────────────────────────
    def latest(self, max_age: float = 2.0):
        with self._lock:
            if self._frame is None or time.time() - self._ts > max_age:
                return None
            return self._frame.copy()

    def preview_jpeg(self, width: int = 480) -> bytes | None:
        import cv2
        f = self.latest()
        if f is None:
            return None
        for fn in list(self.overlays.values()):
            try:
                fn(f)
            except Exception:
                pass
        h = int(f.shape[0] * width / f.shape[1])
        ok, buf = cv2.imencode(".jpg", cv2.resize(f, (width, h)), [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None

    def placeholder_jpeg(self, width: int = 480) -> bytes | None:
        """A dark card with the reason there's no picture, so the preview never stays blank."""
        import textwrap

        import cv2
        import numpy as np
        h = int(width * 9 / 16)
        img = np.full((h, width, 3), 24, dtype=np.uint8)
        lines = textwrap.wrap(self.message(), 40)[:7]
        y = max(30, h // 2 - len(lines) * 12)
        for line in lines:
            cv2.putText(img, line, (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (235, 235, 235), 1, cv2.LINE_AA)
            y += 24
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None

    def snapshot(self, path: Path) -> Path | None:
        """Save the current frame (un-mirrored, as others see you) if the hub is running."""
        import cv2
        f = self.latest(max_age=1.0)
        if f is None:
            return None
        if self.mirror:
            f = cv2.flip(f, 1)
        cv2.imwrite(str(path), f)
        return Path(path)


_hub: CameraHub | None = None


def hub(cfg=None) -> CameraHub:
    global _hub
    from . import context
    c = ((cfg or context.cfg or {}).get("camera") or {})
    g = ((cfg or context.cfg or {}).get("gestures") or {})
    index = int(c.get("index", g.get("camera", 0)))
    if _hub is not None and not _hub.running and (_hub.index != index or _hub.mirror != bool(g.get("mirror", True))):
        _hub = None                                   # Settings changed the webcam number / mirror
    if _hub is None:
        _hub = CameraHub(index, mirror=bool(g.get("mirror", True)), fps=float(c.get("fps", 15)))
    return _hub
