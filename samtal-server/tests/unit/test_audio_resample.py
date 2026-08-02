"""Sample-rate conversion for TTS output."""

import math
import struct

from samtal_server.audio.endpointing import rms
from samtal_server.audio.resample import Resampler


def sine(sample_rate: int, duration_ms: int, hz: float = 440.0) -> bytes:
    samples = sample_rate * duration_ms // 1000
    return b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * n / sample_rate)))
        for n in range(samples)
    )


def test_upsampling_scales_the_sample_count_by_the_rate_ratio() -> None:
    resampler = Resampler(in_rate=22050, out_rate=24000)
    pcm = sine(22050, 500)
    out = resampler.process(pcm) + resampler.flush()
    expected = len(pcm) * 24000 / 22050
    assert abs(len(out) - expected) < 24000 * 2 * 0.01  # within 10 ms
    assert rms(out) > 1000  # the signal survived


def test_chunked_processing_matches_one_shot_processing_in_length() -> None:
    pcm = sine(16000, 300)
    one_shot = Resampler(16000, 24000)
    whole = one_shot.process(pcm) + one_shot.flush()

    chunked = Resampler(16000, 24000)
    out = b""
    for start in range(0, len(pcm), 640):
        out += chunked.process(pcm[start : start + 640])
    out += chunked.flush()
    assert len(out) == len(whole)


def test_identity_rate_passes_the_audio_through() -> None:
    resampler = Resampler(24000, 24000)
    pcm = sine(24000, 120)
    out = resampler.process(pcm) + resampler.flush()
    assert len(out) == len(pcm)


def test_empty_input_produces_empty_output() -> None:
    assert Resampler(16000, 24000).process(b"") == b""
