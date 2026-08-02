"""Two devices, two personas, one server: the plan's M5 acceptance.

One server run, one config: MAC A is bound to the `poet`, MAC B to the
`tutor`, and the two agents share their LLM, ASR, and VAD through
`agent_defaults` while differing in prompt and voice. Two xiaozhi-sdk
simulators hold their conversations concurrently, and each is checked
for both halves of its persona: the reply text quotes that agent's own
prompt (the mock LLM speaks back the system prompt it was handed), and
the received audio's dominant frequency is that agent's own mock voice.

A third device, bound to nothing, gets `default_agent`; against a
config with no agents at all, a device is turned away with 1008.
"""

import asyncio
import contextlib
import math
import struct

import numpy as np
import pytest
import uvicorn
import websockets
from xiaozhi_sdk import XiaoZhiWebsocket

from samtal_server.app import create_app
from samtal_server.config import Config
from samtal_server.ws import WEBSOCKET_PATH

POET_MAC = "aa:bb:cc:dd:ee:11"
TUTOR_MAC = "aa:bb:cc:dd:ee:12"
UNBOUND_MAC = "aa:bb:cc:dd:ee:13"

POET_TONE = 440.0
TUTOR_TONE = 880.0

SAMPLE_RATE = 16000
FRAME_MS = 60
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2


def two_persona_config() -> Config:
    return Config(
        providers={
            "llm": {
                "verse": {"type": "mock", "reply": "{system} in verse about {text}."},
                "lesson": {"type": "mock", "reply": "{system} explains {text}."},
            },
            "asr": {"mock": {"type": "mock", "text": "rain"}},
            "tts": {
                "tenor": {"type": "mock", "tone_hz": POET_TONE},
                "alto": {"type": "mock", "tone_hz": TUTOR_TONE},
            },
            "vad": {"mock": {"type": "mock"}},
        },
        # The half the two personas share; each names only what differs.
        agent_defaults={"asr": "mock", "vad": "mock"},
        agents={
            "poet": {"prompt": "POET", "llm": "verse", "tts": "tenor"},
            "tutor": {"prompt": "TUTOR", "llm": "lesson", "tts": "alto"},
        },
        # Written both ways on purpose: a list and a bare name.
        devices={POET_MAC: ["poet"], TUTOR_MAC: "tutor"},
        default_agent="tutor",
    )


@contextlib.asynccontextmanager
async def running(config: Config):
    """A live server on an ephemeral port, torn down on the way out."""
    server = uvicorn.Server(
        uvicorn.Config(create_app(config), host="127.0.0.1", port=0, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.01)
    try:
        yield server.servers[0].sockets[0].getsockname()[1]
    finally:
        server.should_exit = True
        await task


@pytest.fixture
async def server_port():
    async with running(two_persona_config()) as port:
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


async def converse(port: int, mac: str) -> tuple[list[dict], np.ndarray]:
    """One device's whole conversation: OTA discovery, hello, an
    utterance, and the spoken reply collected until `tts stop`."""
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
        e["text"] for e in events if e.get("type") == "tts" and e["state"] == "sentence_start"
    )


async def test_two_devices_get_two_personas_from_one_server(server_port: int) -> None:
    (poet_events, poet_audio), (tutor_events, tutor_audio) = await asyncio.gather(
        converse(server_port, POET_MAC), converse(server_port, TUTOR_MAC)
    )

    # Each reply came out of its own agent's prompt and its own agent's
    # LLM entry: no prompt state is shared between the two sessions.
    assert spoken(poet_events) == "POET in verse about rain."
    assert spoken(tutor_events) == "TUTOR explains rain."

    # And each was spoken in its own agent's voice.
    assert abs(dominant_hz(poet_audio) - POET_TONE) < 20
    assert abs(dominant_hz(tutor_audio) - TUTOR_TONE) < 20


async def test_an_unbound_device_gets_the_default_agent(server_port: int) -> None:
    events, audio = await converse(server_port, UNBOUND_MAC)
    assert spoken(events) == "TUTOR explains rain."
    assert abs(dominant_hz(audio) - TUTOR_TONE) < 20


async def test_a_device_with_no_agent_at_all_is_turned_away() -> None:
    # No agents and no default_agent: the upgrade is accepted (so the
    # device gets a reason rather than a bare handshake failure) and then
    # closed with the policy code.
    async with running(Config()) as port:
        websocket = await websockets.connect(
            f"ws://127.0.0.1:{port}{WEBSOCKET_PATH}",
            additional_headers={
                "Device-Id": UNBOUND_MAC,
                "Client-Id": "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8",
            },
        )
        with pytest.raises(websockets.ConnectionClosed) as excinfo:
            await asyncio.wait_for(websocket.recv(), timeout=5)
    assert excinfo.value.rcvd is not None
    assert excinfo.value.rcvd.code == 1008
    assert "no agent" in excinfo.value.rcvd.reason
