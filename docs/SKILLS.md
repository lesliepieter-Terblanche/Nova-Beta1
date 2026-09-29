# Skills, plugins and playbooks

Nova has four ways to gain abilities, from no-code to full code:

| Way | What you write | Best for | Restart needed |
|---|---|---|---|
| **Playbook** | A Markdown file of plain-English steps | Repeatable procedures built from existing tools (briefings, meeting prep, weekly review) | Yes |
| **Plugin** | One Python file in `plugins/` | A new ability (an API, a calculation, a device) | Yes |
| **MCP server** | A few lines of config | Anything someone already built an MCP server for | Yes |
| **Built-in skill** | A module in `nova/skills/` | Core abilities you want to contribute back | Yes |

The full list of current tools is in [TOOLS.md](TOOLS.md).

---

## How Nova chooses tools

A 3B local model gets confused by 90+ tool definitions, so each request only offers the relevant **groups**:

1. The `system` group is always offered.
2. A group is added when your request contains one of its **keywords** (e.g. "email" → `google`).
3. Matching playbooks add their `tool_groups`.
4. If nothing matched, `brain` and `web` are offered as a sensible default.

So when you add abilities, give them good keywords.

---

## Playbooks (no code)

A playbook is a procedure. When a request contains one of its `triggers`, its steps are added to Nova's
instructions for that request. It's like a Claude "skill", but for Nova.

`playbooks/client-follow-up.md`:

```markdown
---
name: client-follow-up
description: Follow up with a client after a meeting
triggers: [follow up with, send a follow-up, after my meeting with]
tool_groups: [google, brain]
---
1. recall everything about the client and the last meeting.
2. gmail_search for our latest thread with them (last 30 days) and read it.
3. Draft a short follow-up with gmail_draft: thank them, recap the 2–3 agreed actions with owners
   and dates, propose the next step. Warm, professional, under 120 words.
4. Tell me it's saved as a draft and read me the first line.
```

Tips:
- Refer to tools by name (see [TOOLS.md](TOOLS.md)). Small models follow numbered, concrete steps best.
- Keep them under ~3,000 characters.
- Included examples: `morning-briefing`, `meeting-prep`, `research-to-brain`, `weekly-review`.
- Combine with a [routine](CONFIGURATION.md#routines) to run a playbook on a schedule.

---

## Plugins (one Python file)

Any `.py` file in `plugins/` is loaded at start-up. Decorate functions with `@tool`:

```python
# plugins/load_shedding.py
import httpx
from nova.tools import register_group, tool

register_group("power", ["load shedding", "loadshedding", "eskom", "power cut", "outage"])

@tool(group="power")
def load_shedding_status(area: str) -> str:
    """Get the load-shedding schedule for an area.
    Args:
        area: suburb or area name, e.g. Roodepoort
    """
    r = httpx.get("https://example-api/...", params={"area": area}, timeout=15)
    return r.text[:2000]
```

The rules:

- **Docstring = what the model sees.** First paragraph is the tool description; `Args:` lines describe each parameter.
- **Type hints build the schema.** Use `str`, `int`, `float`, `bool` and `list[str]`. Parameters with defaults are optional.
- **Return a string, dict or list.** It's fed back to the model, so keep it compact (it's truncated at 6,000 chars).
- **Risky? Add `confirm=True`.** Nova asks yes/no first (sending, deleting, buying, posting, changing things).
- **Raise exceptions freely.** They're caught and reported to the model as `ERROR …`.
- **Shared helpers:** `from nova import context` gives you
  - `context.cfg` (settings), `context.llm.complete(prompt)` / `context.llm.see(image, question)`
  - `context.store` (memory: `add_memory`, `recall`, `search_notes`)
  - `context.speech.synth_wav(text, path)`
  - `context.attach(path)` sends a file with the reply (Telegram), and `context.record(kind, title, path)`
    shows it in the dashboard
  - `context.push(text, files)` notifies your phone
- **Dependencies:** add them to a `requirements-plugins.txt` and `pip install -r` it.
  Import heavy libraries *inside* the function so Nova still starts if they're missing.

See `plugins/currency.py` for a complete working example.

---

## Built-in skills

In `nova/skills/`, loaded via the `SKILLS` list in `nova/skills/__init__.py`:

| Module | Group(s) | Summary |
|---|---|---|
| `system.py` | system | time, apps, URLs, status, screenshots, vision, clipboard, shell 🔒, power 🔒, reminders |
| `memory.py` | brain | remember, recall, correct, notes, journal, ingest web pages/documents |
| `files.py` | files | list, find, read (PDF/DOCX/XLSX), write, copy, move 🔒, delete 🔒, tidy 🔒, zip, open, send to phone |
| `web.py` | web | search, scrape page/links/tables, build/edit/list/open websites |
| `browser.py` | browser | open, read, click, type, submit 🔒, scroll, back, keys, screenshot, look |
| `google_ws.py` | google | Gmail, Calendar, Drive, Docs, Sheets, Tasks |
| `media.py` | media | explainer videos, slideshows, voice-overs, transcription, trim, convert, local images |
| `camera_ads.py` | camera | webcam photo/look, product ads (+ video) |
| `weather.py` | weather | live forecast, spoken + weather page (Open-Meteo, free) |
| `globe.py` | globe | God's Eye View 3D globe: fly anywhere, styles, planes overhead, earthquakes, ISS, install 🔒/start/stop |
| `gestures.py` | gestures | gesture control on/off/status/help (engine in `nova/gestures.py`, camera in `nova/camera.py`) |
| `maintenance.py` | maintenance | updates 🔒, versions, rollback 🔒, restart 🔒, dashboard, extensions status |

After adding or changing tools, regenerate the reference: `python scripts/gen_tool_docs.py`.

---

## Using Claude to build skills for Nova

Anthropic's [Skill Creator](https://github.com/anthropics/skills) and MCP builder guidance work well for
drafting Nova plugins and playbooks. Give Claude this file, [TOOLS.md](TOOLS.md) and one example plugin,
then describe the ability you want.
