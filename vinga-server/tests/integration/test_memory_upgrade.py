"""The memory chain's forward migration, proved on rows rather than on
an empty database.

`tests/unit/test_memory_schema.py` asks what the chain builds from
nothing: the head, the columns, the indexes, the checks, and that the
tables and the migrations agree. Every one of those passes on a
database that lost every row, which is exactly the failure a rename can
produce: autogenerate sees `agent` dropped and `owner` added, and a
migration written from that candidate would carry the shape across and
nothing else.

So this seeds a database at `2001_agent_memory`, the revision a
deployment upgrading into this release is stamped at, with rows whose
ids, moments and text this file knows exactly, brings it to head the
way a boot does, and reads them back. What is asserted is what the
promise is worth: the bytes, the scope every existing row turns out to
have had, the held pair standing null, and identity continuing past the
seeded maximum rather than restarting on an id that already names a
fact.

The lane, rather than the unit suite, because the material is a
database in a state no current build produces and the fixture that
makes one is `blank_database`: a migrated template cannot be stamped
backwards.
"""

from typing import Any

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text

from vinga_server.config.models import DatabaseConfig
from vinga_server.db import read_engine, write_engine
from vinga_server.memory.store import MEMORY_CHAIN, MemoryScope, open_memory

# The revision a deployment carrying remembered facts is stamped at, and
# the whole of what this release upgrades from.
BASELINE = "2001_agent_memory"

HEAD = "2003_rename_moves_memory"

# Three facts, written the way the baseline's shape wrote them: an agent
# name that needed sanitizing when memory was a file, text with the
# punctuation a normalizer would have touched, and a moment each. Exact,
# because the assertion is that the bytes did not move.
SEEDED = (
    ("poet", "2026-08-28T09:00:00+00:00", "the user is vegetarian"),
    ("poet", "2026-08-29T10:30:00+00:00", "the user's dog is called Bosse"),
    ("../poet in the kitchen", "2026-08-30T11:15:00+00:00", "the kettle is loud"),
)


@pytest.fixture
def at_the_baseline(blank_database: str) -> DatabaseConfig:
    """A database with the memory chain at `2001_agent_memory` and
    nothing beyond it.

    Alembic is driven the way `db.upgrade_to_head` drives it, with the
    schema created first and the connection and the chain handed over on
    the config's attributes, because the packaged environment refuses to
    run without both. The one difference is the target: a named revision
    rather than head, which is the whole of what makes this a database
    from before the release.
    """
    settings = DatabaseConfig(name=blank_database)
    engine = write_engine(settings, MEMORY_CHAIN)
    try:
        with engine.connect() as connection:
            connection.execute(text(f'create schema if not exists "{MEMORY_CHAIN.schema}"'))
            config = AlembicConfig()
            config.set_main_option("script_location", str(MEMORY_CHAIN.migrations))
            config.attributes["connection"] = connection
            config.attributes["chain"] = MEMORY_CHAIN
            command.upgrade(config, BASELINE)
            connection.commit()
    finally:
        engine.dispose()
    return settings


def _seeded(settings: DatabaseConfig) -> list[int]:
    """The three facts, written through the baseline's own columns.

    Without ids, deliberately: the sequence assigns them, which is the
    state a real deployment is in and the only state in which
    "identity continues past the seeded maximum" means anything. An
    insert naming its own ids would leave the sequence at zero and prove
    the opposite.
    """
    engine = write_engine(settings, MEMORY_CHAIN)
    try:
        with engine.begin() as connection:
            return [
                connection.execute(
                    text(
                        "insert into memory.facts (agent, at, fact) "
                        "values (:agent, :at, :fact) returning id"
                    ),
                    {"agent": agent, "at": at, "fact": fact},
                ).scalar_one()
                for agent, at, fact in SEEDED
            ]
    finally:
        engine.dispose()


def _rows(settings: DatabaseConfig) -> list[dict[str, Any]]:
    """Every fact as the database holds it, read through a door onto a
    database somebody else migrated."""
    engine = read_engine(settings)
    try:
        with engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    text("select * from memory.facts order by id")
                ).mappings()
            ]
    finally:
        engine.dispose()


def _version(settings: DatabaseConfig) -> list[str]:
    engine = read_engine(settings)
    try:
        with engine.connect() as connection:
            return [
                row[0]
                for row in connection.execute(
                    text(f"select * from {MEMORY_CHAIN.schema}.alembic_version")
                )
            ]
    finally:
        engine.dispose()


def test_a_seeded_baseline_upgrades_with_every_fact_it_held(
    at_the_baseline: DatabaseConfig,
) -> None:
    """The upgrade a deployment actually takes: rows in, the same rows
    out, under the names the new shape gives them."""
    settings = at_the_baseline
    issued = _seeded(settings)

    open_memory(settings).close()

    assert _version(settings) == [HEAD]
    carried = _rows(settings)
    assert [(row["id"], row["owner"], row["at"], row["fact"]) for row in carried] == [
        (row_id, agent, at, fact)
        for row_id, (agent, at, fact) in zip(issued, SEEDED, strict=True)
    ]
    # The scope every one of them turns out to have had, and the held
    # pair standing null: an upgraded fact is an active agent fact, not
    # one that arrives already forgotten.
    assert {row["scope"] for row in carried} == {"agent"}
    assert all(row["forgotten_at"] is None for row in carried)
    assert all(row["forgotten_in"] is None for row in carried)


def test_identity_continues_past_the_facts_the_upgrade_carried(
    at_the_baseline: DatabaseConfig,
) -> None:
    """The other half of preserving rows: preserving what addresses
    them. The id is what update, forget and restore name a fact by, so
    an insert after the upgrade must land past every id the upgrade
    carried rather than back on one of them."""
    settings = at_the_baseline
    issued = _seeded(settings)

    store = open_memory(settings)
    try:
        import asyncio

        asyncio.run(
            store.add(
                MemoryScope.AGENT, "poet", "the user took up the cello", agent="poet"
            )
        )
    finally:
        store.close()

    landed = [row["id"] for row in _rows(settings)]
    assert landed[: len(issued)] == issued
    assert landed[-1] > max(issued)


def test_the_ledger_arrives_empty_beside_the_facts(
    at_the_baseline: DatabaseConfig,
) -> None:
    """A `2001_agent_memory` database has no conversation state to
    carry, so the table the upgrade adds starts empty rather than
    inventing a row per thread."""
    settings = at_the_baseline
    _seeded(settings)

    open_memory(settings).close()

    engine = read_engine(settings)
    try:
        with engine.connect() as connection:
            held = connection.execute(text("select count(*) from memory.state")).scalar()
    finally:
        engine.dispose()

    assert held == 0
