"""The short onboarding path: /x/<key>/, an alias of the OTA endpoint.

Onboarding a stock board means typing its backend URL into a captive
portal on a phone, character by character, with nothing on the far side
to say a typo happened. A URL hidden behind a long random segment is
therefore paid for twice: once by whoever types it and once by whoever
debugs the silence a mistyped one produces.

This module serves the same endpoint at a short, secret-derived alias.
The key is `base32(HMAC-SHA256(device-auth secret, label))` truncated to
eight characters: derived from a secret the deployment already has, so
nothing new is configured, stored or persisted; stable across restarts;
and rotating only when the secret rotates. Base32 because A-Z2-7 holds
no 0/O and no 1/I/l, the pairs a person misreads off a 240x240 display,
and matched case-insensitively because a phone keyboard offers lower
case first. It is also served with and without its trailing slash, by
the same handlers rather than by a redirect between them: a captive
portal saves what it likes, and the firmware treats a redirect on this
request as an error rather than following it.

A wrong key answers the stock 404, byte for byte what a path that was
never served answers, and logs the attempted key next to the correct
one so the operator sees the typo character by character. That log line
is a deliberate, recorded trade: the key is a deployment-scoped path
segment, not a per-device token, so the rule that tokens are never
logged is untouched. The configured `server.ota_path` segment is not
printed anywhere, and neither is any device token.

The attempt itself is attacker-controlled text out of a URL, so what
may be repeated is bounded: after case folding, one to ten characters
of the base32 alphabet, and nothing else. That is a mistyped or
over-typed key, which is what the line is for; anything longer or
carrying a newline, a control character or any other byte is counted
rather than quoted, so no request can forge a log entry or choose how
long one is.
"""

import base64
import hashlib
import hmac
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response

from samtal_server import ota
from samtal_server.config import Config
from samtal_server.config.models import ONBOARDING_MOUNT_PATH

logger = logging.getLogger(__name__)

# What the key is derived over. Versioned, so a future scheme can change
# the derivation without a deployment silently keeping the old key.
KEY_LABEL = b"samtal-onboarding-key-v1"

# How much of the base32 digest the key is. Eight characters is 40 bits,
# which is not a password and is not meant to be: it stands in front of
# an endpoint whose stinginess is what actually protects it, and it is
# short enough to type off a screen without a mistake.
KEY_LENGTH = 8

# What a mismatch may repeat back into the log, as an exact rule rather
# than an impression: after case folding, one to ten characters, every
# one of them in the base32 alphabet. Ten because a key mistyped with a
# character or two too many is exactly the mistake the log line exists to
# diagnose, and the upper bound is what keeps an attacker from choosing
# how long a log entry is. The alphabet excludes everything a forged
# entry needs, newlines and control characters first among them.
LOGGABLE_ATTEMPT_LENGTH = KEY_LENGTH + 2

_LOGGABLE_ATTEMPT_RE = re.compile(rf"^[A-Z2-7]{{1,{LOGGABLE_ATTEMPT_LENGTH}}}$")

Handler = Callable[[Request], Awaitable[Response]]


def derive_key(secret: str) -> str:
    """The onboarding key for one device-auth secret.

    Uppercase canonical, because that is what a display renders and what
    the configuration pins; the route matches case-insensitively.
    """
    digest = hmac.new(secret.encode("utf-8"), KEY_LABEL, hashlib.sha256).digest()
    return base64.b32encode(digest).decode("ascii")[:KEY_LENGTH]


def onboarding_key(config: Config) -> str | None:
    """The key this server's short route is served under, or None when
    there is none to derive and none pinned.

    None is not a failure: with device auth off there is no secret, so
    the route mounts keyless at /x/ (a trial network has nothing to hide
    the endpoint from that an open websocket does not already hand over).
    A pinned key is honoured either way, since it replaces the derivation
    rather than depending on it.
    """
    onboarding = config.server.onboarding
    if onboarding.key is not None:
        return onboarding.key
    if not config.server.auth.enabled:
        return None
    # The same variable `build_device_auth` reads, and read again rather
    # than taken from it, so the secret stays where it is used and no
    # object grows a property that hands it out. An enabled auth with no
    # secret has already refused the boot by the time this runs.
    secret = os.environ.get(config.server.auth.secret_env, "").strip()
    return derive_key(secret) if secret else None


