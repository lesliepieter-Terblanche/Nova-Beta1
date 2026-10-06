"""v2.34: more free model providers with automatic switching, Playwright MCP, Real-ESRGAN, Postiz, the skills
library, the one-time switch-on — and God's Eye View removed."""
import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from nova import context, llm, settings, skill_library, upscale
from nova.llm import LLM, LLMReply


# ── free model providers ──────────────────────────────────
class _Limit(Exception):
    status_code = 429


def _llm(nova, monkeypatch, keys=("GROQ_API_KEY", "CEREBRAS_API_KEY", "MISTRAL_API_KEY", "GITHUB_MODELS_TOKEN")):
    cfg, _ = nova
    for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "MISTRAL_API_KEY", "GITHUB_MODELS_TOKEN",
              "XAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k in keys:
        monkeypatch.setenv(k, "test-key")
    return LLM(cfg)


def test_new_free_providers_join_the_chain(nova, monkeypatch):
    m = _llm(nova, monkeypatch)
    assert m.smart == ["groq", "cerebras", "mistral", "github"]                # in the configured order
    assert str(m.providers["cerebras"].client.base_url).startswith("https://api.cerebras.ai/v1")
    assert str(m.providers["mistral"].client.base_url).startswith("https://api.mistral.ai/v1")
    assert str(m.providers["github"].client.base_url).startswith("https://models.github.ai/inference")
    assert m.providers["github"].catalog_url and m.providers["github"].model == "openai/gpt-4o-mini"
    assert "gemini" not in m.providers                                         # no key, no provider
    # an older config.yaml that has never heard of them still gets them (built-in defaults)
    del nova[0]["llm"]["providers"]["cerebras"], nova[0]["llm"]["providers"]["mistral"]
    nova[0]["llm"]["smart"] = ["gemini", "groq", "xai"]
    assert LLM(nova[0]).smart == ["groq", "cerebras", "mistral", "github"]


def test_model_choice_for_the_new_providers():
    assert llm.pick_model(["llama3.1-8b", "gpt-oss-120b", "qwen-3-32b"], "cerebras", "auto") == "gpt-oss-120b"
    assert llm.pick_model(["mistral-embed", "mistral-ocr-latest", "codestral-latest", "mistral-small-latest"],
                          "mistral", "retired-model") == "mistral-small-latest"
    assert llm.pick_model(["openai/gpt-4.1", "meta/llama-4", "openai/gpt-4o-mini"], "github", "") == "openai/gpt-4o-mini"


def test_a_provider_that_hits_its_limit_is_rested(nova, monkeypatch):
    m = _llm(nova, monkeypatch)
    calls = []

    def fake(name, result):
        def chat(messages, tools=None, temperature=0.3, on_delta=None):
            calls.append(name)
            if isinstance(result, Exception):
                raise result
            return LLMReply(result, provider=name)
        return chat
    monkeypatch.setattr(m.providers["groq"], "chat", fake("groq", _Limit("Rate limit reached: tokens per day (TPD)")))
    monkeypatch.setattr(m.providers["cerebras"], "chat", fake("cerebras", _Limit("429 too many requests")))
    monkeypatch.setattr(m.providers["mistral"], "chat", fake("mistral", "Hello from Mistral."))
    assert m.chat([{"role": "user", "content": "hi"}], prefer_smart=True).content == "Hello from Mistral."
    assert calls == ["groq", "cerebras", "mistral"] and m.last_provider == "mistral"
    rest = m.resting()
    assert 1700 < rest["groq"] <= 1800 and 50 < rest["cerebras"] <= 60          # the day's allowance vs a minute
    calls.clear()
    m.chat([{"role": "user", "content": "again"}], prefer_smart=True)
    assert calls == ["mistral"]                                                 # the rested ones aren't asked again
    # everything else down: a rested provider gets another go rather than giving up
    monkeypatch.setattr(m.providers["mistral"], "chat", fake("mistral", RuntimeError("server error")))
    monkeypatch.setattr(m.providers["github"], "chat", fake("github", RuntimeError("server error")))
    monkeypatch.setattr(m.providers["ollama"], "chat", fake("ollama", RuntimeError("not running")))
    monkeypatch.setattr(m.providers["cerebras"], "chat", fake("cerebras", "Back again."))
    calls.clear()
    assert m.chat([{"role": "user", "content": "hi"}], prefer_smart=True).content == "Back again."
    assert calls == ["mistral", "github", "ollama", "groq", "cerebras"] and "cerebras" not in m.resting()
    assert llm.rest_seconds(ValueError("malformed tool arguments")) == 0        # an ordinary error rests nothing


