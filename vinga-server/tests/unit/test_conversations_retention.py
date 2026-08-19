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
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from vinga_server.conversations.records import ToolInvocation, TurnRecord
from vinga_server.conversations.store import (
    RETENTION_DAYS_DEFAULT,
    ConversationStore,
    open_conversations,
    purge,
    read_conversations,
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
        at=101.0,
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


def _until(ready, complaint: str) -> None:
    """Wait for what the test is about, on a store that is still
    running.

    Still running matters here: stopping disposes the engine, which
    checkpoints the log on the way out and takes the sidecar cases
    away. Not an empty queue either, because `get()` returns before the
    record it returned has been written, so waiting on the condition
    itself is what has no window in it."""
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.005)
    raise AssertionError(complaint)


def _holds(path: Path, sentinel: str) -> bool:
    return sentinel.encode() in path.read_bytes()


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

    assert taken.counts() == {"sessions": 1, "turns": 1, "tool_invocations": 1, "events": 1}
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

    assert taken.sessions == 2
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

    assert taken.sessions == 1
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

    assert taken.sessions == 1
    assert stored_sessions(tmp_path) == ["new-kitchen", "old-study"]


def test_a_purge_that_matches_nothing_is_zero_rather_than_an_error(
    tmp_path: Path, stores
) -> None:
    store = stores(retention_days=0)
    store.start()
    record(store, "keep", NOW)
    store.stop()

    assert purge(tmp_path, session="never-existed").counts() == {
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
    """The sentinel case, asserted against a store that is still open.

    Stopping the writer first would checkpoint the log on the way out
    and take the interesting half of the case away, so the store stays
    up: the sentinel is proven to be in the real `-wal` file before the
    purge, and the purge's own checkpoint is what has to remove it.
    `secure_delete` is what keeps the freed pages of the database from
    holding it too."""
    store = stores(retention_days=0)
    store.start()
    store.open_session("secret", 100.0, manifest(NOW))
    store.record_turn("secret", a_turn(heard=f"my password is {SENTINEL}"))
    store.close_session("secret", duration_s=3.0, reason="client")

    database = tmp_path / "conversations.db"
    log = tmp_path / "conversations.db-wal"
    _until(lambda: _holds(log, SENTINEL), "the sentinel never reached the write-ahead log")

    taken = purge(tmp_path, session="secret")

    assert taken.truncated
    assert log.stat().st_size == 0
    for path in (database, log, tmp_path / "conversations.db-shm"):
        assert path.exists(), f"{path.name} went missing"
        assert SENTINEL.encode() not in path.read_bytes(), f"{path.name} still holds it"
    assert stored_sessions(tmp_path) == []


def test_a_reader_defers_the_stores_truncation_and_the_next_marker_takes_it(
    tmp_path: Path, stores
) -> None:
    """A checkpoint cannot move frames a reader is still reading. It
    reports busy rather than raising, the deletion stands, and the
    truncation stays owed until a checkpoint gets its moment.

    Asserted on the database file rather than on the log, because that
    is where the evidence is: `secure_delete` writes the zeroed page
    into the write-ahead log, so until the checkpoint copies it over the
    old page the sentinel is still in `conversations.db` itself.
    """
    seeding = stores(retention_days=0)
    seeding.start()
    seeding.open_session("old", 100.0, manifest(NOW - dt.timedelta(days=400)))
    seeding.record_turn("old", a_turn(heard=f"my password is {SENTINEL}"))
    seeding.close_session("old", duration_s=3.0, reason="client")
    seeding.stop()

    database = tmp_path / "conversations.db"
    assert _holds(database, SENTINEL)

    # Held across the pruning store's start, which is when it prunes.
    reader = read_conversations(tmp_path)
    held = reader.connect()
    held.execute(text("select count(*) from sessions")).scalar()
    pruning = stores(retention_days=90)
    pruning.start()
    _until(
        lambda: pruning._truncation_due,
        "the prune never ran, or was checkpointed past a held reader",
    )

    assert stored_sessions(tmp_path) == []
    assert _holds(database, SENTINEL), "the frames went while a reader held them"

    held.close()
    reader.dispose()

    # The next quiet moment: a marker on the still-running store.
    pruning.open_session("after", 200.0, manifest(NOW))
    pruning.record_turn("after", a_turn(heard="nothing secret"))
    _until(
        lambda: not pruning._truncation_due,
        "the owed truncation was never taken at a later marker",
    )

    assert not _holds(database, SENTINEL)


def test_a_reader_defers_a_purges_truncation_and_a_later_purge_takes_it(
    tmp_path: Path, stores
) -> None:
    """A purge runs in a process of its own with no next marker to wait
    for, so it answers that the truncation is owed instead of pretending
    otherwise, and the next purge is what the command tells the operator
    will take it."""
    store = stores(retention_days=0)
    store.start()
    store.open_session("secret", 100.0, manifest(NOW))
    store.record_turn("secret", a_turn(heard=f"my password is {SENTINEL}"))
    store.open_session("other", 100.0, manifest(NOW, device="bb:bb:bb:bb:bb:bb"))
    store.close_session("secret", duration_s=3.0, reason="client")

    log = tmp_path / "conversations.db-wal"
    _until(lambda: _holds(log, SENTINEL), "the sentinel never reached the write-ahead log")

    reader = read_conversations(tmp_path)
    held = reader.connect()
    held.execute(text("select count(*) from sessions")).scalar()
    try:
        taken = purge(tmp_path, session="secret")
        assert taken.sessions == 1
        assert not taken.truncated
        assert _holds(log, SENTINEL)
    finally:
        held.close()
        reader.dispose()

    assert purge(tmp_path, session="other").truncated

    assert log.stat().st_size == 0
    assert not _holds(log, SENTINEL)
    assert not _holds(tmp_path / "conversations.db", SENTINEL)


def test_the_checkpoint_runs_outside_a_transaction(tmp_path: Path, stores) -> None:
    """SQLite refuses to checkpoint inside a transaction, and every
    connection this engine hands out through SQLAlchemy opens BEGIN
    IMMEDIATE before its first statement. Run through the engine the
    pragma raises every time, which a suppressed exception turns into a
    checkpoint that silently never happened; this is the pin that it
    runs and answers."""
    from vinga_server.conversations.store import _checkpoint

    store = stores(retention_days=0)
    store.start()
    record(store, "one", NOW)
    log = tmp_path / "conversations.db-wal"
    _until(lambda: stored_sessions(tmp_path) == ["one"], "the session was never committed")
    assert log.stat().st_size > 0

    assert _checkpoint(store._engine) is True
    assert log.stat().st_size == 0
