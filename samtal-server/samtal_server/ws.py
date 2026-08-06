"""The device websocket endpoint.

The path devices are sent to by the OTA reply; each accepted upgrade
becomes one `Session`, served with the providers built at startup.

Before any of that, the handshake is gated on the device token the OTA
reply issued. A connection that fails the gate is closed without ever
being accepted, which uvicorn answers as an HTTP 403 on the upgrade
rather than as a websocket close: that is what upstream does, and what
stock firmware handles by retrying and refreshing its token at its next
OTA check. The rejections that come after the accept (a malformed MAC,
a device bound to no agent) stay where M5 put them, inside the session,
because by then the device has proved who it is and the useful thing to
tell it is what is wrong with its configuration.
"""

import logging

from fastapi import APIRouter
from starlette.websockets import WebSocket

from samtal_server.auth import DeviceAuth
from samtal_server.config.models import normalize_mac
from samtal_server.session import Session

logger = logging.getLogger(__name__)

WEBSOCKET_PATH = "/xiaozhi/v1/"

# The scheme both the firmware and xiaozhi-sdk send the token under.
BEARER = "bearer "

router = APIRouter()


def bearer_token(header: str) -> str | None:
    """The token out of an Authorization header, or None when the header
    is missing or is not a bearer one. An empty token, which is what the
    firmware sends when its NVS holds none, is not a token."""
    value = header.strip()
    if not value.lower().startswith(BEARER):
        return None
    return value[len(BEARER) :].strip() or None


def signed_device_id(device_id: str) -> str:
    """The form of the device id a token was signed for: the normalized
    MAC, since that is what the OTA endpoint signs. Anything that is not
    a MAC is passed through as it arrived, so that a device with a
    malformed Device-Id is turned away by the session with an answer
    about its Device-Id rather than silently by the token check."""
    try:
        return normalize_mac(device_id)
    except ValueError:
        return device_id.strip().lower()


def refusal_reason(device_auth: DeviceAuth | None, websocket: WebSocket) -> str | None:
    """Why this handshake is refused, or None when it may proceed.

    The identity a token is checked against is the pair the OTA reply
    signed for, read from the headers the firmware sets: `Device-Id`
    holds the MAC and `Client-Id` the device UUID.
    """
    if device_auth is None:
        return None
    token = bearer_token(websocket.headers.get("authorization", ""))
    if token is None:
        return "no_token"
    device_id = signed_device_id(websocket.headers.get("device-id", ""))
    client_id = websocket.headers.get("client-id", "").strip()
    if not device_auth.verify(token, client_id, device_id):
        return "bad_token"
    return None


@router.websocket(WEBSOCKET_PATH)
async def conversation(websocket: WebSocket) -> None:
    state = websocket.app.state

    device_id = websocket.headers.get("device-id", "").strip().lower()

    refusal = refusal_reason(state.device_auth, websocket)
    if refusal is not None:
        logger.warning(
            "refused a websocket handshake from %s: %s",
            device_id or "an unidentified device",
            refusal,
            extra={"event": "auth_rejected", "device": device_id or None, "reason": refusal},
        )
        # Closed before the accept, so the upgrade is answered 403 and no
        # websocket is ever established.
        await websocket.close()
        return

    session = Session(
        websocket,
        state.config,
        state.agent_providers,
        state.mcp_servers,
        state.memory,
        state.capture,
        state.device_facts,
    )
    # Capacity is checked after the token, so a full server still answers a
    # bad token with a refusal about the token.
    if not state.sessions.try_add(session):
        logger.warning(
            "refused a websocket handshake from %s: the server is at capacity",
            device_id or "an unidentified device",
            extra={
                "event": "session_rejected",
                "device": device_id or None,
                "session": session.session_id,
                "reason": "capacity",
            },
        )
        await websocket.close()
        return

    try:
        await session.run()
    finally:
        state.sessions.remove(session)
