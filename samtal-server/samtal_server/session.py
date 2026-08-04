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

The reply is a tool loop, and the loop lives here rather than in a
provider because only the session can change agents between rounds.
Per reply it snapshots the tools the active agent may use, streams,
executes whatever the model asked for, feeds the results back, and
streams again, up to a small cap whose last round forbids calling so a
reply always ends in speech. History stays text-only: the structured
tool turns exist in a working copy inside one reply, and what survives
is what was actually said aloud.

Two end-of-utterance triggers coexist because the firmware's listening
modes differ: manual mode sends `listen stop`, while auto and realtime
modes stream mic audio until the server decides the user finished,
which is what the endpointer is for. The modes also differ in who
re-arms the listening: auto mode sends a fresh `listen start` after
each reply, while a realtime device asks once and then streams
continuously, so a realtime session here never stops listening. It
therefore hears the user through its own speech, and an utterance that
ends while a reply is streaming cancels that reply and is answered,
which is what barge-in is. `server.barge_in` turns that off for a board
whose echo cancellation leaks its own voice back: those frames are then
dropped, and the conversation stays multi-turn regardless.

What happens in a conversation is logged twice over: as a human
sentence, and as structured `extra=` fields (`event`, `session`,
`device`, and whatever the event carries) that the JSON log format
emits as top-level keys. Retained JSON logs are therefore the
transcript store until v3 brings a real one.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Sequence
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
from samtal_server.providers import (
    AgentProviders,
    Endpointer,
    TextDelta,
    ToolCall,
    ToolChoice,
    ToolDef,
    ToolResult,
    Turn,
)
from samtal_server.text import SentenceSplitter
from samtal_server.tools import builtin, names
from samtal_server.tools.device import DeviceToolClient
from samtal_server.tools.mcp import McpServers
from samtal_server.tools.memory import MemoryStore

logger = logging.getLogger(__name__)

# How long to wait for the device hello; the firmware gives the server
# hello the same ten seconds.
HELLO_TIMEOUT_S = 10.0

# The rate the input side of the pipeline runs at: what devices send,
# and what the endpointer and ASR are fed.
PIPELINE_SAMPLE_RATE = 16000

# How much recent mic audio the utterance buffer keeps. A realtime
# session listens through the silences too, so without a bound the
# buffer would grow for the whole session (about 115 MB at the one-hour
# cap). Well above the endpointer's 10 s `max_utterance_ms`, so what a
# trim can ever drop is silence nobody is going to transcribe.
UTTERANCE_TAIL_S = 30
UTTERANCE_TAIL_BYTES = UTTERANCE_TAIL_S * PIPELINE_SAMPLE_RATE * 2

# What the server speaks: TTS output is resampled to this rate, encoded
# in 60 ms Opus frames, and announced in the server hello.
OUTPUT_AUDIO = messages.AudioParams(
    format="opus", sample_rate=24000, channels=1, frame_duration=60
)

# Websocket close codes (RFC 6455): policy violation for who you are,
# protocol error for what you sent, going away for a server on its way
# out, and normal closure for an ordinary end, which is what the
# duration cap is.
POLICY_VIOLATION = 1008
PROTOCOL_ERROR = 1002
GOING_AWAY = 1001
NORMAL_CLOSURE = 1000

# How long a session being shut down waits for a reply that is already
# speaking. Long enough for a sentence to finish, short enough that a
# stuck provider does not hold up the process; the drain's own bound is
# stricter in practice.
SHUTDOWN_REPLY_GRACE_S = 10.0

# How many times one reply may stream, call tools, and stream again.
# The last permitted round forbids calling, so a reply always ends in
# speech rather than in a tool nobody hears the result of.
MAX_TOOL_ROUNDS = 4

# How long a builtin or a device tool may take. Server tools use their
# own entry's tool_timeout_s. The device hears silence meanwhile, which
# is why this is not generous.
DEFAULT_TOOL_TIMEOUT_S = 15.0

