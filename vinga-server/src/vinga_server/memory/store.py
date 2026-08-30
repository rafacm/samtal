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
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import TypeVar

from sqlalchemy import Connection, Engine, delete, select
from sqlalchemy import update as sql_update

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
from vinga_server.memory.schema import MemoryScope

events = ServerEvents(__name__)

# What one guarded call answers with, whatever that is: a rendering, an
# id, a removed fact. The two seams below are the same shape over
# different answers, and a type variable is what keeps them one seam
# rather than one per return type.
T = TypeVar("T")

# What keeps injection cheap: whichever trips first wins, and an insert
# that overflows drops the oldest facts.
#
# Module-level and read at call time, which is what the two cap suites
# monkeypatch. The same two numbers and the same two names the file
# store enforced, because the storage moved and the promise did not.
MAX_BYTES = 8192
MAX_LINES = 200

# What a device keeps, which is smaller because a device accumulates the
# few notes a place has rather than a person's whole history, and
# because the whole of it is injected rather than searched.
DEVICE_BYTES = 2048
DEVICE_LINES = 30

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

# And what a caller's own mistake answers with, as fixed sentences for
# the same reason: a model reads these out too. Nothing a caller passed
# is quoted back in any of them, because what a caller passes here is
# the user's own words.
NOTHING_TO_REMEMBER = "there is nothing to remember"

TOO_LONG = (
    "this is too long to remember: one fact has to fit inside the space this memory "
    "keeps for all of them, and forgetting everything else would not make room for "
    "it. Nothing was changed; say it in fewer words"
)

# What an id that names nothing this caller may reach answers with, one
# sentence per operation.
#
# A fact that is not there and a fact that belongs to another agent or
# another device are answered identically, on purpose and not by
# accident: a refusal that told them apart would confirm that somebody
# else's ids exist and let them be walked. Nothing of the id is repeated
# back either, for the same reason.
NO_FACT_TO_UPDATE = (
    "there is no such fact to correct: no fact of this memory has that number. "
    "Nothing was changed. Look the fact up again to find the number it has now"
)

NO_FACT_TO_FORGET = (
    "there is no such fact to forget: no fact of this memory has that number. "
    "Nothing was changed. Look the fact up again to find the number it has now"
)

NO_FACT_TO_RESTORE = (
    "there is nothing to bring back: nothing was forgotten in this conversation "
    "under that number, and a fact forgotten in another conversation cannot be "
    "brought back from this one. Nothing was changed"
)


