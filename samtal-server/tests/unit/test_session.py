"""The websocket session, driven the way the firmware drives it.

The client side here plays the device: same headers as `Ota::SetupHttp`
sets, same hello as `WebsocketProtocol::OpenAudioChannel` sends, and the
same Opus codec the server uses, running the client's leg of the audio.
The pipeline behind the session is the mock providers: the ASR answers
a configured transcript (`{ms}` becomes the utterance duration), the
LLM replies "You said <transcript>.", and the TTS speaks a tone whose
length follows the text.
"""

import asyncio
import contextlib
import json
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import samtal_server.device.session as session_module
import samtal_server.runtime.pipeline as pipeline_module
from samtal_server.app import create_app
from samtal_server.audio import rms
from samtal_server.audio.opus import OpusEncoder
from samtal_server.audio.resample import Resampler
from samtal_server.config import Config
from samtal_server.protocol import framing
from samtal_server.providers import Turn, build_agent_providers
from samtal_server.runtime.pipeline import AgentNotAllowed, bespoke_runtime_factory
from samtal_server.runtime.speech import _Synthesis
from samtal_server.tools.mcp import McpServers
from tests.support.configs import (
    BOTH_MAC,
    DEVICE_HELLO,
    DEVICE_MAC,
    ENDPOINT_SILENCE_MS,
    FRAME_BYTES,
    FRAME_MS,
    LONG_REPLY,
    OUTPUT_RATE,
    POET_MAC,
    POET_TONE,
    SAMPLE_RATE,
    TUTOR_TONE,
    config_with_agent,
)
from tests.support.sockets import RecordingSocket
from tests.support.wire import (
    assert_endpointed_speech,
    collect_reply,
    collect_until,
    connect,
    endpoint_silence,
    heard_ms,
    is_reply_start,
    is_transcript,
    say_something,
    send_pcm,
    sentences,
    shake_hands,
    speech_pcm,
    tone_strength,
)

# The mock TTS formula, for computing expected reply durations.
TTS_MS_PER_CHAR = 40
TTS_MIN_MS = 240


TUTOR_MAC = "aa:bb:cc:dd:ee:02"
UNBOUND_MAC = "aa:bb:cc:dd:ee:04"


def two_persona_config() -> Config:
    """Two agents that share everything but their prompt and their voice,
    the shared half coming from agent_defaults. The mock LLM quotes the
    prompt it was handed, so a reply names the agent that spoke it."""
    return Config(
        providers={
            "llm": {"mock": {"type": "mock", "reply": "{system} heard {text}."}},
            "asr": {"mock": {"type": "mock", "text": "{ms} ms"}},
            "tts": {
                "tenor": {"type": "mock", "tone_hz": POET_TONE},
                "alto": {"type": "mock", "tone_hz": TUTOR_TONE},
            },
            "vad": {"mock": {"type": "mock"}},
        },
        agent_defaults={"llm": "mock", "asr": "mock", "vad": "mock"},
        agents={
            "poet": {"prompt": "POET", "tts": "tenor"},
            "tutor": {"prompt": "TUTOR", "tts": "alto"},
        },
        devices={POET_MAC: ["poet"], TUTOR_MAC: "tutor", BOTH_MAC: ["tutor", "poet"]},
        default_agent="poet",
    )


def audio_ms(audio: bytes) -> float:
    return len(audio) / 2 / OUTPUT_RATE * 1000


def expected_tone_ms(spoken: list[str]) -> float:
    return sum(max(TTS_MIN_MS, TTS_MS_PER_CHAR * len(text)) for text in spoken)


def test_the_handshake_answers_a_firmware_hello() -> None:
    with TestClient(create_app(config_with_agent())) as client:
        with connect(client) as websocket:
            reply = shake_hands(websocket)
    assert reply["type"] == "hello"
    assert reply["transport"] == "websocket"
    assert reply["session_id"]
    assert reply["audio_params"] == {
        "format": "opus",
        "sample_rate": OUTPUT_RATE,
        "channels": 1,
        "frame_duration": 60,
    }


