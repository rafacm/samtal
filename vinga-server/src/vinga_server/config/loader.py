"""Load the file half of the configuration, and compose the whole one.

The path comes from the explicit argument, then the VINGA_CONFIG environment
variable; with neither set, defaults apply. Values follow pydantic-settings
source priority: VINGA_-prefixed environment variables (nested keys joined
with __, for example VINGA_SERVER__PORT) override the YAML file, which
overrides the field defaults.

The file holds `server`. The domain half lives in the database, so a
domain section left in the file, or a VINGA_ override for one, refuses
the boot naming where it moved and the command that writes it: a key
that quietly stopped applying is the trap this closes. A section that
retired rather than moved (`memory:`, since remembered facts joined the
database too) refuses through the same door and answers the other
question: what it configured is unconditional now.
Composition then puts the two halves together into `Config`, which is
what validates the whole snapshot the way it always has.
"""

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml
from dotenv import find_dotenv, load_dotenv
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from vinga_server.config import entities
from vinga_server.config.models import (
    DOMAIN_KEYS,
    PROGRAM,
    SERVER_PROGRAM,
    Config,
    DatabaseConfig,
    FieldProblem,
    FileConfig,
    yaml_data_var,
)

CONFIG_ENV_VAR = "VINGA_CONFIG"

# What a command answers on an installation that carries the
# configuration client alone.
#
# The default install of this package is the client, and the server half
# is an extra, so three entry points can be typed by somebody who has
# only the first: `openapi` and `ota-url` in the configuration grammar,
# which read the API's routes and the onboarding package, and
# `vinga-server conversations`, which renders the store's tables off the
# SQLAlchemy metadata. All three answer this, because all three say one
# thing: the command needs the half that is not here. Three sentences
# for one fact would be the duplication the design guide names.
#
# Here rather than in `config/cli.py`, which is where it started, for the
# reason `PROGRAM` sits in `models.py`: two modules read it and only one
# is below both. `main.py` dispatches the conversations group and
# `config/cli.py` gates its two commands, and both already import this
# module for `ConfigError` and the config variable above, so the
# definition costs its readers nothing. `cli` re-exports it, so there is
# one string and not two.
#
# A fixed constant carrying no invocation value, like every sentence
# these entry points print. It is reached with an ImportError in hand,
# whose text is a module path, and the command that reached it may have
# been given a file path; neither is repeated. The two doors that do
# work are named instead, because the answer is always one of them.
NEEDS_THE_SERVER_HALF = (
    "this command needs the server half installed, and this installation carries the "
    "configuration client alone. Run it inside the container image, or from a "
    "checkout, where the whole server is present"
)

# And the other half of the same shape: a command behind an EXTRA rather
# than behind the server half.
#
# Two sentences rather than one, because they send a reader to two
# different places. The server half is a thing you go somewhere that has,
# and the sim extra is a thing you install: `vinga simulator run` speaks a
# websocket, which the configuration client has no library for, and the
# board's own check-in needs none of it.
NEEDS_THE_SIM_EXTRA = (
    "holding a conversation needs a websocket client, and this installation carries the "
    "configuration client alone. Install this package with its `sim` extra and run the "
    "command again; `simulator check-in` needs none of it and works as it is"
)

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
#
# In the server's own spelling, which is the one thing not read off the
# descriptor. These sentences refuse a BOOT: what reads them is an
# operator watching a container fail to start, and inside that container
# `vinga-server` is what a shell answers to while the short script is
# not installed at all. A descriptor's `command` is canonical because a
# generated document may not vary with the invocation; a refusal is the
# opposite case, because the invocation is exactly what its reader has.
# The verb and the noun are the descriptor's either way, so the two
# renderings cannot come to name different commands.


def served(command: str) -> str:
    """One command as a sentence composed by a server spells it.

    The canonical program word swapped for the invocation an image has,
    and nothing else touched: what follows is the grammar, and the
    grammar is one grammar.
    """
    return f"{SERVER_PROGRAM}{command.removeprefix(PROGRAM)}"


