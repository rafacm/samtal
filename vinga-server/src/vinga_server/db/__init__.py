"""The SQLite database holding the domain half of the configuration.

Opening it is one call. `open_database` creates the directory when it
can, configures the connection for concurrent use, and brings the
schema up to date with the Alembic migrations packaged beside this
module. A fresh file migrates from empty to current in one step, so
there is no init command to forget.

It deliberately does no more than that. Verifying that every stored
ciphertext decrypts under the configured keys is a server-startup check
(`verify_secrets`), kept out of here for two reasons. Opening a file is
not judging what is in it: whether a configuration may be served is a
policy about starting, so it is decided once where a start is decided.
And an opener that refused would fail worse than the boot does. A boot
refuses naming the entity and the slot; a database that would not open
is one nothing can migrate, read or repair through this server at
all.

Since #120 there is a second database beside this one, and the
machinery below is written once for both: `open_at`, `database_path`,
`write_engine`, `existing_engine`, `upgrade_to_head` and
`migration_failure` take the filename, the migrations directory and
what that chain has superseded as arguments, and the two functions named
for `vinga.db` supply this database's values. What a caller of the
parameterized half gets is what this one has always had, including the
`ConfigError` sentences: every failure a second database can hit is a
failure to write inside `server.database.dir`, which is the key both of
them live under.

The one thing that is not written once for both is what a database
stamped at a revision its chain no longer has is told. That answer is a
fact of one chain's own history, so it is supplied by the caller that
owns the chain rather than assumed here: the domain database hands in
the revisions its squash deleted and the sentence they earn, and the
conversations database, which has deleted none, hands in nothing and
keeps the ordinary migration-failure sentence.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script.revision import ResolutionError
from alembic.util.exc import CommandError
from sqlalchemy import URL, Engine, create_engine, event, text
from sqlalchemy.exc import OperationalError

from vinga_server.config.loader import ConfigError, DatabaseBusyError, StorageError

DATABASE_FILENAME = "vinga.db"

@dataclass(frozen=True)
class Superseded:
    """What one chain deleted, and what a database still stamped at one
    of those is told.

    A fact of one database's own history, which is why it is declared by
    the caller that owns that history rather than known here. The
    sentence says to throw a file away, so the kind of database it is
    about has to be the kind the sentence is true of: telling the owner
    of a conversations database to re-seed their configuration would be
    advice about the wrong file, and acting on it would delete recorded
    conversations that the store is at pains to erase physically when it
    is asked to.

    `revisions` is closed by construction: it is the list of what a
    squash removed, and no later change can add to it. `sentence` takes
    the database's path as `{path}`.
    """

    revisions: frozenset[str]
    sentence: str


# The domain chain's own answer. The squash (#243) replaced revisions
# 0001 to 0004 with a single baseline, so those four are named one by
# one.
#
# A closed set rather than "any revision this build cannot find", and
# the difference is the whole of the arm below. Two databases produce
# the same Alembic failure and want opposite advice: one written before
# the squash, which cannot be upgraded and has to be replaced, and one
# written by a NEWER build and then met by an older image, which is
# current and must not be touched. Nothing in an unknown revision id
# says which it is; membership here does.
#
# The sentence is the operator-facing whole of "pre-reshape domain
# databases are unsupported": such a database reaches here rather than
# being taken for current and failing later on a column that is gone.
# The next step is the only thing worth saying, because there is no
# other: replace this file and re-seed, which is what the ADR addendum
# records and what the deploy runs.
#
# It names the FILE and not the directory it sits in, which is not
# fussiness: `conversations.db` lives in the same directory under the
# same `server.database.dir`, this change does not touch it, and it
# holds recorded conversations that nothing here has any business
# deleting. An instruction to reset the directory is an instruction to
# destroy it.
DOMAIN_SUPERSEDED = Superseded(
    revisions=frozenset({"0001", "0002", "0003", "0004"}),
    sentence=(
        "the database at {path} is stamped at a revision this build does not carry, "
        "which is what a domain database written before the storage reshape looks "
        "like: its migration chain was replaced by a single baseline and cannot be "
        "upgraded in place. Delete that file, together with the -wal and -shm files "
        "beside it, and re-seed the configuration; keep any conversations.db in the "
        "same directory, which this change does not touch. "
        "docs/adr/2026-08-20-database-upgrades-have-a-compatibility-floor.md records "
        "the decision and what it costs"
    ),
)

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

    Which ConfigError matters to the callers that open at startup: boot,
    the CLI per command, and the lifespan that owns the configuration
    API's engine (the server's when the API is mounted, the API's own
    when it runs standalone, once each since #142). A lock another
    writer is holding is met here rather than inside a repository write,
    and must still be the retryable refusal, because that is what makes
    a locked database a startup that refused with a sentence; a
    directory the server cannot write is the server's problem wherever
    it is found. The messages are the same ones this has always
    raised."""
    return open_at(
        directory, DATABASE_FILENAME, _MIGRATIONS_DIR, superseded=DOMAIN_SUPERSEDED
    )


