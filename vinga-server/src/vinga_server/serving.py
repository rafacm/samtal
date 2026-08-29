"""Serving: the uvicorn configuration, the startup, and the drain.

Everything a running server is, from the boot that reads both halves of
the configuration to the shutdown that lets conversations finish. The
command line in front of it is `main.py`, which reaches this module only
in its serve branch and only when the server half is installed.

The split is what makes that conditional reach possible. This module
imports FastAPI, uvicorn, the composition, the boot path, the onboarding
banner and the providers, and `DrainingServer` subclasses
`uvicorn.Server` at import time, so a module that holds any of it cannot
be imported by an installation that carries the client half alone. The
configuration grammar is on the other side of that line, and the
sentence a thin installation is answered with lives in `main.py`,
which is the one file both installations import.

A redeploy ends every conversation in flight, and the OTA endpoint
cannot be restarted independently of the websocket one, so a graceful
stop is the server's own job. Uvicorn cannot do this part: it
fail-closes every open websocket with 1012 the moment its shutdown
begins, so waiting for its graceful shutdown to drain conversations is
not possible. The drain therefore runs first, from the signal handler,
and uvicorn's shutdown is what happens after it.
"""

import asyncio
import logging
import socket
import sys
from types import FrameType

import uvicorn
from fastapi import FastAPI

from vinga_server import logs, onboarding
from vinga_server.app import StartupFailed, create_app, startup_failure
from vinga_server.composition import Composition
from vinga_server.config import Config, ConfigError
from vinga_server.config.boot import load_boot_config
from vinga_server.providers import ProviderError
from vinga_server.registry import CLOSE_MARGIN_S

logger = logging.getLogger(__name__)

# Websocket keepalive. Uvicorn already pings by default; pinning the
# values here makes them load-bearing and documented rather than
# inherited, which matters because they are what settles the per-path
# idle timeout problem: a conversation socket goes quiet between
# utterances, and a proxy that cannot be configured per path needs only
# a read timeout above this.
PING_INTERVAL_S = 20.0
PING_TIMEOUT_S = 20.0

# Where uvicorn writes what went wrong, including the traceback of a
# lifespan that refused to start. Named here because the filter below
# reaches for it by name, which is uvicorn's own contract for its
# loggers.
UVICORN_ERROR_LOGGER = "uvicorn.error"

# What uvicorn's own graceful shutdown gets, after our drain has already
# had the conversations. Short, because by then there is nothing left to
# wait for but sockets that would not go.
UVICORN_GRACEFUL_SHUTDOWN_S = 5


