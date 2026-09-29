# Installation guide

Budget about 30–45 minutes the first time. Most of that is waiting for downloads.

- [1. Before you start](#1-before-you-start)
- [2. Get the code](#2-get-the-code)
- [3. Run setup](#3-run-setup)
- [4. Add your keys](#4-add-your-keys)
- [5. First start](#5-first-start)
- [6. Telegram remote control](#6-telegram-remote-control)
- [7. Google Workspace](#7-google-workspace)
- [8. GitHub backup, updates and rollback](#8-github-backup-updates-and-rollback)
- [9. Optional extras](#9-optional-extras)
- [Manual install](#manual-install-no-setupbat) · [macOS / Linux](#macos--linux-experimental) · [Uninstall](#uninstall)

---

## 1. Before you start

| Need | Why | Get it |
|---|---|---|
| **Windows 10/11 64-bit** | Setup scripts, app launching and shortcuts are Windows-first | — |
| **Python 3.11** | Runs Nova (3.12 may work; 3.13 is not supported by some audio packages yet) | [python.org](https://www.python.org/downloads/release/python-3119/). **Tick "Add python.exe to PATH".** |
| **8 GB free disk** | Local AI model (~2.5 GB), speech models, packages | — |
| **Microphone + speakers** | Voice | — |
| **NVIDIA GPU (4 GB+)** | Runs the local model fast. Without one, Ollama uses the CPU (slower). | Latest driver from nvidia.com |
| winget | Setup uses it to install Ollama and Git | Built into Windows 11; on Windows 10 install "App Installer" from the Microsoft Store |

**Optional:** webcam · Telegram · Google account · GitHub account · [Node.js LTS](https://nodejs.org) (only for MCP servers started with `npx`) · Docker Desktop (only for Docker-based MCP servers).

## 2. Get the code

**With Git:**
```bat
git clone https://github.com/lesliepieter-Terblanche/Nova-Beta1.git %USERPROFILE%\Nova
```
**Without Git:** on GitHub click **Code → Download ZIP** and unzip it to a permanent folder, e.g. `C:\Users\you\Nova`.
Avoid OneDrive-synced folders: syncing the memory database while Nova writes to it can corrupt it.

## 3. Run setup

Double-click **`setup.bat`**. It will:

1. create a private Python environment in `.venv`
2. install the Python packages in `requirements.txt` (the long step)
3. install the Chromium browser that Nova controls
4. install **Ollama**, download `qwen2.5:3b` + `nomic-embed-text` and build `nova-qwen` (8k context window)
5. optionally download the local vision model (Gemma 3 4B, 3.3 GB). It asks first.
6. download the local voices: Kokoro-82M (natural) and Piper (fast)
7. ask your **name**, **time zone** and **city** and create `config.yaml`
8. create `.env` and the **Nova** and **Nova Brain** desktop shortcuts

It's safe to run again: finished steps are skipped.

## 4. Add your keys

`.env` opens in Notepad at the end of setup. All keys are optional, but more keys means more abilities:

| Key | What it unlocks | Where | Cost |
|---|---|---|---|
| `ELEVENLABS_API_KEY` | Natural voice (otherwise the offline Piper voice) | elevenlabs.io → Profile → API keys | Paid plan or free credits |
| `ELEVENLABS_VOICE_ID` | Your chosen/cloned voice (blank = stock "George") | elevenlabs.io → Voices → ⋯ → Copy voice ID | — |
| `GEMINI_API_KEY` | Smart model for hard tasks + vision (screen, photos, webcam, ads) | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | Free tier |
| `GROQ_API_KEY` | Backup smart model | [console.groq.com/keys](https://console.groq.com/keys) | Free tier |
| `TELEGRAM_BOT_TOKEN` | Phone remote control | Telegram → @BotFather → `/newbot` | Free |
| `PEXELS_API_KEY` | Stock footage behind video scenes | [pexels.com/api](https://www.pexels.com/api/) | Free |

> A Gemini *app* subscription (Google AI Pro/Ultra) doesn't include API access. The AI Studio key is separate and free.

Save `.env`. Never commit it (it's git-ignored).

## 5. First start

Double-click **Nova** on the desktop. You should see:

- a console window (minimised) logging `[llm]`, `[nova] skills: …`, `[dashboard] http://localhost:8765`
- the dashboard opening in your browser
- Nova saying **"Nova is online."**

Say **"Hey Jarvis"**, wait for the chime, then speak. Say "thanks" to end a conversation.

**Check everything:** open a terminal in the Nova folder and run
```bat
run.bat --check
```
Each line shows `OK` or `--` with what's missing.

**No microphone handy?** `run.bat --text` lets you type instead.

## 6. Telegram remote control

1. Start Nova, open your bot in Telegram, send **`/id`**.
2. Put the number in `config.yaml`:
   ```yaml
   telegram:
     allowed_user_ids: [123456789]
   ```
3. Restart Nova. Only listed IDs can use the bot. Everyone else is refused.

Send text or voice notes; send photos or files and they're saved to `workspace/inbox`.
Commands: `/new` (fresh conversation), `/status`, `/id`.

## 7. Google Workspace

One-time, ~10 minutes. Google's APIs are free for personal use.

1. [console.cloud.google.com](https://console.cloud.google.com) → **New project** → name it `Nova`.
2. **APIs & Services → Library**, enable: **Gmail API, Google Calendar API, Google Drive API,
   Google Docs API, Google Sheets API, Google Tasks API**.
3. **APIs & Services → OAuth consent screen** (Google Auth Platform):
   - User type **External**, app name `Nova`, your email as support and developer contact.
   - **Audience / Test users:** add your Gmail address.
   - Click **Publish app**. Otherwise Google expires your sign-in every 7 days. For a personal app you'll
     see an "unverified app" warning when you sign in. Click *Advanced → Go to Nova*.
4. **Credentials → Create credentials → OAuth client ID → Application type: Desktop app** → **Download JSON**.
5. Save it as **`secrets\credentials.json`** in the Nova folder.
6. Run:
   ```bat
   run.bat --google-login
   ```
   Approve in the browser. The token is saved to `secrets\token.json`.

Test by saying: *"What's on my calendar today?"*

## 8. GitHub backup, updates and rollback

1. Double-click **`setup_github.bat`**. It installs Git if needed and creates a **private** repo
   (with the GitHub CLI if you have it, otherwise it opens github.com/new and asks for the URL), tags `v1.0.0`
   and pushes. `.env`, `config.yaml`, `secrets/`, `data/` and `workspace/` are never uploaded.
2. **Save your edits as a version** (after changing code in VS Code):
   VS Code → *Terminal → Run Task → "Nova: save my changes to GitHub"*, or double-click `release.bat`.
3. **Updates:** Nova checks GitHub at start-up and tells you when something newer exists.
   Say *"update yourself"* or run `update.bat`. Nova restarts itself.
4. **Rollback:** say *"roll back to the previous version"*, or run `rollback.bat` to pick any tag or commit.
   Uncommitted edits are stashed first (`git stash pop` brings them back).

## 9. Optional extras

| Extra | How |
|---|---|
| **"Hey Nova" wake word** | [HEY_NOVA.md](HEY_NOVA.md) (free Colab training, ~1 hour) |
| **Start with Windows** | `powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 on` |
| **Private search** (SearXNG) | Docker Desktop, then `docker run -d --name searxng --restart unless-stopped -p 8888:8080 -v "%cd%\scripts\searxng:/etc/searxng" searxng/searxng` and set `web.searxng_url: http://localhost:8888` |
| **Local AI images** (Stable Diffusion 1.5) | `.venv\Scripts\pip install -r requirements-imagegen.txt`, then `media.image_gen_enabled: true` |
| **MCP servers** (Home Assistant, GitHub, Windows apps…) | [MCP.md](MCP.md) |
| **Use Nova's memory from Claude Desktop** | [MCP.md → Nova as an MCP server](MCP.md#nova-as-an-mcp-server) |
| **Scheduled briefings** | `routines:` in [CONFIGURATION.md](CONFIGURATION.md#routines) |
| **Your own skills** | [SKILLS.md](SKILLS.md) |

---

## Manual install (no setup.bat)

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
winget install Ollama.Ollama
ollama pull qwen2.5:3b
ollama pull nomic-embed-text
ollama create nova-qwen -f Modelfile
python scripts\download_voice.py
python scripts\first_run.py
copy .env.example .env
powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1
python main.py
```

## macOS / Linux (experimental)

The Python code is cross-platform, but the scripts, app launching, power actions and shortcuts are Windows-first.
Roughly: install Python 3.11, [Ollama](https://ollama.com), PortAudio (`brew install portaudio` /
`sudo apt install portaudio19-dev`), then follow the manual steps with `source .venv/bin/activate` and run
`python main.py`. Pull requests that improve other platforms are welcome.

## Uninstall

1. Close Nova. Delete the desktop shortcuts, and `scripts\autostart.ps1 off` if you enabled autostart.
2. **Back up `data\`** first if you want to keep your memory (`data\nova.db`) and notes (`data\brain\`).
3. Delete the Nova folder. Optionally uninstall Ollama from *Settings → Apps* and remove its models folder
   `%USERPROFILE%\.ollama`.
4. Revoke Google access at [myaccount.google.com/permissions](https://myaccount.google.com/permissions),
   and delete the Telegram bot with @BotFather (`/deletebot`) if you don't need it.
