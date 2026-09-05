"""A conversation's memory shares its thread's lifecycle.

The two stores are two schemas in one database, and #83 makes that a
promise rather than a coincidence: when a thread is erased or pruned,
what the conversation was keeping and the facts it forgot go in the same
transaction as its turns. This suite is about that transaction and about
the order everything else is held in around it.

What it pins:

- **Atomicity, from both sides.** A deletion answers counts for the
  memory it took, and a deletion that does not commit leaves every one
  of those rows exactly where it was.
- **The lock order.** A transaction that writes both stores takes the
  record chain's advisory lock and then the memory chain's, ascending,
  which is what keeps two of them from deadlocking. Asserted by
  recording the keys as they are taken, walking the erasure path, the
  retention path and the agent rename that takes all three, because a
  lock order is not observable in any other way.
- **Both straddling interleavings, forced rather than reasoned about.**
  A thread-keyed memory write parked inside its transaction while a
  deletion arrives finishes first and is then deleted by that deletion;
  one that arrives while a deletion is inside its own transaction waits,
  meets the dead set, and is refused with the fixed sentence. The gate
  is an advisory lock this suite holds, so each interleaving is an
  arrangement and not a wait.
- **What is not a thread's is not taken.** An agent's facts survive the
  erasure of the conversation they were said in, and a `remember` in
  flight during a deletion is not held up by it: it takes one chain's
  lock and nothing else, because a fact about the user is nobody's
  thread.

Every thread here is minted per test, the way a session mints one: an id
a deletion has named is refused for the life of the process, which is
what the dead set is, and a suite that reused an id would be arranging
something no server can meet.
"""

import asyncio
import contextlib
import datetime as dt
import threading
import uuid
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.support.configs import DEVICE_MAC, POET_MAC, base_config
from tests.support.events import both_formats
from tests.support.sessions import events_of, session_for, until
from tests.support.stores import memory, memory_rows
from vinga_server import db as db_module
from vinga_server.config.api import build_api
from vinga_server.config.models import DatabaseConfig
from vinga_server.config.store import ConfigStore
from vinga_server.conversations import store as record_store
from vinga_server.conversations.records import TurnRecord
from vinga_server.conversations.store import (
    CONVERSATIONS_CHAIN,
    ConversationStore,
    erasures_announced_to,
)
from vinga_server.db import DOMAIN_CHAIN, connection_url, open_database
from vinga_server.memory import store as memory_store
from vinga_server.memory.store import MEMORY_CHAIN, MemoryScope

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# How long a blocked call is given to prove it is blocked. Short, because
# what it stands in front of takes milliseconds when nothing is holding
# it: the assertion is that it did NOT finish, and every scenario here
# releases it immediately afterwards.
BLOCKED_S = 0.2

# How long a released call is given to finish, which is the writer
# timeout every other suite waits under.
DONE_S = 5.0


@pytest.fixture
def api() -> FastAPI:
    return build_api(TOKEN, DatabaseConfig())


@pytest.fixture
def client(api: FastAPI) -> TestClient:
    return TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"})


@pytest.fixture
def thread() -> str:
    return uuid.uuid4().hex


@pytest.fixture
def other_thread() -> str:
    return uuid.uuid4().hex


def manifest(started_at: str = "2026-08-15T10:00:00+00:00") -> dict[str, Any]:
    return {
        "started_at": started_at,
        "server": {"version": "0.1.0", "revision": "abc1234"},
        "device": {"mac": DEVICE_MAC.lower(), "client": "test"},
        "protocol": "1",
        "agent": "poet",
        "agents": ["poet"],
        "providers": {"llm": {"name": "claude", "type": "anthropic"}},
    }


def a_recorded_thread(
    conversation: str,
    session: str = "alpha",
    at: dt.datetime = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC),
) -> None:
    """One thread with a turn on it, written the way the server writes
    one, and the writer let go of. A thread with no row is not a thread
    an erasure can address."""
    store = ConversationStore(DatabaseConfig(), now=lambda: at, retention_days=0)
    store.start()
    try:
        store.open_session(session, 100.0, manifest(at.isoformat()))
        landed = store.record_turn(
            session,
            TurnRecord(
                at=101.2,
                conversation=conversation,
                agent="poet",
                heard="hello",
                reply="Hello.",
            ),
        )
        assert landed.wait(DONE_S), "the turn never landed"
        store.close_session(session, duration_s=2.0, reason="client")
    finally:
        store.stop()


