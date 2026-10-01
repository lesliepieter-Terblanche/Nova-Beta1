"""Centre-of-the-dashboard cards: weather, PC stats, news, screen time, anything Nova wants to show."""
import json
import urllib.request

import pytest

from nova import cards
from nova.tools import REGISTRY


@pytest.fixture(autouse=True)
def fresh_cards():
    cards._cards.clear()
    cards._last_view = 0.0
    yield


def test_cards_queue_and_viewer(nova):
    first = cards.latest_id()
    cards.show("info", "Hello", {"text": "x"})
    assert not cards.viewer_active()
    got = cards.recent(first)
    assert [c["title"] for c in got] == ["Hello"] and cards.viewer_active()
    assert cards.recent(got[-1]["id"]) == []


def test_weather_goes_to_the_dashboard_when_one_is_open(nova, monkeypatch):
    from nova.skills import weather
    data = {"place": "Roodepoort, Gauteng", "short": "Roodepoort", "current": {"temp": 23, "feels": 23, "desc": "clear sky"},
            "days": [{"date": "2026-10-01", "name": "Today", "desc": "clear sky", "max": 25, "min": 11, "rain": 0, "uv": 5,
                      "wind": 10}, {"date": "2026-10-02", "name": "Tomorrow", "desc": "rain", "max": 20, "min": 10,
                                    "rain": 70, "uv": 3, "wind": 20}]}
    monkeypatch.setattr(weather, "fetch", lambda place="": data)
    opened = []
    monkeypatch.setattr(weather.webbrowser, "open", opened.append)
    cards.recent(0)                                                  # a dashboard is watching
    assert REGISTRY["get_weather"].run({"day": "tomorrow"}).startswith("Tomorrow in Roodepoort")
    c = cards.recent(0)[-1]
    assert c["kind"] == "weather" and c["data"]["day"] == 1 and opened == []
    cards._last_view = 0.0                                           # nobody watching: open the weather page
    REGISTRY["get_weather"].run({})
    assert opened and opened[0].endswith("/weather?day=0")


def test_pc_stats_card_and_show_on_screen(nova):
    out = REGISTRY["system_status"].run({})
    c = cards.recent(0)[-1]
    assert c["kind"] == "pc" and 0 <= c["data"]["cpu"] <= 100 and c["data"]["ram_total_gb"] > 0 and "cpu_percent" in out
    REGISTRY["system_status"].run({"show": False})
    assert len(cards.recent(0)) == 1
    assert REGISTRY["show_on_screen"].run({"title": "Axiz Q4", "content": "Open deals: 7\n- Send pricing"}) == \
        "Shown on the dashboard."
    last = cards.recent(0)[-1]
    assert last["kind"] == "info" and last["title"] == "Axiz Q4" and "Open deals: 7" in last["data"]["text"]


def test_dashboard_delivers_cards_and_live_stats(nova):
    from nova.dashboard.server import Dashboard
    cfg = nova[0]
    cfg["dashboard"]["open_on_start"] = False
    cfg["dashboard"]["port"] = 8795
    Dashboard(cfg, None).start()

    def get(path):
        with urllib.request.urlopen(f"http://127.0.0.1:8795{path}", timeout=10) as r:
            return json.loads(r.read())
    seq = get("/api/activity?since=0&cards=0")["card_seq"]
    cards.show("news", "Top headlines", {"items": [{"title": "Rand firms", "source": "News24", "link": "", "ago": ""}]})
    r = get(f"/api/activity?since=0&cards={seq}")
    assert [c["kind"] for c in r["cards"]] == ["news"] and r["card_seq"] == seq + 1
    assert get(f"/api/activity?since=0&cards={r['card_seq']}")["cards"] == []
    st = get("/api/pcstats")
    assert {"cpu", "ram", "disk", "uptime_hours"} <= set(st)
