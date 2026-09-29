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

    def _open(self):
        import cv2
        backend = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.index)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        return cap

    def _loop(self) -> None:
        import cv2
        cap = self._open()
        if not cap.isOpened():
            self.error = f"Couldn't open webcam {self.index} (is another app using it?)"
            print(f"[camera] {self.error}")
            return
        self.error = ""
        gap = 1.0 / max(1.0, self.fps)
        fails = 0
        try:
            while not self._stop.is_set():
                t0 = time.time()
                ok, frame = cap.read()
                if not ok:
                    fails += 1
                    if fails > 30:
                        self.error = "The webcam stopped sending pictures."
                        break
                    time.sleep(0.1)
                    continue
                fails = 0
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
            cap.release()

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
    if _hub is None:
        from . import context
        c = ((cfg or context.cfg or {}).get("camera") or {})
        g = ((cfg or context.cfg or {}).get("gestures") or {})
        _hub = CameraHub(int(c.get("index", g.get("camera", 0))), mirror=bool(g.get("mirror", True)),
                         fps=float(c.get("fps", 15)))
    return _hub
