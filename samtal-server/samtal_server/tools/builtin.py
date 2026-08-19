"""The tools the server implements itself.

Three of them, all bare-named because the namespace rules in `names`
reserve those names. `switch_agent` is defined here but executed by the
session, since a successful switch ends the tool loop rather than
producing a result; `remember` and `random_number` are executed here,
the first against the memory store and the second against the operating
system's entropy.

What `remember` writes is injected by `runtime.prompt`, which is where
the whole system prompt is assembled: this module defines and runs the
tools, and how their output reaches the model is the runtime's.
"""

import secrets
from collections.abc import Sequence

from samtal_server.providers import ToolDef
from samtal_server.tools import names
from samtal_server.tools.memory import MemoryStore


def switch_agent_tool(agents: Sequence[str]) -> ToolDef:
    """Move the conversation to another assistant this device is bound
    to. Offered only when there is somewhere to go, so a device bound to
    one agent gets no dead tool.

    The enum carries the device's full bound list, which is also what
    lets the active agent answer "who can I talk to?" without any extra
    mechanism."""
    listed = ", ".join(agents)
    return ToolDef(
        name=names.SWITCH_AGENT,
        description=(
            "Switch this conversation to another assistant, who will then greet the "
            f"user and take over. Available assistants: {listed}. Use this when the "
            "user asks for one of them by name or asks for something another one "
            "handles, and to answer who they can talk to."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "enum": list(agents),
                    "description": "The assistant to hand the conversation to.",
                }
            },
            "required": ["agent"],
        },
    )


def remember_tool() -> ToolDef:
    """Keep one fact about the user across conversations. Offered only
    when a memory directory is configured."""
    return ToolDef(
        name=names.REMEMBER,
        description=(
            "Remember one short fact about the user for future conversations, such as "
            "a preference, a name, or a routine. Store one fact per call, phrased so "
            "it still makes sense on its own weeks from now. Do not use this for "
            "things that are only true right now."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The fact to remember, as one short sentence.",
                }
            },
            "required": ["text"],
        },
    )


async def remember(store: MemoryStore, agent: str, arguments: dict[str, object]) -> str:
    """Execute `remember`, answering the short confirmation the model
    then phrases in its own words."""
    text = arguments.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError('remember needs a "text" argument holding the fact to remember')
    await store.remember(agent, text)
    return f"Remembered: {' '.join(text.split())}"


# What a drawn number may be, at either end and in either direction.
# Wide enough for anything somebody says out loud (a die, a hundred, a
# million) and narrow enough that the answer stays speakable, which is
# the only output this pipeline has. A model asking for a twenty-digit
# range is not asking on a user's behalf.
RANDOM_BOUND = 1_000_000

# An ordinary die, which is what an argument nobody sent means.
DEFAULT_MINIMUM = 1
DEFAULT_MAXIMUM = 6


def random_number_tool() -> ToolDef:
    """Draw one honest random number.

    Offered unconditionally, unlike its two siblings: it needs no
    configuration, reaches nothing outside this process, and there is
    no per-device or per-deployment fact that would make it apply to
    one agent and not another."""
    return ToolDef(
        name=names.RANDOM_NUMBER,
        description=(
            "Draw one genuinely random whole number, between two bounds that are both "
            "included. Use this whenever chance is supposed to decide: rolling a die "
            "(1 to 6), flipping a coin (1 to 2), drawing lots, picking who goes first, "
            "or thinking of a number for someone to guess. Always call it rather than "
            "choosing a number yourself, because a number you choose is not random and "
            f"will not feel fair. Each bound may be between -{RANDOM_BOUND} and "
            f"{RANDOM_BOUND}."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "minimum": {
                    "type": "integer",
                    "minimum": -RANDOM_BOUND,
                    "maximum": RANDOM_BOUND,
                    "description": (
                        f"The lowest number that may come up. Defaults to {DEFAULT_MINIMUM}."
                    ),
                },
                "maximum": {
                    "type": "integer",
                    "minimum": -RANDOM_BOUND,
                    "maximum": RANDOM_BOUND,
                    "description": (
                        "The highest number that may come up. Defaults to "
                        f"{DEFAULT_MAXIMUM}, an ordinary die."
                    ),
                },
            },
        },
    )


def random_number(arguments: dict[str, object]) -> str:
    """Execute `random_number`, answering the number drawn and the range
    it came from, so what the model was told is legible in the turn's
    record as well as to the model.

    The entropy is the operating system's (`secrets`), not a seeded
    generator: a die a user hears about has to be a die, and a
    pseudo-random stream shared with anything else in the process is a
    weaker claim than the stdlib hands out for free.

    Synchronous, and called inline by the dispatch: drawing a number
    reads a few bytes of kernel entropy and returns, so there is
    nothing here to await and nothing that could hold the event loop.
    """
    minimum = _bound("minimum", arguments.get("minimum", DEFAULT_MINIMUM))
    maximum = _bound("maximum", arguments.get("maximum", DEFAULT_MAXIMUM))
    if minimum > maximum:
        raise ValueError('random_number needs "minimum" to be no greater than "maximum"')
    drawn = minimum + secrets.randbelow(maximum - minimum + 1)
    return f"{drawn}, drawn at random between {minimum} and {maximum}"


def _bound(argument: str, value: object) -> int:
    """One end of the range as a whole number inside the hard bounds, or
    the refusal the model reads and calls again from.

    A bool is refused rather than taken as 0 or 1: Python says a bool is
    an int, and a model that sent `true` for a bound meant something
    this tool cannot honour, so answering it with a coin flip would be
    inventing the question.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'random_number needs a whole number for "{argument}"')
    if not -RANDOM_BOUND <= value <= RANDOM_BOUND:
        raise ValueError(
            f'random_number needs "{argument}" to be between '
            f"-{RANDOM_BOUND} and {RANDOM_BOUND}"
        )
    return value
