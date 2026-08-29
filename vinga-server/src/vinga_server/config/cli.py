"""The `vinga-server config` command group: a client of the API.

The grammar is the one it has always had, one noun per entity kind, YAML
fragments as the write payload; what changed underneath is that a
command is now a request to the configuration API rather than a write
into the database. Nothing here decides anything about the
configuration: parsing, validation, reference checks, existence and
secret handling all live in the repository, which the API mounts, so a
refusal reads the same whichever way it was reached. The API carries the
repository's sentence in `detail` and this prints `detail`, unchanged.

Nothing plaintext is ever an argument: a secret arrives on stdin (not
echoed when the terminal is interactive) or from a named environment
variable, because arguments land in shell history and in the process
list. It then crosses the connection in a request body, which is why the
transport policy below is a refusal rather than a recommendation: the
bearer token rides on every request and grants everything the API can
do, so a plain http:// connection to anything but a loopback address is
not made at all.

There is no second way in. Every command that touches the domain
configuration is a request, so this module opens no database, loads no
encryption key and knows nothing about how a row is stored. A
deployment whose server will not start is recovered by booting one on
an empty database and applying a kept `export`, which is the procedure
`docs/reference/cli.md` writes out; surgical access to the rows
themselves is ordinary SQL and not this grammar's business.

One command stands outside all of this, because onboarding a board
happens before there is anything to configure. `ota-url` derives the
string a person types into a captive portal from the file half and the
environment, and contacts nothing whatsoever. It is one of the two
commands here that need the server half installed, `openapi` being the
other; both answer one fixed sentence when it is not. What answers on that URL
is a question for `vinga-server doctor`, which since #244 is a command
of its own: diagnosing an endpoint is not a configuration concern, and
what the two share is where the URL comes from, which is
`onboarding.origin`.

Every failure leaves as a ConfigError printed to stderr with exit code
1, naming the location and the kind of failure without quoting the value
that caused it, and no traceback from pydantic, PyYAML, SQLAlchemy,
cryptography or httpx reaches the user.
"""

import contextlib
import getpass
import ipaddress
import json
import logging
import os
import re
import shlex
import sys
import textwrap
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from importlib import metadata
from pathlib import Path
from typing import Annotated, Any, get_args, get_origin
from urllib.parse import quote, urlencode, urlunsplit

import httpx
import typer
import yaml
from pydantic import BaseModel, TypeAdapter, ValidationError

# Typer ships its own copy of Click rather than importing the installed
# one, so a usage error arrives as a class of that copy: `click.UsageError`
# would catch none of them, and a boundary that caught nothing would let
# Click's own sentences out as a traceback. The same goes for the context
# a help page is rendered through, which has to be that copy's. Imported
# from where they actually are, named one by one rather than felt for
# through an ancestor, so a Typer release that moves them fails loudly at
# import instead of quietly widening what reaches an operator. That
# tripwire fired once: typer 0.27.2 moved Exit out of the vendored
# exceptions module, so Exit is imported from the core module beside
# Context, where both 0.27.1 and 0.27.2 define it as the same class
# typer exports publicly as typer.Exit.
from typer._click.core import Context, Exit
from typer._click.exceptions import (
    BadArgumentUsage,
    BadOptionUsage,
    BadParameter,
    ClickException,
    MissingParameter,
    NoArgsIsHelpError,
    NoSuchOption,
)
from typer.core import TyperCommand, TyperGroup

from vinga_server import device_endpoint
from vinga_server.broken_pipe import reader_stopped_reading
from vinga_server.config import docgen, entities
from vinga_server.config.loader import (
    CONFIG_ENV_VAR,
    # Defined one module down and re-exported here, because `main.py`
    # answers the same sentence for the conversations group and only
    # `loader` is below both readers. `cli.NEEDS_THE_SERVER_HALF` stays
    # the name every test and both wheel lanes reach for.
    NEEDS_THE_SERVER_HALF,
    NEEDS_THE_SIM_EXTRA,
    ConfigError,
    load_environment_file,
    load_file_config,
)
from vinga_server.config.models import (
    API_MOUNT_PATH,
    MASK,
    PROVIDER_STAGES,
    DomainConfig,
    FileConfig,
    ServerConfig,
)
from vinga_server.config.printing import parsed_url, printable, shown_url
from vinga_server.config.responses import (
    PROBLEM_MEDIA_TYPE,
    PROBLEM_TITLES,
    Acknowledgement,
    AppliedDocument,
    AssembledPrompt,
    ConfigDiff,
    ConfigDocument,
    ConfigReloadResult,
    ConversationDetail,
    ConversationList,
    ConversationTurns,
    DefaultAgentName,
    DeviceBinding,
    Envelope,
    Erasure,
    McpServerStatus,
    PendingDevice,
    Problem,
    RuntimeInfo,
    SecretValue,
    SessionDetail,
    SessionList,
    ThreadErasure,
)
from vinga_server.config.transport import APPLY_LOCATION, check_transportable
from vinga_server.logs import quieted

# The simulator's two modules, imported by name rather than through the
# package's `__init__`, which carries a docstring and no re-exports.
# Both are client-tier pure: `board` reaches `device_endpoint`, the
# models and `protocol`, and `capabilities` reaches `protocol.messages`.
# The conversation half is deliberately NOT imported here, because it is
# the only module in this package that imports `websockets` and the
# extra's gate depends on that import happening inside `run`'s own arm.
# `utterance` is here because it is stdlib and `protocol` and reads a
# file the wheel carries, so nothing about it is behind the extra.
from vinga_server.simulator import board, capabilities, utterance

# Where the API is, when nothing says otherwise: the loopback address of
# this machine, on the port the server half of the configuration names,
# under the prefix the sub-application is mounted at. The port is read
# through the same machinery the server reads it with, so the two cannot
# disagree about it any more than they can about the database directory,
# and the prefix comes from the same constant the server mounts on.
API_URL_ENV = "VINGA_API_URL"

# How a command of this grammar is spelled in anything this repository
# generates: the committed CLI reference and its recipes, the export
# header and the secret commands at its foot, the reference intro, and
# the `command` strings the descriptors carry into the domain reference.
#
# One constant and not the invocation, and that is load-bearing rather
# than tidy. `docgen._quoted` picks the commands it publishes as recipes
# by matching each example file's comment lines against this prefix, so
# a name that varied with the entry point would render an empty recipes
# region through one of them and turn the drift check red on an
# unrelated change. A generated document may no more vary with the
# invocation than with the terminal.
PROGRAM = entities.PROGRAM

# And the two entry points this grammar has, as the fixed string each of
# them prints in a live help page or usage line. A closed map from a
# known entry point to a written-down name: `argv[0]` is never read and
# never interpolated, so a hostile one has no surface here at all.
#
# `main` is reached one of two ways. The console script calls it with no
# arguments, which is the short spelling; `vinga-server config` hands it
# `sys.argv[2:]`, which is the spelling inside the image. Anything that
# calls it some third way gets the canonical constant.
CONSOLE_SCRIPT = "vinga"

DISPATCHED = "vinga-server config"

# What `--version` answers with. The distribution's name rather than
# either invocation's, and the version read off the installed
# distribution rather than written here, so the two halves of a
# deployment can be compared without either of them being asked to
# remember a number. It is the same string whichever way this was
# reached, because a version is a fact about the artifact.
DISTRIBUTION = "vinga-server"

# And what it says when there is no installed distribution to ask, which
# is a tree on `sys.path` with nothing installed from it. A fixed word
# rather than a guess: a version this code invented would be worse than
# no version at all, since the whole point of the read is comparing two
# halves.
VERSION_UNKNOWN = "unknown (nothing is installed under this name)"

# The client's timeouts, explicit because the defaults would lie. The
# server holds a write for up to the database's busy timeout (10 s)
# before answering the retryable 409, and httpx's 5 second default would
# turn exactly that answer into a client-side transport error, replacing
# "nothing was changed; run the command again" with a sentence that says
# nothing about what happened. So: a bounded connect, and a read with
# margin above the busy timeout.
CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 30.0

# What `reload` waits instead, because it is the one request whose
# server-side work is not a database call. The server's envelope is one
# MCP connect timeout plus one prompt-discovery deadline plus small
# change: stops run concurrently under a short bound, starts run
# concurrently under the connect timeout, and an entry that names
# published prompts spends one further bounded phase fetching them, so
# a slow server is reported down rather than waited for. This is
# comfortably above that, because a client that gave up on a reload the
# server then applied would recreate the exact ambiguity the whole
# feature exists to remove: nobody would know what is running.
RELOAD_READ_TIMEOUT_S = 60.0

# And what `apply` waits, which is the sentence above taken to its
# conclusion where no finite envelope exists.
#
# An apply is one transaction, and the transaction loads the whole
# existing configuration and validates the whole resulting one, whose
# size nothing about the request bounds: the document may be small and
# the store it lands in large. So there is no number to derive. What a
# finite bound would buy is the exact thing every timeout here exists to
# prevent, a client that gave up on a write the server then committed,
# leaving nobody able to say what is stored.
#
# So the client waits for the answer, however long the transaction
# takes. The connect timeout stays bounded, because a server that is not
# there must still say so quickly, and the two bounds the server applies
# before it mutates anything (the document's entry count and its body
# size) are where an unbounded request is refused. What is left is
# transport death mid-wait, which is the exposure every write already
# has, and the recovery is the same: read the store back with `export`
# or `show`.
APPLY_READ_TIMEOUT_S: float | None = None

# And what `events tail` waits, which is the same conclusion reached
# from the opposite direction.
#
# A read timeout bounds how long an answer may take to arrive. The event
# stream's answer never finishes arriving: it is the server saying what
# it is doing, and a deployment that is doing nothing at four in the
# morning is a stream with nothing on it, which is the reading an
# operator opened it for. Any finite number here would be a clock that
# ended a healthy tail and reported it as the server going away, which
# is the one thing this command's end-of-stream sentence must be able to
# mean.
#
# The keepalive is what makes that safe rather than merely intended: the
# stream writes a comment line on its own idle interval, so a connection
# that has genuinely died is a read that fails rather than a read that
# waits forever. The connect timeout stays bounded for the reason it
# always is, that a server which is not there must say so quickly.
STREAM_READ_TIMEOUT_S: float | None = None

# Said when the API answered something this client cannot read as an
# answer. The body is deliberately not quoted: what a proxy, a gateway
# or a captive portal returns is not this API's sanitized output, and
# relaying it as though it were is how a middlebox's page ends up looking
# like a configuration error.
UNRECOGNIZED_ANSWER = "a body this client does not recognize"

# The three things a body can fail to be, said in the words each act has
# always said them in. Which one an act meets is a fact of the act, so it
# is written on its row rather than at the raise site.
UNREADABLE_READ = f"the configuration API answered a read with {UNRECOGNIZED_ANSWER}"

UNREADABLE_RELOAD = f"the configuration API answered the reload with {UNRECOGNIZED_ANSWER}"

# What the reload listing prints for a kind this server cannot apply
# while it runs. The sections are declared complete from the first
# release that has any of them, so that a client generated from the
# contract never meets a grown answer, and a kind whose milestone has
# not landed answers null rather than an empty answer that would claim
# it had been considered.
NOT_APPLIED = "(this server does not apply this kind without a restart)"

# A write is the one whose refusal has to say what is now unknown: the
# request may well have been applied, and this client cannot tell.
UNREADABLE_WRITE = (
    f"the configuration API acknowledged the write with {UNRECOGNIZED_ANSWER}; "
    f"read the configuration back to see whether it was applied."
)

# What `apply` adds when the apply was answered and the reload behind it
# was not. Everything it says is something this client knows: the
# transaction ran and its outcomes are printed above, and no completed
# reload answer arrived.
#
# It opens on the store rather than on the write, and that is a
# correction rather than a style. "The document was written" is false of
# two successful applies in three: a document every entry of which was
# already what the store held wrote nothing, and an empty document names
# nothing to write. What is true of all three is what an apply promises,
# which is that the store says what the document says, and that promise
# is exactly what the outcome per entry above spells out.
#
# What it deliberately does not say is what the server is now serving,
# because that is not knowable from here. A 409 says another reload is
# already running, and that one re-read the store either before this
# commit or after it, which decides whether the document is live and is
# not in the answer. A transport failure or a timeout is ambiguous in a
# second way: the request is carried out in tasks that outlive the
# connection, so a reload whose client went away can still finish.
#
# So the sentence sends the operator to the read that does know
# (`diff` compares the stored half against the running one) and to the
# command that settles it either way.
APPLY_UNANSWERED = (
    "The apply was answered and the store is what the document says, entry by entry "
    "above. What did not arrive is a completed answer to the reload behind it, so "
    f"what the server is serving now is not said here: run `{PROGRAM} diff`, which "
    f"compares the stored configuration against the running one, and `{PROGRAM} "
    "reload` if they differ."
)

# And what the event stream says when it stops, which is the same
# sentence whether the body ended cleanly or the connection under it
# died: to whoever is watching, both are the tail going quiet, and a
# client that told them apart would be reporting on a distinction it
# cannot actually make from this side.
#
# It is a failure, and it exits 1 in both modes, because the alternative
# is worse than an error: a tail that ended on a server restart and said
# nothing would be a quiet terminal that looks exactly like a quiet
# deployment. Nothing reconnects on its own for the same reason. A tail
# that rejoined across a gap would go on looking continuous while having
# missed whatever happened in it, and there is no buffer behind the
# stream for it to catch up from.
STREAM_ENDED = (
    "the event stream ended: the server closed it, or something between here and it "
    "did. Nothing has been reconnected, because a tail that rejoined across a gap "
    "would look continuous while missing what happened in it; run the command again "
    "to watch from now on."
)

# And what a frame this client cannot read as an event says. Nothing of
# the frame is in it, for the reason no other unreadable answer is
# quoted back: what a proxy or a gateway writes into a stream is not
# this API's own output.
UNREADABLE_EVENT = (
    f"the event stream carried {UNRECOGNIZED_ANSWER}, so the tail stopped rather than "
    f"printing it. It is not quoted back: what reaches a stream from a middlebox is "
    f"not the API's sanitized output."
)

# How a stored secret is introduced in `show` and `list`. Comment lines
# rather than a mapping: the mask is not a value that could be written
# back, and saying so in the document is more honest than rendering it
# as though it could.
SECRETS_HEADING = f"# stored secrets, set with: {PROGRAM} <kind> secret set"

# The pending listing's columns. Headings a person reads rather than
# field names: what the body has to carry to be read as a listing at all
# is `PendingDevice`, one import below this one.
PENDING_COLUMNS = ("code", "device", "board", "firmware", "expires")

NOTHING_CONFIGURED = (
    f"this server has no MCP servers configured. An entry is written with "
    f"`{PROGRAM} mcp-server set`, and an agent reaches it by naming it in "
    f"its mcp list"
)

NOTHING_APPLIED = (
    "the document names no section of the configuration, so nothing was applied. An applied\n"
    "document's top-level keys are the sections of the domain configuration"
)

NOTHING_PENDING = (
    "no device is waiting to be claimed. A board shows its code within a couple of "
    "minutes of being pointed at this server, and codes are forgotten when the server "
    "restarts, so a board that has been waiting a while shows a fresh one"
)

# The session listing's columns. Upper case, because these are field
# names an operator matches against the API and the store rather than
# words about a board, and because a session id is a uuid hex whose
# column would otherwise be hard to find in a wall of them.
SESSION_COLUMNS = ("SESSION", "DEVICE", "AGENT", "STARTED", "CLOSED", "REASON", "TURNS")

# What a listing shows where the row has nothing. One character, fixed,
# and never derived from the answer: a null device, agent or close is an
# ordinary state of a session, and an empty cell would read as a column
# that failed to render.
NOTHING_THERE = "-"

NO_SESSIONS = (
    "this server has recorded no sessions matching that. Recording is off unless "
    "server.conversations.enabled says otherwise, and a session older than "
    "server.conversations.retention_days has been pruned"
)

# The thread listing's columns, upper case for the reason the session
# listing's are. `LAST-ACTIVE` rather than `LAST_ACTIVE`, because these
# are headings a person reads across a line and this one is two words.
CONVERSATION_COLUMNS = ("CONVERSATION", "AGENT", "TITLE", "LAST-ACTIVE", "TURNS")

NO_CONVERSATIONS = (
    "this server has recorded no conversations matching that. Recording is off unless "
    "server.conversations.enabled says otherwise, and a thread whose last activity is "
    "older than server.conversations.retention_days has been pruned"
)

# What `conversation show` prints where a thread answers no turns.
# Narrow and real rather than defensive: a thread is created by its
# first turn and deleted when it loses its last, so the way to see this
# is for an erasure to land between the two reads this one command
# makes.
#
# A thread recorded under text-off is NOT this case. It has its turns
# and none of the words in them, so its dialogue prints with the fixed
# placeholder on both speakers, which is what says the turn happened and
# nothing of it was stored.
NO_DIALOGUE = (
    "this conversation holds no turns. A thread is created by its first turn and "
    "deleted when it loses its last, so an empty answer here means the store moved "
    "between this command's two reads"
)

# Who said what, in front of a dialogue line. The user's label is fixed
# and this client's own; the agent's is the turn's own agent, bounded
# like every other value an answer carries.
SPEAKER = "you"

# How much of any one value reaches a cell or a block line. Narrower
# than the glimpse the URLs are bounded to, because these land in a
# table: what a column is for is comparing one row against the next, and
# a cell as wide as a title makes a table with one row in it. A session
# id is 32 characters and a stamp is 32, so nothing this server minted
# is truncated by it.
CELL_LENGTH = 64

# What a deletion reports, in the order the rows go. Written out here so
# that the block below prints what the API answers rather than whatever
# a dictionary happened to iterate as, and so that a count added to the
# contract is a line added here rather than a line that quietly appears.
#
# One order for both erasures rather than a second tuple beside it.
# Erasing a thread answers four of these and not the two about sessions,
# because it touches neither the sessions its turns were spoken in nor
# their telemetry, so the block prints the counts its answer carries in
# this order and says nothing about the ones it does not.
ERASED_COUNTS = (
    "sessions",
    "turns",
    "tool_invocations",
    "events",
    "conversations",
    "milestones",
)

# What to do with the URL `ota-url` prints, said beside it on stderr so
# that stdout holds the URL and nothing else.
OTA_URL_GUIDANCE = (
    "Type this into the device's captive portal, under its advanced settings, as the "
    "server address. If the board then shows a six-digit activation code, it has no "
    "agent yet: bind "
    f"it with {PROGRAM} device pending claim <code> <agent>. A deployment with "
    "default_agent set covers every board already, so its boards show no code and start "
    "talking as soon as they connect."
)

# What this command does about onboarding being off. The sentence it
# goes into is `origin.ONBOARDING_OFF`, which is the derivation's own,
# and the fix is the asking command's.
ONBOARDING_OFF_FOR_URL = "Turn onboarding on for a URL short enough to type."


# What `info` prints, and what it is careful about
#
# The banner is the maintainer's own string, character for character. A
# plain hyphen and not a dash of any other width: the no-em-dash rule is
# about em-dashes, and none of these characters is one.
BANNER = "vinga - Conversational AI. Sweded."

# The label in front of the address this CLI actually contacted, which
# is the first question `info` exists to answer. It is a different
# question from the onboarding URL below it, and legitimately a
# different answer: a device reaches this deployment on the origin it
# publishes, and an operator reaches the API wherever they exec'd into.
# What is printed is `Address.shown`, never what was typed.
CONTACTED = "configuration API"

# The label in front of the onboarding URL, carrying the provenance, so
# that the URL itself lands on a line of its own with nothing in front
# of it. Deliberate: a terminal wraps a long line wherever it runs out,
# and this is a value an operator types into a captive portal by hand or
# selects whole. The provenance travels with it for the reason the
# banner and `ota-url` carry it: two of the three sources it can come
# from are inferences.
ONBOARDING_URL_LABEL = "the URL to type into a device's captive portal"

# And what stands there instead when the answer says onboarding is off.
# The path devices are configured at is named and never printed: it is
# `server.ota_path`, which is this deployment's secret, exactly as the
# derivation's own refusal has it.
ONBOARDING_OFF_HERE = (
    "device onboarding is off (server.onboarding.enabled is false), so this deployment "
    "serves no short URL. Devices are configured at the path server.ota_path names, "
    "which is not printed here, since it is this deployment's secret."
)

# The heading over the count per kind. A count and not the tree: what
# `info` answers is orientation, and `vinga list` is the tree.
CONFIGURED = "configured:"

# The two values an identity answer carries that are printed whole
#
# `GLIMPSE_LENGTH` is the bound for far-side text quoted inside a
# sentence, and neither of these is that. The URL is the thing this
# command exists to hand a person, and a truncated URL is not a shorter
# answer, it is a wrong one: it is typed into a captive portal by hand
# and fails there, silently, with nothing on the terminal saying it was
# cut. The provenance is the sentence that says whether to trust the
# origin in it, and it ends with the fix, so a cut at any length loses
# exactly the half worth reading.
#
# No number would have done. `server.public_url` accepts an origin with
# a path prefix and bounds neither, so a legal configuration can compose
# an onboarding URL of any length; a bound here refuses nothing and
# corrupts quietly, which is the one failure this project's refusal
# posture exists to avoid. So they are printed whole, terminal-safe,
# which is the same call `_block` makes for a prompt and for the same
# reason. What a hostile far side could do with that it could already do
# through `agent preview`, and it would need this deployment's API token
# to try.
UNBOUNDED = None


def main(argv: Sequence[str] | None = None) -> int:
    """Run one config command. Returns the process exit code.

    Parsing is inside the boundary, so a mistake in the grammar answers
    the way a mistake in a fragment does: a sentence on stderr and exit
    1. --help still leaves through an exit 0 of its own, because asking
    for help is not a failure.

    An absent `argv` is the console script, which is the whole of what
    tells the two entry points apart here: `vinga-server` hands this
    `sys.argv[2:]` and the script hands it nothing. What that decides is
    one string in a help page, and nothing else.

    The `.env` file is read for this group rather than only in
    `vinga-server.main`, because both spellings have to behave
    identically and the console script never reaches that function. It
    is read where a command is about to run (`_Verbatim.invoke`) rather
    than in front of the parse, so that an invocation which runs no
    command needs no readable environment: a bare `vinga` gets its help
    page whatever the `.env` in the working directory is, which is the
    one moment a reader is least able to act on a sentence about a file
    they may not have written. Every command still runs with the
    environment loaded, because nothing here reads it earlier.

    It is read INSIDE the boundary, which is the whole of why the read
    is a function of the loader's rather than two library calls: a
    `.env` that will not open or will not decode is a failure on a path
    nobody validated, holding the variables an API token and the
    provider credentials come from, and outside the boundary it would
    leave as a traceback with those bytes on the exception. The read has
    moved down the call stack and not out of the boundary: it is still
    inside this `try`, one frame further in.

    And one invocation is answered in front of the parse as well as the
    read, because it has to be answerable when nothing else is: see
    `_version_asked`.
    """
    if _version_asked(sys.argv[1:] if argv is None else argv):
        _print_version()
        raise SystemExit(0)
    try:
        _parsed(
            sys.argv[1:] if argv is None else argv,
            CONSOLE_SCRIPT if argv is None else DISPATCHED,
        )
    except BrokenPipeError:
        # A reader that stopped reading, which is not a failure and is
        # not this grammar's sentence either: `broken_pipe.py` says what
        # the status is and why stdout has to be redirected before this
        # returns. Here for `events tail | head -n 1`, which is how a
        # script waits for one event, and it is caught for every command
        # because `export | head` is the same shape and had the same
        # traceback waiting in it.
        return reader_stopped_reading()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def _parsed(argv: Sequence[str], spelled: str) -> None:
    """The command line, parsed and run.

    Click is driven directly rather than through its standalone mode,
    which prints a usage error itself and exits 2: this group's contract
    is one sentence on stderr and exit 1, and a failure that bypassed it
    would bypass the sanitizing with it. `--help` is the one invocation
    that is not a failure, and it leaves through the exit code Click
    asked for, which is 0.

    Both answers are recorded inside their handler and raised after it,
    the way every other boundary in this module raises. A Click
    exception holds the context it was raised from and that context
    holds the argument list, so an exception raised while one is being
    handled would carry the whole command line as its `__context__` for
    anything walking the chain to find, which for this CLI is where a
    secret typed as an argument would be.

    That applies to `--help` as much as to a refusal, which is why the
    exit code is carried out of the arm rather than raised in it.
    `raise ... from None` sets `__suppress_context__`, which stops a
    traceback being printed and stops nothing else: the Typer exception
    is still on `__context__`, and this module's whole no-leak
    discipline is about what a chain walker finds rather than about what
    is displayed.

    One invocation is answered with a page rather than a sentence, and
    it leaves through the same door as every other: an invocation that
    named no command at all, told apart BY CLASS. `NoArgsIsHelpError` is
    raised by `_Grouped` below and by nothing else in this grammar, and
    it carries the group that was left without a verb, so its page is
    the page the reader stopped at rather than the root's.

    By class rather than by wording, and that distinction is the whole
    of this arm. Every other shape here is a sentence of Click's about
    something that was typed, so a reading that matched on words would
    be a reading a caller could satisfy: `vinga "Missing command"` is an
    unknown command whose name is the marker, and it gets the refusal
    every other unknown command gets.
    """
    problem: str | None = None
    asked_for: int | None = None
    try:
        grammar = command()
        with grammar.make_context(spelled, list(argv)) as context:
            grammar.invoke(context)
        return
    except Exit as asked:
        asked_for = asked.exit_code
    except NoArgsIsHelpError as bare:
        problem = bare.ctx.get_help()
    except ClickException as exc:
        problem = _usage_problem(exc)
    if asked_for is not None:
        raise SystemExit(asked_for)
    raise ConfigError(problem)


# What the context holds after a group has parsed its own options: the
# word that would name a command, and the words after it. Click reads
# exactly these two to decide whether a command was named at all, and
# `_Grouped` reads them for the same decision.
#
# Named rather than felt for, like the exception classes at the top of
# this module and for the same reason: the leading underscore is Typer's
# copy of Click's private spelling, and a release that renames it must
# fail loudly here (an `AttributeError` on the first invocation, which
# every test of this grammar makes) rather than quietly answer False and
# turn every invocation into a bare one.
_LEFT_TO_RESOLVE = ("_protected_args", "args")


class _Grouped(TyperGroup):
    """Every group of this grammar: the root, and one per noun path.

    What it adds is the answer to an invocation that reached a group and
    named no command under it. `vinga`, `vinga provider` and `vinga
    device pending` are each a page of the grammar with nothing chosen
    off it, and what the reader needs there is the list of what they
    could have chosen, without typing a second command to see it. So the
    group raises `NoArgsIsHelpError`, which is Click's own class for
    exactly that meaning, and the boundary prints its context's page the
    way it prints every other answer to an invocation that was not a
    completed command: on stderr, exiting 1.

    Raised here rather than left to Click for two reasons, and the first
    is the load-bearing one. Click states this mistake as a sentence on
    the base `UsageError` ("Missing command."), which is the same class
    and the same shape as an unknown command and an argument too many,
    both of which quote what was typed; a boundary that told them apart
    by wording could be handed the wording. Raising it here makes the
    class the answer. And Click's own `no_args_is_help` sees only the
    case where nothing at all followed, while `vinga --api-url URL` also
    named no command and is also owed the page.

    The library's flag stays off, so this is the one place the decision
    is made.
    """

    def invoke(self, ctx: Context) -> Any:
        if not any(getattr(ctx, held) for held in _LEFT_TO_RESOLVE):
            raise NoArgsIsHelpError(ctx)
        return super().invoke(ctx)


