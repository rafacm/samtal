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

`--local` is the break-glass path, for a database whose server will not
start. It covers four commands (show, delete, clear-secret, set-secret),
opens the database directly, and says so on stderr every time, which is
not something to discover. When a change made that way is observed is
the write's own answer rather than the preamble's: the boot-time
snapshot is the default story, and the exceptions the server side makes
are the exceptions here too, so a `--local` device delete says the
device meets it at its next check-in and a `--local` MCP write names the
reload. Each is the sentence the API answers that same act with.

One command stands outside all of this, because onboarding a board
happens before there is anything to configure. `ota-url` derives the
string a person types into a captive portal from the file half and the
environment, and contacts nothing whatsoever. What answers on that URL
is a question for `vinga-server doctor`, which since #244 is a command
of its own: diagnosing an endpoint is not a configuration concern, and
what the two share is where the URL comes from, which is
`onboarding.origin`.

Every failure leaves as a ConfigError printed to stderr with exit code
1, naming the location and the kind of failure without quoting the value
that caused it, and no traceback from pydantic, PyYAML, SQLAlchemy,
cryptography or httpx reaches the user.
"""

import getpass
import ipaddress
import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, get_args, get_origin
from urllib.parse import quote

import httpx
import typer
import yaml
from pydantic import BaseModel, TypeAdapter, ValidationError

# Typer ships its own copy of Click rather than importing the installed
# one, so a usage error arrives as a class of that copy: `click.UsageError`
# would catch none of them, and a boundary that caught nothing would let
# Click's own sentences out as a traceback. Imported from where they
# actually are, named one by one rather than felt for through an
# ancestor, so a Typer release that moves them fails loudly at import
# instead of quietly widening what reaches an operator.
from typer._click.exceptions import (
    BadArgumentUsage,
    BadOptionUsage,
    BadParameter,
    ClickException,
    Exit,
    MissingParameter,
    NoSuchOption,
)
from typer.core import TyperCommand, TyperGroup

from vinga_server.config import docgen, entities, views
from vinga_server.config.entities import (
    BINDING_NOTICE,
    RELOAD_NOTICE,
    RESTART_NOTICE,
)
from vinga_server.config.loader import CONFIG_ENV_VAR, ConfigError, load_file_config
from vinga_server.config.models import (
    API_MOUNT_PATH,
    PROVIDER_STAGES,
    FileConfig,
    ServerConfig,
)
from vinga_server.config.printing import parsed_url, printable, shown_url
from vinga_server.config.responses import (
    Acknowledgement,
    AssembledPrompt,
    ConfigDocument,
    ConfigReloadResult,
    Envelope,
    McpServerStatus,
    PendingDevice,
)
from vinga_server.config.secrets import (
    MASK,
    EntityKind,
    SecretLocation,
    load_keys,
    provider_identity,
)
from vinga_server.config.store import ConfigStore, check_transportable
from vinga_server.db import open_database

# Imported like anything else since issue #143 split the onboarding
# package. The derivation reads the configuration models, the key
# module beside it and the standard library, and it is the one thing
# this module wants from that package: a second implementation of it is
# the one mistake that could send an operator to a URL this server does
# not serve. It was deferred until #143, because the module that held
# it also held a router over the OTA handlers and so pulled in a whole
# conversation's worth of machinery.
from vinga_server.onboarding.origin import onboarding_url

# Where the API is, when nothing says otherwise: the loopback address of
# this machine, on the port the server half of the configuration names,
# under the prefix the sub-application is mounted at. The port is read
# through the same machinery the server reads it with, so the two cannot
# disagree about it any more than they can about the database directory,
# and the prefix comes from the same constant the server mounts on.
API_URL_ENV = "VINGA_API_URL"

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

# Printed on stderr by every --local invocation, reads included. There
# is no reliable way to tell whether a server is running against the same
# file (a pid file lies after a crash and a lock probe races the answer),
# and a wrong refusal would wedge the recovery path in exactly the
# situation it exists for. Saying what this is, is the honest substitute.
LOCAL_NOTICE = (
    "--local is the break-glass path: it reads and writes the database directly, "
    "bypassing the configuration API. Each write says separately when it takes "
    "effect, the same answer the API gives for the same act."
)

# What --local does not cover, said by naming what it does. The subset is
# the recovery one: look at what is stored, take out what will not load,
# and repair a credential.
LOCAL_SUBSET = (
    "--local covers the recovery subset only: show, delete, clear-secret and "
    "set-secret. Every other command goes through the configuration API, which needs "
    "a running server."
)

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

# How a stored secret is introduced in `show` and `list`. Comment lines
# rather than a mapping: the mask is not a value that could be written
# back, and saying so in the document is more honest than rendering it
# as though it could.
SECRETS_HEADING = "# stored secrets, set with: vinga-server config set-secret"

# The pending listing's columns. Headings a person reads rather than
# field names: what the body has to carry to be read as a listing at all
# is `PendingDevice`, one import below this one.
PENDING_COLUMNS = ("code", "device", "board", "firmware", "expires")

NOTHING_CONFIGURED = (
    "this server has no MCP servers configured. An entry is written with "
    "`vinga-server config set mcp-server`, and an agent reaches it by naming it in "
    "its mcp list"
)

NOTHING_PENDING = (
    "no device is waiting to be claimed. A board shows its code within a couple of "
    "minutes of being pointed at this server, and codes are forgotten when the server "
    "restarts, so a board that has been waiting a while shows a fresh one"
)

# What to do with the URL `ota-url` prints, said beside it on stderr so
# that stdout holds the URL and nothing else.
OTA_URL_GUIDANCE = (
    "Type this into the device's captive portal, under its advanced settings, as the "
    "server address. If the board then shows a six-digit activation code, it has no "
    "agent yet: bind "
    "it with vinga-server config add-device <code> <agent>. A deployment with "
    "default_agent set covers every board already, so its boards show no code and start "
    "talking as soon as they connect."
)

# What this command does about onboarding being off. The sentence it
# goes into is `origin.ONBOARDING_OFF`, which is the derivation's own,
# and the fix is the asking command's.
ONBOARDING_OFF_FOR_URL = "Turn onboarding on for a URL short enough to type."


PROGRAM = "vinga-server config"


def main(argv: Sequence[str] | None = None) -> int:
    """Run one config command. Returns the process exit code.

    Parsing is inside the boundary, so a mistake in the grammar answers
    the way a mistake in a fragment does: a sentence on stderr and exit
    1. --help still leaves through an exit 0 of its own, because asking
    for help is not a failure."""
    try:
        _parsed(sys.argv[1:] if argv is None else argv)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def _parsed(argv: Sequence[str]) -> None:
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
    """
    problem: str | None = None
    asked_for: int | None = None
    try:
        grammar = command()
        with grammar.make_context(PROGRAM, list(argv)) as context:
            grammar.invoke(context)
        return
    except Exit as asked:
        asked_for = asked.exit_code
    except ClickException as exc:
        problem = _usage_problem(exc)
    if asked_for is not None:
        raise SystemExit(asked_for)
    raise ConfigError(problem)


