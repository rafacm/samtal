"""What device authentication does to the boot.

The decision this pins: a server with auth enabled and no secret in the
environment refuses to start. The alternative, warn and serve open,
turns a forgotten environment variable into a silent hole that looks
exactly like a working deployment.
"""

import pytest

from samtal_server.app import create_app
from samtal_server.auth import DeviceAuth, build_device_auth
from samtal_server.config import Config, ConfigError

SECRET_ENV = "SAMTAL_AUTH_SECRET"


def test_auth_is_on_by_default() -> None:
    auth = Config().server.auth
    assert auth.enabled is True
    assert auth.secret_env == SECRET_ENV
    assert auth.token_expire_s == 2592000


def test_a_secret_in_the_environment_gives_an_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SECRET_ENV, "a" * 64)
    assert isinstance(build_device_auth(Config()), DeviceAuth)


def test_enabled_auth_without_a_secret_refuses_to_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(SECRET_ENV, raising=False)
    with pytest.raises(ConfigError) as excinfo:
        create_app(Config())

    message = str(excinfo.value)
    # The message has to carry the fix and the way out, because this is
    # the error a first deployment meets.
    assert SECRET_ENV in message
    assert "openssl rand -hex 32" in message
    assert "server.auth.enabled: false" in message
    assert "SAMTAL_SERVER__AUTH__ENABLED=false" in message


def test_a_blank_secret_counts_as_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_ENV, "   ")
    with pytest.raises(ConfigError):
        build_device_auth(Config())


def test_a_custom_secret_env_var_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SECRET_ENV, raising=False)
    monkeypatch.setenv("MY_OWN_SECRET", "b" * 64)
    config = Config(server={"auth": {"secret_env": "MY_OWN_SECRET"}})
    assert isinstance(build_device_auth(config), DeviceAuth)


def test_disabled_auth_boots_with_no_secret_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(SECRET_ENV, raising=False)
    config = Config(server={"auth": {"enabled": False}})
    assert build_device_auth(config) is None
    assert create_app(config).state.composition.device_auth is None


def test_auth_can_be_turned_off_through_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one-flag LAN opt-out, over the ordinary env override path."""
    monkeypatch.delenv(SECRET_ENV, raising=False)
    monkeypatch.setenv("SAMTAL_SERVER__AUTH__ENABLED", "false")
    from samtal_server.config import Config, load_file_config

    # Read as the file half, which is where the auth section lives, and
    # composed onto an empty domain half the way boot composes it.
    config = Config(server=load_file_config().server)
    assert config.server.auth.enabled is False
    assert build_device_auth(config) is None


def test_the_issuer_is_built_once_and_hangs_on_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SECRET_ENV, "c" * 64)
    app = create_app(Config())
    assert isinstance(app.state.composition.device_auth, DeviceAuth)


def test_the_token_lifetime_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_ENV, "d" * 64)
    auth = build_device_auth(Config(server={"auth": {"token_expire_s": 60}}))
    assert auth is not None
    assert auth._expire_s == 60


def test_a_zero_or_negative_lifetime_is_rejected() -> None:
    with pytest.raises(ValueError):
        Config(server={"auth": {"token_expire_s": 0}})
