"""A rename while somebody is still talking to the agent.

M1 made the rename one transaction over three schemas. This suite is
about the half a transaction cannot do on its own: a live session holds
the name it opened with for as long as it lasts, and the rows it writes
after the rename would land on a thread that has moved. Left alone that
is two defects rather than a cosmetic mismatch. A materialized thread
refuses the turn for misattribution and the writer drops the marker's
whole batch; a thread that has not materialized yet is INSERTed under
the old name, which is a fresh live reference to a name nothing answers
to, made by the act that removes them.

What it pins:

- **The two defects, with the interleaving forced rather than hoped
  for.** The writer parks in front of the transaction that would write
  the turn, the rename commits and publishes while it is parked, and the
  turn is then let through: once with the thread already materialized,
  once with its first turn arriving after the rename.
- **The order that covers the instant between the commit and the
  publication.** The rename holds the conversation record's ordering
  lock across both, and a writer released into that window is shown to
  be parked on the same lock with nothing committed, rather than assumed
  to be.
- **What the translation writes, read off the rows.** A turn spoken
  after the rename carries the new name, each of its legs carries it,
  the turn spoken before it still carries the old one, and the session
  row keeps the name it opened with, because that column's subject is
  the moment the session opened.
- **The lifecycle the per-session map rests on.** A chain of renames
  resolves in one step, an agent nothing renamed is untouched, a batch
  queued behind a close still finds its translation, and a name a rename
  freed and an operator gave to a NEW agent is not translated for the
  session that opened under the new agent. The last one is the case a
  process-wide map would fail, which is why the map is per session.

Every thread and every session here is minted per test, because the
writer's own state is keyed by both and a suite that reused one would be
arranging something no server can produce.
"""

import datetime as dt
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests.support.sessions import WRITER_TIMEOUT_S as TIMEOUT_S
from tests.support.sessions import Gate, until
from tests.support.stores import rows
from vinga_server.config.models import DatabaseConfig
from vinga_server.config.store import ConfigStore
from vinga_server.conversations import store as record_store
from vinga_server.conversations.records import TurnLeg, TurnRecord
from vinga_server.conversations.store import ConversationStore
from vinga_server.db import open_database

# The name a session opens under, the name it is given, and two more for
# the cases that need a third and a fourth. Distinct values, because a
# case that reused one could pass for the wrong reason.
OLD = "sam"
NEW = "poet"
THIRD = "bard"
BYSTANDER = "scribe"
OTHER = "herald"

MAC = "aa:bb:cc:dd:ee:01"

AT = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def config() -> Iterator[ConfigStore]:
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


@pytest.fixture
def session() -> str:
    return uuid.uuid4().hex


@pytest.fixture
def other_session() -> str:
    return uuid.uuid4().hex


def a_working_configuration(store: ConfigStore, *agents: str) -> None:
    """Providers, defaults and one agent per name, which is what a
    rename needs under it: the reference check runs over the state the
    rename would leave, and a state whose providers do not resolve is
    refused before the rename is reached."""
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


def a_turn(
    conversation: str,
    agent: str,
    heard: str,
    legs: tuple[TurnLeg, ...] | None = None,
) -> TurnRecord:
    return TurnRecord(
        at=101.2,
        conversation=conversation,
        agent=agent,
        heard=heard,
        reply="Done.",
        legs=legs,
    )


@pytest.fixture
def gated() -> Iterator[tuple[ConversationStore, Gate]]:
    """A store recording the way the server's does, with the writer's
    parking seam installed, which is what makes an interleaving an
    arrangement rather than a wait.

    Every case here has one, because every case here is about what the
    writer does while the rename commits."""
    gate = Gate()
    store = ConversationStore(
        DatabaseConfig(), now=lambda: AT, retention_days=0, gate=gate
    )
    store.start()
    try:
        yield store, gate
    finally:
        gate.open_forever()
        store.stop()


def opened(store: ConversationStore, session: str, agent: str, gate: Gate) -> None:
    """One session opened and its row committed, with the writer left
    parked in front of nothing."""
    store.open_session(session, 100.0, manifest(agent))
    gate.wait()
    gate.let_through()


def agent_of(conversation: str) -> str:
    (row,) = rows("conversations", conversation=conversation)
    return str(row["agent"])


def turns_of(conversation: str) -> list[dict[str, Any]]:
    return sorted(
        rows("turns", conversation=conversation), key=lambda row: int(row["id"])
    )


# The two defects, forced


