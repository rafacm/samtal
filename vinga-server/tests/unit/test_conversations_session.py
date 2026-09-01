"""What a real conversation leaves in the store.

The store's own suites drive its seams with hand-built records. This one
holds conversations over a websocket against a booted server and reads
the file afterwards, which is the only place three claims can be checked
at once: that the session row is the same shape as the capture's
manifest, that the events rows are the decision track verbatim, and that
the turn rows and the event rows agree about what happened.

The last of those is the "two sources of one truth" risk the plan names.
A turn is assembled by the reply path while the events flow through the
tap, so the two could drift; the cross-check below makes that a test
failure rather than a discovery during an investigation.

Everything about a wedged database is deterministic rather than timed:
the writer parks on the injected gate, the producer's bound is moved to
zero, and a raising engine is swapped in at a marker. A wall clock is
used for the one thing it has to be, the assertion that a reply does not
wait for any of it.
"""

import asyncio
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from starlette.websockets import WebSocketDisconnect

from tests.support.configs import DEVICE_MAC, DEVICE_UUID, recording_config, world
from tests.support.providers import built_world
from tests.support.sessions import WRITER_TIMEOUT_S as TIMEOUT_S
from tests.support.sessions import Gate, attached_taps, drive_reply, open_session, until
from tests.support.sockets import LoopingSocket
from tests.support.stores import memory as lane_memory
from tests.support.wire import connect, say_something, send_pcm, sentences, shake_hands, speech_pcm
from vinga_server.app import create_app
from vinga_server.audio.opus import OpusEncoder
from vinga_server.capture import CaptureStore
from vinga_server.config import Config
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations import store as store_module
from vinga_server.conversations.store import ConversationStore, Half, SessionSink
from vinga_server.db import read_engine
from vinga_server.device import session as session_module
from vinga_server.device.session import DeviceSession
from vinga_server.events import Emission
from vinga_server.events.catalog import carried_values, catalog
from vinga_server.runtime.pipeline import bespoke_runtime_factory
from vinga_server.tools.mcp import McpServers

# A value that has no business anywhere in the file when text storage is
# off, shaped like something an operator would be horrified to find.
SENTINEL = "hunter2-not-a-real-credential-9f31c7"

# The same, for an exception message, since a failure report must carry
# a class name and nothing else.
POISON = "sk-poison-4b1e-never-a-real-credential"

# One frame of silence, which the mock ASR answers with the configured
# transcript.
UTTERANCE = b"\x00\x00" * 320


def read(statement: str) -> list[dict[str, Any]]:
    """Whatever is committed right now, read the way anything else reads
    a live store: a second connection, no migration, no advisory lock.

    The statement is written against the store's schema, which is where
    its tables are: `select * from sessions` would find nothing, and
    that is the point of the schema rather than an inconvenience.
    """
    engine = read_engine(DatabaseConfig())
    try:
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(statement)).mappings()]
    finally:
        engine.dispose()


def declared_fields() -> dict[str, set[str]]:
    """Every event's declared field names, from its declaration.

    The vocabulary's authority is the declaration (#155): the generated
    reference and the README's index are both rendered from it, and the
    README's own table stopped naming fields when it became a
    name-and-when index. So what the stored field names are checked
    against is the declaration itself rather than a table of prose. The
    base fields are dropped here because the store keeps them on the row
    and on the session rather than in the payload column.

    Read off the catalog itself, which is the one home of what an event
    may carry: recreating that surface here would be exactly the second
    structure the conversion exists to remove.
    """
    return {
        name: {
            one.name
            for variant in declaration.variants
            for one in carried_values(variant)
            if one.name not in {"event", "session", "device"}
        }
        for name, declaration in catalog().items()
    }


class SpyingSink(SessionSink):
    """The sink itself, keeping what it was offered on the way through.

    A spy at the same tap position rather than beside it: attached
    separately it would be one dispatch away from what the store sees,
    and the claim under test is precisely that the rows are what this
    position was handed.
    """

    seen: list[Emission] = []

    def emit(self, emission: Emission) -> None:
        SpyingSink.seen.append(emission)
        super().emit(emission)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> list[Emission]:
    SpyingSink.seen = []
    monkeypatch.setattr(session_module, "SessionSink", SpyingSink)
    return SpyingSink.seen


# The record one conversation leaves