def test_settings_know_the_new_keys_and_addons(nova):
    keys = {s["key"]: s for s in settings.SECRETS}
    assert keys["CEREBRAS_API_KEY"]["test"] == "cerebras" and keys["MISTRAL_API_KEY"]["test"] == "mistral"
    assert keys["GITHUB_MODELS_TOKEN"]["test"] == "github" and keys["POSTIZ_API_KEY"]["test"] == "postiz"
    assert {"cerebras", "mistral", "github"} <= set(settings.FIELD_BY_PATH["llm.primary"]["options"])
    spec = settings.mcp_catalog_spec("playwright")
    assert spec["enabled"] and spec["command"] == "npx" and "@playwright/mcp@latest" in spec["args"]
    assert "{workspace}" not in json.dumps(spec)
    assert "globe.auto_start" not in settings.FIELD_BY_PATH


# ── Real-ESRGAN ───────────────────────────────────────────
def _fake_esrgan(tmp, monkeypatch, fail=""):
    """Stands in for the real program: writes a 4× picture, or fails the way a PC without Vulkan does."""
    tool = tmp / "tools" / "realesrgan"
    (tool / "models").mkdir(parents=True)
    (tool / "models" / f"{upscale.MODEL}.bin").write_bytes(b"x")
    monkeypatch.setattr(upscale, "tool_dir", lambda: tool)
    upscale.exe_path().write_bytes(b"x")
    ran = []

    class P:
        returncode, stdout, stderr = (1, "", fail) if fail else (0, "", "")

    def run(cmd, **kw):
        ran.append(cmd)
        if not fail:
            with Image.open(cmd[cmd.index("-i") + 1]) as im:
                im.resize((im.width * 4, im.height * 4)).save(cmd[cmd.index("-o") + 1])
        return P()
    monkeypatch.setattr(upscale.subprocess, "run", run)
    monkeypatch.setattr(upscale, "_failed", "")
    return ran


def test_upscale_photo_and_fallback(nova, monkeypatch):
    _, tmp = nova
    from nova.skills.cinematic import upscale_photo
    photo = tmp / "files" / "small.jpg"
    Image.new("RGB", (300, 200), (90, 140, 200)).save(photo)
    ran = _fake_esrgan(tmp, monkeypatch)
    out = upscale_photo(str(photo), scale=2)
    assert "Sharpened 2× with Real-ESRGAN" in out and ran and upscale.MODEL in ran[0]
    assert Image.open(tmp / "files" / "small_sharp.jpg").size == (600, 400)
    assert not list((tmp / "files").glob("*_in.png")) and not list((tmp / "files").glob("*_x4.png"))
    _fake_esrgan(tmp / "b", monkeypatch, fail="vkCreateInstance failed -9")
    out = upscale_photo(str(photo), scale=4)
    assert "plain enlargement" in out and "Vulkan" in out
    assert Image.open(tmp / "files" / "small_sharp.jpg").size == (600, 400)      # the fallback stops at 2×
    assert "ERROR" in upscale_photo(str(tmp / "files" / "nothing.jpg"))


def test_only_small_photos_are_sharpened_for_a_frame(nova, monkeypatch):
    _, tmp = nova
    monkeypatch.setattr(upscale, "resolve", lambda p: tmp / p)
    ran = _fake_esrgan(tmp, monkeypatch)
    small, big = tmp / "files" / "s.jpg", tmp / "files" / "b.jpg"
    Image.new("RGB", (400, 300), (10, 20, 30)).save(small)
    Image.new("RGB", (1600, 2400), (10, 20, 30)).save(big)
    assert upscale.for_frame(big, (1080, 1920)) == (big, False) and not ran       # big enough already
    assert upscale.for_frame(small, (1080, 1920), "off") == (small, False) and not ran
    path, did = upscale.for_frame(small, (1080, 1920))
    assert did and Image.open(path).size == (1600, 1200) and len(ran) == 1
    assert upscale.for_frame(small, (1080, 1920)) == (path, True) and len(ran) == 1   # kept, not redone
    _fake_esrgan(tmp / "c", monkeypatch, fail="no vulkan device")
    other = tmp / "files" / "o.jpg"
    Image.new("RGB", (400, 300), (1, 2, 3)).save(other)
    assert upscale.for_frame(other, (1080, 1920)) == (other, False)
    assert upscale.for_frame(other, (1080, 1920)) == (other, False)               # and it doesn't keep trying


