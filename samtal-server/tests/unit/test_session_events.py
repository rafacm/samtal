"""The structured events a conversation emits.

Retained JSON logs are what a transcript is read back out of until v3
brings a real conversation store, so the shape of these records is a
contract: `event`, `session`, and `device` on every one, plus the
per-event fields the server README documents. The assertions run against
`caplog.records`, because the fields ride `extra=` and never appear in
the message text, which is also what these tests pin: the human sentence
did not change when the fields arrived.
"""

import asyncio
import json
from dataclasses import replace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from samtal_server.app import create_app
from samtal_server.build_info import REVISION_ENV, revision
from samtal_server.config import Config
from samtal_server.device.boundary import PlayableAudio
from samtal_server.logs import JsonFormatter
from samtal_server.ota import OTA_PATH
from samtal_server.providers import (
    AsrProvider,
    AsrResult,
    ProviderIdentity,
    TextDelta,
    Usage,
)
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
    assert heard.agent == "assistant"
    assert heard.duration_s > 0
    replied = only(caplog, "replied")
    assert replied.agent == "assistant"
    assert replied.sentences == 1
    # Neither half carries a word of it: what was said is content, and
    # content is the conversation store's (#120). These two are the
    # metadata view of the same exchange.
    assert not hasattr(heard, "text")
    assert not hasattr(replied, "text")
    # Both halves carry the same session and device, which is what makes
    # the exchange groupable.
    assert heard.session == replied.session
    assert heard.device == replied.device == DEVICE_MAC.lower()


def test_the_human_message_is_unchanged_by_the_extra_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO"):
        hold_a_conversation(config_with_agent(asr_text="hello"))
    assert "heard 0.30 s of speech" in caplog.text
    assert "assistant replied in 1 sentences" in caplog.text


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
        await session.send_audio(PlayableAudio([b"frame"]))
        session._pace_start = None
        await session.send_audio(PlayableAudio([b"frame"]))

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
    assert session.runtime._providers is not None
    asr = LockingAsr()
    session.runtime._providers = replace(session.runtime._providers, asr=asr)
    session.websocket = cast(Any, TextSink())

    async def speak(synthesis: Any, resampler: Any, into: list[str]) -> None:
        # Sentences reach _speak as a synthesis in flight now (#37), so
        # the stub takes the text off it and skips the audio entirely.
        synthesis.cancel()
        into.append(synthesis.sentence)

    session.runtime._speak = speak  # type: ignore[method-assign]
    session.send_audio = _nothing  # type: ignore[method-assign]

    with caplog.at_level("INFO"):
        await session.runtime._reply(b"\x00\x00" * 320)
        await session.runtime._reply(b"\x00\x00" * 320)

    assert asr.hints == [None, "es"]
    first, second = events(caplog, "heard")
    assert first.language == "es"
    assert first.language_confidence == 0.97
    assert second.language == "es"
    assert not hasattr(second, "language_confidence")


