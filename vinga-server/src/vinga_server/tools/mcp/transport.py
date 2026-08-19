"""One MCP server's connection: bringing it up, and naming what
went wrong when it did not come up.

The two transports the specification gives an entry, a spawned
child over stdio and streamable HTTP, with the policy each of them
is opened under; the values a connection materializes for itself
and never stores; and the vocabulary a failure is answered in,
which is type names and fixed tokens rather than anything a far
side wrote. Nothing here holds state: what a connection becomes is
the manager's, and what this owns is how it is made and how a
failure is classified.
"""

import logging
import os
from collections.abc import Callable
from contextlib import AsyncExitStack

import httpx
import mcp.types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.client.streamable_http import streamable_http_client

from vinga_server.config import McpServerConfig
from vinga_server.config.secrets import SecretStore, resolve_mcp_values
from vinga_server.protocol.mcp import spoken_content

# How long connecting and listing a server's tools may take. The boot
# waits for this once per server, concurrently.
CONNECT_TIMEOUT_S = 10.0


# And why a connection is not there, as the `mcp_down` event says it: a
# closed set of six tokens, one per place the decision is actually made
# (#138). Beside the type names `_reason` answers with rather than
# instead of them, and the difference is who is asking. An operator
# reading one line wants to know that this particular handshake came
# back as a validation error, which is what the sentence carries; a
# collector filtering a month of them wants six buckets, which is what
# the field carries. The two never disagree, because the sentence is
# rendered from `_reason` and the field is chosen where the failure is
# classified, and neither is ever built out of an exception's message.
TRANSPORT_FAILED = "transport_failed"
INITIALIZE_FAILED = "initialize_failed"
DISCOVERY_FAILED = "discovery_failed"
CONNECT_TIMEOUT = "connect_timeout"
CALL_FAILED = "call_failed"
STOPPED = "stopped"


def _emit_nothing(_record: logging.LogRecord) -> bool:
    """A filter that passes no record. See where it is installed."""
    return False


# The SDK's clients log what the far end chose: the session id a server
# picked, the raw body of an initialization result that would not parse,
# and, through logger.exception, a traceback whose validation message
# quotes the bytes that failed. The stdio client does the same for a
# child that writes malformed JSON at it. JSON log events are this
# server's observability surface
# (docs/adr/2026-08-04-json-logs-are-the-observability-surface.md), and
# nothing a third-party server writes was ever part of it, which is the
# reason uvicorn's access log stays off in main.py as well. So those
# records stop at their own logger, before any handler of ours is
# reached. What an operator reads about an MCP server is what this
# module writes: the entry name, the outcome, and tool names that have
# been through the publishing rule.
SDK_LOGGERS = (
    "mcp.client.stdio",
    "mcp.client.streamable_http",
    "mcp.client.session",
    "mcp.shared.session",
)

def quiet_sdk_loggers() -> None:
    """Put the rule above in force, once and out loud.

    Called rather than run when this module is imported (#140), because
    importing a module is not a thing that should change a process's
    logging: a tool that imports this to read a type, or a test that
    never connects to anything, would otherwise have the SDK's loggers
    rearranged under it by the import alone.

    Where it is called is what makes the rule reach every connection,
    so it is called at the one place a connect is ever begun: the
    manager's `_begin`, which creates the task a run lives in. A start
    and a background reconnect both come through there, and neither
    calls the other, so any boundary further out would be one a public
    path walks past.

    Idempotent all the same, since `_begin` runs once per connection: a
    filter already installed is not installed twice, and turning
    propagation off twice is turning it off.
    """
    for _sdk_logger in SDK_LOGGERS:
        logging.getLogger(_sdk_logger).addFilter(_emit_nothing)

    # And the net under the list, because a list of module names is a
    # thing that goes stale: a filter stops the records logged through
    # the logger it sits on, and nothing else, so an SDK module this
    # list does not name would reach the handlers unfiltered. Turning
    # propagation off at the root of the SDK's namespace closes that
    # without naming anything: no record from any `mcp.*` logger reaches
    # a handler of ours. An operator who wants the SDK's own
    # diagnostics can still attach a handler to `mcp` itself, which is a
    # deliberate act rather than the default.
    logging.getLogger("mcp").propagate = False


def _resolve(
    name: str, config: McpServerConfig, secrets: SecretStore | None, group: str
) -> dict[str, str]:
    """This server's env or headers, as the process or the request
    should see them. Called per connection, and the result is never
    stored: the values it holds are the only plaintext secrets the
    server ever materializes."""
    values = config.env if group == "env" else config.headers
    return resolve_mcp_values(name, group, values, secrets)


