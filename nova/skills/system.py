"""PC control: apps, links, status, screenshots, clipboard, reminders, shell."""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import context
from ..config import resolve
from ..tools import register_group, tool

register_group("system", [])   # always available

WINDOWS = platform.system() == "Windows"
APPS = {
    "chrome": "chrome", "browser": "chrome", "edge": "msedge", "firefox": "firefox",
    "notepad": "notepad", "calculator": "calc", "explorer": "explorer", "file explorer": "explorer",
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook", "teams": "ms-teams:",
    "vscode": "code", "vs code": "code", "visual studio code": "code", "terminal": "wt", "cmd": "cmd",
    "spotify": "spotify:", "settings": "ms-settings:", "task manager": "taskmgr", "whatsapp": "whatsapp:",
    "paint": "mspaint", "obsidian": "obsidian:",
}


def _tz():
    return ZoneInfo(context.cfg.assistant.timezone)


def parse_when(when: str) -> dt.datetime:
    import dateparser
    d = dateparser.parse(when, settings={"TIMEZONE": context.cfg.assistant.timezone,
                                         "RETURN_AS_TIMEZONE_AWARE": True, "PREFER_DATES_FROM": "future"})
    if not d:
        raise ValueError(f"I couldn't understand the time '{when}'")
    return d.astimezone(_tz())


@tool(group="system")
def get_time() -> str:
    """Get the current date and time."""
    return dt.datetime.now(_tz()).strftime("%A %d %B %Y, %H:%M")


@tool(group="system")
def open_app(name: str) -> str:
    """Open an application on the PC.
    Args:
        name: app name, e.g. chrome, excel, word, outlook, vscode, spotify, calculator, explorer
    """
    target = APPS.get(name.lower().strip(), name)
    if WINDOWS:
        subprocess.Popen(f'start "" "{target}"', shell=True)
    else:
        subprocess.Popen([target])
    return f"Opening {name}."


@tool(group="system")
def open_url(url: str) -> str:
    """Open a website in the default browser.
    Args:
        url: the web address
    """
    if not url.startswith("http"):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url}"


@tool(group="system")
def system_status() -> dict:
    """CPU, memory, disk, battery and GPU status of the PC."""
    import psutil
    info = {
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "ram_used_percent": psutil.virtual_memory().percent,
        "disk_free_gb": round(shutil.disk_usage(Path.home()).free / 1e9, 1),
        "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1),
    }
    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if bat:
        info["battery_percent"] = bat.percent
        info["plugged_in"] = bat.power_plugged
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                              "--format=csv,noheader"], capture_output=True, text=True, timeout=5).stdout.strip()
        if out:
            info["gpu"] = out
    except Exception:
        pass
    return info


@tool(group="system")
def take_screenshot() -> str:
    """Take a screenshot of the PC screen and send it to the user's phone."""
    import mss
    out = resolve("workspace/screenshots") / f"screen_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    with mss.mss() as s:
        s.shot(mon=-1, output=str(out))
    context.attach(out)
    context.record("image", out.name, out, "screenshot")
    return f"Screenshot saved to {out} and attached."


@tool(group="system")
def look_at_screen(question: str = "Describe what is on the screen.") -> str:
    """Look at what is currently on the PC screen and answer a question about it (uses Gemini vision).
    Args:
        question: what to find out, e.g. "what error is showing?"
    """
    import mss
    out = resolve("workspace/screenshots") / "last_look.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    with mss.mss() as s:
        s.shot(mon=1, output=str(out))
    return context.llm.see(str(out), question)


@tool(group="system")
def look_at_image(path: str, question: str = "Describe this image in detail.") -> str:
    """Look at an image file (photo, screenshot, scan) and answer a question about it (uses Gemini vision).
    Args:
        path: full path to the image
        question: what to find out
    """
    return context.llm.see(os.path.expanduser(path), question)


@tool(group="system")
def read_clipboard() -> str:
    """Read the text currently on the clipboard."""
    import pyperclip
    return pyperclip.paste()[:5000] or "(clipboard is empty)"


