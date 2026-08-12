"""The configuration keys the short onboarding path is built on.

`server.onboarding` (the switch and the pinned key), `server.public_url`
(the origin the startup banner names), and what `server.ota_path` gains:
null to unmount the legacy route, and `/x/` reserved beside `/api/`.
`server.websocket_url` is here too, because what made its refusals
stricter is this milestone: the OTA endpoint's GET renders it verbatim,
and the short path serves that GET to anyone holding the onboarding URL.

Three values here are credentials of a sort and are checked never to be
quoted back: a pinned onboarding key is the segment the token issuer is
served under, and a public or websocket URL a person pasted may carry
userinfo. The sentinel below is checked against everything a refusal can
reach, the way `test_config_env_names.py` does it.
"""

import logging
from pathlib import Path

import pytest

from samtal_server.config import Config, ConfigError
from samtal_server.config.cli import main
from samtal_server.config.models import ONBOARDING_MOUNT_PATH
from tests.unit.test_config import load_config_from_data

# Not a real credential, and shaped so a substring check for it cannot
# match by accident.
PASTED = "hunter2-never-a-real-password-9c3f"


def _chain(exc: BaseException) -> str:
    """A refusal plus everything it is chained to, which is where
    pydantic's own quoting of the rejected input would show up."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [repr(current), str(current)]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


def test_onboarding_is_on_by_default_with_no_pinned_key() -> None:
    onboarding = Config().server.onboarding
    assert onboarding.enabled is True
    assert onboarding.key is None


def test_a_pinned_key_is_accepted_and_normalized() -> None:
    config = load_config_from_data({"server": {"onboarding": {"key": " ab2c4d5e "}}})
    # Upper case is the canonical form, and what the derivation produces;
    # a phone keyboard types the lower-case one, which the route matches.
    assert config.server.onboarding.key == "AB2C4D5E"


@pytest.mark.parametrize(
    "key",
    [
        "ABC",  # too short
        "AB2C4D5E9",  # too long
        "AB2C4D50",  # 0 is not in the base32 alphabet
        "AB2C4D51",  # nor is 1
        "AB2C-D5E",  # nor is anything outside A-Z2-7
        "",
    ],
)
def test_a_key_of_the_wrong_shape_is_refused(key: str) -> None:
    with pytest.raises(ConfigError) as caught:
        load_config_from_data({"server": {"onboarding": {"key": key}}})
    assert "eight base32 characters" in str(caught.value)


def test_the_refused_key_is_not_quoted_back(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG), pytest.raises(ConfigError) as caught:
        load_config_from_data({"server": {"onboarding": {"key": PASTED}}})

    assert PASTED not in str(caught.value)
    assert PASTED not in _chain(caught.value)
    assert all(PASTED not in record.getMessage() for record in caplog.records)


def test_a_websocket_url_carrying_credentials_is_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The leak this closes: the OTA endpoint's GET renders this value
    verbatim, and the short onboarding path serves that same GET, so a
    user:password written here was readable by anyone who had the
    onboarding URL. Refused at load, so no server serves it at all."""
    with caplog.at_level(logging.DEBUG), pytest.raises(ConfigError) as caught:
        load_config_from_data(
            {"server": {"websocket_url": f"wss://admin:{PASTED}@voice.example/xiaozhi/v1/"}}
        )

    message = str(caught.value)
    assert "not a usable websocket URL" in message
    assert "user:password" in message
    assert PASTED not in message
    assert PASTED not in _chain(caught.value)
    assert all(PASTED not in record.getMessage() for record in caplog.records)


