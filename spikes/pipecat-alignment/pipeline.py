"""The minimal pipeline under test, and the app that serves it.

Measurement harness plus the pipeline the plan specifies: Silero VAD,
a canned reply, no LLM, no cloud, no keys. The adapter proper is
`serializer.py`, `control.py` and `edge.py`.

Two wiring decisions are load-bearing for the measurement and are
stated here rather than buried:

- `AudioBufferProcessor` sits **after** `transport.output()`. That is
  the conventional placement, and it is what makes the recording
  wire-aligned at all: the output transport's media sender awaits
  `write_audio_frame` (serialize, send, pace) and pushes the frame
  downstream only afterwards, so the buffer sees a chunk just after
  the send the tap timestamped. Placed before the output transport it
  would record on the TTS's timeline instead, which is what the plan
  assumed and what an unlucky adopter would get.
- The buffer processor is asked for the output rate, 24 kHz, so the
  bot track is recorded natively and pipecat resamples none of it.
  Asking it for 16 kHz instead put its own streaming SOXR resampler
  in the path, which is never flushed at end of stream and so
  truncated the turn track by 92 ms, and it left the two tracks the
  measurement compares going through two different resampler
  implementations. `compose.py` now converts every full track to
  16 kHz through one `resample_poly` call, which is what the plan
  asked for.
"""

import spike_env  # noqa: F401  (must precede every pipecat import)

import asyncio  # noqa: E402
import wave  # noqa: E402
from pathlib import Path  # noqa: E402

from fastapi import FastAPI, WebSocket  # noqa: E402
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from edge import device_edge
from serializer import DEVICE_SAMPLE_RATE, FRAME_MS, OUTPUT_SAMPLE_RATE
from tap import Recorder, TappedWebSocket

# The rate the capture pair is written at, and what the analysis reads.
CAPTURE_RATE = 16000

# How much of the reply is queued at once. The output transport rechunks
# to its own size anyway; this only controls how the harness feeds it.
REPLY_CHUNK_MS = 60


def load_wav(path: Path, expect_rate: int) -> bytes:
    with wave.open(str(path)) as w:
        if w.getnchannels() != 1 or w.getframerate() != expect_rate:
            raise SystemExit(f"{path}: expected mono {expect_rate} Hz")
        return w.readframes(w.getnframes())


class CannedReply(FrameProcessor):
    """Speaks a fixed clip once the user's turn ends. No LLM, no TTS.

    The whole clip is queued as fast as the pipeline accepts it, which
    is the production shape: a TTS service produces faster than real
    time and the output path is what decides when audio leaves.
    """

    def __init__(self, pcm: bytes, sentence: str, recorder: Recorder, **kwargs):
        super().__init__(**kwargs)
        self._pcm = pcm
        self._sentence = sentence
        self._recorder = recorder
        self._spoke = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, VADUserStoppedSpeakingFrame) and not self._spoke:
            self._spoke = True
            await self._speak()

    async def _speak(self) -> None:
        self._recorder.mark("heard")
        await self.push_frame(
            TranscriptionFrame(user_id="device", text=self._sentence, timestamp="")
        )
        await self.push_frame(TTSStartedFrame())
        await self.push_frame(
            TTSTextFrame(text=self._sentence, aggregated_by="sentence")
        )
        chunk = OUTPUT_SAMPLE_RATE * REPLY_CHUNK_MS // 1000 * 2
        for start in range(0, len(self._pcm), chunk):
            await self.push_frame(
                TTSAudioRawFrame(
                    audio=self._pcm[start : start + chunk],
                    sample_rate=OUTPUT_SAMPLE_RATE,
                    num_channels=1,
                )
            )
        await self.push_frame(TTSStoppedFrame())
        self._recorder.mark("reply_queued")


def create_app(
    recorder: Recorder,
    reply_pcm: bytes,
    sentence: str,
    *,
    finished: asyncio.Event | None = None,
) -> FastAPI:
    app = FastAPI()

    @app.websocket("/xiaozhi/v1/")
    async def device(websocket: WebSocket) -> None:
        await websocket.accept()
        transport, control, session_id = device_edge(
            TappedWebSocket(websocket, recorder)
        )
        recorder.mark("session_open", session_id=session_id)

        buffer = AudioBufferProcessor(
            sample_rate=OUTPUT_SAMPLE_RATE,
            num_channels=2,
            buffer_size=OUTPUT_SAMPLE_RATE * 2 // 4,
            auto_start_recording=True,
            # The other bot track the processor offers, recorded for
            # comparison: it is extended only with the bot's own audio,
            # so it escapes the cross-track padding the delivered track
            # carries, but it arrives once, when the bot stops speaking.
            enable_turn_audio=True,
        )

        @buffer.event_handler("on_track_audio_data")
        async def on_track(_proc, user: bytes, bot: bytes, rate: int, _channels: int):
            recorder.on_delivery(user, bot, rate)

        @buffer.event_handler("on_bot_turn_audio_data")
        async def on_bot_turn(_proc, bot: bytes, _rate: int, _channels: int):
            recorder.on_turn(bytes(bot))

        pipeline = Pipeline(
            [
                transport.input(),
                VADProcessor(
                    vad_analyzer=SileroVADAnalyzer(
                        sample_rate=DEVICE_SAMPLE_RATE,
                        params=VADParams(stop_secs=0.6),
                    )
                ),
                CannedReply(reply_pcm, sentence, recorder),
                control,
                transport.output(),
                buffer,
            ]
        )
        worker = PipelineWorker(
            pipeline,
            params=PipelineParams(
                audio_in_sample_rate=DEVICE_SAMPLE_RATE,
                audio_out_sample_rate=OUTPUT_SAMPLE_RATE,
            ),
            idle_timeout_secs=None,
            enable_rtvi=False,
        )
        try:
            await WorkerRunner(handle_sigint=False).run(worker)
        finally:
            if finished is not None:
                finished.set()

    return app


__all__ = ["create_app", "load_wav", "CAPTURE_RATE", "FRAME_MS"]