def onboarding_path(key: str | None) -> str:
    """The path the short route is served on, as a person types it."""
    return f"{ONBOARDING_MOUNT_PATH}/" if key is None else f"{ONBOARDING_MOUNT_PATH}/{key}/"


@dataclass(frozen=True)
class Origin:
    """The origin devices reach this server on, and where it came from.

    The provenance travels with the value because two of the three
    sources are inferences: a URL that came out of `websocket_url` is
    only as right as that key is, and one built from the listen address
    is a guess. A line that named neither would read as fact.
    """

    url: str
    source: str
    guessed: bool = False
    note: str = ""

    @property
    def provenance(self) -> str:
        prefix = "guessed from" if self.guessed else "from"
        return f"{prefix} {self.source}{self.note}"


def public_origin(config: Config) -> Origin:
    """Where a device reaches this server, in the order the plan sets:
    `public_url` as written, else the origin of `websocket_url`, else the
    listen address, which is a guess and says so.

    Total by construction. Every step that could raise falls through to
    the next source instead, and the last source is two configuration
    fields that cannot fail, so an operator never meets this as a
    traceback at startup.
    """
    server = config.server
    if server.public_url:
        return Origin(server.public_url, "server.public_url")
    unreadable = False
    if server.websocket_url:
        derived = _origin_of(server.websocket_url)
        if derived is not None:
            return Origin(derived, "server.websocket_url")
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
        f"http://{_bracketed(server.host)}:{server.port}",
        "the listen address (server.host and server.port)",
        guessed=True,
        note=", " + "; ".join(reasons),
    )


def _origin_of(websocket_url: str) -> str | None:
    """The http origin behind a `ws://` or `wss://` URL, or None when
    there is none to take.

    Built from the parsed hostname and port, never from the raw netloc,
    so a `user:password@host` cannot ride into a log line through the
    banner. Both of the parse steps that raise are caught: `urlsplit`
    itself for a malformed IPv6 host, and `.port` for one that is not a
    number in range. The configuration validator refuses both, and this
    is what keeps a configuration built in code from crashing a startup
    the validator would have refused.
    """
    try:
        parts = urlsplit(websocket_url)
        hostname, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not hostname:
        return None
    scheme = "https" if parts.scheme == "wss" else "http"
    return f"{scheme}://{_bracketed(hostname)}{'' if port is None else f':{port}'}"


