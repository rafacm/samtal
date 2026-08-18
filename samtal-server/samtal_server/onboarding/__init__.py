"""Getting a stock board onto this server, and what it is answered
until an operator claims it.

Two halves, and they were one file until issue #143. One is the short
onboarding path, `/x/<key>/`, an alias of the OTA endpoint that a
person can type into a captive portal without a mistake, and the 404 a
wrong key meets. The other is the activation ceremony that same URL
leads to: the table of devices waiting to be claimed, the six-digit
code each of them is showing, and the `activation` section of the OTA
reply that puts the code on a screen.

A file each, under this one:

- `keys` derives the key, says what path it is served on, guards the
  handlers behind it and reports a miss without quoting either key.
- `origin` answers what address the outside world reaches this server
  on, and logs the startup banner that says it.
- `pending` is the table of waiting devices, its bounds and its claim
  lifecycle. It imports nothing device-facing, which is what lets the
  configuration API and `samtal-server config` read it.
- `unbound` is the one home of the question "what does a device with no
  agent get", plus the reply section that answers it.

The key derivation and the origin resolution take the file half of the
configuration (`server`) rather than the composed whole, because they
read nothing else and because `samtal-server config ota-url` has
nothing else: it prints the URL to type before any server runs, from
the same file and the same environment, by calling these functions. A
second implementation of the derivation is the one thing that could
send an operator to a URL this server does not serve.

This `__init__` IS the module `samtal_server.onboarding`, and three
rules follow from that. EVENTS go through the emitter built here, which
a submodule takes with `from . import events` and reaches no other way,
so the channel is this package's name by construction and the `logger`
field of every record is what it always was. The TUNABLE BOUNDS are
defined here, above the submodule imports, and read through this module
at the moment a decision needs one: the suites monkeypatch them on the
name they have always lived on, and a name imported into a submodule
would be a snapshot no assignment reaches. And submodules import their
siblings directly and take nothing else from here, so only this file
aggregates.
"""

from fastapi import APIRouter

from samtal_server import ota
from samtal_server.config.models import ONBOARDING_MOUNT_PATH
from samtal_server.events import ServerEvents

events = ServerEvents(__name__)

# The activation ceremony's parameters. Constants rather than
# configuration: nobody has field evidence to tune them by, and a knob
# nobody can reason about is schema noise. If the field says otherwise
# they graduate to configuration then.
#
# They are also the bounds the suites move to test what happens at one,
# which is why they are defined HERE, before the submodule imports
# below, and read through this module rather than copied into the
# modules that decide by them.

# How many digits a code has. Six, because the firmware speaks it digit
# by digit off a compiled clip per digit and shows it on a small screen.
CODE_DIGITS = 6

_CODE_CEILING = 10**CODE_DIGITS

# How long one code stands. The device re-checks OTA every half minute
# to two minutes while it waits, so an expired code is replaced on the
# screen long before an operator could type a stale one: what they read
# is always what is current.
CODE_TTL_S = 600.0

# How many devices may be waiting at once. At the cap a new one gets
# exactly today's behavior, an empty token and no activation object,
# and a warning names the cap.
PENDING_CAPACITY = 128

# How many codes may be minted in any window of MINT_WINDOW_S, counting
# first issues and re-issues but not re-displays of a live code. The cap
# above bounds the standing table; this bounds the rate, so an outsider
# who fills the table and waits for it to expire is bounded per window
# rather than only per snapshot.
MINT_BUDGET = 30

MINT_WINDOW_S = 600.0

# How long a code has left after a claim of it failed, at least. A
# minute, because that is what makes the refusal's own advice ("run the
# command again in a moment") true for a code whose deadline the failed
# attempt stepped over. It only ever extends, so a code that had longer
# keeps what it had.
RELEASED_GRACE_S = 60.0

