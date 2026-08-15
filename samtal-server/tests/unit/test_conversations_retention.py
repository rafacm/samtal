"""Retention and purging: what leaves the file, and whether it really
leaves it.

Deletion here is the right-to-delete made operational, so the tests are
about the file rather than about a query. A deletion honored in the
query planner and broken in the file bytes is not honored, which is why
the last case plants a credential-shaped utterance, purges its session,
and hunts the bytes through the database and both of its sidecars.

Nothing sleeps. The store takes a wall clock, so a session recorded
"ninety-one days ago" is a clock the test chose, and the cutoff
boundary is asserted on the day either side of it rather than
approached.
"""

import datetime as dt
import logging
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from samtal_server.conversations.records import ToolInvocation, TurnRecord
from samtal_server.conversations.store import (
    RETENTION_DAYS_DEFAULT,
    ConversationStore,
    open_conversations,
    purge,
)

NOW = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)

SENTINEL = "hunter2-not-a-real-credential-9f31c7"


def manifest(started_at: dt.datetime, device: str = "aa:bb:cc:dd:ee:ff") -> dict[str, Any]:
    return {
        "started_at": started_at.isoformat(),
        "server": {"version": "0.1.0", "revision": "abc1234"},
        "device": {"mac": device, "client": "test"},
        "protocol": "1",
        "agent": "sam",
        "agents": ["sam"],
        "providers": {},
    }


def a_turn(heard: str = "hello there") -> TurnRecord:
    return TurnRecord(
        t_ms=1000,
        agent="sam",
        heard=heard,
        reply="Hi.",
        tools=(ToolInvocation(position=0, source="builtin", name="remember", result="ok"),),
    )


@pytest.fixture
def stores(tmp_path: Path):
    built: list[ConversationStore] = []

    def _build(**options: Any) -> ConversationStore:
        store = ConversationStore(tmp_path, now=lambda: NOW, **options)
        built.append(store)
        return store

    yield _build
    for store in built:
        store.stop()


def record(store: ConversationStore, session: str, started_at: dt.datetime, **facts: Any) -> None:
    """One whole session, start to close, so every table has a row to
    lose."""
    store.open_session(session, 100.0, manifest(started_at, **facts))
    store.record_event(session, "heard", logging.INFO, {"duration_s": 1.0}, 101.0)
    store.record_turn(session, a_turn())
    store.close_session(session, duration_s=5.0, reason="client")


def counts(directory: Path) -> dict[str, int]:
    engine = open_conversations(directory)
    try:
        with engine.connect() as connection:
            return {
                table: connection.execute(text(f"select count(*) from {table}")).scalar_one()
                for table in ("sessions", "turns", "tool_invocations", "events")
            }
    finally:
        engine.dispose()


def stored_sessions(directory: Path) -> list[str]:
    engine = open_conversations(directory)
    try:
        with engine.connect() as connection:
            return [
                row[0]
                for row in connection.execute(text("select session from sessions order by id"))
            ]
    finally:
        engine.dispose()


# Retention


def test_retention_deletes_at_the_cutoff_and_not_a_day_inside_it(
    tmp_path: Path, stores
) -> None:
    """The boundary is the whole of the policy: `retention_days` days
    before the store's own clock, applied to `started_at`, which is the
    column that survives both storage switches for exactly this
    reason."""
    store = stores(retention_days=90)
    store.start()
    record(store, "just-inside", NOW - dt.timedelta(days=89, hours=23))
    record(store, "just-outside", NOW - dt.timedelta(days=90, hours=1))
    store.stop()

    # Pruning runs at each close, so the second session's close is what
    # took the first one's older neighbour.
    assert stored_sessions(tmp_path) == ["just-inside"]
    assert counts(tmp_path) == {
        "sessions": 1,
        "turns": 1,
        "tool_invocations": 1,
        "events": 1,
    }


def test_retention_zero_keeps_everything(tmp_path: Path, stores) -> None:
    """0 is the documented opt-out, and it has to be chosen: the default
    is a window, because a store with no policy retains forever."""
    store = stores(retention_days=0)
    store.start()
    record(store, "ancient", NOW - dt.timedelta(days=4000))
    record(store, "recent", NOW - dt.timedelta(hours=1))
    store.stop()

    assert stored_sessions(tmp_path) == ["ancient", "recent"]
    assert RETENTION_DAYS_DEFAULT == 90


def test_retention_runs_at_start_against_what_a_previous_run_left(
    tmp_path: Path, stores
) -> None:
    """A deployment that recorded and was then restarted must not have
    to hold a conversation before its old sessions go."""
    first = stores(retention_days=0)
    first.start()
    record(first, "ancient", NOW - dt.timedelta(days=400))
    first.stop()
    assert stored_sessions(tmp_path) == ["ancient"]

    second = stores(retention_days=90)
    second.start()
    second.stop()

    assert stored_sessions(tmp_path) == []


