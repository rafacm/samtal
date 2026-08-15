"""A real MCP server for the tests, spoken to over stdio.

Small on purpose, and real on purpose: the tests spawn this as a
subprocess so the client transport under test is the one that ships,
without a network, a key, or anything to mock. Launched with
`sys.executable` so CI needs nothing installed beyond the project's own
dependencies.

Run it by path: `python tests/support/mcp_stdio_server.py`.
"""

import asyncio
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts import base

# What this server says about itself in its initialize result, which is
# one of the two channels an entry may opt into. Imported by the tests
# so the assertions are about these bytes rather than about a copy of
# them.
SHIPPED_INSTRUCTIONS = (
    "Call add for arithmetic rather than working it out, and say the secret word "
    "only when you are asked for it."
)

# An entry may override that through the environment, which is the one
# way a test can make two connections to this server ship two different
# things: a reconnect resolves the entry's `env` again and spawns a new
# child, so a test that wants to watch a capture change changes this
# between them.
SHIPPED_ENV = "SAMTAL_TEST_SHIPPED"

# And one more tool under a name the entry's `env` chooses, which is how
# a test plants a name a real third-party server is free to publish and
# this project would never type into a fixture: a credential somebody
# pasted where a tool name goes. Absent unless the variable is set, so
# every other test sees the fixed tool set above.
SHADOWED_TOOL_ENV = "SAMTAL_TEST_SHADOWED_TOOL"

# One published prompt per shape the client has a rule about: a single
# message, several messages, and a template that cannot be rendered
# without an argument.
HOUSE_STYLE = "Answer in short sentences. Never spell out a number over ten."
FIRST_VOICE = "Introduce yourself before the first tool call of a conversation."
SECOND_VOICE = "Say what a tool answered, not that you called one."

server = FastMCP(
    "samtal-test-tools", instructions=os.environ.get(SHIPPED_ENV, SHIPPED_INSTRUCTIONS)
)


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

_chosen = os.environ.get(SHADOWED_TOOL_ENV)
if _chosen:
    server.add_tool(_namespaced, name=_chosen, description="A name the caller chose.")


@server.prompt()
def house_style() -> str:
    """One message, which is what most published prompts are."""
    return HOUSE_STYLE


@server.prompt()
def two_voices() -> list[base.Message]:
    """Two messages with different roles, so the rendering rule has
    something to be about: the text in order, joined by blank lines, and
    the roles dropped."""
    return [base.UserMessage(FIRST_VOICE), base.AssistantMessage(SECOND_VOICE)]


@server.prompt()
async def slow_guidance() -> str:
    """A prompt that does not answer, so a test can watch the bounds on
    the discovery phase hold while the tools stay up."""
    await asyncio.sleep(600)
    return "never arrives"


@server.prompt()
def about_a_room(room: str) -> str:
    """A template: it declares an argument with no default, so the
    listing marks it required and the client refuses to render it."""
    return f"Talk about the {room}."


if __name__ == "__main__":
    server.run()
