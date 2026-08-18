"""MCP servers configured per agent, on the official Python SDK.

One manager per referenced `mcp_servers` entry, connected once at
startup and shared by every session whose agent names it. Configuration
mistakes fail the boot the way a bad provider does; liveness does not.
A server that is unreachable at startup logs a warning, contributes no
tools, and is reconnected in the background when a session that would
use it opens, so a home automation box rebooting does not require the
conversation server to reboot too.

The set of managers is not fixed for the life of the process: a reload
re-reads the entries and the agents' grants, and stops, starts and keeps
connections accordingly, so an operator who writes an entry does not pay
for it with every live conversation. It is still the only thing that
changes them, and it is asked for rather than noticed.

An entry also carries what its operator wrote about using its tools,
which this layer answers by agent and by grant and knows nothing else
about: the block shape comes from `runtime.prompt`, which is what turns
it into prompt text, so headings and block order are decided there and
never here. Beside it sits what the server itself shipped, through the
two channels the specification gives it: the `instructions` of its
initialize result, and the prompts it publishes. Those are a third
party's words, so each is captured only under a bound and injected only
where the entry opted in, and neither ever reaches a log.

Each manager's whole lifecycle lives in one task: the SDK's clients are
async context managers over anyio task groups, and entering them in one
task while exiting in another is what breaks their cancel scopes. The
task connects, publishes its tools, and then waits until asked to stop.

The responsibilities above are a file each, under this one: `transport`
brings a connection up and names what went wrong, `prompts` captures
what a server ships, `manager` runs one server's lifecycle, `slice`
holds the configuration a registry was built from, `reload` applies a
newly read one, and `registry` answers the questions that need both
halves. This `__init__` IS the module `samtal_server.tools.mcp`: it
builds the one emitter and re-exports what the submodules define, and
holds nothing else.

Two rules follow from that, and hold in every file below it. EVENTS go
through the package emitter, which a submodule takes with
`from . import events` and reaches no other way, so the channel is this
package's name by construction and the retained records' `logger` field
is what it always was. ORDINARY prose records go through the package
LOGGER, which a submodule takes as `logging.getLogger(__package__)` and
never as `__name__`: what an operator reads about an MCP server has
been one logger's worth of lines since this was one file, and the
suites that filter it by that exact name are reading the surface an
operator reads.
"""

import logging

from samtal_server.events import ServerEvents

logger = logging.getLogger(__name__)

# The lifecycle this subsystem records, on the channel above. Five
# events (#138), and what they carry is the entry name the operator
# wrote, a token out of a closed set, and counts and durations this
# server measured: never a name, a message or a byte a far side chose.
events = ServerEvents(__name__)

# And the submodules, imported after the emitter rather than above it,
# which is the whole of what the markers below are for: each of them
# takes `events` from this package, so it has to be here before the
# first of them is read.
from .manager import (  # noqa: E402
    CANCEL_TIMEOUT_S,
    CONNECTED,
    DOWN,
    DROPPED_AFTER_FAILED_CALL,
    STOP_TIMEOUT_S,
    UNUSED,
    McpCallFailed,
    McpConfigError,
    McpServerDown,
    McpServerManager,
    _abandon,
    _forget,
    _managers_for,
    _stopped,
    abandoned,
)
from .prompts import (  # noqa: E402
    DISCOVERY_DEADLINE,
    INSTRUCTIONS_CHANNEL,
    LISTING_CAP,
    NO_PROMPTS_CAPABILITY,
    NON_TEXT_CONTENT,
    NOT_LISTED,
    NOTHING_TO_INJECT,
    PAGE_CAP,
    PROMPT_CALL_TIMEOUT_S,
    PROMPT_CHANNEL,
    PROMPT_DISCOVERY_TIMEOUT_S,
    PROMPT_LISTING_CAP,
    PROMPT_MESSAGE_CAP,
    PROMPT_PAGE_CAP,
    REDACTED,
    REDACTION_FLOOR,
    REQUIRES_ARGUMENTS,
    SHIPPED_BLOCK_LIMIT,
    TOO_LONG,
    TOO_MANY_MESSAGES,
    _discovered,
    _injectable,
    _redactor,
    _rendered,
)
from .registry import McpServers, McpToolNotGranted, _instant  # noqa: E402
from .reload import (  # noqa: E402
    APPLIED,
    REFUSED,
    REFUSED_BUSY,
    REFUSED_IN_PROGRESS,
    REFUSED_INVALID,
    REFUSED_UNEXPECTED,
    REFUSED_UNREADABLE,
    RELOAD_IN_PROGRESS,
    RELOAD_REFUSED,
    RELOAD_UNREADABLE,
    McpReload,
    _refusal,
)
from .slice import (  # noqa: E402
    McpSlice,
    _allowed,
    _nothing_shipped,
    _shadowed,
)
from .transport import (  # noqa: E402
    CALL_FAILED,
    CONNECT_TIMEOUT,
    CONNECT_TIMEOUT_S,
    DISCOVERY_FAILED,
    INITIALIZE_FAILED,
    SDK_LOGGERS,
    STOPPED,
    TRANSPORT_FAILED,
    _carries,
    _connect,
    _connection_identity,
    _down_reason,
    _reason,
    _resolve,
    _result_text,
    quiet_sdk_loggers,
)

