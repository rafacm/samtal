"""Audio codec, resampling, and measurement for the conversation loop."""

import math
from array import array

from vinga_server.audio.opus import OpusDecoder, OpusEncoder
from vinga_server.audio.resample import Resampler

__all__ = ["OpusDecoder", "OpusEncoder", "Resampler", "rms"]


def rms(pcm: bytes) -> float:
    """Root-mean-square amplitude of s16le PCM."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))
