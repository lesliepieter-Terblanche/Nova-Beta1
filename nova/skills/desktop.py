"""PC hands: Nova uses your Windows desktop like you do — opens apps, manages windows, clicks, types, presses keys.

How it clicks reliably: Windows' own accessibility layer (UI Automation) lists every button, field, menu and link
in a window by name, so Nova clicks "Save" by name instead of guessing pixels. For things without names (images,
canvases, some web content) it looks at the screen and clicks what it sees.

"Do it for me": say what you want done ("open Excel, open the Q3 deals file and sort by value") and Nova works
through it step by step, telling you what it's doing.
Safety: it stops and asks before anything that sends, pays, buys, deletes or submits. Press Esc, or throw the
mouse into the top-left corner of the screen, to take over at any moment.
"""
from __future__ import annotations

import json
import platform
import re
import threading
import time

from .. import context
from ..tools import register_group, tool

register_group("desktop", ["click", "double click", "press", "type ", "window", "windows", "minimise", "minimize",
                           "maximise", "maximize", "close the", "switch to", "focus", "snap", "button", "menu", "tab ",
                           "for me", "do it", "fill in", "scroll", "on my pc", "on my computer", "on the screen",
                           "open ", "select", "drag", "copy", "paste", "save it", "webcam", "camera", "app "])

WINDOWS = platform.system() == "Windows"
RISKY = re.compile(r"\b(send|pay|payment|purchase|buy|order|checkout|check out|delete|remove|erase|submit|transfer|"
                   r"confirm|place order|uninstall|format|sign out|log out|publish|post)\b", re.I)
CLICKABLE = {"Button", "MenuItem", "Hyperlink", "ListItem", "TabItem", "TreeItem", "Edit", "ComboBox", "CheckBox",
             "RadioButton", "DataItem", "SplitButton", "MenuBar", "Document", "Text", "Image"}

if WINDOWS:                                    # clicks must land where the screenshot says, even at 125-150 % scaling
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


# ── the desktop backend (Windows UI Automation) ───────────
class Desk:
    """Thin layer over pywinauto so the rest can be tested without Windows."""

    def __init__(self):
        self.controls: dict[int, object] = {}

    def _desktop(self):
        from pywinauto import Desktop
        return Desktop(backend="uia")

    def windows(self) -> list[dict]:
        out = []
        for w in self._desktop().windows():
            try:
                title = w.window_text()
                if title and w.is_visible() and title not in ("Program Manager", "Taskbar"):
                    out.append({"title": title, "active": w.has_keyboard_focus() if hasattr(w, "has_keyboard_focus")
                                else False})
            except Exception:
                continue
        return out

    def find(self, title: str):
        title_l = title.lower()
        best = None
        for w in self._desktop().windows():
            try:
                t = w.window_text()
            except Exception:
                continue
            if t and title_l in t.lower() and w.is_visible():
                if best is None or len(t) < len(best.window_text()):
                    best = w
        if best is None:
            raise LookupError(f"No open window matching '{title}'.")
        return best

    def active(self):
        from pywinauto import Desktop
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            return Desktop(backend="uia").window(handle=hwnd)
        except Exception:
            return None

    def window_action(self, title: str, action: str) -> str:
        w = self.find(title)
        name = w.window_text()
        if action == "focus":
            if w.is_minimized():
                w.restore()
            w.set_focus()
        elif action == "minimize":
            w.minimize()
        elif action == "maximize":
            w.maximize()
        elif action == "restore":
            w.restore()
        elif action == "close":
            w.close()
        elif action in ("snap_left", "snap_right"):
            w.set_focus()
            self.keys("win+left" if action == "snap_left" else "win+right")
        else:
            raise ValueError(f"Unknown window action '{action}'.")
        return name

    def read(self, title: str = "") -> tuple[str, list[dict]]:
        w = self.find(title) if title else self.active()
        if w is None:
            raise LookupError("No active window.")
        seen, out = set(), []
        self.controls = {}
        for c in w.descendants():
            try:
                info = c.element_info
                ctype = info.control_type
                name = (c.window_text() or info.name or "").strip()
                if ctype not in CLICKABLE or not c.is_visible():
                    continue
                if not name and ctype not in ("Edit", "ComboBox", "Document"):
                    continue
                key = (ctype, name[:60])
                if key in seen:
                    continue
                seen.add(key)
                cid = len(out) + 1
                self.controls[cid] = c
                out.append({"id": cid, "type": ctype, "name": name[:80]})
                if len(out) >= 120:
                    break
            except Exception:
                continue
        return w.window_text(), out

    def click(self, cid: int, double: bool = False) -> str:
        c = self.controls.get(int(cid))
        if c is None:
            raise LookupError("That control isn't on screen any more — read the window again.")
        name = c.window_text() or c.element_info.name or c.element_info.control_type
        (c.double_click_input if double else c.click_input)()
        return name

    def type(self, text: str, cid: int | None = None) -> None:
        if cid:
            c = self.controls.get(int(cid))
            if c is None:
                raise LookupError("That field isn't on screen any more.")
            c.click_input()
        from pywinauto.keyboard import send_keys
        send_keys(re.sub(r"([{}()+^%~\[\]])", r"{\1}", text).replace("\n", "{ENTER}"), with_spaces=True,
                  with_newlines=False, pause=0.01)

    def keys(self, combo: str) -> None:
        from pynput.keyboard import Controller, Key
        kb = Controller()
        names = {"ctrl": Key.ctrl, "control": Key.ctrl, "alt": Key.alt, "shift": Key.shift, "win": Key.cmd,
                 "windows": Key.cmd, "enter": Key.enter, "return": Key.enter, "esc": Key.esc, "escape": Key.esc,
                 "tab": Key.tab, "space": Key.space, "backspace": Key.backspace, "delete": Key.delete, "del": Key.delete,
                 "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right, "home": Key.home, "end": Key.end,
                 "pageup": Key.page_up, "pagedown": Key.page_down, **{f"f{i}": getattr(Key, f"f{i}") for i in range(1, 13)}}
        parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
        keys = [names.get(p, p) for p in parts]
        for k in keys:
            kb.press(k)
        for k in reversed(keys):
            kb.release(k)

    def scroll(self, direction: str = "down", amount: int = 5) -> None:
        from pynput.mouse import Controller
        Controller().scroll(0, -amount if direction == "down" else amount)

    def click_xy(self, x: int, y: int, double: bool = False) -> None:
        from pynput.mouse import Button, Controller
        m = Controller()
        m.position = (x, y)
        time.sleep(0.05)
        m.click(Button.left, 2 if double else 1)

    def screenshot(self) -> tuple[str, int, int]:
        import mss

        from ..config import resolve
        out = resolve("workspace/screenshots") / "hands.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        with mss.mss() as s:
            mon = s.monitors[1]
            s.shot(mon=1, output=str(out))
        return str(out), mon["width"], mon["height"]

    def cursor(self) -> tuple[int, int]:
        from pynput.mouse import Controller
        return tuple(int(v) for v in Controller().position)