# What a mistake in the grammar says
#
# Click's own sentences quote what was typed: an unknown option comes
# with a did-you-mean built from it, a bad value is repeated back, an
# unknown command names the word. A secret is never an argument of this
# CLI, and the mistake that would make one (typing the value after
# `set-secret ... api_key`) lands in exactly those sentences, so none of
# them is passed through. Each shape gets a fixed sentence of this
# grammar's own, and a shape not recognized gets the vague one, because
# a message this code has not seen is a message that may carry a value.
#
# Two tables, because Click states its usage errors two ways. The
# subclasses are chosen BY CLASS, which is the reading that cannot be
# fooled by wording; the base `UsageError` is one class for three
# different mistakes, so those are told apart by Click's own fixed
# words, which are the part of the sentence carrying no value.

# Ordered, first match wins, and a subclass comes before the class it
# extends: `MissingParameter` is a `BadParameter`, and an argument that
# is absent is not an argument that is wrong.
_USAGE_PROBLEMS: tuple[tuple[type[BaseException], str], ...] = (
    (NoSuchOption, "that is not an option of this command"),
    (MissingParameter, "a required argument is missing"),
    (BadOptionUsage, "an option was given without its value"),
    (BadArgumentUsage, "an argument was given in a shape this command does not take"),
    (BadParameter, "an argument was given a value this command does not take"),
)

# The mistake whose sentence has to say more than what went wrong,
# because the value it would have echoed is the one thing this CLI is
# built never to see: typing the secret after the slot is where an
# operator meets this, and where Click would have quoted it back.
SECRET_NEVER_AN_ARGUMENT = (
    "unrecognized extra arguments. A secret is never given as an argument: set-secret "
    "reads it from stdin, or from the variable named with --from-env"
)

_USAGE_SHAPES: tuple[tuple[str, str], ...] = (
    ("Got unexpected extra argument", SECRET_NEVER_AN_ARGUMENT),
    ("No such command", "that is not a command"),
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
            return f"{sentence}; run with --help for the grammar"
    stated = exc.format_message()
    for marker, sentence in _USAGE_SHAPES:
        if marker in stated:
            return f"{sentence}; run with --help for the grammar"
    return f"{_USAGE_UNKNOWN}; run with --help for the grammar"


# What one command was given
#
# The seam between the grammar and everything under it. Every act
# addresses its resource, builds its body and takes its break-glass path
# from one of these, and the fields are the whole vocabulary the grammar
# has: the three options accepted on either side of the command word,
# and the arguments that address one entry. Stated as a type rather than
# as a bag of attributes, so what a command can be asked is readable in
# one place and an act that reads a field nobody sets is a name that is
# not there.


@dataclass(frozen=True, kw_only=True)
class Invocation:
    """One command's arguments, resolved."""

    # The three global options, after the merge below: each is what the
    # command position said when it said anything, and what the root
    # position said otherwise.
    config: str | None = None
    api_url: str | None = None
    local: bool = False

    # What addresses one entry, under the names the descriptors'
    # `addressing` tuples use, which are the URL's path parameters and
    # the CLI's positionals for the same reason.
    stage: str = ""
    name: str = ""
    mac: str = ""
    code: str = ""
    slot: str = ""

    # Which kind of entity a command that covers two of them was asked
    # about: the word under `set-secret` and `clear-secret`, which is
    # what decides where a credential is addressed.
    kind: str = ""

    # The rest of what a command can carry: the agents a binding names,
    # the fragment a write reads, the variable a secret is read from,
    # and the entity a schema is asked for.
    agents: tuple[str, ...] = ()
    file: str = ""
    from_env: str | None = None
    entity: str | None = None


# The commands that reach no API
#
# Everything else a command does is a row in the table further down.
# These four are not acts of the configuration API at all: one is about
# onboarding a board, which happens before there is anything to
# configure, and three render the models and the API's own routes
# without opening a database, reaching a server or needing a key.


def _ota_url(args: Invocation) -> None:
    """The URL to type into a board's captive portal.

    The one command here that talks to nothing: no server, no database,
    no encryption key and no API token, because none of them holds any
    part of the answer. It reads the file half the way every other
    command reads it, takes the device-auth secret from the environment
    the server takes it from, and derives the key and the origin with
    the functions the server itself calls, so what it prints is what
    that server answers on rather than a second opinion about it.

    The URL goes to stdout alone, so it can be captured; what to do with
    it, and where its origin came from, go to stderr the way every
    other notice does.
    """
    url, origin = onboarding_url(_server_config(args), ONBOARDING_OFF_FOR_URL)
    print(url)
    sys.stdout.flush()
    print(OTA_URL_GUIDANCE, file=sys.stderr)
    print(f"The URL above is {origin.provenance}.", file=sys.stderr)


def _schema(args: Invocation) -> None:
    """The JSON Schema of one entity kind, or of the whole domain
    configuration. Reads the models and nothing else: no database, no
    configuration file, no encryption key, no server."""
    print(docgen.schema(args.entity), end="")


def _reference(args: Invocation) -> None:
    """The markdown reference, the same document CI diffs the committed
    copy against."""
    print(docgen.reference(), end="")


def _openapi(args: Invocation) -> None:
    """The configuration API's OpenAPI document, the other artifact CI
    diffs its committed copy against. Rendered from the routes, so it
    opens no database and needs no token: the application is built, its
    document is taken, and nothing of it is served."""
    print(docgen.openapi(), end="")


# Reaching the API
#
# One request per command, over a client built behind a seam the
# acceptance suite replaces with a test client, so the same entry point
# runs against the real application with no socket. What the seam does
# not cover is the addressing and the transport policy, which run in
# front of it and are what those tests are checking.


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


def _call(
    args: Invocation,
    method: str,
    path: str,
    body: object = _NOTHING,
    read_timeout_s: float = READ_TIMEOUT_S,
) -> object:
    """One request, and its answer as this client understands it.

    `read_timeout_s` is how long this one endpoint may take to answer,
    which for all but the reload is the same bound the client is built
    with. Set on the client rather than passed with the request: a
    per-request timeout is what httpx would want, and Starlette's
    TestClient refuses one outright, which would take the seam the whole
    acceptance suite runs through with it. Each call builds a client,
    makes one request and closes it, so the two are the same thing here.
    """
    file_config = load_file_config(args.config)
    base_url = _base_url(args, file_config)
    client = build_client(base_url, _token(file_config))
    client.timeout = httpx.Timeout(read_timeout_s, connect=CONNECT_TIMEOUT_S)
    try:
        response = _sent(client, method, path, body, base_url)
    finally:
        client.close()
    return _answer(response, base_url)


def _sent(
    client: httpx.Client, method: str, path: str, body: object, base_url: str
) -> httpx.Response:
    """The request, with a transport failure turned into a sentence.

    The message is built inside the handler and raised after it: an
    exception raised while another is being handled carries that one as
    its context, and httpx's exceptions carry the request, whose URL is
    one of the two things this whole policy exists to keep out of sight.
    """
    problem: str | None = None
    try:
        if body is _NOTHING:
            return client.request(method, path)
        return client.request(method, path, json=body)
    except httpx.HTTPError:
        problem = (
            f"cannot reach the configuration API at {base_url}: the request did not "
            f"complete. Check that the server is running and that this is the address "
            f"it serves. To repair a database with no server to ask, use --local, which "
            f"covers show, delete, clear-secret and set-secret."
        )
    raise ConfigError(problem)


def _answer(response: httpx.Response, base_url: str) -> object:
    """What the API said, or a sentence about why it cannot be read.

    A refusal's `detail` is the repository's own message and is passed
    through untouched, which is what keeps one vocabulary whichever way
    an operator reached the command. Anything else is reported as a
    status code and a fixed sentence: a body this client did not
    recognize did not come from the API's sanitized output, and relaying
    it would put a middlebox's page where a configuration error belongs.
    """
    payload = _payload(response)
    if response.is_success:
        if payload is _NOTHING:
            raise ConfigError(_unreadable(response, base_url))
        return payload
    if isinstance(payload, Mapping) and isinstance(payload.get("detail"), str):
        raise ConfigError(payload["detail"])
    raise ConfigError(_unreadable(response, base_url))


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


def _unreadable(response: httpx.Response, base_url: str) -> str:
    return (
        f"the configuration API at {base_url} answered {response.status_code} with "
        f"{UNRECOGNIZED_ANSWER}. It is not quoted back: what a proxy or a gateway "
        f"returns is not this API's own output."
    )


def _base_url(args: Invocation, file_config: FileConfig) -> str:
    """Where the API is: the flag, then the environment, then this
    machine on the port the server half names."""
    if args.api_url:
        return _permitted(args.api_url, "--api-url")
    named = os.environ.get(API_URL_ENV, "").strip()
    if named:
        return _permitted(named, API_URL_ENV)
    return f"http://127.0.0.1:{file_config.server.port}{API_MOUNT_PATH}"


def _permitted(url: str, source: str) -> str:
    """The transport policy, which is about the token before it is about
    any secret body.

    The bearer token crosses every request and grants everything the API
    can do, secret writes included, so loopback-or-TLS is the rule for
    the whole client rather than a set-secret footnote. There is
    deliberately no flag to override it: such a flag's only purpose would
    be sending the token in clear.
    """
    parsed = parsed_url(url, source)
    shown = shown_url(parsed)
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
            f"would cross it in clear along with anything set-secret sends. Use "
            f"https://, put a TLS-terminating tunnel in front, or exec into the "
            f"running container and reach the API on loopback. There is deliberately "
            f"no flag to override this."
        )
    return url.rstrip("/")


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
            f"environment. To repair a database whose server will not start, use "
            f"--local, which covers show, delete, clear-secret and set-secret."
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
        # Answered back as the mappings the renderers read, so that a
        # value the break-glass path built and a value that arrived over
        # HTTP reach the same renderer in the same shape. Dumping a
        # validated model is also what leaves the extras behind: only
        # what the shape declares is written back out.
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
    reference = views.reference_value(body, shadows) if shadows else None
    return f"  (used instead of {shadows}: {reference})" if reference else ""