def test_a_conversation_lands_a_session_row_shaped_like_the_manifest(
    tmp_path: Path,
) -> None:
    """One manifest, two consumers. The capture is switched on here so
    that the two records of the same session are compared against each
    other rather than against a hand-written expectation."""
    with TestClient(create_app(recording_config(tmp_path, capture=True))) as client:
        with connect(client) as websocket:
            session_id = shake_hands(websocket)["session_id"]
            say_something(websocket)

    (row,) = read("select * from record.sessions")
    (manifest_file,) = (tmp_path / "captures").glob("*.json")
    manifest = json.loads(manifest_file.read_text())

    assert row["session"] == session_id == manifest["session"]
    assert row["device"] == manifest["device"]["mac"] == DEVICE_MAC.lower()
    assert row["client"] == manifest["device"]["client"] == DEVICE_UUID
    assert row["agent"] == manifest["agent"] == "assistant"
    assert row["agents"] == manifest["agents"] == ["assistant"]
    # The column is TEXT, so the negotiated version reads back as the
    # text of the number the manifest carries.
    assert row["protocol"] == str(manifest["protocol"]) == "1"
    assert row["started_at"] == manifest["started_at"]
    assert row["server_version"] == manifest["server"]["version"]
    assert row["revision"] == manifest["server"]["revision"]
    assert row["providers"] == manifest["providers"]
    # Which way the switches were set for this session, so a null column
    # elsewhere is distinguishable from a column never stored.
    assert (row["metrics"], row["text"]) == (1, 1)
    # And the close, which only this record has: the capture's manifest
    # says whether it completed, this says why it ended.
    assert row["closed_at"] is not None
    assert row["duration_s"] >= 0
    assert row["close_reason"] == "client"
    assert row["dropped"] == 0


def test_the_events_rows_are_the_decision_track_verbatim(
    tmp_path: Path, spy: list[Emission]
) -> None:
    with TestClient(create_app(recording_config(tmp_path))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)

    rows = read("select * from record.events order by id")
    declared = declared_fields()

    # One row per event this tap position was offered, in the order it
    # was offered them: the decision track, session_open through
    # session_closed.
    assert [row["name"] for row in rows] == [e.payload["event"] for e in spy]
    assert rows[0]["name"] == "session_open"
    assert rows[-1]["name"] == "session_closed"

    for row, emission in zip(rows, spy, strict=True):
        fields = row["fields"]
        expected = {
            key: value
            for key, value in emission.payload.items()
            if key not in {"event", "session", "device"}
            and key not in store_module.EVENT_CONTENT.get(row["name"], ())
        }
        # Copied verbatim, names and values alike: the store adds no
        # drift of its own, which is what lets the schema reference point
        # at the event reference instead of restating it.
        assert fields == expected, row["name"]
        assert row["level"] == emission.level
        assert row["t_ms"] >= 0
        # And every name it copied is one the registry declares.
        assert row["name"] in declared, row["name"]
        assert set(fields) <= declared[row["name"]], row["name"]


