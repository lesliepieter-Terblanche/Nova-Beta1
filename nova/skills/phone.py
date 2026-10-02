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
                         "torch", "answer the", "hang up", "decline the", "on speaker", "phone screen", "take a photo",
                         "take a picture", "selfie", "navigate to", "directions to", "clipboard to", "to my phone",
                         "code from", "verification code", "one-time", "otp", "what's playing", "whats playing",
                         "next song", "next track", "scheduled message", "missed on", "starts by itself", "self repair",
                         "self-repair", "after a restart", "where is", "where's", "'s phone", "family phone", "wife's", "husband's", "after a reboot", "phone restarts", "phone reboots"])


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
    """Start a phone call from the user's phone — to a number or to someone in the phone's contacts by name.
    Args:
        number: the number (digits with optional +) OR the contact's name, e.g. "Sam Dlamini"
    """
    if err := _ready():
        return err
    import re
    p = ph.phone()
    who = number.strip()
    num = re.sub(r"[^\d+]", "", who)
    if re.search(r"[A-Za-z]", who):
        try:
            found = p.find_contact(who)
        except Exception as e:
            return f"ERROR: I couldn't read the phone's contacts ({e})."
        numbers = list(dict.fromkeys(n for _, n in found))
        if not found:
            return f"I couldn't find '{who}' in your phone's contacts. Say the name as it is saved, or give me the number."
        if len(numbers) > 1:
            return ("Which one? " + "; ".join(f"{n} on {x}" for n, x in found[:6]) +
                    ". Tell me the full name or the number.")
        who, num = found[0][0], numbers[0]
    else:
        who = num
    if len(num) < 3:
        return "That doesn't look like a phone number."
    p.wake()
    p.sh(f"am start -a android.intent.action.CALL -d tel:{num}")
    return f"Calling {who} from your phone." if who == num else f"Calling {who} ({num}) from your phone."


@tool(group="phone")
def phone_call_control(action: str) -> str:
    """Handle a call on the phone: answer, decline, hang_up, speaker or mute.
    Args:
        action: answer | decline | hang_up | speaker | mute
    """
    if err := _ready():
        return err
    p = ph.phone()
    a = action.lower().strip().replace(" ", "_")
    if a == "answer":
        p.key("answer")
        return "Answered the call." if p.call_state() == "in_call" else "I pressed answer — check the phone."
    if a in ("decline", "reject", "hang_up", "end", "end_call", "hangup"):
        p.key("hang_up")
        return "Declined the call." if a in ("decline", "reject") else "Hung up."
    if a in ("speaker", "mute"):
        try:
            hit = p.tap_text(a, "speaker" if a == "speaker" else "mute", "Speaker" if a == "speaker" else "Mute")
        except Exception as e:
            return f"ERROR: {e}"
        return f"Tapped {hit} on the call." if hit else f"I can't see a {a} button — is the call on the screen?"
    return "I can answer, decline, hang up, or switch speaker / mute."


# ── see and control the phone from the PC ─────────────────
@tool(group="phone")
def phone_show_screen() -> str:
    """Show the phone's live screen in a window on the PC (mouse and keyboard work in it)."""
    from .. import phone_mirror
    try:
        return phone_mirror.start()
    except Exception as e:
        return (f"ERROR: I couldn't open the phone's screen ({e}). If the download is blocked, get scrcpy from "
                "github.com/Genymobile/scrcpy, unzip it and put the path to scrcpy.exe in Settings → Phone.")


@tool(group="phone")
def phone_hide_screen() -> str:
    """Close the phone's live screen window on the PC."""
    from .. import phone_mirror
    return phone_mirror.stop()


@tool(group="phone")
def phone_take_photo() -> str:
    """Take a photo with the phone's camera right now and bring it to the PC."""
    if err := _ready():
        return err
    p = ph.phone()
    if p.status()["locked"]:
        p.wake()
    try:
        shot = p.take_photo(ph.out_dir())
    except Exception as e:
        return f"ERROR: {e}"
    if not shot:
        return "The camera opened but no new photo appeared — unlock the phone and try again."
    aid = context.record("image", "Photo from phone", shot, "phone camera")
    context.attach(shot)
    try:
        from .. import cards
        if aid:
            cards.show("image", "Photo from your phone", {"id": f"artifact:{aid}", "name": shot.name, "path": str(shot)})
    except Exception:
        pass
    return f"Photo taken and saved on the PC: {shot}. It is showing on the dashboard."


