"""The `/conversations` namespace: threads read as threads, and erased.

The other projection of the rows `/sessions` answers. A turn names both,
so the two views are two readings of one set of rows rather than two
stores, and what this file is about is the half a session read cannot
give: which threads there are, in the order they were last spoken to,
one of them whole, and the dialogue inside it wherever it was spoken.

Three properties beyond the round trips:

- **Activity moves, so the cursor is a pair.** An immutable row-id
  cursor cannot page an order the rows do not sit in. The listing pages
  on (`last_active_at`, `id`) spelled out as two named parameters, and
  the tests drive equal timestamps split across a boundary, a thread
  spoken to between two pages, and the ordinary edges.
- **Erasing a thread is not erasing a session.** The turns leave
  whatever sessions they were spoken in and those sessions stay, with a
  gap in them; nothing touches their telemetry.
- **A deleted thread never comes back.** Absence of a row is also the
  ordinary state before a first turn, so the writer is told, and a turn
  queued before the delete and a turn produced after it are both
  discarded with false acknowledgements.

Driven through the real routes, against a store seeded by the writer
that would have written it, because what is under test is the contract
rather than the statements behind it.
"""

import datetime as dt
import json
import logging
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.support.configs import DEVICE_MAC
from tests.support.problems import refused
from tests.support.sessions import WRITER_TIMEOUT_S as TIMEOUT_S
from tests.support.sessions import Gate, until
from vinga_server import logs
from vinga_server.config.api import build_api
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations.records import (
    MilestoneRecord,
    ToolInvocation,
    TurnRecord,
)
from vinga_server.conversations.store import ConversationStore
from vinga_server.db import read_engine

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# Shaped so a substring check for it cannot match by accident, and
# planted where a refusal's own input can carry it out.
SENTINEL = "sk-test-3f7c22ab-never-a-real-credential"

FIRST = "9f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5"

SECOND = "1a2b3c4d5e6f708192a3b4c5d6e7f809"

THIRD = "3c4d5e6f708192a3b4c5d6e7f8091a2b"

NOW = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)

TABLES = (
    "sessions",
    "conversations",
    "turns",
    "tool_invocations",
    "conversation_milestones",
    "events",
)


def manifest(agent: str = "sam", started_at: str | None = None) -> dict[str, Any]:
    return {
        "started_at": started_at or "2026-08-15T10:00:00+00:00",
        "server": {"version": "0.1.0", "revision": "abc1234"},
        "device": {"mac": DEVICE_MAC.lower(), "client": "test"},
        "protocol": "1",
        "agent": agent,
        "agents": [agent],
        "providers": {"llm": {"name": "claude", "type": "anthropic"}},
    }


@pytest.fixture
def api() -> FastAPI:
    return build_api(TOKEN, DatabaseConfig())


@pytest.fixture
def client(api: FastAPI) -> TestClient:
    return TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"})


