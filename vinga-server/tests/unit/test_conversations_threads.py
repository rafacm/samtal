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
from vinga_server.conversations import threads
from vinga_server.conversations.records import ToolInvocation, TurnRecord
from vinga_server.conversations.store import ConversationStore, open_conversations
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


# Discovery: which thread a spoken description meant
#
# The read milestone 4's resume tool will consume, tested here against
# the store rather than through a tool that does not exist yet. What is
# under test is the matching, which is the part "five newest" could
# never have: a thread is found because of what was said in it, and the
# same question asked twice of the same rows answers the same way.


def spoken(
    store: ConversationStore,
    session: str,
    name: str,
    heard: str,
    agent: str = "sam",
) -> None:
    """One thread of one agent, opened with one utterance."""
    store.open_session(session, 100.0, MANIFEST | {"agent": agent, "agents": [agent]})
    store.record_turn(
        session,
        TurnRecord(
            at=101.0, conversation=thread(name), agent=agent, heard=heard, reply="Done."
        ),
    )


def asked(agent: str, description: str) -> threads.Candidates:
    engine = open_conversations(DatabaseConfig())
    try:
        with engine.connect() as connection:
            return threads.candidates(connection, agent, description)
    finally:
        engine.dispose()


def test_a_description_matches_on_the_words_the_thread_carries(stores) -> None:
    """Token overlap over the title and the opening excerpt, not
    recency: the thread that was talked about is the one that comes
    back first."""
    store = stores(retention_days=0)
    store.start()
    spoken(store, "a", "bikes", "how do I fix the brakes on my bicycle")
    spoken(store, "b", "soup", "what goes into a leek and potato soup")
    store.stop()

    answer = asked("sam", "the bicycle brakes")

    assert answer.matched is True
    assert [one.conversation for one in answer.found] == [thread("bikes")]
    assert answer.found[0].title == "how do I fix the brakes on my bicycle"
    assert answer.found[0].excerpt == "how do I fix the brakes on my bicycle"


def test_normalization_makes_case_and_punctuation_irrelevant(stores) -> None:
    """Casefolded, punctuation broken into whitespace and split. A
    hyphen becomes a break rather than a deletion, so the two words
    somebody says match the one word a transcript wrote."""
    store = stores(retention_days=0)
    store.start()
    spoken(store, "a", "wine", "Where did we put the WELL-AGED Rioja?!")
    store.stop()

    answer = asked("sam", "well aged rioja")

    assert answer.matched is True
    assert answer.found[0].score == 3


def test_a_relevant_older_thread_beats_newer_unrelated_ones(stores) -> None:
    """The reviewer's case, and the reason this is matching rather than
    a listing: the thread that answers the description is outside the
    newest five and still comes first."""
    store = stores(retention_days=0)
    store.start()
    spoken(store, "old", "telescope", "how far away is the andromeda galaxy")
    store.stop()
    for index in range(6):
        newer = stores(at=NOW + dt.timedelta(hours=index + 1), retention_days=0)
        newer.start()
        spoken(newer, f"new-{index}", f"chatter-{index}", "what is the weather doing")
        newer.stop()

    answer = asked("sam", "andromeda galaxy")

    assert answer.matched is True
    assert [one.conversation for one in answer.found] == [thread("telescope")]


def test_equal_scores_fall_back_to_activity_and_then_to_the_row(stores) -> None:
    """The (score, activity, id) ordering, with the first two forced to
    tie: two threads that match the description equally come back newest
    first, and the answer is the same every time it is asked for."""
    store = stores(retention_days=0)
    store.start()
    spoken(store, "a", "first", "the rain in spain")
    store.stop()
    later = stores(at=NOW + dt.timedelta(hours=1), retention_days=0)
    later.start()
    spoken(later, "b", "second", "the rain in spain")
    later.stop()

    answer = asked("sam", "rain in spain")

    assert [one.conversation for one in answer.found] == [thread("second"), thread("first")]
    assert asked("sam", "rain in spain") == answer


