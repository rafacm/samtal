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

Upstream reference: `main/ota.cc` in 78/xiaozhi-esp32 parses this response.
"""

import logging
import time
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from samtal_server import __version__
from samtal_server.auth import DeviceAuth
from samtal_server.build_info import revision
from samtal_server.config import Config
from samtal_server.config.models import normalize_mac
from samtal_server.device.bindings import DeviceAgents, DeviceBindings
from samtal_server.ws import WEBSOCKET_PATH

if TYPE_CHECKING:
    # Names only, and quoted where they are used: the onboarding module
    # imports this one to serve its handlers, so a module-scope import
    # in this direction would not load. Nothing here runs at runtime.
    from samtal_server.onboarding import PendingDevice, PendingDevices

logger = logging.getLogger(__name__)

# The default path, and the one every test and the README use. An operator
# who exposes the server publicly overrides it with server.ota_path.
OTA_PATH = "/xiaozhi/ota/"

# What a waiting device appends to its OTA URL to poll. The firmware
# builds the URL itself (`Ota::Activate`), adding a slash first when the
# stored one lacks it, so both spellings arrive here as one path.
ACTIVATE_SEGMENT = "activate"

# What the version header carries when a body is worth reading. A board
# with no serial number burned, which every consumer board is, announces
# version 1 and sends `{}`; upstream's own server never reads that body.
ACTIVATION_VERSION_HEADER = "activation-version"

# What a device reports when it tells us nothing usable. Any real version is
# greater, so a device that hides its version is never offered an update.
UNKNOWN_VERSION = "0.0.0"

# Said to a device whose Device-Id is not a MAC, and logged in place of
# what it sent.
#
# Deliberately not `normalize_mac`'s own sentence, which quotes the
# value it refused. This endpoint is unauthenticated and reachable by
# anything that finds the path, so the header is attacker-controlled
# text: quoting it puts a chosen string into a response body and into
# every log line and log shipper behind it. The rule the rest of this
# codebase holds for a rejected configuration value holds here for a
# rejected header.
DEVICE_ID_PROBLEM = (
    "the Device-Id header does not hold a MAC address; it has to be six "
    "colon-separated hex pairs, for example aa:bb:cc:dd:ee:ff. What was sent is not "
    "quoted back, since a header that missed the MAC may hold anything at all"
)


def websocket_url_for(config: Config, request: Request) -> str:
    """The websocket URL to hand this device: the configured one, or the
    address it just reached the OTA endpoint on."""
    configured = config.server.websocket_url
    if configured:
        return configured
    scheme = "wss" if request.url.scheme == "https" else "ws"
    return f"{scheme}://{request.url.netloc}{WEBSOCKET_PATH}"


def token_for(
    device_auth: DeviceAuth | None, client_id: str, mac: str, agents: Sequence[str]
) -> str:
    """The token this device gets, which is a token only when there is
    something for it to reach.

    A device the configuration does not resolve to an agent is turned
    away at the websocket anyway, so issuing it a token would only widen
    what an unauthenticated endpoint hands out: the `devices` map plus
    `default_agent` is the allowlist, and this is where it bites.

    The empty string is sent rather than the key omitted, in both the
    no-agent and the auth-disabled case, because the firmware persists
    what it is given: an empty token clears one left in NVS by another
    server, where a missing key would leave it in place.
    """
    if device_auth is None or not agents:
        return ""
    return device_auth.issue(client_id, mac)


def timezone_offset_minutes(config: Config) -> int:
    """Minutes east of UTC for the device clock, from the config or from the
    server's own current offset."""
    configured = config.server.timezone_offset_minutes
    if configured is not None:
        return configured
    offset = datetime.now().astimezone().utcoffset()
    return round(offset.total_seconds() / 60) if offset is not None else 0


def reported_version(payload: dict[str, Any]) -> str:
    """The firmware version the device reports in `application.version`."""
    application = payload.get("application")
    if isinstance(application, dict):
        version = application.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    return UNKNOWN_VERSION


def reported_board(payload: dict[str, Any]) -> str:
    """The board type the device reports, for logging only."""
    board = payload.get("board")
    if isinstance(board, dict):
        board_type = board.get("type")
        if isinstance(board_type, str) and board_type.strip():
            return board_type.strip()
    return "unknown"


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
    # URL a waiting device polls. The short onboarding router registers
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


