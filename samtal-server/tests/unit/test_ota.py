"""The OTA endpoint, exercised the way the firmware uses it.

The request shapes here mirror `Board::GetSystemInfoJson` and the headers
`Ota::SetupHttp` sets in 78/xiaozhi-esp32.
"""

import logging
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from samtal_server import __version__, logs
from samtal_server.app import create_app
from samtal_server.build_info import REVISION_ENV, revision
from samtal_server.config import Config
from samtal_server.ota import DEVICE_ID_PROBLEM, OTA_PATH

MOCK_PROVIDERS = {stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")}
MOCK_AGENT = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")

DEVICE_MAC = "AA:BB:CC:DD:EE:FF"
DEVICE_UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"

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


def client_for(config: Config | None = None) -> TestClient:
    return TestClient(create_app(config if config is not None else Config()))


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


def test_reply_carries_the_websocket_url_the_device_needs() -> None:
    response = post_system_info(client_for())
    assert response.status_code == 200
    websocket = response.json()["websocket"]
    assert websocket["url"] == "ws://testserver/xiaozhi/v1/"
    assert websocket["version"] == 1
    # A bare Config() binds no device to any agent, so this one gets no
    # token; the token cases are in test_ota_tokens.py.
    assert websocket["token"] == ""


def test_configured_websocket_url_wins_over_the_request_address() -> None:
    config = Config(server={"websocket_url": "wss://voice.example/xiaozhi/v1/"})
    response = post_system_info(client_for(config))
    assert response.json()["websocket"]["url"] == "wss://voice.example/xiaozhi/v1/"


def test_configured_protocol_version_is_advertised() -> None:
    config = Config(server={"protocol_version": 3})
    response = post_system_info(client_for(config))
    assert response.json()["websocket"]["version"] == 3


def test_firmware_section_says_up_to_date() -> None:
    response = post_system_info(client_for())
    # The firmware only updates for a strictly newer version, so echoing the
    # reported one back with no URL is how it reads "nothing to do".
    assert response.json()["firmware"] == {"version": "2.4.0", "url": ""}


def test_device_hiding_its_version_is_offered_no_update() -> None:
    response = post_system_info(client_for(), payload={"mac_address": DEVICE_MAC})
    assert response.json()["firmware"] == {"version": "0.0.0", "url": ""}


def test_reply_names_the_server_build_the_device_will_talk_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The one place a device is told what is on the other end (#41).
    # Additive: the firmware reads the keys it knows and ignores the
    # rest, so this cannot disturb an existing board.
    revision.cache_clear()
    monkeypatch.setenv(REVISION_ENV, "1a2b3c4d")
    try:
        body = post_system_info(client_for()).json()
    finally:
        revision.cache_clear()
    assert body["server"] == {
        "name": "samtal-server",
        "version": __version__,
        "revision": "1a2b3c4d",
    }


def test_a_device_the_configuration_covers_is_never_asked_to_activate() -> None:
    """The activation section exists for a device nothing has bound and
    no default agent covers; everything else is answered as it always
    was. The ceremony's own coverage is test_onboarding_activation.py."""
    config = Config(
        providers=MOCK_PROVIDERS, agents={"assistant": MOCK_AGENT}, default_agent="assistant"
    )
    response = post_system_info(client_for(config))
    assert "activation" not in response.json()


def test_activation_is_not_asked_for_with_onboarding_off() -> None:
    config = Config(server={"onboarding": {"enabled": False}})
    response = post_system_info(client_for(config))
    assert "activation" not in response.json()


def test_server_time_is_sent_in_milliseconds_with_an_offset() -> None:
    config = Config(server={"timezone_offset_minutes": 120})
    response = post_system_info(client_for(config))
    server_time = response.json()["server_time"]
    assert server_time["timezone_offset"] == 120
    # Milliseconds since the epoch, not seconds.
    assert server_time["timestamp"] > 1_700_000_000_000


def test_server_time_offset_defaults_to_the_hosts_own() -> None:
    expected = datetime.now().astimezone().utcoffset()
    response = post_system_info(client_for())
    assert response.json()["server_time"]["timezone_offset"] == round(
        expected.total_seconds() / 60
    )


def test_unknown_device_falls_back_to_the_default_agent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = Config(
        providers=MOCK_PROVIDERS,
        agents={"assistant": MOCK_AGENT, "kitchen": MOCK_AGENT},
        devices={"11:22:33:44:55:66": "kitchen"},
        default_agent="assistant",
    )
    with caplog.at_level("INFO"):
        response = post_system_info(client_for(config))
    assert response.status_code == 200
    assert "resolved to agent assistant" in caplog.text


def test_bound_device_resolves_to_its_own_agent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = Config(
        providers=MOCK_PROVIDERS,
        agents={"assistant": MOCK_AGENT, "kitchen": MOCK_AGENT},
        devices={DEVICE_MAC.lower(): "kitchen"},
        default_agent="assistant",
    )
    with caplog.at_level("INFO"):
        post_system_info(client_for(config))
    assert "resolved to agent kitchen" in caplog.text


def test_device_with_no_agent_at_all_is_still_answered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        response = post_system_info(client_for())
    assert response.status_code == 200
    assert "has no agent" in caplog.text


@pytest.mark.parametrize(
    ("device_id", "client_id", "expected"),
    [
        (None, DEVICE_UUID, "Device-Id"),
        ("   ", DEVICE_UUID, "Device-Id"),
        (DEVICE_MAC, None, "Client-Id"),
        (DEVICE_MAC, "  ", "Client-Id"),
    ],
)
def test_missing_identity_headers_are_rejected(
    device_id: str | None, client_id: str | None, expected: str
) -> None:
    response = post_system_info(client_for(), device_id=device_id, client_id=client_id)
    assert response.status_code == 400
    assert expected in response.json()["error"]


def test_device_id_that_is_not_a_mac_is_rejected() -> None:
    response = post_system_info(client_for(), device_id="not-a-mac")
    assert response.status_code == 400
    assert "does not hold a MAC address" in response.json()["error"]


def test_dashed_and_uppercase_macs_resolve_the_same_device(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = Config(
        providers=MOCK_PROVIDERS,
        agents={"kitchen": MOCK_AGENT},
        devices={"aa:bb:cc:dd:ee:ff": "kitchen"},
        default_agent="kitchen",
    )
    with caplog.at_level("INFO"):
        post_system_info(client_for(config), device_id="AA-BB-CC-DD-EE-FF")
    assert "resolved to agent kitchen" in caplog.text


@pytest.mark.parametrize("body", [b"", b"not json", b"[1, 2, 3]"])
def test_unparseable_body_still_gets_a_usable_reply(body: bytes) -> None:
    client = client_for()
    response = client.post(
        OTA_PATH,
        content=body,
        headers={
            "Device-Id": DEVICE_MAC,
            "Client-Id": DEVICE_UUID,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    assert response.json()["websocket"]["url"] == "ws://testserver/xiaozhi/v1/"
    assert response.json()["firmware"]["version"] == "0.0.0"


def test_a_configured_ota_path_is_where_the_endpoint_serves() -> None:
    """The endpoint issues tokens, so it cannot require one. Hiding it
    behind a long random segment is what an operator exposing the server
    publicly has instead."""
    secret_path = "/xiaozhi/ota/8f3a9c2b1d4e/"
    client = client_for(Config(server={"ota_path": secret_path}))

    response = client.post(
        secret_path, json=SYSTEM_INFO, headers={"Device-Id": DEVICE_MAC, "Client-Id": DEVICE_UUID}
    )
    assert response.status_code == 200
    assert response.json()["websocket"]["url"] == "ws://testserver/xiaozhi/v1/"
    assert client.get(secret_path).status_code == 200

    # And the default path is gone, which is the whole point of moving it.
    assert client.post(OTA_PATH, json=SYSTEM_INFO).status_code == 404
    assert client.get(OTA_PATH).status_code == 404


def test_the_websocket_path_does_not_move_with_it() -> None:
    """Only the OTA path is configurable: the websocket is protected by
    the token, so hiding it would buy nothing and cost a device that
    holds an old URL."""
    client = client_for(Config(server={"ota_path": "/somewhere/else/"}))
    response = client.post(
        "/somewhere/else/",
        json=SYSTEM_INFO,
        headers={"Device-Id": DEVICE_MAC, "Client-Id": DEVICE_UUID},
    )
    assert response.json()["websocket"]["url"].endswith("/xiaozhi/v1/")


def test_get_describes_where_devices_are_sent() -> None:
    config = Config(server={"websocket_url": "ws://192.168.1.10:8003/xiaozhi/v1/"})
    response = client_for(config).get(OTA_PATH)
    assert response.status_code == 200
    assert "ws://192.168.1.10:8003/xiaozhi/v1/" in response.text
    # A human checking the endpoint is reachable is also a human who
    # wants to know which build answered.
    assert revision() in response.text


# The configured path is a credential, so nothing may hand it back


SECRET_SEGMENT = "3f9a1c7e-never-a-real-ota-segment"

SECRET_PATH = f"/xiaozhi/ota/{SECRET_SEGMENT}/"


def _secret_client() -> TestClient:
    return client_for(Config(server={"ota_path": SECRET_PATH}))


@pytest.mark.parametrize(
    "method, path, expected",
    [
        ("post", SECRET_PATH.rstrip("/"), 200),
        ("get", SECRET_PATH.rstrip("/"), 200),
        ("post", f"{SECRET_PATH}activate/", 202),
    ],
)
def test_a_slash_that_is_off_by_one_is_answered_not_redirected(
    method: str, path: str, expected: int
) -> None:
    """Starlette answers a trailing-slash miss with a 307 whose Location
    is the corrected URL, and here the corrected URL is the deployment's
    secret path: a request that had only guessed at the segment would
    have been handed the whole of it in a header. Both spellings are
    registered instead, so there is no redirect to carry it."""
    client = _secret_client()
    headers = {"Device-Id": DEVICE_MAC, "Client-Id": DEVICE_UUID}

    body = {"json": SYSTEM_INFO} if method == "post" else {}

    answer = getattr(client, method)(path, headers=headers, follow_redirects=False, **body)

    assert answer.status_code == expected
    assert "location" not in answer.headers
    assert SECRET_SEGMENT not in str(answer.headers)


def test_a_path_that_was_never_served_still_answers_404() -> None:
    """The corollary: dispatching both spellings must not turn the
    router into one that answers anything."""
    client = _secret_client()

    assert client.post("/xiaozhi/ota/wrong-segment/", json=SYSTEM_INFO).status_code == 404
    assert client.post("/xiaozhi/ota/wrong-segment", json=SYSTEM_INFO).status_code == 404
    assert client.post(f"{SECRET_PATH}activate/x", json={}).status_code == 404


# A rejected Device-Id is text a stranger chose


DEVICE_ID_SENTINEL = "sentinel-4b7f\nWARNING forged entry"


@pytest.mark.parametrize("path", [OTA_PATH, f"{OTA_PATH}activate"])
def test_a_device_id_that_is_not_a_mac_is_never_quoted_back(
    path: str, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """This endpoint is unauthenticated and reachable by anything that
    finds the path, so its headers are attacker-controlled text. The
    validator's own sentence quotes what it refused, which would put a
    chosen string into a response body, into the log, and into whatever
    ships the log."""
    client = client_for()

    with caplog.at_level(logging.DEBUG):
        refused = client.post(
            path,
            json=SYSTEM_INFO,
            headers={"Device-Id": DEVICE_ID_SENTINEL, "Client-Id": DEVICE_UUID},
        )

    assert refused.status_code == 400
    assert refused.json()["error"] == DEVICE_ID_PROBLEM
    rendered = (
        refused.text
        + str(refused.headers)
        + caplog.text
        + "".join(logs.JsonFormatter().format(record) for record in caplog.records)
        + "".join(capsys.readouterr())
    )
    assert "sentinel-4b7f" not in rendered
    assert "forged entry" not in rendered
    # And the line that was written is still a usable diagnostic.
    assert "Device-Id" in caplog.text


def test_a_device_id_that_is_not_a_mac_still_says_what_is_expected() -> None:
    refused = client_for().post(
        OTA_PATH, json=SYSTEM_INFO, headers={"Device-Id": "nope", "Client-Id": DEVICE_UUID}
    )

    assert "six colon-separated hex pairs" in refused.json()["error"]
    assert "aa:bb:cc:dd:ee:ff" in refused.json()["error"]


def test_the_root_ota_path_has_only_one_spelling() -> None:
    """An `ota_path` of "/" is a path the validator permits and whose
    slashless spelling is the empty string, which is not a route. The
    endpoint still answers, on the one spelling it has."""
    client = client_for(Config(server={"ota_path": "/"}))
    headers = {"Device-Id": DEVICE_MAC, "Client-Id": DEVICE_UUID}

    assert client.post("/", json=SYSTEM_INFO, headers=headers).status_code == 200
    assert client.post("/activate", json={}, headers=headers).status_code == 202
    assert client.post("/activate/", json={}, headers=headers).status_code == 202
