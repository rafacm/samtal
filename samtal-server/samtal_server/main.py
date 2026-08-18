"""The command line entry point, and the shutdown that goes with it.

A redeploy ends every conversation in flight, and the OTA endpoint
cannot be restarted independently of the websocket one, so a graceful
stop is the server's own job. Uvicorn cannot do this part: it
fail-closes every open websocket with 1012 the moment its shutdown
begins, so waiting for its graceful shutdown to drain conversations is
not possible. The drain therefore runs first, from the signal handler,
and uvicorn's shutdown is what happens after it.
"""

import argparse
import asyncio
import logging
import sys
from types import FrameType

import uvicorn
from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI

from samtal_server import logs, onboarding
from samtal_server.app import StartupFailed, create_app, startup_failure
from samtal_server.composition import Composition
from samtal_server.config import Config, ConfigError
from samtal_server.config.boot import load_boot_config
from samtal_server.config.loader import CONFIG_ENV_VAR
from samtal_server.events import EventEnforcementError, resolve_enforcement
from samtal_server.providers import ProviderError

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

# The first words that mean "do this, do not serve".
CONFIG_COMMAND = "config"
CONVERSATIONS_COMMAND = "conversations"
EVENTS_COMMAND = "events"


class DrainingServer(uvicorn.Server):
    """A uvicorn server that lets conversations finish before it stops.

    The first signal starts the drain and lets uvicorn exit when it
    completes; a second one is passed straight through, which is how an
    operator in a hurry forces the issue.
    """

    def __init__(self, config: uvicorn.Config, app: FastAPI, drain_s: float) -> None:
        super().__init__(config)
        self._app = app
        self._drain_s = drain_s
        self._draining = False

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        if self._draining or self._drain_s <= 0:
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
        asyncio.get_running_loop().create_task(self._drain(sig, frame))

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
            super().handle_exit(sig, frame)


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
    `uvicorn samtal_server.app:app` keeps uvicorn's own startup-failure
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


def main() -> None:
    # Read a .env file into the environment before anything looks at it, so
    # it can carry SAMTAL_* overrides, SAMTAL_CONFIG, and provider secrets.
    # Real environment variables keep priority over .env values. usecwd makes
    # the search start from the invocation directory, not this file's.
    load_dotenv(find_dotenv(usecwd=True))

    if sys.argv[1:2] == [CONFIG_COMMAND]:
        # `samtal-server config ...` configures and exits; anything else
        # is the server, parsed exactly as it was before this existed. A
        # word check rather than an argparse subparser, so that adding
        # the command group cannot change how `samtal-server --config
        # path` parses. Imported here because the command group pulls in
        # the database machinery, which serving does not need yet.
        from samtal_server.config import cli

        raise SystemExit(cli.main(sys.argv[2:]))

    if sys.argv[1:2] == [CONVERSATIONS_COMMAND]:
        # The second group, dispatched the same way and for the same
        # reasons. It has to work when the server will not start, which
        # is the whole point of a purge command, so it reaches nothing
        # here beyond the file half of the configuration.
        from samtal_server.conversations import cli as conversations_cli

        raise SystemExit(conversations_cli.main(sys.argv[2:]))

    if sys.argv[1:2] == [EVENTS_COMMAND]:
        # The third group, and the one that reaches least: it prints the
        # event registry and opens nothing at all. Dispatched here, above
        # the mode resolution below, so that an unusable
        # SAMTAL_EVENTS_ENFORCEMENT cannot stand between a reader and the
        # document that says what the events are.
        from samtal_server import events_cli

        raise SystemExit(events_cli.main(sys.argv[2:]))

    parser = argparse.ArgumentParser(prog="samtal-server")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help=f"path to the YAML config file (default: ${CONFIG_ENV_VAR})",
    )
    args = parser.parse_args()

    try:
        # How strictly this process holds its events to their
        # declarations (#155). Here rather than at import, because
        # `.env` is what a deployment sets it in and this module imports
        # the app, and therefore the emitters, long before this line
        # runs. After the three subcommand exits above, too: an invalid
        # value of a server-only variable must not block a recovery
        # command or the generated reference.
        # `create_app` resolves it again for a server nothing launched
        # through here.
        resolve_enforcement()
        # Both halves: the file named by --config or SAMTAL_CONFIG, and the
        # domain half from the database that file points at.
        booted = load_boot_config(args.config)
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
            config, booted.secrets, on_started=lambda: onboarding.log_banner(config.server)
        )
    except (ConfigError, EventEnforcementError, ProviderError) as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None

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
        raise SystemExit(1)


if __name__ == "__main__":
    main()