# ── timing ────────────────────────────────────────────────
@tool(group="phone")
def phone_schedule_message(to: str, text: str, when: str, app: str = "whatsapp") -> str:
    """Send a message from the phone LATER ("send Sam happy birthday at 7 tomorrow").
    Args:
        to: the person as named in that app
        text: the message, word for word
        when: date and time as "YYYY-MM-DD HH:MM" (work it out from the current date), or "tomorrow 07:00", "18:30", "in 20 minutes"
        app: whatsapp, sms, telegram, signal, teams, gmail, outlook…
    """
    from .. import phone_schedule
    at = phone_schedule.parse(when)
    if not at:
        return f"I didn't understand the time '{when}'."
    import datetime as dt
    if at <= dt.datetime.now():
        return "That time has already passed."
    if not to.strip() or not text.strip():
        return "I need who it is for and what it should say."
    item = phone_schedule.add(to.strip(), text.strip(), at, app)
    return (f"Scheduled: {ph.MESSAGE_APPS.get(item['app'].lower(), item['app'])} to {item['to']} on "
            f"{at:%A %d %B at %H:%M} — \"{item['text'][:120]}\". The phone must be unlocked then; if it is locked I "
            "keep trying for an hour and tell you.")


@tool(group="phone")
def phone_scheduled() -> str:
    """The messages waiting to be sent from the phone later."""
    from .. import phone_schedule
    items = phone_schedule.load()
    return "\n".join(f"{i['id']}. {i['at']} — {i['app']} to {i['to']}: {i['text'][:100]}" for i in items) \
        if items else "No messages are scheduled."


@tool(group="phone")
def phone_cancel_scheduled(which: str) -> str:
    """Cancel a scheduled phone message.
    Args:
        which: its number, the person's name, or "all"
    """
    from .. import phone_schedule
    gone = phone_schedule.cancel(which)
    return f"Cancelled {len(gone)}: " + "; ".join(f"{g['to']} at {g['at']}" for g in gone) if gone else \
        "I didn't find a scheduled message like that."


@tool(group="phone")
def phone_routine_trigger(name: str, at: str = "", when: str = "", days: str = "") -> str:
    """Make a saved phone routine start BY ITSELF at a time and/or when something happens. Empty at and when = stop
    starting it automatically.
    Args:
        name: the routine
        at: time of day, e.g. "22:00"
        when: charging | unplugged | battery below 20 | bluetooth <device name> | wifi <network name> | leaving wifi <network name>
        days: e.g. "weekdays", "weekend", "mon wed fri"; empty = every day
    """
    from .. import phone_routines
    return phone_routines.set_trigger(name, at, when, days)


# ── everyday convenience ──────────────────────────────────
@tool(group="phone")
def phone_navigate(destination: str) -> str:
    """Start Google Maps navigation on the phone.
    Args:
        destination: an address or place
    """
    if err := _ready():
        return err
    from urllib.parse import quote_plus
    p = ph.phone()
    p.wake()
    p.sh(f"am start -a android.intent.action.VIEW -d 'google.navigation:q={quote_plus(destination)}'")
    return f"Navigation to {destination} is starting on your phone."


@tool(group="phone")
def phone_send_clipboard() -> str:
    """Send what is on the PC's clipboard to the phone: a link opens on the phone, text is typed into the text box
    that is open on the phone."""
    if err := _ready():
        return err
    import re
    try:
        import pyperclip
        text = (pyperclip.paste() or "").strip()
    except Exception as e:
        return f"ERROR: I couldn't read the PC clipboard ({e})."
    if not text:
        return "The PC's clipboard is empty."
    p = ph.phone()
    p.wake()
    if re.fullmatch(r"(https?://|www\.)\S+", text):
        p.open_url(text if "://" in text else "https://" + text)
        return "Opened the link from your clipboard on the phone."
    if len(text) > 2000:
        return "That's too much text to type onto the phone — send it as a file instead."
    try:
        box = next((e for e in p.screen() if e["edit"]), None)
    except Exception:
        box = None
    if not box:
        return "Open the text box on the phone where it should go (a chat, a note, a search bar) and ask me again."
    if not box.get("focused"):
        p.tap(box["x"], box["y"])
        time.sleep(0.6)
    p.type(text)
    return "Typed your PC clipboard into the text box on the phone. Nothing was sent."


BANK = r"(bank|fnb|absa|nedbank|capitec|standard ?bank|investec|discovery|tymebank|african bank|card|payment|purchase|" \
       r"transaction|transfer|paypal|payfast|ozow|beneficiary|debit|withdraw)"


