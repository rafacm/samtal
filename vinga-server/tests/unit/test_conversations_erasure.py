"""Erasing a session, and what erasure means to everything it fed.

Two deletion surfaces on one helper: the addressed form a user
interface and the noun grammar want, and the selector purge the retired
`conversations purge` command settled the semantics of (#282). They are
driven through the real routes here, because what is under test is a
transaction opened per request against a store no writer is holding
open, which is what a deployment with recording off has.

What the file is about, beyond the round trip:

- **Erasure outranks every copy the store derived.** A title is the
  first turn's utterance, a milestone is a summary of turns, and
  `last_active_at` is when the newest turn landed. Deleting the turns
  behind any of them has to move or remove it, and the sentinel test is
  the proof: a credential-shaped utterance that became a title is hunted
  through every table and every read surface after its session goes.
- **A purge names less than everything.** The three selectors combine
  with AND, the day is strict, and a purge that named nothing is refused
  rather than answered with the whole store.
- **Nothing a caller sends is quoted back.** The selectors and the
  session id are the only values these routes are handed, and the
  refusals name the rule instead of the value.
- **A deletion and the writer are ordered, in both directions.** The
  ids a deletion took are published after it commits and inside
  `store.erasure_order()`, and the writer reads them inside its own
  durable transaction on every attempt. So a deletion that does not
  commit leaves the thread alive, and one that commits while the writer
  is between two attempts of the same batch is still seen by the
  second.
"""

import contextlib
import datetime as dt
import json
import logging
from collections.abc import Iterator, Sequence
from dataclasses import replace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.support.configs import DEVICE_MAC
from tests.support.problems import refused
from tests.support.sessions import WRITER_TIMEOUT_S as TIMEOUT_S
from tests.support.sessions import Gate, until
from tests.support.stores import holding_the_write_lock, the_lock_held
from vinga_server import logs
from vinga_server.config.api import build_api
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations.records import MilestoneRecord, TurnRecord
from vinga_server.conversations.store import (
    CONVERSATIONS_CHAIN,
    ConversationStore,
    Half,
)
from vinga_server.db import read_engine

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# Shaped like something an operator would be horrified to find surviving
# a deletion, and so that a substring check for it cannot match by
# accident.
SENTINEL = "sk-test-1d4a90fe-never-a-real-credential"

OTHER_DEVICE = "11:22:33:44:55:66"

FIRST = "9f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5"

SECOND = "1a2b3c4d5e6f708192a3b4c5d6e7f809"

# Every table the store owns, for the sentinel hunt: what a deletion
# leaves behind is not a question about one table.
TABLES = (
    "sessions",
    "conversations",
    "turns",
    "tool_invocations",
    "conversation_milestones",
    "events",
)


def manifest(device: str = DEVICE_MAC.lower(), started_at: str | None = None) -> dict[str, Any]:
    return {
        "started_at": started_at or "2026-08-15T10:00:00+00:00",
        "server": {"version": "0.1.0", "revision": "abc1234"},
        "device": {"mac": device, "client": "test"},
        "protocol": "1",
        "agent": "sam",
        "agents": ["sam"],
        "providers": {"llm": {"name": "claude", "type": "anthropic"}},
    }


def a_turn(conversation: str = FIRST, heard: str | None = "turn the light on") -> TurnRecord:
    return TurnRecord(
        at=101.2, conversation=conversation, agent="sam", heard=heard, reply="Done."
    )


@pytest.fixture
def api() -> FastAPI:
    return build_api(TOKEN, DatabaseConfig())


@pytest.fixture
def client(api: FastAPI) -> TestClient:
    return TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"})


