"""Phone hands: operate the user's Android phone over ADB + Tailscale (engine in nova/phone.py)."""
from __future__ import annotations

import time

from .. import context
from .. import phone as ph
from ..tools import register_group, tool

register_group("phone", ["my phone", "the phone", "on my phone", "phone's", "galaxy", "s24", "android", "cellphone",
                         "cell phone", "mobile", "handset", "phone battery", "phone screen", "phone notifications",
                         "whatsapp", "sms", "text message", "call ", "dial", "phone photos", "phone camera",
                         "ring my", "find my phone", "where is my phone", "track my", "locate my", "wifi", "wi-fi", "bluetooth", "hotspot",
                         "do not disturb", "dnd", "airplane mode", "flight mode", "brightness", "battery saver",
                         "dark mode", "mobile data", "telegram message", "message to", "messages from", "reply to",
                         "what did", "any messages", "new messages", "missed call", "phone routine", "routine",
                         " mode", "signal message", "teams message", "phone health", "phone storage", "flashlight",
                         "torch"])


def _ready() -> str:
    """'' when the phone is connected, otherwise what to tell the user."""
    try:
        return ph.phone().connect()
    except Exception as e:
        return f"I couldn't start the Android connection tool: {e}"


def _one_task() -> str:
    """One phone task per request: when it didn't work out, Nova reports back instead of trying another way herself
    (that is how one WhatsApp message turned into duplicates and a detour through Messenger)."""
    if context.count("phone_task") > 1:
        return ("STOP: one phone task has already been run for this request. Do not start another one or try a "
                "different app. Tell the user exactly what happened and ask what they would like to do next.")
    return ""


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
    if again := _one_task():
        return again
    return ph.run_task(task, allowed)


@tool(group="phone")
def stop_phone_task() -> str:
    """Stop the task Nova is doing on the phone."""
    return ph.stop_task()


# ── health, switches, find my phone ───────────────────────
@tool(group="phone")
def phone_health() -> str:
    """How the phone is doing: battery, temperature, free storage, memory and Wi-Fi."""
    if err := _ready():
        return err
    h = ph.phone().health()
    bits = []
    if h["battery"] is not None:
        bits.append(f"battery {h['battery']}%{' and charging' if h['charging'] else ''}")
    if h["temperature"] is not None:
        bits.append(f"{h['temperature']}°C" + (" (hot)" if h["temperature"] >= 42 else ""))
    if h["storage_free_gb"] is not None:
        bits.append(f"{h['storage_free_gb']} GB free of {h['storage_total_gb']} GB storage ({h['storage_used_pct']}% used)")
    if h["memory_free_pct"] is not None:
        bits.append(f"{h['memory_free_pct']}% memory free")
    bits.append(f"on Wi-Fi '{h['wifi']}'" if h["wifi"] else "not on Wi-Fi (mobile data)")
    try:
        from .. import phone_watch
        phone_watch.state.update(health=h, online=True, checked=time.time())
    except Exception:
        pass
    return f"{h['model'] or 'Your phone'}: " + ", ".join(bits) + "."


@tool(group="phone")
def phone_switch(setting: str, on: bool = True) -> str:
    """Turn a phone setting on or off: wifi, bluetooth, mobile data, do not disturb, airplane mode, location,
    auto rotate, battery saver, dark mode, nfc, silent, vibrate, hotspot, flashlight.
    Args:
        setting: which one
        on: true = on, false = off
    """
    if err := _ready():
        return err
    p = ph.phone()
    name, why = p.switch(setting, on)
    word = "on" if on else "off"
    if name and not why:
        extra = ""
        if name == "airplane mode" and on:
            extra = " That also cuts me off from the phone — switch it off on the phone itself when you're done."
        return f"{name.capitalize()} is {word} on your phone.{extra}"
    # no direct switch for it (hotspot, flashlight…) or the phone refused: use the quick-settings panel instead
    return ph.run_task(f"Turn {word} '{name or setting}' on this phone: swipe down the quick settings panel and tap its "
                       f"button if it is not already {word}; then press home.")


@tool(group="phone")
def phone_set_level(what: str, percent: int) -> str:
    """Set the phone's brightness or a volume.
    Args:
        what: brightness, media volume, ring volume, alarm volume, call volume or notification volume
        percent: 0 to 100
    """
    if err := _ready():
        return err
    why = ph.phone().set_level(what, percent)
    return f"ERROR: the phone refused ({why})" if why else f"{what.capitalize()} is at {max(0, min(100, int(percent)))}% on your phone."


@tool(group="phone")
def phone_ring() -> str:
    """Find my phone: make it ring loudly, even when it is on silent."""
    if err := _ready():
        return err
    how = ph.phone().ring()
    if how == "timer":
        return "Your phone is ringing at full volume — it keeps going until you dismiss it on the phone."
    if how == "ringtone":
        return "Your phone is playing a ringtone at full volume."
    return "ERROR: I couldn't make the phone ring — its clock and music apps both refused."


