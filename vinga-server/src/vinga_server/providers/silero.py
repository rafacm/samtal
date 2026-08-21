"""End-of-utterance detection on Silero VAD.

pysilero-vad compiles the Silero model and its ONNX runtime into one
small wheel with no Python dependencies, which is why this is a core
dependency rather than an extra. The endpointer keeps the M3 feed/reset
shape and bookkeeping (trailing-silence window after speech, utterance
cap), but decides speech per 512-sample window by Silero probability
instead of signal energy.
"""

from pysilero_vad import SileroVoiceActivityDetector

from vinga_server.config.models import ProviderConfig
from vinga_server.providers.base import Endpointer, VadProvider
from vinga_server.providers.registry import OptionsReader

# The only rate Silero VAD accepts here, and the pipeline's input rate.
SAMPLE_RATE = 16000


class SileroEndpointer:
    """Buffers fed PCM into Silero's fixed windows; True the moment the
    utterance ends. Silence before any speech counts toward nothing, so
    a device left listening in a quiet room never trips this."""

    def __init__(
        self, threshold: float, trailing_silence_ms: float, max_utterance_ms: float
    ) -> None:
        self._detector = SileroVoiceActivityDetector()
        self._threshold = threshold
        self._trailing_silence_ms = trailing_silence_ms
        self._max_utterance_ms = max_utterance_ms
        self._window_bytes = self._detector.chunk_bytes()
        self._window_ms = self._detector.chunk_samples() * 1000 / SAMPLE_RATE
        self._pending = b""
        self.reset()

    def reset(self) -> None:
        self._detector.reset()
        self._pending = b""
        self._speech_heard = False
        self._silence_ms = 0.0
        self._utterance_ms = 0.0
        self._speech_ms = 0.0
        self._consumed_bytes = 0
        self._speech_start: int | None = None

    def feed(self, pcm: bytes) -> bool:
        self._pending += pcm
        ended = False
        while len(self._pending) >= self._window_bytes:
            window = self._pending[: self._window_bytes]
            self._pending = self._pending[self._window_bytes :]
            ended = self._account(window) or ended
        return ended

    def speech_start(self) -> int | None:
        return self._speech_start

    def speech_ms(self) -> float:
        return self._speech_ms

    def _account(self, window: bytes) -> bool:
        if self._detector(window) >= self._threshold:
            if not self._speech_heard:
                # The start of this window, to window granularity (32 ms).
                self._speech_start = self._consumed_bytes
            self._speech_heard = True
            self._speech_ms += self._window_ms
            self._silence_ms = 0.0
        elif self._speech_heard:
            self._silence_ms += self._window_ms
        self._consumed_bytes += self._window_bytes
        if not self._speech_heard:
            return False
        self._utterance_ms += self._window_ms
        return (
            self._silence_ms >= self._trailing_silence_ms
            or self._utterance_ms >= self._max_utterance_ms
        )


class SileroVad(VadProvider):
    """The tuning three numbers' worth of it, and an endpointer factory.

    It keeps the default no-op `close`, and that is a fact about what it
    holds rather than an omission (#191). The model lives inside
    `SileroVoiceActivityDetector`, and one of those belongs to each
    session's endpointer rather than to this: a session is gone, and its
    detector with it, long before the world it bound can be disposed of.
    What is left here is three numbers, which nothing has to be told
    about.
    """

    # Inference runs on the host; the model ships with the package.
    egress = False

    def __init__(
        self, threshold: float, trailing_silence_ms: float, max_utterance_ms: float
    ) -> None:
        self._threshold = threshold
        self._trailing_silence_ms = trailing_silence_ms
        self._max_utterance_ms = max_utterance_ms

    def new_endpointer(self) -> Endpointer:
        return SileroEndpointer(
            threshold=self._threshold,
            trailing_silence_ms=self._trailing_silence_ms,
            max_utterance_ms=self._max_utterance_ms,
        )


def build(label: str, config: ProviderConfig) -> SileroVad:
    options = OptionsReader(label, config)
    threshold = options.number("threshold", 0.5)
    trailing_silence_ms = options.number("trailing_silence_ms", 700.0)
    max_utterance_ms = options.number("max_utterance_ms", 10_000.0)
    options.finish()
    return SileroVad(
        threshold=threshold,
        trailing_silence_ms=trailing_silence_ms,
        max_utterance_ms=max_utterance_ms,
    )
