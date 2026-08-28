"""The durable half of a marker: what it retries, what it answers, and
what it says about a hole it could not avoid.

Driven through the store's own seams, like the suite next door: the gate
is where the writer parks, so an interleaving is arranged rather than
waited for, and the engine is replaced with one that refuses chosen
transactions, because nothing a producer can do makes a committed
database fail on the next statement.

Three properties, and each is provable only here:

- **A turn's acknowledgement speaks for that turn.** True when its
  durable transaction commits, false when the turn was dropped, and
  false rather than pending once the writer is gone.
- **The two halves of a marker have independent fates.** An events
  transaction that fails takes no turn with it, and the turns of the
  same marker are in the database when it does.
- **A dropped batch leaves a mark on the thread rather than only in a
  counter.** `conversations.incomplete` is product state, outside the
  metrics switch, latched in memory until a transaction lands it, and
  set from the first byte of a row that materializes after the loss.
"""

import datetime as dt
import logging
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from sqlalchemy import delete

from tests.support.sessions import WRITER_TIMEOUT_S as TIMEOUT_S
from tests.support.sessions import Gate, until
from tests.support.stores import CONVERSATIONS_MANIFEST as MANIFEST
from tests.support.stores import rows
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations.records import SessionTurns, TurnRecord
from vinga_server.conversations.schema import sessions as sessions_table
from vinga_server.conversations.store import (
    TURN_WRITE_ATTEMPTS,
    ConversationStore,
    open_conversations,
)

NOW = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)

CONVERSATION = "9f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5"

OTHER = "1a2b3c4d5e6f708192a3b4c5d6e7f809"


def a_turn(conversation: str = CONVERSATION, **overrides: Any) -> TurnRecord:
    fields: dict[str, Any] = {
        "at": 101.2,
        "conversation": conversation,
        "agent": "sam",
        "heard": "turn the light on",
        "reply": "Done.",
    }
    fields.update(overrides)
    return TurnRecord(**fields)


@pytest.fixture
def stores() -> Iterator[Any]:
    """Stores that are always stopped, and that never prune: every test
    here replaces the engine, and retention would be a transaction of
    its own arriving in the middle of the count."""
    built: list[ConversationStore] = []

    def _build(**options: Any) -> ConversationStore:
        store = ConversationStore(
            DatabaseConfig(), now=lambda: NOW, retention_days=0, **options
        )
        built.append(store)
        return store

    yield _build
    for store in built:
        store.stop()


class Refusing:
    """The real engine, with chosen transactions refused.

    White-box, deliberately, and for the reason the store suite gives
    for its own: the failure under test is a database refusing a write
    the writer has already accepted, and nothing a public call can do
    produces one. The transactions are counted, so a test says which of
    them fails rather than that some of them do.
    """

    def __init__(self, engine: Any, refuse: Any) -> None:
        self.engine = engine
        self.refuse = refuse
        self.begins = 0

    def begin(self) -> Any:
        self.begins += 1
        problem = self.refuse(self.begins)
        if problem is not None:
            raise problem
        return self.engine.begin()

    def dispose(self) -> None:
        self.engine.dispose()


def contended() -> BaseException:
    """A lock that did not arrive inside the timeout, which is the
    member of the db classifier's transient class every contended write
    reaches. Raised as itself rather than wrapped, because `is_busy`
    walks the chain and the top of it is a member."""
    return psycopg.errors.LockNotAvailable("canceling statement due to lock timeout")


def erased(session_id: str) -> None:
    """One session row deleted out from under a live conversation, which
    is what retention and the erasure endpoints both do to it."""
    engine = open_conversations(DatabaseConfig())
    try:
        with engine.begin() as connection:
            connection.execute(
                delete(sessions_table).where(sessions_table.c.session == session_id)
            )
    finally:
        engine.dispose()



def recording(stores: Any, refuse: Any, **options: Any) -> tuple[Any, Refusing]:
    """A store whose session is open and committed, and whose engine is
    then replaced by one that refuses chosen transactions.

    The swap happens with the writer demonstrably idle: the session row
    is in the database, so the open's own transaction is the one that
    put it there and the transactions counted afterwards are the markers
    that follow it. The session has to be opened on THIS store rather
    than on another one before it, because a writer that never saw the
    open holds no batch for the session and refuses its turns.
    """
    store = stores(**options)
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    until(lambda: rows("sessions"), "the session row never landed")
    engine = Refusing(store._engine, refuse)
    store._engine = engine
    return store, engine


# What an acknowledgement answers


def test_a_turn_is_acknowledged_when_its_transaction_commits(stores) -> None:
    """The whole of what the handle promises: true once the row is in
    the database, and not before. The gate is what makes "not before"
    assertable, since the writer is demonstrably parked in front of the
    transaction while the handle is asked."""
    gate = Gate()
    store = stores(gate=gate)
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    gate.wait()
    gate.let_through()

    acknowledgement = store.record_turn("alpha", a_turn())
    gate.wait()
    assert acknowledgement.wait(0.05) is False, "acknowledged before the commit"

    gate.open_forever()
    assert acknowledgement.wait(TIMEOUT_S) is True
    assert len(rows("turns")) == 1


