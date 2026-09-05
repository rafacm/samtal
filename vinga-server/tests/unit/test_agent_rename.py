"""One rename, three schemas, one transaction.

`ConfigStore.rename_agent` is the first write in this server that spans
the domain half, the memory schema and the conversation record at once,
and the properties it has to have are not properties of any one of them.
This suite is about the transaction rather than about the SQL.

What it pins:

- **The sentinel sweep**, which is this file's central claim. One agent
  named a value nothing else in the fixture holds, referenced from every
  live place at once, is renamed; then every row of every table of the
  three schemas is read and rendered as text, and the set of
  `(table, column)` pairs still carrying the sentinel is compared with a
  recorded set. It fails in both directions: a live reference left
  behind, and a dated column that stopped carrying the name it is
  supposed to keep. What it is a claim about is bounded and stated where
  the set is written down: values a fixture wrote, not a schema that can
  name its own agent references.
- **The inventory pin**, so the sweep cannot silently stop covering the
  domain half. `check_references` is what the repository itself calls a
  reference to an agent, it is empty after a rename, and it is shown to
  be non-empty over the same state with one binding put back.
- **The seven refusals**, one case per state of the transaction before
  it writes: one absent source, three occupied destinations, two
  malformed new names, one contended database. Each collision case
  builds its destination in ONE store and leaves it empty in the other
  two, so a check that stopped running fails rather than passing for the
  wrong reason.
- **Atomicity**, driven from the last statement: with the memory rewrite
  refused by the database, the agents row, the bindings, the default
  agent and the threads are all as they were.
- **Reversibility**, which is what the no-confirmation decision rests
  on: rename and rename back, and all three schemas are byte-identical.
  Run a second time with a stranger present, which is the assertion a
  merge fails while a plain round trip passes.
- **The competing write**, once per store that checks a destination: a
  second writer of the same chain, released exactly between the check
  and the update, is queued on the lock rather than landing inside the
  decision. The waiter is required to be that writer's own backend by
  pid, because this lane runs its files across worker processes against
  one instance and a count of anybody's waiters would have been
  satisfied by a suite next door.
- **The order the lock and the check are issued in**, which the three
  pins above cannot see: each of them starts its writer at the statement
  that WRITES, so all three would still pass with a chain's lock taken
  between the destination check and the update rather than before both.
  That is asserted directly, off the statements the one connection
  sends.

Every thread here is minted per test, because a deletion names an id for
the life of the process and a suite that reused one would be arranging
something no server can meet.
"""

import asyncio
import contextlib
import datetime as dt
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from sqlalchemy import event, select

from tests.support.stores import (
    holding_the_write_lock,
    memory,
    memory_rows,
    rows,
    the_lock_held,
)
from vinga_server.config import entities
from vinga_server.config import store as store_module
from vinga_server.config.api import REFUSAL_STATUS
from vinga_server.config.loader import (
    AgentRenameConflictError,
    ConfigError,
    DatabaseBusyError,
    StorageError,
    UnknownEntityError,
)
from vinga_server.config.models import DatabaseConfig, check_references
from vinga_server.config.store import ConfigStore
from vinga_server.conversations import schema as record_schema
from vinga_server.conversations import store as record_store
from vinga_server.conversations.records import TurnLeg, TurnRecord
from vinga_server.conversations.store import CONVERSATIONS_CHAIN, ConversationStore
from vinga_server.db import (
    DOMAIN_CHAIN,
    connection_url,
    open_database,
    read_engine,
    write_engine,
)
from vinga_server.db import schema as domain_schema
from vinga_server.memory import schema as memory_schema
from vinga_server.memory import store as memory_store
from vinga_server.memory.store import MEMORY_CHAIN, MemoryScope, MemoryStore

# The agent kind's own descriptor, which is where the sentence a missing
# agent is refused with lives.
AGENT = entities.descriptor("agent")

# The name under test, shaped so that a substring sweep for it cannot
# match anything else the fixture writes: not a word, not a MAC, not a
# provider type, and not a fragment of the prompt text beside it.
SENTINEL = "agent-4f0c8e21-only-ever-this-agent"

# A second agent nothing renames, so the sweep can tell "the sentinel
# left this column" from "this column is empty".
BYSTANDER = "poet"

# What the sentinel's name is renamed to, and the name a collision is
# arranged under. Distinct values, because a test that reused one could
# pass for the wrong reason.
RENAMED = "agent-9b3d7a54-the-name-it-answers-to-now"
STRANGER = "agent-2e6f1c08-never-part-of-any-rename"

