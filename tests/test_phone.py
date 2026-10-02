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
    context.begin_turn("voice")                                         # "go ahead" is a new request
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
    assert out == 'Done on your phone: sent "test".' and p.sent == ["test"] and len(seen) == 3   # ends AT Send
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
    context.begin_turn("voice")
    REGISTRY["phone_send_message"].run({"to": "Sam", "text": "Draft only", "app": "sms", "send": False})
    assert tasks[-1][1] == "" and "Open Messages" in tasks[-1][0] and "WITHOUT sending" in tasks[-1][0]
    context.begin_turn("voice")
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


def test_a_message_is_sent_once_never_two_or_three_times(fone, monkeypatch):
    """After Send the box is empty again; the model used to go round once more and send the same message again."""
    p, adb, _ = fone
    monkeypatch.setattr(ph, "_recent", {})
    monkeypatch.setattr(ph, "typed_ok", lambda els, text: True)
    asked = []

    def model(*a, **k):                                        # a model that would happily keep typing and sending
        asked.append(1)
        return json.dumps([{"do": "open_app", "name": "whatsapp"}, {"do": "tap", "id": 1},
                           {"do": "type", "text": "On my way", "id": 2}, {"do": "type", "text": "On my way", "id": 2},
                           {"do": "tap", "id": 3}, {"do": "type", "text": "On my way", "id": 2},
                           {"do": "tap", "id": 3}][min(len(asked) - 1, 6)])
    context.llm.complete = model
    goal = ph.message_task("Sam Dlamini", "On my way")
    out = ph.run_task(goal, "send", pause=0)
    assert out == 'Done on your phone: sent "On my way".'
    assert adb.shell.count("input tap 1000 2160") == 1                              # Send pressed exactly once
    assert sum(c.startswith('input text "On%smy%sway"') for c in adb.shell) == 1    # and typed exactly once
    assert len(asked) == 5                                                          # stopped right after Send
    again = ph.run_task(goal, "send", pause=0)                                      # the same request straight after
    assert "haven't sent it again" in again and adb.shell.count("input tap 1000 2160") == 1
    monkeypatch.setattr(ph, "REPEAT_SECONDS", 0)                                    # a minute later it's allowed
    asked.clear()
    assert ph.run_task(goal, "send", pause=0).startswith("Done on your phone: sent")


def test_a_message_task_stays_in_its_app_and_is_not_retried_another_way(rich, monkeypatch):
    p, adb, _ = rich
    monkeypatch.setattr(ph, "_recent", {})
    real = adb.__call__
    p._run = lambda args, timeout=25, binary=False: (0, "package:com.whatsapp\npackage:com.facebook.orca\npackage:com.spotify.music") \
        if args[-1] == "pm list packages" else real(args, timeout, binary)
    steps = iter([{"do": "open_app", "name": "whatsapp"}, {"do": "open_app", "name": "messenger"},
                  {"do": "open_app", "name": "facebook messenger"}])
    context.llm.complete = lambda *a, **k: json.dumps(next(steps))
    context.begin_turn("voice")
    out = REGISTRY["phone_send_message"].run({"to": "Fritz", "text": "Hi", "app": "whatsapp"})
    assert "meant to happen in whatsapp only" in out and "Nothing was sent" in out
    assert not any("com.facebook.orca" in c for c in adb.shell)                   # Messenger was never opened
    # the model may not start a second phone task for the same request ("let me try Messenger instead")
    assert REGISTRY["phone_send_message"].run({"to": "Fritz", "text": "Hi", "app": "messenger"}).startswith("STOP:")
    assert REGISTRY["do_on_phone"].run({"task": "message Fritz on Messenger"}).startswith("STOP:")
    context.begin_turn("voice")                                                   # a new request is a clean slate
    assert not REGISTRY["do_on_phone"].run({"task": "x"}).startswith("STOP:")


