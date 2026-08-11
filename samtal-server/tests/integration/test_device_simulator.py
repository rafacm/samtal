"""The xiaozhi-sdk device simulator against a live server.

This is the plan's M4 acceptance: the sdk discovers the websocket URL
through the OTA endpoint, completes the hello exchange, speaks an
utterance, and gets a coherent spoken reply from the conversation
pipeline running on the mock providers (an `stt` transcript, a
sentence with the reply text, and the spoken audio). Since the sdk
encodes and decodes with opuslib, the run also cross-validates the
server's PyAV codec against an independent one.
"""

import asyncio
import math
import struct

import numpy as np
import pytest
import uvicorn
from xiaozhi_sdk import XiaoZhiWebsocket

from samtal_server.config import Config
from tests.integration.conftest import booted

MOCK_PROVIDERS = {stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")}
MOCK_AGENT = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")

DEVICE_MAC = "aa:bb:cc:dd:ee:01"
SAMPLE_RATE = 16000
FRAME_MS = 60
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2

# The mock TTS speaks 40 ms per character with a 240 ms floor, at 24 kHz.
EXPECTED_REPLY = "You said hello."
EXPECTED_REPLY_S = 40 * len(EXPECTED_REPLY) / 1000


@pytest.fixture
async def server_port():
    config = Config(
        providers=MOCK_PROVIDERS,
        agents={"assistant": MOCK_AGENT},
        default_agent="assistant",
    )
    server = uvicorn.Server(
        uvicorn.Config(booted(config), host="127.0.0.1", port=0, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.01)
    yield server.servers[0].sockets[0].getsockname()[1]
    server.should_exit = True
    await task


def speech_pcm(duration_ms: int) -> bytes:
    samples = SAMPLE_RATE * duration_ms // 1000
    return b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 300 * n / SAMPLE_RATE)))
        for n in range(samples)
    )


async def speak_an_utterance(client: XiaoZhiWebsocket) -> None:
    """About a second of tone, then silence: the server's endpointer
    ends the utterance, since nothing here sends a listen stop."""
    pcm = speech_pcm(960)
    for start in range(0, len(pcm), FRAME_BYTES):
        assert await client.send_audio(pcm[start : start + FRAME_BYTES])
    await client.send_silence_audio(1.2)


async def test_a_second_utterance_is_answered_without_reconnecting(server_port: int) -> None:
    # The sdk listens the way an echo-cancelling board does: mode
    # realtime, one listen start at connect and never another. A server
    # that waits to be re-armed answers the first question and goes deaf
    # for the rest of the session, which is issue #10.
    events: list[dict] = []
    reply_finished = asyncio.Event()

    async def on_message(data: dict) -> None:
        events.append(data)
        if data.get("type") == "tts" and data.get("state") == "stop":
            reply_finished.set()

    client = XiaoZhiWebsocket(
        on_message,
        ota_url=f"http://127.0.0.1:{server_port}/xiaozhi/ota/",
        audio_sample_rate=SAMPLE_RATE,
    )
    try:
        assert await client.init_connection(DEVICE_MAC)
        assert client.mode == "realtime"
        for _ in range(2):
            reply_finished.clear()
            await speak_an_utterance(client)
            await asyncio.wait_for(reply_finished.wait(), timeout=10)
    finally:
        await client.close()

    # Two questions, two transcripts, two spoken answers, one connection.
    assert [e["text"] for e in events if e.get("type") == "stt"] == ["hello", "hello"]
    spoken = [
        e["text"] for e in events if e.get("type") == "tts" and e["state"] == "sentence_start"
    ]
    assert spoken == [EXPECTED_REPLY, EXPECTED_REPLY]


async def test_a_scripted_conversation_gets_a_spoken_reply(server_port: int) -> None:
    events: list[dict] = []
    reply_finished = asyncio.Event()

    async def on_message(data: dict) -> None:
        events.append(data)
        if data.get("type") == "tts" and data.get("state") == "stop":
            reply_finished.set()

    client = XiaoZhiWebsocket(
        on_message,
        ota_url=f"http://127.0.0.1:{server_port}/xiaozhi/ota/",
        audio_sample_rate=SAMPLE_RATE,
    )
    try:
        # OTA discovery, websocket upgrade, hello exchange, listen start.
        assert await client.init_connection(DEVICE_MAC)
        assert client.session_id

        # About a second of tone, then silence: the server's endpointer
        # must end the utterance without any listen stop.
        pcm = speech_pcm(960)
        for start in range(0, len(pcm), FRAME_BYTES):
            assert await client.send_audio(pcm[start : start + FRAME_BYTES])
        await client.send_silence_audio(1.2)
        await asyncio.wait_for(reply_finished.wait(), timeout=10)
    finally:
        await client.close()

    # The pipeline announced what it heard and what it answered.
    (stt,) = [e for e in events if e.get("type") == "stt"]
    assert stt["text"] == "hello"
    tts_states = [e["state"] for e in events if e.get("type") == "tts"]
    assert tts_states[0] == "start"
    assert tts_states[-1] == "stop"
    spoken = [
        e["text"] for e in events if e.get("type") == "tts" and e["state"] == "sentence_start"
    ]
    assert spoken == [EXPECTED_REPLY]

    # The spoken reply, decoded by the sdk's own opuslib decoder. The sdk
    # decodes at the rate it was constructed with rather than the 24 kHz
    # the server hello announced, so assert on a rate-agnostic window.
    audio = np.concatenate(list(client.output_audio_queue))
    duration_s = audio.size / SAMPLE_RATE
    assert EXPECTED_REPLY_S / 2 <= duration_s <= EXPECTED_REPLY_S * 3
    tone_rms = math.sqrt(float(np.mean(audio.astype(np.float64) ** 2)))
    assert tone_rms > 500
