"""The writer behind `conversations.db`: a queue, a thread, and markers.

The sink is off the audio path, which is the whole of its contract. A
producer only ever `put_nowait`s, so the session loop never waits on the
store whether the database is locked, the disk is full or the writer is
dead; a background thread does every database call; and a stalled reply
is never an acceptable price for a record of it.

That contract shapes three decisions that would otherwise look
arbitrary:

- **The queue is unbounded and the bound lives on the droppable class.**
  `Event` records are what a wedged database backs up, and dropping them
  is the documented behavior, so they are refused at the producer beyond
  `MAX_EVENTS_IN_FLIGHT`. `Open`, `Turn` and `Close` are control records
  and are never refused: they are the store's structural truth, they
  arrive at conversational pace, and a dropped `Close` would leave the
  store unable to record its own incompleteness.
- **The writer commits at markers, into per-session batches.** One queue
  carries every session, so a marker commits exactly its own session's
  accumulated batch inside one short `BEGIN IMMEDIATE` transaction, and
  no write lock is held across the interval between two turns where a
  purge or a backup could be waiting on it. A page opened mid
  conversation reads everything up to the last completed turn.
- **Absence of the session row is a tombstone.** Deletion is a second
  writer (retention here, the purge command in another process), so
  every marker transaction begins by confirming its session still
  exists. If it does not, the batch and the session's state are
  discarded and nothing further is written for it, which is what makes
  a purge of a running session final rather than a race the next turn
  undoes.

Storage policy lives here rather than in the pipeline: the runtime hands
over the full record and the writer nulls the content columns when text
storage is off and the numeric columns when metrics storage is off,
skipping the events rows entirely in the second case. Nothing that
leaves this module carries row content, SQL or exception text: the
failure reports are built in the `except` arm out of the exception's
class name.
"""

import contextlib
import datetime as dt
import logging
import queue as queuing
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import ColumnElement, Engine, delete, select

from samtal_server.conversations.records import ToolInvocation, TurnLeg, TurnRecord
from samtal_server.conversations.schema import events as events_table
from samtal_server.conversations.schema import sessions, tool_invocations, turns
from samtal_server.db import BUSY_TIMEOUT_MS, existing_engine, open_at
from samtal_server.events import ServerEvents

events = ServerEvents(__name__)

# The plain channel, for the one report that is not an event. A record
# for a session the writer never opened cannot happen from the real call
# sites (`Open` is enqueued before the runtime can produce anything, on
# the same loop), so it is a defect rather than a condition an operator
# configures around, and giving it an event name would put a fifth token
# in a vocabulary that is a compatibility surface.
logger = logging.getLogger(__name__)

DATABASE_FILENAME = "conversations.db"

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# How many `Event` records may be waiting on the writer before the
# producer starts refusing them. A turn produces tens of them, so this
# is minutes of backlog: a queue this deep means the database is wedged,
# and dropping is then the contract rather than a failure of it.
MAX_EVENTS_IN_FLIGHT = 1024

# How long the store is kept before retention deletes it. Stated rather
# than infinite, because a store with no policy retains forever by
# default; 0 keeps everything and is a deliberate choice.
RETENTION_DAYS_DEFAULT = 90

# How long `stop()` waits for the writer thread. Above the database's
# busy timeout, so a commit parked on another writer's lock has time to
# take it and finish, and a commit that is wedged for any other reason
# cannot hold shutdown past this.
STOP_TIMEOUT_S = BUSY_TIMEOUT_MS / 1000 + 5.0

# The events whose fields carry conversation text today. The writer
# strips the key before the row lands, because content has its own
# tables and its own switch. A live guard until the narrowing removes
# those fields, and defense in depth afterwards, which is what makes the
# store behave identically on both sides of that change.
TEXT_BEARING_EVENTS = frozenset({"heard", "replied", "agent_said"})
TEXT_FIELD = "text"

# How many distinct unknown sessions are remembered for the warn-once
# rule. Bounded so that a defect cannot grow the set without limit; when
# it fills it is emptied, and the warnings begin again.
_UNKNOWN_WARNED_MAX = 64


def conversations_path(directory: str | Path) -> Path:
    """Where the file is, without creating anything: the question a
    caller asks before deciding whether there is a store to read or to
    purge."""
    return Path(directory) / DATABASE_FILENAME


