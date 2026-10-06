"""Tailscale remote access (the tailscale CLI is faked)."""
import json
import threading
import urllib.request

import pytest

from nova import remote

STATUS = {"BackendState": "Running", "Self": {"DNSName": "alex-pc.tail1234.ts.net.", "TailscaleIPs": ["100.64.1.2"]}}


@pytest.fixture()
def ts(monkeypatch):
    state = {"served": {}, "calls": [], "https_enabled": True, "backend": "Running"}

    def run(args, timeout=20):
        state["calls"].append(args)
        if args[:2] == ["status", "--json"]:
            return 0, json.dumps({**STATUS, "BackendState": state["backend"]})
        if args[:3] == ["serve", "status", "--json"]:
            return 0, json.dumps(state["served"])
        if args[:2] == ["serve", "--bg"] or (args[0] == "serve" and "--bg" in args):
            if not state["https_enabled"]:
                return 1, "Serve is not enabled on your tailnet. To enable, visit:\n  https://login.tailscale.com/f/serve?node=abc\n"
            target = args[-1]
            port = next((a.split("=")[1] for a in args if a.startswith("--https=")), "443")
            state["served"][port] = target
            return 0, ""
        if args[0] == "serve" and args[-1] == "off":
            port = args[1].split("=")[1]
            state["served"].pop(port, None)
            return 0, ""
        return 0, ""
    monkeypatch.setattr(remote, "exe", lambda: "tailscale")
    monkeypatch.setattr(remote, "_run", run)

    def serve(args, wait=25):
        code, text = run(args)
        link = remote._https_link(text)
        return ("approve", link) if link else ("ok", text) if code == 0 else ("error", text)
    monkeypatch.setattr(remote, "_serve", serve)
    state["remembered"] = []
    monkeypatch.setattr(remote, "_remember", lambda on: state["remembered"].append(on))
    remote._cache.update(t=0.0, status=None)
    return state


def test_not_installed(monkeypatch):
    monkeypatch.setattr(remote, "exe", lambda: None)
    remote._cache.update(t=0.0, status=None)
    st = remote.status(fresh=True)
    assert not st["installed"] and "tailscale.com/download" in st["message"]


def test_needs_login(nova, ts):
    ts["backend"] = "NeedsLogin"
    st = remote.status(fresh=True)
    assert not st["running"] and "sign in" in st["message"]
    assert not remote.enable()["ok"]


def test_enable_serves_dashboard_then_disable(nova, ts):
    st = remote.status(fresh=True)
    assert st["running"] and st["dns_name"] == "alex-pc.tail1234.ts.net" and not st["serving"]
    r = remote.enable()
    assert r["ok"] and r["url"] == "https://alex-pc.tail1234.ts.net/"
    assert "globe_url" not in r                                   # God's Eye View is gone (v2.34)
    assert ts["served"] == {"443": "http://127.0.0.1:8765"}
    assert remote.allowed_host("alex-pc.tail1234.ts.net") and remote.allowed_host("ALEX-PC.tail1234.ts.net:443")
    assert not remote.allowed_host("evil.tail9999.ts.net") and not remote.allowed_host("example.com")
    assert remote.qr_svg(r["url"]).startswith("<svg")
    d = remote.disable()
    assert d["ok"] and not d["serving"] and ts["served"] == {}


def test_https_not_enabled_gives_the_link_then_check_finishes(nova, ts):
    ts["https_enabled"] = False
    r = remote.enable()
    assert not r["ok"] and r["link"] == "https://login.tailscale.com/f/serve?node=abc"
    assert "Enable" in r["message"]
    # you approve in the browser -> the waiting tailscale command finishes and serves the dashboard
    ts["https_enabled"] = True
    ts["served"]["443"] = "http://127.0.0.1:8765"
    st = remote.finish_setup()
    assert st["url"] and "8443" not in ts["served"]


def test_real_serve_returns_link_without_waiting(monkeypatch, tmp_path):
    """The real tailscale CLI prints the approval link and then blocks — we must return straight away."""
    import sys
    fake = tmp_path / "fake_tailscale.py"
    fake.write_text("import time, sys\nprint('Serve is not enabled on your tailnet.')\n"
                    "print('To enable, visit:\\n  https://login.tailscale.com/f/serve?node=xyz', flush=True)\n"
                    "time.sleep(30)\n")
    real_popen = remote.subprocess.Popen
    monkeypatch.setattr(remote, "exe", lambda: sys.executable)
    monkeypatch.setattr(remote.subprocess, "Popen",
                        lambda cmd, **kw: real_popen([sys.executable, str(fake)], **kw))
    import time
    t = time.time()
    state, text = remote._serve(["serve", "--bg", "http://127.0.0.1:8765"], wait=10)
    assert state == "approve" and text == "https://login.tailscale.com/f/serve?node=xyz"
    assert time.time() - t < 5
    remote._pending["proc"].kill()


