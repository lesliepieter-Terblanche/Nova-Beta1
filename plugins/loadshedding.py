"""Load-shedding (EskomSePush): today's stage and your area's schedule, plus a heads-up before the power goes off.

Setup (free): get a token at https://eskomsepush.gumroad.com/l/api (free tier, 50 checks a day), paste it in
Settings → API keys → EskomSePush token, then say "find my load-shedding area Sandton" and "use area <id>".
Nova then warns you (spoken + Telegram) 30 minutes before each slot. It checks at most every 3 hours, which stays
well inside the free allowance.
"""
from __future__ import annotations

import datetime as dt
import os
import threading
import time

import httpx

from nova import context
from nova.tools import register_group, tool

register_group("loadshedding", ["load shedding", "loadshedding", "load-shedding", "eskom", "power cut", "power outage",
                                "stage", "outage", "espush", "eskomsepush", "lights go off", "power off"])

API = "https://developer.sepush.co.za/business/2.0"
_cache: dict = {}


def _token() -> str:
    return (os.environ.get("ESP_TOKEN") or "").strip()


def _cfg() -> dict:
    return dict(((context.cfg or {}).get("loadshedding") or {}))


def _get(path: str, params: dict | None = None, ttl: float = 0) -> dict:
    key = (path, tuple(sorted((params or {}).items())))
    hit = _cache.get(key)
    if ttl and hit and time.time() - hit[0] < ttl:
        return hit[1]
    if not _token():
        raise RuntimeError("No EskomSePush token yet — add it in Settings → API keys (free at eskomsepush.gumroad.com).")
    r = httpx.get(API + path, params=params, headers={"Token": _token()}, timeout=20)
    if r.status_code == 403:
        raise RuntimeError("EskomSePush rejected the token — check it in Settings → API keys.")
    if r.status_code == 429:
        raise RuntimeError("Today's free EskomSePush checks are used up — it resets at midnight.")
    r.raise_for_status()
    data = r.json()
    _cache[key] = (time.time(), data)
    return data


def _when(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts).astimezone().replace(tzinfo=None)


def _fmt(d: dt.datetime) -> str:
    today = dt.date.today()
    day = "today" if d.date() == today else "tomorrow" if d.date() == today + dt.timedelta(days=1) else d.strftime("%A")
    return f"{day} {d:%H:%M}"


def national_stage() -> str:
    st = (_get("/status", ttl=3600).get("status") or {}).get("eskom") or {}
    stage = str(st.get("stage", "0"))
    nxt = [n for n in st.get("next_stages") or [] if n.get("stage_start_timestamp")]
    out = "No load-shedding nationally right now" if stage in ("0", "") else f"Eskom is on stage {stage}"
    if nxt:
        n = nxt[0]
        out += f"; stage {n.get('stage')} from {_fmt(_when(n['stage_start_timestamp']))}"
    return out + "."


def area_events(area_id: str) -> tuple[str, list[dict]]:
    d = _get("/area", {"id": area_id}, ttl=3 * 3600)
    name = (d.get("info") or {}).get("name") or area_id
    events = [{"start": _when(e["start"]), "end": _when(e["end"]), "note": e.get("note", "")}
              for e in d.get("events") or []]
    return name, [e for e in events if e["end"] > dt.datetime.now()]


@tool(group="loadshedding")
def loadshedding_status(area_id: str = "") -> str:
    """Current load-shedding stage and the next outages for your area.
    Args:
        area_id: EskomSePush area id; empty = the one saved in Settings
    """
    area_id = area_id or str(_cfg().get("area_id") or "")
    try:
        head = national_stage()
        if not area_id:
            return head + " Tell me your suburb ('find my load-shedding area Sandton') to get your own schedule."
        name, events = area_events(area_id)
    except Exception as e:
        return f"ERROR: {e}"
    try:
        from nova import cards
        cards.show("loadshedding", f"Load-shedding · {name}", {"national": head, "area": name, "events": [
            {"start": e["start"].isoformat(), "end": e["end"].isoformat(), "note": e.get("note", "")} for e in events[:8]]})
    except Exception:
        pass
    if not events:
        return f"{head} No outages scheduled for {name}."
    parts = [f"{_fmt(e['start'])} to {e['end']:%H:%M} ({e['note']})" if e["note"] else f"{_fmt(e['start'])} to {e['end']:%H:%M}"
             for e in events[:4]]
    return f"{head} {name}: " + "; ".join(parts) + "."


@tool(group="loadshedding")
def find_loadshedding_area(place: str) -> str:
    """Search EskomSePush for your load-shedding area (to get its id).
    Args:
        place: suburb or town, e.g. Sandton
    """
    try:
        areas = _get("/areas_search", {"text": place}).get("areas") or []
    except Exception as e:
        return f"ERROR: {e}"
    if not areas:
        return f"No load-shedding areas found for '{place}'."
    lines = [f"{a['name']} ({a.get('region', '')}) — id {a['id']}" for a in areas[:6]]
    return "Matching areas: " + "; ".join(lines) + ". Say 'use load-shedding area <id>' to save one."


@tool(group="loadshedding")
def set_loadshedding_area(area_id: str) -> str:
    """Save your load-shedding area so Nova can show your schedule and warn you before outages.
    Args:
        area_id: the id from find_loadshedding_area, e.g. eskde-10-sandtoncityofjohannesburggauteng
    """
    from nova import settings
    r = settings.apply({"values": {"loadshedding.area_id": area_id.strip()}})
    if r["errors"]:
        return f"ERROR: {r['errors']}"
    if context.cfg is not None:
        context.cfg.setdefault("loadshedding", {})["area_id"] = area_id.strip()
    _start_warner()
    return "Saved — I'll use that area and warn you before outages."


# ── heads-up before each outage ───────────────────────────
_warned: set = set()
_thread: threading.Thread | None = None


def check_and_warn(now: dt.datetime | None = None) -> str | None:
    """One check: if an outage starts within warn_minutes, say so once. Returns the message (for tests)."""
    c = _cfg()
    area_id = str(c.get("area_id") or "")
    if not (area_id and _token()):
        return None
    now = now or dt.datetime.now()
    mins = float(c.get("warn_minutes", 30))
    name, events = area_events(area_id)
    for e in events:
        key = (area_id, e["start"].isoformat())
        lead = (e["start"] - now).total_seconds() / 60
        if 0 <= lead <= mins and key not in _warned:
            _warned.add(key)
            msg = (f"Load-shedding in {int(round(lead))} minutes for {name}: {e['start']:%H:%M} to {e['end']:%H:%M}."
                   " Save your work and charge your phone.")
            context.push("⚡ " + msg, [])
            if context.announce:
                context.announce(msg)
            return msg
    return None


def _loop() -> None:
    while True:
        try:
            check_and_warn()
        except Exception as e:
            print(f"[loadshedding] {e}")
        time.sleep(300)          # looks every 5 min at the cached schedule; fetches at most every 3 h


def _start_warner() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    if _cfg().get("warn", True) and _cfg().get("area_id") and _token():
        _thread = threading.Thread(target=_loop, daemon=True, name="loadshedding")
        _thread.start()


_start_warner()
