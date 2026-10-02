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
