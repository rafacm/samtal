"""The device OTA/config endpoint.

A device running stock xiaozhi firmware knows exactly one thing about its
backend: the OTA URL held in NVS. On every boot it POSTs its system info
there and takes the rest of its configuration from the reply, persisting it
to NVS: the websocket URL with its token and protocol version, the wall
clock, and whether a firmware update is waiting.

samtal-server serves no firmware images, so the firmware section always
answers "up to date" by echoing back the version the device reported. The
reply also never carries an `activation` section, which is what keeps
devices from ever being asked to activate.

This endpoint is the token issuer, so it cannot itself require a token.
What protects it instead is stinginess and a configurable path: a token
is issued only to a device the configuration resolves to an agent, and
an operator exposing the server publicly hides the endpoint behind a
long random path segment.

Upstream reference: `main/ota.cc` in 78/xiaozhi-esp32 parses this response.
"""

import logging
import time
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from samtal_server import __version__
from samtal_server.auth import DeviceAuth
from samtal_server.build_info import revision
from samtal_server.config import Config
from samtal_server.config.models import normalize_mac
from samtal_server.ws import WEBSOCKET_PATH

logger = logging.getLogger(__name__)

# The default path, and the one every test and the README use. An operator
# who exposes the server publicly overrides it with server.ota_path.
OTA_PATH = "/xiaozhi/ota/"

# What a device reports when it tells us nothing usable. Any real version is
# greater, so a device that hides its version is never offered an update.
UNKNOWN_VERSION = "0.0.0"


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
    router.post(path)(check_version)
    router.get(path)(describe)
    return router


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
    except ValueError as exc:
        return _bad_request(f"Device-Id header: {exc}")
    agents = config.agents_for_device(mac)

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
    }

    if not agents:
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

    return JSONResponse(
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
                "url": websocket_url_for(config, request),
                "token": token_for(request.app.state.device_auth, client_id, mac, agents),
                "version": config.server.protocol_version,
            },
        }
    )


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
        f"{portal_url_line(config, request.url.path)}\n"
    )


def _bad_request(message: str) -> JSONResponse:
    logger.warning("rejected OTA request: %s", message)
    return JSONResponse({"error": message}, status_code=400)


async def _read_json_object(request: Request) -> dict[str, Any]:
    """The device's system info, or an empty mapping. Nothing in the reply
    depends on it beyond the firmware version and logging, so a device that
    sends a malformed body is still answered rather than turned away."""
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
