"""Settings: read and safely update config.yaml and .env from the dashboard.

- config.yaml is edited with ruamel.yaml in round-trip mode, so your comments and layout survive.
- .env lines are updated in place (comments kept); secrets are never sent back to the browser in full.
- The SCHEMA below drives the Settings page: add a field here and it appears in the UI.
"""
from __future__ import annotations

import os
import re
import shutil
import threading
from pathlib import Path

from .config import ROOT

CONFIG = ROOT / "config.yaml"
TEMPLATE = ROOT / "config.example.yaml"
ENV = ROOT / ".env"
ENV_TEMPLATE = ROOT / ".env.example"
_lock = threading.Lock()

# ── secrets (.env) ────────────────────────────────────────
SECRETS = [
    {"key": "ELEVENLABS_API_KEY", "label": "ElevenLabs API key", "group": "Voice",
     "help": "elevenlabs.io → Profile → API keys", "link": "https://elevenlabs.io/app/settings/api-keys"},
    {"key": "ELEVENLABS_VOICE_ID", "label": "ElevenLabs voice ID", "group": "Voice", "plain": True,
     "help": "Voices → ⋯ → Copy voice ID. Blank = stock George."},
    {"key": "GEMINI_API_KEY", "label": "Gemini API key", "group": "AI models",
     "help": "Free — smart model + vision", "link": "https://aistudio.google.com/apikey", "test": "gemini"},
    {"key": "GROQ_API_KEY", "label": "Groq API key", "group": "AI models",
     "help": "Free — very fast backup model (starts with gsk_)", "link": "https://console.groq.com/keys", "test": "groq"},
    {"key": "XAI_API_KEY", "label": "xAI Grok API key", "group": "AI models",
     "help": "Paid — Grok models (starts with xai-)", "link": "https://console.x.ai", "test": "xai"},
    {"key": "TELEGRAM_BOT_TOKEN", "label": "Telegram bot token", "group": "Remote",
     "help": "Telegram → @BotFather → /newbot", "link": "https://t.me/BotFather", "test": "telegram"},
    {"key": "PEXELS_API_KEY", "label": "Pexels API key", "group": "Media",
     "help": "Free stock footage for videos", "link": "https://www.pexels.com/api/"},
    {"key": "HA_TOKEN", "label": "Home Assistant token", "group": "MCP servers",
     "help": "HA → Profile → Security → Long-lived access tokens"},
    {"key": "GITHUB_TOKEN", "label": "GitHub token", "group": "MCP servers",
     "help": "For the GitHub MCP server", "link": "https://github.com/settings/tokens"},
]
SECRET_KEYS = {s["key"] for s in SECRETS}