async def a_thread_with_memory(conversation: str) -> None:
    """A ledger and a held fact for one thread, through the store's own
    calls rather than through inserts."""
    store = memory()
    await store.set_state(conversation, "scene", "the tavern", agent="poet")
    forgotten = await store.add(
        MemoryScope.AGENT, "poet", f"forgotten in {conversation}", agent="poet"
    )
    await store.forget(MemoryScope.AGENT, "poet", forgotten, conversation, agent="poet")


def state_of(conversation: str) -> list[tuple[str, str]]:
    return [
        (row["key"], row["value"])
        for row in memory_rows("state", conversation=conversation)
    ]


def held_in(conversation: str) -> list[str]:
    return [row["fact"] for row in memory_rows("facts", forgotten_in=conversation)]


def active_facts(owner: str = "poet") -> list[str]:
    return [
        row["fact"]
        for row in memory_rows("facts", owner=owner)
        if row["forgotten_in"] is None
    ]


def _connection() -> psycopg.Connection:
    url = connection_url(DatabaseConfig()).set(drivername="postgresql")
    return psycopg.connect(url.render_as_string(hide_password=False))


class TheLockHeld:
    """One chain's advisory lock, taken by a connection this suite owns
    and released when it says so.

    The gate every interleaving here is arranged with. A write engine
    takes its chain's lock at BEGIN, so a transaction that wants it is
    parked exactly where the scenario needs it: inside the call, having
    already taken everything a caller takes before opening a transaction,
    and having committed nothing. `waited_on` asks the database whether
    somebody is really parked there, so the arrangement is established
    rather than slept for.
    """

    def __init__(self, key: int) -> None:
        self._key = key
        self._holder = _connection()
        self._holder.execute("select pg_advisory_xact_lock(%s)", (key,))
        self._asking = _connection()

    def waited_on(self) -> bool:
        try:
            row = self._asking.execute(
                "select count(*) from pg_locks where locktype = 'advisory' "
                "and not granted and ((classid::bigint << 32) | objid::bigint) = %s",
                (self._key,),
            ).fetchone()
            return bool(row and row[0])
        finally:
            self._asking.rollback()

    def release(self) -> None:
        self._holder.rollback()
        self._holder.close()
        self._asking.close()


def erase_thread(client: TestClient, conversation: str) -> dict[str, int]:
    response = client.delete(f"/conversations/{conversation}")
    assert response.status_code == 200, response.text
    return response.json()


def in_a_thread(work: Any) -> "tuple[threading.Thread, list[Any]]":
    """One call running beside the test, with whatever it answered or
    raised kept where the test can assert on it."""
    answered: list[Any] = []

    def run() -> None:
        try:
            answered.append(work())
        except Exception as exc:  # noqa: BLE001 - a refusal is an answer here
            answered.append(exc)

    running = threading.Thread(target=run, daemon=True)
    running.start()
    return running, answered


@contextlib.contextmanager
def never_committing(api: FastAPI) -> Iterator[None]:
    """A deletion that does everything but commit, which is what a
    database refusing a commit leaves behind: the statements ran and the
    rows are unchanged."""
    runtime = api.state.api_runtime
    opening = runtime.erasures

    @contextlib.contextmanager
    def failing() -> Iterator[Any]:
        with opening() as connection:
            yield connection
            raise RuntimeError("the commit that never happened")

    api.state.api_runtime = replace(runtime, erasures=failing)
    try:
        yield
    finally:
        api.state.api_runtime = runtime


# What a deletion takes


