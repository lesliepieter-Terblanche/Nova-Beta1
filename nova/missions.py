"""Missions: goals Nova works on by itself, once or on a schedule.

  "Mission: every Monday at 8, research what Juniper Mist competitors did in SADC last week and brief me"
  "Start a mission to find 10 potential Avaya resellers in Botswana and put them in a note"

For each run Nova plans 3–6 steps (with the smart model), carries them out with its normal tools, writes a report
into the 2nd brain (Missions folder), and sends you the summary (Telegram + spoken, held while you're away).
Recurring missions see their previous report, so they can tell you what changed.

Safety: missions never do risky things (sending email, deleting, shell…) on their own — those are listed in the
report as "needs your OK" instead.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time

from . import context
from .config import resolve

_SCHEMA = """
CREATE TABLE IF NOT EXISTS missions(
  id INTEGER PRIMARY KEY, title TEXT, goal TEXT, schedule TEXT, status TEXT, created TEXT, next_run TEXT,
  last_run TEXT, runs INTEGER DEFAULT 0, turn INTEGER, progress REAL DEFAULT 0, step TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS mission_runs(
  id INTEGER PRIMARY KEY, mission_id INTEGER, started TEXT, finished TEXT, status TEXT, plan TEXT, steps TEXT,
  report TEXT, summary TEXT);
"""
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
PLAN_PROMPT = """You are planning a background mission for {owner}'s personal assistant, which has tools for web
search and scraping, the browser, Google Workspace (Gmail, Calendar, Drive, Docs, Sheets), files, memory/notes,
weather, the globe (flights, earthquakes) and more.

Mission: {goal}
{previous}
Plan 3 to 6 concrete steps the assistant can do with its tools, in order. The last step must collect the findings.
Never plan to send emails/messages, delete or move files, buy anything or run shell commands — if the goal needs
that, add a step that prepares it (a draft) instead.

Reply with JSON only: {{"title": "short mission name", "steps": [{{"title": "3-6 words", "do": "instruction"}}]}}"""
REPORT_PROMPT = """Write the mission report for {owner}.

Mission: {goal}
Run date: {date}
{previous}
What each step found:
{findings}

Write Markdown: a one-paragraph summary first (what matters, what changed since last time if known), then the key
findings as bullets with sources/links where available, then "Next steps" and, if anything needs {owner}'s OK
(emails to send, files to change), a "Needs your OK" list. Be specific and concise; don't invent facts."""


# ── schedules ─────────────────────────────────────────────
def parse_schedule(text: str) -> dict:
    """'' / 'now' / 'daily 07:30' / 'weekdays 8am' / 'every monday 09:00' / 'mon,thu 16:00' / 'every 6 hours'."""
    t = (text or "").lower().strip()
    if t in ("", "now", "once", "one time", "one-off", "today"):
        return {"type": "once"}
    m = re.search(r"every\s+(\d+)\s*(hours?|hrs?|h|minutes?|mins?|m)\b", t)
    if m:
        n = int(m.group(1))
        mins = n * 60 if m.group(2).startswith("h") else n
        return {"type": "interval", "minutes": max(15, mins)}
    if "hourly" in t or re.search(r"every hour\b", t):
        return {"type": "interval", "minutes": 60}
    at = "08:00"
    m = re.search(r"(\d{1,2})(?::|h)(\d{2})\s*(am|pm)?|(\d{1,2})\s*(am|pm)", t)
    if m:
        h = int(m.group(1) or m.group(4))
        mi = int(m.group(2) or 0)
        ap = m.group(3) or m.group(5)
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        at = f"{h:02d}:{mi:02d}"
    if "weekday" in t or "work day" in t or "workday" in t:
        return {"type": "weekly", "days": DAYS[:5], "at": at}
    if "weekend" in t:
        return {"type": "weekly", "days": DAYS[5:], "at": at}
    days = [d for d in DAYS if re.search(rf"\b{d}", t)]
    if days:
        return {"type": "weekly", "days": days, "at": at}
    if any(w in t for w in ("daily", "every day", "each day", "every morning", "every evening", "every night")):
        if "evening" in t and not m:
            at = "18:00"
        if "night" in t and not m:
            at = "21:00"
        return {"type": "daily", "at": at}
    if "weekly" in t or "every week" in t:
        return {"type": "weekly", "days": ["mon"], "at": at}
    return {"type": "once"}


def describe(s: dict) -> str:
    if s["type"] == "once":
        return "once"
    if s["type"] == "interval":
        m = s["minutes"]
        return f"every {m // 60} h" if m % 60 == 0 else f"every {m} min"
    if s["type"] == "daily":
        return f"daily at {s['at']}"
    days = s["days"]
    label = "weekdays" if days == DAYS[:5] else "weekends" if days == DAYS[5:] else \
        ", ".join(d.title() for d in days)
    return f"{label} at {s['at']}"


def next_run(s: dict, after: dt.datetime) -> dt.datetime | None:
    if s["type"] == "once":
        return None
    if s["type"] == "interval":
        return after + dt.timedelta(minutes=s["minutes"])
    h, m = map(int, s["at"].split(":"))
    for add in range(0, 8):
        day = after + dt.timedelta(days=add)
        cand = day.replace(hour=h, minute=m, second=0, microsecond=0)
        if cand <= after:
            continue
        if s["type"] == "daily" or DAYS[cand.weekday()] in s["days"]:
            return cand
    return None


def _now() -> dt.datetime:
    """PC local time — the same clock as everything else Nova stores."""
    return dt.datetime.now()


def _json(raw: str):
    m = re.search(r"\{.*\}", raw or "", re.S)
    return json.loads(m.group(0)) if m else None


# ── the engine ────────────────────────────────────────────
class Missions:
    def __init__(self):
        self.running: dict[int, dict] = {}         # mission id -> live progress
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def _db(self):
        s = context.store
        with s.lock:
            s.db.executescript(_SCHEMA)
        return s

    # create / manage
    def create(self, goal: str, schedule: str = "", title: str = "", start_now: bool = True) -> dict:
        goal = goal.strip()
        if len(goal) < 8:
            raise ValueError("Tell me a bit more about what the mission should achieve.")
        sched = parse_schedule(schedule)
        s = self._db()
        now = _now()
        nxt = now if start_now or sched["type"] == "once" else next_run(sched, now)
        title = (title or goal.split(".")[0])[:80]
        with s.lock:
            mid = s.db.execute("INSERT INTO missions(title,goal,schedule,status,created,next_run,turn) "
                               "VALUES(?,?,?,?,?,?,?)",
                               (title, goal, json.dumps(sched), "scheduled", now.isoformat(timespec="seconds"),
                                nxt.isoformat(timespec="seconds") if nxt else None, s.current_turn)).lastrowid
            s.db.commit()
        s.log("mission", "missions", f"🚀 New mission: {title} ({describe(sched)})", goal, f"mission:{mid}")
        self.start()
        return self.get(mid)

    def get(self, mid: int) -> dict | None:
        s = self._db()
        with s.lock:
            r = s.db.execute("SELECT * FROM missions WHERE id=?", (mid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["schedule"] = json.loads(d["schedule"] or '{"type":"once"}')
        d["when"] = describe(d["schedule"])
        if mid in self.running:
            d.update(self.running[mid])
        return d

    def all(self) -> list[dict]:
        s = self._db()
        with s.lock:
            ids = [r["id"] for r in s.db.execute("SELECT id FROM missions WHERE status!='deleted' ORDER BY id DESC")]
        return [self.get(i) for i in ids]

    def runs(self, mid: int, limit: int = 10) -> list[dict]:
        s = self._db()
        with s.lock:
            rows = s.db.execute("SELECT * FROM mission_runs WHERE mission_id=? ORDER BY id DESC LIMIT ?",
                                (mid, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["plan"] = json.loads(d["plan"] or "[]")
            d["steps"] = json.loads(d["steps"] or "[]")
            out.append(d)
        return out

    def _set(self, mid: int, **kw) -> None:
        s = context.store
        with s.lock:
            s.db.execute(f"UPDATE missions SET {', '.join(f'{k}=?' for k in kw)} WHERE id=?", (*kw.values(), mid))
            s.db.commit()

    def pause(self, mid: int) -> str:
        m = self.get(mid)
        if not m:
            return f"No mission #{mid}."
        self._set(mid, status="paused")
        return f"Paused mission #{mid} ({m['title']})."

    def resume(self, mid: int) -> str:
        m = self.get(mid)
        if not m:
            return f"No mission #{mid}."
        nxt = next_run(m["schedule"], _now())
        self._set(mid, status="scheduled" if nxt or m["runs"] == 0 else "done",
                  next_run=(nxt or _now()).isoformat(timespec="seconds") if (nxt or m["runs"] == 0) else None)
        self.start()
        return f"Resumed mission #{mid} ({m['title']})."

    def run_now(self, mid: int) -> str:
        m = self.get(mid)
        if not m:
            return f"No mission #{mid}."
        if mid in self.running:
            return f"Mission #{mid} is already running."
        self._set(mid, status="scheduled", next_run=_now().isoformat(timespec="seconds"))
        self.start()
        return f"Running mission #{mid} ({m['title']}) now."

    def delete(self, mid: int) -> str:
        m = self.get(mid)
        if not m:
            return f"No mission #{mid}."
        self._set(mid, status="deleted", next_run=None)
        return f"Deleted mission #{mid} ({m['title']}). Its reports stay in your notes."

    # scheduler
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="missions")
        self._thread.start()

    def due(self, now: dt.datetime | None = None) -> list[int]:
        s = self._db()
        now = now or _now()
        with s.lock:
            rows = s.db.execute("SELECT id FROM missions WHERE status='scheduled' AND next_run IS NOT NULL "
                                "AND next_run<=?", (now.isoformat(timespec="seconds"),)).fetchall()
        return [r["id"] for r in rows if r["id"] not in self.running]

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                for mid in self.due():
                    threading.Thread(target=self.run, args=(mid,), daemon=True, name=f"mission-{mid}").start()
            except Exception as e:
                print(f"[missions] scheduler: {e}")
            self._stop.wait(20)

    # one run
    def run(self, mid: int) -> dict:
        m = self.get(mid)
        if not m or mid in self.running:
            return {}
        s, agent, llm = context.store, context.agent, context.llm
        owner = context.cfg.assistant.owner if context.cfg else "the user"
        started = _now()
        live = {"status": "planning", "progress": 0.02, "step": "Planning"}
        self.running[mid] = live
        self._set(mid, status="running", progress=0.02, step="Planning")
        with s.lock:
            rid = s.db.execute("INSERT INTO mission_runs(mission_id,started,status) VALUES(?,?,'running')",
                               (mid, started.isoformat(timespec="seconds"))).lastrowid
            s.db.commit()
        prev = self.runs(mid, 2)
        prev = [r for r in prev if r["id"] != rid and r["summary"]]
        previous = f"Previous run ({prev[0]['started'][:10]}) summary: {prev[0]['summary'][:1500]}" if prev else ""
        s.log("mission", "missions", f"🚀 Mission started: {m['title']}", m["goal"], f"mission:{mid}", turn=0)
        steps_out, status, report_path, summary = [], "done", "", ""
        try:
            plan = _json(llm.complete(PLAN_PROMPT.format(owner=owner, goal=m["goal"], previous=previous),
                                      prefer_smart=True, temperature=0.3)) or {}
            steps = [st for st in plan.get("steps", []) if isinstance(st, dict) and st.get("do")][:6]
            if not steps:
                steps = [{"title": "Work on the goal", "do": m["goal"]}]
            if plan.get("title") and m["runs"] == 0:
                m["title"] = str(plan["title"])[:80]
                self._set(mid, title=m["title"])
            with s.lock:
                s.db.execute("UPDATE mission_runs SET plan=? WHERE id=?", (json.dumps(steps), rid))
                s.db.commit()
            session = f"mission:{mid}"
            agent.reset(session)
            for i, st in enumerate(steps):
                live.update(status="running", progress=round((i + 0.1) / (len(steps) + 1), 3), step=st["title"])
                self._set(mid, progress=live["progress"], step=st["title"])
                done = "\n".join(f"- {x['title']}: {x['result'][:600]}" for x in steps_out)
                ask = (f"[Background mission for {owner}: {m['goal']}]\n"
                       + (f"Done so far:\n{done}\n" if done else "")
                       + f"Step {i + 1} of {len(steps)} — {st['title']}: {st['do']}\n"
                       "Use your tools. Don't ask me questions; if something is risky (sending, deleting), "
                       "don't do it — say what you'd do. Reply with what you found, including links.")
                t0 = time.time()
                reply = agent.handle(ask, session, prefer_smart=True)
                result = reply.text
                if session in agent.pending:                    # it wanted to do something risky: decline
                    p = agent.pending.pop(session)
                    result += f"\n(Needs your OK — not done: {p.tool.name.replace('_', ' ')} {json.dumps(p.args)[:300]})"
                    if getattr(p, "log_id", None):
                        s.finish(p.log_id, "declined")
                steps_out.append({"title": st["title"], "do": st["do"], "result": result,
                                  "ms": int((time.time() - t0) * 1000), "status": "ok"})
                with s.lock:
                    s.db.execute("UPDATE mission_runs SET steps=? WHERE id=?", (json.dumps(steps_out), rid))
                    s.db.commit()
            live.update(status="writing", progress=0.92, step="Writing the report")
            self._set(mid, progress=0.92, step="Writing the report")
            findings = "\n\n".join(f"### {x['title']}\n{x['result'][:3000]}" for x in steps_out)
            report = llm.complete(REPORT_PROMPT.format(owner=owner, goal=m["goal"], date=f"{started:%A %d %B %Y}",
                                                       previous=previous, findings=findings),
                                  prefer_smart=True, temperature=0.3).strip()
            summary = report.split("\n\n")[0].lstrip("# ").strip()
            if summary.lower().startswith(("summary", "mission")) and "\n" in report:
                summary = next((p.strip() for p in report.split("\n\n")[1:] if p.strip()), summary)
            report_path = self._write_report(m, started, report, steps_out)
        except Exception as e:
            status, summary = "failed", f"The mission hit a problem: {e}"
            print(f"[missions] #{mid} failed: {e}")
        finally:
            self.running.pop(mid, None)
        with s.lock:
            s.db.execute("UPDATE mission_runs SET finished=?, status=?, report=?, summary=? WHERE id=?",
                         (_now().isoformat(timespec="seconds"), status, report_path, summary, rid))
            s.db.commit()
        cur = self.get(mid)
        if cur and cur["status"] != "deleted":
            nxt = next_run(cur["schedule"], _now())
            new_status = "paused" if cur["status"] == "paused" else ("scheduled" if nxt else
                                                                     ("done" if status == "done" else "failed"))
            self._set(mid, status=new_status, runs=cur["runs"] + 1, last_run=started.isoformat(timespec="seconds"),
                      next_run=nxt.isoformat(timespec="seconds") if nxt else None, progress=1.0 if status == "done"
                      else cur["progress"], step="")
        title = (cur or m)["title"]
        icon = "✅" if status == "done" else "⚠️"
        s.log("mission", "missions", f"{icon} Mission {'finished' if status == 'done' else 'failed'}: {title}",
              summary, f"mission:{mid}", turn=0)
        short = summary if len(summary) < 700 else summary[:680].rsplit(" ", 1)[0] + "…"
        context.push(f"{icon} Mission: {title}\n\n{short}", [report_path] if report_path else [])
        if context.announce:
            spoken = short.split(". ")
            context.announce(f"Mission {title} is {'done' if status == 'done' else 'stuck'}. " + ". ".join(spoken[:2]))
        return {"status": status, "report": report_path, "summary": summary, "steps": steps_out}

    def _write_report(self, m: dict, started: dt.datetime, report: str, steps: list) -> str:
        vault = resolve(context.cfg.brain.vault_dir) if context.cfg else resolve("brain")
        folder = vault / "Missions"
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\- ]+", "", m["title"]).strip()[:60] or f"Mission {m['id']}"
        path = folder / f"{safe} {started:%Y-%m-%d %H%M}.md"
        body = [f"# {m['title']}", "", f"*Mission #{m['id']} · {describe(m['schedule'])} · {started:%A %d %B %Y %H:%M}*",
                "", f"**Goal:** {m['goal']}", "", report, "", "---", "", "## Steps"]
        for i, st in enumerate(steps, 1):
            body += [f"{i}. **{st['title']}** — {st['do']}"]
        path.write_text("\n".join(body) + "\n", encoding="utf-8")
        try:
            context.store.index_note(path)
        except Exception:
            pass
        context.record("note", f"Mission report: {m['title']}", path, report[:500])
        return str(path)


_missions: Missions | None = None


def missions() -> Missions:
    global _missions
    if _missions is None:
        _missions = Missions()
    return _missions