@tool(group="system")
def copy_to_clipboard(text: str) -> str:
    """Put text on the clipboard.
    Args:
        text: text to copy
    """
    import pyperclip
    pyperclip.copy(text)
    return "Copied to clipboard."


@tool(group="system", confirm=True)
def run_shell(command: str) -> str:
    """Run a PowerShell command on the PC and return its output. Use for tasks no other tool covers.
    Args:
        command: the PowerShell command
    """
    if not context.cfg.system.get("allow_shell", True):
        return "Shell commands are disabled in config.yaml."
    shell = ["powershell", "-NoProfile", "-Command", command] if WINDOWS else ["bash", "-lc", command]
    p = subprocess.run(shell, capture_output=True, text=True, timeout=120)
    out = (p.stdout + ("\n" + p.stderr if p.stderr else "")).strip()
    return f"exit code {p.returncode}\n{out[-4000:]}"


@tool(group="system", confirm=True)
def power_action(action: str) -> str:
    """Lock, sleep, restart or shut down the PC.
    Args:
        action: one of lock, sleep, restart, shutdown
    """
    cmds = {
        "lock": "rundll32.exe user32.dll,LockWorkStation",
        "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
        "restart": "shutdown /r /t 30",
        "shutdown": "shutdown /s /t 30",
    }
    if action not in cmds:
        return f"Unknown action {action}"
    subprocess.Popen(cmds[action], shell=True)
    return f"{action.title()} started." + (" You have 30 seconds; say 'cancel shutdown' to stop it." if action in ("restart", "shutdown") else "")


@tool(group="system")
def cancel_shutdown() -> str:
    """Cancel a pending restart or shutdown."""
    subprocess.Popen("shutdown /a", shell=True)
    return "Shutdown cancelled."


# ── reminders ─────────────────────────────────────────────
_REM_FILE = None
_rem_lock = threading.Lock()


def _rem_path() -> Path:
    global _REM_FILE
    if _REM_FILE is None:
        _REM_FILE = resolve("data/reminders.json")
        _REM_FILE.parent.mkdir(parents=True, exist_ok=True)
    return _REM_FILE


def _load_rem() -> list:
    p = _rem_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _save_rem(items: list) -> None:
    _rem_path().write_text(json.dumps(items, indent=1), encoding="utf-8")


@tool(group="system")
def set_reminder(text: str, when: str) -> str:
    """Set a reminder. Nova will say it out loud and message the user's phone at that time.
    Args:
        text: what to remind about
        when: when, e.g. "in 20 minutes", "tomorrow 8am", "Friday 14:00"
    """
    at = parse_when(when)
    with _rem_lock:
        items = _load_rem()
        items.append({"id": uuid.uuid4().hex[:6], "text": text, "at": at.isoformat()})
        _save_rem(items)
    return f"Reminder set for {at:%a %d %b %H:%M}: {text}"


@tool(group="system")
def list_reminders() -> list:
    """List upcoming reminders."""
    return _load_rem() or "No reminders."


@tool(group="system")
def cancel_reminder(reminder_id: str) -> str:
    """Cancel a reminder by its id (see list_reminders).
    Args:
        reminder_id: the reminder id
    """
    with _rem_lock:
        items = _load_rem()
        left = [r for r in items if r["id"] != reminder_id]
        _save_rem(left)
    return "Cancelled." if len(left) < len(items) else "No reminder with that id."


def reminder_worker(speak) -> None:
    """Background thread: fires due reminders."""
    while True:
        try:
            now = dt.datetime.now(_tz())
            with _rem_lock:
                items = _load_rem()
                due = [r for r in items if dt.datetime.fromisoformat(r["at"]) <= now]
                if due:
                    _save_rem([r for r in items if r not in due])
            for r in due:
                msg = f"Reminder: {r['text']}"
                if context.store:
                    context.store.log("reminder", "system", msg)
                context.push("⏰ " + msg)
                if speak:
                    speak(msg)
        except Exception as e:
            print(f"[reminders] {e}")
        time.sleep(15)
