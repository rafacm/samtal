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

Each manager's whole lifecycle lives in one task: the SDK's clients are
async context managers over anyio task groups, and entering them in one
task while exiting in another is what breaks their cancel scopes. The
task connects, publishes its tools, and then waits until asked to stop.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import mcp.types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.client.streamable_http import streamable_http_client

from samtal_server.config import Config, McpGrant, McpServerConfig
from samtal_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    ReloadInProgressError,
    StorageError,
)
from samtal_server.config.secrets import SecretStore, resolve_mcp_values
from samtal_server.providers import ToolDef
from samtal_server.tools import names
from samtal_server.tools.publish import PublishedTools, publish

logger = logging.getLogger(__name__)

# How long connecting and listing a server's tools may take. The boot
# waits for this once per server, concurrently.
CONNECT_TIMEOUT_S = 10.0

# How long a manager gets to close its connection when a reload is
# taking it away, before its task is cancelled instead. Short, because
# nothing is waiting on the far side's manners: a stdio child that will
# not read its own stdin closing, or an HTTP server that will not answer
# the session delete, must not hold up the request that asked for the
# reload. Cancellation is the backstop rather than the first move
# because a task unwinds its own exit stack, which is the only place it
# may be unwound.
STOP_TIMEOUT_S = 5.0

# And how long the cancellation itself gets. Cancelling is a request as
# much as the stop event was: a cleanup handler may suppress its
# cancellation or await something slow on the way out, and either would
# put the far side back in charge of how long the reload takes. So the
# whole stop is bounded at STOP_TIMEOUT_S plus this, after which the
# task is left to finish in the background.
CANCEL_TIMEOUT_S = 2.0

# What a configured entry is doing, in the vocabulary the status surface
# answers with. `unused` is not a manager state: no manager exists for
# an entry no agent references, which is a likely answer to "why does
# the agent not have that tool" and is otherwise invisible.
CONNECTED = "connected"
DOWN = "down"
UNUSED = "unused"

# Why a connection is gone when nothing raised on the way in: a tool
# call failed on it and the manager dropped it so the next session
# revives it. A fixed token this application owns, like the type names
# `_reason` answers with and for the same reason, since here there is no
# exception left to name.
DROPPED_AFTER_FAILED_CALL = "DroppedAfterFailedCall"


def _emit_nothing(_record: logging.LogRecord) -> bool:
    """A filter that passes no record. See where it is installed."""
    return False


# The SDK's HTTP client logs what the far end chose: the session id a
# server picked, the raw body of an initialization result that would not
# parse, and, through logger.exception, a traceback whose validation
# message quotes the bytes that failed. JSON log events are this
# server's observability surface and its transcript store
# (docs/adr/2026-08-04-json-logs-are-the-observability-surface.md), and
# nothing a third-party server writes was ever part of it, which is the
# reason uvicorn's access log stays off in main.py as well. So those
# records stop at their own logger, before any handler of ours is
# reached. What an operator reads about an MCP server is what this
# module writes: the entry name, the outcome, and tool names that have
# been through the publishing rule.
logging.getLogger("mcp.client.streamable_http").addFilter(_emit_nothing)


class McpConfigError(ValueError):
    """An MCP server that cannot be built as configured. Raised at boot,
    like a bad provider, because at call time it would fail every
    conversation that reaches it."""


class McpServerDown(RuntimeError):
    """A call to a server that is not currently connected."""


class McpToolNotGranted(LookupError):
    """A call to a tool the speaking agent's grants do not name.

    Not an unreachable state: the snapshot the model was given already
    left the tool out, so this is what remains if a model calls a name
    it was not offered. It travels to the session as the error result an
    unknown tool produces, which the agent phrases in its own words."""


