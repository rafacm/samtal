"""The device websocket endpoint.

The path devices are sent to by the OTA reply; each accepted upgrade
becomes one `Session`, served with the providers built at startup.
"""

from fastapi import APIRouter
from starlette.websockets import WebSocket

from samtal_server.session import Session

WEBSOCKET_PATH = "/xiaozhi/v1/"

router = APIRouter()


@router.websocket(WEBSOCKET_PATH)
async def conversation(websocket: WebSocket) -> None:
    state = websocket.app.state
    await Session(websocket, state.config, state.agent_providers).run()
