"""Load and validate the YAML configuration file.

The path comes from the explicit argument, then the SAMTAL_CONFIG environment
variable; with neither set, defaults apply. Values follow pydantic-settings
source priority: SAMTAL_-prefixed environment variables (nested keys joined
with __, for example SAMTAL_SERVER__PORT) override the YAML file, which
overrides the field defaults.
"""

import os
from pathlib import Path

import yaml
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from samtal_server.config.models import Config, yaml_file_var

CONFIG_ENV_VAR = "SAMTAL_CONFIG"


class ConfigError(Exception):
    """A configuration problem, with a message meant to be shown as is."""


def load_config(path: str | Path | None = None) -> Config:
    if path is None:
        env_path = os.environ.get(CONFIG_ENV_VAR)
        path = Path(env_path) if env_path else None
    else:
        path = Path(path)

    if path is not None:
        _check_config_file(path)

    token = yaml_file_var.set(path)
    try:
        return Config()
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, path)) from exc
    except SettingsError as exc:
        source = str(path) if path is not None else "the configuration"
        raise ConfigError(f"invalid config in {source}: {exc}") from exc
    finally:
        yaml_file_var.reset(token)


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
            f"server/providers/agents/devices/default_agent, got {type(data).__name__}"
        )


def _format_validation_error(exc: ValidationError, path: Path | None) -> str:
    source = str(path) if path is not None else "the configuration"
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