MOVED_KEY_COMMANDS: dict[str, str] = {
    descriptor.moved_key: served(descriptor.command) for descriptor in entities.ENTITIES
} | {setting.name: served(setting.command) for setting in entities.SETTINGS}

# Where the reference for the moved half is, quoted in the refusal
# because that document is what a reader needs next.
DOMAIN_REFERENCE = "docs/reference/domain-config.md"

# The sections that did not move anywhere: they are gone, and what they
# configured is now simply how the server behaves.
#
# `memory:` chose a directory for one file per agent. Remembered facts
# live in the database this server already keeps its other two stores
# in (#314), which needs no directory and no choice, so the section
# retired whole rather than gaining a key that means nothing.
#
# Refused rather than ignored, and this is the same trap
# `_check_moved_keys` closes one door of: `extra="forbid"` would answer
# a file that still carries it, but with a validation error that says a
# key is not permitted and never says what happened to it. A deployment
# meeting that would go looking for a typo.
RETIRED_SECTIONS: dict[str, str] = {
    "memory": (
        "retired; remembered facts live in the database the "
        f"{ENV_PREFIX}DB_* variables name, which this server migrates at every boot, "
        "and every agent can remember without being configured to. Existing files "
        "under the old directory are not read, not imported and not deleted by this "
        "release: archiving or removing them is yours to do"
    ),
}

# The database section's own environment spellings.
#
# One key, one variable, and the variable is the short one. The generic
# `VINGA_SERVER__DATABASE__HOST` would work by accident of the nesting
# scheme, and letting it would give every connection fact two names: the
# compose file feeds the Postgres image from the short spellings, which
# is the whole point of having them, and two spellings that must agree
# are one spelling with a bug pending.
#
# Outside the section scheme rather than inside it, the way
# `VINGA_MASTER_KEY` is: what these name is where the server's own state
# lives, which a deployment sets beside its credentials rather than in
# the file it edits.
DATABASE_SECTION = "database"

DATABASE_ENV_PREFIX = f"{ENV_PREFIX}DB_"

# The four the YAML also carries. The password and the whole-URL
# override have no YAML key at all and are read where the URL is built
# (`vinga_server.db`), so they are deliberately not here: this table is
# what maps a field of `DatabaseConfig` onto its variable, and neither
# of those is a field.
DATABASE_ENV_NAMES: dict[str, str] = {
    "host": f"{DATABASE_ENV_PREFIX}HOST",
    "port": f"{DATABASE_ENV_PREFIX}PORT",
    "name": f"{DATABASE_ENV_PREFIX}NAME",
    "user": f"{DATABASE_ENV_PREFIX}USER",
}


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
    """A lock this call needed did not arrive inside the lock timeout,
    because another connection was holding it. Nothing was changed, and
    the same call may be retried."""


class ReloadInProgressError(ConfigError):
    """A reload was asked for while one was already running. Nothing was
    changed by the second request, and it may be made again once the
    first has answered.

    Here rather than beside its raiser for the reason the busy error is:
    what raises it is the MCP registry, on the conversation side of the
    process, and what has to answer it with a status code is the
    configuration API, which deliberately loads none of that."""


class RunningConfigMovedError(ConfigError):
    """A read that has to describe one running world found that the
    world had moved underneath it. Nothing was changed, and the same
    call may be retried.

    A sibling of the refusal above rather than that refusal reused,
    because the two say different things. `ReloadInProgressError` is
    about a second reload asked for while the first is running; this one
    is about a read whose answer would otherwise mix two states that
    never coexisted, and it can be raised long after the reload that
    moved the world has finished. Retrying is the whole of the advice in
    both cases, which is why they answer under the same status.

    Here rather than beside its raiser for the reason the two above are:
    what raises it is the composition root, on the conversation side of
    the process, and what has to answer it with a status code is the
    configuration API.
    """