async def test_a_handover_logs_how_much_each_agent_said(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scripts = {
        "poet": ScriptedLlm([["Handing you over.", call("switch_agent", agent="tutor")]]),
        "tutor": ScriptedLlm(["Hello, I am the tutor."]),
    }
    session = session_for(base_config(), BOTH_MAC, scripts)
    with caplog.at_level("INFO"):
        spoken = await run_reply(session, "get me the tutor")

    said = only(caplog, "agent_said")
    assert said.agent == "poet"
    # Which agent spoke and how much of the reply was its share. What it
    # said is the store's, and here it is what the reply returned.
    assert said.sentences == 1
    assert not hasattr(said, "text")
    assert spoken == ["Hello, I am the tutor."]
    handover = only(caplog, "handover")
    assert handover.from_agent == "poet"
    assert handover.to_agent == "tutor"
    assert handover.session == said.session


async def test_an_llm_round_is_logged_with_what_it_cost(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The gap between `heard` and `speaking_started` holds the LLM and
    the TTS time to first byte with nothing between them, so a slow
    reply used to be attributable to neither (#55)."""
    script = ScriptedLlm([["Two words.", Usage(prompt_tokens=140, completion_tokens=12)]])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    with caplog.at_level("INFO"):
        await run_reply(session, "say something")

    round_one = only(caplog, "llm_round")
    assert round_one.agent == "poet"
    assert round_one.round == 1
    assert round_one.stage == "llm"
    assert round_one.provider == "mock"
    assert round_one.type == "mock"
    assert round_one.duration_ms >= 0
    assert round_one.first_token_ms >= 0
    # The history the round was given, which is the cheap proxy for a
    # payload growing turn by turn.
    assert round_one.turns == 1
    # The GenAI conventions' names, adapted to this project's field
    # style, which is also what the store's turns columns are called
    # (#120). The `Usage` dataclass they are read off keeps the
    # SDK-shaped names: it is not surface.
    assert round_one.input_tokens == 140
    assert round_one.output_tokens == 12


async def test_a_provider_that_reports_no_usage_is_not_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = session_for(base_config(), POET_MAC, {"poet": ScriptedLlm(["Nothing to add."])})
    with caplog.at_level("INFO"):
        await run_reply(session, "say something")

    logged = only(caplog, "llm_round")
    assert not hasattr(logged, "input_tokens")
    assert not hasattr(logged, "output_tokens")
    assert logged.duration_ms >= 0


async def test_a_round_that_only_called_a_tool_times_no_first_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both providers assemble tool calls after their stream has ended,
    so timing the first event rather than the first token would report
    a whole generation as its own time to first token, on exactly the
    rounds a handover is made of."""
    script = ScriptedLlm([[call("ghost_tool")], "It did not work."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    with caplog.at_level("INFO"):
        await run_reply(session, "do it")

    tool_round, speaking_round = events(caplog, "llm_round")
    assert not hasattr(tool_round, "first_token_ms")
    assert speaking_round.first_token_ms >= 0


async def test_every_round_of_a_reply_is_its_own_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    script = ScriptedLlm([[call("ghost_tool")], "It did not work."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    with caplog.at_level("INFO"):
        await run_reply(session, "do it")

    first, second = events(caplog, "llm_round")
    assert (first.round, second.round) == (1, 2)
    # The second round saw the first round's call and its result, so
    # the payload grew, which is what `turns` is there to show.
    assert second.turns > first.turns


async def test_the_generation_after_a_handover_is_a_round_of_its_own(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The slow call in the field report was the post-handover one. It
    starts a new agent's leg, so a per-leg counter would have called it
    another first round; the count is per reply."""
    scripts = {
        "poet": ScriptedLlm([[call("switch_agent", agent="tutor")]]),
        "tutor": ScriptedLlm(["Tutor here."]),
    }
    session = session_for(base_config(), BOTH_MAC, scripts)
    with caplog.at_level("INFO"):
        await run_reply(session, "get me the tutor")

    first, second = events(caplog, "llm_round")
    assert (first.agent, first.round) == ("poet", 1)
    assert (second.agent, second.round) == ("tutor", 2)


async def test_the_device_is_told_speech_starts_only_when_it_does() -> None:
    """`tts start` puts the device into its speaking state, which is
    what its display shows and what makes a button press an abort of
    speech. Sent when transcription finished, it made a board claim to
    be speaking for the whole of a slow generation (#55), so it waits
    for the first sentence.

    The model appends its own marker at the moment it produces the
    first token, which is what makes the order provable rather than
    incidental."""
    order: list[str] = []

    class Recorder:
        async def send_text(self, text: str) -> None:
            message = json.loads(text)
            order.append(f"{message['type']} {message.get('state', '')}".strip())

        async def send_bytes(self, data: bytes) -> None:
            return None

    class Thinking(ScriptedLlm):
        async def stream(self, *args: Any, **kwargs: Any) -> Any:
            order.append("model thinking")
            await asyncio.sleep(0)
            order.append("first token")
            yield TextDelta("Stockholm.")

    session = session_for(base_config(), POET_MAC, {"poet": cast(Any, Thinking([]))})
    session.websocket = cast(Any, Recorder())
    session.send_audio = _nothing  # type: ignore[method-assign]
    await session.runtime._reply(b"\x00\x00" * 320)

    assert order.index("model thinking") < order.index("tts start")
    assert order.index("first token") < order.index("tts start")
    # And it still brackets the speech, in the order the device expects.
    assert [step for step in order if step.startswith("tts")] == [
        "tts start",
        "tts sentence_start",
        "tts stop",
    ]
    # The transcript still goes out first: that is what tells the user
    # they were heard while the model is thinking.
    assert order[0] == "stt"


class Unreachable:
    """A provider entry whose host cannot be reached, for all three
    stages. Stamped with an identity the way the registry stamps a real
    one, since that is what the events are supposed to carry."""

    def __init__(self, stage: str, exc: BaseException) -> None:
        self._exc = exc
        self.identity = ProviderIdentity(
            stage=stage, name="cloud", type="openai", host="api.example.com"
        )
        self.sample_rate = 16000

    async def transcribe(self, *args: object, **kwargs: object) -> AsrResult:
        raise self._exc

    async def stream(self, *args: object, **kwargs: object) -> Any:
        raise self._exc
        yield  # pragma: no cover - never reached, makes this a generator

    async def synthesize(self, text: str) -> Any:
        raise self._exc
        yield  # pragma: no cover - never reached, makes this a generator


async def reply_with(
    provider_stage: str, exc: BaseException, caplog: pytest.LogCaptureFixture
) -> Any:
    """One reply against a provider that fails, answering the event it
    produced. The reply ends where it always did; what is new is that
    the failure is on the record as more than a traceback."""

    class TextSink:
        async def send_text(self, text: str) -> None:
            return None

    session = session_for(base_config(), POET_MAC, {"poet": ScriptedLlm(["One sentence."])})
    assert session.runtime._providers is not None
    session.runtime._providers = replace(
        session.runtime._providers, **{provider_stage: cast(Any, Unreachable(provider_stage, exc))}
    )
    session.websocket = cast(Any, TextSink())
    session._mac = POET_MAC
    session.send_audio = _nothing  # type: ignore[method-assign]
    with caplog.at_level("INFO"):
        await session.runtime._reply(b"\x00\x00" * 320)
    return only(caplog, "provider_failed")


async def test_a_failing_asr_provider_says_what_it_could_not_reach(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failed = await reply_with("asr", ConnectionRefusedError("no route"), caplog)
    assert failed.stage == "asr"
    assert failed.provider == "cloud"
    assert failed.type == "openai"
    assert failed.host == "api.example.com"
    assert failed.error == "ConnectionRefusedError"
    assert failed.duration_ms >= 0
    assert failed.agent == "poet"
    # The fields every conversation event carries, which is what made
    # this findable at all.
    assert failed.session and failed.device


async def test_a_failing_llm_provider_is_reported_as_the_llm(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failed = await reply_with("llm", ConnectionRefusedError("no route"), caplog)
    assert failed.stage == "llm"
    assert failed.error == "ConnectionRefusedError"


async def test_a_failing_tts_provider_is_reported_as_the_tts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failed = await reply_with("tts", ConnectionRefusedError("no route"), caplog)
    assert failed.stage == "tts"
    assert failed.error == "ConnectionRefusedError"


async def test_a_timeout_is_distinguishable_from_a_refusal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A blocked host that drops rather than rejects shows up as a wait,
    and the wait is the diagnosis. The class is in `error`, and the
    sentence says which kind of failure it was."""
    failed = await reply_with("tts", TimeoutError(), caplog)
    assert failed.error == "TimeoutError"
    assert "timed out" in failed.getMessage()

    caplog.clear()
    refused = await reply_with("tts", ConnectionRefusedError("no route"), caplog)
    assert "failed" in refused.getMessage()


async def test_a_failing_tts_is_reported_once_and_not_blamed_on_the_llm(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The stream is watched around its own iteration rather than
    around the loop that consumes it, so a sentence that fails to
    synthesize is one failure, not two."""
    await reply_with("tts", ConnectionRefusedError("no route"), caplog)
    assert [record.stage for record in events(caplog, "provider_failed")] == ["tts"]


async def test_a_tool_call_is_logged_with_its_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    script = ScriptedLlm([[call("ghost_tool")], "I could not do that."])
    session = session_for(base_config(), BOTH_MAC, {"poet": script})
    with caplog.at_level("INFO"):
        await run_reply(session, "do it")

    logged = only(caplog, "tool_call")
    # A name no namespace publishes is the model's own invention, so the
    # event says which namespace was reached into and names nothing
    # (#120).
    assert logged.source == "unknown"
    assert not hasattr(logged, "tool")
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
    assert line["duration_s"] > 0
    assert line["device"] == DEVICE_MAC.lower()
    assert line["session"]
    # A record is one line, whatever is in the text.
    assert "\n" not in json.dumps(line)
