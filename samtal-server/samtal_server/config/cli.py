"""The `samtal-server config` command group: a client of the API.

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
opens the database directly, and says so on stderr every time: a change
made that way is not observed by a running server until its next start,
which is the boot-time snapshot contract rather than a hazard, but it is
not something to discover. Device bindings are the exception the server
side makes, so a `--local` device delete says the device meets it at its
next check-in, the same sentence the API answers that delete with.

Two commands stand outside all of this, because onboarding a board
happens before there is anything to configure. `ota-url` derives the
string a person types into a captive portal from the file half and the
environment, and contacts nothing whatsoever. `doctor` asks one URL what
it would tell a device, which is a GET of the OTA endpoint rather than an
API call: no bearer token is sent, and a plain http:// address is
ordinary rather than refused, since that is exactly what a device on a
LAN is pointed at.

Every failure leaves as a ConfigError printed to stderr with exit code
1, naming the location and the kind of failure without quoting the value
that caused it, and no traceback from pydantic, PyYAML, SQLAlchemy,
cryptography or httpx reaches the user.
"""

import argparse
import getpass
import ipaddress
import os
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn
from urllib.parse import SplitResult, quote, urlsplit, urlunsplit

import httpx
import yaml

from samtal_server.config import docgen, views
from samtal_server.config.loader import CONFIG_ENV_VAR, ConfigError, load_file_config
from samtal_server.config.models import (
    API_MOUNT_PATH,
    PROVIDER_STAGES,
    FileConfig,
    ServerConfig,
)
from samtal_server.config.secrets import (
    MASK,
    SecretLocation,
    load_keys,
)
from samtal_server.config.store import ConfigStore, check_transportable
from samtal_server.config.writes import (
    BINDING_NOTICE,
    RESTART_NOTICE,
    cleared_secret,
    deleted_agent,
    deleted_device,
    deleted_mcp_server,
    deleted_prompt_fragment,
    deleted_provider,
    wrote_secret,
)
from samtal_server.db import open_database

if TYPE_CHECKING:
    # Names only. The onboarding module serves the OTA handlers, so
    # importing it pulls in a whole conversation's worth of machinery;
    # the two commands that need it import it in their own bodies, and
    # `config reference` and `config openapi` keep loading nothing but
    # the models and the routes.
    from samtal_server.onboarding import Origin

# Where the API is, when nothing says otherwise: the loopback address of
# this machine, on the port the server half of the configuration names,
# under the prefix the sub-application is mounted at. The port is read
# through the same machinery the server reads it with, so the two cannot
# disagree about it any more than they can about the database directory,
# and the prefix comes from the same constant the server mounts on.
API_URL_ENV = "SAMTAL_API_URL"

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
# MCP connect timeout plus small change: stops run concurrently under a
# short bound and starts run concurrently under the connect timeout, so
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
    "bypassing the configuration API, and a running server will not observe a change "
    "made this way until its next start, device bindings excepted: those it reads as "
    "a device asks for them."
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

# How a stored secret is introduced in `show` and `list`. Comment lines
# rather than a mapping: the mask is not a value that could be written
# back, and saying so in the document is more honest than rendering it
# as though it could.
SECRETS_HEADING = "# stored secrets, set with: samtal-server config set-secret"

# The pending listing's columns, and what a listing of nothing says. The
# fields are also what a body has to carry to be read as a listing at
# all.
PENDING_COLUMNS = ("code", "device", "board", "firmware", "expires")

PENDING_FIELDS = frozenset({"mac", "board", "firmware", "expires_at"})

# What a status entry has to carry to be read as one at all. The same
# rule the pending listing applies: a body that does not carry these did
# not come from this API.
STATUS_FIELDS = frozenset({"state", "reason", "since", "tools", "grants"})

# And what `state` may say. The vocabulary is part of the shape: a
# rendering that printed whatever arrived there would be printing a word
# chosen by whatever answered.
STATUS_STATES = frozenset({"connected", "down", "unused"})

# What one block of an assembled prompt has to carry to be read as one.
PROMPT_BLOCK_FIELDS = frozenset({"provenance", "characters", "text"})

# What a reload did, in the order a person reads it: what arrived, what
# was made again, what went, and what nothing happened to. Also what a
# body has to carry to be read as a reload's answer at all.
RELOAD_OUTCOMES = ("started", "restarted", "stopped", "unchanged")

NOTHING_CONFIGURED = (
    "this server has no MCP servers configured. An entry is written with "
    "`samtal-server config set mcp-server`, and an agent reaches it by naming it in "
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
    "it with samtal-server config add-device <code> <agent>. A deployment with "
    "default_agent set covers every board already, so its boards show no code and start "
    "talking as soon as they connect."
)

# Said when there is no short URL to print, with the fix the command
# that asked for one needs. The configured `ota_path` segment is named
# and never quoted: it is a credential, and the derived key is the one
# recorded exception to that rule.
ONBOARDING_OFF = (
    "device onboarding is off (server.onboarding.enabled is false), so this "
    "configuration serves no short URL. Devices are configured at the path "
    "server.ota_path names, on {origin} ({provenance}), and that segment is not printed "
    "here, since it is this deployment's secret. {fix}"
)

ONBOARDING_OFF_FOR_URL = "Turn onboarding on for a URL short enough to type."

ONBOARDING_OFF_FOR_DOCTOR = (
    "Give the URL to check as an argument: samtal-server config doctor URL."
)

# What a URL given to `doctor` is called in every line it prints.
#
# The one URL these commands may show is the derived short one, whose
# key is the recorded exception to "a path segment in front of the token
# issuer is a credential". A URL an operator passes is not that: the
# documented way to check a deployment with onboarding turned off is to
# pass the legacy `ota_path` URL, which is exactly the segment nothing
# may print. So a supplied URL is never displayed, in any verdict, and
# this stands in for it.
SUPPLIED_ENDPOINT = "the supplied OTA endpoint"

# How much of anything that arrived in a response may be repeated back.
# What `doctor` reaches may be a proxy, a captive portal or anything
# else that answers, so the version it claims and the URL it names are
# attacker-controlled text: bounded and printable, or not printed. The
# rule is the one `onboarding._fact` applies to what a device says about
# itself, kept here rather than imported for the reason the onboarding
# import is in a function body. The body itself is never repeated at
# all, bounded or otherwise.
GLIMPSE_LENGTH = 120

# How many redirects `doctor` follows. One, and one is already more
# than a current server produces: since the hardware checkpoint of
# 2026-08-13 every device-facing route answers both spellings of its
# path directly, because the firmware does not follow a redirect on
# that request either. What is left for this to meet is a server older
# than that change, or a proxy in front of one that canonicalizes a
# missing trailing slash. The limit is explicit rather than left to the
# client's default of twenty, since every hop past the first is a hop
# somebody else chose.
CANONICAL_REDIRECTS = 1

# How much of a body is looked at at all. The description this reads is
# three short lines, so a few kilobytes is generous; what the bound is
# for is a megabyte of anything, which nothing should walk a pattern
# over.
PARSED_BODY_LENGTH = 4096

