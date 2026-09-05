"""Opening the memory schema: migration, reopen, the id, and the lock.

The domain database's suite next door covers the shared machinery, so
what is left here is what is specific to a third store: that it really
is a third schema with a chain of its own inside one database, that its
migrations ship inside the package a wheel is built from, that its row
ids come from a sequence, and that its advisory key is somebody else's
neither in value nor in effect.

Nothing here reads or writes a fact through the store, and that is a
division of labour rather than a limitation: `read` and `remember` are
`test_memory_store.py`'s subject, and a suite about a schema has to
reach the table itself. The rows planted below are planted through the
schema's own table for that reason.

The store is opened through `open_memory` and the database is inspected
through `db.read_engine` and `db.write_engine`, both of them public
doors onto a database somebody else has migrated. Nothing here reaches
into the store for an engine: what this suite needs of it is the
migration and the disposal.
"""

from pathlib import Path

import psycopg
import pytest
from sqlalchemy import inspect, text

import vinga_server
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations.store import CONVERSATIONS_CHAIN, open_conversations
from vinga_server.db import DOMAIN_CHAIN, advisory_key, open_database, read_engine, write_engine
from vinga_server.memory import schema
from vinga_server.memory.store import MEMORY_CHAIN, open_memory

EXPECTED_TABLES = {"facts", "state"}

EXPECTED_COLUMNS = {
    "id",
    "scope",
    "owner",
    "at",
    "fact",
    "forgotten_at",
    "forgotten_in",
}

EXPECTED_STATE_COLUMNS = {"conversation", "key", "value", "updated_at"}

# Named in the migration rather than left to the database, so a later
# migration can address them. Two, because there are two access paths: an
# owner's rows within a scope in insertion order, which the ordered read,
# the prune and the lookup filter walk, and the held rows of one thread,
# which restore, erasure, retention and the sweep address.
EXPECTED_INDEXES = {"ix_facts_scope", "ix_facts_forgotten"}

HEAD = "2003_rename_moves_memory"

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


def _fact_row(owner: str, fact: str) -> dict:
    return {
        "scope": "agent",
        "owner": owner,
        "at": "2026-08-30T10:00:00+00:00",
        "fact": fact,
    }


def _refused(engine, statement: str) -> str:
    """What the database says no to, by the constraint it names.

    The message is read for the constraint name and nothing else, which
    is a value this suite wrote into the migration rather than anything
    a caller reaches.
    """
    try:
        with engine.begin() as connection:
            connection.execute(text(statement))
    except Exception as exc:
        cause = getattr(exc, "orig", None)
        assert isinstance(cause, psycopg.errors.CheckViolation), exc
        return str(cause.diag.constraint_name)
    raise AssertionError("the database accepted a row the check forbids")


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
        assert _columns(engine, "state") == EXPECTED_STATE_COLUMNS
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
    """Pruning takes rows off one end and a permanent forgetting takes
    them from anywhere, so a reissued id would be a fact identity that
    named two different facts, and the id is what update, forget and
    restore address. The next insert lands past every id ever issued
    rather than back on one that was taken."""
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
    # And the index the rename replaced is gone rather than left behind
    # to be maintained on every write for nobody.
    assert "ix_facts_agent" not in found


def test_the_held_rows_have_an_index_of_their_own() -> None:
    """Partial, which is the whole point of it: the paths that address
    held rows by their thread must not walk the active majority under
    the writer's lock. Read off the database rather than off the
    metadata, because what a partial index is depends on what was
    declared to Postgres."""
    settings = DatabaseConfig()
    open_memory(settings).close()
    engine = read_engine(settings)
    try:
        with engine.connect() as connection:
            declared = {
                row[0]: row[1]
                for row in connection.execute(
                    text(
                        "select indexname, indexdef from pg_indexes "
                        "where schemaname = :schema"
                    ),
                    {"schema": SCHEMA},
                )
            }
    finally:
        engine.dispose()

    assert "WHERE (forgotten_in IS NOT NULL)" in declared["ix_facts_forgotten"]
    assert "WHERE" not in declared["ix_facts_scope"]


def test_a_scope_the_facts_table_does_not_carry_is_refused() -> None:
    """The vocabulary is three, and conversation data lives in `state`
    alone, so a `facts` row claiming that scope is one nothing reads. The
    check is what makes that a property of the database rather than of
    the code that happens to write it."""
    settings = DatabaseConfig()
    open_memory(settings).close()
    engine = write_engine(settings, MEMORY_CHAIN)
    try:
        refusal = _refused(
            engine,
            "insert into memory.facts (scope, owner, at, fact) values "
            "('conversation', 'poet', '2026-08-30T10:00:00+00:00', 'a fact')",
        )
    finally:
        engine.dispose()

    assert refusal == "ck_facts_scope"


def test_half_a_forgetting_is_refused() -> None:
    """Set together or null together. A row with a moment and no thread
    could never be swept, and one with a thread and no moment could never
    age out, so neither half is allowed to stand alone."""
    settings = DatabaseConfig()
    open_memory(settings).close()
    engine = write_engine(settings, MEMORY_CHAIN)
    try:
        refusal = _refused(
            engine,
            "insert into memory.facts (scope, owner, at, fact, forgotten_at) values "
            "('agent', 'poet', '2026-08-30T10:00:00+00:00', 'a fact', "
            "'2026-08-30T11:00:00+00:00')",
        )
        mirrored = _refused(
            engine,
            "insert into memory.facts (scope, owner, at, fact, forgotten_in) values "
            "('agent', 'poet', '2026-08-30T10:00:00+00:00', 'a fact', 'abc')",
        )
    finally:
        engine.dispose()

    assert {refusal, mirrored} == {"ck_facts_forgotten"}


def test_one_conversation_holds_one_value_per_key() -> None:
    """Upsert by key is the whole of the ledger's semantics, and the
    primary key is what makes a second row under the same key
    impossible rather than merely unwritten."""
    settings = DatabaseConfig()
    open_memory(settings).close()
    engine = write_engine(settings, MEMORY_CHAIN)
    entry = (
        "insert into memory.state (conversation, key, value, updated_at) values "
        "('abc', 'turn', '%s', '2026-08-30T10:00:00+00:00')"
    )
    try:
        with engine.begin() as connection:
            connection.execute(text(entry % "white"))
        with pytest.raises(Exception) as refusal:
            with engine.begin() as connection:
                connection.execute(text(entry % "black"))
        # And the same key under another thread is another entry, which
        # is what keyed-by-thread means.
        with engine.begin() as connection:
            connection.execute(text(entry.replace("'abc'", "'def'") % "black"))
            held = connection.execute(
                text("select conversation, value from memory.state order by conversation")
            ).all()
    finally:
        engine.dispose()

    assert isinstance(refusal.value.orig, psycopg.errors.UniqueViolation)
    assert held == [("abc", "white"), ("def", "black")]


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
