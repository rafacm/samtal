"""The short onboarding path: /x/<key>/, an alias of the OTA endpoint.

Onboarding a stock board means typing its backend URL into a captive
portal on a phone, character by character, with nothing on the far side
to say a typo happened. A URL hidden behind a long random segment is
therefore paid for twice: once by whoever types it and once by whoever
debugs the silence a mistyped one produces.

This package serves the same endpoint at a short, secret-derived alias.
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
never served answers, and says so in a line that quotes neither key. It
used to quote both, the attempt beside the correct one, so that an
operator could read a typo off the log character by character; the PR
#153 review ended that. The correct key is the segment standing in
front of the endpoint that issues device tokens, and repeating it
turned every probe of the path into a request for it; the attempt is
attacker-controlled text out of a URL, and a near miss of a real key is
a hint at the real key. Neither the derived key nor the configured
`server.ota_path` segment is printed anywhere now, and neither is any
device token.

What a miss reports instead is its shape: how many characters arrived,
and whether they were the kind a person types at a key (after case
folding, one to ten characters of the base32 alphabet) or something
nobody was typing. The URL to check a typo against comes from
`samtal-server config ota-url`, which prints it to the operator's own
terminal.
"""

import base64
import hashlib
import hmac
import os
import re
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, Response

from samtal_server.config.models import ONBOARDING_MOUNT_PATH, ServerConfig

from . import events

# What the key is derived over. Versioned, so a future scheme can change
# the derivation without a deployment silently keeping the old key.
KEY_LABEL = b"samtal-onboarding-key-v1"

# How much of the base32 digest the key is. Eight characters is 40 bits,
# which is not a password and is not meant to be: it stands in front of
# an endpoint whose stinginess is what actually protects it, and it is
# short enough to type off a screen without a mistake.
KEY_LENGTH = 8

# What separates a typo from a probe, as an exact rule rather than an
# impression: after case folding, one to ten characters, every one of
# them in the base32 alphabet. Ten because a key mistyped with a
# character or two too many is exactly the mistake the log line exists
# to diagnose.
#
# It used to decide what was repeated back into the log as well, and no
# longer does: nothing of either key is (see `_log_mismatch`). It now
# decides only which of the two events a miss is, which is why it lost
# its "loggable" and neither name is referenced outside this module.
TYPO_ATTEMPT_LENGTH = KEY_LENGTH + 2

_TYPO_ATTEMPT_RE = re.compile(rf"^[A-Z2-7]{{1,{TYPO_ATTEMPT_LENGTH}}}$")

Handler = Callable[[Request], Awaitable[Response]]


def derive_key(secret: str) -> str:
    """The onboarding key for one device-auth secret.

    Uppercase canonical, because that is what a display renders and what
    the configuration pins; the route matches case-insensitively.
    """
    digest = hmac.new(secret.encode("utf-8"), KEY_LABEL, hashlib.sha256).digest()
    return base64.b32encode(digest).decode("ascii")[:KEY_LENGTH]


def onboarding_key(server: ServerConfig) -> str | None:
    """The key this server's short route is served under, or None when
    there is none to derive and none pinned.

    None is not a failure: with device auth off there is no secret, so
    the route mounts keyless at /x/ (a trial network has nothing to hide
    the endpoint from that an open websocket does not already hand over).
    A pinned key is honoured either way, since it replaces the derivation
    rather than depending on it.

    None is also what an enabled auth with no secret in the environment
    answers. Inside the server that state cannot be reached, since it
    has already refused the boot; `samtal-server config ota-url` runs
    with no server and does reach it, and tells the three cases apart by
    the two fields it can read (a pinned key, and whether auth is on)
    without ever asking this module for the secret.
    """
    onboarding = server.onboarding
    if onboarding.key is not None:
        return onboarding.key
    if not server.auth.enabled:
        return None
    # The same variable `build_device_auth` reads, and read again rather
    # than taken from it, so the secret stays where it is used and no
    # object grows a property that hands it out.
    secret = os.environ.get(server.auth.secret_env, "").strip()
    return derive_key(secret) if secret else None


def onboarding_path(key: str | None) -> str:
    """The path the short route is served on, as a person types it."""
    return f"{ONBOARDING_MOUNT_PATH}/" if key is None else f"{ONBOARDING_MOUNT_PATH}/{key}/"


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
            _log_mismatch(folded)
            raise HTTPException(status_code=404)
        return await handler(request)

    return guarded


def _log_mismatch(folded: str) -> None:
    """The diagnostic for a wrong key, which quotes neither key.

    Both halves of what this line used to say were key material (the PR
    #153 review). The expected one is the segment standing in front of
    the token issuer, and repeating it turned any probe of the path into
    a request for it. The attempted one is attacker-controlled text
    arriving in a URL, and a near miss of a real key is itself a hint at
    the real key, which is why the shape rule that used to decide
    whether to quote it now only decides which of the two events this
    is: a string a person could have typed at a key, or a string nobody
    was typing.

    What is left is what a reader can act on: how long the attempt was,
    and which kind it was. The URL to check a typo against comes from
    `samtal-server config ota-url`, on the operator's own terminal.
    """
    if _TYPO_ATTEMPT_RE.match(folded):
        events.warning(
            "a request reached the onboarding path carrying %d characters shaped like "
            "a key, and not this server's; neither is repeated here. Check the URL "
            "typed into the device's captive portal against the one "
            "samtal-server config ota-url prints",
            len(folded),
            event="onboarding_key_mismatch",
            attempted_length=len(folded),
        )
        return
    events.warning(
        "a request reached the onboarding path carrying %d characters that are not "
        "shaped like a key at all, so they are not repeated here; the URL to type "
        "comes from samtal-server config ota-url",
        len(folded),
        event="onboarding_key_unshaped",
        attempted_length=len(folded),
    )
