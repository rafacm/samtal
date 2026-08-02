"""The xiaozhi device websocket wire protocol, isolated from everything else.

JSON control messages live in `messages`, the binary audio framing in
`framing`. Upstream reference: `docs/websocket.md` in 78/xiaozhi-esp32.
"""

from samtal_server.protocol.framing import FramingError
from samtal_server.protocol.messages import ProtocolError

__all__ = ["FramingError", "ProtocolError"]