# What a mistake in the grammar says
#
# Click's own sentences quote what was typed: an unknown option comes
# with a did-you-mean built from it, a bad value is repeated back, an
# unknown command names the word. A secret is never an argument of this
# CLI, and the mistake that would make one (typing the value after
# `provider secret set ... api_key`) lands in exactly those sentences, so none of
# them is passed through. Each shape gets a fixed sentence of this
# grammar's own, and a shape not recognized gets the vague one, because
# a message this code has not seen is a message that may carry a value.
#
# Two tables, because Click states its usage errors two ways. The
# subclasses are chosen BY CLASS, which is the reading that cannot be
# fooled by wording; the base `UsageError` is one class for three
# different mistakes, so those are told apart by Click's own fixed
# words, which are the part of the sentence carrying no value.

# The one this grammar raises for itself as well as translating, so it
# is a name rather than a cell: a `set` given neither a fragment nor a
# key=value pair is missing a required argument, and Click cannot see
# that because either of the two satisfies it.
MISSING_ARGUMENT = "a required argument is missing"

# Ordered, first match wins, and a subclass comes before the class it
# extends: `MissingParameter` is a `BadParameter`, and an argument that
# is absent is not an argument that is wrong.
_USAGE_PROBLEMS: tuple[tuple[type[BaseException], str], ...] = (
    (NoSuchOption, "that is not an option of this command"),
    (MissingParameter, MISSING_ARGUMENT),
    (BadOptionUsage, "an option was given without its value"),
    (BadArgumentUsage, "an argument was given in a shape this command does not take"),
    (BadParameter, "an argument was given a value this command does not take"),
)

# The mistake whose sentence has to say more than what went wrong,
# because the value it would have echoed is the one thing this CLI is
# built never to see: typing the secret after the slot is where an
# operator meets this, and where Click would have quoted it back.
SECRET_NEVER_AN_ARGUMENT = (
    "unrecognized extra arguments. A secret is never given as an argument: a secret "
    "set reads it from stdin, or from the variable named with --from-env"
)

_USAGE_SHAPES: tuple[tuple[str, str], ...] = (
    ("Got unexpected extra argument", SECRET_NEVER_AN_ARGUMENT),
    ("No such command", "that is not a command"),
    # The third has no route through this grammar any more: `_Grouped`
    # decides that case before Click can state it, and answers it with a
    # page. The sentence stays because the marker is still Click's
    # wording for a real mistake, and a Typer release that reached it by
    # some path this module has not seen should meet the sentence for it
    # rather than the vague fallback. It is unreachable, not wrong, and
    # the boundary suite drives it directly.
    ("Missing command", "a command is missing"),
)

# What an unrecognized shape gets. Deliberately vague about the mistake
# rather than specific with Click's words in it.
_USAGE_UNKNOWN = "the command line could not be parsed"


def _usage_problem(exc: ClickException) -> str:
    """One usage mistake, in this grammar's words.

    Never in Click's: the message is read only to tell three shapes of
    one class apart, on markers that are Click's fixed words, and what
    it goes on to quote is exactly what the fixed sentences replace.
    """
    for shape, sentence in _USAGE_PROBLEMS:
        if isinstance(exc, shape):
            return usage_line(sentence)
    stated = exc.format_message()
    for marker, sentence in _USAGE_SHAPES:
        if marker in stated:
            return usage_line(sentence)
    return usage_line(_USAGE_UNKNOWN)


def usage_line(sentence: str) -> str:
    """One usage sentence as it is printed, with the tail every one of
    them carries.

    Named because the boundary is not the only raiser: a mistake in the
    grammar that Click cannot see, because either of two arguments
    satisfies it, is still a mistake in the grammar and reads as one.
    """
    return f"{sentence}; run with --help for the grammar"


# What one command was given
#
# The seam between the grammar and everything under it. Every act
# addresses its resource and builds its body from one of these, and the
# fields are the whole vocabulary the grammar has: the two options
# accepted on either side of the command word, and the arguments that
# address one entry. Stated as a type rather than as a bag of
# attributes, so what a command can be asked is readable in one place
# and an act that reads a field nobody sets is a name that is not there.


@dataclass(frozen=True, kw_only=True)
class Invocation:
    """One command's arguments, resolved."""

    # The global options, after the merge below: each is what the
    # command position said when it said anything, and what the root
    # position said otherwise. The two booleans are resolved to plain
    # booleans exactly once, by that merge, so nothing below this seam
    # has to know that "not given" was ever a third answer.
    config: str | None = None
    api_url: str | None = None
    force: bool = False
    no_input: bool = False

    # What addresses one entry, under the names the descriptors'
    # `addressing` tuples use, which are the URL's path parameters and
    # the CLI's positionals for the same reason.
    stage: str = ""
    name: str = ""
    mac: str = ""
    code: str = ""
    slot: str = ""

    # Which kind of entity a command that covers two of them was asked
    # about, which is what decides where a credential is addressed. Read
    # off the row rather than off the words, because a command's noun
    # path and the kind it addresses are not the same thing once the
    # tree is more than two words deep.
    kind: str = ""

    # The rest of what a command can carry: the agents a binding names,
    # the two ways a write's entity is written, the variable a secret is
    # read from, and the entity a schema is asked for.
    agents: tuple[str, ...] = ()
    file: str = ""
    pairs: tuple[str, ...] = ()
    from_env: str | None = None
    entity: str | None = None

    # Whether the one row with a second act was told to leave it out.
    # `apply` writes the document and installs it; this stages the write
    # instead, and it is a field of the invocation because the row reads
    # it to choose what it runs (`Command.selects`).
    no_reload: bool = False

    # And the provider type a schema is asked about, which goes with the
    # `stage` above: the two together name one type's options, since the
    # registry holds one type name in more than one stage.
    type_name: str = ""

    # The conversation store's session, and the two things a listing and
    # a purge are narrowed by that are not a device. `mac` carries the
    # device for both of them, reused from the verbs that already take
    # one rather than given a second name: what `--device` names is the
    # same board `device show` addresses.
    #
    # `limit` and `before` are text and are not read here. What each has
    # to be is the API's rule, said in the API's own fixed sentence, and
    # a second parser in front of it would be a second vocabulary for
    # one refusal.
    session: str = ""
    limit: str = ""
    before: str = ""

    # And the conversation store's other identity, the thread. Its own
    # field rather than `name`, because the two are addressed at once:
    # `conversation list --agent sam` filters threads by an agent's
    # name, which `name` is already carrying.
    conversation: str = ""

    # What narrows the live event stream beyond the board and the
    # session above, which `mac` and `session` carry for it: what
    # `--device` names is the same board `device show` addresses, and
    # what `--session` names is the same session `session show` does.
    #
    # `level` is text and is not read here, for the reason `limit` is
    # not: what a level may be is the API's rule, said in the API's own
    # fixed sentence, and a second parser in front of it would be a
    # second vocabulary for one refusal.
    level: str = ""

    # And whether the tail keeps going. The one argument in this grammar
    # that changes when a command stops rather than what it asks for.
    follow: bool = False

    # The address a simulated board checks in to. Its own field rather
    # than `name` or `file`, because it is neither an identity nor a
    # payload: it names the deployment, it is held to the device-facing
    # transport policy rather than to the API's, and it is the one
    # positional in this grammar that addresses no row of anything. The
    # MAC and the agents that go with it are `mac` and `agents`, reused
    # from the device verbs that already take them.
    endpoint: str = ""


# The commands that reach no API
#
# Everything else a command does is a row in the table further down.
# These four are not acts of the configuration API at all: one is about
# onboarding a board, which happens before there is anything to
# configure, and three render the models and the API's own routes
# without opening a database, reaching a server or needing a key.


def _from_an_installed_half[T](answered: Callable[[], T], missing: str) -> T:
    """One command's answer, or the given sentence when the half it needs
    is not installed.

    The gate for every command in this grammar that reaches a module the
    default install does not carry. There are three: `ota-url` and
    `openapi` read the server half, and `simulator run` reads the
    websocket client behind the `sim` extra. Everything else is either a
    request, which needs no such module, or a render off the models,
    which are the client half.

    The SENTENCE is a parameter rather than this function's own constant,
    because the two halves send a reader to two different places: one is
    a thing you go somewhere that has, the other is a thing you install.
    A second copy of this function with its own constant would have been
    a second chance to get the ImportError containment wrong, on the one
    surface where getting it wrong relays a module path.

    Recorded inside the handler and raised outside it, the way every
    boundary in this module raises. An ImportError's text is the module
    path it could not find, and an exception raised while one is being
    handled carries it as `__context__` for anything walking the chain;
    raising after the handler leaves neither a cause nor a context.

    Only ImportError is caught, and only around the call: a
    `ConfigError` out of the answer itself is this grammar's own refusal
    and travels as one.
    """
    answers: list[T] = []
    try:
        answers.append(answered())
    except ImportError:
        pass
    if not answers:
        raise ConfigError(missing)
    return answers[0]


def _derived_ota_url(config: ServerConfig) -> tuple[str, object]:
    """The onboarding derivation, imported where it is used.

    Not at the top of this module, and it is the one import here that is
    deferred for weight rather than for a cycle. `onboarding/origin.py`
    imports `.keys`, which imports FastAPI, and naming either submodule
    runs the package's own `__init__`, which imports the aggregate; so
    the derivation is the server half however little of it this command
    wants. Extracting a FastAPI-free half of that package is a second
    responsibility and #287's, and until then this command is gated
    rather than thinned (the plan's decision 9 records why).
    """
    from vinga_server.onboarding.origin import onboarding_url

    return onboarding_url(config, ONBOARDING_OFF_FOR_URL)


def _ota_url(args: Invocation) -> None:
    """The URL to type into a board's captive portal.

    The one command here that talks to nothing: no server, no database,
    no encryption key and no API token, because none of them holds any
    part of the answer. It reads the file half the way every other
    command reads it, takes the device-auth secret from the environment
    the server takes it from, and derives the key and the origin with
    the functions the server itself calls, so what it prints is what
    that server answers on rather than a second opinion about it.

    It does need those functions to be installed, which is what makes it
    one of the two gated commands: it is a server-host command by
    nature, since the file half it reads is the one a laptop does not
    have. The laptop-side question it is confused with, whether that URL
    answers, is `vinga-server doctor`'s since #244.

    The URL goes to stdout alone, so it can be captured; what to do with
    it, and where its origin came from, go to stderr the way every
    other notice does.
    """
    config = _server_config(args)
    url, origin = _from_an_installed_half(
        lambda: _derived_ota_url(config), NEEDS_THE_SERVER_HALF
    )
    print(url)
    sys.stdout.flush()
    print(OTA_URL_GUIDANCE, file=sys.stderr)
    print(f"The URL above is {origin.provenance}.", file=sys.stderr)


def _schema(args: Invocation) -> None:
    """The JSON Schema of one entity kind, of one provider type's
    options when a stage and a type follow `provider`, or of the whole
    domain configuration. Reads the models and the registry and nothing
    else: no database, no configuration file, no encryption key, no
    server."""
    print(docgen.schema(args.entity, args.stage, args.type_name), end="")


def _reference(args: Invocation) -> None:
    """The markdown reference, the same document CI diffs the committed
    copy against."""
    print(docgen.reference(), end="")


def _openapi(args: Invocation) -> None:
    """The configuration API's OpenAPI document, the other artifact CI
    diffs its committed copy against. Rendered from the routes, so it
    opens no database and needs no token: the application is built, its
    document is taken, and nothing of it is served.

    The routes are the server half, so this is the second of the two
    gated commands. What it renders is committed at
    `docs/reference/api-openapi.json`, which is where a client-only
    installation reads the contract instead."""
    print(_from_an_installed_half(docgen.openapi, NEEDS_THE_SERVER_HALF), end="")


def _cli_reference(args: Invocation) -> None:
    """The generated half of the committed CLI reference, the fourth
    artifact CI diffs its committed copy against. Renders the command
    tree and reads the example fragments, and opens nothing else."""
    print(cli_reference(), end="")


# The committed command reference
#
# `docs/reference/cli.md` is half written and half generated, and the
# generated half is this. It lives here rather than in `docgen` because
# what it renders is the command tree, and the command tree is this
# module: a renderer of it that lived anywhere else would import the app
# to reach what its neighbour already has, which is the pass-through the
# design guide deletes. That is the second deliberate exception to
# `docgen`'s no-application rule, beside `openapi()`, and it is the same
# exception: rendering help opens no database, reads no configuration
# file and needs no key, so the command in front of this is as read-only
# as its three neighbours.
#
# Deterministic, because CI diffs it byte for byte. Click's help
# formatter sizes itself to the terminal it is printing into and colors
# what it prints, and neither of those may reach a committed file, so
# every page below is rendered through a context that states its width
# and refuses color. Nothing else about the output depends on the
# machine: the tree is built from the table, and the table is a literal.

# Where the generated region of the committed page begins and ends. The
# hand-written half around it is prose nobody generates (installing the
# thing, reaching a server, rebuilding one), so the drift check
# compares the region between these two markers and leaves the rest
# alone.
REFERENCE_BEGIN = "<!-- generated: cli reference -->"

REFERENCE_END = "<!-- end generated: cli reference -->"

# And a pair inside that pair, around the recipes alone.
#
# Not decoration and not a second copy of the outer lane. The outer check
# regenerates the whole region through the page composer below, so a
# composer that dropped, truncated or reordered the recipes would move
# the committed page and the fresh render together and pass. The inner
# check compares the same bytes against the recipe renderer directly,
# which is the only reader that can tell those two apart, and it is what
# the plan asks the recipes to have of their own.
RECIPES_BEGIN = "<!-- generated: cli recipes -->"

RECIPES_END = "<!-- end generated: cli recipes -->"

# What every help page is wrapped at, stated rather than discovered. 80
# is the width the rest of the generated documentation wraps prose at,
# two columns wider, and it is what keeps a help page inside a fenced
# block on a page somebody reads on a phone.
REFERENCE_WIDTH = 80

# Both spellings of the request for help, on every page of the tree.
# `-h` is the one half the world types first, and a program that answers
# only the long one answers nothing to that.
#
# Named rather than written into the app, because two readers need the
# same answer: the live tree takes it as a context setting, and the
# renderer below builds its own root context by hand and would otherwise
# render pages listing a spelling the live tree does not have, or the
# other way round. Every page under the root inherits it from its
# parent context.
HELP_OPTION_NAMES = ["-h", "--help"]

REFERENCE_INTRO = (
    "Generated by `{program} cli-reference`. Do not edit anything between the two "
    "markers around it by hand: CI regenerates this region and fails on any "
    "difference, so an edit here is reverted by the next run. Everything outside them "
    "is written by hand and generated by nothing."
)

RECIPES_INTRO = (
    "One topic at a time, in the order the whole list runs in against an empty "
    "database. Every line below is read out of the example file it names, so a recipe "
    "cannot come to name a file that moved or an entity name a fragment no longer "
    "uses, and the whole of it is run against a live server on every build."
)

COMMANDS_INTRO = (
    "Every command of the group, with the page its own `--help` prints. A command "
    "takes `--config` and `--api-url` before the command word as well as after it, "
    "and a value given before it survives a command that was not given one."
)


def cli_reference() -> str:
    """The generated region of `docs/reference/cli.md`.

    Two halves, because a reference answers two questions. The recipes
    say what to type to configure a deployment, read out of the example
    fragments by `docgen`. The command pages say what every command
    takes, read off the command tree here. Neither is written twice.
    """
    lines = [
        *_paragraph(REFERENCE_INTRO.format(program=PROGRAM)),
        "",
        "## Recipes",
        "",
        *_paragraph(RECIPES_INTRO),
        "",
        RECIPES_BEGIN,
        *cli_recipes().splitlines(),
        RECIPES_END,
        "",
        "## Every command",
        "",
        *_paragraph(COMMANDS_INTRO),
        "",
        *_help_pages(command(), (PROGRAM,), None),
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def cli_recipes() -> str:
    """The recipes alone, exactly as they sit between their own markers
    on the committed page.

    The composer above pastes this between the markers rather than
    building the recipes itself, and the inner drift check compares the
    page's own bytes against this, so what the check reads and what the
    page carries are the same rendering rather than two of them. The
    leading blank line is part of it: a paragraph pressed against an
    HTML comment is swallowed into the comment's block by every markdown
    renderer there is, and the extraction is "the lines between the two
    markers", which has to be able to say so exactly.
    """
    return "\n".join(["", *docgen.recipe_lines()]) + "\n"


def _help_pages(shape: Any, words: tuple[str, ...], parent: Any) -> list[str]:
    """One command's help page, and the pages of the commands under it.

    The context is built with its width and its color stated, which is
    the whole of what makes this deterministic: left to itself Click
    measures the terminal it is printing into, and a document that
    wrapped differently on a laptop and on a runner would fail its own
    drift check on an unrelated change.
    """
    context = Context(
        shape,
        info_name=words[-1],
        parent=parent,
        terminal_width=REFERENCE_WIDTH,
        max_content_width=REFERENCE_WIDTH,
        color=False,
        help_option_names=HELP_OPTION_NAMES,
    )
    lines = [
        f"### `{' '.join(words)}`",
        "",
        "```",
        *shape.get_help(context).splitlines(),
        "```",
        "",
    ]
    for word, under in getattr(shape, "commands", {}).items():
        lines += _help_pages(under, (*words, word), context)
    return lines


def _paragraph(text: str) -> list[str]:
    """One paragraph of the generated region, wrapped where the rest of
    the generated documentation wraps its prose."""
    return textwrap.wrap(
        text, width=docgen.PROSE_WIDTH, break_long_words=False, break_on_hyphens=False
    )


# Reaching the API
#
# One request per command, over a client built behind a seam the
# acceptance suite replaces with a test client, so the same entry point
# runs against the real application with no socket. What the seam does
# not cover is the addressing and the transport policy, which run in
# front of it and are what those tests are checking.

# The libraries that would narrate the request, and how quiet they are
# held while it is made.
#
# `httpx` writes one line per request at INFO carrying the method, the
# URL and the status, and `logs.py` keeps that deliberately where it
# floors the vendor libraries: for every other caller in this server the
# URL it names says nothing that is not already public. For this one it
# is the address an operator typed, which is accepted with its query
# string whole and can carry `?token=<secret>` in it, so the record the
# library writes is the one surface `Address` exists to keep the
# credential off. A log record is retained in a way a terminal is not.
# `httpcore` traces the connection underneath and is held with it. The
# same two loggers, at the same level and for the same reason, as
# `doctor.py`'s probe; neither module may import the other, so the
# reason is stated in both rather than shared through one.
#
# Held for every request rather than only for an address whose two forms
# differ. The rule is then one rule: this command's own sentences are
# what an operator reads, and no request of its making narrates itself.
# Nothing is lost that anybody needs, because the request is one call
# whose outcome the command reports either way, and a conditional would
# make the quiet part of the value rather than part of the command.
REQUEST_LOGGERS = ("httpx", "httpcore")

# WARNING rather than off, so a library with something genuinely wrong
# to say can still say it, and scoped to the request rather than set
# once, so nothing here changes what a process that imported this logs
# afterwards.
QUIET_LEVEL = logging.WARNING


def build_client(base_url: str, token: str) -> httpx.Client:
    """The connection to the configuration API.

    The one seam in this module. `cli.main()` is and stays synchronous,
    and httpx's ASGI transport is async-only, so the tests replace this
    with Starlette's TestClient: itself a synchronous `httpx.Client`
    subclass that drives an ASGI application through its own portal.

    The token is required rather than defaulted, because every caller
    resolves one before it builds a client and a seam's untaken branch
    is a branch nobody is checking. The one caller that wanted a client
    without an Authorization header was `doctor`, which has its own seam
    now (#244) and no way to carry a credential at all.
    """
    return httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
    )


_NOTHING = object()


@dataclass(frozen=True, kw_only=True)
class Address:
    """Where the API is, in the three forms that are not one string.

    `base` is what the client is built on: the scheme, the host and the
    path, and no query. `query` is the query string the operator's
    address carried, which every request puts back after its own path.
    `shown` is the address with its credentials taken out, bounded and
    made printable, and it is the only one any sentence here may name:
    an accepted URL is not a safe one to print, since the policy below
    refuses the userinfo but says nothing about a query string, and
    `...?token=<secret>` is the other form vendors accept.

    The query is held apart rather than left on the base because a
    client joins an endpoint's path onto the base's whole raw path,
    query included: a base of `https://host/api?token=x` used to send
    `GET /api?token=x/agents`, which is the endpoint's name inside the
    credential's value. `endpoint` below is the composition that was
    missing.

    One type rather than three arguments travelling together, because
    every function that names an address had a plain string to reach for
    and reached for the wrong one (#290): a transport failure and an
    unreadable answer both printed the URL they were given. What crosses
    now is this, and `.shown` is the only field a message can read
    without saying what it is doing.
    """

    base: str
    query: str
    shown: str

    def endpoint(self, path: str, query: str = "") -> str:
        """One endpoint's path under this address, with the arguments
        this request carries and then the query the operator's address
        did.

        The operator's half is reattached as it was written rather than
        re-encoded, since what it holds can be a credential a gateway
        compares literally, and `%20` and `+` are the same space to a
        reader and two different strings to a comparison. The request's
        own half is encoded here, because it is built from what was
        typed at the command rather than parsed out of a URL.
        """
        parts = [part for part in (query, self.query) if part]
        return f"{path}?{'&'.join(parts)}" if parts else path


@dataclass(frozen=True, kw_only=True)
class Reached:
    """Where one invocation's requests go, and what they carry.

    The two answers to "where to reach, in a stated order" (the flag,
    then the environment, then a default derived from the file half),
    resolved once for a whole command rather than once per request.

    Once matters as soon as a command makes more than one request. Each
    of them used to re-read the configuration file and re-resolve the
    address and the token off it, so a file changing under a running
    command could send its second request somewhere its first did not
    go, and `info`, which prints the address it reached before it
    reaches it, could name one endpoint and print another's answer. What
    a command reports about where it went has to be true of where it
    went.

    Frozen and carried rather than re-derived, which is the design
    guide's locality rule applied to a fact with three readers: the line
    `info` opens with, every request's client, and every sentence that
    names an address after a failure.
    """

    address: Address
    token: str


def _reached(args: Invocation) -> Reached:
    """Where this invocation reaches the API, resolved.

    The one read of the file half on the request path, and the one
    resolution of the address and the token off it. A missing token is
    still a sentence before any request is sent, and it is now one
    sentence before the FIRST request rather than before whichever
    request reached this next.
    """
    file_config = load_file_config(args.config)
    return Reached(address=_address(args, file_config), token=_token(file_config))


def _call(
    reached: Reached,
    method: str,
    path: str,
    body: object = _NOTHING,
    read_timeout_s: float | None = READ_TIMEOUT_S,
    query: Mapping[str, str] | None = None,
) -> object:
    """One request, and its answer as this client understands it.

    `reached` is where this whole invocation is talking to, resolved
    once by the row that is performing rather than here: see `Reached`.

    `read_timeout_s` is how long this one endpoint may take to answer,
    which for all but the reload and the apply is the same bound the
    client is built with; None is no bound at all, which is what one
    endpoint has and `APPLY_READ_TIMEOUT_S` says why.

    Set on the client rather than passed with the request: a
    per-request timeout is what httpx would want, and Starlette's
    TestClient refuses one outright, which would take the seam the whole
    acceptance suite runs through with it. Each call builds a client,
    makes one request and closes it, so the two are the same thing here.

    The whole of the request is inside a logging boundary, and that is
    the one thing here that is not about what reaches a terminal: the
    client library writes a line per request naming the URL it was
    given, which for this caller is an operator's address with its query
    string whole. `REQUEST_LOGGERS` above says which loggers and why.
    The token is resolved before the boundary opens, and now before the
    command's first request, so a missing one is still the sentence it
    was.
    """
    with quieted(REQUEST_LOGGERS, QUIET_LEVEL):
        response = _sent(
            method,
            path,
            body,
            reached.address,
            reached.token,
            read_timeout_s,
            urlencode(query or {}),
        )
    return _answer(response, reached.address)


def _sent(
    method: str,
    path: str,
    body: object,
    address: Address,
    token: str,
    read_timeout_s: float | None,
    query: str = "",
) -> httpx.Response:
    """The request, with everything that can go wrong making it turned
    into a sentence.

    Building the client is inside the boundary with the request and the
    close, which is where `doctor.py`'s probe already puts it and for the
    same reason: httpx validates the address when it is handed one, so
    construction is where an address this module's own policy accepted
    and the library then refuses would otherwise leave as a traceback
    with what was typed in it. An IDNA hostname is the shape that does
    it, and it arrives as a `UnicodeError` from under the library rather
    than as anything httpx names, which is why the arm is that wide.

    Every message is built inside a handler and raised after all of
    them: an exception raised while another is being handled carries
    that one as its context, and httpx's exceptions carry the request,
    whose URL is one of the two things this whole policy exists to keep
    out of sight.

    The close is a step of the request rather than tidying after it, so
    it answers a sentence instead of raising: an exception out of a
    `finally` leaves this boundary altogether, taking whatever a driver
    wrote into its message with it, and it would replace a refusal
    already in flight. Whatever failed first is what is reported.
    """
    problem: str | None = None
    client: httpx.Client | None = None
    answered: httpx.Response | None = None
    try:
        try:
            client = build_client(address.base, token)
            client.timeout = httpx.Timeout(read_timeout_s, connect=CONNECT_TIMEOUT_S)
            endpoint = address.endpoint(path, query)
            answered = (
                client.request(method, endpoint)
                if body is _NOTHING
                else client.request(method, endpoint, json=body)
            )
        except httpx.HTTPError:
            problem = _unreachable(address)
        except (httpx.InvalidURL, ValueError):
            problem = _unopenable(address)
    finally:
        problem = problem or _close_failed(client, address)
    if answered is None or problem is not None:
        raise ConfigError(problem)
    return answered


def _unreachable(address: Address) -> str:
    return (
        f"cannot reach the configuration API at {address.shown}: the request did not "
        f"complete. Check that the server is running and that this is the address "
        f"it serves. A deployment whose server will not start at all is recovered "
        f"by booting one on an empty database and applying a kept export."
    )


def _unopenable(address: Address) -> str:
    """What an address this module accepted and the library would not
    open says. The transport policy is about the scheme, the host and
    the credential; whether a host can be encoded at all is the
    library's rule, and this is where its refusal becomes one of ours."""
    return (
        f"no connection can be opened to {address.shown}: the address passed the "
        f"transport policy, and the library that would carry the request will not "
        f"accept it. A hostname holding a character no name may hold is what does "
        f"this. Neither the address as it was typed nor the library's own wording is "
        f"repeated here."
    )


def _close_failed(client: httpx.Client | None, address: Address) -> str | None:
    """Give the connection back, and say so when it will not go.

    Answered rather than raised, for the reason the caller states, and
    named by nothing at all rather than quoted, because a transport
    failing on its way out can put the address, a header or a driver's
    own text into its message."""
    if client is None:
        return None
    try:
        client.close()
    except Exception:
        return (
            f"the configuration API at {address.shown} answered, but the connection to "
            f"it could not be closed, so what it said is not printed: an answer this "
            f"client could not finish reading is not one to act on. What the library "
            f"said is not repeated here."
        )
    return None


