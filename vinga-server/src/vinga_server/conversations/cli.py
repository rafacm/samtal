"""The `vinga-server conversations` command group: schema.

One command, and it needs no running server because it opens nothing at
all: `schema` prints the generated reference off the table declarations.
Nothing in this group reaches the conversation store's file, which is
the property that keeps the group whole once the store is a database
somewhere else (#281).

Every failure leaves as a `ConfigError` printed to stderr with exit code
1, naming the kind of failure without quoting the value that caused it,
and no traceback from argparse reaches the user.
"""

import argparse
import sys
from collections.abc import Sequence
from typing import NoReturn

from vinga_server.config.loader import ConfigError
from vinga_server.conversations import docgen

# The command words, in one place: the parser builds them and the
# refusal for a word that is not one of them names them, so the two
# cannot come to disagree.
COMMANDS = ("schema",)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one conversations command. Returns the process exit code.

    Parsing is inside the boundary, so a mistake in the grammar answers
    the way every other failure does: a sentence on stderr and exit 1.
    --help still leaves through argparse's own exit 0, because asking
    for help is not a failure."""
    try:
        args = _parser().parse_args(argv)
        args.run(args)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


class _Parser(argparse.ArgumentParser):
    """A parser whose usage errors leave through the same door as every
    other failure, for the reason the config group's parser gives:
    argparse would otherwise write to stderr and exit 2 from inside
    parse_args, bypassing the ConfigError boundary.

    And whose sentences are this module's, never argparse's. Several of
    argparse's quote what was typed back at the user (`invalid choice:
    'x'`, `unrecognized arguments: x`), so passing its text through was
    a value on stderr and in whatever collects it. Each shape this
    grammar can produce gets a fixed sentence instead, and a shape that
    is not recognized gets the general one, because a message this code
    has not seen is a message that may carry a value."""

    def error(self, message: str) -> NoReturn:
        raise ConfigError(_usage_problem(message))


# The grammar's own words for what argparse says, matched on a marker
# that carries no value. Ordered, and the first match wins.
_USAGE_PROBLEMS: tuple[tuple[str, str], ...] = (
    ("invalid choice", "that is not a command; expected one of: " + ", ".join(COMMANDS)),
    ("unrecognized arguments", "unrecognized extra arguments"),
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
        prog="vinga-server conversations",
        description=(
            "Read what the conversation store's tables are. The command works "
            "without a running server because it opens nothing: the reference is "
            "rendered from the declarations, not from a file."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    schema = commands.add_parser(
        COMMANDS[0],
        help="print the schema reference",
        description=(
            "Print the generated schema reference to stdout. The committed copy is "
            "docs/reference/conversations-schema.md, and CI diffs the two."
        ),
    )
    schema.set_defaults(run=_schema)

    return parser


def _schema(_args: argparse.Namespace) -> None:
    print(docgen.reference(), end="")


__all__ = ["main"]