def test_the_handle_a_bound_recorder_answers_is_the_stores_own(stores) -> None:
    """`SessionTurns` is what a runtime holds, and it binds a session id
    to the store and nothing else: the answer has to travel back out
    through it or milestone 4's resume has no handle to wait on."""
    store = stores()
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)

    acknowledgement = SessionTurns(store, "alpha").record_turn(a_turn())

    assert acknowledgement.wait(TIMEOUT_S) is True


def test_a_turn_for_a_tombstoned_session_is_acknowledged_false(stores) -> None:
    """A session deleted while it is still talking stops being recorded,
    and a turn already on its way is dropped rather than written as an
    orphan. The handle says so, which is what lets a caller tell a
    dropped write from a slow one."""
    gate = Gate()
    store = stores(gate=gate)
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    gate.wait()
    gate.let_through()

    # Enqueued while the writer is parked, so the deletion below is
    # guaranteed to land before the marker that would have written it.
    acknowledgement = store.record_turn("alpha", a_turn())
    gate.wait()
    erased("alpha")
    gate.open_forever()

    assert acknowledgement.wait(TIMEOUT_S) is False
    assert rows("turns") == []


def test_a_turn_the_writer_will_never_see_is_acknowledged_false(stores) -> None:
    """A store that has stopped accepts nothing more, and the handle it
    answers with is already settled: a caller waiting out its own bound
    for an answer that will never come is worse than a refusal."""
    store = stores()
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    store.stop()

    acknowledgement = store.record_turn("alpha", a_turn())

    assert acknowledgement.wait(TIMEOUT_S) is False


def test_a_turn_for_a_session_the_writer_never_opened_is_acknowledged_false(
    stores,
) -> None:
    """The refusal the writer already made, now answerable. It cannot
    happen from the real call sites, and a handle that stayed pending
    for a defect would make the defect look like a slow database."""
    store = stores()
    store.start()

    acknowledgement = store.record_turn("ghost", a_turn())
    store.stop()

    assert acknowledgement.wait(TIMEOUT_S) is False




# Retrying, and giving up


def test_a_transient_refusal_is_retried_in_place(stores) -> None:
    """A lock that did not arrive is a transaction that would very
    likely commit if it were simply made again, so the writer makes it
    again rather than counting a loss. The engine refuses the first two
    attempts of the turn's transaction and then works, which is the
    whole scenario, and it happens with no sleep anywhere in it."""
    store, engine = recording(
        stores, lambda count: contended() if count <= 2 else None
    )

    acknowledgement = store.record_turn("alpha", a_turn())
    store.stop()

    assert acknowledgement.wait(TIMEOUT_S) is True
    assert engine.begins == TURN_WRITE_ATTEMPTS
    assert len(rows("turns")) == 1


def test_an_exhausted_budget_drops_the_batch_and_answers_false(stores) -> None:
    """Three attempts and no fourth: a contention that outlasts the
    budget is a hole, and the answer says so rather than waiting."""
    store, engine = recording(stores, lambda count: contended())

    acknowledgement = store.record_turn("alpha", a_turn())
    store.stop()

    assert acknowledgement.wait(TIMEOUT_S) is False
    assert engine.begins == TURN_WRITE_ATTEMPTS
    assert rows("turns") == []


def test_a_refusal_that_is_not_transient_is_not_retried(stores) -> None:
    """The classifier's closed set is the whole rule. A database saying
    no for a reason a fourth attempt does not change is answered at
    once, because retrying it is only latency in front of the same
    hole."""
    store, engine = recording(stores, lambda count: RuntimeError("no"))

    acknowledgement = store.record_turn("alpha", a_turn())
    store.stop()

    assert acknowledgement.wait(TIMEOUT_S) is False
    assert engine.begins == 1


# The two halves, and their independent fates


def test_an_events_failure_drops_events_and_keeps_the_turns(stores) -> None:
    """The split, stated as the only thing that can prove it: the same
    marker's turn is in the database and its events are not. Under one
    transaction the turn would have gone with them, which is the silent
    hole in product state this split exists to close.

    The marker's first transaction is the durable half and its second is
    the events, so refusing the second refuses exactly the telemetry."""
    store, _ = recording(
        stores, lambda count: RuntimeError("no") if count == 2 else None
    )

    store.record_event("alpha", "heard", logging.INFO, {"duration_s": 1.0}, 101.0)
    acknowledgement = store.record_turn("alpha", a_turn())
    store.close_session("alpha", duration_s=2.0, reason="client")
    store.stop()

    assert acknowledgement.wait(TIMEOUT_S) is True
    assert len(rows("turns")) == 1
    assert rows("events") == []
    # The loss is counted where a lost record has always been counted.
    (session,) = rows("sessions")
    assert session["dropped"] == 1
    # And the thread is whole: no turn was lost, so nothing claims one
    # was. An events failure is telemetry, not a gap in the record.
    (thread,) = rows("conversations")
    assert thread["incomplete"] is False


