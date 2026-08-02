"""The websocket session, driven the way the firmware drives it.

The client side here plays the device: same headers as `Ota::SetupHttp`
sets, same hello as `WebsocketProtocol::OpenAudioChannel` sends, and the
same Opus codec the server uses, running the client's leg of the audio.
The pipeline behind the session is the mock providers: the ASR answers
a configured transcript (`{ms}` becomes the utterance duration), the
LLM replies "You said <transcript>.", and the TTS speaks a tone whose
length follows the text.
"""

import json
import math
import re
import struct

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import samtal_server.session as session_module
from samtal_server.app import create_app
from samtal_server.audio import rms
from samtal_server.audio.opus import OpusDecoder, OpusEncoder
from samtal_server.config import Config
from samtal_server.protocol import framing
from samtal_server.ws import WEBSOCKET_PATH

DEVICE_MAC = "AA:BB:CC:DD:EE:FF"
DEVICE_UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"

SAMPLE_RATE = 16000
OUTPUT_RATE = 24000
FRAME_MS = 60
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2

# The mock TTS formula, for computing expected reply durations.
TTS_MS_PER_CHAR = 40
TTS_MIN_MS = 240

DEVICE_HELLO = {
    "type": "hello",
    "version": 1,
    "features": {"mcp": True},
    "transport": "websocket",
    "audio_params": {
        "format": "opus",
        "sample_rate": 16000,
        "channels": 1,
        "frame_duration": 60,
    },
}


def config_with_agent(asr_text: str = "hello", llm_reply: str | None = None) -> Config:
    llm: dict[str, object] = {"type": "mock"}
    if llm_reply is not None:
        llm["reply"] = llm_reply
    return Config(
        providers={
            "llm": {"mock": llm},
            "asr": {"mock": {"type": "mock", "text": asr_text}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        agents={"assistant": dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")},
        default_agent="assistant",
    )


POET_MAC = "aa:bb:cc:dd:ee:01"
TUTOR_MAC = "aa:bb:cc:dd:ee:02"
BOTH_MAC = "aa:bb:cc:dd:ee:03"
UNBOUND_MAC = "aa:bb:cc:dd:ee:04"
POET_TONE = 440.0
TUTOR_TONE = 880.0


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


def connect(client: TestClient, device_id: str | None = DEVICE_MAC, version: int = 1):
    headers = {
        "Authorization": "Bearer ",
        "Protocol-Version": str(version),
        "Client-Id": DEVICE_UUID,
    }
    if device_id is not None:
        headers["Device-Id"] = device_id
    return client.websocket_connect(WEBSOCKET_PATH, headers=headers)


def shake_hands(websocket, version: int = 1) -> dict:
    hello = dict(DEVICE_HELLO, version=version)
    websocket.send_text(json.dumps(hello))
    return json.loads(websocket.receive_text())


def speech_pcm(duration_ms: int) -> bytes:
    samples = SAMPLE_RATE * duration_ms // 1000
    return b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 300 * n / SAMPLE_RATE)))
        for n in range(samples)
    )


def send_pcm(websocket, pcm: bytes, encoder: OpusEncoder, version: int = 1) -> None:
    for packet in encoder.encode(pcm):
        websocket.send_bytes(framing.wrap(version, packet))


def collect_reply(websocket, version: int = 1) -> tuple[list[dict], bytes]:
    """Read until tts stop, returning the JSON messages and decoded audio."""
    decoder = OpusDecoder(sample_rate=OUTPUT_RATE)
    texts: list[dict] = []
    audio = b""
    while True:
        received = websocket.receive()
        if received.get("text") is not None:
            message = json.loads(received["text"])
            texts.append(message)
            if message.get("type") == "tts" and message.get("state") == "stop":
                return texts, audio
        else:
            frame = framing.unwrap(version, received["bytes"])
            audio += decoder.decode(frame.payload)


def sentences(texts: list[dict]) -> list[str]:
    return [m["text"] for m in texts if m.get("type") == "tts" and m["state"] == "sentence_start"]


def heard_ms(texts: list[dict]) -> int:
    """The utterance duration the mock ASR embedded in the transcript."""
    (stt,) = [m for m in texts if m.get("type") == "stt"]
    match = re.search(r"\d+", stt["text"])
    assert match is not None, stt
    return int(match.group())


def audio_ms(audio: bytes) -> float:
    return len(audio) / 2 / OUTPUT_RATE * 1000


def expected_tone_ms(spoken: list[str]) -> float:
    return sum(max(TTS_MIN_MS, TTS_MS_PER_CHAR * len(text)) for text in spoken)


def tone_strength(audio: bytes, hz: float) -> float:
    """How much of `audio` sits at `hz`: one DFT bin, normalized, which is
    all it takes to tell two mock voices apart after an Opus round trip."""
    samples = struct.unpack(f"<{len(audio) // 2}h", audio)
    angles = [2 * math.pi * hz * n / OUTPUT_RATE for n in range(len(samples))]
    real = sum(s * math.cos(a) for s, a in zip(samples, angles, strict=True))
    imaginary = sum(s * math.sin(a) for s, a in zip(samples, angles, strict=True))
    return math.hypot(real, imaginary) / len(samples)


def say_something(websocket, duration_ms: int = 300) -> tuple[list[dict], bytes]:
    """One full turn: listen, speak, stop, and collect the reply."""
    websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
    send_pcm(websocket, speech_pcm(duration_ms), OpusEncoder())
    websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
    return collect_reply(websocket)


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
