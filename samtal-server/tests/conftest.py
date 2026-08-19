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

import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

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
# samtal-server` or a bare `python -c "import samtal_server..."`, and
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
for _tree in (_ROOT / "src" / "samtal_server", _ROOT / "tests"):
    for _cache in _tree.rglob("__pycache__"):
        shutil.rmtree(_cache, ignore_errors=True)

AUTH_SECRET_ENV = "SAMTAL_AUTH_SECRET"

# Not a secret: a fixed value, so a failing test is reproducible.
TEST_AUTH_SECRET = "test-secret-" + "0123456789abcdef" * 2

os.environ.setdefault(AUTH_SECRET_ENV, TEST_AUTH_SECRET)

# The configuration API is always mounted and always gated, so a server
# with no token in the environment refuses to boot the same way. The
# same shape as the auth secret above, and set the same way rather than
# through an autouse fixture, for the same reason: a module that builds
# an app while it is being imported needs it before collection, not
# before the first test runs.
API_SECRET_ENV = "SAMTAL_API_SECRET"

TEST_API_SECRET = "test-api-token-" + "fedcba9876543210" * 2

os.environ.setdefault(API_SECRET_ENV, TEST_API_SECRET)

# Every event these lanes drive is held to its declaration (#155), and a
# violation raises rather than being recovered from. The emitters
# default to that already, so this is not what makes the lanes strict;
# what it makes is a lane that STAYS strict when it builds an app.
# `create_app` resolves this variable, and an unset one means forgiving
# there, deliberately: a running server is a deployment however it was
# launched. Set rather than defaulted, unlike the two secrets above,
# because an ambient `forgiving` on a CI runner would quietly relax
# every app-building lane in the suite, which is the one thing this
# variable must not be able to do.
ENFORCEMENT_ENV = "SAMTAL_EVENTS_ENFORCEMENT"

os.environ[ENFORCEMENT_ENV] = "strict"

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
# `SAMTAL_SERVER__DATABASE__DIR` is the deployment's way in and reaches
# only configuration loaded from a file.
PACKAGED_DATABASE_DIR = Path("/var/lib/samtal")

SHARED_DATABASE_DIR = Path(tempfile.mkdtemp(prefix="samtal-tests-"))


def _database_default(directory: Path) -> None:
    """Point `server.database.dir`'s default at `directory`.

    The model is imported here and not at module scope because
    everything above has to run before the first import of anything
    under test, which is what the bytecode note and the two secrets are
    about. The rebuild is not optional: pydantic bakes a default into
    the validator it builds at class creation, so the field alone is not
    where the answer comes from.
    """
    from samtal_server.config.models import DatabaseConfig

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
    directory = tmp_path / "samtal-db"
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