def spellings(path: str) -> tuple[str, ...]:
    """Every spelling of one device-facing path: as written, and without
    its trailing slash, both served by the same handler.

    Not a redirect between the two, which is what this was until a
    factory board met it (2026-08-13): the captive portal saved the
    typed URL without its trailing slash, the device POSTed to the
    slashless spelling, and the firmware does not follow a redirect on
    this request. It rendered "code=307" on its screen and restarted in
    a loop, and nothing reached a handler, so the server had nothing to
    say about it either. A device-facing endpoint cannot spend a round
    trip on a redirect it has no evidence the device will follow, so
    every spelling answers.

    Lives here rather than beside either router because both of them
    need it and this is the module the other imports. One spelling for
    an `ota_path` of "/", which the validator permits and which has no
    second one: an empty route path is not a route.
    """
    return tuple(dict.fromkeys(one for one in (path, path.rstrip("/")) if one))


async def check_version(request: Request) -> Response:
    config: Config = request.app.state.config

    device_id = request.headers.get("device-id", "").strip()
    client_id = request.headers.get("client-id", "").strip()
    if not device_id:
        return _bad_request("the Device-Id header is required and holds the device MAC")
    if not client_id:
        return _bad_request("the Client-Id header is required and holds the device UUID")

    try:
        mac = normalize_mac(device_id)
    except ValueError:
        # Deliberately not the validator's own sentence, which quotes
        # what it refused: see DEVICE_ID_PROBLEM.
        return _bad_request(DEVICE_ID_PROBLEM)
    # The live view rather than the boot snapshot, and awaited off the
    # event loop: a device bound a moment ago gets its token at this
    # check-in rather than after a restart. What it resolves to is the
    # allowlist this endpoint is stingy by, so the answer decides both
    # the token below and what is said about the device here.
    bindings: DeviceBindings = request.app.state.bindings
    resolution = await bindings.resolve(mac)
    agents = list(resolution.agents)

    payload = await _read_json_object(request)
    version = reported_version(payload)
    board = reported_board(payload)
    # This is the only moment a device ever states its firmware version:
    # the websocket handshake does not carry it. Kept so the session
    # about to open can name it, which a capture manifest needs, since
    # echo cancellation is firmware-side.
    request.app.state.device_facts.record(mac, version, board)
    # No session exists yet, so the structured record carries the device
    # rather than a session id; the websocket events pick the device up
    # from here.
    event = {
        "event": "ota_check",
        "device": mac,
        "client": client_id,
        "board": board,
        "firmware": version,
        "agents": agents,
        # Named in every record rather than only in the one that
        # complains about it, so a query for devices waiting on a
        # restart is one field rather than a log-message search.
        "unloaded": list(resolution.unloaded),
    }

    activation = _activation(request, config, resolution, mac, client_id, board, version)
    if activation is not None:
        # A code is a claim ticket read off a screen, not a credential:
        # it belongs in the log line an operator greps for the board
        # they are holding. A device token never does.
        event["code"] = activation["code"]

    if activation is not None:
        logger.warning(
            "device %s (%s, firmware %s) has no agent and is showing activation code "
            "%s; bind it with: samtal-server config add-device %s <agent>",
            device_id,
            board,
            version,
            activation["code"],
            activation["code"],
            extra=event,
        )
    elif not agents and resolution.unloaded:
        # A different problem from having no agent, and a different
        # answer: the binding is there, this process is what is behind.
        logger.warning(
            "device %s (%s, firmware %s) is bound to agent %s, which this server has "
            "not loaded; restart to load it",
            device_id,
            board,
            version,
            ", ".join(resolution.unloaded),
            extra=event,
        )
    elif not agents:
        logger.warning(
            "device %s (%s, firmware %s) has no agent: bind it under devices "
            "or set default_agent",
            device_id,
            board,
            version,
            extra=event,
        )
    else:
        logger.info(
            "device %s (%s, firmware %s) resolved to agent %s%s",
            device_id,
            board,
            version,
            agents[0],
            f" (also bound to {', '.join(agents[1:])})" if len(agents) > 1 else "",
            extra=event,
        )

    body: dict[str, Any] = {}
    if activation is not None:
        # First, the way upstream's own reply carries it, and present
        # only for a device that has to activate: the firmware treats
        # the key's absence as "no activation is ever required".
        body["activation"] = activation
    body.update(
        {
            "server_time": {
                "timestamp": int(time.time() * 1000),
                "timezone_offset": timezone_offset_minutes(config),
            },
            # No image to offer: echoing the reported version back is how the
            # firmware reads "up to date", since it only updates for a
            # strictly newer one.
            "firmware": {"version": version, "url": ""},
            # Ours, not the firmware's: the one place a device is told
            # what it is about to talk to. The firmware reads the keys it
            # knows and ignores the rest, so this is additive.
            "server": {"name": "samtal-server", "version": __version__, "revision": revision()},
            "websocket": {
                # The empty token stays beside the activation object: a
                # device showing a code has nothing to reach yet, and the
                # firmware persists what it is handed, so an empty string
                # clears one another server left in NVS.
                "url": websocket_url_for(config, request),
                "token": token_for(request.app.state.device_auth, client_id, mac, agents),
                "version": config.server.protocol_version,
            },
        }
    )
    return JSONResponse(body)


