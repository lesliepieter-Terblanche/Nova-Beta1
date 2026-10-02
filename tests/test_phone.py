"""Phone hands (Android over adb + Tailscale) — the phone is faked: no adb, no device."""
import json

import pytest

from nova import context
from nova import phone as ph
from nova.tools import REGISTRY, select_tools

UI = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation="0">
<node class="android.widget.FrameLayout" text="" content-desc="" clickable="false" bounds="[0,0][1080,2340]">
<node class="android.widget.TextView" text="Sam Dlamini" content-desc="" clickable="true" bounds="[0,200][1080,360]" resource-id="com.whatsapp:id/contact_row"/>
<node class="android.widget.EditText" text="" content-desc="Message" clickable="true" bounds="[100,2100][900,2220]" resource-id="com.whatsapp:id/entry"/>
<node class="android.widget.ImageButton" text="" content-desc="Send" clickable="true" bounds="[940,2100][1060,2220]" resource-id="com.whatsapp:id/send"/>
<node class="android.view.View" text="" content-desc="" clickable="false" bounds="[0,0][0,0]"/>
</node></hierarchy>UI hierchary dumped to: /dev/tty"""
NOTIF = """  NotificationRecord(0x0a: pkg=com.whatsapp user=UserHandle{0} id=1 tag=null importance=4)
      extras={
          android.title=String (Sam Dlamini)
          android.text=String (Can you send the Mist quote today?)
      }
  NotificationRecord(0x0b: pkg=com.google.android.gm user=UserHandle{0} id=2)
          android.title=String (Northwind PO)
          android.bigText=SpannableString (PO attached for the EX4400s)