class Recorder:
    """A store driven the way the server drives one.

    Through the real writer rather than through inserts, so what the
    reads meet is what a deployment holds: titles derived, activity
    moved, threads and sessions crossing each other the way two real
    conversations do.

    `move_to` rebuilds the store rather than reassigning its clock,
    because the writer is a thread: a clock changed while records are
    still queued stamps some of them with the new reading and some with
    the old, and which is which is a race.
    """

    def __init__(self, at: dt.datetime = NOW) -> None:
        self.at = at
        self.store = self._built()
        self.store.start()

    def _built(self) -> ConversationStore:
        return ConversationStore(
            DatabaseConfig(), now=lambda: self.at, retention_days=0
        )

    def thread(
        self,
        session: str,
        conversation: str,
        heard: str = "turn the light on",
        agent: str = "sam",
        tools: tuple[ToolInvocation, ...] = (),
    ) -> None:
        """One session holding one turn of one thread."""
        self.store.open_session(session, 100.0, manifest(agent))
        self.turn(session, conversation, heard, agent, tools)
        self.store.close_session(session, duration_s=2.0, reason="client")

    def turn(
        self,
        session: str,
        conversation: str,
        heard: str = "turn the light on",
        agent: str = "sam",
        tools: tuple[ToolInvocation, ...] = (),
    ) -> None:
        self.store.record_turn(
            session,
            TurnRecord(
                at=101.2,
                conversation=conversation,
                agent=agent,
                heard=heard,
                reply="Done.",
                tools=list(tools),
            ),
        )

    def checkpoint(
        self,
        session: str,
        conversation: str,
        from_turn: int,
        after_turn: int,
        parent: int | None = None,
        body: str = "we talked about the weather",
    ) -> int:
        """One recap checkpoint on a thread, written the way the runtime
        writes one, and answering the row id a later one would name as
        its `parent`."""
        landed = self.store.record_milestone(
            session,
            MilestoneRecord(
                conversation=conversation,
                from_turn=from_turn,
                after_turn=after_turn,
                parent=parent,
                text=body,
            ),
        )
        assert landed.wait(TIMEOUT_S), "the checkpoint never landed"
        return int(stored("conversation_milestones")[-1]["id"])

    def move_to(self, at: dt.datetime) -> None:
        self.store.stop()
        self.at = at
        self.store = self._built()
        self.store.start()

    def done(self) -> None:
        self.store.stop()


def stored(table: str) -> list[dict[str, Any]]:
    engine = read_engine(DatabaseConfig())
    try:
        with engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    text(f"select * from record.{table} order by id")
                ).mappings()
            ]
    finally:
        engine.dispose()


def listed(client: TestClient, **query: str) -> dict[str, Any]:
    response = client.get("/conversations", params=query)
    assert response.status_code == 200, response.text
    return response.json()


def named(page: dict[str, Any]) -> list[str]:
    return [item["conversation"] for item in page["items"]]


def _leaked(caplog: pytest.LogCaptureFixture) -> str:
    """Everything this server logged, in both shipped formats.

    This server's own channels, and deliberately not every record in the
    process: the HTTP client making these requests logs the URL it asked
    for, which is the caller's own terminal rather than anything this
    server wrote.
    """
    return "".join(
        record.getMessage() + str(record.__dict__) + logs.JsonFormatter().format(record)
        for record in caplog.records
        if record.name.startswith("vinga_server")
    )


# The listing, and the order it is in


def test_a_store_with_no_threads_answers_an_empty_page(client) -> None:
    """No 404 for a deployment that never recorded: the schema is
    migrated at every boot, so what it has is empty tables and an empty
    list is the honest answer about them."""
    page = listed(client)

    assert page == {"items": [], "next_cursor_active": None, "next_cursor_id": None}


def test_the_listing_is_most_recently_active_first(client) -> None:
    """The order the entity is read in: which conversation is still
    going, not which one started first. So a thread begun earlier and
    spoken to since comes ahead of one begun later and left."""
    recording = Recorder()
    recording.thread("alpha", FIRST, "the older thread")
    recording.move_to(NOW + dt.timedelta(hours=1))
    recording.thread("beta", SECOND, "the newer thread")
    recording.move_to(NOW + dt.timedelta(hours=2))
    recording.store.open_session("gamma", 100.0, manifest())
    recording.turn("gamma", FIRST, "the older one, spoken to again")
    recording.store.close_session("gamma", duration_s=1.0, reason="client")
    recording.done()

    page = listed(client)

    assert named(page) == [FIRST, SECOND]
    assert page["items"][0]["turns"] == 2


def test_a_summary_carries_the_whole_row(client) -> None:
    """A thread holds no nested structure, so a listing leaves nothing
    out: what a detail read adds is the checkpoint count and nothing
    else."""
    recording = Recorder()
    recording.thread("alpha", FIRST, "what a thread is called")
    recording.done()

    (item,) = listed(client)["items"]

    assert item == {
        "id": item["id"],
        "conversation": FIRST,
        "agent": "sam",
        "title": "what a thread is called",
        "device": DEVICE_MAC.lower(),
        "incomplete": False,
        "created_at": NOW.isoformat(),
        "last_active_at": NOW.isoformat(),
        "turns": 1,
    }