def _activation(
    request: Request,
    config: Config,
    resolution: DeviceAgents,
    mac: str,
    client_id: str,
    board: str,
    firmware: str,
) -> dict[str, Any] | None:
    """The `activation` section for this check-in, or None when the
    device is not one to activate.

    The gate is database truth rather than the loaded-agent filter: the
    two disagree exactly when a binding or a default agent was written
    after boot naming an agent this server never loaded, and that state
    must not mint a code for a device an operator has already added.
    Such a device gets no code and no token, and the caller says which
    restart will serve it. The other side of the same coin is upgrade
    compatibility: a deployment with a default agent covers every
    unknown MAC by design, so its devices keep receiving a token and no
    activation object, exactly as before.
    """
    if not config.server.onboarding.enabled:
        return None
    # `agents` are the names the boot snapshot loaded and `unloaded` the
    # ones it did not, so the two being empty together is the database
    # holding neither a binding row for this MAC nor a default agent.
    if resolution.agents or resolution.unloaded:
        return None
    if not resolution.authoritative:
        # An empty answer from the snapshot fallback is not the database
        # saying nothing is bound; it is this server not having been
        # able to find out. Issuing a token off a stale answer only
        # repeats what boot decided, which is why that side keeps the
        # fallback, but minting a code off one would offer a claim
        # ticket for a board an operator has already bound, to whoever
        # is holding the endpoint. The warning naming the failure is
        # already in the log, from the view itself.
        logger.warning(
            "device %s is unbound in the configuration this server started with, but "
            "the database could not be read, so no activation code was issued: this "
            "device may already be bound. Fix the database and it is offered one at "
            "its next check",
            mac,
            extra={"event": "activation_not_offered", "device": mac, "reason": "unreadable"},
        )
        return None
    # Imported here for the reason `describe` imports it below: the
    # onboarding module serves these handlers, so it imports this one.
    from samtal_server.onboarding import activation_object

    offer = request.app.state.pending.observe(mac, client_id, board, firmware)
    if offer.device is None:
        logger.warning(
            "device %s is unbound but was offered no activation code: %s. It is "
            "answered exactly as it was before onboarding existed, with no token; "
            "bind it by its MAC with: samtal-server config bind-device %s <agent>",
            mac,
            offer.refused,
            mac,
            extra={"event": "activation_not_offered", "device": mac, "reason": offer.refused},
        )
        return None
    return activation_object(config.server, offer.device)


async def activate(request: Request) -> Response:
    """Where a waiting device polls, in bursts of ten three seconds
    apart, until it is claimed.

    200 means the MAC resolves to an agent this server has loaded, so a
    200 always means the next OTA check hands the device its real
    configuration. 202 is everything else, which is what upstream's own
    "keep waiting" is: a device still unbound, a device bound to an
    agent this process has not loaded (which flips to 200 at the restart
    that loads it), and a device this server has no pending entry for at
    all, since a restart loses the table and the device's own loop
    fetches a fresh code within a couple of minutes.

    What a version-2 body claims cannot be authenticated. Its HMAC is
    computed with a key burned into the device's eFuses, which the
    vendor's cloud knows from registration and samtal has no copy of, so
    the code ceremony governs both versions (the plan's fourth
    author-approved deviation from issue #40). What is checked while the
    MAC is pending is what can be: the body parses, names an algorithm
    this server knows, and echoes the challenge this server issued. A
    poll answering somebody else's challenge is not evidence of
    anything, so it is refused with the 202 it would have got anyway and
    a log line naming which check failed.
    """
    device_id = request.headers.get("device-id", "").strip()
    if not device_id:
        return _bad_request("the Device-Id header is required and holds the device MAC")
    try:
        mac = normalize_mac(device_id)
    except ValueError:
        # Deliberately not the validator's own sentence, which quotes
        # what it refused: see DEVICE_ID_PROBLEM.
        return _bad_request(DEVICE_ID_PROBLEM)

    bindings: DeviceBindings = request.app.state.bindings
    resolution = await bindings.resolve(mac)
    if resolution.agents:
        logger.info(
            "device %s is activated: its next configuration check hands it a token",
            mac,
            extra={
                "event": "activation_complete",
                "device": mac,
                "agents": list(resolution.agents),
            },
        )
        return Response(status_code=200)

    pending = request.app.state.pending
    waiting = pending.waiting_for(mac)
    if waiting is not None:
        await _version_two(request, pending, waiting)
    logger.debug(
        "device %s is still waiting to be claimed",
        mac,
        extra={
            "event": "activation_pending",
            "device": mac,
            "code": None if waiting is None else waiting.code,
            "unloaded": list(resolution.unloaded),
        },
    )
    return Response(status_code=202)


