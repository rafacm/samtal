"""Load the file half of the configuration, and compose the whole one.

The path comes from the explicit argument, then the SAMTAL_CONFIG environment
variable; with neither set, defaults apply. Values follow pydantic-settings
source priority: SAMTAL_-prefixed environment variables (nested keys joined
with __, for example SAMTAL_SERVER__PORT) override the YAML file, which
overrides the field defaults.

The file holds `server` and `memory`. The domain half lives in the
database, so a domain section left in the file, or a SAMTAL_ override for
one, refuses the boot naming where it moved and the command that writes
it: a key that quietly stopped applying is the trap this closes.
Composition then puts the two halves together into `Config`, which is
what validates the whole snapshot the way it always has.
"""

import os
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from samtal_server.config.models import (
    DOMAIN_KEYS,
    Config,
    FileConfig,
    yaml_file_var,
)

CONFIG_ENV_VAR = "SAMTAL_CONFIG"

# The prefix a configuration override carries. Only the domain names
# below are scanned for: everything else under this prefix is either a
# key the file half still owns or a variable that carries a value rather
# than naming a section (SAMTAL_CONFIG, SAMTAL_MASTER_KEY,
# SAMTAL_AUTH_SECRET), and none of those moved anywhere.
ENV_PREFIX = "SAMTAL_"

# What writes each moved section now, so the refusal answers the question
# it raises. One entry per key in DOMAIN_KEYS, checked below.
MOVED_KEY_COMMANDS: dict[str, str] = {
    "providers": "samtal-server config set provider <stage> <name> -f fragment.yaml",
    "mcp_servers": "samtal-server config set mcp-server <name> -f fragment.yaml",
    "agent_defaults": "samtal-server config set agent-defaults -f fragment.yaml",
    "agents": "samtal-server config set agent <name> -f fragment.yaml",
    "devices": "samtal-server config bind-device <mac> <agent> [<agent> ...]",
    "default_agent": "samtal-server config set-default-agent <name>",
}

# Where the reference for the moved half is, quoted in the refusal
# because that document is what a reader needs next.
DOMAIN_REFERENCE = "docs/reference/domain-config.md"


class ConfigError(Exception):
    """A configuration problem, with a message meant to be shown as is."""


def load_file_config(path: str | Path | None = None) -> FileConfig:
    """The file half of the configuration: `server` and `memory`."""
    if path is None:
        env_path = os.environ.get(CONFIG_ENV_VAR)
        path = Path(env_path) if env_path else None
    else:
        path = Path(path)

    _check_moved_environment()
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

    if isinstance(data, dict):
        _check_moved_keys(path, data)


def _check_moved_keys(path: Path, data: dict) -> None:
    """A domain section left in the file, refused where the parsed top
    level is already in hand. Ignoring it silently would leave a
    deployment editing a section the server no longer reads."""
    moved = [key for key in DOMAIN_KEYS if key in data]
    if not moved:
        return
    problems = "\n".join(
        f"  - {key}: moved to the database; write it with: {MOVED_KEY_COMMANDS[key]}"
        for key in moved
    )
    raise ConfigError(
        f"invalid config in {path}:\n{problems}\n"
        f"  Remove these sections from the file: the domain half of the "
        f"configuration lives in the database under server.database.dir. "
        f"See {DOMAIN_REFERENCE}."
    )


def _check_moved_environment() -> None:
    """The same refusal for a SAMTAL_ override of a moved section, and
    deliberately not through pydantic: the environment source looks up
    known fields and ignores every other prefixed variable, even under
    extra="forbid", so a stale SAMTAL_DEFAULT_AGENT would simply stop
    applying without a word. Only the six moved names are matched, so
    the variables that carry a value rather than name a section
    (SAMTAL_CONFIG, SAMTAL_MASTER_KEY, SAMTAL_AUTH_SECRET) are outside
    this by construction."""
    moved = [
        (name, key)
        for key in DOMAIN_KEYS
        for name in sorted(os.environ)
        if name == f"{ENV_PREFIX}{key.upper()}"
        or name.startswith(f"{ENV_PREFIX}{key.upper()}__")
    ]
    if not moved:
        return
    problems = "\n".join(
        f"  - {name}: {key} moved to the database, and this override no longer "
        f"applies; write it with: {MOVED_KEY_COMMANDS[key]}"
        for name, key in moved
    )
    raise ConfigError(
        f"invalid configuration environment:\n{problems}\n"
        f"  Unset these variables: the domain half of the configuration lives in "
        f"the database under server.database.dir. See {DOMAIN_REFERENCE}."
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
