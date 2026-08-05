"""The gates in front of a barge-in.

An utterance the endpointer ends while a reply is in flight may cancel
that reply only on evidence of user speech: enough classified speech,
outside the playback-onset refractory window, and confirmed by a
transcript when nothing faster decides it. A reply still inside ASR is
holding the head of the user's own sentence, so there the barge-in
merges instead of destroying it. A manual `listen stop` mid-reply is a
deliberate act and keeps the unconditional cancel.

The websocket tests drive the gates the way the firmware would; the
session-level tests hold a reply inside a hand-gated ASR to hit the
windows a real socket cannot time.
"""

import asyncio
import json
from dataclasses import replace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

import samtal_server.session as session_module
from samtal_server.app import create_app
from samtal_server.audio.opus import OpusEncoder
from samtal_server.providers import AsrResult, Turn, build_agent_providers
from samtal_server.providers.mock import MockAsr
from tests.unit.test_session import (
    LONG_REPLY,
    RecordingSocket,
    assert_endpointed_speech,
    collect_reply,
    collect_until,
    config_with_agent,
    connect,
    endpoint_silence,
    heard_ms,
    is_reply_start,
    is_transcript,
    send_pcm,
    sentences,
    shake_hands,
    speech_pcm,
)
from tests.unit.test_session_events import events, only

# A reply of about four seconds: long enough that an interruption sent
# right after the first sentence starts lands mid-stream, short enough
# that a test asserting the reply survived does not sit through eight.
SLOW_REPLY = (
    "This reply runs on for roughly four seconds, which leaves an "
    "interruption plenty of stream left to land in."
)


class ScriptedEndpointer:
    """An endpointer whose answers the test writes down. The session
    reads speech_ms before resetting and these tests call
    _finish_utterance directly, so feeding is never exercised."""

    def __init__(self, speech_ms: float) -> None:
        self._speech_ms = speech_ms

    def feed(self, pcm: bytes) -> bool:
        return False

    def reset(self) -> None:
        return None

    def speech_start(self) -> int | None:
        return None

    def speech_ms(self) -> float:
        return self._speech_ms


class GatedAsr(MockAsr):
    """The mock ASR with a hand-operated gate: every call records the
    PCM it was handed and waits for release, so a test can hold a reply
    inside transcription while a barge-in lands."""

    def __init__(self) -> None:
        super().__init__(text="{ms} ms")
        self.pcms: list[bytes] = []
        self.release = asyncio.Event()

    async def transcribe(
        self, pcm: bytes, sample_rate: int, language_hint: str | None = None
    ) -> AsrResult:
        self.pcms.append(pcm)
        await self.release.wait()
        return await super().transcribe(pcm, sample_rate, language_hint)


class ConfirmingAsr:
    """First call is the reply's own ASR; every later call is a
    barge-in confirmation, gated on release, answering the scripted
    result."""

    def __init__(self, confirmation: AsrResult) -> None:
        self._confirmation = confirmation
        self.calls = 0
        self.release = asyncio.Event()

    async def transcribe(
        self, pcm: bytes, sample_rate: int, language_hint: str | None = None
    ) -> AsrResult:
        self.calls += 1
        if self.calls == 1:
            return AsrResult(text="the question")
        await self.release.wait()
        return self._confirmation


def realtime_session(config, asr) -> tuple[session_module.Session, RecordingSocket]:
    """A session mid-conversation on a realtime device, its ASR swapped
    for the test's."""
    socket = RecordingSocket()
    session = session_module.Session(cast(Any, socket), config, build_agent_providers(config))
    session._agents = ["assistant"]
    session._activate_agent("assistant")
    session._listen_mode = "realtime"
    session.listening = True
    assert session._providers is not None
    session._providers = replace(session._providers, asr=asr)
    return session, socket