# The ephemeral user turn a newly switched-in agent is greeted with. It
# is never recorded in the history: it exists because both APIs need a
# fresh completion to end on a user turn, and writing it into `_turns`
# would falsify the transcript with words nobody said.
SWITCH_GREETING = (
    "You have just taken over this conversation from another assistant. "
    "Greet the user briefly as yourself, in the language they have been "
    "speaking, and carry on from what was said above."
)


class AgentNotAllowed(ValueError):
    """Something asked a session to become an agent its device is not
    bound to. The switch_agent tool turns this into a spoken refusal,
    phrased by the agent that is already talking; anywhere else it can
    only mean a bug."""


def _not_allowed(name: str, agents: Sequence[str]) -> AgentNotAllowed:
    """The refusal, built in one place because the model is shown the
    same text the enforcement raises."""
    return AgentNotAllowed(
        f'this device is not bound to agent "{name}"'
        + (f" (bound to: {', '.join(agents)})" if agents else "")
    )


class Session:
    """The server side of one device connection."""

    def __init__(
        self,
        websocket: WebSocket,
        config: Config,
        agent_providers: dict[str, AgentProviders],
        mcp_servers: McpServers | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        self.websocket = websocket
        self.config = config
        self._mcp_servers = mcp_servers if mcp_servers is not None else McpServers({})
        self._memory = memory
        self.session_id = uuid.uuid4().hex
        self.protocol_version = 1
        self.listening = False
        # The mode the last `listen start` asked for, kept because it
        # decides who re-arms the listening after an utterance: the
        # device, or nobody.
        self._listen_mode: str | None = None
        # The device's MAC, set before anything can reject the connection so
        # a rejection names the device it turned away. Unknown until the
        # handshake headers are read.
        self._mac: str | None = None
        self._opened_at: float | None = None
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
        # How much the tail cap has cut from the front of `_utterance`
        # since the last reset, which is what maps the endpointer's
        # speech-start offset (counted over everything fed) onto a
        # position in the buffer that remains.
        self._utterance_dropped = 0
        self._turns: list[Turn] = []
        self._reply_task: asyncio.Task[None] | None = None
        # The device's own tools, when it said it has any. Discovery runs
        # in the background, so an early utterance simply runs without
        # them rather than waiting.
        self._device_tools: DeviceToolClient | None = None
        self._discovery: asyncio.Task[None] | None = None
        # The language the ASR provider asked this session to reuse
        # (`AsrResult.lock_language`). Session-scoped on purpose: the
        # provider is shared between sessions and holds no per-session
        # state, and the speaker does not change on an agent switch.
        self._asr_language: str | None = None
        # Outgoing frame pacing, reset per reply on the first frame.
        self._pace_start: float | None = None
        self._pace_count = 0
        # Whether this reply has sent any audio yet. Pacing restarts per
        # agent leg, so it cannot double as this flag: the event below
        # must fire once per reply, not once per handover.
        self._speaking_started = False

    @property
    def _realtime(self) -> bool:
        """Whether the device is streaming its mic continuously, which is
        what realtime mode means. It sends `listen start` once and never
        again, so listening that stops here stops for the rest of the
        session: this is the flag that keeps it on."""
        return self._listen_mode == "realtime"

    def _event(self, event: str, **fields: Any) -> dict[str, Any]:
        """The structured half of a log line: what every conversation
        event carries, plus this event's own fields. Passed as `extra=`,
        so it is invisible in the text format and top-level keys in the
        JSON one."""
        return {
            "event": event,
            "session": self.session_id,
            "device": self._mac,
            **fields,
        }

    async def run(self) -> None:
        device_id = self.websocket.headers.get("device-id", "").strip()
        client_id = self.websocket.headers.get("client-id", "").strip()
        await self.websocket.accept()
        self._opened_at = asyncio.get_running_loop().time()

        try:
            mac = self._mac = normalize_mac(device_id)
        except ValueError as exc:
            logger.warning(
                "session %s rejected: Device-Id header: %s",
                self.session_id,
                exc,
                extra=self._event("session_rejected", reason="bad_device_id"),
            )
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
                extra=self._event("session_rejected", reason="no_agent"),
            )
            await self._close(POLICY_VIOLATION, "no agent is configured for this device")
            return
        self._agents = agents
        self._activate_agent(agents[0])
        # A server that was down at boot, or that dropped since, gets a
        # background reconnect now, so it is picked up by the time this
        # conversation needs it rather than at the next server restart.
        self._mcp_servers.revive(
            entry for agent in agents for entry in self.config.mcp_for_agent(agent)
        )

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
            extra=self._event(
                "session_open",
                client=client_id or None,
                agent=self._agent,
                agents=list(self._agents),
                protocol=self.protocol_version,
            ),
        )
        self._start_device_discovery(hello)

        try:
            # The cap on a session's total life, which is also what idles
            # one out: a device that stopped talking hours ago holds a
            # slot until this fires.
            async with asyncio.timeout(self.config.server.limits.max_session_s):
                await self._serve()
        except TimeoutError:
            logger.info(
                "session %s reached the %.0f s time limit",
                self.session_id,
                self.config.server.limits.max_session_s,
                extra=self._event("session_limit", duration_s=self._open_duration_s()),
            )
            # The firmware reads a close as the end of a conversation and
            # reconnects on the next wake word, so this is invisible in
            # normal use.
            await self.request_shutdown(NORMAL_CLOSURE, "session time limit reached")
        except WebSocketDisconnect:
            pass
        finally:
            await self._cancel_reply()
            await self._stop_device_discovery()
            logger.info(
                "session %s closed (device %s)",
                self.session_id,
                mac,
                extra=self._event("session_closed", duration_s=self._open_duration_s()),
            )

    async def request_shutdown(
        self,
        code: int = GOING_AWAY,
        reason: str = "server shutting down",
        grace_s: float = SHUTDOWN_REPLY_GRACE_S,
    ) -> bool:
        """End this session cleanly: let a reply that is already speaking
        finish its sentence, then close. Answers whether it did finish.

        The duration cap and the shutdown drain share this, so how a
        session is ended politely lives in one place. Cutting a reply off
        mid-word is what this exists to avoid: the device is speaking to
        somebody.

        `grace_s` is how long that is worth waiting for, and the caller
        decides it: the drain passes its own budget, so configuring
        `server.drain_s` actually lengthens what a reply is given. The
        default is for callers with no budget of their own, like the
        duration cap. A reply that outlasts the grace is abandoned rather
        than waited on, and the False that comes back is what lets the
        caller say so instead of reporting a clean drain.
        """
        finished = True
        reply = self._reply_task
        if reply is not None and not reply.done():
            # asyncio.wait rather than await: a reply that failed is a
            # reply that finished, and its exception is not this method's
            # to raise.
            done, _ = await asyncio.wait([reply], timeout=grace_s)
            finished = bool(done)
        await self._close(code, reason)
        return finished

    def _open_duration_s(self) -> float:
        """How long this session has been open, to one hundredth of a
        second. Zero before the socket was accepted."""
        if self._opened_at is None:
            return 0.0
        return round(asyncio.get_running_loop().time() - self._opened_at, 2)

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
        once at connect, and again mid-reply when switch_agent hands the
        conversation over. The history carries across the switch: it is
        text-only, so nothing provider-specific leaks with it, and the
        new agent seeing what was said is what makes "switch to the
        tutor and explain what we just discussed" work.

        The device's bound list is enforced here rather than left to
        callers, because the next caller is a tool whose argument a model
        chose: an agent that merely exists is not one this device may
        talk to. Nothing is swapped when the name is refused, so the
        session keeps the agent it already had."""
        if name not in self._agents:
            raise _not_allowed(name, self._agents)
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
                await self._handle_audio(received["bytes"])
            elif received.get("text") is not None:
                await self._handle_text(received["text"])

    async def _handle_audio(self, data: bytes) -> None:
        if not self.listening or self._endpointer is None:
            return
        if not self.config.server.barge_in and self._replying():
            # Barge-in off: this is a board whose echo cancellation is
            # not trusted, so what arrives while the server speaks may be
            # the server. Dropped here, before the decode, and nothing
            # has to re-arm afterwards: the guard opens by itself when
            # the reply ends.
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
        if len(self._utterance) > UTTERANCE_TAIL_BYTES:
            excess = len(self._utterance) - UTTERANCE_TAIL_BYTES
            del self._utterance[:excess]
            self._utterance_dropped += excess
        if self._endpointer.feed(pcm):
            await self._finish_utterance()

    async def _handle_text(self, text: str) -> None:
        try:
            message = messages.parse_message(text)
        except messages.ProtocolError as exc:
            logger.warning("session %s: ignored message: %s", self.session_id, exc)
            return

        match message:
            case messages.ListenMessage(state="start", mode=mode):
                # At info: which mode a board asks for decides how the
                # rest of the session behaves, and diagnosing that from
                # the logs should not take turning DEBUG on.
                logger.info("session %s: listening (%s mode)", self.session_id, mode)
                self._listen_mode = mode
                self.listening = True
                self._reset_utterance()
            case messages.ListenMessage(state="stop"):
                self.listening = False
                if self._utterance:
                    await self._finish_utterance()
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

    async def _finish_utterance(self) -> None:
        """Hand the buffered utterance to the reply task. Listening then
        stops until the device asks again, which auto mode does by
        sending `listen start` after the reply's `tts stop`. Not in
        realtime mode: that device asked once and is still streaming, so
        stopping here would leave nobody to re-arm it and the session
        would answer one utterance and go deaf.

        An utterance that ends while a reply is still streaming is the
        user cutting in, so the reply in flight is cancelled and this one
        answered instead. Cancelling sends the old reply's `tts stop`
        before the new reply's `tts start`, because `_cancel_reply` waits
        for the task it cancelled. With `server.barge_in` off the
        utterance is dropped instead, which is what a board with leaky
        echo cancellation wants; from the mic that case is already
        filtered in `_handle_audio`, so what reaches here is a manual
        `listen stop` mid-reply."""
        pcm = self._trimmed_utterance()
        self._reset_utterance()
        if not self._realtime:
            self.listening = False
        if self._replying():
            if not self.config.server.barge_in:
                logger.warning(
                    "session %s: dropping an utterance, a reply is already streaming",
                    self.session_id,
                )
                return
            # From the mic this is realtime mode only, where the device
            # streams through playback: it asks for that mode exactly
            # when its echo cancellation is on, so what arrived is the
            # user's voice and not the assistant's.
            logger.info(
                "session %s: barge-in, cancelling the reply in flight",
                self.session_id,
                extra=self._event("barge_in"),
            )
            await self._cancel_reply()
        logger.info(
            "session %s: utterance of %.1f s",
            self.session_id,
            len(pcm) / 2 / PIPELINE_SAMPLE_RATE,
        )
        self._reply_task = asyncio.create_task(self._reply(pcm))

    def _replying(self) -> bool:
        """Whether a reply is streaming right now, which is what both
        halves of the barge-in decision turn on."""
        return self._reply_task is not None and not self._reply_task.done()

    def _trimmed_utterance(self) -> bytes:
        """The buffered utterance, cut down to the speech plus a short
        pre-roll. A continuously listening session buffers everything
        between utterances (the reply's own playback time, the pause
        while the user thinks), and the endpointer rightly ignores that
        silence, so it would otherwise all ride along to ASR (#14). The
        pre-roll keeps the first phoneme intact; the trailing silence
        the endpointer sat through stays, since it is bounded and ASR
        needs the end of the speech anyway."""
        speech_start = self._endpointer.speech_start() if self._endpointer is not None else None
        if speech_start is None:
            return bytes(self._utterance)
        pre_roll = int(self.config.server.utterance_pre_roll_ms / 1000 * PIPELINE_SAMPLE_RATE) * 2
        start = speech_start - self._utterance_dropped - pre_roll
        if start <= 0:
            return bytes(self._utterance)
        start -= start % 2  # never split a 16-bit sample
        return bytes(self._utterance[start:])

    def _reset_utterance(self) -> None:
        self._utterance.clear()
        self._utterance_dropped = 0
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
        self._speaking_started = False
        heard_s = round(len(pcm) / 2 / PIPELINE_SAMPLE_RATE, 2)
        try:
            result = await providers.asr.transcribe(
                pcm, PIPELINE_SAMPLE_RATE, language_hint=self._asr_language
            )
            if result.lock_language is not None:
                self._asr_language = result.lock_language
            transcript = result.text.strip()
            if transcript:
                await self.websocket.send_text(messages.stt_message(self.session_id, transcript))
                # Only engines that detected carry these; a mock or a
                # pinned language adds no noise to the record.
                language_fields: dict[str, Any] = {}
                if result.language is not None:
                    language_fields["language"] = result.language
                if result.language_confidence is not None:
                    language_fields["language_confidence"] = round(
                        result.language_confidence, 2
                    )
                logger.info(
                    'session %s: heard "%s"',
                    self.session_id,
                    transcript,
                    extra=self._event(
                        "heard",
                        agent=self._agent,
                        text=transcript,
                        duration_s=heard_s,
                        **language_fields,
                    ),
                )
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
                said = " ".join(spoken)
                self._turns.append(Turn("assistant", said))
                logger.info(
                    'session %s: replied "%s"',
                    self.session_id,
                    said,
                    extra=self._event("replied", agent=self._agent, text=said),
                )
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await self.websocket.send_text(messages.tts_message(self.session_id, "stop"))

    async def _speak_reply(self, transcript: str, spoken: list[str]) -> None:
        """One reply, which may be spoken by more than one agent.

        `spoken` collects sentences as their audio goes out, so an abort
        or a barge-in leaves the history holding exactly the part of the
        reply the user heard, sentence by sentence. A successful
        switch_agent ends the current agent's loop: what it said so far
        becomes its own assistant turn, the new agent is activated, and
        a fresh loop runs as that agent, so the greeting arrives in the
        new prompt and the new voice. At most one handover per reply, so
        two agents cannot ping-pong."""
        switches_left = 1
        greeting: Turn | None = None
        while True:
            target = await self._tool_loop(spoken, greeting, switches_left)
            if target is None:
                return
            if spoken:
                said = " ".join(spoken)
                self._turns.append(Turn("assistant", said))
                logger.info(
                    'session %s: %s said "%s"',
                    self.session_id,
                    self._agent,
                    said,
                    extra=self._event("agent_said", agent=self._agent, text=said),
                )
                spoken.clear()
            previous = self._agent
            self._activate_agent(target)
            switches_left -= 1
            logger.info(
                "session %s: handed over from agent %s to %s",
                self.session_id,
                previous,
                target,
                extra=self._event("handover", from_agent=previous, to_agent=target),
            )
            greeting = Turn("user", SWITCH_GREETING)

    async def _tool_loop(
        self, spoken: list[str], greeting: Turn | None, switches_left: int
    ) -> str | None:
        """Stream, run whatever tools the model asked for, and stream
        again, up to the round cap. Returns the agent to hand over to,
        or None when the reply is finished.

        The tool snapshot and the resampler are taken here rather than
        per round, because they belong to the agent speaking; the next
        agent gets its own."""
        assert self._providers is not None
        providers = self._providers
        tools = self._tool_snapshot()
        working = list(self._turns)
        if greeting is not None:
            working.append(greeting)
        resampler = Resampler(providers.tts.sample_rate, OUTPUT_AUDIO.sample_rate)
        self._pace_start = None
        self._pace_count = 0

        switch_to: str | None = None
        for round_index in range(MAX_TOOL_ROUNDS):
            choice: ToolChoice = "none" if round_index == MAX_TOOL_ROUNDS - 1 else "auto"
            splitter = SentenceSplitter()
            leg: list[str] = []
            calls: list[ToolCall] = []
            async for event in providers.llm.stream(
                self._system_prompt(), working, tools, choice
            ):
                if isinstance(event, TextDelta):
                    for sentence in splitter.push(event.text):
                        await self._speak_and_record(sentence, resampler, leg, spoken)
                else:
                    calls.append(event)
            tail = splitter.flush()
            if tail is not None:
                await self._speak_and_record(tail, resampler, leg, spoken)
            if not calls:
                break
            # Whatever preamble was spoken before the calls is part of
            # the assistant turn that asked for them.
            working.append(Turn("assistant", " ".join(leg), tool_calls=tuple(calls)))
            results, switch_to = await self._run_tools(calls, switches_left)
            if switch_to is not None:
                break
            working.append(Turn("tool", "", tool_results=tuple(results)))

        # Drain the resampler's interpolation tail and the encoder's
        # partial frame, which flushing pads with silence.
        packets = self._encoder.encode(resampler.flush()) + self._encoder.flush()
        await self._send_frames(packets)
        return switch_to

    def _tool_snapshot(self) -> list[ToolDef]:
        """What the active agent may reach this reply: the builtins that
        apply, the device's tools once discovery has finished, and the
        tools of the MCP servers its configuration names that are up.

        Taken per reply rather than per session, so a server that came
        back and a device that finished discovering are both picked up
        on the next utterance."""
        assert self._agent is not None
        tools: list[ToolDef] = []
        # A device bound to one agent has nowhere to switch, so it gets
        # no dead tool.
        if len(self._agents) > 1:
            tools.append(builtin.switch_agent_tool(self._agents))
        if self._memory is not None:
            tools.append(builtin.remember_tool())
        if self._device_tools is not None:
            tools.extend(self._device_tools.tools())
        tools.extend(self._mcp_servers.tools_for(self.config.mcp_for_agent(self._agent)))
        return tools

    async def _run_tools(
        self, calls: Sequence[ToolCall], switches_left: int
    ) -> tuple[list[ToolResult], str | None]:
        """Execute one round of calls. Everything but switch_agent runs
        concurrently, since device and server tools are independent;
        switch_agent is resolved here instead, because a successful one
        ends the loop rather than producing a result the model reads."""
        plain = [call for call in calls if call.name != names.SWITCH_AGENT]
        handovers = [call for call in calls if call.name == names.SWITCH_AGENT]
        results = list(await asyncio.gather(*(self._run_one(call) for call in plain)))

        switch_to: str | None = None
        for position, call in enumerate(handovers):
            refusal = self._refuse_handover(call, switches_left, position)
            if refusal is not None:
                results.append(refusal)
                continue
            switch_to = str(call.arguments["agent"])
        return results, switch_to

    def _refuse_handover(
        self, call: ToolCall, switches_left: int, position: int
    ) -> ToolResult | None:
        """Why this switch_agent cannot happen, as an error result the
        current agent phrases in its own voice and language, or None
        when it can."""
        if switches_left <= 0 or position > 0:
            return ToolResult(
                call.id,
                "this conversation has already been handed over once in this reply; "
                "answer as yourself instead",
                is_error=True,
            )
        target = call.arguments.get("agent")
        if not isinstance(target, str) or not target.strip():
            return ToolResult(
                call.id,
                'switch_agent needs an "agent" argument naming one of the available '
                f"assistants: {', '.join(self._agents)}",
                is_error=True,
            )
        if target not in self._agents:
            return ToolResult(call.id, str(_not_allowed(target, self._agents)), is_error=True)
        return None

    async def _run_one(self, call: ToolCall) -> ToolResult:
        """One tool call, bounded and never raising into the loop. Every
        failure becomes an error result: the model explains it in its
        own words, where a canned apology would be fixed-language and
        would throw away whatever the model could still salvage."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            async with asyncio.timeout(self._timeout_for(call.name)):
                content, is_error = await self._dispatch(call)
        except TimeoutError:
            content, is_error = f'the tool "{call.name}" did not answer in time', True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            content, is_error = f'the tool "{call.name}" failed: {exc}', True
        elapsed = loop.time() - started
        logger.info(
            "session %s: tool %s took %.2f s%s",
            self.session_id,
            call.name,
            elapsed,
            " and failed" if is_error else "",
            extra=self._event(
                "tool_call",
                agent=self._agent,
                tool=call.name,
                duration_ms=round(elapsed * 1000),
                is_error=is_error,
            ),
        )
        return ToolResult(tool_call_id=call.id, content=content, is_error=is_error)

    async def _dispatch(self, call: ToolCall) -> tuple[str, bool]:
        """Route a call by the structure of its name: builtins are bare,
        the device's tools are the ones it listed, and everything else
        carries its MCP server entry as a prefix."""
        if call.malformed_arguments is not None:
            logger.warning(
                "session %s: tool %s got unparseable arguments: %s",
                self.session_id,
                call.name,
                call.malformed_arguments,
            )
            return "the arguments were not a JSON object; call again with valid ones", True
        if call.name == names.REMEMBER and self._memory is not None:
            assert self._agent is not None
            return await builtin.remember(self._memory, self._agent, call.arguments), False
        if self._device_tools is not None and self._device_tools.knows(call.name):
            return await self._device_tools.call(call.name, call.arguments)
        split = names.split_qualified(call.name)
        if split is not None and split[0] in self._mcp_servers:
            return await self._mcp_servers.call(call.name, call.arguments)
        return f'there is no tool called "{call.name}"', True

    def _timeout_for(self, name: str) -> float:
        """A server tool gets its entry's configured timeout; builtins
        and device tools the module default."""
        split = names.split_qualified(name)
        if split is not None:
            configured = self._mcp_servers.timeout_for(split[0])
            if configured is not None:
                return configured
        return DEFAULT_TOOL_TIMEOUT_S

    def _system_prompt(self) -> str:
        """The active agent's prompt, plus whatever it remembers."""
        assert self._providers is not None and self._agent is not None
        return builtin.with_memory(self._providers.prompt, self._memory, self._agent)

    async def _speak(self, sentence: str, resampler: Resampler, spoken: list[str]) -> None:
        """Say one sentence, and count it as said only once its audio has
        gone out.

        The order is the point. Frames are paced, so sending a sentence
        takes about as long as hearing it, and a barge-in cancels this
        coroutine somewhere in the middle of that. Counted first, a
        sentence the user heard two frames of would go into the turn the
        round hands the model as its own preamble."""
        assert self._providers is not None
        await self.websocket.send_text(
            messages.tts_message(self.session_id, "sentence_start", text=sentence)
        )
        async for chunk in self._providers.tts.synthesize(sentence):
            await self._send_frames(self._encoder.encode(resampler.process(chunk)))
        spoken.append(sentence)

    async def _speak_and_record(
        self, sentence: str, resampler: Resampler, leg: list[str], spoken: list[str]
    ) -> None:
        """Say a sentence and count it in both places at once: the
        round's own list, which becomes the turn the model is shown, and
        the reply's, which becomes the history.

        One call rather than two lists merged at the end of the round,
        because a barge-in cancels mid-round: merging later loses every
        sentence of that round, including the ones the user sat through
        and answered. Whoever speaks next then has no idea what was
        already said."""
        await self._speak(sentence, resampler, leg)
        spoken.append(sentence)

    async def _send_frames(self, packets: list[bytes]) -> None:
        """Send Opus frames paced at the frame cadence, so a long reply
        cannot flood the device's playback queue. The clock starts at the
        first frame of the reply, not at ASR time."""
        if not packets:
            return
        if not self._speaking_started:
            # The `replied` event marks the last frame of a reply, so on
            # its own the logs cannot tell synthesis cost from speaking
            # time; this marks the first frame, making time-to-first-audio
            # measurable (#22).
            self._speaking_started = True
            logger.info(
                "session %s: speaking started",
                self.session_id,
                extra=self._event("speaking_started", agent=self._agent),
            )
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
