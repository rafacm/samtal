"""Binding a device on a running server, over the API it serves.

The unit lane writes through the repository, which is the seam the
acceptance suites use and cannot show the thing this milestone promises
an operator: that the command they type reaches the server they are
talking to, and the board on the desk is served by the same process a
moment later. Here the write is an HTTP request to the mounted /api, the
check-in is an HTTP request the firmware makes, and the conversation is
the device simulator, all against one uvicorn on a loopback port.
"""

import asyncio
import os

import httpx
import pytest

from tests.integration.conftest import spoken
from tests.support.notices import CHECK_IN, boundaries
from tests.support.stores import the_lock_held
from vinga_server.config import Config
from vinga_server.ota import OTA_PATH

MOCK_PROVIDERS = {stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")}
MOCK_AGENT = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")

DEVICE_MAC = "aa:bb:cc:dd:ee:ff"
DEVICE_UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"

# The board this deployment already had, which is what makes a
# configuration with an agent and an unbound device a bootable one.
BOUND_MAC = "11:22:33:44:55:01"

SYSTEM_INFO = {
    "version": 2,
    "mac_address": DEVICE_MAC,
    "uuid": DEVICE_UUID,
    "application": {"name": "xiaozhi", "version": "2.4.0"},
    "board": {"type": "waveshare-esp32-s3-touch-lcd-1.54"},
}

CONFIG = Config(
    providers=MOCK_PROVIDERS,
    agents={"assistant": MOCK_AGENT},
    devices={BOUND_MAC: ["assistant"]},
)


def _token() -> str:
    return os.environ["VINGA_API_SECRET"]


async def _check_in(client: httpx.AsyncClient) -> dict:
    response = await client.post(
        OTA_PATH,
        json=SYSTEM_INFO,
        headers={"Device-Id": DEVICE_MAC, "Client-Id": DEVICE_UUID},
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_a_bind_over_the_api_reaches_the_devices_next_check_in(
    serve_app, simulate
) -> None:
    """The whole ceremony this milestone is for, minus the code M3 adds:
    an unbound board is refused, one API call binds it, and its own
    re-check hands it a token, on the server that was already running."""
    async with serve_app(CONFIG) as (port, _):
        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base, timeout=30) as client:
            assert (await _check_in(client))["websocket"]["token"] == ""

            answer = await client.put(
                f"/api/devices/{DEVICE_MAC}",
                json={"agents": ["assistant"]},
                headers={"Authorization": f"Bearer {_token()}"},
            )
            assert answer.status_code == 200, answer.text
            # The acknowledgement says what just became true, and this is
            # the path an operator's CLI prints it from.
            assert boundaries(answer.json()) == {CHECK_IN}

            assert (await _check_in(client))["websocket"]["token"] != ""

        # And the device talks: the token it was just handed is accepted
        # at the handshake, and the session resolves the same binding.
        events, _ = await simulate(port, DEVICE_MAC)
    assert any(event.get("type") == "tts" for event in events)


@pytest.mark.asyncio
async def test_a_held_write_lock_stops_neither_a_lookup_nor_a_conversation(
    serve_app, simulate
) -> None:
    """The property the read engine exists for, at the size it matters.

    Every repository write holds the domain chain's advisory lock for
    its whole transaction, and the two device paths now read that
    database. If the read took a lock of its own, or waited for one, an
    operator's write would stall the fleet's check-ins and every connect
    behind them. It cannot: a reader takes no advisory lock and reads
    its own snapshot, which is what MVCC gives it.

    So a real lock is held here, by another connection, across a whole
    conversation: the OTA check that resolves the binding, the
    handshake, the utterance and the spoken reply, plus a lookup on the
    server's own view. The lock is still held when all of it has
    finished, which is what makes this about the interval rather than
    about the order things happened to run in.
    """
    async with serve_app(CONFIG) as (port, app):
        # Taken before anything starts, and held until the block ends,
        # which is what makes this about the interval rather than about
        # the order things happened to run in.
        with the_lock_held():
            conversation = asyncio.create_task(simulate(port, BOUND_MAC))
            bindings = app.state.composition.bindings
            resolved = await asyncio.wait_for(bindings.resolve(BOUND_MAC), timeout=10)
            events, _ = await asyncio.wait_for(conversation, timeout=60)

    assert resolved.names == ("assistant",)
    assert spoken(events), "the conversation produced no reply"
