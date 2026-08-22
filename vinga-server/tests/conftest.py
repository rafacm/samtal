"""What every lane needs before it can build a server.

Device authentication is on by default, and an enabled auth with no
secret in the environment is a boot failure, so a lane that builds an
app needs a secret. Setting one here rather than turning auth off keeps
every lane running the way a real deployment does: real tokens issued
by the OTA endpoint and checked at the websocket handshake.

An already-exported secret wins, which is what lets the smoke lane point
at a container and sign with the secret that container was started with.

Set at import time rather than in a fixture: a test module that builds
the app while it is being imported (the boot test does) needs the
secret before collection, not before the first test runs.

The bytecode setting below is here for the same reason: it has to be in
place before pytest imports the first test module.
"""

import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# A cached `.pyc` records the source's size and its mtime in whole
# seconds, and CPython accepts the cache when both are *equal* to the
# source's current values. So any edit that keeps the byte count and
# leaves the mtime on the second it was compiled on is invisible. Two
# ordinary operations here do exactly that:
#
#   - Checking a regression test really fails without its fix. Reverting
#     usually swaps two statements, which preserves the byte count, and
#     a scripted revert-run-restore finishes inside one second.
#   - Restoring a file from a backup, which carries the backup's mtime
#     rather than the current time, landing back on the compiled second.
#
# The result is a tree that lies about what it is running. It cost half
# an hour on #13, where a restored fix ran as its pre-fix version.
#
# Writing no bytecode removes the cache, and with it the stale cache. It
# covers pytest's assertion-rewritten test bytecode too, which uses the
# same check and matters as much because test files are edited
# constantly. Measured cost is noise: the expensive imports live in
# site-packages and keep their own bytecode.
sys.dont_write_bytecode = True

# The flag stops writes, not reads: a cache that already exists is still
# consulted, and is now never refreshed, which would leave a stale one
# stale forever. Caches do get written outside pytest, by `uv run
# vinga-server` or a bare `python -c "import vinga_server..."`, and
# every tree that predates this file has a full set. So clear them, once,
# before the first import of anything under test.
#
# This also covers the one file the flag cannot: pytest writes a
# conftest's rewritten bytecode *before* it executes the body that sets
# the flag, so by now this run has already cached this file. Clearing
# leaves the next run nothing stale to read.
#
# That leaves one residual, which cannot be closed from inside the file
# that would have to close it: a run whose *own* conftest cache was
# already stale on entry reads it before reaching this line. It is one
# run wide and self-healing, because from here on no run ever ends with
# a conftest cache on disk to go stale. Closing it properly would mean
# clearing before pytest starts, which means a wrapper everyone has to
# remember, which is the thing this file exists to avoid.
#
# Only these two trees, never `.venv`: site-packages bytecode is
# legitimate, expensive to rebuild, and its sources do not get edited.
_ROOT = Path(__file__).resolve().parent.parent
for _tree in (_ROOT / "src" / "vinga_server", _ROOT / "tests"):
    for _cache in _tree.rglob("__pycache__"):
        shutil.rmtree(_cache, ignore_errors=True)

AUTH_SECRET_ENV = "VINGA_AUTH_SECRET"

# Not a secret: a fixed value, so a failing test is reproducible.
TEST_AUTH_SECRET = "test-secret-" + "0123456789abcdef" * 2

os.environ.setdefault(AUTH_SECRET_ENV, TEST_AUTH_SECRET)

# The configuration API is always mounted and always gated, so a server
# with no token in the environment refuses to boot the same way. The
# same shape as the auth secret above, and set the same way rather than
# through an autouse fixture, for the same reason: a module that builds
# an app while it is being imported needs it before collection, not
# before the first test runs.
API_SECRET_ENV = "VINGA_API_SECRET"

TEST_API_SECRET = "test-api-token-" + "fedcba9876543210" * 2

os.environ.setdefault(API_SECRET_ENV, TEST_API_SECRET)

