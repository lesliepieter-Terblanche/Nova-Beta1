# MCP servers

[Model Context Protocol](https://modelcontextprotocol.io) (MCP) is an open standard for connecting AI apps
to tools. Thousands of MCP servers exist, many free and local. Nova works with MCP in **both directions**:

- **Nova as a client:** plug any MCP server into Nova. Its tools become Nova tools you can use by voice.
- **Nova as a server:** Claude Desktop, Claude Code, Cursor and others can use Nova's memory and skills.

---

## Connecting a server

Add it under `mcp_servers` in `config.yaml` and restart. The format matches Claude Desktop's:

```yaml
mcp_servers:
  windows:
    enabled: true
    command: uvx                 # local server started as a process (stdio)
    args: ["windows-mcp"]
    keywords: [window, app, click on, type into]
    confirm: auto
```

Remote or HTTP servers use `url` (streamable HTTP; URLs ending in `/sse`, or `transport: sse`, use SSE):

```yaml
  home_assistant:
    enabled: true
    url: http://homeassistant.local:8123/api/mcp
    headers: { Authorization: "Bearer ${HA_TOKEN}" }
    keywords: [light, geyser, gate, aircon]
    confirm: never
```

| Field | Meaning |
|---|---|
| `command`, `args`, `env`, `cwd` | Start a local server. `${VAR}` expands from `.env`. On Windows `npx` becomes `npx.cmd` automatically. |
| `url`, `headers`, `transport` | Connect to an HTTP server. |
| `keywords` | Words in your request that make Nova offer this server's tools to the model. The server name always counts. |
| `confirm` | `auto` (default): ask before tools marked destructive or named like write/delete/send/create/run…; tools marked read-only never ask. Or `always`, `never`, or a list of tool names. |
| `only_tools` | Expose only these tools. Keeps small local models focused. |
| `timeout` | Seconds to wait for the server to start (default 45). |

Tools appear as `<server>__<tool>`, e.g. `windows__Click-Tool`. Check what's connected:
*"Nova, which extensions are loaded?"* or `run.bat --check`.

**Already use Claude Desktop?** Set `mcp_import_claude_desktop: true` and Nova loads the servers from
`%APPDATA%\Claude\claude_desktop_config.json` too.

### Prerequisites

| Server type | Needs |
|---|---|
| `uvx …` (Python servers) | Nothing extra. `uv` is installed with Nova. |
| `npx …` (Node servers) | [Node.js LTS](https://nodejs.org): `winget install OpenJS.NodeJS.LTS` |
| `docker …` | [Docker Desktop](https://www.docker.com/products/docker-desktop/) |

---

## Recommended servers (free, local-first)

The ones marked ⚙ have ready-made, disabled entries in `config.example.yaml`. The ones marked ➕ can be added with
one click in **Settings → Extensions → Add an MCP server** (then restart Nova).

| Server | What Nova gains | Runs | Setup |
|---|---|---|---|
| ⚙➕ [Excel MCP](https://github.com/haris-musa/excel-mcp-server) | Read/write .xlsx: sheets, formulas, formatting, charts, pivots | Local | `uvx excel-mcp-server stdio` |
| ⚙➕ [ElevenLabs MCP](https://github.com/elevenlabs/elevenlabs-mcp) | Sound effects, voice design, voice cloning, transcription | Cloud (your key/credits) | `uvx elevenlabs-mcp`, `ELEVENLABS_API_KEY` |
| ⚙➕ [YouTube transcript](https://github.com/jkawamoto/mcp-youtube-transcript) | "Summarise this video" — any YouTube transcript | Local | `uvx mcp-youtube-transcript` |
| ⚙➕ [Windows-MCP](https://github.com/CursorTouch/Windows-MCP) | Control any Windows app: read UI, click, type, switch windows | Local | `uvx windows-mcp` |
| ⚙ [Home Assistant MCP Server](https://www.home-assistant.io/integrations/mcp_server/) | Lights, plugs, geyser, gate, alarm, sensors, by voice | Local (your HA) | Enable the integration in HA, create a long-lived token → `HA_TOKEN` |
| ⚙ [GitHub MCP Server](https://github.com/github/github-mcp-server) | Issues, PRs, repos, code search | Local (Docker) | GitHub token → `GITHUB_TOKEN` |
| ⚙ [Git](https://github.com/modelcontextprotocol/servers/tree/main/src/git) | Log, diff, status of a local repo | Local | `uvx mcp-server-git --repository <path>` |
| ⚙ [Fetch](https://github.com/modelcontextprotocol/servers/tree/main/src/fetch) | Any URL → clean Markdown | Local | `uvx mcp-server-fetch` |
| ⚙ [Blender MCP](https://github.com/ahujasid/blender-mcp) | Build and render 3D scenes by voice | Local | Blender + its add-on, `uvx blender-mcp` |
| ⚙ [Google Workspace MCP](https://github.com/taylorwilsdon/google_workspace_mcp) | Extra Google apps: Slides, Forms, Chat… | Local | Reuse your Google OAuth client |
| [Playwright MCP](https://github.com/microsoft/playwright-mcp) | Alternative browser automation | Local | `npx @playwright/mcp@latest` |
| [Filesystem](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem) | Sandboxed file access to extra folders | Local | `npx -y @modelcontextprotocol/server-filesystem <folders>` |
| [Obsidian](https://github.com/MarkusPfundstein/mcp-obsidian) | Search and edit an existing Obsidian vault | Local | Obsidian "Local REST API" plugin |
| [n8n](https://n8n.io) (MCP Server Trigger) | Trigger your own self-hosted automations | Local (self-hosted) | Add an MCP Server Trigger node, use its URL |

Browse more: [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) · [mcp.so](https://mcp.so) · [smithery.ai](https://smithery.ai) · [PulseMCP](https://www.pulsemcp.com).

> **Security:** an MCP server runs with your user's permissions. Install only servers you trust, prefer
> official or well-known repos, keep `confirm: auto` for anything that can change things, and use `only_tools`
> to limit exposure.

---

## Nova as an MCP server

Let Claude Desktop (or Claude Code, Cursor, any MCP client) search and add to Nova's permanent memory, notes,
files, web and Google tools.

Add to Claude Desktop's `claude_desktop_config.json` (*Settings → Developer → Edit config*):

```json
{
  "mcpServers": {
    "nova": {
      "command": "C:\\Users\\you\\Nova\\.venv\\Scripts\\python.exe",
      "args": ["-m", "nova.mcp_server"],
      "cwd": "C:\\Users\\you\\Nova"
    }
  }
}
```

Claude Code:

```bat
claude mcp add nova -- C:\Users\you\Nova\.venv\Scripts\python.exe -m nova.mcp_server
```

- Choose what's exposed with `nova_mcp_server.expose_groups` in `config.yaml`.
- Tools that need Nova's yes/no confirmation are **never** exposed. The client's own approval prompts apply.
- It shares the same memory database as the running Nova, so both see the same brain.
- Ollama should be running for semantic search; otherwise keyword search is used.