def _streamed(
    reached: Reached, path: str, query: Mapping[str, str] | None = None
) -> Iterator[str]:
    """The lines of one answer that does not finish arriving.

    `_sent`'s sibling, and a sibling rather than a flag on it because
    what the two do with a response is opposite: that one reads a body
    and hands it back, this one hands back a body that has no end. What
    they share is everything else, and it is not optional. A stream is
    the one request in this grammar that can fail AFTER a response has
    opened, which is exactly where a bare `build_client` preserves none
    of the boundary: the request loggers are quiet for the whole length
    of the stream and not only for its opening, a failure at any point
    of it is a sanitized sentence naming `Address.shown` and nothing
    else, no exception raised here carries the request URL in its chain,
    and the client is given back however the reader leaves.

    Quieting for that length has a consequence worth stating, because it
    is invisible from here: `quieted` holds a process-global lock for
    the span it covers (`logs.py` says why, and the level it is holding
    is the process's whatever guards it), so this block holds it for as
    long as the stream is open. Every other request boundary in this
    package waits behind it. On a deployment that costs nothing, since a
    tail is a process watching one thing; in a test it means the tail
    cannot share a process with what it is watching, which is why the
    live lane runs it as a subprocess.
    """
    with quieted(REQUEST_LOGGERS, QUIET_LEVEL):
        yield from _reading(reached, path, query)


def _reading(
    reached: Reached, path: str, query: Mapping[str, str] | None
) -> Iterator[str]:
    """One open stream, line by line, and every way it can end.

    It always ends by raising, which is the shape of the thing rather
    than a decision taken here: a stream that stopped is either a
    failure this says a sentence about, or the stream having ended,
    which is `STREAM_ENDED` and is also a failure. A reader that has
    read enough leaves by closing this generator, and the `finally`
    below gives the connection back on that path exactly as it does on
    the others.

    The three arms are `_sent`'s three, for its reasons: httpx validates
    an address when it is handed one, so construction is inside the
    boundary; every message is built inside a handler and raised after
    all of them, because an exception raised while another is being
    handled carries that one as its context and httpx's exceptions carry
    the request; and the close answers a sentence rather than raising,
    so whatever failed first is what is reported.
    """
    address = reached.address
    problem: str | None = None
    client: httpx.Client | None = None
    opened: httpx.Response | None = None
    try:
        try:
            client = build_client(address.base, reached.token)
            client.timeout = httpx.Timeout(STREAM_READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
            endpoint = address.endpoint(path, urlencode(query or {}))
            opened = client.send(client.build_request("GET", endpoint), stream=True)
        except httpx.HTTPError:
            problem = _unreachable(address)
        except (httpx.InvalidURL, ValueError):
            problem = _unopenable(address)
        if opened is not None and problem is None:
            if not opened.is_success:
                _refused_stream(opened, address)
            try:
                yield from opened.iter_lines()
            except httpx.HTTPError:
                # The connection died with the stream open, which from
                # here is the stream ending: see `STREAM_ENDED`.
                problem = STREAM_ENDED
    finally:
        problem = problem or _close_failed(client, address)
    raise ConfigError(problem if problem is not None else STREAM_ENDED)


def _refused_stream(response: httpx.Response, address: Address) -> None:
    """A stream that never opened, said the way every other refusal is.

    A refusal has a body and an end, so it is read whole and handed to
    `_answer`, which is what keeps one vocabulary whichever way an
    operator reached this API: a 401 here says what a 401 says anywhere
    else in this grammar. Reading it is itself a request that can fail,
    which is why the read is inside the boundary too.
    """
    problem: str | None = None
    try:
        response.read()
    except httpx.HTTPError:
        problem = _unreachable(address)
    if problem is not None:
        raise ConfigError(problem)
    # Always a refusal, because the caller asked only for what is not a
    # success, and `_answer` raises on every one of them.
    _answer(response, address)


def _answer(response: httpx.Response, address: Address) -> object:
    """What the API said, or a sentence about why it cannot be read.

    A refusal's `detail` is the repository's own message and is passed
    through untouched, which is what keeps one vocabulary whichever way
    an operator reached the command. Anything else is reported as a
    status code and a fixed sentence: a body this client did not
    recognize did not come from the API's sanitized output, and relaying
    it would put a middlebox's page where a configuration error belongs.

    Which of the two an answer is is decided by `_refusal` below, and
    the decision is narrow on purpose: only a validated
    `application/problem+json` body whose status and title match the
    response is relayed, because a JSON object with a string `detail`
    in it is a shape anything in front of this API can write, and
    every other body is suppressed for the fixed sentence.
    """
    payload = _payload(response)
    if response.is_success:
        if payload is _NOTHING:
            raise ConfigError(_unreadable(response, address))
        return payload
    detail = _refusal(response, payload)
    raise ConfigError(detail if detail is not None else _unreadable(response, address))


def _refusal(response: httpx.Response, payload: object) -> str | None:
    """The sentence this API wrote, or None when what answered is not
    this API's refusal.

    Three things have to agree before a body's own words are relayed to
    a terminal, and they are three because a middlebox can produce any
    one of them by itself:

    - the media type is exactly `application/problem+json`, which is
      what this API answers a refusal with and what a proxy answering
      `application/json` is not;
    - the body validates as the `Problem` model, which forbids extra
      members, so a page carrying a `detail` beside anything else is
      not one;
    - the status in the body is the status of the response and the
      title is the phrase this API gives that status, so a body lifted
      from one refusal and replayed under another is not one either.

    Anything short of all three is `_unreadable`'s fixed sentence, with
    nothing of the body in it. The model is the one in
    `config/responses.py`, which is the half a generated client would
    substitute for; the validation error is dropped inside the arm and
    never raised from, because pydantic puts the input it rejected into
    its own message.
    """
    media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != PROBLEM_MEDIA_TYPE:
        return None
    title = PROBLEM_TITLES.get(response.status_code)
    if title is None:
        return None
    try:
        problem = Problem.model_validate(payload)
    except ValidationError:
        return None
    if problem.status != response.status_code or problem.title != title:
        return None
    return problem.detail


def _payload(response: httpx.Response) -> object:
    """The response's JSON body, or `_NOTHING` when it has none this
    client can read. No exception escapes, so nothing that walks an
    exception chain later finds the body attached to it."""
    if "json" not in response.headers.get("content-type", ""):
        return _NOTHING
    parsed: object = _NOTHING
    try:
        parsed = response.json()
    except ValueError:
        parsed = _NOTHING
    return parsed


def _unreadable(response: httpx.Response, address: Address) -> str:
    return (
        f"the configuration API at {address.shown} answered {response.status_code} with "
        f"{UNRECOGNIZED_ANSWER}. It is not quoted back: what a proxy or a gateway "
        f"returns is not this API's own output."
    )


def _address(args: Invocation, file_config: FileConfig) -> Address:
    """Where the API is: the flag, then the environment, then this
    machine on the port the server half names.

    The last of the three is this module's own string and carries
    nothing to take out, so both of its forms are the same one."""
    if args.api_url:
        return _permitted(args.api_url, "--api-url")
    named = os.environ.get(API_URL_ENV, "").strip()
    if named:
        return _permitted(named, API_URL_ENV)
    local = f"http://127.0.0.1:{file_config.server.port}{API_MOUNT_PATH}"
    return Address(base=local, query="", shown=local)


def _permitted(url: str, source: str) -> Address:
    """The transport policy, which is about the token before it is about
    any secret body.

    The bearer token crosses every request and grants everything the API
    can do, secret writes included, so loopback-or-TLS is the rule for
    the whole client rather than a secret-write footnote. There is
    deliberately no flag to override it: such a flag's only purpose would
    be sending the token in clear.

    An accepted URL leaves as an `Address` rather than as itself, and
    the display form it carries is the one computed here: the policy
    refuses a credential in the userinfo but says nothing about a query
    string, so an accepted address can still hold `?token=<secret>`, and
    the transport failures further up print the address they were given.
    """
    parsed = parsed_url(url, source)
    # Bounded and made printable as well as stripped, because this is
    # the form every sentence below names and a typed address is text
    # nobody has vouched for: `urlsplit` deletes tabs and newlines and
    # leaves every other control character where it was, and a hostname
    # the library goes on to refuse is exactly the one carrying one.
    shown = printable(shown_url(parsed))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConfigError(
            f"{source} is not an http:// or https:// URL with a host: {shown}"
        )
    if parsed.username or parsed.password:
        raise ConfigError(
            f"{source} carries a username or a password in the URL, which is refused: "
            f"this API's credential is a bearer token sent as a header, and anything in "
            f"a URL ends up in shell history, process lists and access logs. The "
            f"address without it is {shown}."
        )
    if parsed.scheme == "http" and not _loopback(parsed.hostname):
        raise ConfigError(
            f"{source} names {shown}, a plain http:// connection to a host that is not "
            f"a loopback address (127.0.0.1, ::1 or localhost), and the bearer token "
            f"would cross it in clear along with anything a secret write sends. Use "
            f"https://, put a TLS-terminating tunnel in front, or exec into the "
            f"running container and reach the API on loopback. There is deliberately "
            f"no flag to override this."
        )
    # Rebuilt from the parsed parts rather than trimmed as a string,
    # which is what takes the query off the base and the fragment with
    # it. The userinfo cannot survive either, and is refused above in
    # any case.
    return Address(
        base=urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")),
        query=parsed.query,
        shown=shown.rstrip("/"),
    )


def _loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _token(file_config: FileConfig) -> str:
    """The bearer token, from the variable `server.api.secret_env` names.

    On a deployment that is exactly the variable the server itself was
    started with, so exec into the running container and the CLI has the
    token and the loopback address for free. Resolved before any request
    is sent, so a missing one is a sentence rather than a 401.
    """
    name = file_config.server.api.secret_env
    token = os.environ.get(name, "").strip()
    if not token:
        raise ConfigError(
            f"{name} is not set, and every request to the configuration API carries its "
            f"value as a bearer token. It is the same variable the server was started "
            f"with: exec into the running container and it is already in the "
            f"environment."
        )
    return token


def _path(*parts: str) -> str:
    """One resource's path, each identity as exactly one segment.

    Percent-encoded with nothing left safe, which is what lets a name
    carrying a space, a percent sign or a character outside ASCII be
    addressed with no second scheme. A name carrying a slash cannot be
    addressed at all, which is why the repository refuses to write one.
    """
    return "/" + "/".join(quote(part, safe="") for part in parts)


def _secret_path(args: Invocation) -> str:
    if args.kind == "provider":
        return _path("providers", args.stage, args.name, "secrets", args.slot)
    return _path("mcp-servers", args.name, "secrets", args.slot)


# Reading an answer
#
# What a body has to be to be read as one is the shape the API declared
# it would send, which is a model in `responses.py` that the API itself
# answers with. There is no second encoding of it here: a rule this
# module kept by hand is a rule that goes stale the day a field is
# renamed, and the two files that would then disagree both say they are
# describing the same thing.


def _understood(shape: object, answer: object, refusal: str) -> Any:
    """One answer, read as the shape the API says it sends, or refused.

    Strict, so nothing is coerced on the way in: a body is free to put
    `true` where a size belongs or an object where a word does, and a
    renderer that printed the coercion would be printing something
    nobody sent. Extra fields are dropped rather than refused, which is
    the one tolerance this keeps deliberately: a newer server that
    answers more than this client knows about is readable, and what it
    said beyond the shape is not printed, because it was not rendered.

    The refusal is built inside the handler and raised after it, and the
    exception itself is not bound to a name: `ValidationError.errors()`
    retains the input it rejected, which for this API can be a
    credential someone pasted into a fragment, and an exception raised
    while another is being handled keeps that one as its `__context__`
    for anything walking the chain to find.

    Answers `Any` rather than `object` because what comes back is the
    shape that was asked for, and every caller reads it as one.
    """
    problem: str | None = None
    try:
        adapter = TypeAdapter(shape)
        # Answered back as the mappings the renderers read, which is the
        # shape a renderer takes. Dumping a validated model is also what
        # leaves the extras behind: only what the shape declares is
        # written back out.
        return adapter.dump_python(adapter.validate_python(_declared(shape, answer), strict=True))
    except ValidationError:
        problem = refusal
    raise ConfigError(problem)


def _declared(shape: object, answer: object) -> object:
    """The answer with anything the shape does not declare left out.

    Every model in `responses.py` forbids extra keys, because the
    document it generates is a contract about what this API sends. This
    client reads that contract from the other side, where an unknown key
    means a server newer than it, so it drops what it does not know
    instead of refusing the whole answer. Guided by the shape and not by
    a list of field names: a mapping keyed by identity is walked into, so
    an entry nested in a listing is treated exactly as one that arrived
    on its own.
    """
    if isinstance(shape, type) and issubclass(shape, Enum):
        # A closed token, which arrives as the string it is declared as
        # and which strict validation will not make a member of. Looked
        # up rather than constructed: a token this client does not know
        # stays the string it was and meets the refusal, where
        # `Applies(answer)` would raise a ValueError out of a boundary
        # that catches validation errors.
        #
        # Only a string is looked up, and that is the same rule stated
        # about the answer rather than about the shape. Nothing bounds
        # what a body puts where a token belongs: a list or an object
        # there is unhashable, and using it as a key would raise a
        # TypeError out of this boundary exactly as constructing the
        # member would have. Every other shape passes through untouched
        # and meets strict validation, which is what turns it into the
        # one fixed sentence a body this client cannot read gets.
        if not isinstance(answer, str):
            return answer
        return {member.value: member for member in shape}.get(answer, answer)
    if isinstance(shape, type) and issubclass(shape, BaseModel):
        if isinstance(answer, Mapping):
            return {
                name: _declared(field.annotation, answer[name])
                for name, field in shape.model_fields.items()
                if name in answer
            }
        return answer
    origin, arguments = get_origin(shape), get_args(shape)
    if origin is dict and isinstance(answer, Mapping):
        return {key: _declared(arguments[1], value) for key, value in answer.items()}
    if origin is list and isinstance(answer, list):
        return [_declared(arguments[0], item) for item in answer]
    if origin is tuple and arguments[-1] is Ellipsis and isinstance(answer, list):
        # A JSON array is a list, and strict validation will not make a
        # tuple of one. The shape asked for a tuple because what it
        # answers with is fixed once it is answered, which is a fact
        # about the model and not about the wire, so the conversion
        # belongs here with the other shape-guided ones rather than as a
        # tolerance inside the validator.
        return tuple(_declared(arguments[0], item) for item in answer)
    # Anything else is a leaf as far as this is concerned, including the
    # unions, which carry no model in any of these shapes, and
    # `dict[str, Any]`, which is where a masked entity body travels
    # through undescribed on purpose.
    return answer


# The onboarding URL
#
# The one command here that is not about the domain configuration and
# does not go near the API: it derives a string from the file half and
# the environment, and contacts nothing. The derivation itself lives in
# `onboarding.origin`, beside the origin resolution it composes, so
# that this and `vinga-server doctor` cannot come to disagree about
# what a person is supposed to type.


def _server_config(args: Invocation) -> ServerConfig:
    """The file half's `server` section, read the way every command
    reads it. No database is opened and no config file has to exist:
    without one the field defaults and the VINGA_ environment are the
    whole answer."""
    return load_file_config(args.config).server


# Rendering


def _show_everything(document: Mapping[str, object]) -> str:
    """The whole domain configuration in one document, in the shape the
    YAML file has today, with the stored secrets listed as masks
    underneath it."""
    notes = _all_secret_notes(document)
    return _yaml(document["config"]) + ("\n" + "\n".join(notes) + "\n" if notes else "")


# Export
#
# The apply-able projection of what a read already answers. There is no
# new read behind it: #207 made every read derive from the descriptor
# registry and stay write-shaped, and #192's marker made the display
# envelope the writable projection, so this is assembly rather than
# translation. The whole-configuration read is already the document
# `apply` takes, section for section, and one entity's envelope already
# carries the fragment `set` takes.
#
# What export adds is the two things a document has to say that a read
# does not. The header says how to reproduce the deployment, in order.
# And the stored credentials become comment annotations naming the
# command that enters each of them, because a credential never travels
# in a read: it is not in the exported bodies at all, and the mask is
# not a value a creating write would accept, so injecting one would make
# an export fail to apply onto an empty store, which is the one place it
# most has to work.

EXPORT_HEADER = f"""\
# The domain configuration of this deployment, in the shape
# `{PROGRAM} apply` takes. Reproduce it in three steps, in this order:
#
#   1. {PROGRAM} apply --no-reload -f <this file>
#   2. the secret set commands at the foot of this file, if any
#   3. {PROGRAM} reload
#
# A stored credential never travels in a read, which is what the second
# step is for, and why the first stages rather than installing: a
# reload builds the engines the document names, and their credentials
# are not in it yet. Applying is additive: a section this document does
# not name is left alone, and nothing in it deletes.
"""

EXPORT_SECRETS_HEADING = (
    "# Stored credentials are not exported. Enter each of them after applying:"
)

EXPORT_SLOTS_HEADING = (
    "# Stored credentials are not exported. These slots hold one, and each is entered\n"
    f"# with `{PROGRAM} <kind> secret set`:"
)

# Which kind holds a stored secret of each addressable kind, read off
# the registry: the noun a secret command sits under and the parameters
# that address one entry of it are the descriptor's, so the command an
# annotation names cannot come to disagree with the command that exists.
_SECRET_HOLDER: dict[str, entities.EntityDescriptor] = {
    kind.secret_slots: kind
    for kind in entities.ENTITIES
    if kind.secret_slots is not None
}


def _exported(document: Mapping[str, object]) -> str:
    """The whole stored configuration as one applicable document."""
    return EXPORT_HEADER + _yaml(document["config"]) + _secret_commands(document["secrets"])


def _secret_commands(secrets: Sequence[Mapping[str, object]]) -> str:
    """Every stored credential as the command that enters it, in the
    fixed order the store lists its locations in, so two exports of one
    configuration are the same bytes."""
    if not secrets:
        return ""
    lines = [
        "#   " + " ".join(shlex.quote(word) for word in _set_secret_words(stored))
        for stored in secrets
    ]
    return "\n".join(["", EXPORT_SECRETS_HEADING, *lines]) + "\n"


def _set_secret_words(stored: Mapping[str, object]) -> list[str]:
    """One stored credential's location as the command that fills it.

    A location's identity is the dotted join of the parameters that
    address the entity, and the repository owns the inverse: a name
    holding a dot is still one name, and a second spelling of the rule
    here would render a command addressing an entity that does not
    exist.

    `--` after the command's own words, and it is not decoration.
    Nothing about a name forbids a leading dash: the write path refuses
    a slash and a control character, and `--from-env` is a legal
    provider name that a secret write would otherwise read as an option
    and refuse. The marker is the shape an operator has to use to write
    such a name in the first place, so the exported command is the
    command they typed.
    """
    holder = _SECRET_HOLDER[str(stored["kind"])]
    return [
        *PROGRAM.split(),
        holder.name,
        "secret",
        "set",
        "--",
        *entities.addressed(holder, str(stored["identity"])),
        str(stored["slot"]),
    ]


def _exported_entity(kind: entities.EntityDescriptor) -> Callable[[Any], str]:
    """One entity's fragment, as the command that writes one takes it.

    The header names the kind and the command rather than the entity,
    because a fragment does not carry where it goes: what a fragment is
    for is being written somewhere, and the `set` that writes it is
    where that is chosen.
    """
    header = f"# One {kind.title.lower()} ({kind.location}), as written by\n# `{kind.command}`.\n"

    def exported(envelope: Mapping[str, object]) -> str:
        return header + _yaml(envelope["entity"]) + _stored_slot_note(envelope["secrets"])

    return exported


def _stored_slot_note(secrets: Mapping[str, object]) -> str:
    """The slots of one entity that hold a stored credential, named
    rather than commanded: a fragment does not say which entity it is
    for, so neither can the command that fills its slots."""
    if not secrets:
        return ""
    return "\n".join(["", EXPORT_SLOTS_HEADING, *(f"#   {slot}" for slot in secrets)]) + "\n"


def _print_entity(envelope: Mapping[str, object]) -> None:
    """One entity's envelope as YAML: the masked body, and its stored
    slots as comment lines. Comments rather than a mapping, because the
    mask is not a value that could be written back, and saying so in the
    document is more honest than rendering it as though it could."""
    body = envelope["entity"]
    notes = _secret_notes(body, envelope["secrets"])
    print(_yaml(body) + ("\n" + "\n".join(notes) + "\n" if notes else ""), end="")


def _all_secret_notes(document: Mapping[str, object]) -> list[str]:
    """Every stored secret in the whole-configuration view, each named by
    its location and marked when it shadows a reference written for the
    same slot."""
    bodies = _bodies(document["config"])
    notes = [
        f"#   {stored['kind']} {stored['identity']} {stored['slot']}: {MASK}"
        + _shadow_note(bodies.get((stored["kind"], stored["identity"]), {}), stored["shadows"])
        for stored in document["secrets"]
    ]
    return [SECRETS_HEADING, *notes] if notes else []


def _secret_notes(body: Mapping[str, object], secrets: Mapping[str, object]) -> list[str]:
    notes = [
        f"#   {slot}: {MASK}" + _shadow_note(body, marks["shadows"])
        for slot, marks in secrets.items()
    ]
    return [SECRETS_HEADING, *notes] if notes else []


def _shadow_note(body: Mapping[str, object], shadows: str | None) -> str:
    """What a stored secret displaces, when the entity also carries a
    reference for the same slot. Ciphertext wins, and making that
    visible is what keeps the precedence from being silent."""
    reference = _reference_value(body, shadows) if shadows else None
    return f"  (used instead of {shadows}: {reference})" if reference else ""


def _reference_value(body: Mapping[str, object], key: str) -> object:
    """What an entity writes under one of its reference-carrying keys,
    addressed the way a stored secret addresses it: a dotted key reaches
    into an MCP server's env or headers, a bare one is a provider's own
    key. Masked already, because the body it reads is."""
    group, dotted, name = key.partition(".")
    if not dotted:
        return body.get(key)
    nested = body.get(group)
    return nested.get(name) if isinstance(nested, Mapping) else None


def _bodies(config: Mapping[str, object]) -> dict[tuple[str, str], Mapping[str, object]]:
    """The masked body of every entity that can hold a stored secret,
    keyed the way a secret location names it."""
    bodies = {
        ("provider", entities.provider_identity(stage, name)): body
        for stage, entries in config["providers"].items()
        for name, body in entries.items()
    }
    bodies.update(
        (("mcp_server", name), body) for name, body in config["mcp_servers"].items()
    )
    return bodies


def _pending_listing(entries: Mapping[str, Mapping[str, str]]) -> str:
    """The devices waiting to be claimed, one line each.

    Columns rather than YAML, because the question this answers is
    which of several boards is the one being held, and the answer is
    read across a line: the code to type, the MAC it will bind, and the
    board and firmware that tell two boards apart.
    """
    if not entries:
        return f"{NOTHING_PENDING}\n"
    return _columns(
        [PENDING_COLUMNS]
        + [
            (code, entry["mac"], entry["board"], entry["firmware"], entry["expires_at"])
            for code, entry in entries.items()
        ]
    )


def _session_listing(page: Mapping[str, Any]) -> str:
    """The sessions this deployment recorded, one line each.

    Columns, because every field of a session is short and the question
    this answers is which of several sessions is the one wanted: the id
    to address, the board it was held on, the agent it opened with, when
    it ran and how much was said.

    Every cell goes through `printable`, including the ones this server
    minted itself. What a cell can hold is not decided here: the agent
    name is an operator's, the device is a board's self-description, and
    a column that wrapped, moved the cursor or recolored the terminal
    would stop being a column. `CELL_LENGTH` rather than the wider bound
    the URLs are printed under, because a cell as wide as a title makes
    a table with one row in it.
    """
    items = page["items"]
    if not items:
        return f"{NO_SESSIONS}\n"
    rows = [SESSION_COLUMNS] + [
        (
            _cell(item["session"]),
            _cell(item["device"]),
            _cell(item["agent"]),
            _cell(item["started_at"]),
            _cell(item["closed_at"]),
            _cell(item["close_reason"]),
            _cell(item["turns"]),
        )
        for item in items
    ]
    return _columns(rows)


def _session_block(session: Mapping[str, Any]) -> str:
    """One session, whole, as lines rather than as columns.

    A block because half of what a session row carries is a list or a
    nested object, and a column holding one is a column that wraps. The
    order is the reading order: what it was, where it ran, how it ended,
    what it recorded, and which build recorded it.
    """
    lines = [
        f"session: {_cell(session['session'])}",
        f"  device: {_cell(session['device'])}",
        f"  client: {_cell(session['client'])}",
        f"  agent: {_cell(session['agent'])}",
        f"  agents: {_names(session['agents'] or ()) or NOTHING_THERE}",
        f"  protocol: {_cell(session['protocol'])}",
        f"  started: {_cell(session['started_at'])}",
        f"  closed: {_cell(session['closed_at'])}",
        f"  duration_s: {_cell(session['duration_s'])}",
        f"  close_reason: {_cell(session['close_reason'])}",
        f"  turns: {_cell(session['turns'])}",
        f"  events: {_cell(session['events'])}",
        f"  dropped: {_cell(session['dropped'])}",
        f"  metrics: {_yes(session['metrics'])}",
        f"  text: {_yes(session['text'])}",
        f"  server_version: {_cell(session['server_version'])}",
        f"  revision: {_cell(session['revision'])}",
    ]
    return "\n".join(lines) + "\n"


def _conversation_listing(page: Mapping[str, Any]) -> str:
    """The threads this deployment recorded, one line each.

    Columns for the reason the session listing has them, and one of
    these cells is content: a title is an utterance of the thread's,
    which came out of a room and through a transcriber, so it goes
    through the same bounding as everything else here and a null one is
    the fixed placeholder rather than an empty cell.
    """
    items = page["items"]
    if not items:
        return f"{NO_CONVERSATIONS}\n"
    rows = [CONVERSATION_COLUMNS] + [
        (
            _cell(item["conversation"]),
            _cell(item["agent"]),
            _cell(item["title"]),
            _cell(item["last_active_at"]),
            _cell(item["turns"]),
        )
        for item in items
    ]
    return _columns(rows)


def _conversation_block(thread: Mapping[str, Any]) -> str:
    """One thread's header, as lines rather than as columns.

    What the dialogue underneath it is a dialogue of: which thread,
    whose it is, what it is called and the two instants that bound it.
    `incomplete` is printed only when it is true, because a thread with
    nothing lost is the ordinary case and a line saying so on every
    thread would make the one that matters harder to see.
    """
    lines = [
        f"conversation: {_cell(thread['conversation'])}",
        f"  agent: {_cell(thread['agent'])}",
        f"  title: {_cell(thread['title'])}",
        f"  created: {_cell(thread['created_at'])}",
        f"  last active: {_cell(thread['last_active_at'])}",
    ]
    if thread["incomplete"]:
        lines.append("  incomplete: yes")
    return "\n".join(lines) + "\n"


def _dialogue_blocks(page: Mapping[str, Any]) -> str:
    """What was said, oldest first, two labelled lines per turn.

    Blocks rather than columns, and that is the whole reason this is not
    a table: a column holding an utterance is a column that wraps, and a
    wrapped column is not a column. The line structure is this
    renderer's alone, which is what the bounding is for: a newline
    inside an utterance is an unprintable and is substituted, so nothing
    a room said can add a line, move the cursor or recolor a terminal.
    """
    items = page["items"]
    if not items:
        return f"{NO_DIALOGUE}\n"
    return "\n".join(
        f"{SPEAKER}: {_cell(turn['heard'])}\n{_cell(turn['agent'])}: {_cell(turn['reply'])}\n"
        for turn in items
    )


def _erasure_block(taken: Mapping[str, Any]) -> str:
    """What a deletion took, one line per table.

    Counts rather than a sentence, because the caller of a purge named a
    set by selector and cannot know what was in it. Rendered in the
    order the rows go: the sessions named, the dialogue they held, and
    the threads and checkpoints left with nothing.
    """
    return (
        "\n".join(f"{name}: {taken[name]}" for name in ERASED_COUNTS if name in taken)
        + "\n"
    )


def _cell(value: object) -> str:
    """One value in a cell or on a block line, bounded.

    Null becomes the fixed placeholder rather than an empty cell, and
    everything else is truncated and made printable before it is
    written: a tab or a newline inside a cell is an unprintable here,
    because a cell that wraps stops being a cell and a block whose line
    structure came from an answer is a block an utterance can write.
    """
    if value is None:
        return NOTHING_THERE
    return printable(str(value), CELL_LENGTH) or NOTHING_THERE


def _yes(value: object) -> str:
    """A boolean the API answered, as a word. Not through `_cell`: what
    a switch says is this client's own vocabulary, and a body that put
    something else there meets strict validation long before this."""
    return "yes" if value else "no"


def _columns(rows: Sequence[Sequence[str]]) -> str:
    """A borderless table: two spaces between columns, every column as
    wide as its widest cell, and no trailing whitespace on a line.

    The one renderer for it, because the pending listing and the session
    listing are the same shape and a second copy would be a second place
    for the gutter to change."""
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    return "".join(
        "  ".join(
            cell.ljust(width) for cell, width in zip(row, widths, strict=True)
        ).rstrip()
        + "\n"
        for row in rows
    )


def _status_block(entries: Mapping[str, Mapping[str, object]]) -> str:
    """What every configured MCP server is doing, one block each.

    A block rather than a row of columns, because two of the three
    things worth reading are lists: the tools the server published, and
    the agents that may reach it. A column holding a list is a column
    that wraps, and the pending listing's shape only works because every
    one of its fields is short.

    One function and not two: the reload answers one of these inside its
    own shape, and the reading is the act's either way, so there is
    nothing left for a second entry point to do.
    """
    if not entries:
        return f"{NOTHING_CONFIGURED}\n"
    lines: list[str] = []
    for name, entry in entries.items():
        reason = entry["reason"]
        lines.append(
            f"{printable(name)}: {entry['state']} since {printable(str(entry['since']))}"
            + (f" ({printable(str(reason))})" if reason is not None else "")
        )
        lines.append("  tools: " + (_names(entry["tools"]) or "(none)"))
        lines.append("  agents: " + (_granted(entry["grants"]) or "(none)"))
    return "\n".join(lines) + "\n"


def _granted(grants: Mapping[str, object]) -> str:
    """Which agents may reach the server, and how much of it: a bare
    name is the whole server, and a name followed by tools in
    parentheses is the allow list that agent was given. Sorted by agent
    name, so two reads of an unchanged world print the same block."""
    return ", ".join(
        f"{printable(agent)} ({allowed})" if (allowed := _names(tools)) else printable(agent)
        for agent, tools in sorted(grants.items())
    )


def _names(values: object) -> str:
    """A list of names from an answer, printed. Bounded and made
    printable one by one even though the shape it was read as has
    established they are strings: what that shape knows about them is
    their type, not their length and not whether every character in them
    can be written to a terminal. `None` is a list of nothing here, which
    is how a grant of the whole server reads."""
    return ", ".join(printable(str(value)) for value in _sequence(values))


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, str) else ()


