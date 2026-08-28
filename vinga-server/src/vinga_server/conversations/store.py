"""The writer behind the conversation record: a queue, a thread, markers.

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
  accumulated batch inside two short transactions, and the chain's
  advisory lock is not held across the interval between two turns. A
  page opened mid conversation reads everything up to the last completed
  turn.
- **Product state and telemetry never share a fate.** The two
  transactions are the durable half (the session row, the threads, the
  turns and their invocations) and the lossy half (the event rows), in
  that order and with independent outcomes. A durable transaction that
  fails on the transient class the db classifier names is retried in
  place up to `TURN_WRITE_ATTEMPTS`; when it is finally dropped, the
  hole is latched on the thread as `conversations.incomplete` rather
  than counted and forgotten, and the mark rides the next durable
  transaction rather than one of its own, so no later acknowledgement
  can become true in front of it. An events transaction that fails drops
  and counts its events exactly as it always did, and touches no turn.
- **Absence of the session row is a tombstone.** Retention deletes whole
  sessions on `started_at` and asks nothing about whether the
  conversation ended, so a session long enough to fall outside the
  window can be taken while it is still talking. Every marker
  transaction therefore begins by confirming its session still exists,
  both halves separately, because they are separate transactions and a
  deletion fits between them. If it does not, the batch and the
  session's state are discarded and nothing further is written for it,
  which is what makes the deletion of a running session final rather
  than a race the next turn undoes.
- **A deleted thread has to be said out loud, because absence cannot say
  it.** A missing conversation row is two different things: a thread
  before its first turn, and a thread that has been erased. So the
  writer keeps the ids it has written a turn onto, a deletion publishes
  what it took through `erased()` below, and a turn for an id in both
  sets is discarded with its acknowledgement resolved false rather than
  materializing the row again. The conversation in the room carries on;
  its record stops, which is what erasure means.

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
tables, and an events row is the wrong place for it at any setting.

Half of that strip is now defense in depth and half of it is still live,
which is worth saying precisely. The narrowing took `text` off `heard`,
`replied` and `agent_said`, so those three keys never arrive; the strip
was written to be correct either way, which is what let the store behave
identically on both sides of that change and what meets a payload that
regains the key. It is a write-time rule and only that: it runs where a
row is built and no read applies it, so it says what lands in this
database and nothing about what an older one holds. `tool` still
arrives, on the one branch the narrowing kept it for: a builtin's name
is this application's own. The strip removes it from the row anyway, so
that every name a call carries lives in `tool_invocations` under the
text switch rather than in two tables under two different rules.
"""

import datetime as dt
import enum
import logging
import queue as queuing
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select

from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations import schema, threads
from vinga_server.conversations.records import (
    Acknowledgement,
    ToolInvocation,
    TurnLeg,
    TurnRecord,
)
from vinga_server.conversations.schema import events as events_table
from vinga_server.conversations.schema import sessions, tool_invocations, turns
from vinga_server.db import LOCK_TIMEOUT_MS, StoreChain, advisory_key, is_busy, open_at
from vinga_server.events import Emission, ServerEvents
from vinga_server.events.catalog import (
    ConversationsDropped,
    ConversationsEnabled,
    ConversationsPruned,
    PruneFailed,
    WriteFailed,
)
from vinga_server.events.values import ClassName, Count, SessionId

events = ServerEvents(__name__)

# The plain channel, for the one report that is not an event. A record
# for a session the writer never opened cannot happen from the real call
# sites (`Open` is enqueued before the runtime can produce anything, on
# the same loop), so it is a defect rather than a condition an operator
# configures around, and giving it an event name would put a fifth token
# in a vocabulary that is a compatibility surface.
logger = logging.getLogger(__name__)

# This store's own chain: the schema its tables and its Alembic version
# table live in, its migrations, and the advisory key its writers
# serialize on. Declared here rather than beside `db.open_at`, because
# which schema a store lives in is a fact of that store; the schema name
# itself is read off the metadata that declares it.
CONVERSATIONS_CHAIN = StoreChain(
    schema=schema.SCHEMA,
    migrations=Path(__file__).resolve().parent / "migrations",
    # The second key in this application's half of the advisory lock
    # space. Distinct from the domain chain's on purpose: the two stores
    # are written by different callers at different moments, and sharing
    # a key would make a conversation wait on a configuration write.
    lock_key=advisory_key(2),
)

