"""The xiaozhi frame serializer for pipecat's websocket transport.

Gate 2's measured artifact. It mirrors the semantics of samtal's own
device boundary (`samtal_server/device/boundary.py`): mic audio and the
protocol's turn-taking acts inbound, transcript, speaking state and
paced reply audio outbound. Everything here would have to exist, and be
maintained, in any pipecat adoption; the harness around it (the canned
reply, the tap, the composer) would not.

Three things are worth reading before the code, because each is a gate
2 finding rather than an implementation detail.

**The serializer cannot answer a message, and never sees an outbound
control frame.** Its only handle on the world is `setup(StartFrame)`,
which carries sample rates and no transport, so the hello handshake
cannot live here. Nor can the `tts` and `stt` messages: pipecat's
websocket output transport delivers audio and transport messages to
`serialize` and drops every other outbound frame (`control.py` has the
detail). Both therefore live in `control.py`, and what remains here is
codec translation plus the JSON that control hands down. That split is
not a style choice, it is what the contract allows.

**One payload per frame.** `serialize` returns a single `str | bytes |
None`, so a chunk of PCM that fills two Opus packets can only return
one of them. samtal's boundary has `encode_audio` return a *batch*
(`PlayableAudio`) for exactly this reason. The serializer therefore
keeps its own encode buffer and the transport is configured to hand it
exactly one frame's worth at a time; the buffer exists to make the
mismatch safe rather than to use it.

**There is no pacing here, and the first reading that there had to be
was wrong.** The feasibility checkpoint read the output transport's
`_send_interval = (audio_chunk_size / self.sample_rate) / 2` as half a
chunk's duration and concluded a reply would leave the socket at twice
real time. `audio_chunk_size` is in *bytes*, so for 16-bit mono the
division by the sample rate already yields twice the chunk's duration
and the `/2` cancels it exactly: the interval is the chunk's own
duration, and the transport paces at real time. Measured: 2103 packets
at a median 60.0 ms inter-send interval, 99.8% within 5 ms of the frame
cadence, with nothing in this file doing anything about it.

The pacing layer the spike first added was therefore redundant, and it
is gone rather than merely disabled, so that gate 2 counts an adapter
an adoption would actually ship. One caveat survives its removal: the
formula only cancels for mono, and `audio_out_channels = 2` would make
the transport send at half real time.
"""

import ctypes.util
import json

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InputTransportMessageFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
    StartFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer

if ctypes.util.find_library("opus") is None:
    # opuslib binds libopus by name; the simulator ships a copy for
    # platforms without one installed, and its shim is the supported way
    # to point ctypes at it.
    from xiaozhi_sdk.utils import setup_opus

    setup_opus()

import opuslib  # noqa: E402

# What devices send: 16 kHz mono Opus in 60 ms frames. Fixed by the
# firmware, not negotiable from here.
DEVICE_SAMPLE_RATE = 16000
FRAME_MS = 60

# What the server announces in its hello and speaks back, as
# samtal-server does.
OUTPUT_SAMPLE_RATE = 24000


class XiaozhiFrameSerializer(FrameSerializer):
    """Translates between xiaozhi wire messages and pipecat frames."""

    def __init__(self, session_id: str, **kwargs):
        super().__init__(**kwargs)
        self._session_id = session_id
        self._decoder = opuslib.Decoder(fs=DEVICE_SAMPLE_RATE, channels=1)
        self._encoder = opuslib.Encoder(
            fs=OUTPUT_SAMPLE_RATE, channels=1, application=opuslib.APPLICATION_AUDIO
        )
        self._in_frame_size = DEVICE_SAMPLE_RATE * FRAME_MS // 1000
        self._out_frame_size = OUTPUT_SAMPLE_RATE * FRAME_MS // 1000
        self._encode_buffer = bytearray()

    async def setup(self, frame: StartFrame) -> None:
        """Called once from each of the two transports, so idempotent."""

    # Device to pipeline.

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes):
            pcm = self._decoder.decode(data, frame_size=self._in_frame_size)
            return InputAudioRawFrame(
                audio=pcm, sample_rate=DEVICE_SAMPLE_RATE, num_channels=1
            )

        message = json.loads(data)
        kind = message.get("type")
        if kind == "hello":
            # Answered by the handshake processor: see the module note.
            return InputTransportMessageFrame(message=message)
        if kind == "abort":
            return InterruptionFrame()
        if kind == "listen":
            # The listening mode is device policy. samtal's edge acts on
            # it (arming, manual end of turn, the barge-in gates); the
            # spike only observes it, and gate 2's obligation map says so.
            return InputTransportMessageFrame(message=message)
        return None

    # Pipeline to device.

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if self.should_ignore_frame(frame):
            return None
        if isinstance(frame, OutputAudioRawFrame):
            return await self._audio(frame)
        if isinstance(
            frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)
        ):
            return json.dumps(frame.message)
        return None

    async def _audio(self, frame: OutputAudioRawFrame) -> bytes | None:
        self._encode_buffer.extend(frame.audio)
        want = self._out_frame_size * 2
        if len(self._encode_buffer) < want:
            return None
        pcm = bytes(self._encode_buffer[:want])
        del self._encode_buffer[:want]
        return self._encoder.encode(pcm, self._out_frame_size)

