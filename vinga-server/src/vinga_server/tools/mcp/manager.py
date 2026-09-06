"""One configured MCP server: its connection, its tools, and its
reconnection.

Each manager's whole lifecycle lives in one task: the SDK's clients
are async context managers over anyio task groups, and entering
them in one task while exiting in another is what breaks their
cancel scopes. The task connects, publishes its tools, and then
waits until asked to stop.

Beside the lifecycle sits what a reload needs of one manager: the
comparison that decides whether a connection stands, the bounded
stop, and the tasks that would not finish unwinding inside it.
"""

import asyncio
import contextlib
import logging
import time
from contextlib import AsyncExitStack
from typing import Any

import mcp.types
from mcp import ClientSession

from vinga_server.config import Config, McpServerConfig
from vinga_server.config.entities import descriptor, entity_location
from vinga_server.config.secrets import SecretStore
from vinga_server.egress import EgressRefusal, check_mcp_server
from vinga_server.events.catalog import (  # noqa: E402
    McpCallDropped,
    McpConnected,
    McpConnectFailed,
    McpDropped,
    McpStopped,
)
from vinga_server.events.values import (  # noqa: E402
    ClassNames,
    Count,
    Identifier,
    McpDown,
    McpTransport,
    Whole,
)
from vinga_server.providers import ToolDef
from vinga_server.runtime.prompt import ServerPrompt
from vinga_server.tools import names
from vinga_server.tools.publish import PublishedTools, publish

from . import events
from .prompts import (
    INSTRUCTIONS_CHANNEL,
    _discovered,
    _injectable,
    _redactor,
)
from .slice import McpSlice
from .transport import (
    CONNECT_TIMEOUT_S,
    DISCOVERY_FAILED,
    TRANSPORT_FAILED,
    _connect,
    _connection_identity,
    _down_reason,
    _reason,
    _resolve,
    _result_text,
    quiet_sdk_loggers,
)

logger = logging.getLogger(__package__)


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


class McpConfigError(ValueError):
    """An MCP server that cannot be built as configured. Raised at boot,
    like a bad provider, because at call time it would fail every
    conversation that reaches it."""


class McpServerDown(RuntimeError):
    """A call to a server that is not currently connected, or to one
    that no longer owns the name it was asked for.

    The second is a reload landing between a call being resolved and
    being executed: the entry that published the name may have gone, or
    a more specific entry may have taken the name over. Both are the
    same answer to the caller, which is that the tool it meant did not
    run, and both are refusals rather than reroutes: a call is executed
    by the entry it was resolved against or by nobody."""


class McpCallFailed(RuntimeError):
    """A call that went out to a server and did not come back.

    Raised in place of whatever the SDK or the transport raised, and
    deliberately carrying none of it, neither in its message nor as a
    cause or a context. This exception is not caught and inspected: the
    pipeline renders it into the tool result the model is given, so
    every character of it is text that goes into the conversation, and
    from there into the record the conversation store keeps. An SDK
    exception raised near a response body can quote that body, and a
    server holding a credential of this deployment's can put it in the
    error it answers with, so what a third party wrote must not be the
    thing that decides what this assistant says.

    What the failure was is recorded where a diagnosis belongs, in the
    `mcp_call_dropped` event beside the raise, as the class name this
    application's own classifier answers with.

    A `RuntimeError` like `McpServerDown`, because to everything above
    these two mean the same thing: the tool did not run.
    """


