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

import json
import logging
import threading
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from samtal_server import logs
from samtal_server.conversations import store as store_module
from samtal_server.conversations.records import ToolInvocation, TurnLeg, TurnRecord
from samtal_server.conversations.store import (
    MAX_EVENTS_IN_FLIGHT,
    STOP_TIMEOUT_S,
    ConversationStore,
    open_conversations,
    read_conversations,
)
from samtal_server.db import BUSY_TIMEOUT_MS
from samtal_server.events import attach_server_tap, detach_server_tap

# Long enough that a wedged writer fails the assertion rather than the
# suite's own scheduling, and never reached when the code is correct.
TIMEOUT_S = 30.0

# A value that must never appear anywhere but the database file, shaped
# like something an operator would be horrified to find in a log.
SENTINEL = "hunter2-not-a-real-credential-9f31c7"

MANIFEST: dict[str, Any] = {
    "started_at": "2026-08-15T10:00:00+00:00",
    "server": {"version": "0.1.0", "revision": "abc1234"},
    "device": {"mac": "aa:bb:cc:dd:ee:ff", "client": "test"},
    "protocol": "1",
    "agent": "sam",
    "agents": ["sam"],
    "providers": {"llm": {"name": "claude", "type": "anthropic"}},
}


class Gate:
    """The writer's parking seam, driven from the test's thread.

    Called once before each marker transaction. `wait()` returns when
    the writer has arrived and is stopped; `let_through()` releases it
    for exactly one more transaction; `open_forever()` stops gating.
    """

    def __init__(self) -> None:
        self._arrived = threading.Semaphore(0)
        self._release = threading.Semaphore(0)
        self._passthrough = False

    def __call__(self) -> None:
        if self._passthrough:
            return
        self._arrived.release()
        assert self._release.acquire(timeout=TIMEOUT_S), "the writer was never released"

    def wait(self) -> None:
        assert self._arrived.acquire(timeout=TIMEOUT_S), "the writer never reached the gate"

    def let_through(self, count: int = 1) -> None:
        self._release.release(count)

    def open_forever(self) -> None:
        self._release.release(1024)
        self._passthrough = True


class Wedged:
    """A gate that never lets go. What `stop()` has to survive."""

    def __init__(self) -> None:
        self.arrived = threading.Event()
        self.forever = threading.Event()

    def __call__(self) -> None:
        self.arrived.set()
        self.forever.wait(timeout=TIMEOUT_S)


@pytest.fixture
def stores(tmp_path: Path):
    """Build stores that are always stopped, so no test leaves a writer
    thread or an open engine behind."""
    built: list[ConversationStore] = []

    def _build(directory: Path | None = None, **options: Any) -> ConversationStore:
        store = ConversationStore(directory or tmp_path, **options)
        built.append(store)
        return store

    yield _build
    for store in built:
        store.stop()


def rows(directory: Path, table: str, **where: Any) -> list[dict[str, Any]]:
    """Read through a second engine, which is what a reader beside a
    running writer is."""
    engine = open_conversations(directory)
    try:
        clause = " and ".join(f"{name} = :{name}" for name in where)
        query = f"select * from {table}" + (f" where {clause}" if where else "")
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(query), where).mappings()]
    finally:
        engine.dispose()


