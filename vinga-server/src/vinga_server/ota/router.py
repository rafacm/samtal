"""Where the handlers are served, and under which spellings.

Two routers over the same three handler objects. `build_router` mounts
them at the configured `server.ota_path`; `build_alias_router` mounts
them again at the short `/x/<key>/` path a person can type into a
captive portal, behind the onboarding key guard. Registration is by
reference in both, which is what makes the alias an alias rather than a
second implementation that could come to answer differently.

The guard is an ordinary dependency here: `onboarding.keys` decides what
a key is and what a miss is told, and this module only asks it to stand
in front of a handler. That direction is the whole of what issue #143
untangled, and it is why the short router lives beside the handlers it
serves rather than beside the key it is guarded by.
"""

from fastapi import APIRouter

from vinga_server.config.models import ONBOARDING_MOUNT_PATH
from vinga_server.onboarding.keys import _guarded, onboarding_path

from .poll import activate
from .reply import check_version, describe

# The default path, and the one every test and the README use. An operator
# who exposes the server publicly overrides it with server.ota_path.
OTA_PATH = "/xiaozhi/ota/"

# What a waiting device appends to its OTA URL to poll. The firmware
# builds the URL itself (`Ota::Activate`), adding a slash first when the
# stored one lacks it, so both spellings arrive here as one path.
ACTIVATE_SEGMENT = "activate"


def spellings(path: str) -> tuple[str, ...]:
    """Every spelling of one path: as written, and without its trailing
    slash, both served by the same handler.

    Not a redirect between the two, which is what this was until a
    factory board met it (2026-08-13): the captive portal saved the
    typed URL without its trailing slash, the device POSTed to the
    slashless spelling, and the firmware does not follow a redirect on
    this request. It rendered "code=307" on its screen and restarted in
    a loop, and nothing reached a handler, so the server had nothing to
    say about it either. A device-facing endpoint cannot spend a round
    trip on a redirect it has no evidence the device will follow, so
    every spelling answers.

    The device-facing routes are what it was written for, and the two
    health probes use it too: their reason is not a factory board but a
    Location header, which a redirect would fill with the probe URL's
    own query string.

    Lives here because both routers below are built from it, and both
    are built here. It used to live beside the handlers instead, so that
    the onboarding module could import it without the import failing;
    that reason is gone with the cycle. One spelling for an `ota_path`
    of "/", which the validator permits and which has no second one: an
    empty route path is not a route.
    """
    return tuple(dict.fromkeys(one for one in (path, path.rstrip("/")) if one))


def build_router(path: str = OTA_PATH) -> APIRouter:
    """The OTA router, served at the configured path. Built per app
    rather than at import time, because the path is configuration and a
    module-level router would have been decided before the config was
    read."""
    router = APIRouter()
    for spelling in spellings(path):
        router.post(spelling)(check_version)
        router.get(spelling)(describe)
    # `path` always ends in a slash (the validator says so), and the
    # firmware appends the segment to whatever it holds, so this is the
    # URL a waiting device polls. The short alias router below registers
    # the same three handlers by reference, through the same helper.
    #
    # This router has a second reason not to redirect, beside the one
    # the helper records: the corrected URL it would put in a Location
    # is the configured `ota_path`, which is this deployment's secret,
    # so a request that had merely guessed at the segment would have
    # been handed the whole of it in a header.
    for spelling in spellings(f"{path}{ACTIVATE_SEGMENT}/"):
        router.post(spelling)(activate)
    return router


def build_alias_router(key: str | None) -> APIRouter:
    """The short-path router, over the same handler objects.

    Built per app rather than at import time, for the reason the OTA
    router is: the key is derived from configuration that has not been
    read when this module is imported.
    """
    router = APIRouter()
    if key is None:
        base = onboarding_path(None)
        for path in spellings(base):
            router.post(path)(check_version)
            router.get(path)(describe)
        # Where a waiting device polls, which is its OTA URL with
        # `activate` after it. Both spellings answer directly, like
        # every other device-facing route: the firmware is not a
        # browser, and a portal that dropped the trailing slash from the
        # endpoint dropped it from this too.
        for path in spellings(f"{base}{ACTIVATE_SEGMENT}/"):
            router.post(path)(activate)
        return router
    for path in spellings(f"{ONBOARDING_MOUNT_PATH}/{{key}}/"):
        router.post(path)(_guarded(key, check_version))
        router.get(path)(_guarded(key, describe))
    # Guarded like everything else on this path: a wrong key must meet
    # the same 404 here as at the endpoint itself, on either spelling.
    for path in spellings(f"{ONBOARDING_MOUNT_PATH}/{{key}}/{ACTIVATE_SEGMENT}/"):
        router.post(path)(_guarded(key, activate))
    return router
