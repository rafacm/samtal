"""The store behind an agent's remembered facts: one chain, two engines.

What this module hides from its callers is the whole of the storage: a
schema of its own and the migrations that build it, an advisory lock
that serializes writers across processes, a write engine that takes
that lock before it reads anything, a separate read-only engine so a
reply's memory read never waits on a writer, and a connection whose
failures are classified rather than quoted. A caller asks for a store
and disposes it; it never learns which schema, which lock key, which
isolation level, or what a psycopg failure looks like.

What a caller does get is two sentences: `read(agent)`, the facts as
the prompt injects them, and `remember(agent, fact)`, one fact kept.
Memory is keyed by agent and not by agent and device, because an agent
is one entity across rooms: "remember I am vegetarian", said in the
kitchen, holds in the bedroom. Telling people apart on a shared device
is the voiceprint problem, and keying by device would fragment memory
without solving it.

There is no recall tool. For memory small enough to inject, injection
is the standard shape: it costs no lookup latency (a recall round trip
is spoken silence) and does not depend on a small local model choosing
to call it. The caps below are what keep that true, and are why this
becomes a two-tier store, a small injected core plus a search tool,
once memory outgrows the prompt.

The chain is declared here rather than beside `db.open_at`, for the
reason the conversation store's is: which schema a store lives in is a
fact of that store, and the schema name is read off the metadata that
declares it.
"""

import asyncio
import contextlib
import datetime as dt
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path

from sqlalchemy import Connection, Engine, delete, select

from vinga_server.config.loader import DatabaseBusyError, StorageError
from vinga_server.config.models import DatabaseConfig
from vinga_server.db import (
    LOCK_TIMEOUT_MS,
    StoreChain,
    advisory_key,
    is_busy,
    open_at,
    read_engine,
)
from vinga_server.events import ServerEvents
from vinga_server.events.catalog import MemoryUnreadable, MemoryUnwritable
from vinga_server.events.values import ClassName, Identifier
from vinga_server.memory import schema

events = ServerEvents(__name__)

# What keeps injection cheap: whichever trips first wins, and an insert
# that overflows drops the oldest facts.
#
# Module-level and read at call time, which is what the two cap suites
# monkeypatch. The same two numbers and the same two names the file
# store enforced, because the storage moved and the promise did not.
MAX_BYTES = 8192
MAX_LINES = 200

# What a write that the database refused answers with, as fixed
# sentences carrying no value at all.
#
# These are the one class of failure in this module a model reads out
# loud: a raise from `remember` becomes the tool result the reply is
# built on, so a psycopg message here would put the DSN it tried, and
# therefore the password in its authority, in front of a model and into
# the conversation record. The class of the failure travels on the
# event instead, where an operator reads it.
UNWRITABLE = (
    "this fact could not be stored: the database this server keeps memory in "
    "refused the write, and nothing was remembered. Nothing of the failure is "
    "repeated here, because a database error quotes the connection it tried and a "
    "connection carries a password"
)

BUSY = (
    "this fact could not be stored: another connection was writing to memory for "
    "longer than the lock timeout allows, and nothing was remembered. Nothing was "
    "changed, and the same fact may simply be offered again"
)


class StoreClosed(Exception):
    """The store is shutting down and is not admitting new calls.

    Raised where a call asks for admission rather than where it would
    reach a connection, which is what makes it cheap and what makes it
    safe: a store that is closing has engines whose pools are about to
    be replaced, and a call let through would open one nobody owns.

    Not a sentence anybody reads. It travels as a class name on the two
    events this module emits, and both call sites contain it exactly as
    they contain a database that is not there: the read answers with no
    memory this round, and the write refuses with `UNWRITABLE`. A
    shutdown is not a failure a model should be given different words
    for.
    """