class Recorder:
    """A store driven the way the server drives one, and let go of.

    Through the real `ConversationStore` rather than through inserts, so
    what a deletion meets is what the writer produces: titles derived,
    activity moved, and the threads and sessions crossing each other the
    way two real conversations do.
    """

    def __init__(self, at: dt.datetime) -> None:
        self.at = at
        self.store = ConversationStore(
            DatabaseConfig(), now=lambda: self.at, retention_days=0
        )
        self.store.start()

    def session(
        self, name: str, device: str = DEVICE_MAC.lower(), started_at: str | None = None
    ) -> None:
        self.store.open_session(name, 100.0, manifest(device, started_at))

    def turn(
        self, session: str, conversation: str = FIRST, heard: str | None = "hello"
    ) -> None:
        self.store.record_turn(session, a_turn(conversation, heard))

    def checkpoint(
        self,
        session: str,
        covered: Sequence[int],
        parent: int | None,
        body: str,
        conversation: str = FIRST,
    ) -> int:
        """One recap checkpoint, written the way the runtime writes one:
        through the store, on the durable path, during the session that
        consented to it. Answers its row id, which is what the next
        recap names as its `parent`."""
        landed = self.store.record_milestone(
            session,
            MilestoneRecord(
                conversation=conversation,
                covered=tuple(covered),
                parent=parent,
                text=body,
            ),
        )
        assert landed.wait(TIMEOUT_S), "the checkpoint never landed"
        return int(stored("conversation_milestones")[-1]["id"])

    def event(self, session: str) -> None:
        self.store.record_event(session, "heard", logging.INFO, {"duration_s": 1.0}, 101.0)

    def close(self, name: str) -> None:
        self.store.close_session(name, duration_s=2.0, reason="client")

    def move_to(self, at: dt.datetime) -> None:
        """Wind the writer's clock forward, with everything before it
        committed.

        A fresh store rather than a mutated lambda, because the writer
        is a thread: a clock changed while records are still queued
        stamps some of them with the new reading and some with the old,
        and which is which is a race. Stopping drains, so what follows
        is stamped later than everything before it by construction.
        """
        self.store.stop()
        self.at = at
        self.store = ConversationStore(
            DatabaseConfig(), now=lambda: self.at, retention_days=0
        )
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


def _leaked(caplog: pytest.LogCaptureFixture) -> str:
    """Everything this server logged, in both shipped formats.

    This server's own channels, and deliberately not every record in the
    process: the HTTP client making these requests logs the URL it asked
    for, which is the caller's own terminal rather than anything this
    server wrote, and it is here only because a TestClient is what
    stands in for curl.
    """
    return "".join(
        record.getMessage() + str(record.__dict__) + logs.JsonFormatter().format(record)
        for record in caplog.records
        if record.name.startswith("vinga_server")
    )


def erase(client: TestClient, session: str) -> dict[str, int]:
    response = client.delete(f"/sessions/{session}")
    assert response.status_code == 200, response.text
    return response.json()


def purge(client: TestClient, **selectors: str) -> dict[str, int]:
    response = client.request("DELETE", "/sessions", params=selectors)
    assert response.status_code == 200, response.text
    return response.json()


def erase_thread(client: TestClient, conversation: str) -> dict[str, int]:
    response = client.delete(f"/conversations/{conversation}")
    assert response.status_code == 200, response.text
    return response.json()


@contextlib.contextmanager
def never_committing(api: FastAPI) -> Iterator[None]:
    """A deletion that does everything but commit.

    The eraser the routes are given is replaced by one that opens the
    real transaction, hands it over, and then fails before it can be
    saved, which is what a database refusing a commit leaves behind: the
    statements ran, the rows are unchanged, and the request is a 500.
    Replaced at the runtime rather than inside the store, so the failure
    happens exactly where the endpoint's own boundary is.
    """
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


# The addressed erasure


def test_a_deleted_session_takes_its_rows_and_its_thread_with_it(client) -> None:
    """One session, one thread, nothing shared: the whole of it goes,
    and the thread goes with it because a thread with no turns is a
    title and two timestamps."""
    recording = Recorder(dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC))
    recording.session("alpha")
    recording.event("alpha")
    recording.turn("alpha", FIRST, "what is the weather like")
    recording.close("alpha")
    recording.done()

    taken = erase(client, "alpha")

    assert taken == {
        "sessions": 1,
        "turns": 1,
        "tool_invocations": 0,
        "events": 1,
        "conversations": 1,
        "milestones": 0,
    }
    for table in TABLES:
        assert stored(table) == [], table


def test_an_unknown_session_is_a_404_naming_no_value(client) -> None:
    """Addressed and not there. The id arrived in the path and is not
    repeated: what a caller needs is the reason a row it expected is
    not there."""
    response = client.delete("/sessions/nothing-of-that-id")

    assert response.status_code == 404
    detail = refused(response.json(), 404)
    assert "nothing-of-that-id" not in response.text
    assert "no session of that id" in detail.lower()


def test_a_deletion_needs_the_token(api) -> None:
    """The gate is in front of routing, so an erasure is behind it for
    the same reason every read is."""
    with TestClient(api) as anonymous:
        assert anonymous.delete("/sessions/alpha").status_code == 401
        assert anonymous.request("DELETE", "/sessions").status_code == 401


# What a deletion has to move, and what it has to remove