def _bodies(config: Mapping[str, object]) -> dict[tuple[str, str], Mapping[str, object]]:
    """The masked body of every entity that can hold a stored secret,
    keyed the way a secret location names it."""
    bodies = {
        ("provider", provider_identity(stage, name)): body
        for stage, entries in config["providers"].items()
        for name, body in entries.items()
    }
    bodies.update(
        (("mcp_server", name), body) for name, body in config["mcp_servers"].items()
    )
    return bodies


def _pending_listing(answer: object) -> str:
    """The devices waiting to be claimed, one line each.

    Columns rather than YAML, because the question this answers is
    which of several boards is the one being held, and the answer is
    read across a line: the code to type, the MAC it will bind, and the
    board and firmware that tell two boards apart.
    """
    entries = _understood(dict[str, PendingDevice], answer, UNREADABLE_READ)
    if not entries:
        return f"{NOTHING_PENDING}\n"
    rows = [PENDING_COLUMNS] + [
        (code, entry["mac"], entry["board"], entry["firmware"], entry["expires_at"])
        for code, entry in entries.items()
    ]
    widths = [max(len(row[column]) for row in rows) for column in range(len(PENDING_COLUMNS))]
    return "".join(
        "  ".join(
            cell.ljust(width) for cell, width in zip(row, widths, strict=True)
        ).rstrip()
        + "\n"
        for row in rows
    )


def _status_listing(answer: object) -> str:
    """What every configured MCP server is doing, one block each.

    A block rather than a row of columns, because two of the three
    things worth reading are lists: the tools the server published, and
    the agents that may reach it. A column holding a list is a column
    that wraps, and the pending listing's shape only works because every
    one of its fields is short.
    """
    return _status_block(_understood(dict[str, McpServerStatus], answer, UNREADABLE_READ))


def _status_block(entries: Mapping[str, Mapping[str, object]]) -> str:
    """The same listing, from a status document that has already been
    read. Split from the check because the reload answers one of these
    inside its own shape, and validating what a shape already validated
    would be the second encoding this module just stopped keeping."""
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


def _prompt_listing(answer: object) -> str:
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
    body = _understood(AssembledPrompt, answer, UNREADABLE_READ)
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


def _reload_listing(answer: object) -> str:
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

    Read as one shape, the status half included, which is what the
    result declares: the outcome lists are printed name by name and the
    status half is a document a listing renders, so a stray shape
    anywhere in here would otherwise become output or a traceback.
    """
    applied = _understood(ConfigReloadResult, answer, UNREADABLE_RELOAD)
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
            + _slots(stored, "provider", provider_identity(stage, name))
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
    costs the prompt budget. `show prompt-fragment` prints one whole,
    and `prompt <agent>` prints what an agent adds up to."""
    return f" ({len(str(body.get('text', '')))} characters)"


