"""The activation ceremony as a device meets it.

What an unbound board receives at its configuration check, what it
receives once it is bound, and what `/activate` answers in between, on
both the legacy OTA path and the short onboarding one. The pending table
itself is tested in test_onboarding_pending.py; here it is the app's own,
reached the way a request reaches it.

The request shapes mirror `Ota::CheckVersion` and `Ota::Activate` in
78/xiaozhi-esp32: the headers the firmware sets, the body it sends for
each activation version, and the status codes it acts on (202 keep
waiting, 200 activated, anything else a failure).
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from tests.support.apps import entered_client
from tests.support.checkin import (
    HEADERS,
    MOCK_AGENT,
    MOCK_PROVIDERS,
    NORMALIZED,
    activate,
    check_in,
    unbound_config,
)
from tests.support.checkin import activation_client as client_for
from tests.support.configs import BOUND_MAC, DEVICE_MAC
from tests.support.events import events as emitted
from tests.support.events import fields_of, only
from vinga_server import logs
from vinga_server.config import Config
from vinga_server.config.cli import DISPATCHED
from vinga_server.onboarding import (
    ACTIVATION_TIMEOUT_MS,
    CODE_DIGITS,
    MINT_BUDGET,
    PENDING_CAPACITY,
    onboarding_key,
    onboarding_path,
)
from vinga_server.ota import ACTIVATE_SEGMENT, OTA_PATH

AUTH_SECRET_ENV = "VINGA_AUTH_SECRET"


def nth_mac(index: int) -> str:
    """One of a series of distinct MACs, for the tests that fill the
    pending table."""
    return f"11:22:33:44:{index // 256:02x}:{index % 256:02x}"


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_SECRET_ENV, "a-fixed-secret-for-the-vector")


def short_path(client: TestClient) -> str:
    return onboarding_path(onboarding_key(client.app.state.composition.server))


# What an unbound device receives


def test_an_unbound_device_is_given_a_code_to_show() -> None:
    with client_for() as client:

        body = check_in(client)

        activation = body["activation"]
        assert len(activation["code"]) == CODE_DIGITS
        assert activation["code"].isdigit()
        # Without a challenge the firmware fails Activate() outright and
        # polls every ten seconds instead of every three.
        assert activation["challenge"] == NORMALIZED
        assert activation["timeout_ms"] == ACTIVATION_TIMEOUT_MS
        # The host on one line and the code under it, which is what the
        # firmware draws on the screen, and what upstream's own server
        # sends.
        assert activation["message"] == f"http://0.0.0.0:8003\n{activation['code']}"


def test_the_message_names_the_deployment_an_operator_typed_in() -> None:
    """The origin the banner resolved, so the screen and the startup
    line agree about where this server is."""
    with client_for(unbound_config(public_url="https://voice.example")) as client:

        activation = check_in(client)["activation"]

        assert activation["message"] == f"https://voice.example\n{activation['code']}"


def test_the_empty_token_stays_beside_the_activation_object() -> None:
    """A device showing a code has nothing to reach yet, and the
    firmware persists what it is handed, so the empty string is what
    clears a token another server left in NVS."""
    with client_for() as client:
        body = check_in(client)

    assert body["activation"]
    assert body["websocket"]["token"] == ""
    assert body["websocket"]["url"]


def test_the_same_device_keeps_being_shown_the_same_code() -> None:
    with client_for() as client:

        first = check_in(client)["activation"]["code"]

        assert check_in(client)["activation"]["code"] == first


def test_two_devices_are_shown_different_codes() -> None:
    with client_for() as client:

        first = check_in(client)["activation"]["code"]
        second = check_in(client, mac="11:22:33:44:55:02")["activation"]["code"]

        assert first != second


# When there is no activation object


def test_a_bound_device_is_never_asked_to_activate() -> None:
    with client_for() as client:

        body = check_in(client, mac=BOUND_MAC)

        assert "activation" not in body
        assert body["websocket"]["token"] != ""


def test_a_configured_default_agent_keeps_todays_behavior_for_unknown_devices() -> None:
    """The upgrade regression. A deployment with a default agent covers
    every unknown MAC by design, so its devices keep receiving a token
    and no activation object: upgrading to this release changes nothing
    for them."""
    config = Config(
        providers=MOCK_PROVIDERS, agents={"assistant": MOCK_AGENT}, default_agent="assistant"
    )
    with client_for(config) as client:

        body = check_in(client)

        assert "activation" not in body
        assert body["websocket"]["token"] != ""


def test_onboarding_turned_off_issues_no_codes() -> None:
    with client_for(unbound_config(onboarding={"enabled": False})) as client:

        body = check_in(client)

        assert "activation" not in body
        assert body["websocket"]["token"] == ""


def test_a_device_bound_to_an_agent_this_server_has_not_loaded_gets_no_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Database truth rather than the loaded-agent filter. The two
    disagree exactly when a binding was written after boot naming an
    agent this process never built, and that state must not mint a code
    for a device an operator has already added: it needs a restart, and
    the log line says so."""
    config = unbound_config()
    # What a binding written after boot looks like from in here: the
    # database says the device is bound, and the snapshot this server
    # loaded has no such agent.
    config.devices[NORMALIZED] = ["written-since-boot"]

    with entered_client(config) as client, caplog.at_level(logging.WARNING):
        body = check_in(client)

    assert "activation" not in body
    assert body["websocket"]["token"] == ""
    assert f"{DISPATCHED} apply" in caplog.text
    assert "restart" not in caplog.text
    assert "device pending claim" not in caplog.text


