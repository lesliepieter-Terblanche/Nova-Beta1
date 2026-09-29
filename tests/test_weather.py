import json
from pathlib import Path

import pytest

from nova.skills import weather

FX = json.loads((Path(__file__).parent / "fixtures" / "open_meteo.json").read_text())


class _Resp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


@pytest.fixture()
def fake_api(monkeypatch):
    calls = []

    def get(url, params=None, timeout=None):
        calls.append((url, params))
        return _Resp(FX["geo"] if "geocoding" in url else FX["forecast"])
    monkeypatch.setattr(weather.httpx, "get", get)
    monkeypatch.setattr(weather.webbrowser, "open", lambda *a, **k: calls.append(("browser", a)))
    return calls


def test_weather_uses_home_city_and_speaks(nova, fake_api):
    nova[0]["assistant"]["city"] = "Roodepoort"
    text = weather.get_weather()
    assert text.startswith("Right now in Roodepoort it's 24 degrees and partly cloudy")
    assert "Tomorrow: overcast" in text and "UV is very high" in text
    assert "http" not in text
    assert fake_api[0][1]["name"] == "Roodepoort"
    assert any(c[0] == "browser" and "/weather?day=0" in c[1][0] for c in fake_api)
    data = json.loads(Path("workspace/weather/latest.json").read_text())
    assert len(data["hours"]) == 24 and len(data["days"]) == 7


def test_weather_for_a_named_day(nova, fake_api):
    text = weather.get_weather("Durban", "Wednesday", show=False)
    d = weather.fetch("Durban")["days"]
    wanted = d[1]                                   # 29 Sep 2026 is a Tuesday, so Wednesday = tomorrow
    assert text.startswith(f"Tomorrow in Roodepoort: {wanted['desc']}")
    assert not any(c[0] == "browser" for c in fake_api)


def test_weather_errors_are_spoken_not_crashing(nova, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("offline")
    monkeypatch.setattr(weather.httpx, "get", boom)
    assert weather.get_weather("Paris").startswith("ERROR")


def test_weather_is_offered_instead_of_web_search(nova):
    from nova.tools import select_tools
    groups = {t.group for t in select_tools("what's the weather like tomorrow?")}
    assert "weather" in groups and "web" not in groups
