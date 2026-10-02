"""Phone hands: Nova operates your Android phone (built for a Galaxy S24+) through ADB — Android's own developer
connection — over your Tailscale network, so it works from anywhere, on Wi-Fi or mobile data.

One-time on the phone: Auto Blocker off, Developer options → USB debugging on, plug into the PC once and tap
"Always allow from this computer". Then say "set up my phone": Nova switches the phone to listen on the network
(port 5555) and remembers its Tailscale address. After a phone restart, plug it in for a few seconds (or switch
Wireless debugging on at home) and say "set up my phone" again.

Nova reads the screen as text (the same accessibility tree screen readers use), taps, types, swipes, opens apps,
takes screenshots, reads notifications and copies files. She stops before sending, paying, buying or deleting unless
you allowed it, and never enters your PIN.
"""
from __future__ import annotations

import io
import json
import platform
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from . import context
from .config import ROOT, resolve

PORT = 5555
TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-{os}.zip"
KEYS = {"home": 3, "back": 4, "enter": 66, "recents": 187, "apps": 187, "power": 26, "wake": 224, "sleep": 223,
        "volume_up": 24, "volume_down": 25, "mute": 164, "play_pause": 85, "next": 87, "previous": 88, "delete": 67,
        "tab": 61, "escape": 111, "menu": 82, "camera": 27, "search": 84, "notifications": 83}
APPS = {"whatsapp": "com.whatsapp", "whatsapp business": "com.whatsapp.w4b", "chrome": "com.android.chrome",
        "gmail": "com.google.android.gm", "maps": "com.google.android.apps.maps", "youtube": "com.google.android.youtube",
        "camera": "com.sec.android.app.camera", "gallery": "com.sec.android.gallery3d", "settings": "com.android.settings",
        "messages": "com.samsung.android.messaging", "phone": "com.samsung.android.dialer",
        "contacts": "com.samsung.android.app.contacts", "calendar": "com.samsung.android.calendar",
        "clock": "com.sec.android.app.clockpackage", "calculator": "com.sec.android.app.popupcalculator",
        "my files": "com.sec.android.app.myfiles", "files": "com.sec.android.app.myfiles",
        "notes": "com.samsung.android.app.notes", "play store": "com.android.vending", "spotify": "com.spotify.music",
        "telegram": "org.telegram.messenger", "linkedin": "com.linkedin.android", "facebook": "com.facebook.katana",
        "instagram": "com.instagram.android", "tiktok": "com.zhiliaoapp.musically", "teams": "com.microsoft.teams",
        "outlook": "com.microsoft.office.outlook", "tailscale": "com.tailscale.ipn", "netflix": "com.netflix.mediaclient",
        "capcut": "com.lemon.lvoverseas", "canva": "com.canva.editor", "drive": "com.google.android.apps.docs",
        "photos": "com.google.android.apps.photos", "uber": "com.ubercab", "takealot": "fi.android.takealot"}
MESSAGING = {"com.whatsapp": "WhatsApp", "com.whatsapp.w4b": "WhatsApp Business",
             "com.samsung.android.messaging": "Messages", "com.google.android.apps.messaging": "Messages",
             "org.telegram.messenger": "Telegram", "org.thoughtcrime.securesms": "Signal",
             "com.google.android.gm": "Gmail", "com.microsoft.office.outlook": "Outlook",
             "com.microsoft.teams": "Teams", "com.samsung.android.dialer": "Phone",
             "com.google.android.dialer": "Phone", "com.samsung.android.incallui": "Phone",
             "com.android.server.telecom": "Phone", "com.facebook.orca": "Messenger",
             "com.instagram.android": "Instagram", "com.linkedin.android": "LinkedIn", "com.Slack": "Slack"}
MESSAGE_APPS = {"whatsapp": "WhatsApp", "whatsapp business": "WhatsApp Business", "sms": "Messages", "text": "Messages",
                "messages": "Messages", "telegram": "Telegram", "signal": "Signal", "gmail": "Gmail", "email": "Gmail",
                "outlook": "Outlook", "teams": "Teams", "messenger": "Messenger", "instagram": "Instagram",
                "linkedin": "LinkedIn", "slack": "Slack"}
# Notifications that aren't a message from a person ("3 new messages", "Checking for new messages"…)
NOISE = re.compile(r"^(\d+ (new |unread )?(messages?|chats?|emails?|conversations?)\b.*|checking for new messages.*|"
                   r"whatsapp web.*|backup .*|.* is (running|active|using).*|tap (to|for) .*|ongoing .*call.*)$", re.I)
