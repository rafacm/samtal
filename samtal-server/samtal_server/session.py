"""One conversation session per device websocket connection.

The session owns the handshake and the audio loop. M3 scope: accept the
upgrade, exchange hellos, buffer each utterance while the device listens,
and speak it back re-encoded (a decode/encode round trip) framed by
`tts start`/`stop`. M4 replaces the echo with the VAD/ASR/LLM/TTS
pipeline; the endpointer here is its stand-in.

Two end-of-utterance triggers coexist because the firmware's listening
modes differ: manual mode sends `listen stop`, while auto and realtime
modes stream mic audio until the server decides the user finished, which
is what the energy endpointer is for. Realtime mode's defining feature,
listening while the server speaks, is not honoured yet: frames arriving
during playback are dropped, as in auto mode.
"""

import asyncio
import contextlib
import logging
import uuid

import av
from starlette.websockets import WebSocket, WebSocketDisconnect

from samtal_server.audio.endpointing import EnergyEndpointer
from samtal_server.audio.opus import OpusDecoder, OpusEncoder
from samtal_server.config import Config
from samtal_server.config.models import normalize_mac
from samtal_server.protocol import framing, messages

logger = logging.getLogger(__name__)

# How long to wait for the device hello; the firmware gives the server
# hello the same ten seconds.
HELLO_TIMEOUT_S = 10.0

# The rate everything inside the session runs at: what devices send, and
# for M3 also what the echo announces back, so nothing is resampled.
# M4's TTS sets its own output rate (typically 24 kHz).
PIPELINE_SAMPLE_RATE = 16000
OUTPUT_AUDIO = messages.AudioParams(
    format="opus", sample_rate=PIPELINE_SAMPLE_RATE, channels=1, frame_duration=60
)

# Websocket close codes (RFC 6455): policy violation for who you are,
# protocol error for what you sent.
POLICY_VIOLATION = 1008
PROTOCOL_ERROR = 1002


