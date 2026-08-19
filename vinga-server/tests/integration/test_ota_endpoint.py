"""The OTA endpoint against a live server over real HTTP.

The unit lane drives the app in-process. This one starts uvicorn on a
loopback port and speaks to it the way the device does, which is what
catches anything the ASGI test client papers over: the config the CLI
loaded reaching the handler, and the websocket URL being derived from the
address the device actually connected to.
"""

import json
import socket
import tempfile
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


# A database directory of this module's own. The configuration below is
# composed while this file is being imported, before any fixture could
# point it anywhere, so it falls to the lane-wide default in
# `tests/conftest.py`; two modules that both did that would seed one
# database between them and read each other's device bindings.
DATABASE_DIR = tempfile.mkdtemp(prefix="vinga-ota-endpoint-")

CONFIG = Config(
    server={"database": {"dir": DATABASE_DIR}},
    providers=MOCK_PROVIDERS,
    agents={"assistant": MOCK_AGENT, "kitchen": MOCK_AGENT},
    devices={DEVICE_MAC: "kitchen"},
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
