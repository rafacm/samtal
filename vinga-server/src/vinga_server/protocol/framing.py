"""Binary framing of the audio carried in websocket binary frames.

Version 1 sends bare Opus packets: the websocket layer already separates
text from binary. Versions 2 and 3 prefix a packed header in network byte
order, mirroring `BinaryProtocol2`/`BinaryProtocol3` in
`main/protocols/websocket_protocol.cc` of 78/xiaozhi-esp32.
"""

import struct
from dataclasses import dataclass

# Payload types carried by the version 2 and 3 headers.
PAYLOAD_OPUS = 0
PAYLOAD_JSON = 1

SUPPORTED_VERSIONS = (1, 2, 3)

# version, type, reserved, timestamp (ms), payload_size
_V2_HEADER = struct.Struct(">HHIII")
# type, reserved, payload_size
_V3_HEADER = struct.Struct(">BBH")


class FramingError(ValueError):
    """A binary frame that does not match the negotiated framing."""


@dataclass(frozen=True)
class Frame:
    """One unwrapped binary frame.

    No timestamp: the version 2 header carries a millisecond field and
    this server reads nothing from it, so an unwrapped frame does not
    offer one. The field itself stays on the wire, where the firmware
    struct puts it."""

    payload_type: int
    payload: bytes


def wrap(
    version: int,
    payload: bytes,
    *,
    payload_type: int = PAYLOAD_OPUS,
) -> bytes:
    """Wrap a payload for sending under the given protocol version."""
    if version == 1:
        return payload
    if version == 2:
        # A literal zero in the header's timestamp field, which is what
        # every frame this server has ever sent carried. The struct is
        # unchanged, so the bytes are.
        header = _V2_HEADER.pack(version, payload_type, 0, 0, len(payload))
        return header + payload
    if version == 3:
        return _V3_HEADER.pack(payload_type, 0, len(payload)) + payload
    raise FramingError(f"unsupported binary protocol version {version}")


def unwrap(version: int, data: bytes) -> Frame:
    """Unwrap a received binary frame under the given protocol version."""
    if version == 1:
        return Frame(PAYLOAD_OPUS, bytes(data))
    if version == 2:
        if len(data) < _V2_HEADER.size:
            raise FramingError(f"version 2 frame of {len(data)} bytes is shorter than its header")
        # The timestamp a stock firmware stamps its frames with is
        # parsed and dropped: the header has to be read past to reach
        # the payload, and nothing above this line asks what it said.
        _, payload_type, _, _, size = _V2_HEADER.unpack_from(data)
        payload = data[_V2_HEADER.size :]
        if len(payload) != size:
            raise FramingError(
                f"version 2 frame announces {size} payload bytes but carries {len(payload)}"
            )
        return Frame(payload_type, payload)
    if version == 3:
        if len(data) < _V3_HEADER.size:
            raise FramingError(f"version 3 frame of {len(data)} bytes is shorter than its header")
        payload_type, _, size = _V3_HEADER.unpack_from(data)
        payload = data[_V3_HEADER.size :]
        if len(payload) != size:
            raise FramingError(
                f"version 3 frame announces {size} payload bytes but carries {len(payload)}"
            )
        return Frame(payload_type, payload)
    raise FramingError(f"unsupported binary protocol version {version}")
