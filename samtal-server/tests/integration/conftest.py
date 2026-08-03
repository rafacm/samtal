"""Shared machinery for the integration lane.

Every test here runs a real server on an ephemeral port and talks to it
with xiaozhi-sdk as the device, on mock providers, so the lane needs no
keys, no models, and no network. The pieces two or more test modules
need live here as fixtures rather than being imported across modules.
"""

import asyncio
import contextlib
import math
import struct
from collections.abc import Sequence
from typing import Any

import numpy as np
import pytest
import uvicorn
from xiaozhi_sdk import XiaoZhiWebsocket

from samtal_server.app import create_app
from samtal_server.config import Config

SAMPLE_RATE = 16000
FRAME_MS = 60
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2


@contextlib.asynccontextmanager
async def running_app(config: Config):
    """A live server on an ephemeral port, yielding its port and the app
    it serves, torn down on the way out. The app is what a test needs
    when it has to reach server-side state (the session registry) that a
    device could not."""
    app = create_app(config)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.01)
    try:
        yield server.servers[0].sockets[0].getsockname()[1], app
    finally:
        server.should_exit = True
        await task


@contextlib.asynccontextmanager
async def running(config: Config):
    """A live server on an ephemeral port, yielding just the port."""
    async with running_app(config) as (port, _):
        yield port


def speech_pcm(duration_ms: int) -> bytes:
    samples = SAMPLE_RATE * duration_ms // 1000
    return b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 300 * n / SAMPLE_RATE)))
        for n in range(samples)
    )


def dominant_hz(audio: np.ndarray) -> float:
    """The strongest frequency in the received reply. The sdk hands its
    decoded audio back at the rate it was constructed with, whatever the
    server hello announced, so the analysis rate is that one."""
    spectrum = np.abs(np.fft.rfft(audio.astype(np.float64)))
    return float(np.fft.rfftfreq(audio.size, 1 / SAMPLE_RATE)[int(np.argmax(spectrum))])


async def converse(
    port: int, mac: str, device_tools: Sequence[dict[str, Any]] | None = None
) -> tuple[list[dict], np.ndarray]:
    """One device's whole conversation: OTA discovery, hello, an
    utterance, and the spoken reply collected until `tts stop`.

    `device_tools` are registered before connecting, so they are what
    the server's tools/list finds; each entry is an xiaozhi-sdk tool
    (name, description, inputSchema, tool_func, is_async)."""
    events: list[dict] = []
    reply_finished = asyncio.Event()

    async def on_message(data: dict) -> None:
        events.append(data)
        if data.get("type") == "tts" and data.get("state") == "stop":
            reply_finished.set()

    client = XiaoZhiWebsocket(
        on_message,
        ota_url=f"http://127.0.0.1:{port}/xiaozhi/ota/",
        audio_sample_rate=SAMPLE_RATE,
    )
    try:
        if device_tools:
            await client.set_mcp_tool(list(device_tools))
        assert await client.init_connection(mac)
        pcm = speech_pcm(960)
        for start in range(0, len(pcm), FRAME_BYTES):
            assert await client.send_audio(pcm[start : start + FRAME_BYTES])
        await client.send_silence_audio(1.2)
        await asyncio.wait_for(reply_finished.wait(), timeout=30)
        await asyncio.sleep(0.3)
        chunks = list(client.output_audio_queue)
    finally:
        await client.close()
    return events, np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)


def spoken(events: list[dict]) -> str:
    return " ".join(
        event["text"]
        for event in events
        if event.get("type") == "tts" and event["state"] == "sentence_start"
    )


@pytest.fixture
def serve():
    """The server runner: `async with serve(config) as port: ...`."""
    return running


@pytest.fixture
def serve_app():
    """The same, plus the app: `async with serve_app(c) as (port, app):`."""
    return running_app


@pytest.fixture
def simulate():
    """The device simulator: `await simulate(port, mac)`."""
    return converse
