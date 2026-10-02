# Enhancements and roadmap

Everything here is **free** and runs **locally** unless marked otherwise. "Fits 4 GB" means it works on a
4 GB-VRAM NVIDIA card with 16 GB RAM (the baseline PC Nova was built for).

**Status:** ✅ built in · ⚙ config-ready (just enable) · 🧩 easy add (plugin/playbook, < 1 day) · 🛠 bigger project

---

## Top 10 next steps (best value on a 4 GB PC)

| # | Upgrade | Why | Status | Fits 4 GB |
|---|---|---|---|---|
| 1 | **Windows-MCP** | Nova can operate any desktop app (Excel, Outlook, settings), not just the browser | ⚙ | ✅ (no GPU) |
| 2 | **Local vision: Gemma 3 4B** | Screen, photo and webcam understanding without the cloud | ✅ v1.2 (auto-fallback after Gemini) | ✅ (swaps with the chat model) |
| 3 | **Kokoro-82M offline voice** | Much more natural than Piper as the offline/backup voice, runs on CPU | ✅ v1.2 | ✅ CPU |
| 4 | **Meeting recorder** | Record mic + PC audio → Whisper transcript → summary, actions and people filed into memory | ✅ v1.2 | ✅ CPU |
| 5 | **Burned-in captions + free B-roll** | Subtitles on videos and stock footage from the free Pexels API | ✅ v1.2 | ✅ |
| 6 | **Morning briefing routine** | Spoken daily rundown at 07:30 + copy on your phone | ✅ v1.2 (with weather) — enable in `routines:` | ✅ |
| 7 | **Home Assistant** | Lights, geyser, gate, alarm, load-shedding automations by voice | ⚙ | ✅ |
| 8 | **Barge-in + push-to-talk hotkey** | Interrupt Nova mid-sentence; talk with a hotkey in noisy places | ✅ v1.2 | ✅ |
| 9 | **SearXNG private search** | Unlimited, private meta-search instead of DuckDuckGo rate limits | ✅ v1.2 (run it in Docker, see INSTALL) | ✅ |
| 10 | **Tailscale for the dashboard** | Open the 3D brain on your phone securely, anywhere | ⚙ `tailscale serve 8765` | ✅ |

---

## 🧠 Brain (language models)

| Option | Notes | Status |
|---|---|---|
| **Qwen 2.5 3B** (current) | Good tool calling for its size | ✅ |
| **Qwen 3 4B** (`qwen3:4b`) | Newer and stronger reasoning. Test tool calling on your prompts before switching. | ⚙ change `Modelfile` |
| **Llama 3.2 3B** / **Phi-4-mini** | Alternatives if Qwen misbehaves | ⚙ |
| **Qwen 2.5 7B / Qwen 3 8B** | Big jump in quality. Needs 8 GB VRAM. | ⚙ (needs GPU upgrade) |
| **LM Studio / llama.cpp server** | Any OpenAI-compatible local server works as a provider | ⚙ |
| **Gemini 2.5 Flash / Groq** (cloud, free tiers) | Current smart tier | ✅ |
| **Response cache** | Instant answers for repeated questions ("what's my target?") | 🧩 |
| **Local router model** | Tiny classifier picks tool groups instead of keywords | 🛠 |

## 👂 Ears (listening)

_Barge-in and push-to-talk shipped in v1.2._

| Option | Notes | Status |
|---|---|---|
| faster-whisper `base.en` (current) | Fast on CPU | ✅ |
| `small.en` / `distil-large-v3` | More accurate, slower. Try `small.en` first. | ⚙ `voice.stt_model` |
| **Silero VAD** | Smarter end-of-speech detection than the energy threshold | 🧩 |
| **Noise suppression** (RNNoise / DeepFilterNet) | Better in cars, offices, with the TV on | 🧩 |
| **Speaker verification** (SpeechBrain ECAPA) | Only obey *your* voice | 🛠 |
| **Custom "Hey Nova"** | [HEY_NOVA.md](HEY_NOVA.md) | ⚙ |
| **Afrikaans / multilingual** | `stt_model: small` + `stt_language: null`; ElevenLabs `eleven_multilingual_v2` | ⚙ |

## 🗣 Voice (speaking)

| Option | Notes | Status |
|---|---|---|
| ElevenLabs Flash v2.5, streamed (current) | Lowest latency | ✅ |
| Piper (current fallback) | Fully offline | ✅ |
| **Kokoro-82M** | Best free CPU voice today, Apache-2.0 | ✅ |
| **Local voice cloning** (XTTS-v2, F5-TTS) | Your own voice offline. Check licences (XTTS weights are non-commercial). ~4 GB VRAM. | 🛠 |
| **Sentence pipelining** (local voices) | Speak sentence 1 while sentence 2 is generated | ✅ |
| **LLM token streaming** | Start speaking before the model has finished writing | 🛠 |

## ♾ Memory and second brain

| Option | Notes | Status |
|---|---|---|
| SQLite + embeddings + Markdown vault (current) | Permanent, versioned, Obsidian-compatible | ✅ |
| **Obsidian** as the note editor | Open `data/brain` as a vault; Nova indexes your edits at start-up | ⚙ |
| **Nightly memory consolidation** | Merge duplicates, summarise the day into the journal, promote important facts | 🧩 routine + playbook |
| **Memory import** | Seed memory from Google Contacts, CV/LinkedIn export, past emails | 🧩 |
| **Encrypted backups** | Nightly zip of `data/` to Google Drive | 🧩 |
| **sqlite-vec / LanceDB** | Faster vector search at 100k+ items | 🛠 |
| **Knowledge graph view** | Entities and relations extracted into the 3D graph | 🛠 |