def test_a_live_thread_keeps_its_other_turns_and_is_renamed(client) -> None:
    """The case the whole cascade exists for: one thread spanning two
    sessions, the older session erased.

    The thread survives with the turns it still has, and the title
    follows, because a title IS the first utterance and the utterance it
    was is gone. `last_active_at` is left where it is, since the turn
    that wrote it is still there.
    """
    began = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)
    recording = Recorder(began)
    recording.session("alpha")
    recording.turn("alpha", FIRST, "the first thing said")
    recording.close("alpha")
    recording.move_to(began + dt.timedelta(hours=3))
    recording.session("beta", started_at=(began + dt.timedelta(hours=3)).isoformat())
    recording.turn("beta", FIRST, "and the thing said later")
    recording.close("beta")
    recording.done()
    before = stored("conversations")[0]

    taken = erase(client, "alpha")

    assert (taken["sessions"], taken["turns"], taken["conversations"]) == (1, 1, 0)
    (thread,) = stored("conversations")
    assert thread["title"] == "and the thing said later"
    # Untouched: the newest turn is the one that wrote it and it is
    # still here, so there is nothing to recompute.
    assert thread["last_active_at"] == before["last_active_at"]
    assert [turn["session"] for turn in stored("turns")] == ["beta"]


def test_activity_moves_back_when_the_turn_that_wrote_it_is_erased(client) -> None:
    """The other half, and the honest half. A turn carries its offset
    from its session's open and no wall clock of its own, so once the
    newest turn is gone the exact instant is not a stored fact: the
    stamp falls back to when the surviving turns' sessions began, which
    is never later than the truth."""
    began = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)
    recording = Recorder(began)
    recording.session("alpha")
    recording.turn("alpha", FIRST, "the first thing said")
    recording.close("alpha")
    later = began + dt.timedelta(hours=3)
    recording.move_to(later)
    recording.session("beta", started_at=later.isoformat())
    recording.turn("beta", FIRST, "and the thing said later")
    recording.close("beta")
    recording.done()

    erase(client, "beta")

    (thread,) = stored("conversations")
    assert thread["title"] == "the first thing said"
    # The thread's own beginning, which is the floor: it is later than
    # the surviving session's `started_at` and never later than the
    # instant the surviving turn really landed.
    assert thread["last_active_at"] == began.isoformat()
    # And never before the thread began, which is the floor that keeps
    # the pair ordered.
    assert thread["created_at"] <= thread["last_active_at"]


def test_a_thread_whose_surviving_turns_have_no_text_loses_its_title(client) -> None:
    """The other end of the title rule. A title IS the first utterance
    bounded, so a thread whose earliest surviving turn stored none has
    nothing to be called: the name is removed rather than left pointing
    at an utterance that is gone.

    Text-off is how a turn comes to have no utterance stored, and a
    deployment can change that switch between two sessions of one
    thread, which is what makes this reachable rather than theoretical.
    """
    recording = Recorder(dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC))
    recording.session("alpha")
    recording.turn("alpha", FIRST, "the utterance the title came from")
    recording.close("alpha")
    recording.done()
    quiet = ConversationStore(
        DatabaseConfig(),
        now=lambda: dt.datetime(2026, 8, 15, 13, 0, tzinfo=dt.UTC),
        retention_days=0,
        text=False,
    )
    quiet.start()
    quiet.open_session("beta", 100.0, manifest())
    quiet.record_turn("beta", a_turn(FIRST, "said but never stored"))
    quiet.close_session("beta", duration_s=1.0, reason="client")
    quiet.stop()
    assert stored("conversations")[0]["title"] == "the utterance the title came from"

    erase(client, "alpha")

    (thread,) = stored("conversations")
    assert thread["title"] is None
    assert len(stored("turns")) == 1


def test_a_greeting_is_not_a_name_and_the_utterance_after_it_is(client) -> None:
    """The rule a landing applies, applied again here so the two cannot
    disagree. A thread a session moved onto opens with the answer that
    greeted the move, and nothing was heard on that turn: the name comes
    from the earliest utterance the thread does hold, before the erasure
    and after it."""
    began = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)
    recording = Recorder(began)
    recording.session("alpha")
    recording.turn("alpha", FIRST, "the first thing said")
    recording.close("alpha")
    recording.move_to(began + dt.timedelta(hours=3))
    recording.session("beta", started_at=(began + dt.timedelta(hours=3)).isoformat())
    # The move onto this thread, then what was said on it afterwards.
    recording.turn("beta", FIRST, None)
    recording.turn("beta", FIRST, "and the thing said later")
    recording.close("beta")
    recording.done()
    assert stored("conversations")[0]["title"] == "the first thing said"

    erase(client, "alpha")

    (thread,) = stored("conversations")
    assert thread["title"] == "and the thing said later"