class SnapshotOnlyError(ConfigError):
    """This server was composed from a configuration handed to it rather
    than read from a store, so there is no store that describes the
    world it is serving.

    The state a test lane and an embedded caller run in: the snapshot is
    the whole truth there is, and it is authoritative for exactly that
    reason. What follows is that the two surfaces which span the two
    sides have nothing to span. A comparison would put the running world
    against a database that describes some other server, or none at all;
    an apply would install that database as this server's whole domain
    half. Both refuse, in one sentence saying so, rather than answering
    something that looks like an answer.

    A sibling of the two refusals above and under their status for the
    same reason theirs is: nothing was changed. Unlike theirs, making
    the request again will not help, and the sentence says so, since the
    only thing that changes this is starting a server from a store.

    Here rather than beside its raiser for the reason the others are:
    what knows the mode is the composition root, and what has to answer
    it with a status code is the configuration API.
    """


class ProviderRefusedError(ConfigError):
    """An apply could not build the engines the stored configuration
    names, so nothing was applied.

    The provider layer's own refusal, translated. `ProviderError` is
    that layer's contract and says which entry, which type and which
    option it choked on, all of which are stored values that a refusal
    over HTTP must not carry; and it is not a `ConfigError`, so nothing
    on the API side would know what status it meant. This is the
    configuration vocabulary's word for the same event: a stored world
    this server cannot run, refused with nothing changed, under the
    status every other unservable stored half answers with.

    Here rather than beside its raiser for the reason the refusals above
    are: what raises it is the apply, on the conversation side of the
    process, and what has to answer it with a status code is the
    configuration API, which deliberately loads no provider.
    """


class StorageError(ConfigError):
    """The stored state cannot be read as configuration, or the database
    could not be read or written at all. Not the caller's mistake."""


# The `.env` file, and what a `.env` that will not read says
#
# Both entry points read one before anything looks at the environment,
# so both meet this. It is the second file this process opens on a path
# nobody validated, and it is the one most likely to be a credential
# store: what a `.env` holds is exactly the variables the API token and
# the provider keys come from.
#
# So it is read behind the same kind of boundary a fragment is. The
# sentence names neither the path nor the library's own wording, for the
# reason `-f`'s does (#289): the path is typed, and a file that will not
# decode is as likely to be a key or an archive as a mistyped `.env`.
# What the failure holds is worse here than for a fragment, because a
# `UnicodeDecodeError` retains the bytes it could not decode and those
# bytes are somebody's variables.
DOTENV_UNREADABLE = (
    "a .env file was found and could not be read. Neither the path nor the system's "
    "own wording is quoted back, and nothing it holds is decoded far enough to be: a "
    ".env is where a deployment's credentials are kept, so a file that will not read "
    "is the last place a refusal may repeat anything. Check that it is UTF-8 text "
    "this user may read"
)

# What the read can fail as. `OSError` is the file; `UnicodeError` is
# the decoding, which is a `ValueError` rather than an `OSError` and so
# escapes an arm that catches only the first; `ValueError` is what the
# parser answers a line it cannot make sense of, and the discovery walk
# raises the same family for a path it cannot resolve.
_DOTENV_FAILURES = (OSError, UnicodeError, ValueError)


def load_environment_file() -> None:
    """Read a `.env` into the environment, or refuse in one sentence.

    Discovery and loading are both inside the boundary, because both
    touch the filesystem: `find_dotenv` walks upwards from the
    invocation directory and can meet a directory it may not read on the
    way. The real environment still wins over what the file says, which
    is python-dotenv's own default and the rule this deployment
    documents.

    The refusal is built inside the handler and raised after it, the way
    every boundary in this package raises: an exception raised while
    another is being handled keeps that one on `__context__` for
    anything walking the chain to find, and here that one holds the
    bytes of somebody's credentials.
    """
    problem: str | None = None
    try:
        load_dotenv(find_dotenv(usecwd=True))
        return
    except _DOTENV_FAILURES:
        problem = DOTENV_UNREADABLE
    raise ConfigError(problem)