_desk: Desk | None = None


def desk() -> Desk:
    global _desk
    if _desk is None:
        _desk = Desk()
    return _desk


def _need_windows() -> str | None:
    if not WINDOWS:
        return "ERROR: PC hands only work on Windows."
    try:
        import pywinauto  # noqa: F401
    except ImportError:
        return "ERROR: pywinauto isn't installed yet — run: pip install -r requirements.txt"
    return None


# ── seeing and clicking by sight ──────────────────────────
LOCATE_PROMPT = """Find this on the screenshot: "{what}".
Reply with JSON only: {{"x": <0-1000>, "y": <0-1000>}} — the centre of it, as thousandths of the image width and
height (0,0 = top-left). If it is not visible, reply {{"x": null, "y": null}}."""


def locate(what: str) -> tuple[int, int] | None:
    path, w, h = desk().screenshot()
    raw = context.llm.see(path, LOCATE_PROMPT.format(what=what))
    m = re.search(r"\{.*\}", raw or "", re.S)
    data = json.loads(m.group(0)) if m else {}
    if data.get("x") is None or data.get("y") is None:
        return None
    return int(float(data["x"]) / 1000 * w), int(float(data["y"]) / 1000 * h)


# ── simple tools ──────────────────────────────────────────
@tool(group="desktop")
def list_windows() -> str:
    """The windows open on the PC right now."""
    if err := _need_windows():
        return err
    ws = desk().windows()
    return "; ".join(w["title"] for w in ws[:30]) or "No windows are open."


@tool(group="desktop")
def window_control(window: str, action: str = "focus") -> str:
    """Bring a window to the front, minimise, maximise, restore, snap it left/right, or close it.
    Args:
        window: part of the window's title, e.g. "Excel", "Chrome", "Q3 deals"
        action: focus, minimize, maximize, restore, snap_left, snap_right or close
    """
    if err := _need_windows():
        return err
    name = desk().window_action(window, action)
    return f"{action.replace('_', ' ').title()}: {name}."


@tool(group="desktop")
def read_window(window: str = "") -> str:
    """List the buttons, fields, menus and links in a window (the active one if empty), with numbers to click.
    Args:
        window: part of the window title, or empty for the one in front
    """
    if err := _need_windows():
        return err
    title, ctrls = desk().read(window)
    return f"Window: {title}\n" + "\n".join(f"{c['id']}. {c['type']} '{c['name']}'" for c in ctrls)


