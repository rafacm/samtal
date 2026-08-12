"""The short onboarding path: the key, the mounts, and the miss.

The two boot refusals of the mount matrix (a null `ota_path` with
onboarding off, and an `ota_path` under the reserved `/x/` prefix) are
configuration refusals and live in `test_onboarding_config.py`; what is
here is what a request meets.
"""

import logging
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from samtal_server.app import create_app
from samtal_server.config import Config
from samtal_server.onboarding import KEY_LENGTH, derive_key, onboarding_key
from samtal_server.ota import OTA_PATH
from tests.unit.test_ota import DEVICE_MAC, DEVICE_UUID, SYSTEM_INFO

AUTH_SECRET_ENV = "SAMTAL_AUTH_SECRET"

# Not a real secret: a fixed value, so the derived key below is a vector
# rather than something the test recomputes with the code under test.
SECRET = "a-fixed-secret-for-the-vector"

KEY = "2EOWIW3N"

HEADERS = {"Device-Id": DEVICE_MAC, "Client-Id": DEVICE_UUID}


@pytest.fixture(autouse=True)
def _fixed_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_SECRET_ENV, SECRET)


def client_for(config: Config | None = None) -> TestClient:
    return TestClient(create_app(config if config is not None else Config()))


def _stable(response: Any) -> dict:
    """One OTA reply without the one field that cannot be equal across
    two requests: the server time, which moves by the millisecond."""
    body = response.json()
    assert body.pop("server_time")
    return body


def test_the_key_is_the_documented_derivation() -> None:
    """A vector rather than a recomputation: the label, the truncation
    and the alphabet are what a provisioned board's URL depends on, so a
    change to any of them has to be a deliberate one."""
    assert derive_key(SECRET) == KEY


def test_the_key_is_eight_unambiguous_characters() -> None:
    key = derive_key("another-secret-entirely")
    assert len(key) == KEY_LENGTH
    # Base32: no 0/O and no 1/I/l to misread off a small display.
    assert re.fullmatch(r"[A-Z2-7]{8}", key), key


def test_the_key_follows_the_secret_and_nothing_else() -> None:
    assert derive_key(SECRET) == derive_key(SECRET)
    assert derive_key(SECRET) != derive_key(SECRET + "!")


def test_the_key_is_derived_from_the_configured_secret_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOME_OTHER_VARIABLE", "a-different-secret")
    config = Config(server={"auth": {"secret_env": "SOME_OTHER_VARIABLE"}})
    assert onboarding_key(config) == derive_key("a-different-secret")


def test_a_pinned_key_replaces_the_derivation() -> None:
    """What a secret rotation uses: the previous key kept alive so
    provisioned boards keep reaching the URL they were given."""
    config = Config(server={"onboarding": {"key": "AB2C4D5E"}})
    assert onboarding_key(config) == "AB2C4D5E"

    client = client_for(config)
    assert client.post("/x/AB2C4D5E/", json=SYSTEM_INFO, headers=HEADERS).status_code == 200
    assert client.post(f"/x/{KEY}/", json=SYSTEM_INFO, headers=HEADERS).status_code == 404


def test_auth_turned_off_leaves_no_secret_and_mounts_the_route_keyless() -> None:
    config = Config(server={"auth": {"enabled": False}})
    assert onboarding_key(config) is None

    client = client_for(config)
    assert client.post("/x/", json=SYSTEM_INFO, headers=HEADERS).status_code == 200
    # And nothing is served under a key, since there is no key.
    assert client.post(f"/x/{KEY}/", json=SYSTEM_INFO, headers=HEADERS).status_code == 404


def test_both_paths_are_mounted_by_default() -> None:
    client = client_for()
    assert client.post(f"/x/{KEY}/", json=SYSTEM_INFO, headers=HEADERS).status_code == 200
    assert client.post(OTA_PATH, json=SYSTEM_INFO, headers=HEADERS).status_code == 200


