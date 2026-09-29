# Changelog

All notable changes. Versions are Git tags; roll back with `rollback.bat` or "Nova, roll back".

## [Unreleased]

## [1.5.0] — 2026-09-29
### Added
- **Track everything on the dashboard.** Click any topic (People, Projects, Actions…) or a header number to get a
  list of every item in it, with when it was made, when it was last used and how often Nova recalled it.
  Click an item for its full story: *where it came from* (the request that created it), a *timeline* (created,
  corrected, recalled, opened), and **Track this** — set To do / In progress / Waiting / Done, ★ pin it, add a note.
- **Actions**: every request is now recorded with each step Nova took (input, result, ✓/✕, how long), what it made
  or learned, which memories it used, and where the time went (thinking vs tools).
- **Busy with** card: what Nova is doing right now, anything waiting for your yes/no, and your in-progress items.
- Header: **Projects** (active, and how many are in progress) and **Skills** — click for every skill with a short
  description, its tools and how often you've used it.
- **xAI Grok** as a cloud model (key in Settings → API keys; starts with `xai-`).
### Fixed
- Groq test/answers failing with "model `llama-3.3-70b-versatile` does not exist": Groq retired it. Cloud models
  are now `auto` — Nova picks the best model your key can use and switches by itself when one is retired.
- Settings explains when a key is pasted in the wrong box (Groq `gsk_…` vs xAI `xai-…`).
### Faster
- The local model and embeddings are loaded at start-up and kept loaded (`llm.keep_alive`, default 24h), so there's
  no 10–30 s wait after a pause.
- Learning after each reply now uses a cloud model when you have one (instead of blocking the local model), and is
  skipped for quick requests like the time or weather.
- Every reply prints and shows its timing, e.g. `2.1s total — thinking 1.8s on ollama (2 steps)`.

## [1.4.3] — 2026-09-29
### Fixed
- Settings: if saving failed on Nova's side, the page showed nothing. Every failure is now shown ("Not saved: …"),
  and the cause is printed in Nova's window and log.
- Settings: a routine that couldn't be saved (e.g. no task filled in) disappeared from the screen. It now stays,
  with the reason shown under the routines. Times like `7:30`, `7h30` or `12:30 pm` are accepted.
- Settings: **Test** buttons no longer say "save first" — they save your changes automatically, then test.

## [1.4.2] — 2026-09-29
### Fixed
- If the voice part crashed at start-up (microphone, wake-word model…), the whole of Nova closed, taking the
  dashboard and Settings with it ("localhost refused to connect"). Voice and Telegram now run in guarded
  threads: an error is shown and retried, and the dashboard, Settings and everything else keep running.

## [1.4.1] — 2026-09-29
### Fixed
- The **Nova Brain** desktop shortcut now starts Nova if it isn't running, then opens the dashboard
  (it used to show "localhost refused to connect"). Run `setup.bat` once to update the shortcut.
- MCP servers connect in the background, so a slow first download (e.g. Windows-MCP) no longer delays start-up.
### Added
- Everything Nova prints is saved to `data\logs\nova.log` (previous run: `nova.previous.log`), including crashes.

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
