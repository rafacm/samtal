"""The startup banner, and the portal line on the OTA GET.

What is being pinned is not the wording but the three things the banner
has to carry: the origin a device reaches, which of the three sources it
came from, and, when it is the listen address, that this is a guess.
Plus what it must never carry: userinfo riding in from `websocket_url`,
the configured `ota_path` segment, and, since the PR #153 review, the
derived onboarding key or any URL built from it.

That last one is the deliberate narrowing. The banner used to hand the
operator the whole short URL, key and all, so that a typo diagnosed
itself; a startup line is a retained record, and the key stands in front
of the endpoint that issues device tokens. `vinga-server config
ota-url` prints it instead, to the operator's own terminal. The portal
line on the OTA GET still carries the full URL, and may: it is served
only to whoever already reached the path it names.

The portal line has a fourth source the banner cannot have (#340): the
address the request arrived on, which beats the listen-address guess and
loses to anything configured. It is rebuilt from the request's parsed
hostname and port rather than taken verbatim the way the websocket URL
beside it is, because it is a URL printed for a person to type.
"""

import logging

import pytest

from tests.support.apps import entered_client
from vinga_server import onboarding
from vinga_server.config import Config
from vinga_server.config.models import ServerConfig
from vinga_server.ota import OTA_PATH

AUTH_SECRET_ENV = "VINGA_AUTH_SECRET"

SECRET = "a-fixed-secret-for-the-vector"

KEY = "NUGFZQ2Y"

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It rides in as the password of a websocket URL,
# which the websocket validator accepts as it accepts any ws:// string.
PASTED = "hunter2-never-a-real-password-9c3f"


@pytest.fixture(autouse=True)
def _fixed_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_SECRET_ENV, SECRET)


def banner_for(config: Config, caplog: pytest.LogCaptureFixture) -> str:
    with caplog.at_level(logging.INFO):
        onboarding.log_banner(config.server)
    # The key must be in no part of the line, at any origin and from any
    # source, so every test through this helper checks it rather than
    # one of them checking it once.
    assert KEY not in caplog.text
    return caplog.text


def banner_record(config: Config, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.INFO):
        onboarding.log_banner(config.server)
    (record,) = [r for r in caplog.records if r.__dict__.get("event") == "onboarding_banner"]
    return record


