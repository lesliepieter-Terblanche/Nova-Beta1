# Changelog

All notable changes. Versions are Git tags; roll back with `rollback.bat` or "Nova, roll back".

## [Unreleased]

## [2.14.1] — 2026-10-01
### Faster voice replies
- **Phone / Tailscale:** the written answer now appears the moment it's ready, and the voice is made while the phone
  fetches it — on Android/Chrome it's **streamed from ElevenLabs as she speaks** (starts after the first words, not
  after the whole answer is made and converted). iPhones get one small MP3, made once.
- **ElevenLabs keeps one connection open** instead of a new secure handshake to Europe/US for every reply
  (≈0.3-0.8 s saved from South Africa).
- **The voice is warmed up at start-up** (Kokoro/Piper loaded, ElevenLabs connection opened), so the first reply of
  the day isn't the slow one.

## [2.14.0] — 2026-10-01
### Added — 🪟 Answers on screen
- Ask for the **weather, PC stats, screen time, the news, load-shedding** or anything you want to *see*, and Nova
  shows it in a window in the middle of the dashboard (on the PC and on your phone) — the 3D brain hides until you
  close it (✕, Esc, or click outside).
- Each request has its own layout: **PC stats** as live circle gauges (CPU, memory, disk, GPU, video memory, battery —
  updating every 2 s, pink when something is maxed out) · **weather** as the full weather page · **screen time** as a
  3D donut with apps · **news** as a headline wall · **load-shedding** as a big stage badge with a 48-hour outage
  timeline · anything else as tidy facts, headings and bullets (new *show on screen* tool).
- The window uses your selected theme colours, has a see-through frame and a 3D feel: it swings in, tilts towards
  your mouse or finger, and its cards float above it.
- When no dashboard is open, the weather still opens the weather page like before.

## [2.13.1] — 2026-09-30
### Fixed — Tailscale: "Blocked request. This host … is not allowed"
- Nova now reads exactly where your Tailscale address forwards to. If it points at the God's Eye View globe instead
  of Nova (that's what showed Vite's "Blocked request" page), Settings → Remote access says so and **Set up** — or the
  next start — puts it back on Nova.
- The globe accepts any Tailscale address (`.ts.net`), even when it starts before Tailscale is connected.

## [2.13.0] — 2026-09-30
### Added — 🎤 Voice on your phone (Tailscale)
- **🎤 button on the dashboard's Ask bar**: tap, talk, pause — Nova hears you (transcribed on the PC), answers **out
  loud on the phone** in her own voice, then listens a few seconds for a follow-up, like "Hey Nova" at the PC. Tap 🎤
  to stop; "thanks / bye" ends it. Works over the https:// Tailscale address (phones only allow the mic on https).
- **Typed questions are spoken back on the device you're using** when you're away from the PC (🔊 on by default
  there; 🔇 to mute). At the PC, 🔊 still speaks through the PC's speakers.
### Changed — ✅ fewer "shall I go ahead?" questions
- New **Settings → General → Ask me before…** (default *irreversible*): commands, moving files, tidying folders,
  installs, updates and restarts **just run**. Nova still asks before deleting, sending or replying to email, calendar
  invites, shutting down, submitting web forms and rolling back. *all* = the old behaviour, *never* = no questions.
  Disk-wiping commands (format, diskpart, Remove-Item -Recurse, rd /s…) always ask.
### Fixed
- **Labels left behind on the 3D brain**: every time the brain refreshed (a new memory, a chat from the phone) the old
  labels stayed frozen on screen while the sphere turned. They are now cleaned up.

## [2.12.0] — 2026-09-30
### Added — seven open-source add-ons
- **📄 markitdown** (Microsoft): Nova now reads Word, **PowerPoint**, Excel (.xlsx/.xls), PDF, **Outlook .msg**, EPUB,
  HTML, CSV and ZIP as clean Markdown — for "read this", Telegram forwards into the brain and answers. Tables stay tables.
  New tools: *document to Markdown*.
