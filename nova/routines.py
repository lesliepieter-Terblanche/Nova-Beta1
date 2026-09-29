"""Scheduled routines from config.yaml, e.g. a spoken morning briefing at 07:30 on weekdays.

routines:
  - name: Morning briefing
    at: "07:30"
    days: [mon, tue, wed, thu, fri]
    prompt: "Give me my morning briefing"
    speak: true        # say it out loud (if the voice loop is running)
    telegram: true     # also send it to my phone
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from zoneinfo import ZoneInfo

from . import context
from .config import resolve

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _state_path():
    p = resolve("data/routines_state.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def run_routine(agent, r: dict, speak=None) -> str:
    reply = agent.handle(r["prompt"], session=f"routine:{r.get('name', 'routine')}")
    if r.get("telegram", True):
        context.push(f"🗓 {r.get('name', 'Routine')}\n\n{reply.text}", reply.files)
    if r.get("speak") and speak:
        speak(reply.text)
    return reply.text


def worker(agent, cfg, speak=None) -> None:
    routines = cfg.get("routines") or []
    if not routines:
        return
    tz = ZoneInfo(cfg.assistant.timezone)
    path = _state_path()
    state = json.loads(path.read_text()) if path.exists() else {}
    print(f"[routines] {len(routines)} scheduled")
    while True:
        now = dt.datetime.now(tz)
        for r in routines:
            key = r.get("name", r["prompt"])
            days = [d[:3].lower() for d in r.get("days", DAYS)]
            hh, mm = map(int, str(r["at"]).split(":"))
            due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if DAYS[now.weekday()] in days and due <= now < due + dt.timedelta(minutes=30) \
                    and state.get(key) != now.date().isoformat():
                state[key] = now.date().isoformat()
                path.write_text(json.dumps(state))
                threading.Thread(target=run_routine, args=(agent, r, speak), daemon=True).start()
        time.sleep(30)
