"""Keeps an eye on the phone in the background: reconnects when the link drops, keeps the dashboard's health card
fresh, and tells you when a message or missed call arrives — so the phone can stay in your pocket.

Alerts are read straight from the phone's notifications and shown / spoken by Nova herself: nothing is sent to an
AI model and nothing is stored in the brain. Switch them off under Settings → Phone.
"""
from __future__ import annotations

import threading
import time

from . import context
from . import phone as ph

state: dict = {"online": None, "health": None, "checked": 0.0, "fails": 0, "told_lost": False, "low": False}
_seen: set | None = None            # notifications already there / already announced
_health_at = 0.0
_started = False
last_message: dict = {}             # the newest message that came in — "reply to that" answers this one


def opt(name: str, default):
    return ph.cfg().get(name, default)


def tell(text: str, speak: bool = True) -> None:
    """On the dashboard (banner) and out loud — not to Telegram: that would land on the very phone we're watching."""
    try:
        if context.store is not None:
            context.store.log("notice", "phone", text[:200], text, turn=0)
    except Exception:
        pass
    if speak and context.announce:
        try:
            context.announce(text)
        except Exception:
            pass
    print(f"[phone] {text}")


def new_messages(p: ph.Phone) -> list[dict]:
    """Messages that weren't there at the last look. The first look only takes stock."""
    global _seen
    now = p.messages()
    keys = {(m["app"], m["from"], m["text"]) for m in now}
    if _seen is None:
        _seen = keys
        return []
    fresh = [m for m in now if (m["app"], m["from"], m["text"]) not in _seen]
    me = str(getattr(getattr(context.cfg, "assistant", None), "name", "") or "").lower()
    if me:                                              # Nova's own Telegram messages landing on the phone: not news
        fresh = [m for m in fresh if not (m["app"] == "Telegram" and me in m["from"].lower())]
    _seen = keys
    allowed = [str(a).lower() for a in (opt("alert_apps", []) or [])]
    if allowed:
        fresh = [m for m in fresh if any(a in m["app"].lower() for a in allowed)]
    return fresh


def announce(fresh: list[dict]) -> str:
    if not fresh:
        return ""
    last_message.clear()
    last_message.update(fresh[-1])
    if len(fresh) <= 2:
        text = " ".join(f"📱 {m['app']} from {m['from']}: {m['text'][:220]}".rstrip(": ") + ("" if m["text"][-1:] in ".!?" else ".")
                        for m in fresh)
    else:
        who: dict[str, int] = {}
        for m in fresh:
            who[m["from"]] = who.get(m["from"], 0) + 1
        text = f"📱 {len(fresh)} new messages on your phone: " + ", ".join(
            f"{n} ({c})" if c > 1 else n for n, c in list(who.items())[:6]) + "."
    tell(text, speak=bool(opt("alerts_speak", True)))
    return text


def tick(p: ph.Phone | None = None) -> None:
    """One look at the phone."""
    global _health_at
    p = p or ph.phone()
    if not p.address():
        return                                          # not set up: nothing to watch
    state["checked"] = time.time()
    try:
        err = p.connect()
    except Exception as e:
        err = str(e)
    if err:
        state["fails"] += 1
        state["online"] = False
        if state["fails"] == 3 and not state["told_lost"]:
            state["told_lost"] = True
            tell("I can't reach your phone. I'll keep trying — check that Tailscale is on on the phone. If the phone "
                 "was restarted, plug it into the PC and say 'set up my phone'.", speak=False)
        return
    if state["told_lost"]:
        tell("Your phone is connected again.", speak=False)
    state.update(online=True, fails=0, told_lost=False)
    if time.time() - _health_at >= float(opt("health_seconds", 60)):
        _health_at = time.time()
        try:
            h = p.health()
            state["health"] = h
            level = h.get("battery")
            if level is not None and level <= int(opt("low_battery", 15)) and not h.get("charging"):
                if not state["low"]:
                    state["low"] = True
                    tell(f"Your phone's battery is at {level} percent — time to charge it.")
            elif level is not None and (level > int(opt("low_battery", 15)) + 5 or h.get("charging")):
                state["low"] = False
        except Exception as e:
            print(f"[phone] health check failed: {e}")
    if opt("alerts", True):
        try:
            announce(new_messages(p))
        except Exception as e:
            print(f"[phone] couldn't read notifications: {e}")


def snapshot() -> dict:
    """What the dashboard's phone card shows."""
    from . import phone_routines
    c = ph.cfg()
    h = state["health"] or {}
    return {"configured": bool(str(c.get("address", "")).strip()), "name": c.get("name") or h.get("model") or "Phone",
            "online": bool(state["online"]), "checked": state["checked"], **{k: h.get(k) for k in (
                "battery", "charging", "temperature", "storage_free_gb", "storage_total_gb", "storage_used_pct",
                "memory_free_pct", "wifi", "signal")},
            "busy": ph._busy.locked(), "alerts": bool(opt("alerts", True)), "routines": list(phone_routines.load())}


def worker() -> None:
    time.sleep(20)                                      # let Nova finish starting
    while True:
        try:
            if opt("watch", True):
                tick()
        except Exception as e:
            print(f"[phone] watcher: {e}")
        time.sleep(max(10, float(opt("check_seconds", 30))))


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=worker, daemon=True, name="phone-watch").start()
