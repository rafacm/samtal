"""Opening the conversation store: migration, reopen, and the ids.

The domain database's suite next door covers the shared machinery, so
what is left here is what is specific to the second store: that it
really is a second schema with a chain of its own inside one database,
that its migrations ship inside the package a wheel is built from, and
that the three cursor tables issue ids a paginating client can trust.

That last one is the reason for the delete-maximum case. Retention
deletes from exactly the end a cursor points past, so a backend that
reissued the highest deleted id would hand a client that had already
read row N a different row N on its next page. Under SQLite that
property was bought with `AUTOINCREMENT` and asserted on the stored
DDL; a Postgres sequence has it by construction, so what is asserted is
the declaration (an identity column, read from `information_schema`)
and the behavior (a delete-then-insert lands past every id ever
issued).
"""

from pathlib import Path

from sqlalchemy import inspect, text

import vinga_server
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations import schema
from vinga_server.conversations.store import CONVERSATIONS_CHAIN, open_conversations
from vinga_server.db import DOMAIN_CHAIN, open_database

EXPECTED_TABLES = {
    "sessions",
    "conversations",
    "turns",
    "tool_invocations",
    "conversation_milestones",
    "events",
}

# The three tables whose ids are cursors. `tool_invocations` is declared
# the same way and is not one: it is read through its parent turn and
# never paginated, so nothing reads its ids as a position.
CURSOR_TABLES = ("sessions", "turns", "events")

EXPECTED_INDEXES = {
    "ix_sessions_device",
    "ix_sessions_started_at",
    "ix_turns_session",
    "ix_tool_invocations_session",
    "ix_tool_invocations_turn",
    "ix_events_session",
    # The five the thread query paths need, carried by the baseline from
    # its first run rather than added once a listing was slow: the
    # agent-filtered listing and discovery in their keyset order, the
    # unfiltered listing and retention's inactivity scan, the per-thread
    # turn walk that hydration reads oldest first, and the
    # latest-milestone lookup. The unique join key is the sixth and is
    # asserted below, because a unique constraint is not an index entry
    # under every inspector.
    "ix_conversations_agent_activity",
    "ix_conversations_last_active",
    "ix_turns_conversation",
    "ix_conversation_milestones_conversation",
}

HEAD = "1002_conversation_threads"


def _tables(engine, schema_name: str) -> set[str]:
    return set(inspect(engine).get_table_names(schema=schema_name))


def _version(engine, schema_name: str) -> list[str]:
    with engine.connect() as connection:
        return [
            row[0]
            for row in connection.execute(
                text(f"select * from {schema_name}.alembic_version")
            )
        ]


# The thread every planted turn below belongs to, in the shape the
# runtime mints.
CONVERSATION = "9f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5"


def _session_row(session_id: str, started_at: str = "2026-08-15T10:00:00+00:00") -> dict:
    return {
        "session": session_id,
        "device": "aa:bb:cc:dd:ee:ff",
        "client": None,
        "agent": "sam",
        "agents": ["sam"],
        "protocol": "1",
        "started_at": started_at,
        "closed_at": None,
        "duration_s": None,
        "close_reason": None,
        "server_version": "0.1.0",
        "revision": "abc1234",
        "providers": {},
        "metrics": True,
        "text": True,
        "dropped": 0,
    }


def test_a_blank_database_gains_a_migrated_conversation_schema(
    blank_database: str,
) -> None:
    """From truly empty: no schemas, no stamps, nothing an init script
    put there. This is the case a migrated template cannot exercise and
    the one a fresh deployment actually has."""
    engine = open_conversations(DatabaseConfig(name=blank_database))
    try:
        assert EXPECTED_TABLES <= _tables(engine, schema.SCHEMA)
        assert _version(engine, schema.SCHEMA) == [HEAD]
    finally:
        engine.dispose()


def test_it_is_a_second_schema_beside_the_domain_one(blank_database: str) -> None:
    """Two chains, one database. Sharing the opener must not end with the
    domain configuration's tables in this schema or its version row in
    the same table."""
    settings = DatabaseConfig(name=blank_database)
    domain = open_database(settings)
    store = open_conversations(settings)
    try:
        assert DOMAIN_CHAIN.schema != CONVERSATIONS_CHAIN.schema
        assert "providers" not in _tables(store, CONVERSATIONS_CHAIN.schema)
        assert "sessions" not in _tables(domain, DOMAIN_CHAIN.schema)
        # And the version tables really are two, each inside its own
        # schema, which is the whole of what keeps the chains apart.
        assert _version(domain, DOMAIN_CHAIN.schema) != _version(
            store, CONVERSATIONS_CHAIN.schema
        )
    finally:
        store.dispose()
        domain.dispose()


def test_an_already_migrated_conversation_schema_reopens() -> None:
    first = open_conversations(DatabaseConfig())
    version = _version(first, schema.SCHEMA)
    first.dispose()

    second = open_conversations(DatabaseConfig())
    try:
        assert EXPECTED_TABLES <= _tables(second, schema.SCHEMA)
        assert _version(second, schema.SCHEMA) == version
    finally:
        second.dispose()


