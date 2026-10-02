"""The phone's screen, live, in a window on the PC — click and type in it with the mouse and keyboard.

Uses scrcpy (free, open source: github.com/Genymobile/scrcpy). Nova downloads it into tools/scrcpy the first time
(about 20 MB) and points it at the same connection she already has to the phone.
"""
from __future__ import annotations

import io
import os
import platform
import shutil
import subprocess
import zipfile
from pathlib import Path

from . import phone as ph
from .config import ROOT

RELEASES = "https://api.github.com/repos/Genymobile/scrcpy/releases/latest"
_proc: subprocess.Popen | None = None


def exe_path() -> Path | None:
    name = "scrcpy.exe" if platform.system() == "Windows" else "scrcpy"
    c = str(ph.cfg().get("scrcpy_path", "")).strip()
    if c and Path(c).exists():
        return Path(c)
    for p in sorted((ROOT / "tools" / "scrcpy").rglob(name)) if (ROOT / "tools" / "scrcpy").exists() else []:
        return p
    found = shutil.which("scrcpy")
    return Path(found) if found else None


def install() -> Path:
    if platform.system() != "Windows":
        raise RuntimeError("install scrcpy with your package manager (e.g. 'sudo apt install scrcpy')")
    import httpx
    rel = httpx.get(RELEASES, timeout=30, follow_redirects=True, headers={"Accept": "application/vnd.github+json"})
    rel.raise_for_status()
    asset = next((a for a in rel.json().get("assets", []) if "win64" in a["name"] and a["name"].endswith(".zip")), None)
    if not asset:
        raise RuntimeError("couldn't find the Windows download of scrcpy")
    r = httpx.get(asset["browser_download_url"], timeout=300, follow_redirects=True)
    r.raise_for_status()
    dest = ROOT / "tools" / "scrcpy"
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(dest)
    p = exe_path()
    if not p:
        raise RuntimeError("downloaded scrcpy but the program isn't in it")
    return p


def running() -> bool:
    return _proc is not None and _proc.poll() is None


def command(exe: Path, serial: str) -> list[str]:
    c = ph.cfg()
    return [str(exe), "-s", serial, "--window-title", "Nova · " + str(c.get("name") or "phone"), "--no-audio",
            "--max-size", str(int(c.get("mirror_size", 1280))), "--video-bit-rate", str(c.get("mirror_bitrate", "4M")),
            "--stay-awake"] + (["--always-on-top"] if c.get("mirror_on_top", False) else [])


def start(popen=subprocess.Popen) -> str:
    """Open the window. Returns what to tell the user."""
    global _proc
    if running():
        return "Your phone's screen is already open on the PC."
    p = ph.phone()
    if err := p.connect():
        return err
    exe = exe_path() or install()
    env = dict(os.environ)
    adb = ph.adb_path()
    if adb:
        env["ADB"] = str(adb)                           # share Nova's own adb: two different ones fight each other
    flags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0     # type: ignore[attr-defined]
    _proc = popen(command(exe, p.serial), env=env, cwd=str(exe.parent), creationflags=flags)
    return ("Your phone's screen is open in a window on the PC — click and type in it like the phone itself. "
            "Say 'close the phone screen' when you're done.")


def stop() -> str:
    global _proc
    if not running():
        _proc = None
        return "The phone's screen isn't open on the PC."
    _proc.terminate()
    _proc = None
    return "Closed the phone's screen on the PC."
