"""The `vinga-server doctor` command: what a device pointed at a URL
would be told.

One question, asked from where a device stands. It is a GET of an OTA
endpoint, and it has four answers, which are the four states worth
telling apart: the address cannot be reached, something other than
vinga-server answers there, vinga-server answers but sends devices to a
plain `ws://` URL from behind TLS, or it is healthy and this says what a
device is handed. Only the first three are failures, and each leaves as
a `ConfigError` printed to stderr with exit code 1.

It reaches no configuration API, no database and no encryption key, and
it sends no credential of any kind: the OTA endpoint is the token
issuer, so it cannot require one, and a client that cannot carry an
Authorization header cannot hand one to whatever answers at a
device-facing address. A plain http:// address is ordinary here rather
than refused, since that is exactly what a device on a LAN is pointed
at.

A module of its own since #244, rather than a command of the
configuration group it grew up in. Diagnosing an endpoint is not a
configuration concern, and the one thing it borrows from that half is
where a deployment's own short URL comes from, which is
`onboarding.origin` and is imported inside the branch that needs it,
for the reason written beside that import.

What it no longer owns is how to talk to such an address at all. The
policy, the stand-in name, the timeouts, the request lifecycle and the
websocket URL rule are `device_endpoint.py`, because `vinga simulator`
stands where this command stands and asks a different question of the
same kind of address. Two implementations of that would be two chances
to leak on the one surface where a mistake is a leak. What stays here is
what this command is: the four verdicts, and the GET that produces them.

Everything printed here treats what arrived as text nobody vouched for.
A URL an operator passes may be the deployment's secret `ota_path`, so
no verdict repeats it. A body that came back may be a proxy's, a
captive portal's or a cloud metadata endpoint's, so it is never quoted,
and the two strings taken out of it are bounded and made printable
first. The usage errors are this module's own sentences for the same
reason: what an operator mistypes at this command is a URL.
"""

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from typing import NoReturn

import httpx

from vinga_server.config.loader import (
    CONFIG_ENV_VAR,
    NEEDS_THE_SERVER_HALF,
    ConfigError,
    load_file_config,
)
from vinga_server.config.models import ServerConfig
from vinga_server.config.printing import printable
from vinga_server.device_endpoint import (
    # Imported rather than restated, which is what makes them one fact
    # each: the stand-in every verdict names, the two loggers a request
    # is held quiet around, the two bounds it waits, the seam this
    # command's suite replaces, and the address type and the boundary
    # itself. `doctor.SUPPLIED_ENDPOINT` and the rest stay the names
    # every test and every sentence here reach for.
    CONNECT_TIMEOUT_S,
    READ_TIMEOUT_S,
    REQUEST_LOGGERS,
    SUPPLIED_ENDPOINT,
    Endpoint,
    build_client,
    downgraded,
    reported_websocket,
    requested,
)

# What to do when there is no URL to derive and none was given.
ONBOARDING_OFF_FOR_DOCTOR = "Give the URL to check as an argument: vinga-server doctor URL."

# Said when the address answered something this command cannot read as
# this endpoint's description. The body is deliberately not quoted:
# what a proxy, a gateway or a captive portal returns is not a
# vinga-server's own output, and relaying it as though it were is how a
# middlebox's page ends up reading like a diagnosis.
UNRECOGNIZED_ANSWER = "a body this client does not recognize"

# How much of a body is looked at at all. The description this reads is
# three short lines, so a few kilobytes is generous; what the bound is
# for is a megabyte of anything, which nothing should walk a pattern
# over.
PARSED_BODY_LENGTH = 4096

# The endpoint's own description of itself, which is what tells a
# vinga-server from anything else answering at that address. Parsed
# rather than shared as a format string: this is a client of an HTTP
# endpoint, the way the configuration API's answers are parsed, and a
# unit test runs the real handler's body through these patterns so that
# a change to what it prints cannot pass unnoticed.
DESCRIBE_FIRST_LINE = re.compile(
    r"^vinga-server (?P<version>\S{1,64}) \(revision [^)\n]{0,64}\) OTA endpoint\."
)