def test_only_the_thread_the_deletion_touched_is_recomputed(client) -> None:
    """A session that talked to two agents holds two threads, and the
    one whose turns are elsewhere is not renamed, not moved and not
    counted."""
    recording = Recorder(dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC))
    recording.session("alpha")
    recording.turn("alpha", FIRST, "the first thread")
    recording.close("alpha")
    recording.session("beta")
    recording.turn("beta", SECOND, "the second thread")
    recording.close("beta")
    recording.done()
    untouched = {row["conversation"]: dict(row) for row in stored("conversations")}[SECOND]

    erase(client, "alpha")

    (thread,) = stored("conversations")
    assert thread == untouched


def a_summarized_thread(sentinel: str = SENTINEL) -> tuple[int, int, int]:
    """One thread with three turns and three real checkpoints on it,
    written the way a session that consented to a recap writes them.

    The shape is the one the lineage exists for. The parent covers the
    turn that is about to be erased and quotes it; the child covers only
    surviving turns and says nothing of it, but consumed the parent, so
    what the parent held reached the child. The third covers survivors
    and consumed nothing, which is the row that must not go.

    The checkpoints are written from a third session, because that is
    when a recap happens: somebody resumed the thread later and said
    yes.
    """
    recording = Recorder(dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC))
    recording.session("alpha")
    recording.turn("alpha", FIRST, sentinel)
    recording.close("alpha")
    recording.session("beta")
    recording.turn("beta", FIRST, "the one after it")
    recording.turn("beta", FIRST, "and one more")
    recording.close("beta")
    recording.done()
    ids = [turn["id"] for turn in stored("turns")]

    later = Recorder(dt.datetime(2026, 8, 16, 12, 0, tzinfo=dt.UTC))
    later.session("gamma")
    parent = later.checkpoint("gamma", [ids[0]], None, f"we talked about {sentinel}")
    child = later.checkpoint("gamma", ids[1:3], parent, "and then about the weather")
    unrelated = later.checkpoint("gamma", ids[1:3], None, "covers only survivors")
    later.close("gamma")
    later.done()
    return parent, child, unrelated


def test_a_milestone_covering_an_erased_turn_dies_with_its_lineage(client) -> None:
    """A summary of erased content is that content, whether it arrived
    directly or through an earlier recap the summarizer consumed. The
    parent's coverage holds the erased turn; the child holds only the
    parent, and both go."""
    parent, child, unrelated = a_summarized_thread()

    taken = erase(client, "alpha")

    assert taken["milestones"] == 2
    assert [row["id"] for row in stored("conversation_milestones")] == [unrelated]
    assert parent not in {row["id"] for row in stored("conversation_milestones")}
    assert child not in {row["id"] for row in stored("conversation_milestones")}


def test_a_recap_of_a_recap_leaves_no_trace_of_the_turn_it_stood_for(client) -> None:
    """The transitive sentinel. A turn that survives only through a
    checkpoint somebody later summarized again is content that reached
    the second checkpoint without a word of it being copied there, so
    absence of the text is not the test: the lineage is.

    Hunted afterwards through every table the store owns and every read
    surface that serves them, which is the same hunt the derived title
    gets and for the same reason.
    """
    _, child, unrelated = a_summarized_thread()
    # Where it belongs, before the deletion: the utterance, the title it
    # became, and the recap that quoted it.
    assert stored("conversations")[0]["title"] == SENTINEL
    assert any(SENTINEL in (row["text"] or "") for row in stored("conversation_milestones"))
    # And where it never was: the descendant carries no word of it.
    (descendant,) = [row for row in stored("conversation_milestones") if row["id"] == child]
    assert SENTINEL not in (descendant["text"] or "")

    erase(client, "alpha")

    assert [row["id"] for row in stored("conversation_milestones")] == [unrelated]
    for table in TABLES:
        for row in stored(table):
            assert SENTINEL not in json.dumps(row, default=str), table
    for path in ("/sessions", "/conversations", f"/conversations/{FIRST}",
                 f"/conversations/{FIRST}/turns"):
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert SENTINEL not in response.text


