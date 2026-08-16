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
  `MAX_EVENTS_IN_FLIGHT`. In flight means not yet written off: queued,
  or sitting in a per-session batch that no marker has committed. A
  count that stopped at the queue would bound nothing, since a session
  that never reaches a marker holds its events in memory while the
  producer sees a fresh allowance. `Open`, `Turn` and `Close` are control records
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

One thing is not policy and does not follow a switch. The `events`
table is metadata-only by construction, so `EVENT_CONTENT` is stripped
from every event's fields whatever the switches say: content has its own
tables, and an events row is the wrong place for it at any setting. The
narrowing has since landed and the events carry none of those keys, so
the strip is defense in depth rather than a live guard: it was written
to be correct either way, which is what let the store behave identically
on both sides of that change and what keeps it correct for a database
written by an older server.
"""

import datetime as dt
import logging
import queue as queuing
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import ColumnElement, Engine, delete, select
from sqlalchemy.exc import OperationalError

from samtal_server.config.loader import ConfigError, DatabaseBusyError, StorageError
from samtal_server.conversations.records import ToolInvocation, TurnLeg, TurnRecord
from samtal_server.conversations.schema import events as events_table
from samtal_server.conversations.schema import sessions, tool_invocations, turns
from samtal_server.db import BUSY_TIMEOUT_MS, existing_engine, open_at
from samtal_server.events import Emission, ServerEvents

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

# The content these events used to carry in their fields, stripped
# before the row lands. One table, consulted in one place, because a
# content key that is scrubbed at some call sites and not others is a
# leak waiting for the next event to be added.
#
# Unconditional, and deliberately not under the text switch: the events
# table is metadata-only by construction, from its first row. `text` was
# the transcript half of three events. `tool` was the called tool's
# name, which is content for the same reason its result is, a device's
# self-description or an MCP far side's vocabulary rather than anything
# this application authored; the name lives on `tool_invocations`, where
# the text switch decides whether it is kept.
#
# The narrowing (#120) has since taken all four off the events, so this
# is defense in depth rather than a live guard. It stays for two
# reasons: a payload that regains a content key meets it here rather
# than in a review, and the same rule reads a database written by a
# server from before the narrowing.
EVENT_CONTENT: dict[str, tuple[str, ...]] = {
    "heard": ("text",),
    "replied": ("text",),
    "agent_said": ("text",),
    "tool_call": ("tool",),
}

# How many distinct unknown sessions are remembered for the warn-once
# rule. Bounded so that a defect cannot grow the set without limit; when
# it fills it is emptied, and the warnings begin again.
_UNKNOWN_WARNED_MAX = 64

# How long a checkpoint waits for a reader to let go before it defers.
# Far below the busy timeout on purpose: by the time it runs, the
# deletion is committed and durable, and truncating the log is tidying
# up after it. A quarter of a second covers a reader that is about to
# finish; waiting the full ten seconds would queue the writer behind a
# reader it has no reason to wait for, which is exactly what deferring
# to the next quiet moment exists to avoid.
CHECKPOINT_WAIT_MS = 250

# What the purge command waits instead. Longer, because a CLI process
# has no next marker to retry at: this is its only chance before it
# exits, and it reports the deferral when it does not get one.
PURGE_CHECKPOINT_WAIT_MS = 1_000


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


def migrate_existing(directory: str | Path) -> bool:
    """Bring a store that is already there up to the current schema, and
    create nothing. Answers whether there was one.

    What a boot with recording switched off does. A deployment that
    recorded last month and records nothing today still has to serve its
    history against the schema this server reads with, and migrating what
    exists is maintenance rather than recording: a missing file stays
    missing, which is what keeps an absent or disabled section a server
    that leaves no database behind."""
    if not conversations_path(directory).exists():
        return False
    open_conversations(directory).dispose()
    return True


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
    """One completed utterance-and-reply cycle. A marker.

    `t_ms` is stamped by the producer rather than carried on the record:
    the runtime hands over the clock reading it heard the utterance at,
    and turning that into an offset needs the session's opening reading,
    which only this store holds."""

    session: str
    record: TurnRecord
    t_ms: int


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


@dataclass(frozen=True)
class Deletion:
    """What a purge removed, and whether the log went with it."""

    sessions: int
    turns: int
    tool_invocations: int
    events: int
    # False when a reader blocked the truncating checkpoint. The rows
    # are gone either way; the frames holding their bytes are not, until
    # a checkpoint gets its moment.
    truncated: bool = True

    def counts(self) -> dict[str, int]:
        """The four numbers a caller prints, in the order it prints
        them."""
        return {
            "sessions": self.sessions,
            "turns": self.turns,
            "tool_invocations": self.tool_invocations,
            "events": self.events,
        }


@dataclass
class _Batch:
    """One session's records since its last marker, held in memory so
    that no transaction is open while the writer waits."""

    turns: list[Turn] = field(default_factory=list)
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
        # Whether a deletion's truncating checkpoint was blocked and is
        # still owed. Retried at the next marker and at stop, which is
        # what "the next quiet moment" means concretely.
        self._truncation_due = False

    # --- lifecycle ----------------------------------------------------

    def start(self) -> None:
        """Begin writing. A daemon thread, because a process that is
        going down must not be held open by a store with a backlog; the
        drain that matters happens in `stop()`, which runs first."""
        if self._thread is not None or self._stopped:
            return
        thread = threading.Thread(target=self._run, name="conversation-store", daemon=True)
        # Kept only once it is really running, and announced only then.
        # A `Thread.start()` that raises (a process out of threads) would
        # otherwise leave a thread nobody can join behind a `stop()` that
        # has to stay harmless, and an event saying this server is
        # recording when it is not.
        thread.start()
        self._thread = thread
        events.warning(
            "recording conversations to %s",
            self.path,
            event="conversations_enabled",
            path=str(self.path),
        )

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
        commits everything this session has accumulated.

        The offset is taken here, off the reading the record carries, for
        the reason the queue item states: the runtime is built before the
        session opens and never learns the reading its offsets are
        measured from."""
        if self._stopped:
            return
        with self._lock:
            # A turn for a session this store never opened is refused by
            # the writer, which says so once. Stamping a zero rather than
            # asking for an offset there keeps that the writer's decision
            # instead of an exception raised on the session loop.
            t_ms = self._offset(session_id, record.at) if session_id in self._opened_at else 0
        self._queue.put_nowait(Turn(session_id, record, t_ms))

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
            batch = self._batches.get(item.session)
            if batch is None:
                # Refused, so it is written off here: nothing further
                # will happen to it.
                self._release(1)
                self._refuse(item.session)
                return
            # Still in flight: moving from the queue to a batch is not
            # writing it off, and the batch is where an unbounded
            # backlog would otherwise accumulate.
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
            batch.turns.append(item)
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

    def _release(self, count: int) -> None:
        """Write off `count` events: committed, rolled back, discarded
        by a tombstone, or refused. Until one of those happens they are
        in flight, and the producer's allowance is what they are counted
        against."""
        if count <= 0:
            return
        with self._lock:
            self._in_flight -= count

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
        # The last quiet moment there will be. A truncation still owed
        # when the server stops has no later marker to wait for.
        self._settle()

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
                    self._release(len(batch.events))
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
        # Committed or rolled back, this batch is written off either way.
        self._release(len(batch.events))
        # Never for a session the tombstone just removed: recreating its
        # state here is exactly the resurrection the check above exists
        # to prevent.
        if session_id in self._batches:
            self._batches[session_id] = _Batch()
        self._settle()

    def _settle(self) -> None:
        """The next quiet moment, taken when one arrives. A deletion
        whose checkpoint a reader blocked leaves the deleted frames in
        the write-ahead log, so the truncation is owed until it lands,
        and every marker the writer commits is another chance at it."""
        if self._truncation_due and _checkpoint(self._engine):
            self._truncation_due = False

    def _alive(self, connection: Any, session_id: str) -> bool:
        found = connection.execute(
            select(sessions.c.id).where(sessions.c.session == session_id)
        ).first()
        return found is not None

    def _write(self, connection: Any, session_id: str, batch: _Batch) -> None:
        for item in batch.turns:
            turn = connection.execute(
                turns.insert().values(self._turn_row(session_id, item))
            )
            turn_id = turn.inserted_primary_key[0]
            rows = [
                self._tool_row(session_id, turn_id, call) for call in item.record.tools
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

    def _turn_row(self, session_id: str, item: Turn) -> dict[str, Any]:
        record = item.record
        return {
            "session": session_id,
            "t_ms": item.t_ms,
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
        for key in EVENT_CONTENT.get(record.name, ()):
            fields.pop(key, None)
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
            if counts["sessions"]:
                self._truncation_due = not _checkpoint(self._engine)
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


@dataclass(frozen=True)
class SessionSink:
    """One session's tap into the store, attached where the capture's is.

    An `EventTap` and nothing more: it takes the emission every other
    consumer is offered and hands the store the three things a row keeps
    that the payload does not already carry as columns. `event`,
    `session` and `device` are popped because they live on the row and on
    the session; what is left is the event's own fields, whose names are
    the vocabulary's, which is the contract.

    The reading rides along rather than the offset. Only the store knows
    what its session was opened at, which is the same rule the turn
    records follow, so both halves of a session's timeline are measured
    from one origin.

    Never blocking and never raising is the contract, as it is for every
    tap: this runs on the session loop, and the whole write path exists
    so that a database cannot make a reply wait.
    """

    store: "ConversationStore"
    session_id: str

    def emit(self, emission: Emission) -> None:
        fields = dict(emission.payload)
        name = str(fields.pop("event", ""))
        fields.pop("session", None)
        fields.pop("device", None)
        self.store.record_event(
            self.session_id, name, emission.level, fields, emission.at
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

    The answer says whether the write-ahead log was truncated as well as
    what was deleted. A reader holding the log open defers the
    truncation, and a CLI process has no next marker to retry at, so the
    caller is told rather than left to assume: the deletion is committed
    either way, and the frames go at the next checkpoint that gets its
    moment.
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
    truncated = False
    # Built inside the handler and raised outside it, the shape
    # `db.open_database` uses: raising in the arm would leave the
    # library's exception reachable through __context__ from the one
    # that travels out, and `from None` sets __suppress_context__
    # without clearing the reference.
    problem: ConfigError | None = None
    counts: dict[str, int] = {}
    try:
        with engine.begin() as connection:
            counts = _delete_sessions(connection, criteria)
    except Exception as exc:  # noqa: BLE001 - classified, never re-raised
        problem = _refusal(exc)
    else:
        truncated = _checkpoint(engine, PURGE_CHECKPOINT_WAIT_MS)
    finally:
        engine.dispose()
    if problem is not None:
        raise problem
    return Deletion(**counts, truncated=truncated)


def _refusal(exc: BaseException) -> ConfigError:
    """A failed deletion, as the sentence the CLI prints.

    The lock that did not clear inside the busy timeout is told from
    everything else on the driver's own message, the same distinction
    `db.migration_failure` makes and for the same reason: it is the only
    one a caller can answer differently.

    Unlike that one, neither sentence carries the driver's line. A purge
    is given a session, a device or a date on its command line, and a
    SQLAlchemy error holds the statement it failed on together with the
    parameters bound to it, so an interpolated detail is a selector
    printed back to a terminal and into whatever collects its output.
    The kind of failure and where to look are what the operator needs;
    the value they just typed is not.
    """
    detail = str(getattr(exc, "orig", ""))
    if isinstance(exc, OperationalError) and ("locked" in detail or "busy" in detail):
        return DatabaseBusyError(
            "the conversation store is busy and the purge was not applied; "
            "nothing was deleted, so run the command again"
        )
    return StorageError(
        "cannot delete from the conversation store; server.database.dir names "
        "the directory it lives in, and the file has to be readable and writable"
    )


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


def _checkpoint(engine: Engine, wait_ms: int = CHECKPOINT_WAIT_MS) -> bool:
    """Fold the write-ahead log back into the database and truncate it,
    so the deleted frames stop existing rather than merely stop being
    read. Answers whether it truncated.

    On a raw DBAPI connection, deliberately, and not through the engine:
    SQLite refuses to checkpoint inside a transaction, and every
    connection this engine hands out through SQLAlchemy takes `BEGIN
    IMMEDIATE` before its first statement. Through the engine the
    statement raises "database table is locked" every single time, which
    a suppressed exception turns into a checkpoint that silently never
    happened. The pragma runs in autocommit here because the connect
    listener hands transaction control to SQLAlchemy, and SQLAlchemy is
    not in the way of a raw connection.

    A checkpoint a reader blocks reports busy in its first column rather
    than raising, so the answer is read rather than assumed. Its caller
    retries at the next quiet moment; the copies that already left the
    file (backups, snapshots) stay the operator's to manage.
    """
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        try:
            cursor.execute(f"PRAGMA busy_timeout={wait_ms}")
            row = cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            # Back to the connection's own budget before it returns to
            # the pool: the short wait belongs to this statement, not to
            # every transaction that borrows the connection next.
            cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()
    except Exception:  # noqa: BLE001 - tidying up never breaks a deletion
        return False
    finally:
        raw.close()
    # (busy, log frames, frames checkpointed). Busy is 0 only when every
    # frame moved and the log was truncated.
    return row is not None and row[0] == 0


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


__all__ = [
    "DATABASE_FILENAME",
    "MAX_EVENTS_IN_FLIGHT",
    "RETENTION_DAYS_DEFAULT",
    "STOP_TIMEOUT_S",
    "Close",
    "ConversationStore",
    "Deletion",
    "Event",
    "Open",
    "SessionSink",
    "Turn",
    "conversations_path",
    "migrate_existing",
    "open_conversations",
    "purge",
    "read_conversations",
]