class DrainingServer(uvicorn.Server):
    """A uvicorn server that lets conversations finish before it stops.

    The first signal starts the drain and lets uvicorn exit when it
    completes; a second one is passed straight through, which is how an
    operator in a hurry forces the issue.

    The drain runs in a task this server owns: it holds the reference,
    and its serving does not finish before that task has, or before the
    bound on it expires (#142).
    """

    def __init__(self, config: uvicorn.Config, app: FastAPI, drain_s: float) -> None:
        super().__init__(config)
        self._app = app
        self._drain_s = drain_s
        self._draining = False
        self._drain_task: asyncio.Task[None] | None = None

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        if self._draining or self._drain_s <= 0:
            # The two ways uvicorn is called directly: a second signal,
            # which is an operator forcing the issue while a drain is
            # already running, and a server configured not to drain at
            # all. Both end the event tails first, because every path
            # out of this process ends them and an open stream is a
            # response uvicorn's graceful shutdown would otherwise wait
            # out; the close is idempotent, so the drain below closing
            # them again costs nothing.
            #
            # Neither drains. The first is already draining and the
            # second never does, which is the whole of what `drain_s <=
            # 0` means: closing a tail is not draining a conversation.
            self._close_live()
            super().handle_exit(sig, frame)
            return
        if getattr(self._app.state, "composition", None) is None:
            # Signalled before there is anything to drain. Startup builds
            # the composition and can spend minutes in it (a provider
            # loading a model), so a redeploy landing on a starting pod
            # is an ordinary case and not a rare one: the sessions the
            # drain reaches through do not exist yet, and reading for
            # them would raise inside a signal handler. Passed straight
            # through, the same way a second signal is.
            super().handle_exit(sig, frame)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Signalled before the loop was up or after it went: there is
            # nothing to drain and nothing to schedule the drain on.
            super().handle_exit(sig, frame)
            return
        self._draining = True
        logger.info("shutting down: draining conversations for up to %.0f s", self._drain_s)
        # Scheduled rather than started here: this runs inside a signal
        # handler, which interrupts the loop rather than running on it.
        loop.call_soon_threadsafe(self._start_drain, sig, frame)

    def _start_drain(self, sig: int, frame: FrameType | None) -> None:
        # Kept rather than discarded (#142). The loop holds only a weak
        # reference to a running task, so an unheld one can be collected
        # mid-drain; and a task nobody awaits reports whatever it raises
        # through the garbage collector, long after the line that would
        # explain it. `_settle_drain` below is where it is joined.
        self._drain_task = asyncio.get_running_loop().create_task(self._drain(sig, frame))

    async def _drain(self, sig: int, frame: FrameType | None) -> None:
        try:
            # Annotated at the boundary, and inside the guard rather than
            # in front of it: `app.state` answers Any, so the annotation
            # is what makes this a typed read, and a state that cannot
            # answer is exactly the case where the exit below still has
            # to run.
            composition: Composition = self._app.state.composition
            await composition.sessions.drain(self._drain_s)
        finally:
            # Whatever the drain did or did not manage, the process is
            # going: uvicorn's own shutdown, and the 1012 fail-close it
            # begins with, are the backstop for anything still holding on.
            #
            # The event tails end between the two, after the
            # conversations have had their say and before uvicorn is
            # told to stop: an operator watching a redeploy sees the
            # drain it was watching for, and no open stream is left for
            # uvicorn's graceful shutdown to wait on.
            self._close_live()
            super().handle_exit(sig, frame)

    def _close_live(self) -> None:
        """End every open event tail.

        Explicit rather than incidental. A live stream is a response
        that never completes on its own, so uvicorn's graceful shutdown
        would wait out its whole budget on one; closing the hub wakes
        every reader and ends it, which costs the shutdown nothing.

        The composition is read defensively for the reason the drain's
        own read is: a signal can arrive while the lifespan is still
        building, and a state bag that cannot answer is exactly the case
        where there is nothing to close.
        """
        composition: Composition | None = getattr(self._app.state, "composition", None)
        if composition is not None:
            composition.live.close()

    async def _serve(self, sockets: list[socket.socket] | None = None) -> None:
        """Uvicorn's serving, with the drain settled before it lets go.

        This overrides `_serve` rather than `serve` because `serve` is
        `with self.capture_signals(): await self._serve(...)`, and that
        context manager re-raises the signal it captured, with the
        original handler back in place, on the way out. For SIGTERM that
        handler is the default one, so the process ends inside `serve`
        and anything written after it never runs (verified: a settle
        placed there does not execute, and the process exits 143). Inside
        `_serve` the settle is still on the loop the drain task was
        created on, and it is ahead of the re-raise.
        """
        try:
            await super()._serve(sockets)
        finally:
            await self._settle_drain()

    async def _settle_drain(self) -> None:
        """Join the drain task, under a bound.

        Usually there is nothing to wait for: the drain ends by asking
        uvicorn to exit, so by the time uvicorn has, its task is done.
        What the wait is for is the other order, an exit uvicorn reached
        by itself (a second signal, forced) while the drain is still in
        flight. The bound is the drain's own budget plus the margin the
        registry holds back for the closes, which is the longest a drain
        that is working can take; past it the task is cancelled, said so
        and left, without waiting to see what it makes of being
        cancelled, because a shutdown that cannot end is worse than a
        conversation that is cut off.

        Either way the task is asked what it ended with, including when
        it had already ended before this ran: a task nobody asks reports
        whatever it raised through the loop's default handler when the
        collector reaches it, which prints the exception in full. What
        this server says about a failed drain is `_report_drain`'s one
        sentence and nothing else.
        """
        task = self._drain_task
        if task is None:
            return
        bound = self._drain_s + CLOSE_MARGIN_S
        if not task.done():
            # `asyncio.wait` rather than `wait_for`, which cancels what it
            # gave up on and then waits for that cancellation to land.
            # What a drain does with a cancellation is the drain's own
            # business (a `finally` closing sockets, a client that
            # swallows it), so waiting for it would make the bound no
            # bound at all. This one only waits.
            await asyncio.wait({task}, timeout=bound)
        if task.done():
            _report_drain(task)
            return
        logger.warning("drain did not finish within %.1f s; exiting anyway", bound)
        # Asked to stop, reported whenever it does stop, and not waited
        # for. The callback is what takes the result off it, and the task
        # holds its callbacks and is itself still held here, so neither
        # goes when this frame does.
        task.add_done_callback(_report_drain)
        task.cancel()