class _Refused(Exception):
    """One of this module's fixed refusals, decided where the rows are
    and carried out of the transaction that decided it.

    Never seen by a caller. `_written` turns it into the `ValueError` the
    tool layer above turns into something the model rephrases, outside
    the handler, so nothing of the transaction travels on a chain; and it
    is a class of its own so that the arm which classifies a database
    failure cannot mistake a decision for one.

    It carries a sentence this module wrote and nothing else, which is
    what makes rolling the transaction back the whole of its cost.
    """


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

    def _read(
        self,
        agent: str,
        scopes: Sequence[MemoryScope],
        work: Callable[[Connection], T],
        empty: T,
    ) -> T:
        """One read on the read engine, contained whole.

        Nothing about a database that cannot be read reaches the caller.
        These are on the path that builds a system prompt, so a raised
        exception would leave as a traceback under "reply failed", and a
        psycopg failure quotes the DSN it tried, which carries a
        password in its authority. A database this server cannot read
        means the agent remembers nothing of these scopes this round, and
        the reply happens. What is logged is the class of the failure and
        never the message, which is the rule the MCP layer's reason
        tokens and the thread reads already follow.

        One connection for whatever the caller asked, and one report per
        scope it asked about. A read that answers three scopes takes one
        connection because the reply path pays for one off-loop hop per
        round, and a statement that fails poisons the transaction the
        other two would have run in, so all of them are lost together and
        all of them are said. A scope that rendered empty with nothing
        said about it would be indistinguishable from a scope with
        nothing in it.

        `scopes` and not one scope for the same reason: `recall` reaches
        across two and the prompt read across three, and each of them
        loses everything it reached for.
        """
        try:
            with self._connected(), self._reader.connect() as connection:
                return work(connection)
        except Exception as exc:  # noqa: BLE001 - the whole point of the seam
            failure = exc
            for scope in scopes:
                events.emit(
                    lambda scope=scope: MemoryUnreadable(  # type: ignore[misc]
                        agent=Identifier(agent),
                        scope=scope,
                        error=ClassName.of(failure),
                    )
                )
            return empty

    def _written(
        self, agent: str, scope: MemoryScope, work: Callable[[Connection], T]
    ) -> T:
        """One transaction on the write engine, its failures classified
        and never quoted.

        The transaction takes the chain's advisory lock before it reads,
        which is what makes every read-decide-write arithmetic below
        correct across processes without a word about isolation levels,
        and what the per-agent `asyncio.Lock` of the file era was a
        single-process approximation of. It is one transaction and not
        two, which is the durability the file store could not offer: no
        reader ever sees an over-cap state, and a failure between a write
        and the prune that follows it leaves the store exactly as it was.

        Two kinds of failure leave here, and only one of them is a
        failure. A `_Refused` is this module's own decision, made where
        the rows are and carried out as the `ValueError` the tool layer
        turns into something the model rephrases; it emits nothing,
        because nothing went wrong. Everything else is the database, and
        is classified by class through the one classifier `db` owns.

        Every refusal is built inside the handler and raised after it,
        cause severed, the way every boundary in this project raises: an
        exception raised while another is being handled keeps that one on
        `__context__`, and here that one holds the connection string it
        tried.
        """
        problem: Exception
        try:
            with self._connected(), self._engine.begin() as connection:
                return work(connection)
        except _Refused as refusal:
            problem = ValueError(str(refusal))
        except Exception as exc:  # noqa: BLE001 - classified, never quoted
            failure = exc
            events.emit(
                lambda: MemoryUnwritable(
                    agent=Identifier(agent),
                    scope=scope,
                    error=ClassName.of(failure),
                )
            )
            # By class and never by message, through the one classifier
            # `db` owns: a contended write is retryable and says so, and
            # everything else is the general refusal.
            problem = DatabaseBusyError(BUSY) if is_busy(exc) else StorageError(UNWRITABLE)
        raise problem

    def read(self, agent: str) -> str:
        """This agent's facts, or an empty string when it has none.

        Rendered as the prompt injects it: one `- fact` line per row, in
        insertion order, with no trailing newline. Read per reply rather
        than cached, so a fact remembered in one session is known to a
        concurrent one on its next reply.

        Synchronous, and on the read engine, which is the whole of why
        the store holds two. Reads take no advisory lock, so this
        answers while another connection is midway through a write and
        can never wait out a lock timeout on the path that builds a
        system prompt.

        The agent's scope whole, uncapped by the core, which is what
        keeps this the sentence #314's callers speak while the scoped
        rendering arrives beside it.
        """
        return self._read(
            agent,
            (MemoryScope.AGENT,),
            lambda connection: _rendered(_active(connection, MemoryScope.AGENT, agent)),
            "",
        )

    async def remember(self, agent: str, fact: str) -> None:
        """Keep one fact for this agent, normalized to a line and capped.

        `add` under another name, on the one scope that existed before
        there were scopes, and the id it returns is dropped: what a
        caller of this sentence does with an id is nothing, and #83's
        callers ask for `add` instead.
        """
        await self.add(MemoryScope.AGENT, agent, fact, agent=agent)

    async def add(
        self, scope: MemoryScope, owner: str, fact: str, *, agent: str
    ) -> int:
        """Keep one fact in one scope, and answer the id it is addressed
        by from now on.

        The address is the pair `(scope, owner)`: an agent's own name
        under `agent`, a device's MAC under `device`. `agent` beside them
        is who was speaking, which is the same name under agent scope and
        a different one under device scope, and it is carried for the
        event alone: an operator reading a refused write wants the agent
        whose conversation lost it.

        Two refusals are decided before a connection is reached, because
        neither needs one and both are the caller's mistake rather than a
        storage failure: nothing to remember, and a fact whose rendered
        line alone will not fit inside the space its scope keeps for all
        of them. The second exists because pruning everything else could
        not make room for it, so accepting it would leave the scope over
        its cap for as long as the fact lived.

        The transaction runs in a worker thread because the driver is
        blocking and the caller is the event loop every live conversation
        shares.
        """
        text = _one_line(fact)
        if not text:
            raise ValueError(NOTHING_TO_REMEMBER)
        _refuse_the_oversized(text, scope)
        return await asyncio.to_thread(self._add, agent, scope, owner, text)

    def _add(self, agent: str, scope: MemoryScope, owner: str, fact: str) -> int:
        def work(connection: Connection) -> int:
            landed = connection.execute(
                schema.facts.insert().values(
                    scope=scope,
                    owner=owner,
                    at=_utc_now().isoformat(),
                    fact=fact,
                )
            ).inserted_primary_key[0]
            _prune(connection, scope, owner, protecting=landed)
            return int(landed)

        return self._written(agent, scope, work)

    async def update(
        self, scope: MemoryScope, owner: str, fact_id: int, text: str, *, agent: str
    ) -> None:
        """Correct one fact in place, keeping the id it is addressed by
        and refreshing the moment it was written.

        The id is bounded by ownership in the WHERE clause rather than by
        the model's good behavior: what this reaches is a row whose
        `(scope, owner)` is the pair the caller named and nothing else,
        so an agent cannot correct another agent's fact by guessing a
        number, and a device's notes are reachable only as the device.

        Held facts are not reachable either. A fact that was forgotten is
        waiting to be brought back as it was said, and editing it there
        would make the undo answer with something the user never said.
        """
        corrected = _one_line(text)
        if not corrected:
            raise ValueError(NOTHING_TO_REMEMBER)
        _refuse_the_oversized(corrected, scope)
        await asyncio.to_thread(self._update, agent, scope, owner, fact_id, corrected)

    def _update(
        self, agent: str, scope: MemoryScope, owner: str, fact_id: int, fact: str
    ) -> None:
        def work(connection: Connection) -> None:
            done = connection.execute(
                sql_update(schema.facts)
                .where(*_addressed(scope, owner, fact_id), _ACTIVE)
                .values(fact=fact, at=_utc_now().isoformat())
            )
            if done.rowcount != 1:
                raise _Refused(NO_FACT_TO_UPDATE)
            # A correction can grow a fact past what its scope holds, so
            # the same prune every mutation ends with runs here too, and
            # the corrected row is the one it may not take.
            _prune(connection, scope, owner, protecting=fact_id)

        self._written(agent, scope, work)

    async def forget(
        self,
        scope: MemoryScope,
        owner: str,
        fact_id: int,
        conversation: str,
        *,
        agent: str,
        permanently: bool = False,
    ) -> str:
        """Forget one fact, and answer what was removed so the agent can
        say it out loud.

        Soft by default, which is the whole of the reversibility
        decision: the row is held rather than erased, out of every read
        and out of every cap, and the conversation it was forgotten in is
        recorded because that is what a restore addresses and what the
        thread's own end takes with it.

        `permanently` erases immediately and enters no held area, so
        there is nothing to bring back and nothing left for an operator
        to find. It is the one door for a fact that should not have been
        stored at all.
        """
        return await asyncio.to_thread(
            self._forget, agent, scope, owner, fact_id, conversation, permanently
        )

    def _forget(
        self,
        agent: str,
        scope: MemoryScope,
        owner: str,
        fact_id: int,
        conversation: str,
        permanently: bool,
    ) -> str:
        def work(connection: Connection) -> str:
            found = connection.execute(
                select(schema.facts.c.fact).where(
                    *_addressed(scope, owner, fact_id), _ACTIVE
                )
            ).first()
            if found is None:
                raise _Refused(NO_FACT_TO_FORGET)
            where = _addressed(scope, owner, fact_id)
            if permanently:
                connection.execute(delete(schema.facts).where(*where))
            else:
                connection.execute(
                    sql_update(schema.facts)
                    .where(*where)
                    .values(
                        forgotten_at=_utc_now().isoformat(),
                        forgotten_in=conversation,
                    )
                )
            # No prune: taking a row out of the active set can only free
            # capacity, never use it.
            return str(found[0])

        return self._written(agent, scope, work)

    async def restore(
        self,
        scope: MemoryScope,
        owner: str,
        conversation: str,
        fact_id: int | None = None,
        *,
        agent: str,
    ) -> str:
        """Bring back a fact this conversation forgot, and answer it.

        With no id, the last thing forgotten in this conversation, which
        is the shape a person actually asks for. With one, that fact, and
        the conversation is still part of the address: a fact forgotten
        somewhere else is not this conversation's to bring back, which
        makes the id door mean what the no-id door means.

        The restored fact comes back exactly as it was said. Its id is
        the one it always had, and its place in the reading order is the
        one it always had, because the held area kept the row rather than
        a copy of its text.
        """
        return await asyncio.to_thread(
            self._restore, agent, scope, owner, conversation, fact_id
        )

    def _restore(
        self,
        agent: str,
        scope: MemoryScope,
        owner: str,
        conversation: str,
        fact_id: int | None,
    ) -> str:
        def work(connection: Connection) -> str:
            held = select(schema.facts.c.id, schema.facts.c.fact).where(
                schema.facts.c.scope == scope,
                schema.facts.c.owner == owner,
                schema.facts.c.forgotten_in == conversation,
            )
            if fact_id is None:
                held = held.order_by(
                    schema.facts.c.forgotten_at.desc(), schema.facts.c.id.desc()
                ).limit(1)
            else:
                held = held.where(schema.facts.c.id == fact_id)
            found = connection.execute(held).first()
            if found is None:
                raise _Refused(NO_FACT_TO_RESTORE)
            connection.execute(
                sql_update(schema.facts)
                .where(schema.facts.c.id == found[0])
                .values(forgotten_at=None, forgotten_in=None)
            )
            # The scope may have refilled while the fact was held, so a
            # restore re-prunes like every other mutation rather than
            # failing or leaving the scope over its cap. The restored row
            # is protected: a prune that took it would answer success and
            # change nothing.
            _prune(connection, scope, owner, protecting=int(found[0]))
            return str(found[1])

        return self._written(agent, scope, work)


