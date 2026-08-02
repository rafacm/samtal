"""Sample-rate conversion on the same PyAV/FFmpeg the codec uses.

TTS engines speak at whatever rate their voice was trained at (Piper's
medium voices are 22.05 kHz); devices are spoken to at the rate the
server hello announced. One resampler instance covers one stream: the
filter carries state between chunks, and `flush` drains the tail it
holds back for interpolation.
"""

import av


class Resampler:
    """Converts s16le mono PCM from one sample rate to another."""

    def __init__(self, in_rate: int, out_rate: int) -> None:
        self.in_rate = in_rate
        self.out_rate = out_rate
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=out_rate)
        self._pts = 0

    def process(self, pcm: bytes) -> bytes:
        """Convert one chunk; the output length follows the rate ratio,
        give or take the samples the filter holds between calls."""
        samples = len(pcm) // 2
        if not samples:
            return b""
        frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
        frame.sample_rate = self.in_rate
        frame.pts = self._pts
        self._pts += samples
        frame.planes[0].update(pcm[: samples * 2])
        return self._collect(self._resampler.resample(frame))

    def flush(self) -> bytes:
        """Drain the samples the filter still holds. The resampler is
        spent afterwards; use a fresh one for the next stream."""
        return self._collect(self._resampler.resample(None))

    @staticmethod
    def _collect(frames: list[av.AudioFrame]) -> bytes:
        return b"".join(bytes(frame.planes[0])[: frame.samples * 2] for frame in frames)