def test_a_thread_that_loses_every_turn_loses_its_milestones_too(client) -> None:
    """Deleted whole means whole. A checkpoint left behind would be a
    summary of a conversation that is not there any more."""
    recording = Recorder(dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC))
    recording.session("alpha")
    recording.turn("alpha", FIRST, "the only thing said")
    recording.close("alpha")
    recording.done()
    ids = [turn["id"] for turn in stored("turns")]
    later = Recorder(dt.datetime(2026, 8, 16, 12, 0, tzinfo=dt.UTC))
    later.session("beta")
    later.checkpoint("beta", [ids[0]], None, "a recap")
    later.close("beta")
    later.done()

    taken = erase(client, "alpha")

    assert taken["conversations"] == 1
    assert taken["milestones"] == 1
    assert stored("conversation_milestones") == []


def test_the_planted_title_is_gone_from_every_table_and_surface(client) -> None:
    """The reviewer's test. A credential-shaped utterance becomes the
    thread's title, which is a copy of it in a second table, and the
    session it was said in is then erased.

    Hunted afterwards through every table the store owns and through
    the read surfaces that serve them, because a deletion that left the
    derived copy behind would have left exactly the thing somebody asked
    to have removed.
    """
    recording = Recorder(dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC))
    recording.session("alpha")
    recording.turn("alpha", FIRST, SENTINEL)
    recording.turn("alpha", FIRST, "and something ordinary")
    recording.close("alpha")
    recording.done()
    # Where it belongs, before the deletion: the utterance and the title
    # it became.
    assert stored("conversations")[0]["title"] == SENTINEL

    erase(client, "alpha")

    for table in TABLES:
        for row in stored(table):
            assert SENTINEL not in json.dumps(row, default=str), table
    listing = client.get("/sessions")
    assert listing.status_code == 200
    assert SENTINEL not in listing.text
    assert listing.json()["items"] == []


def test_a_session_deleted_while_it_is_talking_stops_being_recorded(client) -> None:
    """The tombstone rule, driven through the real endpoint.

    The writer confirms its session still exists at the start of every
    durable transaction, so a deletion that lands between two markers is
    final rather than a race the next turn undoes. The gate is what
    makes the interleaving an arrangement rather than a wait: the second
    turn is enqueued and the writer is demonstrably parked in front of
    the transaction that would write it when the deletion commits.

    The conversation itself carries on to its natural end, because
    neither the runtime nor the device edge knows a deletion happened.
    What it says afterwards is not recorded, which is what erasure
    means, and the acknowledgement is what lets a caller tell that from
    a slow write.
    """
    gate = Gate()
    store = ConversationStore(
        DatabaseConfig(),
        now=lambda: dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC),
        retention_days=0,
        gate=gate,
    )
    store.start()
    try:
        store.open_session("alpha", 100.0, manifest())
        gate.wait()
        gate.let_through()
        store.record_turn("alpha", a_turn(FIRST, "before the deletion"))
        gate.wait()
        gate.let_through()
        until(lambda: stored("turns"), "the first turn never landed")

        # Enqueued while the writer is parked, so the deletion below is
        # guaranteed to land between what is committed and what is not.
        in_flight = store.record_turn("alpha", a_turn(FIRST, "already on its way"))
        gate.wait()

        taken = erase(client, "alpha")
        assert (taken["sessions"], taken["turns"], taken["conversations"]) == (1, 1, 1)

        gate.open_forever()
        assert in_flight.wait(TIMEOUT_S) is False

        # And the conversation finishes the way a real one would.
        after = store.record_turn("alpha", a_turn(FIRST, "and after that"))
        store.close_session("alpha", duration_s=8.0, reason="client")
    finally:
        store.stop()

    assert after.wait(TIMEOUT_S) is False
    for table in TABLES:
        assert stored(table) == [], table