# What every id-addressed operation adds to its WHERE clause, so the
# reach is the row's ownership rather than the model's good behavior.
_ACTIVE = schema.facts.c.forgotten_at.is_(None)


def _addressed(scope: MemoryScope, owner: str, fact_id: int) -> tuple[object, ...]:
    """One fact, addressed the way every operation that names an id
    addresses it: the id AND the pair that owns it.

    Written once because it is one rule, and stating it three times is
    how one of the three comes to be missing it.
    """
    return (
        schema.facts.c.id == fact_id,
        schema.facts.c.scope == scope,
        schema.facts.c.owner == owner,
    )


def _caps(scope: MemoryScope) -> tuple[int, int]:
    """How many lines and how many bytes one scope keeps.

    Read at call time rather than bound anywhere, which is what the cap
    suites' monkeypatch reaches. Per scope because the pressure is: an
    agent accumulates a person's whole history, a device accumulates the
    few notes a place has, and one pair of numbers over both would either
    starve the first or let the second grow into the prompt.
    """
    if scope is MemoryScope.DEVICE:
        return DEVICE_LINES, DEVICE_BYTES
    return MAX_LINES, MAX_BYTES


def _one_line(text: str) -> str:
    """What a fact or a value is stored as: one line, whatever it
    arrived as. The rendering is per line, so a value carrying a newline
    would be two entries in every block that shows it."""
    return " ".join(text.split())


