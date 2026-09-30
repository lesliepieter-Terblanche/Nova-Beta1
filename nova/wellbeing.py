"""Wellbeing: rhythm, energy and routines — private, on this PC only.

  Check-ins   energy, mood (1-5), hours slept and an optional note, once a day. Stored ENCRYPTED in nova.db with
              your backup passphrase. Never sent to any AI model, never part of the 2nd brain, never in answers.
  Heads-ups   patterns YOU switch on (short sleep, late nights, a burst of new projects, high energy on little
              sleep, several heavy days). A gentle note with your own plan — never a diagnosis.
  Night       after your wind-down time, sending email/invites/submissions is held until morning for a fresh look,
              and anything that buys something waits 24 hours (if switched on).
  Anchors     the few things that keep you steady (e.g. "08:00 Morning routine | Water; Medication; One thing"),
              reminded gently, ticked off as wins.
  Summary     a monthly page of sleep, energy and mood to take to your doctor (only if you choose to share it).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import threading
import time

from . import context

_SCHEMA = """CREATE TABLE IF NOT EXISTS wb_meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS wb_checkins(day TEXT PRIMARY KEY, ts TEXT, blob BLOB);
CREATE TABLE IF NOT EXISTS wb_anchor_log(day TEXT, name TEXT, ts TEXT, PRIMARY KEY(day, name));
CREATE TABLE IF NOT EXISTS wb_held(id INTEGER PRIMARY KEY, tool TEXT, args TEXT, label TEXT, created TEXT, due TEXT,
  status TEXT DEFAULT 'held', reason TEXT, told INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS wb_seen(k TEXT PRIMARY KEY, ts TEXT);"""

SEND_TOOLS = {"gmail_send", "gmail_reply", "calendar_invite", "browser_submit"}
SEND_WORDS = re.compile(r"(^|_)(send|reply|post|publish|invite|submit|tweet|message)(_|$)", re.I)
BUY_WORDS = re.compile(r"\b(buy|order|purchase|checkout|check out|pay|payment|place order|add to cart)\b", re.I)
DEFAULT_ANCHORS = ["07:30 Morning routine | Glass of water; Medication (if any); Pick today's one thing",
                   "12:30 Lunch — step away from the screen",
                   "22:30 Wind down | Dim the screens; Write tomorrow's one thing; Bed by 23:15"]


def cfg() -> dict:
    return dict(((context.cfg or {}).get("wellbeing") or {}))


def _sig_cfg() -> dict:
    return dict(cfg().get("signals") or {})


def _db():
    s = context.store
    with s.lock:
        s.db.executescript(_SCHEMA)
    return s


def _today() -> str:
    return dt.date.today().isoformat()


# ── encryption (same passphrase as the backups) ───────────
_fernet = {"f": None}


def _cipher():
    if _fernet["f"] is None:
        from cryptography.fernet import Fernet

        from .dreaming import _key, passphrase
        s = _db()
        with s.lock:
            r = s.db.execute("SELECT v FROM wb_meta WHERE k='salt'").fetchone()
            if not r:
                salt = os.urandom(16).hex()
                s.db.execute("INSERT INTO wb_meta(k,v) VALUES('salt',?)", (salt,))
                s.db.commit()
            else:
                salt = r["v"]
        pw, _ = passphrase(create=True)
        _fernet["f"] = Fernet(_key(pw, bytes.fromhex(salt)))
    return _fernet["f"]


# ── check-ins ─────────────────────────────────────────────
def checkin(energy: int | None = None, mood: int | None = None, sleep: float | None = None, note: str = "",
            day: str | None = None) -> dict:
    def clamp(v, lo, hi):
        return None if v in (None, "") else max(lo, min(hi, type(lo)(v)))
    rec = {"energy": clamp(energy, 1, 5), "mood": clamp(mood, 1, 5), "sleep": clamp(sleep, 0.0, 16.0),
           "note": (note or "").strip()[:1000]}
    day = day or _today()
    old = checkins(days=1, until=day).get(day) or {}
    rec = {k: (v if v not in (None, "") else old.get(k)) for k, v in rec.items()}
    s = _db()
    blob = _cipher().encrypt(json.dumps(rec).encode())
    with s.lock:
        s.db.execute("INSERT OR REPLACE INTO wb_checkins(day, ts, blob) VALUES(?,?,?)",
                     (day, dt.datetime.now().isoformat(timespec="seconds"), blob))
        s.db.commit()
    s.log("track", "wellbeing", "🌿 Checked in", "", turn=0)          # no values in the activity log
    return rec


def checkins(days: int = 60, until: str | None = None) -> dict[str, dict]:
    until = until or _today()
    since = (dt.date.fromisoformat(until) - dt.timedelta(days=days - 1)).isoformat()
    s = _db()
    with s.lock:
        rows = s.db.execute("SELECT day, blob FROM wb_checkins WHERE day>=? AND day<=? ORDER BY day", (since, until)).fetchall()
    out = {}
    for r in rows:
        try:
            out[r["day"]] = json.loads(_cipher().decrypt(r["blob"]))
        except Exception:
            out[r["day"]] = {"error": "can't read (passphrase changed?)"}
    return out


# ── heads-ups (patterns you chose) ────────────────────────
SIGNALS = {
    "short_sleep": "Short sleep",
    "late_nights": "Late nights",
    "project_burst": "A burst of new projects",
    "energy_up": "High energy on little sleep",
    "low_run": "A few heavy days",
}


def signals(today: dt.date | None = None) -> list[dict]:
    """Which of YOUR chosen patterns show up in the last few days. Local only."""
    sc = _sig_cfg()
    today = today or dt.date.today()
    ck = checkins(days=7, until=today.isoformat())
    last3 = [(today - dt.timedelta(days=i)).isoformat() for i in range(3)]
    out = []
    if sc.get("short_sleep", True):
        lim = float(sc.get("short_sleep_hours", 6))
        short = [d for d in last3 if ck.get(d, {}).get("sleep") is not None and ck[d]["sleep"] < lim]
        if len(short) >= 2:
            out.append({"key": "short_sleep", "text": f"under {lim:g} hours of sleep on {len(short)} of the last 3 nights"})
    if sc.get("late_nights", True):
        s = context.store
        nights = set()
        with s.lock:
            for r in s.db.execute("SELECT ts FROM activity WHERE kind='user' AND turn=id AND session NOT LIKE 'mission:%' "
                                  "AND ts>=?", ((today - dt.timedelta(days=3)).isoformat(),)):
                t = dt.datetime.fromisoformat(r["ts"])
                if 0 <= t.hour < 5:
                    nights.add(t.date())
        if len(nights) >= 2:
            out.append({"key": "late_nights", "text": f"busy with Nova after midnight on {len(nights)} of the last 3 nights"})
    if sc.get("project_burst", True):
        n = int(sc.get("burst_count", 4))
        since = (today - dt.timedelta(days=3)).isoformat()
        s = context.store
        with s.lock:
            proj = s.db.execute("SELECT COUNT(*) FROM memories WHERE kind='project' AND created>=?", (since,)).fetchone()[0]
            try:
                mis = s.db.execute("SELECT COUNT(*) FROM missions WHERE created>=?", (since,)).fetchone()[0]
            except Exception:
                mis = 0
        if proj + mis >= n:
            out.append({"key": "project_burst", "text": f"{proj + mis} new projects or missions in 3 days"})
    vals = [ck[d] for d in sorted(ck)[-3:] if d in ck]
    if sc.get("energy_up", True) and len(vals) >= 2:
        en = [v["energy"] for v in vals if v.get("energy")]
        sl = [v["sleep"] for v in vals if v.get("sleep") is not None]
        if en and sl and sum(en) / len(en) >= 4.3 and sum(sl) / len(sl) < 6.5:
            out.append({"key": "energy_up", "text": "high energy while sleeping less"})
    if sc.get("low_run", True):
        run = 0
        for i in range(7):
            m = ck.get((today - dt.timedelta(days=i)).isoformat(), {}).get("mood")
            if m is not None and m <= 2:
                run += 1
            elif i > 0 or m is not None:
                break
        if run >= 3:
            out.append({"key": "low_run", "text": f"{run} heavy days in a row"})
    return out


def headsup(today: dt.date | None = None) -> dict | None:
    sig = signals(today)
    if not sig:
        return None
    day = (today or dt.date.today()).isoformat()
    s = _db()
    with s.lock:
        dismissed = s.db.execute("SELECT 1 FROM wb_seen WHERE k=?", (f"dismiss:{day}",)).fetchone()
    plan = (cfg().get("plan") or "").strip() or ("Maybe protect tonight's sleep, keep tomorrow light, and check in "
                                                 "with someone you trust or your doctor if it keeps up.")
    return {"signals": sig, "dismissed": bool(dismissed), "plan": plan,
            "text": "Just noticing: " + "; ".join(x["text"] for x in sig) + ". It's a pattern, not a verdict."}


def dismiss_headsup() -> None:
    s = _db()
    with s.lock:
        s.db.execute("INSERT OR REPLACE INTO wb_seen(k, ts) VALUES(?,?)", (f"dismiss:{_today()}", _today()))
        s.db.commit()


# ── night & buying guardrails ─────────────────────────────
def _hm(s: str, default: str) -> dt.time:
    try:
        h, m = str(s or default).split(":")
        return dt.time(int(h), int(m))
    except Exception:
        h, m = default.split(":")
        return dt.time(int(h), int(m))


def is_night(at: dt.datetime | None = None) -> bool:
    n = dict(cfg().get("night") or {})
    if not n.get("enabled", True):
        return False
    at = at or dt.datetime.now()
    a, b = _hm(n.get("from"), "23:00"), _hm(n.get("to"), "06:30")
    t = at.time()
    return (t >= a or t < b) if a > b else (a <= t < b)


def release_time(at: dt.datetime | None = None) -> dt.datetime:
    at = at or dt.datetime.now()
    r = _hm((cfg().get("night") or {}).get("release_at"), "08:00")
    day = at.date() if at.time() < r else at.date() + dt.timedelta(days=1)
    return dt.datetime.combine(day, r)


def should_hold(tool_name: str, args: dict, at: dt.datetime | None = None) -> tuple[str, dt.datetime] | None:
    """(reason, when it may go) if this action should wait, else None."""
    if not cfg().get("enabled", True):
        return None
    text = json.dumps(args, ensure_ascii=False)
    buying = bool(BUY_WORDS.search(tool_name.replace("_", " ")) or (tool_name == "browser_submit" and BUY_WORDS.search(text)))
    at = at or dt.datetime.now()
    if buying and cfg().get("buy_pause", True):
        return "24-hour pause on buying", at + dt.timedelta(hours=24)
    sending = tool_name in SEND_TOOLS or bool(SEND_WORDS.search(tool_name))
    if sending and (cfg().get("night") or {}).get("hold_sends", True) and is_night(at):
        return "it's late — a fresh look in the morning", release_time(at)
    return None


def hold(tool_name: str, args: dict, reason: str, due: dt.datetime) -> int:
    s = _db()
    label = tool_name.replace("_", " ")
    for k in ("subject", "title", "to", "what_it_does"):
        if args.get(k):
            label += f": {str(args[k])[:80]}"
            break
    with s.lock:
        hid = s.db.execute("INSERT INTO wb_held(tool,args,label,created,due,reason) VALUES(?,?,?,?,?,?)",
                           (tool_name, json.dumps(args, ensure_ascii=False), label,
                            dt.datetime.now().isoformat(timespec="seconds"), due.isoformat(timespec="minutes"),
                            reason)).lastrowid
        s.db.commit()
    s.log("track", "wellbeing", f"⏸ Held until {due:%a %H:%M}: {label}", reason, turn=0)
    return hid


def held(status: str = "held") -> list[dict]:
    s = _db()
    with s.lock:
        return [dict(r) for r in s.db.execute("SELECT * FROM wb_held WHERE status=? ORDER BY id", (status,))]


def release(hid: int, action: str = "send") -> str:
    """Run (action='send') or drop a held action. Running needs you to have said so — the dashboard button or 'yes'."""
    s = _db()
    with s.lock:
        r = s.db.execute("SELECT * FROM wb_held WHERE id=? AND status='held'", (int(hid),)).fetchone()
    if not r:
        return "Nothing held with that number."
    if action == "drop":
        with s.lock:
            s.db.execute("UPDATE wb_held SET status='dropped' WHERE id=?", (hid,))
            s.db.commit()
        return f"Dropped: {r['label']}."
    from .tools import REGISTRY
    t = REGISTRY.get(r["tool"])
    if not t:
        return f"ERROR: {r['tool']} isn't available any more."
    result = t.run(json.loads(r["args"]))
    with s.lock:
        s.db.execute("UPDATE wb_held SET status='sent' WHERE id=?", (hid,))
        s.db.commit()
    return str(result)


# ── routine anchors ───────────────────────────────────────
def anchors() -> list[dict]:
    raw = cfg().get("anchors")
    raw = DEFAULT_ANCHORS if raw is None else raw
    out = []
    for line in raw or []:
        m = re.match(r"\s*(\d{1,2}:\d{2})\s+(.+)", str(line))
        if not m:
            continue
        name, _, steps = m.group(2).partition("|")
        out.append({"at": m.group(1).zfill(5), "name": name.strip(),
                    "steps": [x.strip() for x in steps.split(";") if x.strip()]})
    return sorted(out, key=lambda a: a["at"])


def anchors_today() -> list[dict]:
    s = _db()
    with s.lock:
        done = {r["name"]: r["ts"] for r in s.db.execute("SELECT name, ts FROM wb_anchor_log WHERE day=?", (_today(),))}
    return [{**a, "done": a["name"] in done, "done_at": done.get(a["name"])} for a in anchors()]


def tick_anchor(name: str, done: bool = True) -> None:
    s = _db()
    with s.lock:
        if done:
            s.db.execute("INSERT OR REPLACE INTO wb_anchor_log(day,name,ts) VALUES(?,?,?)",
                         (_today(), name, dt.datetime.now().isoformat(timespec="seconds")))
        else:
            s.db.execute("DELETE FROM wb_anchor_log WHERE day=? AND name=?", (_today(), name))
        s.db.commit()
    if done:
        s.log("track", "wellbeing", f"🌿 {name} ✓", "", turn=0)


# ── low energy & style ────────────────────────────────────
def low_energy() -> bool:
    s = _db()
    with s.lock:
        r = s.db.execute("SELECT v FROM wb_meta WHERE k=?", (f"low:{_today()}",)).fetchone()
    if r:
        return r["v"] == "1"
    if cfg().get("low_energy_auto", True):
        today = checkins(days=1).get(_today()) or {}
        return bool(today.get("energy") and today["energy"] <= 2)
    return False


def set_low_energy(on: bool) -> None:
    s = _db()
    with s.lock:
        s.db.execute("INSERT OR REPLACE INTO wb_meta(k,v) VALUES(?,?)", (f"low:{_today()}", "1" if on else "0"))
        s.db.commit()


def style_hint() -> str:
    """Added to Nova's instructions on low-energy days / at night. Says nothing about health."""
    if not (context.store and cfg().get("enabled", True)):
        return ""
    try:
        bits = []
        if low_energy():
            bits.append("Today, keep every reply extra short and gentle, and suggest at most one small next step.")
        if is_night():
            bits.append("It's late in the evening for the user: be calm and brief, and don't suggest starting new projects.")
        return (" " + " ".join(bits)) if bits else ""
    except Exception:
        return ""


def state() -> dict:
    today = checkins(days=1).get(_today())
    return {"enabled": cfg().get("enabled", True), "checked_in": bool(today), "today": today,
            "headsup": headsup(), "night": is_night(), "low_energy": low_energy(),
            "anchors": anchors_today(), "held": held(), "nudge_minutes": int(cfg().get("nudge_minutes", 50)),
            "focus_default": bool(cfg().get("focus_default", False))}


# ── the summary for your doctor ───────────────────────────
def _spark(vals: list, lo: float, hi: float, color: str, w: int = 640, h: int = 120) -> str:
    pts = [(i, v) for i, v in enumerate(vals) if v is not None]
    if not pts:
        return f'<svg width="{w}" height="{h}"></svg>'
    n = max(1, len(vals) - 1)
    xy = [(12 + (w - 24) * i / n, h - 12 - (h - 24) * (v - lo) / (hi - lo)) for i, v in pts]
    path = " ".join(f"{'M' if k == 0 else 'L'}{x:.1f},{y:.1f}" for k, (x, y) in enumerate(xy))
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>' for x, y in xy)
    grid = "".join(f'<line x1="12" x2="{w - 12}" y1="{h - 12 - (h - 24) * (g - lo) / (hi - lo):.1f}" '
                   f'y2="{h - 12 - (h - 24) * (g - lo) / (hi - lo):.1f}" stroke="#e5e7eb"/>'
                   for g in (lo, (lo + hi) / 2, hi))
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img">{grid}'
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.2"/>{dots}</svg>')


def report(days: int = 30, include_notes: bool = False) -> str:
    """A printable page (Ctrl+P → Save as PDF). Built on this PC; nothing is uploaded."""
    end = dt.date.today()
    ck = checkins(days=days, until=end.isoformat())
    dates = [(end - dt.timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
    get = lambda k: [ck.get(d, {}).get(k) for d in dates]      # noqa: E731
    en, mo, sl = get("energy"), get("mood"), get("sleep")

    def avg(v):
        v = [x for x in v if x is not None]
        return f"{sum(v) / len(v):.1f}" if v else "—"
    owner = html.escape(context.cfg.assistant.owner if context.cfg else "")
    rows = "".join(f"<tr><td>{d}</td><td>{ck[d].get('sleep') if ck[d].get('sleep') is not None else ''}</td>"
                   f"<td>{ck[d].get('energy') or ''}</td><td>{ck[d].get('mood') or ''}</td>"
                   + (f"<td>{html.escape(ck[d].get('note') or '')}</td>" if include_notes else "") + "</tr>"
                   for d in dates if d in ck)
    s = context.store
    late = {}
    with s.lock:
        for r in s.db.execute("SELECT ts FROM activity WHERE kind='user' AND turn=id AND ts>=?", (dates[0],)):
            t = dt.datetime.fromisoformat(r["ts"])
            if 0 <= t.hour < 5:
                late[t.date().isoformat()] = late.get(t.date().isoformat(), 0) + 1
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Rhythm summary {dates[0]} – {dates[-1]}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font:14px/1.5 system-ui,Segoe UI,sans-serif;color:#111;max-width:760px;margin:28px auto;padding:0 16px}}
h1{{font-size:22px;margin:0}} h2{{font-size:15px;margin:22px 0 4px}} .muted{{color:#6b7280}}
table{{border-collapse:collapse;width:100%;font-size:13px}} td,th{{border-bottom:1px solid #e5e7eb;padding:4px 6px;text-align:left}}
.k{{display:flex;gap:18px;flex-wrap:wrap;margin:10px 0}} .k b{{font-size:20px;display:block}}
@media print{{.noprint{{display:none}}}}</style></head><body>
<p class="noprint muted">Private — made on your PC from your own check-ins. Print or save as PDF to share it (Ctrl+P).</p>
<h1>Sleep, energy and mood — last {days} days</h1>
<div class="muted">{owner} · {dates[0]} to {dates[-1]} · {len(ck)} check-ins</div>
<div class="k"><div><b>{avg(sl)} h</b>average sleep</div><div><b>{avg(en)}/5</b>average energy</div>
<div><b>{avg(mo)}/5</b>average mood</div><div><b>{len(late)}</b>nights active after midnight</div></div>
<h2>Sleep (hours)</h2>{_spark(sl, 0, 12, "#2563eb")}
<h2>Energy (1–5)</h2>{_spark(en, 1, 5, "#d97706")}
<h2>Mood (1–5)</h2>{_spark(mo, 1, 5, "#059669")}
<h2>Day by day</h2><table><tr><th>Date</th><th>Sleep h</th><th>Energy</th><th>Mood</th>{"<th>Note</th>" if include_notes else ""}</tr>{rows}</table>
<p class="muted" style="margin-top:18px">Self-reported daily check-ins. Late-night activity = requests to Nova between 00:00 and 05:00.</p>
</body></html>"""


# ── background: anchors, morning release, heads-up ────────
_thread: threading.Thread | None = None


def tick(at: dt.datetime | None = None) -> list[str]:
    """One pass of the scheduler; returns what it sent (for tests)."""
    if not (context.store and cfg().get("enabled", True)):
        return []
    at = at or dt.datetime.now()
    s = _db()
    sent = []

    def once(key: str) -> bool:
        with s.lock:
            if s.db.execute("SELECT 1 FROM wb_seen WHERE k=?", (key,)).fetchone():
                return False
            s.db.execute("INSERT INTO wb_seen(k, ts) VALUES(?,?)", (key, at.isoformat(timespec="seconds")))
            s.db.commit()
        return True
    day = at.date().isoformat()
    hm = at.strftime("%H:%M")
    for a in anchors_today():
        if a["done"] or not (a["at"] <= hm < _plus(a["at"], 20)) or not once(f"anchor:{day}:{a['name']}"):
            continue
        msg = f"🌿 {a['name']}" + (f" — {', '.join(a['steps'])}" if a["steps"] else "")
        context.push(msg, [])
        if context.announce and not is_night(at):
            context.announce(f"Gentle reminder: {a['name']}.")
        sent.append(msg)
    due = [h for h in held() if h["due"] <= at.isoformat(timespec="minutes") and not h["told"]]
    if due:
        lines = "\n".join(f"#{h['id']} {h['label']}" for h in due)
        msg = f"☀️ Held for a fresh look:\n{lines}\nSend or drop them on the dashboard (Focus), or tell me “send held #n”."
        context.push(msg, [])
        with s.lock:
            s.db.executemany("UPDATE wb_held SET told=1 WHERE id=?", [(h["id"],) for h in due])
            s.db.commit()
        sent.append(msg)
    if cfg().get("signals_telegram", False) and at.hour >= 9 and once(f"headsup:{day}"):
        hu = headsup(at.date())
        if hu and not hu["dismissed"]:
            msg = f"🌿 {hu['text']}\n{hu['plan']}"
            context.push(msg, [])
            sent.append(msg)
    return sent


def _plus(hm: str, minutes: int) -> str:
    t = dt.datetime.combine(dt.date.today(), _hm(hm, "00:00")) + dt.timedelta(minutes=minutes)
    return t.strftime("%H:%M") if t.date() == dt.date.today() else "23:59"


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception as e:
            print(f"[wellbeing] {e}")
        time.sleep(30)


def start() -> None:
    global _thread
    if not (_thread and _thread.is_alive()):
        _thread = threading.Thread(target=_loop, daemon=True, name="wellbeing")
        _thread.start()
