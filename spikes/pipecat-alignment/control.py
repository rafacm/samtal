"""The xiaozhi control plane the serializer is not allowed to express.

Adapter code, counted in gate 2. Two obligations live here, both for
the same reason: pipecat's websocket transport gives the serializer no
way to originate a message and no delivery path for outbound control
frames.

**The handshake.** `FrameSerializer` has one lifecycle hook,
`setup(StartFrame)`, and it carries no transport, so a serializer
cannot answer the device hello. `deserialize` turns the hello into an
`InputTransportMessageFrame` and this processor replies with an urgent
output message, which the output transport hands straight back to
`serialize`.

**The display protocol.** In pipecat 1.7.0 a `TTSStartedFrame`,
`TTSTextFrame` or `TranscriptionFrame` travelling downstream reaches
`BaseOutputTransport.MediaSender._handle_frame`, which routes anything
it does not recognise to `write_transport_frame`, a no-op that
`FastAPIWebsocketOutputTransport` does not override. A
`TTSStoppedFrame` fares worse: the media sender consumes it to raise
"bot stopped speaking" and it never reaches that call at all. So none
of xiaozhi's `tts` and `stt` messages can be produced by the
serializer from the frames that naturally carry them.

The route that works without patching pipecat is to re-emit each of
them as an `OutputTransportMessageFrame` carrying the xiaozhi JSON.
Those *are* delivered, and they travel through the same audio queue as
the reply audio, so ordering against the audio is preserved: `tts
stop` is written after the last packet is sent, which is what the
device's speaking state depends on. The cost is that the protocol's
message construction sits in a processor rather than in the
serializer, and that a pipecat adoption owns this translation table
forever.
"""

from pipecat.frames.frames import (
    Frame,
    InputTransportMessageFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from serializer import FRAME_MS, OUTPUT_SAMPLE_RATE


class XiaozhiControl(FrameProcessor):
    """Answers the device hello and emits the `tts`/`stt` messages."""

    def __init__(self, session_id: str, **kwargs):
        super().__init__(**kwargs)
        self._session_id = session_id

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InputTransportMessageFrame):
            message = frame.message
            if isinstance(message, dict) and message.get("type") == "hello":
                await self.push_frame(
                    OutputTransportMessageUrgentFrame(message=self._server_hello())
                )
            await self.push_frame(frame, direction)
            return

        # `tts start` precedes its audio and `tts stop` follows it, so
        # the message frame goes before or after the frame that caused
        # it accordingly. Both share the output queue with the audio.
        before = None
        after = None
        if isinstance(frame, TTSStartedFrame):
            before = self._tts("start")
        elif isinstance(frame, TTSTextFrame):
            before = self._tts("sentence_start", text=frame.text)
        elif isinstance(frame, TTSStoppedFrame):
            after = self._tts("stop")
        elif isinstance(frame, TranscriptionFrame):
            before = {
                "session_id": self._session_id,
                "type": "stt",
                "text": frame.text,
            }

        if before is not None:
            await self.push_frame(OutputTransportMessageFrame(message=before))
        await self.push_frame(frame, direction)
        if after is not None:
            await self.push_frame(OutputTransportMessageFrame(message=after))

    def _server_hello(self) -> dict:
        """xiaozhi-sdk indexes `session_id` and every `audio_params`
        field without defaults, so none of them are optional."""
        return {
            "type": "hello",
            "transport": "websocket",
            "session_id": self._session_id,
            "audio_params": {
                "format": "opus",
                "sample_rate": OUTPUT_SAMPLE_RATE,
                "channels": 1,
                "frame_duration": FRAME_MS,
            },
        }

    def _tts(self, state: str, text: str | None = None) -> dict:
        message = {"session_id": self._session_id, "type": "tts", "state": state}
        if text is not None:
            message["text"] = text
        return message
