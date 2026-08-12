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
import logging

import httpx
import pytest
import uvicorn

from samtal_server import logs, main
from samtal_server.app import create_app
from samtal_server.config import Config
from samtal_server.ota import ACTIVATE_SEGMENT

MOCK_PROVIDERS = {stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")}
MOCK_AGENT = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")

# The two values that must not be printed, shaped so that a substring
# search for either cannot match by accident.
SECRET_SEGMENT = "3f9a1c7e-never-a-real-ota-segment"
REJECTED_CODE = "914773"

OTA_PATH = f"/xiaozhi/ota/{SECRET_SEGMENT}/"

DEVICE_MAC = "aa:bb:cc:dd:ee:ff"
DEVICE_UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"

CONFIG = Config(
    providers=MOCK_PROVIDERS,
    agents={"assistant": MOCK_AGENT},
    devices={"11:22:33:44:55:01": "assistant"},
    server={"ota_path": OTA_PATH},
)


async def _serving(app, config: Config):
    """The app under the configuration a deployment is served with, on
    an ephemeral port. The port is overridden on the object rather than
    in the configuration, since the models refuse 0 and that is right
    for a deployment."""
    served = main.uvicorn_config(app, config)
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
    assert main.uvicorn_config(create_app(CONFIG), CONFIG).access_log is False