def _refuse_the_oversized(text: str, scope: MemoryScope) -> None:
    """Refuse one item whose rendered line alone will not fit inside its
    scope's byte cap.

    Before any connection, because it needs none, and separately from
    the prune, because the prune cannot answer it: dropping every other
    fact would still leave this one over the cap. The alternative is a
    scope that is silently over its bound for as long as the fact lives.
    """
    if len(f"- {text}".encode()) > _caps(scope)[1]:
        raise ValueError(TOO_LONG)


def _active(
    connection: Connection, scope: MemoryScope, owner: str
) -> list[tuple[int, str]]:
    """One owner's active facts within one scope, oldest first, each
    already a rendered line.

    `ORDER BY id` is the file's line order, and the index the schema
    declares is on exactly the three columns this walks. Rendered here
    rather than at the call sites because the byte cap is applied to the
    rendering, so the line is what both the reader and the prune have to
    be counting.

    Held facts are not active facts. A forgotten one is out of every
    read, out of the prune's arithmetic and out of the caps until it is
    restored, which is what makes the undo a promise rather than a race
    against the next write.
    """
    rows = connection.execute(
        select(schema.facts.c.id, schema.facts.c.fact)
        .where(
            schema.facts.c.scope == scope,
            schema.facts.c.owner == owner,
            schema.facts.c.forgotten_at.is_(None),
        )
        .order_by(schema.facts.c.id)
    ).all()
    return [(row_id, f"- {fact}") for row_id, fact in rows]


