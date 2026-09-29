# Changelog

All notable changes. Versions are Git tags; roll back with `rollback.bat` or "Nova, roll back".

## [Unreleased]

## [2.2.1] — 2026-09-29
### Fixed
- Remote access **Set up** timed out the first time: Tailscale prints an approval link and then waits for you.
  Nova now shows that link straight away (**Approve in Tailscale**), and **Check again** finishes the setup
  (dashboard + globe) once you've approved.

## [2.2.0] — 2026-09-29
### Added
- **Nova on your phone with Tailscale** — private access to the dashboard (and the God's Eye globe) from anywhere:
  - Settings → **Remote access** (Check / Set up / Turn off), the new 📱 button on the dashboard (with a QR code
    to scan), or say "set up remote access".
  - Uses Tailscale Serve: an HTTPS address like `https://your-pc.tailXXXX.ts.net` that only your own devices can
    open. Nova still only listens on the PC; nothing is public. Remote access can only be switched on/off from the PC.
  - The globe is served at `…ts.net:8443`.
- **Phone layout** for the dashboard: compact header, the Busy card and Track buttons above the ask box,
  full-width panels.

## [2.1.0] — 2026-09-29
### Added
- **Nightly dreaming** (every night at 02:30; if the PC was off it catches up once Nova has been running and quiet
  for 10 minutes):
  - merges duplicate memories into one clear memory (old versions kept as history, your tracking follows)
  - finds related ideas across memories and notes and writes down why they matter (💡 connections)
  - writes the day's journal into `brain/Journal/<date>.md` — done, decisions, open loops
  - makes an **encrypted backup** of the brain database, notes, settings and keys into `backups/` (last 7 kept,
    optionally copied to Google Drive › Nova Backups). A passphrase is created for you the first time and sent to
    your Telegram — keep it in a password manager. Restore: `python -m nova.dreaming restore <file> --apply`
  - a Dream note (`brain/Dreams/<date>.md`) and a quiet Telegram summary
  - Settings → Dreaming (Dream now / Back up now), voice: "dream now", "what did you do last night?", "back up now"
- Telegram **quiet hours** (22:00–07:00 by default): night-time messages arrive without a buzz.
### Roadmap
- All five upgrades done: gesture control, presence awareness, screen watcher, missions, nightly dreaming.

## [2.0.0] — 2026-09-29
### Added
- **Missions** — goals Nova works on by itself, once or on a schedule:
  "start a mission every Monday at 8 to research what Juniper Mist competitors did in SADC and brief me".
  - Each run: Nova plans 3–6 steps (smart model), does them with its tools, writes a report into the 2nd brain
    (`Missions/` folder, searchable) and sends you the summary on Telegram and out loud (held if you're away).
  - Recurring runs see the previous report, so they can say what changed. Schedules: once, daily, weekdays,
    specific days, every N hours. Missions that were due while Nova was off run when it starts.
  - Safe by design: missions never send, delete or run commands by themselves — they list those as "Needs your OK".
  - Dashboard: a **Missions** topic; each mission shows live progress, its plan with every step's result, past runs
    with links to the reports, and ▶ Run now / ⏸ Pause / 🗑 Delete. Running missions appear in the Busy card.
  - Voice: "list my missions", "what did the Mist mission find?", "pause mission 2".

## [1.9.0] — 2026-09-29
### Added
- **Screen watcher** — "tell me when…". Nova keeps an eye on the screen, a window, a program or Downloads and
  tells you (spoken — held if you're away — and on Telegram with a screenshot or the file):
  - text appears / disappears ("tell me when *Export complete* shows", "…when *Uploading* is gone") — local OCR
  - a window or the screen stops changing (progress finished) or changes (new message)
  - a window opens / closes, a program finishes ("tell me when ffmpeg is done")
  - a download finishes (new file in Downloads, size settled)
  - a yes/no question about the screen, answered by the vision model ("has the build failed?")
  - optional follow-up: "…then email it to Sam". Watches survive a restart and give up after 2 h by default.
  - The dashboard's Busy card lists what Nova is watching, with ✕ to stop one.

## [1.8.0] — 2026-09-29
### Added
- **Presence awareness** — the webcam notices when you sit down and when you walk away (face detection on the CPU,
  no pictures kept; typing also counts as being here).
  - Sit down: "Welcome back" plus anything Nova wanted to say while you were gone; the first time before noon,
    your morning briefing.
  - Walk away: Nova stops talking, holds spoken reminders for you (they still reach Telegram), the orb dims to
    *away*, and it can lock the PC (off by default).
  - Say "turn on presence awareness", or **Settings → Presence**. The dashboard's Busy card shows 👤 at your desk /
    🚶 away. New "lock my PC" tool (asks first).
- **Telegram starts with Nova** and now tells you: 🟢 online when run.bat starts it, 🔴 when it stops
  (Settings → Telegram). Messages you sent while Nova was off are answered when it starts; ones older than
  30 minutes get "I was offline — send it again" instead of being acted on late.

## [1.7.0] — 2026-09-29
### Added
- **Gesture control** — steer Nova with your hands through the webcam (Google MediaPipe, runs on the CPU, nothing
  leaves the PC). Hold for about half a second:
  ✋ palm = stop talking · 👍 = yes · 👎 = no (answers the question Nova is waiting on) · ✌️ = start listening ·
  ✊ = Escape · 👋 swipe = turn the 3D brain · 👉 point = move the mouse · 🤏 pinch = click, pinch-and-move = drag
  (spin the brain or the God's Eye globe). Every action can be changed in **Settings → Gestures**.
  Say "turn on gestures", or tick it in Settings. The dashboard shows a ✋ chip with the gesture it sees and a live
  **Camera** preview with your hand skeleton.
- **Roadmap on the Projects board** — `roadmap.yaml` syncs Nova's upgrade plan into Projects (To do → In progress →
  Done) on every start. Your own notes and pins are kept.
- One shared webcam for gestures, presence and photos (Windows only lets one app use a camera).

## [1.6.0] — 2026-09-29
### Added
- **God's Eye View globe** ([bilawalsidhu/gods-eye-view](https://github.com/bilawalsidhu/gods-eye-view), MIT):
  a live 3D globe with real aircraft, ships, satellites, earthquakes, weather and public cameras.
  - Nova installs, starts and stops it for you ("install God's Eye View" — needs Node.js 24).
  - "Show me Johannesburg on the globe in night vision / thermal", street → globe views, tactical HUD.
  - Answers by voice from the same free feeds, even without the app: "what planes are overhead?" (adsb.lol),
    "any earthquakes near Joburg this week?" (USGS), "where's the ISS?".
  - 🌐 button on the dashboard, and a **God's Eye View** tab in Settings (Check / Install / start with Nova).
- Settings: **Test Grok** button.

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
