r"""What `deploy/postgres-init.sql` really provisions.

The file is the one home of the read-only analyst role, and nothing in
the server executes it: the compose service mounts it into
`/docker-entrypoint-initdb.d` and an infra repository runs the same
bytes by hand. That is exactly the shape of thing that stops being true
without anybody noticing, so this lane executes the committed file and
asserts its whole contract rather than reading it.

Three claims, and each of them is a way the role could be wrong:

- `vinga_ro` reads every table of the conversation record, so an
  analyst asking what was said gets an answer.
- It inherits `SELECT` on a table created after provisioning ran, so
  the next migration does not silently take the record away from them.
- It has neither `USAGE` on the domain schema nor any write anywhere,
  so a query over conversations cannot reach a stored secret's
  ciphertext or change anything.

Against a database of this test's own rather than the lane's: the file
creates schemas and grants, which is not a state the next test should
inherit, and `vinga_ro` itself is instance-level and survives whatever
happens to a database, which is precisely the fact that makes the file
repeatable and worth asserting.

`psql` is a real prerequisite here and the lane refuses without it,
rather than skipping. The file is written in psql's own language
(`\getenv`, `\gexec`), which is what lets one file serve compose and an
infra repository unchanged, so running it through anything else would
be testing a translation of it. The runner has the client; a
contributor who has a Postgres to point this lane at almost always
does too.
"""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from tests.conftest import reset_database
from tests.support.commands import COMMAND_SECONDS
from vinga_server.config.loader import StorageError
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations import schema as conversations_schema
from vinga_server.conversations.store import open_conversations
from vinga_server.db import (
    DEFAULT_PASSWORD,
    PASSWORD_ENV,
    UNREACHABLE,
    connection_url,
    open_database,
)
from vinga_server.db import schema as domain_schema

PROVISIONING = Path(__file__).resolve().parents[3] / "deploy" / "postgres-init.sql"

# What the file provisions when nothing says otherwise. The password is
# the compose default, which the deployment documentation calls a
# loopback-only convenience; the lane uses it because the lane's
# instance is the compose one.
RO_ROLE = "vinga_ro"
RO_PASSWORD = "vinga_ro"


def _password() -> str:
    return os.environ.get(PASSWORD_ENV) or DEFAULT_PASSWORD


