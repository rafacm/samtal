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
from urllib.parse import urlsplit

import httpx

from vinga_server.config.loader import CONFIG_ENV_VAR, ConfigError, load_file_config
from vinga_server.config.models import ServerConfig
from vinga_server.config.printing import parsed_url, printable, shown_url

# What a URL given to this command is called in every line it prints.
#
# The one URL it may show is the derived short one, whose key is the
# recorded exception to "a path segment in front of the token issuer is
# a credential". A URL an operator passes is not that: the documented
# way to check a deployment with onboarding turned off is to pass the
# legacy `ota_path` URL, which is exactly the segment nothing may print.
# So a supplied URL is never displayed, in any verdict, and this stands
# in for it.
SUPPLIED_ENDPOINT = "the supplied OTA endpoint"

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

# How long this command waits, and the reason is its own. One GET of a
# static description: a bounded connect, so an address nothing listens
# on is reported in seconds rather than at the read bound, and a
# generous read, because the network a device sits on is the thing being
# diagnosed and a slow answer is still an answer. The values are the
# ones this probe has always used. What they are no longer tied to is
# the configuration API client's margin above the database's busy
# timeout, which was never this command's reason for either of them.
CONNECT_TIMEOUT_S = 5.0

READ_TIMEOUT_S = 30.0

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

    No subcommands, no --api-url and no --local, because this command
    reaches no API and no database and offering the flags would say it
    had.
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
        # Imported here rather than at the top of this module, and the
        # reason is the import graph rather than a cycle:
        # `onboarding/__init__` eagerly imports `unbound`, which imports
        # `device.bindings`, which imports `config.store` and
        # `vinga_server.db`. At module level that would pull the whole
        # database machinery into every invocation of this command, URL
        # argument or not, and this branch is the only one that wants
        # anything from that package at all.
        from vinga_server.onboarding.origin import onboarding_url

        derived, _ = onboarding_url(_server_config(args), ONBOARDING_OFF_FOR_DOCTOR)
        url = _device_url(derived, "the onboarding URL this configuration derives")
        shown = url
    response = _probed(url, shown)
    reported = _describe(response.text)
    if reported is None:
        raise ConfigError(_not_vinga_server(shown, response))
    answered = _reported_websocket(reported["websocket"])
    if answered is None:
        raise ConfigError(_unreadable_websocket(shown))
    scheme, websocket = answered
    # Both sides normalized, and the probe's side read from the response
    # rather than from the string an operator typed: `HTTPS://` is the
    # same scheme as `https://`, and the client has already lowered the
    # one the request went out with. No redirect is followed, so the
    # response came from the address that was asked and this is that
    # address's scheme.
    if response.url.scheme == "https" and scheme == "ws":
        raise ConfigError(_plain_websocket(shown, websocket))
    print(
        f"{shown} is vinga-server {printable(reported['version'])}, and sends devices "
        f"to {websocket} (protocol version {printable(reported['protocol'])})."
    )


def _server_config(args: argparse.Namespace) -> ServerConfig:
    """The file half's `server` section, read the way every command
    reads it. No database is opened and no config file has to exist:
    without one the field defaults and the VINGA_ environment are the
    whole answer."""
    return load_file_config(args.config).server


# Reaching the endpoint
#
# One request, built behind a seam the suite replaces with a test
# client, so the same entry point runs against a canned endpoint with no
# socket. What the seam does not cover is the refusals in front of it,
# which are what most of those tests are checking.


def build_client(url: str) -> httpx.Client:
    """The connection to the OTA endpoint, and the one seam here.

    `main()` is and stays synchronous, and httpx's ASGI transport is
    async-only, so the tests replace this with Starlette's TestClient:
    itself a synchronous `httpx.Client` subclass that drives an ASGI
    application through its own portal.

    There is no token parameter, and the absence is the point rather
    than an omission. The OTA endpoint is the token issuer, so it cannot
    require a credential, and this request goes wherever an operator
    typed: a client that cannot carry an Authorization header cannot
    hand the configuration API's bearer token to whatever answers at a
    device-facing address.
    """
    return httpx.Client(
        base_url=url,
        timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
    )


def _device_url(url: str, source: str) -> str:
    """A URL this client may GET the way a device would.

    The configuration API's transport policy (`config/cli.py`)
    deliberately does not apply. It exists because the bearer token
    rides on every request to that API, and this request carries no
    credential at all: the OTA endpoint is the token issuer, so it
    cannot require one. Refusing a plain http:// address here would
    refuse the ordinary LAN deployment, which is exactly what a device
    is pointed at.

    What does apply is the rest of the policy: a URL that cannot be read
    is refused, and userinfo is refused rather than carried, because
    anything in a URL ends up in shell history, in process lists and in
    access logs.

    No refusal repeats the address, not even with the userinfo taken
    off, which is where this is stricter than that policy: an OTA URL
    carries the path segment that stands in front of the token issuer,
    and on a deployment with onboarding turned off that segment is the
    whole protection the endpoint has.
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
    parsed = parsed_url(url, source)
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

    One request, and no redirect is followed at all. Since the hardware
    checkpoint of 2026-08-13 every device-facing route answers both
    spellings of its path directly, because the firmware does not follow
    a redirect on that request either, so a redirect from that address
    is by definition something other than a current deployment
    answering, and where it points is that something's choice. Following
    it would let whatever answers at an address decide which host this
    request reaches next, which inside the network a deployment sits in
    is worth refusing rather than reasoning about.

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


def _redirect_refused(shown: str) -> str:
    return (
        f"{shown} answered with a redirect this command does not follow. Following it "
        f"would let whatever answers there choose which host this request reaches next, "
        f"and this command runs inside the network a deployment sits in. Every "
        f"device-facing route answers both spellings of its path directly, so a redirect "
        f"from that address is something else answering. The target is not repeated here: "
        f"ask the address you meant directly."
    )

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
    return parsed.scheme, printable(shown_url(parsed))


def _unreadable_websocket(shown: str) -> str:
    return (
        f"{shown} answers as vinga-server, but the websocket URL it reports is not a "
        f"ws:// or wss:// URL this client can read, so a device pointed here would be "
        f"handed an address it cannot connect to. It is not quoted back, since it is "
        f"whatever that address returned. Check server.websocket_url on that deployment."
    )


__all__ = ["build_client", "main"]