def test_a_thread_a_session_erasure_took_is_never_written_to_again(client) -> None:
    """The dead-id rule, reached through the session endpoint.

    A thread whose every turn was in one session is deleted whole when
    that session goes, and the session that is still running was not
    the one deleted, so the tombstone does not fire and nothing else
    would stop the next turn from inserting the row again with a title
    derived from whatever is said next.

    Absence cannot be the tombstone here, because absence is also the
    ordinary state of a thread before its first turn. So the deletion
    says what it took, and the writer discards the turns of an id it
    wrote and a deletion has since named.
    """
    gate = Gate()
    store = ConversationStore(
        DatabaseConfig(),
        now=lambda: dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC),
        retention_days=0,
        gate=gate,
    )
    store.start()
    try:
        # The session the thread began in, closed and left behind.
        store.open_session("alpha", 100.0, manifest())
        gate.wait()
        gate.let_through()
        store.record_turn("alpha", a_turn(FIRST, "the whole of this thread"))
        gate.wait()
        gate.let_through()
        store.close_session("alpha", duration_s=2.0, reason="client")
        gate.wait()
        gate.let_through()
        # And the session still talking on it.
        store.open_session("beta", 200.0, manifest())
        gate.wait()
        gate.let_through()
        until(lambda: stored("sessions"), "the sessions never landed")

        # Queued while the writer is parked, so the deletion below lands
        # between what is committed and what is not.
        in_flight = store.record_turn("beta", a_turn(FIRST, "already on its way"))
        gate.wait()

        taken = erase(client, "alpha")
        assert (taken["sessions"], taken["turns"], taken["conversations"]) == (1, 1, 1)

        gate.open_forever()
        assert in_flight.wait(TIMEOUT_S) is False

        # And the one produced after it, which the runtime has no reason
        # to stop producing.
        after = store.record_turn("beta", a_turn(FIRST, "and after that"))
        assert after.wait(TIMEOUT_S) is False
        store.close_session("beta", duration_s=8.0, reason="client")
    finally:
        store.stop()

    # The session that was not deleted is still here; the thread is not,
    # and no turn of it came back.
    assert [row["session"] for row in stored("sessions")] == ["beta"]
    assert stored("conversations") == []
    assert stored("turns") == []


def test_a_thread_this_writer_never_wrote_is_a_first_turn(client) -> None:
    """The other half of the distinction. An id nothing in this process
    has written is a thread before its first turn, whatever some other
    deletion said about some other id, so it materializes as designed."""
    recording = Recorder(dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC))
    recording.session("alpha")
    recording.turn("alpha", FIRST, "the thread that goes")
    recording.close("alpha")
    recording.done()
    erase(client, "alpha")

    fresh = ConversationStore(
        DatabaseConfig(),
        now=lambda: dt.datetime(2026, 8, 15, 13, 0, tzinfo=dt.UTC),
        retention_days=0,
    )
    fresh.start()
    fresh.open_session("beta", 100.0, manifest())
    landed = fresh.record_turn("beta", a_turn(SECOND, "a thread nobody deleted"))
    assert landed.wait(TIMEOUT_S) is True
    fresh.close_session("beta", duration_s=1.0, reason="client")
    fresh.stop()

    (thread,) = stored("conversations")
    assert thread["conversation"] == SECOND
    assert thread["title"] == "a thread nobody deleted"