@tool(group="phone")
def phone_locate() -> str:
    """Where the phone is right now: its position on the map (track / locate my phone)."""
    if err := _ready():
        return err
    try:
        loc = ph.phone().where()
    except Exception as e:
        return f"ERROR: {e}"
    if not loc:
        return ("Your phone hasn't got a position yet — Location may be off. Say 'turn on location on my phone', open "
                "Maps on it once, and ask again.")
    link = f"https://www.google.com/maps?q={loc['lat']:.6f},{loc['lon']:.6f}"
    try:
        from .. import cards, phone_watch
        phone_watch.state["where"] = {**loc, "url": link}
        cards.show("info", "Where your phone is", {"text": f"{loc['lat']:.5f}, {loc['lon']:.5f}\n{link}"})
    except Exception:
        pass
    near = f", accurate to about {loc['accuracy']} metres" if loc["accuracy"] is not None else ""
    return (f"Your phone's last position is {loc['lat']:.5f}, {loc['lon']:.5f}{near}. Map: {link} — it's on the "
            "dashboard too. Say 'ring my phone' to make it ring.")


@tool(group="phone")
def phone_reconnect() -> str:
    """Reconnect to the phone when the connection dropped (no cable needed unless the phone was restarted)."""
    p = ph.phone()
    addr = p.address()
    if addr:
        p._run(["disconnect", addr], timeout=8)
    if err := _ready():
        return err
    return f"Connected to your phone again ({p.serial})."


# ── messages: read, reply, any app ────────────────────────
@tool(group="phone")
def phone_messages(app: str = "", sender: str = "") -> str:
    """Read the messages waiting on the phone (WhatsApp, SMS, Telegram, Signal, Teams, email, missed calls) — "what
    did Sam say?", "any new messages?".
    Args:
        app: only this app (whatsapp, sms, telegram, gmail, outlook, teams, signal…); empty = all
        sender: only from this person; empty = everyone
    """
    if err := _ready():
        return err
    p = ph.phone()
    got = p.messages(app, sender)
    if got:
        try:
            from .. import phone_watch
            phone_watch.last_message.clear()
            phone_watch.last_message.update(got[0])
        except Exception:
            pass
        return "\n".join(f"- {m['app']} — {m['from']}: {m['text'] or '(no text)'}" for m in got[:15])
    if sender:                         # nothing waiting from them: open the chat and read it
        name = ph.MESSAGE_APPS.get(app.lower().strip(), app.strip() or "WhatsApp")
        return ph.run_task(f"Open {name}, open the chat with {sender} and report the last few messages word for word "
                           f"with who said each. Do not type or send anything.")
    return "No new messages waiting on your phone" + (f" in {app}." if app else ".")


@tool(group="phone")
def phone_send_message(to: str, text: str, app: str = "", send: bool = True) -> str:
    """Write a message to someone from the phone and send it — also for replying ("reply to Sam: on my way").
    Works in WhatsApp, SMS (Messages), Telegram, Signal, Teams, Messenger, Gmail and Outlook.
    Args:
        to: the person as they are named in that app; empty = whoever sent the newest message
        text: the message, word for word
        app: whatsapp, sms, telegram, signal, teams, gmail, outlook…; empty = the app their last message came in, else WhatsApp
        send: false when the user said draft / write / prepare (it is typed but not sent)
    """
    from .. import phone_watch
    last = dict(phone_watch.last_message)
    if not to.strip():
        if not last:
            return "Who should I send it to? There's no recent message to reply to."
        to, app = last["from"], app or last["app"]
    elif not app and last and to.lower().strip() in last.get("from", "").lower():
        app = last["app"]
    if not text.strip():
        return "What should the message say?"
    if again := _one_task():
        return again
    name = ph.MESSAGE_APPS.get((app or "whatsapp").lower().strip(), app or "WhatsApp")
    return ph.run_task(ph.message_task(to.strip(), text.strip(), app or "whatsapp", send), "send" if send else "",
                       only={"Messages": "messages"}.get(name, name.lower()), to=to.strip())


# ── saved routines ────────────────────────────────────────
@tool(group="phone")
def phone_run_routine(name: str) -> str:
    """Run a saved phone routine (several steps from one phrase), e.g. "driving mode", "bedtime".
    Args:
        name: the routine's name
    """
    from .. import phone_routines
    return phone_routines.run(name)


@tool(group="phone")
def phone_save_routine(name: str, steps: str) -> str:
    """Save (or replace) a phone routine.
    Args:
        name: what the user will say to start it, e.g. "Driving mode"
        steps: the steps in order, separated by semicolons, in plain words — e.g. "do not disturb on; bluetooth on;
               media volume 70; open maps; open spotify"
    """
    from .. import phone_routines
    todo = phone_routines.steps_of(steps)
    if not name.strip() or not todo:
        return "A routine needs a name and at least one step."
    saved = phone_routines.save(name, todo)
    return f"Saved the phone routine '{saved}' with {len(todo)} steps: {'; '.join(todo)}. Say '{saved.lower()}' to run it."


@tool(group="phone")
def phone_routines_list() -> str:
    """The saved phone routines and their steps."""
    from .. import phone_routines
    data = phone_routines.load()
    return "\n".join(f"- {k}: {'; '.join(v)}" for k, v in data.items()) if data else "No phone routines saved yet."


@tool(group="phone")
def phone_delete_routine(name: str) -> str:
    """Remove a saved phone routine.
    Args:
        name: the routine's name
    """
    from .. import phone_routines
    return f"Removed the phone routine '{name}'." if phone_routines.delete(name) else f"There's no routine called '{name}'."
