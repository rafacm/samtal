"""The M7 acceptance, run against whatever server is up.

Four things, in the order a device meets them: the server is alive, the
OTA endpoint hands out a real websocket URL and a real token, that token
is one this deployment's secret signed, and a whole conversation runs
from audio in to spoken audio out.

The device here is xiaozhi-sdk, the same simulator the integration lane
uses, so it performs the token round trip exactly as firmware does:
persist what OTA gave it, send it as a bearer token on the handshake.
"""

import asyncio
import json
import math
import os
import struct
import urllib.error
import urllib.request

import numpy as np
import pytest
from xiaozhi_sdk import XiaoZhiWebsocket

from tests.smoke.conftest import DEVICE_MAC

SAMPLE_RATE = 16000
FRAME_MS = 60
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2

DEVICE_UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"

SYSTEM_INFO = {
    "version": 2,
    "mac_address": DEVICE_MAC,
    "uuid": DEVICE_UUID,
    "application": {"name": "xiaozhi", "version": "2.4.0"},
    "board": {"type": "smoke-test"},
}


def check_version(ota_url: str) -> dict:
    """The OTA POST a device makes on every boot, headers and all."""
    request = urllib.request.Request(
        ota_url,
        data=json.dumps(SYSTEM_INFO).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Device-Id": DEVICE_MAC,
            "Client-Id": DEVICE_UUID,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        assert response.status == 200
        return json.loads(response.read())


def speech_pcm(duration_ms: int) -> bytes:
    samples = SAMPLE_RATE * duration_ms // 1000
    return b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 300 * n / SAMPLE_RATE)))
        for n in range(samples)
    )


def test_the_server_is_alive(wait_for_server, base_url: str) -> None:
    with urllib.request.urlopen(f"{base_url}/healthz", timeout=10) as response:
        body = json.loads(response.read())
    assert body["status"] == "ok"
    assert body["version"]
    assert body["revision"]


def test_the_server_is_ready_for_a_conversation(wait_for_server, base_url: str) -> None:
    """The other probe, read against the shipped image: a container that
    has just started is not draining and has no session, so an
    orchestrator pointing traffic admission here gets a 200 with the one
    word it decides on."""
    with urllib.request.urlopen(f"{base_url}/readyz", timeout=10) as response:
        assert response.status == 200
        assert json.loads(response.read()) == {"status": "ok"}


def test_the_container_knows_which_build_it_is(wait_for_server, base_url: str) -> None:
    """#41: the route from build argument to ARG to ENV to what the
    server reports exists only inside a real image, so this is the one
    lane that can exercise it. Skipped when nothing said what to expect,
    which is how a container someone started by hand behaves."""
    expected = os.environ.get("VINGA_SMOKE_REVISION", "").strip()
    if not expected:
        pytest.skip("VINGA_SMOKE_REVISION is unset: nothing to compare against")
    with urllib.request.urlopen(f"{base_url}/healthz", timeout=10) as response:
        body = json.loads(response.read())
    assert body["revision"] == expected

    ota = check_version(os.environ["VINGA_SMOKE_OTA_URL"])
    assert ota["server"]["revision"] == expected


def test_the_ota_reply_carries_everything_a_device_needs(
    wait_for_server, ota_url: str
) -> None:
    body = check_version(ota_url)
    websocket = body["websocket"]
    assert websocket["url"].startswith(("ws://", "wss://"))
    assert websocket["url"].endswith("/xiaozhi/v1/")
    assert websocket["version"] >= 1
    assert body["firmware"] == {"version": "2.4.0", "url": ""}
    # Nothing ever asks a device to activate.
    assert "activation" not in body


def test_the_issued_token_is_one_this_deployment_signed(
    wait_for_server, ota_url: str, device_auth
) -> None:
    token = check_version(ota_url)["websocket"]["token"]
    assert token, "auth is on, so a bound device must be issued a token"
    assert device_auth.verify(token, DEVICE_UUID, DEVICE_MAC)


async def test_a_whole_conversation_runs_through_the_server(
    wait_for_server, ota_url: str
) -> None:
    """The acceptance: one device, one utterance, one spoken reply, over
    the same OTA-then-websocket path a real board takes."""
    events: list[dict] = []
    reply_finished = asyncio.Event()

    async def on_message(data: dict) -> None:
        events.append(data)
        if data.get("type") == "tts" and data.get("state") == "stop":
            reply_finished.set()

    client = XiaoZhiWebsocket(on_message, ota_url=ota_url, audio_sample_rate=SAMPLE_RATE)
    try:
        assert await client.init_connection(DEVICE_MAC)
        pcm = speech_pcm(960)
        for start in range(0, len(pcm), FRAME_BYTES):
            assert await client.send_audio(pcm[start : start + FRAME_BYTES])
        await client.send_silence_audio(1.2)
        await asyncio.wait_for(reply_finished.wait(), timeout=60)
        await asyncio.sleep(0.3)
        chunks = list(client.output_audio_queue)
    finally:
        await client.close()

    transcripts = [e["text"] for e in events if e.get("type") == "stt"]
    assert transcripts == ["hello"]
    spoken = [
        e["text"] for e in events if e.get("type") == "tts" and e["state"] == "sentence_start"
    ]
    assert spoken == ["You said hello."]
    # And it came back as audio, not just as text.
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    assert audio.size > 0


def test_nothing_but_the_two_endpoints_is_served(wait_for_server, base_url: str) -> None:
    """The security default: a device needs exactly two paths, and
    nothing else is exposed to be found."""
    for path in ("/", "/docs", "/openapi.json", "/redoc"):
        request = urllib.request.Request(f"{base_url}{path}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        assert status == 404, f"{path} answered {status}"