## 📄 Documents and OCR

| Option | Notes | Status |
|---|---|---|
| PDF/DOCX/XLSX text (current) | pypdf, python-docx, openpyxl | ✅ |
| **Docling** (IBM, MIT) | Tables and layouts from PDFs → clean Markdown | 🧩 |
| **RapidOCR / Tesseract** | Read scans, receipts, photos of whiteboards | 🧩 |
| **Document Q&A** | "What does clause 7 of this contract say?" over long files | 🧩 |
| **Create Word/Excel/PowerPoint** | python-docx / openpyxl / python-pptx from templates (quotes, QBR decks) | 🧩 |

## 🌐 Web and browser

| Option | Notes | Status |
|---|---|---|
| DuckDuckGo search, trafilatura scraping, Playwright browser (current) | | ✅ |
| **SearXNG** (self-hosted) | Private, no rate limits | ✅ |
| **Crawl4AI** | JavaScript-heavy sites, multi-page crawls → Markdown | 🧩 |
| **Price / page watchers** | "Tell me when this drops below R5,000" (routine + scrape) | 🧩 |
| **Website deploy** | Publish built sites to Netlify/Cloudflare Pages/GitHub Pages (free tiers) | 🧩 |
| **Autonomous browsing** (browser-use) | Multi-step web tasks with less hand-holding. Best with the smart model. | 🛠 |

## 🎬 Media

| Option | Notes | Fits 4 GB | Status |
|---|---|---|---|
| Slide/photo videos + narration (current) | | ✅ | ✅ |
| **Burned-in captions** | Timed caption overlays | ✅ | ✅ |
| **Pexels B-roll** | Free stock video behind scenes (Pixabay could be added) | ✅ | ✅ |
| **ComfyUI** | Best local image workflows. SD 1.5 / SDXL-Turbo in `--lowvram` mode. | ✅ (slow) | 🛠 |
| **Real-ESRGAN upscaling** | Sharpen product photos for ads | ✅ | 🧩 |
| **Local text-to-video** (Wan 2.1 1.3B, LTX-Video) | Real AI video clips | ❌ needs ~8 GB+ | 🛠 |
| **Music** (MusicGen small) | Background tracks. Weights are non-commercial. | ⚠️ CPU, slow | 🧩 |

## 📱 Communication and remote

| Option | Notes | Status |
|---|---|---|
| Telegram bot (current) | Text, voice notes, files, push notifications | ✅ |
| **Tailscale** | Private access to the dashboard from your phone | ⚙ |
| **RustDesk** | Free, open-source full remote desktop | ⚙ (separate app) |
| **WhatsApp** | Only via the official WhatsApp Business Cloud API (Meta business setup). Unofficial bridges risk bans. | 🛠 |
| **Discord / Slack bot** | Same pattern as the Telegram bot | 🧩 |
| **Email-to-Nova** | Forward an email to a label; Nova processes it | 🧩 |

## 🏠 Automation and devices

| Option | Notes | Status |
|---|---|---|
| **Home Assistant** (MCP) | Smart home, sensors, automations | ⚙ |
| **n8n** (self-hosted) | Visual automations Nova can trigger, and that can call Nova | ⚙ via MCP |
| **Windows-MCP** | Operate desktop apps | ⚙ |
| **Frigate** | Local AI camera/NVR; "who's at the gate?" | 🛠 |
| **Stream Deck / hotkeys** | Physical buttons for routines | 🧩 |

## 💼 Work and business

| Option | Notes | Status |
|---|---|---|
| **Pipeline sheet** | Deal tracker in Google Sheets. "Add a R1.2m Juniper deal for Northwind, closing Nov." | 🧩 playbook |
| **QBR pack builder** | Pulls numbers from Sheets → PowerPoint from your template | 🧩 |
| **Quote/proposal drafts** | Word template + price list → PDF | 🧩 |
| **CRM via MCP** | HubSpot/Salesforce/Pipedrive MCP servers (cloud services) | ⚙ |
| **LinkedIn/Marketplace posting** | Browser automation with a submit confirmation | 🧩 |

## 🔐 Safety and reliability

| Option | Notes | Status |
|---|---|---|
| Confirmation gates, folder sandbox, Recycle Bin deletes, `.bak` files (current) | | ✅ |
| **Telegram PIN** for risky actions | Second factor for remote shell, email sending and deletes | 🧩 |
| **Audit export** | Activity log → CSV, weekly | 🧩 |
| **MCP servers in Docker** | Isolate third-party servers | ⚙ |
| **Health watchdog** | Auto-restart Ollama/Nova if they hang; Telegram alert | 🧩 |

---

## Claude products that complement Nova

Nova is local and free. These are optional companions:

- **Claude Desktop / Claude Code + Nova's MCP server:** use Nova's memory from Claude
  ([MCP.md](MCP.md#nova-as-an-mcp-server)).
- **Claude Code** is also a fast way to build new Nova plugins. Point it at this repo and describe the skill.

---

Have an idea? [Open a feature request](../../issues/new?template=feature_request.md).