def test_a_short_blip_does_not_interrupt_the_reply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Gate 1: an interruption carrying less classified speech than the
    # floor is a noise blip, whatever it sounded like; the reply plays
    # to the end and the session still answers afterwards.
    config = config_with_agent(asr_text="{ms}", llm_reply=SLOW_REPLY)
    with caplog.at_level("INFO"):
        with TestClient(create_app(config)) as client:
            with connect(client) as websocket:
                shake_hands(websocket)
                encoder = OpusEncoder()
                websocket.send_text(
                    json.dumps({"type": "listen", "state": "start", "mode": "realtime"})
                )
                send_pcm(websocket, speech_pcm(600), encoder)
                endpoint_silence(websocket, encoder)
                opening, _ = collect_until(websocket, is_reply_start)
                # 240 ms of speech, under the 500 ms default floor.
                send_pcm(websocket, speech_pcm(240), encoder)
                endpoint_silence(websocket, encoder)
                rest, _ = collect_reply(websocket)
                # The reply survived whole, and the session still hears.
                send_pcm(websocket, speech_pcm(600), encoder)
                endpoint_silence(websocket, encoder)
                answer, _ = collect_until(websocket, is_transcript)

    assert sentences(opening + rest) == [SLOW_REPLY]
    assert_endpointed_speech(answer, 600)
    suppressed = only(caplog, "barge_in_suppressed")
    assert suppressed.reason == "min_speech"
    assert 180 <= suppressed.speech_ms <= 300
    assert events(caplog, "barge_in") == []