"""


class FakeAdb:
    def __init__(self, devices=None, locked=False):
        self.devs = devices if devices is not None else {"R5CX123": "device"}
        self.calls, self.shell, self.locked = [], [], locked

    def __call__(self, args, timeout=25, binary=False):
        self.calls.append(args)
        if args == ["devices"]:
            return 0, "List of devices attached\n" + "\n".join(f"{s}\t{st}" for s, st in self.devs.items())
        if args[0] == "connect":
            self.devs[args[1]] = "device"
            return 0, f"connected to {args[1]}"
        if args[0] == "mdns":
            return 0, "List of discovered mdns services\n"
        if "tcpip" in args:
            return 0, "restarting in TCP mode port: 5555"
        if "exec-out" in args and "screencap" in args:
            return 0, b"\x89PNG\r\n\x1a\nfakeshot"
        if "exec-out" in args and "uiautomator" in args:
            return 0, UI
        if args[0] == "-s" and args[2] == "shell":
            cmd = args[3]
            self.shell.append(cmd)
            if "ro.product.model" in cmd:
                return 0, "SM-S926B"
            if "version.release" in cmd:
                return 0, "15"
            if cmd.startswith("ip -4 -o addr"):
                return 0, "38: tun0    inet 100.101.102.103/32 scope global tun0"
            if "dumpsys battery" in cmd:
                return 0, "  AC powered: false\n  USB powered: true\n  level: 64\n"
            if "dumpsys window" in cmd:
                return 0, f"mDreamingLockscreen={'true' if self.locked else 'false'}\n mCurrentFocus=Window{{abc u0 com.whatsapp/com.whatsapp.Main}}"
            if "dumpsys power" in cmd:
                return 0, "mWakefulness=Awake"
            if "wm size" in cmd:
                return 0, "Physical size: 1080x2340"
            if "pm list packages" in cmd:
                return 0, "package:com.whatsapp\npackage:com.android.settings\npackage:com.sec.android.app.camera"
            if "dumpsys notification" in cmd:
                return 0, NOTIF
            if cmd.startswith("ls -t"):
                return 0, "20261002_051500.jpg\n20261001_180000.mp4"
            return 0, ""
        if "pull" in args:
            from pathlib import Path
            Path(args[-1]).write_bytes(b"jpeg")
            return 0, "1 file pulled"
        if "push" in args:
            return 0, "1 file pushed"
        return 0, ""


@pytest.fixture()
def fone(nova, monkeypatch):
    adb = FakeAdb()
    p = ph.Phone(run=adb)
    monkeypatch.setattr(ph, "_phone", p)
    monkeypatch.setattr(ph.time, "sleep", lambda s: None)
    saved = {}
    from nova import settings
    monkeypatch.setattr(settings, "apply", lambda payload: saved.update(payload["values"]) or {"saved": []})
    nova[0]["phone"] = {}
    return p, adb, saved


def test_setup_over_the_cable_then_tailscale(fone, nova):
    p, adb, saved = fone
    out = REGISTRY["phone_setup"].run({})
    assert "SM-S926B is connected at 100.101.102.103:5555" in out and "unplug" in out and "from anywhere" in out
    assert ["-s", "R5CX123", "tcpip", "5555"] in adb.calls and ["connect", "100.101.102.103:5555"] in adb.calls
    assert saved == {"phone.address": "100.101.102.103", "phone.name": "SM-S926B"}
    assert p.serial == "100.101.102.103:5555" and nova[0]["phone"]["address"] == "100.101.102.103"
    adb.devs.pop("R5CX123")                                    # cable out: still reachable over Tailscale
    assert REGISTRY["phone_status"].run({}).startswith("SM-S926B (Android 15) is connected, battery 64% and charging")


def test_helpful_messages_when_the_phone_is_not_ready(fone):
    p, adb, _ = fone
    adb.devs.clear()
    assert "Plug it into the PC" in REGISTRY["phone_setup"].run({})
    assert "isn't set up yet" in REGISTRY["phone_status"].run({})
    adb.devs["R5CX123"] = "unauthorized"
    assert "Always allow from this computer" in REGISTRY["phone_setup"].run({})


def test_read_screen_tap_type_open_app(fone):
    p, adb, _ = fone
    els = p.screen() if not p.connect() else []
    assert [(e["text"], e["tap"], e["edit"]) for e in els] == [
        ("Sam Dlamini", True, False), ("Message", True, True), ("Send", True, False)]
    assert (els[2]["x"], els[2]["y"]) == (1000, 2160)
    assert "3. ImageButton 'Send' [tap]" in REGISTRY["phone_read_screen"].run({})
    assert REGISTRY["phone_open_app"].run({"name": "WhatsApp"}) == "Opened WhatsApp on your phone."
    assert any("monkey -p com.whatsapp" in c for c in adb.shell)
    assert "couldn't find" in REGISTRY["phone_open_app"].run({"name": "Fortnite"})
    p.type("I'm late; 5 min & sorry")
    assert adb.shell[-1] == 'input text "I\\\'m%slate\\;%s5%smin%s\\&%ssorry"'
    assert REGISTRY["phone_press"].run({"key": "home"}) == "Pressed home." and adb.shell[-1] == "input keyevent 3"
    p.swipe("up")
    assert adb.shell[-1] == "input swipe 540 1684 540 655 260"


def test_screenshot_notifications_and_files(fone, nova):
    p, adb, _ = fone
    out = REGISTRY["phone_screenshot"].run({})
    assert "saved on the PC" in out and context.attachments()
    n = REGISTRY["phone_notifications"].run({})
    assert "- Whatsapp: Sam Dlamini — Can you send the Mist quote today?" in n and "Gmail: Northwind PO — PO attached" in n
    assert "Copied 2 file(s)" in REGISTRY["phone_get_photos"].run({"count": 2})
    f = nova[1] / "files" / "quote.pdf"
    f.write_bytes(b"%PDF")
    assert REGISTRY["phone_put_file"].run({"path": str(f)}) == "It's on your phone at /sdcard/Download/quote.pdf."


def test_do_on_phone_stops_before_sending(fone):
    p, adb, _ = fone
    steps = iter([{"do": "open_app", "name": "whatsapp"}, {"do": "tap", "id": 1},
                  {"do": "type", "text": "Running 10 min late", "id": 2}, {"do": "tap", "id": 3}])
    context.llm.complete = lambda *a, **k: json.dumps(next(steps))
    out = REGISTRY["do_on_phone"].run({"task": "WhatsApp Sam that I'm running 10 minutes late"})
    assert "stopped before tapping 'Send'" in out and "go ahead and send" in out
    assert "input tap 540 280" in adb.shell and any(c.startswith('input text "Running') for c in adb.shell)
    assert "input tap 1000 2160" not in adb.shell                       # the Send button was NOT pressed
    steps = iter([{"do": "tap", "id": 3}, {"do": "done", "summary": "Message sent to Sam."}])
    out = REGISTRY["do_on_phone"].run({"task": "send it", "allowed": "send"})
    assert out == "Done on your phone: Message sent to Sam." and "input tap 1000 2160" in adb.shell


def test_locked_phone_is_left_alone(nova, monkeypatch):
    adb = FakeAdb(locked=True)
    monkeypatch.setattr(ph, "_phone", ph.Phone(run=adb))
    monkeypatch.setattr(ph.time, "sleep", lambda s: None)
    out = REGISTRY["do_on_phone"].run({"task": "open the gallery"})
    assert "locked" in out and "never enter your PIN" in out


def test_calling_asks_first_and_phone_tools_are_offered(fone, nova):
    from nova.agent import needs_yes
    assert needs_yes(REGISTRY["phone_call"], {"number": "+27 82 555 0100"})
    assert REGISTRY["phone_call"].run({"number": "+27 82 555 0100"}) == "Calling +27825550100 from your phone."
    names = {t.name for t in select_tools("what notifications are on my phone?")}
    assert {"phone_notifications", "do_on_phone", "phone_status"} <= names


def test_set_up_my_phone_goes_straight_to_the_tool(nova, monkeypatch):
    """The model must never improvise this (it once made up a PowerShell command): no model call at all."""
    from nova import context
    from nova.agent import DIRECT, Agent
    from nova.skills import phone as skill
    calls = []
    monkeypatch.setattr(skill.ph, "phone", lambda: type("P", (), {"setup": lambda self: calls.append(1) or "Your Galaxy S24+ is connected."})())
    agent = Agent(nova[0], context.llm)
    context.llm.queue = []                                    # any model call would fail on the empty queue
    for said in ("set up my phone", "Set up my phone.", "Nova, connect my phone", "please setup my Galaxy", "reconnect my S24 plus again"):
        assert agent.handle(said, "voice").text == "Your Galaxy S24+ is connected.", said
    assert len(calls) == 5
    for other in ("call my phone provider about the bill", "set up my phone plan comparison", "what's on my phone screen"):
        assert not any(p.match(other) for p, _ in DIRECT), other
    assert "Never give" in agent._system_prompt("voice", "hi")


def test_send_the_screenshot_to_telegram(nova, monkeypatch, tmp_path):
    """Asked by voice or on the dashboard, the file must really go to Telegram (it used to go nowhere)."""
    from nova import context
    from nova.tools import REGISTRY
    shot = tmp_path / "files" / "phone_20261002_162700.png"
    shot.write_bytes(b"PNG")
    context.record("image", "Phone screenshot", shot, "phone")
    sent = []
    monkeypatch.setattr(context, "notify", lambda text, files: sent.append((text, files)))
    monkeypatch.setattr(context, "telegram_on", True)
    context.begin_turn("dashboard")
    out = REGISTRY["send_to_phone"].run({})                       # "send it to my telegram": the file just made
    assert out == f"Sent {shot.name} to your Telegram." and sent == [(f"📎 {shot.name}", [str(shot)])]
    assert context.attachments() == []
    # asked on Telegram itself: it comes back with the answer, once
    context.begin_turn("telegram")
    assert REGISTRY["send_to_phone"].run({"path": str(shot)}) == f"Sending {shot.name}."
    assert context.attachments() == [str(shot)] and len(sent) == 1
    # Telegram not connected: she says so instead of pretending
    monkeypatch.setattr(context, "telegram_on", False)
    context.begin_turn("voice")
    assert "Telegram isn't connected" in REGISTRY["send_to_phone"].run({"path": str(shot)}) and len(sent) == 1
    from nova.tools import select_tools
    assert "send_to_phone" in {t.name for t in select_tools("send the screenshot to my telegram")}


def test_screenshot_shows_on_the_dashboard(nova, monkeypatch):
    from nova import cards, context
    from nova.skills import phone as skill
    from nova.tools import REGISTRY

    class P:
        def connect(self): return ""
        def wake(self): pass
        def screenshot(self, out):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\x89PNG fake")
            return out
    monkeypatch.setattr(skill.ph, "phone", lambda: P())
    monkeypatch.setattr(skill.ph, "out_dir", lambda: nova[1] / "files")
    before = cards.latest_id()
    context.begin_turn("dashboard")
    out = REGISTRY["phone_screenshot"].run({})
    card = [c for c in cards.recent(before) if c["kind"] == "image"][-1]
    assert card["data"]["id"].startswith("artifact:") and card["data"]["path"] in out and "saved on the PC" in out


class ChatPhone:
    """A WhatsApp chat: a message box, a Send button, and the keyboard's Paste bubble after a long press."""
    def __init__(self):
        self.box, self.focused, self.bubble, self.sent, self.log = "", False, False, [], []

    def connect(self): return ""
    def status(self): return {"screen_on": True, "locked": False, "app": "com.whatsapp"}
    def wake(self): pass

    def screen(self):
        els = [{"id": 1, "text": "Karen", "type": "TextView", "tap": False, "edit": False, "checked": False, "focused": False, "x": 500, "y": 150, "res": "conversation_contact_name"},
               {"id": 2, "text": self.box or "Message", "type": "EditText", "tap": True, "edit": True, "checked": False, "focused": self.focused, "x": 480, "y": 2200, "res": "entry"},
               {"id": 3, "text": "Send", "type": "ImageButton", "tap": True, "edit": False, "checked": False, "focused": False, "x": 1000, "y": 2200, "res": "send"}]
        if self.bubble:
            els.append({"id": 4, "text": "Paste", "type": "TextView", "tap": True, "edit": False, "checked": False, "focused": False, "x": 300, "y": 2080, "res": ""})
        return els

    def tap(self, x, y):
        self.log.append(("tap", x, y))
        if (x, y) == (480, 2200):
            self.bubble, self.focused = self.focused, True          # tapping a box that has the cursor → Paste bubble
        elif (x, y) == (1000, 2200):
            self.sent.append(self.box)
            self.box = ""

    def long_press(self, x, y):
        self.log.append(("long", x, y))
        self.bubble = True

    def type(self, text):
        self.log.append(("type", text))
        if self.focused:
            self.box += text
            self.bubble = False

    def key(self, name):
        self.bubble = False
        return True


