"""What a board says when it checks in, and what binds it afterwards.

A device meets this server at its configuration check long before it
opens a socket: it posts what it is, it is told whether anyone has
claimed it, and if nobody has it is sent round the activation ceremony
until somebody does. What belongs here is that half of the protocol: the
payload the firmware sends, the headers it sets, the apps a suite builds
to answer it, and the two requests it makes (`/ota/` and `/activate`).

The shapes mirror `Board::GetSystemInfoJson`, `Ota::CheckVersion` and
`Ota::Activate` in 78/xiaozhi-esp32, so a suite driving these is on the
path a board is on. The device's identity itself comes from
`configs.py`, so the MAC a check-in presents and the MAC a session
handshake presents have one definition between them.

Two clients live here because the two seams disagree about what an
unconfigured server is: the OTA suite wants a plain `Config()` and the
activation suite wants a configuration where the device under test is
deliberately unbound. Both are context managers, because a check-in
resolves the device through the bindings view and the pending table,
which are built by the lifespan (#142) and released when it leaves.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient

from tests.support.apps import entered_client
from tests.support.configs import BOUND_MAC, DEVICE_MAC, DEVICE_UUID
from vinga_server.config import Config
from vinga_server.ota import ACTIVATE_SEGMENT, OTA_PATH

# --- what a board says about itself -----------------------------------


MOCK_PROVIDERS = {stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")}
MOCK_AGENT = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")


SYSTEM_INFO: dict[str, Any] = {
    "version": 2,
    "language": "en-US",
    "flash_size": 16777216,
    "mac_address": DEVICE_MAC.lower(),
    "uuid": DEVICE_UUID,
    "chip_model_name": "esp32s3",
    "application": {"name": "xiaozhi", "version": "2.4.0"},
    "board": {"type": "waveshare-esp32-s3-touch-lcd-1.54"},
}


HEADERS = {"Device-Id": DEVICE_MAC, "Client-Id": DEVICE_UUID}

# The canonical form of the MAC above, which is what the challenge, the
# pending entry and the bound row all carry.
NORMALIZED = DEVICE_MAC.lower()


# --- the servers it checks in against ---------------------------------


@contextmanager
def ota_client(config: Config | None = None) -> Iterator[TestClient]:
    with entered_client(config if config is not None else Config()) as client:
        yield client


def unbound_config(**server) -> Config:
    return Config(
        providers=MOCK_PROVIDERS,
        agents={"assistant": MOCK_AGENT},
        devices={BOUND_MAC: "assistant"},
        server=server,
    )


@contextmanager
def activation_client(config: Config | None = None) -> Iterator[TestClient]:
    with entered_client(config if config is not None else unbound_config()) as client:
        yield client


# --- the requests a board makes ---------------------------------------


def post_system_info(
    client: TestClient,
    payload: Any = SYSTEM_INFO,
    device_id: str | None = DEVICE_MAC,
    client_id: str | None = DEVICE_UUID,
):
    headers = {}
    if device_id is not None:
        headers["Device-Id"] = device_id
    if client_id is not None:
        headers["Client-Id"] = client_id
    return client.post(OTA_PATH, json=payload, headers=headers)


def check_in(client: TestClient, path: str = OTA_PATH, mac: str = DEVICE_MAC) -> dict:
    response = client.post(
        path, json=SYSTEM_INFO, headers={**HEADERS, "Device-Id": mac}
    )
    assert response.status_code == 200, response.text
    return response.json()


def activate(
    client: TestClient,
    path: str = OTA_PATH,
    mac: str = DEVICE_MAC,
    body: object = None,
    version: str | None = None,
):
    headers = {"Device-Id": mac}
    if version is not None:
        headers["Activation-Version"] = version
    return client.post(
        f"{path}{ACTIVATE_SEGMENT}", json={} if body is None else body, headers=headers
    )


# --- the clock the pending table is aged against ----------------------


class Clock:
    """A clock a test moves by hand."""

    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
