"""One tool namespace, with collisions impossible by construction.

Three sources of tools reach the same list, and the model sees one flat
set of names. Rather than detect collisions when the list is merged and
then have to invent a tie-break, the namespace is structural:

- builtins are bare (`switch_agent`, `remember`);
- the device's tools keep the firmware's `self.` prefix, with the dots
  sanitized away (`self_audio_speaker_set_volume`);
- an MCP server's tools carry their configuration entry name and a
  double underscore (`ha__turn_on_light`).

Configuration then forbids an `mcp_servers` entry from being called
`self` or from taking a builtin's name, which is what makes the three
groups disjoint. Routing a call back to its source reads the same
structure, so nothing has to be remembered about where a tool came from.

A leaf module on purpose: the configuration layer validates entry names
against it, so it must not import anything that imports configuration.
"""

import re

# The device's tools, as the firmware names them.
DEVICE_PREFIX = "self"

# What separates an MCP server entry name from the tool's own name.
SERVER_SEPARATOR = "__"

SWITCH_AGENT = "switch_agent"
REMEMBER = "remember"
BUILTIN_TOOL_NAMES = (SWITCH_AGENT, REMEMBER)

# Names an mcp_servers entry may not take, because they already mean
# something in the merged list.
RESERVED_ENTRY_NAMES = (DEVICE_PREFIX, *BUILTIN_TOOL_NAMES)

# What both LLM APIs accept as a tool name, and how long.
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_TOOL_NAME_LENGTH = 64

_ILLEGAL = re.compile(r"[^A-Za-z0-9_-]")


def sanitize(name: str) -> str:
    """A device tool name the LLM APIs will accept. Device names are
    dotted (`self.audio_speaker.set_volume`) and both APIs restrict tool
    names to `[A-Za-z0-9_-]`, so every other character becomes an
    underscore; the caller keeps a reverse map to call the tool by its
    real name."""
    return _ILLEGAL.sub("_", name)


def is_valid_entry_name(name: str) -> bool:
    """Whether an `mcp_servers` entry name can serve as a tool prefix."""
    return bool(TOOL_NAME_PATTERN.match(name)) and name not in RESERVED_ENTRY_NAMES


def qualified(entry: str, tool: str) -> str:
    """An MCP server's tool under the name the model sees."""
    return f"{entry}{SERVER_SEPARATOR}{tool}"


def unqualified(entry: str, published: str) -> str:
    """The tool's own half of a published name, which is the half a
    per-tool grant names it by (`turn_on_light` for
    `home__turn_on_light`).

    Taken by stripping the entry it was qualified with rather than by
    splitting on the separator: an entry name may legally contain one
    itself, and splitting would then answer with part of the entry."""
    return published.removeprefix(f"{entry}{SERVER_SEPARATOR}")


def split_qualified(name: str) -> tuple[str, str] | None:
    """The entry and tool behind a qualified name, or None when the name
    is not one."""
    entry, separator, tool = name.partition(SERVER_SEPARATOR)
    if not separator or not entry or not tool:
        return None
    return entry, tool
