"""The startup banner, and the same line on the OTA GET.

What is being pinned is not the wording but the three things the line
has to carry: the URL a person types, which of the three sources it came
from, and, when it is the listen address, that this is a guess. Plus
what it must never carry: the configured `ota_path` segment, and
userinfo riding in from `websocket_url`.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from samtal_server import onboarding
from samtal_server.app import create_app
from samtal_server.config import Config
from samtal_server.config.models import ServerConfig
from samtal_server.ota import OTA_PATH

AUTH_SECRET_ENV = "SAMTAL_AUTH_SECRET"

SECRET = "a-fixed-secret-for-the-vector"

KEY = "2EOWIW3N"

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It rides in as the password of a websocket URL,
# which the websocket validator accepts as it accepts any ws:// string.
PASTED = "hunter2-never-a-real-password-9c3f"


@pytest.fixture(autouse=True)
def _fixed_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_SECRET_ENV, SECRET)


def banner_for(config: Config, caplog: pytest.LogCaptureFixture) -> str:
    with caplog.at_level(logging.INFO):
        onboarding.log_banner(config)
    return caplog.text


def test_the_banner_names_the_public_url_it_was_given(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = Config(server={"public_url": "https://voice.example"})
    line = banner_for(config, caplog)
    assert f"https://voice.example/x/{KEY}/" in line
    assert "from server.public_url" in line
    # An origin that was configured is not a guess and must not read as
    # one.
    assert "guessed" not in line


def test_a_public_url_with_a_path_prefix_keeps_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = Config(server={"public_url": "https://voice.example/samtal/"})
    assert f"https://voice.example/samtal/x/{KEY}/" in banner_for(config, caplog)


@pytest.mark.parametrize(
    ("websocket_url", "origin"),
    [
        ("wss://voice.example/xiaozhi/v1/", "https://voice.example"),
        ("ws://192.168.1.10:8003/xiaozhi/v1/", "http://192.168.1.10:8003"),
        ("wss://voice.example:8443/xiaozhi/v1/", "https://voice.example:8443"),
    ],
)
def test_the_websocket_url_is_the_second_source(
    websocket_url: str, origin: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Behind a TLS proxy the websocket URL is the one key a deployment
    already has to set correctly, so it is the better guess than the
    listen address, with wss mapped to https and ws to http."""
    config = Config(server={"websocket_url": websocket_url})
    line = banner_for(config, caplog)
    assert f"{origin}/x/{KEY}/" in line
    assert "from server.websocket_url" in line


def test_the_public_url_wins_over_the_websocket_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = Config(
        server={
            "public_url": "https://voice.example",
            "websocket_url": "ws://192.168.1.10:8003/xiaozhi/v1/",
        }
    )
    line = banner_for(config, caplog)
    assert f"https://voice.example/x/{KEY}/" in line
    assert "192.168.1.10" not in line


def test_the_listen_address_is_a_guess_and_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    line = banner_for(Config(), caplog)
    assert f"http://0.0.0.0:8003/x/{KEY}/" in line
    assert "guessed from" in line
    # The wildcard address is not somewhere a device can go, and a
    # reader who copies it needs to be told that here rather than after
    # provisioning a board with it.
    assert "0.0.0.0 is where the server listens" in line
    assert "server.public_url" in line


def test_a_guess_from_a_real_address_still_reads_as_a_guess(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = Config(server={"host": "192.168.1.10", "port": 9000})
    line = banner_for(config, caplog)
    assert f"http://192.168.1.10:9000/x/{KEY}/" in line
    assert "guessed from" in line


def test_the_keyless_route_is_what_the_banner_names_without_auth(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = Config(server={"public_url": "http://192.168.1.10:8003", "auth": {"enabled": False}})
    line = banner_for(config, caplog)
    assert "http://192.168.1.10:8003/x/ " in f"{line} "
    assert KEY not in line


def test_onboarding_off_names_the_ota_path_without_quoting_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The legacy segment is a credential: an operator hides the token
    issuer behind it, so it belongs in no log line. The derived key is
    the one recorded exception, and there is none here."""
    secret_path = "/xiaozhi/ota/8f3a9c2b1d4e5f60/"
    config = Config(
        server={
            "public_url": "https://voice.example",
            "ota_path": secret_path,
            "onboarding": {"enabled": False},
        }
    )
    line = banner_for(config, caplog)
    assert "server.ota_path" in line
    assert "https://voice.example" in line
    assert secret_path not in line
    assert "8f3a9c2b1d4e5f60" not in line


def credentialed_config() -> Config:
    """A configuration no file can produce: the websocket validator
    refuses userinfo outright (`test_onboarding_config.py` holds that
    refusal and its no-leak assertions). Built by hand, it is what proves
    the second line of defence, that the origin is derived from the
    parsed hostname and port and never from the raw netloc."""
    server = ServerConfig.model_construct(
        websocket_url=f"wss://admin:{PASTED}@voice.example/xiaozhi/v1/"
    )
    return Config.model_construct(server=server)


def test_userinfo_never_reaches_the_banner_even_if_it_slipped_past(
    caplog: pytest.LogCaptureFixture,
) -> None:
    line = banner_for(credentialed_config(), caplog)
    assert f"https://voice.example/x/{KEY}/" in line
    assert PASTED not in line
    assert "admin" not in line


def test_the_describe_line_names_the_path_it_was_reached_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config(server={"public_url": "https://voice.example"})
    client = TestClient(create_app(config))

    short = client.get(f"/x/{KEY}/")
    assert short.status_code == 200
    assert f"https://voice.example/x/{KEY}/" in short.text
    assert "from server.public_url" in short.text

    # Reached on the legacy path, the line is that path: the URL that
    # works for whoever is holding it, not the one this server prefers.
    legacy = client.get(OTA_PATH)
    assert f"https://voice.example{OTA_PATH}" in legacy.text


def test_the_describe_portal_line_carries_no_userinfo_either() -> None:
    response = TestClient(create_app(credentialed_config())).get(f"/x/{KEY}/")

    portal = [
        line for line in response.text.splitlines() if line.startswith("Type this into")
    ]
    assert portal, response.text
    assert PASTED not in portal[0]
    assert f"https://voice.example/x/{KEY}/" in portal[0]
