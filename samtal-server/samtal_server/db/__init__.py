"""The SQLite database holding the domain half of the configuration.

Opening it is one call. `open_database` creates the directory when it
can, configures the connection for concurrent use, and brings the
schema up to date with the Alembic migrations packaged beside this
module. A fresh file migrates from empty to current in one step, so
there is no init command to forget.

It deliberately does no more than that. Verifying that every stored
ciphertext decrypts under the configured keys is a server-startup check
(`verify_secrets`), kept out of here: a missing key, a wrong key, or a
corrupt token is exactly when the CLI is the recovery tool, and a
database that refused to open would take the recovery tool away with
the server.
"""

import os
from pathlib import Path
from urllib.parse import quote

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import URL, Engine, create_engine, event, text
from sqlalchemy.exc import OperationalError

from samtal_server.config.loader import ConfigError, DatabaseBusyError, StorageError

DATABASE_FILENAME = "samtal.db"

# How long a connection waits for another one's write lock before it
# gives up. A CLI write while the server holds the file open is the
# case this exists for; two processes opening a fresh database wait on
# each other here too, rather than racing the baseline migration.
BUSY_TIMEOUT_MS = 10_000

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def open_database(directory: str | Path) -> Engine:
    """Open (creating if needed) and migrate the database in `directory`.

    Returns an engine the caller owns and disposes. Every failure is a
    ConfigError naming `server.database.dir`, because the answer to all
    of them is to point that key somewhere writable.

    Which ConfigError matters to one caller only: the API opens the
    database on every request, so a lock timeout that happens here
    rather than inside a repository write must still be the retryable
    refusal, and a directory the server cannot write is still the
    server's problem and not the request's. The messages are the same
    ones this has always raised."""
    path = _database_path(Path(directory))
    engine = _create_engine(path)
    # Built inside the handler and raised outside it: `from exc` (and
    # `from None`) leave the library's exception reachable from the one
    # that travels out, and a SQLAlchemy error holds the statement it
    # failed on together with its bound parameters.
    problem: ConfigError | None = None
    try:
        _upgrade_to_head(engine)
    except ConfigError:
        engine.dispose()
        raise
    except Exception as exc:
        engine.dispose()
        problem = _failure(exc, path)
    if problem is not None:
        raise problem
    return engine