# The incomplete latch


def test_a_dropped_batch_flags_its_thread_at_the_next_marker(stores) -> None:
    """The hole stops being silent. The flag is written in a
    transaction of its own at the first marker after the loss, because
    a flag that rolled back with a turn would be lost exactly when it
    is true."""
    store, _ = recording(
        stores, lambda count: RuntimeError("no") if count == 2 else None
    )

    first = store.record_turn("alpha", a_turn())
    lost = store.record_turn("alpha", a_turn(heard="and the other one"))
    kept = store.record_turn("alpha", a_turn(heard="and now the kitchen"))
    store.stop()

    assert first.wait(TIMEOUT_S) is True
    assert lost.wait(TIMEOUT_S) is False
    assert kept.wait(TIMEOUT_S) is True
    (thread,) = rows("conversations")
    assert thread["incomplete"] is True
    # Two turns, not three: the flag says a write was lost and does not
    # pretend to have recovered it.
    assert len(rows("turns")) == 2
    # And the title is still the first utterance, which is the one thing
    # a later turn may never rewrite.
    assert thread["title"] == "turn the light on"


@pytest.mark.parametrize("metrics", [True, False])
def test_the_flag_is_written_whatever_the_metrics_switch_says(
    stores, metrics: bool
) -> None:
    """Product state rather than telemetry, and the difference is
    exactly this: `sessions.dropped` is zeroed under metrics-off and a
    thread with a hole in it is true either way. A deployment that
    stores no measurements still has to be told its record has gaps
    before it resumes from one."""
    store, _ = recording(
        stores,
        lambda count: RuntimeError("no") if count == 2 else None,
        metrics=metrics,
    )

    store.record_turn("alpha", a_turn())
    store.record_turn("alpha", a_turn(heard="and the other one"))
    store.record_turn("alpha", a_turn(heard="and now the kitchen"))
    store.close_session("alpha", duration_s=3.0, reason="client")
    store.stop()

    (thread,) = rows("conversations")
    assert thread["incomplete"] is True
    (session,) = rows("sessions")
    assert session["dropped"] == (1 if metrics else 0)


def test_a_lost_first_turn_leaves_no_thread_and_no_flag(stores) -> None:
    """There is nothing to flag and nothing is invented. A row created
    to hold the flag would be an empty thread, which is worse than a
    pending one: it would be listed, offered as a resume candidate and
    have no dialogue behind it."""
    store, _ = recording(stores, lambda count: RuntimeError("no"))

    acknowledgement = store.record_turn("alpha", a_turn())
    store.stop()

    assert acknowledgement.wait(TIMEOUT_S) is False
    assert rows("conversations") == []
    assert rows("turns") == []


def test_a_thread_whose_first_turn_was_lost_materializes_already_flagged(
    stores,
) -> None:
    """The pending id applied to the row that finally appears, inside
    the transaction that creates it, so there is no instant at which the
    row exists and claims to be whole.

    The title comes from the turn that landed, because a title is the
    earliest utterance this store HAS: naming the thread after a turn
    that was never written would be a name for something nobody can
    read.
    """
    store, _ = recording(
        stores, lambda count: RuntimeError("no") if count == 1 else None
    )

    store.record_turn("alpha", a_turn(heard="the one that was lost"))
    store.record_turn("alpha", a_turn(heard="the one that landed"))
    store.stop()

    (thread,) = rows("conversations")
    assert thread["incomplete"] is True
    assert thread["title"] == "the one that landed"


def test_a_later_success_never_clears_the_flag(stores) -> None:
    """An acknowledgement speaks for its own turn, and so does the
    absence of one. A thread that lost a turn in the middle keeps
    saying so however many turns land after it, which is what the
    resume path reads instead of trusting the newest write."""
    store, _ = recording(
        stores, lambda count: RuntimeError("no") if count == 2 else None
    )

    store.record_turn("alpha", a_turn(heard="the first"))
    store.record_turn("alpha", a_turn(heard="the lost one"))
    store.record_turn("alpha", a_turn(heard="the third"))
    store.record_turn("alpha", a_turn(heard="the fourth"))
    store.stop()

    (thread,) = rows("conversations")
    assert thread["incomplete"] is True
    assert [turn["heard"] for turn in rows("turns")] == [
        "the first",
        "the third",
        "the fourth",
    ]


def test_a_loss_flags_only_the_threads_it_touched(stores) -> None:
    """The latch is per thread, not per session: a session that talked
    to two agents holds two threads, and a batch that dropped one of
    them says nothing about the other."""
    store, _ = recording(
        stores, lambda count: RuntimeError("no") if count == 3 else None
    )

    store.record_turn("alpha", a_turn())
    store.record_turn("alpha", a_turn(conversation=OTHER, agent="ada"))
    store.record_turn("alpha", a_turn(conversation=OTHER, agent="ada"))
    store.record_turn("alpha", a_turn())
    store.stop()

    flagged = {row["conversation"]: row["incomplete"] for row in rows("conversations")}
    assert flagged == {CONVERSATION: False, OTHER: True}
