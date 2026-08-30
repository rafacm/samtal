"""Opening the domain configuration schema: migration, reopen, refusal.

The database is opened by the server at boot and by every CLI
invocation, so the interesting cases are the ones that happen without
anybody watching: a blank database, a schema that is already current,
two processes doing either at the same moment, and an instance that is
not there at all.

What retired with SQLite is named here rather than left as an absence,
because the deletions are half of this change. The uncreatable and
unwritable directory cases go with the directory; the stranded-database
family (a file stamped at a revision the squash deleted) goes with the
file, because the only databases carrying those stamps are SQLite files
this build cannot open at all, so no Postgres database can reach the
arm. What replaces both is one refusal for an instance this server
cannot use, tested below.
"""

import threading

import pytest
from sqlalchemy import inspect, text

import vinga_server
from vinga_server.config import ConfigError
from vinga_server.config.loader import DatabaseBusyError, StorageError
from vinga_server.config.models import DatabaseConfig
from vinga_server.db import (
    DOMAIN_CHAIN,
    LOCK_TIMEOUT_MS,
    SCHEMA_NOT_PERMITTED,
    UNREACHABLE,
    connection_url,
    migration_failure,
    open_database,
)

EXPECTED_TABLES = {
    "providers",
    "mcp_servers",
    "prompt_fragments",
    "agent_defaults",
    "agents",
    "devices",
    "domain_settings",
}

# The body column on each entity table, which is where every non-key
# field of that entity lives (#243). A chain that did not create them
# fails here rather than at the first write on a deployment. The same
# set the installed-wheel check in CI holds, and the two move together.
EXPECTED_COLUMNS = {
    "providers": {"stage", "name", "body", "secrets"},
    "mcp_servers": {"name", "body", "secrets"},
    "prompt_fragments": {"name", "body"},
    "agent_defaults": {"id", "body"},
    "agents": {"name", "body"},
}

# The head of the packaged domain chain, which is one revision.
HEAD = "3001_postgres_domain"

SCHEMA = DOMAIN_CHAIN.schema


def _tables(engine) -> set[str]:
    return set(inspect(engine).get_table_names(schema=SCHEMA))


def _columns(engine, table: str) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table, schema=SCHEMA)}


def _version(engine) -> list[str]:
    with engine.connect() as connection:
        return [
            row[0]
            for row in connection.execute(text(f"select * from {SCHEMA}.alembic_version"))
        ]


def test_a_blank_database_gains_a_migrated_schema(blank_database: str) -> None:
    """From truly empty: no schemas, no stamps, nothing an init script
    put there, which is what a deployment that provisioned nothing has.

    The one case a migrated template cannot exercise, which is why this
    test asks for a database made from `template0` rather than for the
    worker's own.
    """
    engine = open_database(DatabaseConfig(name=blank_database))
    try:
        assert EXPECTED_TABLES <= _tables(engine)
        assert _version(engine) == [HEAD]
        for table, columns in EXPECTED_COLUMNS.items():
            assert _columns(engine, table) == columns
    finally:
        engine.dispose()


def test_the_connection_carries_the_lock_timeout() -> None:
    """What lets a CLI write land while the server holds the same
    database open, and what bounds the wait when it cannot."""
    engine = open_database(DatabaseConfig())
    try:
        with engine.connect() as connection:
            assert connection.execute(text("show lock_timeout")).scalar() == (
                f"{LOCK_TIMEOUT_MS // 1000}s"
            )
    finally:
        engine.dispose()


def test_an_already_migrated_schema_reopens(blank_database: str) -> None:
    settings = DatabaseConfig(name=blank_database)

    first = open_database(settings)
    version = _version(first)
    first.dispose()

    second = open_database(settings)
    try:
        assert EXPECTED_TABLES <= _tables(second)
        assert _version(second) == version
    finally:
        second.dispose()


