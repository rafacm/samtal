"""Load the file half of the configuration, and compose the whole one.

The path comes from the explicit argument, then the SAMTAL_CONFIG environment
variable; with neither set, defaults apply. Values follow pydantic-settings
source priority: SAMTAL_-prefixed environment variables (nested keys joined
with __, for example SAMTAL_SERVER__PORT) override the YAML file, which
overrides the field defaults.

The file holds `server` and `memory`. The domain half lives in the
database, and composition puts the two together into `Config`, which is
what validates the whole snapshot the way it always has.
"""

import os
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from samtal_server.config.models import Config, FileConfig, yaml_file_var

CONFIG_ENV_VAR = "SAMTAL_CONFIG"


class ConfigError(Exception):
    """A configuration problem, with a message meant to be shown as is."""


def load_file_config(path: str | Path | None = None) -> FileConfig:
    """The file half of the configuration: `server` and `memory`."""
    if path is None:
        env_path = os.environ.get(CONFIG_ENV_VAR)
        path = Path(env_path) if env_path else None
    else:
        path = Path(path)

    if path is not None:
        _check_config_file(path)

    token = yaml_file_var.set(path)
    try:
        return FileConfig()
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, _source(path))) from exc
    except SettingsError as exc:
        raise ConfigError(f"invalid config in {_source(path)}: {exc}") from exc
    finally:
        yaml_file_var.reset(token)


def compose_config(
    file_half: FileConfig, domain: Mapping[str, object], source: str
) -> Config:
    """The configuration the server boots on: the file half plus the
    domain half, validated together.

    `source` is where the domain half came from, so a problem in it
    names the database rather than a file nobody edited. The domain
    values are the loaded models themselves, which pydantic accepts as
    they are; a mapping of raw sections is equally acceptable, and is
    what a test that has no database in hand passes.
    """
    try:
        return Config(server=file_half.server, memory=file_half.memory, **domain)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, source)) from exc


def _source(path: Path | None) -> str:
    return str(path) if path is not None else "the configuration"


def _check_config_file(path: Path) -> None:
    """Pre-flight check with stable, helpful messages: the pydantic-settings
    YAML source silently skips a missing file, and its parse errors do not
    reliably name line and column."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc.strerror}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        detail = str(exc)
        if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
            mark = exc.problem_mark
            detail = f"{exc.problem} at line {mark.line + 1}, column {mark.column + 1}"
        raise ConfigError(f"invalid YAML in {path}: {detail}") from exc

    if data is not None and not isinstance(data, dict):
        raise ConfigError(
            f"invalid config in {path}: top level must be a mapping of "
            f"server/memory, got {type(data).__name__}"
        )


def _format_validation_error(exc: ValidationError, source: str) -> str:
    lines = [f"invalid config in {source}:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        message = error["msg"]
        # Errors raised as ValueError inside validators arrive prefixed by
        # pydantic; strip that to keep our own wording.
        message = message.removeprefix("Value error, ")
        for line in message.splitlines():
            lines.append(f"  - {location}: {line}" if location else f"  - {line}")
    return "\n".join(lines)
