"""What a deployment's own log holds about the requests it served.

Two of this server's request lines are things nothing may print. The
OTA endpoint's path carries the deployment's secret segment, which is
the only credential a stock board can present, and an activation code
arrives in the path of a claim, rejected value and all. An access log
prints request lines, so this asserts there is no access log: the
server is run through the same uvicorn configuration `serve()` builds,
at INFO, with real requests carrying both sentinels, and neither
reaches either of the two shipped formats.

Run here rather than in the unit lane because the thing under test is
what uvicorn writes, which needs uvicorn to have served something.
"""

import asyncio
import json
import logging
import tempfile

import httpx
import pytest
import uvicorn
import websockets

from tests.integration.conftest import booted
from vinga_server import logs, serving
from vinga_server.app import create_app
from vinga_server.auth import build_device_auth
from vinga_server.config import Config
from vinga_server.ota import ACTIVATE_SEGMENT
from vinga_server.ws import WEBSOCKET_PATH

MOCK_PROVIDERS = {stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")}
MOCK_AGENT = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")

# The two values that must not be printed, shaped so that a substring
# search for either cannot match by accident.
SECRET_SEGMENT = "3f9a1c7e-never-a-real-ota-segment"
REJECTED_CODE = "914773"

OTA_PATH = f"/xiaozhi/ota/{SECRET_SEGMENT}/"

DEVICE_MAC = "aa:bb:cc:dd:ee:ff"
DEVICE_UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"

# The board this deployment has configured, which is the one that gets
# past the handshake and can send a frame.
BOUND_MAC = "11:22:33:44:55:01"

# What a device puts in a frame, standing in for the room: a field this
# server does not model, so nothing here parses it and the only thing
# that could print it is a library rendering the frame.
FRAME_SENTINEL = "sk-test-2b7c9f1e-never-a-real-credential"

# A database directory of this module's own. The configuration below is
# composed while this file is being imported, before any fixture could
# point it anywhere, so it falls to the lane-wide default in
# `tests/conftest.py`; two modules that both did that would seed one
# database between them and read each other's device bindings.
DATABASE_DIR = tempfile.mkdtemp(prefix="vinga-access-logs-")

CONFIG = Config(
    providers=MOCK_PROVIDERS,
    agents={"assistant": MOCK_AGENT},
    devices={BOUND_MAC: ["assistant"]},
    server={"ota_path": OTA_PATH, "database": {"dir": DATABASE_DIR}},
)


async def _serving(app, config: Config):
    """The app under the configuration a deployment is served with, on
    an ephemeral port. The port is overridden on the object rather than
    in the configuration, since the models refuse 0 and that is right
    for a deployment."""
    served = serving.uvicorn_config(app, config)
    served.host, served.port = "127.0.0.1", 0
    server = uvicorn.Server(served)
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.01)
    return server, task


@pytest.mark.asyncio
async def test_no_request_line_reaches_the_log(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app(CONFIG)
    server, task = await _serving(app, CONFIG)
    port = server.servers[0].sockets[0].getsockname()[1]
    # The client here is the device, not the server, and httpx narrates
    # its own requests at INFO. What is under test is what the server
    # writes, so the test's own client is quietened rather than counted
    # against it.
    caplog.set_level(logging.WARNING, logger="httpx")
    try:
        with caplog.at_level(logging.INFO):
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", timeout=30
            ) as client:
                # A device's whole exchange over the secret path, which
                # is where the segment would be printed.
                headers = {"Device-Id": DEVICE_MAC, "Client-Id": DEVICE_UUID}
                assert (await client.post(OTA_PATH, json={}, headers=headers)).status_code == 200
                assert (
                    await client.post(f"{OTA_PATH}{ACTIVATE_SEGMENT}", json={}, headers=headers)
                ).status_code == 202
                assert (await client.get(OTA_PATH)).status_code == 200
                # And a claim of a code no device is showing, which is
                # where the rejected value would be.
                refused = await client.post(
                    f"/api/devices/pending/{REJECTED_CODE}", json={"agents": ["assistant"]}
                )
                assert refused.status_code == 401
    finally:
        server.should_exit = True
        await task

    # Both formats, since a deployment ships either one and the JSON one
    # renders fields the text one leaves out.
    rendered = caplog.text + "".join(
        logs.JsonFormatter().format(record) for record in caplog.records
    )
    assert SECRET_SEGMENT not in rendered
    assert REJECTED_CODE not in rendered
    # And the server really did serve, so this is not an empty log
    # asserting nothing: its own structured event for the check-in is
    # there, naming the device rather than the path.
    assert any(record.__dict__.get("event") == "ota_check" for record in caplog.records)


