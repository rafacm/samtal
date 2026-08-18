"""The device OTA/config endpoint.

A device running stock xiaozhi firmware knows exactly one thing about its
backend: the OTA URL held in NVS. On every boot it POSTs its system info
there and takes the rest of its configuration from the reply, persisting it
to NVS: the websocket URL with its token and protocol version, the wall
clock, and whether a firmware update is waiting.

samtal-server serves no firmware images, so the firmware section always
answers "up to date" by echoing back the version the device reported.

A device the database has nothing to say about is answered with an
`activation` section instead of a token: a six-digit code it shows on
its screen and speaks, which an operator reads off the board and binds
with one command. The device polls `/activate` every three seconds
while it waits, so a bind takes effect with no power cycle and no
button press. A device that is bound, or that a default agent already
covers, is never asked to activate, which is what keeps every existing
deployment answering exactly what it answered before.

This endpoint is the token issuer, so it cannot itself require a token.
What protects it instead is stinginess and a configurable path: a token
is issued only to a device the configuration resolves to an agent this
server has loaded, and an operator exposing the server publicly hides
the endpoint behind a long random path segment.

Which agents a device resolves to is the one question here that is not
answered from the boot snapshot. `DeviceBindings` reads it from the
database at every check-in, so a board bound while the server runs is
handed its token at the next one it makes, seconds later, rather than
after a restart.

A file each, under this one:

- `reply` is the check-in: what a board is told about itself, its
  clock, its firmware and where to talk, and the wrapper that narrates
  the activation decision `onboarding.unbound` makes.
- `poll` is the request a waiting board repeats until it is claimed.
- `router` mounts those three handlers, at the configured OTA path and
  again at the short onboarding alias, by reference and never by copy.

This `__init__` IS the module `samtal_server.ota`, and the two rules
that follow from that are the ones the onboarding package lives by.
EVENTS go through the emitter built here, which a submodule takes with
`from . import events` and reaches no other way, so the channel is this
package's name by construction and the `logger` field of every record is
what it always was: `samtal_server.ota`, which twelve pins assert
literally. And submodules import their siblings directly and take
nothing else from here, so only this file aggregates.

Upstream reference: `main/ota.cc` in 78/xiaozhi-esp32 parses this response.
"""

from samtal_server.events import ServerEvents

events = ServerEvents(__name__)

# And the submodules, imported after the emitter rather than above it:
# each of them reads it out of this module, so it has to be here before
# the first of them is read.
#
# `websocket_url_for` is among them because it is not defined in this
# package any more: it is one of the two modes of
# `onboarding.origin.assemble` (issue #143), and the module that answers
# "what address does the outside world use" answers this too. Named here
# because it was importable from `samtal_server.ota` before the split
# and nothing about the reply body changed.
from samtal_server.onboarding.origin import websocket_url_for  # noqa: E402

from .poll import ACTIVATION_VERSION_HEADER, _version_two, activate  # noqa: E402
from .reply import (  # noqa: E402
    DEVICE_ID_PROBLEM,
    UNKNOWN_VERSION,
    _activation,
    _bad_request,
    _json_object,
    _read_json_object,
    check_version,
    describe,
    reported_board,
    reported_version,
    timezone_offset_minutes,
    token_for,
)
from .router import (  # noqa: E402
    ACTIVATE_SEGMENT,
    OTA_PATH,
    build_alias_router,
    build_router,
    spellings,
)

# What this package answers to, gathered so that importing a name from
# `samtal_server.ota` means what it meant when this was one file. The
# underscored names are here for that reason and not as an invitation.
__all__ = [
    "ACTIVATE_SEGMENT",
    "ACTIVATION_VERSION_HEADER",
    "DEVICE_ID_PROBLEM",
    "OTA_PATH",
    "UNKNOWN_VERSION",
    "_activation",
    "_bad_request",
    "_json_object",
    "_read_json_object",
    "_version_two",
    "activate",
    "build_alias_router",
    "build_router",
    "check_version",
    "describe",
    "events",
    "reported_board",
    "reported_version",
    "spellings",
    "timezone_offset_minutes",
    "token_for",
    "websocket_url_for",
]