def test_listen_stop_gets_a_spoken_reply_with_transcript_and_sentences() -> None:
    with TestClient(create_app(config_with_agent())) as client:
        with connect(client) as websocket:
            reply = shake_hands(websocket)
            session_id = reply["session_id"]
            websocket.send_text(
                json.dumps(
                    {"session_id": session_id, "type": "listen", "state": "start", "mode": "manual"}
                )
            )
            send_pcm(websocket, speech_pcm(600), OpusEncoder())
            websocket.send_text(
                json.dumps({"session_id": session_id, "type": "listen", "state": "stop"})
            )
            texts, audio = collect_reply(websocket)

    (stt,) = [m for m in texts if m["type"] == "stt"]
    assert stt["text"] == "hello"
    states = [m["state"] for m in texts if m["type"] == "tts"]
    assert states[0] == "start"
    assert states[-1] == "stop"
    assert sentences(texts) == ["You said hello."]
    assert all(m["session_id"] == session_id for m in texts)
    # The audio is the mock TTS tone for the sentence, plus at most one
    # frame of padding from the encoder flush.
    assert abs(audio_ms(audio) - expected_tone_ms(sentences(texts))) <= FRAME_MS
    assert rms(audio) > 1000


def test_trailing_silence_ends_the_utterance_without_listen_stop() -> None:
    # Auto and realtime modes never send listen stop; the endpointer must
    # fire on its own once the speech goes quiet.
    with TestClient(create_app(config_with_agent())) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "auto"}))
            encoder = OpusEncoder()
            send_pcm(websocket, speech_pcm(600), encoder)
            send_pcm(websocket, b"\x00" * (FRAME_BYTES * 20), encoder)
            texts, audio = collect_reply(websocket)
    assert sentences(texts) == ["You said hello."]
    assert rms(audio) > 100


def test_the_reply_is_spoken_sentence_by_sentence() -> None:
    config = config_with_agent(llm_reply="First point about {text}. And a second one.")
    with TestClient(create_app(config)) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
            send_pcm(websocket, speech_pcm(300), OpusEncoder())
            websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
            texts, audio = collect_reply(websocket)
    assert sentences(texts) == ["First point about hello.", "And a second one."]
    assert abs(audio_ms(audio) - expected_tone_ms(sentences(texts))) <= FRAME_MS