MAC = "aa:bb:cc:dd:ee:01"
OTHER_MAC = "aa:bb:cc:dd:ee:02"

# The dated record, written down rather than derived, which is what
# makes the sweep an assertion instead of a tautology. Each pair is a
# column whose subject is a moment before the rename:
#
# - `sessions.agent` and `sessions.agents` say what the session opened
#   with, and that moment is before the rename whatever the row's insert
#   time was.
# - `turns.agent` and `turns.legs` say who spoke, and the turns here
#   were spoken before it.
# - `events.fields` is what was emitted at the time, and nothing
#   rewrites an emitted event.
#
# What is deliberately NOT here is `conversations.agent`: a thread is
# owned by the agent under its current name, which is the one live
# column of the record and the reason a renamed agent can still be asked
# to resume its own history.
#
# The bound on the claim, stated because the sweep is where an inventory
# claim would be tempting: this is a fact about values, not about the
# schema. It catches any column this fixture populates, a column added
# later included, and it cannot see a column nothing here writes to.
DATED_RECORD = frozenset(
    {
        ("sessions", "agent"),
        ("sessions", "agents"),
        ("turns", "agent"),
        ("turns", "legs"),
        ("events", "fields"),
    }
)

# The three schemas, in the order the census reads them.
SCHEMAS = (domain_schema, record_schema, memory_schema)

# How long a released call is given to finish, which is the writer
# timeout every other suite waits under, with room for the lock wait a
# forced interleaving parks a second writer in.
DONE_S = 15.0

# How long a second writer is given to appear as a waiter on its chain's
# lock, and how often the question is asked. Asked of the database
# rather than slept for, so this is a ceiling and not a wait.
QUEUED_TIMEOUT_S = 10.0
QUEUED_POLL_S = 0.05

AT = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def store() -> Iterator[ConfigStore]:
    engine = open_database(DatabaseConfig())
    try:
        yield ConfigStore(engine)
    finally:
        engine.dispose()


@pytest.fixture
def thread() -> str:
    return uuid.uuid4().hex


@pytest.fixture
def other_thread() -> str:
    return uuid.uuid4().hex


# What the fixture writes


def a_working_configuration(store: ConfigStore, *agents: str) -> None:
    """Providers, defaults and one agent per name, in the natural order
    a store is built up in."""
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
    store.set_provider("asr", "whisper", {"type": "faster_whisper", "model": "small"})
    store.set_provider("tts", "voice", {"type": "piper", "model": "es"})
    store.set_provider("vad", "silero", {"type": "silero"})
    store.set_agent_defaults(
        {"llm": "claude", "asr": "whisper", "tts": "voice", "vad": "silero"}
    )
    for name in agents:
        store.set_agent(name, {"prompt": "You answer questions."})


def manifest(agent: str) -> dict[str, Any]:
    return {
        "started_at": AT.isoformat(),
        "server": {"version": "0.1.0", "revision": "abc1234"},
        "device": {"mac": MAC, "client": "test"},
        "protocol": "1",
        "agent": agent,
        "agents": [agent],
        "providers": {"llm": {"name": "claude", "type": "anthropic"}},
    }


def a_recorded_thread(conversation: str, agent: str, session: str) -> None:
    """One thread with a turn, a split reply's legs and an event on it,
    written the way the server writes one and the writer let go of.

    The legs and the event are what put an agent name inside JSON in two
    more places, which is half of what the sweep exists to look at.
    """
    store = ConversationStore(DatabaseConfig(), now=lambda: AT, retention_days=0)
    store.start()
    try:
        store.open_session(session, 100.0, manifest(agent))
        store.record_event(session, "heard", 20, {"agent": agent}, 101.0)
        landed = store.record_turn(
            session,
            TurnRecord(
                at=101.2,
                conversation=conversation,
                agent=agent,
                heard="what time is it",
                reply="Just gone noon.",
                legs=(TurnLeg(agent=agent, text="Just gone noon."),),
            ),
        )
        assert landed.wait(DONE_S), "the turn never landed"
        store.close_session(session, duration_s=2.0, reason="client")
    finally:
        store.stop()


async def a_remembered_fact(owner: str, fact: str, conversation: str | None = None) -> int:
    """One fact under one agent's name, held (softly forgotten) when a
    conversation is named, since a held row carries `owner` like any
    other and has to move with the rename."""
    store = memory()
    kept = await store.add(MemoryScope.AGENT, owner, fact, agent=owner)
    if conversation is not None:
        await store.forget(MemoryScope.AGENT, owner, kept, conversation, agent=owner)
    return kept


