"""The Postgres database holding everything this server stores.

Opening it is one call. `open_database` builds an engine from the
connection settings, creates the schema its chain lives in when it is
missing, and brings that chain up to date with the Alembic migrations
packaged beside this module. A blank database migrates to current in one
step, so there is no init command to forget.

It deliberately does no more than that. Verifying that every stored
ciphertext decrypts under the configured keys is a server-startup check
(`verify_secrets`), kept out of here for two reasons. Opening a database
is not judging what is in it: whether a configuration may be served is a
policy about starting, so it is decided once where a start is decided.
And an opener that refused would fail worse than the boot does. A boot
refuses naming the entity and the slot; a database that would not open
is one nothing can migrate, read or repair through this server at all.

There are three stores and one database. What keeps them apart is a
schema each, which is also where each Alembic chain keeps its own
version table and what the read-only analyst role is scoped to (it
reads the conversation record and neither of the others). A
`StoreChain` is the whole of what a store tells this module about
itself: which schema it owns, where its migrations are, and which
advisory-lock key serializes its writers. The domain chain is declared
below, beside the opener that takes it; the conversations and memory
chains are declared beside their own stores, because a chain is a fact
of the store that owns it.

Three properties every caller gets and none of them states:

- **Writers to one store serialize whole.** The write engine's begin
  listener takes the chain's transaction-scoped advisory lock before
  anything is read, so validation and the persist that follows it
  happen under one lock and two writers cannot each validate against
  the state before the other's change and then write over one another.
  A migration takes the same lock before Alembic reads the version
  table, which is how two starting processes settle a baseline race.
- **Every connection's lock wait is bounded.** `lock_timeout` is a
  connection parameter, set on the startup options rather than by a
  statement, so it survives the rollback a pooled connection is
  returned with. A lock that does not arrive inside it fails with
  `LockNotAvailable`, which this module classifies as retryable, which
  is what makes a contended write a 409 with a sentence rather than a
  wait with no end.
- **Nothing about the connection is ever quoted back.** The refusals
  below are fixed strings. A driver's own connection failure quotes the
  DSN it tried, a URL can carry a password in its authority and in its
  query (`sslpassword`), and the discrete values are no better, so
  none of the four travel: not in a message, not in `args`, and not on
  a cause chain, which is why every raise here is built inside its
  handler and raised outside it.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script.revision import ResolutionError
from alembic.util.exc import CommandError
from sqlalchemy import URL, Engine, create_engine, event, make_url, text

from vinga_server.config.loader import ConfigError, DatabaseBusyError, StorageError
from vinga_server.config.models import DatabaseConfig
from vinga_server.db import schema

# How long a connection waits for another one's lock before it gives up.
# A CLI write while the server holds the advisory lock is the case this
# exists for; two processes migrating a fresh database wait on each
# other here too, rather than racing the baseline.
#
# Per lock acquisition, which is what Postgres applies it to, and not a
# bound on a transaction or on a response: a transaction that waits this
# long on the advisory gate can wait again on a later lock, and its
# execution time was never bounded under any backend. The gate is the
# acquisition that matters, because every writer takes it first.
LOCK_TIMEOUT_MS = 10_000

# The environment the connection is read from. Four of the five names
# have a YAML key beside them (`DatabaseConfig`); the password has none
# at all, because a password in a config file is what the
# no-secrets-in-YAML stance exists to prevent, and the URL has none
# because it is the whole of the five at once.
URL_ENV = "VINGA_DB_URL"
PASSWORD_ENV = "VINGA_DB_PASSWORD"

# What a development machine gets when it says nothing, matching the
# compose file's own default so the zero-configuration loop is
# `docker compose up -d --wait` and nothing else. A convenience on an
# instance bound to loopback, which the deployment docs say plainly.
DEFAULT_PASSWORD = "vinga"

# The one dialect this server speaks, and the schemes a URL may name it
# with. `postgresql` alone selects psycopg2, which is not installed and
# is not the driver this project chose, so it is accepted and normalized
# rather than refused: what an operator writes is the scheme Postgres
# itself documents.
DIALECT = "postgresql+psycopg"
ACCEPTED_SCHEMES = frozenset({"postgresql", DIALECT})

# The refusals. Fixed and value-free, every one of them, for the reason
# the module docstring gives: what would be quoted back is a connection
# string, and a connection string is where a password lives.
URL_REFUSED = (
    f"{URL_ENV} does not name a Postgres database. It has to be a postgresql:// or "
    f"postgresql+psycopg:// URL and nothing else: this server keeps both halves of "
    f"its state in Postgres and carries no other driver. Neither the value nor any "
    f"part of it is quoted back, because a database URL carries a password in its "
    f"authority and can carry another in its query"
)

UNREACHABLE = (
    "cannot open the vinga database. Nothing of the connection is repeated here, "
    "because a database URL carries credentials in its authority and in its query: "
    "check that the instance VINGA_DB_HOST and VINGA_DB_PORT name is running and "
    "reachable, that VINGA_DB_NAME exists on it, and that VINGA_DB_USER and "
    "VINGA_DB_PASSWORD are the credentials it expects. Set VINGA_DB_URL to override "
    "all five at once. The development instance starts with "
    "`docker compose up -d --wait`"
)

# What a boot that may not create the schema it needs is told, and the
# whole of the upgrade choreography a release that adds one asks for.
#
# `deploy/postgres-init.sql` runs when a data directory initializes, and
# it deliberately leaves the server role without `CREATE` on the
# database, so an existing least-privilege deployment meeting a release
# that adds a schema cannot make it for itself. The remedy is the rerun
# the recovery documentation already carries, and the file is repeatable
# by construction, so the sentence names it and nothing else.
#
# Fixed and value-free like every other refusal here. The schema that
# was missing is not quoted back: it is this module's own constant
# rather than anything a caller reaches, and naming it would put a
# second thing in a sentence whose whole answer is one command. The
# connection is not repeated for the reason the sentences around it
# give.
SCHEMA_NOT_PERMITTED = (
    "the vinga database refused this server a privilege its migration needs, which "
    "on an existing deployment means a schema this release adds and a server role "
    "that deliberately may not create one. Rerun deploy/postgres-init.sql "
    "administratively against this database before starting this image: it creates "
    "every schema the server owns with AUTHORIZATION to the server role, and every "
    "statement in it is written to be run again. Nothing of the connection is "
    "repeated here, because a database URL carries credentials in its authority and "
    "can carry another in its query"
)

MIGRATION_BUSY = (
    "the vinga database is busy: another connection held a lock this migration needs "
    "for longer than the lock timeout allows. Nothing was changed; start again. A "
    "reader inside a long transaction is what blocks a migration, because the schema "
    "changes it makes need a lock that reads hold off"
)

# The revisions a re-cut deleted, named one by one because the set is
# closed and can never grow: it is the list of what one decision
# removed, and no later change adds to it. Today that is the single
# conversations baseline the thread schema replaced (#190), which is
# the second time this project has spent the priced exit its
# compatibility floor grants.
#
# A closed set rather than "any revision this build cannot find", and
# the difference is the whole of the arm below. Two databases produce
# the same Alembic failure and want opposite advice: one written before
# the re-cut, which cannot be upgraded and has to be replaced, and one
# written by a NEWER build and then met by an older image, which is
# current and must not be touched. Nothing in an unknown revision id
# says which it is; membership here does.
SUPERSEDED_REVISIONS = frozenset({"1001_postgres_conversations"})

# What a database still stamped at one of those is told, and the whole
# of the operator-facing surface of "unsupported". Fixed and value-free
# like every other refusal here: the revision it was stamped at is not
# quoted back, being a value in a table nothing here validates, and the
# connection is not repeated for the reason the sentences above give.
#
# The remedy is the only thing worth saying, because there is no other:
# there is no export format for the conversation record and no importer,
# so the reset is the path, and it is the one the ADR addendum records
# and the one this repository tests.
SUPERSEDED_REVISION = (
    "the record schema of the vinga database is stamped at the revision the "
    "thread schema replaced, and it cannot be upgraded in place: turns recorded "
    "before conversations existed name no conversation, and there is nothing to "
    "derive one from. Drop and recreate the database, or the record schema "
    "on its own, rerun deploy/postgres-init.sql, and start the server again, which "
    "migrates a blank schema to current in one step. The conversation record is "
    "not carried across, which the changelog announces and "
    "docs/adr/2026-08-20-database-upgrades-have-a-compatibility-floor.md records "
    "with what it costs"
)

# The advisory-lock keys, one per chain, carrying a namespace rather
# than being 1 and 2. Advisory locks share one 64-bit space with every
# other application on the instance, and an instance a deployment shares
# is exactly the case where a bare small integer collides with somebody
# else's.
_LOCK_NAMESPACE = 0x76_69_6E_67  # "ving"


def advisory_key(chain: int) -> int:
    """One chain's key in this application's half of the lock space.

    Public because the other chain is declared beside the other store
    and needs a key from the same space: two stores picking their own
    numbers is how two chains come to share one.
    """
    return (_LOCK_NAMESPACE << 32) | chain


@dataclass(frozen=True)
class StoreChain:
    """One store's schema, its migrations, and the key its writers
    serialize on.

    Declared beside the opener that takes it rather than in a module of
    its own: it is the opener's parameter list, grouped so that the
    three facts travel together and cannot be handed in mismatched. Each
    concrete chain is declared beside its own store, because which
    schema a store lives in is a fact of that store.

    `schema` is the whole of what the chain owns: the tables, and the
    `alembic_version` table Alembic keeps inside it, which is what makes
    two chains in one database two chains rather than one with two heads.
    """

    schema: str
    migrations: Path
    lock_key: int


# The schema name is read off the metadata that declares it rather than
# written again here: which schema the domain tables are in is a fact,
# and `db/schema.py` is its home.
DOMAIN_CHAIN = StoreChain(
    schema=schema.SCHEMA,
    migrations=Path(__file__).resolve().parent / "migrations",
    lock_key=advisory_key(1),
)


def open_database(settings: DatabaseConfig) -> Engine:
    """Open and migrate the domain half's schema.

    Returns an engine the caller owns and disposes. Every failure is a
    `ConfigError` carrying one of the fixed sentences above, because the
    answer to all of them is either to point the `VINGA_DB_*` variables
    at a reachable instance or to try again.

    Which `ConfigError` matters to the callers that open at startup:
    boot, the CLI per command, and the lifespan that owns the
    configuration API's engine. A lock another writer is holding is met
    here rather than inside a repository write, and must still be the
    retryable refusal, because that is what makes a contended database a
    startup that refused with a sentence; an instance the server cannot
    reach is the server's problem wherever it is found.
    """
    return open_at(settings, DOMAIN_CHAIN)


def open_at(settings: DatabaseConfig, chain: StoreChain) -> Engine:
    """`open_database` for a chain named by its caller: build the
    engine, create the schema if it is not there, run the chain to head.

    The chain is an argument rather than derived from anything here
    because the two stores keep their migrations beside their own
    packages, and their version tables are separate by virtue of living
    in separate schemas rather than by any naming trick.
    """
    return open_url(connection_url(settings), chain)


def open_url(url: URL, chain: StoreChain) -> Engine:
    """`open_at` for a caller that has already resolved its connection.

    The distinction is not decoration, and the bug that produced it says
    why. The autogeneration entry point made a scratch database from the
    discrete settings and then opened it with `open_at`, which resolves
    the connection AGAIN, and `VINGA_DB_URL` wins that resolution whole.
    So on a machine with an exported production URL, the command created
    a scratch database on the local instance and ran the migration and
    the comparison against production.

    A URL passed in is a connection nothing ambient can replace between
    the moment it is decided and the moment it is used, which is what a
    caller that derives several databases from one instance needs. Every
    other caller keeps `open_at` and its settings, because for them
    resolving from the environment IS the contract.
    """
    engine = _write_engine_at(url, chain)
    # Built inside the handler and raised outside it: `from exc` (and
    # `from None`) leave the library's exception reachable from the one
    # that travels out, and both a SQLAlchemy error and a psycopg one
    # hold the connection string they failed on.
    problem: ConfigError | None = None
    try:
        upgrade_to_head(engine, chain)
    except ConfigError:
        engine.dispose()
        raise
    except Exception as exc:
        engine.dispose()
        problem = migration_failure(exc)
    if problem is not None:
        raise problem
    return engine


def read_engine(settings: DatabaseConfig) -> Engine:
    """An engine for reading a database somebody else has already
    migrated, which on a running server means the one boot opened.

    Everything `open_database` does beyond creating an engine is exactly
    what a device path must not do. It migrates, which is an Alembic
    round trip, and it takes the chain's advisory lock before it reads,
    so a lookup would queue behind whichever writer holds it for up to
    the lock timeout. This does neither.

    Two connection properties carry what the SQLite era got from a URI
    and a hand-written deferred `BEGIN`, and both are the server's
    rather than this code's:

    - `REPEATABLE READ`, because `read_live_binding` reads two rows in
      one transaction so that a write landing between them cannot
      produce a state that never existed, and under the default
      `READ COMMITTED` every statement takes its own snapshot, which is
      exactly that torn read.
    - `default_transaction_read_only`, so "a lookup creates nothing" is
      enforced by the database rather than promised by a mode flag.

    A reader never blocks ordinary DML and is never blocked by it, which
    is the snapshot-read property this engine exists for. What a
    reader's locks do hold off is DDL, so a reader left inside a long
    transaction can make a boot migration wait out its lock timeout and
    refuse retryably; `deploy/postgres-init.sql` caps the analyst side
    of that with role-level timeouts on `vinga_ro`.

    The caller owns the engine and disposes it. Nothing is connected
    here: SQLAlchemy connects lazily, so an unreachable database is a
    failure at the first lookup, where the caller can fall back, rather
    than at app build.
    """
    return create_engine(
        connection_url(settings),
        # Echo off, and parameter logging never enabled, so a secret
        # bound into a statement cannot ride a debug log line. Off by
        # default; named here because turning it on for a debugging
        # session would be a leak rather than a convenience.
        echo=False,
        isolation_level="REPEATABLE READ",
        connect_args=_connect_args(read_only=True),
    )


def write_engine(settings: DatabaseConfig, chain: StoreChain) -> Engine:
    """The engine a schema is migrated and written through.

    Every transaction it opens takes the chain's advisory lock first.
    That is the whole of the single-writer discipline: a lock taken at
    the first write would let two writers each validate against the
    pre-change state and then persist over one another, and would let
    two openers each decide the baseline still needs running.
    """
    return _write_engine_at(connection_url(settings), chain)


def _write_engine_at(url: URL, chain: StoreChain) -> Engine:
    """`write_engine` for a connection already resolved, which is the
    half `open_url` and `write_engine` share."""
    engine = create_engine(
        url,
        echo=False,
        connect_args=_connect_args(read_only=False),
    )

    @event.listens_for(engine, "begin")
    def _serialize(connection: object) -> None:
        connection.exec_driver_sql(  # type: ignore[attr-defined]
            f"SELECT pg_advisory_xact_lock({chain.lock_key})"
        )

    return engine


def upgrade_to_head(engine: Engine, chain: StoreChain) -> None:
    """Bring one chain to head, inside one transaction under its lock.

    The schema is created here rather than by the baseline migration,
    and that is not a matter of taste: with `version_table_schema`
    configured, Alembic creates the schema-qualified version table
    before any `upgrade()` runs, so a `CREATE SCHEMA` written as the
    baseline's first operation would be too late.

    Existence is asked before it is created, rather than leaning on
    `IF NOT EXISTS` alone. `CREATE SCHEMA` checks `CREATE` on the
    database before it looks at whether the schema is there, so the
    `IF NOT EXISTS` form still refuses for a role that lacks that
    privilege, including when the answer would have been "nothing to
    do". Asking first is what lets a deployment provision the schemas
    with `deploy/postgres-init.sql` and give the server role nothing
    but its own schemas.
    """
    with engine.connect() as connection:
        # Takes the lock before Alembic looks at the version table: the
        # loser of a race then reads the schema the winner committed and
        # finds it current. Alembic sees a connection already in a
        # transaction, leaves transaction control alone, and the commit
        # below is what ends it.
        connection.execute(text("SELECT 1"))
        found = connection.execute(
            text("SELECT to_regnamespace(:name) IS NOT NULL"), {"name": chain.schema}
        ).scalar()
        if not found:
            # The name is this module's own constant, never a value from
            # anywhere a caller reaches.
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{chain.schema}"'))
        config = AlembicConfig()
        config.set_main_option("script_location", str(chain.migrations))
        config.attributes["connection"] = connection
        # What the environment needs to put its version table in the
        # right schema and compare against the right one. Handed over
        # rather than imported there, so the environment stays a
        # function of the chain it was invoked for.
        config.attributes["chain"] = chain
        command.upgrade(config, "head")
        connection.commit()


def connection_url(settings: DatabaseConfig) -> URL:
    """The URL an engine is built from: the five discrete facts, or the
    one variable that replaces all five.

    The password is read here and nowhere else. It has no YAML key and
    no field on any model, so it cannot reach a generated reference, an
    API response or a configuration diff by being carried somewhere it
    would be rendered; this function is the single consumer the no-leak
    rule names.

    `VINGA_DB_URL` wins whole when it is set, and is constrained rather
    than trusted: only the two Postgres schemes are accepted, the bare
    one is normalized to the psycopg 3 dialect, and everything else is
    refused. That refusal is what makes "there is no second storage
    backend" a property of the code rather than a line in a document.
    """
    override = os.environ.get(URL_ENV)
    if override:
        return _named_url(override)
    return URL.create(
        DIALECT,
        username=settings.user,
        password=os.environ.get(PASSWORD_ENV) or DEFAULT_PASSWORD,
        host=settings.host,
        port=settings.port,
        database=settings.name,
    )


def is_busy(exc: BaseException) -> bool:
    """Whether this failure is one the caller may simply make again.

    A closed set of three psycopg errors, matched by type and never by
    message, walked to through SQLAlchemy's `orig` because a driver
    error arrives wrapped. Each member has a decision site:

    - `LockNotAvailable` is `lock_timeout` expiring, on the chain's
      advisory gate or on a lock a migration's DDL needed. It is the
      member every contended write reaches, and the reason the
      retryable refusal exists.
    - `DeadlockDetected` and `SerializationFailure` cannot happen under
      the advisory-lock discipline, which orders every writer before it
      reads. They are here because Postgres defines both as retryable
      and a caller that met one would be right to retry: classifying
      them honestly is a better answer than an arm that says nothing
      until the day the discipline gains an exception.

    Everything else is not retryable and stays a `StorageError`. There
    is no message sniffing anywhere in this project because of this
    function: it is the one home for the question, and both raisers ask
    it here.
    """
    seen: set[int] = set()
    cause: BaseException | None = exc
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, _RETRYABLE):
            return True
        cause = getattr(cause, "orig", None)
    return False


def migration_failure(exc: Exception) -> ConfigError:
    """What an open that did not migrate is answered with.

    Four sentences and no fifth: the lock that did not arrive, which the
    caller may retry; a database stamped at a revision a re-cut deleted,
    which has to be replaced; a privilege the role does not have, which
    is the provisioning file's rerun; and everything else, which is an
    instance the server cannot use as configured. None of them carries a
    word of the driver's own text, because a psycopg connection error
    quotes the DSN it tried.

    The privilege arm is classified by exception class and never by
    message, the rule this module holds everywhere: `InsufficientPrivilege`
    is walked to through SQLAlchemy's `orig` exactly as the retryable set
    is, so a database that phrases its refusal differently, or in another
    language, is classified the same.

    The middle arm is narrow on purpose, because the sentence it answers
    with says to throw a database away. Three things have to hold before
    it is said, and each rules out a case that would be told to destroy
    something it should keep. It has to be Alembic's own `CommandError`,
    which a driver failure is not. Its cause has to be a
    `ResolutionError`, which is the stored revision not being findable
    rather than an unreadable script directory or a chain with two
    heads. And the revision it could not find has to be one a re-cut is
    known to have deleted: a database stamped by a NEWER build, met by
    an image that was rolled back, raises exactly the same
    `ResolutionError` and is current rather than stranded, so it falls
    through to the general sentence and its operator rolls forward
    instead of deleting a live volume.
    """
    if is_busy(exc):
        return DatabaseBusyError(MIGRATION_BUSY)
    if _stranded(exc):
        return StorageError(SUPERSEDED_REVISION)
    if _not_permitted(exc):
        return StorageError(SCHEMA_NOT_PERMITTED)
    return StorageError(UNREACHABLE)


def _not_permitted(exc: Exception) -> bool:
    """Whether the database refused this migration a privilege.

    The one failure whose answer is an administrative rerun rather than
    a connection to check, and the shape an existing least-privilege
    deployment meets a release that adds a schema in: `CREATE SCHEMA`
    checks `CREATE` on the database before it looks at whether the
    schema is there, so the role that has served every previous release
    is refused here and nowhere else.

    Walked through `orig` like `is_busy`, and by class, because a driver
    error arrives wrapped and its message is the one thing that may not
    be read: the wording is the server's, it is localized, and reading
    it is how a classifier comes to depend on a sentence.
    """
    seen: set[int] = set()
    cause: BaseException | None = exc
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, psycopg.errors.InsufficientPrivilege):
            return True
        cause = getattr(cause, "orig", None)
    return False


def _stranded(exc: Exception) -> bool:
    """Whether this failure is a database left behind by a re-cut, which
    is the one failure answered by telling an operator to replace it.

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
            return cause.argument in SUPERSEDED_REVISIONS
        cause = cause.__cause__
    return False