# How many `Event` records may be waiting on the writer before the
# producer starts refusing them. A turn produces tens of them, so this
# is minutes of backlog: a queue this deep means the database is wedged,
# and dropping is then the contract rather than a failure of it.
MAX_EVENTS_IN_FLIGHT = 1024

# How many times a durable transaction is tried before its batch is
# dropped. Three rather than one, and three rather than many: the only
# failures retried are the db classifier's transient class (a lock
# timeout on the chain's advisory gate, a deadlock, a serialization
# failure), which either clears on the next attempt or is not transient
# at all. The retries run on the writer thread with no sleep between
# them, because a writer that waits is a writer every other session's
# records are queued behind, and a wait long enough to outlast a real
# contention would be long enough to matter to them.
TURN_WRITE_ATTEMPTS = 3

# How long the store is kept before retention deletes it. Stated rather
# than infinite, because a store with no policy retains forever by
# default; 0 keeps everything and is a deliberate choice.
RETENTION_DAYS_DEFAULT = 90

# How long `stop()` waits for the writer thread. A policy margin above
# the lock wait rather than a derived ceiling: a commit parked on the
# chain's advisory lock gives up after `LOCK_TIMEOUT_MS`, so five
# seconds beyond that is room for the retry and the rest of the batch,
# and nothing bounds a whole transaction's execution. A commit wedged
# for any other reason cannot hold shutdown past this.
STOP_TIMEOUT_S = LOCK_TIMEOUT_MS / 1000 + 5.0

# What is taken off an event's fields before the row lands. One table,
# consulted in one place, because a content key that is scrubbed at some
# call sites and not others is a leak waiting for the next event to be
# added.
#
# Unconditional, and deliberately not under the text switch: the events
# table is metadata-only by construction, from its first row.
#
# `text` was the transcript half of three events and the narrowing
# (#120) has taken it off all three, so those entries are defense in
# depth at the one moment there is: a payload that regains the key meets
# this table on its way into a row rather than meeting a review. The
# strip runs in `_event_row` and nowhere else, so it is a rule about
# what this server writes and not about what it reads. Rows an older
# server wrote are not reached by it, and nothing serves their fields
# either: the API counts the events of a session and never returns them.
#
# `tool` is different, and still live. The narrowing took the called
# tool's name off every branch but one, because a device's name is its
# self-description and an MCP name is a far side's vocabulary, while a
# builtin's is this application's own; that branch still emits `tool`,
# and this strip still removes it from the row. The name is not lost: it
# is on `tool_invocations`, where the text switch decides whether it is
# kept, which is the point. Every name a call carries then lives in one
# table under one rule rather than in two under two, and the decision
# track stays a track of decisions.
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

# Which writers are running in this process, so that a deletion can tell
# them what is gone.
#
# A module-level register rather than a field on anything, because the
# two sides deliberately never meet. A deletion runs on a request thread
# against an engine it opened for itself, precisely so that erasure
# works in a deployment with recording off where no store object exists
# at all; the writer is a thread inside whichever store is recording.
# What they share is the process, and this is that.
#
# Empty is the ordinary state and costs nothing: a deployment that
# records nothing has no writer to tell, and a deletion there publishes
# into an empty list rather than accumulating a backlog nobody will ever
# read.
_writers: "list[ConversationStore]" = []
_writers_lock = threading.Lock()


def erased(threads_gone: "Iterable[str]") -> None:
    """Tell whatever writer is recording in this process that these
    threads are gone.

    The half of the dead-id rule that lives outside the writer. Absence
    of a row cannot be the tombstone, because absence is also the
    ordinary state of a thread before its first turn; so a deletion says
    so out loud, and the writer decides what that means for a turn
    already on its way.

    Called from inside the deletion's own transaction, which is where
    the ordering is decided rather than hoped for: that transaction
    holds the chain's advisory lock, and the writer takes the same lock
    at every marker, so a writer is never inside a marker while this
    runs. It either committed before the deletion, in which case its
    rows are what was deleted, or it begins afterwards and meets this
    at the marker.

    Said before the commit rather than after it, which is a trade rather
    than an oversight: an erasure whose transaction then fails leaves
    these ids marked dead for the rest of the process, and later turns of
    those threads are discarded although the rows survived. The other
    ordering leaves a window in which a turn arriving between the commit
    and this call raises an erased thread from the dead. Erasure
    outranks, so the loss is the one to take, and a failed deletion
    answers 500 and is made again.
    """
    named = set(threads_gone)
    if not named:
        return
    with _writers_lock:
        live = list(_writers)
    for writer in live:
        writer.forget(named)