async def an_agent_referenced_everywhere(
    store: ConfigStore, thread: str, other_thread: str
) -> None:
    """The sentinel agent, reachable from every live place at once: two
    device bindings, the default agent, agent memory with one held row,
    and two recorded threads with turns."""
    a_working_configuration(store, SENTINEL, BYSTANDER)
    store.bind_device(MAC, [SENTINEL])
    store.bind_device(OTHER_MAC, [SENTINEL, BYSTANDER])
    store.set_default_agent(SENTINEL)
    a_recorded_thread(thread, SENTINEL, "alpha")
    a_recorded_thread(other_thread, SENTINEL, "beta")
    await a_remembered_fact(SENTINEL, "the user is vegetarian")
    await a_remembered_fact(SENTINEL, "the user was going to the coast", thread)


# What the database holds, read whole


def every_row() -> dict[str, list[dict[str, Any]]]:
    """Every row of every table of the three schemas, keyed by table.

    Through one read engine, because the tables carry their schema on
    their metadata and one connection therefore addresses all three
    without a `search_path`.
    """
    engine = open_database(DatabaseConfig())
    held: dict[str, list[dict[str, Any]]] = {}
    try:
        with engine.connect() as connection:
            for module in SCHEMAS:
                for table in module.metadata.sorted_tables:
                    held[table.name] = [
                        dict(row)
                        for row in connection.execute(
                            select(table).order_by(*table.primary_key.columns)
                        ).mappings()
                    ]
    finally:
        engine.dispose()
    return held


def carrying(name: str) -> set[tuple[str, str]]:
    """Which `(table, column)` pairs still hold this name anywhere in
    their value, rendered as text so a name inside a JSON array or a
    JSON object is found exactly as one in a text column is."""
    return {
        (table, column)
        for table, held in every_row().items()
        for row in held
        for column, value in row.items()
        if name in str(value)
    }


# The sweep


async def test_a_rename_moves_every_live_reference_and_no_dated_one(
    store: ConfigStore, thread: str, other_thread: str
) -> None:
    """The central pin, as an equality against a recorded set.

    Both directions matter. A live reference the rename forgot leaves a
    pair in the answer that is not in `DATED_RECORD`; a dated column the
    rename touched takes one out of it.
    """
    await an_agent_referenced_everywhere(store, thread, other_thread)

    store.rename_agent(SENTINEL, RENAMED)

    assert carrying(SENTINEL) == DATED_RECORD
    # And the other direction, so that "the sentinel is gone" cannot be
    # satisfied by a rename that lost the rows instead of moving them.
    assert carrying(RENAMED) == {
        ("agents", "name"),
        ("devices", "agents"),
        ("domain_settings", "value"),
        ("conversations", "agent"),
        ("facts", "owner"),
    }


async def test_what_moved_is_answered_by_the_write_itself(
    store: ConfigStore, thread: str, other_thread: str
) -> None:
    """The result type is the seam back: what the transaction did travels
    as fields rather than being recovered by re-reading a store the
    rename has already changed."""
    await an_agent_referenced_everywhere(store, thread, other_thread)

    renamed = store.rename_agent(f"  {SENTINEL}  ", RENAMED)

    assert (renamed.old, renamed.new) == (SENTINEL, RENAMED)
    assert sorted(renamed.devices) == [MAC, OTHER_MAC]
    assert renamed.default_agent is True
    assert (renamed.facts, renamed.threads) == (2, 2)


async def test_the_renamed_agent_keeps_what_it_was_bound_to_and_knew(
    store: ConfigStore, thread: str, other_thread: str
) -> None:
    """The promise in the vocabulary an operator reads it in, rather than
    as a set of column names: the boards, the default, the memory and the
    threads all answer to the new name."""
    await an_agent_referenced_everywhere(store, thread, other_thread)

    store.rename_agent(SENTINEL, RENAMED)

    domain = store.load().domain
    assert RENAMED in domain.agents and SENTINEL not in domain.agents
    assert domain.devices[MAC] == [RENAMED]
    # Every position of a binding, not the first: the other board names
    # the bystander beside it and keeps it.
    assert domain.devices[OTHER_MAC] == [RENAMED, BYSTANDER]
    assert domain.default_agent == RENAMED
    assert {row["owner"] for row in memory_rows("facts")} == {RENAMED}
    assert {row["agent"] for row in rows("conversations")} == {RENAMED}


