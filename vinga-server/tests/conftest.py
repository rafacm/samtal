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

import contextlib
import itertools
import logging
import os
import shutil
import sys
import time
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
# every tree that predates this file has a full set. So clear them, once
# per process, before the first import of anything under test.
#
# Once per process and not once per run, because under xdist this file
# is executed by the controller and by every worker, each of them a
# process of its own. Two of them clearing while a third imports is
# safe by mechanism rather than by luck: CPython's import machinery
# falls back to compiling from source when reading a cached `.pyc`
# raises `OSError`, so a half-deleted cache costs a recompile and can
# never produce a wrong import.
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

# --- the database this lane runs against --------------------------------
#
# Both lanes need a real Postgres, and the shape of how they get one is
# a performance decision as much as an isolation one. A fresh migrated
# database per test would be correct and would cost a `CREATE DATABASE`
# and two Alembic chains per test, which is seconds each; a shared one
# would be fast and would leak every test's rows into the next. So the
# lane takes the middle: one database per worker process, migrated once,
# with a `TRUNCATE ... RESTART IDENTITY CASCADE` between tests. That
# statement is milliseconds and leaves exactly what a fresh database
# has, so a test still opens on empty tables and identity columns still
# start at 1.
#
# The stamps survive it by construction rather than by an exclusion
# list: the tables truncated are enumerated from the two `schema.py`
# metadata objects, and neither of them carries an `alembic_version`
# table. Truncating those would leave the next opener rerunning a
# baseline against schemas that already have its tables.
#
# Three things make this safe to point at a developer's own machine:
#
#   - Every database this lane makes carries a per-run prefix, generated
#     once by the controller and inherited by the workers, so two pytest
#     runs on one instance never collide and a run's leftovers are
#     identifiable.
#   - `VINGA_DB_URL` is cleared and the `VINGA_DB_*` family is
#     overridden for this process, so an exported production connection
#     in the developer's environment cannot redirect the fixtures.
#   - Every destructive statement checks its target first: a `TRUNCATE`
#     asks `current_database()` and refuses unless it is this worker's
#     own generated name, and a `DROP DATABASE` refuses any name outside
#     this run's prefix.
#
# The lane refuses rather than skips when the instance is unreachable. A
# skip would shrink the suite silently and read green while proving
# nothing about the half of this server that stores things.

DB_URL_ENV = "VINGA_DB_URL"

