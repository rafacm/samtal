"""The `samtal-server conversations` command group: purge, and schema.

Two commands, and neither needs a running server. `purge` is the
administrative escape hatch and works the way `config --local` works,
directly against the file named by the composed `server.database.dir`,
because deletion must work exactly when the server is broken or gone.
`schema` prints the generated reference and opens nothing at all.

Every failure leaves as a `ConfigError` printed to stderr with exit code
1, naming the location and the kind of failure without quoting the value
that caused it, and no traceback from SQLAlchemy or pydantic reaches the
user. A missing database is reported plainly and never created: an
operator asking to delete from a store that is not there has made a
mistake about the path, and answering by bringing an empty store into
existence would hide it.
"""

import argparse
import datetime as dt
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from samtal_server.config.loader import CONFIG_ENV_VAR, ConfigError, load_file_config
from samtal_server.conversations import docgen
from samtal_server.conversations.store import conversations_path, purge

# What the operator has to be told, because it is a consequence they
# cannot see in the counts: the two instruments are separate, and one of
# them may still be running.
#
# Wrapped here rather than by argparse, and narrower than a terminal:
# the raw formatter prints these verbatim, and a line that wraps on its
# own is worse than one that was wrapped on purpose.
PURGE_DESCRIPTION = """\
Delete whole sessions, with their turns, tool invocations and events.
At least one selector is required; selectors given together combine
with AND."""

# The command words, in one place: the parser builds them and the
# refusal for a word that is not one of them names them, so the two
# cannot come to disagree.
COMMANDS = ("purge", "schema")

# What a purge says when a reader kept it from truncating the log. The
# deletion is committed either way, so this is a report and not a
# failure, and it names what makes the frames go.
DEFERRED_TRUNCATION = (
    "the rows are deleted, but a reader was holding the write-ahead log open, "
    "so its frames were not truncated yet; the next purge, or the running "
    "server's next write, truncates them"
)

PURGE_NOTES = """\
Capture files are never touched: purging removes rows from
conversations.db and leaves the session's WAV, JSONL and manifest where
they are. The session id is the correlation key for whoever needs to
remove the matching triplet.

Purging a session that is still running ends its recording. The writer
finds the row gone at its next turn and stops writing for that session,
so what the conversation says after the purge is not recorded."""


def main(argv: Sequence[str] | None = None) -> int:
    """Run one conversations command. Returns the process exit code.

    Parsing is inside the boundary, so a mistake in the grammar answers
    the way a mistake in a selector does: a sentence on stderr and exit
    1. --help still leaves through argparse's own exit 0, because asking
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
        prog="samtal-server conversations",
        description=(
            "Read and delete from the conversation store. Both commands work "
            "without a running server: purge goes straight to the file, and schema "
            "opens nothing."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    purging = commands.add_parser(
        COMMANDS[0],
        help="delete sessions from the store",
        description=PURGE_DESCRIPTION,
        epilog=PURGE_NOTES,
        # Both are laid out already; the default formatter would reflow
        # each into one paragraph and lose the blank line between the
        # two notes.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    purging.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help=(
            f"path to the YAML config file naming server.database.dir "
            f"(default: ${CONFIG_ENV_VAR})"
        ),
    )
    purging.add_argument("--session", metavar="ID", help="one session by its id")
    purging.add_argument("--device", metavar="MAC", help="every session of one device")
    purging.add_argument(
        "--before", metavar="YYYY-MM-DD", help="every session that started before that day"
    )
    purging.set_defaults(run=_purge)

    schema = commands.add_parser(
        COMMANDS[1],
        help="print the schema reference",
        description=(
            "Print the generated schema reference to stdout. The committed copy is "
            "docs/reference/conversations-schema.md, and CI diffs the two."
        ),
    )
    schema.set_defaults(run=_schema)

    return parser


def _purge(args: argparse.Namespace) -> None:
    before = _day(args.before)
    if args.session is None and args.device is None and before is None:
        raise ConfigError(
            "a purge needs at least one of --session, --device or --before; "
            "deleting the whole store is not something this command does by "
            "omission"
        )
    directory = _database_dir(args)
    path = conversations_path(directory)
    if not path.is_file():
        raise ConfigError(
            f"there is no conversation store at {path}; server.database.dir names "
            f"the directory it would live in, and nothing is created by asking"
        )
    taken = purge(directory, session=args.session, device=args.device, before=before)
    for name, count in taken.counts().items():
        print(f"{name}: {count}")
    if not taken.truncated:
        # Said rather than left to be discovered. The rows are gone; the
        # frames that held their bytes are still in the write-ahead log
        # because a reader was holding it open, and the next checkpoint
        # that gets its moment is what removes them.
        print(DEFERRED_TRUNCATION)


def _schema(_args: argparse.Namespace) -> None:
    print(docgen.reference(), end="")


def _day(given: str | None) -> dt.date | None:
    """A calendar day, or a refusal that names the format rather than
    repeating what was typed."""
    if given is None:
        return None
    try:
        return dt.date.fromisoformat(given)
    except ValueError:
        raise ConfigError("--before takes a calendar day written as YYYY-MM-DD") from None


def _database_dir(args: argparse.Namespace) -> Path:
    """Where the server keeps its databases, read through the settings
    machinery the server reads it with, so the two cannot disagree. No
    configuration file has to exist: without one the field default and
    the SAMTAL_ environment are the whole answer."""
    return load_file_config(args.config).server.database.dir


__all__ = ["main"]