# The endpoint's own description of itself, which is what tells a
# samtal-server from anything else answering at that address. Parsed
# rather than shared as a format string: this is a client of an HTTP
# endpoint, the way the API answers above are parsed, and a unit test
# runs the real handler's body through these patterns so that a change
# to what it prints cannot pass unnoticed.
DESCRIBE_FIRST_LINE = re.compile(
    r"^samtal-server (?P<version>\S{1,64}) \(revision [^)\n]{0,64}\) OTA endpoint\."
)

DESCRIBE_WEBSOCKET_LINE = re.compile(
    r"^Devices are sent to (?P<websocket>\S{1,256}) "
    r"\(protocol version (?P<protocol>[^)\n]{0,32})\)\.",
    re.MULTILINE,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one config command. Returns the process exit code.

    Parsing is inside the boundary, so a mistake in the grammar answers
    the way a mistake in a fragment does: a sentence on stderr and exit
    1. --help still leaves through argparse's own exit 0, because asking
    for help is not a failure."""
    try:
        args = _parser().parse_args(argv)
        if args.local:
            if not args.local_ok:
                raise ConfigError(LOCAL_SUBSET)
            print(LOCAL_NOTICE, file=sys.stderr)
        args.run(args)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


class _Parser(argparse.ArgumentParser):
    """A parser whose usage errors leave through the same door as every
    other failure.

    argparse writes to stderr and exits 2 from inside parse_args, which
    would make an unknown command or a missing argument the one failure
    that bypasses the ConfigError boundary and the documented exit
    codes. Subparsers inherit this class from the parser that creates
    them, so the whole grammar answers alike."""

    def error(self, message: str) -> NoReturn:
        raise ConfigError(_usage_problem(message))


def _usage_problem(message: str) -> str:
    if message.startswith("unrecognized arguments"):
        # Never the arguments themselves. A secret is never an argument
        # of this CLI, and the mistake that would put one there (typing
        # the value after `set-secret ... api_key`) lands exactly here,
        # where argparse would have echoed it back.
        return (
            "unrecognized extra arguments; run with --help for the grammar. Note that a "
            "secret is never given as an argument: set-secret reads it from stdin, or "
            "from the variable named with --from-env"
        )
    return f"{message}; run with --help for the grammar"


# The commands


def _set_provider(args: argparse.Namespace) -> None:
    fragment = _fragment(args.file)
    check_transportable(f"providers.{args.stage}.{args.name}", fragment)
    _wrote(_call(args, "PUT", _path("providers", args.stage, args.name), fragment))


def _set_mcp_server(args: argparse.Namespace) -> None:
    fragment = _fragment(args.file)
    check_transportable(f"mcp_servers.{args.name}", fragment)
    _wrote(_call(args, "PUT", _path("mcp-servers", args.name), fragment))


def _set_prompt_fragment(args: argparse.Namespace) -> None:
    fragment = _fragment(args.file)
    check_transportable(f"prompt_fragments.{args.name}", fragment)
    _wrote(_call(args, "PUT", _path("prompt-fragments", args.name), fragment))


def _set_agent(args: argparse.Namespace) -> None:
    fragment = _fragment(args.file)
    check_transportable(f"agents.{args.name}", fragment)
    _wrote(_call(args, "PUT", _path("agents", args.name), fragment))


def _set_agent_defaults(args: argparse.Namespace) -> None:
    fragment = _fragment(args.file)
    check_transportable("agent_defaults", fragment)
    _wrote(_call(args, "PUT", _path("agent-defaults"), fragment))


def _delete_provider(args: argparse.Namespace) -> None:
    if args.local:
        with _store(args) as store:
            store.delete_provider(args.stage, args.name)
        _report(deleted_provider(args.stage, args.name))
        return
    _wrote(_call(args, "DELETE", _path("providers", args.stage, args.name)))


def _delete_mcp_server(args: argparse.Namespace) -> None:
    if args.local:
        with _store(args) as store:
            store.delete_mcp_server(args.name)
        _report(deleted_mcp_server(args.name))
        return
    _wrote(_call(args, "DELETE", _path("mcp-servers", args.name)))


def _delete_prompt_fragment(args: argparse.Namespace) -> None:
    if args.local:
        with _store(args) as store:
            store.delete_prompt_fragment(args.name)
        _report(deleted_prompt_fragment(args.name))
        return
    _wrote(_call(args, "DELETE", _path("prompt-fragments", args.name)))


def _delete_agent(args: argparse.Namespace) -> None:
    if args.local:
        with _store(args) as store:
            store.delete_agent(args.name)
        _report(deleted_agent(args.name))
        return
    _wrote(_call(args, "DELETE", _path("agents", args.name)))


def _delete_device(args: argparse.Namespace) -> None:
    if args.local:
        with _store(args) as store:
            # The row the repository deleted names itself, so this path
            # normalizes nothing of its own either.
            deleted = store.delete_device(args.mac)
        # The same sentence the API answers this delete with. A running
        # server reads the devices table, so the removal reaches the
        # device at its next check-in whether the row was deleted
        # through the API or, as here, underneath it.
        _report(deleted_device(deleted), BINDING_NOTICE)
        return
    _wrote(_call(args, "DELETE", _path("devices", args.mac)))


def _bind_device(args: argparse.Namespace) -> None:
    _wrote(_call(args, "PUT", _path("devices", args.mac), {"agents": list(args.agents)}))


def _add_device(args: argparse.Namespace) -> None:
    _wrote(
        _call(
            args,
            "POST",
            _path("devices", "pending", args.code),
            {"agents": list(args.agents)},
        )
    )


def _pending(args: argparse.Namespace) -> None:
    print(_pending_listing(_call(args, "GET", _path("devices", "pending"))), end="")


def _status(args: argparse.Namespace) -> None:
    """What the running server's MCP servers are doing.

    A read of the server rather than of the database, so there is no
    --local for it: what a database says about an entry is what `show
    mcp-server` prints, and a stopped server has no state to report.
    """
    print(_status_listing(_call(args, "GET", _path("runtime", "mcp-servers"))), end="")


def _prompt(args: argparse.Namespace) -> None:
    """The system prompt a new session as this agent would be sent.

    A read of the server rather than of the database, so there is no
    --local: the persona is stored, the guidance is stored, and what
    they add up to is a property of the process that loaded them.
    """
    print(
        _prompt_listing(_call(args, "GET", _path("runtime", "agents", args.name, "prompt"))),
        end="",
    )


def _reload(args: argparse.Namespace) -> None:
    """Apply the stored MCP servers and grants to the running server.

    The one command that changes what a server is doing without writing
    anything, and it prints both halves of the answer: what the reload
    did, and what every configured entry is doing now that it has been
    done. No --local, for the reason `status` has none, and with more
    force: there is nothing to reload when there is no server.
    """
    print(
        _reload_listing(
            _call(
                args,
                "POST",
                _path("runtime", "mcp-servers", "reload"),
                read_timeout_s=RELOAD_READ_TIMEOUT_S,
            )
        ),
        end="",
    )


def _ota_url(args: argparse.Namespace) -> None:
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
    url, origin = _onboarding_url(_server_config(args), ONBOARDING_OFF_FOR_URL)
    print(url)
    sys.stdout.flush()
    print(OTA_URL_GUIDANCE, file=sys.stderr)
    print(f"The URL above is {origin.provenance}.", file=sys.stderr)


def _doctor(args: argparse.Namespace) -> None:
    """What a device pointed at a URL would be told, asked from where a
    device stands.

    Four answers, which are the four states worth telling apart: the
    address cannot be reached, something other than samtal-server
    answers there, samtal-server answers but sends devices to a plain
    `ws://` URL from behind TLS, or it is healthy and this says what a
    device is handed. Only the first three are failures, and they leave
    the way every other failure does.

    Every line names the endpoint by `shown` rather than by the URL: a
    derived URL is the one this command may print, and anything an
    operator passed may be the deployment's secret `ota_path`.
    """
    if args.url:
        url = _device_url(args.url, "the URL given to doctor")
        shown = SUPPLIED_ENDPOINT
    else:
        derived, _ = _onboarding_url(_server_config(args), ONBOARDING_OFF_FOR_DOCTOR)
        url = _device_url(derived, "the onboarding URL this configuration derives")
        shown = url
    response = _probed(url, shown)
    reported = _describe(response.text)
    if reported is None:
        raise ConfigError(_not_samtal_server(shown, response))
    answered = _reported_websocket(reported["websocket"])
    if answered is None:
        raise ConfigError(_unreadable_websocket(shown))
    scheme, websocket = answered
    # Both sides normalized, and the probe's side taken from the
    # response rather than from the string an operator typed: `HTTPS://`
    # is the same scheme as `https://`, and a URL that redirected is
    # answered by wherever it ended up.
    if response.url.scheme == "https" and scheme == "ws":
        raise ConfigError(_plain_websocket(shown, websocket))
    print(
        f"{shown} is samtal-server {_printable(reported['version'])}, and sends devices "
        f"to {websocket} (protocol version {_printable(reported['protocol'])})."
    )


def _set_default_agent(args: argparse.Namespace) -> None:
    _wrote(_call(args, "PUT", _path("default-agent"), {"name": args.name}))


def _clear_default_agent(args: argparse.Namespace) -> None:
    _wrote(_call(args, "DELETE", _path("default-agent")))


def _set_secret(args: argparse.Namespace) -> None:
    location = _secret_location(args)
    secret = _read_secret(args)
    if args.local:
        # The one recovery command that needs a key: it encrypts.
        with _store(args, keyed=True) as store:
            store.set_secret(location, secret)
        _report(wrote_secret(location.describe()))
        return
    _wrote(_call(args, "PUT", _secret_path(args), {"secret": secret}))


def _clear_secret(args: argparse.Namespace) -> None:
    location = _secret_location(args)
    if args.local:
        with _store(args) as store:
            store.clear_secret(location)
        _report(cleared_secret(location.describe()))
        return
    _wrote(_call(args, "DELETE", _secret_path(args)))


def _list(args: argparse.Namespace) -> None:
    print(_summary(_document(_call(args, "GET", _path("config")))), end="")


def _show_all(args: argparse.Namespace) -> None:
    if args.local:
        with _store(args) as store:
            document = views.config(store.load())
    else:
        document = _document(_call(args, "GET", _path("config")))
    print(_show_everything(document), end="")


def _show_provider(args: argparse.Namespace) -> None:
    if args.local:
        with _store(args) as store:
            _print_entity(views.provider(store.read_provider(args.stage, args.name)))
        return
    _print_entity(_envelope(_call(args, "GET", _path("providers", args.stage, args.name))))


def _show_mcp_server(args: argparse.Namespace) -> None:
    if args.local:
        with _store(args) as store:
            _print_entity(views.mcp_server(store.read_mcp_server(args.name)))
        return
    _print_entity(_envelope(_call(args, "GET", _path("mcp-servers", args.name))))


def _show_prompt_fragment(args: argparse.Namespace) -> None:
    if args.local:
        with _store(args) as store:
            _print_entity(views.prompt_fragment(store.read_prompt_fragment(args.name)))
        return
    _print_entity(_envelope(_call(args, "GET", _path("prompt-fragments", args.name))))


def _show_agent(args: argparse.Namespace) -> None:
    if args.local:
        with _store(args) as store:
            _print_entity(views.agent(store.read_agent(args.name)))
        return
    _print_entity(_envelope(_call(args, "GET", _path("agents", args.name))))


def _show_agent_defaults(args: argparse.Namespace) -> None:
    if args.local:
        with _store(args) as store:
            _print_entity(views.agent_defaults(store.read_agent_defaults()))
        return
    _print_entity(_envelope(_call(args, "GET", _path("agent-defaults"))))


def _show_device(args: argparse.Namespace) -> None:
    if args.local:
        with _store(args) as store:
            _print_entity(views.device(store.read_device(args.mac)))
        return
    _print_entity(_envelope(_call(args, "GET", _path("devices", args.mac))))


def _schema(args: argparse.Namespace) -> None:
    """The JSON Schema of one entity kind, or of the whole domain
    configuration. Reads the models and nothing else: no database, no
    configuration file, no encryption key, no server."""
    print(docgen.schema(args.entity), end="")


def _reference(args: argparse.Namespace) -> None:
    """The markdown reference, the same document CI diffs the committed
    copy against."""
    print(docgen.reference(), end="")


def _openapi(args: argparse.Namespace) -> None:
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


def build_client(base_url: str, token: str | None = None) -> httpx.Client:
    """The connection to the configuration API, or to the OTA endpoint.

    The one seam in this module. `cli.main()` is and stays synchronous,
    and httpx's ASGI transport is async-only, so the tests replace this
    with Starlette's TestClient: itself a synchronous `httpx.Client`
    subclass that drives an ASGI application through its own portal.

    Without a token there is no Authorization header, which is what
    `doctor` needs: the OTA endpoint is the token issuer, so it cannot
    require one, and sending the API's bearer token to a device-facing
    address (or to whatever answers there instead) would hand it to
    something that never asked.
    """
    return httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}"} if token else {},
        timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
    )


_NOTHING = object()


def _call(
    args: argparse.Namespace,
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


def _base_url(args: argparse.Namespace, file_config: FileConfig) -> str:
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
    parsed = _parsed(url, source)
    shown = _without_userinfo(parsed)
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


def _parsed(url: str, source: str) -> SplitResult:
    """The URL, split, with the parser's own failures kept inside the
    boundary.

    `urlsplit` raises on a malformed IPv6 literal and `.port` raises on
    a port that is not a number, and both put the text they refused into
    the exception. Outside a handler that is a traceback out of main()
    with the address in it; inside one it is a fixed sentence. The
    address is not quoted even here: what a mistyped URL holds is
    whatever was being typed, and the one thing an operator is typing
    around this command is a token.

    Both are provoked deliberately rather than trusted to happen later:
    `.port` is read here so that its refusal belongs to this function
    rather than to whichever caller touches it first.
    """
    problem: str | None = None
    try:
        parsed = urlsplit(url)
        # Read rather than trusted to be read later: `.port` parses on
        # access, so this is where its refusal belongs rather than in
        # whichever caller touches it first.
        _ = parsed.port
        return parsed
    except ValueError:
        problem = (
            f"{source} is not a URL this client can read. It has to be an http:// or "
            f"https:// address with a host, and a port if it names one has to be a "
            f"number. It is not quoted back, because a mistyped address holds whatever "
            f"was being typed."
        )
    raise ConfigError(problem)


def _without_userinfo(parsed: SplitResult) -> str:
    """The URL as it may be printed. A credential written into a URL is
    refused, and the refusal must not be the thing that publishes it."""
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, ""))


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


def _secret_path(args: argparse.Namespace) -> str:
    if args.kind == "provider":
        return _path("providers", args.stage, args.name, "secrets", args.slot)
    return _path("mcp-servers", args.name, "secrets", args.slot)


def _envelope(answer: object) -> dict[str, object]:
    """One entity read, as the API returns it."""
    if (
        isinstance(answer, Mapping)
        and isinstance(answer.get("entity"), Mapping)
        and isinstance(answer.get("secrets"), Mapping)
    ):
        return dict(answer)
    raise ConfigError(f"the configuration API answered a read with {UNRECOGNIZED_ANSWER}")


def _document(answer: object) -> dict[str, object]:
    """The whole configuration, as the API returns it."""
    if (
        isinstance(answer, Mapping)
        and isinstance(answer.get("config"), Mapping)
        and isinstance(answer.get("secrets"), list)
    ):
        return dict(answer)
    raise ConfigError(f"the configuration API answered a read with {UNRECOGNIZED_ANSWER}")


# The onboarding URL, and what answers on it
#
# Neither of these two commands is about the domain configuration, and
# neither goes near the API: one derives a string from the file half and
# the environment, and the other asks a device-facing endpoint what it
# would tell a device. What they share with the rest of this module is
# its discipline about values that came from somewhere else, which is
# stricter here than anywhere: a URL an operator types and a body some
# unknown address returns are both text nobody vouched for.


def _server_config(args: argparse.Namespace) -> ServerConfig:
    """The file half's `server` section, read the way every command
    reads it. No database is opened and no config file has to exist:
    without one the field defaults and the SAMTAL_ environment are the
    whole answer."""
    return load_file_config(args.config).server


def _onboarding_url(server: ServerConfig, fix: str) -> tuple[str, "Origin"]:
    """The short URL this configuration serves, and where its origin
    came from.

    Nothing here is a second implementation of anything: the key comes
    from `onboarding.onboarding_key` and the origin from
    `onboarding.public_origin`, which are what the server mounts the
    route with and what the startup banner prints. `fix` is what to do
    when onboarding is off, which differs by the command that asked.
    """
    # Imported in the body rather than at module scope: the onboarding
    # module serves the OTA handlers, so it imports `ota` and everything
    # a conversation needs, and `config reference` and `config openapi`
    # are rendered from the models and the routes with none of that
    # loaded.
    from samtal_server import onboarding

    origin = onboarding.public_origin(server)
    if not server.onboarding.enabled:
        raise ConfigError(
            ONBOARDING_OFF.format(origin=origin.url, provenance=origin.provenance, fix=fix)
        )
    key = onboarding.onboarding_key(server)
    # The one state the server itself cannot be in, since it refuses the
    # boot: auth is on, no key is pinned, and the variable the secret
    # would come from holds nothing. It is told apart from the keyless
    # case by the two fields that decide it, so no secret is read here.
    if key is None and server.auth.enabled and server.onboarding.key is None:
        raise ConfigError(
            f"{server.auth.secret_env} is not set, and the onboarding URL's key is "
            f"derived from it. It is the same variable the server is started with: exec "
            f"into the running container, where it is already in the environment, or "
            f"export it here. A deployment that pinned a key under "
            f"server.onboarding.key needs no secret to print its URL."
        )
    return f"{origin.url}{onboarding.onboarding_path(key)}", origin


def _device_url(url: str, source: str) -> str:
    """A URL this client may GET the way a device would.

    The API's transport policy deliberately does not apply. It exists
    because the bearer token rides on every request to the API, and this
    request carries no credential at all: the OTA endpoint is the token
    issuer, so it cannot require one. Refusing a plain http:// address
    here would refuse the ordinary LAN deployment, which is exactly what
    a device is pointed at.

    What does apply is the rest of the policy: a URL that cannot be read
    is refused, and userinfo is refused rather than carried, because
    anything in a URL ends up in shell history, in process lists and in
    access logs.

    No refusal repeats the address, not even with the userinfo taken
    off, which is where this is stricter than the API's policy: an OTA
    URL carries the path segment that stands in front of the token
    issuer, and on a deployment with onboarding turned off that segment
    is the whole protection the endpoint has.
    """
    # Before anything parses it: `urlsplit` deletes tabs, carriage
    # returns and newlines rather than refusing them (WHATWG's rule), so
    # a URL carrying one parses cleanly here and then reaches httpx,
    # which raises InvalidURL naming the character and its position. A
    # URL a person could have typed has no control characters and no
    # spaces in it, so this is where they stop.
    if any(character.isspace() or not character.isprintable() for character in url):
        raise ConfigError(
            f"{source} carries a space, a newline or another character a URL cannot "
            f"hold. It is not quoted back, both because an OTA URL can be the "
            f"deployment's own secret and because repeating a control character is how "
            f"one line of output becomes two."
        )
    parsed = _parsed(url, source)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConfigError(
            f"{source} is not an http:// or https:// URL with a host. It is not quoted "
            f"back, since an OTA URL can be the deployment's own secret."
        )
    if parsed.username or parsed.password:
        raise ConfigError(
            f"{source} carries a username or a password in the URL, which is refused: the "
            f"OTA endpoint takes no credential in its URL, and anything in one ends up in "
            f"shell history, process lists and access logs. The address is not repeated "
            f"here either."
        )
    # Returned exactly as it was given, trailing slash included: the
    # short path and the OTA path both end in one, and a device types
    # what it is given.
    return url


def _probed(url: str, shown: str) -> httpx.Response:
    """One GET of the OTA endpoint, and never anything else.

    A GET is the handler that describes the endpoint; the POST beside it
    is a device's check-in, which mints an activation code for an
    unbound MAC. A diagnosis that could put a number on a board's screen
    and spend the mint budget would be a diagnosis nobody could run
    twice, so this method is not a default but a rule.

    One redirect is followed, and only one shape of it: from a URL
    typed without its trailing slash to the canonical path on the same
    origin. A current server never sends it, since every device-facing
    route answers both spellings directly, but a server older than that
    change does, and reporting it as "not samtal-server" would be this
    command's worst answer. Following any other would let whatever
    answers at an address choose where this request goes next, which
    inside the network a deployment sits in is worth refusing rather
    than reasoning about.

    Building the client is inside the boundary with the request and the
    close. httpx validates a URL when it is given one, so construction
    is a place a URL refused by a library rather than by the check above
    would otherwise leave as a traceback with the address in it.
    """
    problem: str | None = None
    client: httpx.Client | None = None
    try:
        try:
            client = build_client(url)
            response = client.request("GET", url, follow_redirects=False)
            for _ in range(CANONICAL_REDIRECTS):
                if not response.is_redirect:
                    return response
                target = _canonical_slash(response)
                if target is None:
                    raise ConfigError(_redirect_refused(shown))
                response = client.request("GET", target, follow_redirects=False)
            if response.is_redirect:
                raise ConfigError(_redirect_refused(shown))
            return response
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            # The exception's class name and nothing else. httpx puts the
            # request into its exceptions, its InvalidURL quotes the
            # character it refused, and drivers put whatever they like
            # into their messages; the class is the part that says what
            # happened. Raised after the handler, so nothing walking a
            # chain finds the original behind it. ValueError covers the
            # UnicodeError an IDNA host raises on its way down.
            problem = (
                f"cannot reach {shown}: the request did not complete "
                f"({type(exc).__name__}). Check that the server is running, that this is "
                f"the address it serves, and that the network a device sits on can reach "
                f"it."
            )
    finally:
        if client is not None:
            client.close()
    raise ConfigError(problem)


def _canonical_slash(response: httpx.Response) -> str | None:
    """The target of the one redirect this command follows, or None for
    every other redirect there is.

    Same scheme, same host, same port, the same query, and a path that
    is the one just asked for with a slash after it. That is what
    Starlette answers a missing trailing slash with, which is the only
    redirect an operator can meet on the way to an OTA endpoint served
    by this project, and only from a deployment older than the change
    that made every spelling answer for itself. A `Location` this
    cannot read is refused the same way one pointing elsewhere is, and
    neither is repeated back.
    """
    current = response.request.url
    try:
        target = current.join(response.headers.get("location", ""))
    except (httpx.InvalidURL, ValueError):
        return None
    if (target.scheme, target.host, target.port) != (
        current.scheme,
        current.host,
        current.port,
    ):
        return None
    if target.path != f"{current.path}/" or target.query != current.query:
        return None
    return str(target)


def _redirect_refused(shown: str) -> str:
    return (
        f"{shown} answered with a redirect this command does not follow. The only one it "
        f"follows is the server's own, from a URL typed without its trailing slash to the "
        f"canonical path on the same address; following any other would let whatever "
        f"answers there choose which host this request reaches next, and this command "
        f"runs inside the network a deployment sits in. The target is not repeated here: "
        f"ask the address you meant directly."
    )


def _describe(body: str) -> Mapping[str, str] | None:
    """What the OTA endpoint said about itself, or None when this is not
    that endpoint's answer at all.

    Both lines have to be there. The first names the server and its
    version, the second what a device is handed, and an address that
    produces one without the other is not answering as this endpoint
    however it got there.
    """
    head = body[:PARSED_BODY_LENGTH]
    named = DESCRIBE_FIRST_LINE.match(head)
    sent = DESCRIBE_WEBSOCKET_LINE.search(head)
    if named is None or sent is None:
        return None
    return {**named.groupdict(), **sent.groupdict()}


def _not_samtal_server(shown: str, response: httpx.Response) -> str:
    """The status code and a fixed sentence, and nothing of the body.

    The same policy `_unreadable` states for the API, for the same
    reason and with more force: what answers at an address a device was
    pointed at may be a proxy, a captive portal or a cloud metadata
    endpoint, and relaying a bounded prefix of that onto a terminal
    still relays whatever the first line happens to hold.
    """
    return (
        f"{shown} answered {response.status_code}, but not as a samtal-server OTA "
        f"endpoint: a device pointed here would take its configuration from something "
        f"else, or from nothing. It answered with {UNRECOGNIZED_ANSWER}, which is not "
        f"quoted back: what a proxy or a gateway returns is not this server's own "
        f"output."
    )


def _plain_websocket(shown: str, websocket: str) -> str:
    return (
        f"{shown} answers over https, and it sends devices to {websocket}, which is a plain "
        f"ws:// URL. That is the TLS-proxy misconfiguration: the server behind the proxy "
        f"only ever sees plain HTTP, so a websocket URL derived from the request says "
        f"ws://, and a device told to connect that way fails with nothing else looking "
        f"wrong. Set server.websocket_url to the wss:// address the proxy serves and "
        f"restart the server."
    )


def _reported_websocket(url: str) -> tuple[str, str] | None:
    """The websocket URL a response named: its normalized scheme and the
    form that may be printed, or None when it is not a websocket URL
    this command can read.

    The scheme is returned rather than re-derived by the caller, and it
    is the parser's normalized one, because a comparison against the
    literal `ws://` is a comparison a `WS://` walks past.

    What arrived is whatever the far end sent, and identifying the far
    end is the whole point of the command, so it is parsed before it is
    shown: bounded, made printable, and stripped of any credential
    written into it. There is deliberately no fallback to the raw
    string. A URL that will not parse is exactly the one whose userinfo
    could not be taken off, so falling back would print the credential
    in precisely the case the stripping exists for.
    """
    try:
        parsed = urlsplit(url)
        # Read here, since it parses on access and raises for a port
        # that is not a number in range.
        _ = parsed.port
    except ValueError:
        return None
    # `urlsplit` lower-cases the scheme it parsed, which is what makes
    # this a normalization rather than a hope.
    if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
        return None
    return parsed.scheme, _printable(_without_userinfo(parsed))


def _unreadable_websocket(shown: str) -> str:
    return (
        f"{shown} answers as samtal-server, but the websocket URL it reports is not a "
        f"ws:// or wss:// URL this client can read, so a device pointed here would be "
        f"handed an address it cannot connect to. It is not quoted back, since it is "
        f"whatever that address returned. Check server.websocket_url on that deployment."
    )


def _printable(value: str, limit: int = GLIMPSE_LENGTH) -> str:
    """Text that arrived in a response, bounded before it is printed.

    Truncated first and then made printable, so no answer can choose how
    long this command's output is or put a newline, an escape sequence
    or a terminal control code into it. Unprintable characters become a
    question mark rather than disappearing, because something that
    arrived mangled should read as mangled.
    """
    return "".join(
        character if character.isprintable() else "?" for character in value.strip()[:limit]
    )


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
        ("provider", f"{stage}.{name}"): body
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
    entries = _pending_entries(answer)
    if not entries:
        return f"{NOTHING_PENDING}\n"
    rows = [PENDING_COLUMNS] + [
        (
            code,
            str(entry["mac"]),
            str(entry["board"]),
            str(entry["firmware"]),
            str(entry["expires_at"]),
        )
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


def _pending_entries(answer: object) -> Mapping[str, Mapping[str, object]]:
    """The listing, as the API returns it: code to device facts."""
    if isinstance(answer, Mapping) and all(
        isinstance(code, str) and isinstance(entry, Mapping) and PENDING_FIELDS <= set(entry)
        for code, entry in answer.items()
    ):
        return answer
    raise ConfigError(f"the configuration API answered a read with {UNRECOGNIZED_ANSWER}")


def _status_listing(answer: object) -> str:
    """What every configured MCP server is doing, one block each.

    A block rather than a row of columns, because two of the three
    things worth reading are lists: the tools the server published, and
    the agents that may reach it. A column holding a list is a column
    that wraps, and the pending listing's shape only works because every
    one of its fields is short.
    """
    entries = _status_entries(answer)
    if not entries:
        return f"{NOTHING_CONFIGURED}\n"
    lines: list[str] = []
    for name, entry in entries.items():
        reason = entry["reason"]
        lines.append(
            f"{_printable(name)}: {entry['state']} since {_printable(str(entry['since']))}"
            + (f" ({_printable(str(reason))})" if reason is not None else "")
        )
        lines.append("  tools: " + (_names(entry["tools"]) or "(none)"))
        lines.append("  agents: " + (_granted(entry["grants"]) or "(none)"))
    return "\n".join(lines) + "\n"


def _granted(grants: object) -> str:
    """Which agents may reach the server, and how much of it: a bare
    name is the whole server, and a name followed by tools in
    parentheses is the allow list that agent was given. Sorted by agent
    name, so two reads of an unchanged world print the same block."""
    return ", ".join(
        f"{_printable(agent)} ({allowed})" if (allowed := _names(tools)) else _printable(agent)
        for agent, tools in sorted(_mapping(grants).items())
    )


def _names(values: object) -> str:
    """A list from a response, printed. Bounded and made printable one
    by one even though the shape check below has established they are
    strings: what that check knows about them is their type, not their
    length and not whether every character in them can be written to a
    terminal."""
    return ", ".join(_printable(str(value)) for value in _sequence(values))


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, str) else ()


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _prompt_listing(answer: object) -> str:
    """The assembled prompt, block by block, and its total size.

    Every block is printed whole. This command exists to show what the
    model is given, so a concealed tail is exactly what the operator
    came to see, which is why nothing here goes through `_printable`:
    that renderer strips a value and cuts it at `GLIMPSE_LENGTH`, which
    is right for an acknowledgement and fatally wrong here.

    The counts printed are the ones the server reported, which count
    what is stored and sent, so a replaced character below never
    falsifies the accounting.
    """
    body = _assembled_prompt(answer)
    lines: list[str] = []
    for block in body["blocks"]:
        lines.append(f"{_block(str(block['provenance']))} ({block['characters']} characters)")
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

    Applied to the provenance as well as to the text, since a
    provenance names an entry an operator wrote and later milestones
    put more of the world in it.
    """
    return "".join(
        character if character.isprintable() or character in "\n\t" else "?"
        for character in value
    )


def _assembled_prompt(answer: object) -> Mapping[str, object]:
    """The assembled prompt, as the API returns it, checked all the way
    down for the reason the status document is: the renderer prints what
    it is given, and a body this client does not recognize did not come
    from this API."""
    if (
        isinstance(answer, Mapping)
        and _is_count(answer.get("characters"))
        and isinstance(answer.get("blocks"), list)
        and all(_is_prompt_block(block) for block in answer["blocks"])
    ):
        return answer
    raise ConfigError(f"the configuration API answered a read with {UNRECOGNIZED_ANSWER}")


def _is_prompt_block(block: object) -> bool:
    return (
        isinstance(block, Mapping)
        and PROMPT_BLOCK_FIELDS <= set(block)
        and isinstance(block["provenance"], str)
        and isinstance(block["text"], str)
        and _is_count(block["characters"])
    )


def _is_count(value: object) -> bool:
    # bool before int, since a body is free to put one where a number
    # belongs and `True` would otherwise print as a size.
    return isinstance(value, int) and not isinstance(value, bool)


def _reload_listing(answer: object) -> str:
    """What the reload did, and then what is running.

    The outcomes first, because they are the answer to the question that
    was asked, and the status underneath because it is the answer to the
    one that follows: an entry that started is not thereby connected,
    and the block below it says which.
    """
    applied = _reload_outcome(answer)
    lines = [
        f"{outcome}: " + (_names(applied[outcome]) or "(none)") for outcome in RELOAD_OUTCOMES
    ]
    return "\n".join(lines) + "\n\n" + _status_listing(applied["servers"])


def _reload_outcome(answer: object) -> Mapping[str, object]:
    """The reload's answer, as the API returns it: the four outcomes and
    the status document.

    Checked to the same depth as the status document underneath it, and
    for the same reason: `_names` prints every element of the outcome
    lists, and the status half is rendered by the listing that expects
    to have been handed a document, so a stray shape anywhere in here
    would otherwise become output or a traceback."""
    if (
        isinstance(answer, Mapping)
        and "servers" in answer
        and all(
            isinstance(answer.get(outcome), list)
            and all(isinstance(name, str) for name in answer[outcome])
            for outcome in RELOAD_OUTCOMES
        )
    ):
        return answer
    raise ConfigError(f"the configuration API answered the reload with {UNRECOGNIZED_ANSWER}")


def _status_entries(answer: object) -> Mapping[str, Mapping[str, object]]:
    """The status document, as the API returns it: entry name to what
    that entry is doing, checked all the way down.

    Every field, its type and, for `state`, its vocabulary, because the
    renderer prints what it is given and a body this client cannot
    recognize is a body it did not write: what a proxy, a gateway or a
    captive portal returns is text nobody vouched for, and a check that
    only counted keys would have printed whatever was under them.

    Written as plain predicates with no try/except, the rule this module
    keeps for refusals: an exception raised while another is being
    handled carries that one as its context, and the one being handled
    here would hold the body.
    """
    if isinstance(answer, Mapping) and all(
        isinstance(name, str) and _is_status_entry(entry) for name, entry in answer.items()
    ):
        return answer
    raise ConfigError(f"the configuration API answered a read with {UNRECOGNIZED_ANSWER}")


def _is_status_entry(entry: object) -> bool:
    """One entry of the status document, in the shape the committed
    OpenAPI document declares. Extra keys are tolerated and never
    printed, so a newer server saying more than this client knows about
    is readable rather than refused."""
    if not isinstance(entry, Mapping) or not STATUS_FIELDS <= set(entry):
        return False
    reason = entry["reason"]
    state = entry["state"]
    return (
        # The type before the vocabulary: a membership test on an
        # unhashable value raises rather than answering False, and a
        # body is free to put an object where a word belongs.
        isinstance(state, str)
        and state in STATUS_STATES
        and isinstance(entry["since"], str)
        and (reason is None or isinstance(reason, str))
        and _is_name_list(entry["tools"])
        and isinstance(entry["grants"], Mapping)
        and all(
            isinstance(agent, str) and (allowed is None or _is_name_list(allowed))
            for agent, allowed in entry["grants"].items()
        )
    )


def _is_name_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(name, str) for name in value)


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
            f"    {name} ({body.get('type')})"
            + _slots(stored, "provider", f"{stage}.{name}")
            for name, body in config["providers"].get(stage, {}).items()
        ] or ["    (none)"]

    lines.append("mcp_servers:")
    lines += [
        f"  {name} ({body.get('transport')})" + _slots(stored, "mcp_server", name)
        for name, body in config["mcp_servers"].items()
    ] or ["  (none)"]

    lines.append("prompt_fragments:")
    # The size rather than the text: this is the tree, and what an
    # operator reads it for is which fragments exist and what each of
    # them costs the prompt budget. `show prompt-fragment` prints one
    # whole, and `prompt <agent>` prints what an agent adds up to.
    lines += [
        f"  {name} ({len(str(body.get('text', '')))} characters)"
        for name, body in config["prompt_fragments"].items()
    ] or ["  (none)"]

    defaults = _inline(config["agent_defaults"])
    lines.append("agent_defaults: " + (defaults or "(none)"))

    lines.append("agents:")
    lines += [
        f"  {name}" + (f": {_inline(_layer(body))}" if _layer(body) else "")
        for name, body in config["agents"].items()
    ] or ["  (none)"]

    lines.append("devices:")
    lines += [
        f"  {mac} -> {', '.join(bound)}" for mac, bound in config["devices"].items()
    ] or ["  (none)"]

    lines.append(f"default_agent: {config['default_agent'] or '(none)'}")
    return "\n".join(lines) + "\n"


