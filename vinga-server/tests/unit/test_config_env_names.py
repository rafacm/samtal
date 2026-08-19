"""What a key that names an environment variable may hold.

`server.auth.secret_env` and `server.api.secret_env` name the variable
holding a secret; they never hold the secret. The mistake that lands in
them is pasting the value where its variable name belongs, and the
failure that follows quotes the variable name it tells the operator to
set, so a bare non-blank string would print the paste.

Nothing that boots today stops booting: a pasted value never resolved to
a set variable, so such a configuration was already failing, only later
and while echoing what was pasted.

The sentinel below is checked against everything a refusal can reach:
the message, stderr, the captured log records, and the exception chain.
"""

import logging
from pathlib import Path

import pytest

from vinga_server.config import Config, ConfigError, load_file_config
from vinga_server.config.cli import main

# Not a real credential, and shaped so a substring check for it cannot
# match by accident.
PASTED = "sk-ant-api03-never-a-real-credential-3f9c"

SECRET_ENV_KEYS = ("auth", "api")


def _chain(exc: BaseException) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [repr(current), str(current)]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


def _config_file(tmp_path: Path, section: str, value: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(f"server:\n  {section}:\n    secret_env: {value}\n", encoding="utf-8")
    return path


def test_both_keys_default_to_their_own_variable() -> None:
    server = Config().server
    assert server.auth.secret_env == "VINGA_AUTH_SECRET"
    assert server.api.secret_env == "VINGA_API_SECRET"


@pytest.mark.parametrize("section", SECRET_ENV_KEYS)
def test_a_variable_name_is_accepted(tmp_path: Path, section: str) -> None:
    path = _config_file(tmp_path, section, "MY_OWN_SECRET")
    loaded = getattr(load_file_config(path).server, section)
    assert loaded.secret_env == "MY_OWN_SECRET"


@pytest.mark.parametrize("section", SECRET_ENV_KEYS)
def test_a_pasted_value_is_refused_without_being_quoted(
    tmp_path: Path, section: str, caplog: pytest.LogCaptureFixture
) -> None:
    path = _config_file(tmp_path, section, PASTED)

    with caplog.at_level(logging.DEBUG), pytest.raises(ConfigError) as caught:
        load_file_config(path)

    message = str(caught.value)
    assert f"server.{section}.secret_env" in message
    # What the key must hold, and an example of it: the wording the
    # provider validator uses for a key ending in _env.
    assert "must hold the name of an environment variable" in message
    assert "secret_env: VINGA_API_SECRET" in message

    assert PASTED not in message
    # pydantic's own str() quotes the rejected input back, so the chain
    # is the place this leaks if the cause is left attached.
    assert PASTED not in _chain(caught.value)
    assert all(PASTED not in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("section", SECRET_ENV_KEYS)
def test_a_pasted_value_reaches_no_stderr_through_the_cli(
    tmp_path: Path, section: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole boundary: the CLI reads the same file half to find the
    database, and prints a refusal to stderr."""
    path = _config_file(tmp_path, section, PASTED)

    assert main(["--config", str(path), "list"]) == 1

    captured = capsys.readouterr()
    assert "must hold the name of an environment variable" in captured.err
    assert PASTED not in captured.err
    assert PASTED not in captured.out


@pytest.mark.parametrize("section", SECRET_ENV_KEYS)
def test_an_empty_or_spaced_name_is_refused_too(tmp_path: Path, section: str) -> None:
    for written in ('""', '"a name"', '"1LEADING_DIGIT"'):
        with pytest.raises(ConfigError):
            load_file_config(_config_file(tmp_path, section, written))
