"""Retention: what leaves the store, and what "leaves" means.

Deletion here is the right-to-delete made operational, and what that
promises is now the database server's rule rather than a file trick
(#283). Precisely: a deleted row is invisible to every transaction that
begins after the deletion commits, including the analyst role's; a
repeatable-read transaction already in flight when it commits keeps
seeing the row until it ends, which is what MVCC is; and reclaiming the
pages the row occupied is autovacuum's, not a per-delete overwrite.

The held-snapshot case below pins both halves of that in one test,
which is the honest replacement for the SQLite-era pair it retires. Two
tests went with the mechanism: one that hunted a planted credential
through `conversations.db` and both of its sidecars after a deferred
truncating checkpoint, and one that pinned the checkpoint running
outside a transaction. Neither has an analogue: there is no file to
read, no write-ahead log to truncate, and no freed page this process
could write zeros over.

Nothing sleeps. The store takes a wall clock, so a session recorded
"ninety-one days ago" is a clock the test chose, and the cutoff
boundary is asserted on the day either side of it rather than
approached.
"""

import datetime as dt
import logging
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations.records import ToolInvocation, TurnRecord
from vinga_server.conversations.store import (
    RETENTION_DAYS_DEFAULT,
    ConversationStore,
    open_conversations,
)
from vinga_server.db import read_engine

NOW = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)

SENTINEL = "hunter2-not-a-real-credential-9f31c7"


def thread_for(session: str) -> str:
    """One thread per session, in the shape the runtime mints.

    A fresh wake starts a fresh thread, so two sessions in these suites
    are two conversations rather than one; deriving the id from the
    session name keeps that true without every test having to say it.
    """
    return uuid.uuid5(uuid.NAMESPACE_OID, session).hex


# The thread a turn belongs to when a test does not care which.
CONVERSATION = thread_for("a-session")


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


def a_turn(heard: str = "hello there", conversation: str = CONVERSATION) -> TurnRecord:
    return TurnRecord(
        at=101.0,
        conversation=conversation,
        agent="sam",
        heard=heard,
        reply="Hi.",
        tools=(ToolInvocation(position=0, source="builtin", name="remember", result="ok"),),
    )


@pytest.fixture
def stores():
    built: list[ConversationStore] = []

    def _build(at: dt.datetime = NOW, **options: Any) -> ConversationStore:
        """One store, on a clock the test chose.

        `at` is what this store believes the time is, which decides two
        things at once: the cutoff a prune measures from, and the
        `created_at` and `last_active_at` a turn stamps on its thread.
        Both matter now that retention's unit is the thread, so a suite
        that wants an old conversation writes it through a store whose
        clock is old, which is the only difference between a synthetic
        old thread and a real one.
        """
        store = ConversationStore(DatabaseConfig(), now=lambda: at, **options)
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
    store.record_turn(session, a_turn(conversation=thread_for(session)))
    store.close_session(session, duration_s=5.0, reason="client")


def aged(stores: Any, session: str, at: dt.datetime, **facts: Any) -> None:
    """One whole session recorded as it would have been at `at`: its
    row, its thread and its events all that old, which is what a real
    session of that age left behind.

    A store of its own per session, because the clock is a store's and
    a suite that wants two ages wants two clocks.
    """
    store = stores(at=at, retention_days=0)
    store.start()
    record(store, session, at, **facts)
    store.stop()


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


def counts() -> dict[str, int]:
    engine = open_conversations(DatabaseConfig())
    try:
        with engine.connect() as connection:
            return {
                table: connection.execute(
                    text(f"select count(*) from conversations.{table}")
                ).scalar_one()
                for table in (
                    "sessions",
                    "conversations",
                    "turns",
                    "tool_invocations",
                    "events",
                )
            }
    finally:
        engine.dispose()


def stored_sessions() -> list[str]:
    engine = open_conversations(DatabaseConfig())
    try:
        with engine.connect() as connection:
            return [
                row[0]
                for row in connection.execute(
                    text("select session from conversations.sessions order by id")
                )
            ]
    finally:
        engine.dispose()


# Retention


def test_retention_deletes_at_the_cutoff_and_not_a_day_inside_it(
    stores,
) -> None:
    """The boundary is the whole of the policy: `retention_days` days
    before the store's own clock.

    What it is applied to is the thread's `last_active_at`, which is
    the change: a conversation is what retention takes, whole, and the
    session record follows it out once no turn names it any more. In a
    deployment that never resumes anything the two ages coincide, which
    is what these two sessions are.
    """
    aged(stores, "just-inside", NOW - dt.timedelta(days=89, hours=23))
    aged(stores, "just-outside", NOW - dt.timedelta(days=90, hours=1))

    pruning = stores(retention_days=90)
    pruning.start()
    pruning.stop()

    assert stored_sessions() == ["just-inside"]
    assert counts() == {
        "sessions": 1,
        "conversations": 1,
        "turns": 1,
        "tool_invocations": 1,
        "events": 1,
    }


def test_retention_zero_keeps_everything(stores) -> None:
    """0 is the documented opt-out, and it has to be chosen: the default
    is a window, because a store with no policy retains forever."""
    store = stores(retention_days=0)
    store.start()
    record(store, "ancient", NOW - dt.timedelta(days=4000))
    record(store, "recent", NOW - dt.timedelta(hours=1))
    store.stop()

    assert stored_sessions() == ["ancient", "recent"]
    assert RETENTION_DAYS_DEFAULT == 90


def test_retention_runs_at_start_against_what_a_previous_run_left(
    stores,
) -> None:
    """A deployment that recorded and was then restarted must not have
    to hold a conversation before its old sessions go."""
    aged(stores, "ancient", NOW - dt.timedelta(days=400))
    assert stored_sessions() == ["ancient"]

    second = stores(retention_days=90)
    second.start()
    second.stop()

    assert stored_sessions() == []