def _psql(
    database: str,
    *arguments: str,
    user: str,
    password: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    r"""One psql invocation, connecting as `user`.

    `environment` is laid over this process's own, and it is how the
    file's own inputs are handed in: `deploy/postgres-init.sql` reads
    `VINGA_DB_USER` and `VINGA_DB_RO_PASSWORD` with `\getenv`, from the
    environment of the process running psql. That is exactly the seam
    that lets the executor and the server role be two different roles,
    which is what the cases below need and what a deployment has.
    """
    settings = DatabaseConfig(name=database)
    return subprocess.run(
        [
            "psql",
            "--host",
            settings.host,
            "--port",
            str(settings.port),
            "--username",
            user,
            "--dbname",
            database,
            "--no-psqlrc",
            "-v",
            "ON_ERROR_STOP=1",
            *arguments,
        ],
        env=os.environ | {"PGPASSWORD": password} | (environment or {}),
        capture_output=True,
        text=True,
        # psql waits on a connection and then on a statement, and
        # neither wait is this lane's to make unbounded.
        timeout=COMMAND_SECONDS,
    )


def _as_analyst(database: str, statement: str) -> str:
    """One statement run as `vinga_ro`, answering what it said or how it
    refused. Its own connection, because the whole subject is what this
    role may do and a connection as the server role would prove
    nothing."""
    url = connection_url(DatabaseConfig(name=database)).set(
        drivername="postgresql", username=RO_ROLE, password=RO_PASSWORD
    )
    connection = psycopg.connect(url.render_as_string(hide_password=False))
    try:
        try:
            connection.execute(statement)
        except psycopg.Error as refused:
            return f"refused: {refused.sqlstate}"
        return "allowed"
    finally:
        connection.rollback()
        connection.close()


def _administrator() -> str:
    """The role that EXECUTES the file: the lane's superuser, which is
    what the compose instance gives and what the file's header asks
    for."""
    return DatabaseConfig().user


def _sql(database: str, statement: str) -> None:
    """One statement as the administrator, for the lane's own setup and
    teardown rather than for anything under test."""
    url = connection_url(DatabaseConfig(name=database)).set(drivername="postgresql")
    connection = psycopg.connect(url.render_as_string(hide_password=False))
    connection.autocommit = True
    try:
        connection.execute(statement)
    finally:
        connection.close()


def _run_the_file(database: str, server_role: str) -> None:
    """The committed file, executed exactly as an infra repository
    executes it: `psql -f` as an administrator, with the server role's
    name handed in through the environment the file reads."""
    finished = _psql(
        database,
        "--file",
        str(PROVISIONING),
        user=_administrator(),
        password=_password(),
        environment={"VINGA_DB_USER": server_role},
    )
    assert finished.returncode == 0, finished.stderr


@pytest.fixture
def server_role(blank_database: str) -> Iterator[str]:
    """A server role that is NOT the administrator and NOT a superuser.

    This is the whole of what makes the cases below able to fail. Run as
    one role, the file's two parameterized decisions are invisible:
    `AUTHORIZATION` on the schemas does nothing when the executor is
    already the server role, and `\\getenv server_role VINGA_DB_USER`
    does nothing when the value it reads is the role psql connected as.
    Both could be deleted and the suite would stay green.

    Deliberately without `CREATEDB`, `CREATEROLE` or `SUPERUSER`, which
    is the runtime contract the deployment documentation states, and
    deliberately not the owner of the database either: `blank_database`
    belongs to the lane's superuser, so this role has no `CREATE` on the
    database and cannot make a schema for itself. Alembic therefore
    creates its tables only if the file gave it schemas it owns, which
    is the claim.

    The password is the lane's own, because what is under test is what
    this role MAY DO and not how it authenticates.

    Torn down by taking its objects first: a role that still owns a
    schema cannot be dropped, and the database it owns them in outlives
    this fixture.
    """
    name = f"vinga_test_server_{os.getpid()}"
    _sql("postgres", f'drop role if exists "{name}"')
    _sql(
        "postgres",
        f"create role \"{name}\" login password '{_password()}' "
        f"nosuperuser nocreatedb nocreaterole",
    )
    try:
        yield name
    finally:
        # In the database, because that is where the schemas are, and
        # the database may have been dropped and remade under it.
        try:
            _sql(blank_database, f'drop owned by "{name}"')
        except psycopg.Error:  # pragma: no cover - a database already gone
            pass
        _sql("postgres", f'drop role if exists "{name}"')


def _require_the_file() -> None:
    """The two prerequisites every case here has, refused rather than
    skipped: the committed file, and the interpreter it is written
    for."""
    if not PROVISIONING.is_file():  # pragma: no cover - a moved file
        pytest.fail(f"the provisioning file moved out from under this test: {PROVISIONING}")
    if shutil.which("psql") is None:
        pytest.fail(
            "this lane executes deploy/postgres-init.sql, which is written in psql's "
            "own language, and psql is not on PATH. Install the Postgres client "
            "(`postgresql-client` on Debian, `libpq` on Homebrew). It is a refusal "
            "rather than a skip: the read-only role's whole contract is asserted "
            "here and nowhere else."
        )


@pytest.fixture
def provisioned(blank_database: str, server_role: str) -> str:
    """A blank database with the committed file run against it by an
    administrator, naming a server role that is somebody else."""
    _require_the_file()
    _run_the_file(blank_database, server_role)
    return blank_database


def _as_server_role(database: str, role: str) -> DatabaseConfig:
    """The settings a server booting on this database would have."""
    return DatabaseConfig(name=database, user=role)


def _owner_of(database: str, schema: str) -> str:
    """Which role owns one schema, as the catalog reports it."""
    url = connection_url(DatabaseConfig(name=database)).set(drivername="postgresql")
    connection = psycopg.connect(url.render_as_string(hide_password=False))
    try:
        found = connection.execute(
            "select nspowner::regrole::text from pg_namespace where nspname = %s",
            (schema,),
        ).fetchone()
    finally:
        connection.close()
    assert found is not None, schema
    return found[0]


def _role_exists(name: str) -> bool:
    url = connection_url(DatabaseConfig(name="postgres")).set(drivername="postgresql")
    connection = psycopg.connect(url.render_as_string(hide_password=False))
    try:
        return (
            connection.execute(
                "select 1 from pg_roles where rolname = %s", (name,)
            ).fetchone()
            is not None
        )
    finally:
        connection.close()


def test_the_file_gives_the_schemas_to_the_role_it_was_told_about(
    provisioned: str, server_role: str
) -> None:
    """The `AUTHORIZATION` half, and the `\\getenv` half with it.

    An administrator ran the file and owns everything it created by
    default; both schemas must nonetheless belong to the server role the
    environment named, because `CREATE` on a database does not grant a
    role `CREATE` on a schema somebody else owns, and Alembic would then
    be unable to make its own tables.
    """
    assert _owner_of(provisioned, "domain") == server_role
    assert _owner_of(provisioned, "record") == server_role


def test_a_role_with_nothing_but_its_schemas_migrates_both_chains(
    provisioned: str, server_role: str
) -> None:
    """The privilege contract the deployment documentation states, run
    rather than described.

    This role is not a superuser, does not own the database and has no
    `CREATE` on it, so the only reason either chain can create a table
    is that the file handed it schemas it owns. That is also the reason
    `upgrade_to_head` asks whether a schema exists before creating it:
    `CREATE SCHEMA IF NOT EXISTS` checks the database privilege first
    and would refuse here even with nothing to do.
    """
    settings = _as_server_role(provisioned, server_role)

    open_database(settings).dispose()
    open_conversations(settings).dispose()

    for table in conversations_schema.TABLES:
        assert _as_analyst(provisioned, f"select * from record.{table.name}") == (
            "allowed"
        ), table.name


def test_the_analyst_role_reads_every_conversation_table(
    provisioned: str, server_role: str
) -> None:
    """After the server has migrated, which is the ordinary order: the
    file runs at initdb and the tables arrive at the first boot.

    Migrated as the server role, because a table the lane's superuser
    created is a table the default privileges never applied to, and the
    grant would then be proving somebody else's ownership."""
    open_conversations(_as_server_role(provisioned, server_role)).dispose()

    for table in conversations_schema.TABLES:
        assert (
            _as_analyst(provisioned, f"select * from record.{table.name}")
            == "allowed"
        ), table.name


def test_the_analyst_role_inherits_a_table_created_after_provisioning(
    provisioned: str, server_role: str
) -> None:
    """The default-privileges half. A grant on the tables that existed
    would leave the next migration's table unreadable, and nobody would
    find out until an analyst asked for it.

    The later table is created BY THE SERVER ROLE, which is the whole
    point of an `ALTER DEFAULT PRIVILEGES FOR ROLE`: it applies to what
    that role creates and to nothing else."""
    settings = _as_server_role(provisioned, server_role)
    open_conversations(settings).dispose()

    engine = open_conversations(settings)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "create table record.later_migration (id bigint)"
            )
    finally:
        engine.dispose()

    assert _as_analyst(provisioned, "select * from record.later_migration") == (
        "allowed"
    )