async def test_a_held_fact_moves_with_the_rest(
    store: ConfigStore, thread: str, other_thread: str
) -> None:
    """A softly forgotten row carries `owner` like any other, so it moves
    and a restore after the rename still finds it. Stated as its own case
    because the held area is the one part of memory no ordinary read
    walks."""
    await an_agent_referenced_everywhere(store, thread, other_thread)

    store.rename_agent(SENTINEL, RENAMED)

    held = [row for row in memory_rows("facts") if row["forgotten_in"] is not None]
    assert [row["owner"] for row in held] == [RENAMED]
    assert [row["forgotten_in"] for row in held] == [thread]


# The inventory pin


async def test_the_rename_leaves_no_reference_the_repository_can_name(
    store: ConfigStore, thread: str, other_thread: str
) -> None:
    """The domain half's inventory is `check_references`'s own walk
    rather than a list written beside it, and after a rename it finds
    nothing.

    The second half is what keeps that from being vacuous: the same
    function over the same state with one binding put back to the old
    name does report it, so an empty answer above is the rename's doing
    rather than the check's silence.
    """
    await an_agent_referenced_everywhere(store, thread, other_thread)
    store.rename_agent(SENTINEL, RENAMED)

    domain = store.load().domain
    assert check_references(domain) == []

    domain.devices[MAC] = [SENTINEL]
    assert check_references(domain) != []


# The seven refusals, one per state


async def test_a_rename_of_an_agent_that_is_not_there_is_the_missing_sentence(
    store: ConfigStore,
) -> None:
    a_working_configuration(store, BYSTANDER)

    with pytest.raises(UnknownEntityError) as refused:
        store.rename_agent(SENTINEL, RENAMED)

    assert str(refused.value) == AGENT.missing
    assert SENTINEL not in str(refused.value)


async def test_an_agent_under_the_new_name_refuses_the_merge(
    store: ConfigStore, thread: str
) -> None:
    """The domain collision, with the other two destinations empty, so a
    check that stopped running is a failure rather than a case that
    passes for the wrong reason."""
    a_working_configuration(store, SENTINEL, RENAMED)
    a_recorded_thread(thread, SENTINEL, "alpha")
    await a_remembered_fact(SENTINEL, "the user is vegetarian")
    assert memory_rows("facts", owner=RENAMED) == []
    assert rows("conversations", agent=RENAMED) == []

    with pytest.raises(AgentRenameConflictError) as refused:
        store.rename_agent(SENTINEL, RENAMED)

    assert str(refused.value) == store_module.AGENT_EXISTS
    assert SENTINEL not in str(refused.value)
    assert RENAMED not in str(refused.value)
    assert set(store.load().domain.agents) == {SENTINEL, RENAMED}


async def test_memory_under_the_new_name_refuses_the_merge(
    store: ConfigStore, thread: str
) -> None:
    """The memory collision, which is a real state rather than a
    constructed one: facts outlive the agent they belong to by design,
    which is the audit door the listings are."""
    a_working_configuration(store, SENTINEL)
    a_recorded_thread(thread, SENTINEL, "alpha")
    await a_remembered_fact(SENTINEL, "the user is vegetarian")
    await a_remembered_fact(RENAMED, "a fact of an agent that is gone")
    assert RENAMED not in store.load().domain.agents
    assert rows("conversations", agent=RENAMED) == []

    with pytest.raises(AgentRenameConflictError) as refused:
        store.rename_agent(SENTINEL, RENAMED)

    assert str(refused.value) == memory_store.RENAME_OCCUPIED
    assert SENTINEL not in str(refused.value)
    assert RENAMED not in str(refused.value)
    assert set(store.load().domain.agents) == {SENTINEL}
    assert {row["owner"] for row in memory_rows("facts")} == {SENTINEL, RENAMED}


async def test_threads_under_the_new_name_refuse_the_merge(
    store: ConfigStore, thread: str, other_thread: str
) -> None:
    """The record collision, and the same real state one table across: a
    thread has no foreign key, an unbound agent can be deleted while its
    threads stay, and retention prunes by idleness rather than by
    ownership."""
    a_working_configuration(store, SENTINEL)
    a_recorded_thread(thread, SENTINEL, "alpha")
    a_recorded_thread(other_thread, RENAMED, "beta")
    await a_remembered_fact(SENTINEL, "the user is vegetarian")
    assert RENAMED not in store.load().domain.agents
    assert memory_rows("facts", owner=RENAMED) == []

    with pytest.raises(AgentRenameConflictError) as refused:
        store.rename_agent(SENTINEL, RENAMED)

    assert str(refused.value) == record_store.RENAME_OCCUPIED
    assert SENTINEL not in str(refused.value)
    assert RENAMED not in str(refused.value)
    assert {row["agent"] for row in rows("conversations")} == {SENTINEL, RENAMED}
    assert {row["owner"] for row in memory_rows("facts")} == {SENTINEL}


