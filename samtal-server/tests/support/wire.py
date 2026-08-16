"""Driving a session over a real websocket, and reading what came back.

What belongs here is the client's leg of the protocol: the handshake a
device makes, the frames it sends, the messages and audio it reads back,
and the small predicates and extractors a test needs to say what the
reply was. The client here plays the device, with the same headers
`Ota::SetupHttp` sets, the same hello `WebsocketProtocol::OpenAudioChannel`
sends, and the same Opus codec the server runs, so a suite driving one of
these is on the path a board is on.

Nothing here builds a session directly: everything goes through a
`TestClient` around an app, which is what makes this the wire rather than
the in-process drivers in `sessions.py`. The audio shapes come from
`configs.py`, so the rate a test sends at and the rate the server
expects have one definition between them.
"""

import json
import math
import re
import struct
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from samtal_server.audio.opus import OpusDecoder, OpusEncoder
from samtal_server.protocol import framing
from samtal_server.ws import WEBSOCKET_PATH, signed_device_id
from tests.support.configs import (
    DEVICE_HELLO,
    DEVICE_MAC,
    DEVICE_UUID,
    ENDPOINT_SILENCE_MS,
    FRAME_BYTES,
    FRAME_MS,
    OUTPUT_RATE,
    SAMPLE_RATE,
)

# --- opening the channel ----------------------------------------------


def connect(client: TestClient, device_id: str | None = DEVICE_MAC, version: int = 1):
    """The handshake a device makes, token and all.

    Auth is on in every lane, so the token here is a real one, issued by
    the app under test the way the OTA reply would issue it. That keeps
    these tests on the same path a device is on, and leaves the refusal
    cases to the tests that are about refusals.
    """
    headers = {
        "Authorization": f"Bearer {token_for(client, device_id)}",
        "Protocol-Version": str(version),
        "Client-Id": DEVICE_UUID,
    }
    if device_id is not None:
        headers["Device-Id"] = device_id
    return client.websocket_connect(WEBSOCKET_PATH, headers=headers)


def token_for(client: TestClient, device_id: str | None) -> str:
    """A token the app under test accepts for this device id, signed for
    the same form the handshake will present it in."""
    device_auth = client.app.state.device_auth
    if device_auth is None:
        return ""
    return device_auth.issue(DEVICE_UUID, signed_device_id(device_id or ""))


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


def endpoint_silence(websocket, encoder: OpusEncoder, version: int = 1) -> None:
    """The silence that ends an utterance in auto and realtime modes,
    where nothing sends a listen stop."""
    send_pcm(websocket, b"\x00" * (FRAME_BYTES * ENDPOINT_SILENCE_MS // FRAME_MS), encoder, version)


# --- what a device says while it listens -------------------------------


def listen_realtime(websocket) -> None:
    """What a realtime device sends once and never again. It is what
    makes the idle timeout apply at all."""
    websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "realtime"}))


def wait_for_close(websocket) -> WebSocketDisconnect:
    """Read past whatever the server has to say (the MCP handshake a
    device that advertised tools gets) until the socket closes."""
    with pytest.raises(WebSocketDisconnect) as excinfo:
        while True:
            websocket.receive_text()
    return excinfo.value


# --- reading the reply back -------------------------------------------


def collect_until(
    websocket, done: Callable[[dict], bool], version: int = 1
) -> tuple[list[dict], bytes]:
    """Read messages and audio until one of them says to stop, returning
    the JSON messages and the decoded audio."""
    decoder = OpusDecoder(sample_rate=OUTPUT_RATE)
    texts: list[dict] = []
    audio = b""
    while True:
        received = websocket.receive()
        if received.get("text") is not None:
            message = json.loads(received["text"])
            texts.append(message)
            if done(message):
                return texts, audio
        else:
            frame = framing.unwrap(version, received["bytes"])
            audio += decoder.decode(frame.payload)


def collect_reply(websocket, version: int = 1) -> tuple[list[dict], bytes]:
    """Read until tts stop, returning the JSON messages and decoded audio."""
    return collect_until(websocket, is_reply_end, version)


def is_reply_end(message: dict) -> bool:
    return message.get("type") == "tts" and message.get("state") == "stop"


def is_reply_start(message: dict) -> bool:
    """The first sentence of a reply, which is where the server is
    audibly speaking and a barge-in has something to interrupt."""
    return message.get("type") == "tts" and message.get("state") == "sentence_start"


def is_transcript(message: dict) -> bool:
    """What the server heard, announced before it starts answering."""
    return message.get("type") == "stt"


def sentences(texts: list[dict]) -> list[str]:
    return [m["text"] for m in texts if m.get("type") == "tts" and m["state"] == "sentence_start"]


def heard_ms(texts: list[dict]) -> int:
    """The utterance duration the mock ASR embedded in the transcript."""
    (stt,) = [m for m in texts if m.get("type") == "stt"]
    match = re.search(r"\d+", stt["text"])
    assert match is not None, stt
    return int(match.group())


def tone_strength(audio: bytes, hz: float) -> float:
    """How much of `audio` sits at `hz`: one DFT bin, normalized, which is
    all it takes to tell two mock voices apart after an Opus round trip."""
    samples = struct.unpack(f"<{len(audio) // 2}h", audio)
    angles = [2 * math.pi * hz * n / OUTPUT_RATE for n in range(len(samples))]
    real = sum(s * math.cos(a) for s, a in zip(samples, angles, strict=True))
    imaginary = sum(s * math.sin(a) for s, a in zip(samples, angles, strict=True))
    return math.hypot(real, imaginary) / len(samples)


def assert_endpointed_speech(texts: list[dict], speech_ms: int) -> None:
    """Assert the utterance the endpointer handed to ASR was this much
    speech.

    What ASR sees is always longer: an endpointed utterance carries the
    trailing silence the endpointer sat through, and a realtime session
    keeps buffering, so silence sent past one endpoint lands in front of
    the next utterance. Hence a window rather than a duration, wide
    enough for both and still far narrower than the gap between the
    utterance lengths these tests tell apart."""
    assert speech_ms + 600 <= heard_ms(texts) <= speech_ms + ENDPOINT_SILENCE_MS + 180


def say_something(websocket, duration_ms: int = 300) -> tuple[list[dict], bytes]:
    """One full turn: listen, speak, stop, and collect the reply."""
    websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
    send_pcm(websocket, speech_pcm(duration_ms), OpusEncoder())
    websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
    return collect_reply(websocket)
