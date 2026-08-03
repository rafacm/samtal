"""Device authentication and the connection cap, against a live server.

The unit lane drives the gate in-process, where the ASGI test client
reports a pre-accept close as a disconnect. Here a real client speaks
real HTTP to real uvicorn, which is the only place the thing that
actually matters is visible: a refused handshake is answered 403 on the
upgrade, so no websocket is ever established.

The rest of this lane now runs with auth on and xiaozhi-sdk forwarding
the token the OTA endpoint issued it, so the accepted path is covered
by every other test here.
"""

import asyncio

import pytest
import websockets
from websockets.exceptions import InvalidStatus

from samtal_server.auth import build_device_auth
from samtal_server.config import Config
from samtal_server.ws import WEBSOCKET_PATH

MOCK_PROVIDERS = {stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")}
MOCK_AGENT = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")

DEVICE_MAC = "aa:bb:cc:dd:ee:ff"
DEVICE_UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"


def bound_config() -> Config:
    return Config(
        providers=MOCK_PROVIDERS,
        agents={"assistant": MOCK_AGENT},
        devices={DEVICE_MAC: "assistant"},
        default_agent="assistant",
    )


def headers(token: str | None) -> dict[str, str]:
    """The handshake headers the firmware sets, minus the token when it
    has none in NVS (in which case it sends no Authorization at all)."""
    sent = {
        "Device-Id": DEVICE_MAC,
        "Client-Id": DEVICE_UUID,
        "Protocol-Version": "1",
    }
    if token is not None:
        sent["Authorization"] = f"Bearer {token}"
    return sent


async def connect(port: int, token: str | None):
    return await websockets.connect(
        f"ws://127.0.0.1:{port}{WEBSOCKET_PATH}",
        additional_headers=headers(token),
        open_timeout=10,
    )


async def test_a_valid_token_is_upgraded(serve) -> None:
    config = bound_config()
    auth = build_device_auth(config)
    assert auth is not None
    async with serve(config) as port:
        socket = await connect(port, auth.issue(DEVICE_UUID, DEVICE_MAC))
        await socket.close()


async def test_no_token_is_refused_on_the_upgrade(serve) -> None:
    async with serve(bound_config()) as port:
        with pytest.raises(InvalidStatus) as excinfo:
            await connect(port, None)
    assert excinfo.value.response.status_code == 403


async def test_a_doctored_token_is_refused_on_the_upgrade(serve) -> None:
    config = bound_config()
    auth = build_device_auth(config)
    assert auth is not None
    signature, _, issued = auth.issue(DEVICE_UUID, DEVICE_MAC).partition(".")
    doctored = ("B" if signature[0] != "B" else "C") + signature[1:]
    async with serve(config) as port:
        with pytest.raises(InvalidStatus) as excinfo:
            await connect(port, f"{doctored}.{issued}")
    assert excinfo.value.response.status_code == 403


async def test_a_full_server_refuses_the_next_device_and_admits_it_later(serve) -> None:
    """The cap is a count with no queue behind it: the second device is
    turned away rather than parked, and gets in once a slot frees."""
    config = bound_config()
    config.server.limits.max_sessions = 1
    auth = build_device_auth(config)
    assert auth is not None
    token = auth.issue(DEVICE_UUID, DEVICE_MAC)

    async with serve(config) as port:
        first = await connect(port, token)
        with pytest.raises(InvalidStatus) as excinfo:
            await connect(port, token)
        assert excinfo.value.response.status_code == 403

        await first.close()
        # The server frees the slot as the connection closes, which the
        # client learns about before the server has finished doing.
        for _ in range(50):
            try:
                second = await connect(port, token)
            except InvalidStatus:
                await asyncio.sleep(0.05)
            else:
                await second.close()
                return
        pytest.fail("the slot was never freed")


async def test_another_servers_token_is_refused(serve) -> None:
    """The secret is what makes a token this server's, so one issued
    with a different secret is worth nothing here."""
    from samtal_server.auth import DeviceAuth

    stranger = DeviceAuth("some other deployment's secret", expire_s=3600)
    async with serve(bound_config()) as port:
        with pytest.raises(InvalidStatus) as excinfo:
            await connect(port, stranger.issue(DEVICE_UUID, DEVICE_MAC))
    assert excinfo.value.response.status_code == 403