def test_a_deletion_between_two_attempts_is_read_by_the_second(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interval a durable transaction that was made again opens.

    A batch whose transaction met a lock that did not arrive is tried
    again, and a deletion fits between the two attempts exactly as it
    fits before the first: it takes the chain's lock the moment the
    failed attempt lets go of it, commits, and publishes what it took.
    An attempt that read the ids before the marker began would have read
    them once, before any of this, and would then write the turn onto a
    thread that no longer exists.

    So the read is inside the transaction and happens on every attempt.
    The contention is real rather than injected: the lock is held by a
    second connection for as long as the first attempt lasts, and the
    writer parks in front of each attempt, which is what makes the
    ordering an arrangement rather than a race.
    """
    gate = Gate()
    # The engine has to be opened under the shortened timeout, so the
    # store is built inside this and not before it.
    with holding_the_write_lock(monkeypatch, CONVERSATIONS_CHAIN):
        store = ConversationStore(
            DatabaseConfig(),
            now=lambda: dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC),
            retention_days=0,
            gate=gate,
        )
        store.start()
        try:
            store.open_session("alpha", 100.0, manifest())
            gate.wait()
            gate.let_through()
            store.record_turn("alpha", a_turn(FIRST, "the whole of this thread"))
            gate.wait()
            gate.let_through()
            until(lambda: stored("turns"), "the first turn never landed")

            in_flight = store.record_turn("alpha", a_turn(FIRST, "already on its way"))
            # Parked in front of the first attempt, which is then let
            # into a lock somebody else is holding and gives up.
            gate.wait()
            with the_lock_held(CONVERSATIONS_CHAIN):
                gate.let_through()
                # Arriving here is the first attempt having failed: the
                # writer is parked in front of the second one.
                gate.wait()

            taken = erase_thread(client, FIRST)
            assert (taken["conversations"], taken["turns"]) == (1, 1)

            gate.open_forever()
            assert in_flight.wait(TIMEOUT_S) is False
            store.close_session("alpha", duration_s=8.0, reason="client")
        finally:
            store.stop()

    # The session it was spoken in is still here; the thread is not, and
    # the attempt that ran after the deletion wrote none of it back.
    assert [row["session"] for row in stored("sessions")] == ["alpha"]
    assert stored("conversations") == []
    assert stored("turns") == []


def test_a_deletion_that_never_commits_leaves_the_thread_alive(api, client) -> None:
    """The other direction, and the one that has no undo.

    Marking an id dead is for the rest of the process: nothing revives
    it, and every later turn of that thread is discarded with a false
    acknowledgement. So it may only be said of a deletion that actually
    happened. A publication from inside the transaction says it of one
    that then rolls back, and the thread is alive in the database and
    dead to the writer, which is the worst of both.

    The proof is the conversation carrying on afterwards: the row is
    where it was, and the turns that follow land and are acknowledged
    true, which is what a caller waiting on one is entitled to believe.
    """
    store = ConversationStore(
        DatabaseConfig(),
        now=lambda: dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC),
        retention_days=0,
    )
    store.start()
    try:
        store.open_session("alpha", 100.0, manifest())
        first = store.record_turn("alpha", a_turn(FIRST, "before the deletion"))
        assert first.wait(TIMEOUT_S) is True

        with never_committing(api):
            response = client.delete(f"/conversations/{FIRST}")
        assert response.status_code == 500

        # The row the deletion did not take.
        assert [row["conversation"] for row in stored("conversations")] == [FIRST]

        # And the thread is still a thread: what is said next is
        # recorded, and the answer says so.
        after = store.record_turn("alpha", a_turn(FIRST, "and after that"))
        assert after.wait(TIMEOUT_S) is True
        store.close_session("alpha", duration_s=8.0, reason="client")
    finally:
        store.stop()

    assert [row["heard"] for row in stored("turns")] == [
        "before the deletion",
        "and after that",
    ]



def test_a_deletion_between_the_two_halves_leaves_no_orphan_events(client) -> None:
    """The other interval a deletion can land in, and the only one that
    can produce a row nothing will ever prune.

    A marker commits twice, and the writer holds no lock between the
    two. So a deletion can arrive after the turns are durable and before
    the events are, and an events row has no foreign key to refuse it:
    retention reaches events through the session rows that still exist,
    which is exactly what this deletion took. The batch has to meet the
    tombstone inside its own transaction, and be dropped.

    The gate is on the events half here rather than on the durable one,
    because the interval under test is the one between them.
    """
    gate = Gate(Half.EVENTS)
    store = ConversationStore(
        DatabaseConfig(),
        now=lambda: dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC),
        retention_days=0,
        gate=gate,
    )
    store.start()
    try:
        store.open_session("alpha", 100.0, manifest())
        store.record_event("alpha", "heard", logging.INFO, {"duration_s": 1.0}, 101.0)
        store.record_turn("alpha", a_turn(FIRST, "before the deletion"))
        # The durable half of that marker is committed and the events
        # half has not begun, which is the whole of the arrangement.
        gate.wait()

        taken = erase(client, "alpha")
        assert (taken["sessions"], taken["turns"], taken["events"]) == (1, 1, 0)

        gate.open_forever()
        store.close_session("alpha", duration_s=8.0, reason="client")
    finally:
        store.stop()

    for table in TABLES:
        assert stored(table) == [], table



# The purge and its three selectors


def test_a_purge_by_session_is_the_addressed_deletion(client) -> None:
    """The deliberate overlap. Both forms go through one helper, so a
    bare `session=` purge and the addressed delete are the same act
    reached two ways."""
    recording = Recorder(dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC))
    recording.session("alpha")
    recording.turn("alpha", FIRST)
    recording.close("alpha")
    recording.session("beta")
    recording.turn("beta", SECOND)
    recording.close("beta")
    recording.done()

    taken = purge(client, session="alpha")

    assert (taken["sessions"], taken["turns"], taken["conversations"]) == (1, 1, 1)
    assert [row["session"] for row in stored("sessions")] == ["beta"]


def test_a_purge_by_device_takes_that_boards_sessions(client) -> None:
    recording = Recorder(dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC))
    recording.session("alpha")
    recording.turn("alpha", FIRST)
    recording.close("alpha")
    recording.session("beta", device=OTHER_DEVICE)
    recording.turn("beta", SECOND)
    recording.close("beta")
    recording.done()

    taken = purge(client, device=OTHER_DEVICE.upper())

    assert taken["sessions"] == 1
    assert [row["session"] for row in stored("sessions")] == ["alpha"]


def test_the_day_selector_is_strict_about_its_own_day(client) -> None:
    """A session that began at any moment of the named day survives,
    which is what an operator means by "before the fifteenth"."""
    recording = Recorder(dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC))
    recording.session("old", started_at="2026-08-14T23:59:59+00:00")
    recording.close("old")
    recording.session("midnight", started_at="2026-08-15T00:00:00+00:00")
    recording.close("midnight")
    recording.session("later", started_at="2026-08-15T10:00:00+00:00")
    recording.close("later")
    recording.done()

    taken = purge(client, before="2026-08-15")

    assert taken["sessions"] == 1
    assert {row["session"] for row in stored("sessions")} == {"midnight", "later"}


def test_selectors_combine_so_every_one_of_them_has_to_match(client) -> None:
    """AND, not OR: a purge always names less than each selector alone
    would, which is the safe direction for something with no undo."""
    recording = Recorder(dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC))
    recording.session("old-here", started_at="2026-08-01T10:00:00+00:00")
    recording.close("old-here")
    recording.session("old-there", OTHER_DEVICE, "2026-08-01T10:00:00+00:00")
    recording.close("old-there")
    recording.session("new-here", started_at="2026-08-20T10:00:00+00:00")
    recording.close("new-here")
    recording.done()

    taken = purge(client, device=DEVICE_MAC.lower(), before="2026-08-10")

    assert taken["sessions"] == 1
    assert {row["session"] for row in stored("sessions")} == {"old-there", "new-here"}


def test_a_purge_applies_the_whole_cascade_to_the_set(client) -> None:
    """Every per-session rule, over the set the selectors named, in one
    transaction: the thread spanning the erased sessions is renamed from
    what is left of it, and the one left with nothing is deleted."""
    recording = Recorder(dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC))
    recording.session("old-one", started_at="2026-08-01T10:00:00+00:00")
    recording.turn("old-one", FIRST, "the first thing said")
    recording.turn("old-one", SECOND, "a thread that ends here")
    recording.close("old-one")
    recording.session("old-two", started_at="2026-08-02T10:00:00+00:00")
    recording.turn("old-two", FIRST, "still the first thread")
    recording.close("old-two")
    recording.session("new", started_at="2026-08-20T10:00:00+00:00")
    recording.turn("new", FIRST, "and the newest thing")
    recording.close("new")
    recording.done()

    taken = purge(client, before="2026-08-10")

    assert (taken["sessions"], taken["turns"], taken["conversations"]) == (2, 3, 1)
    (thread,) = stored("conversations")
    assert thread["conversation"] == FIRST
    assert thread["title"] == "and the newest thing"


def test_a_purge_with_no_selector_is_refused(client) -> None:
    """The whole store is not something this endpoint can be asked for.
    A query string that lost its arguments to a shell, a proxy or a typo
    would otherwise erase everything, and there is no undo behind it."""
    recording = Recorder(dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC))
    recording.session("alpha")
    recording.turn("alpha", FIRST)
    recording.close("alpha")
    recording.done()

    response = client.request("DELETE", "/sessions")

    assert response.status_code == 422
    detail = refused(response.json(), 422)
    assert "at least one of session, device or before" in detail
    assert len(stored("sessions")) == 1


@pytest.mark.parametrize(
    ("selector", "value"),
    [("device", "not-a-mac"), ("before", "the fifteenth"), ("before", "20260815")],
)
def test_an_unreadable_selector_is_refused_without_being_quoted(
    client, selector: str, value: str
) -> None:
    """The rule the reads already hold to, on the one surface where
    getting it wrong destroys something: what arrived is the caller's,
    and a refusal that echoed it would be the one place this API prints
    what it was handed."""
    response = client.request("DELETE", "/sessions", params={selector: value})

    assert response.status_code == 422
    detail = refused(response.json(), 422)
    assert value not in response.text
    assert "not quoted back" in detail


def test_a_refused_purge_says_nothing_in_the_log_either(
    client, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refusal is a fixed sentence and the value that caused it is
    dropped rather than carried: no exception text, no chain, and
    nothing on either stream."""
    with caplog.at_level(logging.DEBUG):
        response = client.request("DELETE", "/sessions", params={"before": SENTINEL})
        assert refused(response.json(), 422)
        assert SENTINEL not in response.text

    rendered = _leaked(caplog)
    captured = capsys.readouterr()
    assert SENTINEL not in rendered
    assert SENTINEL not in captured.out
    assert SENTINEL not in captured.err