async def _version_two(
    request: Request, pending: "PendingDevices", waiting: "PendingDevice"
) -> None:
    """The checks a version-2 poll can be held to, and the serial number
    it carries, recorded as an observed fact."""
    from samtal_server.onboarding import ACTIVATION_ALGORITHMS

    if request.headers.get(ACTIVATION_VERSION_HEADER, "").strip() != "2":
        # Version 1 is `{}` and upstream's own server never reads it.
        return
    payload = await _json_object(request)
    record = {"event": "activation_refused", "device": waiting.mac, "code": waiting.code}
    if payload is None:
        logger.warning(
            "device %s sent a version-2 activation body that is not a JSON object; it "
            "is answered as still waiting. Nothing of the body is quoted here",
            waiting.mac,
            extra={**record, "reason": "unreadable_body"},
        )
        return
    algorithm = payload.get("algorithm")
    if not isinstance(algorithm, str) or algorithm not in ACTIVATION_ALGORITHMS:
        logger.warning(
            "device %s sent a version-2 activation body naming an algorithm this "
            "server does not know; it is answered as still waiting. The value is not "
            "quoted here, since it is whatever the request carried",
            waiting.mac,
            extra={**record, "reason": "unknown_algorithm"},
        )
        return
    if payload.get("challenge") != waiting.challenge:
        logger.warning(
            "device %s sent a version-2 activation body answering a challenge this "
            "server did not issue for it; it is answered as still waiting",
            waiting.mac,
            extra={**record, "reason": "challenge_mismatch"},
        )
        return
    serial_number = payload.get("serial_number")
    if isinstance(serial_number, str) and serial_number.strip():
        pending.record_serial(waiting.mac, serial_number)


async def describe(request: Request) -> Response:
    """A human check that the endpoint is reachable and pointed somewhere
    sensible. Devices only ever POST here."""
    # Imported here rather than at module scope: the onboarding module
    # serves these same handlers at its short path, so it imports this
    # one, and the pair would not load in that order.
    from samtal_server.onboarding import portal_url_line

    config: Config = request.app.state.config
    return PlainTextResponse(
        f"samtal-server {__version__} (revision {revision()}) OTA endpoint.\n"
        f"Devices are sent to {websocket_url_for(config, request)} "
        f"(protocol version {config.server.protocol_version}).\n"
        # The path this was reached on, so the line is the URL that
        # works rather than the one this server would recommend.
        f"{portal_url_line(config.server, request.url.path)}\n"
    )


def _bad_request(message: str) -> JSONResponse:
    """One refusal, said once to the caller and once to the log.

    Every caller passes a fixed sentence: nothing a request carried is
    interpolated into either channel, which is what keeps a header this
    endpoint could not read out of the log a deployment ships.
    """
    logger.warning(
        "rejected OTA request: %s", message, extra={"event": "ota_request_rejected"}
    )
    return JSONResponse({"error": message}, status_code=400)


async def _read_json_object(request: Request) -> dict[str, Any]:
    """The device's system info, or an empty mapping. Nothing in the reply
    depends on it beyond the firmware version and logging, so a device that
    sends a malformed body is still answered rather than turned away."""
    payload = await _json_object(request)
    return {} if payload is None else payload


async def _json_object(request: Request) -> dict[str, Any] | None:
    """The request's body as a JSON object, or None when it is not one.

    None and the empty object are kept apart here, unlike above: the
    activation checks have to tell a body that could not be read from
    one that was read and said nothing.
    """
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
