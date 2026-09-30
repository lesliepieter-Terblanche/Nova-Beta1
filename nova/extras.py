"""Heavy optional add-ons, each in its own separate Python (tools/<name>/venv) so their pinned packages never clash
with Nova's: browser-use (the AI web agent) and docling (IBM's document reader). Installed once from Settings or by
asking Nova; Nova then runs them as a separate process."""
from __future__ import annotations

import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import ROOT

EXTRAS = {
    "browser_use": {"title": "Web agent (browser-use)", "packages": ["browser-use>=0.13,<0.14"],
                    "minutes": "3-5", "size": "~400 MB"},
    "docling": {"title": "Docling document reader", "packages": ["docling>=2.40"],
                "minutes": "5-15", "size": "~2 GB (includes PyTorch)"},
}
_state: dict[str, dict] = {}
_lock = threading.Lock()


def home(name: str) -> Path:
    return ROOT / "tools" / name.replace("_", "-")


def python(name: str) -> Path:
    v = home(name) / "venv"
    return v / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")


def installed(name: str) -> bool:
    return (home(name) / "installed.txt").exists() and python(name).exists()


def status(name: str) -> dict:
    st = _state.get(name, {})
    return {"name": name, "title": EXTRAS[name]["title"], "installed": installed(name), "state": st.get("state", ""),
            "log": list(st.get("log", []))[-6:], "size": EXTRAS[name]["size"], "minutes": EXTRAS[name]["minutes"]}


def _run(name: str, done=None) -> None:
    st = _state[name]
    log = st["log"]

    def step(args: list[str], what: str) -> None:
        log.append(what)
        r = subprocess.run(args, capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            raise RuntimeError(f"{what.rstrip('…')} failed: {(r.stderr or r.stdout)[-600:]}")
    try:
        home(name).mkdir(parents=True, exist_ok=True)
        if not python(name).exists():
            step([sys.executable, "-m", "venv", str(home(name) / "venv")], "Making a separate Python…")
        step([str(python(name)), "-m", "pip", "install", "-q", "--upgrade", "pip"], "Updating pip…")
        step([str(python(name)), "-m", "pip", "install", "-q", *EXTRAS[name]["packages"]],
             f"Installing {EXTRAS[name]['title']} ({EXTRAS[name]['size']}, {EXTRAS[name]['minutes']} min)…")
        (home(name) / "installed.txt").write_text(time.strftime("%Y-%m-%d %H:%M"), encoding="utf-8")
        st["state"] = "done"
        log.append(f"{EXTRAS[name]['title']} installed ✓")
    except Exception as e:
        st["state"] = "error"
        log.append(str(e))
    if done:
        try:
            done(status(name))
        except Exception:
            pass


def install(name: str, done=None) -> str:
    if name not in EXTRAS:
        raise KeyError(name)
    with _lock:
        if _state.get(name, {}).get("state") == "installing":
            return f"Already installing {EXTRAS[name]['title']}…"
        _state[name] = {"state": "installing", "log": []}
    threading.Thread(target=_run, args=(name, done), daemon=True, name=f"install-{name}").start()
    x = EXTRAS[name]
    return f"Installing {x['title']} in the background ({x['size']}, about {x['minutes']} minutes)."


def announce_done(st: dict) -> None:
    from . import context
    msg = f"✅ {st['title']} is installed." if st["installed"] else \
        f"⚠️ {st['title']} didn't install: {(st['log'] or ['unknown error'])[-1][:300]}"
    if context.push:
        context.push(msg, [])
    if context.announce:
        context.announce(msg)