# ── config fields ─────────────────────────────────────────
# type: text | number | bool | select | list | color | tags(int list)
SCHEMA = [
    {"id": "general", "title": "General", "icon": "user", "fields": [
        {"path": "assistant.name", "label": "Assistant name", "type": "text"},
        {"path": "assistant.owner", "label": "Your name", "type": "text"},
        {"path": "assistant.timezone", "label": "Time zone", "type": "text", "help": "IANA name, e.g. Africa/Johannesburg"},
        {"path": "assistant.city", "label": "Home city", "type": "text", "help": "Used for weather in briefings"},
        {"path": "assistant.max_history_turns", "label": "Short-term memory (turns)", "type": "number", "min": 2, "max": 50},
        {"path": "dashboard.open_on_start", "label": "Open dashboard on start", "type": "bool"},
        {"path": "system.allow_shell", "label": "Allow shell commands (always asks first)", "type": "bool"},
    ]},
    {"id": "voice", "title": "Voice", "icon": "mic", "fields": [
        {"path": "voice.enabled", "label": "Voice enabled", "type": "bool"},
        {"path": "voice.wake_word", "label": "Wake word", "type": "select",
         "options": ["hey_jarvis", "alexa", "hey_mycroft", "models/hey_nova.onnx"], "free": True},
        {"path": "voice.wake_threshold", "label": "Wake sensitivity threshold", "type": "number", "min": 0.1, "max": 0.95, "step": 0.05,
         "help": "Higher = fewer false wakes"},
        {"path": "voice.hotkey", "label": "Push-to-talk hotkey", "type": "text", "help": "e.g. <ctrl>+<alt>+<space> — blank disables"},
        {"path": "voice.barge_in", "label": "Interrupt Nova with the wake word", "type": "bool"},
        {"path": "voice.stt_model", "label": "Speech recognition model", "type": "select",
         "options": ["tiny.en", "base.en", "small.en", "base", "small"]},
        {"path": "voice.stt_language", "label": "Language", "type": "select", "options": ["en", "af", ""],
         "help": "Blank = auto-detect (use a multilingual model)", "free": True},
        {"path": "voice.silence_seconds", "label": "Pause that ends a sentence (s)", "type": "number", "min": 0.5, "max": 3, "step": 0.1},
        {"path": "voice.follow_up_seconds", "label": "Follow-up window (s)", "type": "number", "min": 0, "max": 20},
        {"path": "voice.max_spoken_chars", "label": "Longest spoken answer (chars)", "type": "number", "min": 100, "max": 3000},
        {"path": "voice.chime", "label": "Chime when listening", "type": "bool"},
        {"path": "tts.engine", "label": "Main voice", "type": "select", "options": ["elevenlabs", "kokoro", "piper", "windows"]},
        {"path": "tts.fallback", "label": "Backup voices (in order)", "type": "list", "help": "One per line: kokoro, piper, windows"},
        {"path": "tts.elevenlabs.model", "label": "ElevenLabs model", "type": "select",
         "options": ["eleven_flash_v2_5", "eleven_turbo_v2_5", "eleven_multilingual_v2"], "free": True},
        {"path": "tts.elevenlabs.stability", "label": "ElevenLabs stability", "type": "number", "min": 0, "max": 1, "step": 0.05},
        {"path": "tts.elevenlabs.similarity_boost", "label": "ElevenLabs similarity", "type": "number", "min": 0, "max": 1, "step": 0.05},
        {"path": "tts.elevenlabs.speed", "label": "ElevenLabs speed", "type": "number", "min": 0.7, "max": 1.2, "step": 0.05},
        {"path": "tts.kokoro.voice", "label": "Kokoro voice", "type": "select",
         "options": ["bm_george", "bm_lewis", "bm_daniel", "bm_fable", "bf_emma", "bf_isabella", "bf_alice", "bf_lily",
                     "am_adam", "am_michael", "am_eric", "af_heart", "af_bella", "af_nova", "af_sarah"]},
        {"path": "tts.kokoro.lang", "label": "Kokoro accent", "type": "select", "options": ["en-gb", "en-us"]},
        {"path": "tts.kokoro.speed", "label": "Kokoro speed", "type": "number", "min": 0.7, "max": 1.4, "step": 0.05},
    ]},
    {"id": "brain", "title": "AI brain", "icon": "brain", "fields": [
        {"path": "llm.primary", "label": "Everyday model", "type": "select", "options": ["ollama", "gemini", "groq", "xai"],
         "help": "ollama = private and free but slower on a 4 GB card; gemini/groq = much faster replies"},
        {"path": "llm.smart", "label": "Smart models (in order)", "type": "list", "help": "One per line, e.g. gemini, groq, xai"},
        {"path": "llm.providers.ollama.model", "label": "Local model (Ollama)", "type": "text"},
        {"path": "llm.providers.gemini.model", "label": "Gemini model", "type": "select",
         "options": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"], "free": True},
        {"path": "llm.providers.groq.model", "label": "Groq model", "type": "text", "default": "auto",
         "help": "auto = Nova picks the best model your key can use (and switches when Groq retires one)"},
        {"path": "llm.providers.xai.model", "label": "Grok model", "type": "text", "default": "auto",
         "help": "auto, or e.g. grok-4-fast"},
        {"path": "llm.keep_alive", "label": "Keep local model loaded", "type": "select", "default": "24h",
         "options": ["24h", "2h", "30m", "5m"], "help": "Longer = no waiting for the model to load"},
        {"path": "llm.vision_providers", "label": "Vision models (in order)", "type": "list"},
        {"path": "llm.escalate_keywords", "label": "Phrases that go to the smart model", "type": "list"},
        {"path": "llm.max_tool_rounds", "label": "Max tool steps per request", "type": "number", "min": 2, "max": 15},
        {"path": "brain.learn_automatically", "label": "Learn new memories after each conversation", "type": "bool"},
        {"path": "brain.embed_model", "label": "Embedding model", "type": "text"},
    ]},
    {"id": "telegram", "title": "Telegram", "icon": "send", "fields": [
        {"path": "telegram.enabled", "label": "Telegram bot enabled", "type": "bool"},
        {"path": "telegram.allowed_user_ids", "label": "Allowed Telegram user IDs", "type": "tags",
         "help": "Message your bot /id to find yours. Only these IDs can control Nova."},
        {"path": "telegram.voice_replies", "label": "Reply to voice notes with a voice note", "type": "bool"},
    ]},
    {"id": "google", "title": "Google", "icon": "mail", "fields": [
        {"path": "google.enabled", "label": "Google Workspace enabled", "type": "bool"},
        {"path": "meetings.create_tasks", "label": "Add my meeting action items to Google Tasks", "type": "bool"},
        {"path": "meetings.stt_model", "label": "Meeting transcription model", "type": "select",
         "options": ["base.en", "small.en", "medium.en", "small"]},
    ]},
    {"id": "files", "title": "Files & web", "icon": "folder", "fields": [
        {"path": "files.allowed_roots", "label": "Folders Nova may use", "type": "list", "help": "One folder per line"},
        {"path": "web.searxng_url", "label": "SearXNG URL (private search)", "type": "text", "help": "e.g. http://localhost:8888"},
        {"path": "media.captions", "label": "Captions in videos", "type": "bool"},
        {"path": "media.broll", "label": "Stock footage in videos (Pexels)", "type": "bool"},
        {"path": "media.image_gen_enabled", "label": "Local image generation", "type": "bool", "help": "Needs requirements-imagegen.txt"},
    ]},
    {"id": "appearance", "title": "Appearance", "icon": "palette", "fields": [
        {"path": "dashboard.theme.accent", "label": "Accent colour", "type": "color", "default": "#8b7bff"},
        {"path": "dashboard.theme.accent2", "label": "Second accent", "type": "color", "default": "#4cc9f0"},
        {"path": "dashboard.theme.background", "label": "Space background", "type": "color", "default": "#03040a"},
        {"path": "dashboard.theme.bloom", "label": "Glow intensity", "type": "number", "min": 0, "max": 2.5, "step": 0.05, "default": 0.85},
        {"path": "dashboard.theme.stars", "label": "Star density", "type": "number", "min": 0, "max": 12000, "step": 500, "default": 4500},
        {"path": "dashboard.theme.orbit_speed", "label": "Cinematic orbit speed", "type": "number", "min": 0, "max": 5, "step": 0.1, "default": 1.0},
        {"path": "dashboard.theme.labels", "label": "Show category labels", "type": "bool", "default": True},
    ]},
]
PRESETS = {
    "Nebula (default)": {"accent": "#8b7bff", "accent2": "#4cc9f0", "background": "#03040a"},
    "Aurora": {"accent": "#52ffa8", "accent2": "#4cc9f0", "background": "#020a0a"},
    "Solar": {"accent": "#ffb347", "accent2": "#ff5f6d", "background": "#0a0503"},
    "Westcon blue": {"accent": "#1f8fff", "accent2": "#00c2ff", "background": "#02060f"},
    "Monochrome": {"accent": "#e6e6e6", "accent2": "#9aa0b4", "background": "#050505"},
}

FIELD_BY_PATH = {f["path"]: f for s in SCHEMA for f in s["fields"]}


# ── YAML round-trip helpers ───────────────────────────────
def _yaml():
    from ruamel.yaml import YAML
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)       # matches config.example.yaml's "    - item" style
    return y


