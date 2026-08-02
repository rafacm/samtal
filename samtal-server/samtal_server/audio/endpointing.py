"""End-of-utterance detection by signal energy.

A deliberately small stand-in for a real VAD: the utterance has ended
once speech has been heard and the signal then stays below an RMS
threshold for a trailing-silence window, or once it has simply run long
enough. M4 replaces this with Silero behind the same feed/reset shape;
the thresholds are module constants, not configuration, because this
implementation is not meant to outlive M4.
"""

import math
from array import array

# int16 RMS. A quiet room sits well below this; speech at arm's length
# sits well above it.
SPEECH_RMS_THRESHOLD = 500.0
TRAILING_SILENCE_MS = 700
MAX_UTTERANCE_MS = 10_000


def rms(pcm: bytes) -> float:
    """Root-mean-square amplitude of s16le PCM."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class EnergyEndpointer:
    """Feed decoded PCM chunks; told True the moment the utterance ends."""

    def __init__(
        self,
        sample_rate: int = 16000,
        threshold: float = SPEECH_RMS_THRESHOLD,
        trailing_silence_ms: float = TRAILING_SILENCE_MS,
        max_utterance_ms: float = MAX_UTTERANCE_MS,
    ) -> None:
        self._sample_rate = sample_rate
        self._threshold = threshold
        self._trailing_silence_ms = trailing_silence_ms
        self._max_utterance_ms = max_utterance_ms
        self.reset()

    def reset(self) -> None:
        self._speech_heard = False
        self._silence_ms = 0.0
        self._speech_ms = 0.0

    def feed(self, pcm: bytes) -> bool:
        """Account one chunk; True when the utterance just ended. Silence
        before any speech counts toward nothing, so a device left
        listening in a quiet room never trips this."""
        duration_ms = len(pcm) / 2 / self._sample_rate * 1000
        if rms(pcm) >= self._threshold:
            self._speech_heard = True
            self._silence_ms = 0.0
        elif self._speech_heard:
            self._silence_ms += duration_ms
        if not self._speech_heard:
            return False
        self._speech_ms += duration_ms
        return (
            self._silence_ms >= self._trailing_silence_ms
            or self._speech_ms >= self._max_utterance_ms
        )