def test_the_agent_filter_narrows_the_listing(client) -> None:
    """A conversation belongs to exactly one agent for its whole life,
    which is what makes this a filter rather than a search: story 14 of
    #190, list an agent's conversations."""
    recording = Recorder()
    recording.thread("alpha", FIRST, "hers", agent="nadia")
    recording.thread("beta", SECOND, "his", agent="sam")
    recording.done()

    assert named(listed(client, agent="nadia")) == [FIRST]
    assert named(listed(client, agent="sam")) == [SECOND]
    # A name nothing answers to is an empty page rather than a refusal:
    # an agent name is a word this deployment chose and there is no
    # shape to hold it to.
    assert listed(client, agent="nobody")["items"] == []


# The cursor, which is a pair


def test_one_page_answers_a_null_pair(client) -> None:
    """Null together, exactly when there was nothing beyond this page at
    the moment it was read."""
    recording = Recorder()
    recording.thread("alpha", FIRST)
    recording.done()

    page = listed(client, limit="5")

    assert (page["next_cursor_active"], page["next_cursor_id"]) == (None, None)


def test_a_page_holding_exactly_the_limit_answers_no_cursor(client) -> None:
    """The boundary the one-row-more trick exists for: two threads and a
    page of two is the last page, and a cursor here would send a client
    back for a page that is empty."""
    recording = Recorder()
    recording.thread("alpha", FIRST)
    recording.move_to(NOW + dt.timedelta(hours=1))
    recording.thread("beta", SECOND)
    recording.done()

    page = listed(client, limit="2")

    assert len(page["items"]) == 2
    assert (page["next_cursor_active"], page["next_cursor_id"]) == (None, None)


def test_walking_the_pages_reaches_every_thread_once(client) -> None:
    """The whole listing, a page at a time, with the pair sent back as
    it was answered."""
    recording = Recorder()
    for index, thread in enumerate((FIRST, SECOND, THIRD)):
        recording.move_to(NOW + dt.timedelta(hours=index))
        recording.thread(f"s-{index}", thread)
    recording.done()

    first = listed(client, limit="2")
    second = listed(
        client,
        limit="2",
        cursor_active=first["next_cursor_active"],
        cursor_id=str(first["next_cursor_id"]),
    )

    assert named(first) == [THIRD, SECOND]
    assert named(second) == [FIRST]
    assert (second["next_cursor_active"], second["next_cursor_id"]) == (None, None)


def test_equal_timestamps_split_across_a_boundary_lose_nothing(client) -> None:
    """Why the cursor is a pair rather than a stamp. Three threads that
    share an activity instant order by id descending, and a page that
    ends in the middle of them carries on from the row rather than from
    the second."""
    recording = Recorder()
    for index, thread in enumerate((FIRST, SECOND, THIRD)):
        recording.thread(f"s-{index}", thread)
    recording.done()
    stamps = {row["last_active_at"] for row in stored("conversations")}
    assert len(stamps) == 1, "the three threads were meant to share an instant"

    first = listed(client, limit="2")
    second = listed(
        client,
        limit="2",
        cursor_active=first["next_cursor_active"],
        cursor_id=str(first["next_cursor_id"]),
    )

    assert named(first) + named(second) == [THIRD, SECOND, FIRST]


def test_a_cursor_at_another_offset_pages_from_the_same_instant(client) -> None:
    """The canonicalization, on the boundary that would hide it.

    The comparison is lexicographic on text, so a cursor spelled at
    another offset only works if it is brought to the spelling the rows
    carry before it is compared. Three threads sharing one instant is
    where getting that wrong shows: the pair is the whole of what
    separates them, and a stamp compared as written would put the page
    boundary somewhere else entirely.
    """
    recording = Recorder()
    for index, thread in enumerate((FIRST, SECOND, THIRD)):
        recording.thread(f"s-{index}", thread)
    recording.done()

    first = listed(client, limit="2")
    # The same instant a caller's own client might hand back after
    # parsing it into a local zone.
    elsewhere = (
        dt.datetime.fromisoformat(first["next_cursor_active"])
        .astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30)))
        .isoformat()
    )
    second = listed(
        client,
        limit="2",
        cursor_active=elsewhere,
        cursor_id=str(first["next_cursor_id"]),
    )

    assert elsewhere != first["next_cursor_active"]
    assert named(first) + named(second) == [THIRD, SECOND, FIRST]


