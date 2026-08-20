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

import pytest
from fastapi.testclient import TestClient

from tests.support.configs import LONG_REPLY, config_with_agent
from tests.support.events import events, only
from tests.support.providers import ConfirmingAsr, GatedAsr, ScriptedLlm, ScriptedVad
from tests.support.sessions import (
    end_utterance,
    realtime_session,
    reply_in_flight,
    start_reply,
    wait_for_reply,
)
from tests.support.sockets import spoken
from tests.support.wire import (
    assert_endpointed_speech,
    collect_reply,
    collect_until,
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
from vinga_server.app import create_app
from vinga_server.audio.opus import OpusEncoder
from vinga_server.providers import AsrResult, ProviderIdentity, Turn

# A reply of about four seconds: long enough that an interruption sent
# right after the first sentence starts lands mid-stream, short enough
# that a test asserting the reply survived does not sit through eight.
SLOW_REPLY = (
    "This reply runs on for roughly four seconds, which leaves an "
    "interruption plenty of stream left to land in."
)


def reply_task(session):
    """The reply task in flight, named here because two of these tests
    claim the gate did not replace it and `replying()` answers the same
    either way. `sessions.py` carries the justification."""
    return reply_in_flight(session)


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
    script = ScriptedLlm(["You said 800 ms."])
    session, socket = realtime_session(
        config_with_agent(), asr, ScriptedVad(600), {"assistant": script}
    )
    head = speech_pcm(320)
    tail = speech_pcm(480)

    with caplog.at_level("INFO"):
        await session.runtime.audio(head)
        await end_utterance(session)
        await asyncio.sleep(0.05)  # the reply is now held inside ASR
        await session.runtime.audio(tail)
        await end_utterance(session)
        asr.release.set()
        await wait_for_reply(session)

    assert asr.pcms == [head, head + tail]
    # The event says how long the merged utterance was; what was heard
    # in it is what the model was asked below, since the events carry no
    # transcript (#120).
    assert only(caplog, "heard").duration_s == 0.8
    merged = only(caplog, "barge_in_merged")
    assert merged.speech_ms == 600
    # One turn answered the whole sentence: the model saw the merged
    # utterance and nothing before it, and the device was told one
    # sentence.
    (asked, _tools, _choice) = script.seen[-1]
    assert list(asked) == [Turn("user", "800 ms")]
    assert spoken(socket) == ["You said 800 ms."]


async def test_an_unconfirmed_barge_in_pauses_and_resumes_the_reply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Gate 4, empty transcript: the frames pause the moment the gate is
    # reached, ASR hears nothing in the interruption, and the same
    # reply resumes with its pacing clock shifted by the pause, so the
    # stream picks up where it stopped instead of bursting.
    held = "Hold the thought while this sentence finishes playing out loud."
    config = config_with_agent(server={"barge_in_refractory_ms": 0})
    asr = ConfirmingAsr(AsrResult(text=""))
    script = ScriptedLlm([held, "A second answer nobody should need."])
    session, socket = realtime_session(config, asr, ScriptedVad(600), {"assistant": script})

    with caplog.at_level("INFO"):
        start_reply(session, speech_pcm(600))
        reply = reply_task(session)
        while socket.frames < 3:
            await asyncio.sleep(0.02)
        # White-box, and the five reads below it are the same one. The
        # pacing clock and the gate that holds it are the edge's own
        # state, and what they do is shift the cadence of frames going
        # out. Observing that publicly means measuring the arrival times
        # of paced audio against a wall clock and inferring the shift,
        # which is a race dressed as an assertion; the frames-frozen
        # check beside it is the observable half, and this is the half
        # that says why they froze and that the clock moved with them
        # rather than the stream bursting to catch up afterwards.
        pace_before = session._pace_start

        await session.runtime.audio(speech_pcm(600))
        finish = asyncio.create_task(end_utterance(session))
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
        assert reply_task(session) is reply
        await wait_for_reply(session)

    suppressed = only(caplog, "barge_in_suppressed")
    assert suppressed.reason == "no_transcript"
    assert suppressed.speech_ms == 600
    assert events(caplog, "barge_in") == []
    assert asr.calls == 2
    # One reply, one sentence, and the model was asked once: a cancelled
    # and restarted reply would have asked it again.
    assert spoken(socket) == [held]
    assert len(script.seen) == 1
    assert list(script.seen[0][0]) == [Turn("user", "the question")]


async def test_a_failed_confirmation_is_reported_as_the_provider_failure_it_is(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The confirmation is a second, separate ASR call, and the reply
    survives it failing: playback resumes and the user hears nothing
    wrong. That makes it exactly the failure an operator would never
    find without an event (#53)."""

    class FailingConfirmation(ConfirmingAsr):
        identity = ProviderIdentity(stage="asr", name="ears", type="openai", host="api.example.com")

        async def transcribe(
            self, pcm: bytes, sample_rate: int, language_hint: str | None = None
        ) -> AsrResult:
            self.calls += 1
            if self.calls == 1:
                return AsrResult(text="the question")
            raise TimeoutError

    config = config_with_agent(
        llm_reply="Hold the thought while this sentence finishes playing out loud.",
        server={"barge_in_refractory_ms": 0},
    )
    session, socket = realtime_session(
        config, FailingConfirmation(AsrResult(text="")), ScriptedVad(600)
    )

    with caplog.at_level("INFO"):
        start_reply(session, speech_pcm(600))
        reply = reply_task(session)
        while socket.frames < 3:
            await asyncio.sleep(0.02)
        await session.runtime.audio(speech_pcm(600))
        await end_utterance(session)
        # The reply lives, which is why this needs saying out loud.
        assert reply_task(session) is reply
        await wait_for_reply(session)

    (failed,) = [r for r in caplog.records if getattr(r, "event", None) == "provider_failed"]
    assert failed.stage == "asr"
    assert failed.provider == "ears"
    assert failed.host == "api.example.com"
    assert failed.error == "TimeoutError"
    assert "timed out" in failed.getMessage()


async def test_a_confirmed_barge_in_reuses_the_transcript_and_the_lock(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Gate 4, non-empty transcript: the confirmation is the cancel
    # decision and the new reply's ASR in one. It runs once (two calls
    # total: the first reply's own, then the confirmation), `heard`
    # fires once for the interruption with its language fields, and the
    # language lock takes effect. What was actually heard is the history
    # at the end: the events carry no transcript (#120).
    config = config_with_agent(server={"barge_in_refractory_ms": 0})
    asr = ConfirmingAsr(
        AsrResult(
            text="stop and listen",
            language="es",
            language_confidence=0.9,
            lock_language="es",
        )
    )
    asr.release.set()
    script = ScriptedLlm([SLOW_REPLY, "Answering stop and listen."])
    session, socket = realtime_session(config, asr, ScriptedVad(700), {"assistant": script})

    with caplog.at_level("INFO"):
        start_reply(session, speech_pcm(600))
        while socket.frames < 3:
            await asyncio.sleep(0.02)
        await session.runtime.audio(speech_pcm(600))
        await end_utterance(session)
        await wait_for_reply(session)

    assert asr.calls == 2
    # White-box: what the lock does is ride along as the language hint of
    # the next transcription, and this scenario has no next one. That the
    # session kept it is what says an interruption did not reset it, and
    # a session's own memory of a lock is on no interface.
    assert session.runtime._asr_language == "es"
    first, second = events(caplog, "heard")
    assert first.duration_s > 0
    assert second.language == "es"
    assert second.language_confidence == 0.9
    barged = only(caplog, "barge_in")
    assert barged.speech_ms == 700
    assert barged.speaking_ms >= 0
    # The cut sentence never finished sending, so it is not history: the
    # second round saw both user turns and nothing the first reply said.
    assert list(script.seen[-1][0]) == [
        Turn("user", "the question"),
        Turn("user", "stop and listen"),
    ]
    assert spoken(socket) == [SLOW_REPLY, "Answering stop and listen."]