def test_the_turns_and_their_tool_calls_land_with_their_numbers(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(recording_config(tmp_path))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            first_reply, _ = say_something(websocket)
            second_reply, _ = say_something(websocket)

    turns = read("select * from record.turns order by id")
    calls = read("select * from record.tool_invocations order by id")

    assert len(turns) == 2
    first, second = turns
    assert first["t_ms"] <= second["t_ms"]
    for turn, spoken in zip(turns, (first_reply, second_reply), strict=True):
        assert turn["agent"] == "assistant"
        assert turn["heard"] == "remember that I like tea"
        # What was stored is what the device was told, sentence for
        # sentence.
        assert turn["reply"] == " ".join(sentences(spoken))
        assert turn["legs"] is None, "a single-agent turn has no legs to record"
        # Two rounds: the one that asked for the tool, and the one that
        # spoke the result.
        assert turn["rounds"] == 2
        assert turn["tool_calls"] == 1
        assert turn["heard_duration_s"] > 0
        assert turn["asr_ms"] is not None
        assert turn["llm_ms"] is not None
        assert turn["first_token_ms"] is not None
        assert turn["tts_first_audio_ms"] is not None

    assert [call["turn"] for call in calls] == [first["id"], second["id"]]
    for call in calls:
        assert call["position"] == 0
        assert call["source"] == "builtin"
        assert call["entry"] is None
        assert call["name"] == "remember"
        assert call["arguments"] == {"text": "the user likes tea"}
        assert call["malformed"] == 0
        assert call["is_error"] == 0
        assert call["duration_ms"] is not None


def test_a_real_conversation_lands_a_thread_its_turns_name(tmp_path: Path) -> None:
    """The thread beside the session, from a conversation nobody
    scripted below the wire.

    Two sessions of the same device, so the two halves of the entity are
    both visible: a fresh wake starts a fresh thread, and each session's
    turns name the thread that session opened rather than a thread the
    store invented for them.
    """
    with TestClient(create_app(recording_config(tmp_path))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)

    threads = read("select * from record.conversations order by id")
    turns = read("select * from record.turns order by id")

    assert len(threads) == 2
    for thread in threads:
        assert thread["agent"] == "assistant"
        assert thread["device"] == DEVICE_MAC.lower()
        assert thread["incomplete"] is False
        assert thread["created_at"] == thread["last_active_at"]
        # Derived from the first utterance, which is the whole of the v1
        # rule, and the same utterance the turn row kept.
        assert thread["title"] == "remember that I like tea"
    # Two wakes, two threads, and neither turn belongs to the other's.
    named = [turn["conversation"] for turn in turns]
    assert sorted(named) == sorted(thread["conversation"] for thread in threads)
    assert len(set(named)) == 2


def test_what_a_person_said_becomes_a_title_and_reaches_nothing_else(
    tmp_path: Path, spy: list[Emission], caplog: pytest.LogCaptureFixture
) -> None:
    """A title is derived from what somebody said into a microphone, so
    it is conversation content by the same rule the transcript is.

    Where it belongs: the turn row it was transcribed into, and the
    title the thread took from it. Where it must not be: any event
    field, any row of the decision track, any record this server wrote
    in either format a deployment ships, or any consumer handed the same
    emission. The presence is asserted beside the absence, so a run that
    stored nothing cannot pass this by keeping quiet.

    The sentinel rides the mock ear's configured transcript, which is
    also a provider option and therefore lands in the session row's
    verbatim `providers` column. That is configuration this deployment
    wrote rather than conversation, it is the same column the credential
    case next door already governs, and it is on no surface this test
    makes a claim about.
    """
    config = recording_config(
        tmp_path, asr_text=SENTINEL, llm={"type": "mock", "reply": "Noted."}
    )
    with caplog.at_level("DEBUG"):
        with TestClient(create_app(config)) as client:
            with connect(client) as websocket:
                shake_hands(websocket)
                say_something(websocket)

    (turn,) = read("select * from record.turns")
    (thread,) = read("select * from record.conversations")
    events = read("select * from record.events")

    assert turn["heard"] == SENTINEL
    assert thread["title"] == SENTINEL
    assert turn["conversation"] == thread["conversation"]

    assert [e for e in spy if e.payload["event"] == "heard"], "nothing was heard at all"
    for emission in spy:
        assert SENTINEL not in json.dumps(emission.payload, default=str)
        assert SENTINEL not in repr(emission.args)
    assert events, "the events half of the record is what this claim is about"
    for row in events:
        assert SENTINEL not in json.dumps(row, default=str)
    assert caplog.records, "nothing was logged at all, so this proves nothing"
    for record in caplog.records:
        rendered = record.getMessage() + repr(record.args) + repr(record.__dict__)
        assert SENTINEL not in rendered
    assert SENTINEL not in caplog.text


def test_the_turn_rows_and_the_event_rows_agree(tmp_path: Path) -> None:
    """The two halves are assembled by different code from different
    sources, so drift between them is the risk. One utterance is one
    `heard`, one turn's rounds are its `llm_round` events, and one turn's
    calls are its `tool_call` events."""
    with TestClient(create_app(recording_config(tmp_path))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)
            say_something(websocket)

    turns = read("select * from record.turns")
    events = read("select name, fields from record.events")
    named = [row["name"] for row in events]

    assert len(turns) == named.count("heard") == 2
    assert sum(turn["rounds"] for turn in turns) == named.count("llm_round")
    assert sum(turn["tool_calls"] for turn in turns) == named.count("tool_call")
    agents = {
        row["fields"].get("agent")
        for row in events
        if row["name"] == "heard"
    }
    assert agents == {turn["agent"] for turn in turns} == {"assistant"}


# What a reader sees while the conversation is still going


def test_the_session_row_is_there_from_the_open(tmp_path: Path) -> None:
    # The open is its own marker, so a page opened mid conversation finds
    # the session rather than nothing until it ends.
    with TestClient(create_app(recording_config(tmp_path))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            (row,) = until(
                lambda: read("select * from record.sessions"),
                "the session row never appeared while the session was open",
            )
            assert row["closed_at"] is None
            assert row["close_reason"] is None
            assert row["duration_s"] is None


def test_a_mid_session_read_stops_at_the_last_completed_turn(
    tmp_path: Path, spy: list[Emission]
) -> None:
    """The marker policy, from outside: a turn commits everything up to
    itself, and the utterance being answered right now is still in
    memory. So a reader sees whole turns and never half of one.

    The second utterance is taken as far as its `heard`, which the spy
    reports having been offered; the reply it starts then runs for the
    length of a spoken answer, which is the window this reads in."""
    with TestClient(create_app(recording_config(tmp_path))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)
            # On the events half rather than on the turns, because the
            # events are what this reads at the end and a marker commits
            # its two halves in two transactions: the turn row is
            # visible before its events are. Waiting on the turns lets
            # the read below race the interval between them and find no
            # `heard` at all, which is the opposite of what it is
            # testing.
            until(
                lambda: read("select * from record.events where name = 'heard'"),
                "the first turn never committed both its halves",
            )
            websocket.send_text(
                json.dumps({"type": "listen", "state": "start", "mode": "manual"})
            )
            send_pcm(websocket, speech_pcm(300), OpusEncoder())
            websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
            until(
                lambda: sum(1 for e in spy if e.payload["event"] == "heard") == 2,
                "the second utterance was never heard",
            )

            assert len(read("select * from record.turns")) == 1
            heard = read("select * from record.events where name = 'heard'")
            assert len(heard) == 1, "the open turn's utterance was already visible"


async def test_a_turn_is_visible_before_its_events_are(tmp_path: Path) -> None:
    """The interval the read above waits out, pinned where it is not a
    matter of timing.

    A marker commits twice and holds no lock in between, so a turn row
    is durable while its events are not yet written. That is why the
    read above waits on the events and not on the turns: a wait on the
    turns returns inside this window, where the `heard` it is about to
    count is not there at all. Left to scheduling that window is narrow
    enough to pass by luck on a fast disk and to fail on a loaded runner
    (#367), so it is opened here on purpose, with the writer parked in
    front of the events half.
    """
    gate = Gate(Half.EVENTS)
    store = ConversationStore(DatabaseConfig(), gate=gate)
    store.start()
    try:
        session, websocket, task = await open_session(recording_config(tmp_path), store)
        await drive_reply(session, UTTERANCE)
        # The first events half of the session, which is the turn's: a
        # gate for `Half.EVENTS` is not called in front of the open's
        # marker, because that marker carries no events to write.
        gate.wait()

        # The durable half of the turn's marker has committed and the
        # events half has not begun: exactly the state a wait on
        # `record.turns` is free to return in.
        assert len(read("select * from record.turns")) == 1
        assert read("select * from record.events where name = 'heard'") == [], (
            "the events half committed before the gate let it"
        )

        gate.open_forever()
        await websocket.close(1000, "goodbye")
        await asyncio.wait_for(task, timeout=TIMEOUT_S)
    finally:
        gate.open_forever()
        store.stop()

    # And released, they land, so what the window holds back is delay
    # and not loss.
    assert len(read("select * from record.events where name = 'heard'")) == 1


# The switches, on the file


@pytest.mark.parametrize("metrics", [True, False])
@pytest.mark.parametrize("text_storage", [True, False])
def test_the_switch_combinations_decide_what_a_row_keeps(
    tmp_path: Path, metrics: bool, text_storage: bool
) -> None:
    # The sentinel is planted as the agent's prompt and spoken back by
    # the mock, so what carries it into the record is conversation text
    # and only that. Planting it as the transcript would put it in the
    # provider entries the session row records verbatim, which is
    # configuration rather than conversation and is deliberately not
    # under either switch.
    config = recording_config(
        tmp_path,
        asr_text="hello",
        llm={"type": "mock", "reply": "You said {system}."},
        prompt=SENTINEL,
        metrics=metrics,
        text=text_storage,
    )
    with TestClient(create_app(config)) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)

    (session,) = read("select * from record.sessions")
    (turn,) = read("select * from record.turns")
    events = read("select * from record.events")

    # The spine lands in every enabled configuration: retention, purging
    # and every read key on it.
    assert session["session"] and session["started_at"] and session["closed_at"]
    assert (session["metrics"], session["text"]) == (int(metrics), int(text_storage))
    # The structural half of a turn survives both switches, being neither
    # a measured number nor conversation text.
    assert turn["agent"] == "assistant"
    assert turn["tool_calls"] == 0

    assert (turn["heard"] is not None) is text_storage
    assert (turn["reply"] is not None) is text_storage
    assert (turn["rounds"] is not None) is metrics
    assert (turn["llm_ms"] is not None) is metrics
    assert (session["duration_s"] is not None) is metrics
    assert bool(events) is metrics

    # The switch on the file rather than in the query planner: what was
    # said reaches no byte of the database when text storage is off.
    assert _stored_anywhere(SENTINEL) is text_storage


def test_a_credential_in_a_provider_url_reaches_no_record(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one credential shape no secret-shaped key gives away: written
    into a provider's address, where it used to be copied verbatim into
    both records of every session held against it.

    A real entry with a real `base_url` here rather than a mock option,
    because that is the key an operator actually writes it in. The
    session is a handshake and nothing else: the session row and the
    capture's manifest are both written at the open, which is where the
    provider entries land, and no round ever runs against the address.
    """
    monkeypatch.setenv("VINGA_TEST_PROVIDER_KEY", "not-a-real-credential")
    config = Config(
        server={
            "conversations": {"enabled": True},
            "capture": {"enabled": True, "dir": str(tmp_path / "captures")},
        },
        providers={
            "llm": {
                "vendor": {
                    "type": "openai_compatible",
                    "base_url": f"https://user:{SENTINEL}@host/v1",
                    "model": "a-model",
                    "api_key_env": "VINGA_TEST_PROVIDER_KEY",
                    "egress": True,
                }
            },
            "asr": {"mock": {"type": "mock", "text": "hello"}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        agents={
            "assistant": {"llm": "vendor", "asr": "mock", "tts": "mock", "vad": "mock"}
        },
        default_agent="assistant",
    )

    with caplog.at_level("DEBUG"):
        with TestClient(create_app(config)) as client:
            with connect(client) as websocket:
                shake_hands(websocket)

    (row,) = read("select * from record.sessions")
    (manifest_file,) = (tmp_path / "captures").glob("*.json")
    manifest = json.loads(manifest_file.read_text())

    # What the record is for survives: the entry, its type and the exact
    # model string, and the address without what was in front of it.
    assert row["providers"]["llm"]["base_url"] == "https://host/v1"
    assert row["providers"]["llm"]["model"] == "a-model"
    assert manifest["providers"]["llm"]["base_url"] == "https://host/v1"
    # And the credential reaches nothing that outlives the session.
    assert not _stored_anywhere(SENTINEL)
    assert SENTINEL not in manifest_file.read_text()
    assert SENTINEL not in caplog.text
    printed = capsys.readouterr()
    assert SENTINEL not in printed.out + printed.err


def test_a_rejected_tool_argument_is_kept_as_content_and_named_on_no_telemetry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, spy: list[Emission]
) -> None:
    """Where the two surfaces part, driven with an argument a tool threw
    away: the model asks `remember` to keep a fact that is not a
    sentence at all, and the value inside what it sent is
    credential-shaped. Argument validation refuses before the memory
    store is touched, so the whole ordinary path is what runs, dispatch
    through the rendered exception through the `tool_call` event to the
    stored invocation, and no write happens on the way.

    Both halves of the answer are the content-and-telemetry record
    (`docs/adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md`).
    The conversation store is the system of record for content, gated by
    the API secret and by the deployment's own `text` switch, and what
    the model actually passed is the whole point of it: an argument
    redacted because the tool refused it would hide the evidence of why
    the tool refused, which is the one question the record exists to
    answer. Telemetry is metadata only, so the same value must appear on
    no event field and in no log record, whatever the refusal path does
    with it.

    The value is recorded because the model wrote it, not because it was
    accepted. A rejected argument is content exactly as an accepted one
    is.
    """
    config = recording_config(
        tmp_path,
        llm={
            "type": "mock",
            "reply": "That did not work: {tool_result}.",
            "tool_when": "remember",
            "tool_name": "remember",
            "tool_arguments": {"text": [SENTINEL]},
        },
    )

    with caplog.at_level("DEBUG"):
        with TestClient(create_app(config)) as client:
            with connect(client) as websocket:
                shake_hands(websocket)
                say_something(websocket)

    (call,) = read("select * from record.tool_invocations")
    events = read("select * from record.events")

    # The call happened, was this server's own builtin, and failed.
    assert (call["source"], call["name"], call["is_error"]) == ("builtin", "remember", 1)
    # The content channel's contract: what the model passed, verbatim,
    # rejected value included.
    assert call["arguments"] == {"text": [SENTINEL]}
    # The refusal the model was handed says what to send instead, and
    # quotes no value: it travels back to the model and into this same
    # record, and neither is a reason to echo bytes nobody needs.
    assert 'needs a "text" argument' in call["result"]
    assert SENTINEL not in call["result"]

    # Telemetry, at the tap and on the file and in both formats. The
    # emissions are what every consumer of the events is handed, before
    # the store's own strip, so they are where "no event field" has to
    # hold; the rows are what a deployment keeps; the records are the
    # rendered sentence and the structured fields it carries.
    assert [e for e in spy if e.payload["event"] == "tool_call"], "no call was recorded"
    for emission in spy:
        assert SENTINEL not in json.dumps(emission.payload, default=str)
    assert events, "the events half of the record is what this claim is about"
    for row in events:
        assert SENTINEL not in row["fields"]
        assert SENTINEL not in json.dumps(row, default=str)
    for record in caplog.records:
        rendered = record.getMessage() + repr(record.args) + repr(record.__dict__)
        assert SENTINEL not in rendered
    assert SENTINEL not in caplog.text


def _stored_anywhere(sentinel: str) -> bool:
    """Whether the planted text is anywhere in the store, asked of every
    column of every row of every table it owns.

    The SQLite-era form of this read the database file and both of its
    sidecars, which is the honest surface a file offers and not one a
    server-side store has. This is weaker in one stated way: it cannot
    see a page the server has not yet reclaimed, which is autovacuum's
    business and not a client's. It is the strongest thing a client can
    ask, and it is what the reference and the README now promise.
    """
    for table in ("sessions", "turns", "tool_invocations", "events"):
        for row in read(f"select * from record.{table}"):
            if sentinel in json.dumps(row, default=str):
                return True
    return False


# The close path, from the first attachment on


def _guarded(tmp_path: Path, store: ConversationStore) -> tuple[Any, Any]:
    """A session with both consumers configured, driven through `run`.

    Through `run` rather than through a test client because what is
    under test is where its guard begins: which steps can fail with a
    capture open and a session row started, and whether the close still
    lands when one of them does.
    """
    config = recording_config(tmp_path)
    captures = CaptureStore(tmp_path / "captures", 900.0, 2000.0, 0.0)
    generations = world(config, providers=built_world(config))
    factory = bespoke_runtime_factory(generations, McpServers({}), lane_memory(), store)
    websocket = LoopingSocket()
    session = DeviceSession(
        cast(Any, websocket), generations, factory, captures, conversations=store
    )
    return session, websocket


def _capture_manifest(tmp_path: Path) -> dict[str, Any] | None:
    found = list((tmp_path / "captures").glob("*.json"))
    return json.loads(found[0].read_text()) if found else None


async def test_a_device_that_vanishes_at_the_hello_opens_no_record(
    tmp_path: Path,
) -> None:
    """The hello send is the last step outside the guard, and nothing is
    open when it runs. A device that goes away there recorded nothing,
    rather than leaving a capture nobody closes and a row nobody ends."""
    store = ConversationStore(DatabaseConfig())
    store.start()
    session, websocket = _guarded(tmp_path, store)

    async def refuse(text: str) -> None:
        raise WebSocketDisconnect(code=1006)

    websocket.send_text = refuse  # type: ignore[method-assign]
    try:
        with pytest.raises(WebSocketDisconnect):
            await session.run()
    finally:
        store.stop()

    assert read("select * from record.sessions") == []
    assert _capture_manifest(tmp_path) is None
    assert attached_taps(session) == [], "a consumer was left attached"


@pytest.mark.parametrize(
    "break_step",
    [
        lambda session, boom: setattr(session, "_start_device_discovery", boom),
        lambda session, boom: setattr(session._watchdog, "start", boom),
    ],
    ids=["device discovery", "the idle watchdog"],
)
async def test_a_failure_after_the_open_still_finishes_the_record(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    break_step: Callable[[Any, Any], None],
) -> None:
    """Every step from the first attachment on is inside the guard, so a
    failure at any of them still reaches `session_closed`, the store's
    close, the sink's detach and the capture's close. Before, the guard
    began at the serve loop and these steps sat in front of it."""
    store = ConversationStore(DatabaseConfig())
    store.start()
    session, _ = _guarded(tmp_path, store)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the step nobody expected to fail")

    # Each step is broken where it lives, which for the watchdog is on
    # the object the session starts rather than on the session.
    break_step(session, boom)
    with caplog.at_level("INFO"):
        try:
            with pytest.raises(RuntimeError):
                await session.run()
        finally:
            store.stop()

    (closed,) = [r for r in caplog.records if getattr(r, "event", None) == "session_closed"]
    assert closed.reason == "error"
    (row,) = read("select * from record.sessions")
    assert row["closed_at"] is not None
    assert row["close_reason"] == "error"
    # White-box for the two collaborator reads: a session gives back
    # what it took, and a released collaborator has no public form,
    # which is the point of releasing it. A record still open is a row
    # nobody closes and a capture still open is a manifest that never
    # says it finished, both of which show up in another process.
    assert session._record is None
    assert attached_taps(session) == [], "a consumer was left attached"
    assert session._capture_audio is None
    manifest = _capture_manifest(tmp_path)
    # The manifest's capture block is rewritten by the close, so its
    # `complete` is what says the capture was finished rather than left
    # behind by a process that went away.
    assert manifest is not None and manifest["capture"]["complete"] is True


async def _opened(session: Any, websocket: Any) -> None:
    """Wait on the loop the session is running on, which `until` cannot
    do: it sleeps the thread, and this session is a task beside it."""
    for _ in range(500):
        await asyncio.sleep(0.01)
        # White-box, for the reason `sessions.open_session` gives at
        # the same wait: the handshake's completion is recorded nowhere
        # else, and a caller that polled the runtime alone would return
        # a session still mid-accept.
        if session._opened_at is not None and websocket.inbox.empty():
            return
    raise AssertionError("the session never opened")


async def test_a_cancelled_cleanup_step_still_finishes_the_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A cancellation arriving into the close is the one exception a
    guard must neither swallow nor obey immediately: the task really is
    being cancelled, and the record still has to be finished. It is held
    until the event, the store's close and the capture's close have run,
    and re-raised then, so the caller's task still ends cancelled."""
    store = ConversationStore(DatabaseConfig())
    store.start()
    session, websocket = _guarded(tmp_path, store)
    task = asyncio.create_task(session.run())
    await _opened(session, websocket)

    async def cancelled() -> None:
        raise asyncio.CancelledError

    assert session.runtime is not None
    session.runtime.close = cancelled  # type: ignore[method-assign]

    with caplog.at_level("INFO"):
        try:
            await websocket.close(1000, "goodbye")
            await asyncio.wait([task])
        finally:
            store.stop()

    assert task.cancelled(), "the cancellation did not reach the caller"
    (closed,) = [r for r in caplog.records if getattr(r, "event", None) == "session_closed"]
    assert closed.reason == "client"
    (row,) = read("select * from record.sessions")
    assert row["closed_at"] is not None
    # White-box, per the note at the same pair above.
    assert session._record is None
    assert attached_taps(session) == []
    manifest = _capture_manifest(tmp_path)
    assert manifest is not None and manifest["capture"]["complete"] is True


# A wedged writer, in three deterministic parts


async def test_no_producer_on_the_session_loop_can_wait(tmp_path: Path) -> None:
    """Structural rather than timed: a queue whose blocking `put` raises
    proves that no path from a live session reaches one, whatever the
    writer is doing. The whole session goes through it, open, events,
    turn and close."""

    class Refusing:
        def __init__(self) -> None:
            self.items: list[Any] = []

        def put(self, item: Any) -> None:
            raise AssertionError("a producer blocked on the store")

        def put_nowait(self, item: Any) -> None:
            self.items.append(item)

        def get(self) -> Any:
            raise AssertionError("the writer is not running in this test")

    queue = Refusing()
    store = ConversationStore(DatabaseConfig(), queue=queue)
    try:
        session, websocket, task = await open_session(recording_config(tmp_path), store)
        await drive_reply(session, UTTERANCE)
        await websocket.close(1000, "goodbye")
        await asyncio.wait_for(task, timeout=TIMEOUT_S)
    finally:
        # The thread never started, so this disposes the engine and puts
        # nothing on the queue that would raise.
        store.stop()

    kinds = [type(item).__name__ for item in queue.items]
    assert kinds[0] == "Open"
    assert kinds[-1] == "Close"
    assert "Turn" in kinds
    assert "Event" in kinds


async def test_a_parked_writer_never_delays_a_reply(tmp_path: Path) -> None:
    """The behavioural half, with the writer stopped in front of its very
    first transaction, on the same seam a locked database exercises. A
    heartbeat on the session's own loop says the loop was never blocked,
    and the reply finishes inside a fixed bound rather than inside a
    comparison with another run."""
    released = threading.Event()
    parked = threading.Event()

    def gate(half: Half = Half.DURABLE) -> None:
        parked.set()
        released.wait(timeout=TIMEOUT_S)

    store = ConversationStore(DatabaseConfig(), gate=gate)
    store.start()
    gaps: list[float] = []

    async def heartbeat() -> None:
        loop = asyncio.get_running_loop()
        last = loop.time()
        while True:
            await asyncio.sleep(0.01)
            now = loop.time()
            gaps.append(now - last)
            last = now

    ticking = asyncio.create_task(heartbeat())
    try:
        session, websocket, task = await open_session(recording_config(tmp_path), store)
        assert parked.wait(timeout=TIMEOUT_S), "the writer never reached a marker"
        loop = asyncio.get_running_loop()
        started = loop.time()
        await drive_reply(session, UTTERANCE)
        assert loop.time() - started < 5.0, "the reply waited on the store"
        await websocket.close(1000, "goodbye")
        await asyncio.wait_for(task, timeout=TIMEOUT_S)
        # The loop kept its own appointments throughout: no tick is
        # anywhere near what a blocked database call would cost.
        assert max(gaps) < 0.5
    finally:
        ticking.cancel()
        released.set()
        store.stop()


async def test_events_beyond_the_bound_go_and_the_conversation_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The queue-full path, driven by moving the bound to zero rather
    than by producing a thousand events: every event is refused, said
    once, and counted onto the session row, while the turn and the close
    land because control records are never dropped."""
    monkeypatch.setattr(store_module, "MAX_EVENTS_IN_FLIGHT", 0)
    store = ConversationStore(DatabaseConfig())
    store.start()
    with caplog.at_level("INFO"):
        try:
            session, websocket, task = await open_session(
                recording_config(tmp_path), store
            )
            await drive_reply(session, UTTERANCE)
            await websocket.close(1000, "goodbye")
            await asyncio.wait_for(task, timeout=TIMEOUT_S)
        finally:
            store.stop()

    (row,) = read("select * from record.sessions")
    assert row["dropped"] > 0
    assert row["close_reason"] == "client"
    assert len(read("select * from record.turns")) == 1
    assert read("select * from record.events") == []
    dropped = [
        r for r in caplog.records if getattr(r, "event", None) == "conversations_dropped"
    ]
    assert len(dropped) == 1, "the store said it was behind more than once"
    assert dropped[0].session == session.session_id


async def test_a_failed_write_costs_the_batch_and_not_the_conversation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The failed-marker path, through the engine seam: the writer is
    parked at the turn's marker, the engine is broken under it, and the
    conversation carries on to a normal close. What leaves the store is
    the exception's class name and nothing else, which is why the
    exception carries a credential-shaped message."""

    class Broken:
        def begin(self) -> Any:
            raise RuntimeError(f"the disk went away holding {POISON}")

        def dispose(self) -> None:
            return None

    gate = Gate()
    store = ConversationStore(DatabaseConfig(), gate=gate)
    store.start()
    with caplog.at_level("INFO"):
        try:
            session, websocket, task = await open_session(
                recording_config(tmp_path), store
            )
            # The open's own marker, let through so there is a session
            # row for the close to find.
            gate.wait()
            gate.let_through()
            await drive_reply(session, UTTERANCE)
            gate.wait()
            # White-box: the failure under test is the database going
            # away between a turn and the close that follows it, and
            # only a broken engine puts it exactly there. The real one
            # is let go of first, or its pool outlives this test and a
            # server-side driver says so where the next test runs.
            store._engine.dispose()
            store._engine = Broken()  # type: ignore[assignment]
            gate.open_forever()
            await websocket.close(1000, "goodbye")
            await asyncio.wait_for(task, timeout=TIMEOUT_S)
        finally:
            gate.open_forever()
            store.stop()

    failed = [
        r for r in caplog.records if getattr(r, "event", None) == "conversations_failed"
    ]
    assert failed, "a failed write said nothing"
    assert failed[0].failure == "RuntimeError"
    assert POISON not in caplog.text
    # The session row is what survived, open-shaped, which is the
    # documented incomplete state and the same shape a crash leaves.
    (row,) = read("select * from record.sessions")
    assert row["session"] == session.session_id
    assert row["closed_at"] is None
