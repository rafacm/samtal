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

from samtal_server import logs
from samtal_server.app import create_app
from samtal_server.config import Config, ConfigError
from samtal_server.config.boot import load_boot_config
from samtal_server.config.loader import CONFIG_ENV_VAR
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

# What uvicorn's own graceful shutdown gets, after our drain has already
# had the conversations. Short, because by then there is nothing left to
# wait for but sockets that would not go.
UVICORN_GRACEFUL_SHUTDOWN_S = 5

# The first word that means "configure, do not serve".
CONFIG_COMMAND = "config"


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
            await self._app.state.sessions.drain(self._drain_s)
        finally:
            # Whatever the drain did or did not manage, the process is
            # going: uvicorn's own shutdown, and the 1012 fail-close it
            # begins with, are the backstop for anything still holding on.
            super().handle_exit(sig, frame)


def serve(app: FastAPI, config: Config) -> None:
    """Run the server until it is signalled to stop."""
    server = DrainingServer(
        uvicorn.Config(
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
        ),
        app,
        config.server.drain_s,
    )
    server.run()


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

    parser = argparse.ArgumentParser(prog="samtal-server")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help=f"path to the YAML config file (default: ${CONFIG_ENV_VAR})",
    )
    args = parser.parse_args()

    try:
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
        app = create_app(config, booted.secrets)
    except (ConfigError, ProviderError) as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None

    serve(app, config)


if __name__ == "__main__":
    main()