async def test_erasing_a_thread_takes_its_ledger_and_what_it_forgot(
    client, thread: str, other_thread: str
) -> None:
    """The promise, through the route an operator actually calls. The
    counts ride the deleting transaction, so they are as true as the
    ones about turns beside them."""
    a_recorded_thread(thread)
    await a_thread_with_memory(thread)
    await a_thread_with_memory(other_thread)
    await memory().add(
        MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet"
    )

    taken = erase_thread(client, thread)

    assert (taken["conversations"], taken["state"], taken["held_facts"]) == (1, 1, 1)
    assert state_of(thread) == []
    assert held_in(thread) == []
    # Another thread's memory is another thread's, and an active fact is
    # nobody's thread: it belongs to the agent and outlives every
    # conversation it was said in.
    assert state_of(other_thread) == [("scene", "the tavern")]
    assert held_in(other_thread) == [f"forgotten in {other_thread}"]
    assert active_facts() == ["the user is vegetarian"]


async def test_erasing_a_session_takes_the_memory_of_the_thread_it_took(
    client, thread: str
) -> None:
    """The other door onto the same rule. A thread that loses every turn
    is deleted whole, so its memory goes with it in the same
    transaction."""
    a_recorded_thread(thread)
    await a_thread_with_memory(thread)

    response = client.delete("/sessions/alpha")
    assert response.status_code == 200, response.text
    taken = response.json()

    assert (taken["conversations"], taken["state"], taken["held_facts"]) == (1, 1, 1)
    assert state_of(thread) == []
    assert held_in(thread) == []


async def test_a_deletion_that_does_not_commit_leaves_the_memory(
    client, api, thread: str
) -> None:
    """Atomicity from the other side, which is the whole reason the
    deletes are inside the caller's transaction: a rollback takes them
    with it, and no count was ever answered."""
    a_recorded_thread(thread)
    await a_thread_with_memory(thread)

    with never_committing(api):
        response = client.delete(f"/conversations/{thread}")

    assert response.status_code == 500
    assert state_of(thread) == [("scene", "the tavern")]
    assert held_in(thread) == [f"forgotten in {thread}"]


async def test_retention_takes_the_memory_of_the_threads_it_prunes(
    thread: str, other_thread: str
) -> None:
    """The same transaction, reached by the policy rather than by a
    request. The writer is handed the purge at construction, because
    naming the memory store there would be an import cycle."""
    a_recorded_thread(thread, at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC))
    await a_thread_with_memory(thread)
    await a_thread_with_memory(other_thread)

    pruning = ConversationStore(
        DatabaseConfig(), retention_days=90, purge_memory=memory_store.purge
    )
    pruning.start()
    pruning.stop()

    assert state_of(thread) == []
    assert held_in(thread) == []
    # The thread nobody recorded was never pruned: retention's unit is a
    # thread the record knows about.
    assert state_of(other_thread) == [("scene", "the tavern")]
    assert held_in(other_thread) == [f"forgotten in {other_thread}"]


# Where nothing is recorded, a session's close is its threads' end


async def test_two_sessions_closing_take_their_own_memory_with_them() -> None:
    """The bound on a deployment that records nothing.

    No thread row ever lands there, so no erasure and no retention will
    come for these, and a closed session's threads can never be resumed:
    the close IS the thread's end. Two sessions in one process lifetime,
    because what this exists for is a server that never restarts, and a
    boot sweep alone would leave the rows until it did.
    """
    store = memory()
    threads: list[str] = []
    for scene in ("the tavern", "the docks"):
        session = session_for(base_config(), POET_MAC, memory=store)
        conversation = events_of(session).conversation
        assert conversation is not None
        threads.append(conversation)
        await store.set_state(conversation, "scene", scene, agent="poet")
        assert state_of(conversation) == [("scene", scene)]

        await session.runtime.close()

        assert state_of(conversation) == []
    # Two threads, so the second session really was a second one and the
    # first was not simply resumed.
    assert threads[0] != threads[1]


async def test_a_session_that_records_keeps_its_threads_memory_on_close() -> None:
    """The other side of the same condition, and the reason it is a
    condition at all: where threads are recorded, a session's close is
    not a thread's end. The conversation can be resumed, so what it was
    keeping survives the disconnect and is taken by whatever takes the
    thread."""
    store = memory()
    session = session_for(
        base_config(), POET_MAC, memory=store, conversations=_NoWriter()
    )
    conversation = events_of(session).conversation
    assert conversation is not None
    await store.set_state(conversation, "scene", "the tavern", agent="poet")

    await session.runtime.close()

    assert state_of(conversation) == [("scene", "the tavern")]