def test_the_banner_names_the_public_url_it_was_given(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = Config(server={"public_url": "https://voice.example"})
    line = banner_for(config, caplog)
    assert "https://voice.example" in line
    assert "from server.public_url" in line
    # An origin that was configured is not a guess and must not read as
    # one.
    assert "guessed" not in line
    # And the operator is told where the rest of the URL comes from,
    # since the line no longer holds it.
    assert "vinga-server config ota-url" in line


def test_a_public_url_with_a_path_prefix_keeps_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = Config(server={"public_url": "https://voice.example/vinga/"})
    assert "https://voice.example/vinga" in banner_for(config, caplog)


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
    assert origin in line
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
    assert "https://voice.example" in line
    assert "192.168.1.10" not in line


def test_the_listen_address_is_a_guess_and_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    line = banner_for(Config(), caplog)
    assert "http://0.0.0.0:8003" in line
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
    assert "http://192.168.1.10:9000" in line
    assert "guessed from" in line


def test_the_banner_says_whether_anything_guards_the_short_route(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With device auth off there is no secret to derive a key from and
    the route mounts at /x/ bare. Which key it is stays unsaid; whether
    there is one at all is a fact about the deployment and is said."""
    keyless = Config(
        server={"public_url": "http://192.168.1.10:8003", "auth": {"enabled": False}}
    )
    assert banner_record(keyless, caplog).keyed is False
    caplog.clear()

    keyed = Config(server={"public_url": "http://192.168.1.10:8003"})
    assert banner_record(keyed, caplog).keyed is True


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
    assert "https://voice.example" in line
    assert PASTED not in line
    assert "admin" not in line


def test_the_describe_line_names_the_path_it_was_reached_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config(server={"public_url": "https://voice.example"})
    with entered_client(config) as client:
        short = client.get(f"/x/{KEY}/")
        assert short.status_code == 200
        assert f"https://voice.example/x/{KEY}/" in short.text
        assert "from server.public_url" in short.text

        # Reached on the legacy path, the line is that path: the URL that
        # works for whoever is holding it, not the one this server prefers.
        legacy = client.get(OTA_PATH)
        assert f"https://voice.example{OTA_PATH}" in legacy.text


@pytest.mark.parametrize(
    "websocket_url",
    [
        f"wss://voice.example:{PASTED}/xiaozhi/v1/",
        "wss://voice.example:99999/xiaozhi/v1/",
        f"wss://[::1:{PASTED}/xiaozhi/v1/",
        "ws://[not-an-address/xiaozhi/v1/",
    ],
)
def test_an_unreadable_websocket_url_falls_back_instead_of_raising(
    websocket_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The validator refuses all of these, so none can come from a file.
    The banner is total anyway: reading a port or a host is what used to
    raise a library ValueError, and a startup line is no place to learn
    that a URL cannot be parsed."""
    server = ServerConfig.model_construct(websocket_url=websocket_url)
    line = banner_for(Config.model_construct(server=server), caplog)

    assert "http://0.0.0.0:8003" in line
    # A guess that had a better source and could not use it is not the
    # same guess as one that never had a source, and says so.
    assert "guessed from" in line
    assert "server.websocket_url could not be read" in line
    assert PASTED not in line


def portal_line(response) -> str:
    """The one line of the OTA GET that names the URL to type."""
    lines = [
        line for line in response.text.splitlines() if line.startswith("Type this into")
    ]
    assert lines, response.text
    return lines[0]


def test_with_no_origin_configured_the_line_is_the_address_that_asked() -> None:
    """The bug this file's neighbour on the same reply never had (#340).

    A default configuration listens on 0.0.0.0, so the banner's origin is
    the listen address and a guess. The websocket line in this same reply
    has always answered with the address the request arrived on, and this
    line printed `http://0.0.0.0:8003/...` beside it: one reply, two
    answers, and the one a person is told to type was the one that works
    nowhere.
    """
    with entered_client(Config()) as client:
        response = client.get(f"/x/{KEY}/", headers={"Host": "192.168.1.34:8003"})

    line = portal_line(response)
    assert f"http://192.168.1.34:8003/x/{KEY}/" in line
    assert "from the address this request arrived on" in line
    # The guess and its caveat belong to a line with no request behind
    # it, and there is a request behind this one.
    assert "guessed from" not in line
    assert "0.0.0.0" not in line
    # And the two lines of the reply now name one server.
    assert "ws://192.168.1.34:8003/xiaozhi/v1/" in response.text


def test_a_configured_origin_still_wins_over_the_address_that_asked() -> None:
    """Unchanged, and the half of the order that must not move: an
    operator who named the deployment is answered with what they named,
    whatever Host a request carried."""
    for server in (
        {"public_url": "https://voice.example"},
        {"websocket_url": "wss://voice.example/xiaozhi/v1/"},
    ):
        with entered_client(Config(server=server)) as client:
            response = client.get(f"/x/{KEY}/", headers={"Host": "192.168.1.34:8003"})

        line = portal_line(response)
        assert f"https://voice.example/x/{KEY}/" in line
        assert "192.168.1.34" not in line


def test_an_ipv6_host_comes_back_in_the_brackets_a_url_needs() -> None:
    """The rebuild goes through a parsed hostname, which is where the
    brackets are lost, and `_bracketed` is what puts them back."""
    with entered_client(Config()) as client:
        response = client.get(f"/x/{KEY}/", headers={"Host": "[2001:db8::1]:8003"})

    assert f"http://[2001:db8::1]:8003/x/{KEY}/" in portal_line(response)


def test_an_unreadable_host_falls_back_to_the_guess_rather_than_raising() -> None:
    """Reading a port out of a Host header is the step that raises: a
    number outside the range is shaped like a port and is not one, and
    the endpoint is unauthenticated, so what arrives is whatever a
    stranger sent. The guess is what is left when the request names no
    address this server can read, which is better than a traceback."""
    with entered_client(Config()) as client:
        response = client.get(f"/x/{KEY}/", headers={"Host": "voice.example:99999"})

    line = portal_line(response)
    assert "http://0.0.0.0:8003" in line
    assert "guessed from" in line


def test_userinfo_in_the_host_header_never_reaches_the_reply() -> None:
    """Two layers, and the test is of the reply rather than of either.

    Starlette refuses a Host header that is not a bare host with an
    optional port, and answers from the address the server is listening
    on instead, so a `user:password@host` never becomes this request's
    netloc at all. Behind that, the portal line is rebuilt from the
    parsed hostname and port for the reason this module's docstring
    gives, which is what keeps the rule a fact about the value rather
    than about a framework version.
    """
    with entered_client(Config()) as client:
        response = client.get(
            f"/x/{KEY}/", headers={"Host": f"admin:{PASTED}@192.168.1.34:8003"}
        )

    assert PASTED not in response.text
    assert "admin" not in response.text
    assert portal_line(response).endswith(f"/x/{KEY}/ (from the address this request arrived on)")


def test_the_describe_portal_line_carries_no_userinfo_either() -> None:
    with entered_client(credentialed_config()) as client:
        response = client.get(f"/x/{KEY}/")

    portal = [
        line for line in response.text.splitlines() if line.startswith("Type this into")
    ]
    assert portal, response.text
    assert PASTED not in portal[0]
    assert f"https://voice.example/x/{KEY}/" in portal[0]
