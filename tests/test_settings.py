"""Settings page backend: comment-preserving saves, secrets handling, validation, security."""
import json
import shutil
import urllib.request
from pathlib import Path

import pytest
import yaml

from nova import settings

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def cfgfiles(tmp_path, monkeypatch):
    cfg, env = tmp_path / "config.yaml", tmp_path / ".env"
    shutil.copy(ROOT / "config.example.yaml", cfg)
    shutil.copy(ROOT / ".env.example", env)
    monkeypatch.setattr(settings, "CONFIG", cfg)
    monkeypatch.setattr(settings, "ENV", env)
    return cfg, env


def test_save_keeps_comments_and_quotes_times(cfgfiles):
    cfg, _ = cfgfiles
    r = settings.apply({
        "values": {"assistant.owner": "Pieter", "voice.wake_threshold": 0.6, "telegram.allowed_user_ids": "123, 456",
                   "dashboard.theme.accent": "#52FFA8"},
        "routines": [{"name": "Brief", "at": "7:30", "days": ["mon", "fri"], "prompt": "Morning briefing", "speak": True}],
    })
    assert not r["errors"] and r["restart"]
    text = cfg.read_text()
    assert text.startswith("# ─")                                                    # header comment kept
    assert "wake_word: hey_jarvis      # built-in openWakeWord models" in text      # inline comments survive
    data = yaml.safe_load(text)                                                     # PyYAML (what Nova uses) reads it right
    assert data["assistant"]["owner"] == "Pieter"
    assert data["telegram"]["allowed_user_ids"] == [123, 456]
    assert data["routines"][0]["at"] == "07:30" and data["routines"][0]["days"] == ["mon", "fri"]
    assert data["dashboard"]["theme"]["accent"] == "#52ffa8"
    assert (cfg.with_suffix(".yaml.bak")).exists()


def test_validation_errors(cfgfiles):
    r = settings.apply({"values": {"dashboard.theme.accent": "green", "tts.engine": "robot", "no.such": 1},
                        "routines": [{"at": "25:00", "prompt": "x"}]})
    assert set(r["errors"]) == {"dashboard.theme.accent", "tts.engine", "no.such", "routines"}


def test_theme_only_changes_need_no_restart(cfgfiles):
    assert settings.apply({"values": {"dashboard.theme.bloom": 1.2}})["restart"] is False
    assert settings.theme()["bloom"] == 1.2


def test_secrets_masked_and_written_in_place(cfgfiles, monkeypatch):
    _, env = cfgfiles
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings.apply({"secrets": {"GEMINI_API_KEY": "AIzaSyTEST1234567890wxyz", "NOT_A_KEY": "x"}})
    text = env.read_text()
    assert "GEMINI_API_KEY=AIzaSyTEST1234567890wxyz" in text and "# Gemini" in text and "NOT_A_KEY" not in text
    snap = settings.snapshot()
    g = next(s for s in snap["secrets"] if s["key"] == "GEMINI_API_KEY")
    assert g["set"] and g["hint"].endswith("wxyz") and "AIzaSy" not in json.dumps(snap)
    settings.apply({"secrets": {"GEMINI_API_KEY": None}})
    assert "GEMINI_API_KEY=\n" in env.read_text()


def test_core_skills_cannot_be_disabled(cfgfiles):
    cfg, _ = cfgfiles
    settings.apply({"extensions": {"skills": {"system": False, "browser": False}, "plugins": {"currency": False},
                                   "mcp_servers": {"windows": True}}})
    data = yaml.safe_load(cfg.read_text())
    assert data["skills"]["disabled"] == ["browser"]
    assert data["plugins"]["disabled"] == ["currency"]
    assert data["mcp_servers"]["windows"]["enabled"] is True


def test_dashboard_rejects_foreign_hosts_and_origins(nova, cfgfiles):
    from nova.dashboard.server import Dashboard
    cfg, _ = nova
    cfg["dashboard"]["open_on_start"] = False
    cfg["dashboard"]["port"] = 8799
    Dashboard(cfg, None).start()

    def call(path, headers=None, data=None):
        req = urllib.request.Request(f"http://127.0.0.1:8799{path}", headers=headers or {},
                                     data=json.dumps(data).encode() if data is not None else None)
        try:
            return urllib.request.urlopen(req, timeout=5).status
        except urllib.error.HTTPError as e:
            return e.code
    assert call("/api/settings") == 200
    assert call("/api/settings", {"Host": "evil.example"}) == 403
    assert call("/api/settings", {"Origin": "http://evil.example", "Content-Type": "application/json"}, {}) == 403
    assert call("/api/settings", {"Origin": "http://127.0.0.1:8799", "Content-Type": "application/json"},
                {"values": {"dashboard.theme.bloom": 1.0}}) == 200
