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
from pathlib import Path

import psycopg
import pytest

from tests.support.commands import COMMAND_SECONDS
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations import schema as conversations_schema
from vinga_server.conversations.store import open_conversations
from vinga_server.db import DEFAULT_PASSWORD, PASSWORD_ENV, connection_url, open_database
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


def _psql(database: str, *arguments: str, user: str, password: str) -> subprocess.CompletedProcess:
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
        env=os.environ | {"PGPASSWORD": password},
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


@pytest.fixture
def provisioned(blank_database: str) -> str:
    """A blank database with the committed file run against it, exactly
    as an infra repository runs it: `psql -f`, as a role that may create
    roles and schemas in that database.

    The lane's own superuser stands in for that role, which is what the
    compose instance gives and what the file's header asks for.
    """
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
    settings = DatabaseConfig(name=blank_database)
    finished = _psql(
        blank_database,
        "--file",
        str(PROVISIONING),
        user=settings.user,
        password=_password(),
    )
    assert finished.returncode == 0, finished.stderr
    return blank_database


def test_the_analyst_role_reads_every_conversation_table(provisioned: str) -> None:
    """After the server has migrated, which is the ordinary order: the
    file runs at initdb and the tables arrive at the first boot."""
    open_conversations(DatabaseConfig(name=provisioned)).dispose()

    for table in conversations_schema.TABLES:
        assert (
            _as_analyst(provisioned, f"select * from conversations.{table.name}")
            == "allowed"
        ), table.name


def test_the_analyst_role_inherits_a_table_created_after_provisioning(
    provisioned: str,
) -> None:
    """The default-privileges half. A grant on the tables that existed
    would leave the next migration's table unreadable, and nobody would
    find out until an analyst asked for it."""
    settings = DatabaseConfig(name=provisioned)
    open_conversations(settings).dispose()

    engine = open_conversations(settings)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "create table conversations.later_migration (id bigint)"
            )
    finally:
        engine.dispose()

    assert _as_analyst(provisioned, "select * from conversations.later_migration") == (
        "allowed"
    )


def test_the_analyst_role_cannot_reach_the_domain_schema(provisioned: str) -> None:
    """The whole reason the two stores are two schemas: the domain half
    holds the stored credentials' ciphertexts, and a role scoped to the
    conversation record must not be able to select them."""
    open_database(DatabaseConfig(name=provisioned)).dispose()

    for table in domain_schema.metadata.sorted_tables:
        answer = _as_analyst(provisioned, f"select * from domain.{table.name}")
        # 42501 is insufficient_privilege, which is what a schema with no
        # USAGE answers.
        assert answer == "refused: 42501", table.name


def test_the_analyst_role_cannot_write_what_it_can_read(provisioned: str) -> None:
    """Read-only means read-only. The grant names `SELECT` and nothing
    else, so a delete against the record it can read is refused rather
    than silently allowed by a role somebody widened by hand."""
    open_conversations(DatabaseConfig(name=provisioned)).dispose()

    assert _as_analyst(provisioned, "delete from conversations.sessions") == (
        "refused: 42501"
    )


def test_the_file_runs_again_after_a_reset(provisioned: str) -> None:
    """The claim the recovery documentation rests on: a dropdb/createdb
    takes the schemas and the database-local default privileges with it
    while the instance-level role survives, so the file has to be
    runnable a second time.

    Run twice against one database here, which exercises the same three
    things a rerun after a reset does: an existing role that must not be
    created again, existing schemas, and grants that have to land the
    same place.
    """
    settings = DatabaseConfig(name=provisioned)
    finished = _psql(
        provisioned, "--file", str(PROVISIONING), user=settings.user, password=_password()
    )

    assert finished.returncode == 0, finished.stderr
    open_conversations(settings).dispose()
    assert _as_analyst(provisioned, "select * from conversations.sessions") == "allowed"