def open_conversations(settings: DatabaseConfig) -> Engine:
    """Open and migrate the conversation record's schema.

    Always, and not only when recording is on. Migrating this schema
    creates empty tables, which is not a recording: what the privacy
    promise says is that recording off starts no writer and writes no
    rows, and a deployment that recorded last month still has to serve
    its history against the schema this build reads with. There is no
    file to leave behind for an absence to be visible in.
    """
    return open_at(settings, CONVERSATIONS_CHAIN)


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
    # The handle the producer kept, settled by the writer when this
    # turn's durable transaction commits or when the turn is dropped.
    # Carried on the queue item rather than looked up later, because
    # what it speaks for is this turn and a batch is not one.
    acknowledgement: Acknowledgement


@dataclass(frozen=True)
class Close:
    """A session ended. A marker, and the last record of that session."""

    session: str
    duration_s: float | None
    reason: str | None
    dropped: int


class Half(enum.Enum):
    """Which of a marker's two transactions the writer is about to open.

    Public, and a name rather than a comment, for one reason: it is what
    the injected gate is told. A marker used to open one transaction and
    "the writer is parked at this marker" said everything there was to
    say; it opens two now, and the interval between them is where a
    deletion lands in the one scenario that can produce an orphan event
    row. A test that arranges that interleaving has to be able to say
    which of the two halves it means to stop in front of.
    """

    DURABLE = enum.auto()
    EVENTS = enum.auto()


class _Durable(enum.Enum):
    """What became of a marker's durable transaction.

    Three answers rather than a boolean, because the third one is not a
    failure: a session deleted out from under a live conversation wrote
    nothing and is not going to, and the writer's response to it is to
    forget the session rather than to count a loss and try again.
    """

    COMMITTED = enum.auto()
    TOMBSTONED = enum.auto()
    FAILED = enum.auto()


@dataclass(frozen=True)
class _Stop:
    """The sentinel `stop()` puts behind everything already queued, so
    the drain is the writer's ordinary loop reaching the end rather than
    a second code path."""


@dataclass
class _Batch:
    """One session's records since its last marker, held in memory so
    that no transaction is open while the writer waits."""

    turns: list[Turn] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)