def open_conversations(directory: str | Path) -> Engine:
    """Open (creating if needed) and migrate `conversations.db`.

    `secure_delete` because a right to delete honored in the query
    planner and broken in the file bytes is not honored: freed pages are
    overwritten with zeros rather than left in the freelist, which is
    cheap for a store this append-mostly."""
    return open_at(directory, DATABASE_FILENAME, _MIGRATIONS_DIR, secure_delete=True)


def read_conversations(directory: str | Path) -> Engine:
    """An engine for reading a store somebody else migrated.

    The read behavior `db.read_engine` describes, pointed at this file:
    URI `mode=rw` because a WAL reader may extend the `-shm` index and
    `mode=ro` would refuse exactly the live database this serves, while
    `mode=rw` still refuses to create a missing one; deferred
    transactions, so a read never queues behind the writer; the busy
    timeout; no migration and no pragma that writes."""
    return existing_engine(conversations_path(directory))


# What the producers put on the queue


@dataclass(frozen=True)
class Open:
    """A session began. Its own marker: the session row is committed at
    once, so a page opened mid conversation finds the session."""

    session: str
    opened_at: float
    manifest: dict[str, Any]


@dataclass(frozen=True)
class Event:
    """One structured event, as the tap offered it."""

    session: str
    t_ms: int
    name: str
    level: int
    fields: dict[str, Any]


@dataclass(frozen=True)
class Turn:
    """One completed utterance-and-reply cycle. A marker."""

    session: str
    record: TurnRecord


@dataclass(frozen=True)
class Close:
    """A session ended. A marker, and the last record of that session."""

    session: str
    duration_s: float | None
    reason: str | None
    dropped: int


@dataclass(frozen=True)
class _Stop:
    """The sentinel `stop()` puts behind everything already queued, so
    the drain is the writer's ordinary loop reaching the end rather than
    a second code path."""


@dataclass
class _Batch:
    """One session's records since its last marker, held in memory so
    that no transaction is open while the writer waits."""

    turns: list[TurnRecord] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)