class Session:
    """The server side of one device connection."""

    def __init__(self, websocket: WebSocket, config: Config) -> None:
        self.websocket = websocket
        self.config = config
        self.session_id = uuid.uuid4().hex
        self.protocol_version = 1
        self.listening = False
        self._decoder = OpusDecoder(sample_rate=PIPELINE_SAMPLE_RATE)
        self._encoder = OpusEncoder(
            sample_rate=OUTPUT_AUDIO.sample_rate,
            frame_duration_ms=OUTPUT_AUDIO.frame_duration,
        )
        self._endpointer = EnergyEndpointer(sample_rate=PIPELINE_SAMPLE_RATE)
        self._utterance = bytearray()
        self._reply_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        device_id = self.websocket.headers.get("device-id", "").strip()
        client_id = self.websocket.headers.get("client-id", "").strip()
        await self.websocket.accept()

        try:
            mac = normalize_mac(device_id)
        except ValueError as exc:
            logger.warning("session %s rejected: Device-Id header: %s", self.session_id, exc)
            await self._close(POLICY_VIOLATION, "Device-Id must be the device MAC")
            return

        agent = self.config.agent_for_device(mac)
        if agent is None:
            logger.warning(
                "session %s rejected: device %s has no agent: bind it under devices "
                "or set default_agent",
                self.session_id,
                mac,
            )
            await self._close(POLICY_VIOLATION, "no agent is configured for this device")
            return

        hello = await self._receive_hello()
        if hello is None:
            return
        self.protocol_version = hello.version
        await self.websocket.send_text(messages.server_hello(self.session_id, OUTPUT_AUDIO))
        logger.info(
            "session %s open: device %s (client %s) agent %s, protocol v%d, "
            "%d Hz %d ms frames in",
            self.session_id,
            mac,
            client_id or "unknown",
            agent,
            self.protocol_version,
            hello.audio_params.sample_rate,
            hello.audio_params.frame_duration,
        )

        try:
            await self._serve()
        except WebSocketDisconnect:
            pass
        finally:
            if self._reply_task is not None:
                self._reply_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._reply_task
            logger.info("session %s closed (device %s)", self.session_id, mac)

    async def _receive_hello(self) -> messages.DeviceHello | None:
        """The device speaks first; anything but a timely, well-formed
        hello for opus over websocket ends the connection."""
        try:
            async with asyncio.timeout(HELLO_TIMEOUT_S):
                received = await self.websocket.receive()
        except TimeoutError:
            await self._close(PROTOCOL_ERROR, "no hello received")
            return None
        if received["type"] == "websocket.disconnect":
            return None
        if received.get("text") is None:
            await self._close(PROTOCOL_ERROR, "expected a hello text frame first")
            return None

        try:
            message = messages.parse_message(received["text"])
        except messages.ProtocolError as exc:
            logger.warning("session %s: malformed hello: %s", self.session_id, exc)
            await self._close(PROTOCOL_ERROR, "malformed hello")
            return None
        if not isinstance(message, messages.DeviceHello):
            await self._close(PROTOCOL_ERROR, "expected a hello first")
            return None
        if message.transport != "websocket":
            await self._close(PROTOCOL_ERROR, "transport must be websocket")
            return None
        if message.audio_params.format != "opus":
            await self._close(PROTOCOL_ERROR, "audio format must be opus")
            return None
        if message.version not in framing.SUPPORTED_VERSIONS:
            await self._close(PROTOCOL_ERROR, "unsupported protocol version")
            return None
        return message

    async def _serve(self) -> None:
        while True:
            received = await self.websocket.receive()
            if received["type"] == "websocket.disconnect":
                return
            if received.get("bytes") is not None:
                self._handle_audio(received["bytes"])
            elif received.get("text") is not None:
                await self._handle_text(received["text"])

    def _handle_audio(self, data: bytes) -> None:
        if not self.listening:
            return
        try:
            frame = framing.unwrap(self.protocol_version, data)
        except framing.FramingError as exc:
            logger.warning("session %s: dropped binary frame: %s", self.session_id, exc)
            return
        if frame.payload_type != framing.PAYLOAD_OPUS:
            return
        try:
            pcm = self._decoder.decode(frame.payload)
        except av.FFmpegError as exc:
            logger.warning("session %s: undecodable Opus packet: %s", self.session_id, exc)
            return
        self._utterance.extend(pcm)
        if self._endpointer.feed(pcm):
            self._finish_utterance()

    async def _handle_text(self, text: str) -> None:
        try:
            message = messages.parse_message(text)
        except messages.ProtocolError as exc:
            logger.warning("session %s: ignored message: %s", self.session_id, exc)
            return

        match message:
            case messages.ListenMessage(state="start", mode=mode):
                logger.debug("session %s: listening (%s mode)", self.session_id, mode)
                self.listening = True
                self._reset_utterance()
            case messages.ListenMessage(state="stop"):
                self.listening = False
                if self._utterance:
                    self._finish_utterance()
            case messages.ListenMessage(state="detect", text=word):
                logger.debug("session %s: wake word reported: %s", self.session_id, word)
            case messages.AbortMessage(reason=reason):
                logger.info(
                    "session %s: device aborted (%s)", self.session_id, reason or "no reason"
                )
                if self._reply_task is not None:
                    self._reply_task.cancel()
                self._reset_utterance()
            case messages.McpMessage():
                logger.debug("session %s: MCP message ignored until M6", self.session_id)
            case _:
                logger.debug(
                    "session %s: ignoring %s message", self.session_id, message.type
                )

    def _finish_utterance(self) -> None:
        """Hand the buffered utterance to the reply task. Listening stops
        until the device asks again, which auto mode does by sending
        `listen start` after the reply's `tts stop`."""
        pcm = bytes(self._utterance)
        self._reset_utterance()
        self.listening = False
        if self._reply_task is not None and not self._reply_task.done():
            return
        logger.info(
            "session %s: utterance of %.1f s, echoing it back",
            self.session_id,
            len(pcm) / 2 / PIPELINE_SAMPLE_RATE,
        )
        self._reply_task = asyncio.create_task(self._reply(pcm))

    def _reset_utterance(self) -> None:
        self._utterance.clear()
        self._endpointer.reset()

    async def _reply(self, pcm: bytes) -> None:
        """Speak the utterance back, paced at the frame cadence so the
        device's playback queue is never flooded. Cancelled by `abort`."""
        packets = self._encoder.encode(pcm) + self._encoder.flush()
        try:
            await self.websocket.send_text(messages.tts_message(self.session_id, "start"))
            await self.websocket.send_text(
                messages.tts_message(self.session_id, "sentence_start", text="(echo)")
            )
            loop = asyncio.get_running_loop()
            start = loop.time()
            frame_s = OUTPUT_AUDIO.frame_duration / 1000
            for count, packet in enumerate(packets):
                await asyncio.sleep(start + count * frame_s - loop.time())
                await self.websocket.send_bytes(framing.wrap(self.protocol_version, packet))
        except (WebSocketDisconnect, RuntimeError):
            return  # the device went away mid-reply
        finally:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await self.websocket.send_text(messages.tts_message(self.session_id, "stop"))

    async def _close(self, code: int, reason: str) -> None:
        with contextlib.suppress(RuntimeError):
            await self.websocket.close(code=code, reason=reason)