def test_an_empty_transcript_still_closes_with_tts_stop() -> None:
    # The device (in auto mode) waits for tts stop before listening again,
    # so hearing nothing must still answer with an empty start/stop pair.
    with TestClient(create_app(config_with_agent(asr_text=""))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
            send_pcm(websocket, speech_pcm(300), OpusEncoder())
            websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
            texts, audio = collect_reply(websocket)
    assert [m["state"] for m in texts if m["type"] == "tts"] == ["start", "stop"]
    assert [m for m in texts if m["type"] == "stt"] == []
    assert audio == b""


def test_abort_discards_the_buffered_utterance() -> None:
    with TestClient(create_app(config_with_agent(asr_text="{ms}"))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
            send_pcm(websocket, speech_pcm(600), OpusEncoder())
            websocket.send_text(json.dumps({"type": "abort", "reason": "wake_word_detected"}))
            # After the abort, a stop with an empty buffer must not speak.
            websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
            websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
            send_pcm(websocket, speech_pcm(240), OpusEncoder())
            websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
            texts, _ = collect_reply(websocket)
    # Only the post-abort utterance was transcribed.
    assert 180 <= heard_ms(texts) <= 300


def test_abort_during_a_streaming_reply_does_not_eat_the_next_utterance() -> None:
    # The device barging in mid-reply and speaking again immediately must
    # get the new utterance answered: cancellation of the reply task is
    # awaited, never left in flight to shadow the follow-up.
    with TestClient(create_app(config_with_agent(asr_text="{ms}"))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
            send_pcm(websocket, speech_pcm(1200), OpusEncoder())
            websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
            # The reply is now streaming, paced at the frame cadence.
            # Barge in and speak again straight away.
            websocket.send_text(json.dumps({"type": "abort", "reason": "wake_word_detected"}))
            websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
            send_pcm(websocket, speech_pcm(240), OpusEncoder())
            websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
            # Skip whatever the aborted reply got out, up to its tts stop.
            collect_reply(websocket)
            texts, _ = collect_reply(websocket)
    assert 180 <= heard_ms(texts) <= 300


def test_a_realtime_utterance_sheds_the_silence_that_came_before_it() -> None:
    # The #14 regression: a realtime session buffers the whole gap since
    # the previous utterance (reply playback, thinking time), and every
    # utterance after the first carried it all to ASR. The trim keeps
    # the speech, a pre-roll so the first phoneme survives, and the
    # trailing window the endpointer sat through; the gap itself goes.
    config = config_with_agent(asr_text="{ms} ms")
    pre_roll_ms = config.server.utterance_pre_roll_ms
    with TestClient(create_app(config)) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            websocket.send_text(
                json.dumps({"type": "listen", "state": "start", "mode": "realtime"})
            )
            encoder = OpusEncoder()
            # Sit quiet for five seconds, well over any pre-roll...
            send_pcm(websocket, b"\x00" * (FRAME_BYTES * 5000 // FRAME_MS), encoder)
            # ...then speak.
            send_pcm(websocket, speech_pcm(600), encoder)
            endpoint_silence(websocket, encoder)
            texts, _ = collect_reply(websocket)

    assert 600 + 600 <= heard_ms(texts) <= 600 + pre_roll_ms + ENDPOINT_SILENCE_MS + 180


def test_realtime_mode_serves_a_second_utterance_without_listen_start() -> None:
    # A realtime device asks to listen once and then streams its mic for
    # the rest of the connection. Nothing re-arms the server, so the
    # server must never disarm: issue #10 is one exchange per session.
    with TestClient(create_app(config_with_agent(asr_text="{ms}"))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            encoder = OpusEncoder()
            websocket.send_text(
                json.dumps({"type": "listen", "state": "start", "mode": "realtime"})
            )
            send_pcm(websocket, speech_pcm(600), encoder)
            endpoint_silence(websocket, encoder)
            first, _ = collect_reply(websocket)
            # Straight on to the next question, with no listen start.
            send_pcm(websocket, speech_pcm(240), encoder)
            endpoint_silence(websocket, encoder)
            second, _ = collect_reply(websocket)
    assert_endpointed_speech(first, 600)
    assert_endpointed_speech(second, 240)


def test_realtime_barge_in_cancels_the_reply_in_flight() -> None:
    # The point of listening through playback: speech that lands while
    # the server is talking cuts the reply off and is answered, with no
    # abort and no listen message anywhere. Only the mic said so. The
    # interruption is 600 ms of speech, past the default minimum-speech
    # floor, and the refractory window is off so the cut can land right
    # after playback starts; the mock ASR then transcribes something,
    # which is what confirms the cancel.
    config = config_with_agent(
        asr_text="{ms}", llm_reply=LONG_REPLY, server={"barge_in_refractory_ms": 0}
    )
    with TestClient(create_app(config)) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            encoder = OpusEncoder()
            websocket.send_text(
                json.dumps({"type": "listen", "state": "start", "mode": "realtime"})
            )
            send_pcm(websocket, speech_pcm(600), encoder)
            endpoint_silence(websocket, encoder)
            opening, opening_audio = collect_until(websocket, is_reply_start)
            # The long reply is now streaming, paced at the frame cadence.
            send_pcm(websocket, speech_pcm(600), encoder)
            endpoint_silence(websocket, encoder)
            cut, cut_audio = collect_reply(websocket)
            answer, _ = collect_until(websocket, is_transcript)
    assert sentences(opening + cut)[0].startswith(LONG_REPLY[:20])
    # Cut off well short of the whole reply, and the interruption
    # answered as an utterance in its own right.
    assert audio_ms(opening_audio + cut_audio) < TTS_MS_PER_CHAR * len(LONG_REPLY) / 2
    assert_endpointed_speech(answer, 600)


def test_realtime_without_barge_in_drops_frames_during_playback_but_hears_after() -> None:
    # The fallback for a board whose echo cancellation leaks its own
    # voice back: the reply plays to the end rather than interrupting
    # itself, and the conversation is still multi-turn afterwards.
    config = config_with_agent(
        asr_text="{ms}", llm_reply=LONG_REPLY, server={"barge_in": False}
    )
    with TestClient(create_app(config)) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            encoder = OpusEncoder()
            websocket.send_text(
                json.dumps({"type": "listen", "state": "start", "mode": "realtime"})
            )
            send_pcm(websocket, speech_pcm(600), encoder)
            endpoint_silence(websocket, encoder)
            opening, opening_audio = collect_until(websocket, is_reply_start)
            # Whatever the mic streams now is ignored, the assistant's own
            # voice included.
            send_pcm(websocket, speech_pcm(240), encoder)
            endpoint_silence(websocket, encoder)
            rest, rest_audio = collect_reply(websocket)
            # And once the reply is over, still no listen start needed.
            send_pcm(websocket, speech_pcm(240), encoder)
            endpoint_silence(websocket, encoder)
            answer, _ = collect_until(websocket, is_transcript)
    spoken = sentences(opening + rest)
    assert spoken == [LONG_REPLY]
    assert abs(audio_ms(opening_audio + rest_audio) - expected_tone_ms(spoken)) <= FRAME_MS
    assert_endpointed_speech(answer, 240)


def test_abort_in_realtime_mode_keeps_the_session_listening() -> None:
    # An abort ends the reply, not the listening: the realtime device is
    # still streaming, and there is no listen start coming to revive a
    # session that turned itself off here.
    with TestClient(create_app(config_with_agent(asr_text="{ms}"))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            encoder = OpusEncoder()
            websocket.send_text(
                json.dumps({"type": "listen", "state": "start", "mode": "realtime"})
            )
            send_pcm(websocket, speech_pcm(1200), encoder)
            endpoint_silence(websocket, encoder)
            websocket.send_text(json.dumps({"type": "abort", "reason": "wake_word_detected"}))
            send_pcm(websocket, speech_pcm(240), encoder)
            endpoint_silence(websocket, encoder)
            # Skip whatever the aborted reply got out, up to its tts stop.
            collect_reply(websocket)
            texts, _ = collect_reply(websocket)
    assert_endpointed_speech(texts, 240)


def test_auto_mode_still_requires_a_new_listen_start_after_the_reply() -> None:
    # The other half of the same rule: an auto-mode device shuts its mic
    # down over the reply and re-arms with a fresh listen start, so what
    # arrives in between is not the server's to hear.
    with TestClient(create_app(config_with_agent(asr_text="{ms}"))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            encoder = OpusEncoder()
            websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "auto"}))
            send_pcm(websocket, speech_pcm(600), encoder)
            endpoint_silence(websocket, encoder)
            collect_reply(websocket)
            # Dropped: no listen start has re-armed the session yet.
            send_pcm(websocket, speech_pcm(600), encoder)
            endpoint_silence(websocket, encoder)
            websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "auto"}))
            send_pcm(websocket, speech_pcm(240), encoder)
            endpoint_silence(websocket, encoder)
            texts, _ = collect_reply(websocket)
    assert_endpointed_speech(texts, 240)


def device_session(
    config: Config,
    mac: str,
    providers: dict[str, Any] | None = None,
    memory: Any = None,
    fillers: dict[str, Any] | None = None,
    websocket: Any = None,
    mcp_servers: McpServers | None = None,
    conversations: Any = None,
) -> session_module.DeviceSession:
    """A device session with a real bespoke runtime behind it, built the
    way `run` builds one: the agents resolved from the binding, then the
    factory called with them. Every test that drives a session below the
    websocket goes through here, so the composition root has one shape
    in the tests as well as in the server.

    `mcp_servers` is the running registry, which a test that is about
    tools supplies; an empty one is what every other test here needs,
    and is what a deployment with no MCP entries has. `conversations` is
    the store a turn's record is handed to, None everywhere but in the
    suite that is about the record, which is what a deployment that has
    not asked for one has."""
    factory = bespoke_runtime_factory(
        config,
        providers if providers is not None else build_agent_providers(config),
        mcp_servers if mcp_servers is not None else McpServers({}),
        memory,
        fillers if fillers is not None else {},
        conversations,
    )
    session = session_module.DeviceSession(cast(Any, websocket), config, factory)
    session._agents = config.agents_for_device(mac)
    session.runtime = factory(session, session._events, session._agents)
    return session


async def test_a_barge_in_keeps_the_sentences_the_user_heard() -> None:
    # The history after an interruption has to be what was heard: every
    # sentence whose audio went out, and not the one cut off partway.
    # Getting this wrong either way misleads whoever speaks next, since
    # the reply that answers the interruption is written against it.
    config = config_with_agent(asr_text="hello", llm_reply=f"Ready. {LONG_REPLY}")
    socket = RecordingSocket()
    session = device_session(config, DEVICE_MAC, websocket=socket)

    reply = asyncio.create_task(session.runtime._reply(speech_pcm(600)))
    await asyncio.sleep(0.6)
    heard_frames = socket.frames
    reply.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await reply

    # "Ready." was spoken in full and survives; the long sentence was
    # audible, interrupted, and left out.
    assert session.runtime._turns == [Turn("user", "hello"), Turn("assistant", "Ready.")]
    assert heard_frames > TTS_MIN_MS // FRAME_MS


async def test_only_a_sentence_whose_audio_finished_counts_as_spoken() -> None:
    # What the history keeps has to be what the user actually heard.
    # Barge-in cancels a reply in the middle of sending a sentence, and
    # the model having written that sentence is not evidence anybody
    # heard it: frames are paced, so being written and being heard are
    # seconds apart.
    config = two_persona_config()
    socket = RecordingSocket()
    session = device_session(config, TUTOR_MAC, websocket=socket)
    assert session.runtime._providers is not None
    resampler = Resampler(
        session.runtime._providers.tts.sample_rate, session_module.OUTPUT_AUDIO.sample_rate
    )

    spoken: list[str] = []
    failures: list[tuple[BaseException, float]] = []

    def record_failure(exc: BaseException, elapsed: float) -> None:
        failures.append((exc, elapsed))

    def record_first_audio(elapsed_ms: int) -> None:
        # What a reply does with the measurement is the reply's; this
        # test drives one sentence, with no reply around it to have a
        # first synthesis.
        return None

    tts = session.runtime._providers.tts
    await session.runtime._speak(
        _Synthesis("Short and finished.", tts, record_failure, record_first_audio),
        resampler,
        spoken,
    )
    finished_frames = socket.frames

    cut = asyncio.create_task(
        session.runtime._speak(
            _Synthesis(LONG_REPLY, tts, record_failure, record_first_audio),
            resampler,
            spoken,
        )
    )
    await asyncio.sleep(0.1)
    cut.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cut

    # The interrupted sentence was audible, and is still not recorded.
    assert socket.frames > finished_frames
    assert spoken == ["Short and finished."]
    # The mock voice works, so neither synthesis has anything to report:
    # one finished and one was cancelled, and a failure from either would
    # mean this test is no longer about what counts as spoken.
    assert failures == []


async def test_the_utterance_buffer_keeps_only_a_bounded_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Always listening means buffering the silences too, so the buffer
    # keeps a bounded tail of recent audio rather than the whole session.
    cap = SAMPLE_RATE * 2 * 2  # two seconds
    monkeypatch.setattr(pipeline_module, "UTTERANCE_TAIL_BYTES", cap)
    config = two_persona_config()
    session = device_session(config, TUTOR_MAC)
    session._listen_mode = "realtime"
    session.listening = True

    encoder = OpusEncoder()
    for packet in encoder.encode(b"\x00" * (FRAME_BYTES * 66)):
        await session._handle_audio(framing.wrap(1, packet))

    # Trimmed to the cap, give or take the frame that crossed it, and
    # the quiet room never looked like an utterance.
    assert cap - FRAME_BYTES <= len(session.runtime._utterance) <= cap + FRAME_BYTES


def test_version_2_framing_round_trips() -> None:
    with TestClient(create_app(config_with_agent())) as client:
        with connect(client, version=2) as websocket:
            shake_hands(websocket, version=2)
            websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
            send_pcm(websocket, speech_pcm(300), OpusEncoder(), version=2)
            websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
            texts, audio = collect_reply(websocket, version=2)
    assert sentences(texts) == ["You said hello."]
    assert rms(audio) > 1000


def test_frames_sent_while_not_listening_are_dropped() -> None:
    with TestClient(create_app(config_with_agent(asr_text="{ms}"))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            encoder = OpusEncoder()
            # No listen start yet: this must accumulate nothing.
            send_pcm(websocket, speech_pcm(600), encoder)
            websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
            send_pcm(websocket, speech_pcm(240), encoder)
            websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
            texts, _ = collect_reply(websocket)
    assert 180 <= heard_ms(texts) <= 300


def test_unknown_and_malformed_messages_do_not_end_the_session() -> None:
    with TestClient(create_app(config_with_agent())) as client:
        with connect(client) as websocket:
            reply = shake_hands(websocket)
            websocket.send_text('{"type": "goodbye"}')
            websocket.send_text("not json")
            websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
            send_pcm(websocket, speech_pcm(240), OpusEncoder())
            websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
            texts, _ = collect_reply(websocket)
    assert reply["type"] == "hello"
    assert texts


@pytest.mark.parametrize(
    ("mac", "marker", "tone", "other_tone"),
    [
        (POET_MAC, "POET", POET_TONE, TUTOR_TONE),
        (TUTOR_MAC, "TUTOR", TUTOR_TONE, POET_TONE),
        # Bound to both: the first entry is the agent the conversation
        # starts on, and nothing else selects it.
        (BOTH_MAC, "TUTOR", TUTOR_TONE, POET_TONE),
        # Bound to nothing at all: default_agent, which is the poet here.
        (UNBOUND_MAC, "POET", POET_TONE, TUTOR_TONE),
    ],
)
def test_a_device_gets_the_prompt_and_the_voice_of_its_own_agent(
    mac: str, marker: str, tone: float, other_tone: float
) -> None:
    with TestClient(create_app(two_persona_config())) as client:
        with connect(client, device_id=mac) as websocket:
            shake_hands(websocket)
            texts, audio = say_something(websocket)
    # The reply quotes the prompt the session handed its LLM.
    assert sentences(texts)[0].startswith(marker)
    # And it came back in that agent's voice.
    assert tone_strength(audio, tone) > 10 * tone_strength(audio, other_tone)


def test_two_devices_hold_two_conversations_at_once() -> None:
    # Same server, two open sessions, two personas: neither the prompt nor
    # the voice nor the audio buffered so far may leak between them.
    with TestClient(create_app(two_persona_config())) as client:
        with (
            connect(client, device_id=POET_MAC) as poet,
            connect(client, device_id=TUTOR_MAC) as tutor,
        ):
            shake_hands(poet)
            shake_hands(tutor)
            # The poet is mid-utterance, buffering and not yet endpointed,
            # while the tutor speaks a shorter one and gets answered.
            poet.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
            send_pcm(poet, speech_pcm(600), OpusEncoder())
            tutor_texts, tutor_audio = say_something(tutor, duration_ms=240)
            poet.send_text(json.dumps({"type": "listen", "state": "stop"}))
            poet_texts, poet_audio = collect_reply(poet)

    assert sentences(poet_texts)[0].startswith("POET")
    assert sentences(tutor_texts)[0].startswith("TUTOR")
    assert tone_strength(poet_audio, POET_TONE) > 10 * tone_strength(poet_audio, TUTOR_TONE)
    assert tone_strength(tutor_audio, TUTOR_TONE) > 10 * tone_strength(tutor_audio, POET_TONE)
    # Each session heard only its own audio: the endpointers and utterance
    # buffers are per session, not per provider.
    assert 540 <= heard_ms(poet_texts) <= 660
    assert 180 <= heard_ms(tutor_texts) <= 300


def test_a_session_refuses_an_agent_its_device_is_not_bound_to() -> None:
    # The bound list is the boundary, enforced where the swap happens:
    # M6's switch_agent passes this a name a model chose, and an agent
    # that merely exists on the server is not one this device may reach.
    config = two_persona_config()
    session = device_session(config, TUTOR_MAC)

    with pytest.raises(AgentNotAllowed, match="poet"):
        session.runtime._activate_agent("poet")
    # Refused, and nothing swapped: the session still talks as the tutor.
    assert session._agent == "tutor"
    assert session.runtime._providers is not None
    assert session.runtime._know_how is not None
    assert session.runtime._know_how.text == "TUTOR"


def test_a_device_with_no_agent_is_turned_away() -> None:
    with TestClient(create_app(Config())) as client:
        with connect(client) as websocket:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                websocket.receive_text()
    assert excinfo.value.code == 1008
    assert "no agent" in excinfo.value.reason


def test_a_device_without_a_mac_is_turned_away() -> None:
    with TestClient(create_app(config_with_agent())) as client:
        for bad_id in (None, "not-a-mac"):
            with connect(client, device_id=bad_id) as websocket:
                with pytest.raises(WebSocketDisconnect) as excinfo:
                    websocket.receive_text()
            assert excinfo.value.code == 1008


@pytest.mark.parametrize(
    ("first_message", "reason_fragment"),
    [
        ('{"type": "listen", "state": "start"}', "hello first"),
        ("not json", "malformed hello"),
        (json.dumps(dict(DEVICE_HELLO, transport="mqtt")), "transport"),
        (
            json.dumps(dict(DEVICE_HELLO, audio_params={"format": "pcm"})),
            "opus",
        ),
        (json.dumps(dict(DEVICE_HELLO, version=9)), "version"),
    ],
)
def test_a_bad_opening_message_closes_the_connection(
    first_message: str, reason_fragment: str
) -> None:
    with TestClient(create_app(config_with_agent())) as client:
        with connect(client) as websocket:
            websocket.send_text(first_message)
            with pytest.raises(WebSocketDisconnect) as excinfo:
                websocket.receive_text()
    assert excinfo.value.code == 1002
    assert reason_fragment in excinfo.value.reason


def test_a_silent_client_is_closed_after_the_hello_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_module, "HELLO_TIMEOUT_S", 0.05)
    with TestClient(create_app(config_with_agent())) as client:
        with connect(client) as websocket:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                websocket.receive_text()
    assert excinfo.value.code == 1002
    assert "no hello" in excinfo.value.reason