def read_engine(directory: str | Path) -> Engine:
    """An engine for reading a database somebody else has already
    migrated, which on a running server means the one boot opened.

    Everything `open_database` does beyond creating an engine is exactly
    what a device path must not do. It migrates, which is an Alembic
    round trip; it takes the write lock before it reads, so a lookup
    would queue behind whichever writer holds it for up to the busy
    timeout; and it creates the directory, so a lookup against a
    misconfigured path would leave a database behind rather than fail.
    This does none of the three: no migration, ordinary deferred read
    transactions, and nothing created but the engine.

    Deferred rather than immediate is the whole point. Under WAL a
    deferred transaction that only reads takes no lock at all and reads
    the last committed snapshot, so a CLI or API write in progress
    cannot stall a device asking which agent it may talk to.

    Creating nothing is not a matter of care either: an ordinary SQLite
    filename creates the file when it is missing, whoever opens it, so
    the database is named as a URI with `mode=rw`, which opens an
    existing file for reading and writing and refuses a missing one. WAL
    needs that much write access (a reader maps the `-shm` index and may
    extend it), so read-only would be the wrong mode as well as a
    stronger claim than this makes: what is promised here is that no
    lookup brings a database into existence, and the caller's fallback
    is what a missing one produces.

    The caller owns the engine and disposes it. The file is not opened
    here: SQLAlchemy connects lazily, so a database that cannot be read
    is a failure at the first lookup, where the caller can fall back,
    rather than at app build. That laziness is also why the mode matters
    rather than a check at construction: a volume can go away between
    the two, and the answer must not be a new empty database.
    """
    # Percent-encoded because this is a URI now: a `?` or a `#` in the
    # path would otherwise end it, and the open would land somewhere
    # else entirely. `quote` leaves the separators alone.
    name = quote(str(Path(directory) / DATABASE_FILENAME))
    engine = create_engine(
        # Echo off for the reason it is off above: a statement log is a
        # place values end up.
        URL.create(
            "sqlite+pysqlite", database=f"file:{name}?mode=rw", query={"uri": "true"}
        ),
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _configure_connection(dbapi_connection: object, _record: object) -> None:
        # Transaction control to SQLAlchemy, so the BEGIN below is the
        # one that happens. Without it pysqlite opens transactions of
        # its own and each SELECT would read its own snapshot, which is
        # what would let a lookup see a device's binding from before a
        # write and the default agent from after it.
        dbapi_connection.isolation_level = None  # type: ignore[attr-defined]
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            # No journal_mode pragma: the database is already WAL, set
            # by whoever created it, and it is a property of the file
            # rather than of a connection. Setting it from here would be
            # a write from the read path.
            cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()

    @event.listens_for(engine, "begin")
    def _begin_deferred(connection: object) -> None:
        # Spelled out rather than left to the default, because the
        # default for this project is the immediate one above and the
        # difference is the reason this engine exists.
        connection.exec_driver_sql("BEGIN")  # type: ignore[attr-defined]

    return engine


def _failure(exc: Exception, path: Path) -> ConfigError:
    """The lock that did not arrive inside the busy timeout, told from
    everything else. The distinction is the only one a caller answering
    with a status code needs, and it is made on the driver's own message
    rather than on ours, which never changes.

    That same driver line is what the message carries, deliberately:
    "database is locked" and "unable to open database file" are what
    tell an operator which problem this is. Never SQLAlchemy's own text,
    which wraps the line in the statement and the parameters bound to
    it."""
    detail = str(getattr(exc, "orig", "")) or type(exc).__name__
    problem = (
        f"cannot migrate the database at {path}: {detail}; "
        f"server.database.dir names the directory it lives in"
    )
    if isinstance(exc, OperationalError) and ("locked" in detail or "busy" in detail):
        return DatabaseBusyError(problem)
    return StorageError(problem)


def _database_path(directory: Path) -> Path:
    """The database file inside `directory`, with the directory created
    when that is possible. The error names the configuration key rather
    than the path alone: the default is /var/lib/samtal, which a
    development machine is not going to let anybody write to, and what
    the reader needs is which key to move."""
    problem: str | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        problem = (
            f"cannot create the database directory {directory}: {exc.strerror}; "
            f"set server.database.dir (or SAMTAL_SERVER__DATABASE__DIR) to a "
            f"writable path"
        )
    if problem is not None:
        raise StorageError(problem)
    # mkdir(exist_ok=True) says nothing about an existing directory being
    # writable, and the first write would otherwise fail somewhere deep
    # inside a migration.
    if not os.access(directory, os.W_OK):
        raise StorageError(
            f"the database directory {directory} is not writable; set "
            f"server.database.dir (or SAMTAL_SERVER__DATABASE__DIR) to a "
            f"writable path"
        )
    return directory / DATABASE_FILENAME


def _create_engine(path: Path) -> Engine:
    # Statement echo off and parameter logging never enabled, so a
    # secret bound into an INSERT cannot ride a debug log line. Echo is
    # off by default; it is named here because turning it on for a
    # debugging session would be a leak, not a convenience.
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path)),
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _configure_connection(dbapi_connection: object, _record: object) -> None:
        # pysqlite opens a transaction of its own before DML, which both
        # hides the BEGIN below and makes the journal_mode pragma a
        # no-op. Setting isolation_level to None hands transaction
        # control to SQLAlchemy, and with it to the begin listener.
        dbapi_connection.isolation_level = None  # type: ignore[attr-defined]
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()

    @event.listens_for(engine, "begin")
    def _begin_immediate(connection: object) -> None:
        # Every transaction takes the write lock before it reads. A
        # deferred BEGIN takes it only at the first write, which is what
        # lets two writers each validate against the pre-change snapshot
        # and then persist over one another, and what would let two
        # openers both decide the baseline migration still needs
        # running.
        connection.exec_driver_sql("BEGIN IMMEDIATE")  # type: ignore[attr-defined]

    return engine


def _upgrade_to_head(engine: Engine) -> None:
    with engine.connect() as connection:
        # Take the lock before Alembic looks at the version table: the
        # loser of a race then reads the schema the winner committed and
        # finds it current. Alembic sees a connection already in a
        # transaction, leaves transaction control alone, and the commit
        # below is what ends it.
        connection.execute(text("SELECT 1"))
        config = AlembicConfig()
        config.set_main_option("script_location", str(_MIGRATIONS_DIR))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()


__all__ = ["BUSY_TIMEOUT_MS", "DATABASE_FILENAME", "open_database", "read_engine"]
