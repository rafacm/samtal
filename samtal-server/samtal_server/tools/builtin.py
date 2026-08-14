"""The tools the server implements itself.

Two of them, both bare-named because the namespace rules in `names`
reserve those names. `switch_agent` is defined here but executed by the
session, since a successful switch ends the tool loop rather than
producing a result; `remember` is executed here, against the memory
store.

What `remember` writes is injected by `runtime.prompt`, which is where
the whole system prompt is assembled: this module defines and runs the
tools, and how their output reaches the model is the runtime's.
"""

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