class ConversationStore:
    """The conversation record: one database, one writer thread.

    Built cold. The constructor opens and migrates the database, which
    is work the caller wants to fail at boot; `start()` begins the
    thread, and `stop()` ends it. Both are idempotent, so an app built
    without ever entering its lifespan leaks nothing and a teardown path
    may call `stop()` freely.

    The seams are for the tests that cannot otherwise pin what this
    promises, and each is compared `is not None` rather than by
    truthiness: an injected queue with a blocking `put` that raises is
    how "no producer path can wait" is proved, a wall clock is how
    retention is driven without sleeping, and the gate is where the
    writer parks so a wedged database can be exercised deterministically.
    """

    def __init__(
        self,
        directory: str | Path,
        metrics: bool = True,
        text: bool = True,
        retention_days: int = RETENTION_DAYS_DEFAULT,
        queue: "queuing.SimpleQueue[Any] | None" = None,
        now: Callable[[], dt.datetime] | None = None,
        gate: Callable[[], None] | None = None,
        stop_timeout_s: float = STOP_TIMEOUT_S,
    ) -> None:
        self.directory = Path(directory)
        self.metrics = metrics
        self.text = text
        self.retention_days = retention_days
        self.path = conversations_path(directory)
        self._engine = open_conversations(directory)
        self._queue: queuing.SimpleQueue[Any] = (
            queuing.SimpleQueue() if queue is None else queue
        )
        self._now = _utc_now if now is None else now
        self._gate = gate
        self._stop_timeout_s = stop_timeout_s
        self._thread: threading.Thread | None = None
        self._stopped = False
        # Producer state, touched from the session loop and from the
        # writer thread (which decrements the in-flight count as it
        # consumes), so it is guarded rather than assumed single
        # threaded.
        self._lock = threading.Lock()
        self._in_flight = 0
        self._opened_at: dict[str, float] = {}
        self._dropped: dict[str, int] = {}
        self._warned: set[str] = set()
        # Writer state, touched by the writer thread only.
        self._batches: dict[str, _Batch] = {}
        self._unknown: set[str] = set()
        # Records this writer lost to a failed transaction, per session,
        # folded into the session row's count at close. Writer-side, so
        # unlike the producer's counter it needs no lock.
        self._lost: dict[str, int] = {}

    # --- lifecycle ----------------------------------------------------

    def start(self) -> None:
        """Begin writing. A daemon thread, because a process that is
        going down must not be held open by a store with a backlog; the
        drain that matters happens in `stop()`, which runs first."""
        if self._thread is not None or self._stopped:
            return
        events.warning(
            "recording conversations to %s",
            self.path,
            event="conversations_enabled",
            path=str(self.path),
        )
        self._thread = threading.Thread(
            target=self._run, name="conversation-store", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Accept nothing more, drain what is queued, and let go of the
        file. Idempotent: a second call has nothing to do."""
        if self._stopped:
            return
        self._stopped = True
        thread = self._thread
        if thread is not None:
            self._queue.put(_Stop())
            thread.join(timeout=self._stop_timeout_s)
        # Disposed whether or not the thread came back. A writer still
        # wedged on a commit is a daemon thread in a process that is
        # ending, and holding the pool open would not unwedge it.
        self._engine.dispose()

    # --- what a session hands over ------------------------------------

    def open_session(
        self, session_id: str, opened_at: float, manifest: dict[str, Any]
    ) -> None:
        """Begin one session's record. A control record: never dropped.

        `opened_at` is the session loop's clock reading at open, which is
        what every offset below is measured from and what aligns a row
        with the capture triplet of the same name."""
        if self._stopped:
            return
        with self._lock:
            self._opened_at[session_id] = opened_at
            self._dropped.setdefault(session_id, 0)
        self._queue.put_nowait(Open(session_id, opened_at, dict(manifest)))

    def record_event(
        self, session_id: str, name: str, level: int, fields: dict[str, Any], at: float
    ) -> None:
        """One structured event. The droppable class: refused beyond the
        in-flight bound, which is counted and reported once per session
        and lands on the session row at close."""
        if self._stopped or not self._stores_events():
            return
        with self._lock:
            if session_id not in self._opened_at:
                return
            if self._in_flight >= MAX_EVENTS_IN_FLIGHT:
                self._dropped[session_id] = self._dropped.get(session_id, 0) + 1
                first = session_id not in self._warned
                self._warned.add(session_id)
                t_ms = None
            else:
                self._in_flight += 1
                first = False
                t_ms = self._offset(session_id, at)
        if t_ms is None:
            if first:
                events.warning(
                    "session %s: the conversation store is behind, dropping events",
                    session_id,
                    event="conversations_dropped",
                    session=session_id,
                )
            return
        self._queue.put_nowait(Event(session_id, t_ms, name, level, dict(fields)))

    def record_turn(self, session_id: str, record: TurnRecord) -> None:
        """One completed turn. A control record and a marker: reaching it
        commits everything this session has accumulated."""
        if self._stopped:
            return
        self._queue.put_nowait(Turn(session_id, record))

    def close_session(
        self, session_id: str, duration_s: float | None = None, reason: str | None = None
    ) -> None:
        """End one session's record. A control record and the last
        marker: a dropped close would make the store unable to say what
        it lost."""
        if self._stopped:
            return
        with self._lock:
            dropped = self._dropped.pop(session_id, 0)
            self._opened_at.pop(session_id, None)
            self._warned.discard(session_id)
        self._queue.put_nowait(Close(session_id, duration_s, reason, dropped))

    def _stores_events(self) -> bool:
        """Whether an events row would land. One rule, consulted twice:
        the writer applies it, because storage policy belongs with
        storage, and the producer consults the same method so that a
        deployment with metrics off pays no queue for records that were
        never going to be written and reports no drops of them."""
        return self.metrics

    def _offset(self, session_id: str, at: float) -> int:
        """An event's milliseconds from session open, the capture's
        `t_ms` in the units this schema stores."""
        return max(0, round((at - self._opened_at[session_id]) * 1000))

    # --- the writer ---------------------------------------------------

    def _run(self) -> None:
        self._prune()
        while True:
            item = self._queue.get()
            if isinstance(item, _Stop):
                self._flush_remaining()
                return
            self._accept(item)

    def _accept(self, item: Any) -> None:
        if isinstance(item, Event):
            with self._lock:
                self._in_flight -= 1
            batch = self._batches.get(item.session)
            if batch is None:
                self._refuse(item.session)
                return
            batch.events.append(item)
            return
        if isinstance(item, Open):
            self._batches[item.session] = _Batch()
            self._commit(item.session, opening=item)
            return
        if isinstance(item, Turn):
            batch = self._batches.get(item.session)
            if batch is None:
                self._refuse(item.session)
                return
            batch.turns.append(item.record)
            self._commit(item.session)
            return
        if isinstance(item, Close):
            if item.session not in self._batches:
                self._refuse(item.session)
                return
            self._commit(item.session, closing=item)
            self._batches.pop(item.session, None)
            self._lost.pop(item.session, None)
            self._prune()

    def _refuse(self, session_id: str) -> None:
        """A record for a session this writer is not recording: one it
        never opened, or one a deletion tombstoned out from under a live
        conversation. Dropped, and said once. Never opened cannot happen
        by construction, so this is a defect report rather than an
        operational condition; tombstoned is ordinary, and the record is
        refused for exactly the reason the tombstone exists."""
        if session_id in self._unknown:
            return
        if len(self._unknown) >= _UNKNOWN_WARNED_MAX:
            self._unknown.clear()
        self._unknown.add(session_id)
        logger.warning(
            "the conversation store dropped records for session %s, which it is "
            "not recording",
            session_id,
        )

    def _flush_remaining(self) -> None:
        """The final commit: whatever accumulated behind a session's last
        marker, for every session still open when the server stopped.
        Their rows keep their null close, which is the same shape a crash
        mid-session leaves."""
        for session_id in list(self._batches):
            self._commit(session_id)
            self._batches.pop(session_id, None)

    def _commit(
        self, session_id: str, opening: Open | None = None, closing: Close | None = None
    ) -> None:
        """One marker: one short transaction holding exactly this
        session's batch.

        The write lock is taken before anything is read (the engine
        begins immediate), so the existence check below and the inserts
        that follow it cannot straddle a deletion. A failure rolls the
        whole batch back, which is the unit SQLite gives, and the report
        that leaves carries the exception's class name and nothing
        else."""
        batch = self._batches.get(session_id)
        if batch is None:
            return
        if opening is None and closing is None and not batch.turns and not batch.events:
            # Nothing to write and no row to touch. A transaction here
            # would be a write lock taken for the sake of taking one,
            # which is exactly what holding no lock between markers is
            # about.
            return
        if self._gate is not None:
            self._gate()
        try:
            with self._engine.begin() as connection:
                if opening is not None:
                    connection.execute(
                        sessions.insert().values(self._session_row(opening))
                    )
                elif not self._alive(connection, session_id):
                    # Deleted out from under a live session. The
                    # tombstone is the absence: this session is forgotten
                    # so nothing in flight can resurrect it as orphan
                    # rows.
                    self._batches.pop(session_id, None)
                    return
                self._write(connection, session_id, batch)
                if closing is not None:
                    connection.execute(
                        sessions.update()
                        .where(sessions.c.session == session_id)
                        .values(self._close_row(closing))
                    )
        except Exception as exc:  # noqa: BLE001 - a write never breaks a session
            self._failed(session_id, batch, exc)
        # Never for a session the tombstone just removed: recreating its
        # state here is exactly the resurrection the check above exists
        # to prevent.
        if session_id in self._batches:
            self._batches[session_id] = _Batch()

    def _alive(self, connection: Any, session_id: str) -> bool:
        found = connection.execute(
            select(sessions.c.id).where(sessions.c.session == session_id)
        ).first()
        return found is not None

    def _write(self, connection: Any, session_id: str, batch: _Batch) -> None:
        for record in batch.turns:
            turn = connection.execute(
                turns.insert().values(self._turn_row(session_id, record))
            )
            turn_id = turn.inserted_primary_key[0]
            rows = [
                self._tool_row(session_id, turn_id, call) for call in record.tools
            ]
            if rows:
                connection.execute(tool_invocations.insert(), rows)
        # No events rows at all under metrics-off, rather than rows with
        # their payload emptied: the events table is the structured
        # telemetry the switch turns off.
        if batch.events and self._stores_events():
            connection.execute(
                events_table.insert(),
                [self._event_row(record) for record in batch.events],
            )

    def _failed(self, session_id: str, batch: _Batch, exc: BaseException) -> None:
        """A marker transaction that did not commit. The batch is gone
        and counted; the writer keeps consuming. When the marker was the
        close, the session row stays open-shaped, which is the store's
        documented incomplete state: readable, listed with its null
        close, and pruned on `started_at` like any other."""
        self._lost[session_id] = self._lost.get(session_id, 0) + len(batch.turns) + len(
            batch.events
        )
        events.warning(
            "the conversation store dropped a batch after a write failed (%s)",
            type(exc).__name__,
            event="conversations_failed",
            failure=type(exc).__name__,
        )

    # --- rows, with both switches applied ------------------------------

    def _session_row(self, opening: Open) -> dict[str, Any]:
        """The session spine, written in every enabled configuration:
        retention, purging and the read API all key on it, and the two
        switch columns are what make a null elsewhere readable."""
        manifest = opening.manifest
        device = manifest.get("device")
        device = device if isinstance(device, dict) else {}
        server = manifest.get("server")
        server = server if isinstance(server, dict) else {}
        started_at = manifest.get("started_at")
        return {
            "session": opening.session,
            "device": device.get("mac"),
            "client": device.get("client"),
            "agent": manifest.get("agent"),
            "agents": manifest.get("agents"),
            "protocol": manifest.get("protocol"),
            "started_at": started_at if isinstance(started_at, str) else self._stamp(),
            "closed_at": None,
            "duration_s": None,
            "close_reason": None,
            "server_version": server.get("version"),
            "revision": server.get("revision"),
            "providers": manifest.get("providers"),
            "metrics": self.metrics,
            "text": self.text,
            "dropped": 0,
        }

    def _close_row(self, closing: Close) -> dict[str, Any]:
        lost = closing.dropped + self._lost.get(closing.session, 0)
        return {
            "closed_at": self._stamp(),
            "duration_s": closing.duration_s if self.metrics else None,
            "close_reason": closing.reason,
            "dropped": lost if self.metrics else 0,
        }

    def _turn_row(self, session_id: str, record: TurnRecord) -> dict[str, Any]:
        return {
            "session": session_id,
            "t_ms": record.t_ms,
            "agent": record.agent,
            "heard": record.heard if self.text else None,
            "heard_duration_s": record.heard_duration_s if self.metrics else None,
            "language": record.language,
            "language_confidence": (
                record.language_confidence if self.metrics else None
            ),
            "reply": record.reply if self.text else None,
            "legs": [self._leg(leg) for leg in record.legs] if record.legs else None,
            "asr_ms": record.asr_ms if self.metrics else None,
            "first_token_ms": record.first_token_ms if self.metrics else None,
            "llm_ms": record.llm_ms if self.metrics else None,
            "tts_first_audio_ms": (
                record.tts_first_audio_ms if self.metrics else None
            ),
            "rounds": record.rounds if self.metrics else None,
            "input_tokens": record.input_tokens if self.metrics else None,
            "output_tokens": record.output_tokens if self.metrics else None,
            "tool_calls": len(record.tools),
        }

    def _leg(self, leg: TurnLeg) -> dict[str, Any]:
        """One handover leg. Its halves follow different switches, which
        is why the entry is built here rather than serialized whole."""
        return {
            "agent": leg.agent,
            "text": leg.text if self.text else None,
            "input_tokens": leg.input_tokens if self.metrics else None,
            "output_tokens": leg.output_tokens if self.metrics else None,
        }

    def _tool_row(
        self, session_id: str, turn_id: int, call: ToolInvocation
    ) -> dict[str, Any]:
        """One invocation. The name goes with the arguments and the
        result under one rule: a tool's name originates off this server
        exactly as its result does, and one rule admits no partial
        carve-outs. What survives text-off is what this deployment
        configured or measured, which keeps "this entry was called, it
        took this long, it failed" answerable."""
        return {
            "turn": turn_id,
            "session": session_id,
            "position": call.position,
            "source": call.source,
            "entry": call.entry,
            "name": call.name if self.text else None,
            "malformed": call.malformed,
            "arguments": call.arguments if self.text and not call.malformed else None,
            "result": call.result if self.text else None,
            "is_error": call.is_error,
            "duration_ms": call.duration_ms if self.metrics else None,
        }

    def _event_row(self, record: Event) -> dict[str, Any]:
        fields = dict(record.fields)
        if record.name in TEXT_BEARING_EVENTS:
            fields.pop(TEXT_FIELD, None)
        return {
            "session": record.session,
            "t_ms": record.t_ms,
            "name": record.name,
            "level": record.level,
            "fields": fields,
        }

    def _stamp(self) -> str:
        return self._now().isoformat()

    # --- retention -----------------------------------------------------

    def _prune(self) -> None:
        """Delete whole sessions older than the window, in the writer, so
        it serializes with the writes by construction. Runs at start and
        at each session close, which is often enough for a store that
        only grows when a conversation happens.

        Failure is a dropped prune, not a dropped conversation: a store
        that could not delete still records, and the next close tries
        again."""
        if self.retention_days <= 0:
            return
        cutoff = (self._now() - dt.timedelta(days=self.retention_days)).isoformat()
        try:
            with self._engine.begin() as connection:
                # Lexicographic on UTC ISO-8601 text is chronological
                # when both sides are written by `isoformat` at the same
                # offset, which they are: the cutoff is built here and
                # the column is written from the same clock.
                counts = _delete_sessions(connection, [sessions.c.started_at < cutoff])
            _checkpoint(self._engine)
        except Exception as exc:  # noqa: BLE001 - retention never breaks a session
            events.warning(
                "the conversation store could not prune (%s)",
                type(exc).__name__,
                event="conversations_failed",
                failure=type(exc).__name__,
            )
            return
        if counts["sessions"]:
            events.info(
                "conversations: pruned %d session(s) older than %d days",
                counts["sessions"],
                self.retention_days,
                event="conversations_pruned",
                sessions=counts["sessions"],
            )


def purge(
    directory: str | Path,
    session: str | None = None,
    device: str | None = None,
    before: dt.date | None = None,
) -> dict[str, int]:
    """Delete sessions from the file, with no server involved.

    Selectors combine with AND, and at least one is the caller's to
    enforce: a purge with none would be a truncation wearing the same
    command name. The deletion is one `BEGIN IMMEDIATE` transaction, so
    it is safe beside a running server (the writer's transactions
    serialize with it and a busy database yields the retryable error
    rather than half applying), and it finishes with a truncating
    checkpoint so the deleted frames do not survive in the write-ahead
    log.

    Purging a session that is still running ends its recording: the
    writer finds the row gone at its next marker and stops writing for
    that session. Capture files are a separate instrument and are never
    touched.
    """
    criteria: list[ColumnElement[bool]] = []
    if session is not None:
        criteria.append(sessions.c.session == session)
    if device is not None:
        criteria.append(sessions.c.device == device)
    if before is not None:
        # Midnight UTC of the named day, so `--before 2026-08-15` keeps
        # everything that started on the fifteenth.
        boundary = dt.datetime.combine(before, dt.time.min, dt.UTC)
        criteria.append(sessions.c.started_at < boundary.isoformat())
    if not criteria:
        # A guard against the caller, not against the operator: the CLI
        # refuses this before it gets here, and a purge with no selector
        # would be a truncation wearing the same command name.
        raise ValueError("a purge needs at least one selector")
    engine = existing_engine(conversations_path(directory), immediate=True, secure_delete=True)
    try:
        with engine.begin() as connection:
            counts = _delete_sessions(connection, criteria)
        _checkpoint(engine)
    finally:
        engine.dispose()
    return counts


def _delete_sessions(
    connection: Any, criteria: list[ColumnElement[bool]]
) -> dict[str, int]:
    """One session's rows go together, from every table, or the deletion
    leaves turns and events pointing at nothing. Inside the caller's
    transaction, which is what makes that atomic.

    The children are matched by a subquery against the same criteria
    rather than by a list of ids read out first, so a deletion of many
    sessions is one statement per table instead of a bound parameter per
    session against SQLite's limit."""
    doomed = select(sessions.c.session).where(*criteria)
    counts = {}
    for name, table in (
        ("events", events_table),
        ("tool_invocations", tool_invocations),
        ("turns", turns),
    ):
        counts[name] = connection.execute(
            delete(table).where(table.c.session.in_(doomed))
        ).rowcount
    # Last, because the subquery above reads it.
    counts["sessions"] = connection.execute(delete(sessions).where(*criteria)).rowcount
    return counts


def _checkpoint(engine: Engine) -> None:
    """Fold the write-ahead log back into the database and truncate it,
    so the deleted frames stop existing rather than merely stop being
    read. A checkpoint blocked by a long reader truncates at the next
    quiet moment instead of failing the deletion, which is the stated
    limit rather than a silent one; copies that already left the file
    are the operator's to manage."""
    with contextlib.suppress(Exception), engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


__all__ = [
    "DATABASE_FILENAME",
    "MAX_EVENTS_IN_FLIGHT",
    "RETENTION_DAYS_DEFAULT",
    "STOP_TIMEOUT_S",
    "Close",
    "ConversationStore",
    "Event",
    "Open",
    "Turn",
    "conversations_path",
    "open_conversations",
    "purge",
    "read_conversations",
]