@pytest.mark.parametrize("unaddressable", ["two/parts", "control\x07character", "   "])
async def test_a_new_name_that_cannot_be_addressed_is_refused(
    store: ConfigStore, unaddressable: str
) -> None:
    """The name rule, unchanged and reused: stripped, non-empty, no
    slash, no control character. Nothing about the value is quoted
    back."""
    a_working_configuration(store, SENTINEL)

    with pytest.raises(ConfigError) as refused:
        store.rename_agent(SENTINEL, unaddressable)

    assert not isinstance(refused.value, AgentRenameConflictError)
    assert unaddressable.strip() not in str(refused.value) or not unaddressable.strip()
    assert set(store.load().domain.agents) == {SENTINEL}


@pytest.mark.parametrize("spelling", [SENTINEL, f"  {SENTINEL}  "])
async def test_renaming_to_the_name_it_already_has_is_refused(
    store: ConfigStore, spelling: str
) -> None:
    """Everything strips first, so a name differing only in spacing is
    the same name and there is nothing to rename."""
    a_working_configuration(store, SENTINEL)

    with pytest.raises(ConfigError) as refused:
        store.rename_agent(SENTINEL, spelling)

    assert str(refused.value) == store_module.SAME_NAME
    assert SENTINEL not in str(refused.value)


