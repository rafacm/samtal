"""Token issuance from the OTA endpoint.

The endpoint is unauthenticated, because it is the issuer: a token check
here would be circular. What bounds it is who gets a token. A device the
configuration resolves to no agent would be turned away at the websocket
anyway, so it gets nothing to try with, and the `devices` map plus
`default_agent` is therefore the allowlist.

And, since two of the three answers are the same empty string, the word
the reply carries for which of them it is.
"""

from typing import Any

import pytest

from tests.support.apps import entered_client
from tests.support.checkin import MOCK_AGENT, MOCK_PROVIDERS, SYSTEM_INFO
from tests.support.configs import DEVICE_MAC, DEVICE_UUID
from vinga_server.auth import DeviceAuth, build_device_auth
from vinga_server.config import Config
from vinga_server.ota import OTA_PATH


def bound_config(**overrides: object) -> Config:
    return Config(
        **(
            {
                "providers": MOCK_PROVIDERS,
                "agents": {"assistant": MOCK_AGENT},
                "devices": {DEVICE_MAC.lower(): ["assistant"]},
            }
            | overrides
        )
    )


def checked_in(config: Config, device_id: str = DEVICE_MAC) -> dict[str, Any]:
    """The whole reply, because the token and the word for it are one
    answer and the cases below read both."""
    with entered_client(config) as client:
        response = client.post(
            OTA_PATH,
            json=SYSTEM_INFO,
            headers={"Device-Id": device_id, "Client-Id": DEVICE_UUID},
        )
    assert response.status_code == 200
    return response.json()


def issued_token(config: Config, device_id: str = DEVICE_MAC) -> str:
    return checked_in(config, device_id)["websocket"]["token"]


def test_a_bound_device_gets_a_token_this_server_verifies() -> None:
    config = bound_config()
    token = issued_token(config)
    auth = build_device_auth(config)
    assert isinstance(auth, DeviceAuth)
    # Verified against the same identity pair it was issued for, which is
    # the pair the device will send on the websocket handshake.
    assert auth.verify(token, DEVICE_UUID, DEVICE_MAC.lower())


def test_the_token_is_bound_to_this_device_and_no_other() -> None:
    config = bound_config()
    token = issued_token(config)
    auth = build_device_auth(config)
    assert auth is not None
    assert not auth.verify(token, DEVICE_UUID, "11:22:33:44:55:66")


def test_the_mac_is_normalized_before_it_is_signed() -> None:
    """A device writing its MAC in dashes and upper case gets a token
    that still verifies against the normalized form the websocket uses."""
    config = bound_config()
    token = issued_token(config, device_id="AA-BB-CC-DD-EE-FF")
    auth = build_device_auth(config)
    assert auth is not None
    assert auth.verify(token, DEVICE_UUID, DEVICE_MAC.lower())


def test_a_device_with_no_agent_gets_no_token() -> None:
    """No agent means no conversation to reach, so there is nothing to
    hand it: the devices map plus default_agent is the allowlist."""
    config = Config(
        providers=MOCK_PROVIDERS,
        agents={"assistant": MOCK_AGENT},
        devices={"11:22:33:44:55:66": ["assistant"]},
    )
    assert issued_token(config) == ""


def test_the_default_agent_makes_every_device_a_bound_one() -> None:
    config = bound_config(default_agent="assistant")
    assert issued_token(config, device_id="11:22:33:44:55:66") != ""


def test_disabled_auth_still_sends_an_empty_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sent rather than omitted: the firmware persists what it is given,
    so an empty token clears one another server left in NVS, where a
    missing key would leave it in place."""
    monkeypatch.delenv("VINGA_AUTH_SECRET", raising=False)
    config = bound_config(server={"auth": {"enabled": False}})
    with entered_client(config) as client:
        body = client.post(
            OTA_PATH,
            json=SYSTEM_INFO,
            headers={"Device-Id": DEVICE_MAC, "Client-Id": DEVICE_UUID},
        )
    assert body.json()["websocket"]["token"] == ""


# Why the token is what it is, said in the reply itself (#369)
#
# The empty string a deployment that issues no tokens sends and the
# empty string a board nothing resolves gets are the same bytes, and the
# server is the only side that knows which it just decided.


def test_a_credential_is_answered_with_the_word_for_a_credential() -> None:
    body = checked_in(bound_config())
    assert body["access"] == "token"
    assert body["websocket"]["token"] != ""


def test_disabled_auth_says_the_deployment_issues_no_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reading the empty token above could not carry: this board is
    admitted and there is simply no credential to hand it.

    The token half is the pin above's; it is asserted here as well
    because the pair is what must never disagree.
    """
    monkeypatch.delenv("VINGA_AUTH_SECRET", raising=False)
    body = checked_in(bound_config(server={"auth": {"enabled": False}}))
    assert body["access"] == "open"
    assert body["websocket"]["token"] == ""


@pytest.mark.parametrize("auth_enabled", [True, False])
def test_a_device_with_no_agent_is_denied_under_either_auth_setting(
    monkeypatch: pytest.MonkeyPatch, auth_enabled: bool
) -> None:
    """Being unresolved is the stronger of the two facts: turning device
    authentication off does not give a board an agent to reach, so it is
    turned away under either setting."""
    if not auth_enabled:
        monkeypatch.delenv("VINGA_AUTH_SECRET", raising=False)
    config = Config(
        providers=MOCK_PROVIDERS,
        agents={"assistant": MOCK_AGENT},
        devices={"11:22:33:44:55:66": ["assistant"]},
        server={"auth": {"enabled": auth_enabled}},
    )
    body = checked_in(config)
    assert body["access"] == "denied"
    assert body["websocket"]["token"] == ""


def test_the_token_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("DEBUG"):
        token = issued_token(bound_config())
    assert token
    assert token not in caplog.text
    for record in caplog.records:
        assert token not in str(vars(record))