def test_app_names_are_never_guessed(rich):
    p, adb, _ = rich
    real = adb.__call__
    p._run = lambda args, timeout=25, binary=False: (0, "package:com.whatsapp\npackage:com.sec.android.app.camera\npackage:com.android.settings\npackage:com.sec.android.app.popupcalculator") \
        if args[-1] == "pm list packages" else real(args, timeout, binary)
    p.connect()
    assert p.find_app("WhatsApp") == "com.whatsapp" and p.find_app("the camera app") == "com.sec.android.app.camera"
    assert p.find_app("settings") == "com.android.settings" and p.find_app("calculator") == "com.sec.android.app.popupcalculator"
    for junk in ("the messaging app", "an app", "chat with Fritz", "sec", "facebook messenger", "pop", "spotify"):
        assert p.find_app(junk) == "", junk                   # these used to open whichever app's name was shortest


def test_send_only_in_the_right_persons_chat_and_never_a_thumbs_up(nova, monkeypatch):
    """A message for Fritz once went to the wrong chat, and a 👍 was sent in Messenger: both are blocked now."""
    monkeypatch.setattr(ph.time, "sleep", lambda s: None)
    monkeypatch.setattr(ph, "_recent", {})
    p = ChatPhone()                                            # the chat on screen is with Karen

    class Script:
        def __init__(self, steps):
            self.steps, self.n = steps, 0

        def complete(self, prompt, **kw):
            self.n += 1
            return json.dumps(self.steps[min(self.n, len(self.steps)) - 1])

    out = ph.run_task(ph.message_task("Fritz Smith", "Hi"), "send", p=p, pause=0, to="Fritz Smith",
                      llm=Script([{"do": "type", "text": "Hi", "id": 2}]))
    assert "couldn't find the chat with Fritz Smith" in out and p.sent == [] and p.box == ""   # nothing written
    out = ph.run_task(ph.message_task("Fritz Smith", "Hi"), "send", p=p, pause=0, to="Fritz Smith",
                      llm=Script([{"do": "tap", "id": 3}]))
    assert "isn't with Fritz Smith" in out and p.sent == []
    out = ph.run_task(ph.message_task("Fritz Smith", "Hi"), "send", p=p, pause=0, to="Fritz Smith", max_steps=2,
                      llm=Script([{"do": "type", "text": "Fritz", "id": 2}]))                     # searching for him is fine
    assert ("type", "Fritz") in p.log and p.sent == []
    assert ph.names_on_screen(p.screen(), "karen") and not ph.names_on_screen(p.screen(), "Karen Jones")
    for label in ("Send a like", "Thumbs up", "Share", "Forward", "Video chat"):
        assert ph.risky({"do": "tap", "id": 1}, {1: {"text": label, "res": ""}}, "send"), label   # 'send' ≠ these


# ── v2.29: calls, live screen, photo, scheduled messages, self-starting routines, convenience ──
class CallAdb(RichAdb):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.call, self.photos, self.playing = 0, ["20261002_051500.jpg"], True

    def __call__(self, args, timeout=25, binary=False):
        if args[0] == "-s" and args[2] == "shell":
            cmd = args[3]
            if cmd.startswith("content query"):
                self.shell.append(cmd)
                return 0, ("Row: 0 display_name=Karen Smith, data1=+27 82 555 0100\n"
                           "Row: 1 display_name=Karen Jones, data1=082 555 0200\n"
                           "Row: 2 display_name=Fritz Smith, data1=+27825550300\n"
                           "Row: 3 display_name=Fritz Smith, data1=+27 82 555 0300")
            if "telephony.registry" in cmd:
                return 0, f"  mCallState={self.call}\n  mCallState={self.call}"
            if cmd == "input keyevent 5":
                self.call = 2
            if cmd == "input keyevent 6":
                self.call = 0
            if cmd == "input keyevent 27":
                self.photos.insert(0, "20261002_173000.jpg")
            if cmd.startswith("ls -t"):
                return 0, "\n".join(self.photos)
            if cmd.startswith("dumpsys media_session"):
                return 0, ("Sessions Stack - have 1 sessions:\n    Spotify com.spotify.music/spotify-media-session (userId=0)\n"
                           "      state=PlaybackState {state=3, position=1}\n"
                           "      metadata: size=5, description=Africa, Toto, Toto IV\n") if self.playing else "no sessions"
            if cmd.startswith("dumpsys bluetooth_manager"):
                return 0, "Bonded devices:\n  XX:XX:XX:XX:12:34 [ DUAL ] My Car Audio\n  mActiveDevice: XX:XX:XX:XX:12:34\n"
        return super().__call__(args, timeout, binary)