# Quick switches: what to tell the phone for on / off
SWITCHES = {
    "wifi": ("svc wifi enable", "svc wifi disable"),
    "bluetooth": ("svc bluetooth enable", "svc bluetooth disable"),
    "mobile data": ("svc data enable", "svc data disable"),
    "do not disturb": ("cmd notification set_dnd priority", "cmd notification set_dnd off"),
    "airplane mode": ("cmd connectivity airplane-mode enable", "cmd connectivity airplane-mode disable"),
    "location": ("cmd location set-location-enabled true", "cmd location set-location-enabled false"),
    "auto rotate": ("settings put system accelerometer_rotation 1", "settings put system accelerometer_rotation 0"),
    "battery saver": ("cmd power set-mode 1", "cmd power set-mode 0"),
    "dark mode": ("cmd uimode night yes", "cmd uimode night no"),
    "nfc": ("svc nfc enable", "svc nfc disable"),
    "silent": ("cmd audio set-ringer-mode SILENT", "cmd audio set-ringer-mode NORMAL"),
    "vibrate": ("cmd audio set-ringer-mode VIBRATE", "cmd audio set-ringer-mode NORMAL"),
}
SWITCH_NAMES = {"wi-fi": "wifi", "wi fi": "wifi", "wireless": "wifi", "data": "mobile data", "cellular data": "mobile data",
                "cellular": "mobile data", "dnd": "do not disturb", "do-not-disturb": "do not disturb",
                "donotdisturb": "do not disturb", "focus mode": "do not disturb", "flight mode": "airplane mode",
                "aeroplane mode": "airplane mode", "airplane": "airplane mode", "gps": "location",
                "location services": "location", "rotation": "auto rotate", "auto-rotate": "auto rotate",
                "autorotate": "auto rotate", "screen rotation": "auto rotate", "power saving": "battery saver",
                "power saver": "battery saver", "power saving mode": "battery saver", "night mode": "dark mode",
                "dark theme": "dark mode", "silent mode": "silent", "mute": "silent", "vibrate mode": "vibrate",
                "vibration": "vibrate"}
STREAMS = {"volume": 3, "media volume": 3, "music volume": 3, "ring volume": 2, "ringer volume": 2, "ringtone volume": 2,
           "alarm volume": 4, "call volume": 0, "notification volume": 5}
SHELL_FAIL = re.compile(r"(exception|unknown command|not found|no shell command|permission denial|usage:|"
                        r"can't find service|error:)", re.I)
RISKY = re.compile(r"\b(send|pay|payment|purchase|buy|order|checkout|delete|remove|erase|submit|transfer|confirm|"
                   r"uninstall|post|publish|call|dial|book|subscribe|factory reset|sign out|log out)\b", re.I)


def cfg() -> dict:
    return dict(((context.cfg or {}).get("phone") or {}))


# ── adb itself ────────────────────────────────────────────
def adb_path() -> Path | None:
    """adb from config, the PATH, or Nova's own copy in tools/platform-tools."""
    import shutil
    c = str(cfg().get("adb_path", "")).strip()
    if c and Path(c).exists():
        return Path(c)
    name = "adb.exe" if platform.system() == "Windows" else "adb"
    local = ROOT / "tools" / "platform-tools" / name
    if local.exists():
        return local
    found = shutil.which("adb")
    return Path(found) if found else None


def install_adb() -> Path:
    """Download Google's official platform-tools (about 7 MB) into tools/platform-tools."""
    import httpx
    osname = {"Windows": "windows", "Darwin": "darwin"}.get(platform.system(), "linux")
    r = httpx.get(TOOLS_URL.format(os=osname), timeout=120, follow_redirects=True)
    r.raise_for_status()
    dest = ROOT / "tools"
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(dest)
    p = adb_path()
    if not p:
        raise RuntimeError("downloaded the Android tools but adb isn't in them")
    if platform.system() != "Windows":
        p.chmod(0o755)
    return p