def load_file_config(path: str | Path | None = None) -> FileConfig:
    """The file half of the configuration: `server`.

    The two doors a path arrives through are told apart here, because
    here is the only place that can: an argument is the `--config` flag
    every caller of this function feeds it, and the fallback is the
    environment variable. What comes of that is one phrase naming the
    door rather than the path, threaded through every refusal below
    (#291).
    """
    if path is None:
        env_path = os.environ.get(CONFIG_ENV_VAR)
        path = Path(env_path) if env_path else None
        source = CONFIG_FROM_ENV if path is not None else NO_CONFIG_FILE
    else:
        path = Path(path)
        source = CONFIG_FROM_FLAG

    _check_moved_environment()
    _check_database_environment()
    # The file is read once, here, and what the settings machinery is
    # given is what this read parsed. Handing it the path instead meant
    # a second open behind this boundary, which answered a file that had
    # changed underneath by booting the defaults or by raising the
    # parser's own exception past every sentence above (#291).
    data = _check_config_file(path, source) if path is not None else {}

    token = yaml_data_var.set(data)
    problem: str | None = None
    try:
        return _with_database_environment(FileConfig())
    except ValidationError as exc:
        # Rendered from the error locations and messages only, never
        # from str(exc), which quotes the rejected input back
        # (`input_value=...`); and recorded rather than raised here,
        # because an exception raised inside a handler carries the one
        # being handled as its __context__ and would take that quote
        # with it. A key that names an environment variable is where
        # that matters: what lands in it wrongly is the secret itself.
        problem = _format_validation_error(exc, source)
    except SettingsError as exc:
        # The same care, for the same reason. This is what a malformed
        # VINGA_ override of a structured key raises, and the parser
        # failure underneath it holds the whole rejected environment
        # value (a JSONDecodeError keeps it in `.doc`). The settings
        # error's own text names the field and the source rather than
        # the value, so the message is unchanged; what goes is the
        # chain that carried the value behind it.
        problem = f"invalid config in {source}: {exc}"
    finally:
        yaml_data_var.reset(token)
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
        return Config(server=file_half.server, **domain)
    except ValidationError as exc:
        problem = _format_validation_error(exc, source)
    raise ConfigError(problem)


# Reading the config file
#
# The first file this process opens on a path nobody validated, and the
# door a deployment's whole server half comes through. Everything below
# is held to the rule the `-f` write is held to (#289, #291): what a
# refusal carries is a fixed sentence chosen by the class of the
# failure, plus at most the two integers saying where the parser
# stopped, and every one of them is built inside its handler and raised
# after it, so nothing walking `__cause__` or `__context__` finds the
# library exception that holds the path or the buffer.

# What a refusal calls the file: the mechanism that named the path,
# never the path itself.
#
# A config path is typed, on a command line or into a deployment's
# environment, which makes it the same thing `-f`'s path is and the last
# place a refusal may repeat. What its reader needs is not the string
# they just wrote but which of the two doors it came through, because
# that is the one they go and change. `load_file_config` is the only
# place that knows, since it is where the argument and the variable are
# read, so the phrase is chosen there and threaded down from it.
CONFIG_FROM_FLAG = "the config file --config names"

CONFIG_FROM_ENV = f"the config file {CONFIG_ENV_VAR} names"

# And what a boot with no file at all calls the thing a problem is in:
# there is no file to name a door of, and what was validated came from
# the defaults and the environment.
NO_CONFIG_FILE = "the configuration"

# What a config file that will not read says. One fixed sentence per
# failure, `{source}` filled in with one of the three phrases above, and
# none of them holds the path, the operating system's wording or a byte
# of the file.
#
# The library's own `strerror` is not passed through, for the reason the
# `-f` sentences do not pass it through either: a message this code did
# not write is a message it cannot promise carries no value, and these
# are written from the path they were handed.
CONFIG_NOT_FOUND = (
    "{source} is not there. The path is not quoted back: a refusal here names the "
    "rule rather than what was typed"
)

CONFIG_NOT_READABLE = (
    "{source} cannot be read: check that it is a file this user may read, rather than "
    "a directory or one belonging to somebody else. Neither the path nor the system's "
    "own wording is quoted back"
)

CONFIG_NOT_TEXT = (
    "{source} is not UTF-8 text, so there is no YAML in it to read. Nothing it holds "
    "is quoted back, and nothing of it is decoded far enough to be: a file that fails "
    "to decode is as likely to be a key or an archive as a config file named by mistake"
)