# ── Postiz ────────────────────────────────────────────────
@pytest.fixture()
def postiz(nova, monkeypatch):
    from nova.skills import social
    log = []
    have = [{"id": "ig1", "name": "Harbour Homes", "identifier": "instagram", "profile": "harbourhomes"},
            {"id": "tt1", "name": "Harbour Homes", "identifier": "tiktok", "profile": "harbourhomes"},
            {"id": "li1", "name": "Alex Example", "identifier": "linkedin", "profile": "alex"},
            {"id": "x9", "name": "Old", "identifier": "x", "disabled": True}]

    def call(method, path, **kw):
        log.append((method, path, kw))
        if path == "/integrations":
            return have
        if path == "/upload":
            return {"id": "m1", "path": "https://uploads.example/reel.mp4"}
        if path == "/posts" and method == "POST":
            return [{"postId": f"p{i}", "integration": p["integration"]["id"]} for i, p in enumerate(kw["json"]["posts"])]
        if path == "/posts":
            return {"posts": [{"publishDate": "2026-10-12T16:00:00.000Z", "content": "<p>Wake up to the ocean</p>",
                               "integration": {"providerIdentifier": "instagram", "name": "Harbour Homes"}}]}
        raise AssertionError(path)
    monkeypatch.setattr(social, "call", call)
    return social, log


def test_social_post_is_scheduled_with_media_and_asks_first(postiz, nova):
    social, log = postiz
    _, tmp = nova
    from nova.agent import needs_yes
    from nova.tools import REGISTRY, select_tools
    assert needs_yes(REGISTRY["social_post"], {}) and not needs_yes(REGISTRY["social_channels"], {})
    assert "social_post" in {t.name for t in select_tools("post this reel to instagram tomorrow")}
    assert social.social_channels() == ("Connected channels: Instagram (harbourhomes); TikTok (harbourhomes); "
                                        "LinkedIn (alex).")
    reel = tmp / "files" / "reel.mp4"
    reel.write_bytes(b"fake video")
    out = social.social_post("Wake up to the ocean. #capetown", "instagram and tik tok", str(reel), "tomorrow 6pm",
                             title="Sea Point penthouse")
    assert out.startswith("Scheduled for ") and "Instagram (harbourhomes), TikTok (harbourhomes) with 1 file (2 posts)" in out
    body = next(kw["json"] for m, p, kw in log if (m, p) == ("POST", "/posts"))
    assert body["type"] == "schedule" and body["date"].endswith(".000Z") and body["shortLink"] is False
    ig, tt = body["posts"]
    assert ig["integration"] == {"id": "ig1"} and ig["settings"]["__type"] == "instagram" and ig["settings"]["post_type"] == "post"
    assert ig["value"] == [{"content": "Wake up to the ocean. #capetown",
                            "image": [{"id": "m1", "path": "https://uploads.example/reel.mp4"}]}]
    assert tt["settings"]["privacy_level"] == "PUBLIC_TO_EVERYONE" and tt["settings"]["title"] == "Sea Point penthouse"
    assert sum(1 for m, p, _ in log if p == "/upload") == 1


def test_social_post_refuses_rather_than_guessing(postiz, nova):
    social, log = postiz
    _, tmp = nova
    assert "no connected channel matches facebook" in social.social_post("hi", "facebook")
    assert "needs a video" in social.social_post("hi", "tiktok")
    assert "in the past" in social.social_post("hi", "linkedin", when="1 January 2020")
    assert "can't attach" in social.social_post("hi", "linkedin", media=str(tmp / "files" / "missing.png"))
    assert not [1 for m, p, _ in log if (m, p) == ("POST", "/posts")]                 # nothing went out
    assert social.social_post("Quarter closed.", "linkedin").startswith("Sent for posting now on LinkedIn (alex)")
    assert social.social_post("Idea", "linkedin", draft=True).startswith("Saved as a draft")
    assert "1 post scheduled" in social.social_scheduled() and "Instagram: Wake up to the ocean" in social.social_scheduled()
    assert social.pick_channels("", [{"id": "a"}])[0] == [{"id": "a"}]


def test_postiz_without_a_key_says_what_to_do(nova, monkeypatch):
    from nova.skills import social
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    assert "POSTIZ_API_KEY" in social.social_channels()
    nova[0]["social"] = {"postiz_url": "http://localhost:4007/api/"}
    assert social.base_url() == "http://localhost:4007/api/public/v1"
    nova[0]["social"] = {}
    assert social.base_url() == "https://api.postiz.com/public/v1"


# ── the skills library ────────────────────────────────────
APACHE = "\n                                 Apache License\n                           Version 2.0, January 2004\n"