def _prompt_listing(body: Mapping[str, Any]) -> str:
    """The assembled prompt, block by block, and its total size.

    Every block is printed whole. This command exists to show what the
    model is given, so a concealed tail is exactly what the operator
    came to see, which is why nothing here goes through `printable`:
    that renderer strips a value and cuts it at `GLIMPSE_LENGTH`, which
    is right for an acknowledgement and fatally wrong here.

    The counts printed are the ones the server reported, which count
    what is stored and sent, so a replaced character below never
    falsifies the accounting.
    """
    lines: list[str] = []
    for block in body["blocks"]:
        named = block.get("name")
        lines.append(
            f"{_block(str(block['provenance']))} ({block['characters']} characters)"
            + (
                ""
                if named is None
                else f", the server prompt named {_block(str(named))}"
            )
        )
        lines.append(_block(str(block["text"])))
        lines.append("")
    lines.append(f"total: {body['characters']} characters")
    return "\n".join(lines) + "\n"


def _block(value: str) -> str:
    """A whole block of prompt text, made safe for a terminal and
    nothing else.

    Newlines and tabs pass, because a prompt is written in them.
    Everything else unprintable is replaced rather than dropped, so an
    escape sequence cannot drive the terminal and a block that arrived
    mangled reads as mangled. Nothing is truncated, ever: this is an
    inspection command, and a renderer that quietly cut the text would
    make it lie about the one thing it exists to show.

    Applied to the provenance and to a block's name as well as to its
    text. The provenance names an entry an operator wrote; the name is a
    prompt name a server chose and an operator copied, so nothing bounds
    what it holds, and it is exactly the string a hostile server would
    put an escape sequence in.
    """
    return "".join(
        character if character.isprintable() or character in "\n\t" else "?"
        for character in value
    )


# What a reload's answer can say, read off the shapes it is declared in
#
# Three readings of `ConfigReloadResult` and its sections, all of them
# this renderer's: which sections there are, and within one section
# which fields are lists of names and which are yes-or-no answers.
# Written here rather than beside the models because printing is what
# they are for, and the models are the contract two surfaces share.


def outcomes(section: type[BaseModel]) -> tuple[str, ...]:
    """One reload section's outcome lists, in the order it declares
    them: every field that is a list of names.

    Presentation, which is why the answer is a tuple and not a set, but
    presentation of the model's own fields: read off the declaration
    rather than listed again, so an outcome added to a section is one
    line on that section and this prints it. What the rule leaves out is
    every field that is not a list of names, which today is the MCP
    status mapping and the agent-defaults flag; each of those is
    rendered where its own shape is understood.
    """
    return tuple(
        name
        for name, field in section.model_fields.items()
        if get_origin(field.annotation) is list and get_args(field.annotation) == (str,)
    )


def flags(section: type[BaseModel]) -> tuple[str, ...]:
    """One reload section's yes-or-no answers, in the order it declares
    them.

    The sibling of `outcomes` above and the other half of what a section
    can say: a kind there is one of has nothing to name, so what moved
    about it is a boolean. Read off the declaration for the same reason,
    so that a flag added to a section is a flag this prints.
    """
    return tuple(
        name for name, field in section.model_fields.items() if field.annotation is bool
    )


def _section(annotation: object) -> type[BaseModel]:
    """The model behind one section of the result, whether or not the
    section is optional. A section that is not filled yet is declared
    `Model | None`, and what a renderer needs is the model either way."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return next(
        argument
        for argument in get_args(annotation)
        if isinstance(argument, type) and issubclass(argument, BaseModel)
    )


# Which sections one reload answers with and what shape each of them
# is, read off the result rather than written down beside it: a section
# added to the model is a section this renders, and a field whose shape
# the rendering has no rule for is a failing test rather than output
# that quietly went missing.
RELOAD_SECTIONS: dict[str, type[BaseModel]] = {
    name: _section(field.annotation)
    for name, field in ConfigReloadResult.model_fields.items()
}


def _reload_listing(applied: Mapping[str, Any]) -> str:
    """What the reload applied, kind by kind, and then what is running.

    The outcomes first, because they are the answer to the question that
    was asked, and the MCP status underneath because it is the answer to
    the one that follows: an entry that started is not thereby
    connected, and the block below it says which.

    One block per section, and every section printed, including the ones
    this server does not apply yet: a section silently missing from the
    output would read as a kind with nothing to report rather than as a
    kind this build does not touch. What each section can say is read
    off its own model rather than listed here, so a section or an
    outcome added to the result is one the operator sees, and a field
    shaped like neither a list of names nor a flag is a failing test
    rather than output nobody notices is gone.

    Read as one shape, the status half included, which is what the act
    declares: the outcome lists are printed name by name and the status
    half is a document a listing renders, so a stray shape anywhere in
    here would otherwise become output or a traceback.
    """
    lines: list[str] = []
    for section, shape in RELOAD_SECTIONS.items():
        body = applied[section]
        if body is None:
            lines.append(f"{section}: {NOT_APPLIED}")
            continue
        lines.append(f"{section}:")
        lines += [
            f"  {outcome}: " + (_names(body[outcome]) or "(none)")
            for outcome in outcomes(shape)
        ]
        lines += [f"  {flag}: {'yes' if body[flag] else 'no'}" for flag in flags(shape)]
    return "\n".join(lines) + "\n\n" + _status_block(applied["mcp"]["servers"])


# What the database holds that the running server is not serving
#
# One block per kind, in the order the domain declares them, and every
# kind printed: a kind silently missing from the output would read as a
# kind with nothing pending rather than as one this read does not
# compare. What each block can say is read off its own model, so a field
# added to the comparison is a field this prints, and a field shaped
# like none of the three rules below is a failing test rather than
# output nobody notices is gone.
#
# Three shapes and no fourth. A list of names is a list of names; a
# yes-or-no is a kind there is one of, which has nothing to name; a
# nested model is one kind's answer broken into the moments a
# conversation meets each part at, and it is printed as its own indented
# block for the reason the outer ones are.


def named_lists(section: type[BaseModel]) -> tuple[str, ...]:
    """One diff section's name lists, in the order it declares them."""
    return tuple(
        name
        for name, field in section.model_fields.items()
        if get_origin(field.annotation) is tuple and get_args(field.annotation) == (str, ...)
    )


def nested(section: type[BaseModel]) -> tuple[str, ...]:
    """One diff section's own sub-sections, in the order it declares
    them: the parts of one kind that reach a conversation at different
    moments."""
    return tuple(
        name
        for name, field in section.model_fields.items()
        if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel)
    )


# Which kinds one comparison answers with and what shape each of them
# is, read off the result rather than written down beside it, exactly as
# the reload's sections are.
DIFF_SECTIONS: dict[str, type[BaseModel]] = {
    name: _section(field.annotation) for name, field in ConfigDiff.model_fields.items()
}

# What the label on a block means, said once at the head rather than
# per block: three boundaries and no fourth, and which one a kind's
# changes converge at is the answer's own.
DIFF_INTRO = (
    "# what the stored configuration would change on the running server. `applies`\n"
    "# says when a change of that kind reaches a conversation: `reload` at the next\n"
    "# reload, `check-in` as a device next asks, `restart` at the next server start."
)


def _diff_listing(body: Mapping[str, Any]) -> str:
    """The comparison, kind by kind.

    Names and labels and nothing else, which is what the shape carries:
    no bodies, no values, no masks and no secret marks cross this
    surface, so there is nothing here to filter.
    """
    lines = [DIFF_INTRO]
    for section, shape in DIFF_SECTIONS.items():
        lines += _diff_block(section, shape, body[section], "")
    return "\n".join(lines) + "\n"


def _diff_block(
    name: str, shape: type[BaseModel], body: Mapping[str, object], indent: str
) -> list[str]:
    """One kind's block, and the blocks of the parts under it."""
    lines = [f"{indent}{name}: applies at {printable(str(body['applies']))}"]
    lines += [
        f"{indent}  {listed}: " + (_names(body[listed]) or "(none)")
        for listed in named_lists(shape)
    ]
    lines += [
        f"{indent}  {flag}: {'yes' if body[flag] else 'no'}" for flag in flags(shape)
    ]
    for under in nested(shape):
        lines += _diff_block(
            under, _section(shape.model_fields[under].annotation), body[under], indent + "  "
        )
    return lines


def _identity_block(info: Mapping[str, object]) -> str:
    """What `info` prints of the server's own answer: which build is
    running, and the URL a board is onboarded at.

    Every line is made printable, like every other value an answer
    carries: what is on the other end of `--api-url` is not this
    command's to vouch for. The build's two lines are bounded as well;
    the URL and its provenance are not, and the note on `UNBOUNDED`
    above says why.

    The URL lands on a line with nothing in front of it, and its
    provenance goes on the label line above it. A terminal wraps a long
    line wherever it happens to run out, and a URL broken across two
    rows is one an operator mistypes; a label in front of it would only
    make it happen sooner. With onboarding off there is no URL at all,
    and the sentence that stands there says which switch decides it.

    Everything goes to stdout, this block included. That is not the
    stream split being bent: the whole of what `info` answers is the
    artifact, and the URL in particular must reach the one stream a
    caller can capture, never the one a terminal scrolls past.
    """
    lines = [
        "",
        f"server version: {printable(str(info['version']))}",
        f"server revision: {printable(str(info['revision']))}",
        "",
    ]
    # Asked of the flag, which is the field whose job the question is.
    # It cannot disagree with the two below it: `RuntimeInfo` refuses a
    # body where the three say different things, so this branch and the
    # value it is about are one fact rather than two that have to be
    # kept in step here.
    if not info["onboarding_enabled"]:
        return "\n".join([*lines, ONBOARDING_OFF_HERE]) + "\n"
    url = info["onboarding_url"]
    provenance = printable(str(info["onboarding_provenance"]), UNBOUNDED)
    return (
        "\n".join(
            [
                *lines,
                f"{ONBOARDING_URL_LABEL}, {provenance}:",
                printable(str(url), UNBOUNDED),
            ]
        )
        + "\n"
    )


# What a count of the masked configuration depends on, and what says so
#
# `ConfigDocument` declares the document as `dict[str, Any]` and stops
# there, deliberately: the document's shape is its own prose, and the
# entity models cannot validate an entry whose credential-bearing values
# have been replaced by the mask. So the act's answer is read as far as
# the outer mapping and no further, and everything under it is a body
# nobody has vouched for.
#
# A count needs more than that: that a section is a mapping of entries,
# that a provider section is a mapping of those, and that the default
# agent is a name or nothing. Those are read as shapes through
# `_understood`, like every other answer this module renders, so a
# section that is a number, a list or absent meets the one fixed
# sentence a body this client cannot read gets, rather than a
# `TypeError` or a `KeyError` leaving the boundary as a traceback with
# the answer inside it.
ENTRIES = dict[str, Any]

STAGED_ENTRIES = dict[str, ENTRIES]

NAMED = str | None


def _counted(section: object, kind: entities.EntityDescriptor) -> int:
    """How many entries one section of the masked document holds, read
    as the nesting the descriptor says it has.

    A kind addressed by two segments is a mapping of mappings in the
    document exactly as it is two path parameters on the API, which is
    one fact read off the registry rather than two written down.
    """
    if len(kind.addressing) > 1:
        staged = _understood(STAGED_ENTRIES, section, UNREADABLE_READ)
        return sum(len(under) for under in staged.values())
    return len(_understood(ENTRIES, section, UNREADABLE_READ))


def _configured_counts(document: Mapping[str, object]) -> str:
    """What `info` prints of the stored half: how many of each kind
    there are, and which agent an unbound board reaches.

    A count and not the tree. The question this command answers is
    orientation, so what belongs here is the shape of the deployment and
    not its contents, and `vinga list` prints the contents already.

    The kinds and their order come from the registry, so a kind added
    there is counted here by existing. A kind addressed by no segment is
    the singleton, which there is exactly one of and no count to give;
    one addressed by two is nested a level deeper, which is the same
    fact its URL states. The devices and the default agent are written
    out for the reason `_summary` writes them out: neither is an entity,
    and forcing them into a kind's shape would be inventing a
    generalization rather than finding one.

    Every section is read as a shape before it is counted, and the
    default agent before it is printed: see the note above this
    function. `.get` rather than a subscript, so a section the answer
    left out arrives as None and meets that refusal too, instead of a
    `KeyError` from outside the boundary.
    """
    config = document["config"]
    lines = ["", CONFIGURED]
    for kind in entities.ENTITIES:
        if not kind.addressing:
            continue
        lines.append(f"  {kind.moved_key}: {_counted(config.get(kind.moved_key), kind)}")
    bound = _understood(ENTRIES, config.get("devices"), UNREADABLE_READ)
    lines.append(f"  devices: {len(bound)}")
    default_agent = _understood(NAMED, config.get("default_agent"), UNREADABLE_READ)
    named = printable(default_agent) if default_agent else "(none)"
    lines.append(f"  default_agent: {named}")
    return "\n".join(lines) + "\n"


def _summary(document: Mapping[str, object]) -> str:
    """The tree `config list` prints: one line per entity, with the
    slots that hold a stored secret named but never their values.

    Rendered from the same masked document `show` prints, which is what
    a read of the whole configuration answers with, so the summary can
    say nothing the document does not carry.
    """
    config = document["config"]
    stored = _stored_slots(document["secrets"])
    lines = ["providers:"]
    for stage in PROVIDER_STAGES:
        lines.append(f"  {stage}:")
        lines += [
            f"    {name}{_summarized('provider', body)}"
            + _slots(stored, "provider", entities.provider_identity(stage, name))
            for name, body in config["providers"].get(stage, {}).items()
        ] or ["    (none)"]

    lines.append("mcp_servers:")
    lines += [
        f"  {name}{_summarized('mcp-server', body)}" + _slots(stored, "mcp_server", name)
        for name, body in config["mcp_servers"].items()
    ] or ["  (none)"]

    lines.append("prompt_fragments:")
    lines += [
        f"  {name}{_summarized('prompt-fragment', body)}"
        for name, body in config["prompt_fragments"].items()
    ] or ["  (none)"]

    lines.append("agent_defaults" + _summarized("agent-defaults", config["agent_defaults"]))

    lines.append("agents:")
    lines += [
        f"  {name}{_summarized('agent', body)}" for name, body in config["agents"].items()
    ] or ["  (none)"]

    # The two settings' lines are written here rather than summarized by
    # a descriptor: neither is an entity, a binding reads as the agents
    # it points at and the default agent is one name, and forcing them
    # into a kind's shape would be inventing a generalization rather
    # than finding one.
    lines.append("devices:")
    lines += [
        f"  {mac} -> {', '.join(bound)}" for mac, bound in config["devices"].items()
    ] or ["  (none)"]

    lines.append(f"default_agent: {config['default_agent'] or '(none)'}")
    return "\n".join(lines) + "\n"


# How one entry of each kind reads in that tree, after its name: which
# engine a provider is, how an MCP server is reached, what a fragment
# costs, what an agent overrides. Five answers to one question, so the
# tree above asks by kind rather than knowing them, and the table that
# answers is at the foot of this group: it is read here and written here,
# which is the whole of what a per-kind mapping has to be.


def _summarized(kind: str, body: Mapping[str, object]) -> str:
    return _SUMMARY[kind](body)


def _provider_summary(body: Mapping[str, object]) -> str:
    """Its type, which is what a provider is: everything else in the
    entry is options for that type."""
    return f" ({body.get('type')})"


def _mcp_server_summary(body: Mapping[str, object]) -> str:
    return f" ({body.get('transport')})"


def _prompt_fragment_summary(body: Mapping[str, object]) -> str:
    """The size rather than the text: this is the tree, and what an
    operator reads it for is which fragments exist and what each of them
    costs the prompt budget. `prompt-fragment show` prints one whole,
    and `agent preview <name>` prints what an agent adds up to."""
    return f" ({len(str(body.get('text', '')))} characters)"


def _agent_summary(body: Mapping[str, object]) -> str:
    """What the agent overrides, which is its body without the prompt:
    that is what the line has room for, and `agent show` is where the
    prompt is read."""
    layer = {key: value for key, value in body.items() if key != "prompt"}
    return f": {_inline(layer)}" if layer else ""


def _agent_defaults_summary(body: Mapping[str, object]) -> str:
    """The singleton, which has no name of its own on the line, so what
    follows the section's own name is all of it. Empty is a state worth
    printing: it means every agent has to name everything itself."""
    return f": {_inline(body) or '(none)'}"


_SUMMARY: dict[str, Callable[[Mapping[str, object]], str]] = {
    "provider": _provider_summary,
    "mcp-server": _mcp_server_summary,
    "prompt-fragment": _prompt_fragment_summary,
    "agent": _agent_summary,
    "agent-defaults": _agent_defaults_summary,
}


def _stored_slots(secrets: Sequence[Mapping[str, object]]) -> dict[tuple[str, str], list[str]]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for stored in secrets:
        grouped.setdefault((stored["kind"], stored["identity"]), []).append(stored["slot"])
    return grouped


def _slots(stored: Mapping[tuple[str, str], list[str]], kind: str, identity: str) -> str:
    slots = stored.get((kind, identity), [])
    return f"  [secrets: {', '.join(slots)}]" if slots else ""


def _inline(data: Mapping[str, object]) -> str:
    return " ".join(f"{key}={_short(value)}" for key, value in data.items())