def test_a_thread_spoken_to_between_pages_is_missed_by_that_pass(client) -> None:
    """Stated rather than implied, because activity moves and no cursor
    over a moving value can promise otherwise: a thread whose activity
    carries it ahead of the cursor is missed by that listing pass and is
    at the head of a fresh one. What never happens is a thread whose
    pair did not move being duplicated or skipped."""
    recording = Recorder()
    for index, thread in enumerate((FIRST, SECOND, THIRD)):
        recording.move_to(NOW + dt.timedelta(hours=index))
        recording.thread(f"s-{index}", thread)
    recording.done()

    first = listed(client, limit="2")
    assert named(first) == [THIRD, SECOND]

    # FIRST is spoken to again, which moves it above the cursor.
    moved = Recorder(NOW + dt.timedelta(hours=9))
    moved.store.open_session("later", 100.0, manifest())
    moved.turn("later", FIRST, "and again")
    moved.store.close_session("later", duration_s=1.0, reason="client")
    moved.done()

    second = listed(
        client,
        limit="2",
        cursor_active=first["next_cursor_active"],
        cursor_id=str(first["next_cursor_id"]),
    )

    assert named(second) == []
    assert named(listed(client, limit="2")) == [FIRST, THIRD]


def test_a_cursor_past_the_end_is_an_empty_page(client) -> None:
    """Not a refusal: a pair below everything is a true question with an
    empty answer, which is what a client that read to the end and asked
    once more gets."""
    recording = Recorder()
    recording.thread("alpha", FIRST)
    recording.done()

    page = listed(client, cursor_active="2000-01-01T00:00:00+00:00", cursor_id="1")

    assert page["items"] == []


@pytest.mark.parametrize(
    "query",
    [
        {"cursor_active": "2026-08-15T12:00:00+00:00"},
        {"cursor_id": "4242"},
        {"cursor_active": "the fifteenth", "cursor_id": "4242"},
        {"cursor_active": "2026-08-15T12:00:00+00:00", "cursor_id": "later"},
        {"cursor_active": "2026-08-15T12:00:00", "cursor_id": "4242"},
        {"cursor_active": "2026-08-15", "cursor_id": "4242"},
    ],
    ids=[
        "only-active",
        "only-id",
        "unreadable-active",
        "unreadable-id",
        "naive-active",
        "day-only-active",
    ],
)
def test_half_a_pair_or_an_unreadable_one_is_refused(client, query) -> None:
    """The pair comes together or not at all. Half of it is an argument
    that went missing, and answering it with the top of the listing
    would silently replay a page the caller had already read.

    The last two are readable dates that name no instant. The comparison
    behind the cursor is lexicographic on text, and a stamp written
    without an offset is shorter than every stored `last_active_at`, so
    honoring one would page from a place the caller did not ask for.
    Refused rather than assumed to be UTC: this API never answered one,
    so nothing it answered is being refused."""
    response = client.get("/conversations", params=query)

    assert response.status_code == 422
    detail = refused(response.json(), 422)
    assert "cursor_active and cursor_id come together or not at all" in detail
    for sent in query.values():
        assert sent not in response.text


