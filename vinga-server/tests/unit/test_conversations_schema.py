"""Opening `conversations.db`: creation, migration, reopen, and the ids.

The domain database's suite next door covers the shared machinery, so
what is left here is what is specific to the second database: that it
really is a second file with a chain of its own, that its migrations
ship inside the package a wheel is built from, and that the three cursor
tables issue ids a paginating client can trust.

That last one is the reason for the delete-maximum case. A plain
`INTEGER PRIMARY KEY` reuses the highest deleted rowid, and retention
deletes from exactly the end a cursor points past, so without
`AUTOINCREMENT` a client that had already read row N would be handed a
different row N on its next page.
"""

from pathlib import Path

from sqlalchemy import inspect, text

import vinga_server
from vinga_server.conversations import schema
from vinga_server.conversations.store import (
    DATABASE_FILENAME,
    conversations_path,
    open_conversations,
)
from vinga_server.db import DATABASE_FILENAME as DOMAIN_FILENAME

EXPECTED_TABLES = {"sessions", "turns", "tool_invocations", "events"}

# The three tables whose ids are cursors, which is why they carry
# AUTOINCREMENT and why tool_invocations does not.
CURSOR_TABLES = ("sessions", "turns", "events")

EXPECTED_INDEXES = {
    "ix_sessions_device",
    "ix_sessions_started_at",
    "ix_turns_session",
    "ix_tool_invocations_session",
    "ix_tool_invocations_turn",
    "ix_events_session",
}


def _tables(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _version(engine) -> list[str]:
    with engine.connect() as connection:
        return [row[0] for row in connection.execute(text("select * from alembic_version"))]


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


def test_a_fresh_directory_gains_a_migrated_conversation_database(tmp_path: Path) -> None:
    directory = tmp_path / "db"

    engine = open_conversations(directory)
    try:
        assert (directory / DATABASE_FILENAME).is_file()
        assert EXPECTED_TABLES <= _tables(engine)
        assert len(_version(engine)) == 1
    finally:
        engine.dispose()


def test_it_is_a_second_file_beside_the_domain_database(tmp_path: Path) -> None:
    """Two databases, two chains. Sharing the helpers must not end with
    the domain configuration's tables in this file or its version row in
    the same table."""
    from vinga_server.db import open_database

    directory = tmp_path / "db"
    domain = open_database(directory)
    store = open_conversations(directory)
    try:
        assert (directory / DOMAIN_FILENAME).is_file()
        assert (directory / DATABASE_FILENAME).is_file()
        assert DOMAIN_FILENAME != DATABASE_FILENAME
        assert "providers" not in _tables(store)
        assert "sessions" not in _tables(domain)
    finally:
        store.dispose()
        domain.dispose()


def test_an_already_migrated_conversation_database_reopens(tmp_path: Path) -> None:
    directory = tmp_path / "db"

    first = open_conversations(directory)
    version = _version(first)
    first.dispose()

    second = open_conversations(directory)
    try:
        assert EXPECTED_TABLES <= _tables(second)
        assert _version(second) == version
    finally:
        second.dispose()


def test_the_connection_is_configured_for_concurrent_use_and_erasure(tmp_path: Path) -> None:
    """WAL and a busy timeout for the same reason the domain database
    has them, and secure_delete because a deletion that leaves the words
    in a freelist page is not a deletion."""
    engine = open_conversations(tmp_path / "db")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert connection.execute(text("PRAGMA busy_timeout")).scalar() > 0
            assert connection.execute(text("PRAGMA secure_delete")).scalar() == 1
    finally:
        engine.dispose()


def test_the_cursor_tables_are_declared_autoincrement(tmp_path: Path) -> None:
    """Read off the stored DDL rather than off the metadata, because
    what matters is what SQLite was told, and it is the same assertion
    the installed-wheel step in CI makes."""
    engine = open_conversations(tmp_path / "db")
    try:
        with engine.connect() as connection:
            ddl = {
                row[0]: row[1]
                for row in connection.execute(
                    text("select name, sql from sqlite_master where type = 'table'")
                )
            }
    finally:
        engine.dispose()

    for name in CURSOR_TABLES:
        assert "AUTOINCREMENT" in ddl[name], f"{name} would reuse deleted row ids"
    assert "AUTOINCREMENT" not in ddl["tool_invocations"]


def test_a_deleted_maximum_id_is_never_issued_again(tmp_path: Path) -> None:
    """Retention deletes the newest rows of an old session, which is
    exactly the maximum. Reopened, the next insert must land past every
    id ever issued rather than back on the one a client just read."""
    directory = tmp_path / "db"
    engine = open_conversations(directory)
    try:
        with engine.begin() as connection:
            connection.execute(schema.sessions.insert().values(_session_row("first")))
            connection.execute(schema.sessions.insert().values(_session_row("second")))
        with engine.connect() as connection:
            issued = [row[0] for row in connection.execute(text("select id from sessions"))]
        with engine.begin() as connection:
            connection.execute(text("delete from sessions where id = :id"), {"id": max(issued)})
    finally:
        engine.dispose()

    reopened = open_conversations(directory)
    try:
        with reopened.begin() as connection:
            landed = connection.execute(
                schema.sessions.insert().values(_session_row("third"))
            ).inserted_primary_key[0]
    finally:
        reopened.dispose()

    assert landed > max(issued)


def test_every_index_the_queries_need_exists(tmp_path: Path) -> None:
    """Named in the migration rather than left to the metadata, so that
    a later migration can address them."""
    engine = open_conversations(tmp_path / "db")
    try:
        inspector = inspect(engine)
        found = {
            index["name"]
            for table in EXPECTED_TABLES
            for index in inspector.get_indexes(table)
        }
    finally:
        engine.dispose()

    assert EXPECTED_INDEXES <= found


def test_the_source_column_refuses_a_token_outside_the_closed_set(tmp_path: Path) -> None:
    """The value of the column is that a query may enumerate it, so the
    closed set is a property of the schema and not only of the
    classifier that fills it."""
    from sqlalchemy.exc import IntegrityError

    engine = open_conversations(tmp_path / "db")
    try:
        with engine.begin() as connection:
            connection.execute(schema.sessions.insert().values(_session_row("s")))
            connection.execute(
                schema.turns.insert().values(session="s", t_ms=0, tool_calls=1)
            )
        rejected = False
        try:
            with engine.begin() as connection:
                connection.execute(
                    schema.tool_invocations.insert().values(
                        turn=1,
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


def test_the_path_is_answered_without_creating_anything(tmp_path: Path) -> None:
    """The question a read path and the purge command both ask before
    deciding whether there is a store at all. Asking must not bring one
    into existence."""
    path = conversations_path(tmp_path)

    assert path.name == DATABASE_FILENAME
    assert not path.exists()
    assert not tmp_path.joinpath(DATABASE_FILENAME).exists()