_RETRYABLE = (
    psycopg.errors.LockNotAvailable,
    psycopg.errors.DeadlockDetected,
    psycopg.errors.SerializationFailure,
)


def _connect_args(read_only: bool) -> dict[str, str]:
    """The startup options every connection this module makes carries.

    On the connection's options rather than in a `connect` listener
    running `SET`, and that is load-bearing: a pooled connection is
    returned with a rollback, and a session-level `SET` made inside a
    transaction is undone by one. A startup parameter cannot be rolled
    back, so the timeout holds for the connection's whole life.
    """
    options = [f"-c lock_timeout={LOCK_TIMEOUT_MS}"]
    if read_only:
        options.append("-c default_transaction_read_only=on")
    return {"options": " ".join(options)}


def _named_url(value: str) -> URL:
    """One `VINGA_DB_URL`, parsed and constrained.

    The parse failure is caught and dropped rather than reported:
    SQLAlchemy's own message quotes the string it could not parse, and
    that string is a URL.
    """
    problem: str | None = None
    url: URL | None = None
    try:
        url = make_url(value)
    except Exception:
        problem = URL_REFUSED
    if url is not None and url.drivername not in ACCEPTED_SCHEMES:
        problem = URL_REFUSED
    if problem is not None:
        raise ConfigError(problem)
    assert url is not None
    return url.set(drivername=DIALECT)


__all__ = [
    "ACCEPTED_SCHEMES",
    "DEFAULT_PASSWORD",
    "DIALECT",
    "DOMAIN_CHAIN",
    "LOCK_TIMEOUT_MS",
    "MIGRATION_BUSY",
    "PASSWORD_ENV",
    "SCHEMA_NOT_PERMITTED",
    "SUPERSEDED_REVISION",
    "SUPERSEDED_REVISIONS",
    "URL_ENV",
    "URL_REFUSED",
    "UNREACHABLE",
    "StoreChain",
    "advisory_key",
    "connection_url",
    "is_busy",
    "migration_failure",
    "open_at",
    "open_database",
    "open_url",
    "read_engine",
    "upgrade_to_head",
    "write_engine",
]