@pytest.fixture()
def calls(rich, monkeypatch, tmp_path):
    p, _, said = rich
    adb = CallAdb()
    p._run = adb
    from nova import phone_routines, phone_schedule, phone_watch
    monkeypatch.setattr(phone_routines, "_tpath", lambda: tmp_path / "phone_triggers.json")
    monkeypatch.setattr(phone_schedule, "_path", lambda: tmp_path / "phone_schedule.json")
    monkeypatch.setattr(phone_watch, "_prev", None)
    monkeypatch.setattr(phone_watch, "_call", "idle")
    phone_watch.missed.clear()
    monkeypatch.setattr(ph, "_recent", {})
    return p, adb, said


def test_call_a_contact_by_name(calls):
    from nova.agent import needs_yes
    p, adb, _ = calls
    assert needs_yes(REGISTRY["phone_call"], {"number": "Fritz Smith"})                 # always asks first
    assert REGISTRY["phone_call"].run({"number": "Fritz Smith"}) == "Calling Fritz Smith (+27825550300) from your phone."
    assert adb.shell[-1] == "am start -a android.intent.action.CALL -d tel:+27825550300"
    out = REGISTRY["phone_call"].run({"number": "Karen"})                              # two Karens: ask, don't guess
    assert out.startswith("Which one? Karen Smith on +27825550100; Karen Jones on 0825550200")
    assert not adb.shell[-1].endswith("0100") and "couldn't find 'Thabo'" in REGISTRY["phone_call"].run({"number": "Thabo"})


def test_incoming_call_is_announced_and_answered_by_voice(calls, nova):
    from nova import phone_watch
    from nova.agent import Agent
    p, adb, said = calls
    p.connect()
    adb.notif = "  NotificationRecord(0x1: pkg=com.samsung.android.incallui)\n   android.title=String (Karen Smith)\n   android.text=String (Incoming call)\n"
    adb.call = 1
    assert phone_watch.check_call(p) == "ringing" and phone_watch.check_call(p) == "ringing"
    assert said == ["📞 Karen Smith is calling your phone. Say 'answer the call' or 'decline the call'."]   # once
    agent = Agent(nova[0], context.llm)                                                # no model: straight to the phone
    assert agent.handle("answer the call").text == "Answered the call." and adb.call == 2
    assert agent.handle("Nova, hang up").text == "Hung up." and adb.call == 0
    adb.call = 1
    assert agent.handle("decline the call").text == "Declined the call."
    assert phone_watch.missed_summary() == "On your phone: 1 call (Karen Smith)." and phone_watch.missed_summary() == ""


