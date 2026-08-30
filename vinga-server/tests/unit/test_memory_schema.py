"""Opening the memory schema: migration, reopen, the id, and the lock.

The domain database's suite next door covers the shared machinery, so
what is left here is what is specific to a third store: that it really
is a third schema with a chain of its own inside one database, that its
migrations ship inside the package a wheel is built from, that its row
ids come from a sequence, and that its advisory key is somebody else's
neither in value nor in effect.

Nothing here reads or writes a fact through the store, because the
store does not read or write one yet: what this milestone ships is the
chain, migrated and empty, and `read` and `remember` arrive with the
cutover. The rows planted below are planted through the schema's own
table, which is what a suite about a schema has to reach.

The store is opened through `open_memory` and the database is inspected
through `db.read_engine` and `db.write_engine`, both of them public
doors onto a database somebody else has migrated. Nothing here reaches
into the store for an engine: what it owns is the migration and the
disposal, and those are the whole of its interface today.
"""

from pathlib import Path

from sqlalchemy import inspect, text

import vinga_server
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations.store import CONVERSATIONS_CHAIN, open_conversations
from vinga_server.db import DOMAIN_CHAIN, advisory_key, open_database, read_engine, write_engine
from vinga_server.memory import MEMORY_CHAIN, open_memory, schema

EXPECTED_TABLES = {"facts"}

EXPECTED_COLUMNS = {"id", "agent", "at", "fact"}

# Named in the migration rather than left to the database, so a later
# migration can address it. One index, carrying both halves of the one
# access path there is: an agent's rows in insertion order, which is
# what the ordered read walks and what the prune walks.
EXPECTED_INDEXES = {"ix_facts_agent"}

HEAD = "2001_agent_memory"

SCHEMA = MEMORY_CHAIN.schema


def _tables(engine, schema_name: str) -> set[str]:
    return set(inspect(engine).get_table_names(schema=schema_name))


def _columns(engine, table: str) -> set[str]:
    return {
        column["name"] for column in inspect(engine).get_columns(table, schema=SCHEMA)
    }


def _version(engine, schema_name: str) -> list[str]:
    with engine.connect() as connection:
        return [
            row[0]
            for row in connection.execute(
                text(f"select * from {schema_name}.alembic_version")
            )
        ]


def _fact_row(agent: str, fact: str) -> dict:
    return {"agent": agent, "at": "2026-08-30T10:00:00+00:00", "fact": fact}


def test_a_blank_database_gains_a_migrated_memory_schema(blank_database: str) -> None:
    """From truly empty: no schemas, no stamps, nothing an init script
    put there. The case a migrated template cannot exercise, and the one
    a fresh deployment actually has."""
    settings = DatabaseConfig(name=blank_database)
    open_memory(settings).close()

    engine = read_engine(settings)
    try:
        assert EXPECTED_TABLES <= _tables(engine, SCHEMA)
        assert _version(engine, SCHEMA) == [HEAD]
        assert _columns(engine, "facts") == EXPECTED_COLUMNS
    finally:
        engine.dispose()


def test_it_is_a_third_schema_beside_the_other_two(blank_database: str) -> None:
    """Three chains, one database. Sharing the opener must not end with
    another store's tables in this schema or its version row in the same
    table."""
    settings = DatabaseConfig(name=blank_database)
    domain = open_database(settings)
    record = open_conversations(settings)
    open_memory(settings).close()
    reader = read_engine(settings)
    try:
        assert len({DOMAIN_CHAIN.schema, CONVERSATIONS_CHAIN.schema, SCHEMA}) == 3
        assert "providers" not in _tables(reader, SCHEMA)
        assert "sessions" not in _tables(reader, SCHEMA)
        assert "facts" not in _tables(domain, DOMAIN_CHAIN.schema)
        assert "facts" not in _tables(record, CONVERSATIONS_CHAIN.schema)
        # And the version tables really are three, each inside its own
        # schema, which is the whole of what keeps the chains apart.
        stamps = [
            _version(domain, DOMAIN_CHAIN.schema),
            _version(record, CONVERSATIONS_CHAIN.schema),
            _version(reader, SCHEMA),
        ]
        assert len({tuple(stamp) for stamp in stamps}) == 3
    finally:
        reader.dispose()
        record.dispose()
        domain.dispose()


def test_an_already_migrated_memory_schema_reopens() -> None:
    settings = DatabaseConfig()
    open_memory(settings).close()
    first = read_engine(settings)
    version = _version(first, SCHEMA)
    first.dispose()

    open_memory(settings).close()
    second = read_engine(settings)
    try:
        assert EXPECTED_TABLES <= _tables(second, SCHEMA)
        assert _version(second, SCHEMA) == version
    finally:
        second.dispose()


def test_closing_the_store_twice_is_harmless() -> None:
    """The disposal is registered on the application's exit stack the
    moment the store is opened, in front of everything a boot can still
    fail in, so it has to be safe to reach twice."""
    store = open_memory(DatabaseConfig())
    store.close()
    store.close()


