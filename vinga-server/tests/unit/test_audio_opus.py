"""The Opus codec wrappers: framing discipline and a full round trip."""

import math
import struct

from vinga_server.audio import rms
from vinga_server.audio.opus import OpusDecoder, OpusEncoder

SAMPLE_RATE = 16000
FRAME_SAMPLES = 960  # 60 ms at 16 kHz


def sine_pcm(duration_ms: int, frequency: float = 440.0, amplitude: int = 8000) -> bytes:
    samples = SAMPLE_RATE * duration_ms // 1000
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * frequency * n / SAMPLE_RATE)))
        for n in range(samples)
    )


def test_sixty_milliseconds_in_is_one_packet_out() -> None:
    encoder = OpusEncoder()
    packets = encoder.encode(sine_pcm(60))
    assert len(packets) == 1
    assert packets[0]


def test_odd_chunk_sizes_are_buffered_to_whole_frames() -> None:
    encoder = OpusEncoder()
    pcm = sine_pcm(150)  # 2.5 frames
    step = 1000
    packets = []
    for start in range(0, len(pcm), step):
        packets.extend(encoder.encode(pcm[start : start + step]))
    assert len(packets) == 2
    # The half frame left over pads out with silence on flush.
    assert len(encoder.flush()) == 1
    assert encoder.flush() == []


def test_a_second_utterance_reuses_the_encoder() -> None:
    encoder = OpusEncoder()
    encoder.encode(sine_pcm(90))
    encoder.flush()
    assert len(encoder.encode(sine_pcm(60))) == 1


def test_round_trip_preserves_duration_and_signal() -> None:
    encoder = OpusEncoder()
    decoder = OpusDecoder()
    pcm = sine_pcm(600)
    packets = encoder.encode(pcm) + encoder.flush()
    assert len(packets) == 10
    decoded = b"".join(decoder.decode(packet) for packet in packets)
    # The decoder's 48 kHz-to-16 kHz resampler keeps a constant filter
    # delay of a few samples; the duration must match within 2 ms.
    shortfall = len(pcm) - len(decoded)
    assert 0 <= shortfall <= 2 * SAMPLE_RATE // 1000 * 2
    # Not asserting waveform equality (lossy codec, lookahead shift): the
    # tone must survive as energy in the same ballpark.
    assert rms(decoded) > rms(pcm) * 0.5


def test_decoding_silence_stays_silent() -> None:
    encoder = OpusEncoder()
    decoder = OpusDecoder()
    packets = encoder.encode(b"\x00\x00" * (FRAME_SAMPLES * 5))
    decoded = b"".join(decoder.decode(packet) for packet in packets)
    assert rms(decoded) < 50
