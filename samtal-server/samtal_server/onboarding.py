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
case first.

A wrong key answers the stock 404, byte for byte what a path that was
never served answers, and logs the attempted key next to the correct
one so the operator sees the typo character by character. That log line
is a deliberate, recorded trade: the key is a deployment-scoped path
segment, not a per-device token, so the rule that tokens are never
logged is untouched. The configured `server.ota_path` segment is not
printed anywhere, and neither is any device token.
"""

import base64
import hashlib
import hmac
import logging
import os
from collections.abc import Awaitable, Callable

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


def build_router(key: str | None) -> APIRouter:
    """The short-path router, delegating to the OTA handlers.

    Built per app rather than at import time, for the reason the OTA
    router is: the key is derived from configuration that has not been
    read when this module is imported.
    """
    router = APIRouter()
    if key is None:
        router.post(onboarding_path(None))(ota.check_version)
        router.get(onboarding_path(None))(ota.describe)
        return router
    path = f"{ONBOARDING_MOUNT_PATH}/{{key}}/"
    router.post(path)(_guarded(key, ota.check_version))
    router.get(path)(_guarded(key, ota.describe))
    return router


def _guarded(expected: str, handler: Handler) -> Handler:
    """The handler behind a key check that leaks nothing on a miss.

    Raising the 404 rather than composing one is what makes the response
    byte-identical to an unserved path: the same exception handler
    renders both, so the two cannot drift apart later.
    """

    async def guarded(request: Request) -> Response:
        attempted = str(request.path_params.get("key", ""))
        if not hmac.compare_digest(
            attempted.upper().encode("utf-8"), expected.encode("utf-8")
        ):
            # The one place the correct key is printed, and it is printed
            # next to the attempted one so a typo reads off the pair. A
            # rotated secret diagnoses itself here too.
            logger.warning(
                "onboarding key %s does not match this server's key %s: check the URL "
                "typed into the device's captive portal, character by character",
                attempted,
                expected,
                extra={
                    "event": "onboarding_key_mismatch",
                    "attempted": attempted,
                    "expected": expected,
                },
            )
            raise HTTPException(status_code=404)
        return await handler(request)

    return guarded