# How long a close waits for the calls already inside a connection.
#
# Derived from the lock timeout rather than picked, because that is what
# actually bounds a call: a `remember` parked on the chain's advisory
# gate waits up to `LOCK_TIMEOUT_MS` before the database refuses it, and
# a shorter wait here would expire while a call that is behaving exactly
# as designed is still inside its connection. The margin on top is the
# round trip the refusal itself costs. A store that will not go quiet
# even then does not hold the shutdown open; what happens instead is in
# `close`.
QUIET_TIMEOUT_S = LOCK_TIMEOUT_MS / 1000 + 2.0

# This store's own chain: the schema its table and its Alembic version
# table live in, its migrations, and the advisory key its writers
# serialize on.
MEMORY_CHAIN = StoreChain(
    schema=schema.SCHEMA,
    migrations=Path(__file__).resolve().parent / "migrations",
    # The third key in this application's half of the advisory lock
    # space. Distinct from the other two on purpose: a `remember` is a
    # tool call a human asked for mid-conversation, and sharing the
    # domain chain's key would make it wait behind an `apply`
    # transaction that is deliberately unbounded, while sharing the
    # record's would make it wait behind a turn being written.
    lock_key=advisory_key(3),
)


class MemoryStore:
    """One database's worth of remembered facts.

    Built through `open_memory`, which is where the migration happens,
    so a database the server cannot reach fails the boot rather than the
    first thing an agent is asked to remember.

    Two engines and not one, which is the property the read path is
    bought with. The write engine takes the chain's advisory lock on
    every `begin`, so two writers through independent connections
    serialize whole; the read engine takes nothing, is repeatable-read
    and read-only, and therefore answers while another connection holds
    the chain lock. That is what keeps reading cheap and unblocking on
    the path that builds a system prompt, which is the property the file
    store had for free and a single engine would have spent.
    """

    def __init__(self, engine: Engine, reader: Engine) -> None:
        self._engine = engine
        self._reader = reader
        # The three facts a close and a call have to agree about, and
        # the lock they agree about them under. `read` and `remember`
        # run in a worker thread while the close runs on the loop, so
        # every one of them is touched from two threads: how many calls
        # are inside a connection, whether this store is closing, and
        # whether the pools have already been let go.
        self._quiet = threading.Condition()
        self._in_flight = 0
        self._closing = False
        self._disposed = False

    def close(self) -> None:
        """Stop admitting calls, and let go of both connection pools
        once the calls already inside one have finished with it.

        Registered on the application's exit stack the moment the store
        is opened, so a boot that fails later unwinds through it. Safe
        to call twice, and safe to call while a call is in flight, which
        is the whole reason it is written this way.

        Disposing an engine closes the connections sitting in its pool
        and replaces the pool; a connection checked out at that moment
        is returned to the pool that was replaced, which nothing owns
        any more and which closes nothing when it is collected. The
        reply path reads memory from a worker thread and the shutdown
        drain is bounded, so a call outliving the close is reachable
        rather than theoretical.

        Two things follow, and neither is politeness. The store stops
        admitting calls before it waits, atomically, so a worker that
        was queued behind the close cannot slip in and open a pool this
        method has already decided is going away. And the disposal is
        deferred rather than forced: the wait is bounded by
        `QUIET_TIMEOUT_S` so a shutdown is never held open, but a wait
        that expires hands the disposal to whichever call returns last
        rather than pulling the pool out from under it. Either way it
        happens exactly once.
        """
        with self._quiet:
            self._closing = True
            self._quiet.wait_for(lambda: self._in_flight == 0, timeout=QUIET_TIMEOUT_S)
            mine = self._claim_the_disposal()
        if mine:
            self._dispose()

    def _claim_the_disposal(self) -> bool:
        """Whether the caller is the one that disposes, decided under
        the lock so that exactly one of them is.

        Called by `close` and by the last call out, which is the pair
        the deferral is between: whichever of them finds no call in
        flight and no disposal yet done takes it.
        """
        if self._in_flight or self._disposed:
            return False
        self._disposed = True
        return True

    def _dispose(self) -> None:
        self._engine.dispose()
        self._reader.dispose()

    @contextlib.contextmanager
    def _connected(self) -> Iterator[None]:
        """One call holding a connection, counted so the close can wait
        for it, and refused outright once the close has begun.

        The refusal is raised out of `__enter__`, which is before the
        engine in the caller's `with` is reached at all, so a call that
        arrives during a close touches no pool: what it meets is the
        decision, not a connection.
        """
        with self._quiet:
            if self._closing:
                raise StoreClosed
            self._in_flight += 1
        try:
            yield
        finally:
            with self._quiet:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._quiet.notify_all()
                mine = self._closing and self._claim_the_disposal()
            if mine:
                self._dispose()

    def read(self, agent: str) -> str:
        """This agent's facts, or an empty string when it has none.

        Rendered as the prompt injects it: one `- fact` line per row, in
        insertion order, with no trailing newline. Read per reply rather
        than cached, so a fact remembered in one session is known to a
        concurrent one on its next reply.

        Synchronous, and on the read engine, which is the whole of why
        the store holds two. Reads take no advisory lock, so this
        answers while another connection is midway through a `remember`
        and can never wait out a lock timeout on the path that builds a
        system prompt.

        Nothing about a database that cannot be read reaches the caller.
        This is on the path that builds a system prompt, so a raised
        exception would leave as a traceback under "reply failed", and a
        psycopg failure quotes the DSN it tried, which carries a
        password in its authority. A database this server cannot read
        means this agent remembers nothing this round, and the reply
        happens. What is logged is the class of the failure and never
        the message, which is the rule the MCP layer's reason tokens and
        the thread reads already follow.
        """
        try:
            with self._connected(), self._reader.connect() as connection:
                return _rendered(_stored(connection, agent))
        except Exception as exc:  # noqa: BLE001 - the whole point of the seam
            failure = exc
            events.emit(
                lambda: MemoryUnreadable(
                    agent=Identifier(agent),
                    scope=schema.MemoryScope.AGENT,
                    error=ClassName.of(failure),
                )
            )
            return ""

    async def remember(self, agent: str, fact: str) -> None:
        """Keep one fact, normalized to a line and capped.

        The refusal for an empty fact is a caller's mistake rather than
        a storage failure, so it is a `ValueError` with the sentence it
        has always had and no event: the tool layer above turns a bad
        argument into something the model rephrases.

        Everything else happens in one transaction on the write engine,
        which takes the chain's advisory lock before it reads. That is
        what makes the read-count-prune arithmetic below correct across
        processes without a word about isolation levels, and what the
        per-agent `asyncio.Lock` of the file era was a single-process
        approximation of. The transaction runs in a worker thread
        because the driver is blocking and the caller is the event loop
        every live conversation shares.
        """
        text = " ".join(fact.split())
        if not text:
            raise ValueError("there is nothing to remember")
        await asyncio.to_thread(self._store, agent, text)

    def _store(self, agent: str, fact: str) -> None:
        """The insert and the pruning, in one transaction.

        One transaction and not two, which is the durability the file
        store could not offer: no reader ever sees an over-cap state,
        and a failure between the insert and the prune leaves the store
        exactly as it was rather than over its cap forever.

        The refusal is built inside the handler and raised after it,
        cause severed, the way every boundary in this project raises: an
        exception raised while another is being handled keeps that one
        on `__context__`, and here that one holds the connection string
        it tried.
        """
        problem: Exception | None = None
        try:
            with self._connected(), self._engine.begin() as connection:
                connection.execute(
                    schema.facts.insert().values(
                        scope=schema.MemoryScope.AGENT,
                        owner=agent,
                        at=_utc_now().isoformat(),
                        fact=fact,
                    )
                )
                doomed = _over_the_cap(_stored(connection, agent))
                if doomed:
                    connection.execute(
                        delete(schema.facts).where(schema.facts.c.id.in_(doomed))
                    )
        except Exception as exc:  # noqa: BLE001 - classified, never quoted
            failure = exc
            events.emit(
                lambda: MemoryUnwritable(
                    agent=Identifier(agent),
                    scope=schema.MemoryScope.AGENT,
                    error=ClassName.of(failure),
                )
            )
            # By class and never by message, through the one classifier
            # `db` owns: a contended write is retryable and says so, and
            # everything else is the general refusal.
            problem = DatabaseBusyError(BUSY) if is_busy(exc) else StorageError(UNWRITABLE)
        if problem is not None:
            raise problem