def test_writes_and_sends_a_whatsapp_message(nova, monkeypatch):
    import json as _json
    from nova import phone as ph
    monkeypatch.setattr(ph.time, "sleep", lambda s: None)
    p, seen = ChatPhone(), []

    class Script:
        steps = [{"do": "long_press", "id": 2},                  # the model's old mistake: brings up Paste
                 {"do": "type", "text": "test", "id": 2},
                 {"do": "tap", "id": 3},
                 {"do": "done", "summary": "Sent 'test' to Karen."}]

        def complete(self, prompt, **kw):
            seen.append(prompt)
            return _json.dumps(self.steps[len(seen) - 1])

    out = ph.run_task('On my phone, open WhatsApp, find the chat with Karen, type "test" and send it.', p=p, llm=Script(), pause=0)
    assert out == "Done on your phone: Sent 'test' to Karen." and p.sent == ["test"]      # written by Nova and sent
    assert ("long", 480, 2200) not in p.log and p.log.count(("tap", 480, 2200)) == 1          # no Paste bubble raised
    assert "is ready — use" in seen[1] and "it is in the text box now; next: tap Send" in seen[2]
    assert "Paste" not in seen[1].split("Steps so far")[0].split("On screen")[1]              # never offered to the model
    # a task that doesn't say "send" still stops before the Send button
    p2, seen2 = ChatPhone(), []

    class Script2(Script):
        steps = [{"do": "type", "text": "running late", "id": 2}, {"do": "tap", "id": 3}]

        def complete(self, prompt, **kw):
            seen2.append(prompt)
            return _json.dumps(self.steps[len(seen2) - 1])
    out = ph.run_task("WhatsApp Karen that I'm running late", p=p2, llm=Script2(), pause=0)
    assert "I stopped before tapping 'Send'" in out and p2.sent == [] and p2.box == "running late"