@tool(group="phone")
def phone_code() -> str:
    """Get the newest one-time code (OTP / verification code) that arrived on the phone. It is spoken, shown on the
    dashboard and copied to the PC clipboard — the digits are never put in the reply. Bank and payment codes are skipped."""
    if err := _ready():
        return err
    import re
    for n in ph.phone().notifications():
        text = f"{n['title']} {n['text']}"
        m = re.search(r"(?<![\d.,])(\d{3}[- ]?\d{3}|\d{4,8})(?![\d.,])", n["text"] or "")
        if not m or not re.search(r"(code|otp|pin|verif|one[- ]time|passcode|password|login|sign[- ]?in)", text, re.I):
            continue
        if re.search(BANK, text, re.I):
            return ("The newest code is from a bank or payment — I leave those alone. Read that one on the phone "
                    "yourself.")
        code = re.sub(r"\D", "", m.group(1))
        try:
            import pyperclip
            pyperclip.copy(code)
            copied = " and copied to the PC clipboard"
        except Exception:
            copied = ""
        try:
            from .. import cards
            cards.show("info", f"Code from {n['title'] or 'your phone'}", {"text": code})
        except Exception:
            pass
        if context.speak_now:
            context.speak_now("The code is " + " ".join(code) + ".")
        return f"The code from {n['title'] or 'your phone'} is on the dashboard{copied}. (I don't repeat the digits here.)"
    return "I don't see a one-time code in the phone's notifications."


@tool(group="phone")
def phone_media(action: str = "what") -> str:
    """Control what is playing on the phone, or say what it is.
    Args:
        action: play | pause | play_pause | next | previous | stop | what
    """
    if err := _ready():
        return err
    p = ph.phone()
    a = action.lower().strip().replace(" ", "_")
    if a in ("what", "whats_playing", "now_playing", ""):
        title = p.now_playing()
        return f"Playing on your phone: {title}." if title else "Nothing seems to be playing on your phone."
    key = {"skip": "next", "back": "previous", "resume": "play", "toggle": "play_pause"}.get(a, a)
    if key not in ("play", "pause", "play_pause", "next", "previous", "stop") or not p.key(key):
        return "I can play, pause, skip to next, go to previous or stop."
    return {"play": "Playing.", "pause": "Paused.", "play_pause": "Done.", "next": "Next track.",
            "previous": "Previous track.", "stop": "Stopped."}[key]


@tool(group="phone")
def phone_missed() -> str:
    """What came in on the phone since the user last asked: how many messages and calls, and from whom."""
    from .. import phone_watch
    return phone_watch.missed_summary() or "Nothing new has come in on your phone."


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
def phone_ring(who: str = "") -> str:
    """Find a phone: make it ring loudly, even when it is on silent — the user's own, or a family member's.
    Args:
        who: empty = the user's own phone; otherwise the family member's name
    """
    label, p, err = _whose(who)
    if err:
        return err
    how = p.ring()
    if how == "timer":
        return f"{label} is ringing at full volume — it keeps going until it is dismissed on the phone."
    if how == "ringtone":
        return f"{label} is playing a ringtone at full volume."
    return "ERROR: I couldn't make the phone ring — its clock and music apps both refused."


def _whose(who: str):
    """(label, phone, error) — the user's own phone, or a family phone when `who` names someone listed."""
    if who.strip() and who.strip().lower() not in ("me", "my", "mine", "my phone", "phone"):
        name, fp = ph.family_phone(who)
        if not fp:
            have = ", ".join(ph.family()) or "none yet"
            return "", None, (f"I don't have a phone set up for '{who}'. Family phones: {have}. Plug theirs into the PC "
                              "and say 'add a family phone' with their name.")
        try:
            err = fp.connect()
        except Exception as e:
            err = str(e)
        if err:
            return name, None, (f"I can't reach {name}'s phone right now — Tailscale must be on on it, and after a "
                                "restart it needs the cable once more.")
        return f"{name}'s phone", fp, ""
    if err := _ready():
        return "", None, err
    return "Your phone", ph.phone(), ""