def _ensure_config() -> Path:
    if not CONFIG.exists():
        shutil.copy(TEMPLATE, CONFIG)
    return CONFIG


def load_doc():
    with open(_ensure_config(), encoding="utf-8") as f:
        return _yaml().load(f)


def save_doc(doc) -> None:
    tmp = CONFIG.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        _yaml().dump(doc, f)
    shutil.copy(CONFIG, CONFIG.with_suffix(".yaml.bak"))          # one-step undo
    tmp.replace(CONFIG)


def get_path(doc, path: str, default=None):
    cur = doc
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def set_path(doc, path: str, value) -> None:
    from ruamel.yaml.comments import CommentedMap
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = CommentedMap()
        cur = cur[p]
    cur[parts[-1]] = value


def _plain(v):
    """ruamel objects -> plain JSON-able Python."""
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float):
        return float(v)
    return str(v)


def coerce(field: dict, value):
    t = field["type"]
    if t == "bool":
        return bool(value) if not isinstance(value, str) else value.lower() in ("1", "true", "yes", "on")
    if t == "number":
        n = float(value)
        if "min" in field:
            n = max(field["min"], n)
        if "max" in field:
            n = min(field["max"], n)
        return int(n) if float(n).is_integer() and field.get("step", 1) >= 1 else round(n, 4)
    if t == "list":
        items = value if isinstance(value, list) else str(value).splitlines()
        return [str(x).strip() for x in items if str(x).strip()]
    if t == "tags":
        items = value if isinstance(value, list) else re.split(r"[\s,]+", str(value))
        return [int(x) for x in items if str(x).strip().lstrip("-").isdigit()]
    if t == "color":
        v = str(value).strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError(f"'{v}' isn't a colour like #8b7bff")
        return v.lower()
    if t == "select" and not field.get("free") and value not in field["options"]:
        raise ValueError(f"'{value}' isn't one of {field['options']}")
    return str(value)