def _library_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        def skill(name, lic, body="# Guide\n\nDo the thing well.\n"):
            z.writestr(f"skills-main/skills/{name}/SKILL.md", f"---\nname: {name}\ndescription: About {name}.\n---\n{body}")
            if lic:
                z.writestr(f"skills-main/skills/{name}/LICENSE.txt", lic)
        skill("frontend-design", APACHE, "# Frontend Design\n\n" + ("Make deliberate choices. " * 300))
        z.writestr("skills-main/skills/frontend-design/reference/type.md", "Type scale notes")
        skill("internal-comms", APACHE)
        skill("claude-api", APACHE)
        skill("docx", "© 2025 Anthropic, PBC. All rights reserved.")
        skill("doc-coauthoring", "")
        z.writestr("skills-main/README.md", "readme")
    return buf.getvalue()


def test_skill_library_becomes_playbooks(nova, monkeypatch):
    _, tmp = nova
    from nova import extensions
    monkeypatch.setattr(skill_library, "library_dir", lambda: tmp / "playbooks" / "library")
    monkeypatch.setattr(skill_library, "files_dir", lambda: tmp / "skill-library")
    res = skill_library.import_zip(_library_zip())
    assert res["added"] == ["claude-api", "frontend-design", "internal-comms"]
    assert res["skipped"] == {"doc-coauthoring": "no licence file",
                              "docx": "not open source (Anthropic doesn't allow copying it)"}
    assert res["off"] == ["lib-claude-api"]                                        # written for Claude's own apps
    assert not (tmp / "skill-library" / "docx").exists()
    assert (tmp / "skill-library" / "frontend-design" / "LICENSE.txt").exists()   # the licence travels with it
    assert (tmp / "skill-library" / "frontend-design" / "reference" / "type.md").read_text() == "Type scale notes"
    nova[0]["playbooks"] = {"disabled": res["off"]}
    books = {b.name: b for b in extensions.load_playbooks(tmp / "playbooks")}
    assert set(books) == {"lib-frontend-design", "lib-internal-comms"}
    fd = books["lib-frontend-design"]
    assert "design a website" in fd.triggers and fd.groups == ["web", "files"]
    assert len(fd.body) < 3000 and "written for Claude" in fd.body and "read_file" in fd.body
    assert [b.name for b in extensions.matching_playbooks("please design a website for Harbour Homes")] == ["lib-frontend-design"]
    assert skill_library.installed()["skills"] == res["added"]
    extensions.load_playbooks()


# ── switched on once, God's Eye View gone ─────────────────
def test_one_time_switch_on(nova, monkeypatch):
    cfg, tmp = nova
    from nova import activate
    doc = {"globe": {"dir": "tools/gods-eye-view"}, "skills": {"disabled": ["globe", "weather"]}}
    saved = []
    monkeypatch.setattr(settings, "load_doc", lambda: doc)
    monkeypatch.setattr(settings, "save_doc", lambda d: saved.append(json.loads(json.dumps(d))))
    monkeypatch.setattr(activate, "resolve", lambda p: tmp / "nowhere" / p)        # never the real tools folder
    monkeypatch.setattr(activate.shutil, "which", lambda name: "/usr/bin/npx")
    monkeypatch.setattr(skill_library, "install", lambda: {"added": ["frontend-design"], "off": [], "skipped": {}})
    monkeypatch.setattr(upscale, "install", lambda: Path("x"))
    told = []
    monkeypatch.setattr(context, "push", lambda text, files=None: told.append(text))
    for k in ("CEREBRAS_API_KEY", "MISTRAL_API_KEY", "GITHUB_MODELS_TOKEN", "POSTIZ_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    cfg["globe"] = {"dir": "tools/gods-eye-view"}
    done = activate.run(cfg, tmp, wait=0, background=False)
    assert done == {"globe_removed": True, "playwright": True, "library": True, "upscaler": True, "told_keys": True}
    assert "globe" not in doc and doc["skills"]["disabled"] == ["weather"]
    assert doc["mcp_servers"]["playwright"]["enabled"] and cfg["mcp_servers"]["playwright"]["command"] == "npx"
    assert len(told) == 1 and "Playwright browser control is on" in told[0] and "skills library is installed" in told[0]
    assert "Cerebras, Mistral, GitHub Models" in told[0] and "Postiz" in told[0]
    told.clear()
    activate.run(cfg, tmp, wait=0, background=False)                               # the second start: nothing again
    assert not told


def test_gods_eye_view_is_gone(nova):
    from nova import remote
    from nova.skills import SKILLS
    from nova.tools import REGISTRY
    assert "globe" not in SKILLS
    assert not {"show_on_globe", "planes_overhead", "install_globe", "globe_app"} & set(REGISTRY)
    assert not hasattr(remote, "_globe_port") and "globe_url" not in remote.status(fresh=True)
    root = Path(__file__).resolve().parent.parent
    assert not (root / "nova" / "skills" / "globe.py").exists() and not (root / "nova" / "dashboard" / "globe.html").exists()
    page = (root / "nova" / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "/globe" not in page and "God's Eye" not in page