def test_dashboard_accepts_tailscale_host_and_blocks_others(nova, ts, monkeypatch):
    from nova.dashboard.server import Dashboard
    cfg = nova[0]
    cfg["dashboard"]["open_on_start"] = False
    cfg["dashboard"]["port"] = 8797
    remote.enable()
    Dashboard(cfg, None).start()

    def get(host, path="/api/theme"):
        req = urllib.request.Request(f"http://127.0.0.1:8797{path}", headers={"Host": host})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b""

    assert get("alex-pc.tail1234.ts.net")[0] == 200
    assert get("attacker.example")[0] == 403
    code, body = get("localhost:8797", "/api/remote")
    data = json.loads(body)
    assert code == 200 and data["url"] and data["qr"].startswith("<svg")

    # changing remote access is only allowed from the PC itself
    req = urllib.request.Request("http://127.0.0.1:8797/api/remote", data=b'{"on": false}', method="POST",
                                 headers={"Host": "alex-pc.tail1234.ts.net", "Content-Type": "application/json",
                                          "Origin": "https://alex-pc.tail1234.ts.net"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 403
    assert ts["served"]                                     # still on


def test_remote_tool(nova, ts):
    from nova.tools import REGISTRY, select_tools
    assert "remote_access" in {t.name for t in select_tools("set up tailscale so I can use nova from my phone")}
    assert "Done — open https://alex-pc.tail1234.ts.net/" in REGISTRY["remote_access"].run({"action": "on"})
    assert "Remote access is on" in REGISTRY["remote_access"].run({})


_ = threading


def test_setup_is_remembered_and_restored_on_start(nova, ts):
    r = remote.enable()
    assert r["ok"] and ts["remembered"] == [True]
    ts["served"].clear()                                   # e.g. after a reboot the share is gone
    ts["backend"] = "Stopped"
    calls_before = len(ts["calls"])

    def up_then_running(args, timeout=20, _orig=remote._run):
        if args == ["up"]:
            ts["backend"] = "Running"
        return _orig(args, timeout)
    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(remote, "_run", up_then_running)
    try:
        st = remote.ensure()
    finally:
        mp.undo()
    assert ["up"] in ts["calls"][calls_before:]
    assert st["url"] == "https://alex-pc.tail1234.ts.net/" and "8443" not in ts["served"]
    remote.disable()
    assert ts["remembered"] == [True, False]


def test_startup_summary_lines(nova, capsys):
    import main
    cfg = nova[0]
    cfg["remote"] = {"enabled": True}

    class TG:
        token, allowed, app = "x", {1}, object()
    import time as _t
    orig = _t.sleep
    _t.sleep = lambda s: None
    try:
        main.startup_summary(cfg, False, TG(), {"url": "https://pc.tail1.ts.net/"})
    finally:
        _t.sleep = orig
    out = capsys.readouterr().out
    assert "Telegram    running ✓" in out and "Phone       https://pc.tail1.ts.net/ ✓" in out
    main.startup_summary(cfg, False, None, {})
    out = capsys.readouterr().out
    assert "Telegram    OFF — add your bot token" in out


def test_real_serve_status_and_fixing_a_wrong_mapping(ts, monkeypatch):
    """Tailscale's real JSON: the address pointed at another local app → Nova puts it back."""
    real = {"TCP": {"443": {"HTTPS": True}, "8443": {"HTTPS": True}},
            "Web": {"alex-pc.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:4173"}}},
                    "alex-pc.tail1234.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:4173"}}}}}
    assert remote.serve_map(json.dumps(real)) == {"443": "http://127.0.0.1:4173", "8443": "http://127.0.0.1:4173"}
    fixed = []
    orig = remote._run

    def run(args, timeout=20):
        if args[:3] == ["serve", "status", "--json"]:
            return 0, json.dumps(real)
        if args[:2] == ["serve", "--bg"] and not any(a.startswith("--https=") for a in args):
            real["Web"]["alex-pc.tail1234.ts.net:443"]["Handlers"]["/"]["Proxy"] = args[-1]
            fixed.append(args[-1])
            return 0, ""
        return orig(args, timeout)
    monkeypatch.setattr(remote, "_run", run)
    monkeypatch.setattr(remote, "_serve", lambda args, wait=25: ("ok", run(args)[1]))
    remote._cache.update(t=0.0, status=None)
    st = remote.status(fresh=True)
    assert not st["serving"] and "instead of Nova" in st["message"]
    st = remote.ensure()
    assert fixed == ["http://127.0.0.1:8765"] and st["serving"] and st["url"] == "https://alex-pc.tail1234.ts.net/"
