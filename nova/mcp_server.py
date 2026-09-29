"""Run Nova as an MCP server, so Claude Desktop, Claude Code, Cursor or any MCP client can use
Nova's permanent memory, 2nd brain and local skills.

    python -m nova.mcp_server            (stdio — what Claude Desktop launches)

Claude Desktop config (claude_desktop_config.json):
    "mcpServers": {
      "nova": {
        "command": "C:\\\\Users\\\\you\\\\Nova\\\\.venv\\\\Scripts\\\\python.exe",
        "args": ["-m", "nova.mcp_server"],
        "cwd": "C:\\\\Users\\\\you\\\\Nova"
      }
    }

Only tools that don't need a yes/no confirmation are exposed (the client can't answer Nova's
confirmation prompt); choose the groups with `nova_mcp_server.expose_groups` in config.yaml.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def build_server():
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    sys.stdout = sys.stderr      # stdio transport: keep stdout clean for protocol messages
    from mcp.server.fastmcp import FastMCP

    from . import context
    from .config import load_config, resolve
    from .llm import LLM
    from .skills import load_all
    from .speech import Speech
    from .store import Store
    from .tools import REGISTRY

    cfg = load_config()
    context.cfg = cfg
    context.store = Store(resolve(cfg.brain.db_file), cfg.llm.providers.ollama.base_url, cfg.brain.embed_model)
    context.store.vault = resolve(cfg.brain.vault_dir)
    context.llm = LLM(cfg)
    context.speech = Speech(cfg)
    load_all()
    groups = set((cfg.get("nova_mcp_server") or {}).get("expose_groups", ["brain", "files", "web", "google", "media"]))
    server = FastMCP("nova", instructions=(
        f"Nova is {cfg.assistant.owner}'s local AI assistant. Use recall before answering questions about "
        f"{cfg.assistant.owner}'s work, people and projects; use remember to store durable facts."))
    exposed = []
    for t in REGISTRY.values():
        if t.group in groups and not t.confirm:
            server.add_tool(t.func, name=t.name, description=t.description)
            exposed.append(t.name)
    print(f"[nova-mcp] exposing {len(exposed)} tools: {', '.join(exposed)}", file=sys.stderr)
    sys.stdout = sys.__stdout__
    return server


if __name__ == "__main__":
    build_server().run()
