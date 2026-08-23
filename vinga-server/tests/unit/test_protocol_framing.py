"""The binary audio framing, byte-compatible with the firmware structs.

The expected layouts mirror `BinaryProtocol2`/`BinaryProtocol3` in
`main/protocols/websocket_protocol.cc` of 78/xiaozhi-esp32: packed
structs, network byte order.
"""

import pytest

from vinga_server.protocol.framing import (
    PAYLOAD_JSON,
    PAYLOAD_OPUS,
    Frame,
    FramingError,
    unwrap,
    wrap,
)

OPUS_PAYLOAD = b"\xfc\xff\xfeopus-packet"


def test_version_1_is_the_bare_payload_both_ways() -> None:
    assert wrap(1, OPUS_PAYLOAD) == OPUS_PAYLOAD
    assert unwrap(1, OPUS_PAYLOAD) == Frame(PAYLOAD_OPUS, OPUS_PAYLOAD)


def test_version_2_lays_out_the_firmware_struct() -> None:
    """Including the timestamp field, which this server does not fill:
    the header is the firmware's twenty bytes whatever we put in it, and
    an outgoing frame carries a literal zero there, which is what every
    frame this server has ever sent carried."""
    wrapped = wrap(2, OPUS_PAYLOAD)
    header = (
        (2).to_bytes(2, "big")
        + PAYLOAD_OPUS.to_bytes(2, "big")
        + (0).to_bytes(4, "big")
        + (0).to_bytes(4, "big")
        + len(OPUS_PAYLOAD).to_bytes(4, "big")
    )
    assert wrapped == header + OPUS_PAYLOAD


def test_version_2_round_trips() -> None:
    assert unwrap(2, wrap(2, OPUS_PAYLOAD)) == Frame(PAYLOAD_OPUS, OPUS_PAYLOAD)


def test_a_stock_firmwares_timestamp_is_read_past_and_dropped() -> None:
    """The compatibility half, which a round trip cannot reach: what
    this server wraps carries a zero there, and a board running stock
    firmware stamps a real millisecond count. The frame is built by
    hand for that reason, and unwrapping it must find the payload
    rather than trip over a field nobody reads."""
    incoming = (
        (2).to_bytes(2, "big")
        + PAYLOAD_OPUS.to_bytes(2, "big")
        + (0).to_bytes(4, "big")
        + (0x01020304).to_bytes(4, "big")
        + len(OPUS_PAYLOAD).to_bytes(4, "big")
    ) + OPUS_PAYLOAD

    assert unwrap(2, incoming) == Frame(PAYLOAD_OPUS, OPUS_PAYLOAD)


def test_version_3_lays_out_the_firmware_struct() -> None:
    wrapped = wrap(3, OPUS_PAYLOAD)
    header = bytes([PAYLOAD_OPUS, 0]) + len(OPUS_PAYLOAD).to_bytes(2, "big")
    assert wrapped == header + OPUS_PAYLOAD


def test_version_3_round_trips() -> None:
    assert unwrap(3, wrap(3, OPUS_PAYLOAD)) == Frame(PAYLOAD_OPUS, OPUS_PAYLOAD)


def test_json_payload_type_survives_the_round_trip() -> None:
    frame = unwrap(2, wrap(2, b"{}", payload_type=PAYLOAD_JSON))
    assert frame.payload_type == PAYLOAD_JSON


@pytest.mark.parametrize("version", [2, 3])
def test_truncated_headers_are_rejected(version: int) -> None:
    with pytest.raises(FramingError, match="shorter than its header"):
        unwrap(version, b"\x00")


@pytest.mark.parametrize("version", [2, 3])
def test_a_lying_payload_size_is_rejected(version: int) -> None:
    truncated = wrap(version, OPUS_PAYLOAD)[:-3]
    with pytest.raises(FramingError, match="announces"):
        unwrap(version, truncated)


@pytest.mark.parametrize("version", [0, 4])
def test_unsupported_versions_are_rejected_both_ways(version: int) -> None:
    with pytest.raises(FramingError, match="unsupported"):
        wrap(version, OPUS_PAYLOAD)
    with pytest.raises(FramingError, match="unsupported"):
        unwrap(version, OPUS_PAYLOAD)