def test_live_phone_screen_on_the_pc(calls, monkeypatch, tmp_path, nova):
    from nova import phone_mirror
    from nova.agent import Agent
    p, adb, _ = calls
    exe = tmp_path / "scrcpy.exe"
    exe.write_bytes(b"x")
    started = []

    class Proc:
        def __init__(self, cmd, **kw):
            started.append((cmd, kw))
            self.alive = True

        def poll(self):
            return None if self.alive else 0

        def terminate(self):
            self.alive = False
    monkeypatch.setattr(phone_mirror, "exe_path", lambda: exe)
    monkeypatch.setattr(phone_mirror.subprocess, "Popen", Proc)
    monkeypatch.setattr(phone_mirror, "start", lambda popen=Proc, _s=phone_mirror.start: _s(popen))
    monkeypatch.setattr(phone_mirror, "_proc", None)
    nova[0]["phone"] = {"name": "Galaxy S24+", "mirror_size": 1024}
    out = Agent(nova[0], context.llm).handle("show my phone screen").text
    assert "open in a window on the PC" in out
    cmd = started[0][0]
    assert cmd[:3] == [str(exe), "-s", "R5CX123"] and "--no-audio" in cmd and cmd[cmd.index("--max-size") + 1] == "1024"
    assert "Nova · Galaxy S24+" in cmd and phone_mirror.running()
    assert "already open" in REGISTRY["phone_show_screen"].run({}) and len(started) == 1
    assert Agent(nova[0], context.llm).handle("close the phone screen").text == "Closed the phone's screen on the PC."
    assert not phone_mirror.running()


def test_take_a_photo_with_the_phone(calls):
    p, adb, _ = calls
    out = REGISTRY["phone_take_photo"].run({})
    assert "Photo taken and saved on the PC" in out and "20261002_173000.jpg" in out and context.attachments()
    assert any("STILL_IMAGE_CAMERA" in c for c in adb.shell)


def test_scheduled_message_goes_out_when_due_and_waits_for_a_locked_phone(calls, monkeypatch):
    import datetime as dt

    from nova import phone_schedule
    p, adb, _ = calls
    out = REGISTRY["phone_schedule_message"].run({"to": "Sam Dlamini", "text": "Happy birthday!", "when": "2099-01-01 07:00"})
    assert out.startswith("Scheduled: WhatsApp to Sam Dlamini on Thursday 01 January at 07:00")
    assert "passed" in REGISTRY["phone_schedule_message"].run({"to": "Sam", "text": "x", "when": "2020-01-01 07:00"})
    assert REGISTRY["phone_scheduled"].run({}) == "1. 2099-01-01 07:00 — whatsapp to Sam Dlamini: Happy birthday!"
    told, results = [], iter(["Your phone is locked — unlock it and ask me again. I never enter your PIN.",
                              "Your phone is locked — unlock it and ask me again. I never enter your PIN.",
                              'Done on your phone: sent "Happy birthday!".'])
    asked = []
    monkeypatch.setattr(ph, "run_task", lambda goal, allow="", **k: asked.append((goal, allow, k.get("only"), k.get("to"))) or next(results))
    early = dt.datetime(2098, 12, 31, 23, 0)
    phone_schedule.run_due(told.append, now=early)
    assert asked == [] and told == []                                                  # not yet
    at = dt.datetime(2099, 1, 1, 7, 0)
    phone_schedule.run_due(told.append, now=at)
    phone_schedule.run_due(told.append, now=at + dt.timedelta(minutes=5))              # still locked: said only once
    assert len(told) == 1 and "can't send it yet" in told[0] and len(phone_schedule.load()) == 1
    phone_schedule.run_due(told.append, now=at + dt.timedelta(minutes=9))              # unlocked now
    assert told[-1] == 'Sent your scheduled WhatsApp message to Sam Dlamini: "Happy birthday!".'
    assert phone_schedule.load() == [] and asked[-1][1:] == ("send", "whatsapp", "Sam Dlamini")
    REGISTRY["phone_schedule_message"].run({"to": "Karen", "text": "Hi", "when": "2099-01-01 07:00", "app": "sms"})
    assert REGISTRY["phone_cancel_scheduled"].run({"which": "karen"}).startswith("Cancelled 1: Karen")
    assert phone_schedule.parse("tomorrow 7", dt.datetime(2026, 10, 2, 17, 30)) == dt.datetime(2026, 10, 3, 7, 0)
    assert phone_schedule.parse("9pm", dt.datetime(2026, 10, 2, 17, 30)) == dt.datetime(2026, 10, 2, 21, 0)