def test_pruning_says_how_many_sessions_it_took(
    tmp_path: Path, stores, caplog: pytest.LogCaptureFixture
) -> None:
    """A count and nothing else: which sessions were pruned is a
    question for the store, not for the log."""
    seeding = stores(retention_days=0)
    seeding.start()
    record(seeding, "old-one", NOW - dt.timedelta(days=200))
    record(seeding, "old-two", NOW - dt.timedelta(days=300))
    seeding.stop()

    with caplog.at_level(logging.INFO):
        pruning = stores(retention_days=90)
        pruning.start()
        pruning.stop()

    (pruned,) = [
        record_
        for record_ in caplog.records
        if getattr(record_, "event", "") == "conversations_pruned"
    ]
    assert pruned.levelno == logging.INFO
    assert pruned.sessions == 2
    assert "old-one" not in pruned.getMessage()


# Purging


def test_purge_by_session_takes_that_session_and_its_children(
    tmp_path: Path, stores
) -> None:
    store = stores(retention_days=0)
    store.start()
    record(store, "keep", NOW - dt.timedelta(hours=2))
    record(store, "drop", NOW - dt.timedelta(hours=1))
    store.stop()

    taken = purge(tmp_path, session="drop")

    assert taken == {"sessions": 1, "turns": 1, "tool_invocations": 1, "events": 1}
    assert stored_sessions(tmp_path) == ["keep"]


def test_purge_by_device_takes_every_session_of_that_device(
    tmp_path: Path, stores
) -> None:
    store = stores(retention_days=0)
    store.start()
    record(store, "kitchen-one", NOW, device="aa:aa:aa:aa:aa:aa")
    record(store, "kitchen-two", NOW, device="aa:aa:aa:aa:aa:aa")
    record(store, "study", NOW, device="bb:bb:bb:bb:bb:bb")
    store.stop()

    taken = purge(tmp_path, device="aa:aa:aa:aa:aa:aa")

    assert taken["sessions"] == 2
    assert stored_sessions(tmp_path) == ["study"]


def test_purge_before_a_date_keeps_the_day_itself(tmp_path: Path, stores) -> None:
    """`--before 2026-08-15` means midnight UTC of the fifteenth, so a
    session that started that morning survives."""
    store = stores(retention_days=0)
    store.start()
    record(store, "yesterday", dt.datetime(2026, 8, 14, 23, 59, tzinfo=dt.UTC))
    record(store, "this-morning", dt.datetime(2026, 8, 15, 0, 1, tzinfo=dt.UTC))
    store.stop()

    taken = purge(tmp_path, before=dt.date(2026, 8, 15))

    assert taken["sessions"] == 1
    assert stored_sessions(tmp_path) == ["this-morning"]


def test_selectors_combine_with_and(tmp_path: Path, stores) -> None:
    """Given together they narrow rather than widen, so an operator can
    say "that device, but only what it recorded before Friday"."""
    store = stores(retention_days=0)
    store.start()
    kitchen, study = "aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"
    record(store, "old-kitchen", dt.datetime(2026, 8, 1, tzinfo=dt.UTC), device=kitchen)
    record(store, "new-kitchen", dt.datetime(2026, 8, 14, tzinfo=dt.UTC), device=kitchen)
    record(store, "old-study", dt.datetime(2026, 8, 1, tzinfo=dt.UTC), device=study)
    store.stop()

    taken = purge(tmp_path, device=kitchen, before=dt.date(2026, 8, 10))

    assert taken["sessions"] == 1
    assert stored_sessions(tmp_path) == ["new-kitchen", "old-study"]


def test_a_purge_that_matches_nothing_is_zero_rather_than_an_error(
    tmp_path: Path, stores
) -> None:
    store = stores(retention_days=0)
    store.start()
    record(store, "keep", NOW)
    store.stop()

    assert purge(tmp_path, session="never-existed") == {
        "sessions": 0,
        "turns": 0,
        "tool_invocations": 0,
        "events": 0,
    }
    assert stored_sessions(tmp_path) == ["keep"]


def test_a_purge_with_no_selector_is_refused(tmp_path: Path, stores) -> None:
    """A guard against the caller rather than against the operator: the
    CLI refuses this before it gets here, and a purge with no selector
    would be a truncation wearing the same command name."""
    store = stores(retention_days=0)
    store.start()
    record(store, "keep", NOW)
    store.stop()

    with pytest.raises(ValueError):
        purge(tmp_path)

    assert stored_sessions(tmp_path) == ["keep"]


def test_a_purged_utterance_is_gone_from_the_files_bytes(tmp_path: Path, stores) -> None:
    """The sentinel case. `secure_delete` overwrites the freed pages
    with zeros instead of leaving them in the freelist, and the
    truncating checkpoint keeps the deleted frames from surviving in the
    write-ahead log, so what is asserted is the bytes of the database
    and of both sidecars rather than the answer to a query."""
    store = stores(retention_days=0)
    store.start()
    store.open_session("secret", 100.0, manifest(NOW))
    store.record_turn("secret", a_turn(heard=f"my password is {SENTINEL}"))
    store.close_session("secret", duration_s=3.0, reason="client")
    store.stop()

    database = tmp_path / "conversations.db"
    assert SENTINEL.encode() in database.read_bytes(), "the test never stored the sentinel"

    purge(tmp_path, session="secret")

    for path in (database, tmp_path / "conversations.db-wal", tmp_path / "conversations.db-shm"):
        if path.exists():
            assert SENTINEL.encode() not in path.read_bytes(), f"{path.name} still holds it"
    assert stored_sessions(tmp_path) == []
