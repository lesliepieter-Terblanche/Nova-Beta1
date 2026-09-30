"""Screen time from ActivityWatch (activitywatch.net) — a free app that logs which program / website is in front,
privately, on your own PC. Nova reads its local API to answer "where did my day go?", show it on the Focus screen,
and give a gentle nudge when you drift into YouTube & co. while a Focus task is waiting.

Nothing is uploaded; ActivityWatch keeps its data on the PC and Nova only reads http://localhost:5600.
"""
from __future__ import annotations

import datetime as dt
import threading
import time

import httpx

from . import context

DEFAULT_DISTRACTIONS = ["youtube", "facebook", "instagram", "tiktok", "netflix", "reddit", "twitter", "x.com",
                        "showmax", "twitch", "9gag", "steam"]
FRIENDLY = {"chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox", "explorer.exe": "File Explorer",
            "winword.exe": "Word", "excel.exe": "Excel", "powerpnt.exe": "PowerPoint", "outlook.exe": "Outlook",
            "olk.exe": "Outlook", "ms-teams.exe": "Teams", "teams.exe": "Teams", "code.exe": "VS Code",
            "whatsapp.exe": "WhatsApp", "telegram.exe": "Telegram", "spotify.exe": "Spotify",
            "capcut.exe": "CapCut", "windowsterminal.exe": "Terminal", "zoom.exe": "Zoom"}


def cfg() -> dict:
    return dict(((context.cfg or {}).get("activitywatch") or {}))


def base() -> str:
    return str(cfg().get("url", "http://localhost:5600")).rstrip("/")


def _app(name: str) -> str:
    n = (name or "").strip()
    return FRIENDLY.get(n.lower(), n.removesuffix(".exe").removesuffix(".EXE") or "Unknown")


def _fmt(sec: float) -> str:
    m = int(round(sec / 60))
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


# ── talking to ActivityWatch ──────────────────────────────
def running() -> bool:
    try:
        return httpx.get(f"{base()}/api/0/info", timeout=2).status_code == 200
    except Exception:
        return False


def query(lines: list[str], start: dt.datetime, end: dt.datetime) -> list:
    period = f"{start.astimezone().isoformat()}/{end.astimezone().isoformat()}"
    r = httpx.post(f"{base()}/api/0/query/", json={"timeperiods": [period], "query": lines}, timeout=15)
    r.raise_for_status()
    out = r.json()
    return out[0] if out else []


WINDOW_QUERY = [
    'afk = flood(query_bucket(find_bucket("aw-watcher-afk_")));',
    'win = flood(query_bucket(find_bucket("aw-watcher-window_")));',
    'win = filter_period_intersect(win, filter_keyvals(afk, "status", ["not-afk"]));',
    'RETURN = sort_by_duration(merge_events_by_keys(win, ["app", "title"]));',
]
WEB_QUERY = [
    'web = flood(query_bucket(find_bucket("aw-watcher-web")));',
    'RETURN = sort_by_duration(merge_events_by_keys(web, ["url", "title"]));',
]


def events(start: dt.datetime, end: dt.datetime) -> list[dict]:
    """[{app, title, seconds}] of active (not away) time, longest first."""
    rows = query(WINDOW_QUERY, start, end)
    return [{"app": _app(e["data"].get("app", "")), "title": e["data"].get("title", ""),
             "seconds": float(e.get("duration", 0))} for e in rows if e.get("duration", 0) > 0]


def web(start: dt.datetime, end: dt.datetime) -> list[dict]:
    """Browser tabs, if the ActivityWatch browser extension is installed."""
    try:
        rows = query(WEB_QUERY, start, end)
    except Exception:
        return []
    return [{"url": e["data"].get("url", ""), "title": e["data"].get("title", ""),
             "seconds": float(e.get("duration", 0))} for e in rows if e.get("duration", 0) > 0]


# ── making sense of it ────────────────────────────────────
def period(name: str = "today", now: dt.datetime | None = None) -> tuple[dt.datetime, dt.datetime, str]:
    now = now or dt.datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    n = (name or "today").lower().strip()
    if n == "yesterday":
        return midnight - dt.timedelta(days=1), midnight, "yesterday"
    if n in ("week", "this week", "7 days", "last 7 days"):
        return midnight - dt.timedelta(days=6), now, "the last 7 days"
    if n.startswith("last ") and n.split()[1].isdigit():
        mins = int(n.split()[1]) * (60 if "hour" in n else 1)
        return now - dt.timedelta(minutes=mins), now, n
    if n in ("morning", "this morning"):
        return midnight, now.replace(hour=12, minute=0) if now.hour >= 12 else now, "this morning"
    return midnight, now, "today"


def distractions() -> list[str]:
    return [str(d).lower() for d in (cfg().get("distractions") or DEFAULT_DISTRACTIONS)]


def is_distraction(e: dict, words: list[str] | None = None) -> str:
    """The distraction word an event matches ('' if none)."""
    text = f"{e.get('app', '')} {e.get('title', '')} {e.get('url', '')}".lower()
    return next((w for w in (words or distractions()) if w in text), "")