def _prune(
    connection: Connection, scope: MemoryScope, owner: str, protecting: int
) -> None:
    """Bring one scope's active rows back inside its caps, inside the
    transaction that took it past them.

    Every mutating transaction ends here, which is the whole of the cap
    invariant: an add, an update that grew a fact and a restore into a
    scope that refilled meanwhile all succeed and re-prune rather than
    failing or leaving the scope over its bound.

    `protecting` is the row this transaction just wrote, and it is never
    pruned. A restore whose row is the oldest, or an update of the oldest
    fact, would otherwise be undone by the prune in the same transaction
    that made it, which is a mutation that answers success and changes
    nothing.
    """
    doomed = _over_the_cap(_active(connection, scope, owner), scope, protecting)
    if doomed:
        connection.execute(delete(schema.facts).where(schema.facts.c.id.in_(doomed)))


def _over_the_cap(
    stored: Sequence[tuple[int, str]], scope: MemoryScope, protecting: int
) -> list[int]:
    """Which rows no longer fit, by id. The oldest go first: a fact
    worth keeping tends to get said again, and the alternative (refusing
    to remember anything more) is worse to hear.

    The same algorithm the file store applied to lines, on constants read
    at call time: drop the oldest until the scope is inside its line cap
    and its rendered block is inside its byte cap, never below one fact,
    and never the row the transaction just wrote.
    """
    lines, limit = _caps(scope)
    kept = list(stored)
    while len(kept) > lines or (
        len(kept) > 1 and len(_rendered(kept).encode("utf-8")) > limit
    ):
        oldest = next(
            (index for index, (row_id, _) in enumerate(kept) if row_id != protecting),
            None,
        )
        if oldest is None:
            break
        kept.pop(oldest)
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
    "DEVICE_BYTES",
    "DEVICE_LINES",
    "MAX_BYTES",
    "MAX_LINES",
    "MEMORY_CHAIN",
    "NOTHING_TO_REMEMBER",
    "NO_FACT_TO_FORGET",
    "NO_FACT_TO_RESTORE",
    "NO_FACT_TO_UPDATE",
    "QUIET_TIMEOUT_S",
    "TOO_LONG",
    "UNWRITABLE",
    "MemoryScope",
    "MemoryStore",
    "StoreClosed",
    "open_memory",
]