@tool(group="desktop")
def click_control(number: int, double: bool = False) -> str:
    """Click a button/field/menu item by its number from read_window.
    Args:
        number: the number read_window gave it
        double: double-click instead of a single click
    """
    if err := _need_windows():
        return err
    return f"Clicked '{desk().click(number, double)}'."


@tool(group="desktop")
def click_on_screen(what: str, double: bool = False) -> str:
    """Look at the screen and click something by describing it (for things without a name, like pictures or
    canvas content). Prefer read_window + click_control when the thing has a name.
    Args:
        what: what to click, e.g. "the blue Download button", "the second photo"
        double: double-click
    """
    if err := _need_windows():
        return err
    pos = locate(what)
    if not pos:
        return f"I can't see {what} on the screen."
    desk().click_xy(*pos, double=double)
    return f"Clicked {what}."


@tool(group="desktop")
def type_text(text: str, field_number: int = 0) -> str:
    """Type text where the cursor is, or into a field (number from read_window). New lines press Enter.
    Args:
        text: what to type
        field_number: the field's number from read_window, or 0 for wherever the cursor is
    """
    if err := _need_windows():
        return err
    desk().type(text, field_number or None)
    return f"Typed {len(text)} characters."


@tool(group="desktop")
def press_keys(keys: str) -> str:
    """Press a key or shortcut, e.g. "ctrl+s", "alt+tab", "enter", "win+d", "ctrl+shift+t".
    Args:
        keys: the key combination
    """
    if err := _need_windows():
        return err
    desk().keys(keys)
    return f"Pressed {keys}."


@tool(group="desktop")
def scroll(direction: str = "down", amount: int = 5) -> str:
    """Scroll the window under the mouse.
    Args:
        direction: up or down
        amount: how far (1-20)
    """
    if err := _need_windows():
        return err
    desk().scroll(direction, max(1, min(20, int(amount))))
    return f"Scrolled {direction}."


# ── "do it for me": step-by-step computer use ─────────────
AGENT_PROMPT = """You are operating {owner}'s Windows PC to do this task:
"{goal}"
{allowed}
Open windows: {windows}
Window in front: {title}
Its controls (number. type 'name'):
{controls}
{seen}
Steps so far:
{history}

Choose the ONE next action. JSON only, one of:
{{"do": "open_app", "name": "excel"}}              {{"do": "focus", "window": "part of title"}}
{{"do": "click", "id": 12}}                         {{"do": "double_click", "id": 12}}
{{"do": "type", "text": "...", "id": 7}}            (id optional — omit to type where the cursor is)
{{"do": "keys", "keys": "ctrl+s"}}                  {{"do": "scroll", "direction": "down"}}
{{"do": "click_seen", "what": "description of the thing on screen"}}
{{"do": "look", "question": "what to check on the screen"}}
{{"do": "wait", "seconds": 2}}
{{"do": "done", "summary": "what was done, one sentence"}}
{{"do": "ask", "question": "what you need from {owner}"}}
Prefer click by id. Use keys for shortcuts. Say done as soon as the task is complete."""

_stop = threading.Event()
_running = threading.Lock()


def _watch_escape():
    """Esc = stop. Runs only while Nova has the mouse."""
    try:
        from pynput import keyboard

        def on_press(key):
            if key == keyboard.Key.esc:
                _stop.set()
                return False
        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()
        return listener
    except Exception:
        return None


def _risky(action: dict, controls: dict[int, dict], allow: str) -> str | None:
    """The words that make an action need your OK (unless you already allowed them)."""
    text = ""
    if action.get("do") in ("click", "double_click"):
        text = controls.get(int(action.get("id") or 0), {}).get("name", "")
    elif action.get("do") == "click_seen":
        text = action.get("what", "")
    elif action.get("do") == "keys" and action.get("keys", "").lower().replace(" ", "") in ("ctrl+enter", "alt+s"):
        text = "send"                                  # Outlook / Gmail "send" shortcuts
    m = RISKY.search(text or "")
    if not m:
        return None
    word = m.group(1).lower()
    if allow and word in allow.lower():
        return None
    return word