def test_a_refused_cursor_quotes_nothing_it_was_handed(
    client, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """The rule the whole API holds to, on the argument most likely to
    be pasted rather than typed."""
    with caplog.at_level(logging.DEBUG):
        response = client.get(
            "/conversations", params={"cursor_active": SENTINEL, "cursor_id": "1"}
        )
        assert refused(response.json(), 422)

    captured = capsys.readouterr()
    assert SENTINEL not in response.text
    assert SENTINEL not in _leaked(caplog)
    assert SENTINEL not in captured.out
    assert SENTINEL not in captured.err


# The detail


def test_the_detail_is_the_summary_and_its_checkpoints(client) -> None:
    recording = Recorder()
    recording.thread("alpha", FIRST, "one thread, whole")
    recording.done()

    response = client.get(f"/conversations/{FIRST}")

    assert response.status_code == 200
    detail = response.json()
    assert detail["conversation"] == FIRST
    assert detail["turns"] == 1
    assert detail["milestones"] == 0
    assert detail["checkpoints"] == []


def test_the_detail_answers_the_checkpoints_a_thread_has_accrued(client) -> None:
    """Story 28: the recaps themselves, oldest first, with the range
    each may claim and the one it consumed. The count beside them is
    their length rather than a second read, so a caller can never find
    the two disagreeing."""
    recording = Recorder()
    recording.store.open_session("alpha", 100.0, manifest())
    recording.turn("alpha", FIRST, "the first thing said")
    recording.turn("alpha", FIRST, "and the second")
    # Both turns really written, because a checkpoint records the ids of
    # the turns it read and the writer is a thread behind this one.
    until(lambda: len(stored("turns")) == 2, "the turns never landed")
    ids = [row["id"] for row in stored("turns")]
    earlier = recording.checkpoint("alpha", FIRST, ids[0], ids[0], body="the first recap")
    recording.checkpoint(
        "alpha", FIRST, ids[1], ids[1], parent=earlier, body="the recap of the recap"
    )
    recording.store.close_session("alpha", duration_s=2.0, reason="client")
    recording.done()

    detail = client.get(f"/conversations/{FIRST}").json()

    assert detail["milestones"] == 2
    assert [one["text"] for one in detail["checkpoints"]] == [
        "the first recap",
        "the recap of the recap",
    ]
    (first, second) = detail["checkpoints"]
    assert (first["from_turn"], first["after_turn"], first["parent"]) == (
        ids[0],
        ids[0],
        None,
    )
    assert second["parent"] == first["id"] == earlier
    assert second["created_at"] == NOW.isoformat()


def test_an_unknown_thread_is_a_404_naming_no_value(client) -> None:
    """The id arrived in the path and is not repeated: what a caller
    needs is the reason a row it expected is not there."""
    response = client.get("/conversations/nothing-of-that-id")

    assert response.status_code == 404
    assert "nothing-of-that-id" not in response.text
    assert "no conversation of that id" in refused(response.json(), 404).lower()


# The dialogue


def test_the_dialogue_is_oldest_first_across_its_sessions(client) -> None:
    """The half a session read cannot give: a thread's turns in the
    order they were said, wherever they were said, each naming the
    session it belongs to."""
    recording = Recorder()
    recording.thread("alpha", FIRST, "the first thing said")
    recording.move_to(NOW + dt.timedelta(hours=1))
    recording.store.open_session("beta", 100.0, manifest())
    recording.turn("beta", FIRST, "and the thing said later")
    recording.store.close_session("beta", duration_s=1.0, reason="client")
    recording.done()

    response = client.get(f"/conversations/{FIRST}/turns")

    assert response.status_code == 200
    page = response.json()
    assert [turn["heard"] for turn in page["items"]] == [
        "the first thing said",
        "and the thing said later",
    ]
    assert [turn["session"] for turn in page["items"]] == ["alpha", "beta"]
    assert {turn["conversation"] for turn in page["items"]} == {FIRST}


def test_a_turn_carries_its_calls_nested_in_the_order_they_were_issued(client) -> None:
    recording = Recorder()
    recording.thread(
        "alpha",
        FIRST,
        tools=(
            ToolInvocation(position=0, source="builtin", name="switch_agent", is_error=False),
            ToolInvocation(position=1, source="mcp", entry="home", name="lights", is_error=True),
        ),
    )
    recording.done()

    (turn,) = client.get(f"/conversations/{FIRST}/turns").json()["items"]

    assert turn["tool_calls"] == 2
    assert [call["name"] for call in turn["tool_invocations"]] == [
        "switch_agent",
        "lights",
    ]
    assert [call["is_error"] for call in turn["tool_invocations"]] == [False, True]


def test_the_dialogue_pages_forward_on_the_row_id(client) -> None:
    """The row-id cursor and not the pair: a turn's id never moves, so
    what came after one is a stable question and the direction is the
    one a client reconciling what it has read asks in."""
    recording = Recorder()
    recording.store.open_session("alpha", 100.0, manifest())
    for index in range(3):
        recording.turn("alpha", FIRST, f"the {index} thing said")
    recording.store.close_session("alpha", duration_s=1.0, reason="client")
    recording.done()

    first = client.get(f"/conversations/{FIRST}/turns", params={"limit": "2"}).json()
    second = client.get(
        f"/conversations/{FIRST}/turns",
        params={"limit": "2", "cursor": str(first["next_cursor"])},
    ).json()

    assert len(first["items"]) == 2
    assert [turn["heard"] for turn in second["items"]] == ["the 2 thing said"]
    assert second["next_cursor"] is None


def test_the_dialogue_of_an_unknown_thread_is_a_404(client) -> None:
    """Before the page, so an unknown thread is a refusal rather than an
    empty dialogue that reads like a thread with nothing in it."""
    response = client.get("/conversations/nothing-of-that-id/turns")

    assert response.status_code == 404


# The erasure


def test_erasing_a_thread_leaves_its_sessions_standing(client) -> None:
    """The asymmetry, which is the whole of what this deletion is: the
    turns leave whatever sessions they were spoken in, and those
    sessions and their telemetry stay. A session is a connection episode
    and it still happened, with a gap in it now."""
    recording = Recorder()
    recording.store.open_session("alpha", 100.0, manifest())
    recording.store.record_event("alpha", "heard", logging.INFO, {"duration_s": 1.0}, 101.0)
    recording.turn("alpha", FIRST, "the thread that goes")
    recording.turn("alpha", SECOND, "the thread that stays", agent="nadia")
    recording.store.close_session("alpha", duration_s=2.0, reason="client")
    recording.done()

    response = client.delete(f"/conversations/{FIRST}")

    assert response.status_code == 200
    assert response.json() == {
        "conversations": 1,
        "turns": 1,
        "tool_invocations": 0,
        "milestones": 0,
    }
    assert [row["session"] for row in stored("sessions")] == ["alpha"]
    assert len(stored("events")) == 1
    assert [row["conversation"] for row in stored("conversations")] == [SECOND]
    assert [row["heard"] for row in stored("turns")] == ["the thread that stays"]


def test_erasing_a_thread_takes_the_calls_its_turns_made(client) -> None:
    recording = Recorder()
    recording.thread(
        "alpha",
        FIRST,
        tools=(ToolInvocation(position=0, source="builtin", name="switch_agent", is_error=False),),
    )
    recording.done()

    taken = client.delete(f"/conversations/{FIRST}").json()

    assert taken["tool_invocations"] == 1
    assert stored("tool_invocations") == []


def test_erasing_an_unknown_thread_is_a_404_naming_no_value(client) -> None:
    response = client.delete("/conversations/nothing-of-that-id")

    assert response.status_code == 404
    assert "nothing-of-that-id" not in response.text
    assert "no conversation of that id" in refused(response.json(), 404).lower()


def test_the_thread_namespace_needs_the_token(api) -> None:
    """The gate is in front of routing, so every one of these is behind
    it for the reason every other read is."""
    with TestClient(api) as anonymous:
        assert anonymous.get("/conversations").status_code == 401
        assert anonymous.get(f"/conversations/{FIRST}").status_code == 401
        assert anonymous.get(f"/conversations/{FIRST}/turns").status_code == 401
        assert anonymous.delete(f"/conversations/{FIRST}").status_code == 401


def test_an_erased_thread_leaves_nothing_on_any_surface(
    client, caplog: pytest.LogCaptureFixture
) -> None:
    """The sentinel hunt, on the surface this milestone adds. A
    credential-shaped utterance that became a title and a stored
    transcript is gone from every table and from every read after its
    thread is erased, and the erasure said nothing about it on the way.
    """
    recording = Recorder()
    recording.thread("alpha", FIRST, SENTINEL)
    recording.done()
    assert stored("conversations")[0]["title"] == SENTINEL

    with caplog.at_level(logging.DEBUG):
        assert client.delete(f"/conversations/{FIRST}").status_code == 200

    for table in TABLES:
        for row in stored(table):
            assert SENTINEL not in json.dumps(row, default=str), table
    assert SENTINEL not in client.get("/conversations").text
    assert SENTINEL not in _leaked(caplog)


# The dead-id rule, through the real endpoint


def test_a_turn_queued_before_the_delete_is_discarded(client) -> None:
    """The interleaving the rule exists for, arranged rather than waited
    for: the writer is demonstrably parked in front of the transaction
    that would write the turn when the deletion commits.

    Absence of the row cannot be the tombstone, because absence is also
    the ordinary state before a first turn, so the deletion says what it
    took and the writer discards. The session is untouched and still
    recording, which is what makes this the thread's own rule rather
    than the session tombstone doing the work.
    """
    gate = Gate()
    store = ConversationStore(
        DatabaseConfig(), now=lambda: NOW, retention_days=0, gate=gate
    )
    store.start()
    try:
        store.open_session("alpha", 100.0, manifest())
        gate.wait()
        gate.let_through()
        store.record_turn(
            "alpha",
            TurnRecord(at=101.0, conversation=FIRST, agent="sam", heard="before", reply="."),
        )
        gate.wait()
        gate.let_through()
        until(lambda: stored("turns"), "the first turn never landed")

        # Enqueued while the writer is parked, so the deletion below is
        # guaranteed to land between what is committed and what is not.
        in_flight = store.record_turn(
            "alpha",
            TurnRecord(at=102.0, conversation=FIRST, agent="sam", heard="in flight", reply="."),
        )
        gate.wait()

        assert client.delete(f"/conversations/{FIRST}").status_code == 200

        gate.open_forever()
        assert in_flight.wait(TIMEOUT_S) is False

        # And a turn produced after it, which the runtime has no reason
        # to stop producing: the conversation in the room carries on and
        # its record does not.
        after = store.record_turn(
            "alpha",
            TurnRecord(at=103.0, conversation=FIRST, agent="sam", heard="after", reply="."),
        )
        assert after.wait(TIMEOUT_S) is False
        store.close_session("alpha", duration_s=8.0, reason="client")
    finally:
        store.stop()

    # The session is still here, and no row of the thread came back.
    assert [row["session"] for row in stored("sessions")] == ["alpha"]
    assert stored("conversations") == []
    assert stored("turns") == []


def test_a_thread_deleted_and_spoken_to_again_stays_deleted(client) -> None:
    """The same rule without the gate, over a whole session of its own:
    an id this writer materialized and a deletion has named is never
    materialized again, however many turns arrive for it."""
    store = ConversationStore(DatabaseConfig(), now=lambda: NOW, retention_days=0)
    store.start()
    try:
        store.open_session("alpha", 100.0, manifest())
        landed = store.record_turn(
            "alpha",
            TurnRecord(at=101.0, conversation=FIRST, agent="sam", heard="before", reply="."),
        )
        assert landed.wait(TIMEOUT_S) is True

        assert client.delete(f"/conversations/{FIRST}").status_code == 200

        for index in range(3):
            answered = store.record_turn(
                "alpha",
                TurnRecord(
                    at=102.0 + index,
                    conversation=FIRST,
                    agent="sam",
                    heard=f"and {index}",
                    reply=".",
                ),
            )
            assert answered.wait(TIMEOUT_S) is False
        # A thread nobody deleted still lands, in the same session, from
        # the same writer.
        other = store.record_turn(
            "alpha",
            TurnRecord(at=110.0, conversation=SECOND, agent="sam", heard="a live one", reply="."),
        )
        assert other.wait(TIMEOUT_S) is True
        store.close_session("alpha", duration_s=9.0, reason="client")
    finally:
        store.stop()

    assert [row["conversation"] for row in stored("conversations")] == [SECOND]
    assert client.get(f"/conversations/{FIRST}").status_code == 404