def test_a_scheduled_message_that_never_gets_through_is_reported(calls, monkeypatch):
    import datetime as dt

    from nova import phone_schedule
    phone_schedule.add("Sam", "Hello", dt.datetime(2099, 1, 1, 7, 0))
    monkeypatch.setattr(ph, "run_task", lambda goal, allow="", **k: "Your phone is locked — unlock it and ask me again.")
    told = []
    phone_schedule.run_due(told.append, now=dt.datetime(2099, 1, 1, 8, 30))            # past the hour of trying
    assert "could NOT send your scheduled message to Sam" in told[0] and phone_schedule.load() == []


def test_routines_start_by_themselves(calls, monkeypatch):
    import datetime as dt

    from nova import phone_routines, phone_watch
    p, adb, said = calls
    assert REGISTRY["phone_routine_trigger"].run({"name": "bedtime", "at": "22:00", "days": "weekdays"}) == \
        "'Bedtime' will now start by itself at 22:00 on mon, tue, wed, thu, fri."
    assert "when Bluetooth connects to 'car'" in REGISTRY["phone_routine_trigger"].run({"name": "Driving mode", "when": "bluetooth car"})
    assert "didn't understand" in REGISTRY["phone_routine_trigger"].run({"name": "Bedtime", "when": "whenever"})
    assert phone_routines.needs_bluetooth() and p.connect() == "" and p.bluetooth_connected() == ["My Car Audio"]
    fri = dt.datetime(2026, 10, 2, 22, 5)
    idle = {"charging": False, "battery": 50, "wifi": "HomeFibre", "bluetooth": []}
    assert phone_routines.due(idle, idle, fri) == ["Bedtime"] and phone_routines.due(idle, idle, fri) == []   # once a day
    assert phone_routines.due(idle, idle, dt.datetime(2026, 10, 3, 22, 5)) == []        # Saturday: not a weekday
    assert phone_routines.due(idle, {**idle, "bluetooth": ["My Car Audio"]}, fri) == ["Driving mode"]
    assert phone_routines.due({**idle, "bluetooth": ["My Car Audio"]}, {**idle, "bluetooth": ["My Car Audio"]}, fri) == []
    assert phone_routines.due(None, {**idle, "bluetooth": ["My Car Audio"]}, fri) == []  # first look: no surprise start
    REGISTRY["phone_routine_trigger"].run({"name": "Good morning", "when": "unplugged"})
    assert phone_routines.due({**idle, "charging": True}, idle, fri) == ["Good morning"]
    # end to end through the watcher: the car connects → driving mode runs
    monkeypatch.setattr(phone_watch, "_prev", {**idle, "bluetooth": []})
    phone_watch._automatic(p, {"charging": False, "battery": 50, "wifi": "HomeFibre"}, wait=True)
    assert "svc bluetooth enable" in adb.shell and "cmd notification set_dnd priority" in adb.shell
    notices = [e["title"] for e in context.store.activity(0) if e["kind"] == "notice"]
    assert any("Starting 'Driving mode'" in n for n in notices) and any(n.startswith("Driving mode:") for n in notices)
    assert REGISTRY["phone_routine_trigger"].run({"name": "Bedtime"}) == "'Bedtime' now only runs when you ask for it."


def test_charged_alert_once(calls, nova, monkeypatch):
    from nova import phone_watch
    p, adb, said = calls
    nova[0]["phone"] = {"address": "100.101.102.103", "alerts": False}
    adb.devs["100.101.102.103:5555"] = "device"
    monkeypatch.setattr(p, "health", lambda: {"battery": 82, "charging": True, "wifi": ""})
    phone_watch.tick(p)
    monkeypatch.setattr(phone_watch, "_health_at", 0.0)
    phone_watch.tick(p)
    assert said == ["Your phone has charged to 82 percent — you can unplug it."]