def test_the_row_id_is_a_declared_identity_column() -> None:
    """Read off `information_schema` rather than off the metadata,
    because what matters is what the database was told, and it is the
    same assertion the installed-wheel step in CI makes.

    `BY DEFAULT` rather than `ALWAYS`: a restore names an id, and
    `ALWAYS` would refuse it.
    """
    settings = DatabaseConfig()
    open_memory(settings).close()
    engine = read_engine(settings)
    try:
        with engine.connect() as connection:
            declared = {
                row[0]: row[1]
                for row in connection.execute(
                    text(
                        "select table_name, is_identity from information_schema.columns "
                        "where table_schema = :schema and column_name = 'id'"
                    ),
                    {"schema": SCHEMA},
                )
            }
    finally:
        engine.dispose()

    assert declared["facts"] == "YES", "facts would not have a sequence behind it"


def test_a_deleted_maximum_id_is_never_issued_again() -> None:
    """Pruning takes rows off one end and #83's tombstones will take
    them from anywhere, so a reissued id would be a fact identity that
    named two different facts. The next insert lands past every id ever
    issued rather than back on one that was taken."""
    settings = DatabaseConfig()
    open_memory(settings).close()
    engine = write_engine(settings, MEMORY_CHAIN)
    try:
        with engine.begin() as connection:
            connection.execute(schema.facts.insert().values(_fact_row("sam", "first")))
            connection.execute(schema.facts.insert().values(_fact_row("sam", "second")))
        with engine.begin() as connection:
            issued = [
                row[0]
                for row in connection.execute(text("select id from memory.facts"))
            ]
            connection.execute(
                text("delete from memory.facts where id = :id"), {"id": max(issued)}
            )
        with engine.begin() as connection:
            landed = connection.execute(
                schema.facts.insert().values(_fact_row("sam", "third"))
            ).inserted_primary_key[0]
    finally:
        engine.dispose()

    assert landed > max(issued)


def test_the_index_the_read_and_the_prune_walk_exists() -> None:
    settings = DatabaseConfig()
    open_memory(settings).close()
    engine = read_engine(settings)
    try:
        found = {
            index["name"]
            for table in EXPECTED_TABLES
            for index in inspect(engine).get_indexes(table, schema=SCHEMA)
        }
    finally:
        engine.dispose()

    assert EXPECTED_INDEXES <= found


def test_the_chain_serializes_on_a_key_of_its_own() -> None:
    """The third key in this application's half of the advisory lock
    space, asserted in value and in effect.

    In value, because two stores picking their own numbers is how two
    chains come to share one. In effect, because the value alone would
    still pass if the chain were declared with somebody else's: a
    connection holding the memory chain's lock must not hold up a write
    on the domain chain, which is what "a `remember` never waits behind
    an `apply`" rests on.
    """
    import psycopg

    from vinga_server.db import connection_url

    assert MEMORY_CHAIN.lock_key == advisory_key(3)
    assert MEMORY_CHAIN.lock_key not in {
        DOMAIN_CHAIN.lock_key,
        CONVERSATIONS_CHAIN.lock_key,
    }

    settings = DatabaseConfig()
    dsn = (
        connection_url(settings)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    holder = psycopg.connect(dsn)
    engine = write_engine(settings, DOMAIN_CHAIN)
    try:
        holder.execute(
            "select pg_advisory_xact_lock(%s)", (MEMORY_CHAIN.lock_key,)
        )
        # The domain chain's begin listener takes its own key, so this
        # transaction opens rather than waiting out the lock timeout on
        # somebody else's.
        with engine.begin() as connection:
            assert connection.execute(text("select 1")).scalar() == 1
    finally:
        engine.dispose()
        holder.rollback()
        holder.close()


def test_the_memory_migrations_ship_inside_the_package() -> None:
    """Discovery from an installed wheel is proved in CI, which installs
    one and migrates from it. This is the cheap half: the scripts are
    inside the package directory hatchling builds, not beside it."""
    package = Path(vinga_server.__file__).resolve().parent
    migrations = package / "memory" / "migrations"

    assert (migrations / "env.py").is_file()
    assert list((migrations / "versions").glob("*.py"))


def test_the_baseline_builds_exactly_what_the_tables_declare() -> None:
    """The chain and `memory/schema.py` agree, asked of the whole shape
    rather than of a list somebody remembered to update.

    The same comparison `alembic revision --autogenerate` makes, which
    is the sanctioned way to earn a column, so this asks the migration
    machinery whether it would have anything to write. An empty answer
    is the whole assertion; if it is not empty, the difference it
    reports is the migration that is missing.

    Schema-qualified, which is what `include_schemas` with a name filter
    is for: without it the comparison would see the two sibling schemas
    in the same database and propose dropping them.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    settings = DatabaseConfig()
    open_memory(settings).close()
    engine = read_engine(settings)
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