CONFIG_UNREADABLE = (
    "{source} could not be read. Neither the path nor the system's own wording is "
    "quoted back"
)

# Ordered, first match wins, and a subclass comes before the class it
# extends. The decoding family is in the table because
# `UnicodeDecodeError` is a `ValueError` rather than an `OSError`: the
# read succeeds and the decoding is what fails, which is why a config
# file of bytes used to leave this loader as a traceback, and the
# exception it leaves as holds the buffer it could not decode.
_FILE_PROBLEMS: tuple[tuple[type[BaseException], str], ...] = (
    (FileNotFoundError, CONFIG_NOT_FOUND),
    (NotADirectoryError, CONFIG_NOT_FOUND),
    (IsADirectoryError, CONFIG_NOT_READABLE),
    (PermissionError, CONFIG_NOT_READABLE),
    (UnicodeError, CONFIG_NOT_TEXT),
    (OSError, CONFIG_UNREADABLE),
)

# What the arm catches, read off the table rather than written beside
# it: a shape the table answers and the arm does not catch is a
# traceback.
_FILE_FAILURES = tuple(shape for shape, _ in _FILE_PROBLEMS)

# What a source that will not parse says about what it is not saying.
#
# Here rather than in `config/cli.py`, where it was written, for the
# reason `NEEDS_THE_SERVER_HALF` sits here: two modules say it and only
# this one is below both. It is one statement about one parser, true of
# a fragment typed at a command line and of the file a server boots on,
# and two copies of it would be two sentences free to drift apart.
YAML_NOT_QUOTED = (
    "Nothing of what it holds is quoted back: a source that will not parse is one "
    "nothing here has validated, and what a parser says about one repeats the tag or "
    "the key it stopped on"
)

# What reading YAML can fail as, which is wider than `YAMLError`.
#
# The constructors that turn a scalar into a Python value raise the
# ordinary exceptions when the scalar is out of range: an integer of
# five thousand digits leaves as CPython's own `ValueError` about the
# digit limit, an impossible date leaves as `ValueError` from
# `datetime`, and two thousand nested lists leave as `RecursionError`
# out of the composer. None of them is a `YAMLError`, so an arm that
# catches the documented exception alone lets all three past as a
# traceback carrying the source.
#
# Shared with `config/cli.py` for the reason the locator below is: this
# is one statement about what one parser does, and a boot file is read
# by the same `yaml.safe_load` a fragment is. It sat in `cli` alone
# while the boot path caught `YAMLError`, which is exactly the drift
# one home prevents.
UNPARSEABLE = (yaml.YAMLError, ValueError, ArithmeticError, RecursionError)


def stopped_at(exc: BaseException) -> str:
    """Where the parser stopped, when it says: two integers off the
    exception's mark and nothing else off the exception at all. Empty
    for the failures that carry no mark.

    Shared with `config/cli.py`, which imports it: the locator is the
    one thing a refusal about YAML may take from the parser, and a
    second implementation of the rule is the same rule with a bug
    pending.
    """
    if not isinstance(exc, yaml.MarkedYAMLError) or exc.problem_mark is None:
        return ""
    mark = exc.problem_mark
    return f" at line {mark.line + 1}, column {mark.column + 1}"


def _config_text(path: Path, source: str) -> str:
    """The config file's text, or the fixed sentence for a file that
    will not give any.

    The sentence is chosen by the class of the failure, which is the
    reading that cannot be fooled by wording, and is raised after the
    arm rather than inside it: the exception being handled holds the
    path and, for a file that will not decode, the bytes it was
    decoding.
    """
    problem: str | None = None
    try:
        return path.read_text(encoding="utf-8")
    except _FILE_FAILURES as exc:
        problem = next(
            sentence for shape, sentence in _FILE_PROBLEMS if isinstance(exc, shape)
        ).format(source=source)
    raise ConfigError(problem)