def summary(name: str = "today", now: dt.datetime | None = None) -> dict:
    start, end, label = period(name, now)
    ev = events(start, end)
    apps: dict[str, float] = {}
    for e in ev:
        apps[e["app"]] = apps.get(e["app"], 0) + e["seconds"]
    words = distractions()
    drift: dict[str, float] = {}
    for e in ev:
        w = is_distraction(e, words)
        if w:
            drift[w] = drift.get(w, 0) + e["seconds"]
    total = sum(apps.values())
    top_apps = sorted(apps.items(), key=lambda kv: -kv[1])[:8]
    return {"label": label, "total": total, "total_text": _fmt(total),
            "apps": [{"app": a, "seconds": s, "text": _fmt(s), "share": round(100 * s / total) if total else 0}
                     for a, s in top_apps],
            "titles": [{"app": e["app"], "title": e["title"][:90], "text": _fmt(e["seconds"])} for e in ev[:8]],
            "distracted": sum(drift.values()), "distracted_text": _fmt(sum(drift.values())),
            "drift": [{"what": w, "text": _fmt(s)} for w, s in sorted(drift.items(), key=lambda kv: -kv[1])]}


def as_text(s: dict) -> str:
    if not s["total"]:
        return f"ActivityWatch has no activity for {s['label']} yet."
    lines = [f"Active screen time {s['label']}: {s['total_text']}."]
    lines += [f"• {a['app']}: {a['text']} ({a['share']}%)" for a in s["apps"][:6]]
    if s["distracted"] >= 60:
        lines.append("Drift: " + ", ".join(f"{d['what']} {d['text']}" for d in s["drift"][:4]) + ".")
    return "\n".join(lines)


def time_on(what: str, name: str = "today", now: dt.datetime | None = None) -> str:
    start, end, label = period(name, now)
    w = what.lower().strip()
    rows = [e for e in events(start, end) if w in f"{e['app']} {e['title']}".lower()]
    rows += [{"app": "browser", **e} for e in web(start, end) if w in f"{e['url']} {e['title']}".lower()]
    sec = max(sum(e["seconds"] for e in rows if e["app"] != "browser"),
              sum(e["seconds"] for e in rows if e["app"] == "browser"))
    if not sec:
        return f"No time on {what} {label}."
    top = max(rows, key=lambda e: e["seconds"])
    return f"{_fmt(sec)} on {what} {label}" + (f" (mostly “{top['title'][:70]}”)." if top.get("title") else ".")


# ── gentle nudges ─────────────────────────────────────────
_last_nudge = 0.0
_thread: threading.Thread | None = None


def _hm(s: str) -> dt.time:
    h, m = (str(s) + ":00").split(":")[:2]
    return dt.time(int(h), int(m))


def in_work_hours(at: dt.datetime) -> bool:
    c = cfg()
    days = c.get("work_days", [0, 1, 2, 3, 4])
    return at.weekday() in days and _hm(c.get("work_start", "08:00")) <= at.time() < _hm(c.get("work_end", "17:00"))


def check_drift(at: dt.datetime | None = None, clock: float | None = None) -> str | None:
    """If you've drifted for a while during work hours with a Focus task waiting, a kind nudge (once per window)."""
    global _last_nudge
    c = cfg()
    at = at or dt.datetime.now()
    clock = time.time() if clock is None else clock
    if not (c.get("enabled", True) and c.get("nudges", True)) or not in_work_hours(at):
        return None
    if clock - _last_nudge < 60 * float(c.get("nudge_every_minutes", 45)):
        return None
    try:
        from . import wellbeing
        if wellbeing.is_night(at):
            return None
    except Exception:
        pass
    from . import focus
    now_task = (focus.state() or {}).get("now")
    if not now_task:
        return None
    window = float(c.get("nudge_after_minutes", 15))
    rows = events(at - dt.timedelta(minutes=window * 2), at)
    drift: dict[str, float] = {}
    for e in rows:
        w = is_distraction(e)
        if w:
            drift[w] = drift.get(w, 0) + e["seconds"]
    if not drift or sum(drift.values()) < window * 60:
        return None
    _last_nudge = clock
    where = max(drift, key=drift.get)
    msg = (f"Hey — about {_fmt(sum(drift.values()))} on {where.title()} just now. "
           f"Your now-task is “{now_task['text']}”. Want to go back to it, or park it for later?")
    if context.store:
        context.store.log("focus", "activity", f"🧭 Drift nudge: {where}", turn=0)
    if context.announce:
        context.announce(msg)
    return msg


def _loop() -> None:
    while True:
        try:
            if running():
                check_drift()
        except Exception as e:
            print(f"[activity] {e}")
        time.sleep(300)


def start() -> None:
    global _thread
    if cfg().get("enabled", True) and not (_thread and _thread.is_alive()):
        _thread = threading.Thread(target=_loop, daemon=True, name="activitywatch")
        _thread.start()