async def test_a_materialized_thread_keeps_taking_turns_across_a_rename(
    config: ConfigStore, gated: tuple[ConversationStore, Gate], session: str, thread: str
) -> None:
    """The first defect. The thread's row already says who owns it, and
    `landed()` refuses a turn whose agent does not match it; the durable
    writer answers any exception that is not a busy one by failing the
    marker's whole batch, so every turn spoken after the rename would be
    lost and so would the ones batched with it.

    The interleaving is arranged rather than raced: the second turn is
    enqueued and the writer is demonstrably parked in front of the
    transaction that would write it when the rename commits.
    """
    store, gate = gated
    a_working_configuration(config, OLD)
    opened(store, session, OLD, gate)
    store.record_turn(session, a_turn(thread, OLD, "before the rename"))
    gate.wait()
    gate.let_through()
    until(lambda: rows("turns"), "the first turn never landed")

    # Enqueued while the writer is parked, so the rename below is
    # guaranteed to land between what is committed and what is not.
    in_flight = store.record_turn(session, a_turn(thread, OLD, "already on its way"))
    gate.wait()

    config.rename_agent(OLD, NEW)

    gate.open_forever()
    assert in_flight.wait(TIMEOUT_S) is True, "the turn after the rename was dropped"
    store.close_session(session, duration_s=2.0, reason="client")
    store.stop()

    assert [row["heard"] for row in turns_of(thread)] == [
        "before the rename",
        "already on its way",
    ]
    assert agent_of(thread) == NEW


async def test_a_thread_that_first_lands_after_a_rename_carries_the_new_name(
    config: ConfigStore, gated: tuple[ConversationStore, Gate], session: str, thread: str
) -> None:
    """The second defect, and the one that is worse: the other branch of
    `landed()` INSERTs the thread from the landing, so an untranslated
    first turn writes a fresh row under a name nothing answers to. A
    rename that manufactured a new orphan on its way past would be the
    defect this issue exists to end, made by the act that ends it.
    """
    store, gate = gated
    a_working_configuration(config, OLD)
    opened(store, session, OLD, gate)

    # Nothing of this thread exists yet: its first turn is queued while
    # the writer is parked, and the rename commits in front of it.
    in_flight = store.record_turn(session, a_turn(thread, OLD, "the first thing said"))
    gate.wait()

    config.rename_agent(OLD, NEW)

    gate.open_forever()
    assert in_flight.wait(TIMEOUT_S) is True, "the first turn was dropped"
    store.close_session(session, duration_s=2.0, reason="client")
    store.stop()

    assert agent_of(thread) == NEW
    # And the row whose subject is the moment the session opened keeps
    # that moment's name, even though the insert that carries it is one
    # this same writer made before the rename.
    (opening,) = rows("sessions", session=session)
    assert (opening["agent"], opening["agents"]) == (OLD, [OLD])


# The order that covers the instant between the commit and the publication


class WatchedOrder:
    """The ordering lock, with a count of who is parked on it.

    The claim under test is about an instant, so the instrument has to
    be able to see somebody arrive in it. Everything else about this is
    the real lock: it is entered and left exactly where the module's own
    is, and the count is taken around the acquisition rather than
    instead of it.
    """

    def __init__(self, real: threading.Lock) -> None:
        self._real = real
        self._counting = threading.Lock()
        self.waiting = 0

    def __enter__(self) -> "WatchedOrder":
        with self._counting:
            self.waiting += 1
        self._real.acquire()
        with self._counting:
            self.waiting -= 1
        return self

    def __exit__(self, *problem: object) -> bool:
        self._real.release()
        return False