def _stored_slots(secrets: Sequence[Mapping[str, object]]) -> dict[tuple[str, str], list[str]]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for stored in secrets:
        grouped.setdefault((stored["kind"], stored["identity"]), []).append(stored["slot"])
    return grouped


def _slots(stored: Mapping[tuple[str, str], list[str]], kind: str, identity: str) -> str:
    slots = stored.get((kind, identity), [])
    return f"  [secrets: {', '.join(slots)}]" if slots else ""


def _layer(body: Mapping[str, object]) -> dict[str, object]:
    """An agent's overrides: its body without the prompt, which is what
    the summary line has room for."""
    return {key: value for key, value in body.items() if key != "prompt"}


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


def _read_secret(args: argparse.Namespace) -> str:
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


def _secret_location(args: argparse.Namespace) -> SecretLocation:
    if args.kind == "provider":
        return SecretLocation.provider(args.stage, args.name, args.slot)
    return SecretLocation.mcp_server(args.name, args.slot)


# The database, for the recovery subset only


@contextmanager
def _store(args: argparse.Namespace, keyed: bool = False) -> Iterator[ConfigStore]:
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


def _database_dir(args: argparse.Namespace) -> Path:
    """Where the server keeps its domain configuration, read through the
    settings machinery the server reads it with, so the two cannot
    disagree. No configuration file has to exist: without one the field
    default and the SAMTAL_ environment are the whole answer."""
    return load_file_config(args.config).server.database.dir