def _agent_summary(body: Mapping[str, object]) -> str:
    """What the agent overrides, which is its body without the prompt:
    that is what the line has room for, and `show agent` is where the
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


def _fragment(path: str) -> object:
    """One entity's YAML fragment, from a file or from stdin. Parsed
    here and validated by the models in the repository, which is where
    the rule that a secret-bearing key may only name an environment
    variable already lives."""
    source = "the fragment on stdin" if path == "-" else path
    text = _stdin() if path == "-" else _file(path)
    # Rendered from the problem and the mark rather than from str(exc),
    # which quotes the offending source line back, and recorded rather
    # than raised inside the handler: a PyYAML mark holds the whole
    # buffer it was parsing, which here is the fragment, and an
    # exception raised inside a handler keeps the one being handled as
    # its __context__.
    problem: str | None = None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        detail = str(exc)
        if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
            mark = exc.problem_mark
            detail = f"{exc.problem} at line {mark.line + 1}, column {mark.column + 1}"
        problem = f"invalid YAML in {source}: {detail}"
    raise ConfigError(problem)


def _file(path: str) -> str:
    problem: str | None = None
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        problem = f"fragment file not found: {path}"
    except OSError as exc:
        problem = f"cannot read fragment file {path}: {exc.strerror}"
    raise ConfigError(problem)


def _stdin() -> str:
    return sys.stdin.read()


def _read_secret(args: Invocation) -> str:
    """The secret itself, from a named environment variable or from
    stdin. Never from an argument: arguments land in shell history and
    in the process list. An interactive terminal is read without echo;
    a pipe or a redirect is read plainly, which is what scripts use."""
    if args.from_env:
        secret = os.environ.get(args.from_env, "")
        if not secret:
            raise ConfigError(
                f"--from-env names {args.from_env}, but it is not set in the environment"
            )
        return secret

    if sys.stdin is not None and sys.stdin.isatty():
        secret = getpass.getpass("Secret (not echoed): ")
    else:
        secret = _stdin()
    # The trailing newline is the shell's, not the secret's.
    secret = secret.rstrip("\r\n")
    if not secret:
        raise ConfigError(
            "the secret is empty; pipe it in, type it at the prompt, or name the "
            "variable holding it with --from-env"
        )
    return secret


def _secret_location(args: Invocation) -> SecretLocation:
    if args.kind == "provider":
        return SecretLocation.provider(args.stage, args.name, args.slot)
    return SecretLocation.mcp_server(args.name, args.slot)


# The database, for the recovery subset only


@contextmanager
def _store(args: Invocation, keyed: bool = False) -> Iterator[ConfigStore]:
    """The repository, opened directly. Reached only through --local,
    whose four commands are the ones an operator needs when the server
    they would otherwise ask will not start.

    The keys are loaded only for the one command that needs them.
    `set-secret` encrypts, so it cannot work without a usable key and
    says so; `show`, `delete` and `clear-secret` treat ciphertext as
    opaque and never open it, and a key that will not load is one of the
    exact conditions this path exists to repair. Loading it for them
    would take the recovery tool away in the situation it is for, which
    is the same reason `open_database` does not verify secrets.
    """
    engine = open_database(_database_dir(args))
    try:
        yield ConfigStore(engine, load_keys() if keyed else None)
    finally:
        engine.dispose()


def _database_dir(args: Invocation) -> Path:
    """Where the server keeps its domain configuration, read through the
    settings machinery the server reads it with, so the two cannot
    disagree. No configuration file has to exist: without one the field
    default and the VINGA_ environment are the whole answer."""
    return load_file_config(args.config).server.database.dir


# Output


def _acknowledged(acknowledgement: Mapping[str, object]) -> None:
    """One write acknowledged: what it did, and when it takes effect.

    The one place either is printed, whichever path the write took, so
    the break-glass path and the ordinary one cannot come to describe
    one act differently. Which sentence each is, is the act's row above;
    this is where they are read out.
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
# answered with, how that answer is printed, and, for the four commands
# `--local` covers, the same act against the database instead. The
# dispatcher below is the only reader of a row, so an act's two paths
# are written beside each other and cannot come to describe one act
# differently, which is the class of drift #134 was.
#
# The five commanded kinds' rows are half built and half written out,
# split along what a kind is against what a kind does. Where a kind is
# on the API, what addresses one entry of it, which section it occupies
# in the configuration document and when a write of it takes effect are
# data on its descriptor, and the builders below read them straight off
# it. What a break-glass act does is not: it is a named `ConfigStore`
# method and the sentence the API answers that same act with, so those
# rows are written out one per kind and reached through the two tables
# above.
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
    body: Callable[[Invocation], object] | None = None

    # How long this one endpoint may take to answer. Every act but the
    # reload takes the default, whose bound is the database's.
    read_timeout_s: float = READ_TIMEOUT_S

    # The shape the API says it answers this act with, and the sentence
    # a body that is not one meets. None where the renderer reads its
    # own answer, which is every listing: those are entry points the
    # acceptance suite hands a body to directly, so the reading is
    # theirs.
    answers: object | None = None
    refusal: str = UNREADABLE_READ

    # What is printed, given the answer, whichever path produced it.
    render: Callable[[Any], None]

    # The break-glass path: the same act against the database, answering
    # what the API would have answered for it. Present for exactly the
    # four commands `--local` covers and for nothing else, which is the
    # same fact `local_ok` states on the command's row.
    local: Callable[[Invocation], Any] | None = None


def _act(args: Invocation, act: Act) -> None:
    """One act, run whichever way it was reached.

    The acknowledgement and the notice reach the same renderer either
    way. Over HTTP they are what the API answered. Locally they are
    built by the act's own `local`, saying what the API's route says for
    the same act: the sentence written out beside it, and the timing
    taken from the kind's `notice`, which is a descriptor fact because
    it is about what was written rather than about the path that wrote
    it. `test_a_local_write_acknowledges_what_the_api_acknowledges` runs
    each of these acts both ways and asserts one answer, which is what
    keeps the two spellings of a sentence from drifting apart.
    """
    if args.local:
        act.render(act.local(args))
        return
    answer = _call(
        args,
        act.method,
        act.path(args),
        act.body(args) if act.body is not None else _NOTHING,
        read_timeout_s=act.read_timeout_s,
    )
    act.render(answer if act.answers is None else _understood(act.answers, answer, act.refusal))


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
    """The fragment a write of this kind carries, refused before it
    travels if JSON has no way to say what YAML said. Where it is being
    written is the kind's own section of the configuration document and
    the identity under it, which is what such a refusal names."""

    def body(args: Invocation) -> object:
        fragment = _fragment(args.file)
        location = ".".join((descriptor.moved_key, *_identity(descriptor, args)))
        check_transportable(location, fragment)
        return fragment

    return body


