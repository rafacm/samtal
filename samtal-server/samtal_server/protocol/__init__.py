"""The xiaozhi device websocket wire protocol, isolated from everything else.

JSON control messages live in `messages`, the binary audio framing in
`framing`, and the JSON-RPC payloads of the device's own MCP channel in
`mcp`. Upstream reference: `docs/websocket.md` and `docs/mcp-protocol.md`
in 78/xiaozhi-esp32.
"""

from samtal_server.protocol.framing import FramingError
from samtal_server.protocol.mcp import McpProtocolError
from samtal_server.protocol.messages import ProtocolError

__all__ = ["FramingError", "McpProtocolError", "ProtocolError"]
