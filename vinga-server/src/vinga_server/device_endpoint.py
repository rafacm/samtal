"""A device-facing address a person typed, and what it takes to reach one.

Two commands stand where a board stands. `vinga-server doctor` GETs an
OTA endpoint and says what a device pointed there would be told;
`vinga simulator` POSTs to one as a board does. They ask different
questions of the same kind of address, and the rules for talking to one
are the same rules whichever of them is asking.

Those rules are here rather than in either caller, because the surface
they govern is the one where a mistake is a leak. A supplied OTA URL can
be the deployment's secret `ota_path`, so no sentence repeats it; the
request itself is a place a library writes that URL into a log record
and puts it on an exception; and a second implementation of any of that
would be a second chance to get it wrong, on exactly the surface that
must not be got wrong twice.

What this owns:

- **A parsed endpoint, not a string.** `Endpoint` keeps the address as
  it was given and the parsed parts beside it, so composition is an
  operation rather than string arithmetic: `activation()` appends the
  segment a waiting board polls to the PATH and carries the query and
  the fragment through untouched. That is the discipline
  `config/cli.Address.endpoint` uses on the API side, and it exists here
  for the same reason: a client that joins a path onto an address with
  a query string on it sends the endpoint's name inside the query's
  value.
- **The address policy**, which is the device-facing one and not the
  configuration client's. A plain `http://` address is ordinary here
  rather than refused, because that is exactly what a board on a LAN is
  pointed at, and the request that goes to it carries no credential at
  all: the OTA endpoint is the token issuer, so it cannot require one.
  What does apply is the rest: a URL that cannot be read is refused, and
  a userinfo is refused rather than carried.
- **The stand-in name** every verdict uses instead of the address.
- **The request lifecycle**: one request, no redirect followed, the
  request loggers held quiet around it, the client built inside the
  boundary, the close reported rather than raised, and every failure a
  fixed sentence naming an exception's class and never a value.
- **The websocket URL rule**, since it is the same question asked of a
  different address: what a reply names is where a token this process is
  holding would be sent.

`config/cli.Address` is deliberately not reused, and the reason is
recorded so it is not rediscovered. Nothing under `vinga_server` may
import `config.cli` except `main.py`, and the two types' transport
policies are opposites: one refuses plain HTTP off loopback because the
API's bearer token rides every request, and this one has to allow it.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx

from vinga_server.config.loader import ConfigError
from vinga_server.config.printing import parsed_url, printable, shown_url
from vinga_server.logs import quieted

# What a URL given to one of these commands is called in every line it
# prints.
#
# The one URL that may be shown is a derived short one, whose key is the
# recorded exception to "a path segment in front of the token issuer is a
# credential". A URL an operator passes is not that: the documented way
# to check a deployment with onboarding turned off is to pass the legacy
# `ota_path` URL, which is exactly the segment nothing may print. So a
# supplied URL is never displayed, in any verdict, and this stands in for
# it.
SUPPLIED_ENDPOINT = "the supplied OTA endpoint"

# And what the websocket URL a reply named is called, for the same
# reason one step further out: it is far-side text that decides where a
# device token would be sent, so it is validated and never shown.
REPORTED_WEBSOCKET = "the websocket endpoint the reply named"

# What a waiting board appends to its OTA URL to poll.
#
# The server's own spelling of this segment is `ota.router.ACTIVATE_SEGMENT`,
# and it is not imported here because that module imports FastAPI, which
# the client half does not carry. Two spellings of one fact is a bug
# pending, so `tests/unit/test_device_endpoint.py` holds them equal; the
# server-side import is free there and refused here.
ACTIVATION_SEGMENT = "activate"

# The libraries that would narrate the request, and how quiet they are
# held while it is made.
#
# `httpx` writes one line per request at INFO carrying the method, the
# URL and the status, which `logs.py` names where it floors the vendor
# libraries and keeps deliberately, since for every other caller in this
# server it says nothing that is not already public. For this one it says
# the whole of what every verdict refuses to print: a supplied OTA URL
# can be the deployment's secret `ota_path`, and a log record is a
# retained surface in a way a terminal is not. `httpcore` traces the
# connection under it and is held with it.
#
# WARNING rather than off, so a library that has something genuinely
# wrong to say can still say it, and scoped to the request rather than
# set once, so nothing these commands do changes what a process that
# imported them logs afterwards.
REQUEST_LOGGERS = ("httpx", "httpcore")

QUIET_LEVEL = logging.WARNING

# How long a request to a device-facing address waits, and the reason is
# the doctor's, which is where both numbers come from. A bounded connect,
# so an address nothing listens on is reported in seconds rather than at
# the read bound, and a generous read, because the network a device sits
# on is the thing being diagnosed and a slow answer is still an answer.
# What they are no longer tied to is the configuration API client's
# margin above the database's busy timeout, which was never the reason
# for either of them.
CONNECT_TIMEOUT_S = 5.0

READ_TIMEOUT_S = 30.0


def build_client(url: str) -> httpx.Client:
    """The connection to a device-facing endpoint, and the one seam here.

    Both callers are and stay synchronous, and httpx's ASGI transport is
    async-only, so a suite replaces this with a client of its own: a
    Starlette `TestClient`, itself a synchronous `httpx.Client` subclass,
    or a client over an `httpx.MockTransport`. Each caller imports this
    name into its own module, so a suite patches the seam of the command
    it is driving and no other.

    There is no token parameter, and the absence is the point rather than
    an omission. The OTA endpoint is the token issuer, so it cannot
    require a credential, and this request goes wherever an operator
    typed: a client that cannot carry an Authorization header cannot hand
    the configuration API's bearer token to whatever answers at a
    device-facing address.
    """
    return httpx.Client(
        base_url=url,
        timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
    )


@dataclass(frozen=True)
class Endpoint:
    """A device-facing address that passed the policy, and what may be
    said about it.

    `given` is the address exactly as it was typed, trailing slash
    included, because the short path and the OTA path both end in one and
    a device sends what it was given. `parts` is the same address split,
    which is what makes composition an operation instead of string
    arithmetic. `shown` is what every sentence names it by, and it is
    never the address.
    """

    given: str
    parts: SplitResult
    shown: str

    @classmethod
    def parsed(cls, url: str, source: str, shown: str) -> "Endpoint":
        """One typed address, or the fixed refusal for what is wrong with
        it.

        The configuration API's transport policy deliberately does not
        apply. It exists because the bearer token rides on every request
        to that API, and this request carries no credential at all.
        Refusing a plain `http://` address here would refuse the ordinary
        LAN deployment, which is exactly what a device is pointed at.

        What does apply is the rest of that policy: a URL that cannot be
        read is refused, and userinfo is refused rather than carried,
        because anything in a URL ends up in shell history, in process
        lists and in access logs.

        No refusal repeats the address, not even with the userinfo taken
        off, which is where this is stricter than that policy: an OTA URL
        carries the path segment that stands in front of the token
        issuer, and on a deployment with onboarding turned off that
        segment is the whole protection the endpoint has.
        """
        # Before anything parses it: `urlsplit` deletes tabs, carriage
        # returns and newlines rather than refusing them (WHATWG's rule),
        # so a URL carrying one parses cleanly here and then reaches
        # httpx, which raises InvalidURL naming the character and its
        # position. A URL a person could have typed has no control
        # characters and no spaces in it, so this is where they stop.
        if any(character.isspace() or not character.isprintable() for character in url):
            raise ConfigError(
                f"{source} carries a space, a newline or another character a URL cannot "
                f"hold. It is not quoted back, both because an OTA URL can be the "
                f"deployment's own secret and because repeating a control character is how "
                f"one line of output becomes two."
            )
        parts = parsed_url(url, source)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ConfigError(
                f"{source} is not an http:// or https:// URL with a host. It is not quoted "
                f"back, since an OTA URL can be the deployment's own secret."
            )
        if parts.username or parts.password:
            raise ConfigError(
                f"{source} carries a username or a password in the URL, which is refused: the "
                f"OTA endpoint takes no credential in its URL, and anything in one ends up in "
                f"shell history, process lists and access logs. The address is not repeated "
                f"here either."
            )
        return cls(given=url, parts=parts, shown=shown)

    def activation(self) -> "Endpoint":
        """Where a board waiting for a code polls: this address with the
        activation segment appended to its PATH.

        Appended to the path and nowhere else, which is the whole reason
        this is an operation on a parsed address rather than a string
        with a word stuck on the end. A supplied URL may carry a query
        string, and `...?token=x` + `/activate` is the segment written
        inside the credential's value. The query and the fragment are
        carried through exactly as they were written, since what they
        hold can be a value a gateway compares literally.

        The slash is this side's, the way the firmware's own
        `Ota::Activate` adds one when the stored URL lacks it, so both
        spellings of the stored address poll the same path.
        """
        path = f"{self.parts.path.rstrip('/')}/{ACTIVATION_SEGMENT}"
        composed = self.parts._replace(path=path)
        return Endpoint(given=urlunsplit(composed), parts=composed, shown=self.shown)


def requested(
    method: str,
    endpoint: Endpoint,
    *,
    build: Callable[[str], httpx.Client],
    headers: Mapping[str, str] | None = None,
    body: object | None = None,
) -> httpx.Response:
    """One request to a device-facing address, and never anything else.

    One request, and no redirect is followed at all. Since the hardware
    checkpoint of 2026-08-13 every device-facing route answers both
    spellings of its path directly, because the firmware does not follow
    a redirect on that request either, so a redirect from that address is
    by definition something other than a current deployment answering,
    and where it points is that something's choice. Following it would
    let whatever answers at an address decide which host this request
    reaches next, which inside the network a deployment sits in is worth
    refusing rather than reasoning about.

    Building the client is inside the boundary with the request and the
    close. httpx validates a URL when it is given one, so construction is
    a place a URL refused by a library rather than by the policy above
    would otherwise leave as a traceback with the address in it.

    The whole of it is inside a logging boundary as well, and that is the
    one thing here that is not about what reaches a terminal. The client
    library writes a line per request naming the URL, which for these
    callers is the deployment's own secret arriving in a retained record.
    `REQUEST_LOGGERS` above says which loggers and why.

    The seam is a parameter rather than this module's own name, so a
    suite replaces the client of the command it is driving. Each caller
    passes its own, looked up as that module's global at call time, which
    is what makes patching it work.
    """
    problem: str | None = None
    client: httpx.Client | None = None
    answered: httpx.Response | None = None
    with quieted(REQUEST_LOGGERS, QUIET_LEVEL):
        try:
            try:
                client = build(endpoint.given)
                sent = dict(headers or {})
                # Two calls rather than one with a built-up keyword bag:
                # `json=None` is a body of `null` to httpx and not the
                # absence of one, so a GET that passed it would send a
                # body. None here means no body at all, and `{}` is the
                # empty object a version-1 activation poll sends.
                answered = (
                    client.request(method, endpoint.given, follow_redirects=False, headers=sent)
                    if body is None
                    else client.request(
                        method, endpoint.given, follow_redirects=False, headers=sent, json=body
                    )
                )
                if answered.is_redirect:
                    answered, problem = None, redirect_refused(endpoint.shown)
            except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
                # The exception's class name and nothing else. httpx puts
                # the request into its exceptions, its InvalidURL quotes
                # the character it refused, and drivers put whatever they
                # like into their messages; the class is the part that
                # says what happened. Raised after the handler, so nothing
                # walking a chain finds the original behind it.
                # ValueError covers the UnicodeError an IDNA host raises
                # on its way down.
                problem = (
                    f"cannot reach {endpoint.shown}: the request did not complete "
                    f"({type(exc).__name__}). Check that the server is running, that this "
                    f"is the address it serves, and that the network a device sits on can "
                    f"reach it."
                )
        finally:
            # The close is a step of the request rather than tidying
            # after it, so it answers a sentence instead of raising: an
            # exception out of a `finally` leaves this boundary
            # altogether, taking whatever a driver wrote into its message
            # with it, and it would replace a refusal already in flight.
            # Whatever failed first is what is reported.
            problem = problem or close_failed(client, endpoint.shown)
    if answered is None or problem is not None:
        # Every path that gets here left a sentence: a request that
        # produced no response, a redirect refused, or a close that would
        # not complete after a response that did arrive.
        raise ConfigError(problem)
    return answered


def close_failed(client: httpx.Client | None, shown: str) -> str | None:
    """Give the connection back, and say so when it will not go.

    Answered rather than raised, for the reason the caller's `finally`
    states, and named by its class rather than quoted, for the reason
    every other failure here is: a transport failing on its way out can
    put the address, a header or a driver's own text into its message.

    A close that fails ends the command rather than being swallowed. What
    these commands answer is what one address said when it was asked
    cleanly, and a request that could not be finished is not something to
    report a healthy endpoint from; a diagnostic that quietly dropped a
    failure would be the wrong tool twice over.
    """
    if client is None:
        return None
    try:
        client.close()
    except Exception as exc:
        return (
            f"{shown} answered, but the connection to it could not be closed "
            f"({type(exc).__name__}), so no verdict is printed: a probe that did not "
            f"finish cleanly is not one to call an endpoint healthy from. What the "
            f"library said is not repeated here."
        )
    return None


def redirect_refused(shown: str) -> str:
    return (
        f"{shown} answered with a redirect this command does not follow. Following it "
        f"would let whatever answers there choose which host this request reaches next, "
        f"and this command runs inside the network a deployment sits in. Every "
        f"device-facing route answers both spellings of its path directly, so a redirect "
        f"from that address is something else answering. The target is not repeated here: "
        f"ask the address you meant directly."
    )


def _split_websocket(url: str) -> SplitResult | None:
    """A websocket URL a reply named, split, or None when it is not one
    this client can read at all.

    What arrived is whatever the far end wrote, so the parser's own
    failures are kept inside this boundary: `urlsplit` raises on a
    malformed IPv6 literal and `.port` raises on a port that is not a
    number, and both put the text they refused into the exception.
    """
    try:
        parsed = urlsplit(url)
        # Read here, since it parses on access and raises for a port that
        # is not a number in range.
        _ = parsed.port
    except ValueError:
        return None
    # `urlsplit` lower-cases the scheme it parsed, which is what makes
    # this a normalization rather than a hope.
    if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
        return None
    return parsed


def reported_websocket(url: str) -> tuple[str, str] | None:
    """The websocket URL a reply named: its normalized scheme and the
    form that may be printed, or None when it is not a websocket URL this
    client can read.

    The reading a DIAGNOSIS makes of it. `vinga-server doctor` reports
    where a device would be sent and never goes there, so a URL with a
    credential written into it is still a fact worth reporting, shown
    with the credential taken out.

    The scheme is returned rather than re-derived by the caller, and it
    is the parser's normalized one, because a comparison against the
    literal `ws://` is a comparison a `WS://` walks past.

    It is parsed before it is shown: bounded, made printable, and
    stripped of any credential written into it. There is deliberately no
    fallback to the raw string. A URL that will not parse is exactly the
    one whose userinfo could not be taken off, so falling back would
    print the credential in precisely the case the stripping exists for.
    """
    parsed = _split_websocket(url)
    if parsed is None:
        return None
    return parsed.scheme, printable(shown_url(parsed))


def websocket_target(url: str, reached: str) -> str | None:
    """The websocket URL a reply named, as somewhere a device token may
    actually be sent, or None when it may not be.

    The reading a CLIENT makes of it, which is stricter than the
    diagnosis above by exactly the two rules that are about a credential
    rather than about a configuration.

    It carries no userinfo, refused outright for the reason
    `Endpoint.parsed` refuses one: a credential in a URL reaches shell
    history, process lists and access logs, and a client that connected
    anyway would be the thing that published it.

    And it may not downgrade. An endpoint reached over `https://` may not
    answer with a `ws://` URL, which is the TLS-proxy misconfiguration
    `doctor` already calls out as a failure of its own; a device token
    crossing a plain socket from behind TLS is the same mistake the
    configuration client has no flag to make.

    What comes back is the URL as it was given, because it is what a
    socket is opened on and not what a sentence says. No sentence names
    it: `REPORTED_WEBSOCKET` is what a verdict says instead.
    """
    parsed = _split_websocket(url)
    if parsed is None:
        return None
    if parsed.username or parsed.password:
        return None
    if downgraded(reached, parsed.scheme):
        return None
    return url


def downgraded(reached: str, websocket: str) -> bool:
    """Whether an endpoint reached over TLS answered with a plain
    websocket URL.

    The TLS-proxy misconfiguration, and a rule about a credential as much
    as about a configuration: a device token crossing a plain socket from
    behind TLS is the same mistake the configuration client has no flag
    to make. Both schemes normalized, and the request's read from the
    response rather than from the string an operator typed, since
    `HTTPS://` is the same scheme as `https://` and no redirect was
    followed, so the response came from the address that was asked.
    """
    return reached == "https" and websocket == "ws"


__all__ = [
    "ACTIVATION_SEGMENT",
    "CONNECT_TIMEOUT_S",
    "QUIET_LEVEL",
    "READ_TIMEOUT_S",
    "REPORTED_WEBSOCKET",
    "REQUEST_LOGGERS",
    "SUPPLIED_ENDPOINT",
    "Endpoint",
    "build_client",
    "close_failed",
    "downgraded",
    "redirect_refused",
    "reported_websocket",
    "requested",
    "websocket_target",
]
