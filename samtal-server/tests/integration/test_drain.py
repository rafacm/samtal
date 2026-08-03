"""The shutdown drain against a live server.

The unit lane drives the registry with fake sessions. This one holds a
real conversation over a real socket and drains mid-reply, which is the
only way to see the thing the drain exists for: the sentence the device
is speaking reaches its end, and only then does the socket close.
"""

import asyncio
import json

import pytest
import websockets

from samtal_server.audio.opus import OpusEncoder
from samtal_server.auth import build_device_auth
from samtal_server.config import Config
from samtal_server.protocol import framing
from samtal_server.session import GOING_AWAY
from samtal_server.ws import WEBSOCKET_PATH
from tests.integration.conftest import running_app, speech_pcm

MOCK_PROVIDERS = {
    "llm": {"mock": {"type": "mock", "reply": "One. Two. Three. Four. Five."}},
    "asr": {"mock": {"type": "mock", "text": "count"}},
    "tts": {"mock": {"type": "mock"}},
    "vad": {"mock": {"type": "mock"}},
}
MOCK_AGENT = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")

DEVICE_MAC = "aa:bb:cc:dd:ee:ff"
DEVICE_UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"

DEVICE_HELLO = {
    "type": "hello",
    "version": 1,
    "transport": "websocket",
    "audio_params": {
        "format": "opus",
        "sample_rate": 16000,
        "channels": 1,
        "frame_duration": 60,
    },
}


def drain_config() -> Config:
    return Config(
        providers=MOCK_PROVIDERS,
        agents={"assistant": MOCK_AGENT},
        devices={DEVICE_MAC: "assistant"},
        default_agent="assistant",
    )


async def connect(port: int, config: Config):
    auth = build_device_auth(config)
    assert auth is not None
    socket = await websockets.connect(
        f"ws://127.0.0.1:{port}{WEBSOCKET_PATH}",
        additional_headers={
            "Device-Id": DEVICE_MAC,
            "Client-Id": DEVICE_UUID,
            "Protocol-Version": "1",
            "Authorization": f"Bearer {auth.issue(DEVICE_UUID, DEVICE_MAC)}",
        },
        open_timeout=10,
    )
    await socket.send(json.dumps(DEVICE_HELLO))
    await socket.recv()  # the server hello
    return socket


async def messages_until_close(socket) -> tuple[list[dict], int]:
    """Everything the server says from here until it closes, and the
    close code it used."""
    texts: list[dict] = []
    try:
        while True:
            received = await asyncio.wait_for(socket.recv(), timeout=15)
            if isinstance(received, str):
                texts.append(json.loads(received))
    except websockets.ConnectionClosed as closed:
        assert closed.rcvd is not None
        return texts, closed.rcvd.code


async def test_a_drain_lets_the_reply_finish_then_closes_going_away() -> None:
    config = drain_config()
    async with running_app(config) as (port, app):
        socket = await connect(port, config)
        # Start a reply, then drain while it is still being spoken.
        await socket.send(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
        for packet in OpusEncoder().encode(speech_pcm(300)):
            await socket.send(framing.wrap(1, packet))
        await socket.send(json.dumps({"type": "listen", "state": "stop"}))
        await asyncio.sleep(0.05)

        await app.state.sessions.drain(timeout_s=20)
        texts, code = await messages_until_close(socket)

    spoken = [
        m["text"] for m in texts if m.get("type") == "tts" and m["state"] == "sentence_start"
    ]
    # The whole reply was said, not the part that fitted before the drain.
    assert spoken == ["One.", "Two.", "Three.", "Four.", "Five."]
    assert any(m.get("type") == "tts" and m["state"] == "stop" for m in texts)
    assert code == GOING_AWAY


async def test_a_draining_server_refuses_a_new_conversation() -> None:
    config = drain_config()
    async with running_app(config) as (port, app):
        socket = await connect(port, config)
        await app.state.sessions.drain(timeout_s=5)
        with pytest.raises(websockets.InvalidStatus) as excinfo:
            await connect(port, config)
        await socket.close()
    assert excinfo.value.response.status_code == 403
