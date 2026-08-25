"""A real server on a real port, and a board checking in to it.

What the two CLI lanes both need and neither may own. The in-process
security lane drives `cli.main` against this; the wheel lane drives the
installed `vinga` binary as a subprocess against the same thing. A
second copy of a uvicorn thread and its readiness loop would be one
pending bug in the usual way: the copies drift, and the lane running the
older one quietly proves less than it says.

The server is booted the way a deployment with nothing configured yet is
booted: no configuration file anywhere, the file half from the settings
machinery reading `VINGA_SERVER__*`, and the domain half from the
database that half names. That is the shape the quick start documents,
so it is the shape both lanes talk to.

`check_in` is here for the same reason, and it is the one thing in
either lane that is not a command: a code is minted by a board asking
for one, and `device pending list` and `device pending claim` are about
what an operator does with the number on its screen.

It used to be a hand-written POST, which was a second copy of the
exchange `vinga simulator` now ships. Since #248 it drives the
production board instead, so what a lane calls a device and what an
operator can run are one structure. The other check-in helper,
`tests/support/checkin.py`, stays where it is: its job is driving the
route with hand-built and deliberately malformed bodies, which a
production client would never send, and that is a different question.
"""

import contextlib
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
import uvicorn

from vinga_server.app import create_app
from vinga_server.config.boot import load_boot_config
from vinga_server.config.models import API_MOUNT_PATH, DatabaseConfig
from vinga_server.device_endpoint import SUPPLIED_ENDPOINT, Endpoint
from vinga_server.ota import OTA_PATH
from vinga_server.simulator import board

# The one variable a fileless boot needs: which database it serves. The
# server half of the configuration is otherwise all defaults, which is
# what "a handful of environment variables" means for a deployment that
# has not yet configured anything.
DATABASE_NAME_ENV = "VINGA_DB_NAME"

CONFIG_ENV = "VINGA_CONFIG"

# What a lane's board says it is, which is what the pending listing
# shows. Read off the shipped simulator rather than written here: a
# second spelling of it would be a listing assertion that passes while
# the command prints something else.
BOARD = board.BOARD_TYPE


@dataclass(frozen=True)
class Live:
    """One running server, as a lane addresses it."""

    # What a device reaches: the origin the OTA endpoint is served on.
    origin: str

    # Which database it serves, so a test can read the rows back rather
    # than take the API's word for it.
    database: DatabaseConfig

    @property
    def api_url(self) -> str:
        """Where the configuration API is, which is what the CLI is
        pointed at."""
        return f"{self.origin}{API_MOUNT_PATH}"


@contextlib.contextmanager
def serving(database: DatabaseConfig | None = None) -> Iterator[Live]:
    """A real uvicorn on an ephemeral loopback port, booted with no
    configuration file at all.

    `load_boot_config()` with no path and no `VINGA_CONFIG` in the
    environment is exactly what `main()` runs on a deployment that
    passed no `--config`: the file half comes from the settings
    machinery, which reads the `VINGA_DB_*` names and the
    `VINGA_SERVER__*` ones and nothing else, and the domain half comes
    from the database that half names.
    """
    database = DatabaseConfig() if database is None else database
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv(CONFIG_ENV, raising=False)
        patch.setenv(DATABASE_NAME_ENV, database.name)
        booted = load_boot_config()
    with served(create_app(booted.config, booted.secrets), database) as running:
        yield running


@contextlib.contextmanager
def served(app: object, database: DatabaseConfig | None = None) -> Iterator[Live]:
    """One built application on a real uvicorn, on an ephemeral loopback
    port.

    Split from `serving` because two lanes need a thread-served app and
    only one of them wants a fileless boot: the conversation lane builds
    its app from a `Config` with mock providers in it, because a server
    with no providers can hold no conversation. A second copy of a
    uvicorn thread and its readiness loop is the pending bug in the usual
    way.

    The port lives on the socket rather than in the configuration, the
    way `tests/integration/conftest.py` does it: the models refuse 0,
    which is right for a deployment and is not what binding an ephemeral
    port means.

    Run in a thread rather than on a loop of the caller's own, because
    what talks to it is either `cli.main`, which is synchronous and stays
    that way, or a subprocess; uvicorn skips its signal handlers off the
    main thread, which is the one thing that would otherwise need care
    here.
    """
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline, "the server never started"
            time.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield Live(
            origin=f"http://127.0.0.1:{port}",
            database=DatabaseConfig() if database is None else database,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=30)


def check_in(live: Live, mac: str) -> board.CheckIn:
    """One board's OTA check-in, made by the board this repository ships.

    The only thing in either CLI lane that is not a command, and it is
    here because two of the commands need a device: a code is minted by a
    board asking for one, and `device pending list` and `device pending
    claim` are about what an operator does with the number on its screen.

    It answers the closed four-state reading rather than a body, which is
    the reading `vinga simulator check-in` prints: a lane asking "was
    this board offered a code" asks the same question an operator does,
    in the same words.
    """
    endpoint = Endpoint.parsed(f"{live.origin}{OTA_PATH}", "the lane's OTA URL", SUPPLIED_ENDPOINT)
    return board.check_in(endpoint, board.Identity.of(mac))
