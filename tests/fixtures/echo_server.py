"""Tiny MCP server used by the tests."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool()
def shout(text: str) -> str:
    """Return the text in capitals."""
    return text.upper()


@mcp.tool()
def delete_everything(really: bool = False) -> str:
    """A dangerous-sounding tool (should require confirmation)."""
    return "pretend-deleted"


if __name__ == "__main__":
    mcp.run()
