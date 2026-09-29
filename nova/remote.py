"""Remote access with Tailscale: open Nova's dashboard (and the globe) from your phone, anywhere, privately.

Tailscale puts your PC and phone on a private network only your devices can join. "Tailscale Serve" then gives the
PC an HTTPS address like https://your-pc.tail1234.ts.net that forwards to Nova on this PC. Nova itself still only
listens on localhost, and nothing is exposed to the public internet (we never use "Funnel").

Setup once: install Tailscale on the PC and the phone (tailscale.com/download), sign in with the same account,
then Settings → Remote access → Set up (or say "set up remote access").
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import context

GLOBE_HTTPS_PORT = 8443
_cache: dict = {"t": 0.0, "status": None}


def exe() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    if platform.system() == "Windows":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
            p = Path(base) / "Tailscale" / "tailscale.exe"
            if base and p.exists():
                return str(p)
    return None


def _run(args: list[str], timeout: int = 20) -> tuple[int, str]:
    ts = exe()
    if not ts:
        return 127, "Tailscale isn't installed."
    flags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0   # type: ignore[attr-defined]
    try:
        p = subprocess.run([ts, *args], capture_output=True, text=True, timeout=timeout, creationflags=flags)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "Tailscale didn't answer in time."
    except Exception as e:
        return 1, str(e)


def _port() -> int:
    return int(((context.cfg or {}).get("dashboard") or {}).get("port", 8765)) if context.cfg else 8765


def _globe_port() -> int:
    return int(((context.cfg or {}).get("globe") or {}).get("port", 4173)) if context.cfg else 4173


def status(fresh: bool = False) -> dict:
    """Is Tailscale installed, signed in, and is Nova being served? Cached for 30 s (it's called per request)."""
    if not fresh and _cache["status"] and time.time() - _cache["t"] < 30:
        return _cache["status"]
    out = {"installed": exe() is not None, "running": False, "dns_name": "", "ip": "", "serving": False,
           "globe_serving": False, "url": "", "globe_url": "", "message": ""}
    if not out["installed"]:
        out["message"] = "Tailscale isn't installed on this PC — get it from tailscale.com/download."
    else:
        code, text = _run(["status", "--json"])
        try:
            st = json.loads(text[text.index("{"):]) if "{" in text else {}
        except Exception:
            st = {}
        state = st.get("BackendState", "")
        me = st.get("Self") or {}
        out["dns_name"] = (me.get("DNSName") or "").rstrip(".").lower()
        out["ip"] = (me.get("TailscaleIPs") or [""])[0]
        out["running"] = state == "Running"
        if state == "NeedsLogin":
            out["message"] = "Tailscale needs you to sign in — open the Tailscale app on this PC."
        elif state and state != "Running":
            out["message"] = f"Tailscale is {state.lower()} — open the Tailscale app and connect."
        if out["running"]:
            code, text = _run(["serve", "status", "--json"])
            served = text if code == 0 else ""
            out["serving"] = f"127.0.0.1:{_port()}" in served or f"localhost:{_port()}" in served
            out["globe_serving"] = f":{_globe_port()}" in served
            if out["dns_name"]:
                out["url"] = f"https://{out['dns_name']}/" if out["serving"] else ""
                out["globe_url"] = f"https://{out['dns_name']}:{GLOBE_HTTPS_PORT}/" if out["globe_serving"] else ""
            if not out["serving"]:
                out["message"] = "Tailscale is connected. Click Set up to reach Nova from your phone."
    _cache.update(t=time.time(), status=out)
    return out


def allowed_host(host: str) -> bool:
    """The dashboard accepts requests addressed to this PC's Tailscale name (used by the Host check)."""
    host = (host or "").lower().split(":")[0]
    if not host.endswith(".ts.net"):
        return False
    st = status()
    return bool(st["dns_name"]) and host == st["dns_name"]


def _https_link(text: str) -> str:
    m = re.search(r"https://login\.tailscale\.com/\S+", text or "")
    return m.group(0).rstrip(".") if m else ""


def enable(globe_too: bool = True) -> dict:
    st = status(fresh=True)
    if not st["installed"] or not st["running"]:
        return {"ok": False, "message": st["message"] or "Tailscale isn't running."}
    code, text = _run(["serve", "--bg", f"http://127.0.0.1:{_port()}"], timeout=40)
    if code != 0:
        link = _https_link(text)
        if link or "https" in text.lower() and "enable" in text.lower():
            return {"ok": False, "message": "One more step: turn on HTTPS for your Tailscale network (free, one click)"
                    + (f": {link}" if link else " in the Tailscale admin console → DNS → HTTPS Certificates.")
                    + " Then click Set up again.", "link": link}
        return {"ok": False, "message": f"Tailscale said: {text.strip()[:300]}"}
    if globe_too:
        _run(["serve", "--bg", f"--https={GLOBE_HTTPS_PORT}", f"http://127.0.0.1:{_globe_port()}"], timeout=40)
    st = status(fresh=True)
    if context.store:
        context.store.log("remote", "system", "📱 Remote access on (Tailscale)", st["url"], turn=0)
    return {**st, "ok": bool(st["url"]),
            "message": f"Done — open {st['url']} on your phone (Tailscale must be on there too)."
            if st["url"] else "Tailscale accepted it, but I can't see the address yet — try Check in a moment."}


def disable() -> dict:
    _run(["serve", "--https=443", "off"])
    _run(["serve", f"--https={GLOBE_HTTPS_PORT}", "off"])
    st = status(fresh=True)
    return {**st, "ok": True, "message": "Remote access is off. Nova only answers on this PC again."}


def qr_svg(text: str, scale: int = 5) -> str:
    try:
        import segno
    except ImportError:
        return ""
    return segno.make(text, error="m").svg_inline(scale=scale, dark="#e9ecff", light=None, border=2)