@pytest.mark.asyncio
async def test_the_served_configuration_has_no_access_log() -> None:
    """The property behind the assertion above, stated where a future
    change to the uvicorn configuration would meet it."""
    assert serving.uvicorn_config(create_app(CONFIG), CONFIG).access_log is False


@pytest.mark.asyncio
async def test_debug_puts_no_request_line_and_no_frame_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """And the same deployment run at DEBUG, which is #124.

    Turning the level up is the ordinary thing to do while diagnosing
    something, and until the floor in `logs.configure` existed it undid
    the decision above: `uvicorn.error` traces the request line and
    every request header, so the secret segment and a device's bearer
    token came back, and uvicorn hands that same logger to the
    websockets protocol, which renders every frame's payload with the
    text decoded. Nothing here writes those records, so the assertion is
    about what a library says while the server serves.
    """
    # Seeded through the repository rather than composed straight from
    # the object, because the server reads its device bindings from the
    # database it opens at startup, and a device with no agent is turned
    # away before it can send anything.
    server, task = await _serving(booted(CONFIG), CONFIG)
    port = server.servers[0].sockets[0].getsockname()[1]
    auth = build_device_auth(CONFIG)
    assert auth is not None, "the handshake under test is the authenticated one"
    token = auth.issue(DEVICE_UUID, BOUND_MAC)

    # What `logs.configure` would have done at startup. Applied here
    # rather than through `configure`, which would take the root handler
    # over from the fixture that is doing the capturing.
    levels = {name: logging.getLogger(name).level for name in logs.VENDOR_LOG_FLOORS}
    logs.quiet_vendor_libraries(logging.DEBUG)
    # This test's own client is a device, not the server: both of these
    # narrate what they send, and what is under test is what the server
    # writes.
    caplog.set_level(logging.WARNING, logger="httpx")
    caplog.set_level(logging.WARNING, logger="websockets.client")
    try:
        with caplog.at_level(logging.DEBUG):
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", timeout=30
            ) as client:
                headers = {"Device-Id": DEVICE_MAC, "Client-Id": DEVICE_UUID}
                assert (await client.post(OTA_PATH, json={}, headers=headers)).status_code == 200
            socket = await websockets.connect(
                f"ws://127.0.0.1:{port}{WEBSOCKET_PATH}",
                additional_headers={
                    "Device-Id": BOUND_MAC,
                    "Client-Id": DEVICE_UUID,
                    "Protocol-Version": "1",
                    "Authorization": f"Bearer {token}",
                },
                open_timeout=30,
            )
            await socket.send(
                json.dumps(
                    {
                        "type": "hello",
                        "version": 1,
                        "transport": "websocket",
                        "note": FRAME_SENTINEL,
                        "audio_params": {
                            "format": "opus",
                            "sample_rate": 16000,
                            "channels": 1,
                            "frame_duration": 60,
                        },
                    }
                )
            )
            # The frame has to have been read before the log is read.
            await asyncio.wait_for(socket.recv(), timeout=30)
            await socket.close()
    finally:
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)
        server.should_exit = True
        await task

    rendered = caplog.text + "".join(
        logs.JsonFormatter().format(record) for record in caplog.records
    )
    assert SECRET_SEGMENT not in rendered
    assert token not in rendered
    assert FRAME_SENTINEL not in rendered
    # And the run really was a run: the session opened on the frame that
    # was sent, so there was one to render and a request to trace.
    assert any(record.__dict__.get("event") == "session_open" for record in caplog.records)
