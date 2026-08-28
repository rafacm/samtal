"""The writer: what it commits, when, and what it refuses to say.

Everything here is driven through the store's own seams rather than
through timing, because every property under test is about ordering
between a session loop and a background thread and a sleep would pin
none of them. The gate below is the seam the writer parks on, so
"invisible before the marker" is asserted with the writer demonstrably
stopped in front of that marker rather than after a wait that happened
to be long enough.

The two guarantees worth stating out loud, since the tests below are
the only place they are provable:

- **A marker commits its own session and no other.** One queue carries
  every session, so the interleaved case is the one that would fail if
  the writer batched globally.
- **Nothing the store says carries what the store holds.** The last
  tests plant a credential-shaped sentinel in a row and in an exception
  message and hunt it through both shipped log formats, the event
  fields, an attached server tap and the process output.
"""

import datetime as dt
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from tests.support.sessions import WRITER_TIMEOUT_S as TIMEOUT_S
from tests.support.sessions import Gate
from tests.support.stores import CONVERSATIONS_MANIFEST as MANIFEST
from tests.support.stores import rows
from vinga_server import logs
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations import store as store_module
from vinga_server.conversations.records import ToolInvocation, TurnLeg, TurnRecord
from vinga_server.conversations.store import (
    MAX_EVENTS_IN_FLIGHT,
    RETENTION_DAYS_DEFAULT,
    STOP_TIMEOUT_S,
    ConversationStore,
    _Batch,
)
from vinga_server.db import LOCK_TIMEOUT_MS, read_engine
from vinga_server.events import attach_server_tap, detach_server_tap

# A value that must never appear anywhere but the database file, shaped
# like something an operator would be horrified to find in a log.
SENTINEL = "hunter2-not-a-real-credential-9f31c7"

class Wedged:
    """A gate that never lets go. What `stop()` has to survive."""

    def __init__(self) -> None:
        self.arrived = threading.Event()
        self.forever = threading.Event()

    def __call__(self) -> None:
        self.arrived.set()
        self.forever.wait(timeout=TIMEOUT_S)


@pytest.fixture
def stores():
    """Build stores that are always stopped, so no test leaves a writer
    thread or an open engine behind."""
    built: list[ConversationStore] = []

    def _build(**options: Any) -> ConversationStore:
        store = ConversationStore(DatabaseConfig(), **options)
        built.append(store)
        return store

    yield _build
    for store in built:
        store.stop()


def _until(ready: Any, complaint: str) -> None:
    """Wait for what the test is about, on a running writer.

    Not for an empty queue: `get()` returns before the record it
    returned has been accepted, so an empty queue says the writer has
    taken the last item and nothing about what it did with it. Waiting
    on the condition itself has no such window, and a wait that never
    ends is the test failing with its own sentence."""
    deadline = time.monotonic() + TIMEOUT_S
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.005)
    raise AssertionError(complaint)


CONVERSATION = "9f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5"


def a_turn(**overrides: Any) -> TurnRecord:
    fields: dict[str, Any] = {
        "at": 101.2,
        "conversation": CONVERSATION,
        "agent": "sam",
        "heard": "turn the light on",
        "heard_duration_s": 1.4,
        "language": "en",
        "language_confidence": 0.98,
        "reply": "Done.",
        "asr_ms": 210,
        "first_token_ms": 340,
        "llm_ms": 900,
        "tts_first_audio_ms": 260,
        "rounds": 2,
        "input_tokens": 512,
        "output_tokens": 24,
        "tools": (
            ToolInvocation(
                position=0,
                source="mcp",
                entry="home",
                name="turn_on_light",
                arguments={"room": "kitchen"},
                result="ok",
                duration_ms=42,
            ),
        ),
    }
    fields.update(overrides)
    return TurnRecord(**fields)


# The marker policy