# The break-glass paths, written out per kind: one entry read or one row
# deleted through the repository's own verb for that kind, answered with
# what the API answers the same act with.
#
# Per kind rather than through the descriptor, because the generic
# version needed `ConfigStore`'s methods hung on the registry as unbound
# callables, which is the store publishing its own interface through a
# global for one caller's convenience. What that caller wanted was the
# typed method, and it can have it by name. Of the two halves of the
# answer, the timing is still read from the kind's own `notice`, and
# the sentence is written out here as the route writes it out there,
# held equal by the differential test `_act` above names.

_PROVIDER = entities.descriptor("provider")
_MCP_SERVER = entities.descriptor("mcp-server")
_PROMPT_FRAGMENT = entities.descriptor("prompt-fragment")
_AGENT = entities.descriptor("agent")
_AGENT_DEFAULTS = entities.descriptor("agent-defaults")


def _deleting_provider(args: Invocation) -> Any:
    with _store(args) as store:
        store.delete_provider(args.stage, args.name)
    return {
        "wrote": f"provider {args.stage}.{args.name} deleted, with its stored secrets",
        "notice": _PROVIDER.notice,
    }


def _deleting_mcp_server(args: Invocation) -> Any:
    with _store(args) as store:
        store.delete_mcp_server(args.name)
    return {
        "wrote": f"mcp-server {args.name} deleted, with its stored secrets",
        "notice": _MCP_SERVER.notice,
    }


def _deleting_prompt_fragment(args: Invocation) -> Any:
    with _store(args) as store:
        store.delete_prompt_fragment(args.name)
    return {
        "wrote": f"prompt-fragment {args.name} deleted",
        "notice": _PROMPT_FRAGMENT.notice,
    }


def _deleting_agent(args: Invocation) -> Any:
    with _store(args) as store:
        store.delete_agent(args.name)
    return {"wrote": f"agent {args.name} deleted", "notice": _AGENT.notice}


def _showing_provider(args: Invocation) -> Any:
    with _store(args) as store:
        read = store.read_provider(args.stage, args.name)
    return views.provider(read)


def _showing_mcp_server(args: Invocation) -> Any:
    with _store(args) as store:
        read = store.read_mcp_server(args.name)
    return views.mcp_server(read)


def _showing_prompt_fragment(args: Invocation) -> Any:
    with _store(args) as store:
        read = store.read_prompt_fragment(args.name)
    return views.prompt_fragment(read)


def _showing_agent(args: Invocation) -> Any:
    with _store(args) as store:
        read = store.read_agent(args.name)
    return views.agent(read)


def _showing_agent_defaults(args: Invocation) -> Any:
    with _store(args) as store:
        read = store.read_agent_defaults()
    return views.agent_defaults(read)


# Which of them each kind's row is built with. Keyed by the same names
# the registry uses, so a kind added there without one here is a
# KeyError at import rather than a command that quietly has no
# break-glass path.
_LOCAL_DELETE: dict[str, Callable[[Invocation], Any]] = {
    "provider": _deleting_provider,
    "mcp-server": _deleting_mcp_server,
    "prompt-fragment": _deleting_prompt_fragment,
    "agent": _deleting_agent,
}

_LOCAL_SHOW: dict[str, Callable[[Invocation], Any]] = {
    "provider": _showing_provider,
    "mcp-server": _showing_mcp_server,
    "prompt-fragment": _showing_prompt_fragment,
    "agent": _showing_agent,
    "agent-defaults": _showing_agent_defaults,
}