DESCRIBE_WEBSOCKET_LINE = re.compile(
    r"^Devices are sent to (?P<websocket>\S{1,256}) "
    r"\(protocol version (?P<protocol>[^)\n]{0,32})\)\.",
    re.MULTILINE,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the doctor. Returns the process exit code.

    Parsing is inside the boundary, so a mistake in the grammar answers
    the way an address nothing answers on does: a sentence on stderr and
    exit 1. --help still leaves through argparse's own exit 0, because
    asking for help is not a failure."""
    try:
        _doctor(_parser().parse_args(argv))
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


class _Parser(argparse.ArgumentParser):
    """A parser whose usage errors leave through the same door as every
    other failure, and whose sentences are this module's rather than
    argparse's.

    The first half is the reason the other command groups give: argparse
    writes to stderr and exits 2 from inside parse_args, which would
    make a mistake in the grammar the one failure that bypasses the
    ConfigError boundary and the documented exit codes.

    The second half is this command's own, and it is why the config
    group's shared boundary was not worth keeping. That one passes
    argparse's words through for all but one shape. Here the thing an
    operator mistypes is a URL, and an OTA URL can be the deployment's
    own secret, so a sentence that quotes the argument
    (`unrecognized arguments: <url>`) is a leak. Each shape this grammar
    can produce gets a fixed sentence, and a shape that is not
    recognized gets the vague one, because a message this code has not
    seen is a message that may carry a value."""

    def error(self, message: str) -> NoReturn:
        raise ConfigError(_usage_problem(message))


# This grammar's own words for what argparse says, matched on a marker
# that carries no value. Ordered, and the first match wins.
_USAGE_PROBLEMS: tuple[tuple[str, str], ...] = (
    (
        "unrecognized arguments",
        "unrecognized extra arguments: this command takes one URL and nothing else",
    ),
    ("expected one argument", "an option was given without its value"),
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
    """The whole grammar: one optional URL and --config.

    No subcommands and no --api-url, because this command reaches no
    API and no database and offering the flag would say it had.
    """
    parser = _Parser(
        prog="vinga-server doctor",
        description=(
            "Ask an OTA URL what it would tell a device, and say what is wrong. With no "
            "URL it checks the one this configuration derives, which is the URL "
            "vinga-server config ota-url prints. It is a GET and never a POST, so it "
            "mints nothing, and it sends no credential."
        ),
    )
    parser.add_argument(
        "url",
        metavar="URL",
        nargs="?",
        help="the OTA URL to check (default: the one config ota-url prints)",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help=(
            f"path to the YAML config file the derived URL comes from "
            f"(default: ${CONFIG_ENV_VAR})"
        ),
    )
    return parser


# The diagnosis


def _doctor(args: argparse.Namespace) -> None:
    """What a device pointed at a URL would be told, asked from where a
    device stands.

    Four answers, which are the four states worth telling apart: the
    address cannot be reached, something other than vinga-server
    answers there, vinga-server answers but sends devices to a plain
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
        url = _device_url(_derived_url(args), "the onboarding URL this configuration derives")
        shown = url
    response = _probed(url, shown)
    reported = _describe(response.text)
    if reported is None:
        raise ConfigError(_not_vinga_server(shown, response))
    answered = reported_websocket(reported["websocket"])
    if answered is None:
        raise ConfigError(_unreadable_websocket(shown))
    scheme, websocket = answered
    if downgraded(response.url.scheme, scheme):
        raise ConfigError(_plain_websocket(shown, websocket))
    print(
        f"{shown} is vinga-server {printable(reported['version'])}, and sends devices "
        f"to {websocket} (protocol version {printable(reported['protocol'])})."
    )


def _derived_url(args: argparse.Namespace) -> str:
    """The onboarding URL this configuration derives, or the fixed
    sentence when the server half is not installed.

    Imported here rather than at the top of this module, and for two
    reasons that stack. The first is weight: `onboarding/__init__`
    eagerly imports `unbound`, which imports `device.bindings`, which
    imports `config.store` and `vinga_server.db`, so a module-level
    import would pull the whole database machinery into every
    invocation of this command, URL argument or not. The second is that
    the same chain reaches FastAPI, which the default install of this
    package does not carry, so on a client-only install this import
    does not merely cost something, it fails.

    Which is why it is gated, and why the gate is HERE rather than
    around the command. A laptop diagnosing a remote deployment passes
    the URL, and that half must keep working with the client half
    alone: it opens a socket and reads an answer and wants nothing of
    this package's server. Only the derivation needs the other half,
    because only the derivation reads the onboarding key.

    Recorded inside the handler and raised outside it, the way every
    sanitized boundary here raises: an ImportError's text is the module
    path it could not find, and an exception raised while one is being
    handled carries it as `__context__` for anything walking the chain.
    A `ConfigError` out of the derivation itself is this command's own
    refusal and travels as one, since only ImportError is caught.
    """
    config = _server_config(args)
    derived: list[str] = []
    try:
        from vinga_server.onboarding.origin import onboarding_url

        derived.append(onboarding_url(config, ONBOARDING_OFF_FOR_DOCTOR)[0])
    except ImportError:
        pass
    if not derived:
        raise ConfigError(NEEDS_THE_SERVER_HALF)
    return derived[0]


def _server_config(args: argparse.Namespace) -> ServerConfig:
    """The file half's `server` section, read the way every command
    reads it. No database is opened and no config file has to exist:
    without one the field defaults and the VINGA_ environment are the
    whole answer."""
    return load_file_config(args.config).server


# Reaching the endpoint
#
# One request, and everything about making one is `device_endpoint`: the
# policy in front of it, the client seam, the logging boundary, the
# refused redirect and the close. What is this command's own is which
# METHOD it makes, and that is a rule rather than a default.

# What a URL handed to the probe is called if the policy has anything to
# say about it, which by then it does not: `_doctor` has already run it.
PROBED_URL = "the URL this probe was given"


def _device_url(url: str, source: str) -> str:
    """A URL this client may GET the way a device would.

    The policy is `device_endpoint.Endpoint.parsed`, which is where it
    lives now that `vinga simulator` stands where this command stands and
    asks a different question of the same kind of address. What comes
    back is the address exactly as it was given, trailing slash included:
    the short path and the OTA path both end in one, and a device sends
    what it was handed.
    """
    return Endpoint.parsed(url, source, SUPPLIED_ENDPOINT).given


def _probed(url: str, shown: str) -> httpx.Response:
    """One GET of the OTA endpoint, and never anything else.

    A GET is the handler that describes the endpoint; the POST beside it
    is a device's check-in, which mints an activation code for an unbound
    MAC. A diagnosis that could put a number on a board's screen and
    spend the mint budget would be a diagnosis nobody could run twice, so
    this method is not a default but a rule.

    That rule is the whole of what this decides, and it is why the line
    is here rather than folded into its caller: it binds the method and
    this command's own client seam, which its suite replaces, to a
    boundary two commands share.

    The address is split again here rather than carried down from
    `_doctor`, because the boundary composes from parsed parts and the
    seam is handed a string. The policy that split applies has already
    passed, so none of its refusals is reachable from this call; what is
    wanted is the split.
    """
    return requested("GET", Endpoint.parsed(url, PROBED_URL, shown), build=build_client)


# Reading what answered


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


def _not_vinga_server(shown: str, response: httpx.Response) -> str:
    """The status code and a fixed sentence, and nothing of the body.

    The same policy `_unreadable` states for the API, for the same
    reason and with more force: what answers at an address a device was
    pointed at may be a proxy, a captive portal or a cloud metadata
    endpoint, and relaying a bounded prefix of that onto a terminal
    still relays whatever the first line happens to hold.
    """
    return (
        f"{shown} answered {response.status_code}, but not as a vinga-server OTA "
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


def _unreadable_websocket(shown: str) -> str:
    return (
        f"{shown} answers as vinga-server, but the websocket URL it reports is not a "
        f"ws:// or wss:// URL this client can read, so a device pointed here would be "
        f"handed an address it cannot connect to. It is not quoted back, since it is "
        f"whatever that address returned. Check server.websocket_url on that deployment."
    )


# The names this module answers to. The three bounds and the two loggers
# are `device_endpoint`'s facts, imported rather than restated, and they
# are listed here because this is where a caller and this command's own
# suite read them: what the probe waits and which loggers it holds quiet
# are questions about `vinga-server doctor`, whatever module owns the
# answer.
__all__ = [
    "CONNECT_TIMEOUT_S",
    "READ_TIMEOUT_S",
    "REQUEST_LOGGERS",
    "SUPPLIED_ENDPOINT",
    "build_client",
    "main",
]