def test_the_cursor_tables_are_declared_identity_columns() -> None:
    """Read off `information_schema` rather than off the metadata,
    because what matters is what the database was told, and it is the
    same assertion the installed-wheel step in CI makes.

    `BY DEFAULT` rather than `ALWAYS`: a restore and the suites that
    plant rows both name an id, and `ALWAYS` would refuse them.
    """
    engine = open_conversations(DatabaseConfig())
    try:
        with engine.connect() as connection:
            declared = {
                row[0]: row[1]
                for row in connection.execute(
                    text(
                        "select table_name, is_identity from information_schema.columns "
                        "where table_schema = :schema and column_name = 'id'"
                    ),
                    {"schema": schema.SCHEMA},
                )
            }
    finally:
        engine.dispose()

    for name in CURSOR_TABLES:
        assert declared[name] == "YES", f"{name} would not have a sequence behind it"


def test_a_deleted_maximum_id_is_never_issued_again() -> None:
    """Retention deletes the newest rows of an old session, which is
    exactly the maximum. The next insert must land past every id ever
    issued rather than back on the one a client just read."""
    engine = open_conversations(DatabaseConfig())
    try:
        with engine.begin() as connection:
            connection.execute(schema.sessions.insert().values(_session_row("first")))
            connection.execute(schema.sessions.insert().values(_session_row("second")))
        with engine.connect() as connection:
            issued = [
                row[0]
                for row in connection.execute(text("select id from conversations.sessions"))
            ]
        with engine.begin() as connection:
            connection.execute(
                text("delete from conversations.sessions where id = :id"),
                {"id": max(issued)},
            )
    finally:
        engine.dispose()

    reopened = open_conversations(DatabaseConfig())
    try:
        with reopened.begin() as connection:
            landed = connection.execute(
                schema.sessions.insert().values(_session_row("third"))
            ).inserted_primary_key[0]
    finally:
        reopened.dispose()

    assert landed > max(issued)


def test_every_index_the_queries_need_exists() -> None:
    """Named in the migration rather than left to the database, so that
    a later migration can address them."""
    engine = open_conversations(DatabaseConfig())
    try:
        inspector = inspect(engine)
        found = {
            index["name"]
            for table in EXPECTED_TABLES
            for index in inspector.get_indexes(table, schema=schema.SCHEMA)
        }
    finally:
        engine.dispose()

    assert EXPECTED_INDEXES <= found


def test_the_thread_join_key_is_unique() -> None:
    """`conversations.conversation` addresses one thread, the way
    `sessions.session` addresses one session: a second row under the
    same id would make a resume ambiguous and a turn's reference
    meaningless. Asserted as the constraint rather than as an index,
    which is how the database was told."""
    from sqlalchemy.exc import IntegrityError

    engine = open_conversations(DatabaseConfig())
    row = {
        "conversation": CONVERSATION,
        "agent": "sam",
        "device": "aa:bb:cc:dd:ee:ff",
        "title": None,
        "incomplete": False,
        "created_at": "2026-08-15T10:00:00+00:00",
        "last_active_at": "2026-08-15T10:00:00+00:00",
    }
    refused = False
    try:
        with engine.begin() as connection:
            connection.execute(schema.conversations.insert().values(row))
        try:
            with engine.begin() as connection:
                connection.execute(schema.conversations.insert().values(row))
        except IntegrityError:
            refused = True
    finally:
        engine.dispose()

    assert refused


def test_the_source_column_refuses_a_token_outside_the_closed_set() -> None:
    """The value of the column is that a query may enumerate it, so the
    closed set is a property of the schema and not only of the
    classifier that fills it."""
    from sqlalchemy.exc import IntegrityError

    engine = open_conversations(DatabaseConfig())
    try:
        with engine.begin() as connection:
            connection.execute(schema.sessions.insert().values(_session_row("s")))
            turn = connection.execute(
                schema.turns.insert().values(
                    session="s", conversation=CONVERSATION, t_ms=0, tool_calls=1
                )
            ).inserted_primary_key[0]
        rejected = False
        try:
            with engine.begin() as connection:
                connection.execute(
                    schema.tool_invocations.insert().values(
                        turn=turn,
                        session="s",
                        position=0,
                        source="whatever",
                        malformed=False,
                        is_error=False,
                    )
                )
        except IntegrityError:
            rejected = True
    finally:
        engine.dispose()

    assert rejected


def test_the_conversation_migrations_ship_inside_the_package() -> None:
    """Discovery from an installed wheel is proved in CI, which installs
    one and migrates from it. This is the cheap half: the scripts are
    inside the package directory hatchling builds, not beside it."""
    package = Path(vinga_server.__file__).resolve().parent
    migrations = package / "conversations" / "migrations"

    assert (migrations / "env.py").is_file()
    assert list((migrations / "versions").glob("*.py"))


def test_the_baseline_builds_exactly_what_the_tables_declare() -> None:
    """The chain and `conversations/schema.py` agree, asked of the whole
    shape rather than of a list somebody remembered to update.

    The check this store never had. Its SQLite-era suite asserted the
    stored DDL of three tables, which said nothing about a column added
    to the metadata without a migration; the domain chain has had this
    since #243 and this is the same comparison, which is also the one
    `alembic revision --autogenerate` makes.

    Schema-qualified, which is what `include_schemas` with a name filter
    is for: without it the comparison would see the domain schema's
    tables in the same database and propose dropping them.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    engine = open_conversations(DatabaseConfig())
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "include_schemas": True,
                    "version_table_schema": CONVERSATIONS_CHAIN.schema,
                    "include_name": lambda name, type_, parents: (
                        type_ != "schema" or name == CONVERSATIONS_CHAIN.schema
                    ),
                },
            )
            difference = compare_metadata(context, schema.metadata)
    finally:
        engine.dispose()

    assert difference == []