def test_the_session_row_is_visible_from_the_open(stores) -> None:
    """The open is its own marker, which is what lets a page opened mid
    conversation find the session it is about."""
    gate = Gate()
    store = stores(gate=gate)
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    gate.wait()
    gate.let_through()
    # The second marker's arrival is the proof the first one committed:
    # the writer is single threaded, so being in front of this one means
    # being past that one.
    store.record_turn("alpha", a_turn())
    gate.wait()

    (session,) = rows("sessions")
    assert session["session"] == "alpha"
    assert session["device"] == "aa:bb:cc:dd:ee:ff"
    assert session["agent"] == "sam"
    assert session["started_at"] == MANIFEST["started_at"]
    assert session["closed_at"] is None
    assert (session["metrics"], session["text"]) == (1, 1)
    assert rows("turns") == []

    gate.open_forever()


def test_rows_are_invisible_before_their_marker_and_visible_after(
    stores,
) -> None:
    """A turn's rows land with the turn, not as they arrive: the writer
    accumulates in memory and holds no transaction between markers."""
    gate = Gate()
    store = stores(gate=gate)
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    gate.wait()
    gate.let_through()

    store.record_event("alpha", "heard", logging.INFO, {"duration_s": 1.4}, 101.4)
    store.record_turn("alpha", a_turn())
    gate.wait()

    assert rows("turns") == []
    assert rows("events") == []

    gate.open_forever()
    store.stop()

    (turn,) = rows("turns")
    assert turn["heard"] == "turn the light on"
    assert turn["tool_calls"] == 1
    (invocation,) = rows("tool_invocations")
    assert invocation["turn"] == turn["id"]
    assert invocation["entry"] == "home"
    (event,) = rows("events")
    assert (event["name"], event["t_ms"]) == ("heard", 1400)


def test_one_sessions_marker_exposes_nothing_of_another(stores) -> None:
    """One queue, many batches. A global next-marker policy would commit
    session B's half-assembled turn when session A completed one, which
    is what this interleaving would catch."""
    gate = Gate()
    store = stores(gate=gate)
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    gate.wait()
    gate.let_through()
    store.open_session("beta", 100.0, MANIFEST)
    gate.wait()
    gate.let_through()

    # Session beta is mid-turn: events accumulated, no marker yet.
    store.record_event("beta", "heard", logging.INFO, {"duration_s": 2.0}, 102.0)
    store.record_event("beta", "llm_round", logging.INFO, {"rounds": 1}, 102.5)
    # Session alpha completes one.
    store.record_event("alpha", "heard", logging.INFO, {"duration_s": 1.0}, 101.0)
    store.record_turn("alpha", a_turn())
    gate.wait()
    gate.let_through()
    # And another alpha marker, whose arrival proves the one above
    # committed.
    store.record_turn("alpha", a_turn(at=104.0))
    gate.wait()

    assert [row["session"] for row in rows("turns")] == ["alpha"]
    assert [row["session"] for row in rows("events")] == ["alpha"]

    gate.open_forever()
    store.stop()

    assert {row["session"] for row in rows("events")} == {"alpha", "beta"}


# The bound, and what it may not drop