# Where a database goes when nobody said.
#
# Most of both lanes composes a `Config` in memory and names no
# directory, so `server.database.dir` falls to the packaged default,
# which no development machine or runner can write. That used to be
# harmless: nothing on the serving path opened the configuration
# database, and the one thing that looked at it tolerated its absence.
# Since #142 the lifespan opens and migrates it once at startup, so every
# app a test enters needs a directory it can create, and a lane that
# wrote to the packaged one would be writing to a deployment's data
# volume.
#
# So the default is moved, in two steps. Here, once, for the whole run,
# because a test module that composes its `Config` at import time (two
# integration suites do) has it before any fixture could run; and again
# per test in the fixture below, so that a test which does reach a
# database gets one of its own.
#
# On the model rather than through the environment, because a
# `Config(...)` built in Python reads no environment:
# `VINGA_SERVER__DATABASE__DIR` is the deployment's way in and reaches
# only configuration loaded from a file.
PACKAGED_DATABASE_DIR = Path("/var/lib/vinga")

SHARED_DATABASE_DIR = Path(tempfile.mkdtemp(prefix="vinga-tests-"))


def _database_default(directory: Path) -> None:
    """Point `server.database.dir`'s default at `directory`.

    The model is imported here and not at module scope because
    everything above has to run before the first import of anything
    under test, which is what the bytecode note and the two secrets are
    about. The rebuild is not optional: pydantic bakes a default into
    the validator it builds at class creation, so the field alone is not
    where the answer comes from.
    """
    from vinga_server.config.models import DatabaseConfig

    DatabaseConfig.model_fields["dir"].default = directory
    DatabaseConfig.model_rebuild(force=True)


_database_default(SHARED_DATABASE_DIR)


@pytest.fixture(autouse=True)
def writable_database_dir(tmp_path: Path) -> Iterator[Path]:
    """Somewhere this test may keep a database of its own.

    Autouse and per-test, so the isolation is the same as `tmp_path`'s: a
    test that names its own directory is unaffected, and one that does
    not gets a fresh empty one rather than sharing the run's.

    Ask for `packaged_database_dir` to get the shipped default back.
    """
    directory = tmp_path / "vinga-db"
    _database_default(directory)
    yield directory
    _database_default(SHARED_DATABASE_DIR)


@pytest.fixture
def packaged_database_dir() -> Iterator[Path]:
    """Undo the fixture above, for a test about what a deployment gets.

    Autouse fixtures are set up before the ones a test asks for by name,
    so this runs second and has something to put back, and the teardown
    order is the mirror of that.
    """
    _database_default(PACKAGED_DATABASE_DIR)
    yield PACKAGED_DATABASE_DIR


# --- the lane guard -----------------------------------------------------
#
# A refused emission is dropped after one report on the emitter's own
# channel (#239), which is the right answer in production and a terrible
# one in a lane: a malformed emission would cost a green suite nothing
# at all and be discovered in a deployment's logs. So the lanes read
# that report as a failure. This, and not a mode the emitters run in, is
# what keeps them loud.
#
# The handler is installed ONCE, from `pytest_configure`, and that is
# the whole design decision here. A function-scoped fixture is only live
# between its own setup and teardown, and pytest builds higher-scoped
# fixtures first and tears them down last, so a handler it installed
# would miss exactly the runs worth policing: the module-scoped fixture
# that drives all eighty-one emit paths, the integration lane's
# module-scoped uvicorn boot, and anything a module teardown emits.
# Strict mode used to cover those because it raised from inside the
# emitter, at whatever scope was running, and a replacement that covers
# less is not a replacement.
#
# So the ledger below runs for the whole session and the per-test
# fixture reads a DELTA off it. A refusal made in a module fixture's
# setup is attributed to the test that first asked for that fixture,
# which is the test whose failure a reader can act on. What is left over
# at the end of the run belongs to no test at all (a session or module
# teardown, an atexit hook) and is reported against the run itself
# rather than dropped.

# The root of every channel this server emits on. Records propagate to
# their ancestors, so one handler here sees every subsystem's, which is
# what makes the guard one handler rather than one per channel.
EVENT_CHANNEL_ROOT = "vinga_server"