# ── v2.28: health, switches, ring, messages, alerts, routines, reconnect ──
class RichAdb(FakeAdb):
    """The fake phone with storage, memory, Wi-Fi, volume and a clock that takes timers."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.notif = NOTIF
        self.timer_ok = True

    def __call__(self, args, timeout=25, binary=False):
        if args[0] == "disconnect":
            self.calls.append(args)
            self.devs.pop(args[1], None)
            return 0, f"disconnected {args[1]}"
        if args[0] == "-s" and args[2] == "shell":
            cmd = args[3]
            if "dumpsys battery" in cmd:
                self.shell.append(cmd)
                return 0, "  AC powered: false\n  USB powered: false\n  level: 64\n  temperature: 287\n"
            if cmd.startswith("df -k"):
                return 0, "Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/block/dm-50 239468544 121634816 117440512 51% /data"
            if "meminfo" in cmd:
                return 0, "MemTotal:       11534336 kB\nMemFree: 300000 kB\nMemAvailable:    4613734 kB"
            if cmd == "cmd wifi status":
                return 0, 'Wifi is enabled\nWifi is connected to "HomeFibre"\nWifiInfo: SSID: "HomeFibre", BSSID: aa:bb, RSSI: -52, Link speed'
            if "volume --stream" in cmd and "--get" in cmd:
                self.shell.append(cmd)
                return 0, "volume is 5 in range [0..15]"
            if "dumpsys notification" in cmd:
                return 0, self.notif
            if "SET_TIMER" in cmd and not self.timer_ok:
                self.shell.append(cmd)
                return 0, "Error: Activity not started, unable to resolve Intent"
            if cmd.startswith("for d in"):
                return 0, "/system/media/audio/ringtones/Atomic_Bell.ogg\n/system/media/audio/ringtones/Over_the_Horizon.ogg"
            if cmd.startswith("svc nfc"):
                self.shell.append(cmd)
                return 0, "svc: unknown command nfc\nusage: svc [wifi|data]"
        return super().__call__(args, timeout, binary)


@pytest.fixture()
def rich(nova, monkeypatch, tmp_path):
    adb = RichAdb()
    p = ph.Phone(run=adb)
    monkeypatch.setattr(ph, "_phone", p)
    monkeypatch.setattr(ph.time, "sleep", lambda s: None)
    from nova import phone_routines, phone_watch
    monkeypatch.setattr(phone_routines, "_path", lambda: tmp_path / "phone_routines.json")
    monkeypatch.setattr(phone_watch, "_seen", None)
    monkeypatch.setattr(phone_watch, "_health_at", 0.0)
    phone_watch.state.update(online=None, health=None, checked=0.0, fails=0, told_lost=False, low=False)
    phone_watch.last_message.clear()
    said = []
    monkeypatch.setattr(context, "announce", said.append)
    nova[0]["phone"] = {}
    return p, adb, said


def test_health_reads_battery_storage_memory_and_wifi(rich):
    p, adb, _ = rich
    out = REGISTRY["phone_health"].run({})
    assert out == ("SM-S926B: battery 64%, 28.7°C, 112.0 GB free of 228.4 GB storage (51% used), 40% memory free, "
                   "on Wi-Fi 'HomeFibre'.")
    from nova import phone_watch
    snap = phone_watch.snapshot()
    assert snap["battery"] == 64 and snap["wifi"] == "HomeFibre" and snap["online"] and "Driving mode" in snap["routines"]


def test_quick_switches_and_levels(rich):
    p, adb, _ = rich
    assert REGISTRY["phone_switch"].run({"setting": "Wi-Fi", "on": False}) == "Wifi is off on your phone."
    assert adb.shell[-1] == "svc wifi disable"
    assert REGISTRY["phone_switch"].run({"setting": "DND", "on": True}) == "Do not disturb is on on your phone."
    assert adb.shell[-1] == "cmd notification set_dnd priority"
    assert "cuts me off" in REGISTRY["phone_switch"].run({"setting": "flight mode", "on": True})
    assert REGISTRY["phone_set_level"].run({"what": "brightness", "percent": 40}) == "Brightness is at 40% on your phone."
    assert adb.shell[-1] == "settings put system screen_brightness 102"
    assert REGISTRY["phone_set_level"].run({"what": "media volume", "percent": 60}).startswith("Media volume is at 60%")
    assert adb.shell[-1] == "cmd media_session volume --stream 3 --set 9"         # 60% of the phone's 0..15
    assert ph.quick("turn on bluetooth", p) == "Bluetooth is on." and adb.shell[-1] == "svc bluetooth enable"
    assert ph.quick("volume 100", p) == "Volume set to 100%." and ph.quick("open whatsapp", p) == "Opened whatsapp."
    assert ph.quick("book me an uber", p) is None


def test_a_switch_without_a_direct_command_uses_the_quick_settings_panel(rich, monkeypatch):
    p, adb, _ = rich
    asked = []
    monkeypatch.setattr(ph, "run_task", lambda goal, allow="", **k: asked.append(goal) or "Done on your phone: hotspot on")
    assert "hotspot on" in REGISTRY["phone_switch"].run({"setting": "hotspot", "on": True})
    assert "quick settings" in asked[0] and "'hotspot'" in asked[0]
    REGISTRY["phone_switch"].run({"setting": "nfc", "on": True})                  # the phone refused the direct way
    assert len(asked) == 2 and "'nfc'" in asked[1]


def test_find_my_phone_rings_through_silent(rich):
    p, adb, _ = rich
    assert "ringing at full volume" in REGISTRY["phone_ring"].run({})
    assert "cmd media_session volume --stream 4 --set 15" in adb.shell and any("SET_TIMER" in c for c in adb.shell)
    adb.timer_ok = False                                                          # the clock refuses: play a ringtone
    assert "playing a ringtone" in REGISTRY["phone_ring"].run({})
    assert "Over_the_Horizon.ogg" in adb.shell[-1] and "cmd media_session volume --stream 3 --set 15" in adb.shell


def test_ring_my_phone_skips_the_model(rich, nova):
    from nova.agent import Agent
    out = Agent(nova[0], context.llm).handle("Nova, find my phone").text             # FakeLLM queue is empty
    assert "ringing" in out


def test_reading_messages_and_replying_in_the_same_app(rich, monkeypatch):
    p, adb, _ = rich
    adb.notif = NOTIF + """  NotificationRecord(0x0c: pkg=com.whatsapp user=UserHandle{0} id=3)
          android.title=String (WhatsApp)
          android.text=String (2 new messages)
  NotificationRecord(0x0d: pkg=org.telegram.messenger user=UserHandle{0} id=4)
          android.title=String (Karen Smith)
          android.text=String (Dinner at 7?)
  NotificationRecord(0x0e: pkg=com.spotify.music user=UserHandle{0} id=5)
          android.title=String (Now playing)
          android.text=String (Song)