def test_events_beyond_the_bound_are_dropped_counted_and_said_once(
    stores, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The droppable class, dropped at the producer so the session loop
    never waits. The count lands on the session row at close, which is
    how the store records its own incompleteness."""
    monkeypatch.setattr(store_module, "MAX_EVENTS_IN_FLIGHT", 4)
    gate = Gate()
    store = stores(gate=gate)
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    gate.wait()

    with caplog.at_level(logging.INFO):
        for index in range(10):
            store.record_event("alpha", "vad_edge", logging.DEBUG, {"n": index}, 101.0)

    dropped = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "conversations_dropped"
    ]
    assert len(dropped) == 1, "the drop is reported once per session, not once per record"
    assert dropped[0].levelno == logging.WARNING
    assert dropped[0].session == "alpha"

    gate.open_forever()
    store.close_session("alpha", duration_s=12.0, reason="client")
    store.stop()

    (session,) = rows("sessions")
    assert session["dropped"] == 6
    assert len(rows("events")) == 4


def test_the_bound_counts_events_the_writer_is_holding_in_a_batch(
    tmp_path: Path, stores, caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In flight means not yet written off, which a batch nobody has
    committed is not.

    A count that stopped at the queue would bound nothing: the writer
    here is running and drains everything it is given, so a session
    that never reaches a marker would hold its events in memory while
    the producer saw a fresh allowance every time, which is unbounded
    memory behind a bound that reads as satisfied. The events below sit
    in the batch, demonstrably off the queue, and the allowance stays
    spent."""
    monkeypatch.setattr(store_module, "MAX_EVENTS_IN_FLIGHT", 4)
    store = stores()
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)

    with caplog.at_level(logging.INFO):
        for index in range(10):
            store.record_event("alpha", "vad_edge", logging.DEBUG, {"n": index}, 101.0)
    # White-box for this file's reads of the writer, and its own thread
    # is the reason. The store's promise is that a conversation's rows
    # land and that a bounded queue never becomes unbounded memory, and
    # both are about work in flight on a thread nobody outside can see:
    # a batch not yet committed is by definition not in the database, so
    # a public read answers the same before and after the bug. The
    # thread reads below are the join that keeps one test's writer from
    # outliving it.
        _until(
            lambda: len(store._batches.get("alpha", _Batch()).events) == 4,
            "the writer never took the accepted events into its batch",
        )

    # Off the queue and into the batch, with no marker to commit them.
    assert store._queue.empty()
    assert store._in_flight == 4
    assert (
        len(
            [
                record
                for record in caplog.records
                if getattr(record, "event", "") == "conversations_dropped"
            ]
        )
        == 1
    )

    # And the allowance comes back when the marker writes them off.
    # White-box, per the note at the batch assertion above.
    store.record_turn("alpha", a_turn())
    _until(
        lambda: store._in_flight == 0,
        "a committed batch never gave its allowance back",
    )
    store.record_event("alpha", "vad_edge", logging.DEBUG, {"n": 99}, 102.0)
    store.close_session("alpha", duration_s=1.0, reason="client")
    store.stop()

    assert len(rows("events")) == 5
    (session,) = rows("sessions")
    assert session["dropped"] == 6


