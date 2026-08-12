"""The short onboarding path: the key, the mounts, and the miss.

The two boot refusals of the mount matrix (a null `ota_path` with
onboarding off, and an `ota_path` under the reserved `/x/` prefix) are
configuration refusals and live in `test_onboarding_config.py`; what is
here is what a request meets.
"""

import json
import logging
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from samtal_server import logs
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


def _rendered(records: list[logging.LogRecord]) -> tuple[str, list[dict]]:
    """The captured records in both shipped formats: the human one, and
    the JSON one a container writes. A record is only safe when it is
    safe in both."""
    text = logging.Formatter(logs.TEXT_FORMAT)
    formatter = logs.JsonFormatter()
    lines = [formatter.format(record) for record in records]
    return "\n".join(text.format(record) for record in records), [
        json.loads(line) for line in lines
    ]


def test_an_over_typed_key_is_still_quoted_back(caplog: pytest.LogCaptureFixture) -> None:
    """The rule has to keep the mistake it exists for: a key typed with
    a character too many is a typo, not an attack."""
    with caplog.at_level(logging.WARNING):
        client_for().get(f"/x/{KEY}X/")
    assert f"{KEY}X" in caplog.text
    assert KEY in caplog.text


@pytest.mark.parametrize(
    "segment",
    [
        # A forged second log entry, which a raw value would produce in
        # the text format.
        "AAAA%0ABBBB",
        "AAAA%0D%0ABBBB",
        # A control character, invisible in a terminal.
        "AAAA%00BBBB",
        # Longer than anything a person types at an eight-character key.
        "A" * 500,
        # Right alphabet, still too long to be a typo of one.
        "AAAAAAAAAAAAAAAA",
        # An escape sequence, which a terminal would act on.
        "%1B%5B31mAAAA",
    ],
)
def test_an_unshaped_key_is_counted_rather_than_repeated(
    segment: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        response = client_for().get(f"/x/{segment}/")
    assert response.status_code == 404

    text, objects = _rendered(caplog.records)
    assert objects, "the mismatch went unlogged"
    # Nothing of the attempt in either format, in the message or in the
    # structured fields.
    assert "AAAA" not in text
    assert all("AAAA" not in json.dumps(payload) for payload in objects)
    # And nothing forged: one object per record, each a single line.
    assert all(payload["event"] == "onboarding_key_unshaped" for payload in objects)
    assert all("attempted" not in payload for payload in objects)
    assert len(text.splitlines()) == len(caplog.records)


def test_the_correct_key_is_not_broadcast_at_unshaped_probes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The key is deliberately logged for a typo, which is a person who
    is holding it already. A scanner is not, so its line carries the
    length and nothing else."""
    with caplog.at_level(logging.WARNING):
        client_for().get("/x/" + "A" * 500 + "/")
    text, objects = _rendered(caplog.records)
    assert KEY not in text
    assert all(KEY not in json.dumps(payload) for payload in objects)
    assert objects[0]["attempted_length"] == 500


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_wrong_key_without_the_slash_is_answered_the_same_way(method: str) -> None:
    """Left to Starlette, the slashless spelling is a trailing-slash
    redirect that runs before any handler and echoes the attempted key
    in its Location header, which would make a wrong key distinguishable
    from a path that was never served. The router registers that
    spelling itself, behind the same guard, so a miss is a miss on
    either one."""
    client = client_for()
    # The device's POST carries a body; a person checking the URL by
    # hand sends a GET with none. Both must miss the same way.
    body = {"json": SYSTEM_INFO} if method == "post" else {}

    missed = getattr(client, method)(
        "/x/AAAAAAAA", headers=HEADERS, follow_redirects=False, **body
    )
    unserved = getattr(client, method)(
        "/no/such/route", headers=HEADERS, follow_redirects=False, **body
    )

    assert missed.status_code == unserved.status_code == 404
    assert missed.content == unserved.content
    assert missed.headers["content-type"] == unserved.headers["content-type"]
    assert "location" not in missed.headers
    assert "AAAAAAAA" not in str(missed.headers)


def test_the_short_path_serves_both_spellings_itself() -> None:
    """A captive portal saves the typed URL without its trailing slash,
    and a factory board proved on 2026-08-13 that the firmware does not
    follow a redirect on this request: it rendered "code=307" and
    restarted in a loop. So the slashless spelling is served rather than
    redirected, and this asserts it with redirects disabled, which is
    what the device does.
    """
    client = client_for()

    answered = client.post(
        f"/x/{KEY}", json=SYSTEM_INFO, headers=HEADERS, follow_redirects=False
    )
    assert answered.status_code == 200
    assert "location" not in answered.headers
    # The version the system info carried, so the body reached the
    # handler rather than being lost on the way to another URL.
    assert answered.json()["firmware"]["version"] == "2.4.0"

    slashed = client.post(
        f"/x/{KEY}/", json=SYSTEM_INFO, headers=HEADERS, follow_redirects=False
    )
    assert _stable(answered) == _stable(slashed)

    described = client.get(f"/x/{KEY}", headers=HEADERS, follow_redirects=False)
    assert described.status_code == 200
    assert "location" not in described.headers


def test_a_query_on_the_slashless_spelling_still_reaches_the_handler() -> None:
    """A portal that appends something of its own must not turn the URL
    into a miss."""
    answered = client_for().get(
        f"/x/{KEY}?probe=1", headers=HEADERS, follow_redirects=False
    )
    assert answered.status_code == 200


def test_the_keyless_route_serves_both_spellings_too() -> None:
    """Auth off is the trial network, where the portal behaves exactly
    as it does everywhere else."""
    client = client_for(Config(server={"auth": {"enabled": False}}))
    for path in ("/x/", "/x"):
        answered = client.post(
            path, json=SYSTEM_INFO, headers=HEADERS, follow_redirects=False
        )
        assert answered.status_code == 200, path