class ConversationStore:
    """The conversation record: one database, one writer thread.

    Built cold. The constructor opens and migrates the database, which
    is work the caller wants to fail at startup; `start()` begins the
    thread, and `stop()` ends it. Both are idempotent, so a teardown may
    call `stop()` whether or not `start()` ran, which is what the
    lifespan's exit stack does: it registers the stop the moment the
    store is constructed, in front of starting it. An app that never
    enters its lifespan builds no store at all (#142).

    The seams are for the tests that cannot otherwise pin what this
    promises, and each is compared `is not None` rather than by
    truthiness: an injected queue with a blocking `put` that raises is
    how "no producer path can wait" is proved, a wall clock is how
    retention is driven without sleeping, and the gate is where the
    writer parks so a wedged database can be exercised deterministically.
    The gate is told which half of a marker it stands in front of: the
    two halves are separate transactions, and what can happen in the
    interval between them is a scenario of its own.
    """

    def __init__(
        self,
        settings: DatabaseConfig,
        metrics: bool = True,
        text: bool = True,
        retention_days: int = RETENTION_DAYS_DEFAULT,
        queue: "queuing.SimpleQueue[Any] | None" = None,
        now: Callable[[], dt.datetime] | None = None,
        gate: Callable[[Half], None] | None = None,
        stop_timeout_s: float = STOP_TIMEOUT_S,
    ) -> None:
        self.settings = settings
        self.metrics = metrics
        self.text = text
        self.retention_days = retention_days
        self._engine = open_conversations(settings)
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
        # threaded. It is also what admission is decided under: every
        # producer reads `_stopped` and puts its record on the queue
        # holding this, and `stop()` sets the flag and queues the
        # sentinel holding it too, so no record can enter behind a drain
        # that has already finished.
        self._lock = threading.Lock()
        self._in_flight = 0
        self._opened_at: dict[str, float] = {}
        self._dropped: dict[str, int] = {}
        self._warned: set[str] = set()
        # Writer state, touched by the writer thread only.
        self._batches: dict[str, _Batch] = {}
        # Each recording session's device, kept because a thread's row
        # records the device it was begun on and a turn record carries
        # only the pair that owns it. Read at the marker that
        # materializes a thread, so it is kept for exactly as long as
        # the batch beside it.
        self._devices: dict[str, str | None] = {}
        self._unknown: set[str] = set()
        # Records this writer lost to a failed transaction, per session,
        # folded into the session row's count at close. Writer-side, so
        # unlike the producer's counter it needs no lock. The closing
        # marker's own events are the one loss this cannot carry, since
        # the close row is written before them; `_count_late_loss` adds
        # those to the row instead.
        self._lost: dict[str, int] = {}
        # Threads a dropped durable batch left a hole in, waiting for
        # their `incomplete` flag to land. In memory rather than in the
        # database because the database is the thing that just refused a
        # write; the flag rides the next durable transaction of any
        # session and is offered again at every marker after that until
        # it lands, and a thread whose every write failed leaves no row
        # and therefore nothing to flag, which is the honest record.
        self._incomplete: set[str] = set()
        # Threads a deletion has said are gone, published from a request
        # thread and drained by the writer at its next marker. Guarded,
        # because the two sides are different threads; drained rather
        # than kept, because what it is for is the intersection below and
        # a set that only grew would be a backlog nothing ever reads.
        self._published: set[str] = set()
        # Threads this writer has written a turn onto, and threads it now
        # refuses. The distinction is the whole of the dead-id rule: a
        # deletion of an id this process wrote makes it dead forever
        # after, while a turn for an id this writer has never seen is a
        # first turn and materializes as designed, because an id is minted
        # per session and agent and never travels between processes.
        #
        # Both grow with the threads one process handles, which is what
        # bounds them: a thread is a conversation somebody had, not a
        # record the store loops over.
        self._written: set[str] = set()
        self._dead: set[str] = set()

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
        # Reachable by a deletion from this moment: a thread cannot be
        # deleted before a writer has written it, and this writer starts
        # writing here.
        with _writers_lock:
            _writers.append(self)
        events.emit(ConversationsEnabled)

    def stop(self) -> None:
        """Accept nothing more, drain what is queued, and let go of the
        connections. Idempotent: a second call has nothing to do.

        Refusing and queueing the sentinel happen under the producers'
        own lock, and that is the whole of what makes the drain final. A
        producer decides it is admitted and puts its record on the queue
        under the same lock, so it is either in front of the sentinel and
        answered by the drain, or after this and refused with an
        acknowledgement that is already false. Between them there is no
        third place for a record to land, which is where one used to:
        behind a drain that had finished, holding a handle nothing would
        ever settle.

        The join is outside the lock, because the writer takes it every
        time it writes a batch off."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            thread = self._thread
            if thread is not None:
                self._queue.put(_Stop())
        if thread is not None:
            thread.join(timeout=self._stop_timeout_s)
        # Unregistered after the drain rather than before it: what is
        # queued still gets written, so a deletion landing while this is
        # draining still has to be able to stop it.
        with _writers_lock:
            if self in _writers:
                _writers.remove(self)
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
        with self._lock:
            if self._stopped:
                return
            self._opened_at[session_id] = opened_at
            self._dropped.setdefault(session_id, 0)
            self._queue.put_nowait(Open(session_id, opened_at, dict(manifest)))

    def record_event(
        self, session_id: str, name: str, level: int, fields: dict[str, Any], at: float
    ) -> None:
        """One structured event. The droppable class: refused beyond the
        in-flight bound, which is counted and reported once per session
        and lands on the session row at close."""
        if not self._stores_events():
            return
        with self._lock:
            if self._stopped or session_id not in self._opened_at:
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
                self._queue.put_nowait(
                    Event(session_id, t_ms, name, level, dict(fields))
                )
        # Outside the lock, because reporting a drop is not admitting a
        # record and the emitter is a stranger's code path.
        if t_ms is None and first:
            events.emit(lambda: ConversationsDropped(session=SessionId(session_id)))

    def record_turn(self, session_id: str, record: TurnRecord) -> Acknowledgement:
        """One completed turn. A control record and a marker: reaching it
        commits everything this session has accumulated.

        The offset is taken here, off the reading the record carries, for
        the reason the queue item states: the runtime is built before the
        session opens and never learns the reading its offsets are
        measured from.

        The handle that comes back is the durable path's answer about
        this turn and no other. Creating one costs an event object and
        nothing else, and the audio path drops it unwaited, which is why
        there is no second entry point for callers that do not care.
        A store that has already stopped answers a settled refusal
        rather than a handle nothing will ever settle."""
        acknowledgement = Acknowledgement()
        with self._lock:
            if self._stopped:
                acknowledgement.settle(False)
                return acknowledgement
            # A turn for a session this store never opened is refused by
            # the writer, which says so once. Stamping a zero rather than
            # asking for an offset there keeps that the writer's decision
            # instead of an exception raised on the session loop.
            t_ms = self._offset(session_id, record.at) if session_id in self._opened_at else 0
            self._queue.put_nowait(Turn(session_id, record, t_ms, acknowledgement))
        return acknowledgement

    def close_session(
        self, session_id: str, duration_s: float | None = None, reason: str | None = None
    ) -> None:
        """End one session's record. A control record and the last
        marker: a dropped close would make the store unable to say what
        it lost."""
        with self._lock:
            if self._stopped:
                return
            dropped = self._dropped.pop(session_id, 0)
            self._opened_at.pop(session_id, None)
            self._warned.discard(session_id)
            self._queue.put_nowait(Close(session_id, duration_s, reason, dropped))

    def forget(self, threads_gone: Iterable[str]) -> None:
        """These threads have been deleted; stop writing to them.

        Called from the deletion's transaction on a request thread, so
        it does the least it can: it records what was said and leaves
        every decision to the writer, which reads it at its next marker
        where nothing else is in flight. A store that has stopped keeps
        taking these, because what is queued behind the sentinel is still
        going to be written.
        """
        with self._lock:
            self._published.update(threads_gone)

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
                self._abandon_the_rest()
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
            self._devices[item.session] = _device_of(item.manifest)
            self._commit(item.session, opening=item)
            return
        if isinstance(item, Turn):
            batch = self._batches.get(item.session)
            if batch is None:
                item.acknowledgement.settle(False)
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
            self._devices.pop(item.session, None)
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

    def _abandon_the_rest(self) -> None:
        """Whatever arrived behind the stop sentinel, answered rather
        than left hanging.

        Nothing can, now that admission and the sentinel are taken under
        one lock: a producer is either in front of the sentinel or
        refused. This stays anyway, and the reason is the asymmetry
        rather than a suspicion about the lock. It costs one look at an
        empty queue, and what it answers for is a caller waiting out its
        own bound, or forever, for an answer that exists. The queue is
        also an injected seam, so what the writer finds behind the
        sentinel is not a question this class alone decides."""
        while True:
            try:
                item = self._queue.get_nowait()
            except queuing.Empty:
                return
            if isinstance(item, Turn):
                item.acknowledgement.settle(False)

    def _commit(
        self, session_id: str, opening: Open | None = None, closing: Close | None = None
    ) -> None:
        """One marker: two short transactions holding exactly this
        session's batch.

        The durable half first (the session row, the turns, their
        invocations, the threads they land on and the marks a previous
        loss owes those threads), then the events. The split is what
        keeps product state and telemetry from sharing a fate: neither
        failure decides the other's, and only the first is retried.

        The chain's advisory lock is taken before anything is read (the
        engine's begin listener takes it), so the existence check inside
        the durable half and the inserts that follow it cannot straddle a
        deletion. A failure rolls that half back whole, and the report
        that leaves carries the exception's class name and nothing else.
        """
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
            self._gate(Half.DURABLE)
        # After the gate and immediately before the transaction, which is
        # what "at its next marker" has to mean: a deletion that lands
        # while this writer is holding a batch has to be read after it
        # landed, and the last moment before the lock is taken is the
        # latest this writer can ask.
        self._discard_dead(batch)
        if opening is None and closing is None and not batch.turns and not batch.events:
            # Everything this marker was holding belonged to a thread
            # that has been erased, so there is nothing left to write.
            return
        outcome = self._durable(session_id, batch, opening, closing)
        if outcome is _Durable.TOMBSTONED:
            # Deleted out from under a live session. The tombstone is the
            # absence: this session is forgotten so nothing in flight can
            # resurrect it as orphan rows, and the turns it was holding
            # are answered as the dropped records they are.
            self._batches.pop(session_id, None)
            self._devices.pop(session_id, None)
            self._settle(batch, landed=False)
            self._release(len(batch.events))
            return
        if outcome is _Durable.COMMITTED:
            # Written, and therefore deletable: from here on, a deletion
            # naming one of these threads makes its id dead rather than
            # leaving it to materialize again.
            self._written.update(item.record.conversation for item in batch.turns)
        # Nothing is answered before the transaction that decides it has
        # committed, and that is why the incomplete marks are inside it:
        # a waiter woken by an acknowledgement reads the thread's row
        # next, and it may never find that row still claiming to be
        # whole. A later turn must never imply an earlier one landed.
        self._settle(batch, landed=outcome is _Durable.COMMITTED)
        lost = self._events(session_id, batch)
        if closing is not None and lost:
            # The close row was written before this half ran, and the
            # writer is about to forget this session, so a loss here has
            # nowhere else to go.
            self._count_late_loss(session_id, lost)
        # Committed or rolled back, this batch is written off either way.
        self._release(len(batch.events))
        # Never for a session the tombstone just removed: recreating its
        # state here is exactly the resurrection the check above exists
        # to prevent.
        if session_id in self._batches:
            self._batches[session_id] = _Batch()

    def _durable(
        self,
        session_id: str,
        batch: _Batch,
        opening: Open | None,
        closing: Close | None,
    ) -> "_Durable":
        """The half a conversation is made of, committed or dropped.

        Retried in place, and only for the db classifier's transient
        class: a lock that did not arrive is a transaction that would
        very likely commit if it were simply made again, and everything
        else is a database saying no for a reason a fourth attempt does
        not change. No sleep between attempts, because this thread is
        every other session's writer too.

        The marks a previous loss owes are written here too, after the
        turns and before the commit, which is what makes an
        acknowledgement safe to believe: the caller that wakes on one
        cannot find a thread with a known hole still claiming to be
        whole, because the mark and the turn became true at the same
        instant. It costs nothing when nothing is owed, and when the
        transaction is rolled back the ids stay owed and the turns stay
        lost together, which is the honest pair.

        A thread with no row yet is not marked and no row is made for
        it: an empty thread would be listed and offered as something to
        resume with no dialogue behind it. Its id waits here until a
        later turn of the same thread materializes the row, which it
        does with the mark already true (`threads.Landing.incomplete`).
        """
        for attempt in range(TURN_WRITE_ATTEMPTS):
            try:
                with self._engine.begin() as connection:
                    if opening is not None:
                        connection.execute(
                            sessions.insert().values(self._session_row(opening))
                        )
                    elif not self._alive(connection, session_id):
                        return _Durable.TOMBSTONED
                    self._write(connection, session_id, batch)
                    # After the turns rather than before them, so a
                    # thread this transaction has just materialized is
                    # one of the rows this finds and stops owing.
                    marked = threads.flag_incomplete(connection, self._incomplete)
                    if closing is not None:
                        connection.execute(
                            sessions.update()
                            .where(sessions.c.session == session_id)
                            .values(self._close_row(closing))
                        )
                # Discharged only once the transaction that wrote them
                # has committed, which is what the placement below the
                # block says.
                self._incomplete -= marked
                return _Durable.COMMITTED
            except Exception as exc:  # noqa: BLE001 - a write never breaks a session
                if attempt + 1 < TURN_WRITE_ATTEMPTS and is_busy(exc):
                    continue
                self._failed(session_id, batch, exc)
                return _Durable.FAILED
        # Unreachable: the loop either returns or exhausts its budget in
        # the arm above. Stated so the function has one exit type.
        return _Durable.FAILED

    def _events(self, session_id: str, batch: _Batch) -> int:
        """The lossy half, in a transaction of its own. Answers how many
        records it lost, which is none unless the transaction failed.

        No events rows at all under metrics-off, rather than rows with
        their payload emptied: the events table is the structured
        telemetry the switch turns off. A failure here drops and counts
        exactly what it dropped, and never touches a turn. The count is
        answered as well as kept because the caller is the only one that
        knows whether this session has a later marker to carry it: at
        every marker but the close it lands on the session row later,
        and at the close there is no later.

        The session is confirmed again inside this transaction, and that
        is not the durable half's check repeated for tidiness. The two
        halves are separate transactions, so a deletion can commit in
        the interval between them, and an events row has no foreign key
        to refuse it afterwards: retention reaches events only through
        the session rows that still exist, so a batch inserted behind a
        tombstone would be a row nothing can ever prune. Dropped
        silently, as everything else a tombstone overtakes is, and not
        counted either: the count's home is the session row, and the
        session row is what just went.
        """
        if not batch.events or not self._stores_events():
            return 0
        # In front of the transaction rather than in front of the
        # method, so a marker with nothing to write here is not a stop
        # for a transaction that never opens.
        if self._gate is not None:
            self._gate(Half.EVENTS)
        try:
            with self._engine.begin() as connection:
                if not self._alive(connection, session_id):
                    return 0
                connection.execute(
                    events_table.insert(),
                    [self._event_row(record) for record in batch.events],
                )
        except Exception as exc:  # noqa: BLE001 - a write never breaks a session
            # Bound to an ordinary local before the thunk closes over it,
            # the rule `_prune` states: `except ... as` unbinds its own
            # name when the block ends.
            failed: BaseException = exc
            self._lost[session_id] = self._lost.get(session_id, 0) + len(batch.events)
            events.emit(lambda: WriteFailed(failure=ClassName.of(failed)))
            return len(batch.events)
        return 0

    def _count_late_loss(self, session_id: str, lost: int) -> None:
        """Events the closing marker lost, added to the row that already
        says what this session dropped.

        Every other marker's loss waits in `_lost` and is folded into
        the close row when it is written. The close row is written by
        the durable half, which runs before this one, so a failure in
        the closing marker's events transaction would be counted in
        memory and then discarded with the rest of the session's state:
        the store would have promised a count of what it lost and then
        not kept one. One update in a transaction of its own is what
        that costs, on a marker that happens once per session.

        A session the tombstone took in the meantime matches no row, and
        that is the answer rather than a problem to solve: erasure
        outranks a counter about rows that are not there. A failure here
        is reported and not retried, because the writer has nothing left
        to retry it from.
        """
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    sessions.update()
                    .where(sessions.c.session == session_id)
                    .values(dropped=sessions.c.dropped + lost)
                )
        except Exception as exc:  # noqa: BLE001 - a write never breaks a session
            failed: BaseException = exc
            events.emit(lambda: WriteFailed(failure=ClassName.of(failed)))

    def _discard_dead(self, batch: _Batch) -> None:
        """The dead-id rule, applied at the marker: what a deletion said
        is gone, taken off this batch before anything is written.

        Two states, and keeping them apart is the whole of it. A thread
        whose row is simply absent is one before its first turn, and a
        turn for it materializes it as designed. A thread this writer
        wrote and a deletion has since named is dead: its turns are
        discarded, their acknowledgements resolve false, and the id is
        never materialized again, because a deleted conversation coming
        back as a new row with a new title derived from whatever was said
        next is the erasure undone.

        Discarded rather than counted as lost, which follows the
        tombstone above: a session deleted out from under a live
        conversation counts nothing either. What was deleted is not a
        write that failed, and `sessions.dropped` is where the store
        reports its own failures.

        The in-memory conversation carries on speaking; the runtime and
        the device edge know nothing about a deletion, and the user's
        experience is not the eraser's to interrupt. What stops is the
        record of it, which is what erasure means.
        """
        with self._lock:
            arrived, self._published = self._published, set()
        # Only what this writer wrote: an id it has never seen belongs to
        # no thread of this process, and a turn for one is a first turn.
        newly = arrived & self._written
        if newly:
            self._dead |= newly
            # A hole in a thread that no longer exists is not a fact
            # about anything, and its flag would never land.
            self._incomplete -= newly
        if not self._dead or not batch.turns:
            return
        kept = []
        for item in batch.turns:
            if item.record.conversation in self._dead:
                item.acknowledgement.settle(False)
            else:
                kept.append(item)
        batch.turns[:] = kept

    def _settle(self, batch: _Batch, landed: bool) -> None:
        """What became of every turn of this batch, said once each."""
        for item in batch.turns:
            item.acknowledgement.settle(landed)

    def _alive(self, connection: Any, session_id: str) -> bool:
        found = connection.execute(
            select(sessions.c.id).where(sessions.c.session == session_id)
        ).first()
        return found is not None

    def _write(self, connection: Any, session_id: str, batch: _Batch) -> None:
        # One stamp for the marker, so a turn and the activity it moves
        # on its thread land on one instant rather than on two readings
        # of the clock a microsecond apart.
        at = self._stamp()
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
            # The thread this turn is on, materialized with the first
            # one and moved by every later one, in this same
            # transaction. Storage policy is applied before the handover
            # rather than inside the thread store: a title derives from
            # what was stored, so text-off derives none by the ordinary
            # path. A landing the thread store refuses raises out of
            # here and rolls this marker back, batch and all: losing a
            # turn is recoverable and counted, while storing one no
            # thread owns is a row retention can never reach.
            threads.landed(
                connection,
                threads.Landing(
                    conversation=item.record.conversation,
                    agent=item.record.agent,
                    device=self._devices.get(session_id),
                    heard=item.record.heard if self.text else None,
                    at=at,
                    # A thread with a hole in it, materializing at last.
                    # The flag rides the insert rather than following it,
                    # so there is no instant at which the row exists and
                    # claims to be whole.
                    incomplete=item.record.conversation in self._incomplete,
                ),
            )

    def _failed(self, session_id: str, batch: _Batch, exc: BaseException) -> None:
        """A durable transaction that did not commit. The batch is gone
        and counted, the threads it fed are latched as incomplete, and
        the writer keeps consuming. When the marker was the close, the
        session row stays open-shaped, which is the store's documented
        incomplete state: readable, listed with its null close, and
        pruned on `started_at` like any other."""
        self._lost[session_id] = self._lost.get(session_id, 0) + len(batch.turns)
        # Product state, and deliberately not under the metrics switch:
        # `sessions.dropped` above is zeroed under metrics-off and a
        # thread with a hole in it is true either way.
        self._incomplete.update(item.record.conversation for item in batch.turns)
        events.emit(lambda: WriteFailed(failure=ClassName.of(exc)))

    # --- rows, with both switches applied ------------------------------

    def _session_row(self, opening: Open) -> dict[str, Any]:
        """The session spine, written in every enabled configuration:
        retention and the read API both key on it, and the two switch
        columns are what make a null elsewhere readable."""
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
            "conversation": record.conversation,
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
        """Run retention, in the writer, so it serializes with the
        writes by construction. Runs at start and at each session close,
        which is often enough for a store that only grows when a
        conversation happens.

        The ruleset is `threads.prune`, whose unit is the thread: a
        conversation older than the window goes whole, events go by
        their session's age, and a session row goes once no turn names
        it. What is decided here is only when to run it and what to say
        about it afterwards.

        What deletion means here is stated exactly, because the backend
        decides it and the backend has moved. A deleted row is invisible
        to every transaction that begins after this one commits,
        including the analyst role's; a repeatable-read transaction
        already in flight when it commits keeps seeing the row until it
        ends, which is what MVCC is. Reclaiming the space the row
        occupied is the database server's own storage maintenance
        (autovacuum), not a per-delete overwrite: there is no freed page
        for this process to write zeros over, and the file the SQLite
        era erased in place is not this deployment's to reach.

        Failure is a dropped prune, not a dropped conversation: a store
        that could not delete still records, and the next close tries
        again."""
        if self.retention_days <= 0:
            return
        cutoff = (self._now() - dt.timedelta(days=self.retention_days)).isoformat()
        try:
            with self._engine.begin() as connection:
                taken = threads.prune(connection, cutoff)
        except Exception as exc:  # noqa: BLE001 - retention never breaks a session
            # Bound to an ordinary local before the thunk closes over
            # it: `except ... as` unbinds its own name when the block
            # ends, and a closure reaching for it afterwards would find
            # nothing. The construction still runs inside the emitter's
            # guard, which is the whole reason it is a thunk.
            failed: BaseException = exc
            events.emit(lambda: PruneFailed(failure=ClassName.of(failed)))
            return
        if taken.anything():
            events.emit(
                lambda: ConversationsPruned(
                    conversations=Count(taken.conversations),
                    sessions=Count(taken.sessions),
                    days=Count(self.retention_days),
                )
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


def _device_of(manifest: dict[str, Any]) -> str | None:
    """The device a session opened on, out of the manifest both the
    session row and the thread row are built from.

    One reader for the one nested shape, because the two callers want
    the same answer at different moments: the row is written at the
    open, the thread's is wanted at whichever later marker materializes
    a thread.
    """
    device = manifest.get("device")
    return device.get("mac") if isinstance(device, dict) else None


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


__all__ = [
    "CONVERSATIONS_CHAIN",
    "MAX_EVENTS_IN_FLIGHT",
    "RETENTION_DAYS_DEFAULT",
    "STOP_TIMEOUT_S",
    "TURN_WRITE_ATTEMPTS",
    "Close",
    "ConversationStore",
    "Event",
    "Half",
    "Open",
    "SessionSink",
    "Turn",
    "erased",
    "open_conversations",
]
