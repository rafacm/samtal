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
    """A binary frame that does not match the negotiated framing.

    Its message is one of the fixed sentences below and never anything
    else. The session logs this exception verbatim at warning level
    (`device/session.py`), which is a retained surface, and the no-leak
    model keeps that surface free of far-side bytes and exception prose
    (`docs/architecture/observability-surfaces.md`). A header field is
    whatever the far side wrote in it, and a length compared against one
    is that value restated, so neither is interpolated here, integer
    lengths included: which category a frame failed is the whole of what
    a diagnosis is owed, and the drop is counted as `framing_error`
    beside the line.
    """


# Everything a `FramingError` may say, one fixed sentence per category.
# Named here rather than written at each raise so that a suite pinning
# what reaches a log record reads the sentence from this module instead
# of keeping a second copy of it, which is the only way the two cannot
# come to disagree. The protocol version in a sentence is a literal at
# the raise it belongs to, not a value read off the frame.
SHORT_V2_FRAME = "version 2 frame is shorter than its header"
SHORT_V3_FRAME = "version 3 frame is shorter than its header"
V2_SIZE_MISMATCH = "version 2 frame carries a different number of payload bytes than it announces"
V3_SIZE_MISMATCH = "version 3 frame carries a different number of payload bytes than it announces"
UNSUPPORTED_VERSION = "unsupported binary protocol version"
UNFRAMED_STREAM = "version 1 carries no frame boundaries, so a run of them cannot be walked"

# Which header a stream of frames is walked by. Both layouts end in
# `payload_size`, which is the whole of what walking one takes, and both
# are declared above: a reader that unpacked `>HHIII` for itself would be
# the second home for a struct this module exists to own.
_STREAM_HEADERS = {2: _V2_HEADER, 3: _V3_HEADER}

_SHORT_FRAME = {2: SHORT_V2_FRAME, 3: SHORT_V3_FRAME}

_SIZE_MISMATCH = {2: V2_SIZE_MISMATCH, 3: V3_SIZE_MISMATCH}


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
    raise FramingError(UNSUPPORTED_VERSION)


def unwrap(version: int, data: bytes) -> Frame:
    """Unwrap a received binary frame under the given protocol version."""
    if version == 1:
        return Frame(PAYLOAD_OPUS, bytes(data))
    if version == 2:
        if len(data) < _V2_HEADER.size:
            raise FramingError(SHORT_V2_FRAME)
        # The timestamp a stock firmware stamps its frames with is
        # parsed and dropped: the header has to be read past to reach
        # the payload, and nothing above this line asks what it said.
        _, payload_type, _, _, size = _V2_HEADER.unpack_from(data)
        payload = data[_V2_HEADER.size :]
        if len(payload) != size:
            raise FramingError(V2_SIZE_MISMATCH)
        return Frame(payload_type, payload)
    if version == 3:
        if len(data) < _V3_HEADER.size:
            raise FramingError(SHORT_V3_FRAME)
        payload_type, _, size = _V3_HEADER.unpack_from(data)
        payload = data[_V3_HEADER.size :]
        if len(payload) != size:
            raise FramingError(V3_SIZE_MISMATCH)
        return Frame(payload_type, payload)
    raise FramingError(UNSUPPORTED_VERSION)


def frames(version: int, data: bytes) -> tuple[Frame, ...]:
    """Every frame in a run of them, walked by what their headers
    announce.

    A websocket delivers one frame per message, so nothing on the wire
    needs this. What does is a run of frames stored as a file, which is
    how `vinga simulator` carries the packets it sends: a file of bare
    Opus packets has no boundaries at all, because that is exactly what
    bare means, and inventing a container for them would put a second
    length format in a repository whose whole posture is that the wire
    has one home.

    So a stored utterance is a run of version 2 frames, and this is what
    reads one. Version 1 is refused rather than guessed at, for the
    reason above: it is the version with nothing to walk by. Each frame
    is handed to `unwrap`, so what a payload has to satisfy is checked in
    the one place that checks it for the wire.
    """
    header = _STREAM_HEADERS.get(version)
    if header is None:
        raise FramingError(UNFRAMED_STREAM if version == 1 else UNSUPPORTED_VERSION)
    walked: list[Frame] = []
    at = 0
    while at < len(data):
        if len(data) - at < header.size:
            raise FramingError(_SHORT_FRAME[version])
        # The last field of both layouts, which is the one fact a walk
        # needs and the reason this lives beside the structs.
        size = header.unpack_from(data, at)[-1]
        end = at + header.size + size
        if end > len(data):
            raise FramingError(_SIZE_MISMATCH[version])
        walked.append(unwrap(version, data[at:end]))
        at = end
    return tuple(walked)
