"""Phone hands: operate the user's Android phone over ADB + Tailscale (engine in nova/phone.py)."""
from __future__ import annotations

import time

from .. import context
from .. import phone as ph
from ..tools import register_group, tool

register_group("phone", ["my phone", "the phone", "on my phone", "phone's", "galaxy", "s24", "android", "cellphone",
                         "cell phone", "mobile", "handset", "phone battery", "phone screen", "phone notifications",
                         "whatsapp", "sms", "text message", "call ", "dial", "phone photos", "phone camera"])


def _ready() -> str:
    """'' when the phone is connected, otherwise what to tell the user."""
    try:
        return ph.phone().connect()
    except Exception as e:
        return f"I couldn't start the Android connection tool: {e}"


@tool(group="phone")
def phone_setup() -> str:
    """Connect Nova to the user's Android phone (first time, or after the phone was restarted). The phone must be
    plugged into the PC with USB debugging allowed, or have Wireless debugging on while on the home Wi-Fi."""
    try:
        return ph.phone().setup()
    except Exception as e:
        return (f"ERROR: I couldn't get the Android connection tool ready ({e}). If the download is blocked, get "
                "'SDK Platform-Tools for Windows' from developer.android.com, unzip it and put the path to adb.exe "
                "in Settings → Phone.")


@tool(group="phone")
def phone_status() -> str:
    """Is the phone connected, its battery, whether it's locked and which app is open."""
    if err := _ready():
        return err
    s = ph.phone().status()
    bits = [f"{s['model']} (Android {s['android']}) is connected"]
    if s["battery"] is not None:
        bits.append(f"battery {s['battery']}%{' and charging' if s['charging'] else ''}")
    bits.append("locked" if s["locked"] else "screen on" if s["screen_on"] else "screen off")
    if s["app"] and not s["locked"]:
        bits.append(f"showing {s['app']}")
    return ", ".join(bits) + "."


@tool(group="phone")
def phone_screenshot() -> str:
    """Take a screenshot of the phone and attach it."""
    if err := _ready():
        return err
    p = ph.phone()
    p.wake()
    out = ph.out_dir() / f"phone_{time.strftime('%Y%m%d_%H%M%S')}.png"
    try:
        p.screenshot(out)
    except Exception as e:
        return f"ERROR: {e}"
    aid = context.record("image", "Phone screenshot", out, "phone")
    context.attach(out)
    try:                                        # show it on the dashboard straight away
        from .. import cards
        if aid:
            cards.show("image", "Phone screenshot", {"id": f"artifact:{aid}", "name": out.name, "path": str(out)})
    except Exception:
        pass
    return (f"Screenshot taken and saved on the PC: {out}. It is showing on the dashboard. "
            "(To send it to Telegram use send_to_phone.)")


@tool(group="phone")
def phone_read_screen() -> str:
    """Read what is on the phone's screen right now (text, buttons, fields) as a numbered list."""
    if err := _ready():
        return err
    try:
        els = ph.phone().screen()
    except Exception as e:
        return f"ERROR: {e}"
    if not els:
        return "The phone's screen has nothing readable (is it off or locked?)."
    return "\n".join(f"{e['id']}. {e['type']} '{e['text']}'" + (" [tap]" if e["tap"] else "") for e in els[:120])


@tool(group="phone")
def phone_open_app(name: str) -> str:
    """Open an app on the phone.
    Args:
        name: the app, e.g. WhatsApp, Camera, Gallery, Maps, Settings
    """
    if err := _ready():
        return err
    p = ph.phone()
    p.wake()
    pkg = p.open_app(name)
    return f"Opened {name} on your phone." if pkg else f"I couldn't find an app called {name} on your phone."


@tool(group="phone")
def phone_press(key: str) -> str:
    """Press a phone button.
    Args:
        key: home, back, recents, enter, power, wake, sleep (lock), volume_up, volume_down, mute, play_pause, next,
             previous, notifications
    """
    if err := _ready():
        return err
    return f"Pressed {key}." if ph.phone().key(key) else f"I don't know the key '{key}'."


@tool(group="phone")
def phone_notifications() -> str:
    """The notifications currently on the phone (app, title, text)."""
    if err := _ready():
        return err
    items = ph.phone().notifications()
    if not items:
        return "No notifications on your phone."
    names = {v: k.title() for k, v in ph.APPS.items()}
    lines = [f"- {names.get(n['app'], n['app'].rsplit('.', 1)[-1])}: {n['title']}"
             + (f" — {n['text'][:140]}" if n["text"] else "") for n in items[:20]]
    try:
        from .. import cards
        cards.show("info", "Phone notifications", {"text": "\n".join(lines)})
    except Exception:
        pass
    return "\n".join(lines)


@tool(group="phone")
def phone_get_photos(count: int = 3) -> str:
    """Copy the newest photos / videos from the phone's camera to the PC and attach them.
    Args:
        count: how many (1-20)
    """
    if err := _ready():
        return err
    p = ph.phone()
    got = []
    for remote in p.latest_photos(count):
        try:
            got.append(p.pull(remote, ph.out_dir() / remote.rsplit("/", 1)[-1]))
        except Exception as e:
            print(f"[phone] {remote}: {e}")
    for f in got[:5]:
        context.attach(f)
        context.record("image", f.name, f, "from phone")
    return f"Copied {len(got)} file(s) to {ph.out_dir()}." if got else "I couldn't find photos on the phone."


@tool(group="phone")
def phone_put_file(path: str) -> str:
    """Copy a file from the PC to the phone's Download folder.
    Args:
        path: the file on the PC
    """
    if err := _ready():
        return err
    from .files import safe
    try:
        where = ph.phone().push(safe(path))
    except Exception as e:
        return f"ERROR: {e}"
    return f"It's on your phone at {where}."


@tool(group="phone")
def phone_open_link(url: str) -> str:
    """Open a web page or link on the phone.
    Args:
        url: the address
    """
    if err := _ready():
        return err
    p = ph.phone()
    p.wake()
    p.open_url(url if "://" in url else "https://" + url)
    return f"Opened {url} on your phone."


@tool(group="phone", confirm=True)
def phone_call(number: str) -> str:
    """Start a phone call from the user's phone.
    Args:
        number: the number to call, digits with optional +
    """
    if err := _ready():
        return err
    import re
    num = re.sub(r"[^\d+]", "", number)
    if len(num) < 3:
        return "That doesn't look like a phone number."
    p = ph.phone()
    p.wake()
    p.sh(f"am start -a android.intent.action.CALL -d tel:{num}")
    return f"Calling {num} from your phone."


@tool(group="phone")
def do_on_phone(task: str, allowed: str = "") -> str:
    """Carry out a multi-step task on the user's phone by reading its screen and tapping / typing, step by step
    (e.g. "open WhatsApp and write to Sam that I'm running late", "turn on the hotspot", "find the cheapest Uber").
    Stops before sending, paying, buying, calling or deleting unless those words are in `allowed`.
    Args:
        task: what to do, in plain words with every detail needed
        allowed: risky actions the USER explicitly approved in their own words, e.g. "send" (empty = none)
    """
    return ph.run_task(task, allowed)


@tool(group="phone")
def stop_phone_task() -> str:
    """Stop the task Nova is doing on the phone."""
    return ph.stop_task()