def _short(value: object) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(str(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{...}"
    return str(value)


def _yaml(data: object) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


# Input


# Reading YAML
#
# The one place this CLI calls a parser, and therefore the one place a
# value nobody has validated meets a library whose business is to
# describe what it could not read. Both ways a YAML value reaches this
# group go through it: a fragment or a document read from a file or
# from stdin, and one inline value written beside a key.
#
# What a caller is told is fixed, plus at most the two integers saying
# where the parser stopped. Never the parser's own words: `problem`
# names the tag or the key it choked on, and `!<credential> value` is a
# document PyYAML answers by quoting the credential back. That leak was
# in the `-f` write from the beginning and is closed here for both
# callers at once, because there is one boundary now rather than two
# that happened to agree.
#
# What is caught is wider than `YAMLError`, which is the other half of
# the same fix. The constructors that turn a scalar into a Python value
# raise the ordinary exceptions when the scalar is out of range: an
# integer of five thousand digits leaves as CPython's own `ValueError`
# about the digit limit, an impossible date leaves as `ValueError` from
# `datetime`, and two thousand nested lists leave as `RecursionError`
# out of the composer. None of them is a `YAMLError`, so all three used
# to reach an operator as a traceback with the source in it.
_UNPARSEABLE = (yaml.YAMLError, ValueError, ArithmeticError, RecursionError)

YAML_NOT_QUOTED = (
    "Nothing of what it holds is quoted back: a source that will not parse is one "
    "nothing here has validated, and what a parser says about one repeats the tag or "
    "the key it stopped on"
)

# What the inline form calls the thing it could not read. Its own
# source name rather than a path, because there is no file: the value
# is one argument, and where the parser stopped is a column inside it.
PAIR_SOURCE = "an inline field's value"


def _parsed_yaml(text: str, source: str) -> object:
    """One YAML source read, or the fixed sentence for one that will
    not read.

    Recorded inside the handler and raised after it, the rule this
    module raises by: a PyYAML mark holds the whole buffer it was
    parsing, and an exception raised inside a handler keeps the one
    being handled as its `__context__` for anything walking the chain to
    find. What survives the arm is a string built from `source`, which
    is the caller's, and two integers.
    """
    problem: str | None = None
    parsed: object = None
    try:
        parsed = yaml.safe_load(text)
    except _UNPARSEABLE as exc:
        problem = f"invalid YAML in {source}{_stopped_at(exc)}. {YAML_NOT_QUOTED}"
    if problem is not None:
        raise ConfigError(problem)
    return parsed


def _stopped_at(exc: BaseException) -> str:
    """Where the parser stopped, when it says: two integers off the
    exception's mark and nothing else off the exception at all. Empty
    for the failures that carry no mark, which is every one of the
    non-YAML family above."""
    if not isinstance(exc, yaml.MarkedYAMLError) or exc.problem_mark is None:
        return ""
    mark = exc.problem_mark
    return f" at line {mark.line + 1}, column {mark.column + 1}"


# What a source that will not parse is called. Two names of this
# module's own, and neither is the path: `-f` takes one file, so the
# path adds nothing an operator does not have on the line they just
# typed, and a path is typed, which makes it the last place a refusal
# may repeat (#289). Where the parser stopped is a line and a column,
# which is what locates the mistake inside the file.
FILE_SOURCE = "the fragment file"

STDIN_SOURCE = "the fragment on stdin"


def _fragment(path: str) -> object:
    """One entity's YAML fragment or one whole document, from a file or
    from stdin. Parsed here and validated by the models in the
    repository, which is where the rule that a secret-bearing key may
    only name an environment variable already lives."""
    source = STDIN_SOURCE if path == "-" else FILE_SOURCE
    return _parsed_yaml(_piped() if path == "-" else _file(path), source)


# Inline values
#
# `set <kind> [identity] key=value...` assembles the fragment the YAML
# would, and nothing else: the pairs become a mapping, dotted keys nest,
# and what comes out enters the exact path a `-f` fragment enters, so
# the same check, the same request and the same acknowledgement follow.
#
# The parser is a no-leak boundary of its own, built the way `_fragment`
# is and, since the two share `_parsed_yaml` above, out of the same
# boundary: a value typed beside a key is where a paste lands, so every
# refusal below is a fixed sentence naming what was wrong with the shape
# and never what was written, and each is raised after its arm rather
# than inside it, so no exception chain carries the string that was
# being parsed.
#
# A value is held to one scalar. `yaml.safe_load` will happily read
# `[a, b]` or `{a: 1}` out of one argument, and the contract is that an
# inline value is a scalar: a structure belongs in a fragment, where it
# can be read.

PAIR_NEEDS_EQUALS = (
    "an inline field is written key=value, and one of these arguments has no =. "
    "Nothing typed is quoted back"
)

PAIR_EMPTY_KEY = (
    "an inline field's key is empty, or has an empty segment between two dots: write "
    "a.b rather than .a, a. or a..b. Nothing typed is quoted back"
)

PAIR_DUPLICATE_KEY = (
    "an inline field's key is given twice, and one write says one thing about a key. "
    "Nothing typed is quoted back"
)

PAIR_NESTED_KEY = (
    "one inline field's key nests inside another's, such as a.b beside a, which says "
    "two things about the same place. Nothing typed is quoted back"
)

PAIR_NOT_SCALAR = (
    "an inline field's value reads as a list or a mapping, and an inline value has to "
    "be one scalar; write a structure as a fragment with -f. Nothing typed is quoted "
    "back"
)

# The two ways of writing an entity are alternatives, so neither and
# both are each a mistake in the grammar. Neither is the missing
# argument Click cannot see, because either of the two satisfies it.
BOTH_INPUTS = (
    "a write takes either -f with a YAML fragment or key=value arguments, and this "
    "command was given both"
)


def _written_entity(args: Invocation) -> object:
    """The entity a write sends, from whichever of the two ways of
    writing one this command was given."""
    if args.file and args.pairs:
        raise ConfigError(usage_line(BOTH_INPUTS))
    if args.file:
        return _fragment(args.file)
    if args.pairs:
        return _pairs(args.pairs)
    raise ConfigError(usage_line(MISSING_ARGUMENT))


def _pairs(written: Sequence[str]) -> dict[str, object]:
    """Inline `key=value` arguments as the mapping they assemble.

    Split on the FIRST `=`, so a value holding one is a value: `=` is
    the separator and not a character the value may not contain. The
    keys are read and checked against each other before any value is
    parsed, because a key that is written twice or written inside
    another is a mistake about the whole set rather than about one pair.
    """
    keys = [_pair_key(pair) for pair in written]
    _distinct(keys)
    assembled: dict[str, object] = {}
    for key, pair in zip(keys, written, strict=True):
        _nest(assembled, key, _scalar(pair.partition("=")[2]))
    return assembled


def _pair_key(pair: str) -> tuple[str, ...]:
    """One pair's key, as the segments its dots name."""
    key, equals, _ = pair.partition("=")
    if not equals:
        raise ConfigError(PAIR_NEEDS_EQUALS)
    segments = tuple(key.split("."))
    if not all(segments):
        raise ConfigError(PAIR_EMPTY_KEY)
    return segments


def _distinct(keys: Sequence[tuple[str, ...]]) -> None:
    """No key written twice, and no key written inside another.

    The second is the one worth saying out loud: `a.b=1 a=2` asks for a
    mapping and a scalar at one place, and whichever of them a parser
    happened to apply last would be an answer the operator did not
    choose.
    """
    for position, key in enumerate(keys):
        for other in keys[position + 1 :]:
            if key == other:
                raise ConfigError(PAIR_DUPLICATE_KEY)
            if key[: len(other)] == other or other[: len(key)] == key:
                raise ConfigError(PAIR_NESTED_KEY)


def _scalar(value: str) -> object:
    """One pair's value, read as YAML reads it and held to a scalar.

    Read through the same boundary a fragment is read through, so that
    the two ways of writing an entity meet one sentence for a source
    that will not parse and one set of failures is caught for both:
    everything PyYAML raises rather than the documented `YAMLError`
    alone, which for one argument matters as much as for a file, since
    an integer of five thousand digits fits on a command line.

    What is not refused here is everything JSON cannot carry, which a
    scalar can still be: a bare date, `.nan`. Those meet
    `check_transportable`'s own sentence a step later, which is where
    that rule lives for a fragment too.
    """
    parsed = _parsed_yaml(value, PAIR_SOURCE)
    if isinstance(parsed, (Mapping, list, tuple, set, frozenset)):
        raise ConfigError(PAIR_NOT_SCALAR)
    return parsed


def _nest(under: dict[str, object], key: Sequence[str], value: object) -> None:
    """One dotted key's value, put where its dots nest it, making the
    mappings on the way.

    Nothing on the way can be anything but a mapping this made or a
    place nothing has written yet, because `_distinct` has already
    refused a key that nests inside another.
    """
    head, rest = key[0], key[1:]
    if not rest:
        under[head] = value
        return
    below = under.setdefault(head, {})
    if not isinstance(below, dict):  # pragma: no cover - _distinct rules it out
        raise ConfigError(PAIR_NESTED_KEY)
    _nest(below, rest, value)


# What a fragment file that cannot be read says. One fixed sentence per
# failure, and none of them holds the path, the operating system's
# wording or a byte of the file (#289).
#
# The path is typed, and this CLI's whole no-leak posture is about what
# was typed: a fragment lives next to the deployment it configures, and
# `-f` is one option away from the secret paths. The library's own
# `strerror` is not passed through for the reason Click's sentences are
# not: a message this code did not write is a message it cannot promise
# carries no value.
FILE_NOT_FOUND = (
    "there is no file at the path -f names. It is not quoted back: a refusal here "
    "names the rule rather than what was typed"
)

FILE_NOT_READABLE = (
    "the file -f names cannot be read: check that it is a file this user may read, "
    "rather than a directory or one belonging to somebody else. Neither the path nor "
    "the system's own wording is quoted back"
)

FILE_NOT_TEXT = (
    "the file -f names is not UTF-8 text, so there is no YAML in it to read. Nothing "
    "it holds is quoted back, and nothing of it is decoded far enough to be: a file "
    "that fails to decode is as likely to be a key or an archive as a mistyped "
    "fragment"
)

FILE_UNREADABLE = (
    "the file -f names could not be read. Neither the path nor the system's own "
    "wording is quoted back"
)

# Ordered, first match wins, and a subclass comes before the class it
# extends. The decoding family is here because `UnicodeDecodeError` is a
# `ValueError` rather than an `OSError`: the read succeeds and the
# decoding is what fails, which is why it used to leave as a traceback,
# and the exception it leaves as holds the buffer it could not decode.
# Caught as the whole family, which is what `docgen`'s reader of the
# example fragments catches for the same reason.
_FILE_PROBLEMS: tuple[tuple[type[BaseException], str], ...] = (
    (FileNotFoundError, FILE_NOT_FOUND),
    (NotADirectoryError, FILE_NOT_FOUND),
    (IsADirectoryError, FILE_NOT_READABLE),
    (PermissionError, FILE_NOT_READABLE),
    (UnicodeError, FILE_NOT_TEXT),
    (OSError, FILE_UNREADABLE),
)

# What the arm catches, read off the table rather than written beside
# it: a shape the table answers and the arm does not catch is a
# traceback, which is the half of #289 that was not about echoing.
_FILE_FAILURES = tuple(shape for shape, _ in _FILE_PROBLEMS)


def _file(path: str) -> str:
    """One fragment file's text, or the fixed sentence for a file that
    will not give any.

    The sentence is chosen by the class of the failure, which is the
    reading that cannot be fooled by wording, and is raised after the
    arm rather than inside it: the exception being handled holds the
    path and, for a file that will not decode, the bytes it was
    decoding, and an exception raised inside a handler keeps that one on
    its `__context__` for anything walking the chain to find.
    """
    problem: str | None = None
    try:
        return Path(path).read_text(encoding="utf-8")
    except _FILE_FAILURES as exc:
        problem = next(
            sentence for shape, sentence in _FILE_PROBLEMS if isinstance(exc, shape)
        )
    raise ConfigError(problem)


# What `-f -` says when it is run at a terminal with nothing piped in.
#
# It used to block: the read is unconditional, so a person who typed
# `apply -f -` at a prompt met a cursor and no explanation, which is the
# same rule as the secret prompt broken from the other side, by never
# asking whether there is anybody there. The published answer is to quit
# and point at the help, and this grammar's shape for that is one
# sentence with the usage tail every other mistake in it carries.
STDIN_AT_A_TERMINAL = (
    "-f - reads from standard input, and standard input here is a terminal with "
    "nothing piped into it. Pipe the document in, or name a file with -f"
)


def _stdin() -> str:
    """Standard input, read whole."""
    return sys.stdin.read()


def _piped() -> str:
    """The document `-f -` names, or the sentence for a terminal with
    nothing piped into it.

    The one place this grammar asks whether there is anybody there
    before reading, and it is the document path alone. A credential's
    read asks the same question the other way round, by prompting when
    there is somebody and reading plainly when there is not, so it
    already has an answer for a terminal and does not want this one.
    """
    if sys.stdin is not None and sys.stdin.isatty():
        raise ConfigError(usage_line(STDIN_AT_A_TERMINAL))
    return _stdin()


# What a `--from-env` naming nothing says, and what it deliberately does
# not say: which name it was given (#289). A variable name is typed on
# the command line, and the mistake that produces this refusal most
# often is typing the secret itself where the name belongs, which is the
# one value this whole command exists never to see. The rule is named
# instead, since that is what tells an operator what to look at.
FROM_ENV_NOT_SET = (
    "--from-env names a variable that is not set in this environment, or is set to an "
    "empty value. The name is not quoted back: what follows --from-env is typed, and "
    "typing the secret there instead of the variable holding it is the mistake this "
    "refusal meets most. Check the spelling, and that the variable is exported"
)


# What a destructive verb asks, and the two sentences it answers with
#
# Every one of the three is a fixed constant carrying no address and no
# other value from the command line. That is a real usability cost, paid
# knowingly: a prompt that cannot say which entry it means is worse to
# read than one that can. It is paid because the address is built from
# `stage`, `name`, `mac`, `code` and `slot`, all typed, and a mistyped
# command is exactly where a credential lands in an address field, which
# is the mistake these sentences exist not to repeat. The answer to
# "which entry is this" is a `show` before the delete, not a sentence
# that quotes back what was typed.
#
# The question goes to stderr rather than to stdout, which is where
# every other thing about a run goes and what keeps `> file` clean; it
# is asked only when stdin is a terminal, so the non-terminal path is
# complete without it.
CONFIRMATION = (
    "This deletes what the command addresses, and nothing in this grammar puts it "
    "back: an export taken beforehand is the only copy. Type y to go ahead: "
)

DECLINED = "nothing was deleted, because the confirmation was not answered with y"

# And what a terminal that cannot be read says. The question is asked
# and the answer never arrives: a stream that has gone, or bytes the
# terminal's encoding will not decode, which is an ordinary thing for a
# pasted answer to be. Neither is quoted, and the decoding failure is
# the reason: what it retains is the bytes it could not read, which came
# off a terminal an operator is typing a delete into.
CONFIRMATION_UNREADABLE = (
    "the confirmation could not be read from this terminal, so nothing was deleted. "
    "What could not be read is not repeated here. Run it again, or run it with "
    "--force, which answers the question without asking it"
)

NO_INPUT_REFUSED = (
    "a destructive command asks for a confirmation at a terminal, and --no-input "
    "disables every prompt. Run it again with --force, which answers the question "
    "this would have asked"
)


def _permitted_to_destroy(args: Invocation) -> None:
    """Whether a destructive verb may go ahead, asked at a terminal.

    Five answers and one rule under them: never block a pipe, and never
    take the only door away. `--force` answers the question, so it
    proceeds whatever else was given; `--no-input` takes the asking away
    and refuses, because a confirmation has no second way to be
    answered, which is exactly why a secret set is not refused by the
    same flag: a secret has three doors and disabling one leaves two.
    A stream that is not a terminal has nobody to ask, so it proceeds.
    """
    if args.force:
        return
    if sys.stdin is None or not sys.stdin.isatty():
        return
    if args.no_input:
        raise ConfigError(NO_INPUT_REFUSED)
    print(CONFIRMATION, end="", file=sys.stderr, flush=True)
    if _answered().strip().lower() != "y":
        raise ConfigError(DECLINED)


# What an interactive read can fail as, and the shape every one of them
# is made behind.
#
# Three reads in this grammar ask a person for something: the
# confirmation before a destructive verb, the no-echo prompt a secret is
# typed at, and the plain read of a piped one. Each of them can fail in
# ways no argument of theirs decides. `EOFError` is what a prompt raises
# when the stream ends under it, and it is not an `OSError`; a stream
# that has gone is an `OSError`; bytes the encoding will not decode
# leave as a `UnicodeError`, which is a `ValueError` and not an
# `OSError` at all. An arm catching one family lets the other two out.
#
# What they carry is why they are caught rather than merely handled: a
# decoding failure holds the bytes it could not read, and those bytes
# came off a terminal somebody was typing a credential or a delete into.
# So the sentence is built inside the handler and raised after it, and
# nothing walking the chain finds the failure, or what it held, behind
# the refusal.
_INPUT_FAILURES = (EOFError, OSError, UnicodeError, ValueError)


def _read_from(reader: Callable[[], str], problem: str) -> str:
    """One interactive read, or this grammar's own sentence for a stream
    that would not give one.

    One shape for the three, because they differ only in the sentence:
    what a caller cannot supply is the boundary, and three copies of it
    would be three chances to catch two families out of three.
    """
    failed: str | None = None
    try:
        return reader()
    except _INPUT_FAILURES:
        failed = problem
    raise ConfigError(failed)


def _answered() -> str:
    """What was typed at the confirmation, or the sentence for a
    terminal that would not give it.

    The one read in this grammar that happens after something has
    already been printed, which is the whole of what makes its sentence
    its own rather than the secret read's.
    """
    return _read_from(sys.stdin.readline, CONFIRMATION_UNREADABLE)


# What an empty secret says. Named rather than written at its raise
# site, because two paths answer with it now: a read that gave nothing,
# and a terminal that was never read because prompting was disabled.
# What a secret that could not be read at all says. Distinct from the
# empty one, because they are different facts about a run: an empty
# secret is a stream that answered with nothing, and this is a stream
# that did not answer. Neither says what it held.
SECRET_UNREADABLE = (
    "the secret could not be read from this terminal, and nothing was stored. What "
    "could not be read is not repeated here, and neither is what the system said "
    "about it. Pipe the value in, or name the variable holding it with --from-env"
)

SECRET_EMPTY = (
    "the secret is empty; pipe it in, type it at the prompt, or name the "
    "variable holding it with --from-env"
)


def _read_secret(args: Invocation) -> str:
    """The secret itself, from a named environment variable or from
    stdin. Never from an argument: arguments land in shell history and
    in the process list. An interactive terminal is read without echo;
    a pipe or a redirect is read plainly, which is what scripts use.

    `--no-input` does not refuse: what that flag disables is prompting,
    and a value is still reachable two other ways. What it does at a
    terminal is answer immediately rather than read, and that is not a
    third answer but the same one arrived at without hanging first. A
    terminal is where somebody types, so a terminal with the typing
    disabled has nothing in it: reading one waits for an end-of-file
    only a person can send, which is the block `-f -` used to have and
    the thing the whole prompt rule is about. The value such a read
    would yield is the empty one, and this is that answer without the
    wait.

    A destructive verb is refused by the same flag rather than answered,
    because its confirmation has no other way to be given.
    """
    if args.from_env:
        secret = os.environ.get(args.from_env, "")
        if not secret:
            raise ConfigError(FROM_ENV_NOT_SET)
        return secret

    at_a_terminal = sys.stdin is not None and sys.stdin.isatty()
    if at_a_terminal and args.no_input:
        raise ConfigError(SECRET_EMPTY)
    if at_a_terminal:
        secret = _read_from(
            lambda: getpass.getpass("Secret (not echoed): "), SECRET_UNREADABLE
        )
    else:
        secret = _read_from(_stdin, SECRET_UNREADABLE)
    # The trailing newline is the shell's, not the secret's.
    secret = secret.rstrip("\r\n")
    if not secret:
        raise ConfigError(SECRET_EMPTY)
    return secret


# Output


def _applied(answer: Mapping[str, object]) -> None:
    """One staged document read out: what each entry did, and then the
    boundaries the ones that were written are waiting on.

    The rendering of `--no-reload`, which is the invocation that leaves
    a write waiting: nothing in this command installs it, so the
    boundaries are what the operator has to be told about.

    One line per entry on stdout, in the order the answer lists them,
    which is the configuration's own section order. The notices go to
    stderr the way a single write's does, and each distinct one once: a
    document that wrote nine entities is waiting on one reload, not on
    nine, and printing the sentence nine times would say otherwise.
    """
    for notice in _applied_entries(answer):
        print(notice, file=sys.stderr)


def _applied_quietly(answer: Mapping[str, object]) -> None:
    """The same document read out with its boundaries left off, which is
    what a default `apply` prints.

    The reload runs behind this rendering and prints what it applied, so
    a notice saying to run one would be telling the operator to run the
    command whose answer is on the next line. What is dropped is only
    the notice: the outcome per entry is the answer to what was asked,
    and it is printed either way.
    """
    _applied_entries(answer)


def _applied_entries(answer: Mapping[str, object]) -> tuple[str, ...]:
    """What an applied document did, entry by entry, and the distinct
    boundaries the entries that were written are waiting on.

    The half both renderings share, printed here rather than returned,
    so the two of them differ in exactly the thing they are named for.

    A document that named nothing has one line and no boundaries, which
    is the same shape rather than a second one: it leaves through the
    same flush, and an empty answer has nothing to be waiting on.
    """
    entries = answer["entries"]
    for entry in entries:
        print(f"{_entry_name(entry)}: {entry['outcome']}")
    if not entries:
        print(NOTHING_APPLIED)
    # Flushed here rather than by the caller, so whatever follows on
    # stderr lands after the lines it is about rather than ahead of
    # them: stderr is unbuffered and stdout is not. That is a notice
    # under `--no-reload` and a refusal from the reload under the
    # default, and both have to arrive underneath what was written.
    #
    # On both arms, which is what the early return used to miss: a
    # document that named nothing still printed a line, and a reload
    # refused behind it still lands on stderr, so the one output an
    # empty apply has would have been read after the failure it came
    # before.
    sys.stdout.flush()
    return tuple(
        dict.fromkeys(
            # Whole, for the reason a prompt and the onboarding URL are
            # printed whole: a boundary sentence cut at a bound would
            # lose the command it ends with, which is the half an
            # operator acts on. What the bound is never for is the other
            # half of this function, which has no exceptions: nothing an
            # answer carries steers a terminal.
            printable(str(entry["notice"]), UNBOUNDED)
            for entry in entries
            if entry["notice"] is not None
        )
    )


def _entry_name(entry: Mapping[str, object]) -> str:
    """Where one applied entry is, as an operator reads their own
    document: the section, and the identity under it where the section
    holds entries rather than one thing.

    Both halves go through `printable` even though one of them is a
    closed token, because this line is far-side text on stdout: an
    identity is an operator's own name for a row as the store holds it,
    and a body that put an escape sequence or a lone surrogate in one
    would otherwise steer a terminal or raise a `UnicodeEncodeError`
    past the boundary that turns a failure into a sentence.
    """
    section, identity = printable(str(entry["section"])), printable(str(entry["identity"]))
    return f"{section}.{identity}" if identity else section


def _acknowledged(acknowledgement: Mapping[str, object]) -> None:
    """One write acknowledged: what it did, and when it takes effect.

    Both are the API's own words, carried through unchanged: what an act
    did and which boundary it lands at are decided where the write
    happens, and this is where they are read out.
    """
    print(f"wrote {acknowledgement['wrote']}")
    # Flushed first, so the notice lands after the line it is about
    # rather than ahead of it: stderr is unbuffered and stdout is not.
    sys.stdout.flush()
    print(acknowledgement["notice"], file=sys.stderr)


# The acts
#
# One row per thing a command does: where the act is on the API and how
# this command's arguments address it, what it sends, what it is
# answered with, and how that answer is printed. The dispatcher below is
# the only reader of a row.
#
# The five commanded kinds' rows are built rather than written out.
# Where a kind is on the API, what addresses one entry of it and which
# section it occupies in the configuration document are data on its
# descriptor, and the builders below read them straight off it.
#
# What is written entirely by hand is what a descriptor does not
# describe at all: the devices and the default agent are settings
# written with their own verbs, and the secret slots are addressed under
# an entity rather than as one.


@dataclass(frozen=True, kw_only=True)
class Act:
    """One thing a `vinga-server config` command does."""

    # The request: the verb, the path this command's arguments address,
    # and the body it carries, where it carries one.
    method: str
    path: Callable[[Invocation], str]
    # The query arguments this request carries, where it carries any.
    # Apart from `path` on purpose: what identifies an operation is the
    # path the document is written in, and a filter or a selector is not
    # part of that identity. It is also what keeps the operator's own
    # query string, which can be a credential, from being re-encoded
    # alongside arguments this command built itself.
    query: Callable[[Invocation], dict[str, str]] | None = None
    body: Callable[[Invocation], object] | None = None

    # How long this one endpoint may take to answer. Every act but the
    # reload and the apply takes the default, whose bound is the
    # database's; None is no bound, which is one act's answer.
    read_timeout_s: float | None = READ_TIMEOUT_S

    # The shape the API declares for the body this act sends, or None
    # where it sends none. Declared and never validated against: a
    # fragment is the operator's YAML and the server is what refuses a
    # bad one, so a second refusal here would be a second encoding of
    # the same rule. What reads it is the contract check, which holds
    # every act's request against the committed document, and #287's
    # generator after it. A method and a path alone leave exactly the
    # four bodies with adapters in front of them free to drift.
    sends: object | None = None

    # The shape the API says it answers this act with, and the sentence
    # a body that is not one meets. Required, because every act has an
    # answer: a shape known only inside a renderer is a fact with no
    # home, and one nothing outside the closure can read is a contract
    # no test can hold to the document.
    answers: object
    refusal: str = UNREADABLE_READ

    # What is printed, given the answer.
    render: Callable[[Any], None]

    # What this act adds to its refusal when it is not the first act of
    # its command, which is to say when something before it has already
    # changed the deployment. None for every act that either changes
    # nothing or runs alone, which is all but one of them: see
    # `APPLY_UNANSWERED`, the sentence `apply`'s reload carries.
    #
    # On the act rather than at the boundary because it is a fact about
    # what this act follows: the same reload run by `reload` itself
    # follows nothing and has nothing extra to say.
    unanswered: str | None = None

    def read(self, answer: object) -> Any:
        """One answer, read as the shape this act says it is sent.

        On the act rather than in the renderer that used to do it, which
        is what makes the shape inspectable from the row: the same fact
        the contract check compares against the document is the one the
        command validates with, so the two cannot come apart.
        """
        return _understood(self.answers, answer, self.refusal)


def _act(args: Invocation, act: Act, reached: Reached) -> None:
    """One act: one request, and its answer printed.

    The acknowledgement and the notice are the API's, read as the shape
    the act says it is answered with and handed to the act's renderer.

    `reached` is handed in rather than resolved here, so that the acts
    of one command all go to one place and a command that says where it
    is going says it truly: see `Reached`.
    """
    answer = _call(
        reached,
        act.method,
        act.path(args),
        act.body(args) if act.body is not None else _NOTHING,
        read_timeout_s=act.read_timeout_s,
        query=act.query(args) if act.query is not None else {},
    )
    act.render(act.read(answer))


def _performed(args: Invocation, acts: "tuple[Act, ...]", reached: Reached) -> None:
    """One invocation's acts, in the order it makes them, stopping at
    the first that is refused.

    Stopping is what makes a sequence honest about what ran: a refused
    `apply` never reaches the reload behind it, because there is
    nothing to install and the refusal is the whole answer.

    An act that failed behind an act that already changed something
    answers with its own refusal and then with what its row says is now
    unknown (`Act.unanswered`). The sentence is built inside the
    handler and raised outside it, the way every boundary in this
    module raises: an exception raised while another is being handled
    carries that one on `__context__` for a chain walker to find, and
    what a refusal quotes is this module's own words rather than
    whatever the failure was carrying.
    """
    problem: str | None = None
    for position, act in enumerate(acts):
        try:
            _act(args, act, reached)
            continue
        except ConfigError as refused:
            problem = str(refused)
            if position and act.unanswered is not None:
                problem = f"{problem}\n{act.unanswered}"
        break
    if problem is not None:
        raise ConfigError(problem)


def _contacted(args: Invocation, reached: Reached) -> None:
    """The banner, and the address this CLI is about to contact.

    What `info` knows before it has asked anything, and the half of its
    answer no server can supply: which server it is talking to. The
    device-facing origin the answer carries and the address an operator
    dialled can legitimately differ, so printing one as though it were
    the other would answer the question wrongly rather than not at all.

    `Address.shown` and never `args.api_url`. The transport policy
    refuses a credential in a URL's userinfo and says nothing about its
    query string, so an accepted address can still hold `?token=...`;
    the display form is the one with that taken out, bounded and made
    printable, and it is the only form anything here may name (#290).

    The address is the one this invocation resolved, handed in rather
    than resolved again here, so the line names where the requests after
    it actually go rather than where a second resolution would have gone
    (`Reached`). Flushed for the reason `_acknowledged` flushes: stderr
    is unbuffered and stdout is not, so a refusal from the first act
    would otherwise land above the lines it followed.
    """
    print(BANNER)
    print(f"{CONTACTED}: {reached.address.shown}")
    sys.stdout.flush()


def _identity(descriptor: entities.EntityDescriptor, args: Invocation) -> tuple[str, ...]:
    """What addresses one entry of this kind, taken off the command
    line. The descriptor's parameters are the URL's path parameters and
    the CLI's positional arguments, which are the same names for the
    same reason, so a provider's two are read the way every other kind's
    one is."""
    return tuple(getattr(args, parameter) for parameter in descriptor.addressing)


def _entity_path(
    descriptor: entities.EntityDescriptor, *under: str
) -> Callable[[Invocation], str]:
    """Where one entry of this kind is, and what is addressed under it."""

    def path(args: Invocation) -> str:
        return _path(descriptor.route.lstrip("/"), *_identity(descriptor, args), *under)

    return path


def _fragment_body(
    descriptor: entities.EntityDescriptor,
) -> Callable[[Invocation], object]:
    """The entity a write of this kind carries, from a YAML fragment or
    from inline `key=value` arguments, refused before it travels if JSON
    has no way to say what YAML said.

    One body for both ways of writing one, which is the whole of what
    the inline form is: the pairs assemble the mapping the fragment
    would have held, and everything after that is the same.

    Where it is being written is named as the kind's own section and no
    further. The addressed form (`providers.<stage>.<name>`) used to be
    built here, out of the stage and the name this command line carried,
    and a refusal is exactly where those must not be said: the mistake
    that reaches this one is a value nothing has validated, typed where
    an identity or a credential goes.
    """

    def body(args: Invocation) -> object:
        fragment = _written_entity(args)
        check_transportable(descriptor.moved_key, fragment)
        return fragment

    return body


SET_ENTITY: dict[str, Act] = {
    kind.name: Act(
        method="PUT",
        path=_entity_path(kind),
        body=_fragment_body(kind),
        sends=kind.model,
        answers=Acknowledgement,
        refusal=UNREADABLE_WRITE,
        render=_acknowledged,
    )
    for kind in entities.ENTITIES
}

# The singleton has no delete anywhere, and says so by carrying
# `has_delete=False` rather than by being named as an exception here.
DELETE_ENTITY: dict[str, Act] = {
    kind.name: Act(
        method="DELETE",
        path=_entity_path(kind),
        answers=Acknowledgement,
        refusal=UNREADABLE_WRITE,
        render=_acknowledged,
    )
    for kind in entities.ENTITIES
    if kind.has_delete
}

SHOW_ENTITY: dict[str, Act] = {
    kind.name: Act(
        method="GET",
        path=_entity_path(kind),
        answers=Envelope,
        render=_print_entity,
    )
    for kind in entities.ENTITIES
}


# A device binding and the default agent are domain-level fields written
# with their own verbs (bind, claim, delete, set, clear) rather than from
# a fragment, so their rows are written here rather than built from a
# kind's descriptor.


def _device_path(args: Invocation) -> str:
    return _path("devices", args.mac)


def _binding(args: Invocation) -> object:
    return {"agents": list(args.agents)}


def _claim_path(args: Invocation) -> str:
    return _path("devices", "pending", args.code)


def _waiting_path(args: Invocation) -> str:
    return _path("devices", "pending")


def _default_agent_path(args: Invocation) -> str:
    return _path("default-agent")


def _default_agent_name(args: Invocation) -> object:
    return {"name": args.name}


DELETE_DEVICE = Act(
    method="DELETE",
    path=_device_path,
    answers=Acknowledgement,
    refusal=UNREADABLE_WRITE,
    render=_acknowledged,
)

SHOW_DEVICE = Act(
    method="GET",
    path=_device_path,
    answers=Envelope,
    render=_print_entity,
)

BIND_DEVICE = Act(
    method="PUT",
    path=_device_path,
    body=_binding,
    sends=DeviceBinding,
    answers=Acknowledgement,
    refusal=UNREADABLE_WRITE,
    render=_acknowledged,
)

# The same binding, addressed by the six digits on a board's screen
# instead of by a MAC nobody has had to find.
ADD_DEVICE = Act(
    method="POST",
    path=_claim_path,
    body=_binding,
    sends=DeviceBinding,
    answers=Acknowledgement,
    refusal=UNREADABLE_WRITE,
    render=_acknowledged,
)

SET_DEFAULT_AGENT = Act(
    method="PUT",
    path=_default_agent_path,
    body=_default_agent_name,
    sends=DefaultAgentName,
    answers=Acknowledgement,
    refusal=UNREADABLE_WRITE,
    render=_acknowledged,
)

CLEAR_DEFAULT_AGENT = Act(
    method="DELETE",
    path=_default_agent_path,
    answers=Acknowledgement,
    refusal=UNREADABLE_WRITE,
    render=_acknowledged,
)


# A stored credential is addressed under the entity that holds it, in
# the slot it fills, which is why these two rows are not an entity's.
# One command covers both kinds, and which sentence follows the entity a
# credential is stored on is the API's answer: it has four secret
# routes, each statically one of them.


def _secret_body(args: Invocation) -> object:
    return {"secret": _read_secret(args)}


SET_SECRET = Act(
    method="PUT",
    path=_secret_path,
    body=_secret_body,
    sends=SecretValue,
    answers=Acknowledgement,
    refusal=UNREADABLE_WRITE,
    render=_acknowledged,
)

CLEAR_SECRET = Act(
    method="DELETE",
    path=_secret_path,
    answers=Acknowledgement,
    refusal=UNREADABLE_WRITE,
    render=_acknowledged,
)


# The reads that are not of one entity: the whole configuration, the
# boards waiting to be claimed, and the three that ask the running
# server rather than the database.


def _printed(listing: Callable[[Any], str]) -> Callable[[Any], None]:
    """A renderer that answers the whole of its output at once. Each
    listing ends in its own newline, so nothing is added after it."""

    def render(answer: Any) -> None:
        print(listing(answer), end="")

    return render


def _config_path(args: Invocation) -> str:
    return _path("config")


def _running_path(args: Invocation) -> str:
    return _path("runtime", "mcp-servers")


def _reload_path(args: Invocation) -> str:
    return _path("runtime", "config", "reload")


def _assembled_path(args: Invocation) -> str:
    return _path("runtime", "agents", args.name, "prompt")


def _info_path(args: Invocation) -> str:
    return _path("runtime", "info")


LIST = Act(
    method="GET",
    path=_config_path,
    answers=ConfigDocument,
    render=_printed(_summary),
)

# The same read, rendered as a count per kind. `info`'s second act: what
# it needs of the stored half is its shape rather than its contents, and
# a read that already answers the whole document can be asked for either
# (`show` and `export` are two more renderings of it).
COUNTS = Act(
    method="GET",
    path=_config_path,
    answers=ConfigDocument,
    render=_printed(_configured_counts),
)

SHOW_ALL = Act(
    method="GET",
    path=_config_path,
    answers=ConfigDocument,
    render=_printed(_show_everything),
)

EXPORT_ALL = Act(
    method="GET",
    path=_config_path,
    answers=ConfigDocument,
    render=_printed(_exported),
)

EXPORT_ENTITY: dict[str, Act] = {
    kind.name: Act(
        method="GET",
        path=_entity_path(kind),
        answers=Envelope,
        render=_printed(_exported_entity(kind)),
    )
    for kind in entities.ENTITIES
}

PENDING = Act(
    method="GET",
    path=_waiting_path,
    answers=dict[str, PendingDevice],
    render=_printed(_pending_listing),
)


# The conversation store's sessions: three acts on the two resources the
# store serves, and the one place in this grammar that reaches a schema
# the domain configuration knows nothing about. They are here for the
# reason the amendment to #190 gives: a command that touches the record
# is a request like every other, and there is no second way in.


def _sessions_path(args: Invocation) -> str:
    return _path("sessions")


def _session_path(args: Invocation) -> str:
    return _path("sessions", args.session)


def _session_filters(args: Invocation) -> dict[str, str]:
    """What narrows a listing. Only what was written: an absent flag is
    an argument the request does not carry, so the API's own defaults
    are the defaults, said once."""
    return {
        name: value
        for name, value in (("device", args.mac), ("limit", args.limit))
        if value
    }


def _purge_selectors(args: Invocation) -> dict[str, str]:
    """What a purge names. The same rule as the filters above, and the
    refusal for naming none of them is the API's: a purge that erased
    everything because its arguments were lost on the way is exactly
    what the endpoint refuses, and a second copy of that rule here would
    be a second sentence for one decision."""
    return {
        name: value
        for name, value in (
            ("session", args.session),
            ("device", args.mac),
            ("before", args.before),
        )
        if value
    }


LIST_SESSIONS = Act(
    method="GET",
    path=_sessions_path,
    query=_session_filters,
    answers=SessionList,
    render=_printed(_session_listing),
)

SHOW_SESSION = Act(
    method="GET",
    path=_session_path,
    answers=SessionDetail,
    render=_printed(_session_block),
)

DELETE_SESSION = Act(
    method="DELETE",
    path=_session_path,
    answers=Erasure,
    refusal=UNREADABLE_WRITE,
    render=_printed(_erasure_block),
)

PURGE_SESSIONS = Act(
    method="DELETE",
    path=_sessions_path,
    query=_purge_selectors,
    answers=Erasure,
    refusal=UNREADABLE_WRITE,
    render=_printed(_erasure_block),
)

# And the store's other projection, the thread. The same schema and a
# different question: a session is one connection episode, a
# conversation is a durable thread with one agent that may span several
# of them, and a turn belongs to both.


def _conversations_path(args: Invocation) -> str:
    return _path("conversations")


def _conversation_path(args: Invocation) -> str:
    return _path("conversations", args.conversation)


def _dialogue_path(args: Invocation) -> str:
    return _path("conversations", args.conversation, "turns")


def _conversation_filters(args: Invocation) -> dict[str, str]:
    """What narrows a thread listing. The rule the session filters
    follow: only what was written, so the API's own defaults are the
    defaults, said once. No cursor flags, deliberately, and the reason
    is the same as there: one invocation prints one page, and walking
    the record is what the API is for."""
    return {
        name: value
        for name, value in (("agent", args.name), ("limit", args.limit))
        if value
    }


LIST_CONVERSATIONS = Act(
    method="GET",
    path=_conversations_path,
    query=_conversation_filters,
    answers=ConversationList,
    render=_printed(_conversation_listing),
)

SHOW_CONVERSATION = Act(
    method="GET",
    path=_conversation_path,
    answers=ConversationDetail,
    render=_printed(_conversation_block),
)

READ_DIALOGUE = Act(
    method="GET",
    path=_dialogue_path,
    answers=ConversationTurns,
    render=_printed(_dialogue_blocks),
)

DELETE_CONVERSATION = Act(
    method="DELETE",
    path=_conversation_path,
    answers=ThreadErasure,
    refusal=UNREADABLE_WRITE,
    render=_printed(_erasure_block),
)

# The read that says which deployment answered, which none of the reads
# above it does: they say what is stored or what is running, and this
# one says whose. `info`'s first act.
IDENTITY = Act(
    method="GET",
    path=_info_path,
    answers=RuntimeInfo,
    render=_printed(_identity_block),
)

# A read of the running server rather than of the database: what a
# database says about an entry is what `show mcp-server` prints, and a
# stopped server has no state to report.
STATUS = Act(
    method="GET",
    path=_running_path,
    answers=dict[str, McpServerStatus],
    render=_printed(_status_block),
)

# The other read of the running server: the persona is stored and the
# guidance is stored, but what they add up to is a property of the
# process that loaded them.
PROMPT = Act(
    method="GET",
    path=_assembled_path,
    answers=AssembledPrompt,
    render=_printed(_prompt_listing),
)

# The one act that changes what a server is doing without writing
# anything, and it prints both halves of the answer: what the reload
# applied, and what every configured MCP entry is doing now that it has
# been done.
RELOAD = Act(
    method="POST",
    path=_reload_path,
    read_timeout_s=RELOAD_READ_TIMEOUT_S,
    answers=ConfigReloadResult,
    refusal=UNREADABLE_RELOAD,
    render=_printed(_reload_listing),
)


def _diff_path(args: Invocation) -> str:
    return _path("runtime", "config", "diff")


# The read the other two in this namespace cannot give: they say what is
# running and the entity reads say what is stored, and this is the
# question an operator actually has after a write.
DIFF = Act(
    method="GET",
    path=_diff_path,
    answers=ConfigDiff,
    render=_printed(_diff_listing),
)


# The one write that carries the whole configuration rather than one
# entry of it. The document is checked for what JSON cannot carry before
# it travels, exactly as a fragment is and against the same rule, under
# the location the repository names a refusal about the document as a
# whole with.


def _apply_path(args: Invocation) -> str:
    return _path("apply")


def _document_body(args: Invocation) -> object:
    document = _fragment(args.file)
    check_transportable(APPLY_LOCATION, document)
    return document


APPLY = Act(
    method="POST",
    path=_apply_path,
    body=_document_body,
    sends=DomainConfig,
    read_timeout_s=APPLY_READ_TIMEOUT_S,
    answers=AppliedDocument,
    refusal=UNREADABLE_WRITE,
    render=_applied,
)

# The two acts an `apply` actually runs, which are the two above with
# what one invocation makes of them written on: the write rendered
# without the boundaries a reload is about to cross, and the reload
# carrying the sentence a failure behind a committed write may claim.
#
# Derived from the rows rather than written out beside them, so the
# request half cannot come apart from the row the contract check
# enumerates: the method, the path, the body, the shapes and the two
# timeouts are the ones above, and what differs is what this command
# prints.
APPLY_QUIETLY = replace(APPLY, render=_applied_quietly)

APPLY_RELOAD = replace(RELOAD, unanswered=APPLY_UNANSWERED)


def _applying(args: Invocation) -> tuple[Act, ...]:
    """Which acts one `apply` runs.

    The verb does what its name promises: it writes the document and
    installs it, which is the two acts in that order. `--no-reload`
    stages instead, and stages is the whole of what it does: the write
    is the same request either way, and what changes is that nothing
    installs it and the rendering says so.
    """
    if args.no_reload:
        return (APPLY,)
    return (APPLY_QUIETLY, APPLY_RELOAD)


# The simulated board
#
# The one command here that stands where a device stands rather than
# where an operator does. Everything it knows about the exchange is
# `simulator.board`, and everything it knows about talking to a
# device-facing address is `device_endpoint`; what is here is the
# grammar's half, which is what is printed and which of the states is a
# failure.
#
# Two credentials stay apart, and the seam is `--claim`. Without the
# flag no API token is read and no API request is made: the device side
# never touches the operator-side credential, which is what "kept
# distinct" has to mean to be worth saying. With it, the claim is
# `ADD_DEVICE`, the same act `device pending claim` performs, so there is
# no second encoding of the claim and no new row in the contract check's
# covered set.

# Where the URL comes from, said on the help page, because there is
# deliberately no derivation behind it. The resolution order the guide
# asks for would end at `onboarding.origin`, which is the import that
# gates `ota-url` on the server half, and inheriting that gate would make
# this command refuse on the very install it exists for.
ENDPOINT_HELP = (
    f"the OTA URL to check in to: the address `{PROGRAM} ota-url` prints inside the "
    f"image, or the one already written into a board's NVS"
)

MAC_HELP = (
    f"the address this simulated board presents (default: {board.DEFAULT_MAC}, whose "
    f"leading octet is the locally-administered bit; a second board is "
    f"02:00:00:00:00:02)"
)

CLAIM_HELP = (
    "bind this board to an agent through the configuration API and check in again to be "
    "issued a token; repeat the option for several agents (default: print the code and "
    "the command to run)"
)

# What is printed on stderr beside an activation code, which is the same
# advice `ota-url` gives beside its URL: what to do next is a notice, and
# stdout holds what the board was handed.
CLAIM_GUIDANCE = (
    f"This board is showing an activation code, the way a screen would. Bind it with "
    f"`{PROGRAM} device pending claim <code> <agent>`, or run this command again with "
    f"--claim <agent> to do both."
)

# What a claim needs and did not get. `--claim` is addressed by the six
# digits a board is showing, so a board that was not offered a code has
# nothing for the claim to address.
NOTHING_TO_CLAIM = (
    "--claim binds the board showing an activation code, and this check-in was not "
    "offered one. A board that is already bound needs no claim, and a board this "
    "deployment will not admit is not one a claim can help: run without --claim to see "
    "which of the two it is."
)

# And what a ceremony that ran its course without being admitted says.
# The claim went through, so this is the server not yet serving what the
# binding names.
NOT_ADMITTED_YET = (
    f"this board was claimed, and the activation poll was still answering keep-waiting "
    f"when the bound expired. A binding to an agent this server is not serving yet flips "
    f"at the reload that installs it: run `{PROGRAM} reload`, and then this command "
    f"again."
)

# What the reply's firmware block said, as this side read it. Three
# sentences over two booleans, and no far-side value in any of them: a
# real board's use of that block is a decision rather than a display, so
# the decision is what crosses and the version and the URL stay where
# every other far-side string in this command stays.
FIRMWARE_OFFERED = (
    "firmware: an image was offered, and nothing here fetches one: a simulated board "
    "has no partitions to write it to. Neither the version nor the address it named is "
    "repeated."
)

FIRMWARE_UP_TO_DATE = (
    "firmware: no image was offered, and the version named back is the one this board "
    "announced, which is how a deployment with nothing to offer says so."
)

FIRMWARE_UNEXPECTED_VERSION = (
    "firmware: no image was offered, and the version named back is not the one this "
    "board announced. A board reads that as up to date too, since there is nothing to "
    "fetch; the version is not repeated, being whatever that endpoint returned."
)

NOT_ADMITTED_AFTER_CLAIM = (
    f"this board was claimed and the activation poll said it was activated, and the "
    f"check-in after it did not hand this board a token. Nothing here can go on from "
    f"that: read what the deployment says about the MAC with "
    f"`{PROGRAM} device show <mac>`."
)


def _simulator_check_in(args: Invocation) -> None:
    """Check in to an OTA URL as a board would, and say what it was told.

    Three of the four states are a command that worked, because a
    simulated board reporting the state it is in is the answer, and only
    a reply this client will not read as one is a failure.
    """
    endpoint = device_endpoint.Endpoint.parsed(
        args.endpoint, board.GIVEN_URL, device_endpoint.SUPPLIED_ENDPOINT
    )
    identity = board.Identity.of(args.mac)
    state = board.check_in(endpoint, identity)
    if args.agents:
        state = _claimed(args, endpoint, identity, state)
    _reported(state, endpoint)


def _claimed(
    args: Invocation,
    endpoint: "device_endpoint.Endpoint",
    identity: board.Identity,
    state: board.CheckIn,
) -> board.CheckIn:
    """The four-step ceremony a real board and an operator perform
    between them.

    Check in and read a code; claim it through the act the grammar
    already has; poll where a waiting board polls; and check in AGAIN.
    The fourth step is the one that makes the other three worth anything:
    an activating check-in's token is empty and the poll route answers a
    status, so the only thing that mints a token is a check-in reply. A
    socket opened with the token from step one would be refused at the
    handshake with no_token, which is the confusion
    `docs/xiaozhi-notes.md` warns about from the other side.

    The same MAC and the same client id cross all four requests, because
    the token is signed for the two of them together.
    """
    if not isinstance(state, board.Activating):
        if isinstance(state, board.Refused):
            return state
        raise ConfigError(NOTHING_TO_CLAIM)
    # The one request this command makes, and the one place it resolves
    # the operator-side credential: inside the `--claim` arm, which is
    # what keeps the device side clear of it.
    _act(replace(args, code=state.code), ADD_DEVICE, _reached(args))
    waited = board.polled(endpoint, identity, state.timeout_ms)
    if isinstance(waited, board.Refused):
        return waited
    if isinstance(waited, board.StillWaiting):
        raise ConfigError(NOT_ADMITTED_YET)
    admitted = board.check_in(endpoint, identity)
    if isinstance(admitted, board.Activating | board.Unwelcome):
        raise ConfigError(NOT_ADMITTED_AFTER_CLAIM)
    return admitted


# What `run` says beyond what `check-in` says.
#
# The conversation is what this verb exists for, so the transcript and
# the reply's sentences go to stdout as they arrive: they are far-side
# text, and they are the artifact the command exists to print. Everything
# else about the exchange is a count, a duration or a name this side
# chose.

RUN_HELP = (
    "check in to an OTA URL as a board would, then hold one conversation over the "
    "websocket: say the packaged sentence, and print the transcript and the reply as "
    "they arrive"
)

# What a board that may not speak is told when it was asked to speak.
# `check-in` reports those two states and exits 0, because reporting the
# state a board is in is the answer; `run` was asked for a conversation
# and cannot have one, so the same states are a refusal here.
CANNOT_CONVERSE = (
    "this board is not admitted, so there is no conversation to hold. Run "
    f"`{PROGRAM} simulator check-in` against the same address to see which of the states "
    f"it is in, and pass --claim <agent> to bind it."
)


def _simulator_run(args: Invocation) -> None:
    """Check in as a board, then hold one turn of a conversation.

    Everything before the socket is `check-in`'s, exactly: the same
    endpoint, the same identity, the same four-step ceremony behind
    --claim. The token and the websocket address this opens with are the
    LAST check-in reply's, which is the only thing that mints either.

    Everything this command needs of its own INSTALLATION is settled
    before anything about the arguments, and both are settled before
    anything reaches the network. The extra is the first of those and the
    packaged utterance is the second: a board with nothing to say cannot
    hold a conversation whatever the address answers, and finding that
    out after a check-in, a claim and an activation poll would mean a
    command that could not speak had already rebound a device and spent
    a ceremony to say so.

    So the order is: what is installed, then what was typed, then what
    the network says.
    """
    held = _from_an_installed_half(_the_conversation_half, NEEDS_THE_SIM_EXTRA)
    said = utterance.packaged()
    endpoint = device_endpoint.Endpoint.parsed(
        args.endpoint, board.GIVEN_URL, device_endpoint.SUPPLIED_ENDPOINT
    )
    identity = board.Identity.of(args.mac)
    state = board.check_in(endpoint, identity)
    if args.agents:
        state = _claimed(args, endpoint, identity, state)
    if isinstance(state, board.Refused):
        raise ConfigError(state.problem)
    if not isinstance(state, board.Admitted):
        raise ConfigError(CANNOT_CONVERSE)
    print(
        f"{device_endpoint.SUPPLIED_ENDPOINT} admitted this board, and the conversation is "
        f"open on {device_endpoint.REPORTED_WEBSOCKET}, which is not printed.\n"
        f"protocol version: {state.protocol_version}\n"
        f"{_firmware(state.firmware)}\n"
        f"saying: {said.sentence}"
    )
    sys.stdout.flush()
    _conversed(held, state, identity, said)


def _the_conversation_half():
    """The websocket half of the simulator, imported here and nowhere
    else in this module.

    That is the whole of what makes `sim` an extra: `conversation.py`
    holds the only `websockets` import in the package, nothing imports it
    at module scope, and a bare install reaching this line gets an
    ImportError the gate turns into a sentence naming the extra.
    """
    from vinga_server.simulator import conversation

    return conversation


def _conversed(
    held, state: board.Admitted, identity: board.Identity, said: utterance.Utterance
) -> None:
    """One turn, and what is said about it afterwards.

    `held` is the module the gate handed back, passed in rather than
    imported here so that this function names no module the client half
    does not have. Its `Reply` is unannotated for the same reason, which
    is the gate's cost rather than an omission.
    """
    reply = held.converse(
        target=state.websocket,
        token=state.token,
        identity=identity,
        version=state.protocol_version,
        said=said,
        say=_as_it_arrives,
    )
    print(
        f"reply: {reply.packets} frames, {reply.audio_bytes} bytes, about "
        f"{reply.audio_ms} ms of audio, which is counted rather than decoded\n"
        f"the conversation reached: {reply.state}\n"
        f"close: {reply.closed}"
    )
    for surprise in reply.surprises:
        print(f"out of order: {surprise}", file=sys.stderr)


def _as_it_arrives(line: str) -> None:
    """One line of the conversation, as it happens rather than at the
    end. Flushed, because the whole point is watching it."""
    print(line)
    sys.stdout.flush()


def _reported(state: board.CheckIn, endpoint: "device_endpoint.Endpoint") -> None:
    """What the board was handed, on stdout, and what to do about it on
    stderr.

    Everything read out of the reply goes out through the endpoint's own
    door: the code, the message and the challenge are the artifact this
    command exists to show, and they are still whatever that address
    returned, so they are bounded, made printable, and stripped of any
    part of the supplied address they hand back. That last rule is why
    the endpoint is a parameter here rather than the state alone. A
    refusal quotes nothing, so these three fields are the only route a
    supplied URL has to a surface at all, and reflecting a request target
    into an answer is what a proxy, a captive portal and an error page
    each do by default.

    The device token and the websocket URL are not that artifact and are
    named by their stand-ins. The firmware block is neither: what is
    said about it is this side's own reading of it, per `_firmware`.
    """
    if isinstance(state, board.Refused):
        raise ConfigError(state.problem)
    if isinstance(state, board.Activating):
        print(
            f"{device_endpoint.SUPPLIED_ENDPOINT} answered, and this board is not "
            f"claimed yet.\n"
            f"activation code: {endpoint.repeated(state.code)}\n"
            f"what a screen would show: {endpoint.repeated(state.message)}\n"
            f"challenge: {endpoint.repeated(state.challenge)}\n"
            f"{_firmware(state.firmware)}"
        )
        sys.stdout.flush()
        print(CLAIM_GUIDANCE, file=sys.stderr)
        return
    if isinstance(state, board.Admitted):
        print(
            f"{device_endpoint.SUPPLIED_ENDPOINT} answered, and this board is admitted.\n"
            f"device token: issued, and its value is never printed\n"
            f"websocket: {device_endpoint.REPORTED_WEBSOCKET}, which is not printed "
            f"either: it is what a token would be sent to\n"
            f"protocol version: {state.protocol_version}\n"
            f"{_firmware(state.firmware)}"
        )
        return
    print(
        f"{device_endpoint.SUPPLIED_ENDPOINT} answered, and this board may not speak.\n"
        f"It was issued no token and offered no activation code, which is what three "
        f"configurations look like from here: onboarding is turned off on that "
        f"deployment and nothing resolves this MAC; or this MAC is bound to an agent "
        f"that deployment is not serving yet, which `{PROGRAM} reload` installs; or the "
        f"table of boards waiting to be claimed would not take another one.\n"
        f"{_firmware(state.firmware)}"
    )


def _firmware(read: board.Firmware) -> str:
    """What the reply said about an image, as this side read it.

    Three sentences over two booleans, and no far-side value in any of
    them. What a real board does with that block is decide rather than
    display, so the decision is what survives the crossing: an image was
    named or it was not, and the version named back is this board's own
    or it is not. A deployment with nothing to offer echoes the version
    it was told, which is how the firmware reads "up to date", and a
    version that comes back changed with no image behind it is the one
    combination worth saying out loud.
    """
    if read.offered:
        return FIRMWARE_OFFERED
    return FIRMWARE_UP_TO_DATE if read.announced else FIRMWARE_UNEXPECTED_VERSION


# The live event stream
#
# The one read of this API whose answer does not finish, which is why it
# is a local function like `ota-url` rather than an `Act`: an act is a
# buffered request whose one answer is handed to one renderer, and
# bending that shape around a body with no end would deform the grammar's
# core for one command. What it borrows instead is the transport, which
# is the half that matters: `_streamed` above carries the whole no-leak
# boundary across opening, iterating and giving the connection back.
#
# What the wire looks like is the API's published contract rather than
# an import. This half of the program is the client, and the module that
# writes these frames is precisely what it may not reach
# (`tests/unit/test_cli_import_weight.py`); a generated client would
# carry the same words for the same reason.

# The stream's own event name for a reader that fell behind, and the key
# its object carries.
DROPPED_EVENT = "dropped"

# The two fields the stream owns and the one every event carries, which
# this renderer prints in front of the rest rather than among them.
STREAM_TIME = "ts"

STREAM_LEVEL = "level"

EVENT_NAME = "event"

# The one level whose name is not printed. It is the default the stream
# filters at, so it is what most of a tail is, and a word on every line
# saying "ordinary" is a word that stops being read. Every other level
# is named, DEBUG included: an event admitted below the default has to
# say that it is one.
UNNAMED_LEVEL = "INFO"

# What may be printed as a bare word rather than as an encoded value.
#
# Two things go through this: an event's name and its level's name, both
# of which are vocabulary from a closed declared set and both of which
# would be unreadable in quotes. The pattern is what makes printing them
# bare safe rather than trusting: a name that is not one of these
# characters is not one this API declares, so it is encoded like any
# other value and the line's one-line guarantee is kept whatever
# arrived.
_BARE_WORD = re.compile(r"\A[A-Za-z0-9_.:-]{1,64}\Z")

EVENTS_DEVICE_HELP = "only the events of this board, by MAC (default: every board)"

EVENTS_SESSION_HELP = (
    "only the events of this session, by its uuid hex (default: every session)"
)

EVENTS_LEVEL_HELP = (
    "the lowest level to show, in any case: DEBUG, INFO, WARNING or ERROR "
    "(default: INFO, which is what the retained log carries)"
)

FOLLOW_HELP = (
    "keep streaming until interrupted; without it the command prints the first "
    "matching event and exits"
)

TAIL_HELP = (
    "what this server is saying right now, one line per event, as it says it; "
    "without --follow it waits for the first event, prints it and exits"
)


def _events_tail(args: Invocation) -> None:
    """The structured events of the running server, as they happen.

    Two modes and two exact contracts. Without `--follow` this waits for
    the first event the filters admit, prints it and exits 0, which is
    the scriptable "wait for the next X" and the only reading a tail
    with no buffer behind it can offer. With `--follow` it prints until
    something stops it: an interrupt, which is a reader who was told to
    stop and is therefore exit 0, or the stream ending, which is exit 1
    and `STREAM_ENDED`.

    The events go to stdout, one line each and flushed as they arrive,
    because that is what a caller opened this for and a block-buffered
    pipe would deliver a live stream in four-kilobyte lumps. The dropped
    notices go to stderr, because a reader falling behind is about this
    invocation rather than about the deployment: `tail | grep` reads
    only the events, and the person watching still learns that some went
    past.
    """
    reached = _reached(args)
    with contextlib.closing(
        _streamed(reached, _path("runtime", "events"), _event_filters(args))
    ) as lines:
        try:
            for name, fields in _frames(lines):
                if name == DROPPED_EVENT:
                    print(_dropped_notice(fields), file=sys.stderr)
                    continue
                print(_event_line(fields))
                sys.stdout.flush()
                if not args.follow:
                    return
        except KeyboardInterrupt:
            # A tail that was told to stop did its job. Caught here
            # rather than at the boundary because this is the one
            # command in the grammar whose ordinary ending it is.
            return


def _event_filters(args: Invocation) -> dict[str, str]:
    """What narrows the stream. Only what was written: an absent flag is
    an argument the request does not carry, so the API's own defaults
    are the defaults, said once."""
    return {
        name: value
        for name, value in (
            ("device", args.mac),
            ("session", args.session),
            ("level", args.level),
        )
        if value
    }


def _frames(lines: Iterable[str]) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """The stream's lines as the events they encode.

    Server-Sent Events is a line vocabulary rather than a document: a
    frame is the lines up to the next blank one, `event:` names it and
    `data:` carries it, and a line beginning with a colon is a comment,
    which is what the keepalive an idle stream sends is made of. A field
    this client has no use for is ignored, which is what the format asks
    a reader to do and what keeps a stream that grows a field from
    breaking a client that does not want it.
    """
    name = ""
    data: list[str] = []
    for line in lines:
        if line == "":
            if data:
                yield name, _frame_fields("\n".join(data))
            name, data = "", []
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == EVENT_NAME:
                name = value
            elif field == "data":
                data.append(value)


def _frame_fields(data: str) -> Mapping[str, Any]:
    """One frame's object, or a refusal with nothing of the frame in it.

    Refused rather than skipped. A tail that quietly dropped what it
    could not parse would go on looking live while showing less than
    arrived, which is the failure this whole command's end-of-stream
    contract exists to make impossible. Recorded inside the handler and
    raised outside it, this module's rule: a JSON decoding error carries
    the document it was decoding.
    """
    parsed: object = None
    try:
        parsed = json.loads(data)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        raise ConfigError(UNREADABLE_EVENT)
    return parsed


def _event_line(fields: Mapping[str, Any]) -> str:
    """One event as one physical line.

    The clock time it happened at, its level unless that is the one a
    tail is mostly made of, its name, and then everything else it
    carries as `key=value` in the order the event declares them, which
    is the order the retained record writes them in.

    One line by encoding rather than by hope. An event's values are
    identifiers, counts, durations and reason tokens, and the identifier
    vocabulary explicitly admits bytes a terminal reads as instructions,
    so a value is rendered as its compact JSON encoding: a newline
    arrives as `\\n` and an escape sequence as `\\u001b` instead of
    breaking the line in two or steering the terminal it landed in. That
    is the output-determinism practice's second half, which has no
    exception.

    This is a rendering of the record the JSON log retains, not a second
    vocabulary. A reader who needs the object itself reads the log, or
    the stream, which carries exactly it.
    """
    parts = [_time_of_day(fields.get(STREAM_TIME))]
    level = fields.get(STREAM_LEVEL)
    if level is not None and level != UNNAMED_LEVEL:
        parts.append(_bare(level))
    if EVENT_NAME in fields:
        parts.append(_bare(fields[EVENT_NAME]))
    parts += [
        f"{_bare(key)}={_value(value)}"
        for key, value in fields.items()
        if key not in (STREAM_TIME, STREAM_LEVEL, EVENT_NAME)
    ]
    return " ".join(parts)


def _dropped_notice(fields: Mapping[str, Any]) -> str:
    """What a reader that fell behind is told, in the count's own words.

    On stderr and phrased as a gap rather than as an error, because it
    is neither this command's failure nor the server's: the stream
    overwrites the oldest events for a reader that has stopped keeping
    up, which is the alternative to slowing a conversation down.
    """
    return (
        f"{_value(fields.get(DROPPED_EVENT))} events are missing above this line: this "
        f"reader fell behind, and the server overwrote the oldest of them rather than "
        f"holding a conversation up for it."
    )


def _time_of_day(value: object) -> str:
    """The stream's stamp as a person watching reads it: the clock time,
    without the date a tail is already inside of. Anything that is not a
    stamp is rendered as the value it is, which is what keeps this from
    being the one place a line could come apart."""
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(value).strftime("%H:%M:%S")
    return _value(value)


def _bare(value: object) -> str:
    """A declared word printed as it is, and anything else encoded."""
    return value if isinstance(value, str) and _BARE_WORD.match(value) else _value(value)


def _value(value: object) -> str:
    """One value as a line may carry it.

    Numbers as themselves, because a count and a duration are what a
    reader scans a tail for and quoting them would bury them. Everything
    else as compact JSON with nothing above plain ASCII left unescaped,
    which is the whole of the one-line guarantee. A boolean is not a
    number here: `true` is what the record says and `1` is not.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


# The grammar
#
# One row per command: where it sits in the command tree, what it does,
# and how it declares its arguments. The table is the whole of the
# grammar and the loop at the foot of this module is the only reader of
# a row, so adding a command is a row rather than a paragraph of parser
# construction.


# What each of the two global options says, and the two positions they
# are accepted in. Both readings are natural: `vinga-server --config
# path` is how the server takes it, and options after their subcommand
# is how everything else does.
CONFIG_HELP = (
    f"path to the YAML config file naming server.port and server.api.secret_env "
    f"(default: ${CONFIG_ENV_VAR})"
)

API_URL_HELP = (
    f"base URL of the configuration API (default: ${API_URL_ENV}, then "
    f"http://127.0.0.1:<server.port>{API_MOUNT_PATH})"
)

FILE_HELP = (
    "YAML fragment for this entity, or - to read it from stdin; the alternative to "
    "key=value arguments, and never both (default: none, and one of the two forms "
    "must be given)"
)

DOCUMENT_HELP = (
    "YAML document to apply, or - to read it from stdin: the sections of the domain "
    "configuration, with the entities in each written as they are for set"
)

# The staging spelling. Named for what it turns off rather than for what
# it leaves, because what it leaves is what the verb used to do and the
# verb is what changed: the write is the same request either way.
NO_RELOAD_HELP = (
    "write the document and stop there, leaving the running server on what it is "
    f"already serving until a `{PROGRAM} reload` (default: write it, then reload)"
)

PAIRS_HELP = (
    "the entity written inline, one key=value per field; a dotted key nests "
    "(filler.enabled=true) and a value reads as one YAML scalar. The alternative to "
    "-f, and never both"
)

# What the help page of every `set` command opens with. The store
# already refuses a plaintext credential by the shape of the key it was
# written under, whichever way the entity was written; what this adds is
# the reason an inline value is the wrong place for one even when the
# key would have been accepted.
SECRET_NOT_A_PAIR = (
    f"A credential is never a key=value argument: arguments land in shell history and "
    f"in the process list. Store one with `{PROGRAM} <kind> secret set`, which reads "
    "it from stdin or from the variable --from-env names, and never echoes it."
)

FROM_ENV_HELP = (
    "read the value from this variable (default: stdin, read without echo at a terminal)"
)

FORCE_HELP = (
    "answer the confirmation a destructive command asks at a terminal, so it does not "
    "ask (default: it asks)"
)

NO_INPUT_HELP = (
    "never prompt: a destructive command refuses rather than asking, and a secret is "
    "read from stdin or --from-env (default: prompt at a terminal)"
)

STAGE_HELP = ", ".join(PROVIDER_STAGES)

PROVIDER_SLOT_HELP = "the option it fills, such as api_key"

MCP_SLOT_HELP = "env.<KEY> or headers.<KEY>"

SESSION_HELP = "the session's uuid hex, as a listing prints it"

DEVICE_FILTER_HELP = "only the sessions of this board, by MAC (default: every board)"

LIMIT_HELP = "how many rows this page may hold (default: the API's own, 50)"

BEFORE_HELP = (
    "only the sessions that began before this UTC day, as YYYY-MM-DD (default: "
    "however far back the store goes)"
)

CONVERSATION_HELP = "the conversation's uuid hex, as a listing prints it"

AGENT_FILTER_HELP = "only the conversations of this agent, by name (default: every agent)"

SELECTED_SESSION_HELP = (
    "only this session, by its uuid hex (default: every session the other selectors "
    "leave)"
)

# The two that follow `schema provider`. A provider type is addressed by
# its stage and its name together everywhere else in this command group,
# and its options are addressed the same way for the same reason: one
# type name lives in more than one stage.
SCHEMA_STAGE_HELP = "with TYPE, the options of one provider type: llm, asr, tts or vad"

SCHEMA_TYPE_HELP = "with STAGE, the provider type whose options to print"

# The first thing anybody reads of this grammar, so it is written in the
# vocabulary of the person reading it rather than in this repository's.
# "The domain half" is a real distinction here (the file half boots a
# server, the domain half is what it serves) and it is a distinction
# nobody has met yet at the moment they run `vinga` for the first time:
# a sentence that opens with it says what this command group is NOT
# before it has said what it is. What it is, is the thing they came to
# do.
DESCRIPTION = (
    "Configure a running vinga server: providers, MCP servers, agents, "
    "devices and their secrets. Commands go through the configuration API."
)

# The declared copy of each option, as one annotation apiece, so a
# command that takes them says so in two lines and cannot come to spell
# one of them differently from its siblings.
#
# `None` is the not-given value, and it is an answer rather than a
# sentinel of convenience: neither option can be typed as None, so the
# merge below reproduces argparse's `default=SUPPRESS` dance exactly. A
# sentinel object of this module's own would read back as its repr in
# the help, which is the one place these defaults are published.
ConfigOption = Annotated[str | None, typer.Option("--config", metavar="PATH", help=CONFIG_HELP)]

ApiUrlOption = Annotated[str | None, typer.Option("--api-url", metavar="URL", help=API_URL_HELP)]

# The two prompt-control options, and `bool | None` is the load-bearing
# part rather than a nicety. They ride `Globals` like the two above, so
# an absent copy at the command position must not overwrite what the
# root position said; an ordinary boolean default would arrive as False
# and make `vinga --no-input agent delete kids` prompt.
ForceOption = Annotated[bool | None, typer.Option("--force", help=FORCE_HELP)]

NoInputOption = Annotated[bool | None, typer.Option("--no-input", help=NO_INPUT_HELP)]

# The two ways a write's entity is given, declared once apiece for the
# same reason the three globals are: a `set` command says so in two
# lines and cannot come to spell one of them differently from its
# siblings. Neither is required on its own, because either satisfies the
# command; what refuses neither and both is `_written_entity`, which is
# the only place that can see the pair of them.
FileOption = Annotated[str, typer.Option("-f", "--file", metavar="PATH", help=FILE_HELP)]

PairsArgument = Annotated[
    list[str] | None, typer.Argument(metavar="KEY=VALUE", help=PAIRS_HELP)
]


@dataclass(frozen=True, kw_only=True)
class Globals:
    """The two options, as far as the positions so far have resolved
    them.

    The root callback builds the first answer and every position under
    it folds its own copies in, so a value given before the command
    survives a command that was not given one. That survival is the
    load-bearing half: without it `--config path show provider` would
    read the default file, because the command's own empty copy would
    overwrite what came before it.
    """

    config: str | None = None
    api_url: str | None = None
    force: bool | None = None
    no_input: bool | None = None

    def merged(
        self,
        *,
        config: str | None,
        api_url: str | None,
        force: bool | None = None,
        no_input: bool | None = None,
    ) -> "Globals":
        """The same options with one more position's copies folded in,
        each winning only where it was given.

        The two booleans fold on `is not None` for the reason the two
        strings fold on `is None`: what is being preserved is the
        distinction between "said false" and "said nothing", and a
        plain boolean has only one of those.
        """
        return Globals(
            config=self.config if config is None else config,
            api_url=self.api_url if api_url is None else api_url,
            force=self.force if force is None else force,
            no_input=self.no_input if no_input is None else no_input,
        )


@dataclass(frozen=True, kw_only=True)
class Command:
    """One command of the grammar."""

    # Where it sits: the words that name it, root first. One word is a
    # command of the group itself; anything longer is a command under
    # the noun path its leading words name, and every such path is a
    # key of `GROUPS`.
    words: tuple[str, ...]

    # Which entity kind it addresses, for the commands that cover more
    # than one. An explicit fact rather than a position in `words`: a
    # provider's secret rows are three words deep and their kind is the
    # first of them, while `device pending claim` is three words deep
    # and its kind is the device the first word names, so no positional
    # rule reads both correctly.
    kind: str = ""

    # What it does. An act is a request to the configuration API; a
    # tuple of them is a command whose one output is assembled from more
    # than one read, in the order they are written; the commands that
    # reach no API carry their own function instead.
    #
    # A tuple rather than a second row, because what an operator asked
    # for is one thing: `conversation show` prints a thread's header and
    # then its dialogue, and the API answers those as two resources
    # because one of them is paginated and the other is not.
    does: "Act | tuple[Act, ...] | Callable[[Invocation], None]"

    # What it prints before its first request, for the one command whose
    # answer starts with something no server can supply. `info` opens
    # with the banner and with the address this CLI is about to contact,
    # and a renderer cannot say either: an act's renderer is handed the
    # answer and nothing else, which is what keeps a rendering a function
    # of what came back. So the fact about the invocation is printed by
    # the row that knows it, before any act runs, rather than smuggled
    # into an act that would then be two things.
    # Given what this invocation resolved as well as its arguments,
    # because what `info` opens with is where its requests are about to
    # go. A row with no acts resolves nothing and therefore opens with
    # nothing: there would be no address to name and no token to demand.
    opens: "Callable[[Invocation, Reached], None] | None" = None

    # Which of the acts above one invocation runs, for the row where an
    # option decides. `apply` is the one: it writes and then installs
    # what it wrote, and `--no-reload` stages instead.
    #
    # A hook from the invocation rather than a tuple cut down after the
    # fact, because the two things that vary are not the same thing.
    # What a row CAN reach is what `acts()` answers and what the
    # contract check enumerates coverage from, and it does not change
    # with a flag. What one invocation ran is this, and it is also
    # where the rendering is chosen, since an act's renderer is handed
    # the answer and nothing else.
    selects: "Callable[[Invocation], tuple[Act, ...]] | None" = None

    # How its arguments are declared, which is a function Typer reads a
    # signature off. One per argument shape rather than one per command,
    # and the row is handed to it, so what a command performs is read
    # off the row rather than closed over a second time.
    declare: "Callable[[Command], Callable[..., None]]"

    # What the command listing says about it, which is also the heading
    # of its own help page.
    help: str

    # What follows that page, for the commands that take a fragment: the
    # fields the fragment may carry, rendered from the models.
    epilog: str | None = None

    # Whether this verb's effect cannot be undone by running another
    # command with information the operator still has. A delete destroys
    # the body; a `set` does not, as long as an `export` exists, which is
    # why replacement writes and rebindings are not here. A fact on the
    # registration rather than a list beside it, so the confirmation is
    # driven by the same table everything else about a command is.
    destroys: bool = False

    def acts(self) -> "tuple[Act, ...]":
        """The requests this command makes, in the order it makes them,
        and none for a command that reaches no API.

        Read off the row rather than reconstructed by whoever asks: the
        contract check holds every act against the committed document
        and would otherwise carry a second copy of the rule below, which
        is exactly how a command that grew a second request comes to be
        a request nobody compared.
        """
        if isinstance(self.does, Act):
            return (self.does,)
        if isinstance(self.does, tuple):
            return self.does
        return ()

    def performs(self, args: Invocation) -> "tuple[Act, ...]":
        """The acts this invocation runs, which for every row but one
        are the acts the row has: see `selects`."""
        if self.selects is None:
            return self.acts()
        return self.selects(args)

    def perform(self, args: Invocation) -> None:
        """What this command does, once its arguments are in hand."""
        if self.destroys:
            _permitted_to_destroy(args)
        if self.acts():
            # Once, in front of every act and of the opener, so that
            # what this command says about where it is reaching is true
            # of every request it then makes (`Reached`).
            reached = _reached(args)
            if self.opens is not None:
                self.opens(args, reached)
            _performed(args, self.performs(args), reached)
            return
        # No acts is the third arm of `does`: a command that reaches no
        # API, carrying its own function.
        self.does(args)


class _Verbatim(TyperCommand):
    """Every leaf of this grammar: one command, about to run.

    Two things it does that Click's own command class does not, and they
    are unrelated except in being true of every command here.

    Its epilog is printed as it was laid out. Click rewraps an epilog
    paragraph by paragraph, which would reflow the field listing under a
    `set` command into prose. That listing is generated already wrapped,
    at a width narrower than a terminal, for exactly the reason
    argparse's raw formatter was asked for before this: a line that
    wraps on its own is worse than one wrapped on purpose.

    And the `.env` file is read here, which is the last moment before a
    command runs and the first moment it is known that one will. Every
    command of this group needs the environment (the API address, the
    token, the config path and the master key are all read from it) and
    no invocation that runs no command does: a bare `vinga` is answered
    with a help page, and `--help` and `--version` are answered without
    one, so none of the three may be turned into a sentence about a
    `.env` the reader may not have written. Reading it at the boundary's
    mouth made that failure the answer to every invocation, including
    the ones whose whole purpose is to work when nothing else does.

    It is read on the way in rather than by each command body for the
    reason the boundary exists: forty-odd bodies reading it is forty-odd
    chances to forget, and the environment has to be loaded before the
    first thing looks at it whichever command that is.
    """

    def invoke(self, ctx: Any) -> Any:
        load_environment_file()
        return super().invoke(ctx)

    def format_epilog(self, ctx: Any, formatter: Any) -> None:
        if not self.epilog:
            return
        formatter.write_paragraph()
        for line in self.epilog.splitlines():
            formatter.write(f"{line}\n")


def _version(shown: bool) -> None:
    """The installed version, printed and done.

    Eager, so it is answered while the command line is still being
    parsed and does not need a command word after it. It leaves the way
    `--help` leaves, through the exit the boundary carries out of its
    handler, because asking is not failing.

    Rarely reached at all, since `main` answers the same question in
    front of the parse and this one is what answers it if that
    recognizer ever stops recognizing a spelling. Both print through
    `_print_version` rather than one of them formatting the line again,
    and `test_the_version_is_the_same_bytes_through_either_spelling`
    holds them to it. Neither needs the environment: no `.env` is read
    until a command is about to run.
    """
    if not shown:
        return
    _print_version()
    raise typer.Exit(0)


def _print_version() -> None:
    print(f"{DISTRIBUTION} {installed_version()}")


# What is answered before the environment is read
#
# `--version` has to succeed whatever else is wrong, and that is the
# whole of its contract: it is the question an operator asks when they
# are already comparing two halves of a deployment that disagree, which
# is exactly when the rest of a machine is not in a state to be relied
# on. A `.env` that will not decode is one such state, and reading it
# first made the one command that must always answer exit 1 with a
# sentence about a file it was never asked about.
#
# So the root position is recognized without a parser, and recognizing
# it is possible because the root's options are a closed set: everything
# before the first command word is either `--version`, one of the root
# flags, or one of the root options and its value. The sets are read off
# the built tree rather than listed, so an option added to the root
# joins them by being declared, and anything this does not recognize
# ends the scan and goes to the parser, which is the answer that was
# always there.
#
# `--config path --version` therefore answers, and `--config --version`
# does not, because there the word is the option's value and not the
# root's. That distinction is the reason this reads the parameters
# rather than searching the list for a string.


def _root_options() -> tuple[frozenset[str], frozenset[str]]:
    """The root's flags and its value-taking options, by every spelling
    each of them answers to."""
    flags: set[str] = set()
    valued: set[str] = set()
    for parameter in command().params:
        into = flags if getattr(parameter, "is_flag", False) else valued
        into.update(parameter.opts)
    return frozenset(flags), frozenset(valued)


def _version_asked(argv: Sequence[str]) -> bool:
    """Whether this command line asks the root for its version.

    Read left to right, consuming what the root accepts, and stopping at
    the first word it does not: a command word means whatever follows is
    that command's business, and this grammar declares `--version`
    nowhere but the root.
    """
    flags, valued = _root_options()
    skip = False
    for word in argv:
        if skip:
            skip = False
            continue
        if word == "--version":
            return True
        if word in valued:
            skip = True
            continue
        if word not in flags:
            return False
    return False


def installed_version() -> str:
    """What the packaging system says is installed under this name.

    Answered rather than raised for a tree nothing was installed from,
    which is a real state a contributor can be in and not a failure of
    the command they asked for.
    """
    try:
        return metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return VERSION_UNKNOWN


VersionOption = Annotated[
    bool,
    typer.Option(
        "--version",
        is_eager=True,
        callback=_version,
        help="print the installed version and exit",
    ),
]


def _root(
    context: typer.Context,
    config: ConfigOption = None,
    api_url: ApiUrlOption = None,
    force: ForceOption = None,
    no_input: NoInputOption = None,
    version: VersionOption = False,
) -> None:
    """The global options in the position before the command word.

    Their answer is put on the context rather than passed, because the
    positions under this one add to it: a group callback folds its own
    copies in and a command folds its own in after that, and each of
    them reads one object.
    """
    context.obj = Globals(
        config=config, api_url=api_url, force=force, no_input=no_input
    )


def _resolved(context: typer.Context) -> Globals:
    """What the positions above this one made of the two options.

    Answered as an empty `Globals` when there is nothing there, which is
    what a command reached without the root callback having run would
    see. Nothing in this grammar reaches one, and defaulting is cheaper
    than a branch every command would have to carry.
    """
    resolved = context.obj
    return resolved if isinstance(resolved, Globals) else Globals()


def _invocation(
    row: Command,
    context: typer.Context,
    config: str | None = None,
    api_url: str | None = None,
    force: bool | None = None,
    no_input: bool | None = None,
    **addressed: Any,
) -> Invocation:
    """One command's arguments, with the two global options resolved.

    The two come in as this command's own copies, which is one of the
    positions they are accepted in; what the positions above it made of
    them is on the context, and the merge is what lets a value given
    before the command survive a command that was not given one.
    """
    resolved = _resolved(context).merged(
        config=config, api_url=api_url, force=force, no_input=no_input
    )
    return Invocation(
        config=resolved.config,
        api_url=resolved.api_url,
        force=bool(resolved.force),
        no_input=bool(resolved.no_input),
        # Which kind a command that covers several of them was asked
        # about, declared on the row: see `Command.kind`.
        kind=row.kind,
        **addressed,
    )


# How each shape of command declares its arguments
#
# Typer reads a signature, so an argument shape is a function and a
# command is one of these applied to its row. There are fewer of them
# than there are commands because the grammar repeats itself: five kinds
# addressed by a name, one addressed by a stage and a name, two settings
# addressed by a MAC and by six digits on a screen.


def _plain(row: Command) -> Callable[..., None]:
    """A command that addresses nothing: the reads of the whole
    configuration and of the running server, the reload, and the
    singleton, which is the one entity there is only one of."""

    def run(
        context: typer.Context,
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(_invocation(row, context, config, api_url, force, no_input))

    return run


def _named(row: Command) -> Callable[..., None]:
    """A command addressing one entry by its name."""

    def run(
        context: typer.Context,
        name: Annotated[str, typer.Argument(metavar="NAME")],
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(_invocation(row, context, config, api_url, force, no_input, name=name))

    return run


def _staged(row: Command) -> Callable[..., None]:
    """A command addressing one provider, which takes two words because
    two stages may hold the same name."""

    def run(
        context: typer.Context,
        stage: Annotated[str, typer.Argument(metavar="STAGE", help=STAGE_HELP)],
        name: Annotated[str, typer.Argument(metavar="NAME")],
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row, context, config, api_url, force, no_input, stage=stage, name=name
            )
        )

    return run


def _by_mac(row: Command) -> Callable[..., None]:
    """A command addressing one device by the address it connects
    with."""

    def run(
        context: typer.Context,
        mac: Annotated[str, typer.Argument(metavar="MAC")],
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(_invocation(row, context, config, api_url, force, no_input, mac=mac))

    return run


def _written(row: Command) -> Callable[..., None]:
    """The singleton's write: an entity and nothing to address it
    with."""

    def run(
        context: typer.Context,
        pairs: PairsArgument = None,
        file: FileOption = "",
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row, context, config, api_url, force, no_input,
                file=file, pairs=_given(pairs),
            )
        )

    return run


def _named_write(row: Command) -> Callable[..., None]:
    """One named entity's write, from a fragment or from inline
    fields."""

    def run(
        context: typer.Context,
        name: Annotated[str, typer.Argument(metavar="NAME")],
        pairs: PairsArgument = None,
        file: FileOption = "",
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row, context, config, api_url, force, no_input,
                name=name, file=file, pairs=_given(pairs),
            )
        )

    return run


def _staged_write(row: Command) -> Callable[..., None]:
    """One provider's write, from a fragment or from inline fields."""

    def run(
        context: typer.Context,
        stage: Annotated[str, typer.Argument(metavar="STAGE", help=STAGE_HELP)],
        name: Annotated[str, typer.Argument(metavar="NAME")],
        pairs: PairsArgument = None,
        file: FileOption = "",
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row, context, config, api_url, force, no_input,
                stage=stage, name=name, file=file, pairs=_given(pairs),
            )
        )

    return run


def _applied_document(row: Command) -> Callable[..., None]:
    """The whole configuration in one file. No inline fields here: a
    document is several entities and the sections around them, which is
    what a file is for."""

    def run(
        context: typer.Context,
        file: Annotated[
            str, typer.Option("-f", "--file", metavar="PATH", help=DOCUMENT_HELP)
        ],
        no_reload: Annotated[bool, typer.Option("--no-reload", help=NO_RELOAD_HELP)] = False,
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row, context, config, api_url, force, no_input,
                file=file, no_reload=no_reload,
            )
        )

    return run