def _bracketed(host: str) -> str:
    """An IPv6 literal in the brackets a URL needs, anything else as it
    is. `urlsplit` strips the brackets from a hostname, and this is what
    puts them back."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def portal_url_line(config: Config, path: str) -> str:
    """The one line naming the URL to type into a device's captive
    portal, for the path it is served on."""
    origin = public_origin(config)
    return f"Type this into the device's captive portal: {origin.url}{path} ({origin.provenance})"


def log_banner(config: Config) -> None:
    """Say the onboarding URL out loud at startup.

    With onboarding on this is the short URL, key and all: the key is a
    deployment-scoped path segment, deliberately printed so that a typo
    and a rotated secret both diagnose themselves. With onboarding off
    the line names `server.ota_path` without quoting it, because that
    segment is a credential and the logs must not carry it.
    """
    server = config.server
    origin = public_origin(config)
    if not server.onboarding.enabled:
        logger.info(
            "device onboarding is off: devices are configured at the server.ota_path "
            "path on %s (%s), which is not printed here, since that segment is this "
            "deployment's secret",
            origin.url,
            origin.provenance,
            extra={
                "event": "onboarding_banner",
                "origin": origin.url,
                "origin_source": origin.source,
                "onboarding": False,
            },
        )
        return
    key = onboarding_key(config)
    url = f"{origin.url}{onboarding_path(key)}"
    logger.info(
        "device onboarding URL: %s (%s)",
        url,
        origin.provenance,
        extra={
            "event": "onboarding_banner",
            "url": url,
            "origin_source": origin.source,
            "onboarding": True,
        },
    )


def build_router(key: str | None) -> APIRouter:
    """The short-path router, delegating to the OTA handlers.

    Built per app rather than at import time, for the reason the OTA
    router is: the key is derived from configuration that has not been
    read when this module is imported.
    """
    router = APIRouter()
    if key is None:
        for path in _spellings(onboarding_path(None)):
            router.post(path)(ota.check_version)
            router.get(path)(ota.describe)
        return router
    for path in _spellings(f"{ONBOARDING_MOUNT_PATH}/{{key}}/"):
        router.post(path)(_guarded(key, ota.check_version))
        router.get(path)(_guarded(key, ota.describe))
    return router


def _spellings(path: str) -> tuple[str, str]:
    """The path as written and the path without its trailing slash, both
    of which are served by the same handler.

    Not a redirect between the two, which is what this was until a
    factory board met it (2026-08-13): the captive portal saved the
    typed URL without its trailing slash, the device POSTed to the
    slashless spelling, and the firmware does not follow a redirect on
    this request. It rendered "code=307" on its screen and restarted in
    a loop, and nothing reached a handler, so the server had nothing to
    say about it either. A device-facing endpoint cannot spend a
    round trip on a redirect it has no evidence the device will follow,
    so both spellings answer.
    """
    return path, path.rstrip("/")


def _guarded(expected: str, handler: Handler) -> Handler:
    """The handler behind a key check that leaks nothing on a miss.

    Raising the 404 rather than composing one is what makes the response
    byte-identical to an unserved path: the same exception handler
    renders both, so the two cannot drift apart later.
    """

    async def guarded(request: Request) -> Response:
        # Case folded once: it is what the comparison uses, and it is
        # also the only form that is ever logged, which is what makes
        # the shape check below a guarantee about the output rather than
        # about the input (an upper-casing of some Unicode characters is
        # an ASCII letter, and the folded string is what is rendered).
        folded = str(request.path_params.get("key", "")).upper()
        if not hmac.compare_digest(folded.encode("utf-8"), expected.encode("utf-8")):
            _log_mismatch(folded, expected)
            raise HTTPException(status_code=404)
        return await handler(request)

    return guarded


def _log_mismatch(folded: str, expected: str) -> None:
    """The diagnostic for a wrong key, which repeats the attempt only
    when the attempt is something a person could have typed at a key.

    The correct key beside the attempted one is what makes a typo and a
    rotated secret diagnose themselves, and it is the recorded trade
    from issue #40. The attempt, though, is attacker-controlled text
    arriving in a URL, and a raw one carries a newline into the log as a
    forged second entry, or a megabyte of anything. So it is repeated
    only when it matches the shape a mistyped key has; anything else is
    counted rather than quoted, and the correct key is left out of that
    line too, so probing cannot turn the log into a broadcast of it.
    """
    if _LOGGABLE_ATTEMPT_RE.match(folded):
        logger.warning(
            "onboarding key %s does not match this server's key %s: check the URL "
            "typed into the device's captive portal, character by character",
            folded,
            expected,
            extra={
                "event": "onboarding_key_mismatch",
                "attempted": folded,
                "expected": expected,
            },
        )
        return
    logger.warning(
        "a request reached the onboarding path carrying %d characters that are not "
        "shaped like a key at all, so they are not repeated here; the URL to type is "
        "in the startup line",
        len(folded),
        extra={"event": "onboarding_key_unshaped", "attempted_length": len(folded)},
    )