def test_a_credentialed_websocket_url_reaches_no_stderr_through_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole boundary: the CLI reads the same file half, and prints
    its refusal to stderr."""
    monkeypatch.delenv("SAMTAL_CONFIG", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(
        f"server:\n  websocket_url: wss://admin:{PASTED}@voice.example/xiaozhi/v1/\n",
        encoding="utf-8",
    )

    assert main(["--config", str(path), "list"]) == 1

    captured = capsys.readouterr()
    assert "not a usable websocket URL" in captured.err
    assert PASTED not in captured.err
    assert PASTED not in captured.out


def test_an_ordinary_websocket_url_still_passes() -> None:
    config = load_config_from_data(
        {"server": {"websocket_url": " wss://voice.example/xiaozhi/v1/ "}}
    )
    assert config.server.websocket_url == "wss://voice.example/xiaozhi/v1/"


def test_public_url_is_unset_by_default() -> None:
    assert Config().server.public_url is None


@pytest.mark.parametrize(
    ("written", "stored"),
    [
        ("https://voice.example", "https://voice.example"),
        # The trailing slash is normalized away, so the onboarding path
        # joins it without doubling the separator.
        ("https://voice.example/", "https://voice.example"),
        ("http://192.168.1.10:8003/", "http://192.168.1.10:8003"),
        # A proxy may serve the server under a prefix, which is part of
        # the URL a person types.
        ("https://voice.example/samtal/", "https://voice.example/samtal"),
        ("  https://voice.example  ", "https://voice.example"),
    ],
)
def test_a_public_origin_is_accepted_and_normalized(written: str, stored: str) -> None:
    config = load_config_from_data({"server": {"public_url": written}})
    assert config.server.public_url == stored


@pytest.mark.parametrize(
    "url",
    [
        "voice.example",
        "ws://voice.example",
        "wss://voice.example",
        "https://",
        "",
    ],
)
def test_a_public_url_that_is_not_an_http_origin_is_refused(url: str) -> None:
    with pytest.raises(ConfigError) as caught:
        load_config_from_data({"server": {"public_url": url}})
    assert "not a usable public URL" in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        f"https://admin:{PASTED}@voice.example",
        f"https://voice.example?token={PASTED}",
        f"https://voice.example#{PASTED}",
    ],
)
def test_userinfo_a_query_and_a_fragment_are_refused_without_the_value(
    url: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The one value that is printed at startup and handed to a person to
    type, so what it may not carry is refused rather than stripped, and
    the refusal never repeats it."""
    with caplog.at_level(logging.DEBUG), pytest.raises(ConfigError) as caught:
        load_config_from_data({"server": {"public_url": url}})

    message = str(caught.value)
    assert "not a usable public URL" in message
    assert PASTED not in message
    assert PASTED not in _chain(caught.value)
    assert all(PASTED not in record.getMessage() for record in caplog.records)


def test_a_null_ota_path_unmounts_the_legacy_route() -> None:
    config = load_config_from_data({"server": {"ota_path": None}})
    assert config.server.ota_path is None
    # And the short route is what serves devices instead, so it has to be
    # on for this to be a configuration rather than a dead server.
    assert config.server.onboarding.enabled is True


def test_a_null_ota_path_with_onboarding_off_refuses_the_boot() -> None:
    with pytest.raises(ConfigError) as caught:
        load_config_from_data(
            {"server": {"ota_path": None, "onboarding": {"enabled": False}}}
        )
    message = str(caught.value)
    assert "no device could fetch its configuration" in message
    # Both ways out are named, since either is a legitimate deployment.
    assert "ota_path" in message
    assert "onboarding" in message


@pytest.mark.parametrize(
    ("path", "segment"),
    [
        (f"{ONBOARDING_MOUNT_PATH}/", None),
        (f"{ONBOARDING_MOUNT_PATH}/{PASTED}/", PASTED),
    ],
)
def test_an_ota_path_under_the_onboarding_prefix_is_refused(
    path: str, segment: str | None
) -> None:
    """The short route owns /x/, so an OTA path there would be two
    routers claiming one prefix."""
    with pytest.raises(ConfigError) as caught:
        load_config_from_data({"server": {"ota_path": path}})
    message = str(caught.value)
    assert f"{ONBOARDING_MOUNT_PATH}/ is reserved" in message
    # The prefix is named, since that is the rule being explained, but
    # the configured segment is not: it is the closest thing this key has
    # to a secret, the same posture the /api/ refusal holds.
    if segment is not None:
        assert segment not in message
        assert segment not in _chain(caught.value)


def test_a_path_that_merely_starts_with_x_is_still_allowed() -> None:
    """The reservation is the /x/ prefix, not the letter: /xiaozhi/ota/
    is the default and has to keep working."""
    assert load_config_from_data(
        {"server": {"ota_path": "/xiaozhi/ota/"}}
    ).server.ota_path == "/xiaozhi/ota/"
