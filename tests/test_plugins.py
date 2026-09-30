"""v2.5 plugins: load-shedding, price watcher, news (the internet is faked) + the one-click MCP catalogue."""
import datetime as dt
import json
import sys

import pytest

from nova.extensions import load_plugins
from nova.tools import REGISTRY, select_tools

from conftest import ROOT


class Resp:
    def __init__(self, data=None, text="", status=200):
        self._data, self.text, self.status_code = data, text or (json.dumps(data) if data is not None else ""), status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture()
def plugins(nova):
    load_plugins(ROOT / "plugins")
    return {n: sys.modules[f"nova_plugins.{n}"] for n in ("loadshedding", "price_watch", "news")}


# ── load-shedding ─────────────────────────────────────────
def _soon(minutes):
    t = (dt.datetime.now() + dt.timedelta(minutes=minutes, seconds=20)).replace(microsecond=0).astimezone()
    return t.isoformat()


def test_loadshedding_status_and_warning(plugins, nova, monkeypatch):
    ls = plugins["loadshedding"]
    ls._cache.clear()
    ls._warned.clear()
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        assert headers == {"Token": "tok"}
        if url.endswith("/status"):
            return Resp({"status": {"eskom": {"stage": "2", "next_stages": []}}})
        if url.endswith("/areas_search"):
            return Resp({"areas": [{"id": "eskde-10-roodepoort", "name": "Roodepoort", "region": "Eskom Direct"}]})
        return Resp({"info": {"name": "Roodepoort"}, "events": [
            {"start": _soon(20), "end": _soon(140), "note": "Stage 2"}]})
    monkeypatch.setattr(ls.httpx, "get", get)
    monkeypatch.delenv("ESP_TOKEN", raising=False)
    assert "No EskomSePush token" in REGISTRY["loadshedding_status"].run({})
    monkeypatch.setenv("ESP_TOKEN", "tok")
    assert "id eskde-10-roodepoort" in REGISTRY["find_loadshedding_area"].run({"place": "Roodepoort"})
    out = REGISTRY["loadshedding_status"].run({"area_id": "eskde-10-roodepoort"})
    assert "stage 2" in out and ("Roodepoort: today" in out or "Roodepoort: tomorrow" in out)   # near midnight
    nova[0]["loadshedding"] = {"area_id": "eskde-10-roodepoort", "warn_minutes": 30}
    said = []
    monkeypatch.setattr(ls.context, "announce", said.append)
    msg = ls.check_and_warn()
    assert msg and "Load-shedding in 20 minutes for Roodepoort" in msg and said
    assert ls.check_and_warn() is None                     # warns once per slot
    n = len(calls)
    ls.loadshedding_status("eskde-10-roodepoort")
    assert len(calls) == n                                  # cached, saves the 50-a-day allowance
    assert "loadshedding_status" in {t.name for t in select_tools("what stage of load shedding are we on")}


# ── price watcher ─────────────────────────────────────────
PAGE = """<html><head><meta property="og:title" content="Hisense 55 inch TV">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"Hisense 55\\" UHD TV",
"offers":{"@type":"Offer","price":"8999.00","priceCurrency":"ZAR"}}</script></head><body>R 8 999</body></html>"""


def test_price_from_html_variants(plugins):
    pw = plugins["price_watch"]
    assert pw.from_html(PAGE)["price"] == 8999.0
    meta = '<meta property="product:price:amount" content="1299.50"><title>Kettle</title>'
    assert pw.from_html(meta) == {"title": "Kettle", "price": 1299.5, "currency": "ZAR"}
    assert pw.from_html("<p>Now only R2 499,00!</p>")["price"] == 2499.0
    assert pw._num("R 12 345") == 12345.0


def test_takealot_api(plugins, monkeypatch):
    pw = plugins["price_watch"]
    seen = {}

    def get(url, params=None, headers=None, timeout=None, **kw):
        seen["url"] = url
        return Resp({"title": "Sony WH-1000XM5", "buybox": {"prices": [6499]}})
    monkeypatch.setattr(pw.httpx, "get", get)
    info = pw.price_of("https://www.takealot.com/sony-wh-1000xm5/PLID73463574")
    assert info == {"title": "Sony WH-1000XM5", "price": 6499.0, "currency": "ZAR"}
    assert seen["url"].endswith("product-details/PLID73463574")


