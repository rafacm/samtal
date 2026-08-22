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

import pytest
import websockets

from tests.integration.conftest import converse, dominant_hz, running, spoken
from vinga_server.auth import build_device_auth
from vinga_server.config import Config
from vinga_server.ws import WEBSOCKET_PATH

POET_MAC = "aa:bb:cc:dd:ee:11"
TUTOR_MAC = "aa:bb:cc:dd:ee:12"
UNBOUND_MAC = "aa:bb:cc:dd:ee:13"

POET_TONE = 440.0
TUTOR_TONE = 880.0


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
        devices={POET_MAC: ["poet"], TUTOR_MAC: ["tutor"]},
        default_agent="tutor",
    )


@pytest.fixture
async def server_port():
    async with running(two_persona_config()) as port:
        yield port


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
    # No agents and no default_agent: the device proves who it is at the
    # handshake, so the upgrade is accepted (it gets a reason rather than
    # a bare handshake failure) and then closed with the policy code.
    client_id = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"
    config = Config()
    auth = build_device_auth(config)
    assert auth is not None
    async with running(config) as port:
        websocket = await websockets.connect(
            f"ws://127.0.0.1:{port}{WEBSOCKET_PATH}",
            additional_headers={
                "Device-Id": UNBOUND_MAC,
                "Client-Id": client_id,
                "Authorization": f"Bearer {auth.issue(client_id, UNBOUND_MAC)}",
            },
        )
        with pytest.raises(websockets.ConnectionClosed) as excinfo:
            await asyncio.wait_for(websocket.recv(), timeout=5)
    assert excinfo.value.rcvd is not None
    assert excinfo.value.rcvd.code == 1008
    assert "no agent" in excinfo.value.rcvd.reason