REFUSALS_EXPECTED = "refusals_are_expected"


class _Ledger:
    """Every refusal report this run has made, and how much of it has
    been accounted for.

    An append-only list and a high-water mark rather than a list the
    per-test check empties, because what is unaccounted for at the end
    of the run is a fact worth reporting and an emptied list cannot
    state it.
    """

    def __init__(self) -> None:
        self.refused: list[logging.LogRecord] = []
        self.accounted = 0

    def unaccounted(self) -> list[logging.LogRecord]:
        """What has been refused since the last time this was asked, and
        marked as accounted for by the asking."""
        fresh = self.refused[self.accounted :]
        self.accounted = len(self.refused)
        return fresh

    def described(self, records: list[logging.LogRecord]) -> str:
        return "; ".join(f"{one.name}: {one.args}" for one in records)


_LEDGER = _Ledger()


class _Watch(logging.Handler):
    """The handler that fills the ledger.

    The message is matched unrendered, which is what makes the check
    exact: `msg` is the fixed template the emitter reports with, and
    every other record on these channels is either an event or a
    different sentence.
    """

    def __init__(self, message: str) -> None:
        super().__init__(level=logging.NOTSET)
        self._message = message

    def emit(self, record: logging.LogRecord) -> None:
        if record.msg == self._message:
            _LEDGER.refused.append(record)


def pytest_configure(config: pytest.Config) -> None:
    """Arm the guard before anything is collected, so that no fixture
    scope, and no import a test module does at collection time, runs
    outside it."""
    from vinga_server.events import REFUSAL_MESSAGE

    logging.getLogger(EVENT_CHANNEL_ROOT).addHandler(_Watch(REFUSAL_MESSAGE))


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Fail the run for refusals no test could be held to.

    A module or session teardown is the ordinary way to reach this, and
    a refusal there is exactly as much of a schema bug as one inside a
    test. Reported against the run rather than dropped, and the run
    fails for it, because a guard whose residual is a printed line
    nobody reads is a guard with a hole in it.
    """
    residual = _LEDGER.unaccounted()
    if not residual:
        return
    session.config.stash[_RESIDUAL] = _LEDGER.described(residual)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


_RESIDUAL: pytest.StashKey[str] = pytest.StashKey()


def pytest_terminal_summary(
    terminalreporter: Any, exitstatus: int, config: pytest.Config
) -> None:
    """Say so where a reader looks, since a mutated exit status on its
    own says only that something failed."""
    residual = config.stash.get(_RESIDUAL, None)
    if residual is None:
        return
    terminalreporter.section("event schema refusals outside any test")
    terminalreporter.write_line(
        f"the event schema refused an emission where no test owns it: "
        f"{residual}. A refusal is a schema bug; the likely site is a "
        f"module or session fixture's teardown."
    )


@pytest.fixture
def refusals_are_expected() -> None:
    """Ask the guard to let this test's refusals through.

    Requested by name, and by the tests that drive the refusal path on
    purpose: the sentinel suite, which exists to prove what a refusal
    may say, and the emit-path pins next door. Everything else in both
    lanes is held to emitting nothing the schema would refuse.
    """


@pytest.fixture(autouse=True)
def no_unexpected_refusals(request: pytest.FixtureRequest) -> Iterator[None]:
    """Fail any test whose run made an emitter refuse an emission.

    The check is at teardown and reads everything unaccounted for, not
    only what arrived after this fixture's own setup: a refusal made
    while a module-scoped fixture was being built belongs to the test
    that asked for it, and that fixture was built before this one.
    """
    yield
    unaccounted = _LEDGER.unaccounted()
    if unaccounted and REFUSALS_EXPECTED not in request.fixturenames:
        pytest.fail(
            f"the event schema refused {len(unaccounted)} emission(s) in "
            f"this test or in a fixture it asked for: "
            f"{_LEDGER.described(unaccounted)}. A refusal is a schema bug. "
            f"If the test drives one on purpose, request the "
            f"`{REFUSALS_EXPECTED}` fixture."
        )
