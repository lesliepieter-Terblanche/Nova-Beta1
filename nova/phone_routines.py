"""Saved phone routines: one phrase runs several steps on the phone ("driving mode" → do not disturb on, Bluetooth
on, open Maps, open Spotify). Kept in data/phone_routines.json; make and change them by voice.

A step is plain words. Simple ones (a switch, brightness / volume, open an app, press a key) run instantly; anything
else is done by reading the screen and tapping, like any other phone task — with the usual stop before sending,
paying, buying or deleting.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from . import phone as ph
from .config import resolve

DEFAULTS = {
    "Driving mode": ["do not disturb on", "bluetooth on", "media volume 70", "open maps"],
    "Bedtime": ["do not disturb on", "brightness 10", "media volume 0", "battery saver on"],
    "Good morning": ["do not disturb off", "battery saver off", "brightness 60", "ring volume 80"],
}


def _path():
    p = resolve("data/phone_routines.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict[str, list[str]]:
    p = _path()
    if not p.exists():
        return {k: list(v) for k, v in DEFAULTS.items()}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {str(k): [str(s) for s in v] for k, v in data.items() if isinstance(v, list)}
    except Exception:
        return {k: list(v) for k, v in DEFAULTS.items()}


def _write(data: dict) -> None:
    _path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def steps_of(text: str) -> list[str]:
    """'dnd on, open maps and then open spotify' → the separate steps."""
    parts = re.split(r"\s*(?:;|\n|,| and then | then |(?<!\w)and (?=(?:open|turn|switch|set|put|press|enable|disable)\b))\s*", text)
    return [p.strip(" .") for p in parts if p and p.strip(" .")]


def key_of(name: str, data: dict | None = None) -> str:
    data = load() if data is None else data
    n = norm(name)
    return next((k for k in data if norm(k) == n), "") or \
        next((k for k in data if norm(k) in (n + " mode", n.removesuffix(" mode"), n.removesuffix(" routine"))), "")


def save(name: str, steps: list[str]) -> str:
    data = load()
    old = key_of(name, data)
    if old:
        data.pop(old)
    data[name.strip().capitalize()] = steps
    _write(data)
    return name.strip().capitalize()


def delete(name: str) -> bool:
    data = load()
    k = key_of(name, data)
    if not k:
        return False
    data.pop(k)
    _write(data)
    t = triggers()
    if t.pop(k, None) is not None:
        _tpath().write_text(json.dumps(t, indent=2), encoding="utf-8")
    return True


LEAD = re.compile(r"^(?:hey |ok |please )?(?:nova[,!]? )?(?:please )?(?:(?:start|run|activate|do|launch|begin|switch to|"
                  r"go (?:in)?to|turn on|switch on|enable|set|put (?:my|the) phone (?:in|into|on)) )?(?:my |the )?", re.I)
TAIL = re.compile(r"(?: (?:routine|on (?:my|the) phone|please|now))*[.!]?$", re.I)


def find(text: str) -> str:
    """The routine this sentence is asking for ('driving mode', 'start my bedtime routine') — '' when it isn't one."""
    if len(text) > 60:
        return ""
    core = TAIL.sub("", LEAD.sub("", text.strip(), count=1))
    data = load()
    n = norm(core)
    return next((k for k in data if norm(k) == n), "") if n else ""


def run(name: str, p: ph.Phone | None = None) -> str:
    data = load()
    k = key_of(name, data)
    if not k:
        have = ", ".join(data) or "none yet"
        return f"I don't have a phone routine called '{name}'. Saved routines: {have}."
    p = p or ph.phone()
    err = p.connect()
    if err:
        return err
    done, failed = [], []
    for step in data[k]:
        try:
            out = ph.quick(step, p)
            if out is None:
                out = ph.run_task(step, p=p)
            ok = not re.match(r"^(I stopped|I need you|I didn't finish|Your phone is locked|I'm already busy|I can't)", out)
        except Exception as e:
            out, ok = str(e), False
        (done if ok else failed).append(step if ok else f"{step} ({out[:120]})")
    text = f"{k}: " + (f"did {len(done)} of {len(data[k])} steps" if failed else "all done") + \
        (f" — {', '.join(done)}." if done else ".")
    return text + (f" Not done: {'; '.join(failed)}." if failed else "")