def test_the_analyst_role_cannot_reach_the_domain_schema(
    provisioned: str, server_role: str
) -> None:
    """The whole reason the two stores are two schemas: the domain half
    holds the stored credentials' ciphertexts, and a role scoped to the
    conversation record must not be able to select them."""
    open_database(_as_server_role(provisioned, server_role)).dispose()

    for table in domain_schema.metadata.sorted_tables:
        answer = _as_analyst(provisioned, f"select * from domain.{table.name}")
        # 42501 is insufficient_privilege, which is what a schema with no
        # USAGE answers.
        assert answer == "refused: 42501", table.name


def test_the_analyst_role_cannot_write_what_it_can_read(
    provisioned: str, server_role: str
) -> None:
    """Read-only means read-only. The grant names `SELECT` and nothing
    else, so a delete against the record it can read is refused rather
    than silently allowed by a role somebody widened by hand."""
    open_conversations(_as_server_role(provisioned, server_role)).dispose()

    assert _as_analyst(provisioned, "delete from record.sessions") == (
        "refused: 42501"
    )


@pytest.fixture
def owned_database(blank_database: str, server_role: str) -> Iterator[str]:
    """A blank database the SERVER role owns, and no provisioning file.

    The other real deployment shape, and the one that makes the "run it
    before or after the first migration and it lands the same place"
    sentence in the file's header checkable: a role that owns its
    database has `CREATE` on it, so it makes its own schemas on the
    first boot and the analyst role is provisioned afterwards.

    Ownership is handed back at teardown, because `DROP OWNED BY` does
    not give up a database and `DROP ROLE` refuses for a role that still
    owns one.
    """
    _sql("postgres", f'alter database "{blank_database}" owner to "{server_role}"')
    try:
        yield blank_database
    finally:
        _sql("postgres", f'alter database "{blank_database}" owner to "{_administrator()}"')


