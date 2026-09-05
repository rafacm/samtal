"""The whole ceremony, over HTTP, against one running server.

The firmware's activation loop simulated as the loop actually runs
(`Application::CheckNewVersion` in 78/xiaozhi-esp32): check
configuration, be handed a code, poll /activate in bursts three seconds
apart, and re-check configuration each time round. Here the operator's
claim lands in the middle of that, and the same process hands the board
a token seconds later and then holds a conversation with it, all through
the short URL an operator typed into a captive portal.

The unit lane drives the same states through a test client. What only
this lane can show is that they are one server: the code minted by the
device-facing endpoint is the code the API's listing shows and the API's
claim retires, on one process, over real sockets.
"""

import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import booted
from tests.support.notices import CHECK_IN, boundaries
from vinga_server.config import Config
from vinga_server.onboarding import onboarding_key, onboarding_path
from vinga_server.ota import ACTIVATE_SEGMENT

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

HEADERS = {"Device-Id": DEVICE_MAC, "Client-Id": DEVICE_UUID}


def _token() -> str:
    return os.environ["VINGA_API_SECRET"]


def _authorized() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}"}


async def _check_in(client: httpx.AsyncClient, path: str) -> dict:
    """What the firmware POSTs at every pass of its activation loop."""
    response = await client.post(path, json=SYSTEM_INFO, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


async def _poll(client: httpx.AsyncClient, path: str) -> int:
    """One `Ota::Activate`: a version-1 body, identified by its header,
    whose status code is the whole answer."""
    response = await client.post(
        f"{path}{ACTIVATE_SEGMENT}",
        json={},
        headers={"Device-Id": DEVICE_MAC, "Activation-Version": "1"},
    )
    return response.status_code


@pytest.mark.asyncio
async def test_a_board_is_onboarded_by_the_code_it_shows(
    serve_app, simulate, tmp_path: Path
) -> None:
    async with serve_app(CONFIG) as (port, app):
        # The URL an operator reads off the startup banner and types
        # into the board's captive portal. Everything below is what the
        # board does with it.
        short = onboarding_path(onboarding_key(app.state.composition.server))
        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base, timeout=30) as client:
            body = await _check_in(client, short)
            code = body["activation"]["code"]
            assert body["websocket"]["token"] == ""
            assert body["activation"]["challenge"] == DEVICE_MAC

            # The board polls, and is told to keep waiting.
            assert await _poll(client, short) == 202

            # The operator, meanwhile, sees the board on the desk in the
            # listing and claims it. Same process, same code.
            listing = await client.get("/api/devices/pending", headers=_authorized())
            assert listing.json()[code]["mac"] == DEVICE_MAC
            assert listing.json()[code]["board"] == SYSTEM_INFO["board"]["type"]

            claim = await client.post(
                f"/api/devices/pending/{code}",
                json={"agents": ["assistant"]},
                headers=_authorized(),
            )
            assert claim.status_code == 200, claim.text
            assert claim.json()["wrote"] == f"device {DEVICE_MAC} bound to assistant"
            assert boundaries(claim.json()) == {CHECK_IN}

            # The board's very next poll, three seconds after the last
            # one, sees it: no power cycle, no button press.
            assert await _poll(client, short) == 200

            # And the check that follows carries the real configuration
            # and asks for nothing more.
            bound = await _check_in(client, short)
            assert bound["websocket"]["token"] != ""
            assert "activation" not in bound

            # The code is spent, and the listing is empty again.
            assert (
                await client.get("/api/devices/pending", headers=_authorized())
            ).json() == {}

        # Then the board talks, over the short path it was onboarded
        # through: the token it was just handed is accepted at the
        # handshake and the session resolves the binding the claim
        # wrote.
        events, _ = await simulate(port, DEVICE_MAC, None, short)
    assert any(event.get("type") == "tts" for event in events)

    # The binding survives the server. A second app booted from the same
    # directory hands the board its token straight away, at the same
    # onboarding URL, because the key is derived from the same secret.
    restarted = booted(CONFIG)
    with TestClient(restarted) as client:
        server = restarted.state.composition.server
        assert onboarding_path(onboarding_key(server)) == short
        after = client.post(short, json=SYSTEM_INFO, headers=HEADERS).json()
    assert after["websocket"]["token"] != ""
    assert "activation" not in after


@pytest.mark.asyncio
async def test_a_stale_code_is_refused_by_the_running_server(
    serve_app, tmp_path: Path
) -> None:
    """The other half of the same ceremony: the operator types a number
    that is no longer the one on the screen, and is told so rather than
    binding something."""
    async with serve_app(CONFIG) as (port, app):
        short = onboarding_path(onboarding_key(app.state.composition.server))
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            code = (await _check_in(client, short))["activation"]["code"]

            refused = await client.post(
                "/api/devices/pending/000000",
                json={"agents": ["assistant"]},
                headers=_authorized(),
            )

            assert refused.status_code == 404
            assert "on the device's screen" in refused.json()["detail"]
            # And the board is still showing its own number, unbound.
            assert (
                await client.get("/api/devices/pending", headers=_authorized())
            ).json()[code]["mac"] == DEVICE_MAC
            assert await _poll(client, short) == 202