- **📑 docling** (IBM, optional): much better PDFs — real tables, columns, scanned pages. New: *extract tables* →
  "put the tables from this price list into Excel" (numbers stay numbers). Install from Settings → Documents; it lives
  in its own Python so nothing else is disturbed.
- **🎙 Silero VAD**: Nova now decides when you start and stop talking with a small speech-detecting AI (2 MB, CPU)
  instead of loudness — fans, typing and the TV no longer start a recording. Settings → Voice.
- **🧭 ActivityWatch screen time**: "where did my day go?", "how long was I on YouTube?", a *Where today went* card on
  the Focus screen, and a kind nudge when you drift during work hours while a Focus task waits (never at night,
  at most once every 45 min). Settings → Screen time.
- **🎨 ComfyUI images**: *generate image* now uses ComfyUI on your own graphics card (free) — square, landscape,
  vertical, 4:5 and banner, 1-4 variations, sensible settings per model (SD 1.5, SDXL, Turbo) or your own exported
  workflow. Nova can start ComfyUI itself. Settings → Images.
- **✂️ moviepy video tools**: *video to shorts* (AI picks the best moments from what's said → vertical 9:16 with bold
  word-by-word captions), *add captions*, *make vertical* (blurred background or crop), *join videos* (cross-fades).
- **🌐 browser-use web agent**: hand over whole website tasks — portals, forms, deal registrations, invoices, comparing
  prices — in its own Chrome window, in the background, reporting back by voice/Telegram. Stops right before paying,
  buying, sending, submitting, booking or deleting unless you allowed it. Log in once to the sites it should use.
  Install from Settings → Web agent (its own Python, so its pinned packages never clash with Nova's).

## [2.11.1] — 2026-09-30
### Fixed — 📷 Camera preview on the dashboard
- The **Camera** button on the ✋ chip no longer shows an empty box: if there's no picture it says why
  (starting up, webcam busy in another app, Windows camera privacy, black picture / privacy cover, camera off).
- The webcam is opened more reliably: tries DirectShow, then Media Foundation, waits for the first real picture,
  falls back to another webcam on the PC if the chosen one doesn't work, and re-opens after a USB hiccup.
- Changing **Webcam number** in Settings now takes effect the next time gestures start.

## [2.11.0] — 2026-09-30
### Changed — 🖐 Whole-hand control (new default "hand" mode)
- **The cursor follows your whole hand** — move it left, right, up or down (no more pointing with one finger).
  It tracks the middle of your palm, so it stays put when you close your hand.
- **✊ Close your hand = click** on the link, file or folder under the cursor (close twice quickly = open).
  Keep it closed and move = drag; open your hand to let go.
- **👈 Swipe left = back** (browser, File Explorer…) · **👉 swipe right = the Nova dashboard** (brings it to the front,
  or opens it). The cursor jumps back to where it was before the swipe.
- **🔍 Push your hand towards the camera = zoom in, pull it back = zoom out** (Ctrl + wheel: browsers, Office,
  photos, Explorer, the dashboard). Holding still at the new distance doesn't keep zooming.
- **👍 hold = Enter · 👎 hold = Delete** — and when Nova is waiting for an answer they still mean yes / no.
- ✌️ held = listen, ✌️ moving up/down = scroll. Hover labels unchanged.
- Settings → Gestures: **How it works** (hand / finger), a zoom switch and hand-mode actions. The old point-and-pinch
  scheme stays available as `style: finger`.

## [2.10.0] — 2026-09-30
### Added — ✋ Air mouse (gesture upgrade)
- **Smooth, precise cursor** that follows your index finger (One Euro filtering: steady when you hold still, quick when
  you move), across all monitors, and it **no longer jumps when you pinch**. "Hand area" setting = how far you need
  to move your arm.
- **Hover labels:** rest the cursor on something and a small label appears next to it — **📁 Folder · Q3 Deals
  (12 items)**, **📄 PDF document · Price list.pdf · 2.4 MB · 12 Sep**, **🔗 Link · Juniper Mist**, **🔘 Button · Save** —
  with the gestures you can use on it. Uses Windows' own accessibility info; the label is click-through. Optional:
  Nova says it out loud. The dashboard's ✋ chip shows it too.
- **Gestures act on what you point at:** 🤏 pinch = click · 🤏🤏 two quick pinches = double-click (open) ·
  🤏 pinch and hold = right-click menu · 🤏 pinch and move = drag · ✌️ two fingers up/down = scroll ·
  👍 on a file, folder or link = open it (when Nova isn't waiting for a yes) · 👋 swipe = back/forward in a browser
  or File Explorer.

## [2.9.0] — 2026-09-30
### Added — 🖐 PC hands
- **Nova uses your PC like you do:** opens any app (also by its Start-menu name, e.g. "open Avaya Workplace"), your
  **webcam** (Camera app), folders (Downloads, Documents, Desktop…), and manages windows — bring to front, minimise,
  maximise, snap left/right, close.
- **Clicks and types in any Windows app** by the names of its buttons and fields (Windows UI Automation — reliable,
  no pixel guessing), presses shortcuts, scrolls, and clicks things it can *see* when they have no name.
- **"Do it for me":** "open Excel, open Q3 deals and sort by Value", "in Outlook start a mail to Sam with the price
  list attached" — Nova works step by step and shows each step in the dashboard's Busy card.
- **Safety:** it stops before anything that sends, pays, buys, deletes, submits or publishes and asks you
  ("go ahead and send"). Press **Esc**, or throw the mouse into the top-left corner, to take over instantly.
  Night guardrails still hold emails until morning.
- Needs `pip install -r requirements.txt` (adds pywinauto, Windows only).

## [2.8.3] — 2026-09-30
### Added
- **Settings → Google → Google sign-in details:** paste your **Client ID** and **Client secret** (or upload the JSON
  file from Google Cloud) and click **Connect Google** — no files to rename or copy, no command line. Kept in `.env`.

## [2.8.2] — 2026-09-30
### Fixed
- Google sign-in: Nova now finds your Google client file by itself — even when Windows hid the extension
  (`credentials.json.json`) or it's still in Downloads as `client_secret_….json` — and copies it into place.
  It also tells you if you downloaded a "Web application" client instead of a "Desktop app" one.
  `run.bat --google-login` shows a clear message instead of crashing.

## [2.8.1] — 2026-09-30
### Changed
- The main categories (Goals, Decisions, People, Projects…) have a small permanent label on the brain again; single
  items stay tag-free.
- Hovering is much easier: every shape has a generous invisible hover area, so pointing near a star shows what it is —
  its type, its name, and the project it belongs to (categories show how many items they hold).

## [2.8.0] — 2026-09-30
### Added
- **🎨 Canva (official connector)** — first in Settings → Extensions → *Add an MCP server*. Nova can create and edit
  posters, social posts, presentations, thumbnails and flyers in your Canva account, find, resize and export designs.
  The first start opens a Canva sign-in in your browser; after that it stays signed in.
- **Google Slides:** "Make a 6-slide Avaya QBR deck for Axiz" → a Google Slides presentation **plus a PowerPoint
  (.pptx) copy** on your PC (sent with the reply on Telegram).
- **Ready-to-use Google Sheets:** coloured bold header, frozen top row, filters, sized columns and rand formatting.
### Changed
- **Google stays signed in:** Settings → Google → **Connect Google** (no more command line). Nova asks Google for a
  long-lived refresh token, so you sign in once. If Google ever signs Nova out, the message now says exactly why and
  how to fix it (the 7-day "Testing mode" limit → *Publish app* once). Your Google password is never stored.
- Slow-to-start MCP servers (like Canva's first sign-in) no longer hold up the others.

## [2.7.0] — 2026-09-30
### Added — Focus & wellbeing (made for ADHD and bipolar II)
- **◎ Focus mode** (button top right, or press **F**): one screen with the time, your next meeting countdown, the **one
  thing Now** and the two Next. ✓ Done · 🪜 *I'm stuck* (a two-minute first step, then small steps) · *Not now*
  (parked in Later, no guilt) · ⏱ 25/50-minute timer with a soft chime. **Brain dump** box parks thoughts instantly.
  Wins today (tasks done, anchors ticked, check-in) — no streaks that can break, nothing ever turns red.
- **Gentle time checks:** after 50 minutes on one thing (Settings), "water, stretch — is this still the one thing?"
- **Calm visuals** (Settings → Focus & wellbeing): no spinning camera, softer glow, no particles, muted colours.
  Focus mode, low-energy days and night time are always calm; at night the brain dims and warms.
- **🌙 Low-energy mode:** on by itself when your check-in energy is 1–2 (or tap it): only the one thing on screen, and
  Nova keeps replies extra short and gentle.
- **🌿 Private check-in:** energy, mood (1–5), hours slept and a note — in Focus or `/checkin 3 4 7 note` on Telegram.
  Encrypted on your PC with your backup passphrase; never sent to any AI model, never in the 2nd brain or answers.
- **Heads-ups you choose:** short sleep, busy after midnight, a burst of new projects, high energy on little sleep,
  several heavy days — a gentle note with *your own plan* (agreed with your doctor), never a diagnosis.
- **Night guardrails:** after 23:00 (your choice) emails, invites and submissions are held until 08:00 for a fresh look
  (Send / Drop in Focus, or "send held #3"); anything that buys waits 24 hours.
- **Routine anchors** (e.g. "07:30 Morning routine | Water; Medication; Pick today's one thing") — reminded gently,
  ticked as wins.
- **📈 Trends + summary for your doctor:** sleep, energy and mood charts, and a printable 30-day page (notes optional).
- Voice & Telegram: "what should I do?", "I'm stuck on the QBR", "brain dump: …", "I'm done", "not now",
  `/focus`, `/dump`, `/checkin`.

## [2.6.1] — 2026-09-30
### Changed
- **A clean brain sphere:** no more name tags floating on the globe (turn them back on in Settings → Appearance →
  *Show name tags on the brain*). Hover any shape for its name.
- **Every category has its own colour**, and each family its own shape: memories ● spheres, notes ◆ diamonds,
  creations ■ cubes, activity ▲ pyramids, people ● with a ring.
- **🎨 Colour key** (top right) shows what each colour means, with counts, plus every project's colour. Click a
  colour to list those items, click a project to light up its cluster. Minimise it with ▾ (M minimises all panels);
  it starts minimised on phones.

## [2.6.0] — 2026-09-30
### Added
- **📥 Forward anything to Telegram → filed in your brain.** Forwarded messages, bare links, PDFs/Word/Excel files,
  photos (whiteboards and slides are transcribed; **business cards become people cards**) and forwarded voice notes
  (transcribed) are summarised and saved in brain/Inbox, with the people in them carded and the right project
  linked. `/save <text or link>`, or reply `/save` to any message, does the same. A file sent *with* a caption is
  still treated as a request. Switch off in Settings → Telegram. "Save this to my brain: <link or file>" works by voice too.
- **👤 People cards.** Everyone you deal with gets a card: company, role, email, phone, notes, when they were last
  mentioned, and everything that mentions them — grouped into *Deals & money*, *What Nova knows*, *Notes & meetings*,
  *Requests*. Cards are made from what you say, meeting notes, business cards and forwards, and the nightly dream
  catches up on the rest. People appear in the globe linked to their mentions; click one to light up their web.
  Edit, merge ("Sam" + "Sam Dlamini") or delete cards in their panel; add people from the People list.
  Voice: "Who is Sam?", "What's Lerato's number?", "Save Johan Pretorius at Nokia, account manager".
- **🧠 Ask the brain.** Tick **🧠 brain** in the dashboard's ask bar (or ask Nova "what do I know about…") and the
  answer comes only from your own memories, notes, people cards and creations, with numbered sources you can click.
- **Weekly digest.** Sunday's dream sends "what's new in your brain" to Telegram and saves it in brain/Digests
  (Settings → Dreaming). Ask any time: "what's new in my brain this week?"
- **🕘 Timeline on the globe.** Replay how your brain grew with ▶, drag through time, or show only one month or week.

## [2.5.1] — 2026-09-30
### Changed
- Dashboard: the left panel (Busy with, Layout, View…) and **Live activity** are now see-through so the brain shows
  behind them; they turn solid while your mouse is over them.
- Both can be **minimised** (‹ / ▾ buttons, or press **M** for both). Minimised, the left panel becomes a small ☰
  button that glows while Nova is working, and Live activity becomes a pill with a count of new events. Nova
  remembers your choice on each device.

## [2.5.0] — 2026-09-30
### Added
- **⚡ Load-shedding** (plugins/loadshedding.py, EskomSePush): "what stage are we on?", your area's schedule, and a
  spoken + Telegram heads-up 30 minutes before your power goes off. Free token (Settings → API keys → EskomSePush
  token); say "find my load-shedding area Roodepoort", then "use area <id>". Checks are cached so Nova stays well
  inside the free 50-a-day allowance.
- **💸 Price watcher** (plugins/price_watch.py): "tell me when this drops below R8,000" + a Takealot or other shop link.
  Nova checks every few hours (Takealot via its product API, other shops via the price they publish for Google)
  and messages you once when the price reaches your target. "What prices are you watching?", "stop watch 2".
- **📰 News briefing** (plugins/news.py): SA headlines (News24, BusinessTech, MyBroadband, TechCentral), "any news
  about Nokia?", and **vendor news** for Juniper, Avaya, Nokia, SonarSource and Westcon-Comstor (edit the list in
  Settings → News). Free, no key.
- The **morning briefing** now includes today's load-shedding and a line of vendor news.
- **One-click MCP servers** — Settings → Extensions → *Add an MCP server*: Windows control, Excel, ElevenLabs studio
  (sound effects, voice design) and YouTube transcripts. Restart Nova after adding.
- Settings: new **Load-shedding** and **News** sections with Check buttons.

## [2.4.0] — 2026-09-29
### Added
- **The 2nd brain sorts itself into projects — always.** Every project is the centre of its own cluster; the
  memories, notes, creations, missions, people and actions that belong to it gather around it, joined by lines in the
  project's colour. Sorting runs automatically every time the brain refreshes, so new things drop into the right
  cluster as they arrive.
  - Linking uses the project's names (Nova, TrueHome, Juniper, Avaya…) and meaning (local embeddings).
    Projects nest: every "Nova upgrade: …" roadmap item sits under the Nova project.
  - Click a project: its whole cluster lights up, the rest dims, and its panel lists **Everything in this project**
    by category.
  - Any item's panel has a **Project** picker: move it to another project, to none, or back to automatic.
    Your choices are saved permanently.
  - **Layout** switch: **Projects** (clusters), **Categories** (tidy sectors) or **Web** (free-flowing).
    Projects is the default; your choice is remembered (Settings → Appearance → Brain layout).

## [2.3.0] — 2026-09-29
### Added
- **Everything starts with run.bat**:
  - **Phone access (Tailscale)**: once Set up has worked, Nova remembers it (`remote.enabled`), and on every start it
    reconnects Tailscale (starts the Tailscale app / `tailscale up` if needed) and re-shares the dashboard and
    globe. If Tailscale ever needs a one-time approval again, the link is sent to your Telegram.
  - **Telegram** already started with Nova; the 🟢 online message now includes your phone dashboard link.
  - A **"Nova is ready"** summary in Nova's window shows Dashboard, Voice, Telegram and Phone — and exactly what to
    fix if one of them isn't running (e.g. "add your bot token in Settings → API keys").

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