def test_navigate_media_and_clipboard(calls, monkeypatch):
    import sys
    import types
    p, adb, _ = calls
    assert REGISTRY["phone_navigate"].run({"destination": "OR Tambo Airport"}).startswith("Navigation to OR Tambo")
    assert adb.shell[-1] == "am start -a android.intent.action.VIEW -d 'google.navigation:q=OR+Tambo+Airport'"
    assert REGISTRY["phone_media"].run({"action": "what"}) == "Playing on your phone: Africa, Toto."
    assert REGISTRY["phone_media"].run({"action": "next"}) == "Next track." and adb.shell[-1] == "input keyevent 87"
    assert REGISTRY["phone_media"].run({"action": "pause"}) == "Paused." and adb.shell[-1] == "input keyevent 127"
    clip = types.SimpleNamespace(text="https://example.com/page", copied=[])
    clip.paste = lambda: clip.text
    clip.copy = clip.copied.append
    monkeypatch.setitem(sys.modules, "pyperclip", clip)
    assert "Opened the link" in REGISTRY["phone_send_clipboard"].run({})
    assert adb.shell[-1] == "am start -a android.intent.action.VIEW -d 'https://example.com/page'"
    clip.text = "Gate code is 4471"
    assert "Typed your PC clipboard" in REGISTRY["phone_send_clipboard"].run({})
    assert adb.shell[-1] == 'input text "Gate%scode%sis%s4471"'


def test_one_time_code_is_never_put_in_the_reply_and_bank_codes_are_left_alone(calls, monkeypatch, nova):
    import sys
    import types

    from nova.agent import Agent
    p, adb, _ = calls
    clip = types.SimpleNamespace(copied=[])
    clip.copy = clip.copied.append
    monkeypatch.setitem(sys.modules, "pyperclip", clip)
    spoken = []
    monkeypatch.setattr(context, "speak_now", spoken.append)
    adb.notif = "  NotificationRecord(0x1: pkg=com.samsung.android.messaging)\n   android.title=String (Microsoft)\n   android.text=String (Use verification code 482913 to sign in.)\n"
    out = Agent(nova[0], context.llm).handle("what's the code?").text                   # no model call
    assert "482913" not in out and "Microsoft" in out and clip.copied == ["482913"]
    assert spoken == ["The code is 4 8 2 9 1 3."]
    adb.notif = "  NotificationRecord(0x1: pkg=com.samsung.android.messaging)\n   android.title=String (FNB)\n   android.text=String (Your OTP is 771204 for a payment of R500.)\n"
    out = REGISTRY["phone_code"].run({})
    assert "bank or payment" in out and "771204" not in out and clip.copied == ["482913"] and len(spoken) == 1
    adb.notif = NOTIF
    assert "don't see a one-time code" in REGISTRY["phone_code"].run({})