# ── .env helpers ──────────────────────────────────────────
def read_env() -> dict[str, str]:
    out = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def mask(value: str) -> str:
    if not value:
        return ""
    return ("•" * 8) + value[-4:] if len(value) > 8 else "•" * len(value)


def write_env(updates: dict[str, str | None]) -> None:
    """Set (or clear with None/"") keys in .env, keeping every other line and comment."""
    if not ENV.exists():
        shutil.copy(ENV_TEMPLATE, ENV) if ENV_TEMPLATE.exists() else ENV.write_text("", encoding="utf-8")
    lines = ENV.read_text(encoding="utf-8").splitlines()
    done = set()
    for i, line in enumerate(lines):
        m = re.match(r"\s*([A-Z0-9_]+)\s*=", line)
        if m and m.group(1) in updates:
            k = m.group(1)
            lines[i] = f"{k}={updates[k] or ''}"
            done.add(k)
    for k, v in updates.items():
        if k not in done:
            lines.append(f"{k}={v or ''}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for k, v in updates.items():                  # apply to this process too
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


# ── extensions ────────────────────────────────────────────
def extension_inventory(doc) -> dict:
    from .skills import SKILLS
    disabled_skills = set(get_path(doc, "skills.disabled", []) or [])
    disabled_plugins = set(get_path(doc, "plugins.disabled", []) or [])
    disabled_books = set(get_path(doc, "playbooks.disabled", []) or [])
    plugins = sorted(p.stem for p in (ROOT / "plugins").glob("*.py") if not p.name.startswith("_"))
    books = []
    for f in sorted((ROOT / "playbooks").rglob("*.md")):
        if f.name.lower() == "readme.md":
            continue
        m = re.search(r"^description:\s*(.+)$", f.read_text(encoding="utf-8"), re.M)
        name_m = re.search(r"^name:\s*(.+)$", f.read_text(encoding="utf-8"), re.M)
        name = name_m.group(1).strip() if name_m else f.stem
        books.append({"name": name, "description": m.group(1).strip() if m else "", "enabled": name not in disabled_books})
    servers = []
    for name, spec in (get_path(doc, "mcp_servers", {}) or {}).items():
        spec = spec or {}
        servers.append({"name": name, "enabled": bool(spec.get("enabled", True)),
                        "target": spec.get("url") or " ".join([str(spec.get("command", ""))] + [str(a) for a in spec.get("args", [])]),
                        "confirm": str(spec.get("confirm", "auto"))})
    return {
        "skills": [{"name": s, "enabled": s not in disabled_skills, "core": s in ("system", "memory", "maintenance")}
                   for s in SKILLS],
        "plugins": [{"name": p, "enabled": p not in disabled_plugins} for p in plugins],
        "playbooks": books,
        "mcp_servers": servers,
        "mcp_import_claude_desktop": bool(get_path(doc, "mcp_import_claude_desktop", False)),
    }


# ── public API used by the dashboard ──────────────────────
def snapshot() -> dict:
    doc = load_doc()
    env = read_env()
    values = {}
    for f in FIELD_BY_PATH.values():
        v = get_path(doc, f["path"], f.get("default"))
        values[f["path"]] = _plain(v)
    return {
        "schema": SCHEMA,
        "values": values,
        "secrets": [{**s, "set": bool(env.get(s["key"])),
                     "hint": env.get(s["key"], "") if s.get("plain") else mask(env.get(s["key"], ""))}
                    for s in SECRETS],
        "extensions": extension_inventory(doc),
        "routines": _plain(get_path(doc, "routines", []) or []),
        "presets": PRESETS,
        "google_connected": (ROOT / "secrets" / "token.json").exists(),
    }


def apply(payload: dict) -> dict:
    """payload = {values: {path: v}, secrets: {KEY: v|None}, extensions: {...}, routines: [...]}.
    Returns {"saved": [...], "errors": {...}, "restart": bool}."""
    errors, saved = {}, []
    with _lock:
        doc = load_doc()
        for path, v in (payload.get("values") or {}).items():
            f = FIELD_BY_PATH.get(path)
            if not f:
                errors[path] = "unknown setting"
                continue
            try:
                set_path(doc, path, coerce(f, v))
                saved.append(path)
            except Exception as e:
                errors[path] = str(e)

        ext = payload.get("extensions")
        if ext:
            for kind, cfg_path in (("skills", "skills.disabled"), ("plugins", "plugins.disabled"),
                                   ("playbooks", "playbooks.disabled")):
                if kind in ext:
                    off = sorted(n for n, on in ext[kind].items() if not on
                                 and not (kind == "skills" and n in ("system", "memory", "maintenance")))
                    set_path(doc, cfg_path, off)
                    saved.append(cfg_path)
            for name, on in (ext.get("mcp_servers") or {}).items():
                if get_path(doc, f"mcp_servers.{name}") is not None:
                    set_path(doc, f"mcp_servers.{name}.enabled", bool(on))
                    saved.append(f"mcp_servers.{name}.enabled")
            if "mcp_import_claude_desktop" in ext:
                set_path(doc, "mcp_import_claude_desktop", bool(ext["mcp_import_claude_desktop"]))
                saved.append("mcp_import_claude_desktop")

        if "routines" in payload:
            try:
                set_path(doc, "routines", _validate_routines(payload["routines"]))
                saved.append("routines")
            except Exception as e:
                errors["routines"] = str(e)

        if saved:
            save_doc(doc)

        secrets = {k: (v.strip() if isinstance(v, str) else v) for k, v in (payload.get("secrets") or {}).items()}
        bad = [k for k in secrets if k not in SECRET_KEYS]
        for k in bad:
            errors[k] = "unknown key"
            secrets.pop(k)
        if secrets:
            write_env(secrets)
            saved += list(secrets)

    only_live = all(p.startswith("dashboard.theme.") for p in saved)
    return {"saved": saved, "errors": errors, "restart": bool(saved) and not only_live}


def _validate_routines(items) -> list:
    from ruamel.yaml.comments import CommentedMap, CommentedSeq
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString as Q
    out = CommentedSeq()
    for r in items or []:
        at = str(r.get("at", "")).strip()
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", at):
            raise ValueError(f"'{at}' isn't a time like 07:30")
        prompt = str(r.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("each routine needs something to do")
        days = [d[:3].lower() for d in r.get("days") or []
                if d[:3].lower() in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")]
        item = CommentedMap()
        item["name"] = str(r.get("name") or prompt[:40])
        item["at"] = Q(at.zfill(5))                   # quoted: unquoted 07:30 means 450 in YAML 1.1
        if days and len(days) < 7:
            seq = CommentedSeq(days)
            seq.fa.set_flow_style()
            item["days"] = seq
        item["prompt"] = Q(prompt)
        item["speak"] = bool(r.get("speak", False))
        item["telegram"] = bool(r.get("telegram", True))
        out.append(item)
    return out


def theme() -> dict:
    """Current dashboard theme, read fresh from config.yaml so changes apply without a restart."""
    try:
        t = _plain(get_path(load_doc(), "dashboard.theme", {}) or {})
    except Exception:
        t = {}
    defaults = {f["path"].split(".")[-1]: f.get("default") for f in SCHEMA[-1]["fields"]}
    return {**defaults, **{k: v for k, v in t.items() if v is not None}}


def disabled(kind: str, cfg=None) -> set[str]:
    """Names switched off in Settings for 'skills', 'plugins' or 'playbooks'."""
    from . import context
    cfg = cfg if cfg is not None else context.cfg
    try:
        return set(((cfg or {}).get(kind) or {}).get("disabled") or [])
    except Exception:
        return set()


# ── connection tests (buttons on the Settings page) ───────
def run_test(kind: str) -> dict:
    import httpx

    from .config import load_config
    cfg = load_config()
    env = read_env()
    try:
        if kind in ("ollama", "gemini", "groq", "xai"):
            import time

            from .llm import Provider, provider_config
            p = provider_config(cfg, kind)
            if not p:
                return {"ok": False, "message": f"No '{kind}' provider in config."}
            key = env.get(p.get("key_env", ""), "") if p.get("key_env") else "local"
            if not key:
                return {"ok": False, "message": f"No {p.get('key_env')} saved yet."}
            if kind == "groq" and key.startswith("xai-"):
                return {"ok": False, "message": "That's an xAI Grok key (starts with xai-). Paste it in the "
                        "'xAI Grok API key' box instead — Groq keys start with gsk_."}
            if kind == "xai" and key.startswith("gsk_"):
                return {"ok": False, "message": "That's a Groq key (starts with gsk_). Paste it in the "
                        "'Groq API key' box instead — xAI keys start with xai-."}
            prov = Provider(kind, p["base_url"], p["model"], key, 30)
            t = time.perf_counter()
            reply = prov.chat([{"role": "user", "content": "Reply with just: OK"}])
            secs = time.perf_counter() - t
            msg = f"{prov.model} answered '{reply.content[:30]}' in {secs:.1f}s."
            if prov.switched_from and prov.switched_from != "auto":
                apply({"values": {f"llm.providers.{kind}.model": prov.model}})
                msg += f" ('{prov.switched_from}' was retired by {kind}, so I switched to {prov.model} and saved it.)"
            return {"ok": True, "message": msg}
        if kind == "telegram":
            token = env.get("TELEGRAM_BOT_TOKEN", "")
            if not token:
                return {"ok": False, "message": "No bot token saved yet."}
            r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15).json()
            if not r.get("ok"):
                return {"ok": False, "message": r.get("description", "Telegram rejected the token.")}
            ids = (cfg.get("telegram") or {}).get("allowed_user_ids") or []
            sent = 0
            for uid in ids:
                s = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=15,
                               json={"chat_id": uid, "text": "✅ Nova test message — Telegram is working."}).json()
                sent += bool(s.get("ok"))
            extra = f" Test message sent to {sent} of {len(ids)} allowed users." if ids else \
                " Add your user ID (message the bot /id) to receive messages."
            return {"ok": True, "message": f"Connected to @{r['result']['username']}.{extra}"}
        if kind == "voice":
            from .speech import Speech
            sp = Speech(cfg)
            sp.el_key = env.get("ELEVENLABS_API_KEY", "")
            order = sp.engine_order()
            text = f"Hi {cfg.assistant.owner}, this is how I sound."
            threading.Thread(target=sp.speak, args=(text,), daemon=True).start()
            return {"ok": True, "message": f"Speaking with {order[0]} (backups: {', '.join(order[1:]) or 'none'})."}
        if kind == "elevenlabs":
            key = env.get("ELEVENLABS_API_KEY", "")
            if not key:
                return {"ok": False, "message": "No ElevenLabs key saved yet."}
            r = httpx.get("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": key}, timeout=15)
            if r.status_code != 200:
                return {"ok": False, "message": f"ElevenLabs said {r.status_code}: {r.text[:120]}"}
            d = r.json()
            left = d.get("character_limit", 0) - d.get("character_count", 0)
            return {"ok": True, "message": f"{d.get('tier', 'plan').title()} plan — {left:,} characters left this month."}
        if kind == "pexels":
            key = env.get("PEXELS_API_KEY", "")
            if not key:
                return {"ok": False, "message": "No Pexels key saved yet."}
            r = httpx.get("https://api.pexels.com/v1/search", params={"query": "ocean", "per_page": 1},
                          headers={"Authorization": key}, timeout=15)
            return {"ok": r.status_code == 200, "message": "Pexels key works." if r.status_code == 200
                    else f"Pexels said {r.status_code}."}
        if kind == "google":
            ok = (ROOT / "secrets" / "token.json").exists()
            return {"ok": ok, "message": "Google is connected." if ok else
                    "Not connected yet — run: run.bat --google-login"}
        return {"ok": False, "message": f"Unknown test '{kind}'."}
    except Exception as e:
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


def restart_soon(delay: float = 1.5) -> None:
    """Restart Nova. Under run.bat, exit code 42 makes it start again; otherwise re-exec ourselves."""
    import sys

    from .updater import RESTART_CODE

    def go():
        if os.environ.get("NOVA_LAUNCHER") == "run.bat":
            os._exit(RESTART_CODE)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Timer(delay, go).start()