def test_the_refractory_window_swallows_the_playback_onset(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Gate 3: right after the reply starts speaking, what the mic hears
    # is as likely the onset transient through the device's echo
    # cancellation as the user, so even sustained speech is dropped.
    # The window is raised far past the test's timing so landing inside
    # it is certain.
    config = config_with_agent(
        asr_text="{ms}",
        llm_reply=SLOW_REPLY,
        server={"barge_in_refractory_ms": 100_000},
    )
    with caplog.at_level("INFO"):
        with TestClient(create_app(config)) as client:
            with connect(client) as websocket:
                shake_hands(websocket)
                encoder = OpusEncoder()
                websocket.send_text(
                    json.dumps({"type": "listen", "state": "start", "mode": "realtime"})
                )
                send_pcm(websocket, speech_pcm(600), encoder)
                endpoint_silence(websocket, encoder)
                opening, _ = collect_until(websocket, is_reply_start)
                # Long enough to pass the minimum-speech floor.
                send_pcm(websocket, speech_pcm(600), encoder)
                endpoint_silence(websocket, encoder)
                rest, _ = collect_reply(websocket)

    assert sentences(opening + rest) == [SLOW_REPLY]
    suppressed = only(caplog, "barge_in_suppressed")
    assert suppressed.reason == "refractory"
    assert 540 <= suppressed.speech_ms <= 660
    assert events(caplog, "barge_in") == []


def test_a_manual_stop_mid_reply_still_cancels_unconditionally(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The gates read acoustic evidence; a user holding the button and
    # speaking is not acoustics. With every gate raised sky-high, a
    # manual listen stop still cuts the reply and gets answered.
    config = config_with_agent(
        asr_text="{ms}",
        llm_reply=LONG_REPLY,
        server={"barge_in_min_speech_ms": 100_000, "barge_in_refractory_ms": 100_000},
    )
    with caplog.at_level("INFO"):
        with TestClient(create_app(config)) as client:
            with connect(client) as websocket:
                shake_hands(websocket)
                websocket.send_text(
                    json.dumps({"type": "listen", "state": "start", "mode": "manual"})
                )
                send_pcm(websocket, speech_pcm(600), OpusEncoder())
                websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
                collect_until(websocket, is_reply_start)
                # The reply is streaming; the user presses and speaks.
                websocket.send_text(
                    json.dumps({"type": "listen", "state": "start", "mode": "manual"})
                )
                send_pcm(websocket, speech_pcm(240), OpusEncoder())
                websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
                collect_reply(websocket)  # the cut reply's tts stop
                answer, _ = collect_until(websocket, is_transcript)

    assert 180 <= heard_ms(answer) <= 300
    barged = only(caplog, "barge_in")
    assert 180 <= barged.speech_ms <= 300
    assert barged.speaking_ms >= 0
    assert events(caplog, "barge_in_suppressed") == []


async def test_a_barge_in_during_transcription_merges_the_sentence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Gate 2: the reply in flight was still transcribing the head of
    # the user's sentence when the continuation endpointed. Cancelling
    # it outright would eat the head; instead the head's PCM is
    # prepended and one reply answers the whole sentence. The mock ASR
    # embeds the duration it was handed, which pins the merged length
    # exactly: 320 ms of head plus 480 ms of tail is 800 ms.
    asr = GatedAsr()
    session, _ = realtime_session(config_with_agent(), asr)
    session._endpointer = ScriptedEndpointer(speech_ms=600)
    head = speech_pcm(320)
    tail = speech_pcm(480)

    with caplog.at_level("INFO"):
        session._utterance = bytearray(head)
        await session._finish_utterance(endpointed=True)
        await asyncio.sleep(0.05)  # the reply is now held inside ASR
        session._utterance = bytearray(tail)
        await session._finish_utterance(endpointed=True)
        asr.release.set()
        assert session._reply_task is not None
        await session._reply_task

    assert asr.pcms == [head, head + tail]
    heard = only(caplog, "heard")
    assert heard.text == "800 ms"
    assert heard.duration_s == 0.8
    merged = only(caplog, "barge_in_merged")
    assert merged.speech_ms == 600
    assert session._turns == [Turn("user", "800 ms"), Turn("assistant", "You said 800 ms.")]


async def test_an_unconfirmed_barge_in_pauses_and_resumes_the_reply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Gate 4, empty transcript: the frames pause the moment the gate is
    # reached, ASR hears nothing in the interruption, and the same
    # reply resumes with its pacing clock shifted by the pause, so the
    # stream picks up where it stopped instead of bursting.
    config = config_with_agent(
        llm_reply="Hold the thought while this sentence finishes playing out loud.",
        server={"barge_in_refractory_ms": 0},
    )
    asr = ConfirmingAsr(AsrResult(text=""))
    session, socket = realtime_session(config, asr)
    session._endpointer = ScriptedEndpointer(speech_ms=600)

    with caplog.at_level("INFO"):
        session._reply_task = asyncio.create_task(session._reply(speech_pcm(600)))
        reply = session._reply_task
        while socket.frames < 3:
            await asyncio.sleep(0.02)
        pace_before = session._pace_start

        session._utterance = bytearray(speech_pcm(600))
        finish = asyncio.create_task(session._finish_utterance(endpointed=True))
        await asyncio.sleep(0.05)
        # Paused: the confirmation is in flight and no frames move.
        assert not session._pace_resume.is_set()
        frozen = socket.frames
        await asyncio.sleep(0.3)
        assert socket.frames == frozen

        asr.release.set()
        await finish
        assert session._pace_resume.is_set()
        # The clock shifted by the pause, so the cadence survives it.
        assert pace_before is not None and session._pace_start is not None
        assert session._pace_start - pace_before >= 0.3
        # The same reply, never cancelled, plays to the end.
        assert session._reply_task is reply
        await reply

    suppressed = only(caplog, "barge_in_suppressed")
    assert suppressed.reason == "no_transcript"
    assert suppressed.speech_ms == 600
    assert events(caplog, "barge_in") == []
    assert asr.calls == 2
    assert session._turns == [
        Turn("user", "the question"),
        Turn("assistant", "Hold the thought while this sentence finishes playing out loud."),
    ]


async def test_a_confirmed_barge_in_reuses_the_transcript_and_the_lock(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Gate 4, non-empty transcript: the confirmation is the cancel
    # decision and the new reply's ASR in one. It runs once (two calls
    # total: the first reply's own, then the confirmation), `heard`
    # fires once for the interruption with the confirmed text and its
    # language fields, and the language lock takes effect.
    config = config_with_agent(
        llm_reply="Answering {text}.", server={"barge_in_refractory_ms": 0}
    )
    asr = ConfirmingAsr(
        AsrResult(
            text="stop and listen",
            language="es",
            language_confidence=0.9,
            lock_language="es",
        )
    )
    asr.release.set()
    session, socket = realtime_session(config, asr)
    session._endpointer = ScriptedEndpointer(speech_ms=700)

    with caplog.at_level("INFO"):
        session._reply_task = asyncio.create_task(session._reply(speech_pcm(600)))
        while socket.frames < 3:
            await asyncio.sleep(0.02)
        session._utterance = bytearray(speech_pcm(600))
        await session._finish_utterance(endpointed=True)
        assert session._reply_task is not None
        await session._reply_task

    assert asr.calls == 2
    assert session._asr_language == "es"
    first, second = events(caplog, "heard")
    assert first.text == "the question"
    assert second.text == "stop and listen"
    assert second.language == "es"
    assert second.language_confidence == 0.9
    barged = only(caplog, "barge_in")
    assert barged.speech_ms == 700
    assert barged.speaking_ms >= 0
    # The cut sentence never finished sending, so it is not history;
    # the answer to the confirmed transcript is.
    assert session._turns == [
        Turn("user", "the question"),
        Turn("user", "stop and listen"),
        Turn("assistant", "Answering stop and listen."),
    ]
