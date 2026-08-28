"""A conversation's life as rows: when one appears, which turns it
refuses, what it is called, and when it stops being current.

Driven through the writer's own surface rather than against
`threads.py` directly, because that is where the lifecycle actually
happens: the row lands inside the transaction that stores its first
turn, and a test that inserted one itself would be pinning a helper
instead of the rule. What comes back is read through a second engine,
which is what a reader beside a running writer is.

The clock is the store's, which is what lets a thread be written as
old or as recent without anything sleeping.

What a title must NOT reach is next door in `test_conversations_session.py`,
where a real conversation is held over a websocket and the sentinel is
hunted through every surface a record can leave on.
"""

import datetime as dt
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests.support.stores import CONVERSATIONS_MANIFEST as MANIFEST
from tests.support.stores import rows
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations.records import TurnRecord
from vinga_server.conversations.store import ConversationStore
from vinga_server.conversations.threads import TITLE_CHARACTERS

NOW = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)

def thread(name: str) -> str:
    """A thread id in the shape the runtime mints, derived from a name
    so a test can say which conversation it means."""
    return uuid.uuid5(uuid.NAMESPACE_OID, name).hex


def a_turn(conversation: str, heard: str | None = "turn the light on") -> TurnRecord:
    return TurnRecord(at=101.0, conversation=conversation, agent="sam", heard=heard, reply="Done.")


@pytest.fixture
def stores() -> Iterator[Any]:
    built: list[ConversationStore] = []

    def _build(at: dt.datetime = NOW, **options: Any) -> ConversationStore:
        store = ConversationStore(DatabaseConfig(), now=lambda: at, **options)
        built.append(store)
        return store

    yield _build
    for store in built:
        store.stop()


# When a thread becomes a row


def test_a_session_with_no_turn_leaves_no_thread_behind(stores) -> None:
    """A wake that produced no transcript produces no turn, and an empty
    thread would clutter every listing and every spoken discovery for
    the sake of a conversation that never happened."""
    store = stores()
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    store.close_session("alpha", duration_s=1.0, reason="idle")
    store.stop()

    assert len(rows("sessions")) == 1
    assert rows("conversations") == []


def test_the_thread_row_lands_with_its_first_turn(stores) -> None:
    """The row materializes in the writer, in the transaction that
    stores the turn, from the pair the turn was stamped with and the
    device the session opened on."""
    store = stores()
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    store.record_turn("alpha", a_turn(thread("one")))
    store.close_session("alpha", duration_s=1.0, reason="client")
    store.stop()

    (row,) = rows("conversations")
    assert row["conversation"] == thread("one")
    assert row["agent"] == "sam"
    assert row["device"] == MANIFEST["device"]["mac"]
    assert row["incomplete"] is False
    assert row["created_at"] == row["last_active_at"] == NOW.isoformat()
    # And the turn names it, which is what makes the session view and
    # the thread view two readings of one row.
    (turn,) = rows("turns")
    assert (turn["conversation"], turn["session"]) == (thread("one"), "alpha")


def test_two_agents_in_one_session_are_two_threads(stores) -> None:
    """A conversation has exactly one agent, so a session that talked to
    two of them touched two threads and each is its own row."""
    store = stores()
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    store.record_turn("alpha", a_turn(thread("sam")))
    ada = a_turn(thread("ada"))
    store.record_turn("alpha", TurnRecord(**{**vars(ada), "agent": "ada"}))
    store.close_session("alpha", duration_s=1.0, reason="client")
    store.stop()

    assert {(row["conversation"], row["agent"]) for row in rows("conversations")} == {
        (thread("sam"), "sam"),
        (thread("ada"), "ada"),
    }


# What a thread refuses to own


def test_a_session_that_understood_no_device_stores_no_turn(stores) -> None:
    """A thread's device is not null, and a turn stored outside every
    thread is a turn nothing prunes: retention reaches turns through
    their conversation row and keeps every session a turn still names,
    so one such row would outlive the window forever. The turn is
    dropped and counted instead, exactly as any other failed write is.
    """
    store = stores(retention_days=0)
    store.start()
    store.open_session("alpha", 100.0, {**MANIFEST, "device": {"client": "test"}})
    store.record_turn("alpha", a_turn(thread("one")))
    store.close_session("alpha", duration_s=1.0, reason="client")
    store.stop()

    assert rows("conversations") == []
    assert rows("turns") == []
    assert rows("tool_invocations") == []
    # The session row is the open's own marker and committed before any
    # of this; what the refusal leaves on it is the count.
    (session,) = rows("sessions")
    assert session["device"] is None
    assert session["dropped"] == 1


