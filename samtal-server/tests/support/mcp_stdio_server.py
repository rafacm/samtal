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


def _dotted() -> str:
    """A name the MCP specification allows and both LLM APIs refuse."""
    return "dotted answer"


def _overlong() -> str:
    """A name that is legal until an entry prefix is added to it."""
    return "long answer"


def _namespaced() -> str:
    """A name carrying the separator that divides an entry from a tool,
    so that under an entry called `home` it publishes as
    `home__inside__secret_word`, which is also what an entry called
    `home__inside` publishes its own `secret_word` as. The answer is
    distinct from that one's, so a test can tell which of the two a call
    actually reached."""
    return "inside answer"


# Registered by hand rather than by decorator, because a function name
# cannot express what a real third-party server is free to publish.
server.add_tool(_dotted, name="weather.today/v2", description="A dotted, slashed name.")
server.add_tool(_overlong, name="b" * 60, description="A name only just short enough.")
server.add_tool(
    _namespaced, name="inside__secret_word", description="A name holding the separator."
)


if __name__ == "__main__":
    server.run()