# What this subsystem answers to, gathered so that importing a name from
# `samtal_server.tools.mcp` means what it meant when this was one file.
#
# The underscored names are here for the same reason and not as an
# invitation: the suites read some of them off this module, and the
# smallest public seam for each is milestone 4's to draw. A re-export
# copies a binding, so it serves an IMPORT and not a rebinding: a test
# that monkeypatches a relocated constant patches the submodule that
# owns it, which is what the port table records.
__all__ = [
    "APPLIED",
    "CALL_FAILED",
    "CANCEL_TIMEOUT_S",
    "CONNECTED",
    "CONNECT_TIMEOUT",
    "CONNECT_TIMEOUT_S",
    "DISCOVERY_DEADLINE",
    "DISCOVERY_FAILED",
    "DOWN",
    "DROPPED_AFTER_FAILED_CALL",
    "INITIALIZE_FAILED",
    "INSTRUCTIONS_CHANNEL",
    "LISTING_CAP",
    "McpCallFailed",
    "McpConfigError",
    "McpReload",
    "McpServerDown",
    "McpServerManager",
    "McpServers",
    "McpSlice",
    "McpToolNotGranted",
    "NON_TEXT_CONTENT",
    "NOTHING_TO_INJECT",
    "NOT_LISTED",
    "NO_PROMPTS_CAPABILITY",
    "PAGE_CAP",
    "PROMPT_CALL_TIMEOUT_S",
    "PROMPT_CHANNEL",
    "PROMPT_DISCOVERY_TIMEOUT_S",
    "PROMPT_LISTING_CAP",
    "PROMPT_MESSAGE_CAP",
    "PROMPT_PAGE_CAP",
    "REDACTED",
    "REDACTION_FLOOR",
    "REFUSED",
    "REFUSED_BUSY",
    "REFUSED_INVALID",
    "REFUSED_IN_PROGRESS",
    "REFUSED_UNEXPECTED",
    "REFUSED_UNREADABLE",
    "RELOAD_IN_PROGRESS",
    "RELOAD_REFUSED",
    "RELOAD_UNREADABLE",
    "REQUIRES_ARGUMENTS",
    "SDK_LOGGERS",
    "SHIPPED_BLOCK_LIMIT",
    "STOPPED",
    "STOP_TIMEOUT_S",
    "TOO_LONG",
    "TOO_MANY_MESSAGES",
    "TRANSPORT_FAILED",
    "UNUSED",
    "_abandon",
    "_allowed",
    "_carries",
    "_connect",
    "_connection_identity",
    "_discovered",
    "_down_reason",
    "_forget",
    "_injectable",
    "_instant",
    "_managers_for",
    "_nothing_shipped",
    "_reason",
    "_redactor",
    "_refusal",
    "_rendered",
    "_resolve",
    "_result_text",
    "_shadowed",
    "_stopped",
    "abandoned",
    "events",
    "logger",
    "quiet_sdk_loggers",
]
