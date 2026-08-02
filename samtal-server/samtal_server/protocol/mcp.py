"""The device's MCP channel: JSON-RPC 2.0 inside `mcp` messages.

The roles are the reverse of the usual arrangement: the device is the
MCP server and samtal-server the client, because the tools being
discovered are the board's own (its speaker, its screen, its battery).
Every payload here is standard JSON-RPC 2.0, carried in the `payload`
field of an `mcp` protocol message.

This module is the wire layer only: it builds request payloads and
reads response payloads. Who sends them, when, and what happens to the
tools lives in `samtal_server.tools.device`.

Upstream reference: `docs/mcp-protocol.md` in 78/xiaozhi-esp32.
"""

import json
from dataclasses import dataclass
from typing import Any

JSONRPC_VERSION = "2.0"

# The MCP revision the device firmware announces back; sent so the two
# sides agree rather than guess.
PROTOCOL_VERSION = "2024-11-05"


class McpProtocolError(ValueError):
    """A payload that cannot be read as a JSON-RPC response."""


def envelope(session_id: str, payload: dict[str, Any]) -> str:
    """One JSON-RPC payload wrapped in the `mcp` protocol message the
    websocket carries."""
    return json.dumps({"session_id": session_id, "type": "mcp", "payload": payload})


def initialize_request(request_id: int, client_name: str, client_version: str) -> dict[str, Any]:
    """The handshake that opens the device's MCP session.

    The empty `vision` stanza is deliberate. samtal has no image
    endpoint (a v1 non-goal), and the real firmware treats vision as
    optional, but xiaozhi-sdk reads `capabilities.vision.url` and
    `.token` unconditionally and raises a KeyError without them, which
    would strand every simulator conversation before tool discovery.
    Empty strings say "no vision endpoint" to both."""
    return {
        "jsonrpc": JSONRPC_VERSION,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"vision": {"url": "", "token": ""}},
            "clientInfo": {"name": client_name, "version": client_version},
        },
        "id": request_id,
    }


def initialized_notification() -> dict[str, Any]:
    """The notification the MCP specification requires after a
    successful `initialize`. Notifications carry no id and get no
    reply."""
    return {"jsonrpc": JSONRPC_VERSION, "method": "notifications/initialized"}


def tools_list_request(request_id: int, cursor: str = "") -> dict[str, Any]:
    """One page of the device's tool list. `withUserTools` stays false:
    those are the tools a companion app offers a human directly, not
    ones an assistant should be able to reach on its own."""
    return {
        "jsonrpc": JSONRPC_VERSION,
        "method": "tools/list",
        "params": {"cursor": cursor, "withUserTools": False},
        "id": request_id,
    }


def tools_call_request(
    request_id: int, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Invoke one device tool, under the name the device listed it as."""
    return {
        "jsonrpc": JSONRPC_VERSION,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
        "id": request_id,
    }


@dataclass(frozen=True)
class Response:
    """A JSON-RPC response: an id, and exactly one of result or error."""

    id: int
    result: dict[str, Any] | None = None
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None


@dataclass(frozen=True)
class DeviceTool:
    """One tool as the device lists it, under its own name."""

    name: str
    description: str
    input_schema: dict[str, Any]


def parse_response(payload: dict[str, Any]) -> Response | None:
    """Read an incoming payload as a response to one of our requests.

    Returns None for anything that is not one: a device-initiated
    notification (no id) or a request from the device. Malformed
    responses raise, because a response we cannot match leaves a
    request waiting for a reply that will never come."""
    if "method" in payload:
        return None
    raw_id = payload.get("id")
    if not isinstance(raw_id, int) or isinstance(raw_id, bool):
        raise McpProtocolError(f"response without a numeric id: {payload!r}")

    error = payload.get("error")
    if error is not None:
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message") or "no message"
            return Response(id=raw_id, error=f"{message} (code {code})")
        return Response(id=raw_id, error=str(error))

    result = payload.get("result")
    if not isinstance(result, dict):
        raise McpProtocolError(f"response {raw_id} carries neither result nor error")
    return Response(id=raw_id, result=result)


def parse_tools_page(result: dict[str, Any]) -> tuple[list[DeviceTool], str]:
    """One `tools/list` page: the tools on it, and the cursor for the
    next page (empty when this was the last one). Entries that are not
    usable tool descriptions are skipped rather than failing the whole
    page, since one odd tool should not cost the device all of them."""
    tools: list[DeviceTool] = []
    for entry in result.get("tools") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        schema = entry.get("inputSchema")
        tools.append(
            DeviceTool(
                name=name,
                description=str(entry.get("description") or ""),
                input_schema=schema if isinstance(schema, dict) else {"type": "object"},
            )
        )
    cursor = result.get("nextCursor")
    return tools, cursor if isinstance(cursor, str) else ""


def parse_tool_result(result: dict[str, Any]) -> tuple[str, bool]:
    """A `tools/call` result as text plus its error flag. Content items
    the protocol allows but a spoken assistant cannot use (images, audio)
    are named rather than dropped silently, so the model can say what it
    got back instead of appearing to ignore it."""
    parts: list[str] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        else:
            parts.append(f"[unsupported {item.get('type', 'unknown')} content]")
    return "\n".join(parts), bool(result.get("isError"))
