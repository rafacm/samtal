"""The `samtal-server events` command group: reference.

One command, and it reaches nothing. No database, no configuration file,
no key, and no server: the registry is a Python module, and printing it
is reading that module. That is why the entrypoint dispatches this group
before it resolves `SAMTAL_EVENTS_ENFORCEMENT` or parses a server's
arguments, the way it dispatches `config` and `conversations`: a
server-only variable somebody misspelled must not stand between a reader
and the document that says what the events are.

Usage errors leave as a sentence of this module's own, for the reason
the conversations group gives: several of argparse's quote what was
typed back at the user, and a value on stderr is a value in whatever
collects stderr. A reader who stops reading leaves the same way: this
document is long enough to outrun a pipe buffer, so `| head` is an
ordinary thing to do with it and has to be an ordinary thing to answer.
"""

import argparse
import os
import signal
import sys
from collections.abc import Sequence
from typing import NoReturn

from samtal_server import events_docgen

# The command words, in one place: the parser builds them and the
# refusal for a word that is not one of them names them, so the two
# cannot come to disagree.
COMMANDS = ("reference",)

# What a process that was cut off reports, by the convention a shell
# already understands: the signal that would have killed it, offset by
# 128. `head -n 1` is not an error to report; it is a reader who has
# read enough.
BROKEN_PIPE_STATUS = 128 + signal.SIGPIPE


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
        return _reader_stopped_reading()
    return 0


def _reader_stopped_reading() -> int:
    """A consumer closed the pipe, which is not a failure to report.

    Two things have to happen for it to stay unreported. The status is
    the shell's own for a process cut off by SIGPIPE, so a pipeline reads
    the way a pipeline does. And the file descriptor behind `sys.stdout`
    is replaced with the null device before returning, because the
    interpreter flushes its streams on the way out and a flush to a pipe
    nobody is reading raises a second time, after this function is out of
    the way: Python would print `Exception ignored on flushing
    sys.stdout` to stderr, which is the traceback this exists to prevent
    wearing different words.
    """
    try:
        empty = os.open(os.devnull, os.O_WRONLY)
        os.dup2(empty, sys.stdout.fileno())
    except OSError:
        # Whatever stdout has become cannot be redirected. There is
        # nothing further to do about it and nothing to say about it
        # either, since saying it is what this avoids.
        pass
    return BROKEN_PIPE_STATUS


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
        prog="samtal-server events",
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
