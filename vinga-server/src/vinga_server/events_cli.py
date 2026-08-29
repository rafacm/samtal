"""The `vinga-server events` command group: reference.

One command, and it reaches nothing. No database, no configuration file,
no key, and no server: the catalog is a Python module, and printing it
is reading that module. That is why the entrypoint dispatches this group
before it parses a server's arguments, the way it dispatches `config`
and `conversations`: a document that says what the events are must not
need a server that starts.

Usage errors leave as a sentence of this module's own, for the reason
the conversations group gives: several of argparse's quote what was
typed back at the user, and a value on stderr is a value in whatever
collects stderr. A reader who stops reading leaves the same way: this
document is long enough to outrun a pipe buffer, so `| head` is an
ordinary thing to do with it and has to be an ordinary thing to answer.
"""

import argparse
import sys
from collections.abc import Sequence
from typing import NoReturn

from vinga_server import events_docgen
from vinga_server.broken_pipe import reader_stopped_reading

# The command words, in one place: the parser builds them and the
# refusal for a word that is not one of them names them, so the two
# cannot come to disagree.
COMMANDS = ("reference",)


class EventsCommandError(Exception):
    """A command line this group could not act on. Its own type rather
    than the configuration's, because nothing here reads configuration:
    borrowing `ConfigError` would have imported the settings machinery
    into a command that opens nothing."""


def main(argv: Sequence[str] | None = None) -> int:
    """Run one events command. Returns the process exit code."""
    try:
        args = _parser().parse_args(argv)
        args.run(args)
    except EventsCommandError as exc:
        print(exc, file=sys.stderr)
        return 1
    except BrokenPipeError:
        return reader_stopped_reading()
    return 0


class _Parser(argparse.ArgumentParser):
    """A parser whose usage errors leave through the same door as every
    other failure, and whose sentences are this module's rather than
    argparse's."""

    def error(self, message: str) -> NoReturn:
        raise EventsCommandError(_usage_problem(message))


# The grammar's own words for what argparse says, matched on a marker
# that carries no value. Ordered, and the first match wins.
_USAGE_PROBLEMS: tuple[tuple[str, str], ...] = (
    ("invalid choice", "that is not a command; expected one of: " + ", ".join(COMMANDS)),
    ("unrecognized arguments", "unrecognized extra arguments"),
    ("expected one argument", "an option was given without its value"),
    ("required", "a command is missing"),
)

# What an unrecognized shape gets. Deliberately vague about the mistake
# rather than specific with argparse's words in it.
_USAGE_UNKNOWN = "the command line could not be parsed"


def _usage_problem(message: str) -> str:
    for marker, sentence in _USAGE_PROBLEMS:
        if marker in message:
            return f"{sentence}; run with --help for the grammar"
    return f"{_USAGE_UNKNOWN}; run with --help for the grammar"


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="vinga-server events",
        description=(
            "Read the declared event surface. The command needs no running "
            "server and opens nothing."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    reference = commands.add_parser(
        COMMANDS[0],
        help="print the event schema reference",
        description=(
            "Print the generated event schema reference to stdout. The committed "
            "copy is docs/reference/events.md, and CI diffs the two."
        ),
    )
    reference.set_defaults(run=_reference)

    return parser


def _reference(_args: argparse.Namespace) -> None:
    print(events_docgen.reference(), end="")


__all__ = ["EventsCommandError", "main"]
