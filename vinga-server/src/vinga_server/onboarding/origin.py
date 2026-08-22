"""What address the outside world reaches this server on, and the
startup line that says it.

One question, asked in four places: the banner below, the line
`vinga-server config ota-url` and `config doctor` print, the `message`
an activating device shows on its screen, and the websocket URL the OTA
reply hands every board. The first three call `public_origin`, so a
deployment names itself the same way wherever it is named, and the
provenance travels with the value because two of the three sources it
resolves are inferences. The fourth calls `websocket_url_for`.

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
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Request

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
from .keys import onboarding_key

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


def public_origin(server: ServerConfig) -> Origin:
    """Where a device reaches this server, in the order the plan sets:
    `public_url` as written, else the origin of `websocket_url`, else the
    listen address, which is a guess and says so.

    Total by construction. Every step that could raise falls through to
    the next source instead, and the last source is two configuration
    fields that cannot fail, so an operator never meets this as a
    traceback at startup.
    """
    if server.public_url:
        return Origin(server.public_url, OriginSource.PUBLIC_URL)
    unreadable = False
    if server.websocket_url:
        derived = _origin_of(server.websocket_url)
        if derived is not None:
            return Origin(derived, OriginSource.WEBSOCKET_URL)
        unreadable = True

    reasons: list[str] = []
    if unreadable:
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


def portal_url_line(server: ServerConfig, path: str) -> str:
    """The one line naming the URL to type into a device's captive
    portal, for the path it is served on."""
    origin = public_origin(server)
    return f"Type this into the device's captive portal: {origin.url}{path} ({origin.provenance})"


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