def test_watch_alerts_once_when_price_drops(plugins, monkeypatch):
    pw = plugins["price_watch"]
    monkeypatch.setattr(pw, "_start", lambda: None)
    price = {"v": 8999.0}
    monkeypatch.setattr(pw, "price_of", lambda url: {"title": "Hisense TV", "price": price["v"], "currency": "ZAR"})
    pushed = []
    monkeypatch.setattr(pw.context, "push", lambda t, f=None: pushed.append(t))
    out = REGISTRY["watch_price"].run({"url": "https://shop.example/tv", "target_price": 8000})
    assert "Watching Hisense TV" in out and "R8 000" in out
    assert "#1 Hisense TV" in REGISTRY["list_price_watches"].run({})
    later = dt.datetime.now() + dt.timedelta(hours=7)
    assert pw.check_due(later) == []                         # still too expensive
    price["v"] = 7499.0
    alerts = pw.check_due(later + dt.timedelta(hours=7))
    assert alerts and "R7 499" in alerts[0] and pushed
    assert pw.check_due(later + dt.timedelta(hours=14)) == []  # no repeat while it stays low
    assert pw.check_due(later + dt.timedelta(hours=1)) == []   # not due yet either way
    assert "Stopped 1 price watch." == REGISTRY["stop_price_watch"].run({"watch_id": 0})
    assert "watch_price" in {t.name for t in select_tools("tell me when this drops below R8000 on takealot")}


# ── news ──────────────────────────────────────────────────
RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Eskom suspends load-shedding</title><link>https://n.example/1</link>
<pubDate>Tue, 29 Sep 2026 08:00:00 +0200</pubDate></item>
<item><title>Rand firms against dollar</title><link>https://n.example/2</link>
<pubDate>Tue, 29 Sep 2026 10:00:00 +0200</pubDate></item></channel></rss>"""
GOOGLE = """<?xml version="1.0"?><rss><channel>
<item><title>Nokia wins SA fibre deal - TechCentral</title><link>https://g.example/1</link>
<pubDate>Mon, 28 Sep 2026 09:00:00 GMT</pubDate><source>TechCentral</source></item></channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom story</title>
<link href="https://a.example/1"/><updated>2026-09-29T07:00:00Z</updated></entry></feed>"""


def test_news_parse_and_tools(plugins, monkeypatch):
    nw = plugins["news"]
    items = nw.parse(RSS, "News24")
    assert [i["title"] for i in items] == ["Rand firms against dollar", "Eskom suspends load-shedding"]
    assert nw.parse(GOOGLE)[0] | {"when": None} == {"title": "Nokia wins SA fibre deal", "link": "https://g.example/1",
                                                     "when": None, "source": "TechCentral"}
    assert nw.parse(ATOM, "X")[0]["link"] == "https://a.example/1"
    assert nw.parse("not xml") == []

    def get(url, headers=None, timeout=None, follow_redirects=None):
        return Resp(text=GOOGLE if "news.google.com" in url else RSS)
    monkeypatch.setattr(nw.httpx, "get", get)
    out = REGISTRY["news_briefing"].run({})
    assert out.startswith("Top headlines:") and out.count("Rand firms") == 1   # de-duplicated across feeds
    assert "Nokia wins SA fibre deal (TechCentral" in REGISTRY["news_briefing"].run({"topic": "Nokia"})
    assert "Juniper Networks: Nokia wins" in REGISTRY["vendor_news"].run({})
    assert "news_briefing" in {t.name for t in select_tools("what's in the news today")}


# ── one-click MCP servers ─────────────────────────────────
def test_mcp_catalog_add(monkeypatch, tmp_path):
    from ruamel.yaml import YAML

    from nova import settings
    cfg = tmp_path / "config.yaml"
    cfg.write_text("mcp_servers:\n  filesystem:\n    command: npx\n    enabled: true\n")
    monkeypatch.setattr(settings, "load_doc", lambda: YAML().load(cfg.read_text()))
    monkeypatch.setattr(settings, "save_doc", lambda d: YAML().dump(d, cfg.open("w")))
    inv = settings.extension_inventory(settings.load_doc())
    assert {c["name"] for c in inv["mcp_catalog"]} == {"canva", "windows", "excel", "elevenlabs", "youtube"}
    assert inv["mcp_catalog"][0]["name"] == "canva"
    assert not any(c["added"] for c in inv["mcp_catalog"])
    r = settings.apply({"extensions": {"mcp_add": ["youtube", "elevenlabs", "nope"]}})
    assert "mcp_add.nope" in r["errors"]
    doc = settings.load_doc()
    assert doc["mcp_servers"]["youtube"]["args"] == ["mcp-youtube-transcript"]
    el = doc["mcp_servers"]["elevenlabs"]
    assert el["enabled"] and el["env"]["ELEVENLABS_API_KEY"] == "${ELEVENLABS_API_KEY}"
    assert el["env"]["ELEVENLABS_MCP_BASE_PATH"].endswith("workspace/audio") and "{workspace}" not in str(el)
    assert "filesystem" in doc["mcp_servers"]
    added = {c["name"] for c in settings.extension_inventory(doc)["mcp_catalog"] if c["added"]}
    assert added == {"youtube", "elevenlabs"}