def _given(pairs: list[str] | None) -> tuple[str, ...]:
    """A variadic argument as the seam holds it. Click answers an
    absent one with None or with an empty tuple depending on the
    version, and the seam's field is one thing: the pairs that were
    written."""
    return tuple(pairs or ())


def _by_session(row: Command) -> Callable[..., None]:
    """A command addressing one recorded session by its id."""

    def run(
        context: typer.Context,
        session: Annotated[str, typer.Argument(metavar="SESSION", help=SESSION_HELP)],
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(row, context, config, api_url, force, no_input, session=session)
        )

    return run


def _filtered_sessions(row: Command) -> Callable[..., None]:
    """The session listing, narrowed by a board and bounded by a count.

    Both are flags rather than positionals, because neither addresses a
    session: one says which board's sessions to show and the other how
    many. No cursor flag, deliberately: one invocation prints one page,
    and walking the whole record backwards is what the API is for.
    """

    def run(
        context: typer.Context,
        device: Annotated[
            str | None, typer.Option("--device", metavar="MAC", help=DEVICE_FILTER_HELP)
        ] = None,
        limit: Annotated[
            str | None, typer.Option("--limit", metavar="N", help=LIMIT_HELP)
        ] = None,
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row,
                context,
                config,
                api_url,
                force,
                no_input,
                mac=device or "",
                limit=limit or "",
            )
        )

    return run


