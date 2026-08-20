"""The websocket handshake gate.

The gate runs before `websocket.accept()`, so a refusal is an HTTP 403
on the upgrade and not a websocket close. That distinction is the point:
it is what upstream does, it is what stock firmware handles by retrying
and refreshing its token at its next OTA check, and it means an
unauthorized caller never gets a socket to send anything down.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.support.configs import DEVICE_MAC, DEVICE_UUID, config_with_agent
from tests.support.wire import device_headers, handshake, shake_hands
from vinga_server.app import create_app
from vinga_server.config import Config
from vinga_server.ws import bearer_token


def issued_for(client: TestClient, device_id: str = DEVICE_MAC.lower()) -> str:
    return client.app.state.composition.device_auth.issue(DEVICE_UUID, device_id)


def test_a_valid_token_gets_a_conversation() -> None:
    with TestClient(create_app(config_with_agent())) as client:
        with handshake(client, device_headers(issued_for(client))) as websocket:
            assert shake_hands(websocket)["type"] == "hello"


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "not-a-token",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.1700000000",
        "a.b.c",
    ],
)
def test_a_missing_or_bad_token_never_reaches_the_accept(token: str | None) -> None:
    with TestClient(create_app(config_with_agent())) as client:
        # The refusal happens on the upgrade, so the client never gets a
        # websocket to read from: connecting is what raises.
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with handshake(client, device_headers(token)):
                pass
    assert excinfo.value.code == 1000


def test_an_expired_token_is_refused() -> None:
    with TestClient(create_app(config_with_agent())) as client:
        auth = client.app.state.composition.device_auth
        # White-box, for the reason `test_auth.py` gives at its own
        # signatures: an expired token cannot be issued, only aged.
        old = 1700000000
        expired = f"{auth._sign(DEVICE_UUID, DEVICE_MAC.lower(), old)}.{old}"
        with pytest.raises(WebSocketDisconnect):
            with handshake(client, device_headers(expired)):
                pass


def test_another_devices_token_is_refused() -> None:
    with TestClient(create_app(config_with_agent())) as client:
        stolen = issued_for(client, "11:22:33:44:55:66")
        with pytest.raises(WebSocketDisconnect):
            with handshake(client, device_headers(stolen)):
                pass


def test_a_dashed_upper_case_mac_still_matches_its_token() -> None:
    """The OTA endpoint signs the normalized MAC, so the gate has to
    normalize too, or a device that writes its MAC in dashes could never
    connect with the token it was just handed."""
    with TestClient(create_app(config_with_agent())) as client:
        token = issued_for(client)
        headers = device_headers(token, device_id="AA-BB-CC-DD-EE-FF")
        with handshake(client, headers) as websocket:
            assert shake_hands(websocket)["type"] == "hello"


def test_a_token_without_the_bearer_scheme_is_refused() -> None:
    with TestClient(create_app(config_with_agent())) as client:
        headers = device_headers(None)
        headers["Authorization"] = issued_for(client)
        with pytest.raises(WebSocketDisconnect):
            with handshake(client, headers):
                pass


def test_the_bearer_scheme_is_matched_case_insensitively() -> None:
    with TestClient(create_app(config_with_agent())) as client:
        headers = device_headers(None)
        headers["Authorization"] = f"bearer {issued_for(client)}"
        with handshake(client, headers) as websocket:
            assert shake_hands(websocket)["type"] == "hello"


def test_a_refusal_is_logged_as_an_auth_rejection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        with TestClient(create_app(config_with_agent())) as client:
            with pytest.raises(WebSocketDisconnect):
                with handshake(client, device_headers(None)):
                    pass

    (rejected,) = [r for r in caplog.records if getattr(r, "event", None) == "auth_rejected"]
    assert rejected.reason == "no_token"
    # No device, and none in the sentence either. Nothing is
    # authenticated when this line is written, so the Device-Id header
    # is a string whoever opened the socket chose (the PR #153 review);
    # the reason token is this server's own word and is what is left.
    assert rejected.device is None
    assert DEVICE_MAC.lower() not in rejected.getMessage()


def test_a_bad_token_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    secret_looking = "Zm9yZ2VkLXNpZ25hdHVyZS1ub2JvZHktc2hvdWxkLXNlZQ.1700000000"
    with caplog.at_level("DEBUG"):
        with TestClient(create_app(config_with_agent())) as client:
            with pytest.raises(WebSocketDisconnect):
                with handshake(client, device_headers(secret_looking)):
                    pass
    assert secret_looking not in caplog.text


def test_a_configuration_rejection_still_happens_after_the_accept() -> None:
    """A device that proved who it is, but that the configuration binds
    to no agent, is told so on an accepted socket: it has a real problem
    to fix, and 1008 with a reason is how it hears about it."""
    with TestClient(create_app(Config())) as client:
        with handshake(client, device_headers(issued_for(client))) as websocket:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                websocket.receive_text()
    assert excinfo.value.code == 1008
    assert "no agent" in excinfo.value.reason


def test_the_gate_is_a_no_op_when_auth_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VINGA_AUTH_SECRET", raising=False)
    config = config_with_agent()
    config.server.auth.enabled = False
    with TestClient(create_app(config)) as client:
        assert client.app.state.composition.device_auth is None
        headers = {"Device-Id": DEVICE_MAC, "Client-Id": DEVICE_UUID}
        with handshake(client, headers) as websocket:
            assert shake_hands(websocket)["type"] == "hello"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc.123", "abc.123"),
        ("bearer abc.123", "abc.123"),
        ("BEARER   abc.123  ", "abc.123"),
        ("Bearer ", None),
        ("Bearer", None),
        ("", None),
        ("abc.123", None),
        ("Basic abc.123", None),
    ],
)
def test_bearer_token_parsing(header: str, expected: str | None) -> None:
    assert bearer_token(header) == expected
