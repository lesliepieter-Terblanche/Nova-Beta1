# Changelog

All notable changes. Versions are Git tags; roll back with `rollback.bat` or "Nova, roll back".

## [Unreleased]

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
