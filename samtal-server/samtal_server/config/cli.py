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

Every failure leaves as a ConfigError printed to stderr with exit code
1, naming the location and the kind of failure without quoting the value
that caused it, and no traceback from pydantic, PyYAML, SQLAlchemy,
cryptography or httpx reaches the user.
"""

import argparse
import getpass
import ipaddress
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn
from urllib.parse import SplitResult, quote, urlsplit, urlunsplit

import httpx
import yaml

from samtal_server.config import docgen, views
from samtal_server.config.loader import CONFIG_ENV_VAR, ConfigError, load_file_config
from samtal_server.config.models import (
    API_MOUNT_PATH,
    PROVIDER_STAGES,
    FileConfig,
    normalize_mac,
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
    deleted_provider,
    wrote_secret,
)
from samtal_server.db import open_database

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
            store.delete_device(args.mac)
        # The same sentence the API answers this delete with. A running
        # server reads the devices table, so the removal reaches the
        # device at its next check-in whether the row was deleted
        # through the API or, as here, underneath it.
        _report(deleted_device(_mac(args.mac)), BINDING_NOTICE)
        return
    _wrote(_call(args, "DELETE", _path("devices", args.mac)))


def _bind_device(args: argparse.Namespace) -> None:
    _wrote(_call(args, "PUT", _path("devices", args.mac), {"agents": list(args.agents)}))


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


def build_client(base_url: str, token: str) -> httpx.Client:
    """The connection to the configuration API.

    The one seam in this module. `cli.main()` is and stays synchronous,
    and httpx's ASGI transport is async-only, so the tests replace this
    with Starlette's TestClient: itself a synchronous `httpx.Client`
    subclass that drives an ASGI application through its own portal.
    """
    return httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
    )


_NOTHING = object()


def _call(
    args: argparse.Namespace, method: str, path: str, body: object = _NOTHING
) -> object:
    """One request, and its answer as this client understands it."""
    file_config = load_file_config(args.config)
    base_url = _base_url(args, file_config)
    client = build_client(base_url, _token(file_config))
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


def _mac(mac: str) -> str:
    problem: str | None = None
    try:
        return normalize_mac(mac)
    except ValueError as exc:
        problem = str(exc)
    raise ConfigError(problem)


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
    entity = kinds.add_parser("agent", parents=[common])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_delete_agent)
    entity = kinds.add_parser("device", parents=[common])
    entity.add_argument("mac", metavar="MAC")
    entity.set_defaults(run=_delete_device)

    bind = commands.add_parser(
        "bind-device", parents=[common], help="bind a device to one or more agents"
    )
    bind.add_argument("mac", metavar="MAC")
    bind.add_argument("agents", metavar="AGENT", nargs="+")
    bind.set_defaults(run=_bind_device)

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
