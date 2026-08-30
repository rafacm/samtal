"""The store behind what an agent remembers: one chain, two engines.

What this module hides from its callers is the whole of the storage: a
schema of its own and the migrations that build it, an advisory lock
that serializes writers across processes, a write engine that takes
that lock before it reads anything, a separate read-only engine so a
reply's memory read never waits on a writer, and a connection whose
failures are classified rather than quoted. A caller asks for a store
and disposes it; it never learns which schema, which lock key, which
isolation level, or what a psycopg failure looks like. It never learns
what a scope is stored as either: it names one, and the store knows
which table, which cap and which index that means.

Three memories, addressed as a scope and an owner. An agent's own,
keyed by its configured name, because an agent is one entity across
rooms: "remember I am vegetarian", said in the kitchen, holds in the
bedroom. A device's, keyed by its MAC and shared by every agent bound
to it, which is where the place and the household belong. And a
conversation's, keyed by the thread's uuid hex, a ledger of what is
currently true that shares its thread's lifecycle whole. Telling people
apart on a shared device is still the voiceprint problem, and none of
these three solves it.

What a caller gets, in the order the sentences are met:

- `read_for_prompt(agent, device, conversation)`, every scope a prompt
  is assembled from, rendered in one round trip. The agent's block is
  the newest of its facts rather than the whole of them, and a caller
  with no device or no thread behind it says so and gets the scopes it
  asked for.
- `add`, `update`, `forget` and `restore`, addressed by the id `add`
  answers with and bounded by the ownership of the row rather than by
  the model's good behavior. One door onto keeping a fact, whichever
  scope it belongs to, so the rules a fact is held to cannot differ by
  which sentence a caller happened to speak. `restore` is addressed by
  every memory the caller may reach rather than by one of them, because
  with no id it is a choice among rows and which memory holds the newest
  of them is its answer to give.
- `recall(agent, device, query)`, a bounded lookup over every active
  fact those two scopes hold, which is how the model reaches what the
  prompt did not inject and how it learns the number of a fact it wants
  to correct.
- `set_state` and `clear_state`, the conversation's ledger.
- `purge_threads` and `sweep`, which take the memory of threads that
  are gone, and `purge`, the same deletes on a transaction somebody
  else owns, which is how a thread's erasure takes its memory in the
  commit that takes its turns.

A conversation's memory shares its thread's lifecycle, so this store
keeps the other end of the conversation record's erasure-order protocol:
every thread-keyed write is ordered against the deletion that may be
taking that thread, and `threads_erased` is what a deletion tells it.
What a caller sees is one more fixed refusal.

Injection is still the standard shape for what fits: it costs no lookup
latency (a lookup round trip is spoken silence) and does not depend on
a small local model choosing to call it. The caps are what keep that
true, and the two-tier shape the file store's docstring predicted is
what the agent scope became once it outgrew the prompt: a small
injected core, and the rest reachable by asking.

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
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from sqlalchemy import Connection, Engine, and_, delete, func, or_, select, union
from sqlalchemy import update as sql_update

from vinga_server.config.loader import ConfigError, DatabaseBusyError, StorageError
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations.schema import conversations
from vinga_server.conversations.store import erasure_order
from vinga_server.db import (
    LOCK_TIMEOUT_MS,
    StoreChain,
    advisory_key,
    is_busy,
    open_at,
    read_engine,
    take_the_chain_lock,
)
from vinga_server.events import ServerEvents
from vinga_server.events.catalog import (
    MemoryCleanupFailed,
    MemoryUnreadable,
    MemoryUnwritable,
)
from vinga_server.events.values import ClassName, Identifier
from vinga_server.memory import schema
from vinga_server.memory.schema import FACT_SCOPES, MemoryScope

events = ServerEvents(__name__)

# What one guarded call answers with, whatever that is: a rendering, an
# id, a removed fact. The two seams below are the same shape over
# different answers, and a type variable is what keeps them one seam
# rather than one per return type.
T = TypeVar("T")

# What an agent's scope holds: whichever trips first wins, and an insert
# that overflows drops the oldest facts.
#
# Sized for a memory rather than for a prompt, which is what the two
# tiers below buy. The file store kept two hundred lines in eight
# kilobytes because every one of them was injected into every reply, so
# the storage cap was a context budget wearing another name; now the
# block is the core and the rest is reached by looking it up, so what
# these two bound is how much an agent may accumulate before the oldest
# of it starts falling off. A thousand facts is years of an ordinary
# conversation.
#
# Module-level and read at call time, which is what the two cap suites
# monkeypatch.
MAX_BYTES = 65536
MAX_LINES = 1000

# What a device keeps, which is smaller because a device accumulates the
# few notes a place has rather than a person's whole history, and
# because the whole of it is injected rather than searched.
DEVICE_BYTES = 2048
DEVICE_LINES = 30

# What an agent's block in the prompt holds: the newest forty lines
# inside four kilobytes, which is a small fraction of what the scope
# stores. Newest first because a fact worth keeping tends to get said
# again, and the rest is reachable through the lookup, which searches
# the core too.
CORE_BYTES = 4096
CORE_LINES = 40

# What one conversation's ledger keeps. Fifty entries inside four
# kilobytes, and a write that would leave it past either is refused
# rather than silently trimmed: a ledger that drops keys is a ledger the
# model cannot trust, and one that grows without a bound is not a
# ledger at all.
STATE_BYTES = 4096
STATE_KEYS = 50

# How old an orphan has to be before the sweep takes it.
#
# A grace period rather than none, because state can precede its
# thread's row: a thread materializes at its first turn, so a ledger
# written before that turn lands has no thread to be found by and is not
# an orphan yet. A day is long enough that no live conversation is ever
# inside it and short enough that a deployment which never records
# threads at all stays bounded.
SWEEP_GRACE = dt.timedelta(days=1)

# What a lookup answers with. Bounded because a match set is unbounded
# by nature and a tool result is read by a model with a context window:
# twenty lines inside two kilobytes, and a fixed sentence saying so
# where more matched than fit.
RECALL_BYTES = 2048
RECALL_LINES = 20

# What a write that the database refused answers with, as fixed
# sentences carrying no value at all.
#
# These are the one class of failure in this module a model reads out
# loud: a raise from `add` becomes the tool result the reply is
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

# And what a cleanup that could not delete answers its caller with.
# Separate sentences from the two above because they are read somewhere
# else: nothing a model says is built from these, and what asks for a
# purge is a thread's erasure or its retention prune, whose caller is an
# operator. Value-free for one reason more than the others: what a purge
# binds into its statements is thread ids, and an erasure that quoted
# them back would be repeating the identifiers it exists to remove.
PURGE_FAILED = (
    "the memory of those conversations could not be removed: the database this "
    "server keeps memory in refused the delete, and nothing was changed. Nothing of "
    "the failure is repeated here, because a database error quotes the statement it "
    "ran and the values bound into it"
)

PURGE_BUSY = (
    "the memory of those conversations could not be removed: another connection was "
    "writing to memory for longer than the lock timeout allows, and nothing was "
    "changed. The same request may simply be made again"
)

# What a fact operation named with a scope no fact can carry answers.
#
# Decided here rather than left to the check constraint, which would
# also refuse it: a constraint violation arrives as a database failure,
# and this module would report a healthy database as broken and tell a
# model its fact could not be stored. What went wrong is that the call
# was wrong, and this is the sentence that says so.
NOT_A_FACT_SCOPE = (
    "a fact belongs either to the agent or to the device. What is currently true in "
    "one conversation is kept as that conversation's state instead, which is a "
    "different thing to write and a different thing to read back. Nothing was changed"
)

NOTHING_TO_LOOK_FOR = "there is nothing to look for"

# What a write addressed to a thread that has been deleted answers with.
#
# Not a failure, and not something to try again: the conversation this
# note would belong to has been erased, and writing it would be the
# erasure undone. The agent goes on talking; what stops is the record of
# it, which is what erasure means.
CONVERSATION_ERASED = (
    "this conversation is no longer stored, so nothing can be kept about it. Nothing "
    "was changed, and nothing will be; carry on talking, and remember anything worth "
    "keeping as a fact instead"
)

NOTHING_TO_SET = "there is nothing to write down: a note needs a name and something to say"

# The three a ledger write is refused with, each naming the bound it
# would have crossed. Refusals rather than a silent trim, and separate
# sentences rather than one, because what a model should do next differs:
# clear something, say something shorter, or say this one thing shorter.
STATE_FULL = (
    "this conversation is already keeping as many notes as it can hold. Nothing was "
    "written down; clear one that no longer matters and write this one again"
)

STATE_TOO_MUCH = (
    "this conversation cannot hold any more than it already does. Nothing was written "
    "down; clear a note that no longer matters, or say this one in fewer words"
)

STATE_ENTRY_TOO_LONG = (
    "this note is too long to keep: one note has to fit inside the space this "
    "conversation keeps for all of them. Nothing was written down; say it in fewer "
    "words"
)

# What a bounded lookup ends with when it left something out. Fixed and
# value-free like every other sentence here: it says that more matched
# and what to do about it, and quotes neither the query nor a count,
# because a count of somebody's private facts is itself something to
# say out loud.
MORE_MATCHED = (
    "More was remembered than fits here. Ask for something narrower to see the rest"
)

# What a match too long to be answered whole ends with. Three dots and
# nothing more: the marker has to be shorter than anything it could be
# asked to stand in for, and it says the one thing a reader needs, which
# is that there was more of this line.
ELLIPSIS = "..."


@dataclass(frozen=True)
class Purged:
    """How much of a thread's memory went with it.

    Counted rather than assumed, because both callers answer with these
    numbers: an erasure through the operator API reports what it took,
    and the boot sweep reports what it healed.
    """

    state: int
    held_facts: int


# What a purge that had nothing to take, or could not take it, answers.
NOTHING_PURGED = Purged(state=0, held_facts=0)


@dataclass(frozen=True)
class PromptMemory:
    """What one reply's prompt is assembled from, all three scopes at
    once and each already rendered.

    A value rather than three calls, because the reply path pays for one
    off-loop hop per round and three would cost three; and rendered
    rather than raw, because the shapes behind these blocks are this
    module's business and the assembly's business is the text.

    Fields in the order the blocks are meant to be read, which is also
    their precedence: what this conversation is currently doing, what
    the agent knows, what the place knows. Each is the empty string
    where the scope holds nothing, and where it could not be read.
    """

    state: str
    agent: str
    device: str


# What a prompt read answers when nothing could be read at all. A named
# constant rather than three empty strings at the call site: a reply
# assembled over an unreadable database has to be the reply assembled
# over an empty one.
NOTHING_REMEMBERED = PromptMemory(state="", agent="", device="")


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

    Not a sentence anybody reads. It travels as a class name on the
    events this module emits, and every seam contains it exactly as it
    contains a database that is not there: a read answers with no memory
    this round, a write refuses with `UNWRITABLE`, and a cleanup takes
    nothing. A shutdown is not a failure a model should be given
    different words for.
    """