def test_pruning_says_how_many_threads_and_session_records_it_took(
    stores, caplog: pytest.LogCaptureFixture
) -> None:
    """Two counts and nothing else: how much dialogue left, and how many
    session records went with it. Which ones is a question for the
    store, not for the log."""
    aged(stores, "old-one", NOW - dt.timedelta(days=200))
    aged(stores, "old-two", NOW - dt.timedelta(days=300))

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
    assert (pruned.conversations, pruned.sessions) == (2, 2)
    assert "old-one" not in pruned.getMessage()


def test_a_session_older_than_the_cutoff_keeps_the_spine_a_live_thread_needs(
    stores,
) -> None:
    """The case the ruleset exists for.

    A session begun before the cutoff, holding a thread that was spoken
    to after it. Under session-age pruning its turns would go, taking
    dialogue out of a conversation somebody is still having. So the
    thread is kept whole, the session keeps the minimal row those turns
    cross-reference, and only the part of it that is telemetry, its
    events, is pruned on the session's own age.
    """
    thread = "1a2b3c4d5e6f70819a2b3c4d5e6f7081"
    began = NOW - dt.timedelta(days=100)
    opening = stores(at=began, retention_days=0)
    opening.start()
    opening.open_session("long-runner", 100.0, manifest(began))
    opening.record_event("long-runner", "heard", logging.INFO, {"duration_s": 1.0}, 101.0)
    opening.record_turn("long-runner", a_turn(conversation=thread))
    opening.stop()

    # The same session, still talking a day ago, which is what makes its
    # thread live and its own row old at the same time.
    lately = stores(at=NOW - dt.timedelta(days=1), retention_days=0)
    lately.start()
    lately.open_session("long-runner", 100.0, manifest(began))
    lately.record_turn("long-runner", a_turn(conversation=thread))
    lately.close_session("long-runner", duration_s=5.0, reason="client")
    lately.stop()

    pruning = stores(retention_days=90)
    pruning.start()
    pruning.stop()

    assert stored_sessions() == ["long-runner"]
    assert counts() == {
        "sessions": 1,
        "conversations": 1,
        "turns": 2,
        "tool_invocations": 2,
        # The decision track of a session this old is gone, whether or
        # not its row survived: it is telemetry scoped to the session
        # rather than part of the thread.
        "events": 0,
    }


def test_a_session_record_past_the_cutoff_goes_once_no_turn_names_it(
    stores,
) -> None:
    """The other half of rule 3. A session that recorded no turn, or
    whose every turn left with its thread, is a spine holding nothing
    up, and it is pruned on its own age like any other record."""
    began = NOW - dt.timedelta(days=200)
    store = stores(at=began, retention_days=0)
    store.start()
    store.open_session("silent", 100.0, manifest(began))
    store.record_event("silent", "heard", logging.INFO, {"duration_s": 1.0}, 101.0)
    store.close_session("silent", duration_s=2.0, reason="idle")
    store.stop()
    assert stored_sessions() == ["silent"]

    pruning = stores(retention_days=90)
    pruning.start()
    pruning.stop()

    assert stored_sessions() == []
    assert counts() == {
        "sessions": 0,
        "conversations": 0,
        "turns": 0,
        "tool_invocations": 0,
        "events": 0,
    }


def test_a_snapshot_open_across_a_prune_keeps_seeing_what_the_prune_took() -> None:
    """The whole of what deletion promises, both halves, in one test.

    A repeatable-read transaction opened before retention runs keeps
    seeing the row it deleted, because that is what its snapshot is; a
    transaction opened after the delete commits does not. Promising
    otherwise would be promising something MVCC does not do, and the
    generated reference and the README say it in these words.

    Held across the pruning store's start, which is when it prunes.
    """
    long_ago = NOW - dt.timedelta(days=400)
    seeding = ConversationStore(DatabaseConfig(), now=lambda: long_ago, retention_days=0)
    seeding.start()
    seeding.open_session("old", 100.0, manifest(long_ago))
    seeding.record_turn("old", a_turn(heard=f"my password is {SENTINEL}"))
    seeding.close_session("old", duration_s=3.0, reason="client")
    seeding.stop()

    reader = read_engine(DatabaseConfig())
    held = reader.connect()
    try:
        # The snapshot is taken by the first statement, so it has to
        # happen before the prune rather than after it.
        assert held.execute(text("select count(*) from conversations.sessions")).scalar() == 1

        pruning = ConversationStore(DatabaseConfig(), now=lambda: NOW, retention_days=90)
        pruning.start()
        try:
            _until(lambda: stored_sessions() == [], "the prune never ran")

            # The transaction that began before the delete committed
            # still sees the row, sentinel and all: this is the
            # weakening the docs state rather than paper over.
            assert held.execute(text("select count(*) from conversations.sessions")).scalar() == 1
            assert (
                held.execute(text("select heard from conversations.turns")).scalar()
                == f"my password is {SENTINEL}"
            )
        finally:
            pruning.stop()
    finally:
        held.close()
        reader.dispose()

    # And every transaction that begins afterwards, including the
    # analyst role's, finds nothing: not the session, not its turn, and
    # not the words in it.
    fresh = read_engine(DatabaseConfig())
    try:
        with fresh.connect() as connection:
            assert (
                connection.execute(text("select count(*) from conversations.sessions")).scalar()
                == 0
            )
            assert (
                connection.execute(
                    text("select count(*) from conversations.turns where heard like :like"),
                    {"like": f"%{SENTINEL}%"},
                ).scalar()
                == 0
            )
    finally:
        fresh.dispose()
