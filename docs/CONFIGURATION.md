# Configuration

Nova reads two files from its folder:

| File | Holds | Committed to Git? |
|---|---|---|
| `config.yaml` | Settings (created from `config.example.yaml` by setup) | No, it's personal |
| `.env` | API keys and tokens (created from `.env.example`) | **Never** |

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
  smart: [gemini, groq]     # tried in order for hard requests, or when the primary fails
  providers:
    ollama: { base_url: http://localhost:11434/v1, model: nova-qwen, timeout: 90 }
    gemini: { base_url: https://generativelanguage.googleapis.com/v1beta/openai/, model: gemini-2.5-flash, key_env: GEMINI_API_KEY }
    groq:   { base_url: https://api.groq.com/openai/v1, model: llama-3.3-70b-versatile, key_env: GROQ_API_KEY }
  vision_provider: gemini
  escalate_keywords: ["think hard", "in detail", "write a", "draft", "analyse", "compare", ...]
  max_tool_rounds: 6
```

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
```

## tts

```yaml
tts:
  engine: elevenlabs        # elevenlabs | piper. Piper is also the automatic fallback.
  elevenlabs:
    voice_id: JBFqnCBsd6RMkjVDRZzb   # overridden by ELEVENLABS_VOICE_ID in .env
    model: eleven_flash_v2_5         # fastest; eleven_multilingual_v2 = richer
    stability: 0.45
    similarity_boost: 0.8
    style: 0.15
    speed: 1.0
  piper:
    voice: voices/en_GB-alan-medium.onnx   # any voice from huggingface.co/rhasspy/piper-voices
```

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
```

The file `data/brain/_Nova Memory.md` is a readable mirror of everything Nova knows. To change a memory,
tell Nova ("that's wrong, Sam moved to Globex"). The old version is kept as history.

## dashboard

```yaml
dashboard:
  enabled: true
  port: 8765
  open_on_start: true
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
media:
  output_dir: workspace/videos
  image_gen_enabled: false           # local Stable Diffusion (requirements-imagegen.txt)
  image_model: runwayml/stable-diffusion-v1-5
system:
  allow_shell: true                  # run_shell always asks for confirmation; false disables it entirely
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
