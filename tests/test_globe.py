"""God's Eye View integration: links, live-data answers and app management (network mocked)."""
from urllib.parse import parse_qs, urlparse

from nova.tools import REGISTRY, select_tools


class Resp:
    def __init__(self, data, code=200):
        self._d, self.status_code = data, code

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


JHB = {"name": "Johannesburg", "label": "Johannesburg, Gauteng, South Africa", "lat": -26.2041, "lon": 28.0473}


def _fake_get(url, params=None, **kw):
    if "adsb.lol" in url:
        return Resp({"ac": [
            {"hex": "abc", "flight": "SAA335 ", "t": "A20N", "desc": "AIRBUS A320neo", "alt_baro": 9000, "gs": 250,
             "lat": -26.13, "lon": 28.24},
            {"hex": "def", "flight": "", "r": "ZS-XYZ", "alt_baro": "ground", "lat": -26.14, "lon": 28.23},
            {"hex": "mil", "flight": "SAAF01", "alt_baro": 20000, "lat": -25.9, "lon": 28.2, "dbFlags": 1}]})
    if "usgs" in url:
        return Resp({"features": [
            {"properties": {"mag": 3.1, "place": "10 km S of Orkney, South Africa", "time": 1790000000000},
             "geometry": {"coordinates": [26.6, -27.0, 5]}},
            {"properties": {"mag": 2.6, "place": "Carletonville", "time": 1790100000000},
             "geometry": {"coordinates": [27.4, -26.4, 3]}}]})
    if "wheretheiss" in url and "coordinates" in url:
        return Resp({"country_code": "??", "timezone_id": ""})
    if "wheretheiss" in url:
        return Resp({"latitude": -12.5, "longitude": 45.1, "altitude": 418.2, "velocity": 27580})
    return Resp({}, 404)


def _patch(monkeypatch, tmp):
    from nova.skills import globe, weather
    monkeypatch.setattr(globe.httpx, "get", _fake_get)
    monkeypatch.setattr(weather, "geocode", lambda p: JHB)
    opened = []
    monkeypatch.setattr(globe, "_open", opened.append)
    monkeypatch.setattr(globe, "app_dir", lambda: tmp / "gev")
    return globe, opened


def test_view_url_encodes_camera_and_style(nova):
    from nova.skills import globe
    u = urlparse(globe.view_url(-33.92, 18.42, "street", "night vision", hud=True))
    q = parse_qs(u.fragment)
    assert u.netloc == "localhost:4173" and q["style"] == ["nvg"] and q["alt"] == ["600"]
    assert q["hud"] == ["tactical"] and q["lat"] == ["-33.92000"]
    assert parse_qs(urlparse(globe.view_url(0, 0, "nonsense", "thermal")).fragment)["style"] == ["flir"]


def test_planes_overhead_speaks_a_summary(nova, monkeypatch):
    _, tmp = nova
    _patch(monkeypatch, tmp)
    out = REGISTRY["planes_overhead"].run({"place": "Johannesburg", "radius_km": 40})
    assert out.startswith("3 aircraft within 40 km of Johannesburg: 2 flying, 1 on the ground, 1 military")
    assert "SAA335 (AIRBUS A320neo)" in out and "ZS-XYZ" in out and "on the ground" in out


def test_earthquakes_and_iss(nova, monkeypatch):
    _, tmp = nova
    _patch(monkeypatch, tmp)
    q = REGISTRY["recent_earthquakes"].run({"place": "Johannesburg"})
    assert q.startswith("2 earthquakes near Johannesburg") and "magnitude 3.1 10 km S of Orkney" in q
    iss = REGISTRY["where_is_the_iss"].run({"show": False})
    assert "12.5°S, 45.1°E" in iss and "418 km up" in iss


def test_not_installed_is_explained_and_install_needs_confirmation(nova, monkeypatch):
    _, tmp = nova
    globe, opened = _patch(monkeypatch, tmp)
    monkeypatch.setattr(globe, "running", lambda: False)
    out = REGISTRY["show_on_globe"].run({"place": "Johannesburg"})
    assert "isn't installed" in out and not opened
    assert REGISTRY["install_globe"].confirm is True
    monkeypatch.setattr(globe, "node_version", lambda: (22, "v22.3.0"))
    assert "needs Node.js 24" in globe.install()


def test_show_on_globe_opens_link_when_running(nova, monkeypatch):
    _, tmp = nova
    globe, opened = _patch(monkeypatch, tmp)
    monkeypatch.setattr(globe, "running", lambda: True)
    out = REGISTRY["show_on_globe"].run({"place": "Johannesburg", "style": "thermal", "view": "region"})
    assert out == "Showing Johannesburg, Gauteng, South Africa on the globe (region view, thermal)."
    assert "style=flir" in opened[0] and "lat=-26.20410" in opened[0]


def test_globe_tools_are_picked_for_globe_requests(nova):
    names = {t.name for t in select_tools("what planes are flying over my house right now")}
    assert "planes_overhead" in names
    names = {t.name for t in select_tools("show me Cape Town on the god's eye globe in night vision")}
    assert "show_on_globe" in names


def test_dashboard_globe_status_and_settings_check(nova, monkeypatch):
    from nova import settings
    _, tmp = nova
    globe, _ = _patch(monkeypatch, tmp)
    monkeypatch.setattr(globe, "running", lambda: False)
    monkeypatch.setattr(globe, "node_version", lambda: (24, "v24.14.0"))
    st = globe.status()
    assert st["node_ok"] and not st["installed"] and st["url"] == "http://localhost:4173"
    r = settings.run_test("globe")
    assert not r["ok"] and "isn't installed" in r["message"]