# The connection the lane uses, and the connection it stops anything
# else from choosing. Defaults match the compose service, so a checkout
# needs `docker compose up -d --wait` and nothing else; CI's service
# container sets them explicitly.
os.environ.pop(DB_URL_ENV, None)
DB_HOST = os.environ.setdefault("VINGA_DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.setdefault("VINGA_DB_PORT", "5432"))
DB_USER = os.environ.setdefault("VINGA_DB_USER", "vinga")
DB_PASSWORD = os.environ.setdefault("VINGA_DB_PASSWORD", "vinga")

# The maintenance database every `CREATE`/`DROP DATABASE` is issued
# from. Never one of ours: a database cannot be dropped from a
# connection inside it.
MAINTENANCE_DATABASE = "postgres"

# What this run calls its databases.
#
# Generated once and passed to the workers through the environment,
# which is the channel xdist gives for free: the controller imports this
# file first and every worker it spawns inherits what it set, so
# `setdefault` yields one prefix per run rather than one per process.
RUN_ENV = "VINGA_TEST_RUN"
RUN = os.environ.setdefault(RUN_ENV, f"{int(time.time())}_{os.getpid()}")
WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")

# A pytest started from inside another pytest, which is what the lane
# guard's own tests do (`pytester` runs a whole session in a
# subprocess). It inherits this process's environment, prefix included,
# so without a name of its own it would provision nothing, truncate the
# outer run's database between its own tests, and drop it at its own
# session finish. `PYTEST_CURRENT_TEST` is set while a test is running
# and is therefore in the environment exactly when a run is nested.
NESTED = "PYTEST_CURRENT_TEST" in os.environ

DATABASE_PREFIX = f"vinga_test_{RUN}"

# Migrated once and cloned, so each worker pays one `CREATE DATABASE`
# instead of two Alembic chains.
TEMPLATE_DATABASE = f"{DATABASE_PREFIX}_template"

# This process's own. Every fixture, every app and every `DatabaseConfig()`
# built in Python during this run points here.
LANE_DATABASE = f"{DATABASE_PREFIX}_{WORKER}" + (f"_nested{os.getpid()}" if NESTED else "")

# What serializes the workers while they provision. `CREATE DATABASE`
# cannot run inside a transaction, so the product's transaction-scoped
# advisory lock cannot coordinate it; this is a session-level lock on an
# autocommit maintenance connection, which is the shape that can.
PROVISIONING_LOCK = 0x76_69_6E_67_00_00_00_09

UNREACHABLE = (
    "the test lane needs a Postgres to run against and could not reach one at "
    f"{DB_HOST}:{DB_PORT}. Start the development instance with "
    "`docker compose up -d --wait` from the repository root, or point "
    "VINGA_DB_HOST, VINGA_DB_PORT, VINGA_DB_USER and VINGA_DB_PASSWORD at an "
    "instance whose role may create databases. The lane refuses rather than "
    "skipping, because a suite that quietly stops exercising storage reads "
    "green while proving nothing"
)


def _maintenance():
    """An autocommit connection on the maintenance database.

    Autocommit because `CREATE DATABASE` and `DROP DATABASE` refuse to
    run inside a transaction, which is also why the lock this lane holds
    while it provisions is session-level rather than the product's
    transaction-scoped one.
    """
    import psycopg

    problem: str | None = None
    try:
        return psycopg.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            dbname=MAINTENANCE_DATABASE,
            autocommit=True,
            connect_timeout=10,
        )
    except Exception:
        # The driver's own message quotes the DSN it tried, password
        # included, and this one is printed by a test runner into a
        # terminal and a CI log. Built here and raised outside the
        # handler, so the chain does not carry it either.
        problem = UNREACHABLE
    raise RuntimeError(problem)


def _application_tables() -> list[str]:
    """Every table the two stores own, schema-qualified, in an order
    `TRUNCATE` accepts.

    Read off the metadata rather than listed, which is what keeps the
    two `alembic_version` tables out of it: they are Alembic's and are
    in neither metadata object, so the stamps survive truncation because
    nothing here can name them.
    """
    from vinga_server.conversations import schema as conversations_schema
    from vinga_server.db import schema as domain_schema

    return [
        f'"{table.schema}"."{table.name}"'
        for metadata in (domain_schema.metadata, conversations_schema.metadata)
        for table in metadata.sorted_tables
    ]


# Whether this process is the one that created its own database, which
# is the only process allowed to drop it. Set by `_provision` below.
#
# Belt as well as braces beside the nested-run name above: a process
# that inherited a prefix and found the database already there has no
# business taking it away from whoever made it, whatever the name says.
_PROVISIONED_HERE = False


def _provision() -> None:
    """This worker's database, cloned from the run's migrated template.

    Under the session-level lock, because every worker runs this at the
    same moment and only one of them may create the template. The loser
    finds it already there and clones it, which is the same answer.
    """
    global _PROVISIONED_HERE
    connection = _maintenance()
    try:
        connection.execute("select pg_advisory_lock(%s)", (PROVISIONING_LOCK,))
        if not _exists(connection, TEMPLATE_DATABASE):
            connection.execute(f'create database "{TEMPLATE_DATABASE}"')
            _migrate(TEMPLATE_DATABASE)
        if not _exists(connection, LANE_DATABASE):
            connection.execute(
                f'create database "{LANE_DATABASE}" template "{TEMPLATE_DATABASE}"'
            )
            _PROVISIONED_HERE = True
    finally:
        connection.execute("select pg_advisory_unlock(%s)", (PROVISIONING_LOCK,))
        connection.close()


def _exists(connection, name: str) -> bool:
    found = connection.execute(
        "select 1 from pg_database where datname = %s", (name,)
    ).fetchone()
    return found is not None


def _migrate(name: str) -> None:
    """Both chains to head in `name`, through the product's own openers.

    The product's, and not a metadata `create_all`: what a test opens
    afterwards has to be the schema a deployment's migration produces,
    including the two version tables, or the lane would be proving a
    shape nothing ships.
    """
    from vinga_server.config.models import DatabaseConfig
    from vinga_server.conversations.store import open_conversations
    from vinga_server.db import open_database

    settings = DatabaseConfig(host=DB_HOST, port=DB_PORT, name=name, user=DB_USER)
    open_database(settings).dispose()
    open_conversations(settings).dispose()