def test_control_records_are_never_dropped(
    stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dropped close would make the store unable to record its own
    incompleteness, so the bound is on the droppable class alone."""
    monkeypatch.setattr(store_module, "MAX_EVENTS_IN_FLIGHT", 0)
    store = stores()
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    store.record_event("alpha", "heard", logging.INFO, {"duration_s": 1.0}, 101.0)
    store.record_turn("alpha", a_turn())
    store.close_session("alpha", duration_s=9.0, reason="idle")
    store.stop()

    (session,) = rows("sessions")
    assert session["close_reason"] == "idle"
    assert session["duration_s"] == 9.0
    assert session["dropped"] == 1
    assert len(rows("turns")) == 1
    assert rows("events") == []


def test_the_writers_defaults_are_the_documented_ones(stores) -> None:
    """The numbers a test that injects a seam cannot prove, pinned as
    values and, where a value is not the whole claim, as behavior.

    Every test above either injects a clock, a gate or a smaller bound,
    so a store built the way the server will build it is the one thing
    none of them exercises. This builds one.
    """
    from sqlalchemy import text as sql

    assert MAX_EVENTS_IN_FLIGHT == 1024
    # A policy margin above the lock wait rather than a derived ceiling:
    # a commit parked on the advisory lock gives up after
    # LOCK_TIMEOUT_MS, and five seconds beyond that is room for the rest
    # of the batch. Not merely "above the lock timeout", because a
    # budget that drifted down to the timeout itself would still satisfy
    # a greater-than.
    assert STOP_TIMEOUT_S == LOCK_TIMEOUT_MS / 1000 + 5.0
    assert LOCK_TIMEOUT_MS == 10_000

    store = stores()
    assert store.retention_days == RETENTION_DAYS_DEFAULT == 90
    # Both storage switches default on under an enabled store, which is
    # what makes enabling it alone give the documented defaults.
    assert (store.metrics, store.text) == (True, True)
    # The timeout the connection really carries, rather than the
    # constant it was meant to be built from.
    # White-box: it rides on the connection the writer holds, and
    # nothing reports it. What it protects is a second writer failing
    # retryably rather than waiting without end, which no read can show.
    with store._engine.connect() as connection:
        assert connection.execute(sql("show lock_timeout")).scalar() == "10s"


def test_a_default_store_really_prunes_at_ninety_days() -> None:
    """The retention default asserted as the behavior it names, not as
    the number it is written with: a store built with nothing but a
    connection has to delete a session older than the window and keep
    one inside it. Only the clock is injected, because the alternative
    is a test that waits ninety days."""
    now = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)

    def manifest_at(started: dt.datetime) -> dict[str, Any]:
        return {**MANIFEST, "started_at": started.isoformat()}

    seeding = ConversationStore(DatabaseConfig(), now=lambda: now, retention_days=0)
    seeding.start()
    for name, age in (("ancient", 91), ("recent", 89)):
        seeding.open_session(name, 100.0, manifest_at(now - dt.timedelta(days=age)))
        seeding.close_session(name, duration_s=1.0, reason="client")
    seeding.stop()
    assert {row["session"] for row in rows("sessions")} == {"ancient", "recent"}

    # Built the way the server will build it: a connection and a clock.
    store = ConversationStore(DatabaseConfig(), now=lambda: now)
    store.start()
    store.stop()

    assert [row["session"] for row in rows("sessions")] == ["recent"]


def test_no_producer_path_can_wait_on_the_writer(stores) -> None:
    """Structural rather than timed: a queue whose blocking `put` raises
    proves no producer reaches one, whatever the writer is doing."""

    class Refusing:
        def __init__(self) -> None:
            self.items: list[Any] = []

        def put(self, item: Any) -> None:
            raise AssertionError("a producer blocked on the store")

        def put_nowait(self, item: Any) -> None:
            self.items.append(item)

        def get(self) -> Any:
            raise AssertionError("the writer is not running in this test")

    queue = Refusing()
    store = stores(queue=queue)
    store.open_session("alpha", 100.0, MANIFEST)
    store.record_event("alpha", "heard", logging.INFO, {}, 101.0)
    store.record_turn("alpha", a_turn())
    store.close_session("alpha", duration_s=1.0, reason="client")

    assert len(queue.items) == 4


# What the writer refuses


def test_records_for_a_session_it_never_opened_are_refused_once(
    stores, caplog: pytest.LogCaptureFixture
) -> None:
    """By construction this cannot happen, since the open is enqueued
    before the runtime can produce anything on the same loop. It is
    still refused rather than written, because an orphan turn is worse
    than a missing one."""
    store = stores()
    store.start()
    with caplog.at_level(logging.WARNING):
        store.record_turn("ghost", a_turn())
        store.record_turn("ghost", a_turn(at=102.0))
        store.close_session("ghost", duration_s=1.0, reason="client")
        store.stop()

    refusals = [
        record
        for record in caplog.records
        if record.name == store_module.__name__ and "not recording" in record.getMessage()
    ]
    assert len(refusals) == 1
    assert "ghost" in refusals[0].getMessage()
    assert rows("turns") == []
    assert rows("sessions") == []


@pytest.mark.parametrize("after_a_turn", [False, True])
def test_a_session_deleted_under_a_live_one_is_never_resurrected(
    stores, after_a_turn: bool
) -> None:
    """Retention is a deleter that can take a session that is still
    talking, because it asks nothing about whether the conversation
    ended. A thread and the session holding it that are both older than
    the window are unusual and entirely legal, and they go at the next
    close.

    The clock moves once, deliberately. Retention prunes a thread on
    its own last activity, so a session this old with turns stamped now
    would be a session whose conversation is live, which is the case
    the ruleset exists to protect rather than the case this test is
    about. Every real session of this age has turns of that age too;
    only a test can hold a clock still.

    Driven through the real prune rather than a hand-written DELETE,
    because what is under test is the interaction between the two and a
    hand-written statement is only the half this suite already controls.
    The queue is what fixes the interleaving: everything below is
    enqueued while the writer is parked, so the deleting marker is
    guaranteed to land between the records already committed and the
    records still in flight. Two of them: the deletion landing before the
    session's first turn marker has committed anything, and landing after
    a commit with more records already on their way. In both, absence of
    the session row is the tombstone: the writer discards the batch,
    forgets the session, and nothing still in flight can recreate it as
    orphan rows.

    The conversation then finishes normally, exactly as a real one would,
    since neither the runtime nor the device edge knows a deletion
    happened. What it says afterwards is not recorded.
    """
    now = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)
    older_than_the_window = {
        **MANIFEST,
        "started_at": (now - dt.timedelta(days=400)).isoformat(),
    }
    inside_it = {**MANIFEST, "started_at": now.isoformat()}

    long_ago = now - dt.timedelta(days=400)
    clock = [long_ago]

    gate = Gate()
    store = stores(gate=gate, now=lambda: clock[0], retention_days=90)
    store.start()
    store.open_session("alpha", 100.0, older_than_the_window)
    gate.wait()
    gate.let_through()

    if after_a_turn:
        store.record_turn("alpha", a_turn())
        gate.wait()
        gate.let_through()
        # Waited out rather than assumed: the clock moves next, and it
        # has to move after this turn stamped its thread with the old
        # reading rather than in the middle of the transaction doing it.
        _until(lambda: rows("turns"), "alpha's first turn never landed")
        # Committed, and more already on its way.
        store.record_event("alpha", "heard", logging.INFO, {"duration_s": 1.0}, 102.0)
    clock[0] = now

    # A second conversation, inside the window, whose close is when
    # retention runs. Enqueued ahead of the rest of alpha's records, so
    # the prune it triggers lands between what alpha has committed and
    # what it still has in flight.
    store.open_session("beta", 200.0, inside_it)
    store.close_session("beta", duration_s=1.0, reason="client")

    if not after_a_turn:
        # Nothing of alpha but its open row has been committed.
        store.record_event("alpha", "heard", logging.INFO, {"duration_s": 1.0}, 101.0)
    store.record_turn("alpha", a_turn(at=103.0))
    gate.wait()
    gate.open_forever()

    # And the conversation carries on to its natural end.
    store.record_event("alpha", "replied", logging.INFO, {"duration_s": 2.0}, 104.0)
    store.record_turn("alpha", a_turn(at=105.0))
    store.close_session("alpha", duration_s=8.0, reason="client")
    store.stop()

    assert [row["session"] for row in rows("sessions")] == ["beta"]
    assert rows("turns") == []
    assert rows("tool_invocations") == []
    assert rows("events") == []


# The two storage switches, at the row level


@pytest.mark.parametrize("metrics", [True, False])
@pytest.mark.parametrize("text_storage", [True, False])
def test_each_switch_combination_nulls_its_own_half(
    stores, metrics: bool, text_storage: bool
) -> None:
    """Every combination is a supported configuration: metrics without
    text is the stricter setting, text without metrics is the
    transparency-first one, and the session row is the spine in all
    four. The pipeline always hands the full record; the nulling is the
    writer's, which is why it is asserted on the rows."""
    store = stores(metrics=metrics, text=text_storage)
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    store.record_event("alpha", "heard", logging.INFO, {"duration_s": 1.0}, 101.0)
    store.record_turn(
        "alpha",
        a_turn(legs=(TurnLeg("sam", "Done.", 512, 24), TurnLeg("ada", "Hello.", 8, 4))),
    )
    store.close_session("alpha", duration_s=30.0, reason="limit")
    store.stop()

    (session,) = rows("sessions")
    (turn,) = rows("turns")
    (invocation,) = rows("tool_invocations")
    legs = turn["legs"]

    # The spine, in every configuration: retention and every read key on it.
    assert session["started_at"] and session["closed_at"]
    assert session["close_reason"] == "limit"
    assert (session["metrics"], session["text"]) == (int(metrics), int(text_storage))
    # And the structural halves of a turn, which are neither content nor
    # telemetry.
    assert turn["t_ms"] == 1200
    assert turn["agent"] == "sam"
    assert turn["language"] == "en"
    assert turn["tool_calls"] == 1
    assert (invocation["source"], invocation["entry"]) == ("mcp", "home")

    if text_storage:
        assert turn["heard"] == "turn the light on"
        assert turn["reply"] == "Done."
        assert invocation["name"] == "turn_on_light"
        assert invocation["arguments"] == {"room": "kitchen"}
        assert invocation["result"] == "ok"
        assert [leg["text"] for leg in legs] == ["Done.", "Hello."]
    else:
        assert turn["heard"] is None
        assert turn["reply"] is None
        assert invocation["name"] is None
        assert invocation["arguments"] is None
        assert invocation["result"] is None
        assert [leg["text"] for leg in legs] == [None, None]

    if metrics:
        assert session["duration_s"] == 30.0
        assert (turn["asr_ms"], turn["llm_ms"]) == (210, 900)
        assert (turn["input_tokens"], turn["output_tokens"]) == (512, 24)
        assert turn["tts_first_audio_ms"] == 260
        assert invocation["duration_ms"] == 42
        assert [leg["input_tokens"] for leg in legs] == [512, 8]
        assert len(rows("events")) == 1
    else:
        assert session["duration_s"] is None
        assert (turn["asr_ms"], turn["llm_ms"]) == (None, None)
        assert (turn["input_tokens"], turn["output_tokens"]) == (None, None)
        assert turn["tts_first_audio_ms"] is None
        assert invocation["duration_ms"] is None
        assert [leg["input_tokens"] for leg in legs] == [None, None]
        assert rows("events") == []


@pytest.mark.parametrize("text_storage", [True, False])
def test_an_events_row_never_carries_content_whatever_the_switches_say(
    stores, text_storage: bool
) -> None:
    """The events table is metadata-only by construction, from the first
    row, and that is not a policy the text switch decides: content has
    its own tables, and an events row is the wrong place for it at any
    setting.

    Both kinds are planted. The transcript half of the three text-
    bearing events, and the called tool's name on `tool_call`, which is
    content for the same reason a result is: a device's
    self-description or an MCP server's vocabulary, never anything this
    application authored. Under text-on especially, since that is the
    setting where nulling by switch would have let the peer's bytes
    through.

    A live guard until the narrowing removes these fields from the
    events, and defense in depth after it, which is what makes the store
    behave identically on both sides of that change."""
    store = stores(text=text_storage)
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    for name in ("heard", "replied", "agent_said"):
        store.record_event("alpha", name, logging.INFO, {"text": SENTINEL, "kept": 1}, 101.0)
    store.record_event(
        "alpha",
        "tool_call",
        logging.INFO,
        {"tool": SENTINEL, "source": "mcp", "entry": "home", "duration_ms": 42},
        101.5,
    )
    store.record_event("alpha", "llm_round", logging.INFO, {"duration_ms": 12}, 102.0)
    store.close_session("alpha", duration_s=3.0, reason="client")
    store.stop()

    stored = rows("events")
    assert len(stored) == 5
    for row in stored:
        # The JSON columns come back as values rather than as the text
        # they were dumped to: psycopg reads a `json` column into Python
        # objects, where the SQLite driver handed back the string.
        assert "text" not in row["fields"]
        assert "tool" not in row["fields"]
    assert stored[0]["fields"] == {"kept": 1}
    # What the event is still worth reading for: which entry was called,
    # how it was routed, and how long it took. Names this deployment
    # configured, never one a peer chose.
    assert stored[3]["fields"] == {"source": "mcp", "entry": "home", "duration_ms": 42}
    # And nowhere else in the store either.
    #
    # The SQLite-era form of this read the database file's bytes, which
    # is the honest surface a file offers and not one a server-side
    # store has. What replaces it is every column of every row of every
    # table: weaker, because it cannot see a page the server has not
    # reclaimed, and it is the strongest thing a client can ask.
    assert _absent_from_every_column(SENTINEL)


def _absent_from_every_column(sentinel: str) -> bool:
    """Whether the planted text is nowhere in the store, asked of every
    column of every row of every table it owns.

    The honest surface a server-side store offers, and deliberately a
    weaker claim than the one it replaces: reading the database file's
    bytes could see a freed page the engine had not overwritten, and
    nothing a client can ask reaches that. What autovacuum has not yet
    reclaimed is the database server's own storage maintenance, which
    the retention docstring and the generated reference both say.
    """
    engine = read_engine(DatabaseConfig())
    try:
        with engine.connect() as connection:
            for table in ("sessions", "turns", "tool_invocations", "events"):
                for row in connection.execute(
                    text(f"select * from conversations.{table}")
                ).mappings():
                    if sentinel in json.dumps(dict(row), default=str):
                        return False
    finally:
        engine.dispose()
    return True


# Failure, and what it may say


class Raising:
    """An engine whose every transaction fails, with the failure
    carrying bytes nothing may repeat."""

    def __init__(self, message: str) -> None:
        self.message = message

    def begin(self):
        raise RuntimeError(self.message)

    def dispose(self) -> None:
        return None


def test_a_failed_marker_drops_its_batch_and_names_only_the_class(
    stores, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rollback is the database's own unit, so the batch is atomically gone;
    what leaves the store is the exception's class name and nothing
    else, because a SQLAlchemy error holds the statement it failed on
    and the parameters bound to it."""
    seen: list[Any] = []

    class Tap:
        def emit(self, emission: Any) -> None:
            seen.append(emission)

    tap = Tap()
    attach_server_tap(tap)
    gate = Gate()
    store = stores(gate=gate, retention_days=0)
    try:
        store.start()
        store.open_session("alpha", 100.0, MANIFEST)
        gate.wait()
        gate.let_through()
        store.record_turn("alpha", a_turn(heard=SENTINEL, reply=SENTINEL))
        # Parked in front of the turn's own transaction, which is what
        # makes the swap below hit exactly that one and no other.
        gate.wait()
        # White-box: the failure under test is the database refusing a
        # write the writer has already accepted, with the refusal's text
        # holding what was being written. Nothing public can make a
        # committed engine fail on the next statement, and what is being
        # proved is that the report of it carries no content.
        # The real engine is let go of first: replacing it would
        # otherwise leave its pool open for the rest of the run, which a
        # server-side driver notices and a file-backed one did not.
        store._engine.dispose()
        store._engine = Raising(f"near {SENTINEL}: syntax error")
        with caplog.at_level(logging.INFO):
            gate.open_forever()
            store.stop()
    finally:
        detach_server_tap(tap)

    failures = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "conversations_failed"
    ]
    assert len(failures) == 1
    assert failures[0].levelno == logging.WARNING
    assert failures[0].failure == "RuntimeError"

    rendered = caplog.text + "".join(
        logs.JsonFormatter().format(record) for record in caplog.records
    )
    captured = capsys.readouterr()
    assert SENTINEL not in rendered
    assert SENTINEL not in captured.out
    assert SENTINEL not in captured.err
    assert all(record.exc_info is None for record in caplog.records)
    assert seen, "the server tap was offered nothing"
    assert all(SENTINEL not in json.dumps(item.payload, default=str) for item in seen)
    # And the batch really is gone rather than half applied.
    assert rows("turns") == []
    assert rows("tool_invocations") == []


def test_a_refused_landing_names_only_its_class(
    stores, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """The store's own refusal, held to the same standard as the
    database's: a turn it will not attribute to a thread fails the
    marker, and what leaves is the class name. No engine is swapped
    here, because this failure is one the shipped write path raises by
    itself, and the record it refuses carries the sentinel in every
    field a report could reach for, the agent included."""
    with caplog.at_level(logging.INFO):
        store = stores(retention_days=0)
        store.start()
        store.open_session("alpha", 100.0, MANIFEST)
        store.record_turn("alpha", a_turn())
        store.record_turn(
            "alpha", a_turn(agent=SENTINEL, heard=SENTINEL, reply=SENTINEL)
        )
        store.close_session("alpha", duration_s=1.0, reason="client")
        store.stop()

    failures = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "conversations_failed"
    ]
    assert len(failures) == 1
    assert failures[0].failure == "MisattributedTurn"

    rendered = caplog.text + "".join(
        logs.JsonFormatter().format(record) for record in caplog.records
    )
    captured = capsys.readouterr()
    assert SENTINEL not in rendered
    assert SENTINEL not in captured.out
    assert SENTINEL not in captured.err
    assert all(record.exc_info is None for record in caplog.records)
    # And the thread is still the one agent's, holding the one turn that
    # belonged to it.
    (thread,) = rows("conversations")
    assert thread["agent"] == "sam"
    assert len(rows("turns")) == 1


def test_a_failed_close_leaves_the_session_open_shaped(stores) -> None:
    """The documented incomplete state: readable, listed with its null
    close, and pruned on `started_at` like any other row, which is the
    same shape a crash mid-session leaves behind."""
    gate = Gate()
    store = stores(gate=gate, retention_days=0)
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    gate.wait()
    gate.let_through()
    store.record_turn("alpha", a_turn())
    gate.wait()
    gate.let_through()
    store.close_session("alpha", duration_s=5.0, reason="drain")
    gate.wait()
    # White-box, same shape: a close that cannot be written is what
    # leaves a session row open, and only a broken engine produces one.
    store._engine.dispose()
    store._engine = Raising("no")
    gate.open_forever()
    store.stop()

    (session,) = rows("sessions")
    assert session["closed_at"] is None
    assert session["close_reason"] is None
    assert session["duration_s"] is None
    # The turn that did commit is still there: the close is what failed.
    assert len(rows("turns")) == 1


# Lifecycle


def test_stop_is_idempotent_and_bounded_by_a_wedged_writer(stores) -> None:
    """A commit that cannot finish must not hold shutdown past the
    drain budget, and a teardown path may call stop twice without
    either call being a different act from the other."""
    wedged = Wedged()
    store = stores(gate=wedged, stop_timeout_s=0.2)
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    assert wedged.arrived.wait(timeout=TIMEOUT_S)

    first = threading.Event()
    threading.Thread(target=lambda: (store.stop(), first.set()), daemon=True).start()
    assert first.wait(timeout=TIMEOUT_S), "stop did not return inside its bound"

    # And again, which has nothing to do.
    store.stop()

    # White-box: joining the writer needs the thread, and no public call
    # waits out one wedged on a gate the test is holding.
    # Release the wedged writer AND wait it out. Without the join this
    # test walks away while its writer thread is still winding down,
    # and a neighbouring test that enumerates threads by name can see
    # it on a slow runner (the main-push CI failure of 2026-08-19).
    writer = store._thread
    wedged.forever.set()
    assert writer is not None
    writer.join(timeout=TIMEOUT_S)
    assert not writer.is_alive(), "the released writer did not exit"
    # And the pool the released writer opened after `stop()` had already
    # disposed the first one. That is what a daemon thread in a process
    # that is ending really does, and it is exactly as harmless there;
    # here the process carries on, and a connection to a server outlives
    # a file handle in a way this lane would find later and blame on
    # whichever test ran next.
    store._engine.dispose()


def test_a_store_that_never_started_leaks_no_thread(stores) -> None:
    """An app built for a test and never entered as a lifespan builds
    the store cold, so stopping it is a dispose and nothing else. The
    schema exists because the constructor migrates it, which is work
    that belongs at boot rather than at the first turn."""
    store = stores()
    store.stop()

    assert not [
        thread for thread in threading.enumerate() if thread.name == "conversation-store"
    ]
    assert rows("sessions") == []


# Reading beside the writer


def test_a_reader_sees_committed_rows_while_the_writer_is_still_going(stores) -> None:
    """The read engine reads the live store, so the case that matters is
    a commit made by a writer that has not let go of its connection.

    Its SQLite-era shape said the same thing about an uncheckpointed
    write-ahead log and a `mode=rw` URI, both of which retired with the
    file. What is left is the promise: a reader takes no advisory lock,
    is never blocked by the writer, and sees what the writer committed.
    """
    gate = Gate()
    store = stores(gate=gate)
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    gate.wait()
    gate.let_through()
    store.record_turn("alpha", a_turn())
    gate.wait()
    gate.let_through()
    # Parked in front of a third marker, so the turn above is committed
    # and the writer is still holding its connection.
    store.record_turn("alpha", a_turn(at=109.0))
    gate.wait()

    engine = read_engine(DatabaseConfig())
    try:
        with engine.connect() as connection:
            found = [
                dict(row)
                for row in connection.execute(
                    text("select * from conversations.turns")
                ).mappings()
            ]
    finally:
        engine.dispose()

    assert [row["session"] for row in found] == ["alpha"]

    gate.open_forever()


def test_the_read_engine_creates_nothing(stores) -> None:
    """A lookup must not bring anything into existence, which used to be
    a `mode=rw` URI refusing to create a missing file and is now the
    server's own rule: the connection is opened with
    `default_transaction_read_only`, so a write through it is refused
    whatever asked for it."""
    from sqlalchemy.exc import DBAPIError

    engine = read_engine(DatabaseConfig())
    try:
        with pytest.raises(DBAPIError):
            with engine.connect() as connection:
                connection.execute(
                    text("create table conversations.not_allowed (id int)")
                )
    finally:
        engine.dispose()
