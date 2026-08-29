"""What address the outside world reaches this server on, and the
startup line that says it.

One question, asked in five places: the banner below, the line
`vinga-server config ota-url` and `vinga-server doctor` print, the
`message` an activating device shows on its screen, the websocket URL
the OTA reply hands every board, and the portal line printed beside it
on the human GET of the same endpoint. The first three call
`public_origin`, so a deployment names itself the same way wherever it
is named, and the provenance travels with the value because two of the
three sources it resolves are inferences. The fourth calls
`websocket_url_for` and the fifth `portal_url_line`.

What a deployment may configure is two rules rather than one, and they
are deliberately not the same rule. The four addresses a person or a
screen is shown resolve `public_url` first and the origin of
`websocket_url` behind it, which is `_configured` below: `public_url`
is the name a deployment goes by, and where it is unset the websocket
URL is the one key a proxied deployment has already had to get right.
`websocket_url_for` consults `server.websocket_url` and nothing else,
because what it answers is the URL a board is handed rather than an
origin to print: `public_url` names an origin, may carry a path prefix
and says nothing about the websocket route, so a wire URL built from it
would be a guess wearing a configured key's authority.

Where the applicable key is unset, the two that answer a request have a
fallback the other three cannot have. A request is a demonstration of
an address that reached this server and the listen address is a guess
(issue #340), so the banner and the two commands guess, because nothing
has asked them anything, and the two lines in a reply prefer what the
request arrived on.

`onboarding_url` is the assembly the two commands share, and it lives
here for the same reason the origin does: it is one composition of the
origin above and the key `keys.py` derives, and a second copy of it
would be a second answer to what a person is supposed to type.

Both of them assemble through `assemble` below and neither assembles on
its own, which is the point of gathering them here (issue #143): there
is one place that turns a scheme, an authority and a path into a URL,
and one place to read to know what a device is told.

The two modes are a real distinction and not a convenience. An address
this server RETAINS (a banner, a screen, a log line, a printed URL) is
rebuilt from a parsed hostname and port, which is what keeps a
`user:password@host` out of it. An address that goes back out on the
WIRE, to the board that just reached us, is the request's own netloc
verbatim: it is the address that demonstrably works, this server trusts
no forwarded header to improve on it, and a rebuild could only make it
different from what arrived.

Which mode a request-derived origin is in is decided by what is done
with it rather than by where it came from. `websocket_url_for` is on the
wire and takes the netloc verbatim; the portal line is a URL printed for
a person to type, so it is retained by that rule and is rebuilt from the
request's own parsed hostname and port. The two agree on every Host a
board or a browser really sends, and where they would differ (userinfo
in the header, a port that is not a number) it is the printed line that
must not carry it.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Request

from vinga_server.config.loader import ConfigError
from vinga_server.config.models import ServerConfig
from vinga_server.device.boundary import WEBSOCKET_PATH
from vinga_server.events.catalog import OnboardingOff, OnboardingOn
from vinga_server.events.values import (
    Flag,
    Identifier,
    OriginProvenance,
    OriginSource,
)

from . import events
from .keys import onboarding_key, onboarding_path

# Said when there is no short URL to derive, with the fix the command
# that asked for one needs. The configured `ota_path` segment is named
# and never quoted: it is a credential, and the derived key is the one
# recorded exception to that rule.
ONBOARDING_OFF = (
    "device onboarding is off (server.onboarding.enabled is false), so this "
    "configuration serves no short URL. Devices are configured at the path "
    "server.ota_path names, on {origin} ({provenance}), and that segment is not printed "
    "here, since it is this deployment's secret. {fix}"
)

# What `assemble` may be handed as an authority, and the whole of the
# difference between its two modes. A `str` is a netloc taken verbatim
# from a request; a pair is a hostname and an optional port this module
# parsed out of something and rebuilds, bracketing an IPv6 literal on
# the way back.
NetLoc = str | tuple[str, int | None]


def assemble(scheme: str, netloc: NetLoc, path: str = "") -> str:
    """One URL, from the three pieces every caller here has.

    The only place that writes `scheme://authority` for an address a
    DEVICE is told about, which is the set this module is responsible
    for. (`config/cli.py` writes one more, the loopback address of this
    machine's own API, which no device ever hears and which resolves
    from a port rather than from anything a request carried.)

    Which mode a caller is in is which type it passes, and the two are
    documented in this module's own docstring: verbatim for the wire,
    rebuilt for anything retained.
    """
    if isinstance(netloc, tuple):
        hostname, port = netloc
        netloc = f"{_bracketed(hostname)}{'' if port is None else f':{port}'}"
    return f"{scheme}://{netloc}{path}"


def websocket_url_for(server: ServerConfig, request: Request) -> str:
    """The websocket URL to hand this device: the configured one, or the
    address it just reached the OTA endpoint on.

    The wire mode of `assemble`, and the one caller of it: what goes
    into the reply is the netloc the request arrived with, exactly as it
    arrived. Cannot fail, and trusts no forwarded header.
    """
    configured = server.websocket_url
    if configured:
        return configured
    scheme = "wss" if request.url.scheme == "https" else "ws"
    return assemble(scheme, request.url.netloc, WEBSOCKET_PATH)


@dataclass(frozen=True)
class Origin:
    """The origin devices reach this server on, and where it came from.

    The provenance travels with the value because two of the three
    sources are inferences: a URL that came out of `websocket_url` is
    only as right as that key is, and one built from the listen address
    is a guess. A line that named neither would read as fact.
    """

    url: str
    source: OriginSource
    guessed: bool = False
    note: str = ""

    @property
    def provenance(self) -> str:
        prefix = "guessed from" if self.guessed else "from"
        return f"{prefix} {self.source}{self.note}"


def _configured(server: ServerConfig) -> Origin | None:
    """The origin this configuration states, and None where it states
    none: `public_url` as written, else the origin of `websocket_url`.

    The half of the order the four printed addresses share, held in one
    place so that a line answering a request and a line printed with no
    request cannot come to disagree about which key wins (#340). What
    differs between them is only what happens when this answers None.

    Not the wire's order, which is `websocket_url_for`'s own and reads
    one key: `public_url` names an origin rather than a websocket URL,
    and this module's docstring says why that is a distinction rather
    than an omission.
    """
    if server.public_url:
        return Origin(server.public_url, OriginSource.PUBLIC_URL)
    if server.websocket_url:
        derived = _origin_of(server.websocket_url)
        if derived is not None:
            return Origin(derived, OriginSource.WEBSOCKET_URL)
    return None


def public_origin(server: ServerConfig) -> Origin:
    """Where a device reaches this server, in the order the plan sets:
    `public_url` as written, else the origin of `websocket_url`, else the
    listen address, which is a guess and says so.

    The answer where nothing has asked anything: the startup banner and
    the two commands that print a URL with no server running. A line
    answering a request has a better last source than the listen address
    and takes it (`portal_url_line`).

    Total by construction. Every step that could raise falls through to
    the next source instead, and the last source is two configuration
    fields that cannot fail, so an operator never meets this as a
    traceback at startup.
    """
    configured = _configured(server)
    if configured is not None:
        return configured

    reasons: list[str] = []
    if server.websocket_url:
        # Reachable only for a configuration built in code, since the
        # validator refuses one a file could hold. Said out loud anyway:
        # a guess that had a better source and could not use it is not
        # the same guess as one that never had a source.
        reasons.append("server.websocket_url could not be read as a URL")
    if server.host in ("0.0.0.0", "::", "[::]"):
        reasons.append(
            f"{server.host} is where the server listens rather than a name a device "
            f"can reach"
        )
    reasons.append("set server.public_url to name this deployment exactly")
    return Origin(
        assemble("http", (server.host, server.port)),
        OriginSource.LISTEN_ADDRESS,
        guessed=True,
        note=", " + "; ".join(reasons),
    )


def _origin_of(websocket_url: str) -> str | None:
    """The http origin behind a `ws://` or `wss://` URL, or None when
    there is none to take.

    The retained mode of `assemble`: built from the parsed hostname and
    port, never from the raw netloc, so a `user:password@host` cannot
    ride into a log line through the banner. Both of the parse steps
    that raise are caught: `urlsplit` itself for a malformed IPv6 host,
    and `.port` for one that is not a number in range. The configuration
    validator refuses both, and this is what keeps a configuration built
    in code from crashing a startup the validator would have refused.
    """
    try:
        parts = urlsplit(websocket_url)
        hostname, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not hostname:
        return None
    scheme = "https" if parts.scheme == "wss" else "http"
    return assemble(scheme, (hostname, port))


def _bracketed(host: str) -> str:
    """An IPv6 literal in the brackets a URL needs, anything else as it
    is. `urlsplit` strips the brackets from a hostname, and this is what
    puts them back."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def onboarding_url(server: ServerConfig, fix: str) -> tuple[str, Origin]:
    """The short URL this configuration serves, and where its origin
    came from.

    Nothing here is a second implementation of anything: the key comes
    from `keys.onboarding_key` and the origin from `public_origin`
    above, which are what the server mounts the route with and what the
    startup banner prints. `fix` is what to do when onboarding is off,
    which differs by the command that asked, so each of them keeps its
    own sentence and this one keeps neither.

    Reached by the two commands that answer with no server running, and
    a refusal is theirs to print: it leaves as the `ConfigError` both of
    their boundaries already catch.
    """
    origin = public_origin(server)
    if not server.onboarding.enabled:
        raise ConfigError(
            ONBOARDING_OFF.format(origin=origin.url, provenance=origin.provenance, fix=fix)
        )
    key = onboarding_key(server)
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
    return f"{origin.url}{onboarding_path(key)}", origin


def _arrived_on(request: Request) -> Origin | None:
    """The origin this request reached this server on, or None when its
    Host carries no address to read.

    The retained mode of `assemble`, for the reason this module's
    docstring gives: the line built from this is printed for a person to
    type, so it is rebuilt from the parsed hostname and port rather than
    taken from the netloc the way the websocket URL beside it is.

    Starlette takes a Host header as the request's netloc only when it
    is a bare host with an optional port, and answers from the listen
    address otherwise, so userinfo does not arrive here to begin with.
    The rebuild is this module's own layer behind that one, which is
    what makes the rule a fact about the value rather than about a
    framework version. Reading the port is caught for its own reason,
    the one `_origin_of` catches it for: a number outside the range is
    shaped like a port and is not one, this endpoint is
    unauthenticated, and a printed line is no place to learn that a
    header could not be parsed.
    """
    try:
        hostname, port = request.url.hostname, request.url.port
    except ValueError:
        return None
    if not hostname:
        return None
    scheme = "https" if request.url.scheme == "https" else "http"
    return Origin(assemble(scheme, (hostname, port)), OriginSource.REQUEST_HOST)


def portal_url_line(server: ServerConfig, request: Request) -> str:
    """The one line naming the URL to type into a device's captive
    portal, for the request that asked for it.

    Three sources, first answer wins, and only the last of them is new
    (#340). A configured origin wins exactly as it does for the banner
    and the two commands, which is the shared rule `_configured` holds.
    Failing that the address is the one this request arrived on, which
    is the preference the websocket URL in the same reply has always
    had for the key it reads: this line used to print the
    listen-address guess beside a websocket URL derived from the
    request, so one reply said `ws://192.168.1.34:8003/` and
    `http://0.0.0.0:8003/` about the same server. Failing both, the
    guess, which is reachable only for a Host header that names no
    readable address.

    The path is the request's own, so the line is the URL that works for
    whoever is holding it rather than the one this server would
    recommend.
    """
    origin = _configured(server) or _arrived_on(request) or public_origin(server)
    return (
        f"Type this into the device's captive portal: "
        f"{origin.url}{request.url.path} ({origin.provenance})"
    )


def log_banner(server: ServerConfig) -> None:
    """Say where devices are configured at startup, and where to read
    the URL to type.

    Not the URL itself, and this is a deliberate narrowing (the PR #153
    review). The derived key is a path segment standing in front of the
    token issuer, and a startup line is a retained record like every
    other: shipped to whatever collects logs, kept as long as they are
    kept, readable by everyone who can read them. Printing it here to
    let a typo diagnose itself traded that away for a convenience the
    operator already has by another route.

    The route is `vinga-server config ota-url`, which derives the same
    URL from the same file and the same secret, contacts nothing, and
    prints it to the operator's own terminal rather than to a log. So
    the banner names the origin, says whether the short path is on and
    whether a key stands in front of it, and points at that command.
    With onboarding off it names `server.ota_path` without quoting it,
    for the reason it always did.
    """
    origin = public_origin(server)
    if not server.onboarding.enabled:
        events.emit(
            lambda: OnboardingOff(
                origin=Identifier(origin.url),
                origin_source=origin.source,
                provenance=OriginProvenance(origin.provenance),
            )
        )
        return
    events.emit(
        lambda: OnboardingOn(
            origin=Identifier(origin.url),
            origin_source=origin.source,
            # Whether anything stands in front of the short route at
            # all. With device auth off there is no secret to derive a
            # key from and it mounts keyless, which is a fact about the
            # deployment rather than about the key, so it is safe to say
            # and worth saying.
            keyed=Flag(onboarding_key(server) is not None),
            provenance=OriginProvenance(origin.provenance),
        )
    )