def drop_database(name: str) -> None:
    """One of this run's databases, gone.

    The guard is the whole reason this is a function: `DROP DATABASE`
    runs from outside the database it drops, so `current_database()`
    cannot be the check the way it is for a truncation. What is checked
    instead is the name, against the prefix this run generated, so a
    developer's own database cannot be named here by any path.
    """
    assert name.startswith(DATABASE_PREFIX), (
        f"refusing to drop {name!r}, which is not one of this run's databases"
    )
    connection = _maintenance()
    try:
        connection.execute(f'drop database if exists "{name}" with (force)')
    finally:
        connection.close()


def create_database(name: str, template: str | None) -> None:
    """One throwaway database for a test whose subject is migration.

    `template` names the run's migrated template for a test that wants a
    current store, and None for one whose subject is the fresh migration
    itself, which is created from `template0` and is as blank as a
    database gets. A clone of the migrated template cannot exercise a
    migration that has already run.
    """
    assert name.startswith(DATABASE_PREFIX), (
        f"refusing to create {name!r} outside this run's namespace"
    )
    connection = _maintenance()
    try:
        connection.execute("select pg_advisory_lock(%s)", (PROVISIONING_LOCK,))
        source = template if template is not None else "template0"
        connection.execute(f'create database "{name}" template "{source}"')
    finally:
        connection.execute("select pg_advisory_unlock(%s)", (PROVISIONING_LOCK,))
        connection.close()


def reset_database(name: str) -> None:
    """One of this run's databases, taken back to blank.

    Dropped and made again from `template0`, which is the documented
    reset an operator runs (`dropdb` then `createdb`, then the
    provisioning file again). The lane needs it because the recovery
    case destroys the deployment half way through, and destroying a
    database is not something a connection inside it can do.
    """
    drop_database(name)
    create_database(name, template=None)


def _database_default(name: str) -> None:
    """Point `DatabaseConfig`'s defaults at one database.

    On the model rather than through the environment, because a
    `Config(...)` built in Python reads no environment, and most of both
    lanes composes its configuration exactly that way. The rebuild is
    not optional: pydantic bakes a default into the validator it builds
    at class creation, so the field alone is not where the answer comes
    from.

    The model is imported here and not at module scope because
    everything above has to run before the first import of anything
    under test, which is what the bytecode note and the two secrets are
    about.
    """
    from vinga_server.config.models import DatabaseConfig

    DatabaseConfig.model_fields["host"].default = DB_HOST
    DatabaseConfig.model_fields["port"].default = DB_PORT
    DatabaseConfig.model_fields["user"].default = DB_USER
    DatabaseConfig.model_fields["name"].default = name
    DatabaseConfig.model_rebuild(force=True)


# Both at import, before the first test module is collected: a suite
# that composes its `Config` while it is being imported (two of them do)
# needs the default in place by then, and one that opens a database in a
# module-scoped fixture needs the database itself.
_provision()
_database_default(LANE_DATABASE)


def clear_store() -> None:
    """Everything both stores hold, gone, with the identity counters
    back at one and the migration stamps untouched.

    Public because one caller is not a fixture: the event baseline
    drives every emit path in one test, and two of its drivers open a
    session of the same name, which one database cannot hold at once.
    """
    import psycopg

    connection = psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=LANE_DATABASE,
        autocommit=True,
        # A test that left a writer holding a lock is a defect, and a
        # teardown that waits ten seconds for it hides which test did
        # it behind a slow suite.
        options="-c lock_timeout=5000",
    )
    try:
        current = connection.execute("select current_database()").fetchone()[0]
        assert current == LANE_DATABASE, (
            f"refusing to truncate {current!r}, which is not this worker's "
            f"test database"
        )
        connection.execute(
            "truncate table " + ", ".join(_application_tables()) +
            " restart identity cascade"
        )
    except psycopg.errors.LockNotAvailable as exc:  # pragma: no cover - a defect
        raise AssertionError(
            "the test left a connection holding a lock on the store, so the "
            "lane could not clear it for the next test"
        ) from exc
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def clean_store() -> Iterator[None]:
    """A store with nothing in it, for every test.

    Autouse and per-test, so the isolation is `tmp_path`'s: a test opens
    on empty tables whatever the one before it wrote. At teardown rather
    than setup, so the last test of a run leaves the database clean too,
    which is what makes a leftover row a signal rather than noise.
    """
    yield
    clear_store()