def test_a_bound_but_unloaded_device_keeps_being_told_to_wait() -> None:
    config = unbound_config()
    config.devices[NORMALIZED] = ["written-since-boot"]

    with entered_client(config) as client:
        assert activate(client).status_code == 202


# The bounds


def test_a_device_arriving_at_the_mint_budget_is_answered_as_it_was_before(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with client_for() as client:
        for index in range(MINT_BUDGET):
            check_in(client, mac=nth_mac(index))

        with caplog.at_level(logging.WARNING):
            body = check_in(client)

        assert "activation" not in body
        assert body["websocket"]["token"] == ""
        assert "was offered no activation code" in caplog.text
        assert str(MINT_BUDGET) in caplog.text
        assert "device bind" in caplog.text


def test_the_cap_names_itself_when_it_is_what_fired(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the shipped constants the budget binds first, so the cap is
    reached only with the budget lifted; it is the bound that still
    holds if the budget is ever raised."""
    monkeypatch.setattr("vinga_server.onboarding.MINT_BUDGET", PENDING_CAPACITY * 2)
    with client_for() as client:
        for index in range(PENDING_CAPACITY):
            check_in(client, mac=nth_mac(index))

        with caplog.at_level(logging.WARNING):
            body = check_in(client)

        assert "activation" not in body
        assert str(PENDING_CAPACITY) in caplog.text


# The code in the logs, and what is not in them


def test_the_code_is_logged_with_the_command_that_binds_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A code is a claim ticket read off a screen, not a credential, so
    it belongs in the line an operator greps for the board in front of
    them. A device token never does."""
    with client_for() as client:

        with caplog.at_level(logging.WARNING):
            code = check_in(client)["activation"]["code"]

        assert f"device pending claim {code}" in caplog.text
        record = next(one for one in caplog.records if one.__dict__.get("event") == "ota_check")
        assert record.__dict__["code"] == code


def test_a_bound_devices_record_carries_no_code(caplog: pytest.LogCaptureFixture) -> None:
    with client_for() as client:

        with caplog.at_level(logging.INFO):
            token = check_in(client, mac=BOUND_MAC)["websocket"]["token"]

        record = next(one for one in caplog.records if one.__dict__.get("event") == "ota_check")
        assert "code" not in record.__dict__
        # And the token that was issued is in no log line either, in either
        # of the two shipped formats.
        assert token not in caplog.text
        assert token not in logs.JsonFormatter().format(record)


def test_the_describe_page_says_nothing_about_any_code() -> None:
    with client_for() as client:
        code = check_in(client)["activation"]["code"]

        page = client.get(OTA_PATH).text

        assert code not in page


# /activate, on both routers


@pytest.mark.parametrize("router", ["legacy", "short"])
def test_activate_says_keep_waiting_until_the_device_is_bound(router: str) -> None:
    with client_for() as client:
        path = OTA_PATH if router == "legacy" else short_path(client)
        check_in(client, path)

        assert activate(client, path).status_code == 202


@pytest.mark.parametrize("router", ["legacy", "short"])
def test_activate_says_activated_once_the_device_resolves_to_a_loaded_agent(
    router: str,
) -> None:
    """A 200 always means the next configuration check hands the device
    its real configuration, which is why it is the loaded agent that
    decides it."""
    with client_for() as client:
        path = OTA_PATH if router == "legacy" else short_path(client)

        assert activate(client, path, mac=BOUND_MAC).status_code == 200


def test_activate_answers_a_device_it_has_never_heard_of_with_202() -> None:
    """A restart loses the table. The device's own loop re-checks
    configuration and gets a fresh code within a couple of minutes, and
    "keep waiting" is what upstream answers meanwhile."""
    with client_for() as client:
        assert activate(client).status_code == 202


def test_activate_needs_the_device_id_header() -> None:
    with client_for() as client:
        response = client.post(f"{OTA_PATH}{ACTIVATE_SEGMENT}", json={})

    assert response.status_code == 400
    assert "Device-Id" in response.json()["error"]


def test_activate_refuses_a_device_id_that_is_not_a_mac() -> None:
    with client_for() as client:
        response = activate(client, mac="not-a-mac")

    assert response.status_code == 400


def test_a_wrong_key_cannot_reach_activate() -> None:
    """The short path's guard applies to every route on it: a wrong key
    is answered exactly as a path that was never served."""
    with client_for() as client:

        wrong = client.post(f"/x/AAAAAAAA/{ACTIVATE_SEGMENT}", json={}, headers=HEADERS)
        unserved = client.post("/nothing/here/activate", json={}, headers=HEADERS)

        assert wrong.status_code == 404
        assert (wrong.text, dict(wrong.headers)) == (unserved.text, dict(unserved.headers))


def test_activate_answers_both_spellings_itself() -> None:
    """Whatever the portal saved is what the device appends `activate`
    to, so both spellings arrive here and both are served rather than
    redirected between: a factory board proved on 2026-08-13 that the
    firmware does not follow a redirect on these requests. A wrong key
    still meets the stock 404 with no Location on either spelling."""
    with client_for() as client:
        short = short_path(client)
        check_in(client, short)

        for path in (f"{short}{ACTIVATE_SEGMENT}", f"{short}{ACTIVATE_SEGMENT}/"):
            answered = client.post(
                path, json={}, headers={"Device-Id": DEVICE_MAC}, follow_redirects=False
            )
            assert answered.status_code == 202, path
            assert "location" not in answered.headers, path

        wrong = client.post(
            f"/x/AAAAAAAA/{ACTIVATE_SEGMENT}/", json={}, headers=HEADERS, follow_redirects=False
        )
        assert wrong.status_code == 404
        assert "location" not in wrong.headers
        assert "AAAAAAAA" not in str(wrong.headers)


# Version 2


def test_a_version_one_body_is_accepted_as_it_is(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A board with no serial number burned, which is every consumer
    board, sends `{}` and version 1; upstream's own server never reads
    that body.

    Stock firmware is the compatibility floor this project promises, so
    the consequence is asserted and not just the status code: the poll
    is answered as still waiting, carrying the code the board is showing
    its owner, and none of the three version-2 checks is applied to a
    body that was never written to answer them. Their three refusals
    ride one event name, so an empty `activation_refused` is all three:
    unreadable body, unknown algorithm, challenge mismatch."""
    with client_for() as client:
        code = check_in(client)["activation"]["code"]

        with caplog.at_level(logging.DEBUG, logger="vinga_server.ota"):
            answer = activate(client, body={}, version="1")

    assert answer.status_code == 202
    assert fields_of(only(caplog, "activation_pending"))["code"] == code
    assert emitted(caplog, "activation_refused") == []


def _refusal(caplog: pytest.LogCaptureFixture) -> str:
    record = next(
        one for one in caplog.records if one.__dict__.get("event") == "activation_refused"
    )
    return record.__dict__["reason"]


def test_a_version_two_body_that_is_not_an_object_is_refused_with_its_own_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with client_for() as client:
        check_in(client)

        with caplog.at_level(logging.WARNING):
            response = client.post(
                f"{OTA_PATH}{ACTIVATE_SEGMENT}",
                content=b"not json at all",
                headers={"Device-Id": DEVICE_MAC, "Activation-Version": "2"},
            )

        assert response.status_code == 202
        assert _refusal(caplog) == "unreadable_body"


def test_a_version_two_body_naming_an_unknown_algorithm_is_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with client_for() as client:
        challenge = check_in(client)["activation"]["challenge"]

        with caplog.at_level(logging.WARNING):
            response = activate(
                client,
                body={"algorithm": "rot13", "challenge": challenge, "hmac": "00"},
                version="2",
            )

        assert response.status_code == 202
        assert _refusal(caplog) == "unknown_algorithm"
        assert "rot13" not in caplog.text


def test_a_version_two_body_answering_another_challenge_is_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A poll answering somebody else's challenge is not evidence of
    anything, so it is refused with the 202 it would have got anyway and
    a line naming which check failed."""
    with client_for() as client:
        check_in(client)

        with caplog.at_level(logging.WARNING):
            response = activate(
                client,
                body={
                    "algorithm": "hmac-sha256",
                    "serial_number": "SN-0001",
                    "challenge": "11:22:33:44:55:66",
                    "hmac": "00",
                },
                version="2",
            )

        assert response.status_code == 202
        assert _refusal(caplog) == "challenge_mismatch"


def test_a_version_two_bodys_serial_number_is_recorded_as_an_observed_fact() -> None:
    """There is nothing to check it against: the HMAC beside it is
    computed with a key burned into the device's eFuses that only the
    vendor's cloud has. It is kept because it is the one durable
    identity a board offers."""
    with client_for() as client:
        challenge = check_in(client)["activation"]["challenge"]

        activate(
            client,
            body={
                "algorithm": "hmac-sha256",
                "serial_number": "SN-0001",
                "challenge": challenge,
                "hmac": "9f" * 32,
            },
            version="2",
        )

        pending = client.app.state.composition.pending
        assert pending.waiting_for(NORMALIZED).serial_number == "SN-0001"


def test_a_refused_version_two_body_records_no_serial_number() -> None:
    with client_for() as client:
        check_in(client)

        activate(
            client,
            body={"algorithm": "hmac-sha256", "serial_number": "SN-0001", "challenge": "wrong"},
            version="2",
        )

        assert client.app.state.composition.pending.waiting_for(NORMALIZED).serial_number is None


def test_nothing_of_a_refused_body_is_repeated_into_a_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The body is attacker-controlled text, and a version-2 one carries
    an HMAC. Neither the values nor anything shaped like an injected log
    entry may reach either of the shipped formats."""
    sentinel = "sentinel-9f3a\nWARNING forged entry"
    with client_for() as client:
        check_in(client)

        with caplog.at_level(logging.WARNING):
            activate(
                client,
                body={"algorithm": sentinel, "serial_number": sentinel, "challenge": sentinel},
                version="2",
            )

        rendered = caplog.text + "".join(
            logs.JsonFormatter().format(record) for record in caplog.records
        )
        assert "sentinel-9f3a" not in rendered
        assert "forged entry" not in rendered
        for record in caplog.records:
            assert json.loads(logs.JsonFormatter().format(record))