"""
    out = REGISTRY["phone_messages"].run({})
    assert out.splitlines() == ["- WhatsApp — Sam Dlamini: Can you send the Mist quote today?",
                                "- Gmail — Northwind PO: PO attached for the EX4400s",
                                "- Telegram — Karen Smith: Dinner at 7?"]
    assert REGISTRY["phone_messages"].run({"sender": "karen"}) == "- Telegram — Karen Smith: Dinner at 7?"
    tasks = []
    monkeypatch.setattr(ph, "run_task", lambda goal, allow="", **k: tasks.append((goal, allow)) or "Done on your phone: sent")
    REGISTRY["phone_send_message"].run({"to": "", "text": "Yes, see you then"})   # "reply" = whoever was read last
    assert tasks[-1][1] == "send" and "Open Telegram, open the chat with Karen Smith" in tasks[-1][0]
    assert '"Yes, see you then" — then tap Send.' in tasks[-1][0]
    REGISTRY["phone_send_message"].run({"to": "Sam", "text": "Draft only", "app": "sms", "send": False})
    assert tasks[-1][1] == "" and "Open Messages" in tasks[-1][0] and "WITHOUT sending" in tasks[-1][0]
    REGISTRY["phone_send_message"].run({"to": "Sam", "text": "Quote attached", "app": "outlook"})
    assert "Open Outlook, start a new email to Sam" in tasks[-1][0]
    REGISTRY["phone_messages"].run({"sender": "Thabo"})                           # nothing waiting: read the chat itself
    assert "open the chat with Thabo" in tasks[-1][0] and "Do not type or send" in tasks[-1][0]


def test_whatsapp_name_message_is_one_send_not_improvised(rich, nova, monkeypatch):
    from nova.agent import Agent, message_request
    assert message_request("WhatsApp Karen: test") == {"to": "Karen", "text": "test", "app": "whatsapp", "send": True}
    assert message_request('Send Karen a WhatsApp message saying "I love you"') == {
        "to": "Karen", "text": "I love you", "send": True, "app": "whatsapp"}
    assert message_request("draft Sam a telegram saying hello")["send"] is False
    assert message_request("whatsapp is not working") is None and message_request("what is whatsapp") is None
    tasks = []
    monkeypatch.setattr(ph, "run_task", lambda goal, allow="", **k: tasks.append((goal, allow)) or "Done on your phone: sent")
    monkeypatch.setattr(context.llm, "complete", lambda prompt, **k: '{"to": "karen smith", "text": "i love you"}')
    out = Agent(nova[0], context.llm).handle("whatsapp karen smith i love you").text   # no model round at all
    assert out == "Done on your phone: sent" and tasks == [
        ('Open WhatsApp, open the chat with karen smith (use the search if it is not on the first screen; pick the '
         'person, not a group), type exactly this message: "i love you" — then tap Send.', "send")]


def test_alerts_only_for_messages_that_are_new(rich, nova):
    p, adb, said = rich
    from nova import phone_watch
    nova[0]["phone"] = {"address": "100.101.102.103"}
    adb.devs["100.101.102.103:5555"] = "device"
    phone_watch.tick(p)                                       # first look: takes stock, says nothing
    assert said == [] and phone_watch.state["online"] and phone_watch.state["health"]["battery"] == 64
    adb.notif = NOTIF + """  NotificationRecord(0x0d: pkg=com.whatsapp user=UserHandle{0} id=4)
          android.title=String (Karen Smith)
          android.text=String (Are you on your way?)
