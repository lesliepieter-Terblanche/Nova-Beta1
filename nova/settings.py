"""Settings: read and safely update config.yaml and .env from the dashboard.

- config.yaml is edited with ruamel.yaml in round-trip mode, so your comments and layout survive.
- .env lines are updated in place (comments kept); secrets are never sent back to the browser in full.
- The SCHEMA below drives the Settings page: add a field here and it appears in the UI.
"""
from __future__ import annotations

import json
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
    {"key": "NOVA_BACKUP_PASSPHRASE", "label": "Backup passphrase", "group": "Backups",
     "help": "Encrypts the nightly backups. Made for you if empty — keep a copy, you need it to restore"},
    {"key": "TELEGRAM_BOT_TOKEN", "label": "Telegram bot token", "group": "Remote",
     "help": "Telegram → @BotFather → /newbot", "link": "https://t.me/BotFather", "test": "telegram"},
    {"key": "GOOGLE_OAUTH_CLIENT_ID", "label": "Google Client ID", "group": "Google", "plain": True,
     "help": "Google Cloud → APIs & Services → Credentials → your Desktop app OAuth client (ends in .apps.googleusercontent.com)",
     "link": "https://console.cloud.google.com/apis/credentials"},
    {"key": "GOOGLE_OAUTH_CLIENT_SECRET", "label": "Google Client secret", "group": "Google",
     "help": "Same page — the Client secret (starts with GOCSPX-)"},
    {"key": "ESP_TOKEN", "label": "EskomSePush token", "group": "South Africa",
     "help": "Free (50 checks a day) — load-shedding schedule and warnings", "link": "https://eskomsepush.gumroad.com/l/api",
     "test": "loadshedding"},
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
        {"path": "voice.vad", "label": "How Nova hears you talking", "type": "select", "default": "silero",
         "options": ["silero", "loudness"], "help": "silero = a small AI that ignores fans, typing and the TV"},
        {"path": "voice.vad_threshold", "label": "Speech detection sensitivity", "type": "number", "min": 0.2,
         "max": 0.9, "step": 0.05, "default": 0.5, "help": "Higher = needs clearer speech (fewer false starts)"},
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
        {"path": "telegram.file_forwards", "label": "File forwarded messages, links and files in my brain", "type": "bool",
         "default": True, "help": "Forward anything to the bot (links, PDFs, photos, business cards, voice notes) and "
         "it's summarised and filed. Send a file WITH a caption to ask for something else instead"},
        {"path": "telegram.announce_online", "label": "Message me when Nova starts / stops", "type": "bool",
         "default": True, "help": "🟢 online when run.bat starts Nova, 🔴 when it stops"},
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
    {"id": "gestures", "title": "Gestures", "icon": "hand", "fields": [
        {"path": "gestures.enabled", "label": "Gesture control", "type": "bool", "default": False,
         "help": "Uses the webcam. Also: say 'turn on gestures'"},
        {"path": "gestures.style", "label": "How it works", "type": "select", "default": "hand", "options": ["hand", "finger"],
         "help": "hand = move your whole hand, close it to click · finger = older point-and-pinch"},
        {"path": "gestures.mouse", "label": "Air mouse (move the cursor, ✊ close = click, close + move = drag, ✌️ = scroll)", "type": "bool", "default": True},
        {"path": "gestures.zoom", "label": "🔍 Hand towards the camera = zoom in, pull back = zoom out", "type": "bool", "default": True},
        {"path": "gestures.hand_actions.thumbs_up", "label": "👍 Thumbs up (hold)", "type": "select", "default": "enter", "options": ["enter", "delete", "back", "forward", "open_dashboard", "stop", "yes", "no", "listen", "escape", "none", "key:alt+left", "key:alt+right", "key:space", "key:media_play_pause", "key:media_next", "key:media_previous"], "free": True},
        {"path": "gestures.hand_actions.thumbs_down", "label": "👎 Thumbs down (hold)", "type": "select", "default": "delete", "options": ["enter", "delete", "back", "forward", "open_dashboard", "stop", "yes", "no", "listen", "escape", "none", "key:alt+left", "key:alt+right", "key:space", "key:media_play_pause", "key:media_next", "key:media_previous"], "free": True},
        {"path": "gestures.hand_actions.swipe_left", "label": "👈 Swipe left", "type": "select", "default": "back", "options": ["enter", "delete", "back", "forward", "open_dashboard", "stop", "yes", "no", "listen", "escape", "none", "key:alt+left", "key:alt+right", "key:space", "key:media_play_pause", "key:media_next", "key:media_previous"], "free": True},
        {"path": "gestures.hand_actions.swipe_right", "label": "👉 Swipe right", "type": "select", "default": "open_dashboard", "options": ["enter", "delete", "back", "forward", "open_dashboard", "stop", "yes", "no", "listen", "escape", "none", "key:alt+left", "key:alt+right", "key:space", "key:media_play_pause", "key:media_next", "key:media_previous"], "free": True},
        {"path": "gestures.hand_actions.victory", "label": "✌️ Victory (hold)", "type": "select", "default": "listen", "options": ["enter", "delete", "back", "forward", "open_dashboard", "stop", "yes", "no", "listen", "escape", "none", "key:alt+left", "key:alt+right", "key:space", "key:media_play_pause", "key:media_next", "key:media_previous"], "free": True},
        {"path": "gestures.hand_actions.palm", "label": "✋ Open palm held still (1 s)", "type": "select", "default": "none", "options": ["enter", "delete", "back", "forward", "open_dashboard", "stop", "yes", "no", "listen", "escape", "none", "key:alt+left", "key:alt+right", "key:space", "key:media_play_pause", "key:media_next", "key:media_previous"], "free": True},
        {"path": "gestures.hover_labels", "label": "Label what's under the cursor (file, folder, link, button…)",
         "type": "bool", "default": True},
        {"path": "gestures.speak_labels", "label": "…and say it out loud", "type": "bool", "default": False},
        {"path": "gestures.reach", "label": "Hand area (smaller = less arm movement)", "type": "number", "min": 0.35,
         "max": 0.9, "step": 0.05, "default": 0.64},
        {"path": "gestures.scroll_speed", "label": "✌️ Scroll speed", "type": "number", "min": 1, "max": 10, "default": 2},
        {"path": "gestures.camera", "label": "Webcam number", "type": "number", "min": 0, "max": 5, "default": 0},
        {"path": "gestures.mirror", "label": "Mirror (hand right = pointer right)", "type": "bool", "default": True},
        {"path": "gestures.fps", "label": "Checks per second", "type": "number", "min": 4, "max": 30, "default": 12,
         "help": "Lower = lighter on the CPU"},
        {"path": "gestures.actions.palm", "label": "Finger mode · ✋ Open palm (hold)", "type": "select", "default": "stop", "options": ["stop", "yes", "no", "listen", "escape", "dashboard", "auto", "none", "key:alt+left", "key:alt+right", "key:space", "key:media_play_pause", "key:media_next", "key:media_previous"], "free": True},
        {"path": "gestures.actions.thumbs_up", "label": "Finger mode · 👍 Thumbs up (hold)", "type": "select", "default": "yes", "options": ["stop", "yes", "no", "listen", "escape", "dashboard", "auto", "none", "key:alt+left", "key:alt+right", "key:space", "key:media_play_pause", "key:media_next", "key:media_previous"], "free": True},
        {"path": "gestures.actions.thumbs_down", "label": "Finger mode · 👎 Thumbs down (hold)", "type": "select", "default": "no", "options": ["stop", "yes", "no", "listen", "escape", "dashboard", "auto", "none", "key:alt+left", "key:alt+right", "key:space", "key:media_play_pause", "key:media_next", "key:media_previous"], "free": True},
        {"path": "gestures.actions.victory", "label": "Finger mode · ✌️ Victory (hold)", "type": "select", "default": "listen", "options": ["stop", "yes", "no", "listen", "escape", "dashboard", "auto", "none", "key:alt+left", "key:alt+right", "key:space", "key:media_play_pause", "key:media_next", "key:media_previous"], "free": True},
        {"path": "gestures.actions.fist", "label": "Finger mode · ✊ Fist (hold)", "type": "select", "default": "escape", "options": ["stop", "yes", "no", "listen", "escape", "dashboard", "auto", "none", "key:alt+left", "key:alt+right", "key:space", "key:media_play_pause", "key:media_next", "key:media_previous"], "free": True},
        {"path": "gestures.actions.swipe_left", "label": "Finger mode · 👈 Swipe left (auto = back in a browser / Explorer)", "type": "select", "default": "auto", "options": ["stop", "yes", "no", "listen", "escape", "dashboard", "auto", "none", "key:alt+left", "key:alt+right", "key:space", "key:media_play_pause", "key:media_next", "key:media_previous"], "free": True},
        {"path": "gestures.actions.swipe_right", "label": "Finger mode · 👉 Swipe right (auto = forward)", "type": "select", "default": "auto", "options": ["stop", "yes", "no", "listen", "escape", "dashboard", "auto", "none", "key:alt+left", "key:alt+right", "key:space", "key:media_play_pause", "key:media_next", "key:media_previous"], "free": True},
    ]},
    {"id": "remote", "title": "Remote access", "icon": "phone", "fields": [
        {"path": "remote.enabled", "label": "Start phone access with Nova", "type": "bool", "default": False,
         "help": "Switched on automatically after Set up works. Reconnects Tailscale and re-shares Nova on every start"},
        {"path": "dashboard.allowed_hosts", "label": "Extra allowed addresses", "type": "list",
         "help": "Not needed for Tailscale — your PC's Tailscale name is allowed automatically"},
    ]},
    {"id": "wellbeing", "title": "Focus & wellbeing", "icon": "heart", "fields": [
        {"path": "wellbeing.enabled", "label": "Focus & wellbeing features", "type": "bool", "default": True},
        {"path": "wellbeing.focus_default", "label": "Open the dashboard in Focus mode", "type": "bool", "default": False,
         "help": "One thing now, two next, nothing else — press F or the ◎ button to switch"},
        {"path": "dashboard.theme.calm", "label": "Calm visuals", "type": "bool", "default": False,
         "help": "No spinning camera, softer glow, no moving particles, muted colours"},
        {"path": "wellbeing.nudge_minutes", "label": "Gentle 'time check' after (minutes on one thing)", "type": "number",
         "min": 15, "max": 180, "default": 50},
        {"path": "wellbeing.anchors", "label": "Routine anchors", "type": "list",
         "default": ["07:30 Morning routine | Glass of water; Medication (if any); Pick today's one thing",
                     "12:30 Lunch — step away from the screen",
                     "22:30 Wind down | Dim the screens; Write tomorrow's one thing; Bed by 23:15"],
         "help": "One per line: time, name, then optional steps after | separated by ;"},
        {"path": "wellbeing.night.enabled", "label": "Night guardrails", "type": "bool", "default": True},
        {"path": "wellbeing.night.from", "label": "Night starts", "type": "text", "default": "23:00"},
        {"path": "wellbeing.night.to", "label": "Night ends", "type": "text", "default": "06:30"},
        {"path": "wellbeing.night.hold_sends", "label": "Hold emails, invites and submissions until morning",
         "type": "bool", "default": True},
        {"path": "wellbeing.night.release_at", "label": "…and show them to me again at", "type": "text", "default": "08:00"},
        {"path": "wellbeing.buy_pause", "label": "24-hour pause before anything that buys", "type": "bool", "default": True},
        {"path": "wellbeing.low_energy_auto", "label": "Low-energy mode when my check-in energy is 1–2", "type": "bool",
         "default": True},
        {"path": "wellbeing.signals.short_sleep", "label": "Heads-up: short sleep (2 of 3 nights)", "type": "bool", "default": True},
        {"path": "wellbeing.signals.short_sleep_hours", "label": "…short means under (hours)", "type": "number",
         "min": 3, "max": 9, "step": 0.5, "default": 6},
        {"path": "wellbeing.signals.late_nights", "label": "Heads-up: busy after midnight (2 of 3 nights)", "type": "bool", "default": True},
        {"path": "wellbeing.signals.project_burst", "label": "Heads-up: a burst of new projects", "type": "bool", "default": True},
        {"path": "wellbeing.signals.burst_count", "label": "…this many in 3 days", "type": "number", "min": 2, "max": 15, "default": 4},
        {"path": "wellbeing.signals.energy_up", "label": "Heads-up: high energy on little sleep", "type": "bool", "default": True},
        {"path": "wellbeing.signals.low_run", "label": "Heads-up: 3+ heavy days in a row", "type": "bool", "default": True},
        {"path": "wellbeing.signals_telegram", "label": "Also send heads-ups to Telegram", "type": "bool", "default": False},
        {"path": "wellbeing.plan", "label": "My plan when a heads-up shows", "type": "text", "default": "",
         "help": "Best agreed with your doctor, e.g. 'Sleep first, no new projects this week, text Dr M.'"},
    ]},
    {"id": "documents", "title": "Documents", "icon": "doc", "fields": [
        {"path": "documents.docling", "label": "Use docling for PDFs (better tables & scans)", "type": "select",
         "default": "auto", "options": ["auto", "off"], "help": "auto = use it once installed (button below)"},
    ]},
    {"id": "images", "title": "Images (ComfyUI)", "icon": "image", "fields": [
        {"path": "media.image_provider", "label": "Make images with", "type": "select", "default": "auto",
         "options": ["auto", "comfyui", "local"], "help": "auto = ComfyUI when it's running"},
        {"path": "media.comfyui.url", "label": "ComfyUI address", "type": "text", "default": "http://127.0.0.1:8188"},
        {"path": "media.comfyui.checkpoint", "label": "Model (part of the name)", "type": "text", "default": "",
         "help": "Blank = the first model in ComfyUI/models/checkpoints"},
        {"path": "media.comfyui.start_command", "label": "Start ComfyUI with (optional)", "type": "text", "default": "",
         "help": r"e.g. C:\ComfyUI_windows_portable\run_nvidia_gpu.bat — Nova starts it when needed"},
        {"path": "media.comfyui.workflow", "label": "Your own workflow (optional)", "type": "text", "default": "",
         "help": "A ComfyUI 'Export (API)' .json with {{prompt}} {{negative}} {{seed}} {{width}} {{height}}"},
    ]},
    {"id": "web_agent", "title": "Web agent", "icon": "cursor", "fields": [
        {"path": "web_agent.provider", "label": "AI that drives the browser", "type": "select", "default": "auto",
         "options": ["auto", "gemini", "groq", "xai", "ollama"], "help": "auto = Gemini, then Groq, then Grok"},
        {"path": "web_agent.model", "label": "Model (optional)", "type": "text", "default": "",
         "help": "Blank = the provider's default (Gemini 2.5 Flash)"},
        {"path": "web_agent.max_steps", "label": "Most steps per task", "type": "number", "min": 5, "max": 100,
         "default": 30},
        {"path": "web_agent.headless", "label": "Work invisibly (no browser window)", "type": "bool", "default": False},
        {"path": "web_agent.chrome_path", "label": "Browser to use (optional)", "type": "text", "default": "",
         "help": "Blank = Chrome, or Edge if there's no Chrome"},
    ]},
    {"id": "activitywatch", "title": "Screen time", "icon": "clock", "fields": [
        {"path": "activitywatch.enabled", "label": "Use ActivityWatch screen time", "type": "bool", "default": True,
         "help": "Free app from activitywatch.net — data stays on this PC"},
        {"path": "activitywatch.url", "label": "ActivityWatch address", "type": "text", "default": "http://localhost:5600"},
        {"path": "activitywatch.nudges", "label": "Gentle nudge when I drift during work hours", "type": "bool",
         "default": True, "help": "Only when a Focus task is waiting, never at night"},
        {"path": "activitywatch.nudge_after_minutes", "label": "…after this many minutes of drift", "type": "number",
         "min": 5, "max": 90, "default": 15},
        {"path": "activitywatch.nudge_every_minutes", "label": "At most one nudge every (minutes)", "type": "number",
         "min": 15, "max": 240, "default": 45},
        {"path": "activitywatch.work_start", "label": "Work day starts", "type": "text", "default": "08:00"},
        {"path": "activitywatch.work_end", "label": "Work day ends", "type": "text", "default": "17:00"},
        {"path": "activitywatch.distractions", "label": "What counts as drift", "type": "list",
         "default": ["youtube", "facebook", "instagram", "tiktok", "netflix", "reddit", "twitter", "x.com", "showmax"],
         "help": "One per line — matched against app names, window titles and sites"},
    ]},
    {"id": "loadshedding", "title": "Load-shedding", "icon": "bolt", "fields": [
        {"path": "loadshedding.area_id", "label": "Your EskomSePush area id", "type": "text", "default": "",
         "help": "Say 'find my load-shedding area Roodepoort' and Nova fills this in"},
        {"path": "loadshedding.warn", "label": "Warn me before the power goes off", "type": "bool", "default": True},
        {"path": "loadshedding.warn_minutes", "label": "…this many minutes before", "type": "number", "min": 5,
         "max": 120, "default": 30},
    ]},
    {"id": "news", "title": "News", "icon": "news", "fields": [
        {"path": "news.vendors", "label": "Companies to follow", "type": "list",
         "default": ["Juniper Networks", "Avaya", "Nokia", "SonarSource", "Westcon-Comstor"],
         "help": "One per line — used for 'vendor news' and the morning briefing"},
    ]},
    {"id": "dreaming", "title": "Dreaming", "icon": "moon", "fields": [
        {"path": "dreaming.enabled", "label": "Dream every night", "type": "bool", "default": True},
        {"path": "dreaming.at", "label": "At", "type": "text", "default": "02:30",
         "help": "If the PC is off then, Nova catches up ~10 minutes after it next starts"},
        {"path": "dreaming.consolidate", "label": "Merge duplicate memories", "type": "bool", "default": True},
        {"path": "dreaming.connect", "label": "Find connections between ideas", "type": "bool", "default": True},
        {"path": "dreaming.journal", "label": "Write the day's journal", "type": "bool", "default": True},
        {"path": "dreaming.people", "label": "Update people cards", "type": "bool", "default": True},
        {"path": "dreaming.weekly_digest", "label": "Sunday: send 'what's new in your brain'", "type": "bool", "default": True},
        {"path": "dreaming.backup", "label": "Encrypted backup", "type": "bool", "default": True},
        {"path": "dreaming.backup_keep", "label": "Backups to keep", "type": "number", "min": 1, "max": 60, "default": 7},
        {"path": "dreaming.drive_backup", "label": "Also copy backups to Google Drive", "type": "bool", "default": False},
    ]},
    {"id": "presence", "title": "Presence", "icon": "eye", "fields": [
        {"path": "presence.enabled", "label": "Presence awareness", "type": "bool", "default": False,
         "help": "Uses the webcam. Also: say 'turn on presence awareness'"},
        {"path": "presence.away_seconds", "label": "Away after (seconds without you)", "type": "number", "min": 15,
         "max": 900, "default": 60},
        {"path": "presence.greet", "label": "Welcome me back", "type": "bool", "default": True},
        {"path": "presence.greet_after_seconds", "label": "…after being away at least (s)", "type": "number", "min": 0,
         "max": 7200, "default": 300},
        {"path": "presence.morning_briefing", "label": "Morning briefing when I first sit down", "type": "bool",
         "default": True},
        {"path": "presence.briefing_prompt", "label": "Briefing request", "type": "text",
         "default": "I just sat down at my desk. Give me my short morning briefing: today's calendar, the weather and anything urgent."},
        {"path": "presence.lock_pc", "label": "Lock the PC when I walk away", "type": "bool", "default": False},
        {"path": "presence.lock_after_seconds", "label": "Lock after (seconds away)", "type": "number", "min": 30,
         "max": 3600, "default": 120},
    ]},
    {"id": "globe", "title": "God's Eye View", "icon": "globe", "fields": [
        {"path": "globe.auto_start", "label": "Start the globe with Nova", "type": "bool", "default": False,
         "help": "Otherwise it starts the first time you ask for it"},
        {"path": "globe.port", "label": "Port", "type": "number", "min": 1024, "max": 65535, "default": 4173},
        {"path": "globe.dir", "label": "Install folder", "type": "text", "default": "tools/gods-eye-view"},
    ]},
    {"id": "appearance", "title": "Appearance", "icon": "palette", "fields": [
        {"path": "dashboard.theme.accent", "label": "Accent colour", "type": "color", "default": "#8b7bff"},
        {"path": "dashboard.theme.accent2", "label": "Second accent", "type": "color", "default": "#4cc9f0"},
        {"path": "dashboard.theme.background", "label": "Space background", "type": "color", "default": "#03040a"},
        {"path": "dashboard.theme.bloom", "label": "Glow intensity", "type": "number", "min": 0, "max": 2.5, "step": 0.05, "default": 0.85},
        {"path": "dashboard.theme.stars", "label": "Star density", "type": "number", "min": 0, "max": 12000, "step": 500, "default": 4500},
        {"path": "dashboard.theme.orbit_speed", "label": "Cinematic orbit speed", "type": "number", "min": 0, "max": 5, "step": 0.1, "default": 1.0},
        {"path": "dashboard.theme.tags", "label": "Show name tags on the brain", "type": "bool", "default": False,
         "help": "Off = a clean sphere; colours are explained in the dashboard's colour key"},
        {"path": "dashboard.theme.layout", "label": "Brain layout", "type": "select", "default": "projects",
         "options": ["projects", "categories", "web"],
         "help": "projects = everything clustered around the project it belongs to; categories = tidy sectors; web = free-flowing"},
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
# One-click MCP servers (Settings → Extensions → Add). All free; they run on this PC through uv (uvx).
MCP_CATALOG = [
    {"name": "canva", "title": "Canva", "desc": "Design posters, social posts, presentations, thumbnails and flyers "
     "in your Canva account; find, edit, resize and export designs. Signs in once in your browser (needs Node.js)",
     "spec": {"command": "npx", "args": ["-y", "mcp-remote@latest", "https://mcp.canva.com/mcp"], "timeout": 300,
              "confirm": "auto", "keywords": ["canva", "design", "poster", "flyer", "social post", "instagram post",
                                              "thumbnail", "banner", "brochure", "presentation design", "brand kit",
                                              "infographic", "invitation", "resize design", "logo"]}},
    {"name": "windows", "title": "Windows control", "desc": "Open apps, click, type, read windows — full PC control",
     "spec": {"command": "uvx", "args": ["windows-mcp"], "confirm": "auto",
              "keywords": ["window", "app", "desktop", "click on", "type into", "outlook app", "settings app"]}},
    {"name": "excel", "title": "Excel", "desc": "Read and write .xlsx workbooks in Documents: sheets, formulas, charts",
     "spec": {"command": "uvx", "args": ["excel-mcp-server", "stdio"], "env": {"EXCEL_FILES_PATH": "${USERPROFILE}/Documents"},
              "confirm": "auto", "keywords": ["excel", "xlsx", "spreadsheet", "workbook", "pivot"]}},
    {"name": "elevenlabs", "title": "ElevenLabs studio", "desc": "Voice design, sound effects, voice cloning, "
     "transcripts (uses your ElevenLabs key and credits)",
     "spec": {"command": "uvx", "args": ["elevenlabs-mcp"], "env": {"ELEVENLABS_API_KEY": "${ELEVENLABS_API_KEY}",
                                                                     "ELEVENLABS_MCP_BASE_PATH": "{workspace}/audio"},
              "confirm": "auto", "keywords": ["sound effect", "voice design", "clone voice", "elevenlabs", "design a voice"]}},
    {"name": "youtube", "title": "YouTube transcripts", "desc": "Read any YouTube video's transcript — "
     "'summarise this video'", "spec": {"command": "uvx", "args": ["mcp-youtube-transcript"], "confirm": "never",
                                  "keywords": ["youtube", "video transcript", "summarise this video", "summarize this video"]}},
]


def mcp_catalog_spec(name: str) -> dict | None:
    item = next((c for c in MCP_CATALOG if c["name"] == name), None)
    if not item:
        return None
    ws = str((ROOT / "workspace").resolve()).replace("\\", "/")
    spec = json.loads(json.dumps(item["spec"]).replace("{workspace}", ws))
    return {"enabled": True, **spec}


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
        "mcp_catalog": [{k: c[k] for k in ("name", "title", "desc")} | {"added": get_path(doc, f"mcp_servers.{c['name']}") is not None}
                        for c in MCP_CATALOG],
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
            for name in ext.get("mcp_add") or []:
                spec = mcp_catalog_spec(str(name))
                if not spec:
                    errors[f"mcp_add.{name}"] = "unknown server"
                    continue
                if get_path(doc, f"mcp_servers.{name}") is None:
                    set_path(doc, f"mcp_servers.{name}", spec)
                else:
                    set_path(doc, f"mcp_servers.{name}.enabled", True)
                saved.append(f"mcp_servers.{name}")
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
        if kind in ("remote", "remote_on", "remote_off"):
            from . import remote
            if kind == "remote_on":
                r = remote.enable()
                return {"ok": r["ok"], "message": r["message"]}
            if kind == "remote_off":
                return {"ok": True, "message": remote.disable()["message"]}
            st = remote.status(fresh=True)
            if st["url"]:
                return {"ok": True, "message": f"On — open {st['url']} on your phone (Tailscale on)."
                        + (f" Globe: {st['globe_url']}" if st["globe_url"] else "")}
            return {"ok": st["running"], "message": st["message"] or "Tailscale is connected but not serving Nova yet."}
        if kind in ("loadshedding", "news"):
            import importlib.util
            import sys
            os.environ.update({k: v for k, v in env.items() if k == "ESP_TOKEN" and v})
            mod = sys.modules.get(f"nova_plugins.{kind}")          # the copy Nova already loaded
            if mod is None:
                spec = importlib.util.spec_from_file_location(f"nova_plugins.{kind}", ROOT / "plugins" / f"{kind}.py")
                mod = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = mod
                spec.loader.exec_module(mod)
            from . import context as _ctx
            _ctx.cfg = _ctx.cfg or cfg
            out = mod.loadshedding_status() if kind == "loadshedding" else mod.news_briefing(max_items=3)
            return {"ok": not out.startswith("ERROR"), "message": out.removeprefix("ERROR: ")}
        if kind in ("docling_install", "web_agent_install"):
            from . import extras
            name = "docling" if kind == "docling_install" else "browser_use"
            st = extras.status(name)
            if st["installed"]:
                return {"ok": True, "message": f"{st['title']} is installed ✓"}
            if st["state"] == "installing":
                return {"ok": True, "message": " · ".join(st["log"][-2:]) or "Installing…"}
            return {"ok": True, "message": extras.install(name, extras.announce_done) + " Click again to see progress."}
        if kind == "web_agent_login":
            from . import context as _ctx
            from . import web_agent
            _ctx.cfg = _ctx.cfg or cfg
            return {"ok": True, "message": web_agent.open_for_login("https://accounts.google.com")}
        if kind == "comfyui":
            from . import comfy
            from . import context as _ctx
            _ctx.cfg = _ctx.cfg or cfg
            if not comfy.running():
                return {"ok": False, "message": f"ComfyUI isn't running at {comfy.base()}. Start ComfyUI first "
                        "(or fill in 'Start ComfyUI with')."}
            names = comfy.checkpoints()
            if not names:
                return {"ok": False, "message": "ComfyUI is running but has no models — add one to "
                        "ComfyUI/models/checkpoints (SD 1.5 or SDXL-Turbo for a 4 GB card)."}
            return {"ok": True, "message": f"ComfyUI is running with {len(names)} model(s); I'll use "
                    f"{comfy.pick_checkpoint()}. Say 'make an image of …' to try it."}
        if kind == "activitywatch":
            from . import activity
            from . import context as _ctx
            _ctx.cfg = _ctx.cfg or cfg
            if not activity.running():
                return {"ok": False, "message": f"ActivityWatch isn't running at {activity.base()}. Install it from "
                        "activitywatch.net and start it (it sits in the system tray)."}
            return {"ok": True, "message": activity.as_text(activity.summary("today")).replace("\n", " · ")}
        if kind in ("dream", "backup"):
            import threading as _t

            from .dreaming import backup, dreamer
            if kind == "backup":
                info = backup()
                extra = " A backup passphrase was created and saved in .env — the Telegram bot sends it to you." \
                    if info.get("new_passphrase") else ""
                return {"ok": True, "message": f"Backed up {info['files']} files ({info['size_mb']} MB) to "
                        f"{info['path']}.{extra}" + (f" Drive: {info.get('drive') or info.get('drive_error')}"
                                                   if info.get("drive") or info.get("drive_error") else "")}
            if dreamer().running:
                return {"ok": True, "message": f"Already dreaming ({dreamer().step})."}
            _t.Thread(target=dreamer().dream, daemon=True).start()
            return {"ok": True, "message": "Dreaming now — the report arrives on Telegram and in brain/Dreams."}
        if kind in ("presence", "presence_off"):
            from .presence import presence
            p = presence()
            if kind == "presence_off":
                return {"ok": True, "message": p.stop()}
            msg = p.start() if not p.enabled else "Presence awareness is on."
            if not p.enabled:
                return {"ok": False, "message": msg}
            seen = "I can see you" if p.faces else "I can't see a face yet — sit in front of the camera"
            return {"ok": True, "message": f"{msg} {seen}."}
        if kind in ("gestures", "gestures_off"):
            from .gestures import engine
            e = engine()
            if kind == "gestures_off":
                return {"ok": True, "message": e.stop()}
            msg = e.start() if not e.enabled else "Gesture control is on."
            s = e.status()
            if not s["enabled"]:
                return {"ok": False, "message": msg}
            return {"ok": True, "message": f"{msg} Watch the preview on the dashboard (click the ✋ chip)."}
        if kind == "globe":
            from .skills import globe
            st = globe.status()
            if not st["node_ok"]:
                return {"ok": False, "message": st["node"]}
            if st["installing"]:
                return {"ok": True, "message": "Installing now — see data/logs/globe.log."}
            if not st["installed"]:
                return {"ok": False, "message": f"Node {st['node']} is fine, but God's Eye View isn't installed yet — "
                        "click Install, or say 'install God's Eye View'."}
            return {"ok": True, "message": f"Installed (Node {st['node']}), "
                    + (f"running at {st['url']}." if st["running"] else "not running — it starts when you ask for it.")}
        if kind == "globe_install":
            from .skills import globe
            return {"ok": True, "message": globe.install()}
        if kind == "google":
            from .skills import google_ws
            if not (ROOT / "secrets" / "token.json").exists():
                return {"ok": False, "message": "Not connected yet — click Connect Google."}
            try:
                google_ws.login(interactive=False)
                google_ws._services.clear()
                me = google_ws.svc("gmail", "v1").users().getProfile(userId="me").execute().get("emailAddress", "")
                return {"ok": True, "message": f"Connected as {me} — Nova stays signed in (no need to log in again)."}
            except Exception as e:
                return {"ok": False, "message": str(e)}
        if kind == "google_connect":
            import threading as _t

            from .skills import google_ws
            if not google_ws.find_client_file(ROOT / "secrets" / "credentials.json"):
                return {"ok": False, "message": "Nova can't find your Google client file. Download it from Google "
                        "Cloud (Credentials → your Desktop app OAuth client → Download JSON) into Downloads, then "
                        "click Connect Google again — Nova finds it by itself."}
            _t.Thread(target=google_ws.login, kwargs={"interactive": True}, daemon=True).start()
            return {"ok": True, "message": "A Google sign-in page is opening in your browser on this PC — pick your "
                    "account and allow everything once. Then click Check."}
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
