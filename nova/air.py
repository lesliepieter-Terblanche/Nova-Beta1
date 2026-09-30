"""Air mouse helpers: smooth pointer filtering, "what's under my finger" labels, and the floating label window.

  describe(info)   → "📁 Folder · Q3 Deals" / "📄 PDF document · 2.4 MB · Price list.pdf" / "🔗 Link · Juniper Mist"
  HoverLabels      → watches the cursor; when it rests on something, asks Windows (UI Automation) what it is and
                     shows a small see-through label next to the cursor (clicks pass straight through it)
"""
from __future__ import annotations

import datetime as dt
import math
import os
import platform
import queue
import threading
import time
from pathlib import Path

WINDOWS = platform.system() == "Windows"


# ── One Euro filter: steady when the hand is still, responsive when it moves ──
class OneEuro:
    def __init__(self, min_cutoff: float = 1.2, beta: float = 8.0, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = self.dx = self.t = None

    @staticmethod
    def _alpha(cutoff: float, dt_: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / max(dt_, 1e-4))

    def __call__(self, x: float, t: float) -> float:
        if self.x is None:
            self.x, self.dx, self.t = x, 0.0, t
            return x
        dt_ = max(t - self.t, 1e-4)
        dx = (x - self.x) / dt_
        a_d = self._alpha(self.d_cutoff, dt_)
        self.dx = a_d * dx + (1 - a_d) * self.dx
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        a = self._alpha(cutoff, dt_)
        self.x = a * x + (1 - a) * self.x
        self.t = t
        return self.x

    def reset(self):
        self.x = self.dx = self.t = None


# ── what is under the cursor ──────────────────────────────
FILE_KINDS = {
    ".pdf": "PDF document", ".docx": "Word document", ".doc": "Word document", ".xlsx": "Excel workbook",
    ".xls": "Excel workbook", ".csv": "CSV table", ".pptx": "PowerPoint deck", ".ppt": "PowerPoint deck",
    ".txt": "Text file", ".md": "Note", ".jpg": "Photo", ".jpeg": "Photo", ".png": "Image", ".gif": "GIF",
    ".webp": "Image", ".mp4": "Video", ".mov": "Video", ".mkv": "Video", ".mp3": "Audio", ".wav": "Audio",
    ".m4a": "Audio", ".zip": "ZIP archive", ".rar": "RAR archive", ".7z": "7-Zip archive", ".exe": "Program",
    ".msi": "Installer", ".lnk": "Shortcut", ".url": "Web shortcut", ".html": "Web page", ".py": "Python script",
    ".json": "JSON file", ".eml": "Email", ".msg": "Outlook email", ".ics": "Calendar invite",
}
KIND_EMOJI = {"Hyperlink": "🔗", "Button": "🔘", "SplitButton": "🔘", "MenuItem": "☰", "TabItem": "🗂",
              "Edit": "✏️", "ComboBox": "▾", "CheckBox": "☑️", "RadioButton": "◉", "Image": "🖼",
              "TreeItem": "🌿", "DataItem": "▦", "Document": "📃", "Text": "🔤", "ListItem": "•", "Window": "🪟",
              "TitleBar": "🪟", "ScrollBar": "↕", "Slider": "🎚", "Pane": ""}
KIND_WORD = {"Hyperlink": "Link", "Button": "Button", "SplitButton": "Button", "MenuItem": "Menu", "TabItem": "Tab",
             "Edit": "Text box", "ComboBox": "Drop-down", "CheckBox": "Tick box", "RadioButton": "Option",
             "Image": "Image", "TreeItem": "Folder tree", "DataItem": "Cell", "Document": "Page", "Text": "Text",
             "ListItem": "Item", "TitleBar": "Window", "ScrollBar": "Scroll bar", "Slider": "Slider"}
HINTS = {"folder": "🤏🤏 open · 🤏 hold = menu", "file": "🤏🤏 open · 🤏 hold = menu · 🤏 move = drag",
         "link": "🤏 open · 🤏 hold = menu", "button": "🤏 click", "field": "🤏 click, then say what to type",
         "tab": "🤏 switch"}


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def describe_path(p: Path) -> dict:
    if p.is_dir():
        try:
            n = sum(1 for _ in p.iterdir())
        except Exception:
            n = None
        return {"emoji": "📁", "kind": "Folder", "name": p.name or str(p),
                "detail": f"{n} item{'s' if n != 1 else ''}" if n is not None else "", "hint": HINTS["folder"]}
    kind = FILE_KINDS.get(p.suffix.lower(), (p.suffix.upper().lstrip(".") + " file") if p.suffix else "File")
    detail = ""
    try:
        st = p.stat()
        detail = f"{_size(st.st_size)} · {dt.datetime.fromtimestamp(st.st_mtime):%d %b %Y}"
    except Exception:
        pass
    emoji = "🖼" if kind in ("Photo", "Image", "GIF") else "🎬" if kind == "Video" else "🎵" if kind == "Audio" \
        else "📦" if "archive" in kind else "⚙️" if kind in ("Program", "Installer") else "📄"
    return {"emoji": emoji, "kind": kind, "name": p.name, "detail": detail, "hint": HINTS["file"]}


def describe(info: dict) -> dict | None:
    """info = {"type": UIA control type, "name": ..., "value": url/text, "folder": Explorer/Desktop folder or ""}
    → {"emoji", "kind", "name", "detail", "hint", "text"} or None when there's nothing worth labelling."""
    ctype, name = info.get("type") or "", (info.get("name") or "").strip()
    folder = info.get("folder") or ""
    out = None
    if folder and name and ctype in ("ListItem", "Edit", "Text", "DataItem", "Image"):
        p = Path(folder) / name
        if not p.exists() and ctype != "ListItem":
            p = None
        if p is not None and p.exists():
            out = describe_path(p)
        elif ctype == "ListItem":
            out = {"emoji": "📄", "kind": "File", "name": name, "detail": "", "hint": HINTS["file"]}
    if out is None:
        if not name:
            return None
        if ctype == "Hyperlink":
            url = info.get("value") or ""
            out = {"emoji": "🔗", "kind": "Link", "name": name[:70], "detail": url[:60], "hint": HINTS["link"]}
        elif ctype == "TreeItem":
            out = {"emoji": "📁", "kind": "Folder", "name": name, "detail": "", "hint": HINTS["folder"]}
        elif ctype in ("Edit", "ComboBox"):
            out = {"emoji": KIND_EMOJI[ctype], "kind": KIND_WORD[ctype], "name": name[:60], "detail": "",
                   "hint": HINTS["field"]}
        elif ctype == "TabItem":
            out = {"emoji": "🗂", "kind": "Tab", "name": name[:60], "detail": "", "hint": HINTS["tab"]}
        elif ctype in ("Pane", "Group", "Custom", "Window") and len(name) > 60:
            return None
        else:
            out = {"emoji": KIND_EMOJI.get(ctype, "•"), "kind": KIND_WORD.get(ctype, ctype or "Item"),
                   "name": name[:70], "detail": "",
                   "hint": HINTS["button"] if ctype in ("Button", "SplitButton", "MenuItem", "CheckBox") else ""}
    out["text"] = f"{out['emoji']} {out['kind']} · {out['name']}" + (f"\n{out['detail']}" if out["detail"] else "")
    return out


def _explorer_folder(el) -> str:
    """The folder an Explorer window (or the desktop) is showing, found from the element's window."""
    try:
        top = el.top_level_parent()
        cls = top.element_info.class_name
        if cls in ("Progman", "WorkerW"):
            return str(Path.home() / "Desktop")
        if cls != "CabinetWClass":
            return ""
        for d in top.descendants(control_type="ToolBar"):
            nm = d.element_info.name or ""
            if nm.startswith("Address: "):
                path = nm[len("Address: "):].strip()
                special = {"Desktop": Path.home() / "Desktop", "Downloads": Path.home() / "Downloads",
                           "Documents": Path.home() / "Documents", "Pictures": Path.home() / "Pictures",
                           "Music": Path.home() / "Music", "Videos": Path.home() / "Videos"}
                return str(special.get(path, path))
    except Exception:
        pass
    return ""


def inspect_point(x: int, y: int) -> dict | None:
    """Ask Windows UI Automation what's at (x, y)."""
    if not WINDOWS:
        return None
    try:
        from pywinauto import Desktop
        el = Desktop(backend="uia").from_point(x, y)
    except Exception:
        return None
    try:
        info = el.element_info
        value = ""
        if info.control_type == "Hyperlink":
            try:
                value = el.legacy_properties().get("Value", "") or ""
            except Exception:
                value = ""
        return {"type": info.control_type, "name": info.name or el.window_text(), "value": value,
                "folder": _explorer_folder(el) if info.control_type in ("ListItem", "Edit", "Text", "Image") else ""}
    except Exception:
        return None


# ── the floating label next to the cursor ─────────────────
class Overlay:
    """A tiny always-on-top, see-through, click-through label (Tk in its own thread)."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, daemon=True, name="air-label")
        self.thread.start()

    def show(self, text: str, x: int, y: int):
        self.q.put(("show", text, x, y))

    def hide(self):
        self.q.put(("hide",))

    def _run(self):
        try:
            import tkinter as tk
        except Exception:
            return
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.92)
        except Exception:
            pass
        root.configure(bg="#0b0f24")
        lab = tk.Label(root, text="", justify="left", fg="#e9ecff", bg="#0b0f24", font=("Segoe UI", 10),
                       padx=10, pady=6)
        lab.pack()
        root.withdraw()
        if WINDOWS:
            try:
                import ctypes
                root.update_idletasks()
                hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
                GWL_EXSTYLE, LAYERED, TRANSPARENT, TOOL, NOACTIVATE = -20, 0x80000, 0x20, 0x80, 0x08000000
                style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | LAYERED | TRANSPARENT | TOOL | NOACTIVATE)
            except Exception:
                pass
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()

        def pump():
            try:
                while True:
                    cmd = self.q.get_nowait()
                    if cmd[0] == "show":
                        _, text, x, y = cmd
                        lab.config(text=text)
                        root.update_idletasks()
                        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
                        nx, ny = min(x + 22, sw - w - 4), min(y + 24, sh - h - 4)
                        root.geometry(f"+{nx}+{ny}")
                        root.deiconify()
                    elif cmd[0] == "hide":
                        root.withdraw()
            except queue.Empty:
                pass
            root.after(40, pump)
        root.after(40, pump)
        root.mainloop()


class HoverLabels:
    """When the air-mouse cursor rests on something, label it."""

    def __init__(self, get_cursor, is_active, on_label=None, rest_ms: int = 220, inspector=inspect_point,
                 overlay: Overlay | None = None):
        self.get_cursor, self.is_active, self.on_label = get_cursor, is_active, on_label
        self.rest = rest_ms / 1000
        self.inspect = inspector
        self.overlay = overlay or Overlay()
        self.current: dict | None = None
        self._last_pos, self._since, self._described = None, 0.0, None
        self._stop = threading.Event()
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        if WINDOWS:
            self.overlay.start()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="air-hover")
        self.thread.start()

    def stop(self):
        self._stop.set()
        self.overlay.hide()

    def step(self, now: float | None = None) -> dict | None:
        """One check (separate for tests). Returns the label shown, if it changed."""
        now = time.time() if now is None else now
        if not self.is_active():
            if self.current:
                self.current = None
                self._described = None
                self.overlay.hide()
                if self.on_label:
                    self.on_label(None)
            return None
        pos = self.get_cursor()
        if pos is None:
            return None
        if self._last_pos is None or math.dist(pos, self._last_pos) > 6:
            self._last_pos, self._since = pos, now
            return None
        if now - self._since < self.rest or (self._described and math.dist(pos, self._described) <= 6):
            return None
        self._described = pos
        info = self.inspect(int(pos[0]), int(pos[1]))
        label = describe(info) if info else None
        if label != self.current:
            self.current = label
            if label:
                self.overlay.show(label["text"] + (f"\n{label['hint']}" if label.get("hint") else ""), *pos)
            else:
                self.overlay.hide()
            if self.on_label:
                self.on_label(label)
            return label
        return None

    def _loop(self):
        if WINDOWS:
            try:
                import comtypes
                comtypes.CoInitialize()
            except Exception:
                pass
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as e:
                print(f"[air] {e}")
            time.sleep(0.08)


def virtual_screen() -> tuple[int, int, int, int]:
    """(left, top, width, height) of all monitors together."""
    if WINDOWS:
        try:
            import ctypes
            u = ctypes.windll.user32
            return u.GetSystemMetrics(76), u.GetSystemMetrics(77), u.GetSystemMetrics(78), u.GetSystemMetrics(79)
        except Exception:
            pass
    return 0, 0, 1920, 1080


_ = os
