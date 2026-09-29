import sys
from pathlib import Path

from nova import context
from nova.extensions import load_playbooks, load_plugins, matching_playbooks
from nova.tools import REGISTRY

ROOT = Path(__file__).resolve().parent.parent


def test_playbooks_load_and_match():
    books = load_playbooks(ROOT / "playbooks")
    assert {"morning-briefing", "meeting-prep"} <= {b.name for b in books}
    assert matching_playbooks("good morning, brief me")[0].name == "morning-briefing"


def test_plugins_load(nova):
    assert "currency" in load_plugins(ROOT / "plugins")
    assert "convert_currency" in REGISTRY


def test_mcp_client_roundtrip(nova):
    from nova.mcp_client import MCPManager
    cfg, _ = nova
    cfg["mcp_servers"] = {"echo": {"command": sys.executable,
                                   "args": [str(ROOT / "tests" / "fixtures" / "echo_server.py")],
                                   "keywords": ["echo"]}}
    mgr = MCPManager(cfg)
    status = mgr.start(timeout=60)
    assert status["echo"].startswith("connected"), status
    assert REGISTRY["echo__shout"].run({"text": "hi"}) == "HI"
    assert REGISTRY["echo__shout"].confirm is False
    assert REGISTRY["echo__delete_everything"].confirm is True
    context.mcp = mgr


def test_nova_as_mcp_server(nova):
    from nova.mcp_server import build_server
    server = build_server()
    import asyncio
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"recall", "remember", "write_note"} <= names
    assert "delete_path" not in names          # confirm-only tools are never exposed