def _check_config_file(path: Path, source: str) -> dict[str, object]:
    """The file half's YAML, read once, parsed once and checked, or the
    fixed sentence for a file that cannot give one.

    What it returns is what the settings machinery is then built from,
    which is the point of doing the work here: the pydantic-settings
    YAML source skips a missing file in silence, does not name an
    encoding, and answers a file it cannot parse with the parser's own
    exception, path and offending line included. Nothing behind this
    opens the file again, so none of that can happen to a file that
    changes between two reads.
    """
    text = _config_text(path, source)

    problem: str | None = None
    data: object = None
    try:
        data = yaml.safe_load(text)
    except UNPARSEABLE as exc:
        # The locator and nothing else off the exception, and raised
        # after the handler: a parser exception retains the buffer it
        # was parsing, so leaving it as the context would attach the
        # whole file to a refusal about one line of it, and its own
        # `problem` names the tag or the key it stopped on, which for a
        # file holding credentials is the one thing it may not repeat.
        # The whole family rather than `YAMLError`, so a scalar the
        # constructors refuse is this sentence rather than a traceback;
        # those carry no mark, so the locator is empty for them.
        problem = f"invalid YAML in {source}{stopped_at(exc)}. {YAML_NOT_QUOTED}"
    if problem is not None:
        raise ConfigError(problem)

    if data is not None and not isinstance(data, dict):
        # The type name is the shape the file has, not a value out of
        # it, so it stays where the path goes.
        raise ConfigError(
            f"invalid config in {source}: top level must be a mapping of "
            f"server, got {type(data).__name__}"
        )

    if not isinstance(data, dict):
        # A file holding nothing at all, which is a legitimate config
        # file: `safe_load` answers None for it, and what the settings
        # sources want is a mapping saying nothing.
        return {}

    _check_moved_keys(source, data)
    return data


def _check_moved_keys(source: str, data: dict) -> None:
    """A domain section left in the file, refused where the parsed top
    level is already in hand. Ignoring it silently would leave a
    deployment editing a section the server no longer reads."""
    _check_retired_keys(source, data)
    moved = [key for key in DOMAIN_KEYS if key in data]
    if not moved:
        return
    problems = "\n".join(
        f"  - {key}: moved to the database; write it with: {MOVED_KEY_COMMANDS[key]}"
        for key in moved
    )
    raise ConfigError(
        f"invalid config in {source}:\n{problems}\n"
        f"  Remove these sections from the file: the domain half of the "
        f"configuration lives in the database the {DATABASE_ENV_PREFIX}* variables "
        f"name. See {DOMAIN_REFERENCE}."
    )


def _check_retired_keys(source: str, data: dict) -> None:
    """A section that is gone rather than moved, refused with the
    sentence that says so.

    Its own arm and not a second entry in the table above, because the
    two refusals answer different questions. A moved section is written
    somewhere else and the refusal names the command that writes it; a
    retired one is not written anywhere at all, and what its reader
    needs is that the behavior it configured is now unconditional.

    Value-free, like every refusal in this module: the section's name is
    this module's own constant and what the operator wrote under it is
    never quoted back. A directory is not a credential, but a value
    pasted into the wrong key is exactly the case a rule with an
    exception in it does not cover.
    """
    retired = [key for key in RETIRED_SECTIONS if key in data]
    if not retired:
        return
    problems = "\n".join(f"  - {key}: {RETIRED_SECTIONS[key]}" for key in retired)
    raise ConfigError(
        f"invalid config in {source}:\n{problems}\n"
        f"  Remove these sections from the file: they configure nothing any more."
    )


