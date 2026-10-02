# Configuration

Nova reads two files from its folder:

| File | Holds | Committed to Git? |
|---|---|---|
| `config.yaml` | Settings (created from `config.example.yaml` by setup) | No, it's personal |
| `.env` | API keys and tokens (created from `.env.example`) | **Never** |

**Easiest:** use the **Settings page**, the gear icon on the dashboard (http://localhost:8765/settings).
It edits these same files, keeps your comments, validates values, masks keys, and has test buttons and a
restart button. Appearance changes apply instantly. Everything below can also be edited by hand.

Restart Nova after editing (say *"restart yourself"*, or close and reopen). Values in `config.yaml` can reference
environment variables from `.env` with `${NAME}` where noted (MCP `args`, `env`, `headers`).

---

## assistant

```yaml
assistant:
  name: Nova                # what the assistant calls itself (and the dashboard title)
  owner: Alex               # what it calls you
  timezone: Africa/Johannesburg   # IANA name — used for reminders, calendar, routines
  city: Johannesburg        # optional, for weather in briefings
  max_history_turns: 12     # short-term conversation memory per channel (long-term memory is separate)
```

## llm

```yaml
llm:
  primary: ollama           # tried first for every request
  smart: [gemini, groq, xai]  # tried in order for hard requests, or when the primary fails
  providers:
    ollama: { base_url: http://localhost:11434/v1, model: nova-qwen, timeout: 90 }
    gemini: { base_url: https://generativelanguage.googleapis.com/v1beta/openai/, model: gemini-2.5-flash, key_env: GEMINI_API_KEY }
    groq:   { base_url: https://api.groq.com/openai/v1, model: auto, key_env: GROQ_API_KEY }
    xai:    { base_url: https://api.x.ai/v1, model: auto, key_env: XAI_API_KEY }
    ollama_vision: { base_url: http://localhost:11434/v1, model: gemma3:4b, timeout: 180 }
  vision_providers: [gemini, ollama_vision]   # tried in order; local keeps vision working offline
  escalate_keywords: ["think hard", "in detail", "write a", "draft", "analyse", "compare", ...]
  max_tool_rounds: 6
  keep_alive: 24h           # keep the local model loaded between commands
```

- `model: auto` lets Nova pick the best chat model your key can use, and switch by itself when the provider
  retires a model. You can still name one (e.g. `openai/gpt-oss-120b`); if it disappears Nova falls back to auto.
- gemini, groq and xai are built in: adding the key in Settings is enough, even if they're missing from your
  `config.yaml`. A cloud key that isn't in `smart` is used as the last backup.
- **Speed:** on a 4 GB GPU, `primary: gemini` (or `groq`) answers several times faster than the local model;
  Ollama then stays the private, offline backup.

- Any **OpenAI-compatible** endpoint can be added as a provider: LM Studio (`http://localhost:1234/v1`),
  llama.cpp server, vLLM, OpenRouter, a work Azure OpenAI deployment and so on. Add it under `providers` and
  reference it in `primary` or `smart`.
- Providers whose `key_env` isn't set in `.env` are skipped automatically.
- **Fully offline:** `smart: []`. Website building and scripts then use the local model (lower quality). Vision (screen, photos, ads) needs Gemini, or a local vision model (see ENHANCEMENTS.md).
- **Bigger local model** (8 GB+ VRAM): `ollama pull qwen2.5:7b`, edit `Modelfile` (`FROM qwen2.5:7b`),
  `ollama create nova-qwen -f Modelfile`.
- Nova escalates automatically after 3 tool rounds, so a struggling small model hands over to the smart one.

## voice

```yaml
voice:
  enabled: true
  wake_word: hey_jarvis     # hey_jarvis | alexa | hey_mycroft | models/hey_nova.onnx (custom)
  wake_threshold: 0.5       # higher = fewer false wakes, lower = more sensitive
  stt_model: base.en        # tiny.en | base.en | small.en | base / small (multilingual)
  stt_language: en          # null = auto-detect (use a multilingual model)
  silence_seconds: 1.1      # how long a pause ends your sentence
  max_record_seconds: 25
  follow_up_seconds: 6      # keep listening this long after answering, without the wake word
  max_spoken_chars: 450     # longer answers: the start is spoken, the rest goes to screen/Telegram
  chime: true
  barge_in: true            # say the wake word (or press the hotkey) while Nova talks to interrupt it
  hotkey: "<ctrl>+<alt>+<space>"   # push-to-talk from anywhere; "" to disable (pynput format)
```

## tts

```yaml
tts:
  engine: elevenlabs        # first choice: elevenlabs | kokoro | piper | windows
  fallback: [kokoro, piper, windows]   # tried in order when the first choice fails
  elevenlabs:
    voice_id: JBFqnCBsd6RMkjVDRZzb   # overridden by ELEVENLABS_VOICE_ID in .env
    model: eleven_flash_v2_5         # fastest; eleven_multilingual_v2 = richer
    stability: 0.45
    similarity_boost: 0.8
    style: 0.15
    speed: 1.0
  kokoro:                   # natural local voice (Kokoro-82M on CPU), downloaded by setup
    model: models/kokoro-v1.0.int8.onnx
    voices: models/voices-v1.0.bin
    voice: bm_george        # British: bm_george bm_lewis bm_daniel bf_emma bf_isabella · US: am_adam af_heart af_bella
    lang: en-gb
    speed: 1.0
  piper:
    voice: voices/en_GB-alan-medium.onnx   # any voice from huggingface.co/rhasspy/piper-voices
```

Engines without their key/model files are skipped automatically. Local voices are generated sentence by sentence
while the previous sentence plays, so they start talking quickly.

## telegram

```yaml
telegram:
  enabled: true
  allowed_user_ids: [123456789]   # only these users can control Nova (send /id to your bot)
  voice_replies: true             # voice note in → voice note back
```

## google

```yaml
google:
  enabled: true
  credentials_file: secrets/credentials.json
  token_file: secrets/token.json
```

## brain (memory)

```yaml
brain:
  vault_dir: data/brain       # Markdown notes; open the folder as an Obsidian vault if you like
  db_file: data/nova.db       # memories, search index, activity log, creations
  embed_model: nomic-embed-text
  learn_automatically: true   # extract durable memories after each conversation
  structure: strict           # the filing rules below (off = the old free layout)
```

### Filing rules (`structure: strict`)

```
01_Personal/                          02_Work/
  01_Life_Admin/                        01_Role_&_Responsibilities/   Standard_Operating_Procedures/
  02_Health_&_Fitness/                  02_Clients_&_Partners/        Active_Accounts/  Partner_Network/
  03_Interests_&_Projects/              03_Projects_&_Strategy/       <Project_Name>/  Strategic_Growth_Plans/
      Hobbies/  Travel/                 04_Resources_&_Reference/     Technical_Documentation/
  04_Finance_&_Budgets/                 05_Meetings_&_Reports/        Meetings/  Digests/  Missions/
  05_Journal/   Dreams/
```

- Only those two folders at the top; numbered categories; at most three folders deep; `Pascal_Snake_Case` file names
  (dates stay in front: `2026-09-12_Axiz_Mist_Pricing.md`). Pictures and files a note links to sit in the category's
  `Attachments/` folder.
- Every new note is placed straight away. If something fits both sides it goes where it is mainly *used*; below 70 %
  certainty Nova files it on her best guess and asks you on the dashboard (🗂) whether that is right.
- **Existing notes are never moved by themselves.** Nova prepares a plan; you answer her questions and press *Back up
  and move*. A copy of the whole brain and the database goes to `backups/brain-before-reorganise-…` first, and links,
  embeds, attachments, pins, statuses and project links are carried along.
- Every note starts with two label lines, e.g. `[LABEL: DOMAIN: WORK]` and `[LABEL: STATUS: WAITING-ON-USER]`.
  Statuses: `IN-PROGRESS` (Nova is busy with it), `WAITING-ON-USER` (needs your decision, file or answer),
  `COMPLETED` (finished and delivered), `BACKLOG` (planned, not started). It is the same status as *Track this* on
  the dashboard; editing the label in the file works too. Filed documents default to `COMPLETED`.
- `Status_Board.md` in `02_Work/05_Meetings_&_Reports/` and `01_Personal/05_Journal/` lists everything by status,
  what waits on you first.

The file `01_Personal/05_Journal/Nova_Memory.md` is a readable mirror of everything Nova knows. To change a memory,
tell Nova ("that's wrong, Sam moved to Globex"). The old version is kept as history.

## meetings

```yaml
meetings:
  folder: workspace/meetings   # the recordings (notes go to 02_Work/05_Meetings_&_Reports/Meetings/ in the brain)
  stt_model: small.en          # transcription model for recordings (more accurate than live voice)
  create_tasks: true           # add *your* action items to Google Tasks
```

Say *"record this meeting"* / *"stop recording"*, or *"summarise the recording C:\…\call.mp4"*.
Nova records the default microphone **and** whatever the PC plays (WASAPI loopback), so both sides of a
Teams/Zoom/Meet call are captured. Let participants know you're recording.

## dashboard

```yaml
dashboard:
  enabled: true
  port: 8765
  open_on_start: true
  allowed_hosts: []           # extra host names allowed to reach it, e.g. ["my-pc.tail1234.ts.net"] for Tailscale
  theme:                      # set from Settings → Appearance (applies live)
    style: vivid              # vivid (colourful, default) · light (navy panels on a pale page) · dark
    palette: aurora           # vivid look: aurora · ocean · sunset · forest · midnight
    tilt: true                # the Personal and Work panels lean towards each other (3D); false = flat
    rows: 6                   # files shown per folder before "+ n more"
    accent: "#8b7bff"         # light / dark looks only
    accent2: "#4cc9f0"
    background: "#03040a"     # dark look only
```

## skills · plugins · playbooks (switch off)

```yaml
skills:    { disabled: [browser] }      # system, memory and maintenance can't be disabled
plugins:   { disabled: [currency] }
playbooks: { disabled: [weekly-review] }
```

The dashboard listens on `127.0.0.1` only. To reach it from your phone, use a private network such as
[Tailscale](https://tailscale.com) (`tailscale serve 8765`). Don't port-forward it to the internet.

## files

```yaml
files:
  allowed_roots: ["~/Documents", "~/Desktop", "~/Downloads", "~/Pictures", "~/Videos", "./workspace"]
```

Nova refuses any path outside these folders. Deletes go to the Recycle Bin, and overwritten files get a `.bak` copy.

## web · media · system

```yaml
web:
  sites_dir: workspace/sites         # websites Nova builds (preview at http://localhost:8765/sites/<name>/)
  searxng_url: ""                    # e.g. http://localhost:8888 — private search (falls back to DuckDuckGo)
media:
  output_dir: workspace/videos
  captions: true                     # burned-in subtitles
  broll: true                        # Pexels stock footage per scene (needs PEXELS_API_KEY)
  image_gen_enabled: false           # local Stable Diffusion (requirements-imagegen.txt)
  image_model: runwayml/stable-diffusion-v1-5
system:
  allow_shell: true                  # false disables run_shell entirely
  confirm: irreversible              # ask first for: irreversible (delete, send/reply email, invites, shutdown,
                                     # submitting web forms, rollback) | all (every risky tool) | never
                                     # disk-wiping shell commands (format, diskpart, Remove-Item -Recurse…) always ask
```

## MCP servers

See [MCP.md](MCP.md). Summary:

```yaml
mcp_import_claude_desktop: false     # also load servers from Claude Desktop's config
mcp_servers:
  my_server:
    enabled: true
    command: uvx                      # or: url: https://…/mcp   (+ headers)
    args: ["some-mcp-server"]
    env: { API_KEY: "${MY_KEY}" }
    keywords: [words, that, trigger, it]
    confirm: auto                     # auto | always | never | [tool, names]
    only_tools: []                    # optional allow-list
    timeout: 45
nova_mcp_server:
  expose_groups: [brain, files, web, google, media]
```

## routines

Things Nova does on its own at a set time. Each routine runs its `prompt` exactly as if you'd said it,
so it can use playbooks and any tool.

```yaml
routines:
  - name: Morning briefing
    at: "07:30"
    days: [mon, tue, wed, thu, fri]    # default: every day
    prompt: "Give me my morning briefing"
    speak: true                          # say it out loud (voice must be running)
    telegram: true                       # send it to your phone
  - name: Inbox sweep
    at: "12:30"
    prompt: "Check for urgent unread emails from customers and summarise them"
    telegram: true
```

A routine missed by less than 30 minutes (PC asleep, Nova restarting) still runs once.

## Command-line options

```text
run.bat                  voice + Telegram + dashboard (restarts itself after updates)
run.bat --text           type instead of talking
run.bat --no-voice       Telegram + dashboard only
run.bat --google-login   connect Google
run.bat --check          health check
python -m nova.mcp_server   run Nova as an MCP server (stdio)
python -m nova.updater check | update | versions | rollback [target]
```