def _report_drain(task: "asyncio.Task[None]") -> None:
    """Take what the drain ended with off its task, and say only what may
    be said about it.

    Retrieving it is the point: an exception nobody takes off a task is
    reported by the loop's default handler when the collector gets there,
    which renders the exception and its chain, and a drain runs through
    provider clients and a database. So the failure is reported the way
    a provider that would not build is (`providers/registry.py`): the
    class name is said, the message is not, and no traceback is
    attached. The exit is already under way, since `_drain` delivers it
    in a `finally`, so there is nothing to raise this into either.
    """
    if task.cancelled():
        return
    failure = task.exception()
    if failure is not None:
        logger.warning(
            "the drain failed (%s). What it said is not repeated here, because a "
            "client failing on its way out can quote the endpoint or the credential "
            "its entry names",
            type(failure).__name__,
        )


def uvicorn_config(app: FastAPI, config: Config) -> uvicorn.Config:
    """How this server is served. Built here rather than inline so that
    what a deployment runs is one object a test can also run."""
    return uvicorn.Config(
        app,
        host=config.server.host,
        port=config.server.port,
        ws_ping_interval=PING_INTERVAL_S,
        ws_ping_timeout=PING_TIMEOUT_S,
        timeout_graceful_shutdown=UVICORN_GRACEFUL_SHUTDOWN_S,
        # Leave uvicorn's loggers to propagate into the root handler
        # configured at startup, so its lines share our format and
        # level instead of arriving in a second, fixed one.
        log_config=None,
        # And leave the access log off, which is what makes that
        # propagation safe. An access line is a request line, and two of
        # this server's request lines are things nothing may print: the
        # OTA path carries the deployment's secret segment, and an
        # activation code arrives in the path of a claim, rejected value
        # and all. Neither was ever part of the observability surface
        # either (docs/adr/2026-08-04-json-logs-are-the-observability-surface.md):
        # what an operator reads is the structured events, which name
        # the device, the agent and the outcome, and are written by
        # handlers that know which of their values may be said out loud.
        access_log=False,
    )


class _QuietStartupFailure(logging.Filter):
    """Drops the traceback uvicorn renders for a refused startup, on the
    one path that reports the refusal itself.

    Construction is the lifespan's since #142, so a boot failure happens
    inside uvicorn rather than in front of it, and Starlette hands
    uvicorn the whole formatted traceback of whatever the lifespan
    raised. Left alone, an operator who mistyped a provider option meets
    a stack of frames from this application, FastAPI and contextlib, and
    then the same sentence twice: once at the end of that traceback and
    once from `main()`. The contract this entry point has always kept for
    a refused boot is one sentence and nothing else.

    So the record carrying that traceback is dropped, and only while the
    lifespan has actually recorded a boot failure: a lifespan exception
    that is not one is a bug, and its traceback is the whole of what
    anybody has to work with. `serve()` installs this and takes it off
    again, so it is the command line's alone. A server started as
    `uvicorn vinga_server.app:app` keeps uvicorn's own startup-failure
    surface, which is what the plan records for that path.
    """

    def __init__(self, app: FastAPI) -> None:
        super().__init__()
        self._app = app

    def filter(self, record: logging.LogRecord) -> bool:
        if startup_failure(self._app) is None:
            return True
        if record.exc_info is not None and isinstance(record.exc_info[1], StartupFailed):
            return False
        message = record.getMessage()
        return "Traceback (most recent call last)" not in message and (
            StartupFailed.__name__ not in message
        )