# Output


def _wrote(answer: object) -> None:
    """What the API said the write did, and when it applies."""
    what = answer.get("wrote") if isinstance(answer, Mapping) else None
    notice = answer.get("notice") if isinstance(answer, Mapping) else None
    if not isinstance(what, str):
        raise ConfigError(
            f"the configuration API acknowledged the write with {UNRECOGNIZED_ANSWER}; "
            f"read the configuration back to see whether it was applied."
        )
    _report(what, notice if isinstance(notice, str) else RESTART_NOTICE)


def _report(what: str, notice: str = RESTART_NOTICE) -> None:
    print(f"wrote {what}")
    # Flushed first, so the notice lands after the line it is about
    # rather than ahead of it: stderr is unbuffered and stdout is not.
    sys.stdout.flush()
    print(notice, file=sys.stderr)


# The grammar


def _fragment_parser(
    kinds: argparse._SubParsersAction, name: str, parents: list[argparse.ArgumentParser]
) -> argparse.ArgumentParser:
    """One `set <kind>` command, whose help lists the fields its
    fragment may carry. The list is generated from the same
    Field(description=...) values the reference and the JSON Schema are
    rendered from, so the three cannot disagree and nobody has to
    remember to update a help string when a field changes."""
    return kinds.add_parser(
        name,
        parents=parents,
        epilog=docgen.fragment_help(name),
        # The epilog is laid out already; the default formatter would
        # reflow it into one paragraph.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def _parser() -> argparse.ArgumentParser:
    config_help = (
        f"path to the YAML config file naming server.port and server.api.secret_env "
        f"(default: ${CONFIG_ENV_VAR})"
    )
    api_url_help = (
        f"base URL of the configuration API (default: ${API_URL_ENV}, then "
        f"http://127.0.0.1:<server.port>{API_MOUNT_PATH})"
    )
    local_help = (
        "read and write the database directly instead of the API: the recovery subset "
        "(show, delete, clear-secret, set-secret), for when the server will not start"
    )
    # Accepted before the command and after it, because both readings are
    # natural: `samtal-server --config path` is how the server takes it,
    # and options after their subcommand is how everything else does. The
    # per-command copy suppresses its default rather than defaulting to
    # None, or an option given before the command would be overwritten by
    # the command's own empty default.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config", metavar="PATH", default=argparse.SUPPRESS, help=config_help
    )
    common.add_argument(
        "--api-url", metavar="URL", default=argparse.SUPPRESS, help=api_url_help
    )
    common.add_argument(
        "--local", action="store_true", default=argparse.SUPPRESS, help=local_help
    )
    # For the commands that read the file half and reach no API: the
    # same --config, and none of the flags that address one.
    file_only = argparse.ArgumentParser(add_help=False)
    file_only.add_argument(
        "--config", metavar="PATH", default=argparse.SUPPRESS, help=config_help
    )
    fragment = argparse.ArgumentParser(add_help=False)
    fragment.add_argument(
        "-f",
        "--file",
        metavar="PATH",
        required=True,
        help="YAML fragment for this entity, or - to read it from stdin",
    )

    parser = _Parser(
        prog="samtal-server config",
        description=(
            "Read and write the domain half of the configuration: providers, "
            "MCP servers, agents, devices and their secrets. Commands go through the "
            "configuration API on the running server; --local is the recovery path."
        ),
    )
    parser.add_argument("--config", metavar="PATH", default=None, help=config_help)
    parser.add_argument("--api-url", metavar="URL", default=None, help=api_url_help)
    parser.add_argument("--local", action="store_true", help=local_help)
    # Which commands --local covers, carried on the command itself so
    # that adding one to the subset is a line beside the command rather
    # than a list somewhere else to keep in step.
    parser.set_defaults(local_ok=False)
    commands = parser.add_subparsers(dest="command", required=True)

    setter = commands.add_parser(
        "set", help="create or replace one entity from a YAML fragment"
    )
    kinds = setter.add_subparsers(dest="kind", required=True)
    entity = _fragment_parser(kinds, "provider", [common, fragment])
    entity.add_argument("stage", metavar="STAGE", help=", ".join(PROVIDER_STAGES))
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_set_provider)
    entity = _fragment_parser(kinds, "mcp-server", [common, fragment])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_set_mcp_server)
    entity = _fragment_parser(kinds, "prompt-fragment", [common, fragment])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_set_prompt_fragment)
    entity = _fragment_parser(kinds, "agent", [common, fragment])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_set_agent)
    entity = _fragment_parser(kinds, "agent-defaults", [common, fragment])
    entity.set_defaults(run=_set_agent_defaults)

    deleter = commands.add_parser("delete", help="delete one entity")
    deleter.set_defaults(local_ok=True)
    kinds = deleter.add_subparsers(dest="kind", required=True)
    entity = kinds.add_parser("provider", parents=[common])
    entity.add_argument("stage", metavar="STAGE", help=", ".join(PROVIDER_STAGES))
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_delete_provider)
    entity = kinds.add_parser("mcp-server", parents=[common])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_delete_mcp_server)
    entity = kinds.add_parser("prompt-fragment", parents=[common])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_delete_prompt_fragment)
    entity = kinds.add_parser("agent", parents=[common])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_delete_agent)
    entity = kinds.add_parser("device", parents=[common])
    entity.add_argument("mac", metavar="MAC")
    entity.set_defaults(run=_delete_device)

    # Two ways to bind a board, and which one an operator wants depends
    # on what they are holding: a MAC they already know, or a device in
    # front of them showing six digits. The help text says exactly that,
    # because the pair is otherwise the kind of thing a person picks
    # wrongly once and then remembers wrongly.
    bind = commands.add_parser(
        "bind-device",
        parents=[common],
        help="bind a device by the MAC you already know, to one or more agents",
    )
    bind.add_argument("mac", metavar="MAC")
    bind.add_argument("agents", metavar="AGENT", nargs="+")
    bind.set_defaults(run=_bind_device)

    add = commands.add_parser(
        "add-device",
        parents=[common],
        help=(
            "bind the device showing this activation code, which is the six digits on "
            "its screen; use bind-device when you know the MAC instead"
        ),
    )
    add.add_argument(
        "code",
        metavar="CODE",
        help="the six digits the device is showing and speaking",
    )
    add.add_argument("agents", metavar="AGENT", nargs="+")
    add.set_defaults(run=_add_device)

    waiting = commands.add_parser(
        "pending",
        parents=[common],
        help="the devices showing an activation code, and the code each is showing",
    )
    waiting.set_defaults(run=_pending)

    # The other read of the running server rather than of the database,
    # and the reason neither takes --local: there is no state to report
    # when there is no server to ask.
    running = commands.add_parser(
        "status",
        parents=[common],
        help=(
            "what each configured MCP server is doing on the running server: connected, "
            "down, or unused because no agent references it, since when, and which "
            "tools it published"
        ),
    )
    running.set_defaults(run=_status)

    # The other read of the running server, and the one that answers
    # what the model is actually given: the configuration says what an
    # agent is made of, and this says what that adds up to.
    assembled = commands.add_parser(
        "prompt",
        parents=[common],
        help=(
            "the system prompt a new session as this agent would be sent, block by "
            "block with the size of each and the total; a conversation already running "
            "holds what it assembled when it started"
        ),
    )
    assembled.add_argument("name", metavar="AGENT")
    assembled.set_defaults(run=_prompt)

    # The one command that changes what the server is doing rather than
    # what is stored, which is why it is a verb of its own rather than a
    # flag on a write: an operator writes several entries and grant
    # lists and applies them once.
    applying = commands.add_parser(
        "reload",
        parents=[common],
        help=(
            "apply the stored MCP servers and the agents' grant lists to the running "
            "server, without a restart and without dropping a conversation"
        ),
    )
    applying.set_defaults(run=_reload)

    # The two onboarding commands take --config and nothing else. One
    # contacts nothing at all and the other reaches a device-facing
    # endpoint, so neither has anything to do with --api-url or the
    # bearer token, and offering the flags would say they had.
    portal = commands.add_parser(
        "ota-url",
        parents=[file_only],
        help=(
            "the URL to type into a device's captive portal; derived from this "
            "configuration and the device-auth secret, and it contacts nothing"
        ),
    )
    portal.set_defaults(run=_ota_url)

    check = commands.add_parser(
        "doctor",
        parents=[file_only],
        help="ask an OTA URL what it would tell a device, and say what is wrong",
    )
    check.add_argument(
        "url",
        metavar="URL",
        nargs="?",
        help="the OTA URL to check (default: the one ota-url prints)",
    )
    check.set_defaults(run=_doctor)

    default = commands.add_parser(
        "set-default-agent", parents=[common], help="the agent an unbound device reaches"
    )
    default.add_argument("name", metavar="NAME")
    default.set_defaults(run=_set_default_agent)

    cleared = commands.add_parser(
        "clear-default-agent",
        parents=[common],
        help="unset it, leaving the devices map as the allowlist",
    )
    cleared.set_defaults(run=_clear_default_agent)

    secret = commands.add_parser(
        "set-secret", help="store one credential, encrypted, read from stdin or a variable"
    )
    secret.set_defaults(local_ok=True)
    kinds = secret.add_subparsers(dest="kind", required=True)
    entity = kinds.add_parser("provider", parents=[common])
    entity.add_argument("stage", metavar="STAGE", help=", ".join(PROVIDER_STAGES))
    entity.add_argument("name", metavar="NAME")
    entity.add_argument("slot", metavar="SLOT", help="the option it fills, such as api_key")
    entity.add_argument("--from-env", metavar="VAR", help="read the value from this variable")
    entity.set_defaults(run=_set_secret)
    entity = kinds.add_parser("mcp-server", parents=[common])
    entity.add_argument("name", metavar="NAME")
    entity.add_argument("slot", metavar="SLOT", help="env.<KEY> or headers.<KEY>")
    entity.add_argument("--from-env", metavar="VAR", help="read the value from this variable")
    entity.set_defaults(run=_set_secret)

    clear = commands.add_parser("clear-secret", help="remove one stored credential")
    clear.set_defaults(local_ok=True)
    kinds = clear.add_subparsers(dest="kind", required=True)
    entity = kinds.add_parser("provider", parents=[common])
    entity.add_argument("stage", metavar="STAGE", help=", ".join(PROVIDER_STAGES))
    entity.add_argument("name", metavar="NAME")
    entity.add_argument("slot", metavar="SLOT")
    entity.set_defaults(run=_clear_secret)
    entity = kinds.add_parser("mcp-server", parents=[common])
    entity.add_argument("name", metavar="NAME")
    entity.add_argument("slot", metavar="SLOT")
    entity.set_defaults(run=_clear_secret)

    listing = commands.add_parser("list", parents=[common], help="a summary tree")
    listing.set_defaults(run=_list)

    # Read-only and local: these three render the models and the API's
    # own routes, so they take no --config, open no database, reach no
    # server and need no encryption key. Keep it that way: the
    # documentation lane runs `config reference` and `config openapi`
    # from a plain sync, with no database, no key and no token anywhere.
    schema = commands.add_parser(
        "schema", help="the JSON Schema of one entity, or of the whole domain half"
    )
    schema.add_argument(
        "entity",
        metavar="ENTITY",
        nargs="?",
        help=", ".join(docgen.entity_names()) + " (default: domain)",
    )
    schema.set_defaults(run=_schema)

    reference = commands.add_parser(
        "reference", help="the markdown reference, generated from the models"
    )
    reference.set_defaults(run=_reference)

    openapi = commands.add_parser(
        "openapi", help="the configuration API's OpenAPI document, generated from its routes"
    )
    openapi.set_defaults(run=_openapi)

    show = commands.add_parser("show", parents=[common], help="everything, or one entity")
    show.set_defaults(run=_show_all, local_ok=True)
    kinds = show.add_subparsers(dest="kind")
    entity = kinds.add_parser("provider", parents=[common])
    entity.add_argument("stage", metavar="STAGE", help=", ".join(PROVIDER_STAGES))
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_show_provider)
    entity = kinds.add_parser("mcp-server", parents=[common])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_show_mcp_server)
    entity = kinds.add_parser("prompt-fragment", parents=[common])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_show_prompt_fragment)
    entity = kinds.add_parser("agent", parents=[common])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_show_agent)
    entity = kinds.add_parser("agent-defaults", parents=[common])
    entity.set_defaults(run=_show_agent_defaults)
    entity = kinds.add_parser("device", parents=[common])
    entity.add_argument("mac", metavar="MAC")
    entity.set_defaults(run=_show_device)

    return parser


__all__ = ["RESTART_NOTICE", "build_client", "main"]
