"""Load the file half of the configuration, and compose the whole one.

The path comes from the explicit argument, then the VINGA_CONFIG environment
variable; with neither set, defaults apply. Values follow pydantic-settings
source priority: VINGA_-prefixed environment variables (nested keys joined
with __, for example VINGA_SERVER__PORT) override the YAML file, which
overrides the field defaults.

The file holds `server` and `memory`. The domain half lives in the
database, so a domain section left in the file, or a VINGA_ override for
one, refuses the boot naming where it moved and the command that writes
it: a key that quietly stopped applying is the trap this closes.
Composition then puts the two halves together into `Config`, which is
what validates the whole snapshot the way it always has.
"""

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from vinga_server.config import entities
from vinga_server.config.models import (
    DOMAIN_KEYS,
    Config,
    FieldProblem,
    FileConfig,
    yaml_file_var,
)

CONFIG_ENV_VAR = "VINGA_CONFIG"

# The prefix a configuration override carries. Only the domain names
# below are scanned for: everything else under this prefix is either a
# key the file half still owns or a variable that carries a value rather
# than naming a section (VINGA_CONFIG, VINGA_MASTER_KEY,
# VINGA_AUTH_SECRET), and none of those moved anywhere.
ENV_PREFIX = "VINGA_"

# What writes each moved section now, so the refusal answers the question
# it raises. Read off the descriptors rather than written out again: the
# command an operator is sent to here and the command the generated
# reference prints for the same kind were byte-identical strings held
# together by nothing, which is the duplication the descriptor's
# `command` exists to end. One entry per key in DOMAIN_KEYS, which is
# what a kind's `moved_key` and a setting's `name` are.
MOVED_KEY_COMMANDS: dict[str, str] = {
    descriptor.moved_key: descriptor.command for descriptor in entities.ENTITIES
} | {setting.name: setting.command for setting in entities.SETTINGS}

# Where the reference for the moved half is, quoted in the refusal
# because that document is what a reader needs next.
DOMAIN_REFERENCE = "docs/reference/domain-config.md"


class ConfigError(Exception):
    """A configuration problem, with a message meant to be shown as is.

    `problems` is that same refusal decomposed, where it decomposes:
    one `FieldProblem` per thing wrong, each addressing a field of the
    submitted fragment by JSON Pointer and carrying the sentence the
    message already says about it. It exists so the API can answer a
    form something to highlight without a second validation pass and
    without parsing its own prose; the CLI keeps printing the message
    and never looks at it.

    Optional and empty by default, so every raise site that has nothing
    structured to say keeps reading as it did, and so do the subclasses,
    which inherit this constructor untouched. An empty tuple is the
    honest answer for the refusals that name no field of the request:
    an unreadable stored row, a reference to another entity, a body
    whose whole shape was wrong.
    """

    def __init__(self, message: object, problems: Sequence[FieldProblem] = ()) -> None:
        super().__init__(message)
        self.problems: tuple[FieldProblem, ...] = tuple(problems)


# The refusals that are not simply "this configuration is wrong". They
# live here, beside ConfigError and above anything that imports a
# database driver, because both raisers need them: the repository, and
# `open_database`, which the API is on the path of for every request.
# All of them subclass ConfigError, so the CLI and the boot path keep
# catching one exception and printing one sentence, and the messages are
# unchanged; what the types add is a caller that has to answer with a
# status code rather than a sentence.


class UnknownEntityError(ConfigError):
    """The named entity, or the stored secret in that slot, does not
    exist. The request was well formed and addressed nothing."""


class DeviceAlreadyBoundError(ConfigError):
    """The device a conditional bind addressed is already configured, so
    the write was not made.

    Its own type because its caller has to tell it from every other
    refusal: an activation code names a device that was unbound when the
    code was issued, and a code outlives the state it was issued in.
    Binding anyway would replace a newer decision with an older one."""


class DatabaseBusyError(ConfigError):
    """Another process held the write lock for longer than the busy
    timeout allows. Nothing was changed, and the same call may be
    retried."""


class ReloadInProgressError(ConfigError):
    """A reload was asked for while one was already running. Nothing was
    changed by the second request, and it may be made again once the
    first has answered.

    Here rather than beside its raiser for the reason the busy error is:
    what raises it is the MCP registry, on the conversation side of the
    process, and what has to answer it with a status code is the
    configuration API, which deliberately loads none of that."""


class StorageError(ConfigError):
    """The stored state cannot be read as configuration, or the database
    could not be read or written at all. Not the caller's mistake."""


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
    problem: str | None = None
    try:
        return FileConfig()
    except ValidationError as exc:
        # Rendered from the error locations and messages only, never
        # from str(exc), which quotes the rejected input back
        # (`input_value=...`); and recorded rather than raised here,
        # because an exception raised inside a handler carries the one
        # being handled as its __context__ and would take that quote
        # with it. A key that names an environment variable is where
        # that matters: what lands in it wrongly is the secret itself.
        problem = _format_validation_error(exc, _source(path))
    except SettingsError as exc:
        # The same care, for the same reason. This is what a malformed
        # VINGA_ override of a structured key raises, and the parser
        # failure underneath it holds the whole rejected environment
        # value (a JSONDecodeError keeps it in `.doc`). The settings
        # error's own text names the field and the source rather than
        # the value, so the message is unchanged; what goes is the
        # chain that carried the value behind it.
        problem = f"invalid config in {_source(path)}: {exc}"
    finally:
        yaml_file_var.reset(token)
    raise ConfigError(problem)


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
    problem: str | None = None
    try:
        return Config(server=file_half.server, memory=file_half.memory, **domain)
    except ValidationError as exc:
        problem = _format_validation_error(exc, source)
    raise ConfigError(problem)


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

    problem: str | None = None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # Rendered from the problem and the mark rather than from
        # str(exc), which quotes the offending source line back, and
        # raised after the handler: a parser exception retains the
        # buffer it was parsing, so leaving it as the context would
        # attach the whole file to a refusal about one line of it.
        detail = str(exc)
        if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
            mark = exc.problem_mark
            detail = f"{exc.problem} at line {mark.line + 1}, column {mark.column + 1}"
        problem = f"invalid YAML in {path}: {detail}"
    if problem is not None:
        raise ConfigError(problem)

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
    """The same refusal for a VINGA_ override of a moved section, and
    deliberately not through pydantic: the environment source looks up
    known fields and ignores every other prefixed variable, even under
    extra="forbid", so a stale VINGA_DEFAULT_AGENT would simply stop
    applying without a word. Only the six moved names are matched, so
    the variables that carry a value rather than name a section
    (VINGA_CONFIG, VINGA_MASTER_KEY, VINGA_AUTH_SECRET) are outside
    this by construction.

    Matched without regard to case, because that is how the source this
    replaces reads them: pydantic-settings is case-insensitive by
    default, over the whole variable name and not only the part after
    the prefix, so `VINGA_server__port` sets the port and
    `VINGA_aGeNtS__x` would have set an agent. A case-sensitive scan
    would leave exactly those spellings applying to nothing, silently,
    which is the hole this whole check exists to close. The variable is
    reported in the spelling it was written in, since that is what has
    to be found and unset."""
    moved = [
        (name, key)
        for key in DOMAIN_KEYS
        for name in sorted(os.environ)
        if name.upper() == f"{ENV_PREFIX}{key.upper()}"
        or name.upper().startswith(f"{ENV_PREFIX}{key.upper()}__")
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
