"""The command line entry point: which of the five things to do.

Four command words that configure, report or diagnose, and everything
else is the server. This module holds the dispatch and the sentences a
mistake in front of it gets, and nothing about any of the five: each
group is imported inside the branch that reaches it.

That was already true of the four command words, for a reason of their
own (a document saying what the events are must not need a server that
starts). Since the dependency split it is true of serving as well, and
for the reason this file exists to make possible. The default
installation of this package is the configuration client: pydantic,
httpx, typer, PyYAML and dotenv, and none of FastAPI, uvicorn,
SQLAlchemy, cryptography, the LLM SDKs or the audio stack. Serving is
`vinga_server.serving`, which imports all of them and subclasses
`uvicorn.Server` at import time, so an installation without the server
half cannot import that module at all. It is reached in the serve branch
alone, and an installation that cannot reach it is answered with one
fixed sentence rather than an ImportError traceback naming a module
path.
"""

import argparse
import sys
from importlib import import_module
from types import ModuleType
from typing import NoReturn

from vinga_server import logs
from vinga_server.config.loader import (
    CONFIG_ENV_VAR,
    NEEDS_THE_SERVER_HALF,
    ConfigError,
    load_environment_file,
)

# The first words that mean "do this, do not serve".
CONFIG_COMMAND = "config"
CONVERSATIONS_COMMAND = "conversations"
EVENTS_COMMAND = "events"
DOCTOR_COMMAND = "doctor"

# All of them, in one place: the dispatch below checks them one at a
# time and the refusal for a word that is none of them names them, so
# the two cannot come to disagree about what this entry point takes.
COMMANDS = (CONFIG_COMMAND, CONVERSATIONS_COMMAND, EVENTS_COMMAND, DOCTOR_COMMAND)

# What a first word that is not one of them gets, and what a mistake in
# the server's own arguments gets. Both are fixed sentences that repeat
# nothing of what was typed, which used to be argparse's job and can no
# longer be: `doctor` takes a URL, an OTA URL can be the deployment's
# own secret, and `vinga-server docter https://host/<secret>/` would
# otherwise have printed it on stderr on its way to an exit code.
UNKNOWN_COMMAND = "that is not a command; expected one of: " + ", ".join(COMMANDS)

# The two modules this entry point can be asked for and may not have,
# named as strings because they are imported by name rather than
# written down as an import: a `from vinga_server import serving` at
# module scope is exactly what the split exists to prevent.
SERVING = "vinga_server.serving"
CONVERSATIONS_GROUP = "vinga_server.conversations.cli"

# What an installation carrying the client half alone is told when it is
# asked to serve. The conversations group answers
# `NEEDS_THE_SERVER_HALF` instead, which is the sentence the two gated
# commands of the configuration grammar answer with: serving is one
# fact and a command that needs the other half is another.
#
# A fixed constant, like every other sentence out of this entry point: it
# names no argument, no path and nothing of the ImportError that reached
# it, whose text is a module path and is the value most likely to be
# relayed by accident. The three doors are named instead, because the
# answer to this is always one of them.
CANNOT_SERVE = (
    "this installation carries the configuration client alone, so there is no server "
    "here to run. A deployment runs the published container image; a checkout gets the "
    "server half from uv sync, which installs the serve extra"
)

# The server's own grammar is one option, so there are two shapes to
# name and a vague fallback for anything else, the way the conversations
# group does it.
_USAGE_PROBLEMS: tuple[tuple[str, str], ...] = (
    ("expected one argument", "an option was given without its value"),
    ("unrecognized arguments", "unrecognized extra arguments"),
)

_USAGE_UNKNOWN = "the command line could not be parsed"

# What both refusals exit with. Two, which is what argparse has always
# answered a usage error with, so nothing scripted around this entry
# point learns a new number from a change about what is printed.
USAGE_EXIT_CODE = 2


class _Parser(argparse.ArgumentParser):
    """The server's own parser, whose usage errors say nothing of what
    was typed.

    argparse quotes the arguments it did not recognize, which was
    harmless while everything reaching this parser was the server's own
    `--config`. It stopped being harmless when a command word became
    something an operator can mistype in front of a URL: the dispatch
    above catches that shape, and this catches the rest, so no path out
    of this entry point echoes an argument. Written the way the
    conversations group writes it: a marker-matched table of fixed
    sentences and a deliberately vague fallback."""

    def error(self, message: str) -> NoReturn:
        print(_usage_problem(message), file=sys.stderr)
        raise SystemExit(USAGE_EXIT_CODE)


def _usage_problem(message: str) -> str:
    for marker, sentence in _USAGE_PROBLEMS:
        if marker in message:
            return f"{sentence}; run with --help for the grammar"
    return f"{_USAGE_UNKNOWN}; run with --help for the grammar"