@pytest.fixture
def blank_database() -> Iterator[str]:
    """A database with nothing in it at all: no schemas, no stamps.

    For a test whose subject is the migration itself, which a clone of
    the migrated template cannot exercise. `template0` is the empty
    database Postgres ships for exactly this.
    """
    name = f"{DATABASE_PREFIX}_blank_{os.getpid()}_{next(_THROWAWAY)}"
    create_database(name, template=None)
    try:
        yield name
    finally:
        drop_database(name)


@contextlib.contextmanager
def throwaway_database(template: str | None = TEMPLATE_DATABASE) -> Iterator[str]:
    """One database of a caller's own, made and dropped around a block.

    The fixtures below are this with a scope attached. It is also
    reachable directly, for the one caller that needs more than one per
    test: a lane that runs a deployment script twice wants each run to
    meet an empty store, which is what the throwaway directory used to
    give it.
    """
    name = f"{DATABASE_PREFIX}_own_{os.getpid()}_{next(_THROWAWAY)}"
    create_database(name, template=template)
    try:
        yield name
    finally:
        drop_database(name)


@pytest.fixture(scope="module")
def module_database() -> Iterator[str]:
    """A migrated database for a whole module, cloned from the run's
    template.

    For the lanes that seed once and then run a dozen tests against what
    they seeded: the per-test truncation clears this worker's database,
    which is what keeps ordinary tests independent and is exactly wrong
    for a module that configured a deployment in its first test. A
    database the truncation does not name is the honest way to have
    both.

    Module-scoped rather than session-scoped, so two such modules still
    cannot see each other's rows.
    """
    name = f"{DATABASE_PREFIX}_module_{os.getpid()}_{next(_THROWAWAY)}"
    create_database(name, template=TEMPLATE_DATABASE)
    try:
        yield name
    finally:
        drop_database(name)


@pytest.fixture
def spare_database() -> Iterator[str]:
    """A second migrated database of this test's own, cloned from the
    run's template.

    For a test that needs two stores at once (a decoy server, a second
    deployment) rather than one whose subject is migration.
    """
    name = f"{DATABASE_PREFIX}_spare_{os.getpid()}_{next(_THROWAWAY)}"
    create_database(name, template=TEMPLATE_DATABASE)
    try:
        yield name
    finally:
        drop_database(name)


@pytest.fixture
def packaged_database() -> Iterator[Any]:
    """The shipped defaults back, for a test about what a deployment
    gets rather than about what this lane runs on.

    Autouse fixtures are set up before the ones a test asks for by name,
    so this runs second and has something to put back, and the teardown
    order is the mirror of that.
    """
    from vinga_server.config.models import DatabaseConfig

    shipped = {"host": "127.0.0.1", "port": 5432, "name": "vinga", "user": "vinga"}
    for field, value in shipped.items():
        DatabaseConfig.model_fields[field].default = value
    DatabaseConfig.model_rebuild(force=True)
    try:
        yield DatabaseConfig()
    finally:
        _database_default(LANE_DATABASE)


_THROWAWAY = itertools.count()