async def test_no_durable_batch_can_commit_between_the_commit_and_the_publication(
    config: ConfigStore,
    gated: tuple[ConversationStore, Gate],
    session: str,
    thread: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordering lock's own claim, driven by a gate in that instant.

    The rename's transaction and its publication are two steps, and
    between the commit that released the chain locks and the
    announcement there is an instant in which a writer could take those
    locks, find nothing published, and write a turn onto a thread that
    has just moved. The rename holds the record's ordering lock across
    both, so that instant is covered.

    Forced rather than hoped for in both directions: the writer is
    released into exactly that window, and what is asserted there is
    that it is really parked on the same lock and that nothing of its
    batch has reached the database.
    """
    store, gate = gated
    a_working_configuration(config, OLD)
    opened(store, session, OLD, gate)
    store.record_turn(session, a_turn(thread, OLD, "before the rename"))
    gate.wait()
    gate.let_through()
    until(lambda: rows("turns"), "the first turn never landed")

    in_flight = store.record_turn(session, a_turn(thread, OLD, "already on its way"))
    gate.wait()

    order = WatchedOrder(record_store._erasure_order)
    monkeypatch.setattr(record_store, "_erasure_order", order)
    publishing = record_store.renamed
    arranged = threading.Event()

    def released_into_the_window(old: str, new: str) -> None:
        # Called after the commit and inside the order, which is the
        # window this test is about.
        gate.open_forever()
        until(lambda: order.waiting, "the writer never reached the order")
        assert len(rows("turns")) == 1, "a batch committed inside the window"
        arranged.set()
        publishing(old, new)

    monkeypatch.setattr(record_store, "renamed", released_into_the_window)
    config.rename_agent(OLD, NEW)

    assert arranged.is_set(), "the window was never arranged"
    assert in_flight.wait(TIMEOUT_S) is True, "the turn after the rename was dropped"
    store.close_session(session, duration_s=2.0, reason="client")
    store.stop()

    assert len(turns_of(thread)) == 2
    assert agent_of(thread) == NEW


# What the translation writes, read off the rows


async def test_the_turn_written_after_a_rename_is_read_back_whole(
    config: ConfigStore, gated: tuple[ConversationStore, Gate], session: str, thread: str
) -> None:
    """The row rather than the thread's owner, which is the assertion a
    landing-only translation would pass while writing exactly the
    disagreeing row this exists to catch: `turns.agent` under the old
    name on a thread under the new one.

    Three subjects, three answers. A turn spoken after the rename is a
    new write and says who is speaking. A turn spoken before it is dated
    record and is never touched. The session row says what the session
    opened with, and that moment is before the rename whatever the row's
    insert time was.
    """
    store, gate = gated
    a_working_configuration(config, OLD, BYSTANDER)
    opened(store, session, OLD, gate)
    legs = (TurnLeg(agent=OLD, text="Let me ask."), TurnLeg(agent=BYSTANDER, text="Done."))
    store.record_turn(session, a_turn(thread, OLD, "before the rename", legs))
    gate.wait()
    gate.let_through()
    until(lambda: rows("turns"), "the first turn never landed")

    in_flight = store.record_turn(session, a_turn(thread, OLD, "after the rename", legs))
    gate.wait()

    config.rename_agent(OLD, NEW)

    gate.open_forever()
    assert in_flight.wait(TIMEOUT_S) is True
    store.close_session(session, duration_s=2.0, reason="client")
    store.stop()

    before, after = turns_of(thread)
    assert before["agent"] == OLD
    assert [leg["agent"] for leg in before["legs"]] == [OLD, BYSTANDER]
    assert after["agent"] == NEW
    assert [leg["agent"] for leg in after["legs"]] == [NEW, BYSTANDER]
    (opening,) = rows("sessions", session=session)
    assert (opening["agent"], opening["agents"]) == (OLD, [OLD])
    assert agent_of(thread) == NEW


async def test_a_leg_naming_a_second_renamed_agent_moves_with_it(
    config: ConfigStore, gated: tuple[ConversationStore, Gate], session: str, thread: str
) -> None:
    """A leg says who spoke that part of the reply, and it is written by
    the same insert at the same instant as the turn's own column. So it
    is resolved on its own account through the same translations: a
    handover to a second agent that was also renamed carries the name
    that agent has now, for the reason the turn's does."""
    store, gate = gated
    a_working_configuration(config, OLD, BYSTANDER)
    opened(store, session, OLD, gate)
    legs = (TurnLeg(agent=OLD, text="Let me ask."), TurnLeg(agent=BYSTANDER, text="Done."))
    in_flight = store.record_turn(session, a_turn(thread, OLD, "after both", legs))
    gate.wait()

    config.rename_agent(OLD, NEW)
    config.rename_agent(BYSTANDER, OTHER)

    gate.open_forever()
    assert in_flight.wait(TIMEOUT_S) is True
    store.close_session(session, duration_s=2.0, reason="client")
    store.stop()

    (written,) = turns_of(thread)
    assert written["agent"] == NEW
    assert [leg["agent"] for leg in written["legs"]] == [NEW, OTHER]


# The lifecycle the per-session map rests on


async def test_a_session_of_an_agent_nothing_renamed_is_untouched(
    config: ConfigStore, gated: tuple[ConversationStore, Gate], session: str, thread: str
) -> None:
    """A translation that reached a name nobody moved would be a rename
    of an agent that was never renamed."""
    store, gate = gated
    a_working_configuration(config, OLD, BYSTANDER)
    opened(store, session, BYSTANDER, gate)
    in_flight = store.record_turn(session, a_turn(thread, BYSTANDER, "hello"))
    gate.wait()

    config.rename_agent(OLD, NEW)

    gate.open_forever()
    assert in_flight.wait(TIMEOUT_S) is True
    store.close_session(session, duration_s=2.0, reason="client")
    store.stop()

    assert agent_of(thread) == BYSTANDER
    assert [row["agent"] for row in turns_of(thread)] == [BYSTANDER]


async def test_a_chain_of_renames_resolves_in_one_step(
    config: ConfigStore, gated: tuple[ConversationStore, Gate], session: str, thread: str
) -> None:
    """Composed on insert rather than walked on read: a session that
    opened as the first name lands under the third, and nothing in the
    writer follows a chain."""
    store, gate = gated
    a_working_configuration(config, OLD)
    opened(store, session, OLD, gate)
    in_flight = store.record_turn(session, a_turn(thread, OLD, "through two renames"))
    gate.wait()

    config.rename_agent(OLD, NEW)
    config.rename_agent(NEW, THIRD)

    gate.open_forever()
    assert in_flight.wait(TIMEOUT_S) is True
    store.close_session(session, duration_s=2.0, reason="client")
    store.stop()

    assert agent_of(thread) == THIRD
    assert [row["agent"] for row in turns_of(thread)] == [THIRD]


async def test_a_batch_queued_behind_a_close_still_finds_its_translation(
    config: ConfigStore, gated: tuple[ConversationStore, Gate], session: str, thread: str
) -> None:
    """Where the retirement is, stated as a case.

    A session's translations are dropped where its device is dropped,
    which is after the close has been committed rather than when it
    arrives. Retiring them when the close was ENQUEUED would take them
    from a turn that is still in the queue in front of it, and that turn
    would land under the old name.
    """
    store, gate = gated
    a_working_configuration(config, OLD)
    opened(store, session, OLD, gate)
    in_flight = store.record_turn(session, a_turn(thread, OLD, "the last thing said"))
    # Queued behind the turn the writer is parked in front of.
    store.close_session(session, duration_s=2.0, reason="client")
    gate.wait()

    config.rename_agent(OLD, NEW)

    gate.open_forever()
    assert in_flight.wait(TIMEOUT_S) is True
    store.stop()

    assert agent_of(thread) == NEW
    assert [row["agent"] for row in turns_of(thread)] == [NEW]


async def test_a_freed_name_given_to_a_new_agent_is_not_translated(
    config: ConfigStore,
    gated: tuple[ConversationStore, Gate],
    session: str,
    other_session: str,
    thread: str,
    other_thread: str,
) -> None:
    """The case a process-wide map fails, and the pin the lifecycle
    decision rests on.

    A rename frees the old name, and the grammar allows an operator to
    create a new agent under it straight away. A map keyed by nothing but
    the pair of names would file that new agent's turns under the renamed
    one and land its threads there. The map is per session and a
    publication marks the sessions live at that instant, so the session
    that opened afterwards has nothing to translate: its name came out of
    the store after the rename.
    """
    store, gate = gated
    a_working_configuration(config, OLD)
    opened(store, session, OLD, gate)
    store.record_turn(session, a_turn(thread, OLD, "the older conversation"))
    gate.wait()
    gate.let_through()
    until(lambda: rows("turns"), "the first turn never landed")

    config.rename_agent(OLD, NEW)
    # The freed name, given to somebody else entirely.
    config.set_agent(OLD, {"prompt": "A different agent under a free name."})
    opened(store, other_session, OLD, gate)

    draining = store.record_turn(session, a_turn(thread, OLD, "still the old one"))
    gate.wait()
    gate.let_through()
    fresh = store.record_turn(other_session, a_turn(other_thread, OLD, "and the new one"))
    gate.wait()
    gate.open_forever()

    assert draining.wait(TIMEOUT_S) is True
    assert fresh.wait(TIMEOUT_S) is True
    store.close_session(session, duration_s=2.0, reason="client")
    store.close_session(other_session, duration_s=2.0, reason="client")
    store.stop()

    assert agent_of(thread) == NEW
    # The turn spoken before the rename keeps the name it was spoken
    # under; the one spoken after it is a new write and says who is
    # speaking.
    assert [row["agent"] for row in turns_of(thread)] == [OLD, NEW]
    assert agent_of(other_thread) == OLD
    assert [row["agent"] for row in turns_of(other_thread)] == [OLD]