def test_a_thread_refuses_a_turn_stamped_with_another_agent(stores) -> None:
    """A conversation belongs to exactly one agent for its whole life,
    so a turn naming an existing thread and a different agent is a
    defect, not a second speaker. Nothing of it commits, and the thread
    is left where its own agent put it."""
    store = stores(retention_days=0)
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    store.record_turn("alpha", a_turn(thread("one")))
    intruder = a_turn(thread("one"), heard="and now it is mine")
    store.record_turn("alpha", TurnRecord(**{**vars(intruder), "agent": "ada"}))
    store.close_session("alpha", duration_s=1.0, reason="client")
    store.stop()

    (row,) = rows("conversations")
    assert row["agent"] == "sam"
    assert row["title"] == "turn the light on"
    # The refusal rolled back, so the thread was never even moved
    # forward by the turn it refused.
    assert row["last_active_at"] == row["created_at"]
    (turn,) = rows("turns")
    assert (turn["agent"], turn["conversation"]) == ("sam", thread("one"))
    (session,) = rows("sessions")
    assert session["dropped"] == 1


# What a thread is called


def test_the_title_is_the_first_utterance(stores) -> None:
    store = stores()
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    store.record_turn("alpha", a_turn(thread("one"), heard="what is the weather like"))
    store.record_turn("alpha", a_turn(thread("one"), heard="and tomorrow"))
    store.close_session("alpha", duration_s=1.0, reason="client")
    store.stop()

    (row,) = rows("conversations")
    # The FIRST one: a title that moved with every turn would rename a
    # conversation out from under the person resuming it.
    assert row["title"] == "what is the weather like"


def test_a_long_first_utterance_is_truncated_to_a_title(stores) -> None:
    """A title is read aloud among candidates and printed in a table
    cell, and both of those are lines."""
    said = "word " * 60
    store = stores()
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    store.record_turn("alpha", a_turn(thread("one"), heard=said))
    store.close_session("alpha", duration_s=1.0, reason="client")
    store.stop()

    (row,) = rows("conversations")
    assert len(row["title"]) == TITLE_CHARACTERS
    assert row["title"] == said.strip()[:TITLE_CHARACTERS]


def test_text_off_derives_no_title(stores) -> None:
    """A title is the utterance it came from, so a deployment that
    stores no utterances stores no titles either. The thread is still a
    row: it is what a turn references and what retention prunes."""
    store = stores(text=False)
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    store.record_turn("alpha", a_turn(thread("one"), heard="the whole of what was said"))
    store.close_session("alpha", duration_s=1.0, reason="client")
    store.stop()

    (row,) = rows("conversations")
    assert row["title"] is None
    assert row["conversation"] == thread("one")


def test_an_utterance_of_only_whitespace_names_nothing(stores) -> None:
    store = stores()
    store.start()
    store.open_session("alpha", 100.0, MANIFEST)
    store.record_turn("alpha", a_turn(thread("one"), heard="   \n  "))
    store.close_session("alpha", duration_s=1.0, reason="client")
    store.stop()

    (row,) = rows("conversations")
    assert row["title"] is None


# When a thread was last spoken to


def test_every_turn_moves_the_thread_forward(stores) -> None:
    """`last_active_at` is what the listing orders on and what retention
    measures, so it is rewritten by each turn while `created_at` stays
    where the thread began."""
    later = NOW + dt.timedelta(hours=2)
    opening = stores(retention_days=0)
    opening.start()
    opening.open_session("alpha", 100.0, MANIFEST)
    opening.record_turn("alpha", a_turn(thread("one")))
    opening.stop()

    continuing = stores(at=later, retention_days=0)
    continuing.start()
    continuing.open_session("beta", 100.0, MANIFEST)
    continuing.record_turn("beta", a_turn(thread("one")))
    continuing.stop()

    (row,) = rows("conversations")
    assert row["created_at"] == NOW.isoformat()
    assert row["last_active_at"] == later.isoformat()
    # One thread, two sessions, and its turns are in both of them.
    assert {turn["session"] for turn in rows("turns")} == {"alpha", "beta"}
