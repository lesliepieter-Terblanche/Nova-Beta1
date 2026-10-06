"""Remote access with Tailscale: open Nova's dashboard from your phone, anywhere, privately.

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

OLD_GLOBE_PORT = 8443          # God's Eye View used to be shared here (removed in v2.34); still switched off on disable
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


def status(fresh: bool = False) -> dict:
    """Is Tailscale installed, signed in, and is Nova being served? Cached for 30 s (it's called per request)."""
    if not fresh and _cache["status"] and time.time() - _cache["t"] < 30:
        return _cache["status"]
    out = {"installed": exe() is not None, "running": False, "dns_name": "", "ip": "", "serving": False,
           "url": "", "message": ""}
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
            m = serve_map(served)
            if m is not None:                    # exact: which local port each https port really forwards to
                out["root_target"] = m.get("443", "")
                out["serving"] = _points_to(m.get("443", ""), _port())
            else:
                out["serving"] = f"127.0.0.1:{_port()}" in served or f"localhost:{_port()}" in served
            if out["dns_name"]:
                out["url"] = f"https://{out['dns_name']}/" if out["serving"] else ""
            if not out["serving"] and out.get("root_target"):
                out["message"] = (f"Your Tailscale address points at {out['root_target']} instead of Nova — "
                                  "click Set up to fix it.")
            elif not out["serving"]:
                out["message"] = "Tailscale is connected. Click Set up to reach Nova from your phone."
    _cache.update(t=time.time(), status=out)
    return out


def serve_map(text: str) -> dict | None:
    """`tailscale serve status --json` → {"443": "http://127.0.0.1:8765", "8443": "…"} (None if not that format)."""
    try:
        d = json.loads(text[text.index("{"):]) if "{" in (text or "") else {}
    except ValueError:
        return None
    web = d.get("Web") if isinstance(d, dict) else None
    if not isinstance(web, dict):
        return None
    out = {}
    for host_port, conf in web.items():
        handler = ((conf or {}).get("Handlers") or {}).get("/") or {}
        out[host_port.rsplit(":", 1)[-1]] = str(handler.get("Proxy") or handler.get("Path") or "")
    return out


def _points_to(target: str, port: int) -> bool:
    t = (target or "").rstrip("/")
    return t.endswith(f"127.0.0.1:{port}") or t.endswith(f"localhost:{port}") or t == str(port)


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


def _serve(args: list[str], wait: float = 25) -> tuple[str, str]:
    """Run `tailscale serve …`. The first time, Tailscale prints an approval link and then WAITS until you approve it
    in the browser — so we return as soon as that link appears and let it finish in the background.
    Returns (state, text): state is "ok", "approve" (text = link), or "error"."""
    import threading
    ts = exe()
    if not ts:
        return "error", "Tailscale isn't installed."
    flags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0   # type: ignore[attr-defined]
    proc = subprocess.Popen([ts, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            creationflags=flags)
    lines: list[str] = []
    reader = threading.Thread(target=lambda: [lines.append(ln) for ln in proc.stdout], daemon=True)
    reader.start()
    end = time.time() + wait
    while time.time() < end:
        link = _https_link("".join(lines))
        if link:
            _pending["proc"] = proc                       # keeps waiting for your approval, then finishes by itself
            return "approve", link
        if proc.poll() is not None:
            reader.join(timeout=2)
            text = "".join(lines)
            link = _https_link(text)
            if link:
                return "approve", link
            return ("ok", text) if proc.returncode == 0 else ("error", text.strip()[:300] or "Tailscale failed.")
        time.sleep(0.2)
    text = "".join(lines)
    link = _https_link(text)
    if link:
        _pending["proc"] = proc
        return "approve", link
    proc.kill()
    return "error", "Tailscale didn't answer in time. Open the Tailscale app, make sure it says Connected, then retry."


_pending: dict = {}


def enable() -> dict:
    st = status(fresh=True)
    if not st["installed"] or not st["running"]:
        return {"ok": False, "message": st["message"] or "Tailscale isn't running."}
    state, text = _serve(["serve", "--bg", f"http://127.0.0.1:{_port()}"])
    if state == "approve":
        return {"ok": False, "link": text, "message": "One-time step: open this link, click Enable (it lets your PC "
                f"share Nova privately on your Tailscale network), then click Check again: {text}"}
    if state == "error":
        return {"ok": False, "message": f"Tailscale said: {text}"}
    st = status(fresh=True)
    if context.store:
        context.store.log("remote", "system", "📱 Remote access on (Tailscale)", st["url"], turn=0)
    if st["url"]:
        _remember(True)
    return {**st, "ok": bool(st["url"]),
            "message": f"Done — open {st['url']} on your phone (Tailscale must be on there too)."
            if st["url"] else "Tailscale accepted it, but I can't see the address yet — try Check in a moment."}


def _remember(on: bool) -> None:
    """Remember that remote access should come back every time Nova starts."""
    try:
        from . import settings
        settings.apply({"values": {"remote.enabled": on}})
    except Exception as e:
        print(f"[remote] couldn't save setting: {e}")


def ensure() -> dict:
    """Called when Nova starts: make sure Tailscale is connected and Nova is shared on it (if you set it up)."""
    if not exe():
        return status(fresh=True)
    st = status(fresh=True)
    if not st["running"]:
        gui = Path(exe()).with_name("tailscale-ipn.exe")
        if platform.system() == "Windows" and gui.exists():          # the tray app starts/reconnects Tailscale
            try:
                subprocess.Popen([str(gui)], creationflags=0x00000008)   # DETACHED_PROCESS
            except Exception:
                pass
        _run(["up"], timeout=25)                                     # reconnect (no-op if already up)
        for _ in range(10):
            st = status(fresh=True)
            if st["running"]:
                break
            time.sleep(2)
    if st["running"] and not st["serving"]:
        state, text = _serve(["serve", "--bg", f"http://127.0.0.1:{_port()}"], wait=15)
        if state == "approve":
            context.push("📱 Tailscale needs a one-time approval to share Nova with your phone — open this on the PC "
                         f"and click Enable: {text}", [])
    if st["running"]:
        st = finish_setup()
    return st


def finish_setup() -> dict:
    """After you've approved the link: report the address."""
    return status(fresh=True)


def disable() -> dict:
    _run(["serve", "--https=443", "off"])
    _run(["serve", f"--https={OLD_GLOBE_PORT}", "off"])
    _remember(False)
    st = status(fresh=True)
    return {**st, "ok": True, "message": "Remote access is off. Nova only answers on this PC again."}


def qr_svg(text: str, scale: int = 5) -> str:
    try:
        import segno
    except ImportError:
        return ""
    return segno.make(text, error="m").svg_inline(scale=scale, dark="#e9ecff", light=None, border=2)
