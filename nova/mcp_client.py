"""MCP client: plug any Model Context Protocol server into Nova.

Every tool an MCP server offers becomes a Nova tool (named <server>__<tool>), so
Nova can use the whole MCP ecosystem — Home Assistant, GitHub, Obsidian, Windows
desktop control, databases, Blender… — configured in config.yaml under `mcp_servers`,
in the same shape Claude Desktop uses. Existing Claude Desktop servers can be imported.

Supports local servers (stdio: `command` + `args`) and remote ones (`url`, streamable HTTP or SSE).
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import os
import re
import threading
from contextlib import AsyncExitStack
from pathlib import Path

from . import context
from .config import resolve
from .tools import REGISTRY, Tool, register_group

WRITE_WORDS = re.compile(r"(write|delete|remove|create|send|update|move|rename|exec|run|post|push|kill|set_|"
                         r"insert|drop|commit|merge|click|type|press|install|turn_on|turn_off|call_service)", re.I)


def _safe_name(server: str, tool: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", f"{server}__{tool}")[:64]


def _clean_schema(s):
    """Drop JSON-schema keys some model APIs (e.g. Gemini) reject."""
    if isinstance(s, dict):
        return {k: _clean_schema(v) for k, v in s.items() if k not in ("$schema", "additionalProperties")}
    if isinstance(s, list):
        return [_clean_schema(x) for x in s]
    return s


def claude_desktop_servers() -> dict:
    """Read MCP servers already configured in Claude Desktop (Windows/macOS)."""
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json",
        Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
    ]
    for p in candidates:
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8")).get("mcpServers", {}) or {}
            except Exception as e:
                print(f"[mcp] couldn't read {p}: {e}")
    return {}


class MCPManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.loop = asyncio.new_event_loop()
        self.sessions: dict = {}
        self.status: dict[str, str] = {}
        self._stack: AsyncExitStack | None = None
        self._ready = threading.Event()

    # ── lifecycle ─────────────────────────────────────────
    def servers(self) -> dict:
        servers = {}
        if self.cfg.get("mcp_import_claude_desktop"):
            for name, spec in claude_desktop_servers().items():
                servers[name] = {**spec, "enabled": True}
        for name, spec in (self.cfg.get("mcp_servers") or {}).items():
            servers[name] = dict(spec or {})
        return {n: s for n, s in servers.items() if s.get("enabled", True)}

    def start(self, timeout: float = 60) -> dict[str, str]:
        if not self.servers():
            return {}
        for name in self.servers():
            self.status.setdefault(name, "connecting…")
        threading.Thread(target=self._run_loop, daemon=True, name="mcp").start()
        self._ready.wait(timeout)
        return self.status

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._connect_all())
        self._ready.set()
        self.loop.run_forever()

    async def _connect_all(self):
        self._stack = AsyncExitStack()
        for name, spec in self.servers().items():
            try:
                await asyncio.wait_for(self._connect(name, spec), timeout=float(spec.get("timeout", 45)))
            except Exception as e:
                self.status[name] = f"failed: {type(e).__name__}: {e}"
                print(f"[mcp] {name} failed to start: {e}")

    async def _connect(self, name: str, spec: dict):
        from mcp import ClientSession, StdioServerParameters
        if spec.get("url"):
            url = spec["url"]
            headers = {k: os.path.expandvars(str(v)) for k, v in (spec.get("headers") or {}).items()}
            if spec.get("transport") == "sse" or url.rstrip("/").endswith("/sse"):
                from mcp.client.sse import sse_client
                read, write = await self._stack.enter_async_context(sse_client(url, headers=headers))
            else:
                from mcp.client.streamable_http import streamablehttp_client
                read, write, _ = await self._stack.enter_async_context(streamablehttp_client(url, headers=headers))
        else:
            from mcp.client.stdio import stdio_client
            cmd = spec["command"]
            if os.name == "nt" and cmd in ("npx", "npm", "pnpm", "yarn"):
                cmd += ".cmd"
            env = {**os.environ, **{k: os.path.expandvars(str(v)) for k, v in (spec.get("env") or {}).items()}}
            params = StdioServerParameters(command=cmd, args=[os.path.expandvars(str(a)) for a in spec.get("args", [])],
                                           env=env, cwd=spec.get("cwd"))
            read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        tools = (await session.list_tools()).tools
        self.sessions[name] = session
        self._register(name, spec, tools)
        self.status[name] = f"connected ({len(tools)} tools)"
        print(f"[mcp] {name}: {len(tools)} tools")

    # ── tools ─────────────────────────────────────────────
    def _needs_confirm(self, spec: dict, tool) -> bool:
        policy = spec.get("confirm", "auto")
        if isinstance(policy, list):
            return tool.name in policy
        if policy == "always":
            return True
        if policy == "never":
            return False
        ann = getattr(tool, "annotations", None)
        if ann is not None:
            if getattr(ann, "readOnlyHint", None) is True:
                return False
            if getattr(ann, "destructiveHint", None) is True:
                return True
        return bool(WRITE_WORDS.search(tool.name))

    def _register(self, server: str, spec: dict, tools) -> None:
        group = f"mcp_{re.sub(r'[^a-z0-9]+', '_', server.lower())}"
        register_group(group, [server.lower().replace("_", " ")] + [k.lower() for k in spec.get("keywords", [])])
        allow = set(spec.get("only_tools") or [])
        for t in tools:
            if allow and t.name not in allow:
                continue
            params = _clean_schema(dict(t.inputSchema or {}))
            params.setdefault("type", "object")
            params.setdefault("properties", {})
            fn = self._caller(server, t.name)
            nova_name = _safe_name(server, t.name)
            REGISTRY[nova_name] = Tool(nova_name, f"[{server}] {(t.description or t.name).strip()[:900]}",
                                       fn, params, group, self._needs_confirm(spec, t))

    def _caller(self, server: str, tool_name: str):
        def call(**kwargs):
            fut = asyncio.run_coroutine_threadsafe(self.sessions[server].call_tool(tool_name, kwargs), self.loop)
            return self._render(server, fut.result(timeout=180))
        return call

    @staticmethod
    def _render(server: str, result) -> str:
        parts = []
        for c in result.content or []:
            kind = getattr(c, "type", "")
            if kind == "text":
                parts.append(c.text)
            elif kind == "image":
                ext = (c.mimeType or "image/png").split("/")[-1]
                out = resolve("workspace/mcp") / f"{server}_{dt.datetime.now():%Y%m%d_%H%M%S}.{ext}"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(base64.b64decode(c.data))
                context.attach(out)
                parts.append(f"[image saved to {out}]")
            elif kind == "resource":
                res = c.resource
                parts.append(getattr(res, "text", None) or f"[resource {getattr(res, 'uri', '')}]")
            else:
                parts.append(str(c))
        if not parts and getattr(result, "structuredContent", None):
            parts.append(json.dumps(result.structuredContent, ensure_ascii=False))
        text = "\n".join(parts) or "Done."
        return ("ERROR from MCP server: " + text) if getattr(result, "isError", False) else text