def main() -> None:
    # Read a .env file into the environment before anything looks at it, so
    # it can carry VINGA_* overrides, VINGA_CONFIG, and provider secrets.
    # Real environment variables keep priority over .env values, and the
    # search starts from the invocation directory rather than this
    # file's.
    #
    # Behind the loader's boundary, which is the same one the config CLI
    # reads it through: a `.env` is where a deployment's credentials are
    # kept, and a file that will not open or will not decode would
    # otherwise leave this entry point as a traceback holding those
    # bytes. One sentence and exit 1, which is what every other
    # configuration failure here answers with.
    try:
        load_environment_file()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None

    # The floor under the libraries that narrate somebody else's bytes
    # (#124), before any command below opens a database or a socket.
    # `logs.configure` applies it again with the server's own level, but
    # it cannot run until the configuration has been read, and reading it
    # is itself a database open: a process started with SQL echoing would
    # otherwise print every statement of the boot, parameters and all,
    # before the floor arrived. Without a level, because the one this
    # server was told to use is in the configuration nothing has read
    # yet, and the call is idempotent.
    logs.quiet_vendor_libraries()

    if sys.argv[1:2] == [CONFIG_COMMAND]:
        # `vinga-server config ...` configures and exits; anything else
        # is the server, parsed exactly as it was before this existed. A
        # word check rather than an argparse subparser, so that adding
        # the command group cannot change how `vinga-server --config
        # path` parses. Imported here because it is a command group like
        # the three below rather than because it is heavy: the grammar is
        # the client half, and this installation always has it.
        #
        # It reads the .env file itself, since the console script `vinga`
        # never reaches this function and both spellings behave alike.
        from vinga_server.config import cli

        raise SystemExit(cli.main(sys.argv[2:]))

    if sys.argv[1:2] == [CONVERSATIONS_COMMAND]:
        # The second group, dispatched the same way and for the same
        # reasons. It says what the conversation store's tables are,
        # which must be answerable when the server will not start, so
        # it opens no database.
        #
        # It does read the store's SQLAlchemy metadata to say it, which
        # is the server half, so it goes through the gate: an
        # installation carrying the client alone is told which half is
        # missing rather than shown where the import failed.
        conversations_cli = _server_half(CONVERSATIONS_GROUP, NEEDS_THE_SERVER_HALF)

        raise SystemExit(conversations_cli.main(sys.argv[2:]))

    if sys.argv[1:2] == [EVENTS_COMMAND]:
        # The third group, and the one that reaches least: it prints
        # the event registry and opens nothing at all. Dispatched here,
        # above the boot below, for the reason the two groups above are:
        # a document that says what the events are must not need a
        # server that starts.
        from vinga_server import events_cli

        raise SystemExit(events_cli.main(sys.argv[2:]))

    if sys.argv[1:2] == [DOCTOR_COMMAND]:
        # The fourth, and the one that reaches furthest out: it asks a
        # device-facing address what it would tell a device. Dispatched
        # here for the reason the three above are, and imported lazily
        # for a reason of its own: it opens no database and must not
        # start needing one, which the import-weight test pins.
        from vinga_server import doctor

        raise SystemExit(doctor.main(sys.argv[2:]))

    if sys.argv[1:2] and not sys.argv[1].startswith("-"):
        # A first word that is not one of the four, answered here rather
        # than by the parser below. Falling through was safe while the
        # only thing past this point was the server's own --config;
        # since `doctor` takes a URL, an unrecognized word is followed
        # by whatever was being typed at a command that takes secrets,
        # and argparse would have echoed it. The known words are named,
        # the typed ones are not.
        print(UNKNOWN_COMMAND, file=sys.stderr)
        raise SystemExit(USAGE_EXIT_CODE)

    parser = _Parser(prog="vinga-server")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help=f"path to the YAML config file (default: ${CONFIG_ENV_VAR})",
    )
    args = parser.parse_args()

    raise SystemExit(_server_half(SERVING, CANNOT_SERVE).run(args.config))


def _server_half(module: str, sentence: str) -> ModuleType:
    """One dispatch target that needs the server half, or the fixed
    sentence and an exit of 1.

    Two branches reach it. Serving is the obvious one: `serving` imports
    FastAPI and uvicorn and subclasses `uvicorn.Server` at import time.
    The conversations group is the quiet one: it renders the store's
    tables off the SQLAlchemy metadata, so importing it pulls in
    SQLAlchemy, and without this it ended in a `ModuleNotFoundError`
    traceback on the one entry point whose every other answer is a
    sentence.

    The other three groups are NOT reached through here, deliberately.
    `config`, `events` and `doctor` are the client half, so an
    installation that has this file has them; if one of them will not
    import, that is a bug in this server and its traceback is the whole
    of what anybody has to work with.

    Recorded inside the handler and answered outside it, the way every
    sanitized boundary in this repository answers: an exception raised
    while an ImportError is being handled carries that ImportError as
    its `__context__`, and an ImportError's text is the module path it
    could not find. Nothing here reads it, and nothing walking a chain
    out of this function finds it either.

    Only ImportError is caught, for the same reason: a module that
    imports and then fails for a reason of its own is a bug, not a
    missing half.
    """
    found: ModuleType | None = None
    try:
        found = import_module(module)
    except ImportError:
        pass
    if found is None:
        print(sentence, file=sys.stderr)
        raise SystemExit(1)
    return found


if __name__ == "__main__":
    main()
