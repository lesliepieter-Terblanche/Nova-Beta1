# Changelog

All notable changes. Versions are Git tags; roll back with `rollback.bat` or "Nova, roll back".

## [Unreleased]

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
