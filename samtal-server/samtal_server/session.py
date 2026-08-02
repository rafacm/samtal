"""One conversation session per device websocket connection.

The session owns the handshake and the conversation loop. It talks as
one agent at a time, the active agent, picked at connect from the agents
the device is bound to; prompt, providers, and endpointer all come from
that agent, so swapping it swaps all three.

While the device listens, decoded mic audio feeds the agent's
endpointer; when the utterance ends, ASR transcribes it, the LLM streams
a reply into sentences, and TTS speaks each sentence back as paced Opus
frames, framed by `tts start`/`sentence_start`/`stop` with the
transcript announced in an `stt` message. Conversation history lives
here, one list of turns per connection.

Two end-of-utterance triggers coexist because the firmware's listening
modes differ: manual mode sends `listen stop`, while auto and realtime
modes stream mic audio until the server decides the user finished,
which is what the endpointer is for. Realtime mode's defining feature,
listening while the server speaks, is not honoured yet: frames arriving
during playback are dropped, as in auto mode.
"""

import asyncio
import contextlib
import logging
import uuid
from typing import Any

import av
from starlette.websockets import WebSocket, WebSocketDisconnect

from samtal_server import __version__
from samtal_server.audio.opus import OpusDecoder, OpusEncoder
from samtal_server.audio.resample import Resampler
from samtal_server.config import Config
from samtal_server.config.models import normalize_mac
from samtal_server.protocol import framing, messages
from samtal_server.protocol import mcp as mcp_protocol
from samtal_server.providers import AgentProviders, Endpointer, TextDelta, Turn
from samtal_server.text import SentenceSplitter
from samtal_server.tools.device import DeviceToolClient

logger = logging.getLogger(__name__)

# How long to wait for the device hello; the firmware gives the server
# hello the same ten seconds.
HELLO_TIMEOUT_S = 10.0

# The rate the input side of the pipeline runs at: what devices send,
# and what the endpointer and ASR are fed.
PIPELINE_SAMPLE_RATE = 16000

# What the server speaks: TTS output is resampled to this rate, encoded
# in 60 ms Opus frames, and announced in the server hello.
OUTPUT_AUDIO = messages.AudioParams(
    format="opus", sample_rate=24000, channels=1, frame_duration=60
)

# Websocket close codes (RFC 6455): policy violation for who you are,
# protocol error for what you sent.
POLICY_VIOLATION = 1008
PROTOCOL_ERROR = 1002


class AgentNotAllowed(ValueError):
    """Something asked a session to become an agent its device is not
    bound to. M6's switch_agent tool turns this into a spoken refusal;
    until then it can only mean a bug, since the only activation is the
    device's own first bound agent."""