def serve(app: FastAPI, config: Config) -> None:
    """Run the server until it is signalled to stop, or until its startup
    refuses.

    A refused startup is caught here rather than allowed out: uvicorn
    ends the process itself when a lifespan startup fails
    (`uvicorn.Server.startup` calls `sys.exit(3)`), and that would put
    uvicorn's exit code and its rendered traceback in place of the one
    sentence and the exit code of 1 this entry point has always answered
    a boot failure with. So a `SystemExit` is swallowed exactly when the
    lifespan recorded a boot failure, and `main()` reports it; anything
    else that raises `SystemExit` in there is not ours to interpret and
    goes on out. The filter above is the other half of the same
    contract, and is on the logger only for as long as this runs.
    """
    server = DrainingServer(uvicorn_config(app, config), app, config.server.drain_s)
    quiet = _QuietStartupFailure(app)
    uvicorn_errors = logging.getLogger(UVICORN_ERROR_LOGGER)
    uvicorn_errors.addFilter(quiet)
    try:
        server.run()
    except SystemExit:
        if startup_failure(app) is None:
            raise
    finally:
        uvicorn_errors.removeFilter(quiet)


def run(config_path: str | None) -> int:
    """Boot both halves of the configuration and serve until stopped.
    Returns the process exit code.

    The whole lifecycle in one call, because the command line in front
    of it has no business knowing the order: the file half is read, the
    domain half comes from the database that file points at, logging is
    configured as early as the configuration allows, the application is
    built, and `serve` runs it.

    Both refusals are sentences rather than tracebacks, and both leave
    with 1. A configuration that will not load and a provider that will
    not build are the operator's own mistakes and are printed as they
    are raised; a startup that refused inside the lifespan is read back
    off the application afterwards, since uvicorn is what stopped and
    `serve` is what caught its exit.
    """
    try:
        # Both halves: the file named by --config or VINGA_CONFIG, and the
        # domain half from the database that file points at.
        booted = load_boot_config(config_path)
        config = booted.config
        # Logging is configured as early as the config allows, since the
        # format is a config key. Anything that fails before this point is a
        # config error, and those are printed to stderr rather than logged.
        logs.configure(config.server)
        # Pass the app object rather than an import string: the config just
        # read (from --config, which reaches nothing else) has to be the one
        # the app serves from.
        #
        # The URL to type into a device's captive portal goes in as the
        # started callback rather than being printed here: the app is
        # described at this line and built inside `serve` below, and a
        # line announcing where to point a device must not be printed by
        # a server that then fails to start (#142). It stays the CLI's,
        # because an app built for a test lane or an external ASGI server
        # has no operator reading its startup output.
        app = create_app(
            config,
            booted.secrets,
            on_started=lambda: onboarding.log_banner(config.server),
            # Read from the store, three lines above, which is what makes
            # this server's device bindings the database's live answer.
            from_store=True,
        )
    except (ConfigError, ProviderError) as exc:
        print(exc, file=sys.stderr)
        return 1

    serve(app, config)

    # A startup that refused says so here rather than through a
    # traceback. Construction is the lifespan's now, so a boot failure
    # happens inside uvicorn: the lifespan records its sanitized sentence
    # and raises `StartupFailed`, uvicorn stops, `serve` returns, and the
    # sentence is printed and the exit code set exactly as they were when
    # this failed in front of `serve`.
    failure = startup_failure(app)
    if failure is not None:
        print(failure, file=sys.stderr)
        return 1
    return 0


__all__ = [
    "PING_INTERVAL_S",
    "PING_TIMEOUT_S",
    "UVICORN_ERROR_LOGGER",
    "UVICORN_GRACEFUL_SHUTDOWN_S",
    "DrainingServer",
    "run",
    "serve",
    "uvicorn_config",
]