class _NoWriter:
    """A conversation store that records nothing and exists to say that
    one is there.

    What the factory reads off `conversations` is its presence, which is
    what decides whether a session purges its own threads at close; what
    it does with a turn is another suite's business.
    """

    def record_turn(self, session_id: str, record: Any) -> None:
        return None


# The lock order


@pytest.fixture
def keys_taken(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Every advisory key this process takes, in the order it takes
    them.

    White-box in its reach and unavoidable in what it pins: a lock order
    is not observable from any surface, and the property it protects (two
    transactions over the same two chains cannot deadlock) is exactly
    what a test cannot provoke on demand. All three names are patched
    because each caller holds its own reference: the write engine's begin
    listener reads `db`'s, the memory purge and the memory rename read
    the one that module imported, and the record's own rename reads
    the one its module imported.
    """
    taken: list[int] = []
    real = db_module.take_the_chain_lock

    def recording(connection: Any, chain: Any) -> None:
        taken.append(chain.lock_key)
        real(connection, chain)

    monkeypatch.setattr(db_module, "take_the_chain_lock", recording)
    monkeypatch.setattr(memory_store, "take_the_chain_lock", recording)
    monkeypatch.setattr(record_store, "take_the_chain_lock", recording)
    return taken


async def test_a_thread_erasure_takes_both_chain_locks_in_ascending_order(
    client, thread: str, keys_taken: list[int]
) -> None:
    a_recorded_thread(thread)
    await a_thread_with_memory(thread)
    keys_taken.clear()

    erase_thread(client, thread)

    assert keys_taken == [CONVERSATIONS_CHAIN.lock_key, MEMORY_CHAIN.lock_key]
    assert keys_taken == sorted(keys_taken)


async def test_a_retention_pass_takes_both_chain_locks_in_ascending_order(
    thread: str, keys_taken: list[int]
) -> None:
    a_recorded_thread(thread, at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC))
    await a_thread_with_memory(thread)
    pruning = ConversationStore(
        DatabaseConfig(), retention_days=90, purge_memory=memory_store.purge
    )
    keys_taken.clear()

    pruning.start()
    pruning.stop()

    assert keys_taken == [CONVERSATIONS_CHAIN.lock_key, MEMORY_CHAIN.lock_key]
    assert keys_taken == sorted(keys_taken)


async def test_an_agent_rename_takes_all_three_chain_locks_in_ascending_order(
    thread: str, keys_taken: list[int]
) -> None:
    """The third path, and the first transaction in this server that can
    hold all three keys at once.

    It opens on the domain chain's write engine, whose begin listener
    takes key 1, and then crosses into the record chain's 2 and the
    memory chain's 3, in that order. Ascending, and therefore incapable
    of closing a cycle with the erasure above, which takes 2 and then 3.
    """
    a_recorded_thread(thread)
    await a_thread_with_memory(thread)
    engine = open_database(DatabaseConfig())
    try:
        store = ConfigStore(engine)
        store.set_provider("llm", "claude", {"type": "anthropic", "model": "c-5"})
        store.set_agent_defaults({"llm": "claude"})
        store.set_agent("poet", {"prompt": "You are a poet."})
        keys_taken.clear()

        store.rename_agent("poet", "bard")
    finally:
        engine.dispose()

    assert keys_taken == [
        DOMAIN_CHAIN.lock_key,
        CONVERSATIONS_CHAIN.lock_key,
        MEMORY_CHAIN.lock_key,
    ]
    assert keys_taken == sorted(keys_taken)


# The two straddling interleavings


async def test_a_write_that_began_before_an_erasure_is_deleted_with_the_thread(
    client, thread: str
) -> None:
    """The first half of the ordering, forced.

    The write is parked inside its own transaction on a lock this test
    holds, so it has already taken its place in the erasure order and
    committed nothing. The erasure cannot begin until it lets go, which
    is what the bounded wait proves; and when it does, the rows the write
    just committed are the rows the erasure deletes.
    """
    a_recorded_thread(thread)
    store = memory()
    held = TheLockHeld(MEMORY_CHAIN.lock_key)
    try:
        writing, wrote = in_a_thread(
            lambda: asyncio.run(
                store.set_state(thread, "scene", "the tavern", agent="poet")
            )
        )
        until(held.waited_on, "the write never reached its transaction")

        erasing, erased = in_a_thread(lambda: erase_thread(client, thread))
        erasing.join(BLOCKED_S)
        assert erasing.is_alive(), "the erasure did not wait for the write"
    finally:
        held.release()

    writing.join(DONE_S)
    erasing.join(DONE_S)
    assert wrote == [None], wrote
    assert erased[0]["state"] == 1
    assert state_of(thread) == []


async def test_a_write_that_begins_after_an_erasure_is_refused(
    client, thread: str
) -> None:
    """The other half. The erasure is parked inside its transaction on a
    lock this test holds, so the write arrives while it is in flight and
    waits; when it wakes, the thread is gone and the dead set says so.

    The refusal is one fixed sentence, compared by equality: nothing of
    the conversation, the note or the database is in it.
    """
    a_recorded_thread(thread)
    store = memory()
    with erasures_announced_to(store.threads_erased):
        held = TheLockHeld(CONVERSATIONS_CHAIN.lock_key)
        try:
            erasing, erased = in_a_thread(lambda: erase_thread(client, thread))
            until(held.waited_on, "the erasure never reached its transaction")

            writing, wrote = in_a_thread(
                lambda: asyncio.run(
                    store.set_state(thread, "scene", "the tavern", agent="poet")
                )
            )
            writing.join(BLOCKED_S)
            assert writing.is_alive(), "the write did not wait for the erasure"
        finally:
            held.release()

        erasing.join(DONE_S)
        writing.join(DONE_S)

    assert erased[0]["conversations"] == 1
    (refusal,) = wrote
    assert isinstance(refusal, ValueError)
    assert str(refusal) == memory_store.CONVERSATION_ERASED
    assert state_of(thread) == []


async def test_the_refusal_repeats_nothing_of_the_note_it_refused(
    thread: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The one refusal this milestone adds, held to the rule every other
    one in the store is held to: a fixed sentence compared by equality,
    with nothing of the conversation, the name or the value in it, in
    the sentence or in either log format.

    Driven with a credential-shaped value, because what a ledger holds is
    the model's own text and a note is exactly the shape of thing
    somebody pastes a secret into.
    """
    spoken = "sk-test-9f1c02ab-a-note-nobody-should-repeat"
    store = memory()
    store.threads_erased(frozenset({thread}))

    with caplog.at_level("DEBUG"):
        with pytest.raises(ValueError) as refusal:
            await store.set_state(thread, "secret", spoken, agent="poet")
        with pytest.raises(ValueError) as cleared:
            await store.clear_state(thread, "secret", agent="poet")

    assert str(refusal.value) == memory_store.CONVERSATION_ERASED
    assert str(cleared.value) == memory_store.CONVERSATION_ERASED
    assert refusal.value.__cause__ is None and refusal.value.__context__ is None
    for surface in (str(refusal.value), both_formats(caplog)):
        assert spoken not in surface
        assert thread not in surface


async def test_a_fact_remembered_during_an_erasure_is_kept(
    client, thread: str
) -> None:
    """What is not a thread's is not the erasure's, and does not queue
    behind it either: an agent's fact takes the memory chain's lock and
    nothing else, so it is answered while the erasure is still in flight
    and survives it."""
    a_recorded_thread(thread)
    store = memory()
    held = TheLockHeld(CONVERSATIONS_CHAIN.lock_key)
    try:
        erasing, erased = in_a_thread(lambda: erase_thread(client, thread))
        until(held.waited_on, "the erasure never reached its transaction")

        await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
        assert active_facts() == ["the user is vegetarian"]
    finally:
        held.release()

    erasing.join(DONE_S)
    assert erased[0]["conversations"] == 1
    assert active_facts() == ["the user is vegetarian"]
