"""One tool namespace, with collisions impossible by construction.

Three sources of tools reach the same list, and the model sees one flat
set of names. Rather than detect collisions when the list is merged and
then have to invent a tie-break, the namespace is structural:

- builtins are bare (`switch_agent`, `remember`, `set_state`,
  `clear_state`, `new_conversation`, `resume_conversation`);
- the device's tools keep the firmware's `self.` prefix, with the dots
  sanitized away (`self_audio_speaker_set_volume`);
- an MCP server's tools carry their configuration entry name and a
  double underscore (`ha__turn_on_light`).

Configuration then forbids an `mcp_servers` entry from being called
`self` or from taking a builtin's name, which is what makes the three
groups disjoint. Routing a call back to its source reads the same
structure, so nothing has to be remembered about where a tool came from.

The one place the structure is not enough on its own is between two MCP
entries: an entry name may contain the separator, so `home` and
`home__inside` can publish the same name, and `owner_of` below settles
which of them a name belongs to.

A leaf module on purpose: the configuration layer validates entry names
against it, so it must not import anything that imports configuration.
"""

import re
from collections.abc import Iterable

# The device's tools, as the firmware names them.
DEVICE_PREFIX = "self"

# What separates an MCP server entry name from the tool's own name.
SERVER_SEPARATOR = "__"

SWITCH_AGENT = "switch_agent"
REMEMBER = "remember"
# The two the conversation's own ledger is written with. Bare like the
# rest, and named here for the same reason: what an entry may not be
# called is decided by this tuple.
SET_STATE = "set_state"
CLEAR_STATE = "clear_state"
# The two the conversation itself is moved with. Named here like the
# other builtins, which is what reserves them against an MCP entry by
# construction: the reservation is the reason this tuple exists, and a
# tool that changes which thread a session is on is exactly the one no
# far side may shadow.
NEW_CONVERSATION = "new_conversation"
RESUME_CONVERSATION = "resume_conversation"
BUILTIN_TOOL_NAMES = (
    SWITCH_AGENT,
    REMEMBER,
    SET_STATE,
    CLEAR_STATE,
    NEW_CONVERSATION,
    RESUME_CONVERSATION,
)

# The tools whose order in a round is their meaning, so a reply may not
# run two of them at once.
#
# Both write one conversation's ledger and both are addressed by a key
# the model chooses, so two calls in one round can name the same entry:
# a set and a clear of `scene`, or two sets of it. What is current after
# that round is whichever ran last, which means the model's order IS the
# answer, and a round that ran them concurrently would leave the
# database's lock arrival deciding it instead.
#
# Nothing else here has the property. A device tool and a server tool
# touch different worlds; `remember` appends rather than replacing, so
# two of them in a round leave both facts whichever order they land in;
# and the three that move a session are resolved by the loop itself, in
# issue order, and never reach a dispatch at all.
ORDERED_TOOL_NAMES = (SET_STATE, CLEAR_STATE)

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


def owner_of(published: str, entries: Iterable[str]) -> str | None:
    """Which of these `mcp_servers` entries a published tool name
    belongs to, or None when none of them does.

    The longest entry that qualifies the name wins, and that is the
    whole of the rule. An entry name may itself contain the separator
    (`home__inside` is a legal one), so a published name can be
    qualified by two configured entries at once, and the more specific
    one is the one whose namespace it is in: `home__inside__turn_on` is
    `home__inside`'s tool `turn_on` before it is `home`'s tool
    `inside__turn_on`. Splitting at the first separator instead would
    hand the name to `home`, which is a different server, and the caller
    would run a tool nobody asked for.

    The answer depends on the configured entries and on nothing else, so
    two callers asking about one name get one answer however the servers
    behind them happen to be doing.
    """
    owner: str | None = None
    for entry in entries:
        prefix = f"{entry}{SERVER_SEPARATOR}"
        if published.startswith(prefix) and len(published) > len(prefix):
            if owner is None or len(entry) > len(owner):
                owner = entry
    return owner