def test_connection_restores_itself_after_a_phone_restart(rich, nova, monkeypatch):
    """Port 5555 is gone after a restart. With Wireless debugging on, Nova finds its port and re-arms 5555."""
    p, adb, _ = rich
    from nova import phone_watch
    nova[0]["phone"] = {"address": "100.101.102.103", "alerts": False}
    adb.devs.clear()
    real, tls, state = adb.__call__, "100.101.102.103:41877", {"armed": False}

    def run(args, timeout=25, binary=False):
        if args[0] == "connect":
            adb.calls.append(args)
            if args[1] == tls or (args[1].endswith(":5555") and state["armed"]):
                adb.devs[args[1]] = "device"
                return 0, f"connected to {args[1]}"
            return 0, f"failed to connect to {args[1]}"
        if "tcpip" in args:
            state["armed"] = True
        return real(args, timeout, binary)
    p._run = run
    assert "can't reach" in p.connect()
    assert "Wireless debugging isn't on" in p.rearm(scan=lambda ip: [])              # nothing to find: says why
    assert p.rearm(scan=lambda ip: [41877, 44001]) == "" and p.serial == "100.101.102.103:5555"
    assert ["-s", tls, "tcpip", "5555"] in adb.calls and ["disconnect", tls] in adb.calls
    # the watcher does it by herself and says so
    adb.devs.clear()
    state["armed"] = False
    monkeypatch.setattr(ph, "scan_ports", lambda ip, **k: [41877])
    monkeypatch.setattr(p, "rearm", lambda scan=None, _r=p.rearm: _r(scan=lambda ip: [41877]))
    monkeypatch.setattr(phone_watch, "_background", lambda job: job())
    phone_watch.tick(p)
    phone_watch.tick(p)                                                              # second miss: repair
    assert phone_watch.state["online"] and p.serial == "100.101.102.103:5555"
    assert any("restored the connection" in e["title"] for e in context.store.activity(0) if e["kind"] == "notice")


def test_self_repair_setup_grants_the_automation_app_its_permission(rich):
    p, adb, _ = rich
    real = adb.__call__
    out = REGISTRY["phone_self_repair_setup"].run({})
    assert "install MacroDroid" in out and "settings put global adb_allowed_connection_time 0" in adb.shell
    p._run = lambda args, timeout=25, binary=False: (0, "package:com.whatsapp\npackage:com.arlosoft.macrodroid") \
        if args[-1] == "pm list packages" else real(args, timeout, binary)
    out = REGISTRY["phone_self_repair_setup"].run({})
    assert "pm grant com.arlosoft.macrodroid android.permission.WRITE_SECURE_SETTINGS" in adb.shell
    assert "MacroDroid now has the permission" in out and "adb_wifi_enabled, value 1" in out


def test_family_phone_is_located_and_rung_only_when_listed(rich, nova, monkeypatch):
    p, adb, _ = rich
    out = REGISTRY["phone_locate"].run({"who": "Sam"})
    assert "don't have a phone set up for 'Sam'" in out and "add a family phone" in out
    # her phone on the cable → added under her name, not as the main phone
    adb.devs.clear()
    adb.devs["R9FAMILY1"] = "device"
    saved = {}
    from nova import settings
    monkeypatch.setattr(settings, "apply", lambda payload: saved.update(payload["values"]) or {"saved": []})
    out = REGISTRY["phone_add_family"].run({"name": "sam's"})
    assert out.startswith("Added Sam's SM-S926B") and "only use it to show where it is and to make it ring" in out
    assert saved == {"phone.family": ["Sam = 100.101.102.103"]} and "address" not in nova[0]["phone"]
    assert ph.family() == {"Sam": "100.101.102.103"}
    name, fp = ph.family_phone("Sam's phone")
    assert name == "Sam" and fp.address() == "100.101.102.103:5555" and ph.family_phone("Thabo") == ("", None)
    dump = "  last location=Location[fused -26.107600,28.056800 hAcc=14 et=+3d2h]"
    monkeypatch.setattr(ph.Phone, "where", lambda self: {"lat": -26.1076, "lon": 28.0568, "accuracy": 14, "source": "fused"} if self.fixed else None)
    out = REGISTRY["phone_locate"].run({"who": "Sam"})
    assert out.startswith("Sam's phone was last at -26.10760, 28.05680, accurate to about 14 metres.") and dump
    assert "Sam's phone is ringing at full volume" in REGISTRY["phone_ring"].run({"who": "Sam"})
    adb.devs.clear()                                                                # her phone is off / out of reach
    real = adb.__call__
    p._run = lambda args, timeout=25, binary=False: (0, "failed to connect") if args[0] == "connect" else real(args, timeout, binary)
    assert "can't reach Sam's phone" in REGISTRY["phone_locate"].run({"who": "Sam"})
