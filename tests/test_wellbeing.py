"""Focus mode + wellbeing: one thing at a time, private check-ins, heads-ups, night guardrails, anchors."""
import datetime as dt
import json
import urllib.request

import pytest

from nova import context, focus, wellbeing


@pytest.fixture()
def wb(nova, monkeypatch):
    import nova.dreaming as dreaming
    monkeypatch.setattr(dreaming, "passphrase", lambda create=True: ("test-passphrase", False))
    wellbeing._fernet["f"] = None
    nova[0]["wellbeing"] = {"enabled": True}
    return nova


# ── focus ─────────────────────────────────────────────────
def test_now_next_later_and_wins(wb):
    a = focus.add("Send Axiz the Mist pricing", "end")
    b = focus.add("Prep the Avaya QBR", "end")
    c = focus.add("Call Lerato", "end")
    st = focus.state()
    assert st["now"]["text"] == a["text"] and [t["text"] for t in st["next"]] == [b["text"], c["text"]]
    assert st["now"]["started"]                                   # clock starts for the Now task
    focus.add("Reply to Sam", "now")
    assert focus.state()["now"]["text"] == "Reply to Sam"
    focus.add("Book the car service", "next")
    assert focus.state()["next"][0]["text"] == "Book the car service"
    dumped = focus.brain_dump("buy printer ink\n- renew licence; ask about leave")
    assert len(dumped) == 3 and all(d["status"] == "later" for d in dumped)
    now_id = focus.state()["now"]["id"]
    focus.set_status(now_id, "done")
    assert focus.wins_today()["tasks"] == 1
    focus.set_status(focus.state()["now"]["id"], "later")          # not now → parked, no guilt
    assert any(t["text"] == "Book the car service" for t in focus.state()["later"])
    focus.set_status(dumped[0]["id"], "open-now")
    assert focus.state()["now"]["text"] == "buy printer ink"


def test_breakdown_gives_a_tiny_first_step(wb, monkeypatch):
    t = focus.add("Prep the Avaya QBR", "now")
    monkeypatch.setattr(context.llm, "complete", lambda p, **k: json.dumps(
        {"steps": ["Open last quarter's QBR deck", "Copy it to a new file", "Update the revenue slide"]}))
    steps = focus.breakdown(t["id"])
    st = focus.state()
    assert [s["text"] for s in steps][0] == "Open last quarter's QBR deck"
    assert st["now"]["text"] == "Open last quarter's QBR deck" and st["now"]["for"] == "Prep the Avaya QBR"
    assert focus.open_tasks()[3]["text"] == "Prep the Avaya QBR"   # the big task waits after its steps


def test_tracked_items_offered(wb):
    context.store.add_memory("TrueHome launch plan", "project")
    context.store.set_tracking("memory:1", status="doing")
    assert focus.from_brain_items()[0]["item"] == "memory:1"
    t = focus.add("TrueHome launch plan", "now", source="item:memory:1")
    focus.set_status(t["id"], "done")
    assert context.store.get_tracking("memory:1")["status"] == "done"


# ── private check-ins ─────────────────────────────────────
def test_checkins_are_encrypted_and_private(wb):
    wellbeing.checkin(2, 3, 5.5, "rough night")
    wellbeing.checkin(mood=4)                                     # later update keeps the rest
    s = context.store
    blob = s.db.execute("SELECT blob FROM wb_checkins").fetchone()["blob"]
    assert b"rough night" not in blob and b"energy" not in blob
    today = wellbeing.checkins(days=1)[dt.date.today().isoformat()]
    assert today == {"energy": 2, "mood": 4, "sleep": 5.5, "note": "rough night"}
    logs = [r["title"] + (r["detail"] or "") for r in s.db.execute("SELECT title, detail FROM activity")]
    assert not any("rough night" in x or "5.5" in x for x in logs)
    from nova.tools import REGISTRY
    assert not any(t.func.__module__ == "nova.wellbeing" for t in REGISTRY.values())
    assert wellbeing.low_energy()                                  # energy 2 → low-energy day
    assert "extra short and gentle" in wellbeing.style_hint() and "mood" not in wellbeing.style_hint()


def test_signals_and_headsup(wb):
    today = dt.date.today()
    for i, (sl, en, mo) in enumerate([(4.5, 5, 3), (5, 5, 3), (7, 3, 3)]):
        wellbeing.checkin(en, mo, sl, day=(today - dt.timedelta(days=i)).isoformat())
    keys = {x["key"] for x in wellbeing.signals()}
    assert {"short_sleep", "energy_up"} <= keys
    s = context.store
    for d in (1, 2):
        ts = dt.datetime.combine(today - dt.timedelta(days=d - 1), dt.time(1, 30)).isoformat()
        tid = s.log("user", "voice", "late request")
        s.db.execute("UPDATE activity SET ts=?, turn=id WHERE id=?", (ts, tid))
    s.db.commit()
    assert "late_nights" in {x["key"] for x in wellbeing.signals()}
    wb[0]["wellbeing"]["plan"] = "Sleep first, text Dr M."
    hu = wellbeing.headsup()
    assert "not a verdict" in hu["text"] and hu["plan"] == "Sleep first, text Dr M." and not hu["dismissed"]
    wellbeing.dismiss_headsup()
    assert wellbeing.headsup()["dismissed"]
    wb[0]["wellbeing"]["signals"] = {"short_sleep": False, "energy_up": False, "late_nights": False}
    assert wellbeing.signals() == []                               # only the patterns you chose