class Session:
    """The server side of one device connection."""

    def __init__(
        self,
        websocket: WebSocket,
        config: Config,
        agent_providers: dict[str, AgentProviders],
    ) -> None:
        self.websocket = websocket
        self.config = config
        self.session_id = uuid.uuid4().hex
        self.protocol_version = 1
        self.listening = False
        self._agent_providers = agent_providers
        # The agents this device may talk to, and the one it is talking to
        # now. M5 activates the first at connect and never switches; M6's
        # switch_agent tool moves between them.
        self._agents: list[str] = []
        self._agent: str | None = None
        self._providers: AgentProviders | None = None
        self._decoder = OpusDecoder(sample_rate=PIPELINE_SAMPLE_RATE)
        self._encoder = OpusEncoder(
            sample_rate=OUTPUT_AUDIO.sample_rate,
            frame_duration_ms=OUTPUT_AUDIO.frame_duration,
        )
        self._endpointer: Endpointer | None = None
        self._utterance = bytearray()
        self._turns: list[Turn] = []
        self._reply_task: asyncio.Task[None] | None = None
        # The device's own tools, when it said it has any. Discovery runs
        # in the background, so an early utterance simply runs without
        # them rather than waiting.
        self._device_tools: DeviceToolClient | None = None
        self._discovery: asyncio.Task[None] | None = None
        # Outgoing frame pacing, reset per reply on the first frame.
        self._pace_start: float | None = None
        self._pace_count = 0

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

        agents = [
            name for name in self.config.agents_for_device(mac) if name in self._agent_providers
        ]
        if not agents:
            logger.warning(
                "session %s rejected: device %s has no agent: bind it under devices "
                "or set default_agent",
                self.session_id,
                mac,
            )
            await self._close(POLICY_VIOLATION, "no agent is configured for this device")
            return
        self._agents = agents
        self._activate_agent(agents[0])

        hello = await self._receive_hello()
        if hello is None:
            return
        self.protocol_version = hello.version
        await self.websocket.send_text(messages.server_hello(self.session_id, OUTPUT_AUDIO))
        logger.info(
            "session %s open: device %s (client %s) agent %s%s, protocol v%d, "
            "%d Hz %d ms frames in",
            self.session_id,
            mac,
            client_id or "unknown",
            self._agent,
            f" (also bound to {', '.join(self._agents[1:])})" if len(self._agents) > 1 else "",
            self.protocol_version,
            hello.audio_params.sample_rate,
            hello.audio_params.frame_duration,
        )
        self._start_device_discovery(hello)

        try:
            await self._serve()
        except WebSocketDisconnect:
            pass
        finally:
            await self._cancel_reply()
            await self._stop_device_discovery()
            logger.info("session %s closed (device %s)", self.session_id, mac)

    def _start_device_discovery(self, hello: messages.DeviceHello) -> None:
        """Ask a device that advertised MCP for its tools. In the
        background: the handshake is three round trips, and the
        conversation must not wait on a board that may never answer."""
        if hello.features.get("mcp") is not True:
            return
        self._device_tools = DeviceToolClient(
            self._send_mcp, f"session {self.session_id}", "samtal-server", __version__
        )
        self._discovery = asyncio.create_task(self._device_tools.discover())

    async def _stop_device_discovery(self) -> None:
        if self._device_tools is not None:
            self._device_tools.close()
        if self._discovery is not None:
            self._discovery.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._discovery
            self._discovery = None

    async def _send_mcp(self, payload: dict[str, Any]) -> None:
        await self.websocket.send_text(mcp_protocol.envelope(self.session_id, payload))

    def _activate_agent(self, name: str) -> None:
        """Talk as this agent from now on: its prompt, its providers, and a
        fresh endpointer from its VAD, since the previous agent's endpointer
        carries the previous agent's tuning and mid-utterance state. Called
        once at connect in M5; M6's switch_agent tool calls it again
        mid-session, and decides then what becomes of the history.

        The device's bound list is enforced here rather than left to
        callers, because the next caller is a tool whose argument a model
        chose: an agent that merely exists is not one this device may
        talk to. Nothing is swapped when the name is refused, so the
        session keeps the agent it already had."""
        if name not in self._agents:
            raise AgentNotAllowed(
                f'this device is not bound to agent "{name}"'
                + (f" (bound to: {', '.join(self._agents)})" if self._agents else "")
            )
        self._agent = name
        self._providers = self._agent_providers[name]
        self._endpointer = self._providers.vad.new_endpointer()
        self._reset_utterance()

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
        if not self.listening or self._endpointer is None:
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
                await self._cancel_reply()
                self._reset_utterance()
            case messages.McpMessage(payload=payload):
                if self._device_tools is None:
                    logger.debug(
                        "session %s: MCP message from a device that did not advertise MCP",
                        self.session_id,
                    )
                else:
                    self._device_tools.handle(payload)
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
            # Only reachable in realtime mode, where the mic streams while
            # a reply plays; one reply at a time until realtime is honoured.
            logger.warning(
                "session %s: dropping an utterance, a reply is already streaming",
                self.session_id,
            )
            return
        logger.info(
            "session %s: utterance of %.1f s",
            self.session_id,
            len(pcm) / 2 / PIPELINE_SAMPLE_RATE,
        )
        self._reply_task = asyncio.create_task(self._reply(pcm))

    def _reset_utterance(self) -> None:
        self._utterance.clear()
        if self._endpointer is not None:
            self._endpointer.reset()

    async def _cancel_reply(self) -> None:
        """Cancel a reply in flight and see the cancellation through.
        Waiting matters: a fire-and-forget cancel leaves the task not yet
        done, and an utterance finishing in that window would be dropped."""
        if self._reply_task is None:
            return
        self._reply_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._reply_task
        self._reply_task = None

    async def _reply(self, pcm: bytes) -> None:
        """Run one utterance through ASR, the LLM, and TTS. Cancelled by
        `abort`; provider failures end the reply but not the session. The
        closing `tts stop` is sent even then, because the device (in auto
        mode) waits for it before listening again."""
        assert self._providers is not None
        providers = self._providers
        spoken: list[str] = []
        try:
            transcript = (await providers.asr.transcribe(pcm, PIPELINE_SAMPLE_RATE)).strip()
            if transcript:
                await self.websocket.send_text(messages.stt_message(self.session_id, transcript))
                logger.info('session %s: heard "%s"', self.session_id, transcript)
            else:
                logger.info("session %s: nothing transcribed", self.session_id)
            await self.websocket.send_text(messages.tts_message(self.session_id, "start"))
            if transcript:
                self._turns.append(Turn("user", transcript))
                await self._speak_reply(transcript, spoken)
        except (WebSocketDisconnect, RuntimeError):
            return  # the device went away mid-reply
        except Exception:
            logger.exception("session %s: reply failed", self.session_id)
        finally:
            if spoken:
                self._turns.append(Turn("assistant", " ".join(spoken)))
                logger.info('session %s: replied "%s"', self.session_id, " ".join(spoken))
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await self.websocket.send_text(messages.tts_message(self.session_id, "stop"))

    async def _speak_reply(self, transcript: str, spoken: list[str]) -> None:
        """Stream the LLM reply, speaking each sentence as it completes.
        `spoken` collects what was said so the history keeps even the part
        of a reply that an abort cut short."""
        assert self._providers is not None
        providers = self._providers
        splitter = SentenceSplitter()
        resampler = Resampler(providers.tts.sample_rate, OUTPUT_AUDIO.sample_rate)
        self._pace_start = None
        self._pace_count = 0
        async for event in providers.llm.stream(providers.prompt, self._turns):
            if isinstance(event, TextDelta):
                for sentence in splitter.push(event.text):
                    await self._speak(sentence, resampler, spoken)
        tail = splitter.flush()
        if tail is not None:
            await self._speak(tail, resampler, spoken)
        # Drain the resampler's interpolation tail and the encoder's
        # partial frame, which flushing pads with silence.
        packets = self._encoder.encode(resampler.flush()) + self._encoder.flush()
        await self._send_frames(packets)

    async def _speak(self, sentence: str, resampler: Resampler, spoken: list[str]) -> None:
        assert self._providers is not None
        await self.websocket.send_text(
            messages.tts_message(self.session_id, "sentence_start", text=sentence)
        )
        spoken.append(sentence)
        async for chunk in self._providers.tts.synthesize(sentence):
            await self._send_frames(self._encoder.encode(resampler.process(chunk)))

    async def _send_frames(self, packets: list[bytes]) -> None:
        """Send Opus frames paced at the frame cadence, so a long reply
        cannot flood the device's playback queue. The clock starts at the
        first frame of the reply, not at ASR time."""
        if not packets:
            return
        loop = asyncio.get_running_loop()
        frame_s = OUTPUT_AUDIO.frame_duration / 1000
        if self._pace_start is None:
            self._pace_start = loop.time()
        for packet in packets:
            await asyncio.sleep(self._pace_start + self._pace_count * frame_s - loop.time())
            await self.websocket.send_bytes(framing.wrap(self.protocol_version, packet))
            self._pace_count += 1

    async def _close(self, code: int, reason: str) -> None:
        with contextlib.suppress(RuntimeError):
            await self.websocket.close(code=code, reason=reason)