def test_a_null_ota_path_leaves_only_the_short_route() -> None:
    client = client_for(Config(server={"ota_path": None}))
    assert client.post(f"/x/{KEY}/", json=SYSTEM_INFO, headers=HEADERS).status_code == 200
    assert client.post(OTA_PATH, json=SYSTEM_INFO, headers=HEADERS).status_code == 404
    assert client.get(OTA_PATH).status_code == 404


def test_onboarding_turned_off_mounts_no_short_route() -> None:
    client = client_for(Config(server={"onboarding": {"enabled": False}}))
    assert client.post(f"/x/{KEY}/", json=SYSTEM_INFO, headers=HEADERS).status_code == 404
    assert client.get(f"/x/{KEY}/").status_code == 404
    assert client.post("/x/", json=SYSTEM_INFO, headers=HEADERS).status_code == 404
    # The legacy path is untouched, which is the whole of what this
    # deployment asked for.
    assert client.post(OTA_PATH, json=SYSTEM_INFO, headers=HEADERS).status_code == 200


def test_the_key_matches_however_a_phone_keyboard_types_it() -> None:
    client = client_for()
    for typed in (KEY, KEY.lower(), "2eOwIw3N"):
        response = client.post(f"/x/{typed}/", json=SYSTEM_INFO, headers=HEADERS)
        assert response.status_code == 200, typed


def test_the_short_path_answers_exactly_what_the_legacy_path_answers() -> None:
    client = client_for()
    short = client.post(f"/x/{KEY}/", json=SYSTEM_INFO, headers=HEADERS)
    legacy = client.post(OTA_PATH, json=SYSTEM_INFO, headers=HEADERS)
    assert short.status_code == legacy.status_code == 200
    assert _stable(short) == _stable(legacy)
    assert short.headers["content-type"] == legacy.headers["content-type"]


def test_a_wrong_key_is_answered_exactly_as_a_path_that_never_existed() -> None:
    client = client_for()
    missed = client.post("/x/AAAAAAAA/", json=SYSTEM_INFO, headers=HEADERS)
    unserved = client.post("/no/such/route/", json=SYSTEM_INFO, headers=HEADERS)
    assert missed.status_code == unserved.status_code == 404
    assert missed.content == unserved.content
    assert missed.headers["content-type"] == unserved.headers["content-type"]


def test_a_wrong_key_says_nothing_about_the_right_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = client_for()
    with caplog.at_level(logging.WARNING):
        response = client.post("/x/2EOWIW3M/", json=SYSTEM_INFO, headers=HEADERS)
        described = client.get("/x/2EOWIW3M/")

    # The log is where the operator reads the typo off, one character at
    # a time: the attempted key beside the correct one.
    assert "2EOWIW3M" in caplog.text
    assert KEY in caplog.text
    assert any(
        record.__dict__.get("event") == "onboarding_key_mismatch" for record in caplog.records
    )

    # And the response says none of it, at any status, on either method.
    for answered in (response, described):
        assert answered.status_code == 404
        assert KEY not in answered.text
        assert KEY not in str(answered.headers)


@pytest.mark.parametrize("path", [f"/x/{KEY}", OTA_PATH.rstrip("/")])
def test_a_missing_trailing_slash_still_reaches_the_handler(path: str) -> None:
    """A captive portal may strip the trailing slash. Starlette answers
    307, which preserves the method and the body, but that is asserted
    here rather than assumed: a 302 or a 303 would turn the device's
    POST into a GET and lose its system info."""
    client = client_for()

    redirected = client.post(
        path, json=SYSTEM_INFO, headers=HEADERS, follow_redirects=False
    )
    assert redirected.status_code == 307
    assert redirected.headers["location"].endswith(f"{path}/")

    followed = client.post(path, json=SYSTEM_INFO, headers=HEADERS)
    assert followed.status_code == 200
    # The body survived the redirect: this is the version the system info
    # carried, and a lost body would answer with the unknown one.
    assert followed.json()["firmware"]["version"] == "2.4.0"