def open_at(
    directory: str | Path,
    filename: str,
    migrations: Path,
    secure_delete: bool = False,
    superseded: "Superseded | None" = None,
) -> Engine:
    """`open_database` for a database named by its caller: create the
    directory, open the file, run its own migration chain to head.

    The migrations directory is an argument rather than derived from the
    filename because the two databases keep their chains beside their
    own packages, and their version tables are separate by virtue of
    being separate files rather than by any naming trick.

    `secure_delete` is off here and on for the conversations database:
    it costs a write of zeros over freed pages, which is the price of
    deletion being physical rather than an entry removed from an index,
    and the domain configuration has no right-to-delete to honor.

    `superseded` is what this chain has deleted and what a database
    still stamped at one of those is told, and it defaults to nothing
    for the reason `Superseded` gives: a chain that has deleted no
    revision has no such database to answer, and a sentence about
    re-seeding a configuration is the wrong advice about any file but
    one.
    """
    path = database_path(Path(directory), filename)
    engine = write_engine(path, secure_delete=secure_delete)
    # Built inside the handler and raised outside it: `from exc` (and
    # `from None`) leave the library's exception reachable from the one
    # that travels out, and a SQLAlchemy error holds the statement it
    # failed on together with its bound parameters.
    problem: ConfigError | None = None
    try:
        upgrade_to_head(engine, migrations)
    except ConfigError:
        engine.dispose()
        raise
    except Exception as exc:
        engine.dispose()
        problem = migration_failure(exc, path, superseded)
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
    return existing_engine(Path(directory) / DATABASE_FILENAME)


def existing_engine(path: Path) -> Engine:
    """An engine for a database file that has to be there already, which
    is what `read_engine` is.

    It reads and takes no lock (`BEGIN`), and it creates nothing, which
    is the property its callers need and the reason the database is named
    as a URI. It took a write lock and overwrote freed pages for one
    other caller, the conversations purge; that command is gone (#282)
    and its arguments went with it, so what is left is one shape with no
    options rather than a shape with two nobody passes.
    """
    # Percent-encoded because this is a URI now: a `?` or a `#` in the
    # path would otherwise end it, and the open would land somewhere
    # else entirely. `quote` leaves the separators alone.
    name = quote(str(path))
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
    def _begin(connection: object) -> None:
        # Spelled out rather than left to the default, because a read
        # taking no lock is the reason this engine exists at all and it
        # is not what the rest of the project does. Under WAL a deferred
        # transaction that only reads takes no lock and reads the last
        # committed snapshot, so a write in progress cannot stall it.
        connection.exec_driver_sql("BEGIN")  # type: ignore[attr-defined]

    return engine


