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
missed: list[dict] = []             # what came in since you last asked (names and apps only, kept in memory)
_prev: dict | None = None           # charging / Wi-Fi / Bluetooth at the last look, for routines that start themselves
_call = "idle"
_jobs = threading.Lock()            # one scheduled message / self-starting routine at a time


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
    missed.extend({"app": m["app"], "from": m["from"], "t": time.time()} for m in fresh)
    del missed[:-200]
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
        if opt("self_repair", True) and (state["fails"] == 2 or time.time() - state.get("repair_at", 0) >= 300):
            state["repair_at"] = time.time()            # the phone may have restarted: try to restore the link myself

            def repair():
                if not p.rearm():
                    state.update(online=True, fails=0, told_lost=False)
                    tell("Your phone had restarted — I've restored the connection to it myself.", speak=False)
            _background(repair)
        try:                                            # a message that is due still gets its "couldn't send" report
            from . import phone_schedule
            if phone_schedule.due():
                _background(lambda: phone_schedule.run_due(lambda text: tell(text, speak=False), p=p))
        except Exception:
            pass
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
            target = int(opt("charged_alert", 80) or 0)
            if target and h.get("charging") and level is not None and level >= target:
                if not state.get("charged"):
                    state["charged"] = True
                    tell(f"Your phone has charged to {level} percent — you can unplug it.")
            elif not h.get("charging") or (level is not None and level < target - 5):
                state["charged"] = False
            _automatic(p, h)
        except Exception as e:
            print(f"[phone] health check failed: {e}")
    if opt("alerts", True):
        try:
            announce(new_messages(p))
        except Exception as e:
            print(f"[phone] couldn't read notifications: {e}")


def _background(job) -> None:
    def go():
        if not _jobs.acquire(blocking=False):
            return
        try:
            job()
        except Exception as e:
            print(f"[phone] {e}")
        finally:
            _jobs.release()
    threading.Thread(target=go, daemon=True, name="phone-job").start()


def _automatic(p: ph.Phone, h: dict, wait: bool = False) -> None:
    """Scheduled messages that are due, and routines whose time or event has come."""
    global _prev
    from . import phone_routines, phone_schedule
    cur = {"charging": bool(h.get("charging")), "battery": h.get("battery"), "wifi": h.get("wifi") or ""}
    if phone_routines.needs_bluetooth():
        try:
            cur["bluetooth"] = p.bluetooth_connected()
        except Exception:
            cur["bluetooth"] = (_prev or {}).get("bluetooth", [])
    names = phone_routines.due(_prev, cur)
    _prev = cur

    def work():
        for name in names:
            tell(f"📱 Starting '{name}' on your phone.", speak=False)
            tell(phone_routines.run(name, p), speak=False)
        phone_schedule.run_due(lambda text: tell(text, speak=False), p=p)
    if names or phone_schedule.due():
        work() if wait else _background(work)


def check_call(p: ph.Phone | None = None) -> str:
    """A quick look for an incoming call (every few seconds): says who is calling, once per call."""
    global _call
    p = p or ph.phone()
    now = p.call_state()
    if now == "ringing" and _call != "ringing":
        who = ""
        try:
            who = p.caller()
        except Exception:
            pass
        missed.append({"app": "Phone", "from": who or "someone", "t": time.time()})
        tell(f"📞 {who or 'Someone'} is calling your phone. Say 'answer the call' or 'decline the call'.",
             speak=bool(opt("alerts_speak", True)))
    _call = now
    return now


def missed_summary(clear: bool = True) -> str:
    """'On your phone: 3 WhatsApp messages (Karen 2, Sam), 1 call (Fritz).' — '' when nothing came in."""
    if not missed:
        return ""
    groups: dict[str, dict[str, int]] = {}
    for m in missed:
        groups.setdefault(m["app"], {}).setdefault(m["from"], 0)
        groups[m["app"]][m["from"]] += 1
    parts = []
    for app, who in groups.items():
        n = sum(who.values())
        kind = "call" if app == "Phone" else f"{app} message"
        parts.append(f"{n} {kind}{'s' if n != 1 else ''} (" + ", ".join(f"{k} {c}" if c > 1 else k for k, c in list(who.items())[:5]) + ")")
    if clear:
        missed.clear()
    return "On your phone: " + ", ".join(parts) + "."


def snapshot() -> dict:
    """What the dashboard's phone card shows."""
    from . import phone_routines
    c = ph.cfg()
    h = state["health"] or {}
    return {"configured": bool(str(c.get("address", "")).strip()), "name": c.get("name") or h.get("model") or "Phone",
            "online": bool(state["online"]), "checked": state["checked"], **{k: h.get(k) for k in (
                "battery", "charging", "temperature", "storage_free_gb", "storage_total_gb", "storage_used_pct",
                "memory_free_pct", "wifi", "signal")},
            "busy": ph._busy.locked(), "alerts": bool(opt("alerts", True)), "routines": list(phone_routines.load()),
            "scheduled": len(_scheduled()), "mirror": _mirroring(), "call": _call}


def _scheduled() -> list:
    try:
        from . import phone_schedule
        return phone_schedule.load()
    except Exception:
        return []


def _mirroring() -> bool:
    try:
        from . import phone_mirror
        return phone_mirror.running()
    except Exception:
        return False


def worker() -> None:
    time.sleep(20)                                      # let Nova finish starting
    last = 0.0
    while True:
        try:
            if opt("watch", True):
                if time.time() - last >= max(10, float(opt("check_seconds", 30))):
                    last = time.time()
                    tick()
                elif state["online"] and opt("call_alerts", True) and ph.phone().address():
                    check_call()
        except Exception as e:
            print(f"[phone] watcher: {e}")
        time.sleep(5)


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=worker, daemon=True, name="phone-watch").start()