def test_low_run_and_burst(wb):
    today = dt.date.today()
    for i in range(3):
        wellbeing.checkin(2, 1, 8, day=(today - dt.timedelta(days=i)).isoformat())
    for i in range(4):
        context.store.add_memory(f"Project number {i}: something new", "project")
    keys = {x["key"] for x in wellbeing.signals()}
    assert {"low_run", "project_burst"} <= keys


# ── night & buying guardrails ─────────────────────────────
def test_should_hold(wb):
    night = dt.datetime.combine(dt.date.today(), dt.time(23, 40))
    day = dt.datetime.combine(dt.date.today(), dt.time(14, 0))
    reason, due = wellbeing.should_hold("gmail_send", {"to": "sam@axiz.co.za"}, night)
    assert "fresh look" in reason and due.strftime("%H:%M") == "08:00" and due.date() > night.date()
    assert wellbeing.should_hold("gmail_send", {}, day) is None
    assert wellbeing.should_hold("web_search", {}, night) is None
    r2 = wellbeing.should_hold("browser_submit", {"what_it_does": "Place order for printer ink"}, day)
    assert r2 and r2[0] == "24-hour pause on buying" and r2[1] == day + dt.timedelta(hours=24)
    early = dt.datetime.combine(dt.date.today(), dt.time(2, 0))
    assert wellbeing.release_time(early).date() == early.date()
    wb[0]["wellbeing"]["night"] = {"enabled": False}
    assert wellbeing.should_hold("gmail_send", {}, night) is None


def test_agent_holds_a_late_send_and_it_can_be_released(wb, monkeypatch):
    from conftest import LLMReply, ToolCall

    from nova.agent import Agent
    from nova.tools import REGISTRY, tool
    sent = []

    @tool(group="system", confirm=True)
    def post_update(text: str) -> str:
        """Post an update.
        Args:
            text: the update
        """
        sent.append(text)
        return "Posted."
    monkeypatch.setattr(wellbeing, "is_night", lambda at=None: True)
    llm = context.llm
    llm.queue = [LLMReply(content="", tool_calls=[ToolCall(id="1", name="post_update", arguments={"text": "Big news"})]),
                 LLMReply(content="It's late, so I've held that until the morning.", tool_calls=[])]
    agent = Agent(wb[0], llm)
    r = agent.handle("post the big news", session="text")
    assert "held" in r.text and not sent                            # no yes/no question, nothing posted
    h = wellbeing.held()
    assert len(h) == 1 and h[0]["tool"] == "post_update"
    assert wellbeing.release(h[0]["id"], "send") == "Posted." and sent == ["Big news"]
    assert wellbeing.held() == []
    REGISTRY.pop("post_update", None)


# ── routine anchors & the scheduler ───────────────────────
def test_anchors_and_tick(wb, monkeypatch):
    wb[0]["wellbeing"]["anchors"] = ["08:00 Morning routine | Water; Medication", "22:30 Wind down"]
    a = wellbeing.anchors()
    assert a[0] == {"at": "08:00", "name": "Morning routine", "steps": ["Water", "Medication"]}
    pushed = []
    monkeypatch.setattr(context, "push", lambda t, f=None: pushed.append(t))
    at = dt.datetime.combine(dt.date.today(), dt.time(8, 5))
    out = wellbeing.tick(at)
    assert out and "Morning routine" in out[0] and "Water, Medication" in out[0]
    assert wellbeing.tick(at) == []                                 # once
    wellbeing.tick_anchor("Wind down")
    assert [x["done"] for x in wellbeing.anchors_today()] == [False, True]
    wellbeing.hold("gmail_send", {"subject": "Pricing"}, "late", at - dt.timedelta(minutes=5))
    out = wellbeing.tick(at + dt.timedelta(minutes=1))
    assert any("Held for a fresh look" in m and "Pricing" in m for m in out)


def test_report_is_local_and_notes_optional(wb):
    wellbeing.checkin(3, 4, 7, "private words")
    page = wellbeing.report(30)
    assert "Sleep, energy and mood" in page and "7.0 h" in page and "private words" not in page
    assert "private words" in wellbeing.report(30, include_notes=True)


def test_dashboard_focus_api(wb):
    from nova.dashboard.server import Dashboard
    cfg = wb[0]
    cfg["dashboard"]["open_on_start"] = False
    cfg["dashboard"]["port"] = 8793
    Dashboard(cfg, None).start()

    def call(path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:8793{path}", data=json.dumps(body).encode() if body else None,
                                     headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:8793"},
                                     method="POST" if body is not None else "GET")
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return json.loads(raw) if r.headers.get_content_type() == "application/json" else raw.decode()
    call("/api/focus", {"action": "add", "text": "Send Axiz pricing", "where": "now"})
    call("/api/focus", {"action": "dump", "text": "ink; licence"})
    st = call("/api/focus")
    assert st["now"]["text"] == "Send Axiz pricing" and len(st["later"]) == 2 and not st["wb"]["checked_in"]
    call("/api/wellbeing", {"action": "checkin", "energy": 4, "mood": 4, "sleep": 7.5})
    st = call("/api/focus")
    assert st["wb"]["checked_in"] and st["wb"]["anchors"]
    call("/api/focus", {"action": "done", "id": st["now"]["id"]})
    assert call("/api/focus")["wins"]["tasks"] == 1
    tr = call("/api/wellbeing/trends?days=10")
    assert tr["checkins"][dt.date.today().isoformat()]["sleep"] == 7.5
    assert "Sleep, energy and mood" in call("/wellbeing/report?days=30")