def _stored(connection: Connection, agent: str) -> list[tuple[int, str]]:
    """One agent's active facts, oldest first, each already a rendered
    line.

    `ORDER BY id` is the file's line order, and the index the schema
    declares is on exactly the three columns this walks. Rendered here
    rather than at the two call sites because the byte cap is applied to
    the rendering, so the line is what both the reader and the prune
    have to be counting.

    Held facts are not stored facts. A forgotten one is out of every
    read, out of the prune's arithmetic and out of the caps until it is
    restored, which is what makes the undo a promise rather than a race
    against the next write.
    """
    rows = connection.execute(
        select(schema.facts.c.id, schema.facts.c.fact)
        .where(
            schema.facts.c.scope == schema.MemoryScope.AGENT,
            schema.facts.c.owner == agent,
            schema.facts.c.forgotten_at.is_(None),
        )
        .order_by(schema.facts.c.id)
    ).all()
    return [(row_id, f"- {fact}") for row_id, fact in rows]


def _over_the_cap(stored: Sequence[tuple[int, str]]) -> list[int]:
    """Which rows no longer fit, by id. The oldest go first: a fact
    worth keeping tends to get said again, and the alternative (refusing
    to remember anything more) is worse to hear.

    The same algorithm the file store applied to lines, on the same two
    constants read at call time: keep the newest `MAX_LINES`, then drop
    the oldest while the rendered block is over `MAX_BYTES`, never below
    one fact.
    """
    kept = list(stored[-MAX_LINES:])
    while len(kept) > 1 and len(_rendered(kept).encode("utf-8")) > MAX_BYTES:
        kept.pop(0)
    surviving = {row_id for row_id, _ in kept}
    return [row_id for row_id, _ in stored if row_id not in surviving]