def _with_database_environment(file_half: FileConfig) -> FileConfig:
    """The file half with `VINGA_DB_*` applied over its database section.

    Applied here rather than through pydantic-settings, for the reason
    the moved-key check below is written by hand: the nesting scheme
    derives a variable name from a field path, and these four names are
    deliberately not derived from theirs. What the scheme would have
    produced is refused instead, by `_check_database_environment`.

    A model copy rather than a second `FileConfig()`, which would re-read
    every source and could pick up a file that changed underneath.
    """
    section = file_half.server.database
    overrides = {
        field: os.environ[name]
        for field, name in DATABASE_ENV_NAMES.items()
        if os.environ.get(name)
    }
    if not overrides:
        return file_half
    problem: str | None = None
    try:
        applied = section.model_copy(
            update={
                field: int(value) if field == "port" else value
                for field, value in overrides.items()
            }
        )
        # Copies do not validate, so the section is put back through its
        # own model. A port of 0 or 99999 is a refusal here rather than
        # a connection failure with a fixed sentence later.
        applied = DatabaseConfig.model_validate(applied.model_dump())
    except (ValueError, ValidationError):
        # The value is not repeated, because one of these variables is
        # read beside a password and a refusal that echoes its input is
        # a refusal one typo away from echoing the wrong one.
        problem = (
            f"invalid database environment: {DATABASE_ENV_NAMES['port']} has to be a "
            f"port number between 1 and 65535, and the other {DATABASE_ENV_PREFIX}* "
            f"variables plain strings. What was set is not quoted back"
        )
    if problem is not None:
        raise ConfigError(problem)
    server = file_half.server.model_copy(update={"database": applied})
    return file_half.model_copy(update={"server": server})


def _check_database_environment() -> None:
    """The generic spelling of a database key, refused in favour of the
    short one.

    `VINGA_SERVER__DATABASE__HOST` would otherwise work, silently, as a
    second name for what `VINGA_DB_HOST` names, and the two would come to
    disagree the moment somebody set both. It is refused rather than
    honored because the short names are the ones the compose file feeds
    the database image from, so they are the ones a `.env` holds.

    Matched without regard to case, for the reason the check below is:
    pydantic-settings reads the whole variable name case-insensitively,
    so a case-sensitive scan would leave exactly the spellings that do
    apply unrefused.
    """
    prefix = f"{ENV_PREFIX}SERVER__{DATABASE_SECTION.upper()}__"
    found = [name for name in sorted(os.environ) if name.upper().startswith(prefix)]
    if not found:
        return
    problems = "\n".join(
        f"  - {name}: use "
        + DATABASE_ENV_NAMES.get(
            name.upper().removeprefix(prefix).lower(), f"{DATABASE_ENV_PREFIX}*"
        )
        + " instead"
        for name in found
    )
    raise ConfigError(
        f"invalid configuration environment:\n{problems}\n"
        f"  Unset these variables: the database connection is named by the "
        f"{DATABASE_ENV_PREFIX}* variables, which the compose file and a deployment's "
        f"own environment both use, so a second spelling of the same fact is not "
        f"honored. {DATABASE_ENV_PREFIX}PASSWORD and {DATABASE_ENV_PREFIX}URL carry "
        f"the credentials and have no configuration key at all."
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
    _check_retired_environment()
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
        f"the database the {DATABASE_ENV_PREFIX}* variables name. "
        f"See {DOMAIN_REFERENCE}."
    )


def _check_retired_environment() -> None:
    """The same refusal for a VINGA_ override of a retired section, and
    for the same reason the moved one exists.

    `VINGA_MEMORY__DIR` is a spelling that applied yesterday: the
    environment source reads known fields and ignores every other
    prefixed variable, `extra="forbid"` notwithstanding, so deleting the
    field alone would leave that variable naming nothing, silently, on a
    deployment that set it deliberately.

    Matched without regard to case, because that is how the source this
    replaces reads them, so `VINGA_memory__dir` is refused with the
    spelling it was written in. The variable's NAME is reported and its
    value never is: what a reader has to find and unset is the name, and
    what lands in an environment variable wrongly is the one thing a
    refusal must not repeat.
    """
    retired = [
        (name, key)
        for key in RETIRED_SECTIONS
        for name in sorted(os.environ)
        if name.upper() == f"{ENV_PREFIX}{key.upper()}"
        or name.upper().startswith(f"{ENV_PREFIX}{key.upper()}__")
    ]
    if not retired:
        return
    problems = "\n".join(
        f"  - {name}: {key} is {RETIRED_SECTIONS[key]}" for name, key in retired
    )
    raise ConfigError(
        f"invalid configuration environment:\n{problems}\n"
        f"  Unset these variables: they configure nothing any more."
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