def _drop_this_process_databases() -> None:
    """Take this process's databases away with it.

    Every process that provisioned one drops it, and whichever one is
    last drops the template as well: under the provisioning lock, "last"
    is the process that finds nothing else of this run's left. A run
    that is killed outright leaves its databases behind, which is what
    the per-run prefix makes cleanable rather than confusing.

    Only the process that created its database drops it. A run that
    found one already there is a nested session or a hand-exported
    prefix, and taking somebody else's database away at the end of it
    would leave every test after this point without one.
    """
    if not _PROVISIONED_HERE:
        return
    drop_database(LANE_DATABASE)
    connection = _maintenance()
    try:
        connection.execute("select pg_advisory_lock(%s)", (PROVISIONING_LOCK,))
        remaining = connection.execute(
            "select 1 from pg_database where datname like %s and datname <> %s",
            (f"{DATABASE_PREFIX}%", TEMPLATE_DATABASE),
        ).fetchone()
        if remaining is None:
            connection.execute(
                f'drop database if exists "{TEMPLATE_DATABASE}" with (force)'
            )
    finally:
        connection.execute("select pg_advisory_unlock(%s)", (PROVISIONING_LOCK,))
        connection.close()


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
#
# Under xdist there is one ledger per process, which is right for the
# per-test half (the test and its fixtures ran in that process) and not
# enough for the residual half: the run's exit status and the terminal
# summary belong to the controller, which has neither the refusal nor a
# way to hear about it. So a worker's residual is handed up over
# `workeroutput` and the controller does the reporting and the failing,
# in `pytest_testnodedown` and the two hooks below.

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


_RESIDUAL: pytest.StashKey[list[str]] = pytest.StashKey()

# What a worker calls its residual when it hands it up. The controller
# is a separate process with its own empty ledger, so without a channel
# every one of the three process-local pieces above (the ledger, the
# exit status, the terminal summary) would stay inside the worker and a
# residual refusal would vanish: xdist unregisters a worker's terminal
# reporter and derives the run's status from test reports, and a
# residual belongs to no report. `workeroutput` is that channel, and it
# carries strings, so what crosses is the description rather than the
# records.
_RESIDUAL_OUTPUT = "vinga_residual_refusals"


def _note_residual(config: pytest.Config, described: str) -> None:
    """Add one process's residual to what this run will report."""
    config.stash.setdefault(_RESIDUAL, []).append(described)


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Fail the run for refusals no test could be held to.

    A module or session teardown is the ordinary way to reach this, and
    a refusal there is exactly as much of a schema bug as one inside a
    test. Reported against the run rather than dropped, and the run
    fails for it, because a guard whose residual is a printed line
    nobody reads is a guard with a hole in it.

    Three processes reach this, and only one of them owns the run's exit
    status. A worker (the one with a `workeroutput`) hands its residual
    up and stops there. A serial run has no workers and both halves are
    its own. A controller has an empty ledger of its own and whatever
    `pytest_testnodedown` collected from the workers, and it is the
    process whose exit status is the run's, so it is where the failing
    happens in both parallel and serial shape.

    All three also take their own test databases away with them, which
    is the first thing here because it has to happen whichever of the
    three this process is.
    """
    _drop_this_process_databases()
    residual = _LEDGER.unaccounted()
    workeroutput = getattr(session.config, "workeroutput", None)
    if workeroutput is not None:
        if residual:
            workeroutput[_RESIDUAL_OUTPUT] = _LEDGER.described(residual)
        return
    if residual:
        _note_residual(session.config, _LEDGER.described(residual))
    if session.config.stash.get(_RESIDUAL, None):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_testnodedown(node: Any, error: object) -> None:
    """Collect a finished worker's residual, on the controller.

    Called from the distribution session's own loop as each worker
    reports itself finished, which is well before the controller's
    `pytest_sessionfinish` above. The worker is named, because with the
    files split across workers the name is the first thing that narrows
    where the teardown ran.

    Only xdist calls this, so a serial run never enters it and behaves
    exactly as it did before there were workers.
    """
    described = getattr(node, "workeroutput", {}).get(_RESIDUAL_OUTPUT)
    if described:
        _note_residual(node.config, f"{node.gateway.id}: {described}")


def pytest_terminal_summary(
    terminalreporter: Any, exitstatus: int, config: pytest.Config
) -> None:
    """Say so where a reader looks, since a mutated exit status on its
    own says only that something failed.

    On the controller as much as in a serial run: this is the process
    that still has a terminal reporter under xdist, which is why the
    residual has to reach it rather than being printed where it was
    found.
    """
    residual = config.stash.get(_RESIDUAL, None)
    if not residual:
        return
    terminalreporter.section("event schema refusals outside any test")
    terminalreporter.write_line(
        f"the event schema refused an emission where no test owns it: "
        f"{'; '.join(residual)}. A refusal is a schema bug; the likely "
        f"site is a module or session fixture's teardown."
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