def _tailed(row: Command) -> Callable[..., None]:
    """The event tail: three filters and the one option that says when
    it stops.

    Every one of them a flag, because none of them addresses anything: a
    tail with no filters is the whole server's traffic, which is the
    reading it is opened for most often, and `--follow` is about this
    invocation rather than about what is being asked for.

    The three filters are the query's own words, so what an operator
    types and what the API parses are one vocabulary. What each may be
    is the API's rule and its refusal, said there and not restated here.
    """

    def run(
        context: typer.Context,
        device: Annotated[
            str | None, typer.Option("--device", metavar="MAC", help=EVENTS_DEVICE_HELP)
        ] = None,
        session: Annotated[
            str | None, typer.Option("--session", metavar="ID", help=EVENTS_SESSION_HELP)
        ] = None,
        level: Annotated[
            str | None, typer.Option("--level", metavar="LEVEL", help=EVENTS_LEVEL_HELP)
        ] = None,
        follow: Annotated[bool, typer.Option("--follow", help=FOLLOW_HELP)] = False,
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row,
                context,
                config,
                api_url,
                force,
                no_input,
                mac=device or "",
                session=session or "",
                level=level or "",
                follow=follow,
            )
        )

    return run


def _selected_sessions(row: Command) -> Callable[..., None]:
    """The purge's three selectors, every one of them a flag.

    A selector is not an address: a purge names a set, and the set is
    narrowed by every selector that was written. All three are optional
    here and at least one is required, which is the API's rule and its
    sentence rather than a second copy of it in the grammar.
    """

    def run(
        context: typer.Context,
        session: Annotated[
            str | None,
            typer.Option("--session", metavar="ID", help=SELECTED_SESSION_HELP),
        ] = None,
        device: Annotated[
            str | None, typer.Option("--device", metavar="MAC", help=DEVICE_FILTER_HELP)
        ] = None,
        before: Annotated[
            str | None, typer.Option("--before", metavar="YYYY-MM-DD", help=BEFORE_HELP)
        ] = None,
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row,
                context,
                config,
                api_url,
                force,
                no_input,
                session=session or "",
                mac=device or "",
                before=before or "",
            )
        )

    return run