def test_the_file_lands_the_same_place_run_after_the_first_migration(
    owned_database: str, server_role: str
) -> None:
    """The half of the grant that only the later order can prove.

    Every other case here runs the file first, where `ALTER DEFAULT
    PRIVILEGES` alone would carry the whole contract: every table is a
    future table. Run second, the tables already exist and only
    `GRANT SELECT ON ALL TABLES` reaches them, so deleting that
    statement is a change this case fails on and no other does.

    Both halves are still asserted afterwards, because a file that
    covered the present and dropped the future would be the same bug
    the other way round.
    """
    settings = _as_server_role(owned_database, server_role)
    open_database(settings).dispose()
    open_conversations(settings).dispose()

    _run_the_file(owned_database, server_role)

    for table in conversations_schema.TABLES:
        assert _as_analyst(owned_database, f"select * from record.{table.name}") == (
            "allowed"
        ), table.name

    engine = open_conversations(settings)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "create table record.later_still (id bigint)"
            )
    finally:
        engine.dispose()

    assert _as_analyst(owned_database, "select * from record.later_still") == (
        "allowed"
    )


# The upgrade, which is this file run again


# The schema the store lived in before it was renamed `record`. Written
# out rather than imported, because it is the thing that went away:
# nothing in the current tree still names it, and nothing later can
# derive it.
PREVIOUS_STORE_SCHEMA = "conversations"


def _the_previous_files_shape(database: str, server_role: str) -> None:
    """A database provisioned the way the file provisioned one before
    the store's schema was renamed.

    The old file's own statements, run as the administrator: the two
    schemas that release named, owned by the server role, and the
    analyst role granted on the one the store lived in. Written here
    rather than kept as a second committed file, because what is being
    upgraded FROM is a released shape rather than anything this
    repository still ships, and a copy of a retired file beside the
    current one would be a second thing to keep honest.

    Only the half the upgrade turns on is reproduced: the schemas, the
    role, and its grants. The role-level timeouts and the domain revoke
    are the file's own contract and are asserted where the file itself
    is run.
    """
    for schema in ("domain", PREVIOUS_STORE_SCHEMA):
        _sql(database, f'create schema if not exists "{schema}" authorization "{server_role}"')
    if _role_exists(RO_ROLE):
        _sql("postgres", f"alter role \"{RO_ROLE}\" with login password '{RO_PASSWORD}'")
    else:
        _sql("postgres", f"create role \"{RO_ROLE}\" login password '{RO_PASSWORD}'")
    _sql(database, f'grant connect on database "{database}" to "{RO_ROLE}"')
    _sql(database, f'grant usage on schema "{PREVIOUS_STORE_SCHEMA}" to "{RO_ROLE}"')
    _sql(
        database,
        f'grant select on all tables in schema "{PREVIOUS_STORE_SCHEMA}" to "{RO_ROLE}"',
    )
    _sql(
        database,
        f'alter default privileges for role "{server_role}" '
        f'in schema "{PREVIOUS_STORE_SCHEMA}" grant select on tables to "{RO_ROLE}"',
    )


