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