def a_turn(**overrides: Any) -> TurnRecord:
    fields: dict[str, Any] = {
        "t_ms": 1200,
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


def test_the_session_row_is_visible_from_the_open(tmp_path: Path, stores) -> None:
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

    (session,) = rows(tmp_path, "sessions")
    assert session["session"] == "alpha"
    assert session["device"] == "aa:bb:cc:dd:ee:ff"
    assert session["agent"] == "sam"
    assert session["started_at"] == MANIFEST["started_at"]
    assert session["closed_at"] is None
    assert (session["metrics"], session["text"]) == (1, 1)
    assert rows(tmp_path, "turns") == []

    gate.open_forever()


def test_rows_are_invisible_before_their_marker_and_visible_after(
    tmp_path: Path, stores
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

    assert rows(tmp_path, "turns") == []
    assert rows(tmp_path, "events") == []

    gate.open_forever()
    store.stop()

    (turn,) = rows(tmp_path, "turns")
    assert turn["heard"] == "turn the light on"
    assert turn["tool_calls"] == 1
    (invocation,) = rows(tmp_path, "tool_invocations")
    assert invocation["turn"] == turn["id"]
    assert invocation["entry"] == "home"
    (event,) = rows(tmp_path, "events")
    assert (event["name"], event["t_ms"]) == ("heard", 1400)


def test_one_sessions_marker_exposes_nothing_of_another(tmp_path: Path, stores) -> None:
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
    store.record_turn("alpha", a_turn(t_ms=4000))
    gate.wait()

    assert [row["session"] for row in rows(tmp_path, "turns")] == ["alpha"]
    assert [row["session"] for row in rows(tmp_path, "events")] == ["alpha"]

    gate.open_forever()
    store.stop()

    assert {row["session"] for row in rows(tmp_path, "events")} == {"alpha", "beta"}


# The bound, and what it may not drop


def test_events_beyond_the_bound_are_dropped_counted_and_said_once(
    tmp_path: Path, stores, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
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

    (session,) = rows(tmp_path, "sessions")
    assert session["dropped"] == 6
    assert len(rows(tmp_path, "events")) == 4


def test_control_records_are_never_dropped(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
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

    (session,) = rows(tmp_path, "sessions")
    assert session["close_reason"] == "idle"
    assert session["duration_s"] == 9.0
    assert session["dropped"] == 1
    assert len(rows(tmp_path, "turns")) == 1
    assert rows(tmp_path, "events") == []


def test_the_writers_defaults_are_the_documented_ones() -> None:
    """The bound and the join budget are the two numbers a test that
    injects a seam cannot prove, so they are pinned where they are
    written."""
    assert MAX_EVENTS_IN_FLIGHT == 1024
    assert STOP_TIMEOUT_S > BUSY_TIMEOUT_MS / 1000


def test_no_producer_path_can_wait_on_the_writer(tmp_path: Path, stores) -> None:
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
    tmp_path: Path, stores, caplog: pytest.LogCaptureFixture
) -> None:
    """By construction this cannot happen, since the open is enqueued
    before the runtime can produce anything on the same loop. It is
    still refused rather than written, because an orphan turn is worse
    than a missing one."""
    store = stores()
    store.start()
    with caplog.at_level(logging.WARNING):
        store.record_turn("ghost", a_turn())
        store.record_turn("ghost", a_turn(t_ms=2000))
        store.close_session("ghost", duration_s=1.0, reason="client")
        store.stop()

    refusals = [
        record
        for record in caplog.records
        if record.name == store_module.__name__ and "not recording" in record.getMessage()
    ]
    assert len(refusals) == 1
    assert "ghost" in refusals[0].getMessage()
    assert rows(tmp_path, "turns") == []
    assert rows(tmp_path, "sessions") == []


def test_a_session_deleted_mid_stream_is_never_resurrected(tmp_path: Path, stores) -> None:
    """The purge command is a second writer, so absence of the session
    row is the tombstone: the writer discards the batch, forgets the
    session, and nothing still in flight can recreate it as orphan
    rows."""
    gate = Gate()
    store = stores(gate=gate)
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    gate.wait()
    gate.let_through()
    store.record_turn("alpha", a_turn())
    gate.wait()

    # Deleted out from under the live session, exactly as `purge` does.
    engine = open_conversations(tmp_path)
    try:
        with engine.begin() as connection:
            connection.execute(text("delete from sessions where session = 'alpha'"))
    finally:
        engine.dispose()

    gate.open_forever()
    store.record_event("alpha", "heard", logging.INFO, {}, 103.0)
    store.record_turn("alpha", a_turn(t_ms=5000))
    store.close_session("alpha", duration_s=8.0, reason="client")
    store.stop()

    assert rows(tmp_path, "sessions") == []
    assert rows(tmp_path, "turns") == []
    assert rows(tmp_path, "tool_invocations") == []
    assert rows(tmp_path, "events") == []


# The two storage switches, at the row level


@pytest.mark.parametrize("metrics", [True, False])
@pytest.mark.parametrize("text_storage", [True, False])
def test_each_switch_combination_nulls_its_own_half(
    tmp_path: Path, stores, metrics: bool, text_storage: bool
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

    (session,) = rows(tmp_path, "sessions")
    (turn,) = rows(tmp_path, "turns")
    (invocation,) = rows(tmp_path, "tool_invocations")
    legs = json.loads(turn["legs"])

    # The spine, in every configuration: retention and purging key on it.
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
        assert json.loads(invocation["arguments"]) == {"room": "kitchen"}
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
        assert len(rows(tmp_path, "events")) == 1
    else:
        assert session["duration_s"] is None
        assert (turn["asr_ms"], turn["llm_ms"]) == (None, None)
        assert (turn["input_tokens"], turn["output_tokens"]) == (None, None)
        assert turn["tts_first_audio_ms"] is None
        assert invocation["duration_ms"] is None
        assert [leg["input_tokens"] for leg in legs] == [None, None]
        assert rows(tmp_path, "events") == []


def test_the_text_bearing_events_lose_their_text_before_the_row_lands(
    tmp_path: Path, stores
) -> None:
    """The events table is metadata-only by construction, from the first
    row: content has its own tables and its own switch. A live guard
    until the narrowing removes those fields, and defense in depth
    after, which is what makes the store behave identically either side
    of that change."""
    store = stores()
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    for name in ("heard", "replied", "agent_said"):
        store.record_event("alpha", name, logging.INFO, {"text": SENTINEL, "kept": 1}, 101.0)
    store.record_event("alpha", "llm_round", logging.INFO, {"duration_ms": 12}, 102.0)
    store.close_session("alpha", duration_s=3.0, reason="client")
    store.stop()

    stored = rows(tmp_path, "events")
    assert len(stored) == 4
    for row in stored:
        fields = json.loads(row["fields"])
        assert "text" not in fields
    assert json.loads(stored[0]["fields"]) == {"kept": 1}
    assert SENTINEL not in (tmp_path / "conversations.db").read_bytes().decode(
        "utf-8", "ignore"
    )


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
    tmp_path: Path, stores, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rollback is SQLite's own unit, so the batch is atomically gone;
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
    assert rows(tmp_path, "turns") == []
    assert rows(tmp_path, "tool_invocations") == []


def test_a_failed_close_leaves_the_session_open_shaped(tmp_path: Path, stores) -> None:
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
    store._engine = Raising("no")
    gate.open_forever()
    store.stop()

    (session,) = rows(tmp_path, "sessions")
    assert session["closed_at"] is None
    assert session["close_reason"] is None
    assert session["duration_s"] is None
    # The turn that did commit is still there: the close is what failed.
    assert len(rows(tmp_path, "turns")) == 1


# Lifecycle


def test_stop_is_idempotent_and_bounded_by_a_wedged_writer(tmp_path: Path, stores) -> None:
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
    wedged.forever.set()


def test_a_store_that_never_started_leaks_no_thread(tmp_path: Path, stores) -> None:
    """An app built for a test and never entered as a lifespan builds
    the store cold, so stopping it is a dispose and nothing else. The
    file exists because the constructor migrates it, which is work that
    belongs at boot rather than at the first turn."""
    store = stores()
    store.stop()

    assert not [
        thread for thread in threading.enumerate() if thread.name == "conversation-store"
    ]
    assert (tmp_path / "conversations.db").is_file()


# Reading beside the writer


def test_a_reader_sees_committed_rows_while_the_wal_is_uncheckpointed(
    tmp_path: Path, stores
) -> None:
    """The read engine opens the live file, so the case that matters is
    a commit still living in the write-ahead log. `mode=ro` would refuse
    it (a WAL reader may extend the `-shm` index); `mode=rw` serves it
    and still refuses to create a file that is not there."""
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
    # and the writer still holds its connection: closing the last one
    # would checkpoint the log and take the case away.
    store.record_turn("alpha", a_turn(t_ms=9000))
    gate.wait()
    wal = tmp_path / "conversations.db-wal"
    assert wal.is_file() and wal.stat().st_size > 0

    engine = read_conversations(tmp_path)
    try:
        with engine.connect() as connection:
            found = [
                dict(row)
                for row in connection.execute(text("select * from turns")).mappings()
            ]
    finally:
        engine.dispose()

    assert [row["session"] for row in found] == ["alpha"]

    gate.open_forever()


def test_the_read_engine_refuses_to_create_a_missing_store(tmp_path: Path) -> None:
    """A lookup against a misconfigured path must not leave a database
    behind, which is what naming the file as a URI with `mode=rw`
    buys."""
    from sqlalchemy.exc import OperationalError

    engine = read_conversations(tmp_path / "nothing-here")
    try:
        with pytest.raises(OperationalError):
            with engine.connect() as connection:
                connection.execute(text("select 1"))
    finally:
        engine.dispose()

    assert not (tmp_path / "nothing-here").exists()