class Phone:
    """One Android phone over adb. `run` can be replaced in tests."""

    def __init__(self, run=None):
        self._run = run or self._real_run
        self.serial = ""
        self.lock = threading.Lock()

    @staticmethod
    def _real_run(args: list[str], timeout: float = 25, binary: bool = False):
        exe = adb_path() or install_adb()
        flags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0   # type: ignore[attr-defined]
        p = subprocess.run([str(exe), *args], capture_output=True, timeout=timeout, creationflags=flags)
        if binary:
            return p.returncode, p.stdout
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace").strip()

    # ── connection ────────────────────────────────────────
    def devices(self) -> list[tuple[str, str]]:
        _, out = self._run(["devices"])
        return [tuple(line.split()[:2]) for line in out.splitlines()[1:] if "\t" in line or len(line.split()) == 2]

    def address(self) -> str:
        a = str(cfg().get("address", "")).strip()
        return a if not a or ":" in a else f"{a}:{PORT}"

    def connect(self) -> str:
        """Use the saved network address (reconnecting if needed), else a phone on a cable. '' = connected."""
        addr = self.address()
        devs = dict(self.devices())
        if addr and devs.get(addr) == "device":
            self.serial = addr
            return ""
        if addr:
            for attempt in (1, 2):
                if devs.get(addr) or attempt == 2:        # a stale link ("offline") blocks a fresh one: drop it first
                    self._run(["disconnect", addr], timeout=8)
                _, out = self._run(["connect", addr], timeout=12)
                if "connected" in out.lower() and dict(self.devices()).get(addr) == "device":
                    self.serial = addr
                    return ""
        usb = [s for s, st in devs.items() if st == "device" and ":" not in s]
        if usb:
            self.serial = usb[0]
            return ""
        if any(st == "unauthorized" for st in devs.values()):
            return ("The phone is asking 'Allow USB debugging?' — unlock it, tick 'Always allow from this computer' "
                    "and tap Allow.")
        if addr:
            return (f"I can't reach the phone at {addr}. Is Tailscale on on the phone? If the phone was restarted, plug "
                    "it into the PC for a moment and say 'set up my phone'.")
        return "The phone isn't set up yet — plug it into the PC with the cable and say 'set up my phone'."

    def sh(self, command: str, timeout: float = 25) -> str:
        code, out = self._run(["-s", self.serial, "shell", command], timeout=timeout)
        return out

    def setup(self) -> str:
        """Cable (or home Wi-Fi wireless debugging) → listen on port 5555 → remember the Tailscale address."""
        devs = self.devices()
        if any(st == "unauthorized" for _, st in devs):
            return ("The phone is asking 'Allow USB debugging?' — unlock it, tick 'Always allow from this computer', "
                    "tap Allow, then say 'set up my phone' again.")
        ready = [s for s, st in devs if st == "device"]
        if not ready:                                        # no cable: try Wireless debugging on the home Wi-Fi
            _, found = self._run(["mdns", "services"], timeout=10)
            m = re.search(r"_adb-tls-connect\S*\s+(\d+\.\d+\.\d+\.\d+:\d+)", found)
            if m:
                self._run(["connect", m.group(1)], timeout=12)
                ready = [s for s, st in self.devices() if st == "device"]
        if not ready and self.address() and not self.connect():   # already set up: the link had only dropped
            return f"Your {cfg().get('name') or 'phone'} is connected again at {self.serial}."
        if not ready:
            return ("I can't see the phone. Plug it into the PC with the cable (use a data cable, pick 'File transfer' "
                    "if it asks), unlock it and tap Allow on the 'Allow USB debugging?' question — then try again.")
        usb = [s for s in ready if ":" not in s]
        self.serial = (usb or ready)[0]
        model = self.sh("getprop ro.product.model").strip() or "phone"
        ip = self.tailscale_ip()
        self._run(["-s", self.serial, "tcpip", str(PORT)], timeout=15)
        time.sleep(2.5)
        if not ip:
            wifi = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", self.sh("ip -4 addr show wlan0"))
            ip = wifi.group(1) if wifi else ""
            note = (" Tailscale isn't on on the phone, so this only works on your home Wi-Fi — install Tailscale on "
                    "the phone, sign in, and set it up again to use it from anywhere.")
        else:
            note = " It works from anywhere while Tailscale is on on the phone."
        if not ip:
            return f"Found your {model}, but I couldn't work out its network address. Is Wi-Fi or Tailscale on?"
        addr = f"{ip}:{PORT}"
        _, out = self._run(["connect", addr], timeout=12)
        ok = "connected" in out.lower()
        try:
            from . import settings
            settings.apply({"values": {"phone.address": ip, "phone.name": model}})
        except Exception as e:
            print(f"[phone] couldn't save the address: {e}")
        if context.cfg is not None:
            context.cfg.setdefault("phone", {})
            context.cfg["phone"]["address"], context.cfg["phone"]["name"] = ip, model
        if ok:
            self.serial = addr
            return f"Your {model} is connected at {addr}. You can unplug the cable.{note}"
        return (f"Your {model} is set to listen, but I couldn't connect to {addr} yet ({out[:80]}). Keep it plugged in "
                "and try 'phone status' in a few seconds.")

    def tailscale_ip(self) -> str:
        """The phone's Tailscale address: ask the phone, else pick the Android device in this PC's Tailscale."""
        out = self.sh("ip -4 -o addr show")
        m = re.search(r"inet (100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+)", out)
        if m:
            return m.group(1)
        try:
            from . import remote
            _, text = remote._run(["status", "--json"])
            st = json.loads(text[text.index("{"):])
            for peer in (st.get("Peer") or {}).values():
                if peer.get("OS") == "android" and peer.get("Online") and peer.get("TailscaleIPs"):
                    return peer["TailscaleIPs"][0]
        except Exception:
            pass
        return ""

    # ── looking ───────────────────────────────────────────
    def status(self) -> dict:
        bat = self.sh("dumpsys battery")
        level = re.search(r"level: (\d+)", bat)
        plugged = re.search(r"(AC|USB|Wireless) powered: true", bat)
        win = self.sh("dumpsys window | grep -E 'mDreamingLockscreen|mShowingLockscreen|isKeyguardShowing|mCurrentFocus'")
        power = self.sh("dumpsys power | grep -E 'mWakefulness='")
        focus = re.search(r"mCurrentFocus=Window\{\S+ \S+ ([^/\s}]+)", win)
        return {"model": self.sh("getprop ro.product.model").strip(), "android": self.sh("getprop ro.build.version.release").strip(),
                "battery": int(level.group(1)) if level else None, "charging": bool(plugged),
                "screen_on": "Awake" in power, "locked": bool(re.search(r"(Lockscreen|KeyguardShowing)=true", win)),
                "app": focus.group(1) if focus else "", "connection": self.serial}

    def health(self) -> dict:
        """Battery, temperature, storage, memory and Wi-Fi — for the dashboard card and 'how is my phone'."""
        bat = self.sh("dumpsys battery")
        num = lambda pat, text: (int(m.group(1)) if (m := re.search(pat, text)) else None)   # noqa: E731
        level, temp = num(r"level: (\d+)", bat), num(r"temperature: (\d+)", bat)
        h = {"model": self.sh("getprop ro.product.model").strip(), "battery": level,
             "charging": bool(re.search(r"(AC|USB|Wireless) powered: true", bat)),
             "temperature": round(temp / 10, 1) if temp is not None else None,
             "storage_free_gb": None, "storage_total_gb": None, "storage_used_pct": None,
             "memory_free_pct": None, "wifi": "", "signal": None, "connection": self.serial}
        for line in self.sh("df -k /data").splitlines()[1:]:
            cols = line.split()
            if len(cols) >= 4 and cols[1].isdigit() and cols[3].isdigit() and int(cols[1]):
                total, free = int(cols[1]), int(cols[3])
                h.update(storage_total_gb=round(total / 1048576, 1), storage_free_gb=round(free / 1048576, 1),
                         storage_used_pct=round(100 * (total - free) / total))
                break
        mem = self.sh("cat /proc/meminfo")
        mt, ma = num(r"MemTotal:\s+(\d+)", mem), num(r"MemAvailable:\s+(\d+)", mem)
        if mt and ma is not None:
            h["memory_free_pct"] = round(100 * ma / mt)
        wifi = self.sh("cmd wifi status")
        ssid = re.search(r'SSID: "([^"]+)"', wifi)
        if ssid and "<unknown" not in ssid.group(1):
            h["wifi"], h["signal"] = ssid.group(1), num(r"RSSI: (-?\d+)", wifi)
        return h

    def switch(self, name: str, on: bool) -> tuple[str, str]:
        """Flip a quick switch. Returns (the switch's proper name or '', what the phone said when it refused)."""
        n = re.sub(r"\s+", " ", name.lower().strip().removeprefix("the ").removeprefix("my "))
        n = SWITCH_NAMES.get(n, n)
        if n not in SWITCHES:
            return "", ""
        out = self.sh(SWITCHES[n][0 if on else 1])
        return n, (out[:160] if SHELL_FAIL.search(out or "") else "")

    def set_level(self, what: str, percent: int) -> str:
        """Brightness or a volume, 0-100. Returns '' when done, otherwise why not."""
        pct = max(0, min(100, int(percent)))
        w = what.lower().strip()
        if "bright" in w:
            self.sh("settings put system screen_brightness_mode 0")
            out = self.sh(f"settings put system screen_brightness {max(1, round(pct * 2.55))}")
            return out[:160] if SHELL_FAIL.search(out or "") else ""
        stream = STREAMS.get(w if w in STREAMS else w + " volume" if w + " volume" in STREAMS else "volume", 3)
        got = self.sh(f"cmd media_session volume --stream {stream} --get")
        m = re.search(r"\[(\d+)\.\.(\d+)\]", got)
        top = int(m.group(2)) if m else 15
        out = self.sh(f"cmd media_session volume --stream {stream} --set {round(top * pct / 100)}")
        return out[:160] if SHELL_FAIL.search(out or "") and "volume is" not in out else ""

    def ring(self) -> str:
        """Make the phone ring loudly even on silent: a one-second timer (alarms ignore silent mode), with the alarm
        volume at full. If the clock refuses, play a ringtone at full media volume instead."""
        self.wake()
        self.set_level("alarm volume", 100)
        out = self.sh("am start -a android.intent.action.SET_TIMER --ei android.intent.extra.alarm.LENGTH 1 "
                      "--ez android.intent.extra.alarm.SKIP_UI true --es android.intent.extra.alarm.MESSAGE Nova")
        if not re.search(r"(error|unable to resolve|exception|denial)", out, re.I):
            return "timer"
        tones = self.sh("for d in /system/media/audio/ringtones /product/media/audio/ringtones; "
                        "do ls $d/*.ogg 2>/dev/null; done | head -n 40").split()
        tones = [t for t in tones if t.endswith(".ogg")]
        if not tones:
            return ""
        tone = next((t for t in tones if "horizon" in t.lower()), tones[0])
        self.set_level("media volume", 100)
        out = self.sh(f"am start -a android.intent.action.VIEW -d 'file://{tone}' -t audio/ogg")
        return "" if re.search(r"(error|unable to resolve|exception)", out, re.I) else "ringtone"

    def where(self) -> dict | None:
        """The phone's last known position (from Android's own location service): lat, lon, accuracy in metres."""
        out = self.sh("dumpsys location", timeout=40)
        found = {}
        for m in re.finditer(r"Location\[(\w+) (-?\d+[.,]\d+),(-?\d+[.,]\d+)([^\]]*)", out):
            src, lat, lon = m.group(1), float(m.group(2).replace(",", ".")), float(m.group(3).replace(",", "."))
            acc = re.search(r"(?:hAcc|acc)=(\d+(?:[.,]\d+)?)", m.group(4))
            if abs(lat) < 0.0001 and abs(lon) < 0.0001:
                continue
            found.setdefault(src, {"lat": lat, "lon": lon, "source": src,
                                   "accuracy": round(float(acc.group(1).replace(",", "."))) if acc else None})
        return next((found[k] for k in ("fused", "gps", "network") if k in found), None) or next(iter(found.values()), None)

    def messages(self, app: str = "", sender: str = "") -> list[dict]:
        """Messages waiting on the phone (from its notifications): WhatsApp, SMS, Telegram, email, missed calls…"""
        want = MESSAGE_APPS.get(app.lower().strip(), app.strip()).lower()
        who = sender.lower().strip()
        out = []
        for n in self.notifications():
            name = MESSAGING.get(n["app"])
            if not name or not n["title"] or NOISE.match(n["text"] or n["title"]):
                continue
            if n["title"].strip().lower() in (name.lower(), "whatsapp", "messages") and not n["text"]:
                continue
            if want and want not in name.lower():
                continue
            if who and who not in n["title"].lower():
                continue
            out.append({"app": name, "from": n["title"], "text": n["text"]})
        return out

    def screenshot(self, path: Path) -> Path:
        code, data = self._run(["-s", self.serial, "exec-out", "screencap", "-p"], timeout=30, binary=True)
        if code != 0 or not data.startswith(b"\x89PNG"):
            raise RuntimeError("the phone didn't send a screenshot (banking and some payment apps block it)")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def screen(self) -> list[dict]:
        """What's on screen as numbered elements: text, type, whether it can be tapped, and where its centre is."""
        code, raw = self._run(["-s", self.serial, "exec-out", "uiautomator", "dump", "/dev/tty"], timeout=30)
        start, end = raw.find("<?xml"), raw.rfind("</hierarchy>")
        if start < 0 or end < 0:
            raise RuntimeError("couldn't read the screen (some apps block it, and it fails while a video plays)")
        out = []
        for node in ET.fromstring(raw[start:end + len("</hierarchy>")]).iter("node"):
            a = node.attrib
            text = (a.get("text") or "").strip() or (a.get("content-desc") or "").strip()
            clickable = a.get("clickable") == "true" or a.get("checkable") == "true"
            editable = "EditText" in a.get("class", "")
            if not text and not clickable and not editable:
                continue
            m = re.findall(r"\d+", a.get("bounds", ""))
            if len(m) != 4:
                continue
            x0, y0, x1, y1 = map(int, m)
            if x1 <= x0 or y1 <= y0:
                continue
            out.append({"id": len(out) + 1, "text": text[:120], "type": a.get("class", "").rsplit(".", 1)[-1],
                        "tap": clickable, "edit": editable, "checked": a.get("checked") == "true",
                        "focused": a.get("focused") == "true",
                        "x": (x0 + x1) // 2, "y": (y0 + y1) // 2, "res": a.get("resource-id", "").rsplit("/", 1)[-1]})
        return out

    def size(self) -> tuple[int, int]:
        m = re.search(r"(\d+)x(\d+)", self.sh("wm size"))
        return (int(m.group(1)), int(m.group(2))) if m else (1080, 2340)

    # ── doing ─────────────────────────────────────────────
    def wake(self) -> None:
        self.sh("input keyevent 224")
        self.sh("wm dismiss-keyguard")

    def tap(self, x: int, y: int) -> None:
        self.sh(f"input tap {int(x)} {int(y)}")

    def long_press(self, x: int, y: int) -> None:
        self.sh(f"input swipe {int(x)} {int(y)} {int(x)} {int(y)} 700")

    def swipe(self, direction: str) -> None:
        w, h = self.size()
        cx, cy = w // 2, h // 2
        moves = {"up": (cx, int(h * .72), cx, int(h * .28)), "down": (cx, int(h * .32), cx, int(h * .76)),
                 "left": (int(w * .85), cy, int(w * .15), cy), "right": (int(w * .15), cy, int(w * .85), cy)}
        x0, y0, x1, y1 = moves.get(direction, moves["up"])
        self.sh(f"input swipe {x0} {y0} {x1} {y1} 260")

    def type(self, text: str) -> None:
        for i, line in enumerate(str(text).split("\n")):
            if i:
                self.sh("input keyevent 66")
            if line:
                safe = re.sub(r"([\\\"'`$&|;<>()*?!~#{}\[\]])", r"\\\1", line).replace(" ", "%s")
                self.sh(f'input text "{safe}"')

    def key(self, name: str) -> bool:
        code = KEYS.get(name.lower().strip().replace(" ", "_"))
        if code is None:
            return False
        self.sh(f"input keyevent {code}")
        return True

    def packages(self) -> list[str]:
        return [p.removeprefix("package:").strip() for p in self.sh("pm list packages").splitlines() if p.strip()]

    def find_app(self, name: str) -> str:
        n = name.lower().strip()
        if "." in n and " " not in n:
            return n
        pkgs = self.packages()
        if APPS.get(n) in pkgs:
            return APPS[n]
        words = [w for w in re.split(r"\W+", n) if w]
        hits = [p for p in pkgs if all(w in p.lower() for w in words)]
        return sorted(hits, key=len)[0] if hits else ""

    def open_app(self, name: str) -> str:
        pkg = self.find_app(name)
        if not pkg:
            return ""
        self.sh(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
        return pkg

    def open_url(self, url: str) -> None:
        self.sh(f"am start -a android.intent.action.VIEW -d '{url}'")

    def notifications(self) -> list[dict]:
        out = self.sh("dumpsys notification --noredact", timeout=40)
        items, cur = [], None
        for line in out.splitlines():
            m = re.search(r"NotificationRecord\(\S+: pkg=(\S+)", line)
            if m:
                cur = {"app": m.group(1), "title": "", "text": ""}
                items.append(cur)
                continue
            if cur is None:
                continue
            t = re.search(r"android\.title=(?:String|SpannableString) \((.*)\)\s*$", line)
            x = re.search(r"android\.(?:text|bigText)=(?:String|SpannableString) \((.*)\)\s*$", line)
            if t and not cur["title"]:
                cur["title"] = t.group(1)
            if x and not cur["text"]:
                cur["text"] = x.group(1)
        seen, clean = set(), []
        for n in items:
            key = (n["app"], n["title"], n["text"])
            if (n["title"] or n["text"]) and key not in seen:
                seen.add(key)
                clean.append(n)
        return clean

    def pull(self, remote_path: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        code, out = self._run(["-s", self.serial, "pull", remote_path, str(dest)], timeout=180)
        if code != 0:
            raise RuntimeError(out[-200:])
        return dest

    def push(self, local: Path, remote_dir: str = "/sdcard/Download/") -> str:
        code, out = self._run(["-s", self.serial, "push", str(local), remote_dir], timeout=300)
        if code != 0:
            raise RuntimeError(out[-200:])
        self.sh(f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d 'file://{remote_dir}{local.name}'")
        return remote_dir + local.name

    def latest_photos(self, count: int = 3) -> list[str]:
        out = self.sh("ls -t /sdcard/DCIM/Camera | head -n %d" % max(1, min(20, count)))
        return [f"/sdcard/DCIM/Camera/{n.strip()}" for n in out.splitlines() if n.strip() and "No such" not in n]


_phone: Phone | None = None


def phone() -> Phone:
    global _phone
    if _phone is None:
        _phone = Phone()
    return _phone


def out_dir() -> Path:
    d = resolve("workspace/phone")
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── "do it for me" on the phone ───────────────────────────
AGENT_PROMPT = """You are operating {owner}'s Android phone (Samsung Galaxy) to do this task:
"{goal}"
{allowed}
App in front: {app}
On screen (number. type 'text' [tap = can be tapped, edit = text box]):
{elements}

Steps so far:
{history}

Choose the ONE next action. JSON only, one of:
{{"do": "open_app", "name": "whatsapp"}}      {{"do": "tap", "id": 12}}       {{"do": "long_press", "id": 12}}
{{"do": "type", "text": "...", "id": 7}}      (id optional: taps that text box first)
{{"do": "key", "key": "back"}}                (home, back, enter, recents, notifications, volume_up, volume_down)
{{"do": "swipe", "direction": "up"}}          (up = scroll down the page; down, left, right)
{{"do": "open_url", "url": "https://…"}}      {{"do": "wait", "seconds": 2}}
{{"do": "done", "summary": "what was done / what you found, one or two sentences"}}
{{"do": "ask", "question": "what you need from {owner}"}}
To write text ALWAYS use "type" with the text box's id — never long-press a text box and never paste (the clipboard
isn't yours). After typing a message, tap the Send button to send it. Check "Steps so far" before repeating a step.
Never enter a PIN, password or bank details. Say done as soon as the task is complete."""

BUBBLE = {"paste", "clipboard", "select all", "paste as plain text"}      # the keyboard's pop-up, never useful to Nova


def typed_ok(els: list[dict], text: str) -> bool:
    """Did the text arrive in a text box?"""
    want = re.sub(r"\s+", " ", text).strip().lower()[:24]
    return bool(want) and any(e["edit"] and want in re.sub(r"\s+", " ", e["text"]).lower() for e in els)


def message_task(to: str, text: str, app: str = "whatsapp", send: bool = True) -> str:
    """The step-by-step instruction for writing a message to someone in a messaging app."""
    name = MESSAGE_APPS.get(app.lower().strip(), app.strip() or "WhatsApp")
    end = "then tap Send." if send else "and stop there WITHOUT sending — it is only a draft."
    if name in ("Gmail", "Outlook"):
        return (f'Open {name}, start a new email to {to} (pick them from the suggestions), put a short fitting subject, '
                f'write exactly this as the body: "{text}" — {end}')
    return (f'Open {name}, open the chat with {to} (use the search if it is not on the first screen; pick the person, '
            f'not a group), type exactly this message: "{text}" — {end}')


def quick(text: str, p: "Phone") -> str | None:
    """Do a simple phone step without the model (a switch, a level, open an app, a key, ring). None = not that simple."""
    t = re.sub(r"\s+", " ", text.strip().rstrip(".!")).lower()
    t = re.sub(r"\s+on (?:my|the) phone$", "", t)
    m = re.match(r"^(?:set |put |turn )?(?:the |my )?(brightness|(?:media |music |ring(?:er|tone)? |alarm |call |notification )?"
                 r"volume)(?: to| at)? (\d{1,3}) ?(?:%|percent)?$", t)
    if m:
        why = p.set_level(m.group(1), int(m.group(2)))
        return f"{m.group(1).capitalize()} set to {min(100, int(m.group(2)))}%." if not why else None
    m = re.match(r"^(?:turn |switch |put |set )?(?:the |my )?(.+?) (on|off)$", t) or \
        re.match(r"^(?:turn|switch) (on|off) (?:the |my )?(.+)$", t) or re.match(r"^(enable|disable) (?:the |my )?(.+)$", t)
    if m:
        a, b = m.group(1), m.group(2)
        name, state = (a, b) if b in ("on", "off") else (b, "on" if a in ("on", "enable") else "off")
        done, why = p.switch(name, state == "on")
        if done and not why:
            return f"{done.capitalize()} is {state}."
        return None
    m = re.match(r"^open (.+)$", t)
    if m:
        target = m.group(1).removeprefix("the ").removesuffix(" app")
        if re.match(r"^(https?://|www\.)\S+$", target):
            p.open_url(target if "://" in target else "https://" + target)
            return f"Opened {target}."
        p.wake()
        return f"Opened {target}." if p.open_app(target) else None
    m = re.match(r"^press (?:the )?(.+?)(?: button| key)?$", t)
    if m and p.key(m.group(1)):
        return f"Pressed {m.group(1)}."
    if re.match(r"^(lock|lock (?:the|my) (?:phone|screen)|screen off)$", t):
        p.key("sleep")
        return "Locked the phone."
    if re.match(r"^(ring|ring (?:the|my) phone|find (?:the|my) phone)$", t):
        return "The phone is ringing." if p.ring() else None
    return None


_stop = threading.Event()
_busy = threading.Lock()


def stop_task() -> str:
    _stop.set()
    return "Stopping what I'm doing on the phone."


def risky(action: dict, by_id: dict, allow: str) -> str | None:
    text = ""
    if action.get("do") in ("tap", "long_press"):
        el = by_id.get(int(action.get("id") or 0), {})
        text = f"{el.get('text', '')} {el.get('res', '').replace('_', ' ')}"
    m = RISKY.search(text)
    if not m:
        return None
    word = m.group(1).lower()
    return None if allow and word in allow.lower() else word


def run_task(goal: str, allow: str = "", max_steps: int = 25, p: Phone | None = None, llm=None, pause: float = 1.2) -> str:
    p, llm = p or phone(), llm or context.llm
    if not _busy.acquire(blocking=False):
        return "I'm already busy on the phone — say 'stop the phone task' first."
    _stop.clear()
    owner = context.cfg.assistant.owner if context.cfg else "the user"
    history: list[str] = []
    if re.search(r"\bsend\b", goal, re.I) and "send" not in (allow or "").lower():
        allow = (allow + " send").strip()             # "send Karen a message" is itself the permission to send it
    try:
        err = p.connect()
        if err:
            return err
        st = p.status()
        if not st["screen_on"] or st["locked"]:
            p.wake()
            time.sleep(pause)
            if p.status()["locked"]:
                return "Your phone is locked — unlock it and ask me again. I never enter your PIN."
        for step in range(1, max_steps + 1):
            if _stop.is_set():
                return f"Stopped. Done so far: {'; '.join(history[-5:]) or 'nothing yet'}."
            try:
                els = p.screen()
            except Exception as e:
                els = []
                history.append(f"{step}. (couldn't read the screen: {e})")
            by_id = {e["id"]: e for e in els}
            listing = "\n".join(f"{e['id']}. {e['type']} '{e['text']}'" + (" [tap]" if e["tap"] else "")
                                + (" [edit]" if e["edit"] else "") + (" [focused]" if e.get("focused") else "")
                                + (" [on]" if e["checked"] else "")
                                for e in els[:140] if e["text"].strip().lower() not in BUBBLE) or "(nothing readable)"
            raw = llm.complete(AGENT_PROMPT.format(
                owner=owner, goal=goal, allowed=f"{owner} has approved: {allow}." if allow else "",
                app=p.status()["app"] if step == 1 else history[-1] if history else "", elements=listing,
                history="\n".join(history[-12:]) or "(none)"), prefer_smart=True, temperature=0.1)
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
                return f"Done on your phone: {act.get('summary', goal)}"
            if do == "ask":
                return f"I need you: {act.get('question', '')}"
            word = risky(act, by_id, allow)
            if word:
                target = by_id.get(int(act.get("id") or 0), {}).get("text", "")
                return (f"I stopped before tapping '{target}' on your phone — that would {word} something. Everything "
                        f"is ready. Say 'go ahead and {word}' and I'll finish it.")
            try:
                if do == "open_app":
                    pkg = p.open_app(str(act.get("name", "")))
                    note = f"Opened {act.get('name')}" if pkg else f"Couldn't find an app called {act.get('name')}"
                    time.sleep(pause)
                elif do in ("tap", "long_press"):
                    el = by_id[int(act["id"])]
                    if el["edit"] and (do == "long_press" or el.get("focused")):
                        note = (f"The text box '{el['text'] or el['res']}' is ready — use "
                                f'{{"do": "type", "text": "…", "id": {el["id"]}}} to write in it')
                        if not el.get("focused"):
                            p.tap(el["x"], el["y"])       # a long press only brings up Paste: a tap is what's needed
                    else:
                        (p.tap if do == "tap" else p.long_press)(el["x"], el["y"])
                        note = f"Tapped '{el['text'] or el['res']}'"
                elif do == "type":
                    text = str(act.get("text", ""))
                    box = by_id.get(int(act["id"])) if str(act.get("id") or "").isdigit() else None
                    box = box if box and box["edit"] else next((e for e in els if e["edit"] and e.get("focused")), None) \
                        or next((e for e in els if e["edit"]), None)
                    if box and not box.get("focused"):   # tapping a box that already has the cursor brings up "Paste"
                        p.tap(box["x"], box["y"])
                        time.sleep(0.6)
                    p.type(text)
                    time.sleep(0.4)
                    landed = None
                    try:
                        landed = typed_ok(p.screen(), text)
                        if not landed and box:           # the keyboard wasn't ready: once more
                            p.key("escape")
                            p.tap(box["x"], box["y"])
                            time.sleep(0.8)
                            p.type(text)
                            time.sleep(0.4)
                            landed = typed_ok(p.screen(), text)
                    except Exception:
                        pass
                    note = f"Typed '{text[:40]}'" + (" — it is in the text box now; next: tap Send" if landed else
                                                    " — but it did NOT appear in a text box" if landed is False else "")
                elif do == "key":
                    note = f"Pressed {act.get('key')}" if p.key(str(act.get("key", ""))) else f"No key {act.get('key')}"
                elif do == "swipe":
                    p.swipe(str(act.get("direction", "up")))
                    note = f"Swiped {act.get('direction', 'up')}"
                elif do == "open_url":
                    p.open_url(str(act.get("url", "")))
                    note = f"Opened {act.get('url')}"
                elif do == "wait":
                    time.sleep(min(10, float(act.get("seconds", 2))))
                    note = "Waited"
                else:
                    note = f"(unknown action {do})"
            except Exception as e:
                note = f"{do} failed: {e}"
            history.append(f"{step}. {note}")
            if context.store:
                context.store.log("phone", "phone", f"📱 {note[:90]}", turn=0)
            time.sleep(pause)
        return f"I didn't finish in {max_steps} steps. Done so far: {'; '.join(history[-6:])}."
    finally:
        _busy.release()