def test_concurrent_openers_serialize_on_the_migration_lock(
    blank_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first opener is held inside the migration after its
    transaction has taken the advisory lock; the second is started and
    must not reach the migration until the first commits. That pins the
    serialization property directly, rather than hoping the scheduler
    produces the race: the lock is taken before Alembic's version-table
    read, so the loser reads the schema the winner committed and finds
    it current instead of creating the same tables twice. Without the
    lock, the second opener enters the migration while the first still
    holds it, and the ordering assertion below fails."""
    from vinga_server import db as db_module

    settings = DatabaseConfig(name=blank_database)
    real_upgrade = db_module.command.upgrade
    first_inside = threading.Event()
    release_first = threading.Event()
    entered: list[int] = []
    entered_lock = threading.Lock()
    failures: list[BaseException] = []
    engines = []

    def gated_upgrade(config, revision) -> None:
        with entered_lock:
            ordinal = len(entered)
            entered.append(ordinal)
        if ordinal == 0:
            first_inside.set()
            assert release_first.wait(timeout=30), "the first opener was never released"
        real_upgrade(config, revision)

    monkeypatch.setattr(db_module.command, "upgrade", gated_upgrade)

    def opener() -> None:
        try:
            engines.append(open_database(settings))
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            failures.append(exc)

    first = threading.Thread(target=opener)
    first.start()
    assert first_inside.wait(timeout=30), "the first opener never reached the migration"

    second = threading.Thread(target=opener)
    second.start()
    # The second opener must park on the advisory lock, outside the
    # migration, for as long as the first holds it. The window is long
    # enough to catch an unserialized entry and far inside the lock
    # timeout, so a correctly parked opener neither enters nor fails.
    second.join(timeout=1.0)
    assert second.is_alive(), "the second opener finished while the first held the lock"
    with entered_lock:
        assert entered == [0], "the second opener entered the migration behind the lock"

    release_first.set()
    first.join(timeout=60)
    second.join(timeout=60)

    try:
        assert not first.is_alive() and not second.is_alive()
        assert not failures, failures
        assert len(engines) == 2
        assert len(entered) == 2
        for engine in engines:
            assert EXPECTED_TABLES <= _tables(engine)
            assert len(_version(engine)) == 1
    finally:
        for engine in engines:
            engine.dispose()


# The instance this server cannot use
#
# One refusal, with a fixed sentence, replacing the directory family
# (uncreatable, unwritable) and the stranded-file family (a database
# stamped at a revision the squash deleted). The first two were about a
# path; the third was advice to delete a file, and there is no file.


def test_an_unreachable_instance_refuses_with_the_fixed_sentence() -> None:
    """The boot refusal decision 7 of the issue asks for: a sentence,
    not a traceback, and one that names the variables to look at."""
    with pytest.raises(ConfigError) as caught:
        open_database(DatabaseConfig(port=1))

    assert str(caught.value) == UNREACHABLE
    assert isinstance(caught.value, StorageError)
    assert not isinstance(caught.value, DatabaseBusyError)


def test_a_database_that_is_not_there_refuses_the_same_way() -> None:
    """The other shape of unreachable, and deliberately the same
    sentence: a name that does not exist on a reachable instance is a
    connection this server cannot make, and the operator's next step is
    the same list of variables. Distinguishing the two would mean
    reporting what the driver said, and the driver quotes the DSN."""
    with pytest.raises(ConfigError) as caught:
        open_database(DatabaseConfig(name="vinga_no_such_database_at_all"))

    assert str(caught.value) == UNREACHABLE


# A schema the role may not create
#
# The one migration failure whose answer is a command rather than a
# connection to check, and the shape an existing least-privilege
# deployment meets a release that adds a schema in (#314). The whole
# path is driven in the integration lane, against a real restricted
# role and the committed provisioning file; what is pinned here is the
# classification, which is by exception class and never by message.


def test_a_privilege_the_role_lacks_names_the_provisioning_rerun() -> None:
    """`InsufficientPrivilege`, walked to through SQLAlchemy's `orig` the
    way the retryable set is, and answered with the rerun.

    Constructed rather than provoked, because what this pins is the
    classifier: a database that phrases its refusal differently, or in
    another language, has to be classified the same, which is only true
    while nothing here reads a message.
    """
    import psycopg
    from sqlalchemy.exc import DBAPIError

    refused = psycopg.errors.InsufficientPrivilege(
        "permission denied for database sk-test-9e21b4-never-a-real-credential"
    )
    problem = migration_failure(DBAPIError("create schema", {}, refused))

    assert str(problem) == SCHEMA_NOT_PERMITTED
    assert isinstance(problem, StorageError)
    assert not isinstance(problem, DatabaseBusyError)
    assert "deploy/postgres-init.sql" in str(problem)


def test_the_refused_privilege_repeats_nothing_the_driver_said() -> None:
    """A psycopg error quotes what it was asked about, and a refusal is
    printed to an operator's terminal and into whatever captured it."""
    import psycopg
    from sqlalchemy.exc import DBAPIError

    planted = "sk-test-9e21b4-never-a-real-credential"
    refused = psycopg.errors.InsufficientPrivilege(f"permission denied for {planted}")
    problem = migration_failure(DBAPIError("create schema", {"p": planted}, refused))

    assert planted not in str(problem)
    assert planted not in repr(problem.args)
    assert problem.__cause__ is None
    assert problem.__context__ is None


@pytest.mark.parametrize(
    "planted",
    [
        "sk-test-2b7e11-never-a-real-credential",
        "hunter2",
    ],
)
def test_no_refusal_repeats_the_password(
    monkeypatch: pytest.MonkeyPatch, planted: str
) -> None:
    """The sentinel, in its simplest form: a credential-shaped password
    in the environment, an open that cannot succeed, and nothing of it
    anywhere on the way out.

    The whole chain, not only the message: psycopg quotes the DSN it
    tried in its own error, and `from exc` would leave that reachable
    from the refusal that travels.
    """
    monkeypatch.setenv("VINGA_DB_PASSWORD", planted)

    with pytest.raises(ConfigError) as caught:
        open_database(DatabaseConfig(port=1))

    problem = caught.value
    assert planted not in str(problem)
    assert planted not in repr(problem.args)
    assert problem.__cause__ is None
    assert problem.__context__ is None


def test_the_baseline_builds_exactly_what_the_tables_declare() -> None:
    """The chain and `db/schema.py` agree, asked of the whole shape
    rather than of a list somebody remembered to update.

    This is the gate the squash removed. The chain is one file with no
    successor, so a column added to `schema.py` without a migration used
    to be invisible: the tables all exist, the body columns are all
    there, the head is still one revision, and the first write on a
    deployment fails on a column that was never created. Nothing else
    here pins column types, nullability, the primary keys or the
    singleton check constraint either.

    `compare_metadata` is the same comparison `alembic revision
    --autogenerate` makes, which the plan makes the sanctioned way to
    earn a column back, so this asks the migration machinery whether it
    would have anything to write. An empty answer is the whole
    assertion: if it is not empty, the difference it reports is the
    migration that is missing.

    Schema-qualified, and that part is not decoration: without the name
    filter the comparison would see the conversation store's tables in
    the same database and propose dropping them.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from vinga_server.db import schema

    engine = open_database(DatabaseConfig())
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "include_schemas": True,
                    "version_table_schema": SCHEMA,
                    "include_name": lambda name, type_, parents: (
                        type_ != "schema" or name == SCHEMA
                    ),
                },
            )
            difference = compare_metadata(context, schema.metadata)
    finally:
        engine.dispose()

    assert difference == []


def test_the_migrations_ship_inside_the_package() -> None:
    """Discovery from an installed wheel is proved in CI, which installs
    one and migrates from it. This is the cheap half: the scripts are
    inside the package directory hatchling builds, not beside it."""
    from pathlib import Path

    package = Path(vinga_server.__file__).resolve().parent
    migrations = package / "db" / "migrations"

    assert (migrations / "env.py").is_file()
    assert list((migrations / "versions").glob("*.py"))


# The URL override, and what it will not accept
#
# `VINGA_DB_URL` replaces all five discrete facts when it is set, which
# is what a deployment with a connection string in a secret manager
# needs. It is constrained rather than trusted: accepting any SQLAlchemy
# URL would admit `sqlite://` and psycopg2's `postgresql://` dialect,
# and "there is no second storage backend" would be documentation
# rather than a property of the code.


def test_the_url_override_wins_over_the_discrete_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "VINGA_DB_URL", "postgresql+psycopg://someone:pw@elsewhere:6543/other"
    )

    url = connection_url(DatabaseConfig())

    assert url.host == "elsewhere"
    assert url.port == 6543
    assert url.database == "other"


def test_a_bare_postgresql_url_is_normalized_to_psycopg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`postgresql://` alone selects psycopg2, which is not installed and
    is not the driver this project chose. It is what Postgres itself
    documents, so it is accepted and normalized rather than refused."""
    monkeypatch.setenv("VINGA_DB_URL", "postgresql://someone:pw@elsewhere:6543/other")

    assert connection_url(DatabaseConfig()).drivername == "postgresql+psycopg"


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///vinga.db",
        "mysql+pymysql://someone:pw@elsewhere/vinga",
        "postgresql+psycopg2://someone:pw@elsewhere/vinga",
        "not a url at all",
        "",
    ],
)
def test_a_url_that_is_not_this_backend_is_refused(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    """Every other scheme, and an unparseable string, one by one.

    The empty string is the one that is not a refusal: an unset-shaped
    value falls through to the discrete settings, which is what an
    operator who cleared the variable meant.
    """
    monkeypatch.setenv("VINGA_DB_URL", url)

    if url == "":
        assert connection_url(DatabaseConfig()).host == "127.0.0.1"
        return

    with pytest.raises(ConfigError) as caught:
        connection_url(DatabaseConfig())

    assert "postgresql" in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///sk-test-4a9c02-never-a-real-credential.db",
        "mysql://someone:sk-test-4a9c02-never-a-real-credential@host/vinga",
        "postgresql+psycopg2://u:p@h/db?sslpassword=sk-test-4a9c02-never-a-real-credential",
        "://sk-test-4a9c02-never-a-real-credential",
    ],
)
def test_a_refused_url_is_never_quoted_back(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    """The three places a URL can carry a credential: its authority, its
    query parameters, and a value somebody pasted into the wrong
    variable entirely.

    A password-hidden rendering would cover only the first, which is why
    the refusal is fixed rather than rendered at all. The parse failure
    is included because SQLAlchemy's own message quotes the string it
    could not parse.
    """
    monkeypatch.setenv("VINGA_DB_URL", url)
    planted = "sk-test-4a9c02-never-a-real-credential"

    with pytest.raises(ConfigError) as caught:
        connection_url(DatabaseConfig())

    problem = caught.value
    assert planted not in str(problem)
    assert planted not in repr(problem.args)
    assert problem.__cause__ is None
    assert problem.__context__ is None
