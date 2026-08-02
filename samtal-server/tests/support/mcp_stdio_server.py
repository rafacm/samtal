"""A real MCP server for the tests, spoken to over stdio.

Small on purpose, and real on purpose: the tests spawn this as a
subprocess so the client transport under test is the one that ships,
without a network, a key, or anything to mock. Launched with
`sys.executable` so CI needs nothing installed beyond the project's own
dependencies.

Run it by path: `python tests/support/mcp_stdio_server.py`.
"""

import asyncio

from mcp.server.fastmcp import FastMCP

server = FastMCP("samtal-test-tools")


@server.tool()
def secret_word() -> str:
    """The secret word, which only this tool knows."""
    return "rhubarb"


@server.tool()
def add(first: int, second: int) -> int:
    """Add two whole numbers."""
    return first + second


@server.tool()
async def slow_answer(seconds: float) -> str:
    """Answer after sleeping, to exercise the call timeout."""
    await asyncio.sleep(seconds)
    return f"awake after {seconds} s"


@server.tool()
def always_fails() -> str:
    """Raise, so the failure reaches the model as an error result."""
    raise RuntimeError("this tool is broken on purpose")


if __name__ == "__main__":
    server.run()