# And the submodules, imported after the emitter and the bounds rather
# than above them, which is the whole of what the markers below are
# for: each of them reads one or the other out of this module, so both
# have to be here before the first of them is read.
from .keys import (  # noqa: E402
    _TYPO_ATTEMPT_RE,
    KEY_LABEL,
    KEY_LENGTH,
    TYPO_ATTEMPT_LENGTH,
    Handler,
    _guarded,
    _log_mismatch,
    derive_key,
    onboarding_key,
    onboarding_path,
)
from .origin import (  # noqa: E402
    Origin,
    _bracketed,
    _origin_of,
    log_banner,
    portal_url_line,
    public_origin,
)
from .pending import (  # noqa: E402
    BUDGET_SPENT,
    CAPACITY_REACHED,
    FACT_LENGTH,
    Claim,
    Offer,
    PendingDevice,
    PendingDevices,
    _drawn,
    _fact,
)
from .unbound import (  # noqa: E402
    ACTIVATION_ALGORITHMS,
    ACTIVATION_TIMEOUT_MS,
    Unbound,
    activation_for,
    activation_object,
)

# What this package answers to, gathered so that importing a name from
# `samtal_server.onboarding` means what it meant when this was one file.
# The underscored names are here for that reason and not as an
# invitation: the suites and the neighbouring modules read some of them
# off this module.
#
# `build_router` is deliberately absent: it is a construction-only
# factory this file still defines, and M2 of issue #143 retires it.
__all__ = [
    "ACTIVATION_ALGORITHMS",
    "ACTIVATION_TIMEOUT_MS",
    "BUDGET_SPENT",
    "CAPACITY_REACHED",
    "CODE_DIGITS",
    "CODE_TTL_S",
    "FACT_LENGTH",
    "KEY_LABEL",
    "KEY_LENGTH",
    "MINT_BUDGET",
    "MINT_WINDOW_S",
    "PENDING_CAPACITY",
    "RELEASED_GRACE_S",
    "TYPO_ATTEMPT_LENGTH",
    "Claim",
    "Handler",
    "Offer",
    "Origin",
    "PendingDevice",
    "PendingDevices",
    "Unbound",
    "_CODE_CEILING",
    "_TYPO_ATTEMPT_RE",
    "_bracketed",
    "_drawn",
    "_fact",
    "_guarded",
    "_log_mismatch",
    "_origin_of",
    "activation_for",
    "activation_object",
    "derive_key",
    "events",
    "log_banner",
    "onboarding_key",
    "onboarding_path",
    "portal_url_line",
    "public_origin",
]


def build_router(key: str | None) -> APIRouter:
    """The short-path router, delegating to the OTA handlers.

    Built per app rather than at import time, for the reason the OTA
    router is: the key is derived from configuration that has not been
    read when this module is imported.

    Temporary, and the one edge from this package to `ota` that is left
    (issue #143). A router over ota's handlers belongs beside them, with
    the guard as an ordinary dependency; it stays here only until M2
    moves it to `ota.router.build_alias_router` and `app.py` calls that
    instead, at which point this function and the import above it go.
    """
    router = APIRouter()
    if key is None:
        base = onboarding_path(None)
        for path in ota.spellings(base):
            router.post(path)(ota.check_version)
            router.get(path)(ota.describe)
        # Where a waiting device polls, which is its OTA URL with
        # `activate` after it. Both spellings answer directly, like
        # every other device-facing route: the firmware is not a
        # browser, and a portal that dropped the trailing slash from the
        # endpoint dropped it from this too.
        for path in ota.spellings(f"{base}{ota.ACTIVATE_SEGMENT}/"):
            router.post(path)(ota.activate)
        return router
    for path in ota.spellings(f"{ONBOARDING_MOUNT_PATH}/{{key}}/"):
        router.post(path)(_guarded(key, ota.check_version))
        router.get(path)(_guarded(key, ota.describe))
    # Guarded like everything else on this path: a wrong key must meet
    # the same 404 here as at the endpoint itself, on either spelling.
    for path in ota.spellings(f"{ONBOARDING_MOUNT_PATH}/{{key}}/{ota.ACTIVATE_SEGMENT}/"):
        router.post(path)(_guarded(key, ota.activate))
    return router
