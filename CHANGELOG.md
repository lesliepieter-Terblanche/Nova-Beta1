# Changelog

All notable changes. Versions are Git tags; roll back with `rollback.bat` or "Nova, roll back".

## [Unreleased]

## [1.4.0] — 2026-09-29
### Added
- **Weather skill:** live forecast from Open-Meteo (free, no key) for your home city or anywhere, today or any day
  this week. Nova reads it out and opens a new animated **weather page** (current conditions, 24-hour chart with rain
  chance, 7-day outlook, city search). The morning briefing uses it too.
### Fixed
- Nova no longer answers with just a link — it reads out what it found.
- Dashboard pages are no longer cached by the browser, so new features show up right after an update.

## [1.3.1] — 2026-09-29
### Fixed
- `setup.bat` is now fully resumable: it skips Ollama models that are already downloaded, retries interrupted
  downloads up to 3 times, and warns not to click inside the window (Windows pauses it).
- Setup no longer downloads Playwright's Chromium when Chrome or Edge is installed (that download often times out);
  browser control now tries Chrome, then Edge, then Chromium.
- Setup and `run.bat` could hang forever when Ollama was frozen (e.g. after an interrupted download). They now check
  Ollama over HTTP with short timeouts and restart it automatically.

## [1.3.0] — 2026-09-29
### Added
- **Settings page** (gear icon on the dashboard, or http://localhost:8765/settings):
  - API keys: add, replace, clear and test (ElevenLabs credits, Gemini, Groq, Telegram, Pexels). Keys are masked and stay in `.env`.
  - General, Voice, AI brain, Telegram (allowed IDs), Google, Files & web settings.
  - Extensions: switch built-in skills, plugins, playbooks and MCP servers on/off.
  - Routines editor with day picker.
  - Appearance: colour presets, accent/background colours, glow, stars, orbit speed, labels, with live preview.
    Applied to the 3D dashboard instantly.
  - One-click restart.
- Saves keep your `config.yaml` comments (ruamel.yaml round-trip) and keep a `config.yaml.bak`.
- Dashboard hardening: Host-header check (DNS-rebinding protection) and same-origin check on every write.
- 6 new tests (35 total).

## [1.2.0] — 2026-09-29
### Added
- **Meeting recorder:** records your mic + PC audio (Teams/Zoom/Meet), transcribes locally, writes summary,
  decisions, action items, people and a follow-up email draft to `Meetings/`, teaches memory, and adds your actions
  to Google Tasks. Also summarises existing recordings.
- **Kokoro-82M** natural local voice; configurable voice chain `elevenlabs → kokoro → piper → windows`;
  sentence-pipelined playback so local voices start speaking sooner.
- **Barge-in:** say the wake word while Nova is talking to interrupt it. **Push-to-talk hotkey** (Ctrl+Alt+Space).
- **Videos:** burned-in captions and free Pexels stock footage behind each scene (`PEXELS_API_KEY`).
- **Local vision fallback:** `vision_providers: [gemini, ollama_vision]` with Gemma 3 4B; images are downscaled first.
- **SearXNG** private search (`web.searxng_url`) with DuckDuckGo fallback.
- Morning-briefing playbook now includes the weather.
- 11 new tests (29 total).

## [1.1.0] — 2026-09-29
### Added
- **MCP client:** connect any MCP server (stdio, streamable HTTP, SSE) via `mcp_servers`; annotation-aware
  confirmations; optional import from Claude Desktop.
- **Nova as an MCP server** (`python -m nova.mcp_server`) so Claude Desktop, Claude Code and others can use Nova's memory and skills.
- **Plugins** (`plugins/*.py`, example: currency conversion) and **playbooks** (`playbooks/*.md`: morning briefing,
  meeting prep, research-to-brain, weekly review).
- **Routines:** scheduled prompts (spoken and/or sent to Telegram).
- Optional fully-local vision provider (Gemma 3 4B via Ollama).
- `config.example.yaml` template + first-run questions; personal `config.yaml` is no longer committed.
- GitHub-ready docs (install, configuration, skills, MCP, architecture, troubleshooting, roadmap), tests and CI.

### Changed
- SQLite memory now uses WAL mode (safe concurrent reads).
- MCP SDK pinned to 1.x (2.x renamed its server API).

## [1.0.0] — 2026-09-29
### Added
- Voice loop (openWakeWord, faster-whisper, ElevenLabs streaming with Piper fallback).
- Hybrid model router: Ollama (Qwen 2.5 3B) → Gemini → Groq, with vision.
- Permanent self-learning memory with versioned corrections; Markdown vault.
- 3D cinematic second-brain dashboard with live activity.
- Telegram remote control with voice notes and files.
- Skills: Google Workspace, files, web search/scrape/website builder, browser control, webcam product ads,
  narrated videos, voice-overs, transcription, reminders, PC control.
- GitHub updates/rollback, desktop shortcuts, VS Code project.