# How long a close waits for the calls already inside a connection.
#
# Derived from the lock timeout rather than picked, because that is what
# actually bounds a call: an `add` parked on the chain's advisory
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
    # space. Distinct from the other two on purpose: remembering is a
    # tool call a human asked for mid-conversation, and sharing the
    # domain chain's key would make it wait behind an `apply`
    # transaction that is deliberately unbounded, while sharing the
    # record's would make it wait behind a turn being written.
    lock_key=advisory_key(3),
)


class MemoryStore:
    """One database's worth of memory: three scopes and one ledger.

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
        # the lock they agree about them under. Every call runs in a
        # worker thread while the close runs on the loop, so every one of
        # them is touched from two threads: how many calls are inside a
        # connection, whether this store is closing, and whether the
        # pools have already been let go.
        self._quiet = threading.Condition()
        self._in_flight = 0
        self._closing = False
        self._disposed = False
        # The threads a deletion in this process has said are gone, and
        # the lock the two sides agree about them under: a deletion
        # publishes from a request thread or the writer's, and every
        # thread-keyed write reads from a worker thread of its own.
        #
        # It grows with the threads one process erases, which is what
        # bounds it: a deleted conversation is something somebody asked
        # for, not a row this store loops over.
        self._dead: set[str] = set()
        self._graves = threading.Lock()

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

    def threads_erased(self, threads: frozenset[str]) -> None:
        """These threads are gone; write nothing more about them.

        The memory store's half of the erasure-order protocol, called by
        the `erased()` fan-out the conversation record publishes through
        and by nothing else. It does the least it can, for the reason the
        writer's own `forget` does: it records what was said, and the
        decision is taken by whoever is about to write.
        """
        with self._graves:
            self._dead |= threads

    def _thread_keyed(self, conversation: str, write: Callable[[], T]) -> T:
        """One write about one thread, ordered against the deletion that
        may be taking that thread.

        The other side of what the conversation writer already keeps.
        `erasure_order()` is held across the whole write, and it is taken
        OUTSIDE the chain's advisory lock, never inside: a deletion holds
        it across its transaction and the publication that follows, so
        whichever of the two went first, the second sees all of it. A
        write that began before the deletion completes before it, and its
        rows are then deleted by the deletion's own transaction; one that
        begins after meets the dead set here and is refused.

        There is no third interleaving, which is the whole point of
        holding the lock rather than checking the set and hoping: the
        instant between a deletion's commit and its publication is
        exactly where a write could otherwise land and recreate a row for
        a thread that is gone.

        What it costs is that a thread-keyed memory write and a turn
        being recorded serialize on one process-level lock. Both are
        conversational-pace writes that already serialize on a chain lock
        in the database, so what is added is the wait for whichever of
        the two got there first.
        """
        with erasure_order():
            with self._graves:
                gone = conversation in self._dead
            if gone:
                raise ValueError(CONVERSATION_ERASED)
            return write()

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
        self,
        agent: str,
        scopes: Sequence[MemoryScope],
        work: Callable[[Connection], T],
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

        `scopes` and not one scope, for the reason `_read` takes a
        sequence: one write can reach into more than one of them, and a
        restore with no number reaches into every memory this caller can
        address to find the newest thing it lost. What a failure costs is
        all of them, so all of them are said, and a report naming one
        would be a guess about which row the statement never found.
        """
        problem: Exception
        try:
            with self._connected(), self._engine.begin() as connection:
                return work(connection)
        except _Refused as refusal:
            problem = ValueError(str(refusal))
        except Exception as exc:  # noqa: BLE001 - classified, never quoted
            failure = exc
            for scope in scopes:
                events.emit(
                    lambda scope=scope: MemoryUnwritable(  # type: ignore[misc]
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
        _only_a_fact_scope(scope)
        text = _one_line(fact)
        if not text:
            raise ValueError(NOTHING_TO_REMEMBER)
        if _oversized(text, scope):
            raise ValueError(TOO_LONG)
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

        return self._written(agent, (scope,), work)

    def purge_threads(self, threads: Sequence[str]) -> Purged:
        """The same purge in a transaction of this store's own, for a
        caller that holds none.

        What a deployment with no conversation store needs at session
        close: no thread rows land, so no erasure and no retention will
        ever come for these, and a closed session's threads can never be
        resumed. Contained like every other lifecycle cleanup, because
        there is nobody to refuse: the session is going away either way.
        """
        return self._cleaned(lambda connection: purge(connection, threads))

    def sweep(self, grace: dt.timedelta = SWEEP_GRACE) -> Purged:
        """Take the state and the held facts of threads that have no
        row in the conversation record and have not been written to for
        a while.

        Narrowed to what no transaction covers, which is what makes it a
        heal rather than a policy: pre-upgrade leftovers, threads that
        never landed a first turn, and deployments where no thread rows
        land at all. Everything a thread erasure or a retention prune
        reaches is taken inside those transactions instead.

        The anti-join reads `record.conversations`, which is another
        chain's table in the same database, and this module importing
        that schema is the honest statement that the deletion promise
        crosses schemas. It runs on the write engine because it deletes,
        so it holds the memory chain's lock and nobody else's.

        Contained, and reported as a cleanup rather than as a failed
        read or write: there is no acting agent and no scope to name,
        because what this answers for is every thread nobody owns.
        """
        cutoff = (_utc_now() - grace).isoformat()

        def work(connection: Connection) -> Purged:
            return Purged(
                state=int(
                    connection.execute(
                        delete(schema.state).where(
                            schema.state.c.updated_at < cutoff,
                            ~_recorded(schema.state.c.conversation),
                        )
                    ).rowcount
                ),
                held_facts=int(
                    connection.execute(
                        delete(schema.facts).where(
                            schema.facts.c.forgotten_in.is_not(None),
                            schema.facts.c.forgotten_at < cutoff,
                            ~_recorded(schema.facts.c.forgotten_in),
                        )
                    ).rowcount
                ),
            )

        return self._cleaned(work)

    def _cleaned(self, work: Callable[[Connection], Purged]) -> Purged:
        """One cleanup transaction, contained whole.

        Neither a read nor a write in the sense the other two seams
        mean: nobody asked for it, nobody is waiting for the answer, and
        there is no agent whose reply it belongs to. So a failure is not
        refused to anybody and not attributed to anybody; it is said
        once, by class, and the rows it did not take are taken by the
        next boot.
        """
        try:
            with self._connected(), self._engine.begin() as connection:
                return work(connection)
        except Exception as exc:  # noqa: BLE001 - the whole point of the seam
            failure = exc
            events.emit(lambda: MemoryCleanupFailed(error=ClassName.of(failure)))
            return NOTHING_PURGED

    def read_for_prompt(
        self, agent: str, device: str | None, conversation: str | None
    ) -> PromptMemory:
        """Everything a prompt needs to know of memory, in one round
        trip.

        One connection for every scope it reaches: the reply path takes
        exactly one off-loop hop per round, and a read per scope would
        take three. The blocks come back rendered and in reading order,
        and what the assembly does with them is its own business.

        Contained scope by scope in what it costs a reply: a database
        this server cannot read means the agent remembers nothing this
        round and the reply happens, and every scope that was reached
        for and lost says so, so an empty block is never silence about a
        failure.

        The agent's block is the core rather than the whole of the
        scope, and the whole of it is never read here: the scope holds a
        thousand facts and the block shows the newest forty of them, so
        what is not injected is what the lookup is for.

        A device that never identified itself has no device scope, and a
        prompt assembled outside any conversation has no ledger. Both
        are said with None rather than reached with an owner nothing
        matches: an empty string is a name a row could be stored under,
        and answering "no rows" by accident is not the same as saying
        there is nothing to read. The preview an operator asks for is
        exactly that shape, one scope of the three.
        """
        return self._read(
            agent,
            _reaching(device, conversation),
            lambda connection: PromptMemory(
                state=(
                    ""
                    if conversation is None
                    else _ledger_rendered(_ledger(connection, conversation))
                ),
                agent=_core(_newest(connection, MemoryScope.AGENT, agent, CORE_LINES)),
                device=(
                    ""
                    if device is None
                    else _rendered(_active(connection, MemoryScope.DEVICE, device))
                ),
            ),
            NOTHING_REMEMBERED,
        )

    def recall(self, agent: str, device: str, query: str) -> str:
        """Every active fact this agent can reach whose words contain
        the query, newest first, each with the id it is addressed by.

        Both scopes, because both are this agent's to reach on this
        device, and the whole of each of them rather than the part the
        prompt did not inject: the injected core shows no ids, so this
        is also how the model finds the number of a fact it needs to
        correct or forget, core facts included. A lookup that searched
        only what was not injected could not answer that at all.

        Case-insensitive substring and nothing cleverer. What a person
        says is not what a fact was stored as, and matching on the words
        themselves is a rule a model can predict; fuzzy matching was
        rejected where the ids were decided.

        The query is a substring even where it is punctuation: its
        wildcards are escaped, so a lookup for `%` finds the facts with
        a per cent sign in them rather than every fact there is.

        Bounded, and it says when it was: a match set is unbounded by
        nature, and a tool result that ran past the model's context
        would cost the reply it was meant to serve.
        """
        wanted = _one_line(query)
        if not wanted:
            raise ValueError(NOTHING_TO_LOOK_FOR)
        return self._read(
            agent,
            (MemoryScope.AGENT, MemoryScope.DEVICE),
            lambda connection: _bounded(_matching(connection, agent, device, wanted)),
            "",
        )

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
        _only_a_fact_scope(scope)
        corrected = _one_line(text)
        if not corrected:
            raise ValueError(NOTHING_TO_REMEMBER)
        if _oversized(corrected, scope):
            raise ValueError(TOO_LONG)
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

        self._written(agent, (scope,), work)

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
        _only_a_fact_scope(scope)
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

        if permanently:
            # Nothing thread-keyed happens: the row is erased rather than
            # held, so no conversation owns anything afterwards and the
            # erasure of one cannot be undone by this.
            return self._written(agent, (scope,), work)
        return self._thread_keyed(
            conversation, lambda: self._written(agent, (scope,), work)
        )

    async def set_state(
        self, conversation: str, key: str, value: str, *, agent: str
    ) -> None:
        """Write down what is currently true in this conversation, under
        a name the model chose.

        Upsert by key is the whole of the semantics: the key is the
        identity, so writing the same one again replaces what it held
        rather than adding a second entry. There is no undo, and there is
        nothing to undo: a ledger of what is currently true is not a
        record of the user's words, which is what the soft forgetting of
        facts exists to protect.

        A write that would leave the ledger past either of its bounds is
        refused, and that is the whole reason the bounds are stated here
        rather than enforced by a trim. Dropping a key to make room would
        make every entry a guess about whether it is still there, and
        growing past the byte bound would put an unbounded ledger into
        every round's prompt.

        Whether the ledger is over its bound is decided inside the
        transaction, under the chain's lock, so the count this write is
        held against is the count the write lands on.
        """
        named = _one_line(key)
        said = _one_line(value)
        if not named or not said:
            raise ValueError(NOTHING_TO_SET)
        if len(_entry(named, said).encode("utf-8")) > STATE_BYTES:
            raise ValueError(STATE_ENTRY_TOO_LONG)
        await asyncio.to_thread(self._set_state, agent, conversation, named, said)

    def _set_state(
        self, agent: str, conversation: str, key: str, value: str
    ) -> None:
        def work(connection: Connection) -> None:
            held = dict(_ledger(connection, conversation))
            if key not in held and len(held) + 1 > STATE_KEYS:
                raise _Refused(STATE_FULL)
            after = dict(held)
            after[key] = value
            if len(_ledger_rendered(sorted(after.items())).encode("utf-8")) > STATE_BYTES:
                raise _Refused(STATE_TOO_MUCH)
            now = _utc_now().isoformat()
            # Decided here rather than with an upsert, because the chain's
            # advisory lock has already made this transaction the only
            # writer: what the read above saw is what the write below
            # lands on.
            if key in held:
                connection.execute(
                    sql_update(schema.state)
                    .where(
                        schema.state.c.conversation == conversation,
                        schema.state.c.key == key,
                    )
                    .values(value=value, updated_at=now)
                )
            else:
                connection.execute(
                    schema.state.insert().values(
                        conversation=conversation,
                        key=key,
                        value=value,
                        updated_at=now,
                    )
                )

        self._thread_keyed(
            conversation,
            lambda: self._written(agent, (MemoryScope.CONVERSATION,), work),
        )

    async def clear_state(
        self, conversation: str, key: str | None = None, *, agent: str
    ) -> int:
        """Forget one thing this conversation was keeping, or the whole
        ledger, and answer how many entries went.

        No refusal for a key that is not there, and no held area either.
        Clearing what is already clear is what the caller asked for, and
        the count is what says whether anything was.
        """
        return await asyncio.to_thread(self._clear_state, agent, conversation, key)

    def _clear_state(self, agent: str, conversation: str, key: str | None) -> int:
        # The same statement the operator's door issues, which is the
        # whole of what clearing a ledger is: what the two doors differ
        # in is the guard around it, not the SQL. A second copy here
        # would be a second place for the key's normalization to change.
        return self._thread_keyed(
            conversation,
            lambda: self._written(
                agent,
                (MemoryScope.CONVERSATION,),
                lambda connection: clear_ledger(connection, conversation, key),
            ),
        )

    async def restore(
        self,
        owners: Sequence[tuple[MemoryScope, str]],
        conversation: str,
        fact_id: int | None = None,
        *,
        agent: str,
    ) -> str:
        """Bring back a fact this conversation forgot, and answer it.

        Addressed by every memory the caller may reach rather than by one
        of them, because with no id this is a choice among rows: the last
        thing forgotten in this conversation, which is the shape a person
        actually asks for, and which memory that fact was in is the
        answer rather than the question. Deciding it one memory at a time
        would answer with the newest thing the first memory lost, which
        on a conversation that forgot one fact and one note is the older
        of the two.

        With an id, that fact, and the conversation is still part of the
        address: a fact forgotten somewhere else is not this
        conversation's to bring back, which makes the id door mean what
        the no-id door means. It is bounded by the same owners, so a
        number belonging to a memory this caller cannot reach is answered
        exactly as a number belonging to nobody.

        One statement over both memories inside one transaction, under
        the chain's lock, which is what makes "the last thing" true of
        the moment it lands rather than of a read that raced a write.

        The restored fact comes back exactly as it was said. Its id is
        the one it always had, and its place in the reading order is the
        one it always had, because the held area kept the row rather than
        a copy of its text.
        """
        for scope, _ in owners:
            _only_a_fact_scope(scope)
        return await asyncio.to_thread(
            self._restore, agent, owners, conversation, fact_id
        )

    def _restore(
        self,
        agent: str,
        owners: Sequence[tuple[MemoryScope, str]],
        conversation: str,
        fact_id: int | None,
    ) -> str:
        def work(connection: Connection) -> str:
            held = select(
                schema.facts.c.id,
                schema.facts.c.fact,
                schema.facts.c.scope,
                schema.facts.c.owner,
            ).where(
                _owned_by(owners),
                schema.facts.c.forgotten_in == conversation,
            )
            if fact_id is None:
                # Newest by when it was forgotten, and the id breaks a
                # tie: two rows can carry one timestamp, and an order
                # that stopped there would be the database's to pick.
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
            # failing or leaving the scope over its cap. It prunes the
            # memory the row is actually in, which is the row's own pair
            # and not the caller's first guess, and the restored row is
            # protected: a prune that took it would answer success and
            # change nothing.
            _prune(connection, found[2], found[3], protecting=int(found[0]))
            return str(found[1])

        return self._thread_keyed(
            conversation,
            lambda: self._written(agent, [scope for scope, _ in owners], work),
        )


def purge(connection: Connection, threads: Sequence[str]) -> Purged:
    """Take the memory of threads that are gone, on a connection the
    caller already holds.

    The seam the cross-store deletion is made of: this module owns the
    SQL and the caller owns the transaction, so a thread's erasure and
    its memory leave in the same commit and there is never a moment when
    the thread is gone while its state remains. A failure therefore has
    to reach the caller and take its transaction down with it, because a
    purge that swallowed one would let an erasure answer with counts a
    rollback made false.

    What reaches the caller is a refusal of this module's own, never the
    driver's. A SQLAlchemy failure carries the statement it ran and the
    parameters bound into it, and the parameters here are thread ids: a
    caller that rendered one into a problem body or a log line would be
    quoting the very identifiers an erasure exists to remove. So the
    refusal is built inside the handler and raised after it, cause
    severed, and the caller's transaction rolls back exactly as it would
    have.

    A function rather than a method, because neither caller has a store.
    The conversation record's writer is handed this at construction
    (importing it there would be a cycle, since this module reads the
    record's own table), and a deletion through the operator API runs on
    a connection it opened for itself, precisely so that erasure works
    in a deployment with recording off. What the SQL needs is a
    connection, and a store would be a parameter neither of them has to
    give.

    The memory chain's advisory lock is taken before the first statement
    rather than left to the caller's engine, which is what makes the
    ascending order `db.advisory_key` states a property of this
    function: a caller on the record chain's write engine is already
    holding key 2 when it arrives here, and this takes key 3 second. A
    caller already on the memory chain's own engine is taking a lock it
    holds, which costs nothing. It is taken inside the boundary below
    like every other statement here, because a lock that does not arrive
    inside the timeout is exactly the contended case the retryable
    sentence is for.

    A thread's memory is its ledger and the facts it forgot. Active
    facts are nobody's thread: they belong to the agent or the device
    and outlive every conversation.
    """
    if not threads:
        return NOTHING_PURGED
    problem: Exception
    try:
        take_the_chain_lock(connection, MEMORY_CHAIN)
        return Purged(
            state=int(
                connection.execute(
                    delete(schema.state).where(schema.state.c.conversation.in_(threads))
                ).rowcount
            ),
            held_facts=int(
                connection.execute(
                    delete(schema.facts).where(schema.facts.c.forgotten_in.in_(threads))
                ).rowcount
            ),
        )
    except Exception as exc:  # noqa: BLE001 - classified, never quoted
        # By class and never by message, through the one classifier `db`
        # owns, the same pair every other write here answers with: a
        # contended delete is retryable and says so.
        problem = (
            DatabaseBusyError(PURGE_BUSY) if is_busy(exc) else StorageError(PURGE_FAILED)
        )
    raise problem


# The operator's door
#
# The same rows, asked a different question. What an agent reaches is
# its own memory through a tool; what an operator reaches is every
# memory this deployment holds, to see what has accrued and to correct
# or remove it. Both are this module's SQL, for the reason `purge` is:
# the tables, the caps, the held area and the index are what this module
# exists to keep from its callers, and a route that wrote its own
# statements would learn all four.
#
# Functions on a caller's connection rather than methods on the store,
# again for `purge`'s reason: the routes open a connection for the
# length of one request through `db`, so a deletion works in a
# deployment with recording off and nothing holds an engine between
# requests. What the SQL needs is a connection, and a store would be a
# parameter no route has to give.
#
# Two rules run through the block, and both are the operator door being
# a different door rather than a second copy of the tools':
#
# - **Every deletion here is a hard delete.** The held area is the
#   spoken undo, which belongs to the conversation that forgot the fact;
#   an operator removing a fact is removing it, and a row held for an
#   undo nobody is in a position to speak would be the correction not
#   taken.
# - **A correction is held to the same cap invariant a tool's is.** It
#   is refused where its own line alone will not fit, and the scope is
#   re-pruned inside the same transaction, so no door can leave a scope
#   over its bound.


def owners(
    connection: Connection, scope: MemoryScope, after: str | None, limit: int
) -> list[dict[str, object]]:
    """Who holds facts in one scope, by name, with how many rows each of
    them holds.

    Ordered by the owner and paged on it, because there is no other
    total order over a set of names and a listing that could not be
    walked would be a listing that eventually cannot be served.

    Orphans are answered like anything else: what makes a row an
    agent's is the name it was stored under, and an agent that has been
    renamed leaves rows nothing configured points at. Hiding them would
    hide the reason an operator opened this listing.
    """
    query = (
        select(schema.facts.c.owner, func.count().label("facts"))
        .where(schema.facts.c.scope == scope)
        .group_by(schema.facts.c.owner)
        .order_by(schema.facts.c.owner)
        .limit(limit)
    )
    if after is not None:
        query = query.where(schema.facts.c.owner > after)
    return [dict(row) for row in connection.execute(query).mappings()]


def conversations_holding_memory(
    connection: Connection, after: str | None, limit: int
) -> list[dict[str, object]]:
    """Which threads hold memory, with how much of each kind.

    A thread holds two things and they live in different tables: the
    ledger it is currently keeping, and the facts it forgot and could
    bring back. So the owner set is the union of the two, which is what
    makes a thread with one and not the other a row rather than an
    omission, and the counts beside it are read per thread.

    A thread the conversation record no longer has is answered here too,
    for the reason an orphaned agent is: that is precisely what an
    operator is looking at this listing to find.
    """
    holders = union(
        select(schema.state.c.conversation.label("conversation")),
        select(schema.facts.c.forgotten_in.label("conversation")).where(
            schema.facts.c.forgotten_in.is_not(None)
        ),
    ).subquery()
    query = (
        select(
            holders.c.conversation,
            select(func.count())
            .select_from(schema.state)
            .where(schema.state.c.conversation == holders.c.conversation)
            .scalar_subquery()
            .label("state"),
            select(func.count())
            .select_from(schema.facts)
            .where(schema.facts.c.forgotten_in == holders.c.conversation)
            .scalar_subquery()
            .label("held_facts"),
        )
        .order_by(holders.c.conversation)
        .limit(limit)
    )
    if after is not None:
        query = query.where(holders.c.conversation > after)
    return [dict(row) for row in connection.execute(query).mappings()]


def facts_of(
    connection: Connection,
    scope: MemoryScope,
    owner: str,
    after: int | None,
    limit: int,
) -> list[dict[str, object]]:
    """One owner's facts in one scope, oldest first, held ones included.

    The reading order the injected block has, extended with what the
    block never shows: the id every correction and deletion is addressed
    by, when the row was last written, and the held pair, which is what
    marks a fact somebody forgot and could still bring back.

    Paged on the id, which is the order, so a walk recovers the whole
    scope once.
    """
    query = (
        select(
            schema.facts.c.id,
            schema.facts.c.fact,
            schema.facts.c.at,
            schema.facts.c.forgotten_at,
            schema.facts.c.forgotten_in,
        )
        .where(schema.facts.c.scope == scope, schema.facts.c.owner == owner)
        .order_by(schema.facts.c.id)
        .limit(limit)
    )
    if after is not None:
        query = query.where(schema.facts.c.id > after)
    return [dict(row) for row in connection.execute(query).mappings()]


def correct(
    connection: Connection,
    scope: MemoryScope,
    owner: str,
    fact_id: int,
    text: str,
) -> dict[str, object] | None:
    """Correct one fact in place, and answer the row as it now stands,
    or None where nothing this call may reach has that id.

    Addressed the way every id-addressed operation in this module is:
    the id AND the pair that owns it, so a fact of another agent or of
    another board is not reachable by naming a number under this one.
    Held facts are not reachable either, exactly as they are not through
    the tool: a fact that was forgotten is waiting to be brought back as
    it was said, and editing it there would make the undo answer with
    something nobody said.

    None rather than a refusal, because which sentence a caller is
    answered with is the route's to choose and there is only one for
    both cases: a missing fact and an inaccessible one are told apart by
    nobody.

    The correction can grow a fact past what its scope holds, so this
    ends where every mutation in this module ends, at the prune, with
    the row it just wrote protected from it.
    """
    corrected = _one_line(text)
    if not corrected:
        raise ConfigError(NOTHING_TO_REMEMBER)
    if _oversized(corrected, scope):
        raise ConfigError(TOO_LONG)
    found = connection.execute(
        sql_update(schema.facts)
        .where(*_addressed(scope, owner, fact_id), _ACTIVE)
        .values(fact=corrected, at=_utc_now().isoformat())
        .returning(
            schema.facts.c.id,
            schema.facts.c.fact,
            schema.facts.c.at,
            schema.facts.c.forgotten_at,
            schema.facts.c.forgotten_in,
        )
    ).mappings().first()
    if found is None:
        return None
    _prune(connection, scope, owner, protecting=fact_id)
    return dict(found)


def erase_fact(
    connection: Connection, scope: MemoryScope, owner: str, fact_id: int
) -> int:
    """Erase one fact, held or active, and answer how many rows went,
    which is one or none.

    The held ones are reachable here and are not through the tool, which
    is the difference between the two doors rather than an inconsistency:
    a held fact is a stored fact, it is in the listing above, and an
    operator who can see it must be able to remove it.
    """
    return int(
        connection.execute(
            delete(schema.facts).where(*_addressed(scope, owner, fact_id))
        ).rowcount
    )


def erase_facts(connection: Connection, scope: MemoryScope, owner: str) -> int:
    """Erase everything one owner holds in one scope, and answer how
    many rows went.

    Not addressed at a row, so nothing is refused for being absent: an
    owner with no rows is erased of nothing and the count says so, which
    is the same contract the selector purge on the conversation record
    keeps.
    """
    return int(
        connection.execute(
            delete(schema.facts).where(
                schema.facts.c.scope == scope, schema.facts.c.owner == owner
            )
        ).rowcount
    )


def ledger_of(connection: Connection, conversation: str) -> list[dict[str, object]]:
    """One conversation's ledger, by key, with the moment each entry was
    last written.

    Whole rather than paged, and that is the ledger's own property: a
    write past `STATE_KEYS` or `STATE_BYTES` is refused, so the whole of
    one is bounded by construction.
    """
    return [
        dict(row)
        for row in connection.execute(
            select(
                schema.state.c.key,
                schema.state.c.value,
                schema.state.c.updated_at,
            )
            .where(schema.state.c.conversation == conversation)
            .order_by(schema.state.c.key)
        ).mappings()
    ]


def clear_ledger(
    connection: Connection, conversation: str, key: str | None
) -> int:
    """Clear one entry of a conversation's ledger, or the whole of it,
    and answer how many entries went.

    A key names one entry and no key means all of them, which is the
    request's own difference rather than this function's: what arrives
    with no body is a caller asking for the ledger.
    """
    where = [schema.state.c.conversation == conversation]
    if key is not None:
        # Normalized the way the writer normalizes it, so the key an
        # operator reads out of the ledger above is the key that matches:
        # what is stored is one line, whatever it arrived as.
        where.append(schema.state.c.key == _one_line(key))
    return int(connection.execute(delete(schema.state).where(*where)).rowcount)


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


def _owned_by(owners: Sequence[tuple[MemoryScope, str]]) -> object:
    """One WHERE clause over every memory a caller may reach.

    The same bound `_addressed` states for one of them, said once for a
    set: what an operation reaches is a row whose `(scope, owner)` is one
    of the pairs the caller named, so a caller that named its own agent
    and its own device cannot touch anything else by guessing a number.
    """
    return or_(
        *(
            and_(schema.facts.c.scope == scope, schema.facts.c.owner == owner)
            for scope, owner in owners
        )
    )


def _only_a_fact_scope(scope: MemoryScope) -> None:
    """Refuse a scope no fact can carry, before a connection is reached.

    Every operation that names a scope asks this first. The check
    constraint on the table would refuse the row too, and that is the
    wrong place for it to be caught: a constraint violation arrives here
    as a database failure, so a caller's mistake would be reported as a
    memory that could not be written and an operator would be told a
    healthy database refused a write.

    Value-free like every refusal here, and it names the two members
    rather than the one that was passed: the members are this module's
    own vocabulary and what a caller passed is a caller's value.
    """
    if scope not in FACT_SCOPES:
        raise ValueError(NOT_A_FACT_SCOPE)


def _caps(scope: MemoryScope) -> tuple[int, int]:
    """How many lines and how many bytes one scope keeps.

    Read at call time rather than bound anywhere, which is what the cap
    suites' monkeypatch reaches. Per scope because the pressure is: an
    agent accumulates a person's whole history, a device accumulates the
    few notes a place has, and one pair of numbers over both would either
    starve the first or let the second grow into the prompt.

    Two scopes and not three: every caller that reaches here has passed
    `_only_a_fact_scope`, and a conversation's ledger is bounded by its
    own two constants rather than by these.
    """
    if scope is MemoryScope.DEVICE:
        return DEVICE_LINES, DEVICE_BYTES
    return MAX_LINES, MAX_BYTES


def _one_line(text: str) -> str:
    """What a fact or a value is stored as: one line, whatever it
    arrived as. The rendering is per line, so a value carrying a newline
    would be two entries in every block that shows it."""
    return " ".join(text.split())


def _oversized(text: str, scope: MemoryScope) -> bool:
    """Whether one item's rendered line alone will not fit inside its
    scope's byte cap.

    Asked before any connection, because it needs none, and separately
    from the prune, because the prune cannot answer it: dropping every
    other fact would still leave this one over the cap. The alternative
    is a scope that is silently over its bound for as long as the fact
    lives.

    A predicate rather than a refusal, because two doors ask it and they
    raise different things: a model's write leaves as the `ValueError`
    the tool layer rephrases, and an operator's correction leaves as the
    refusal this API answers a 422 with. The rule is one either way.
    """
    return len(f"- {text}".encode()) > _caps(scope)[1]


def _reaching(
    device: str | None, conversation: str | None
) -> tuple[MemoryScope, ...]:
    """Which scopes one prompt read is actually reading.

    What the containment reports when the read is lost, so it has to be
    what was reached for rather than the vocabulary: a preview with no
    device and no thread behind it reads one scope, and a report naming
    three would tell an operator that two scopes nobody asked about
    could not be read.
    """
    reached = [MemoryScope.AGENT]
    if conversation is not None:
        reached.insert(0, MemoryScope.CONVERSATION)
    if device is not None:
        reached.append(MemoryScope.DEVICE)
    return tuple(reached)


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


def _newest(
    connection: Connection, scope: MemoryScope, owner: str, lines: int
) -> list[tuple[int, str]]:
    """The newest `lines` active facts of one owner, in reading order.

    Bounded in the statement rather than sliced after it, which is the
    two-tier shape made real: the agent scope holds up to `MAX_LINES`
    facts and the block injects the newest few of them, so reading the
    whole scope to render forty lines would spend on every round exactly
    what the split exists to save.

    Newest by which rows are taken and oldest-first in how they read,
    which is not a contradiction: what falls out of the block is what
    was said longest ago, and what remains is read in the order it was
    said.
    """
    rows = connection.execute(
        select(schema.facts.c.id, schema.facts.c.fact)
        .where(
            schema.facts.c.scope == scope,
            schema.facts.c.owner == owner,
            _ACTIVE,
        )
        .order_by(schema.facts.c.id.desc())
        .limit(lines)
    ).all()
    return [(row_id, f"- {fact}") for row_id, fact in reversed(rows)]


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


def _recorded(column: object) -> object:
    """Whether a thread named by this column has a row in the
    conversation record.

    Read through the table another chain declares rather than through a
    name written here: what makes a thread real is the record's own row,
    and a copy of that table's name in this module would be a second
    place to fix when it moves.
    """
    return (
        select(conversations.c.id)
        .where(conversations.c.conversation == column)
        .exists()
    )


def _core(newest: Sequence[tuple[int, str]]) -> str:
    """The part of one agent's scope that is injected: of the newest
    lines, the ones that fit inside the block's byte cap.

    The line bound is the read's, since the statement asked for exactly
    `CORE_LINES` of them; what is left here is the byte bound, which
    cannot be asked of the database because it is counted on the
    rendering.

    It trims to empty where it has to, unlike the prune, and the
    difference is what each of the two protects. The prune never goes
    below one fact because dropping it would lose it; this drops
    nothing, and a fact longer than the whole block is still stored,
    still looked up and still corrected by its id. Keeping it here
    instead would put a block over its cap into every round's prompt,
    which is the one thing the cap exists to prevent.
    """
    kept = list(newest)
    while kept and len(_rendered(kept).encode("utf-8")) > CORE_BYTES:
        kept.pop(0)
    return _rendered(kept)


def _ledger(connection: Connection, conversation: str) -> list[tuple[str, str]]:
    """One conversation's ledger, by key.

    Ordered by the key rather than by when it was written, because a
    ledger is a set of current truths rather than a history: the reading
    order a model meets should not move because one entry was touched.
    """
    return [
        (key, value)
        for key, value in connection.execute(
            select(schema.state.c.key, schema.state.c.value)
            .where(schema.state.c.conversation == conversation)
            .order_by(schema.state.c.key)
        ).all()
    ]


def _entry(key: str, value: str) -> str:
    """One ledger line, which is what both the rendering and the byte
    bound are counted in."""
    return f"- {key}: {value}"


def _ledger_rendered(held: Sequence[tuple[str, str]]) -> str:
    return "\n".join(_entry(key, value) for key, value in held)


def _matching(
    connection: Connection, agent: str, device: str, wanted: str
) -> list[tuple[int, str]]:
    """The active facts of this agent and this device whose words
    contain `wanted`, newest first, each rendered with its id.

    Newest first because a lookup answers a question asked now, and
    because the bound below cuts from the far end: what is dropped
    should be the oldest thing that matched rather than the most recent.
    """
    rows = connection.execute(
        select(schema.facts.c.id, schema.facts.c.fact)
        .where(
            or_(
                and_(
                    schema.facts.c.scope == MemoryScope.AGENT,
                    schema.facts.c.owner == agent,
                ),
                and_(
                    schema.facts.c.scope == MemoryScope.DEVICE,
                    schema.facts.c.owner == device,
                ),
            ),
            _ACTIVE,
            schema.facts.c.fact.ilike(_containing(wanted), escape="\\"),
        )
        .order_by(schema.facts.c.id.desc())
    ).all()
    return [(row_id, f"- [{row_id}] {fact}") for row_id, fact in rows]


def _containing(wanted: str) -> str:
    """One substring as a LIKE pattern, with the pattern language
    escaped out of the caller's words.

    Without this a query of `%` matches every fact an agent has, and one
    of `_` matches every single-character one: the model chose those
    characters as text, and a search that read them as syntax would
    answer a question nobody asked.
    """
    escaped = wanted.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _bounded(matches: Sequence[tuple[int, str]]) -> str:
    """As many matches as the bound allows, and a fixed sentence when
    that was not all of them.

    The bound is on the answer rather than on the matches, so the
    continuation counts against both limits: what a caller was promised
    is a result inside `RECALL_LINES` and `RECALL_BYTES`, and a sentence
    appended after the arithmetic would be a result that is over them by
    exactly the length of this module's longest constant.

    Whether the continuation is needed depends on where this stops, and
    where it stops depends on the continuation, so each prefix is
    measured as the answer it would produce: the lines, plus the
    sentence if anything would be left over.

    One match always survives. A single fact longer than the whole
    bound would otherwise answer with the refinement sentence and
    nothing else, and there is nothing to refine towards: the model
    would have asked for the one thing that matched and been told to ask
    again. It comes back cut instead, with its id intact, because the id
    is what the answer is for.
    """
    kept: list[tuple[int, str]] = []
    for match in matches:
        candidate = [*kept, match]
        left_over = len(candidate) < len(matches)
        if len(candidate) + int(left_over) > RECALL_LINES:
            break
        if len(_answered(candidate, left_over).encode("utf-8")) > RECALL_BYTES:
            break
        kept.append(match)
    if not kept and matches:
        row_id, line = matches[0]
        room = RECALL_BYTES
        if len(matches) > 1:
            room -= len(MORE_MATCHED.encode("utf-8")) + 1
        kept = [(row_id, _shortened(line, room))]
    return _answered(kept, len(kept) < len(matches))


def _answered(kept: Sequence[tuple[int, str]], left_over: bool) -> str:
    """One lookup's whole answer: the lines it kept, and the fixed
    sentence where it kept fewer than matched."""
    found = _rendered(kept)
    if not left_over:
        return found
    return f"{found}\n{MORE_MATCHED}" if found else MORE_MATCHED


def _shortened(line: str, room: int) -> str:
    """One rendered match cut to fit, from the right, so the id at the
    front of it survives.

    Cut on bytes and decoded with what will not decode dropped, because
    the bound is a byte bound and a multibyte character straddling it is
    not half a character. The marker is inside the room rather than
    added to it, for the reason the caller's bound exists.
    """
    if len(line.encode("utf-8")) <= room:
        return line
    keeping = max(room - len(ELLIPSIS), 0)
    return line.encode("utf-8")[:keeping].decode("utf-8", "ignore") + ELLIPSIS


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
    "CONVERSATION_ERASED",
    "CORE_BYTES",
    "CORE_LINES",
    "DEVICE_BYTES",
    "ELLIPSIS",
    "DEVICE_LINES",
    "MAX_BYTES",
    "MAX_LINES",
    "MEMORY_CHAIN",
    "MORE_MATCHED",
    "NOTHING_TO_LOOK_FOR",
    "NOTHING_PURGED",
    "NOTHING_REMEMBERED",
    "NOTHING_TO_REMEMBER",
    "NOT_A_FACT_SCOPE",
    "NOTHING_TO_SET",
    "NO_FACT_TO_FORGET",
    "NO_FACT_TO_RESTORE",
    "NO_FACT_TO_UPDATE",
    "PURGE_BUSY",
    "PURGE_FAILED",
    "QUIET_TIMEOUT_S",
    "RECALL_BYTES",
    "RECALL_LINES",
    "STATE_BYTES",
    "STATE_ENTRY_TOO_LONG",
    "STATE_FULL",
    "STATE_KEYS",
    "STATE_TOO_MUCH",
    "SWEEP_GRACE",
    "TOO_LONG",
    "UNWRITABLE",
    "MemoryScope",
    "MemoryStore",
    "PromptMemory",
    "Purged",
    "StoreClosed",
    "clear_ledger",
    "conversations_holding_memory",
    "correct",
    "erase_fact",
    "erase_facts",
    "facts_of",
    "ledger_of",
    "open_memory",
    "owners",
    "purge",
]