def _by_conversation(row: Command) -> Callable[..., None]:
    """A command addressing one recorded thread by its id."""

    def run(
        context: typer.Context,
        conversation: Annotated[
            str, typer.Argument(metavar="CONVERSATION", help=CONVERSATION_HELP)
        ],
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row, context, config, api_url, force, no_input, conversation=conversation
            )
        )

    return run


def _filtered_conversations(row: Command) -> Callable[..., None]:
    """The thread listing, narrowed by an agent and bounded by a count.

    `--agent` is a flag and not an address for the reason `--device` is
    one next door: it says whose threads to show rather than naming one
    thread, which is how story 14 of #190 is supplied.
    """

    def run(
        context: typer.Context,
        agent: Annotated[
            str | None, typer.Option("--agent", metavar="NAME", help=AGENT_FILTER_HELP)
        ] = None,
        limit: Annotated[
            str | None, typer.Option("--limit", metavar="N", help=LIMIT_HELP)
        ] = None,
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row,
                context,
                config,
                api_url,
                force,
                no_input,
                name=agent or "",
                limit=limit or "",
            )
        )

    return run


def _provider_secret(row: Command) -> Callable[..., None]:
    """Storing a credential on one provider. The value is never here: it
    is read from stdin or from the variable `--from-env` names."""

    def run(
        context: typer.Context,
        stage: Annotated[str, typer.Argument(metavar="STAGE", help=STAGE_HELP)],
        name: Annotated[str, typer.Argument(metavar="NAME")],
        slot: Annotated[str, typer.Argument(metavar="SLOT", help=PROVIDER_SLOT_HELP)],
        from_env: Annotated[
            str | None, typer.Option("--from-env", metavar="VAR", help=FROM_ENV_HELP)
        ] = None,
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row, context, config, api_url, force, no_input,
                stage=stage, name=name, slot=slot, from_env=from_env,
            )
        )

    return run


def _mcp_secret(row: Command) -> Callable[..., None]:
    """The same, on one MCP server."""

    def run(
        context: typer.Context,
        name: Annotated[str, typer.Argument(metavar="NAME")],
        slot: Annotated[str, typer.Argument(metavar="SLOT", help=MCP_SLOT_HELP)],
        from_env: Annotated[
            str | None, typer.Option("--from-env", metavar="VAR", help=FROM_ENV_HELP)
        ] = None,
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row, context, config, api_url, force, no_input,
                name=name, slot=slot, from_env=from_env,
            )
        )

    return run


def _provider_slot(row: Command) -> Callable[..., None]:
    """Clearing a stored credential from one provider."""

    def run(
        context: typer.Context,
        stage: Annotated[str, typer.Argument(metavar="STAGE", help=STAGE_HELP)],
        name: Annotated[str, typer.Argument(metavar="NAME")],
        slot: Annotated[str, typer.Argument(metavar="SLOT", help=PROVIDER_SLOT_HELP)],
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row, context, config, api_url, force, no_input,
                stage=stage, name=name, slot=slot,
            )
        )

    return run


def _mcp_slot(row: Command) -> Callable[..., None]:
    """The same, on one MCP server."""

    def run(
        context: typer.Context,
        name: Annotated[str, typer.Argument(metavar="NAME")],
        slot: Annotated[str, typer.Argument(metavar="SLOT", help=MCP_SLOT_HELP)],
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row, context, config, api_url, force, no_input, name=name, slot=slot
            )
        )

    return run


def _bound_by_mac(row: Command) -> Callable[..., None]:
    """Binding a board whose address is already known, to one agent or
    several."""

    def run(
        context: typer.Context,
        mac: Annotated[str, typer.Argument(metavar="MAC")],
        agents: Annotated[list[str], typer.Argument(metavar="AGENT")],
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row, context, config, api_url, force, no_input,
                mac=mac, agents=tuple(agents),
            )
        )

    return run


def _bound_by_code(row: Command) -> Callable[..., None]:
    """The same binding, addressed by the six digits on a board's screen
    instead of by a MAC nobody has had to find."""

    def run(
        context: typer.Context,
        code: Annotated[
            str,
            typer.Argument(
                metavar="CODE", help="the six digits the device is showing and speaking"
            ),
        ],
        agents: Annotated[list[str], typer.Argument(metavar="AGENT")],
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row, context, config, api_url, force, no_input,
                code=code, agents=tuple(agents),
            )
        )

    return run


def _simulated_board(row: Command) -> Callable[..., None]:
    """The simulator's verbs: one URL, and two options about the board
    it pretends to be.

    One positional and everything heterogeneous a flag, which is the
    homogeneity rule. The URL is required rather than derived, and the
    help says where to get one: deriving it would mean reading
    `onboarding.origin`, which is the import that gates `ota-url` on the
    server half, and a headline command that refused on a client install
    would be the one thing this must not be.

    `--config` and `--api-url` apply because `--claim` reaches the
    configuration API. `--force` and `--no-input` are offered because
    they are offered everywhere, and are inert here: neither verb
    prompts and neither destroys.
    """

    def run(
        context: typer.Context,
        endpoint: Annotated[str, typer.Argument(metavar="URL", help=ENDPOINT_HELP)],
        mac: Annotated[
            str,
            # The default is in the help sentence with the reason it was
            # chosen, so Click's own copy of it would be the same string
            # twice on one page.
            typer.Option("--mac", metavar="MAC", help=MAC_HELP, show_default=False),
        ] = board.DEFAULT_MAC,
        claim: Annotated[
            list[str] | None, typer.Option("--claim", metavar="AGENT", help=CLAIM_HELP)
        ] = None,
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        force: ForceOption = None,
        no_input: NoInputOption = None,
    ) -> None:
        row.perform(
            _invocation(
                row, context, config, api_url, force, no_input,
                endpoint=endpoint, mac=mac, agents=_given(claim),
            )
        )

    return run


def _from_the_file_half(row: Command) -> Callable[..., None]:
    """The onboarding command, which takes `--config` and nothing else.

    It contacts nothing at all, so it has nothing to do with `--api-url`
    or the bearer token, and offering the flags would say it had. What
    answers on the URL it prints is `vinga-server doctor`, a command of
    its own since #244.
    """

    def run(context: typer.Context, config: ConfigOption = None) -> None:
        row.perform(_invocation(row, context, config))

    return run


def _of_an_entity(row: Command) -> Callable[..., None]:
    """The schema command, which names one entity kind or none, and one
    provider type's options when a stage and a type follow it.

    Three positionals rather than a flag, because they read as what they
    are: `schema provider asr faster_whisper` is the same
    stage-then-name order every provider command is addressed in.
    """

    def run(
        context: typer.Context,
        entity: Annotated[
            str | None,
            typer.Argument(
                metavar="ENTITY", help=", ".join(docgen.entity_names()) + " (default: domain)"
            ),
        ] = None,
        stage: Annotated[
            str,
            typer.Argument(metavar="STAGE", help=SCHEMA_STAGE_HELP),
        ] = "",
        type_name: Annotated[
            str,
            typer.Argument(metavar="TYPE", help=SCHEMA_TYPE_HELP),
        ] = "",
    ) -> None:
        row.perform(_invocation(row, context, entity=entity, stage=stage, type_name=type_name))

    return run


def _rendered(row: Command) -> Callable[..., None]:
    """The two documents rendered from the models and from the routes,
    which take no arguments at all."""

    def run(context: typer.Context) -> None:
        row.perform(_invocation(row, context))

    return run


# What a command listing says about one entity kind's command: the verb,
# and where in the configuration document the kind lives. Read off the
# descriptor, so a kind cannot come to be described one way in the help
# and another way in the generated reference.


def _about(verb: str, kind: entities.EntityDescriptor) -> str:
    return f"{verb} {kind.location}"


def _set_epilog(name: str) -> str:
    """What follows a `set` command's help page: the one value that must
    never be typed as an argument, and then the fields the entity may
    carry.

    The second half is generated from the same `Field(description=...)`
    values the reference and the JSON Schema are rendered from, so the
    three cannot disagree and nobody has to remember to update a help
    string when a field changes. The first half is wrapped here at the
    width that half is wrapped at, because the page is printed as it was
    laid out rather than reflowed.
    """
    warning = textwrap.wrap(
        SECRET_NOT_A_PAIR,
        width=docgen.HELP_WIDTH,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return "\n".join([*warning, "", docgen.fragment_help(name)])


# The groups of the tree, keyed by the noun path they sit at
#
# A group's own help is the one fact a leaf row cannot carry, so it is
# stated here; everything else about the shape of the tree is derived
# from the words in the table below, which is what makes a three-word
# row a row rather than a special case.
#
# The five entity nouns are derived, through the same `_about` the rows'
# own help comes from, so a new kind arrives as a noun carrying its four
# verbs rather than as five edits. What stays written out is what the
# registry cannot supply: the device, the boards waiting under it, the
# default agent, and the two secret sub-nouns.
#
# A sub-noun is not invented per command. A path segment followed by an
# identity of its own is a sub-noun (`/providers/{stage}/{name}/secrets/
# {slot}`, `/devices/pending/{code}`); a trailing segment with no
# identity after it is an attribute of its parent, and reading one is a
# verb on the parent, which is what `agent preview` is.
GROUPS: dict[tuple[str, ...], str] = {
    **{(kind.name,): _about("read and write", kind) for kind in entities.ENTITIES},
    ("provider", "secret"): "credentials stored on providers.<stage>.<name>",
    ("mcp-server", "secret"): "credentials stored on mcp_servers.<name>",
    ("device",): "read and write devices.<mac>, which agents a board reaches",
    ("device", "pending"): "the boards waiting to be claimed, and claiming one",
    ("default-agent",): "the agent an unbound device reaches",
    # A noun with verbs rather than two flat words, because it has a
    # subject: the simulated board, which persists across invocations as
    # its MAC and which more than one verb asks about. `simulate` and
    # `check-in` side by side at the top level would be exactly the list
    # of things-and-actions that noun first exists to remove.
    ("simulator",): "a simulated board, checking in the way one with a screen would",
    # The conversation store's own noun, and singular for the reason the
    # cli-guide's naming rule gives: `show` and `delete` address one
    # entry. The guide is amended by the change that lands this
    # (docs/plans/2026-08-28-first-class-conversations.md), because its
    # examples spelled the noun plural before there was one.
    ("session",): "the sessions this server recorded, and erasing them",
    # And the store's other entity, singular under the same rule: `show`
    # and `delete` address one thread.
    ("conversation",): "the conversations this server recorded, and erasing them",
    # The live counterpart of the two above: the same events those
    # records are assembled from, before anything has been written down.
    # A noun with a verb rather than a flat `tail`, because the noun is
    # what the verb is about and because a second verb over the same
    # subject is where this goes next.
    #
    # The adjacent `vinga-server events reference` is a different
    # program's spelling of the same word and keeps its own home, which
    # the cli-guide's two-spellings section explains: that one prints
    # what the events ARE and needs no server, this one prints what a
    # server is saying and reaches one.
    ("events",): "what the running server is saying right now, as it says it",
}


def _entity_rows(kind: entities.EntityDescriptor) -> tuple[Command, ...]:
    """One entity kind's four verbs, in the order a reader meets them.

    Built from the descriptor rather than written out, which is the
    whole of what noun first buys here: a kind that arrives in the
    registry arrives in the grammar with a page per verb, and none of
    the four can come to be described one way in the help and another
    way in the reference.
    """
    addressed_write = _staged_write if kind.addressing == ("stage", "name") else (
        _named_write if kind.addressing else _written
    )
    addressed_read = _staged if kind.addressing == ("stage", "name") else (
        _named if kind.addressing else _plain
    )
    rows = [
        Command(
            words=(kind.name, "set"),
            kind=kind.name,
            does=SET_ENTITY[kind.name],
            declare=addressed_write,
            help=_about("create or replace", kind),
            epilog=_set_epilog(kind.name),
        ),
        Command(
            words=(kind.name, "show"),
            kind=kind.name,
            does=SHOW_ENTITY[kind.name],
            declare=addressed_read,
            help=_about("print", kind),
        ),
        Command(
            words=(kind.name, "export"),
            kind=kind.name,
            does=EXPORT_ENTITY[kind.name],
            declare=addressed_read,
            help=_about("export", kind),
        ),
    ]
    if kind.has_delete:
        rows.append(
            Command(
                words=(kind.name, "delete"),
                kind=kind.name,
                does=DELETE_ENTITY[kind.name],
                declare=addressed_read,
                help=_about("delete", kind),
                destroys=True,
            )
        )
    return tuple(rows)


COMMANDS: tuple[Command, ...] = (
    *(row for kind in entities.ENTITIES for row in _entity_rows(kind)),
    # A stored credential is addressed under the entity that holds it,
    # in the slot it fills, and `secrets` is followed by `{slot}` on the
    # API, so it is a sub-noun of the kind rather than a verb of it.
    Command(
        words=("provider", "secret", "set"),
        kind="provider",
        does=SET_SECRET,
        declare=_provider_secret,
        help="store a credential on providers.<stage>.<name>",
    ),
    Command(
        words=("provider", "secret", "clear"),
        kind="provider",
        does=CLEAR_SECRET,
        declare=_provider_slot,
        help="remove a stored credential from providers.<stage>.<name>",
        destroys=True,
    ),
    Command(
        words=("mcp-server", "secret", "set"),
        kind="mcp-server",
        does=SET_SECRET,
        declare=_mcp_secret,
        help="store a credential on mcp_servers.<name>",
    ),
    Command(
        words=("mcp-server", "secret", "clear"),
        kind="mcp-server",
        does=CLEAR_SECRET,
        declare=_mcp_slot,
        help="remove a stored credential from mcp_servers.<name>",
        destroys=True,
    ),
    # The read of the running server that belongs to the MCP entries:
    # what is stored is `mcp-server show`, and what each entry is doing
    # right now is this. A read of the process rather than of the
    # database, so there is no state to report when there is no server
    # to ask.
    #
    # Under the noun since #341, where it always belonged: it is a verb
    # of the MCP servers and of nothing else, so the flat spelling put a
    # per-noun read at the top level, next to the verbs whose subject is
    # the whole deployment. The word is unchanged and there is no alias,
    # which is the pre-release stance: a board is reflashable and a
    # deployment is this repository's own.
    Command(
        words=("mcp-server", "status"),
        kind="mcp-server",
        does=STATUS,
        declare=_plain,
        help=(
            "what each configured MCP server is doing on the running server: connected, "
            "down, or unused because no agent references it, since when, and which "
            "tools it published"
        ),
    ),
    # The read of the running server that belongs to one agent: what is
    # stored is `agent show`, and what a new session would be sent is
    # this. A verb rather than the noun `prompt`, because a noun in the
    # verb slot reads as a possessive and hides what the command does.
    Command(
        words=("agent", "preview"),
        kind="agent",
        does=PROMPT,
        declare=_named,
        help=(
            "the system prompt a new session as this agent would be sent, block by "
            "block with the size of each and the total; a conversation already running "
            "holds what it assembled when it started"
        ),
    ),
    # The device is a noun the registry does not describe: a binding is
    # a domain-level field written with verbs of its own rather than
    # from a fragment.
    #
    # Two ways to bind a board, and which one an operator wants depends
    # on what they are holding: a MAC they already know, or a device in
    # front of them showing six digits. Two verbs of one noun now, on
    # two different sub-nouns, so the pair is told apart by what it
    # addresses rather than by its help text alone.
    Command(
        words=("device", "bind"),
        kind="device",
        does=BIND_DEVICE,
        declare=_bound_by_mac,
        help="bind a device by the MAC you already know, to one or more agents",
    ),
    Command(
        words=("device", "show"),
        kind="device",
        does=SHOW_DEVICE,
        declare=_by_mac,
        help="print devices.<mac>: the agents that board is bound to",
    ),
    Command(
        words=("device", "delete"),
        kind="device",
        does=DELETE_DEVICE,
        declare=_by_mac,
        help="delete devices.<mac>, so the board it names reaches the default agent",
        destroys=True,
    ),
    Command(
        words=("device", "pending", "list"),
        kind="device",
        does=PENDING,
        declare=_plain,
        help="the devices showing an activation code, and the code each is showing",
    ),
    Command(
        words=("device", "pending", "claim"),
        kind="device",
        does=ADD_DEVICE,
        declare=_bound_by_code,
        help=(
            "bind the device showing this activation code, which is the six digits on "
            "its screen; use device bind when you know the MAC instead"
        ),
    ),
    # The setting that is a noun with two verbs. `<name>` is payload
    # rather than address: `/default-agent` has no path parameter.
    Command(
        words=("default-agent", "set"),
        does=SET_DEFAULT_AGENT,
        declare=_named,
        help="the agent an unbound device reaches",
    ),
    Command(
        words=("default-agent", "clear"),
        does=CLEAR_DEFAULT_AGENT,
        declare=_plain,
        help="unset it, leaving the devices map as the allowlist",
        destroys=True,
    ),
    # The flat verbs: their subject is the whole deployment, or nothing
    # stored at all. Inventing a noun to put in front of them would
    # invent a word for the thing the program is already about.
    #
    # First of them, because it is the one an operator runs first: which
    # deployment is this, and am I talking to the one I think I am. Two
    # acts in one row for the reason `conversation show` has two: what
    # was asked for is one thing, and the API answers it as two
    # resources because identity is the running server's and the counts
    # are the store's.
    Command(
        words=("info",),
        does=(IDENTITY, COUNTS),
        opens=_contacted,
        declare=_plain,
        help=(
            "what deployment this is: the API this CLI reached, the running server's "
            "version and revision, the URL to type into a device's captive portal, and "
            "how much of each kind is configured"
        ),
    ),
    # The one write that carries the whole configuration. Its own row
    # rather than a flag on a noun's `set`, because what it takes is a
    # document and what it promises is one transaction over all of it.
    #
    # Two acts, because applying a configuration is what the word means
    # to the person typing it: the document is written and then
    # installed on the running server. `--no-reload` is the spelling for
    # the other thing, staging a write for a later reload, and which of
    # the two an invocation runs is `_applying`'s to say. `does` is what
    # the row can reach either way, which is what coverage is about.
    Command(
        words=("apply",),
        does=(APPLY, RELOAD),
        selects=_applying,
        declare=_applied_document,
        help=(
            "write a whole document in one transaction and apply it to the running "
            "server, refused whole if anything in it will not resolve; additive, "
            "never deleting, and waiting for the write's answer however long the "
            "transaction takes; or write it without applying, with --no-reload"
        ),
    ),
    Command(words=("list",), does=LIST, declare=_plain, help="a summary tree"),
    Command(
        words=("show",),
        does=SHOW_ALL,
        declare=_plain,
        help="print the whole stored configuration, with its stored secrets masked",
    ),
    Command(
        words=("export",),
        does=EXPORT_ALL,
        declare=_plain,
        help="the stored configuration as a document apply takes",
    ),
    # The seat #193 reserved. Flat with the three above it, because its
    # subject is the deployment: it compares the whole stored half
    # against the whole running one, and there is no noun to put in
    # front of it that is not the thing the program is already about.
    Command(
        words=("diff",),
        does=DIFF,
        declare=_plain,
        help=(
            "what the stored configuration would change on the running server, kind by "
            "kind, with the boundary each kind's changes reach a conversation at"
        ),
    ),
    # The conversation store's sessions. Reads of a different schema
    # from everything above, and two erasures of it, all of them
    # requests: there is no local-database path here and there is not
    # going to be one (#281, #282).
    Command(
        words=("session", "list"),
        does=LIST_SESSIONS,
        declare=_filtered_sessions,
        help=(
            "the sessions this server recorded, newest first, one page of them; "
            "narrow it with --device and size the page with --limit"
        ),
    ),
    Command(
        words=("session", "show"),
        does=SHOW_SESSION,
        declare=_by_session,
        help=(
            "print one recorded session: the board and agent it ran with, how it "
            "ended, and what it stored"
        ),
    ),
    Command(
        words=("session", "delete"),
        does=DELETE_SESSION,
        declare=_by_session,
        help=(
            "erase one recorded session and everything it holds: its turns wherever "
            "their conversations are, the calls they made, and its events"
        ),
        destroys=True,
    ),
    Command(
        words=("session", "purge"),
        does=PURGE_SESSIONS,
        declare=_selected_sessions,
        help=(
            "erase every session the selectors name, in one transaction; at least one "
            "of --session, --device and --before is required and several are combined"
        ),
        destroys=True,
    ),
    # The conversation store's other entity: the durable thread the same
    # turns project as. Reads and one erasure, requests like the four
    # above them and for the same reason.
    Command(
        words=("conversation", "list"),
        does=LIST_CONVERSATIONS,
        declare=_filtered_conversations,
        help=(
            "the conversations this server recorded, most recently active first, one "
            "page of them; narrow it with --agent and size the page with --limit"
        ),
    ),
    Command(
        words=("conversation", "show"),
        does=(SHOW_CONVERSATION, READ_DIALOGUE),
        declare=_by_conversation,
        help=(
            "print one recorded conversation: whose thread it is, what it is called "
            "and when it ran, and then a page of what was said in it, oldest first"
        ),
    ),
    Command(
        words=("conversation", "delete"),
        does=DELETE_CONVERSATION,
        declare=_by_conversation,
        help=(
            "erase one recorded conversation: its turns out of whatever sessions they "
            "were spoken in, the calls they made, and its recap checkpoints; the "
            "sessions themselves are left with a gap rather than deleted"
        ),
        destroys=True,
    ),
    # The live half of the two above, and the one row in this table that
    # is not a request with an answer: it opens a stream and prints it
    # until it is told to stop, which is why it carries its own function
    # rather than an act (`_events_tail`).
    Command(
        words=("events", "tail"),
        does=_events_tail,
        declare=_tailed,
        help=TAIL_HELP,
    ),
    # The one command that changes what the server is doing rather than
    # what is stored, which is why it is a verb of its own rather than a
    # flag on a write: an operator writes several entries and grant
    # lists and applies them once.
    Command(
        words=("reload",),
        does=RELOAD,
        declare=_plain,
        help=(
            "apply the stored configuration to the running server, without a restart "
            "and without dropping a conversation"
        ),
    ),
    Command(
        words=("ota-url",),
        does=_ota_url,
        declare=_from_the_file_half,
        help=(
            "the URL to type into a device's captive portal; derived from this "
            "configuration and the device-auth secret, and it contacts nothing"
        ),
    ),
    # Read-only and offline: these three render the models and the
    # API's own routes, so they take no --config, open no database,
    # reach no server and need no encryption key. Keep it that way: the
    # documentation lane runs `reference` and `openapi` from a plain
    # sync, with no database, no key and no token anywhere.
    # The board nobody has to own. It reaches the OTA endpoint the way a
    # device does, and reaches the configuration API only when --claim
    # says so, which is why it takes both global options and why the
    # device half works with neither of them set.
    Command(
        words=("simulator", "check-in"),
        does=_simulator_check_in,
        declare=_simulated_board,
        help=(
            "check in to an OTA URL as a board would, and say what a board at that "
            "address would be handed"
        ),
        epilog=capabilities.epilog(docgen.HELP_WIDTH),
    ),
    Command(
        words=("simulator", "run"),
        does=_simulator_run,
        declare=_simulated_board,
        help=RUN_HELP,
        epilog=capabilities.epilog(docgen.HELP_WIDTH),
    ),
    Command(
        words=("schema",),
        does=_schema,
        declare=_of_an_entity,
        help="the JSON Schema of one entity, or of the whole domain half",
    ),
    Command(
        words=("reference",),
        does=_reference,
        declare=_rendered,
        help="the markdown reference, generated from the models",
    ),
    Command(
        words=("openapi",),
        does=_openapi,
        declare=_rendered,
        help="the configuration API's OpenAPI document, generated from its routes",
    ),
    Command(
        words=("cli-reference",),
        does=_cli_reference,
        declare=_rendered,
        help=(
            "the generated half of the CLI reference: the recipes read out of the "
            "example fragments, and every command's own help page"
        ),
    ),
)


# The order a reader meets the top level in, which is the table's own:
# the nouns in the registry's order, then the device and the default
# agent, then the flat verbs, of which `info` is the first because it is
# the one an operator runs before they know anything else.
_ORDER = tuple(dict.fromkeys(row.words[0] for row in COMMANDS))


def command() -> TyperGroup:
    """The whole grammar, as the one command that runs it.

    Built per call, the way the parser it replaces was: nothing here is
    stateful, and a fresh tree is what keeps one test reading a command's
    help from depending on what another did to it. A name rather than a
    private because the tree is what the help tests enumerate and what
    the committed command reference will be rendered from.
    """
    app = typer.Typer(
        help=DESCRIPTION,
        # Every group of this grammar is a `_Grouped`, which is where an
        # invocation that named no command is answered with this page.
        cls=_Grouped,
        # And the library's own no-args help stays off, so that decision
        # is made in one place. It would also be the wrong answer twice
        # over: it sees only the case where nothing at all followed, and
        # it is a request for help rather than a failure, while arriving
        # without a command is a failure that this grammar answers
        # helpfully. The page goes to stderr, since stdout is data and
        # this invocation produced none, and the exit stays 1, since a 0
        # would say a command completed when none was typed.
        no_args_is_help=False,
        # Neither of the two options Typer would otherwise add: this
        # group's options are the three below and nothing else.
        add_completion=False,
        # Help formatted by Click rather than by Rich, so that what it
        # prints does not depend on a terminal, on colors, or on whether
        # an optional package happens to be installed.
        rich_markup_mode=None,
        # And `-h` beside `--help`, on every page of the tree: it is the
        # spelling half the world types first, and a program that
        # answers only the long one answers nothing to that.
        context_settings={"help_option_names": HELP_OPTION_NAMES},
    )
    app.callback()(_root)
    # One group per noun path, built before anything is attached, so a
    # row three words deep finds the group its leading words name rather
    # than being registered under the first of them with the word in the
    # middle discarded.
    #
    # The same class and the same flag at every noun as at the root
    # above, so `vinga provider` and `vinga device pending` are answered
    # with their own pages rather than the root's: each group raises
    # from its own context, and the boundary prints the page of whatever
    # context it is handed.
    groups = {
        path: typer.Typer(cls=_Grouped, no_args_is_help=False, rich_markup_mode=None)
        for path in GROUPS
    }
    for row in COMMANDS:
        under = groups[row.words[:-1]] if len(row.words) > 1 else app
        under.command(
            row.words[-1],
            cls=_Verbatim,
            help=row.help,
            # Click shortens a command's help for the listing, cutting
            # it at its first sentence or at the terminal's width. These
            # are one sentence each and the listing is where an operator
            # reads them, so the short form is the same string rather
            # than a truncation of it.
            short_help=row.help,
            epilog=row.epilog,
        )(row.declare(row))
    # Deepest first, so a sub-noun is attached to its parent before that
    # parent is attached to the tree above it.
    for path in sorted(GROUPS, key=len, reverse=True):
        described = GROUPS[path]
        above = groups[path[:-1]] if len(path) > 1 else app
        above.add_typer(
            groups[path], name=path[-1], help=described, short_help=described
        )
    grammar = typer.main.get_command(app)
    # Typer registers every command before every group, which would put
    # `set` and `show` at the foot of the listing whatever the table
    # says. The order a reader meets them in is the table's, so it is
    # restored from the table rather than left to the library.
    grammar.commands = {word: grammar.commands[word] for word in _ORDER}
    return grammar


__all__ = [
    "COMMANDS",
    "NEEDS_THE_SERVER_HALF",
    "NEEDS_THE_SIM_EXTRA",
    "build_client",
    "command",
    "main",
]
