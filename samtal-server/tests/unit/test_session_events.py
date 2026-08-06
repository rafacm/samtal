"""The structured events a conversation emits.

Retained JSON logs are what a transcript is read back out of until v3
brings a real conversation store, so the shape of these records is a
contract: `event`, `session`, and `device` on every one, plus the
per-event fields the server README documents. The assertions run against
`caplog.records`, because the fields ride `extra=` and never appear in
the message text, which is also what these tests pin: the human sentence
did not change when the fields arrived.
"""

import json
from dataclasses import replace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from samtal_server.app import create_app
from samtal_server.build_info import REVISION_ENV, revision
from samtal_server.config import Config
from samtal_server.logs import JsonFormatter
from samtal_server.ota import OTA_PATH
from samtal_server.providers import AsrProvider, AsrResult
from tests.unit.test_session import (
    DEVICE_MAC,
    DEVICE_UUID,
    config_with_agent,
    connect,
    say_something,
    shake_hands,
)
from tests.unit.test_session_tools import (
    BOTH_MAC,
    POET_MAC,
    ScriptedLlm,
    _nothing,
    base_config,
    call,
    run_reply,
    session_for,
)


def events(caplog: pytest.LogCaptureFixture, name: str) -> list:
    return [record for record in caplog.records if getattr(record, "event", None) == name]


def only(caplog: pytest.LogCaptureFixture, name: str):
    matching = events(caplog, name)
    assert len(matching) == 1, f"expected one {name} record, got {len(matching)}"
    return matching[0]


def hold_a_conversation(config: Config) -> None:
    with TestClient(create_app(config)) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)