"""
    phone_watch.tick(p)
    assert said == ["📱 WhatsApp from Karen Smith: Are you on your way?"]
    assert phone_watch.last_message["from"] == "Karen Smith"
    notices = [e for e in context.store.activity(0) if e["kind"] == "notice"]
    assert notices and "Karen Smith" in notices[-1]["title"]
    phone_watch.tick(p)                                       # same notifications: nothing more
    assert len(said) == 1
    nova[0]["phone"]["alerts"] = False
    adb.notif += "  NotificationRecord(0x0f: pkg=com.whatsapp)\n   android.title=String (Sam)\n   android.text=String (Hi)\n"
    phone_watch.tick(p)
    assert len(said) == 1


def test_reconnects_by_herself_and_says_when_she_cannot(rich, nova):
    p, adb, said = rich
    from nova import phone_watch
    nova[0]["phone"] = {"address": "100.101.102.103"}
    adb.devs.clear()
    adb.devs["100.101.102.103:5555"] = "offline"              # a stale link: drop it and connect afresh
    phone_watch.tick(p)
    assert ["disconnect", "100.101.102.103:5555"] in adb.calls and phone_watch.state["online"]
    real = adb.__call__
    p._run = lambda args, timeout=25, binary=False: (0, "failed to connect") if args[0] == "connect" else real(args, timeout, binary)
    adb.devs.clear()
    for _ in range(4):
        phone_watch.tick(p)
    lost = [e["title"] for e in context.store.activity(0) if e["kind"] == "notice" and "can't reach your phone" in e["title"]]
    assert len(lost) == 1 and not phone_watch.state["online"]  # said once, not every round
    p._run = adb
    phone_watch.tick(p)
    assert phone_watch.state["online"] and any("connected again" in e["title"] for e in context.store.activity(0))
    assert REGISTRY["phone_reconnect"].run({}).startswith("Connected to your phone again")


def test_low_battery_is_mentioned_once(rich, nova, monkeypatch):
    p, adb, said = rich
    from nova import phone_watch
    nova[0]["phone"] = {"address": "100.101.102.103", "alerts": False}
    adb.devs["100.101.102.103:5555"] = "device"
    monkeypatch.setattr(p, "health", lambda: {"battery": 12, "charging": False})
    phone_watch.tick(p)
    monkeypatch.setattr(phone_watch, "_health_at", 0.0)
    phone_watch.tick(p)
    assert said == ["Your phone's battery is at 12 percent — time to charge it."]


def test_saved_routines_run_from_one_phrase(rich, nova, monkeypatch):
    p, adb, _ = rich
    from nova import phone_routines
    from nova.agent import Agent
    assert phone_routines.find("driving mode") == "Driving mode" and phone_routines.find("Start my bedtime routine") == "Bedtime"
    assert phone_routines.find("what is driving mode") == "" and phone_routines.find("turn on bluetooth") == ""
    out = REGISTRY["phone_save_routine"].run({"name": "gym time", "steps": "dnd on; bluetooth on, media volume 80 and open spotify; order a protein shake"})
    assert "with 5 steps" in out and "Say 'gym time' to run it" in out
    slow = []
    monkeypatch.setattr(ph, "run_task", lambda goal, allow="", **k: slow.append(goal) or "I stopped before tapping 'Order' on your phone")
    out = Agent(nova[0], context.llm).handle("gym time").text                      # no model: straight to the routine
    assert out.startswith("Gym time: did 3 of 5 steps — dnd on, bluetooth on, media volume 80.")
    assert "Not done: open spotify" in out and "order a protein shake (I stopped before" in out
    assert slow == ["open spotify", "order a protein shake"]                     # Spotify isn't installed on the fake phone
    assert "svc bluetooth enable" in adb.shell and "cmd media_session volume --stream 3 --set 12" in adb.shell
    assert "- Gym time: dnd on; bluetooth on" in REGISTRY["phone_routines_list"].run({})
    assert REGISTRY["phone_delete_routine"].run({"name": "Gym time"}).startswith("Removed")
    assert phone_routines.find("gym time") == ""


def test_where_is_my_phone(rich, nova, monkeypatch):
    p, adb, _ = rich
    from nova.agent import Agent
    dump = ("  last location=Location[network -26.200000,28.040000 hAcc=900 et=+2d]\n"
            "  last location=Location[fused -26,107600,28,056800 hAcc=14,5 et=+3d2h alt=1560.0]\n"
            "  last location=Location[gps 0.000000,0.000000 hAcc=1]\n")
    monkeypatch.setattr(p, "sh", lambda cmd, timeout=25: dump if cmd == "dumpsys location" else "")
    out = Agent(nova[0], context.llm).handle("where is my phone?").text              # no model call
    assert "-26.10760, 28.05680, accurate to about 14 metres" in out
    assert "https://www.google.com/maps?q=-26.107600,28.056800" in out
    monkeypatch.setattr(p, "sh", lambda cmd, timeout=25: "")
    assert "Location may be off" in REGISTRY["phone_locate"].run({})
