"""The binary audio framing, byte-compatible with the firmware structs.

The expected layouts mirror `BinaryProtocol2`/`BinaryProtocol3` in
`main/protocols/websocket_protocol.cc` of 78/xiaozhi-esp32: packed
structs, network byte order.
"""

import pytest

from vinga_server.protocol.framing import (
    PAYLOAD_JSON,
    PAYLOAD_OPUS,
    SHORT_V2_FRAME,
    SHORT_V3_FRAME,
    UNFRAMED_STREAM,
    UNSUPPORTED_VERSION,
    V2_SIZE_MISMATCH,
    V3_SIZE_MISMATCH,
    Frame,
    FramingError,
    frames,
    unwrap,
    wrap,
)

OPUS_PAYLOAD = b"\xfc\xff\xfeopus-packet"

# A payload size no real frame would announce and no length in this
# suite happens to be, so finding it in a rendered message or a log
# record can only mean the header's own bytes were quoted back.
DECLARED_SENTINEL = 987654321


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


@pytest.mark.parametrize(
    ("version", "sentence"), [(2, SHORT_V2_FRAME), (3, SHORT_V3_FRAME)]
)
def test_truncated_headers_are_rejected(version: int, sentence: str) -> None:
    with pytest.raises(FramingError) as raised:
        unwrap(version, b"\x00")
    assert str(raised.value) == sentence


@pytest.mark.parametrize(
    ("version", "sentence"), [(2, V2_SIZE_MISMATCH), (3, V3_SIZE_MISMATCH)]
)
def test_a_lying_payload_size_is_rejected(version: int, sentence: str) -> None:
    truncated = wrap(version, OPUS_PAYLOAD)[:-3]
    with pytest.raises(FramingError) as raised:
        unwrap(version, truncated)
    assert str(raised.value) == sentence


@pytest.mark.parametrize("version", [0, 4])
def test_unsupported_versions_are_rejected_both_ways(version: int) -> None:
    for call in (wrap, unwrap):
        with pytest.raises(FramingError) as raised:
            call(version, OPUS_PAYLOAD)
        # Not even the version asked for: what a session negotiated is
        # a value a device proposed, and the closed set of versions
        # this server speaks is written down in `SUPPORTED_VERSIONS`.
        assert str(raised.value) == UNSUPPORTED_VERSION


def test_a_lying_v2_header_is_not_quoted_back_by_the_refusal() -> None:
    """The refusal a session logs verbatim, hunted for the header field
    that caused it. `size` is four bytes the far side chose, and the
    length compared against it is that choice restated, so a message
    naming either would put a peer's bytes on a retained surface
    (`docs/architecture/observability-surfaces.md`). Nothing of the
    frame reaches the exception's message, its `args`, or a chain
    behind it: the category is the whole answer.
    """
    header = (
        (2).to_bytes(2, "big")
        + PAYLOAD_OPUS.to_bytes(2, "big")
        + (0).to_bytes(4, "big")
        + (0).to_bytes(4, "big")
        + DECLARED_SENTINEL.to_bytes(4, "big")
    )

    with pytest.raises(FramingError) as raised:
        unwrap(2, header + OPUS_PAYLOAD)

    planted = str(DECLARED_SENTINEL)
    assert str(raised.value) == V2_SIZE_MISMATCH
    assert planted not in str(raised.value)
    assert planted not in repr(raised.value.args)
    # And no cause or context carrying it either: the struct's own
    # unpack succeeded, and a chain is the other way a value travels.
    assert raised.value.__cause__ is None and raised.value.__context__ is None


# A run of frames stored as a file, which is what the packaged utterance
# is. Nothing on the wire needs this: a websocket delivers one frame per
# message, and it is a stored stream that has to be walked.


def test_a_run_of_version_2_frames_walks_back_to_the_payloads() -> None:
    """The round trip the packaged utterance depends on: wrapped one at
    a time, concatenated, and read back as the same packets in the same
    order."""
    packets = [b"first", b"second-packet", b"\x00\xffthird"]

    walked = frames(2, b"".join(wrap(2, packet) for packet in packets))

    assert [frame.payload for frame in walked] == packets
    assert {frame.payload_type for frame in walked} == {PAYLOAD_OPUS}


def test_a_run_of_version_3_frames_walks_the_same_way() -> None:
    packets = [b"first", b"second-packet"]

    walked = frames(3, b"".join(wrap(3, packet) for packet in packets))

    assert [frame.payload for frame in walked] == packets


def test_an_empty_run_is_no_frames_rather_than_a_refusal() -> None:
    """A file with nothing in it is a file with no frames in it, which
    is a fact rather than a fault. What refuses an empty asset is the
    reader that wanted one."""
    assert frames(2, b"") == ()


def test_version_1_has_no_boundaries_to_walk_and_says_so() -> None:
    """Bare Opus is bare: there is no length anywhere in it, which is
    the whole reason the asset is stored as version 2 frames. Refused
    rather than guessed at, and by its own sentence rather than by the
    unsupported-version one, because 1 is a version this module very
    much supports for a single frame."""
    with pytest.raises(FramingError) as raised:
        frames(1, OPUS_PAYLOAD)

    assert str(raised.value) == UNFRAMED_STREAM


def test_a_version_nothing_declares_is_refused() -> None:
    with pytest.raises(FramingError) as raised:
        frames(4, b"")

    assert str(raised.value) == UNSUPPORTED_VERSION


def test_a_run_ending_inside_a_header_is_refused() -> None:
    """A truncated file, caught at the header rather than read past."""
    with pytest.raises(FramingError) as raised:
        frames(2, wrap(2, OPUS_PAYLOAD) + b"\x00\x02\x00")

    assert str(raised.value) == SHORT_V2_FRAME


def test_a_run_ending_inside_a_payload_is_refused_without_quoting_it() -> None:
    """The other truncation: a header announcing more bytes than the
    file holds. The announced length is four bytes the far side chose,
    so the sentence is the category and nothing else, which is the same
    rule `unwrap` is held to above.
    """
    truncated = wrap(2, OPUS_PAYLOAD)[: -len(OPUS_PAYLOAD) + 2]

    with pytest.raises(FramingError) as raised:
        frames(2, truncated)

    assert str(raised.value) == V2_SIZE_MISMATCH
    assert str(len(OPUS_PAYLOAD)) not in repr(raised.value.args)