def test_a_conversation_logs_heard_and_replied(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        hold_a_conversation(config_with_agent(asr_text="what time is it"))

    heard = only(caplog, "heard")
    assert heard.text == "what time is it"
    assert heard.agent == "assistant"
    assert heard.duration_s > 0
    replied = only(caplog, "replied")
    assert replied.text == "You said what time is it."
    assert replied.agent == "assistant"
    # Both halves of the exchange carry the same session and device, which
    # is what makes a transcript groupable.
    assert heard.session == replied.session
    assert heard.device == replied.device == DEVICE_MAC.lower()


def test_the_human_message_is_unchanged_by_the_extra_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO"):
        hold_a_conversation(config_with_agent(asr_text="hello"))
    assert 'heard "hello"' in caplog.text
    assert 'replied "You said hello."' in caplog.text


def test_session_open_and_closed_bracket_the_conversation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO"):
        hold_a_conversation(config_with_agent())

    opened = only(caplog, "session_open")
    assert opened.client == DEVICE_UUID
    assert opened.agent == "assistant"
    assert opened.agents == ["assistant"]
    assert opened.protocol == 1
    assert opened.revision == revision()
    closed = only(caplog, "session_closed")
    assert closed.session == opened.session
    assert closed.duration_s >= 0


def test_session_open_names_the_build_that_served_it(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#41: the logs already ship to a collector, so a revision here
    makes every session attributable to a build, not only the ones
    somebody thought to investigate. Two field sessions that behaved
    differently were otherwise indistinguishable from one code change
    and two different rooms."""
    revision.cache_clear()
    monkeypatch.setenv(REVISION_ENV, "5e6f7a8b")
    try:
        with caplog.at_level("INFO"):
            hold_a_conversation(config_with_agent())
    finally:
        revision.cache_clear()
    assert only(caplog, "session_open").revision == "5e6f7a8b"


def test_a_device_with_no_agent_logs_a_rejection(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        with TestClient(create_app(Config())) as client:
            with connect(client) as websocket:
                with pytest.raises(WebSocketDisconnect):
                    websocket.receive_text()

    rejected = only(caplog, "session_rejected")
    assert rejected.reason == "no_agent"
    # A rejection still names the device it turned away.
    assert rejected.device == DEVICE_MAC.lower()


def test_a_malformed_mac_logs_a_rejection_with_no_device(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        with TestClient(create_app(config_with_agent())) as client:
            with connect(client, device_id="not-a-mac") as websocket:
                with pytest.raises(WebSocketDisconnect):
                    websocket.receive_text()

    rejected = only(caplog, "session_rejected")
    assert rejected.reason == "bad_device_id"
    # There is no MAC to name: the header is what was wrong.
    assert rejected.device is None


def test_the_ota_check_is_an_event_of_its_own(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        with TestClient(create_app(config_with_agent())) as client:
            client.post(
                OTA_PATH,
                json={"application": {"version": "2.4.0"}, "board": {"type": "waveshare"}},
                headers={"Device-Id": DEVICE_MAC, "Client-Id": DEVICE_UUID},
            )

    check = only(caplog, "ota_check")
    # No session exists at OTA time, so this event carries the device and
    # not a session id.
    assert check.device == DEVICE_MAC.lower()
    assert check.client == DEVICE_UUID
    assert check.board == "waveshare"
    assert check.firmware == "2.4.0"
    assert check.agents == ["assistant"]


def test_speaking_started_marks_the_first_frame_of_a_reply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO"):
        hold_a_conversation(config_with_agent(asr_text="hello"))

    started = only(caplog, "speaking_started")
    assert started.agent == "assistant"
    replied = only(caplog, "replied")
    assert started.session == replied.session
    # It exists to make time-to-first-audio measurable, so it must sit
    # between the transcription and the end of the reply.
    order = [caplog.records.index(record) for record in (only(caplog, "heard"), started, replied)]
    assert order == sorted(order)


async def test_a_reply_starts_speaking_only_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pacing restarts per agent leg (a handover sets `_pace_start`
    back to None); the event must not restart with it."""

    class Sink:
        async def send_bytes(self, data: bytes) -> None:
            return None

    session = session_for(base_config(), POET_MAC)
    session.websocket = cast(Any, Sink())
    with caplog.at_level("INFO"):
        await session._send_frames([b"frame"])
        session._pace_start = None
        await session._send_frames([b"frame"])

    started = only(caplog, "speaking_started")
    assert started.agent == "poet"


class LockingAsr(AsrProvider):
    """Detects Spanish confidently on the first call and asks for it to
    be reused; later calls answer in whatever they were pinned to."""

    def __init__(self) -> None:
        self.hints: list[str | None] = []

    async def transcribe(
        self, pcm: bytes, sample_rate: int, language_hint: str | None = None
    ) -> AsrResult:
        self.hints.append(language_hint)
        if language_hint is None:
            return AsrResult(
                "hola", language="es", language_confidence=0.97, lock_language="es"
            )
        return AsrResult("hola otra vez", language=language_hint)


async def test_the_session_hands_a_locked_language_back_as_a_hint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The detect-once policy lives in the provider, but the cache is
    the session's: `lock_language` from one utterance returns as
    `language_hint` on the next, and the heard events carry what was
    detected."""

    class TextSink:
        async def send_text(self, text: str) -> None:
            return None

    session = session_for(base_config(), BOTH_MAC)
    assert session._providers is not None
    asr = LockingAsr()
    session._providers = replace(session._providers, asr=asr)
    session.websocket = cast(Any, TextSink())

    async def speak(synthesis: Any, resampler: Any, into: list[str]) -> None:
        # Sentences reach _speak as a synthesis in flight now (#37), so
        # the stub takes the text off it and skips the audio entirely.
        synthesis.cancel()
        into.append(synthesis.sentence)

    session._speak = speak  # type: ignore[method-assign]
    session._send_frames = _nothing  # type: ignore[method-assign]

    with caplog.at_level("INFO"):
        await session._reply(b"\x00\x00" * 320)
        await session._reply(b"\x00\x00" * 320)

    assert asr.hints == [None, "es"]
    first, second = events(caplog, "heard")
    assert first.language == "es"
    assert first.language_confidence == 0.97
    assert second.language == "es"
    assert not hasattr(second, "language_confidence")


async def test_a_handover_logs_what_each_agent_said(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scripts = {
        "poet": ScriptedLlm([["Handing you over.", call("switch_agent", agent="tutor")]]),
        "tutor": ScriptedLlm(["Hello, I am the tutor."]),
    }
    session = session_for(base_config(), BOTH_MAC, scripts)
    with caplog.at_level("INFO"):
        await run_reply(session, "get me the tutor")

    said = only(caplog, "agent_said")
    assert said.agent == "poet"
    assert said.text == "Handing you over."
    handover = only(caplog, "handover")
    assert handover.from_agent == "poet"
    assert handover.to_agent == "tutor"
    assert handover.session == said.session


async def test_a_tool_call_is_logged_with_its_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    script = ScriptedLlm([[call("ghost_tool")], "I could not do that."])
    session = session_for(base_config(), BOTH_MAC, {"poet": script})
    with caplog.at_level("INFO"):
        await run_reply(session, "do it")

    logged = only(caplog, "tool_call")
    assert logged.tool == "ghost_tool"
    assert logged.agent == "poet"
    assert logged.duration_ms >= 0
    # An unknown tool is an error result, and the record says so.
    assert logged.is_error is True


def test_the_fields_survive_the_json_formatter(caplog: pytest.LogCaptureFixture) -> None:
    """What the formatter is for: the fields reach the emitted line."""
    with caplog.at_level("INFO"):
        hold_a_conversation(config_with_agent(asr_text="good morning"))

    line = json.loads(JsonFormatter().format(only(caplog, "heard")))
    assert line["event"] == "heard"
    assert line["text"] == "good morning"
    assert line["device"] == DEVICE_MAC.lower()
    assert line["session"]
    # A record is one line, whatever is in the text.
    assert "\n" not in json.dumps(line)