def test_a_deployment_on_the_previous_file_upgrades_by_rerunning_it(
    blank_database: str, server_role: str
) -> None:
    """The upgrade the schema rename really asks for, run in the order
    the documentation gives it.

    The rename ships no migration, so what an operator does is rerun the
    updated file and then boot. This case is why that order is stated
    rather than left to be discovered: on the privilege contract this
    lane exists to hold, the server role has no `CREATE` on the database
    and cannot make the new schema for itself, so a server started first
    refuses with the fixed database refusal instead of quietly recording
    into a store nobody provisioned.

    The domain half is untouched by the rename and migrates either way,
    which is what makes the refusal specific rather than a database that
    is simply unreachable. After the rerun the record half migrates and
    the analyst reads it, which is the other half of what the file is
    for: an owner-role deployment that skipped the rerun would have the
    schema and no grants on it.
    """
    _require_the_file()
    settings = _as_server_role(blank_database, server_role)
    _the_previous_files_shape(blank_database, server_role)

    open_database(settings).dispose()
    with pytest.raises(StorageError) as refusal:
        open_conversations(settings)
    assert str(refusal.value) == UNREACHABLE

    _run_the_file(blank_database, server_role)

    open_conversations(settings).dispose()
    for table in conversations_schema.TABLES:
        assert _as_analyst(blank_database, f"select * from record.{table.name}") == (
            "allowed"
        ), table.name
    # And the schema the store used to live in is still standing, which
    # is what "the old one is left where it is" means.
    assert _owner_of(blank_database, PREVIOUS_STORE_SCHEMA) == server_role


def test_the_file_runs_again_over_what_it_already_made(
    provisioned: str, server_role: str
) -> None:
    """The cheap half of repeatability: a second run over a database
    that already has everything.

    What it exercises is the three statements that would fail if they
    were written as plain creates: a role that is already there, schemas
    that are already there, and grants that have to land the same place
    twice.
    """
    _run_the_file(provisioned, server_role)

    open_conversations(_as_server_role(provisioned, server_role)).dispose()
    assert _as_analyst(provisioned, "select * from record.sessions") == "allowed"


def test_the_file_runs_again_after_the_documented_reset(
    provisioned: str, server_role: str
) -> None:
    """The claim the recovery documentation actually rests on, run as the
    documentation writes it.

    A second run over the same database is not that claim. It leaves the
    schemas and the database-local default privileges standing, so it
    would pass with every grant in the file deleted. What recovery does
    is `dropdb` and `createdb`, which destroys both while the
    instance-level `vinga_ro` survives, and the file has to put the
    database-local half back on a database that no longer has any of it.

    So the database is really taken away here. What is asserted after the
    rerun is both halves of the grant: the tables that exist by then, and
    a table created afterwards, which is the `ALTER DEFAULT PRIVILEGES`
    the drop took with it.
    """
    settings = _as_server_role(provisioned, server_role)
    open_conversations(settings).dispose()

    # The documented reset: dropdb, then createdb. `drop owned by` first,
    # because this role owns the schemas and the fixture's own teardown
    # is not what is under test here.
    _sql(provisioned, f'drop owned by "{server_role}"')
    reset_database(provisioned)

    assert _role_exists(RO_ROLE), (
        "vinga_ro is instance-level and must survive a database reset, which is the "
        "fact that makes the file's create-or-rotate shape necessary"
    )

    _run_the_file(provisioned, server_role)
    open_conversations(settings).dispose()

    assert _as_analyst(provisioned, "select * from record.sessions") == "allowed"

    engine = open_conversations(settings)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "create table record.after_the_reset (id bigint)"
            )
    finally:
        engine.dispose()

    assert _as_analyst(provisioned, "select * from record.after_the_reset") == (
        "allowed"
    )
