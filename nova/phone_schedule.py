"""Messages to send from the phone later ("send Sam happy birthday at 7 tomorrow"). Kept in data/phone_schedule.json.

The phone must be unlocked when the time comes — Nova never enters your PIN. If it is locked she keeps trying for an
hour (phone.schedule_retry_minutes), tells you, and sends as soon as you unlock it within that time.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from . import phone as ph
from .config import resolve

FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def _path():
    p = resolve("data/phone_schedule.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> list[dict]:
    try:
        return list(json.loads(_path().read_text(encoding="utf-8")))
    except Exception:
        return []


def _write(items: list[dict]) -> None:
    _path().write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def parse(when: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """'2026-10-03 07:00', 'tomorrow 07:00', '07:00' (today, or tomorrow when that time has passed), 'in 20 minutes'."""
    now = now or dt.datetime.now()
    w = when.strip().lower().replace(" at ", " ")
    for fmt in FORMATS:
        try:
            return dt.datetime.strptime(w.upper().replace("T", " ") if "t" in w and "-" in w else w, fmt.replace("T", " "))
        except ValueError:
            pass
    m = re.match(r"^in (\d+) ?(min|minute|minutes|hour|hours|h|m)$", w)
    if m:
        n = int(m.group(1))
        return now + (dt.timedelta(hours=n) if m.group(2).startswith("h") else dt.timedelta(minutes=n))
    m = re.match(r"^(today|tomorrow|tonight)?\s*(\d{1,2})(?:[:h.](\d{2}))?\s*(am|pm)?$", w)
    if not m:
        return None
    hour, minute = int(m.group(2)), int(m.group(3) or 0)
    if m.group(4) == "pm" and hour < 12:
        hour += 12
    if m.group(4) == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if m.group(1) == "tomorrow" or (m.group(1) is None and t <= now):
        t += dt.timedelta(days=1)
    return t


def add(to: str, text: str, when: dt.datetime, app: str = "whatsapp") -> dict:
    items = load()
    item = {"id": max([i.get("id", 0) for i in items] + [0]) + 1, "to": to, "text": text, "app": app or "whatsapp",
            "at": when.strftime("%Y-%m-%d %H:%M")}
    items.append(item)
    _write(items)
    return item


def cancel(which: str) -> list[dict]:
    """Cancel by number or by the person's name. Returns what was removed."""
    items = load()
    w = which.lower().strip()
    gone = [i for i in items if str(i.get("id")) == w or (w and w in i.get("to", "").lower()) or w in ("all", "everything")]
    if gone:
        _write([i for i in items if i not in gone])
    return gone


def due(now: dt.datetime | None = None) -> list[dict]:
    now = now or dt.datetime.now()
    return [i for i in load() if dt.datetime.strptime(i["at"], "%Y-%m-%d %H:%M") <= now]


def run_due(tell, now: dt.datetime | None = None, p: ph.Phone | None = None) -> None:
    """Send what is due. `tell(text)` reports each result."""
    now = now or dt.datetime.now()
    retry = float(ph.cfg().get("schedule_retry_minutes", 60))
    for item in due(now):
        name = ph.MESSAGE_APPS.get(item["app"].lower().strip(), item["app"])
        out = ph.run_task(ph.message_task(item["to"], item["text"], item["app"], True), "send", p=p,
                          only={"Messages": "messages"}.get(name, name.lower()), to=item["to"])
        late = (now - dt.datetime.strptime(item["at"], "%Y-%m-%d %H:%M")).total_seconds() / 60
        waiting = re.match(r"^(Your phone is locked|I'm already busy|I can't reach|The phone isn't)", out)
        if waiting and late < retry:
            if not item.get("told"):
                item["told"] = True
                _write([item if i.get("id") == item["id"] else i for i in load()])
                tell(f"The message to {item['to']} is due, but I can't send it yet: {out} I'll keep trying for "
                     f"{int(retry)} minutes.")
            continue
        _write([i for i in load() if i.get("id") != item["id"]])
        if out.startswith("Done on your phone"):
            tell(f"Sent your scheduled {name} message to {item['to']}: \"{item['text'][:120]}\".")
        else:
            tell(f"I could NOT send your scheduled message to {item['to']} (\"{item['text'][:80]}\"): {out}")