# ── routines that start by themselves ─────────────────────
# data/phone_triggers.json: {"Bedtime": {"at": "22:00", "days": ["mon", …]}, "Driving mode": {"when": "bluetooth car"}}
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _tpath():
    p = resolve("data/phone_triggers.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def triggers() -> dict:
    try:
        return dict(json.loads(_tpath().read_text(encoding="utf-8")))
    except Exception:
        return {}


def parse_when(text: str) -> dict | None:
    """'charging', 'unplugged', 'bluetooth car', 'wifi HomeFibre', 'leaving wifi HomeFibre', 'battery below 20'."""
    t = re.sub(r"\s+", " ", text.strip().lower())
    t = re.sub(r"^(when|if|once|as soon as) (the phone |my phone |it |i )?", "", t)
    if re.match(r"^(is |starts? |i )?(charging|plugged in|on charge|charge)$", t):
        return {"kind": "charging"}
    if re.match(r"^(is |i )?(unplugged|unplug|off charge|stops? charging|not charging)$", t):
        return {"kind": "unplugged"}
    m = re.match(r"^battery (?:is |drops? |falls? |goes )?(?:below|under|less than|at|to) (\d{1,3}) ?%?$", t)
    if m:
        return {"kind": "battery_below", "value": int(m.group(1))}
    m = re.match(r"^(?:connect(?:s|ed)? to |on |joins? )?(bluetooth|wifi|wi-fi)(?: connects?(?: to)?| connected(?: to)?)? (.+)$", t)
    if m:
        return {"kind": "bluetooth" if m.group(1) == "bluetooth" else "wifi", "value": m.group(2).strip(" '\"")}
    m = re.match(r"^(?:leaving|leave|leaves|off|disconnect(?:s|ed)? from|away from) (?:wifi|wi-fi) (.+)$", t)
    if m:
        return {"kind": "wifi_left", "value": m.group(1).strip(" '\"")}
    return None


def set_trigger(name: str, at: str = "", when: str = "", days: str = "") -> str:
    """Give a routine a time and/or an event that starts it. Empty at + when = it only runs when asked."""
    k = key_of(name)
    if not k:
        return f"I don't have a phone routine called '{name}'."
    data = triggers()
    if not at.strip() and not when.strip():
        data.pop(k, None)
        _tpath().write_text(json.dumps(data, indent=2), encoding="utf-8")
        return f"'{k}' now only runs when you ask for it."
    t: dict = {}
    if at.strip():
        m = re.match(r"^(\d{1,2})[:h.]?(\d{2})?\s*(am|pm)?$", at.strip().lower())
        if not m or int(m.group(1)) > 23:
            return f"I didn't understand the time '{at}' — say it like 22:00."
        hour = int(m.group(1)) + (12 if m.group(3) == "pm" and int(m.group(1)) < 12 else 0)
        t["at"] = f"{hour:02d}:{int(m.group(2) or 0):02d}"
        chosen = [d[:3] for d in re.split(r"[\s,]+", days.lower()) if d[:3] in DAYS]
        if "weekday" in days.lower():
            chosen = DAYS[:5]
        elif "weekend" in days.lower():
            chosen = DAYS[5:]
        if chosen:
            t["days"] = chosen
    if when.strip():
        w = parse_when(when)
        if not w:
            return (f"I didn't understand '{when}'. I can start a routine when the phone is charging or unplugged, when "
                    "the battery drops below a level, or when it connects to a named Bluetooth device or Wi-Fi.")
        t["when"] = w
    data[k] = t
    _tpath().write_text(json.dumps(data, indent=2), encoding="utf-8")
    return f"'{k}' will now start by itself {describe(t)}."


def describe(t: dict) -> str:
    bits = []
    if t.get("at"):
        bits.append(f"at {t['at']}" + (f" on {', '.join(t['days'])}" if t.get("days") else " every day"))
    w = t.get("when") or {}
    if w:
        bits.append({"charging": "when the phone starts charging", "unplugged": "when the phone is unplugged",
                     "battery_below": f"when the battery drops below {w.get('value')}%",
                     "bluetooth": f"when Bluetooth connects to '{w.get('value')}'",
                     "wifi": f"when the phone joins the Wi-Fi '{w.get('value')}'",
                     "wifi_left": f"when the phone leaves the Wi-Fi '{w.get('value')}'"}.get(w.get("kind"), ""))
    return " and ".join(b for b in bits if b)


def needs_bluetooth() -> bool:
    return any((t.get("when") or {}).get("kind") == "bluetooth" for t in triggers().values())


def due(prev: dict | None, cur: dict, now: dt.datetime | None = None) -> list[str]:
    """Routines whose moment has come. prev / cur: {charging, battery, wifi, bluetooth: [names]} then and now."""
    now = now or dt.datetime.now()
    data, out, changed = triggers(), [], False
    for name, t in data.items():
        if not key_of(name):
            continue
        fire = False
        if t.get("at"):
            hh, mm = map(int, t["at"].split(":"))
            start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            today = DAYS[now.weekday()] in (t.get("days") or DAYS)
            if today and start <= now < start + dt.timedelta(minutes=20) and t.get("last") != now.date().isoformat():
                fire, t["last"], changed = True, now.date().isoformat(), True
        w = t.get("when") or {}
        if w and prev is not None:
            v = str(w.get("value", "")).lower()
            kind = w.get("kind")
            if kind == "charging":
                fire |= bool(cur.get("charging")) and not prev.get("charging")
            elif kind == "unplugged":
                fire |= bool(prev.get("charging")) and not cur.get("charging")
            elif kind == "battery_below" and cur.get("battery") is not None and prev.get("battery") is not None:
                fire |= cur["battery"] < int(w["value"]) <= prev["battery"]
            elif kind == "wifi":
                fire |= v in str(cur.get("wifi") or "").lower() and v not in str(prev.get("wifi") or "").lower()
            elif kind == "wifi_left":
                fire |= v in str(prev.get("wifi") or "").lower() and v not in str(cur.get("wifi") or "").lower()
            elif kind == "bluetooth":
                was = any(v in b.lower() for b in prev.get("bluetooth") or [])
                fire |= any(v in b.lower() for b in cur.get("bluetooth") or []) and not was
        if fire:
            out.append(name)
    if changed:
        _tpath().write_text(json.dumps(data, indent=2), encoding="utf-8")
    return out