class McpServerManager:
    """One configured MCP server: its connection, its tools, and its
    reconnection.

    A registry and a reload touch fourteen members of one of these and
    nothing else: `state`, `reason`, `since`, `tool_timeout_s`,
    `shipped_instructions`, `shipped_prompts`, `tools`, `listed_at`,
    `expect`, `same_as`, `ensure_reconnecting`, `start`, `stop` and
    `call`. That is what a manager owes the world around it, at the size
    that world actually reads, and no member is on the list for looking
    like it belonged: each is there because `registry.py` or `reload.py`
    calls it.

    Everything else here (the run, the exit stack, the session, the
    identity a reload compares by, the abandonment of a task that will
    not end) is this class's own business. Keeping the two apart is a
    review of those two modules rather than a declaration, which is what
    it always really was.
    """

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
        _resolve(name, config, secrets, "env")
        _resolve(name, config, secrets, "headers")
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
        # What this server shipped about itself on the connection that
        # is up, held beside the published tools because it has their
        # lifetime exactly: it arrived with this connection and it goes
        # when this connection does. The instructions are captured
        # whatever the entry's opt-in says, since the opt-in is excluded
        # from connection identity and a false-to-true reload has to be
        # able to expose what a connection nobody restarted already
        # holds; the prompts are fetched only when the entry names some,
        # since that field does restart the connection.
        self._instructions: str | None = None
        self._prompts: tuple[ServerPrompt, ...] = ()
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
    def session(self) -> ClientSession | None:
        """The SDK session this manager's calls go over, or None while
        this server is down.

        The live object rather than a copy of what it holds, because
        what reads it is a suite standing in the way of one of its
        methods: a far side that stops answering mid-call is a thing
        this manager has to behave around, and there is no configuration
        that produces one on demand. Read-only, since which session a
        manager has is its run's own business: one is opened by the task
        that connects and dropped by the one that unwinds, and a session
        assigned from outside would belong to neither.
        """
        return self._session

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

    def listed_at(self, published: str) -> int | None:
        """Where this server listed one of its published tools, counted
        from one, or None for a name this connection does not know.

        The identifier every line about a tool uses, because it is the
        only one this side owns: a published name is half an operator's
        entry name and half whatever the far side called its tool, and
        the second half is bytes nothing bounds. Read off the
        publication rather than counted from `tools()`, since that list
        is this one with the unpublishable dropped out of it and
        counting it would answer with a position the far side's listing
        never had.
        """
        return self._published.position_of(published)

    @property
    def shipped_instructions(self) -> str | None:
        """What this server said about itself when it connected, or None
        when it said nothing, said too much, or is not connected.

        Captured whatever the entry's opt-in says, and what the opt-in
        decides is whether a prompt and an inspection read carry it.
        """
        return self._instructions

    @property
    def shipped_prompts(self) -> tuple[ServerPrompt, ...]:
        """The prompts this entry named that this server published and
        this connection could render, in the order the entry named
        them."""
        return self._prompts

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

        The fragment's prompt fields are left out of the comparison,
        because they are not part of the world this connects to: see
        `_connection_identity`.
        """
        return (
            _connection_identity(self._config) == _connection_identity(other._config)
            and self._secrets_mark == other._secrets_mark
        )

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
        """Create the task one run of this server lives in.

        The one place a connection is ever begun, which is why the SDK
        quieting is here rather than at a caller. `start` is not the
        only public way in: a session opening revives a down server
        through `ensure_reconnecting`, which comes straight here, so a
        rule installed in `start` would leave a process whose first
        connect is a background reconnect talking to a server with the
        SDK's own loggers still reaching every handler of ours.
        """
        # Before the task exists rather than inside it, so the rule is
        # in force at the instant a connect becomes reachable.
        quiet_sdk_loggers()
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
        began = time.monotonic()
        # Which part of the envelope below a failure would be about.
        # Three quite different things happen inside one `try` (a
        # transport that has to be brought up, a handshake the far side
        # answers, and a listing it has to produce), and by the time an
        # exception arrives at the handler they are indistinguishable:
        # any of the three can raise the same wrapped group. So the
        # marker is advanced between the stack entries, here and inside
        # `_connect`, and it is what the down event's reason is read
        # off. A local rather than a field on the manager, because it
        # means nothing between two runs and outside this one it is
        # nobody's business.
        phase = TRANSPORT_FAILED

        def reached(next_phase: str) -> None:
            nonlocal phase
            phase = next_phase

        try:
            async with AsyncExitStack() as stack:
                async with asyncio.timeout(CONNECT_TIMEOUT_S):
                    session, initialized = await _connect(
                        self._name, self._config, self._secrets, stack, reached
                    )
                    reached(DISCOVERY_FAILED)
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
                # A count, and no names anywhere in the line. Half of a
                # published name is whatever the far side called its
                # tool, sanitizing replaces only the characters an LLM
                # API refuses, and a credential is alphanumeric and
                # survives that whole, so a server holding one of this
                # deployment's own could put it into the retained logs
                # by listing a tool under it. Which names an entry
                # published is a question with an answer that is not a
                # log line: `vinga-server config mcp-server status`
                # prints them, to a terminal, for whoever asked.
                published = len(self._published.tools)
                events.emit(
                    lambda: McpConnected(
                        entry=Identifier(self._name),
                        # The configured transport is the config model's
                        # own literal, read into the event vocabulary here.
                        transport=McpTransport(self._config.transport),
                        tools=Count(published),
                        duration_ms=Whole(round((time.monotonic() - began) * 1000)),
                    )
                )
                self._warn_about_unpublished()
                # The optional half, and it runs here for a reason. The
                # tools are the entry's load-bearing part, and inside the
                # envelope above a raised exception marks this manager
                # down and takes every one of them away; out here the
                # connection is published and nothing below can cost it.
                # It runs before the settle rather than behind it, so
                # `start()` still returns to a manager whose whole answer
                # is in place, at the price of one discovery deadline on
                # the boot and on a reload.
                await self._capture(session, initialized)
                self._announce_shipped()
                self._settled.set()
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except Exception as raised:
            # Bound to an ordinary local first: `except ... as` unbinds
            # its name when the block ends, and the thunk below is built
            # here and called inside the emitter's guard.
            failed = raised
            self._became(DOWN, _reason(failed))
            events.emit(
                lambda: McpConnectFailed(
                    entry=Identifier(self._name),
                    # `_down_reason` answers the transport module's own
                    # constants, so the crossing is spelled here.
                    reason=McpDown(_down_reason(failed, phase)),
                    duration_ms=Whole(round((time.monotonic() - began) * 1000)),
                    failure=ClassNames(_reason(failed)),
                )
            )
        finally:
            self._session = None
            self._published = PublishedTools(tools=[], originals={})
            self._forget_shipped()
            # A stop with nothing wrong carries no reason. A failure
            # recorded its own just above, and a connection dropped
            # after a failed call recorded the fixed token before it
            # asked this task to end, so neither is overwritten here.
            if self._state == CONNECTED:
                self._became(DOWN, None)
                # And it is the one way down that is not a warning: a
                # shutdown and a reload both come through here, and an
                # operator who asked for one is not being told about a
                # problem. No duration either, deliberately: what the
                # field means everywhere else on this event is how long
                # the connect ran before it failed, and how long a
                # working connection lasted is a different number that
                # would be answering under the same name.
                events.emit(lambda: McpStopped(entry=Identifier(self._name)))
            self._settled.set()

    async def _capture(
        self, session: ClientSession, initialized: mcp.types.InitializeResult
    ) -> None:
        """Take what this server ships, with this deployment's own
        credentials taken back out of it.

        A server holds whatever the entry's `env` and `headers` gave it,
        and an opted-in entry asks for its words to be put in a system
        prompt and on a gated read. That is the operator's decision about
        a third party's text and it stands; it is not a decision to let
        the server hand this deployment's own secrets back through a
        surface the rest of the API refuses to read them from. So the
        materialized values are resolved once here, every occurrence of
        one is replaced before anything is stored, and only the redacted
        text is kept.

        The resolved values live for the length of this call and are
        never held on the manager, which is the rule `__init__` already
        follows and for the same reason: a manager lives as long as the
        process.
        """
        try:
            redact = _redactor(
                _resolve(self._name, self._config, self._secrets, "env"),
                _resolve(self._name, self._config, self._secrets, "headers"),
            )
        except Exception as exc:
            # Fail closed, and do not take the tools with it. Resolving
            # succeeded a moment ago inside the connect or there would be
            # no session here, so this is the environment moving under a
            # running server; what it costs is the optional half, and the
            # alternative is keeping text nothing was redacted from.
            logger.warning(
                "mcp server %s: its own configured values could not be resolved (%s), "
                "so nothing this server ships is kept this time",
                self._name,
                _reason(exc),
            )
            return
        self._instructions = _injectable(
            self._name, redact(initialized.instructions), INSTRUCTIONS_CHANNEL
        )
        self._prompts = await _discovered(
            self._name, self._config, session, initialized.capabilities, redact
        )

    def _announce_shipped(self) -> None:
        """What this connection captured, in sizes and positions.

        Said at all because an operator tuning a small model's context
        budget should not have to open an inspection surface to learn
        that a server started shipping a thousand characters overnight,
        and said this way because the bytes themselves reach exactly two
        places, neither of which is a log.
        """
        if self._instructions is None and not self._prompts:
            return
        logger.info(
            "mcp server %s shipped guidance: %d characters of instructions, and "
            "prompts at inject_prompts position(s) %s",
            self._name,
            0 if self._instructions is None else len(self._instructions),
            ", ".join(
                f"{prompt.position} ({len(prompt.text)} characters)"
                for prompt in self._prompts
            )
            or "none",
        )

    async def call(self, published: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Run one of this server's tools, named as the model was given
        it. The name goes back out as the server listed it, since what
        the model saw may have been sanitized. A transport failure marks
        the server down (so the next session revives it) and is raised
        as `McpCallFailed` for the session to turn into an error result.

        What the far side raised does not travel with it. The failure is
        classified inside the handler, where the exception is, and the
        exception this method raises is built after the handler has
        closed, so it carries no cause, no context and no words but
        this application's own: the pipeline renders it into a tool
        result and hands that to the model.
        """
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
        except Exception as exc:
            self._mark_down(self._published.position_of(published), _reason(exc))
        else:
            return _result_text(result), bool(result.isError)
        # Outside the handler, which is what makes it structural rather
        # than a promise: past this line there is no exception being
        # handled, so nothing can attach itself as a context, and there
        # is nothing in scope to quote by accident.
        raise McpCallFailed(
            f'MCP server "{self._name}" did not answer this call, and its connection '
            "has been dropped"
        )

    def _mark_down(self, position: int | None, error: str) -> None:
        """Unwind the connection so the next session reconnects it.

        Two events rather than one, and the pairing is contract (#138):
        one failed call is two stories, and they are read by different
        questions. `mcp_call_dropped` is the tool's: something the model
        asked for did not happen, and which tool it was is the whole of
        what a conversation's reader needs. `mcp_down` is the
        connection's: this entry is gone until something revives it,
        which is the same fact a connect failure reports and belongs in
        the same bucket as one. Emitting only the first would hide an
        entry going away inside a line about a tool; only the second
        would lose which call took it away.

        The tool is said by its position in this server's listing and
        never by its name. A published name is half an operator's entry
        name and half what the far side called its tool, sanitizing
        only replaces the characters an LLM API refuses, and an
        alphanumeric credential goes through that untouched, so a
        server that lists a tool under one of this deployment's own
        secrets could put it in the retained logs by failing a call.
        The position is a number this code counted; the name is a
        question `vinga-server config mcp-server status` answers, in a
        terminal, for whoever asked.
        """
        # And the kind of failure it was, as the class name `_reason`
        # answers with, which is the same field `provider_failed` has
        # carried since #137 and the same rule: a type name says what
        # went wrong, and a message says what a stranger wrote. This is
        # the only place it is recorded now, since the exception raised
        # to the session carries nothing.
        events.emit(
            lambda: McpCallDropped(
                entry=Identifier(self._name),
                position=None if position is None else Count(position),
                error=ClassNames(error),
            )
        )
        events.emit(lambda: McpDropped(entry=Identifier(self._name)))
        self._became(DOWN, DROPPED_AFTER_FAILED_CALL)
        self._session = None
        self._published = PublishedTools(tools=[], originals={})
        self._forget_shipped()
        self._stop.set()

    def _forget_shipped(self) -> None:
        """Drop what the connection that is going shipped about itself.

        Beside the published tools wherever they are dropped, and for
        the same reason: this text arrived on that connection, and a
        server that is down has not told this one anything. What the next
        connect captures replaces it.
        """
        self._instructions = None
        self._prompts = ()


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
        # What both refusals below name this entry: composed once, and
        # through `entity_location`, the one home for where an entry is
        # written, rather than joined to `mcp_servers.` by hand in each
        # of them. The duplication was visible inside a single sentence,
        # since an entry that will not construct is reported as this
        # location wrapped around the one `resolve_mcp_values` composes
        # for the group underneath it, and that half has read the helper
        # since #414. The provider build composes its own label once for
        # its two halves and for the same reason (#413).
        #
        # The strip that helper carries is defence here rather than a
        # leak closed, which is the one place this differs from that
        # label. A provider name is held to one URL path segment at write
        # time only, so a row stored before that rule still composes and
        # reaches the build; an MCP entry name becomes a tool-name
        # prefix, so `models.check_mcp_entry_names` holds it to
        # `[A-Za-z0-9_-]+` on every composition as well, and a name that
        # could carry a credential refuses the whole snapshot before
        # anything here runs (#420).
        written_at = entity_location(descriptor("mcp-server"), name)
        if config.server.local_only:
            # One module holds the rule for entries and providers alike
            # (#30, #136); what stays here is this surface's own
            # exception around the sentence it composed.
            try:
                check_mcp_server(written_at, entry)
            except EgressRefusal as exc:
                raise McpConfigError(str(exc)) from exc
        try:
            managers[name] = McpServerManager(
                name, entry, secrets, configured.allowed_names(name)
            )
        except ValueError as exc:
            raise McpConfigError(f"{written_at}: {exc}") from exc
    return managers


# The tasks that would not finish unwinding inside their bound. Held
# here because the event loop keeps only a weak reference to a task, so
# a task nobody is awaiting can be collected mid-unwind, and because a
# task that ends in an exception nobody retrieved prints one at
# interpreter shutdown. Both are answered by keeping it until it is
# done and consuming whatever it ended with.
#
# Public because what it holds is a fact about this process rather than
# a detail of the stop that put it there: a reload that answered while
# a connection was still unwinding left something running, and this is
# where anything asking whether it has finished looks. Written by the
# two functions below and by nothing else.
abandoned: set[asyncio.Task[None]] = set()


def _abandon(task: asyncio.Task[None]) -> None:
    """Stop waiting for a manager's task, and let it finish on its own.

    Not a violation of the rule the module docstring states. That rule
    is about entering a transport in one task and exiting it in
    another; this task is still the only thing unwinding its own exit
    stack, and nothing here touches it again. What is given up is the
    waiting, which belongs to a caller holding a request open.
    """
    abandoned.add(task)
    task.add_done_callback(_forget)


def _forget(task: asyncio.Task[None]) -> None:
    abandoned.discard(task)
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