def _rendered(stored: Sequence[tuple[int, str]]) -> str:
    return "\n".join(line for _, line in stored)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def open_memory(settings: DatabaseConfig) -> MemoryStore:
    """Open and migrate the memory schema, and answer the store on it.

    Always, and not behind any switch. The schema is migrated at every
    boot the way the record schema is, because migrating creates an
    empty table and an empty table is not a memory; an agent that has
    been told nothing reads as the empty string and gets no block in its
    prompt, exactly as an empty file rendered.

    Every failure is a `ConfigError` carrying one of the fixed sentences
    `db` owns, including the one for a role that may not create the
    schema, which is what an existing least-privilege deployment meets
    if it starts this image before rerunning
    `deploy/postgres-init.sql`.
    """
    engine = open_at(settings, MEMORY_CHAIN)
    # The read engine connects to nothing here (SQLAlchemy connects
    # lazily), so what is guarded is the narrow case of a URL the write
    # engine accepted and this one did not. Bare `raise`, because what
    # would travel is already one of `db`'s own sanitized refusals and
    # re-wrapping it would add a chain rather than remove one.
    try:
        reader = read_engine(settings)
    except Exception:
        engine.dispose()
        raise
    return MemoryStore(engine, reader)


__all__ = [
    "BUSY",
    "QUIET_TIMEOUT_S",
    "MAX_BYTES",
    "MAX_LINES",
    "MEMORY_CHAIN",
    "UNWRITABLE",
    "MemoryStore",
    "StoreClosed",
    "open_memory",
]