def test_nothing_matching_answers_the_newest_and_says_so(stores) -> None:
    """A dead end is a worse answer than a list somebody can still pick
    from, so the newest come back with `matched` false and the caller
    is the one that says nothing matched."""
    store = stores(retention_days=0)
    store.start()
    for index in range(6):
        spoken(store, f"s-{index}", f"thread-{index}", f"the {index} thing said")
    store.stop()

    answer = asked("sam", "something nobody ever mentioned")

    assert answer.matched is False
    assert len(answer.found) == threads.RESUME_CANDIDATES
    assert all(one.score == 0 for one in answer.found)


def test_discovery_is_scoped_to_the_agent_that_asked(stores) -> None:
    """A conversation belongs to exactly one agent, so another agent's
    thread is not a candidate however well it matches."""
    store = stores(retention_days=0)
    store.start()
    spoken(store, "a", "hers", "the andromeda galaxy", agent="nadia")
    store.stop()

    assert asked("sam", "the andromeda galaxy").found == ()
    assert [one.conversation for one in asked("nadia", "andromeda").found] == [
        thread("hers")
    ]


def test_a_thread_stored_with_no_text_scores_nothing_and_still_lists(stores) -> None:
    """Text-off leaves a thread with no title and no excerpt. It cannot
    be found by description, and it is still offered among the newest,
    which is the honest answer: it exists and there is nothing to match
    on."""
    store = stores(retention_days=0, text=False)
    store.start()
    spoken(store, "a", "quiet", "said but never stored")
    store.stop()

    answer = asked("sam", "said but never stored")

    assert answer.matched is False
    assert [one.conversation for one in answer.found] == [thread("quiet")]
    assert (answer.found[0].title, answer.found[0].excerpt) == (None, None)


# The backlog: one thread as the resume path reads it
#
# The other half of what milestone 4 consumes. Discovery above answers
# which thread; this answers what is in it, and it answers it in the
# hydrator's own type, so the rows-to-context path has one seam and not
# two.


def test_the_backlog_is_the_thread_oldest_first(stores) -> None:
    store = stores(retention_days=0)
    store.start()
    store.open_session("a", 100.0, MANIFEST)
    store.record_turn("a", a_turn(thread("one"), heard="first"))
    store.record_turn("a", a_turn(thread("one"), heard="second"))
    store.stop()

    found = read_backlog(thread("one"))

    assert found is not None
    assert found.agent == "sam"
    assert found.incomplete is False
    assert [(one.heard, one.reply) for one in found.turns] == [
        ("first", "Done."),
        ("second", "Done."),
    ]


def test_the_backlog_names_the_tools_a_turn_ran(stores) -> None:
    """Names, in the order the model issued them, and nothing a call
    could not be named by: an unnamed call is left out rather than
    rendered as a blank."""
    store = stores(retention_days=0)
    store.start()
    store.open_session("a", 100.0, MANIFEST)
    store.record_turn(
        "a",
        TurnRecord(
            at=101.0,
            conversation=thread("tools"),
            agent="sam",
            heard="lights",
            reply="Done.",
            tools=(
                ToolInvocation(position=0, source="builtin", name="remember"),
                ToolInvocation(position=1, source="unknown", name=None),
            ),
        ),
    )
    store.stop()

    found = read_backlog(thread("tools"))

    assert found is not None
    assert found.turns[0].tools == ("remember",)


def test_a_thread_nobody_wrote_has_no_backlog() -> None:
    """Which is also what a deleted thread looks like from here, and
    deliberately the same answer: there is nothing to resume either
    way."""
    assert read_backlog(thread("never")) is None


def test_a_thread_with_a_lost_write_says_so(stores) -> None:
    """The mark a dropped durable batch left, carried to the resume path
    because an acknowledgement speaks for one turn and a hole in the
    middle of a thread is what no per-turn answer can describe."""
    store = stores(retention_days=0)
    store.start()
    store.open_session("a", 100.0, MANIFEST)
    store.record_turn("a", a_turn(thread("holed")))
    store.stop()
    engine = open_conversations(DatabaseConfig())
    try:
        with engine.begin() as connection:
            threads.flag_incomplete(connection, [thread("holed")])
    finally:
        engine.dispose()

    found = read_backlog(thread("holed"))

    assert found is not None and found.incomplete is True


def read_backlog(conversation: str) -> threads.Backlog | None:
    engine = open_conversations(DatabaseConfig())
    try:
        with engine.connect() as connection:
            return threads.backlog(connection, conversation)
    finally:
        engine.dispose()