@tool(group="phone")
def phone_locate(who: str = "") -> str:
    """Where a phone is right now on the map: the user's own, or a family member's phone that was added with
    their agreement ("where is Sam?").
    Args:
        who: empty = the user's own phone; otherwise the family member's name
    """
    label, p, err = _whose(who)
    if err:
        return err
    try:
        loc = p.where()
    except Exception as e:
        return f"ERROR: {e}"
    if not loc:
        return (f"{label} hasn't got a position yet — Location may be off on it. Switch Location on, open Maps on "
                "it once, and ask again.")
    link = f"https://www.google.com/maps?q={loc['lat']:.6f},{loc['lon']:.6f}"
    try:
        from .. import cards, phone_watch
        if not who.strip():
            phone_watch.state["where"] = {**loc, "url": link}
        cards.show("info", f"Where {label[0].lower() + label[1:]} is", {"text": f"{loc['lat']:.5f}, {loc['lon']:.5f}\n{link}"})
    except Exception:
        pass
    near = f", accurate to about {loc['accuracy']} metres" if loc["accuracy"] is not None else ""
    return f"{label} was last at {loc['lat']:.5f}, {loc['lon']:.5f}{near}. Map: {link} — it's on the dashboard too."


@tool(group="phone")
def phone_add_family(name: str) -> str:
    """Add a family member's phone so it can be located and rung. Their phone must be plugged into the PC with USB
    debugging allowed and Tailscale on, and they must have agreed to it.
    Args:
        name: the person's first name, e.g. "Sam"
    """
    name = name.strip().split("'")[0].title()
    if not name:
        return "Whose phone is it? Give me their first name."
    p = ph.Phone(run=ph.phone()._run)
    try:
        out = p.setup(save=False)
    except Exception as e:
        return f"ERROR: {e}"
    ip, model = p.last_setup
    if not ip:
        return out
    if ip == str(ph.cfg().get("address", "")).strip():
        return "That is your own phone on the cable — unplug it and plug in theirs."
    entries = [e for e in (ph.cfg().get("family") or []) if not str(e).lower().startswith(name.lower() + " =")]
    entries.append(f"{name} = {ip}")
    try:
        from .. import settings
        settings.apply({"values": {"phone.family": entries}})
    except Exception as e:
        print(f"[phone] couldn't save the family phone: {e}")
    if context.cfg is not None:
        context.cfg.setdefault("phone", {})
        context.cfg["phone"]["family"] = entries
    try:
        p.keep_trust()
    except Exception:
        pass
    return (f"Added {name}'s {model} ({out}) I only use it to show where it is and to make it ring — say 'where is "
            f"{name}' or 'ring {name}'s phone'.")


@tool(group="phone")
def phone_reconnect() -> str:
    """Reconnect to the phone when the connection dropped (no cable needed unless the phone was restarted)."""
    p = ph.phone()
    addr = p.address()
    if addr:
        p._run(["disconnect", addr], timeout=8)
    if err := _ready():
        try:
            if not p.rearm():                    # the phone restarted: restore the link through Wireless debugging
                return f"Your phone had restarted — I've restored the connection ({p.serial})."
        except Exception:
            pass
        return err
    return f"Connected to your phone again ({p.serial})."


@tool(group="phone")
def phone_self_repair_setup() -> str:
    """Prepare the phone so the connection restores itself after the phone restarts (no cable): gives an automation
    app on the phone the one permission it needs and explains the single rule to create in it."""
    if err := _ready():
        return err
    p = ph.phone()
    p.keep_trust()
    pkg, name = p.automation_app()
    if not pkg:
        return ("I've told the phone to keep trusting this PC. For the rest, install MacroDroid (free) from the Play "
                "Store on the phone and ask me again — it is what switches Wireless debugging back on after a restart.")
    out = p.sh(f"pm grant {pkg} android.permission.WRITE_SECURE_SETTINGS")
    if ph.SHELL_FAIL.search(out or ""):
        return (f"ERROR: the phone refused to give {name} the permission ({out[:120]}). On a Samsung, switch on "
                "'Disable permission monitoring' in Developer options if it is there, and ask me again.")
    return (f"Done: the phone will keep trusting this PC, and {name} now has the permission it needs. One rule to "
            f"create in {name} on the phone — Trigger: Device Boot. Action: System Setting, type Global, key "
            "adb_wifi_enabled, value 1. Save it and allow the app to run in the background (battery: Unrestricted). "
            "After a restart, unlock the phone once; when it is on Wi-Fi I find it and reconnect by myself within a "
            "few minutes.")


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
    out = ph.run_task(ph.message_task(to.strip(), text.strip(), app or "whatsapp", send), "send" if send else "",
                      only={"Messages": "messages"}.get(name, name.lower()), to=to.strip())
    if send and out.startswith("Done on your phone"):
        from .. import undo
        undo.record(f"sent {to.strip()} a {name} message", "none", why="a sent message can't be unsent from here; "
                    "delete it for everyone in the app")
    return out


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
