# Troubleshooting

Start with the health check. It tells you what's missing:

```bat
run.bat --check
```

## Setup

| Problem | Fix |
|---|---|
| `Python 3.11 was not found` | Install 3.11 from python.org with **Add python.exe to PATH** ticked, then reopen the terminal. |
| `pip install` fails on a package with "Microsoft Visual C++ 14.0 is required" | Install [Build Tools for Visual Studio](https://visualstudio.microsoft.com/visual-cpp-build-tools/) ("Desktop development with C++"), rerun setup. Usually caused by using Python 3.13. Use 3.11. |
| `winget` not recognised | Windows 10: install **App Installer** from the Microsoft Store, or install [Ollama](https://ollama.com/download) and [Git](https://git-scm.com) manually. |
| `ollama pull` hangs or fails | Check your internet, then run the three `ollama` lines from INSTALL.md manually. The model is ~2.5 GB. |
| Setup looks frozen | Press **Enter** once (clicking inside a console window pauses it). Ollama also pauses at 100% while it verifies a download. If it's still stuck after 5 minutes, press Ctrl+C and run `setup.bat` again — finished steps are skipped and downloads resume. |
| "Failed to install browsers" | Harmless if you have Chrome or Edge — Nova uses those. Otherwise retry later: `.venv\Scripts\python -m playwright install chromium`. |
| Setup window closes instantly | Run it from a terminal (`cd` to the folder, type `setup.bat`) to see the error. |

## Voice

| Problem | Fix |
|---|---|
| Never wakes up | Check the Windows microphone privacy setting (Settings → Privacy → Microphone → allow desktop apps). Set your mic as the **default** input device. Lower `voice.wake_threshold` to 0.35. |
| Wakes up randomly | Raise `wake_threshold` to 0.6–0.7. |
| Cuts me off mid-sentence | Raise `voice.silence_seconds` to 1.5. |
| Keeps recording forever | Background noise is above the threshold. Use a headset or move away from fans/TV. |
| Wrong words transcribed | `voice.stt_model: small.en` (more accurate, slower). |
| Robotic voice | ElevenLabs isn't being used. Check `ELEVENLABS_API_KEY`, your quota and `tts.engine: elevenlabs`. The console shows `[tts] ElevenLabs failed (…)` with the reason. |
| No sound at all | Check the default output device. `pip install --force-reinstall sounddevice`. |
| `PortAudio library not found` | `pip install --force-reinstall sounddevice` (the Windows wheel bundles PortAudio). |

| Hotkey does nothing | Another app may own Ctrl+Alt+Space. Change `voice.hotkey` (e.g. `"<ctrl>+<shift>+n"`). Some games/admin windows block global hotkeys. |
| Nova interrupts itself | Its own voice is triggering the wake word through the speakers. Use a headset, lower the volume, or set `voice.barge_in: false`. |
| Kokoro voice not used | Run `python scripts\download_voice.py`. Check `tts.fallback` includes `kokoro`. |

## Meetings

| Problem | Fix |
|---|---|
| Only my side of the call was recorded | PC-audio capture uses the **default playback device**. Make sure the call plays through it (not a separate headset device), or set that headset as default. |
| "PC audio capture unavailable" | `pip install --force-reinstall soundcard`. Mic-only recording still works. |
| Notes take long | A 1-hour meeting takes a few minutes to transcribe on CPU. Use `meetings.stt_model: base.en` for speed. |
| No Google Tasks created | Google must be connected, and the action item must be yours ("me"/your name). |

## Brain

| Problem | Fix |
|---|---|
| `All models failed` | Is Ollama running? `ollama list` should show `nova-qwen`. Start it with `ollama serve`. |
| Slow answers | The first request loads the model into the GPU (~10 s). Keep Ollama running. `nvidia-smi` shows whether the GPU is used. |
| Local model gives odd answers or calls the wrong tools | Normal for 3B models on complex asks. Add a `GEMINI_API_KEY`: hard requests and struggling turns escalate automatically. Add words to `llm.escalate_keywords`. |
| `[llm] smart=none` | No Gemini/Groq keys in `.env`. |
| Gemini `400` on tool schema | An MCP server's tool schema uses features Gemini rejects. Use `only_tools` to exclude it, or put Groq first in `smart`. |
| Semantic memory search not working | `ollama pull nomic-embed-text`. Without it, keyword search is used. |

## Telegram

| Problem | Fix |
|---|---|
| Bot says "Not authorised" | Put the ID it shows into `telegram.allowed_user_ids` and restart. |
| Bot doesn't respond | Check `TELEGRAM_BOT_TOKEN`. Only one program can poll a bot. Close other copies of Nova. |
| No voice replies | ElevenLabs or ffmpeg issue. Text still arrives, and the console shows `[telegram] voice reply failed`. |

## Google

| Problem | Fix |
|---|---|
| `Google isn't connected yet` | Settings → Google → **Connect Google** |
| "needs a one-time reconnect for new permissions (Slides)" | Enable **Google Slides API** in the Cloud Console, then click **Connect Google** once. |
| `Missing secrets\credentials.json` | Download the **Desktop app** OAuth client JSON, save it with exactly that name. |
| `access_denied` / "app not verified" blocks you | Add yourself as a test user, or publish the app, then click *Advanced → Go to Nova*. |
| Logged out every 7 days | Publish the app (OAuth consent screen → Publish app), then click **Connect Google** once more. |
| `insufficient permissions` | You enabled more APIs later. Delete `secrets\token.json` and run `--google-login` again. |
| `API has not been used in project` | Enable that API in Google Cloud Console → Library. |

## Browser, webcam, media

| Problem | Fix |
|---|---|
| Browser tools fail to start | `.venv\Scripts\python -m playwright install chromium` |
| Sites log you out | Nova uses its own profile in `data\browser_profile`. Log in once in *that* window. |
| `Couldn't open the webcam` | Close Teams/Zoom/Camera app. Check Settings → Privacy → Camera. |
| Ads have the full photo, not a cut-out | `pip install "rembg[cpu]"`. The first run downloads a ~170 MB model. |
| Video creation fails | The console shows the ffmpeg error. Check disk space. |

## MCP

| Problem | Fix |
|---|---|
| `[mcp] <name> failed to start` | Run the `command args…` from the config manually in a terminal to see its error. For `uvx`, start Nova via `run.bat` so the venv's `uvx` is on PATH. For `npx`, install Node.js LTS. |
| Tools never used | Add `keywords` that you actually say. Check with *"which extensions are loaded?"* |
| Asks for confirmation too often | `confirm: never` for read-only servers, or a list of the tools that should ask. |

## Updates and rollback

| Problem | Fix |
|---|---|
| "Nova isn't a git repository yet" | Run `setup_github.bat` (or clone the repo instead of downloading the ZIP). |
| Update says my edits were stashed | They're safe: `git stash list`, then `git stash pop`. |
| Want to undo a rollback | Say "update yourself" or run `update.bat`. |

## Settings page

| Problem | Fix |
|---|---|
| "Some settings weren't saved" | The message names the field and why (e.g. colour must look like `#8b7bff`, time like `07:30`). |
| Changes don't take effect | Most need a restart. Use the banner's **Restart Nova now**. Appearance applies instantly. |
| Messed up `config.yaml` | Each save keeps the previous version as `config.yaml.bak`. Copy it back. |
| 403 when opening via Tailscale or another name | Add the host name to `dashboard.allowed_hosts`. |

## Gestures

| Problem | Fix |
|---|---|
| "MediaPipe isn't installed" | Run `update.bat`, or `.venv\Scripts\pip install mediapipe`. |
| "Couldn't open webcam" | Close Teams/Zoom/Camera app, or set another **Webcam number** in Settings → Gestures. |
| Model download fails | The first start downloads two small models (`models/vision/`) from Google — check the internet connection. |
| Pointer jumps / wrong gesture | Good light, hand 40–80 cm from the camera, palm facing it. Lower **Checks per second** if the PC is busy. |
| Camera preview is empty / shows a message | Read the message in the preview. Usual fixes: close Teams, Zoom and the Camera app; open the webcam's privacy cover; Windows Settings → Privacy & security → Camera → turn on **Camera access** and **Let desktop apps access your camera**; try another **Webcam number**. |
| Cursor doesn't move | Update to 2.11+ (the cursor now follows your whole hand). Check the ✋ chip on the dashboard says "hand seen"; click **Camera** to see the hand box — keep your hand inside it. |
| Have to stretch to reach the screen edges | Make **Hand area** smaller in Settings → Gestures. |
| Clicks when I don't want | Keep your fingers loosely open while moving — a curled, fist-like hand counts as a click. |
| Zooms by accident | Keep roughly the same distance from the camera while moving, or untick **zoom** in Settings → Gestures. |
| Swipe doesn't trigger | Swipe quicker and flatter (a fast sideways flick of an open hand). |
| Don't want the mouse moved | Untick **Air mouse** in Settings → Gestures. |

## Phone access (Tailscale)

| Problem | Fix |
|---|---|
| "Tailscale isn't installed" | Install it from tailscale.com/download on the PC **and** the phone; sign in with the same account. |
| Set up asks to enable HTTPS | Click the link it shows (Tailscale admin → DNS → enable MagicDNS + HTTPS Certificates), then Set up again. |
| Phone can't open the address | Tailscale must be switched **on** in the phone app, and Nova running on the PC. |
| 403 on the phone | Update Nova (the PC's Tailscale name is allowed automatically since 2.2). |
| Globe doesn't open on the phone | Start it once on the PC (🌐), then use the `:8443` address. |

## Backups & dreaming

| Problem | Fix |
|---|---|
| Where are my backups? | `backups\nova-backup-<date>.nova` (encrypted, last 7). Optional copy in Google Drive › Nova Backups. |
| Lost the passphrase | It's in `.env` as `NOVA_BACKUP_PASSPHRASE` (also sent to your Telegram the first time). Without it a backup can't be opened. |
| Restore on a new PC | Install Nova, stop it, then `python -m nova.dreaming restore backups\<file>.nova --apply` and enter the passphrase. Current files are moved to `restore\before-…`. |
| Dreaming didn't run | The PC was off at 02:30 — it catches up ~10 min after Nova starts. Or say "dream now". |

## Slow replies

Every reply shows where the time went (console: `[timing] …`; dashboard: open the action → *Where the time went*).

| What you see | Fix |
|---|---|
| First command after a pause takes 10–30 s | Fixed in 1.5 (model kept loaded). Restart Nova once after updating so Ollama picks up `OLLAMA_KEEP_ALIVE`. |
| "thinking" is most of the time, on `ollama` | A 3B model on a 4 GB card needs 2–6 s per step. For much faster answers set **Settings → General → Everyday model** to `gemini` (or `groq`); Ollama stays as the offline backup. |
| Several steps for a simple question | Normal for tool use (1 step to decide, 1 to answer). Short, specific requests help. |
| Slow right after another command | Background learning was using the local model; in 1.5 it uses Gemini/Groq when a key is set. |

## Cloud model errors

| Problem | Fix |
|---|---|
| `model ... does not exist` / `decommissioned` | The provider retired the model. Set the model to `auto` in Settings (1.5 does this by itself on **Test**). |
| Groq test says it's an xAI key | Groq keys start with `gsk_`; xAI Grok keys start with `xai-`. Paste each in its own box. |
| `401` / invalid API key | Copy the key again (no spaces), save, Test. |

## Nova won't start / "localhost refused to connect"

Nova isn't running. Start it with the **Nova** or **Nova Brain** shortcut, or run `run.bat` in a Command Prompt to
see messages. The full log of the last run is in `data\logs\nova.log` (and `nova.previous.log`).

## Still stuck?

[Open an issue](../../issues/new?template=bug_report.md) with the output of `run.bat --check` and the console
lines around the error. Remove keys and personal details first.

## Add-ons (v2.12)

| Problem | Fix |
|---|---|
| "No image generator is running" | Start ComfyUI (or fill in **Start ComfyUI with** in Settings → Images). Check it at http://127.0.0.1:8188. |
| ComfyUI out of memory | On a 4 GB card use SD 1.5 models or SDXL-Turbo, and start ComfyUI with `--lowvram`. |
| "ActivityWatch isn't running" | Install from activitywatch.net and start it; the tray icon must be there. Website names need its browser extension. |
| Web agent: "needs a Gemini or Groq key" | Add one in Settings → API keys. The small local model can't drive a browser reliably. |
| Web agent stops at a login | Settings → Web agent → **Open its browser to log in**, sign in, close the window, ask again. |
| Web agent install fails | Click **Install web agent** again to see the error. It needs internet and ~400 MB free. |
| Tables not found in a PDF | Install docling (Settings → Documents). Scanned PDFs work only with docling. |
| Nova doesn't hear me / starts on noise | Settings → Voice → **Speech detection sensitivity** (lower = hears softer speech). Or switch to **loudness**. |
| Shorts take long | Normal: about 1-2 minutes per 40-second clip on a laptop CPU. |

## Phone hands

| Problem | Fix |
|---|---|
| USB debugging is greyed out ("blocked by Auto Blocker") | Samsung: Settings → Security and privacy → Auto Blocker → off. It has to stay off while Nova controls the phone. |
| "I can't see the phone" | Use a data cable (not charge-only), unlock the phone, pull down the notification shade → USB → **File transfer**, and tap **Allow** on "Allow USB debugging?" with *Always allow* ticked. |
| "I can't reach the phone at 100.x.x.x" | Tailscale must be on on the phone. If the phone was restarted, plug it in and click **Set up my phone** again. |
| Download of the Android tools fails | Get *SDK Platform-Tools for Windows* from developer.android.com, unzip, and put the path to `adb.exe` in Settings → Phone. |
| Black screenshot / can't read the screen | Banking, payment and some streaming apps block this on purpose. |
| "Your phone is locked" | Unlock it yourself — Nova never enters your PIN. |

## Brain filing (01_Personal / 02_Work)

- **Nothing moved after the update** — correct: Nova only prepares a plan. Open the dashboard, press 🗂, answer the
  questions and press *Back up and move*. Or say "show me the brain structure" / "plan the brain reorganisation".
- **A note went to the wrong place** — 🗂 → pick the right domain and category → *Move it here* (links follow). Or
  tell Nova "move the note … to Personal, Finance".
- **Something looks wrong after the move** — the whole brain as it was is in `backups/brain-before-reorganise-<date>`
  (the 🗂 window shows the exact folder), together with `_nova_database_backup.db`. Close Nova, copy the folder back
  over `data/brain` and the database over `data/nova.db`.
- **I don't want the label lines or the rules** — `brain.structure: off` in `config.yaml`.
- **A status is wrong** — change it with *Track this* on the dashboard, or edit the `[LABEL: STATUS: …]` line in the
  note; the status boards follow.
- **The dashboard looks different / where is the sphere?** — since 2.18 the dashboard is the Master Index folder tree
  (faster, far less memory). Prefer it dark? Settings → Appearance → *Dashboard look* → dark.
- **A file isn't in the tree** — a folder shows its first few files; click "+ n more", or the number next to a
  category to list everything in it. Memories, creations and actions open from the counters top right.
- **Something is filed on the wrong side** — right-click it on the dashboard (long-press on the phone) → *Move to
  01_Personal / 02_Work* and pick the category. Deleted a note by mistake? The file is in `backups/deleted/`; drop it
  back onto the dashboard (📎) to file it again.

## Nova is slow to answer / the voice comes late

- Since 2.22 she speaks the first sentence while still writing the rest (Settings → Voice → *Start speaking while the
  answer is still being written*). If the voice still starts late, the wait is the model thinking, not the voice.
- Look at the `[timing]` line in Nova's window after a request: `thinking 6.2s on ollama` means the local model is the
  slow part. Put a fast cloud model first (Settings → Models → primary: Groq or Gemini) and keep Ollama as the
  private fallback; simple questions then start within about a second.
- Requests that use tools (calendar, email, web) need one model step per tool, so they take longer than a plain
  question whatever the voice does.