async def test_a_contended_database_is_the_retryable_refusal(
    store: ConfigStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seventh state, answered by the sentence every write here
    answers a held lock with."""
    a_working_configuration(store, SENTINEL)

    with holding_the_write_lock(monkeypatch):
        engine = open_database(DatabaseConfig())
        try:
            contended = ConfigStore(engine)
            with the_lock_held():
                with pytest.raises(DatabaseBusyError):
                    contended.rename_agent(SENTINEL, RENAMED)
        finally:
            engine.dispose()


def test_the_conflict_answers_the_status_a_state_of_the_world_answers() -> None:
    """The type and its status never exist apart. Without this row a
    destination collision would fall through to the 422 the plain
    refusal maps to and read as a malformed request."""
    assert REFUSAL_STATUS[AgentRenameConflictError] == 409
    # And it is a `ConfigError`, which is what carries it untranslated
    # through the classifying arms of the two stores it is raised in.
    assert issubclass(AgentRenameConflictError, ConfigError)


# Atomicity


@contextlib.contextmanager
def _the_facts_refusing_updates() -> Iterator[None]:
    """A test-only `BEFORE UPDATE` trigger on the facts table, which is
    the cheapest genuine failure the last statement of a rename can
    meet: a statement that runs and is refused."""
    holder = _connection()
    try:
        holder.execute(
            "create function memory.refuse_fact_update() returns trigger "
            "language plpgsql as $$ begin raise exception 'no updates in this "
            "test'; end $$"
        )
        holder.execute(
            "create trigger refuse_fact_update before update on memory.facts "
            "for each row execute function memory.refuse_fact_update()"
        )
        holder.commit()
        yield
    finally:
        holder.execute("drop trigger if exists refuse_fact_update on memory.facts")
        holder.execute("drop function if exists memory.refuse_fact_update()")
        holder.commit()
        holder.close()


async def test_a_memory_rewrite_that_fails_rolls_the_whole_rename_back(
    store: ConfigStore, thread: str, other_thread: str
) -> None:
    """One connection, three schemas, one database, so failure atomicity
    is free: any refusal rolls the whole transaction back and there is no
    half-renamed state to compensate for.

    Driven from the LAST statement, which is the one that would leave the
    most behind if the three writes were three transactions.
    """
    await an_agent_referenced_everywhere(store, thread, other_thread)
    before = every_row()

    with _the_facts_refusing_updates():
        with pytest.raises(StorageError) as refused:
            store.rename_agent(SENTINEL, RENAMED)

    assert str(refused.value) == memory_store.RENAME_FAILED
    assert every_row() == before


# Reversibility, which is what the no-confirmation decision rests on


async def test_a_rename_and_a_rename_back_leave_the_database_as_it_was(
    store: ConfigStore, thread: str, other_thread: str
) -> None:
    a_working_configuration(store, SENTINEL, BYSTANDER)
    store.bind_device(MAC, [SENTINEL])
    store.set_default_agent(SENTINEL)
    a_recorded_thread(thread, SENTINEL, "alpha")
    await a_remembered_fact(SENTINEL, "the user is vegetarian")
    await a_remembered_fact(SENTINEL, "the user was going to the coast", thread)
    before = every_row()

    store.rename_agent(SENTINEL, RENAMED)
    store.rename_agent(RENAMED, SENTINEL)

    assert every_row() == before


async def test_a_round_trip_with_a_stranger_present_leaves_the_stranger_alone(
    store: ConfigStore, thread: str, other_thread: str
) -> None:
    """The assertion a merge fails even though the plain round trip
    passes. A third name that was never involved holds memory and threads
    of its own, and neither rename may pick them up: if it did, the
    second rename would carry them across and nothing afterwards could
    tell whose they were.
    """
    a_working_configuration(store, SENTINEL, STRANGER)
    store.bind_device(MAC, [SENTINEL])
    a_recorded_thread(thread, SENTINEL, "alpha")
    a_recorded_thread(other_thread, STRANGER, "beta")
    await a_remembered_fact(SENTINEL, "the user is vegetarian")
    await a_remembered_fact(STRANGER, "a fact of somebody else entirely")
    before = every_row()

    store.rename_agent(SENTINEL, RENAMED)
    store.rename_agent(RENAMED, SENTINEL)

    assert every_row() == before
    assert [row["fact"] for row in memory_rows("facts", owner=STRANGER)] == [
        "a fact of somebody else entirely"
    ]
    assert [row["conversation"] for row in rows("conversations", agent=STRANGER)] == [
        other_thread
    ]


# The competing write, between the check and the update


def _connection() -> psycopg.Connection:
    url = connection_url(DatabaseConfig()).set(drivername="postgresql")
    return psycopg.connect(url.render_as_string(hide_password=False))


def _queued_on(key: int, pid: int) -> bool:
    """Whether ONE named backend is really parked on one chain's
    advisory lock, asked of the database rather than slept for.

    The pid is the whole of this predicate, and it was not always. A
    count of ungranted waiters on the key answers yes for anybody's
    waiter, and this lane runs its files across worker processes against
    one instance, so a suite next door writing to the same chain would
    have satisfied it. What the pin claims is that THIS writer was made
    to wait, so it asks about that writer's own backend.
    """
    asking = _connection()
    try:
        row = asking.execute(
            "select count(*) from pg_locks where locktype = 'advisory' "
            "and not granted and ((classid::bigint << 32) | objid::bigint) = %s "
            "and pid = %s",
            (key, pid),
        ).fetchone()
        return bool(row and row[0])
    finally:
        asking.rollback()
        asking.close()


def a_writers_engine(chain: Any) -> "tuple[Any, list[int]]":
    """One chain's write engine, with the backend pid of every
    connection it makes recorded as it is made.

    On `connect` and deliberately not on `begin`, which was tried first
    and does not work. A write engine's own `begin` handler is what takes
    the chain's advisory lock, and a second handler does not run before
    it: `insert=True` did not put this one in front, so the pid was
    recorded only once the lock had been granted, which is exactly when a
    blocked writer is no longer blocked. `connect` is strictly earlier
    than any transaction, so the pid of the backend that is about to wait
    is in hand before it waits, which is what lets the predicate above
    name it.

    A fresh engine per competitor, so its pool is empty and the first
    connection it makes is the one that blocks.
    """
    engine = write_engine(DatabaseConfig(), chain)
    pids: list[int] = []

    def remember(dbapi_connection: Any, record: Any) -> None:
        pids.append(int(dbapi_connection.info.backend_pid))

    event.listen(engine, "connect", remember)
    return engine, pids


class Competing:
    """A second writer of one chain, started from inside the rename's own
    transaction and shown to be queued on the chain's lock.

    The gate is what makes this an arrangement rather than a wait. It is
    installed in place of the statement builder the rename reaches for
    AFTER it has decided its destination is free and BEFORE it writes, so
    the interleaving under test is forced rather than hoped for: the
    second writer is started exactly in that window, and the assertion is
    that the database will not let it in.

    `pids` is the list `a_writers_engine` fills, and it is what makes the
    waiting claim about this writer rather than about anybody's: the
    first entry is the backend that is about to take the chain's lock, so
    it is waited for first and then required to BE the ungranted waiter.
    """

    def __init__(self, key: int, work: Callable[[], Any], pids: list[int]) -> None:
        self._key = key
        self._work = work
        self._pids = pids
        self.answered: list[Any] = []
        self.queued = False
        self.finished_early = False
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        try:
            self.answered.append(self._work())
        except Exception as exc:  # noqa: BLE001 - a refusal is an answer here
            self.answered.append(exc)

    def _waited_for(self, question: Callable[[], bool]) -> bool:
        for _ in range(int(QUEUED_TIMEOUT_S / QUEUED_POLL_S)):
            if question():
                return True
            time.sleep(QUEUED_POLL_S)
        return False

    def start_and_wait_to_be_queued(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if self._waited_for(lambda: bool(self._pids)):
            pid = self._pids[0]
            self.queued = self._waited_for(lambda: _queued_on(self._key, pid))
        # Recorded rather than asserted here, because this runs inside
        # the transaction under test and an assertion would leave it open.
        self.finished_early = bool(self.answered)

    def join(self) -> None:
        assert self._thread is not None
        self._thread.join(DONE_S)
        assert not self._thread.is_alive(), "the second writer never finished"


@contextlib.contextmanager
def released_between_the_check_and_the_write(
    monkeypatch: pytest.MonkeyPatch, module: Any, name: str, competitor: Competing
) -> Iterator[None]:
    """The gate: one statement builder replaced by a wrapper that starts
    the second writer on its first call and then delegates.

    The first call is the rename's own write, which is the statement
    immediately after the destination check, so what the second writer
    meets is a decision already made and not yet written.
    """
    real = getattr(module, name)
    fired = threading.Event()

    def gated(*args: Any, **kwargs: Any) -> Any:
        if not fired.is_set():
            fired.set()
            competitor.start_and_wait_to_be_queued()
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, gated)
    yield
    assert fired.is_set(), "the gate never ran, so nothing was arranged"


async def test_a_memory_write_cannot_land_between_the_check_and_the_update(
    store: ConfigStore, thread: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claim is about the lock rather than about the table: the lock
    `rename_owner` holds is the one every writer of the memory chain
    takes at BEGIN, so a competing `add` under the destination name
    queues behind this transaction and the rename's decision is still
    true when it writes."""
    a_working_configuration(store, SENTINEL)
    await a_remembered_fact(SENTINEL, "the user is vegetarian")
    engine, pids = a_writers_engine(MEMORY_CHAIN)

    def competing_add() -> int:
        # The store's own public call, on engines of its own rather than
        # the lane's, which is what a second process's writer is and what
        # lets its backend be named before it blocks.
        elsewhere = MemoryStore(engine, read_engine(DatabaseConfig()))
        return asyncio.run(
            elsewhere.add(
                MemoryScope.AGENT, RENAMED, "a fact from elsewhere", agent=RENAMED
            )
        )

    writer = Competing(MEMORY_CHAIN.lock_key, competing_add, pids)
    try:
        with released_between_the_check_and_the_write(
            monkeypatch, memory_store, "sql_update", writer
        ):
            renamed = store.rename_agent(SENTINEL, RENAMED)
        writer.join()
    finally:
        engine.dispose()

    assert writer.queued, "the second writer was never parked on the memory chain"
    assert not writer.finished_early, "the second writer landed inside the decision"
    # The rename moved its own row and only its own: the fact that
    # arrived afterwards is not one it counted.
    assert renamed.facts == 1
    assert sorted(row["fact"] for row in memory_rows("facts", owner=RENAMED)) == [
        "a fact from elsewhere",
        "the user is vegetarian",
    ]


async def test_a_record_write_cannot_land_between_the_check_and_the_update(
    store: ConfigStore, thread: str, other_thread: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same claim on the record chain, whose lock `rename_agent`
    takes as its own first statement."""
    a_working_configuration(store, SENTINEL)
    a_recorded_thread(thread, SENTINEL, "alpha")
    engine, pids = a_writers_engine(CONVERSATIONS_CHAIN)

    def competing_thread() -> None:
        with engine.begin() as connection:
            connection.execute(
                record_schema.conversations.insert().values(
                    conversation=other_thread,
                    agent=RENAMED,
                    device=MAC,
                    created_at=AT.isoformat(),
                    last_active_at=AT.isoformat(),
                )
            )

    writer = Competing(CONVERSATIONS_CHAIN.lock_key, competing_thread, pids)
    try:
        with released_between_the_check_and_the_write(
            monkeypatch, record_store, "update", writer
        ):
            renamed = store.rename_agent(SENTINEL, RENAMED)
        writer.join()
    finally:
        engine.dispose()

    assert writer.queued, "the second writer was never parked on the record chain"
    assert not writer.finished_early, "the second writer landed inside the decision"
    assert renamed.threads == 1
    assert {row["conversation"] for row in rows("conversations", agent=RENAMED)} == {
        thread,
        other_thread,
    }


async def test_a_domain_write_cannot_land_between_the_check_and_the_update(
    store: ConfigStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the third store that checks a destination, whose lock is the
    one the write engine's begin listener takes rather than one the
    rename asks for: a competing write under the destination name queues
    on the domain chain exactly as the other two do.

    The competing write is a binding to the destination name, which is
    the one that says WHEN it ran without any clock. Run inside the
    rename's window it would read a state in which nothing answers to
    that name and be refused for an unresolved reference; run after the
    commit, which is what the lock forces, it resolves and is written.
    """
    a_working_configuration(store, SENTINEL)
    engine, pids = a_writers_engine(DOMAIN_CHAIN)

    def competing_bind() -> Any:
        return ConfigStore(engine).bind_device(OTHER_MAC, [RENAMED])

    writer = Competing(DOMAIN_CHAIN.lock_key, competing_bind, pids)
    try:
        with released_between_the_check_and_the_write(
            monkeypatch, store_module, "_rename_agent_row", writer
        ):
            store.rename_agent(SENTINEL, RENAMED)
        writer.join()
    finally:
        engine.dispose()

    assert writer.queued, "the second writer was never parked on the domain chain"
    assert not writer.finished_early, "the second writer landed inside the decision"
    assert not isinstance(writer.answered[0], Exception), writer.answered[0]
    assert set(store.load().domain.agents) == {RENAMED}
    assert store.load().domain.devices[OTHER_MAC] == [RENAMED]


# And the order the lock and the check are issued in


async def test_each_store_locks_its_chain_before_it_looks_at_its_destination(
    thread: str,
) -> None:
    """The half the three pins above cannot see.

    Each of them starts its second writer at the statement that WRITES,
    which is after the destination check, so all three would still pass
    if a chain's lock were taken between the check and the update rather
    than before both. That arrangement is not the one the stores claim:
    a check made outside the lock can go stale before the update runs,
    which is precisely the merge the destination rule exists to refuse.

    So the order is asserted directly, off the statements the one
    connection issues. `before_cursor_execute` sees every statement of
    the rename's transaction in the order it is sent, the advisory lock
    among them, and each chain's lock has to come before the first
    statement that names that chain's table.
    """
    issued: list[str] = []
    engine = open_database(DatabaseConfig())

    def record(connection: Any, cursor: Any, statement: str, *rest: Any) -> None:
        issued.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        store = ConfigStore(engine)
        a_working_configuration(store, SENTINEL)
        a_recorded_thread(thread, SENTINEL, "alpha")
        await a_remembered_fact(SENTINEL, "the user is vegetarian")
        issued.clear()

        store.rename_agent(SENTINEL, RENAMED)
    finally:
        engine.dispose()

    def first(fragment: str) -> int:
        for position, statement in enumerate(issued):
            if fragment in statement:
                return position
        raise AssertionError(f"no statement of the rename contained {fragment!r}")

    locked_record = first(f"pg_advisory_xact_lock({CONVERSATIONS_CHAIN.lock_key})")
    locked_memory = first(f"pg_advisory_xact_lock({MEMORY_CHAIN.lock_key})")
    assert locked_record < first("record.conversations")
    assert locked_memory < first("memory.facts")
    # And the whole of the ascending order at statement level, which the
    # lock-order walk asserts by recording the keys and this asserts by
    # reading the wire: the domain chain's lock is the transaction's
    # first statement, and the other two follow it in order.
    assert first(f"pg_advisory_xact_lock({DOMAIN_CHAIN.lock_key})") < locked_record
    assert locked_record < locked_memory


def test_the_lane_can_read_all_three_schemas_through_one_connection() -> None:
    """The premise the sweep rests on, proved rather than assumed: every
    table carries its schema on its metadata, so one connection addresses
    all three without a `search_path`."""
    held = every_row()

    assert {"agents", "conversations", "facts"} <= set(held)