def migration_failure(
    exc: Exception, path: Path, superseded: "Superseded | None" = None
) -> ConfigError:
    """The lock that did not arrive inside the busy timeout, told from
    everything else. The distinction is the only one a caller answering
    with a status code needs, and it is made on the driver's own message
    rather than on ours, which never changes.

    That same driver line is what the message carries, deliberately:
    "database is locked" and "unable to open database file" are what
    tell an operator which problem this is. Never SQLAlchemy's own text,
    which wraps the line in the statement and the parameters bound to
    it.

    One failure is not the driver's at all and is answered on its own:
    a database stamped at a revision the squash deleted. Alembic raises
    its own `CommandError` for it, and that exception carries no `orig`,
    so the sentence above would have reported the class name and nothing
    else.

    Narrow on purpose, because the sentence it answers with says to
    throw a database away. Four things have to hold before it is said,
    and each of them rules out a case that would be told to destroy
    something it should keep. The caller has to have handed in a
    `superseded`, so a database whose chain deleted nothing is never
    told its chain deleted something. It has to be a `CommandError`,
    which a driver failure is not. Its cause has to be Alembic's own
    `ResolutionError`, which is the stored revision not being findable,
    rather than an unreadable script directory or a chain with two
    heads. And the revision it could not find has to be one that chain
    is known to have deleted: a database stamped at a revision from a
    NEWER build, met by an image that was rolled back, produces exactly
    the same `ResolutionError`, and it is current rather than stranded,
    so it falls through to the sentence above and its operator rolls
    forward instead of deleting a live volume.

    The stored revision is read off the cause rather than out of the
    file, since Alembic has already found it, and it is never quoted
    back: it is a value in a file nothing here validates.
    """
    if superseded is not None and _stranded(exc, superseded):
        return StorageError(superseded.sentence.format(path=path))
    detail = str(getattr(exc, "orig", "")) or type(exc).__name__
    problem = (
        f"cannot migrate the database at {path}: {detail}; "
        f"server.database.dir names the directory it lives in"
    )
    if isinstance(exc, OperationalError) and ("locked" in detail or "busy" in detail):
        return DatabaseBusyError(problem)
    return StorageError(problem)


def _stranded(exc: Exception, superseded: Superseded) -> bool:
    """Whether this failure is a database left behind by a squash of
    this chain, which is the one failure answered by throwing the file
    away.

    The cause chain is walked rather than only its first link, because
    what makes this the right question is Alembic's `ResolutionError`
    being in it at all; which library happened to wrap it is not this
    module's business to depend on.
    """
    if not isinstance(exc, CommandError):
        return False
    seen: set[int] = set()
    cause: BaseException | None = exc.__cause__
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, ResolutionError):
            return cause.argument in superseded.revisions
        cause = cause.__cause__
    return False


def database_path(directory: Path, filename: str = DATABASE_FILENAME) -> Path:
    """The database file inside `directory`, with the directory created
    when that is possible. The error names the configuration key rather
    than the path alone: the default is /var/lib/vinga, which a
    development machine is not going to let anybody write to, and what
    the reader needs is which key to move."""
    problem: str | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        problem = (
            f"cannot create the database directory {directory}: {exc.strerror}; "
            f"set server.database.dir (or VINGA_SERVER__DATABASE__DIR) to a "
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
            f"server.database.dir (or VINGA_SERVER__DATABASE__DIR) to a "
            f"writable path"
        )
    return directory / filename


def write_engine(path: Path, secure_delete: bool = False) -> Engine:
    """The engine a database is created, migrated and written through.

    `secure_delete` overwrites a freed page with zeros instead of
    leaving its bytes in the freelist. Off by default, because it is
    paid on every delete and the domain configuration has nothing to
    erase; on for the conversations database, where a retention pass that
    left the words in the file would not be a deletion."""
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
            if secure_delete:
                cursor.execute("PRAGMA secure_delete=ON")
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


def upgrade_to_head(engine: Engine, migrations: Path = _MIGRATIONS_DIR) -> None:
    with engine.connect() as connection:
        # Take the lock before Alembic looks at the version table: the
        # loser of a race then reads the schema the winner committed and
        # finds it current. Alembic sees a connection already in a
        # transaction, leaves transaction control alone, and the commit
        # below is what ends it.
        connection.execute(text("SELECT 1"))
        config = AlembicConfig()
        config.set_main_option("script_location", str(migrations))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()


__all__ = [
    "BUSY_TIMEOUT_MS",
    "DATABASE_FILENAME",
    "DOMAIN_SUPERSEDED",
    "Superseded",
    "database_path",
    "existing_engine",
    "migration_failure",
    "open_at",
    "open_database",
    "read_engine",
    "upgrade_to_head",
    "write_engine",
]