async def _connect(
    name: str,
    config: McpServerConfig,
    secrets: SecretStore | None,
    stack: AsyncExitStack,
    reached: Callable[[str], None],
) -> tuple[ClientSession, mcp.types.InitializeResult]:
    """The connected session, and what the handshake answered with.

    The initialization result is returned rather than discarded
    because it is where a server describes itself: its `instructions`
    is one of the two channels this entry may opt into, and its
    capabilities are what says whether asking for prompts is a
    question this server answers at all.

    `reached` is `_run`'s phase marker, advanced once here: bringing
    a transport up and speaking the handshake over it are two
    failures an operator does about different things, and this is
    the only place that can tell them apart, since afterwards they
    are the same wrapped exception arriving at the same handler.
    """
    if config.transport == "stdio":
        assert config.command is not None
        parameters = StdioServerParameters(
            command=config.command,
            args=list(config.args),
            # Merged rather than replaced: a spawned server still
            # needs a PATH and a HOME to find its own tools.
            env={**get_default_environment(), **_resolve(name, config, secrets, "env")},
        )
        # The child's stderr goes nowhere. The SDK's default hands it
        # this process's own stderr, which makes a spawned server's
        # every line part of what a deployment collects: a server
        # that logs the credential it was given, or that prints a
        # stack trace holding what it was asked, would be publishing
        # through us. Nothing here reads it, and a child that cannot
        # be diagnosed from its own logs is diagnosed by running it
        # by hand. Opened on the stack so the sink closes with the
        # connection that used it.
        errlog = stack.enter_context(open(os.devnull, "w"))
        read, write = await stack.enter_async_context(
            stdio_client(parameters, errlog=errlog)
        )
    else:
        assert config.url is not None
        # The transport takes a caller-managed httpx client rather
        # than headers, a timeout and a redirect policy of its own,
        # so the HTTP policy is stated here. The values come from
        # the SDK's own create_mcp_http_client, which is what the
        # deprecated wrapper this replaced built its client with, so
        # this stays a client swap rather than a behavior change for
        # deployments already running them: redirects followed for a
        # proxy in front of the server, 30 s for everything but the
        # read, and 300 s for the read, which was the wrapper's
        # sse_read_timeout and is deliberately longer than
        # CONNECT_TIMEOUT_S because a streamable_http server may
        # hold a GET stream open with nothing to say on it. Entered
        # on the stack before the transport, so unwinding closes the
        # transport first and the client after it, in this one task.
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                headers=_resolve(name, config, secrets, "headers") or None,
                follow_redirects=True,
                timeout=httpx.Timeout(30.0, read=300.0),
            )
        )
        read, write, _ = await stack.enter_async_context(
            streamable_http_client(config.url, http_client=client)
        )
    reached(INITIALIZE_FAILED)
    session = await stack.enter_async_context(ClientSession(read, write))
    return session, await session.initialize()


# The entry fields that configure prompt text rather than the
# connection. Excluded from the comparison below, and the exclusion is
# the whole of what makes an instructions edit apply without a restart.
#
# `inject_prompts` is deliberately not one of them: editing it changes
# what a connect fetches from the server, so applying it means fetching
# again, and the honest way to say that is a restarted connection.
_PROMPT_ONLY_FIELDS = ("instructions", "use_server_instructions")


def _connection_identity(config: McpServerConfig) -> McpServerConfig:
    """One entry with the fields the connection never sees removed.

    An operator fixing a typo in the guidance has not changed the server
    this talks to, and dropping a live connection to apply it (with a
    mid-call tool list and, for stdio, a respawned child process) would
    be churn without a cause. So the reload reports such an entry as
    `unchanged`, which is honest about the connection, and the new text
    reaches conversations at their next activation through the slice,
    which a reload swaps whatever it did to the managers.
    """
    return config.model_copy(update=dict.fromkeys(_PROMPT_ONLY_FIELDS))


def _reason(exc: BaseException) -> str:
    """Why a connection did not happen, in words this server owns.

    An exception's message is not one of them: a server that answers the
    handshake with nonsense puts its own bytes in the validation error
    that follows, and the log line an operator reads is no place for
    them. The type names say what kind of failure it was, which is what
    the line was ever used for, and a group is unwrapped because
    "ExceptionGroup" says nothing at all."""
    if isinstance(exc, BaseExceptionGroup):
        return ", ".join(sorted({_reason(sub) for sub in exc.exceptions})) or "ExceptionGroup"
    return type(exc).__name__


def _carries(exc: BaseException, kind: type[BaseException]) -> bool:
    """Whether one failure, or anything inside the group it may be, is
    of this kind.

    Types and nothing else, and a group is walked to the bottom rather
    than read at the top: the transports raise inside anyio task groups,
    and sometimes inside a group holding a group, so the thing that
    actually went wrong is never the object the handler catches.
    Matching on a message instead would put a far side's own words in
    the branch that decides what this server publishes about it.
    """
    if isinstance(exc, BaseExceptionGroup):
        return any(_carries(sub, kind) for sub in exc.exceptions)
    return isinstance(exc, kind)


def _down_reason(exc: BaseException, phase: str) -> str:
    """Which of the closed set one failed connect belongs in.

    The phase marker is the answer, and two failure types override it,
    because both cross a phase boundary and the phase would be the
    wrong half of the story:

    - a bound that expired is `connect_timeout` whichever call was
      outstanding when it did, since what an operator does about it is
      the same thing (look at the box, or raise the bound) and it is
      not the same thing they would do about a server that answered;
    - a transport error is `transport_failed` even when it surfaces
      during the handshake, which is where an unanswered TCP connection
      surfaces: the streamable_http client is entered before it has
      spoken to anything, so a URL nobody answers raises on the first
      request rather than on the way in. Calling that an initialization
      failure would say the far side answered.
    """
    if _carries(exc, TimeoutError):
        return CONNECT_TIMEOUT
    if _carries(exc, httpx.TransportError):
        return TRANSPORT_FAILED
    return phase


def _result_text(result: mcp.types.CallToolResult) -> str:
    """A tool result as speakable text. Content a voice assistant cannot
    use is named rather than dropped, so the model can say what it got
    instead of appearing to ignore it.

    Typed content, which is what the SDK hands back: every item has a
    `type` and a text item's text is a string, so there is nothing here
    to be tolerant of. The sentence a non-text item is named by is the
    device channel's too, and lives with it."""
    return spoken_content(
        (item.type, item.text if isinstance(item, mcp.types.TextContent) else None)
        for item in result.content
    )