SET_ENTITY: dict[str, Act] = {
    kind.name: Act(
        method="PUT",
        path=_entity_path(kind),
        body=_fragment_body(kind),
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
        local=_LOCAL_DELETE[kind.name],
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
        local=_LOCAL_SHOW[kind.name],
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


def _deleting_device(args: Invocation) -> Any:
    """The break-glass unbind. A running server reads the devices table,
    so the removal reaches the device at its next check-in whether the
    row was deleted through the API or, as here, underneath it, which is
    the notice this answers with.

    Plainly that notice, and not the one the API computes: the sentence
    the API answers a device write with depends on whether it has the
    named agent loaded, and this path has no loaded server to ask.
    """
    with _store(args) as store:
        # The row the repository deleted names itself, so this path
        # normalizes nothing of its own either.
        deleted = store.delete_device(args.mac)
    return {"wrote": f"device {deleted} deleted", "notice": BINDING_NOTICE}


def _showing_device(args: Invocation) -> Any:
    with _store(args) as store:
        read = store.read_device(args.mac)
    return views.device(read)


DELETE_DEVICE = Act(
    method="DELETE",
    path=_device_path,
    answers=Acknowledgement,
    refusal=UNREADABLE_WRITE,
    render=_acknowledged,
    local=_deleting_device,
)

SHOW_DEVICE = Act(
    method="GET",
    path=_device_path,
    answers=Envelope,
    render=_print_entity,
    local=_showing_device,
)

BIND_DEVICE = Act(
    method="PUT",
    path=_device_path,
    body=_binding,
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
    answers=Acknowledgement,
    refusal=UNREADABLE_WRITE,
    render=_acknowledged,
)

SET_DEFAULT_AGENT = Act(
    method="PUT",
    path=_default_agent_path,
    body=_default_agent_name,
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
# One command covers both kinds, so it asks `_secret_notice` below which
# sentence follows the entity the credential is stored on; the API says
# the same by having four secret routes, each statically one of them.


def _secret_notice(kind: EntityKind) -> str:
    """When a stored credential takes effect, which follows the entity it
    is stored on, and is now the same answer for both of them.

    The reload rebuilds the MCP entries with their credentials and the
    provider entries with theirs, so a rotation on either is applied by
    it: a credential is read as the thing that uses it is made, and a
    reload makes both again (#191). Kept as a question rather than
    collapsed into one sentence, because what decides it is still the
    entity kind and a third kind would arrive with its own answer. The
    API says the same by having four secret routes, two per kind, each
    statically one of these sentences; one CLI command covers both
    kinds, so it asks here.
    """
    return RELOAD_NOTICE if kind in ("mcp_server", "provider") else RESTART_NOTICE


def _secret_body(args: Invocation) -> object:
    return {"secret": _read_secret(args)}


def _storing_secret(args: Invocation) -> Any:
    location = _secret_location(args)
    secret = _read_secret(args)
    # The one recovery command that needs a key: it encrypts.
    with _store(args, keyed=True) as store:
        store.set_secret(location, secret)
    return {
        "wrote": f"secret for {location.describe()}",
        "notice": _secret_notice(location.kind),
    }


def _clearing_secret(args: Invocation) -> Any:
    location = _secret_location(args)
    with _store(args) as store:
        store.clear_secret(location)
    return {
        "wrote": f"secret for {location.describe()} cleared",
        "notice": _secret_notice(location.kind),
    }


SET_SECRET = Act(
    method="PUT",
    path=_secret_path,
    body=_secret_body,
    answers=Acknowledgement,
    refusal=UNREADABLE_WRITE,
    render=_acknowledged,
    local=_storing_secret,
)

CLEAR_SECRET = Act(
    method="DELETE",
    path=_secret_path,
    answers=Acknowledgement,
    refusal=UNREADABLE_WRITE,
    render=_acknowledged,
    local=_clearing_secret,
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


def _stored_config(args: Invocation) -> Any:
    with _store(args) as store:
        return views.config(store.load())


def _running_path(args: Invocation) -> str:
    return _path("runtime", "mcp-servers")


def _reload_path(args: Invocation) -> str:
    return _path("runtime", "config", "reload")


def _assembled_path(args: Invocation) -> str:
    return _path("runtime", "agents", args.name, "prompt")


LIST = Act(
    method="GET",
    path=_config_path,
    answers=ConfigDocument,
    render=_printed(_summary),
)

SHOW_ALL = Act(
    method="GET",
    path=_config_path,
    answers=ConfigDocument,
    render=_printed(_show_everything),
    local=_stored_config,
)

PENDING = Act(method="GET", path=_waiting_path, render=_printed(_pending_listing))

# A read of the running server rather than of the database, so there is
# no local row for it: what a database says about an entry is what `show
# mcp-server` prints, and a stopped server has no state to report.
STATUS = Act(method="GET", path=_running_path, render=_printed(_status_listing))

# The other read of the running server, and for the same reason no local
# row: the persona is stored and the guidance is stored, but what they
# add up to is a property of the process that loaded them.
PROMPT = Act(method="GET", path=_assembled_path, render=_printed(_prompt_listing))

# The one act that changes what a server is doing without writing
# anything, and it prints both halves of the answer: what the reload
# applied, and what every configured MCP entry is doing now that it has
# been done. No local row either, with more force than the two above:
# there is nothing to reload when there is no server.
RELOAD = Act(
    method="POST",
    path=_reload_path,
    read_timeout_s=RELOAD_READ_TIMEOUT_S,
    render=_printed(_reload_listing),
)


# The grammar
#
# One row per command: where it sits in the command tree, what it does,
# whether the break-glass path covers it, and how it declares its
# arguments. The table is the whole of the grammar and the loop at the
# foot of this module is the only reader of a row, so adding a command
# is a row rather than a paragraph of parser construction.
#
# `local_ok` is a column here for the same reason an act's two paths are
# one row above: it used to be installed imperatively on a subparser,
# where nothing could read it back, and the differential suite's claim
# to cover the whole break-glass subset was kept by review alone. A
# column is a fact a test can hold that suite to.


# What each of the three global options says, and the two positions they
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

LOCAL_HELP = (
    "read and write the database directly instead of the API: the recovery subset "
    "(show, delete, clear-secret, set-secret), for when the server will not start"
)

FILE_HELP = "YAML fragment for this entity, or - to read it from stdin"

FROM_ENV_HELP = "read the value from this variable"

STAGE_HELP = ", ".join(PROVIDER_STAGES)

PROVIDER_SLOT_HELP = "the option it fills, such as api_key"

MCP_SLOT_HELP = "env.<KEY> or headers.<KEY>"

DESCRIPTION = (
    "Read and write the domain half of the configuration: providers, "
    "MCP servers, agents, devices and their secrets. Commands go through the "
    "configuration API on the running server; --local is the recovery path."
)

# The declared copy of each option, as one annotation apiece, so a
# command that takes them says so in three lines and cannot come to
# spell one of them differently from its siblings.
#
# `None` and `False` are the not-given values, and they are answers
# rather than sentinels of convenience: neither option can be typed as
# None, and `--local` has no negative spelling, so the merge below
# reproduces argparse's `default=SUPPRESS` dance exactly. A sentinel
# object of this module's own would read back as its repr in the help,
# which is the one place these defaults are published.
ConfigOption = Annotated[str | None, typer.Option("--config", metavar="PATH", help=CONFIG_HELP)]

ApiUrlOption = Annotated[str | None, typer.Option("--api-url", metavar="URL", help=API_URL_HELP)]

LocalOption = Annotated[bool, typer.Option("--local", help=LOCAL_HELP)]


@dataclass(frozen=True, kw_only=True)
class Globals:
    """The three options, as far as the positions so far have resolved
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
    local: bool = False

    def merged(self, *, config: str | None, api_url: str | None, local: bool) -> "Globals":
        """The same options with one more position's copies folded in,
        each winning only where it was given.

        `--local` accumulates rather than overrides, because it is
        presence-only: a flag that is not there says nothing, and cannot
        unsay a flag that is.
        """
        return Globals(
            config=self.config if config is None else config,
            api_url=self.api_url if api_url is None else api_url,
            local=self.local or local,
        )


@dataclass(frozen=True, kw_only=True)
class Command:
    """One command of the grammar."""

    # Where it sits: the words that name it, root first. One word is a
    # command of the group itself, two is a command under one of the
    # groups named in `GROUPS`.
    words: tuple[str, ...]

    # What it does. An act is a request to the configuration API, or the
    # same act against the database where `--local` covers it; the four
    # commands that reach neither carry their own function instead.
    does: "Act | Callable[[Invocation], None]"

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

    # Whether `--local` covers it.
    local_ok: bool = False

    def perform(self, args: Invocation) -> None:
        """What this command does, once its arguments are in hand."""
        if isinstance(self.does, Act):
            _act(args, self.does)
            return
        self.does(args)


class _Verbatim(TyperCommand):
    """A command whose epilog is printed as it was laid out.

    Click rewraps an epilog paragraph by paragraph, which would reflow
    the field listing under a `set` command into prose. That listing is
    generated already wrapped, at a width narrower than a terminal, for
    exactly the reason argparse's raw formatter was asked for before
    this: a line that wraps on its own is worse than one wrapped on
    purpose.
    """

    def format_epilog(self, ctx: Any, formatter: Any) -> None:
        if not self.epilog:
            return
        formatter.write_paragraph()
        for line in self.epilog.splitlines():
            formatter.write(f"{line}\n")


def _root(
    context: typer.Context,
    config: ConfigOption = None,
    api_url: ApiUrlOption = None,
    local: LocalOption = False,
) -> None:
    """The three options in the position before the command word.

    Their answer is put on the context rather than passed, because the
    positions under this one add to it: a group callback folds its own
    copies in and a command folds its own in after that, and each of
    them reads one object.
    """
    context.obj = Globals(config=config, api_url=api_url, local=local)


def _resolved(context: typer.Context) -> Globals:
    """What the positions above this one made of the three options.

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
    local: bool = False,
    **addressed: Any,
) -> Invocation:
    """One command's arguments, with the three global options resolved
    and the break-glass gate passed.

    The three come in as this command's own copies, which is one of the
    positions they are accepted in; what the positions above it made of
    them is on the context, and the merge is what lets a value given
    before the command survive a command that was not given one.

    The gate is here rather than in `main` because membership of the
    recovery subset is a fact of the command and the row states it. It
    runs before the command does anything at all, which is what keeps a
    refused `--local set` from reading a fragment off stdin first.
    """
    resolved = _resolved(context).merged(config=config, api_url=api_url, local=local)
    if resolved.local:
        if not row.local_ok:
            raise ConfigError(LOCAL_SUBSET)
        print(LOCAL_NOTICE, file=sys.stderr)
    return Invocation(
        config=resolved.config,
        api_url=resolved.api_url,
        local=resolved.local,
        # Which kind a command that covers several of them was asked
        # about is its last word, which is the same string the registry
        # keys that kind under.
        kind=row.words[-1] if len(row.words) > 1 else "",
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
        local: LocalOption = False,
    ) -> None:
        row.perform(_invocation(row, context, config, api_url, local))

    return run


def _named(row: Command) -> Callable[..., None]:
    """A command addressing one entry by its name."""

    def run(
        context: typer.Context,
        name: Annotated[str, typer.Argument(metavar="NAME")],
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        local: LocalOption = False,
    ) -> None:
        row.perform(_invocation(row, context, config, api_url, local, name=name))

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
        local: LocalOption = False,
    ) -> None:
        row.perform(_invocation(row, context, config, api_url, local, stage=stage, name=name))

    return run


def _by_mac(row: Command) -> Callable[..., None]:
    """A command addressing one device by the address it connects
    with."""

    def run(
        context: typer.Context,
        mac: Annotated[str, typer.Argument(metavar="MAC")],
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        local: LocalOption = False,
    ) -> None:
        row.perform(_invocation(row, context, config, api_url, local, mac=mac))

    return run


def _written(row: Command) -> Callable[..., None]:
    """The singleton's write: a fragment and nothing to address it
    with."""

    def run(
        context: typer.Context,
        file: Annotated[str, typer.Option("-f", "--file", metavar="PATH", help=FILE_HELP)],
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        local: LocalOption = False,
    ) -> None:
        row.perform(_invocation(row, context, config, api_url, local, file=file))

    return run


def _named_write(row: Command) -> Callable[..., None]:
    """One named entity's write, from a fragment."""

    def run(
        context: typer.Context,
        name: Annotated[str, typer.Argument(metavar="NAME")],
        file: Annotated[str, typer.Option("-f", "--file", metavar="PATH", help=FILE_HELP)],
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        local: LocalOption = False,
    ) -> None:
        row.perform(_invocation(row, context, config, api_url, local, name=name, file=file))

    return run


def _staged_write(row: Command) -> Callable[..., None]:
    """One provider's write, from a fragment."""

    def run(
        context: typer.Context,
        stage: Annotated[str, typer.Argument(metavar="STAGE", help=STAGE_HELP)],
        name: Annotated[str, typer.Argument(metavar="NAME")],
        file: Annotated[str, typer.Option("-f", "--file", metavar="PATH", help=FILE_HELP)],
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        local: LocalOption = False,
    ) -> None:
        row.perform(
            _invocation(row, context, config, api_url, local, stage=stage, name=name, file=file)
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
        local: LocalOption = False,
    ) -> None:
        row.perform(
            _invocation(
                row, context, config, api_url, local,
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
        local: LocalOption = False,
    ) -> None:
        row.perform(
            _invocation(
                row, context, config, api_url, local, name=name, slot=slot, from_env=from_env
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
        local: LocalOption = False,
    ) -> None:
        row.perform(
            _invocation(row, context, config, api_url, local, stage=stage, name=name, slot=slot)
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
        local: LocalOption = False,
    ) -> None:
        row.perform(_invocation(row, context, config, api_url, local, name=name, slot=slot))

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
        local: LocalOption = False,
    ) -> None:
        row.perform(
            _invocation(row, context, config, api_url, local, mac=mac, agents=tuple(agents))
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
        local: LocalOption = False,
    ) -> None:
        row.perform(
            _invocation(row, context, config, api_url, local, code=code, agents=tuple(agents))
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
    """The schema command, which names one entity kind or none."""

    def run(
        context: typer.Context,
        entity: Annotated[
            str | None,
            typer.Argument(
                metavar="ENTITY", help=", ".join(docgen.entity_names()) + " (default: domain)"
            ),
        ] = None,
    ) -> None:
        row.perform(_invocation(row, context, entity=entity))

    return run


def _rendered(row: Command) -> Callable[..., None]:
    """The two documents rendered from the models and from the routes,
    which take no arguments at all."""

    def run(context: typer.Context) -> None:
        row.perform(_invocation(row, context))

    return run


def _whole_or_one(row: Command) -> Callable[..., None]:
    """The one group word that is also a command: `show` alone is the
    whole configuration, `show <kind>` is one entity.

    Its options are accepted in its own position either way, so when a
    kind follows they are folded into what the command under it reads
    rather than acted on here.
    """

    def run(
        context: typer.Context,
        config: ConfigOption = None,
        api_url: ApiUrlOption = None,
        local: LocalOption = False,
    ) -> None:
        if context.invoked_subcommand is not None:
            context.obj = _resolved(context).merged(config=config, api_url=api_url, local=local)
            return
        row.perform(_invocation(row, context, config, api_url, local))

    return run


# What a command listing says about one entity kind's command: the verb,
# and where in the configuration document the kind lives. Read off the
# descriptor, so a kind cannot come to be described one way in the help
# and another way in the generated reference.


def _about(verb: str, kind: entities.EntityDescriptor) -> str:
    return f"{verb} {kind.location}"


# The four groups that are only groups, and the one that is also a
# command. A group's own help is the one fact a leaf row cannot carry,
# so it is stated here; everything else about the shape of the tree is
# derived from the words in the table below.
GROUPS: dict[str, str] = {
    "set": "create or replace one entity from a YAML fragment",
    "delete": "delete one entity",
    "set-secret": "store one credential, encrypted, read from stdin or a variable",
    "clear-secret": "remove one stored credential",
    "show": "everything, or one entity",
}


COMMANDS: tuple[Command, ...] = (
    *(
        Command(
            words=("set", kind.name),
            does=SET_ENTITY[kind.name],
            declare=_staged_write if kind.addressing == ("stage", "name") else (
                _named_write if kind.addressing else _written
            ),
            help=_about("create or replace", kind),
            # Generated from the same Field(description=...) values the
            # reference and the JSON Schema are rendered from, so the
            # three cannot disagree and nobody has to remember to update
            # a help string when a field changes.
            epilog=docgen.fragment_help(kind.name),
        )
        for kind in entities.ENTITIES
    ),
    *(
        Command(
            words=("delete", kind.name),
            does=DELETE_ENTITY[kind.name],
            declare=_staged if kind.addressing == ("stage", "name") else _named,
            help=_about("delete", kind),
            local_ok=True,
        )
        for kind in entities.ENTITIES
        if kind.has_delete
    ),
    Command(
        words=("delete", "device"),
        does=DELETE_DEVICE,
        declare=_by_mac,
        help="delete devices.<mac>, so the board it names reaches the default agent",
        local_ok=True,
    ),
    # Two ways to bind a board, and which one an operator wants depends
    # on what they are holding: a MAC they already know, or a device in
    # front of them showing six digits. The help text says exactly that,
    # because the pair is otherwise the kind of thing a person picks
    # wrongly once and then remembers wrongly.
    Command(
        words=("bind-device",),
        does=BIND_DEVICE,
        declare=_bound_by_mac,
        help="bind a device by the MAC you already know, to one or more agents",
    ),
    Command(
        words=("add-device",),
        does=ADD_DEVICE,
        declare=_bound_by_code,
        help=(
            "bind the device showing this activation code, which is the six digits on "
            "its screen; use bind-device when you know the MAC instead"
        ),
    ),
    Command(
        words=("pending",),
        does=PENDING,
        declare=_plain,
        help="the devices showing an activation code, and the code each is showing",
    ),
    # A read of the running server rather than of the database, and the
    # reason neither this nor the next takes --local: there is no state
    # to report when there is no server to ask.
    Command(
        words=("status",),
        does=STATUS,
        declare=_plain,
        help=(
            "what each configured MCP server is doing on the running server: connected, "
            "down, or unused because no agent references it, since when, and which "
            "tools it published"
        ),
    ),
    # The other read of the running server, and the one that answers
    # what the model is actually given: the configuration says what an
    # agent is made of, and this says what that adds up to.
    Command(
        words=("prompt",),
        does=PROMPT,
        declare=_named,
        help=(
            "the system prompt a new session as this agent would be sent, block by "
            "block with the size of each and the total; a conversation already running "
            "holds what it assembled when it started"
        ),
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
    Command(
        words=("set-default-agent",),
        does=SET_DEFAULT_AGENT,
        declare=_named,
        help="the agent an unbound device reaches",
    ),
    Command(
        words=("clear-default-agent",),
        does=CLEAR_DEFAULT_AGENT,
        declare=_plain,
        help="unset it, leaving the devices map as the allowlist",
    ),
    Command(
        words=("set-secret", "provider"),
        does=SET_SECRET,
        declare=_provider_secret,
        help="store a credential on providers.<stage>.<name>",
        local_ok=True,
    ),
    Command(
        words=("set-secret", "mcp-server"),
        does=SET_SECRET,
        declare=_mcp_secret,
        help="store a credential on mcp_servers.<name>",
        local_ok=True,
    ),
    Command(
        words=("clear-secret", "provider"),
        does=CLEAR_SECRET,
        declare=_provider_slot,
        help="remove a stored credential from providers.<stage>.<name>",
        local_ok=True,
    ),
    Command(
        words=("clear-secret", "mcp-server"),
        does=CLEAR_SECRET,
        declare=_mcp_slot,
        help="remove a stored credential from mcp_servers.<name>",
        local_ok=True,
    ),
    Command(words=("list",), does=LIST, declare=_plain, help="a summary tree"),
    # Read-only and local: these three render the models and the API's
    # own routes, so they take no --config, open no database, reach no
    # server and need no encryption key. Keep it that way: the
    # documentation lane runs `config reference` and `config openapi`
    # from a plain sync, with no database, no key and no token anywhere.
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
        words=("show",),
        does=SHOW_ALL,
        declare=_whole_or_one,
        help=GROUPS["show"],
        local_ok=True,
    ),
    *(
        Command(
            words=("show", kind.name),
            does=SHOW_ENTITY[kind.name],
            declare=_staged if kind.addressing == ("stage", "name") else (
                _named if kind.addressing else _plain
            ),
            help=_about("print", kind),
            local_ok=True,
        )
        for kind in entities.ENTITIES
    ),
    Command(
        words=("show", "device"),
        does=SHOW_DEVICE,
        declare=_by_mac,
        help="print devices.<mac>: the agents that board is bound to",
        local_ok=True,
    ),
)


# The order a reader meets the commands in, which is the table's own.
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
        # A group with nothing after it is a mistake in the grammar, not
        # a request for help: `vinga-server config` on its own answers
        # the way every other mistake does.
        no_args_is_help=False,
        # Neither of the two options Typer would otherwise add: this
        # group's options are the three below and nothing else.
        add_completion=False,
        # Help formatted by Click rather than by Rich, so that what it
        # prints does not depend on a terminal, on colors, or on whether
        # an optional package happens to be installed.
        rich_markup_mode=None,
    )
    app.callback()(_root)
    groups = {word: typer.Typer(no_args_is_help=False, rich_markup_mode=None) for word in GROUPS}
    for row in COMMANDS:
        declared = row.declare(row)
        if len(row.words) == 1 and row.words[0] in groups:
            groups[row.words[0]].callback(invoke_without_command=True)(declared)
            continue
        under = groups[row.words[0]] if len(row.words) > 1 else app
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
        )(declared)
    for word, described in GROUPS.items():
        app.add_typer(
            groups[word],
            name=word,
            help=described,
            short_help=described,
            invoke_without_command=any(row.words == (word,) for row in COMMANDS),
        )
    grammar = typer.main.get_command(app)
    # Typer registers every command before every group, which would put
    # `set` and `show` at the foot of the listing whatever the table
    # says. The order a reader meets them in is the table's, so it is
    # restored from the table rather than left to the library.
    grammar.commands = {word: grammar.commands[word] for word in _ORDER}
    return grammar


__all__ = ["COMMANDS", "RESTART_NOTICE", "build_client", "command", "main"]
