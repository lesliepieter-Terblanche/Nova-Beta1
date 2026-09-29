"""Presence awareness state machine (no webcam needed)."""
import datetime as dt
import time

from nova import context
from nova.agent import Agent
from nova.llm import LLMReply


def _p(nova, monkeypatch, **cfg):
    from nova import presence as P
    nova[0]["presence"] = {"away_seconds": 60, "arrive_seconds": 2, "greet_after_seconds": 300, **cfg}
    p = P.Presence(nova[0])
    p.enabled = True
    said, stopped = [], []
    monkeypatch.setattr(context, "speak_now", said.append, raising=False)
    monkeypatch.setattr(context.speech, "stop", lambda: stopped.append(1), raising=False)
    return p, said, stopped


def test_arrive_leave_and_welcome_back(nova, monkeypatch):
    p, said, stopped = _p(nova, monkeypatch)
    p.last_arrival_day = dt.date.today().isoformat()          # not the first arrival today
    assert p.update(1, 0) is None                             # needs 2 s of seeing you
    assert p.update(1, 2.5) == "arrived" and p.present is True and said == []   # first sighting: no greeting
    assert p.update(0, 30) is None                            # a short look away doesn't count
    assert p.update(0, 63) == "left" and p.present is False and stopped
    assert context.store.status == "away"

    assert p.hold_or_say("Reminder: call Axiz about the Mist deal.")
    assert p.held and said == []

    p.update(1, 500)
    assert p.update(1, 503) == "arrived"
    assert said and said[0].startswith("Welcome back, Friend. While you were away: Reminder: call Axiz")
    assert p.held == [] and context.store.status == "idle"


def test_short_absence_just_delivers_held_messages(nova, monkeypatch):
    p, said, _ = _p(nova, monkeypatch)
    p.last_arrival_day = dt.date.today().isoformat()
    p.update(1, 0), p.update(1, 3)
    p.update(0, 70)
    p.hold_or_say("Your download finished.")
    p.update(1, 100), p.update(1, 103)
    assert said == ["While you were away: Your download finished."]


def test_typing_counts_as_present_and_nothing_is_held_when_here(nova, monkeypatch):
    p, said, _ = _p(nova, monkeypatch)
    assert p.update(0, 0, idle=3) == "arrived"                 # camera can't see you, but you're typing
    assert p.update(0, 100, idle=5) is None and p.present is True
    assert not p.hold_or_say("hello")
    p.enabled = False
    p.present = False
    assert not p.hold_or_say("hello")                         # presence off: never hold


def test_locks_pc_after_walking_away(nova, monkeypatch):
    from nova import presence as P
    locks = []
    monkeypatch.setattr(P, "lock_pc", lambda: locks.append(1) or True)
    p, _, _ = _p(nova, monkeypatch, lock_pc=True, lock_after_seconds=120)
    p.update(1, 0), p.update(1, 3)
    p.update(0, 70)                                           # left (last seen at 3)
    p.update(0, 100)
    assert not locks
    p.update(0, 125)
    p.update(0, 200)
    assert locks == [1]


def test_morning_briefing_on_first_arrival(nova, monkeypatch):
    from nova import presence as P
    cfg = nova[0]
    p, said, _ = _p(nova, monkeypatch)
    agent = Agent(cfg, context.llm)
    monkeypatch.setattr(context, "agent", agent)

    class Morning(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 9, 30, 7, 45)
    monkeypatch.setattr(P.dt, "datetime", Morning)
    p.last_arrival_day = "2026-09-29"
    p.present, p.since = False, 0
    context.llm.queue = [LLMReply("Morning! Two meetings today and 24 degrees.")]
    p.update(1, 30000), p.update(1, 30003)
    for _ in range(40):
        if said:
            break
        time.sleep(0.05)
    assert said == ["Morning! Two meetings today and 24 degrees."]


def test_presence_tool_and_dashboard(nova):
    from nova.dashboard.server import Dashboard
    from nova.tools import REGISTRY, select_tools
    assert "presence_awareness" in {t.name for t in select_tools("turn on presence awareness")}
    assert REGISTRY["lock_computer"].confirm is True
    assert REGISTRY["presence_awareness"].run({}).startswith("Presence awareness is off")
    assert Dashboard(nova[0], None).now()["presence"]["enabled"] is False


def test_on_frame_counts_faces(nova, monkeypatch):
    import numpy as np
    p, _, _ = _p(nova, monkeypatch)

    class Det:
        def detect_for_video(self, image, ms):
            return type("R", (), {"detections": [object()]})()
    p._detector = Det()
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    for i in range(10):
        p.on_frame(frame, 50 + i * 0.6)
    assert p.faces == 1 and p.present is True