def run_task(goal: str, allow: str = "", max_steps: int = 20, d: Desk | None = None, llm=None) -> str:
    d, llm = d or desk(), llm or context.llm
    if not _running.acquire(blocking=False):
        return "I'm already busy on the PC — say 'stop' or press Esc first."
    _stop.clear()
    listener = _watch_escape() if d is _desk else None
    owner = context.cfg.assistant.owner if context.cfg else "the user"
    history: list[str] = []
    seen = ""
    store = context.store
    try:
        if context.announce:
            context.announce("I'm taking the mouse. Press Escape to stop me.")
        for step in range(1, max_steps + 1):
            if _stop.is_set():
                return f"Stopped — you took over. Done so far: {'; '.join(history[-5:]) or 'nothing yet'}."
            try:
                if d.cursor() == (0, 0):
                    return "Stopped — mouse thrown to the corner. " + ("Done so far: " + "; ".join(history[-5:])
                                                                      if history else "")
            except Exception:
                pass
            try:
                title, ctrls = d.read("")
            except Exception as e:
                title, ctrls = f"(couldn't read: {e})", []
            by_id = {c["id"]: c for c in ctrls}
            prompt = AGENT_PROMPT.format(
                owner=owner, goal=goal, allowed=f"{owner} has approved: {allow}." if allow else "",
                windows="; ".join(w["title"] for w in d.windows()[:15]), title=title,
                controls="\n".join(f"{c['id']}. {c['type']} '{c['name']}'" for c in ctrls[:120]) or "(none readable)",
                seen=f"What you saw last: {seen}" if seen else "", history="\n".join(history[-12:]) or "(none)")
            raw = llm.complete(prompt, prefer_smart=True, temperature=0.1)
            m = re.search(r"\{.*\}", raw or "", re.S)
            try:
                act = json.loads(m.group(0)) if m else {}
            except json.JSONDecodeError:
                act = {}
            do = act.get("do")
            if not do:
                history.append(f"{step}. (couldn't decide)")
                continue
            if do == "done":
                return f"Done: {act.get('summary', goal)}"
            if do == "ask":
                return f"I need you: {act.get('question', '')} (Say the answer and 'carry on'.)"
            word = _risky(act, by_id, allow)
            if word:
                target = by_id.get(int(act.get("id") or 0), {}).get("name") or act.get("what", "")
                return (f"I stopped before clicking '{target}' — that would {word} something. Everything is ready. "
                        f"Say 'go ahead and {word}' and I'll finish it.")
            seen = ""
            try:
                if do == "open_app":
                    from .system import open_app
                    note = open_app(act.get("name", ""))
                    time.sleep(2.5)
                elif do == "focus":
                    note = "Focused " + d.window_action(act.get("window", ""), "focus")
                elif do in ("click", "double_click"):
                    note = f"Clicked '{d.click(int(act['id']), do == 'double_click')}'"
                elif do == "type":
                    d.type(str(act.get("text", "")), int(act["id"]) if act.get("id") else None)
                    note = f"Typed '{str(act.get('text', ''))[:40]}'"
                elif do == "keys":
                    d.keys(str(act.get("keys", "")))
                    note = f"Pressed {act.get('keys')}"
                elif do == "scroll":
                    d.scroll(act.get("direction", "down"))
                    note = f"Scrolled {act.get('direction', 'down')}"
                elif do == "click_seen":
                    pos = locate(act.get("what", "")) if d is _desk else getattr(d, "locate", lambda w: None)(act.get("what", ""))
                    if pos:
                        d.click_xy(*pos)
                        note = f"Clicked {act.get('what')}"
                    else:
                        note = f"Couldn't see {act.get('what')}"
                elif do == "look":
                    path, *_ = d.screenshot()
                    seen = llm.see(path, act.get("question", "What is on the screen?"))[:600]
                    note = f"Looked: {seen[:80]}"
                elif do == "wait":
                    time.sleep(min(10, float(act.get("seconds", 2))))
                    note = "Waited"
                else:
                    note = f"Unknown action {do}"
            except Exception as e:
                note = f"{do} failed: {e}"
            history.append(f"{step}. {note}")
            if store:
                store.log("tool", "desktop", f"🖱 {note}"[:200], json.dumps(act)[:500], turn=store.current_turn)
            time.sleep(0.6)
        return f"I ran out of steps. Done so far: {'; '.join(history[-6:])}."
    finally:
        if listener:
            try:
                listener.stop()
            except Exception:
                pass
        _running.release()


@tool(group="desktop")
def do_on_pc(task: str, allowed: str = "") -> str:
    """Do a job on the PC for the user, step by step, clicking and typing in any app like a person would
    (e.g. "open Excel, open Q3 deals and sort by Value", "in Outlook, attach the latest price list to a new mail
    to Sam"). Stops before sending, paying, buying, deleting or submitting unless those words are in `allowed`.
    Args:
        task: the whole job, in plain words
        allowed: risky actions the user has explicitly approved, e.g. "send" or "delete" (empty = none)
    """
    if err := _need_windows():
        return err
    return run_task(task, allowed)


@tool(group="desktop")
def stop_pc_task() -> str:
    """Stop what Nova is doing on the PC right now."""
    _stop.set()
    return "Stopping — the mouse and keyboard are yours."
