"""Opus encode and decode on PyAV's bundled FFmpeg.

Chosen over opuslib (what upstream and xiaozhi-sdk use) for its prebuilt
wheels on every deployment target and because the same dependency covers
M4's resampling and TTS-format decoding. One quirk to know: FFmpeg's Opus
decoders always emit 48 kHz, Opus's internal rate, whatever rate the
stream was encoded from, so decoded audio is resampled to the rate this
module promises before it leaves.

All PCM at this boundary is s16le mono.
"""

import av


class OpusDecoder:
    """Decodes Opus packets to s16le mono PCM at a fixed sample rate."""

    def __init__(self, sample_rate: int = 16000) -> None:
        self.sample_rate = sample_rate
        self._codec = av.CodecContext.create("libopus", "r")
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)

    def decode(self, packet: bytes) -> bytes:
        """One Opus packet in, its PCM out. Raises av.FFmpegError on data
        that does not decode."""
        chunks: list[bytes] = []
        for frame in self._codec.decode(av.Packet(packet)):
            for resampled in self._resampler.resample(frame):
                # The plane buffer can be padded past the sample count.
                chunks.append(bytes(resampled.planes[0])[: resampled.samples * 2])
        return b"".join(chunks)


class OpusEncoder:
    """Encodes s16le mono PCM into fixed-duration Opus packets, buffering
    a partial frame between calls until it fills."""

    def __init__(self, sample_rate: int = 16000, frame_duration_ms: int = 60) -> None:
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_samples = sample_rate * frame_duration_ms // 1000
        self.frame_bytes = self.frame_samples * 2
        codec = av.CodecContext.create("libopus", "w")
        codec.sample_rate = sample_rate
        codec.layout = "mono"
        codec.format = "s16"
        codec.options = {"application": "voip", "frame_duration": str(frame_duration_ms)}
        codec.open()
        self._codec = codec
        self._pending = b""
        self._pts = 0

    def encode(self, pcm: bytes) -> list[bytes]:
        """Feed PCM of any length; get every packet that filled."""
        self._pending += pcm
        packets: list[bytes] = []
        while len(self._pending) >= self.frame_bytes:
            chunk = self._pending[: self.frame_bytes]
            self._pending = self._pending[self.frame_bytes :]
            packets.extend(self._encode_frame(chunk))
        return packets

    def flush(self) -> list[bytes]:
        """Pad any pending partial frame with silence and encode it. The
        codec itself is not drained: its few milliseconds of lookahead
        stay inside, which keeps it reusable for the next utterance."""
        if not self._pending:
            return []
        chunk = self._pending.ljust(self.frame_bytes, b"\x00")
        self._pending = b""
        return self._encode_frame(chunk)

    def _encode_frame(self, chunk: bytes) -> list[bytes]:
        frame = av.AudioFrame(format="s16", layout="mono", samples=self.frame_samples)
        frame.sample_rate = self.sample_rate
        frame.pts = self._pts
        frame.planes[0].update(chunk)
        self._pts += self.frame_samples
        return [bytes(packet) for packet in self._codec.encode(frame)]
