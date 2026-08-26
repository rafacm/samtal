"""What the autogeneration command connects to.

`python -m vinga_server.db.migrations.autogen` is the one thing in this
repository that makes a database, migrates it and drops it, and it is
run by a maintainer on a machine where an exported `VINGA_DB_URL` may
name anything at all. That combination is worth pinning: the command
used to make its scratch database from the discrete settings and then
open it through `open_at`, which resolves the connection AGAIN from the
environment, and a URL wins that resolution whole. The scratch database
landed locally and the migration ran wherever the URL pointed.

Driven against a real database rather than a mock, because what is
asserted is which database has schemas in it afterwards, and that is a
fact only Postgres can answer. The chain's migrations directory is
copied into `tmp_path` first: autogenerate WRITES a revision file, and a
test that let it write into the packaged tree would leave one behind.
"""

import shutil
from pathlib import Path

import psycopg
import pytest

from vinga_server.config.models import DatabaseConfig
from vinga_server.db import DOMAIN_CHAIN, StoreChain, connection_url
from vinga_server.db.migrations import autogen


def _chain_in(tmp_path: Path) -> StoreChain:
    """The domain chain, with its migrations somewhere disposable.

    Same schema and same lock key, because the subject is the connection
    and not the chain: what moves is only the directory the revision
    file is written into.
    """
    where = tmp_path / "migrations"
    shutil.copytree(DOMAIN_CHAIN.migrations, where)
    return StoreChain(
        schema=DOMAIN_CHAIN.schema, migrations=where, lock_key=DOMAIN_CHAIN.lock_key
    )


def _url_of(database: str) -> str:
    """One of this run's databases as a `VINGA_DB_URL` would name it."""
    return connection_url(DatabaseConfig(name=database)).render_as_string(
        hide_password=False
    )


def _schemas(database: str) -> set[str]:
    """Every schema in one database, as its own connection reports them."""
    url = connection_url(DatabaseConfig(name=database)).set(drivername="postgresql")
    connection = psycopg.connect(url.render_as_string(hide_password=False))
    try:
        found = connection.execute(
            "select nspname from pg_namespace where nspname not like 'pg\\_%' "
            "and nspname <> 'information_schema'"
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in found}


def _exists(database: str) -> bool:
    url = connection_url(DatabaseConfig(name="postgres")).set(drivername="postgresql")
    connection = psycopg.connect(url.render_as_string(hide_password=False))
    try:
        return (
            connection.execute(
                "select 1 from pg_database where datname = %s", (database,)
            ).fetchone()
            is not None
        )
    finally:
        connection.close()


def test_a_url_override_never_migrates_the_database_it_names(
    blank_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug, stated as the property that was violated.

    `VINGA_DB_URL` names a database with nothing in it. The command must
    derive its scratch database from that same connection, migrate THAT,
    and leave the database the URL actually names as blank as it found
    it. Before the connection was resolved once, this database came back
    with the domain schema in it and a version table stamped.
    """
    monkeypatch.setenv("VINGA_DB_URL", _url_of(blank_database))
    before = _schemas(blank_database)

    autogen.generate("a probe of where this lands", _chain_in(tmp_path))

    assert _schemas(blank_database) == before
    assert DOMAIN_CHAIN.schema not in _schemas(blank_database)


def test_the_scratch_database_is_made_migrated_and_taken_away(
    blank_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the other half, so the case above cannot be satisfied by a
    command that does nothing at all.

    A revision file is written, which is only possible against a
    database Alembic reached and compared, and the scratch database is
    gone afterwards, which is what makes the command repeatable.
    """
    monkeypatch.setenv("VINGA_DB_URL", _url_of(blank_database))
    chain = _chain_in(tmp_path)
    before = set((chain.migrations / "versions").glob("*.py"))

    autogen.generate("a probe that leaves a file", chain)

    written = set((chain.migrations / "versions").glob("*.py")) - before
    assert len(written) == 1, written
    assert not _exists(autogen.SCRATCH)
