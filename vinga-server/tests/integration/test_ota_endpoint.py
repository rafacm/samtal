"""The OTA endpoint against a live server over real HTTP.

The unit lane drives the app in-process. This one starts uvicorn on a
loopback port and speaks to it the way the device does, which is what
catches anything the ASGI test client papers over: the config the CLI
loaded reaching the handler, and the websocket URL being derived from the
address the device actually connected to.
"""

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest
import uvicorn

from tests.integration.conftest import booted
from vinga_server.auth import build_device_auth
from vinga_server.config import Config
from vinga_server.ota import OTA_PATH

MOCK_PROVIDERS = {stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")}
MOCK_AGENT = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")

DEVICE_MAC = "aa:bb:cc:dd:ee:ff"
DEVICE_UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"

SYSTEM_INFO = {
    "version": 2,
    "mac_address": DEVICE_MAC,
    "uuid": DEVICE_UUID,
    "application": {"name": "xiaozhi", "version": "2.4.0"},
    "board": {"type": "waveshare-esp32-s3-touch-lcd-1.54"},
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# No database of this module's own any more, and the reason is worth
# keeping: the configuration below is composed while this file is being
# imported, before any fixture could point it anywhere, so two modules
# that did that used to seed one directory between them and read each
# other's device bindings. The lane now gives each worker one database
# and clears it between tests (`tests/conftest.py`), so what a test
# seeds is what it reads and nothing survives into the next one.

CONFIG = Config(
    providers=MOCK_PROVIDERS,
    agents={"assistant": MOCK_AGENT, "kitchen": MOCK_AGENT},
    devices={DEVICE_MAC: ["kitchen"]},
    default_agent="assistant",
)


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    """A real uvicorn serving the app, yielding its base URL."""
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(booted(CONFIG), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            server.should_exit = True
            raise TimeoutError("server did not start within 10 seconds")
        time.sleep(0.05)

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def check_version(base_url: str, device_id: str = DEVICE_MAC) -> tuple[int, dict]:
    """POST system info the way the firmware does, headers and all."""
    request = urllib.request.Request(
        f"{base_url}{OTA_PATH}",
        data=json.dumps(SYSTEM_INFO).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Device-Id": device_id,
            "Client-Id": DEVICE_UUID,
            "User-Agent": "waveshare-esp32-s3-touch-lcd-1.54/2.4.0",
            "Accept-Language": "en-US",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_device_gets_a_complete_configuration(server: str) -> None:
    status, body = check_version(server)
    assert status == 200

    host = server.removeprefix("http://")
    assert body["websocket"]["url"] == f"ws://{host}/xiaozhi/v1/"
    assert body["websocket"]["version"] == 1
    # Auth is on by default, so a bound device is handed a token this
    # server will accept back on the websocket handshake.
    auth = build_device_auth(CONFIG)
    assert auth is not None
    assert auth.verify(body["websocket"]["token"], DEVICE_UUID, DEVICE_MAC)
    assert body["firmware"] == {"version": "2.4.0", "url": ""}
    assert body["server_time"]["timestamp"] > 1_700_000_000_000
    assert "activation" not in body


def test_websocket_url_points_back_at_the_server_that_answered(server: str) -> None:
    """The derived URL has to be one the device can actually reach, not the
    loopback name of whatever interface the server bound."""
    _, body = check_version(server)
    url = body["websocket"]["url"]
    host, _, port = url.removeprefix("ws://").partition("/")[0].rpartition(":")
    with socket.create_connection((host, int(port)), timeout=5):
        pass


def test_the_portal_line_names_the_address_the_request_arrived_on(server: str) -> None:
    """The trap the 2026-08-29 walkthrough hit, over real HTTP (#340).

    This configuration names no origin and listens on the wildcard
    address, so the line used to read `http://0.0.0.0:8003/...` directly
    under a websocket URL derived from the request: one reply, two
    answers, and the one a person is told to type worked nowhere. Both
    lines name the address the request really arrived on now.

    Worth an integration test of its own rather than only a unit one,
    because the fact under it is a Host header a real client sent
    through a real server rather than a scope a test client composed.
    """
    with urllib.request.urlopen(f"{server}{OTA_PATH}", timeout=10) as response:
        body = response.read().decode("utf-8")

    host = server.removeprefix("http://")
    portal = next(line for line in body.splitlines() if line.startswith("Type this into"))
    assert portal.endswith(
        f"http://{host}{OTA_PATH} (from the address this request arrived on)"
    )
    assert f"ws://{host}/xiaozhi/v1/" in body
    assert "0.0.0.0" not in body


def test_unknown_device_is_still_configured(server: str) -> None:
    status, body = check_version(server, device_id="11:22:33:44:55:66")
    assert status == 200
    assert body["websocket"]["url"].endswith("/xiaozhi/v1/")


def test_malformed_device_id_is_refused(server: str) -> None:
    status, body = check_version(server, device_id="not-a-mac")
    assert status == 400
    assert "does not hold a MAC address" in body["error"]
    # And says what one is, without repeating what arrived: the header
    # is attacker-controlled text on an unauthenticated endpoint.
    assert "six colon-separated hex pairs" in body["error"]
    assert "not-a-mac" not in body["error"]