class McpServerManager:
    """One configured MCP server: its connection, its tools, and its
    reconnection."""

    def __init__(
        self,
        name: str,
        config: McpServerConfig,
        secrets: SecretStore | None = None,
        expected: frozenset[str] = frozenset(),
    ) -> None:
        self._name = name
        self._config = config
        self._secrets = secrets
        # What some agent's allow list names of this entry, which is
        # what this server's published tools are checked against. Held
        # here rather than looked up, because the check happens whenever
        # this connects and a background reconnect has nobody to ask.
        self._expected = expected
        # Resolved once here and thrown away. Resolving at construction
        # is what makes an unset $VAR or a token that will not decrypt
        # fail the boot rather than the first conversation that needs
        # the server. Keeping the result is what must not happen: a
        # manager lives as long as the process, so a decrypted
        # credential kept on it is a plaintext secret held for the
        # lifetime of the server, in a long-lived object, for the sake
        # of a value that is needed for the length of one connection.
        # _connect resolves again, where the value goes straight into
        # the child process or the request headers.
        self._resolve("env")
        self._resolve("headers")
        # What the reload's diff compares this manager's stored
        # credentials by. Taken here because this is where the store is
        # in hand, and opaque by construction: it says whether two loads
        # hold the same secrets for this entry and carries nothing of
        # them. A deployment with no store at all is an empty one, so
        # "no secrets" and "a store holding none for this entry" are the
        # same world rather than two.
        self._secrets_mark = (secrets if secrets is not None else SecretStore()).fingerprint(
            "mcp_server", name
        )
        self._session: ClientSession | None = None
        self._published = PublishedTools(tools=[], originals={})
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._settled = asyncio.Event()
        # What the status surface reports, written where `_run` and
        # `_mark_down` already decide these things. A manager that has
        # not connected yet is down with no reason: the first attempt is
        # what supplies one.
        self._state = DOWN
        self._reason: str | None = None
        self._since = time.time()

    @property
    def name(self) -> str:
        return self._name

    @property
    def up(self) -> bool:
        return self._session is not None

    @property
    def state(self) -> str:
        """`connected` or `down`, as the status surface says it."""
        return self._state

    @property
    def reason(self) -> str | None:
        """Why this server is down, as a token this application owns, or
        None when nothing has failed. Never a far side's own words."""
        return self._reason

    @property
    def since(self) -> float:
        """When the current state and reason were recorded."""
        return self._since

    @property
    def tool_timeout_s(self) -> float:
        return self._config.tool_timeout_s

    def tools(self) -> list[ToolDef]:
        """This server's tools, under the names the model sees. Empty
        while the server is down, which is how an agent configured with
        an unreachable server still holds a conversation."""
        return list(self._published.tools)

    async def start(self) -> None:
        """Connect and list the tools, or log why not. Never raises: a
        dead server is not a boot failure."""
        self._begin()
        await self._settled.wait()

    def expect(self, allowed: frozenset[str]) -> None:
        """The tool names the agents' grants name of this entry now.

        Set by a reload as well as at construction, including on a
        manager it left connected: an operator who adds a grant to a
        server that is already up has published nothing new, and the
        mismatch would otherwise go unmentioned until the next connect.
        """
        self._expected = allowed
        if self.up:
            self._warn_about_unpublished()

    def _warn_about_unpublished(self) -> None:
        """Say which allowed tools this server did not publish.

        An allow list cannot be checked when it is written: only a live
        connection knows what a server offers. It is checked against the
        published mapping and never against the raw listing, because a
        tool this server listed and publication dropped (an unusable
        name, a collision, too long once prefixed) is exactly as
        unreachable as one it never listed, and comparing against the
        listing would stay quiet about it.

        Only names the operator wrote are printed. What the server chose
        to call things is its own bytes, and this line is not where they
        start crossing.
        """
        published = {names.unqualified(self._name, tool.name) for tool in self._published.tools}
        missing = sorted(self._expected - published)
        if missing:
            logger.warning(
                "mcp server %s: %s allowed by a grant but not published by this server, "
                "so no agent can reach %s",
                self._name,
                ", ".join(missing),
                "it" if len(missing) == 1 else "them",
            )

    def same_as(self, other: "McpServerManager") -> bool:
        """Whether these two were built from the same world: the same
        entry fragment, and the same stored secrets behind it.

        What the reload's diff asks, and the whole of what decides that
        an entry keeps its live connection. Both halves matter: an
        operator who rotates a credential has changed the server this
        connects to as surely as one who edits its URL, and comparing
        only the fragment would leave the old token in a process nobody
        restarted.
        """
        return self._config == other._config and self._secrets_mark == other._secrets_mark

    async def stop(self, timeout: float | None = None) -> None:
        """Ask this server's task to end, and see it out.

        `timeout` bounds the waiting, for a caller (the reload) that has
        a request open and cannot wait on a far side's manners. What
        happens at the bound is a cancellation of the manager's own
        task, never anything done to a transport from here: a client of
        the SDK is an async context manager over an anyio task group,
        and unwinding one from another task is what breaks its cancel
        scopes.
        """
        self._stop.set()
        task = self._task
        if task is None:
            return
        self._task = None
        if timeout is None:
            await task
            return
        done, _ = await asyncio.wait([task], timeout=timeout)
        if not done:
            logger.warning(
                "mcp server %s did not close within %.0f s; cancelling it",
                self._name,
                timeout,
            )
            task.cancel()
            # Cancelling is a request too. A cleanup handler that
            # suppresses its cancellation, or one that awaits something
            # slow on the way out, would otherwise take the caller's
            # bound with it: the endpoint that asked for this reload is
            # holding a request open, and a far side's manners are not
            # what its client's timeout should be spent on.
            done, _ = await asyncio.wait([task], timeout=CANCEL_TIMEOUT_S)
            if not done:
                logger.warning(
                    "mcp server %s has not finished unwinding %.0f s after being "
                    "cancelled; leaving it to finish in the background",
                    self._name,
                    CANCEL_TIMEOUT_S,
                )
                _abandon(task)
                return
        # A cancelled task raises here, and a task whose own unwind
        # failed raises whatever that was; neither is the caller's to
        # meet, since this connection is being taken away either way.
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await task

    def ensure_reconnecting(self) -> None:
        """Start a background reconnect if this server is down and no
        attempt is already running. Called when a session opens, so a
        server that came back is picked up without a restart."""
        if self.up or (self._task is not None and not self._task.done()):
            return
        logger.info("mcp server %s: reconnecting in the background", self._name)
        self._begin()

    def _begin(self) -> None:
        self._settled = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name=f"mcp-{self._name}")

    def _became(self, state: str, reason: str | None) -> None:
        """Record what this server is doing and when it started doing
        it.

        The reason counts as part of the condition rather than as a note
        beside it: a server that goes on being down for a new reason has
        failed again, and an instant that stayed put would date that
        failure to the previous one.
        """
        if (state, reason) == (self._state, self._reason):
            return
        self._state = state
        self._reason = reason
        self._since = time.time()

    async def _run(self) -> None:
        self._stop.clear()
        try:
            async with AsyncExitStack() as stack:
                async with asyncio.timeout(CONNECT_TIMEOUT_S):
                    session = await self._connect(stack)
                    listed = await session.list_tools()
                # A third-party server's names are no more trustworthy
                # than a device's: they publish through the same rule,
                # or one badly named tool fails every later request.
                self._published = publish(
                    (
                        (tool.name, tool.description or "", tool.inputSchema)
                        for tool in listed.tools
                    ),
                    prefix=self._name,
                    label=f"mcp server {self._name}",
                )
                self._session = session
                self._became(CONNECTED, None)
                logger.info(
                    "mcp server %s connected with %d tool(s): %s",
                    self._name,
                    len(self._published.tools),
                    ", ".join(tool.name for tool in self._published.tools) or "none",
                )
                self._warn_about_unpublished()
                self._settled.set()
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._became(DOWN, _reason(exc))
            logger.warning(
                "mcp server %s is unavailable, its tools are absent: %s",
                self._name,
                self._reason,
            )
        finally:
            self._session = None
            self._published = PublishedTools(tools=[], originals={})
            # A stop with nothing wrong carries no reason. A failure
            # recorded its own just above, and a connection dropped
            # after a failed call recorded the fixed token before it
            # asked this task to end, so neither is overwritten here.
            if self._state == CONNECTED:
                self._became(DOWN, None)
            self._settled.set()

    def _resolve(self, group: str) -> dict[str, str]:
        """This server's env or headers, as the process or the request
        should see them. Called per connection, and the result is never
        stored: the values it holds are the only plaintext secrets the
        server ever materializes."""
        values = self._config.env if group == "env" else self._config.headers
        return resolve_mcp_values(self._name, group, values, self._secrets)

    async def _connect(self, stack: AsyncExitStack) -> ClientSession:
        if self._config.transport == "stdio":
            assert self._config.command is not None
            parameters = StdioServerParameters(
                command=self._config.command,
                args=list(self._config.args),
                # Merged rather than replaced: a spawned server still
                # needs a PATH and a HOME to find its own tools.
                env={**get_default_environment(), **self._resolve("env")},
            )
            read, write = await stack.enter_async_context(stdio_client(parameters))
        else:
            assert self._config.url is not None
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
                    headers=self._resolve("headers") or None,
                    follow_redirects=True,
                    timeout=httpx.Timeout(30.0, read=300.0),
                )
            )
            read, write, _ = await stack.enter_async_context(
                streamable_http_client(self._config.url, http_client=client)
            )
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    async def call(self, published: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Run one of this server's tools, named as the model was given
        it. The name goes back out as the server listed it, since what
        the model saw may have been sanitized. A transport failure marks
        the server down (so the next session revives it) and is raised
        for the session to turn into an error result."""
        session = self._session
        if session is None:
            raise McpServerDown(f'MCP server "{self._name}" is not connected')
        original = self._published.original_for(published)
        if original is None:
            raise KeyError(f'MCP server "{self._name}" has no tool called "{published}"')
        try:
            result = await session.call_tool(original, arguments)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._mark_down()
            raise
        return _result_text(result), bool(result.isError)

    def _mark_down(self) -> None:
        """Unwind the connection so the next session reconnects it."""
        logger.warning("mcp server %s: dropping the connection after a failed call", self._name)
        self._became(DOWN, DROPPED_AFTER_FAILED_CALL)
        self._session = None
        self._published = PublishedTools(tools=[], originals={})
        self._stop.set()


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


def _instant(when: float) -> str:
    """One of the instants the status view carries, as a person reads
    it. UTC and ISO-8601, the shape the pending listing answers with,
    because a status read compared against a server's log is compared
    against that server's clock."""
    return datetime.fromtimestamp(when, UTC).isoformat()


def _result_text(result: mcp.types.CallToolResult) -> str:
    """A tool result as speakable text. Content a voice assistant cannot
    use is named rather than dropped, so the model can say what it got
    instead of appearing to ignore it."""
    parts: list[str] = []
    for item in result.content:
        if isinstance(item, mcp.types.TextContent):
            parts.append(item.text)
        else:
            parts.append(f"[unsupported {item.type} content]")
    return "\n".join(parts)


def _managers_for(
    config: Config, secrets: SecretStore | None, configured: "McpSlice"
) -> dict[str, McpServerManager]:
    """One manager per entry some agent references, built and not
    started.

    Everything that can refuse a configuration happens here: the egress
    declaration `server.local_only` requires, the `$VAR` references an
    entry's env and headers name, and the stored credentials behind
    them. At boot that makes a bad entry a boot failure; on a reload it
    makes one a refusal that has touched nothing, which is the same
    property from the other side.
    """
    managers: dict[str, McpServerManager] = {}
    for name in sorted(config.referenced_mcp_servers()):
        entry = config.mcp_servers[name]
        if config.server.local_only:
            _check_egress(name, entry)
        try:
            managers[name] = McpServerManager(
                name, entry, secrets, configured.allowed_names(name)
            )
        except ValueError as exc:
            raise McpConfigError(f"mcp_servers.{name}: {exc}") from exc
    return managers


# The tasks that would not finish unwinding inside their bound. Held
# here because the event loop keeps only a weak reference to a task, so
# a task nobody is awaiting can be collected mid-unwind, and because a
# task that ends in an exception nobody retrieved prints one at
# interpreter shutdown. Both are answered by keeping it until it is
# done and consuming whatever it ended with.
_abandoned: set[asyncio.Task[None]] = set()


def _abandon(task: asyncio.Task[None]) -> None:
    """Stop waiting for a manager's task, and let it finish on its own.

    Not a violation of the rule the module docstring states. That rule
    is about entering a transport in one task and exiting it in
    another; this task is still the only thing unwinding its own exit
    stack, and nothing here touches it again. What is given up is the
    waiting, which belongs to a caller holding a request open.
    """
    _abandoned.add(task)
    task.add_done_callback(_forget)


def _forget(task: asyncio.Task[None]) -> None:
    _abandoned.discard(task)
    # Retrieved and dropped: a cancelled task raises here, and one whose
    # unwind failed raises whatever that was, and neither has anyone
    # left to tell.
    with contextlib.suppress(Exception, asyncio.CancelledError):
        task.exception()


async def _stopped(manager: McpServerManager) -> None:
    """One manager taken down as part of a reload, inside its bound and
    never raising: a connection that will not close cleanly is still a
    connection this server is finished with."""
    with contextlib.suppress(Exception):
        await manager.stop(STOP_TIMEOUT_S)


def _allowed(grant: McpGrant, tools: list[ToolDef]) -> list[ToolDef]:
    """The tools of one server this grant reaches.

    Matched by the published name without its entry prefix, which is the
    identifier this application owns: it has been through the publishing
    rule, the status surface prints it and the model calls it, so what
    the operator wrote is compared against what the model would see and
    never against what the server listed.
    """
    if grant.tools is None:
        return tools
    allowed = set(grant.tools)
    return [tool for tool in tools if names.unqualified(grant.server, tool.name) in allowed]


def _check_egress(name: str, entry: McpServerConfig) -> None:
    """Enforce server.local_only for one referenced MCP server (#30).
    Tool arguments carry conversation-derived data, and no transport
    knows its own egress (a stdio command may proxy anywhere, a url may
    name localhost), so unlike providers there is nothing class-level to
    consult: every referenced entry needs the operator's declaration."""
    if entry.egress is False:
        return
    if entry.egress is None:
        raise McpConfigError(
            f"mcp_servers.{name}: server.local_only is on, and whether an MCP "
            f"server sends session data off this network cannot be known from "
            f'its transport; declare "egress: false" on this entry to assert '
            f"that whatever its command or URL reaches stays local"
        )
    raise McpConfigError(
        f"mcp_servers.{name}: server.local_only is on, but this entry declares "
        f"that it sends session data off this network"
    )


@dataclass(frozen=True)
class McpSlice:
    """The configuration an `McpServers` was built from: every entry
    under `mcp_servers`, referenced or not, and what each agent may
    reach of each.

    Kept rather than consulted again, so the status surface has one
    source and cannot disagree with what is running: an entry an
    operator has written since boot is not part of this world yet, and a
    view that read the database would say it was.
    """

    entries: tuple[str, ...] = ()
    grants: Mapping[str, tuple[McpGrant, ...]] = field(default_factory=dict)

    @classmethod
    def of(cls, config: Config) -> "McpSlice":
        return cls(
            entries=tuple(sorted(config.mcp_servers)),
            grants={
                agent: tuple(config.mcp_for_agent(agent)) for agent in sorted(config.agents)
            },
        )

    def allowed_by_agent(self, entry: str) -> dict[str, list[str] | None]:
        """Which agents may reach one entry and how much of it: the
        allow list they were given, or None for the whole server. In the
        order the grants were taken, which is agent-name order.

        One value per agent, since a list may name a server once."""
        return {
            agent: (None if grant.tools is None else list(grant.tools))
            for agent, grants in self.grants.items()
            for grant in grants
            if grant.server == entry
        }

    def grants_for(self, agent: str) -> tuple[McpGrant, ...]:
        """What one agent may reach, entry by entry and with each
        entry's allow list, and nothing for an agent this slice does not
        know.

        Not an error, deliberately: a session is holding the agent it
        was built with, and a reload can have applied a configuration
        that agent was deleted from. Answering "no servers" leaves that
        conversation talking without tools until it ends, which is what
        the rest of a deleted agent's session does too."""
        return tuple(self.grants.get(agent, ()))

    def entries_for(self, agent: str) -> tuple[str, ...]:
        """Which entries one agent may reach, whole or in part. What a
        revive needs: an allow list narrows the tools, never whether the
        connection is worth making."""
        return tuple(grant.server for grant in self.grants_for(agent))

    def allows(self, agent: str, entry: str, published: str) -> bool:
        """Whether one agent's grants reach one published tool of one
        entry. False for an agent this slice does not know and for an
        entry it was never granted, so the question has one answer
        rather than two."""
        for grant in self.grants_for(agent):
            if grant.server != entry:
                continue
            return grant.tools is None or names.unqualified(entry, published) in grant.tools
        return False

    def allowed_names(self, entry: str) -> frozenset[str]:
        """Every tool name some grant allows of one entry, unprefixed.

        What a publication is checked against, so an allow list naming
        something the server does not offer is visible. A whole-server
        grant contributes nothing: it names no tool, so it can name none
        that failed to arrive."""
        return frozenset(
            name
            for grants in self.grants.values()
            for grant in grants
            if grant.server == entry and grant.tools is not None
            for name in grant.tools
        )


@dataclass(frozen=True)
class McpReload:
    """What one reload did to the running servers, by entry name.

    Four outcomes and no fifth: every configured entry the new world
    references is one of the first three or unchanged, and an entry that
    went away is stopped. What is deliberately not here is whether a
    server that was started came up: that is the status surface's
    answer, taken in the same breath by whoever asked for the reload,
    because a start that connected to nothing is a reload that applied.
    """

    started: tuple[str, ...] = ()
    restarted: tuple[str, ...] = ()
    stopped: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()


# What a reload refused during preparation says, in front of the
# refusal's own sentence. The lead is the operationally important half:
# a caller has to know that the servers are as they were, not half way
# to something else.
RELOAD_REFUSED = "the reload was refused and nothing was changed:"

RELOAD_IN_PROGRESS = (
    "a reload of this server's MCP servers is already running. Nothing was changed by "
    "this request; make it again once the first has answered."
)


class McpServers:
    """Every MCP server some agent references, built at startup."""

    def __init__(
        self, managers: dict[str, McpServerManager], configured: McpSlice | None = None
    ) -> None:
        self._managers = managers
        self._configured = configured if configured is not None else McpSlice()
        # An entry no agent references has no manager and never
        # transitions, so the instant its status carries is when this
        # configuration took effect, which is what this is.
        self._since = time.time()
        # Whether a reload is between its two phases right now. A plain
        # flag rather than a lock because a second reload is refused
        # rather than queued: it would apply a configuration read after
        # the first one's, to a world the first one is in the middle of
        # changing.
        self._reloading = False
        # The apply in flight, if any. Held because it outlives the
        # request that asked for it when that request is cancelled, and
        # the loop keeps only a weak reference to a task nobody awaits.
        self._applying: asyncio.Task[McpReload] | None = None

    @classmethod
    def build(cls, config: Config, secrets: SecretStore | None = None) -> "McpServers":
        """Managers for the entries agents actually use, the way only
        referenced providers are built. Raises McpConfigError for an
        entry that cannot be built, or one that server.local_only
        forbids, which fails the boot.

        `secrets` is the store a snapshot was loaded with, or None for a
        deployment whose credentials are all environment references."""
        configured = McpSlice.of(config)
        return cls(_managers_for(config, secrets, configured), configured)

    def __len__(self) -> int:
        return len(self._managers)

    def __contains__(self, entry: object) -> bool:
        return entry in self._managers

    async def start_all(self) -> None:
        """Connect every server concurrently, so one slow box does not
        add its timeout to the boot of the next."""
        if self._managers:
            await asyncio.gather(*(manager.start() for manager in self._managers.values()))

    async def stop_all(self) -> None:
        """Close every connection, so stdio child processes do not
        outlive the server."""
        for manager in self._managers.values():
            with contextlib.suppress(Exception):
                await manager.stop()

    def tools_for(self, entries: Iterable[str]) -> list[ToolDef]:
        """The tools of these entries, skipping servers that are down."""
        return [
            tool
            for entry in entries
            if entry in self._managers
            for tool in self._managers[entry].tools()
        ]

    def tools_for_agent(self, agent: str) -> list[ToolDef]:
        """The tools one agent may reach right now, its grants applied.

        Asked by agent rather than handed a list of entries, because the
        list is part of what a reload replaces: a session was built on
        the configuration that was loaded at boot, and the grants that
        decide what it may reach are the ones swapped in with the
        managers they name. A snapshot taken through here therefore sees
        one world, and the next reply's snapshot sees the next one.
        """
        return [
            tool
            for grant in self._configured.grants_for(agent)
            for tool in _allowed(grant, self.tools_for([grant.server]))
        ]

    def revive(self, entries: Iterable[str]) -> None:
        """Kick off a background reconnect for any of these that is
        down. Called when a session opens."""
        for entry in entries:
            manager = self._managers.get(entry)
            if manager is not None:
                manager.ensure_reconnecting()

    def revive_for_agents(self, agents: Iterable[str]) -> None:
        """The same, for everything the named agents may reach, through
        the grants that are running now."""
        self.revive(
            [entry for agent in agents for entry in self._configured.entries_for(agent)]
        )

    async def reload(
        self, read: Callable[[], tuple[Config, SecretStore | None]]
    ) -> McpReload:
        """Apply a freshly read configuration to what is running.

        `read` is the re-read of the stored configuration, handed in
        rather than done here: opening a database belongs to the layer
        that owns one, and this layer owns where it runs. It runs in a
        worker thread, because it takes the database's write lock and
        waits out its busy timeout, and this coroutine is on the event
        loop that every live conversation is on.

        Two phases, and only the second touches anything running.
        Preparation validates and builds every manager the new world
        needs; any failure there (an unset `$VAR`, a credential that will
        not decrypt, an egress declaration `server.local_only` forbids)
        refuses with the managers and the grants exactly as they were.
        Application then stops what is going, starts what is new, and
        swaps the slice, so the grants change at one instant rather than
        across one.

        Being unreachable is not a preparation failure, which is the
        boot's rule carried over: a candidate that connects to nothing
        applies as a down manager with its reason on the status surface,
        revived when a session that would use it opens.

        One at a time. A second reload while one is running is refused
        rather than queued, because it would carry a configuration read
        later than the first one's into a world the first one is halfway
        through changing.

        The second phase finishes whatever happens to the caller. A
        client that disconnects cancels the handler awaiting this, and a
        cancellation landing between the stops and the swap would leave
        stopped managers in the live set and started ones reachable by
        nobody, with the exclusion released as though the reload were
        done. So the apply runs in a task of its own behind a shield:
        cancelling the request cancels the waiting, and the world still
        arrives in one piece.
        """
        if self._reloading:
            raise ReloadInProgressError(RELOAD_IN_PROGRESS)
        self._reloading = True
        applying: asyncio.Task[McpReload] | None = None
        try:
            config, secrets = await self._read(read)
            # One slice, composed before preparation and applied after
            # it, so the world the candidates were built for is the
            # world that gets installed.
            configured = McpSlice.of(config)
            candidates = self._prepared(config, secrets, configured)
            applying = asyncio.create_task(
                self._apply(configured, candidates), name="mcp-reload"
            )
            self._applying = applying
            return await asyncio.shield(applying)
        finally:
            # Held until the apply itself is over, cancelled caller or
            # not: a second reload starting against a world the first is
            # still changing is exactly what the exclusion is for.
            if applying is None or applying.done():
                self._release(applying)
            else:
                applying.add_done_callback(self._release)

    def _release(self, applying: "asyncio.Task[McpReload] | None") -> None:
        """The reload is over, however it ended. Also where an apply
        whose caller went away has its outcome consumed, so it does not
        end as an unretrieved exception at shutdown."""
        self._reloading = False
        self._applying = None
        if applying is not None and applying.done():
            with contextlib.suppress(Exception, asyncio.CancelledError):
                applying.exception()

    @staticmethod
    async def _read(
        read: Callable[[], tuple[Config, SecretStore | None]],
    ) -> tuple[Config, SecretStore | None]:
        """The stored configuration, read off this loop, refusing in the
        words the other half of the preparation refuses in.

        In a worker thread because it takes the database's write lock
        and waits out its busy timeout, and this coroutine is on the
        loop every live conversation is on.

        A stored snapshot that will not compose is as much a reload that
        changed nothing as a candidate that would not build, and an
        operator reading the two sentences should not have to work out
        which half of the preparation they are in. The two exceptions
        keep their own type because their type is the answer: a busy
        database is retryable and answers 409, unreadable stored state
        is not the caller's fault and answers 500.

        Recorded and re-raised outside the handler, the rule this
        codebase settled on: raised inside one, the refusal would carry
        whatever the read was holding when it failed.
        """
        problem: str | None = None
        try:
            return await asyncio.to_thread(read)
        except (DatabaseBusyError, StorageError):
            raise
        except ConfigError as exc:
            problem = f"{RELOAD_REFUSED} {exc}"
        raise ConfigError(problem)

    def _prepared(
        self, config: Config, secrets: SecretStore | None, configured: McpSlice
    ) -> dict[str, McpServerManager]:
        """Every manager the new configuration needs, built while
        nothing running has been touched.

        The refusal is recorded and raised outside the handler, the rule
        this codebase settled on: raised inside one, it would carry the
        exception being handled as its context, and one of these is a
        decryption failure. Its message names locations and never
        values, which is what lets it travel out as the API's sanitized
        sentence.
        """
        problem: str | None = None
        try:
            return _managers_for(config, secrets, configured)
        except (McpConfigError, ConfigError) as exc:
            problem = f"{RELOAD_REFUSED} {exc}"
        raise ConfigError(problem)

    async def _apply(
        self, configured: McpSlice, candidates: dict[str, McpServerManager]
    ) -> McpReload:
        """The second phase: the diff, the lifecycles, and the swap.

        The lifecycle work is concurrent the way `start_all` already
        connects at boot, so the whole of it is one connect timeout plus
        small change rather than a sum over servers. Stops first and
        starts after them, so an entry whose command was edited does not
        have two copies of the same child process alive at once.
        """
        keep: dict[str, McpServerManager] = {}
        started: list[str] = []
        restarted: list[str] = []
        unchanged: list[str] = []
        going: list[McpServerManager] = []
        arriving: list[McpServerManager] = []
        for name, candidate in candidates.items():
            running = self._managers.get(name)
            if running is not None and running.same_as(candidate):
                # The connection stands; only what the grants name of it
                # may have moved, which the kept manager is told so a
                # newly allowed name that never published is still said
                # out loud.
                running.expect(configured.allowed_names(name))
                keep[name] = running
                unchanged.append(name)
                continue
            if running is not None:
                going.append(running)
                restarted.append(name)
            else:
                started.append(name)
            keep[name] = candidate
            arriving.append(candidate)
        stopped = sorted(set(self._managers) - set(candidates))
        going += [self._managers[name] for name in stopped]

        if going:
            await asyncio.gather(*(_stopped(manager) for manager in going))
        if arriving:
            await asyncio.gather(*(manager.start() for manager in arriving))
        # The swap, and everything it decides at once: which managers a
        # tool snapshot reaches, and which entries an agent's grant
        # names. Assigned rather than mutated, and with no await between
        # the two, so no reply can be built on half of one world.
        self._managers = keep
        self._configured = configured
        self._since = time.time()
        logger.info(
            "mcp servers reloaded: %d started, %d restarted, %d stopped, %d unchanged",
            len(started),
            len(restarted),
            len(stopped),
            len(unchanged),
        )
        return McpReload(
            started=tuple(started),
            restarted=tuple(restarted),
            stopped=tuple(stopped),
            unchanged=tuple(unchanged),
        )

    def status(self) -> dict[str, dict[str, Any]]:
        """What every configured entry is doing right now, by name.

        One entry per configured `mcp_servers` entry rather than one per
        manager, because an entry no agent references has no manager at
        all and its absence from a list of tools is exactly the thing an
        operator cannot see from anywhere else.

        The tool lists are published names and nothing else. A
        description, or the name a server listed before the publishing
        rule got to it, is bytes that server chose, and a server holding
        a credential of ours can reflect one in either; a gated read that
        carried them would be the secret-readback path the rest of the
        API refuses to be.
        """
        return {entry: self._status_of(entry) for entry in self._configured.entries}

    def _status_of(self, entry: str) -> dict[str, Any]:
        # `grants` is a mapping rather than a list of agent names, and
        # the value is how much of the server that agent gets: None is
        # all of it, a list is the tools it was allowed. Beside the
        # published list above it, which is what makes an allow list
        # naming something this server does not offer answerable in one
        # read.
        grants = self._configured.allowed_by_agent(entry)
        manager = self._managers.get(entry)
        if manager is None:
            return {
                "state": UNUSED,
                "reason": None,
                "since": _instant(self._since),
                "tools": [],
                "grants": grants,
            }
        return {
            "state": manager.state,
            "reason": manager.reason,
            "since": _instant(manager.since),
            "tools": [tool.name for tool in manager.tools()],
            "grants": grants,
        }

    def timeout_for(self, entry: str) -> float | None:
        manager = self._managers.get(entry)
        return None if manager is None else manager.tool_timeout_s

    async def call(
        self, published: str, arguments: dict[str, Any], agent: str
    ) -> tuple[str, bool]:
        """Run a tool under the qualified name the model was given, for
        the agent that is speaking. The entry prefix says which server
        owns it, and the server maps the rest back to whatever it
        actually listed.

        The grant is checked here as well as when the snapshot was
        taken, so "this agent cannot reach that tool" does not rest on
        the model only calling what it was shown. The agent is passed in
        rather than remembered: one registry serves every session, and
        the grants are the ones running now.
        """
        split = names.split_qualified(published)
        manager = self._managers.get(split[0]) if split is not None else None
        if manager is None:
            raise McpServerDown(f'no MCP server owns a tool called "{published}"')
        if not self._configured.allows(agent, manager.name, published):
            raise McpToolNotGranted(
                f'this assistant is not allowed to use the tool "{published}"'
            )
        return await manager.call(published, arguments)
