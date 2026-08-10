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
which is what barge-in is. An endpointer-driven cancel is gated: a
reply is only cancelled on evidence of user speech (enough classified
speech, a transcript when in doubt), because acoustics alone are as
often noise or the reply's own bleed as the user (#28). A manual
`listen stop` mid-reply is a deliberate act and cancels
unconditionally. `server.barge_in` turns all of it off for a board
whose echo cancellation leaks its own voice back: those frames are
then dropped, and the conversation stays multi-turn regardless.

What happens in a conversation is logged twice over: as a human
sentence, and as structured `extra=` fields (`event`, `session`,
`device`, and whatever the event carries) that the JSON log format
emits as top-level keys. Retained JSON logs are therefore the
transcript store until v3 brings a real one.
"""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any

import av
from starlette.websockets import WebSocket, WebSocketDisconnect

from samtal_server import __version__
from samtal_server.audio.opus import OpusDecoder, OpusEncoder
from samtal_server.audio.resample import Resampler
from samtal_server.build_info import revision
from samtal_server.capture import (
    CAPTURE_RATE,
    CaptureStore,
    DeviceFacts,
    SessionCapture,
)
from samtal_server.config import Config
from samtal_server.config.models import normalize_mac
from samtal_server.device.boundary import PIPELINE_SAMPLE_RATE
from samtal_server.device.events import SessionEvents, logger
from samtal_server.filler import FillerClips
from samtal_server.protocol import framing, messages
from samtal_server.protocol import mcp as mcp_protocol
from samtal_server.providers import AgentProviders, AsrResult, Endpointer
from samtal_server.runtime.pipeline import PipelineRuntime
from samtal_server.tools.device import DeviceToolClient
from samtal_server.tools.mcp import McpServers
from samtal_server.tools.memory import MemoryStore

# How long to wait for the device hello; the firmware gives the server
# hello the same ten seconds.
HELLO_TIMEOUT_S = 10.0

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

class Session:
    """The server side of one device connection."""

    def __init__(
        self,
        websocket: WebSocket,
        config: Config,
        agent_providers: dict[str, AgentProviders],
        mcp_servers: McpServers | None = None,
        memory: MemoryStore | None = None,
        captures: CaptureStore | None = None,
        device_facts: DeviceFacts | None = None,
        fillers: dict[str, FillerClips] | None = None,
    ) -> None:
        self.websocket = websocket
        self.config = config
        self._captures = captures
        self._device_facts = device_facts if device_facts is not None else DeviceFacts()
        self._capture: SessionCapture | None = None
        # Built only when a capture starts, so a server that is not
        # recording pays for none of this.
        self._capture_decoder: OpusDecoder | None = None
        self._capture_reply_decoder: OpusDecoder | None = None
        self._capture_resampler: Resampler | None = None
        self._mcp_servers = mcp_servers if mcp_servers is not None else McpServers({})
        self.session_id = uuid.uuid4().hex
        # Created at construction with the session id and no device
        # identity yet, so the bad-Device-Id rejection carries
        # `device: None` the way it does today; the edge writes the MAC
        # onto it as soon as one is understood.
        self._events = SessionEvents(self.session_id)
        # The conversation behind this connection. Built here while the
        # extraction is in flight; the wiring commit hands the session a
        # factory instead, so the edge stops naming the providers, the
        # MCP servers and the memory store at all.
        self.runtime = PipelineRuntime(
            self,
            config,
            self._events,
            agent_providers,
            self._mcp_servers,
            memory,
        )
        self.protocol_version = 1
        self.listening = False
        # The mode the last `listen start` asked for, kept because it
        # decides who re-arms the listening after an utterance: the
        # device, or nobody.
        self._listen_mode: str | None = None
        self._opened_at: float | None = None
        # Which agents exist at all, for the binding check below. The
        # runtime owns the providers themselves; the edge only needs to
        # know that a bound agent was actually built.
        self._agent_providers = agent_providers
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
        self._reply_task: asyncio.Task[None] | None = None
        # The device's own tools, when it said it has any. Discovery runs
        # in the background, so an early utterance simply runs without
        # them rather than waiting.
        self._device_tools: DeviceToolClient | None = None
        self._discovery: asyncio.Task[None] | None = None
        # Outgoing frame pacing, reset per reply on the first frame.
        self._pace_start: float | None = None
        self._pace_count = 0
        # Whether this reply has sent any audio yet. Pacing restarts per
        # agent leg, so it cannot double as this flag: the event below
        # must fire once per reply, not once per handover.
        self._speaking_started = False
        # When this reply's first frame went out, for the barge-in
        # refractory gate and the barge_in event's speaking_ms.
        self._speaking_started_at: float | None = None
        # Whether this reply has told the device it is speaking. The
        # `tts start` it stands for is sent once per reply, and never
        # before there is something to say.
        self._tts_started = False
        # The PCM the reply task in flight was handed, held until its
        # ASR call returns. Still being set is the mid-ASR marker: a
        # barge-in landing then killed the head of the user's own
        # sentence, so this is also the merge source that reconstitutes
        # it in front of the continuation.
        self._reply_pcm: bytes | None = None
        # The frame pacer waits on this before each send. The
        # transcript-confirmation gate clears it to hold playback while
        # ASR decides whether anything was said; resuming shifts the
        # pacing clock by the pause, so the stream picks up where it
        # stopped instead of bursting to catch up.
        self._pace_resume = asyncio.Event()
        self._pace_resume.set()
        self._pace_paused_at: float | None = None
        # The pre-synthesized filler clips, keyed by agent; empty means
        # no agent masks its latency. One timer per turn, armed at the
        # transcription: `_filler_sounding` flips the moment it fires,
        # which is what lets the real reply's audio queue behind the
        # clip's tail rather than interleave with it, and the fire
        # counter is what rotates the phrase variants.
        self._fillers = fillers if fillers is not None else {}
        self._filler_task: asyncio.Task[None] | None = None
        self._filler_sounding = False
        self._filler_fires = 0
        # When this session last did any conversing, which is what the
        # idle timeout counts from. Set at the end of an utterance and at
        # the end of a reply, so "whichever is later" needs no
        # comparison: both just write the current time.
        self._last_activity: float | None = None
        self._idle_watchdog: asyncio.Task[None] | None = None

    @property
    def output_sample_rate(self) -> int:
        """The rate reply audio has to arrive at to be encoded. Part of
        the device-facing boundary: it is a fact about the wire format,
        and the runtime resamples its voices to it."""
        return int(OUTPUT_AUDIO.sample_rate)

    @property
    def _agents(self) -> list[str]:
        """The agents this device may talk to. Owned by the runtime,
        read here for the handshake log line and the capture manifest."""
        return self.runtime._agents

    @_agents.setter
    def _agents(self, agents: list[str]) -> None:
        self.runtime._agents = agents

    @property
    def _providers(self) -> AgentProviders | None:
        """The active agent's providers. The barge-in confirmation still
        runs ASR from the edge until the gate ladder moves."""
        return self.runtime._providers

    @_providers.setter
    def _providers(self, providers: AgentProviders | None) -> None:
        self.runtime._providers = providers

    @property
    def _asr_language(self) -> str | None:
        return self.runtime._asr_language

    @property
    def _mac(self) -> str | None:
        """The device's MAC, set before anything can reject the
        connection so a rejection names the device it turned away.
        Unknown until the handshake headers are read. It lives on the
        events object because every event carries it."""
        return self._events.device

    @_mac.setter
    def _mac(self, mac: str | None) -> None:
        self._events.device = mac

    @property
    def _agent(self) -> str | None:
        """The agent talking right now. It lives on the events object
        because both sides of the split attribute events to it, and
        they have to see the same activation at the same moment."""
        return self._events.agent

    @_agent.setter
    def _agent(self, name: str | None) -> None:
        self._events.agent = name

    @property
    def _realtime(self) -> bool:
        """Whether the device is streaming its mic continuously, which is
        what realtime mode means. It sends `listen start` once and never
        again, so listening that stops here stops for the rest of the
        session: this is the flag that keeps it on."""
        return self._listen_mode == "realtime"

    def _event(self, event: str, **fields: Any) -> dict[str, Any]:
        return self._events.event(event, **fields)

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
        self.runtime._activate_agent(agents[0])
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
        # Before the session_open event below, so that event is the first
        # line of the decision track rather than missing from it.
        self._start_capture(client_id)
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
                # The widest payoff for one field: the JSON logs already
                # ship to a collector, so every session from here on is
                # attributable to a build, not only the ones somebody
                # thought to investigate.
                revision=revision(),
            ),
        )
        self._start_device_discovery(hello)
        self._start_idle_watchdog()

        try:
            # The cap on a session's total life. The idle watchdog is
            # what ends an abandoned realtime conversation long before
            # this; what is left for the cap is the session that keeps
            # talking, and the auto-mode device the watchdog leaves
            # alone.
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
            await self._stop_idle_watchdog()
            await self._cancel_reply()
            await self._stop_device_discovery()
            logger.info(
                "session %s closed (device %s)",
                self.session_id,
                mac,
                extra=self._event("session_closed", duration_s=self._open_duration_s()),
            )
            # After session_closed, so it is the last line of the
            # decision track and the WAV header is patched with a length
            # covering everything.
            if self._capture is not None:
                self._events.detach_capture()
                self._capture.close()
                self._capture = None

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

    def _start_capture(self, client_id: str) -> None:
        """Begin recording this session, when a directory is configured."""
        if self._captures is None or self._opened_at is None:
            return
        self._capture = self._captures.open(
            self.session_id, self._opened_at, self._manifest(client_id)
        )
        if self._capture is None:
            return
        self._events.attach_capture(self._capture)
        self._capture_decoder = OpusDecoder(sample_rate=PIPELINE_SAMPLE_RATE)
        self._capture_reply_decoder = OpusDecoder(sample_rate=OUTPUT_AUDIO.sample_rate)
        self._capture_resampler = Resampler(OUTPUT_AUDIO.sample_rate, CAPTURE_RATE)

    def _manifest(self, client_id: str) -> dict[str, Any]:
        """What this capture was made against.

        A capture outlives the code that made it, so it has to carry
        enough to be interpreted later. The barge-in thresholds matter
        most: an old capture analysed after they change is misleading
        unless it states its own. The provider entries are recorded
        verbatim rather than as a hash, because the exact model string
        is the only handle on a hosted model whose behaviour changed
        without a version bump on this side. They hold environment
        variable names rather than secrets, which the config schema
        enforces.
        """
        server = self.config.server
        return {
            "session": self.session_id,
            "started_at": datetime.now(UTC).isoformat(),
            "server": {"version": __version__, "revision": revision()},
            "device": {
                "mac": self._mac,
                "client": client_id or None,
                # Reported at OTA check-in, not on this socket. Empty
                # when the device reached the websocket without checking
                # in first, which a restarted server also produces.
                **self._device_facts.get(self._mac),
            },
            "protocol": self.protocol_version,
            "agent": self._agent,
            "agents": list(self._agents),
            "providers": self._provider_manifest(),
            "audio": {
                "capture_rate": CAPTURE_RATE,
                "pipeline_rate": PIPELINE_SAMPLE_RATE,
                "output_rate": OUTPUT_AUDIO.sample_rate,
                "frame_duration_ms": OUTPUT_AUDIO.frame_duration,
            },
            "barge_in": {
                "enabled": server.barge_in,
                "min_speech_ms": server.barge_in_min_speech_ms,
                "refractory_ms": server.barge_in_refractory_ms,
                "utterance_pre_roll_ms": server.utterance_pre_roll_ms,
            },
        }

    def _provider_manifest(self) -> dict[str, Any]:
        if self._agent is None:
            return {}
        described: dict[str, Any] = {}
        for stage in ("llm", "asr", "tts", "vad"):
            name, _ = self.config.provider_for_agent(self._agent, stage)
            if name is None:
                continue
            entry = getattr(self.config.providers, stage).get(name)
            if entry is None:
                continue
            described[stage] = {"name": name, **entry.model_dump(exclude_none=True)}
        return described

    def _start_idle_watchdog(self) -> None:
        """Start the timer that hangs up on a conversation nobody is
        having any more."""
        self._mark_activity()
        self._idle_watchdog = asyncio.create_task(self._watch_for_idle())

    async def _stop_idle_watchdog(self) -> None:
        if self._idle_watchdog is not None:
            self._idle_watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_watchdog
            self._idle_watchdog = None

    def _mark_activity(self) -> None:
        """Record that the conversation is alive right now. Called at
        both ends the idle timeout counts from, so "the last utterance or
        the last reply, whichever is later" falls out of writing the
        current time at each rather than having to compare them."""
        self._last_activity = asyncio.get_running_loop().time()

    async def _watch_for_idle(self) -> None:
        """Close a realtime session that has stopped conversing.

        Nothing on the device side ends a realtime session: the firmware
        has no idle timeout, and its only closers are a button press,
        losing the network, and powering off. So a user who simply walks
        away leaves the mic streaming to the server, holding one of
        `max_sessions`, keeping the board out of the sleep mode that
        `CanEnterSleepMode` refuses while an audio channel is open, and
        running Opus decode and VAD over the silence, until the hour of
        `max_session_s` is up. This is the bound that makes that a
        couple of minutes instead (#20).

        Realtime only. An auto-mode device stops listening after each
        reply and re-arms per turn, so it is not streaming a room to
        anybody; realtime is the mode that asks once and then never
        stops. The mode is not known until the device sends its `listen
        start`, and it can in principle change, so this checks each time
        round rather than deciding once.

        Arriving audio is deliberately not activity. A realtime session
        streams continuously, silence included, so counting frames would
        mean the timer never fires, which is the bug. What counts is
        conversation: an utterance ending, or a reply ending. A reply
        still streaming counts too, and not because it would otherwise
        be cut off (`request_shutdown` waits politely for one to finish
        speaking) but because of what follows: a timer that came due
        mid-reply has already decided to hang up, so the socket would
        close the instant the reply ended and the user would get no
        window at all to answer what they just heard.
        """
        timeout = self.config.server.limits.idle_timeout_s
        loop = asyncio.get_running_loop()
        while True:
            now = loop.time()
            if not self._realtime or self._replying():
                self._mark_activity()
            assert self._last_activity is not None
            remaining = self._last_activity + timeout - now
            if remaining > 0:
                await asyncio.sleep(remaining)
                continue
            logger.info(
                "session %s idle for %.0f s, hanging up",
                self.session_id,
                timeout,
                extra=self._event(
                    "session_idle", idle_s=timeout, duration_s=self._open_duration_s()
                ),
            )
            # A normal closure rather than going away: the server is
            # fine, this conversation is simply over. The firmware reads
            # it as the end of one and reconnects on the next wake word.
            await self.request_shutdown(NORMAL_CLOSURE, "idle timeout")
            return

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
        # Before every guard below, deliberately. The guards drop frames
        # when the session is not listening and when barge-in is off
        # mid-reply, and those are precisely the frames that explain a
        # misfire, so a capture taken after them would be missing the
        # evidence it exists for (#42).
        self._capture_microphone(data)
        if not self.listening or self._endpointer is None:
            self._note_dropped("not_listening")
            return
        if not self.config.server.barge_in and self._replying():
            # Barge-in off: this is a board whose echo cancellation is
            # not trusted, so what arrives while the server speaks may be
            # the server. Dropped here, before the decode, and nothing
            # has to re-arm afterwards: the guard opens by itself when
            # the reply ends.
            self._note_dropped("barge_in_off")
            return
        try:
            frame = framing.unwrap(self.protocol_version, data)
        except framing.FramingError as exc:
            logger.warning("session %s: dropped binary frame: %s", self.session_id, exc)
            self._note_dropped("framing_error")
            return
        if frame.payload_type != framing.PAYLOAD_OPUS:
            self._note_dropped("not_opus")
            return
        try:
            pcm = self._decoder.decode(frame.payload)
        except av.FFmpegError as exc:
            logger.warning("session %s: undecodable Opus packet: %s", self.session_id, exc)
            self._note_dropped("undecodable")
            return
        self._utterance.extend(pcm)
        if len(self._utterance) > UTTERANCE_TAIL_BYTES:
            excess = len(self._utterance) - UTTERANCE_TAIL_BYTES
            del self._utterance[:excess]
            self._utterance_dropped += excess
        endpointed = self._endpointer.feed(pcm)
        # After the feed, so the sample is the endpointer's opinion of
        # the audio just recorded rather than of the frame before it.
        self._capture_vad()
        if endpointed:
            await self._finish_utterance(endpointed=True)

    def _note_dropped(self, reason: str) -> None:
        self._events.dropped(reason)

    def _capture_vad(self) -> None:
        if self._endpointer is None:
            return
        self._events.vad(self._endpointer.speech_ms(), self.listening, self._replying())

    def _capture_microphone(self, data: bytes) -> None:
        """Decode a mic frame for the capture, whatever the session then
        does with it.

        Its own decoder, not the pipeline's: this one sees every frame
        while the pipeline's sees only the frames that got past the
        guards, and pushing the guarded frames through the pipeline
        decoder would change what the conversation hears."""
        if self._capture is None or self._capture_decoder is None:
            return
        try:
            frame = framing.unwrap(self.protocol_version, data)
            if frame.payload_type != framing.PAYLOAD_OPUS:
                return
            pcm = self._capture_decoder.decode(frame.payload)
        except (framing.FramingError, av.FFmpegError):
            # Already counted as dropped by the caller, and a frame the
            # capture cannot read is not a reason to stop capturing.
            return
        self._capture.microphone(pcm, asyncio.get_running_loop().time())

    def _capture_reply(self, packet: bytes) -> None:
        """Record a frame as it is paced out, which is what the speaker
        played rather than what was synthesized. Decoded back from the
        Opus that actually went to the device, and resampled to the
        capture rate so one sample index means one instant in both
        channels."""
        if (
            self._capture is None
            or self._capture_reply_decoder is None
            or self._capture_resampler is None
        ):
            return
        try:
            pcm = self._capture_reply_decoder.decode(packet)
        except av.FFmpegError:
            return
        self._capture.reply(
            self._capture_resampler.process(pcm), asyncio.get_running_loop().time()
        )

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
                # Asking to listen is a conversational act, and it is
                # also the moment this session can first become one the
                # idle timeout applies to. Without the mark, a session
                # that turns realtime late inherits whatever was left of
                # a window that was being extended for free while it was
                # not realtime, and can be hung up on seconds after the
                # user starts talking.
                self._mark_activity()
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

    async def _finish_utterance(self, endpointed: bool = False) -> None:
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
        for the task it cancelled. When the endpointer decided the end,
        the cancel first has to pass the gates in `_gate_barge_in`,
        because that decision is acoustic and acoustics mid-reply are as
        often noise or playback bleed as the user; a manual `listen
        stop` is the user holding the button and speaking, so it stays
        unconditional. With `server.barge_in` off the utterance is
        dropped instead, which is what a board with leaky echo
        cancellation wants; from the mic that case is already filtered
        in `_handle_audio`, so what reaches here is a manual `listen
        stop` mid-reply."""
        # Before any of the gates below can drop it: an utterance that
        # ended is somebody talking, whether or not it earns a reply.
        #
        # Today every path from here either starts a reply or leaves one
        # already running, and a reply marks again when it ends, so this
        # is always superseded and no test can tell it apart. It stays
        # because the rule the timeout is specified by names both ends,
        # and because the day an utterance stops implying a reply is not
        # a day anyone will remember this.
        self._mark_activity()
        speech_ms = round(self._endpointer.speech_ms()) if self._endpointer is not None else 0
        pcm = self._trimmed_utterance()
        self._reset_utterance()
        if not self._realtime:
            self.listening = False
        result: AsrResult | None = None
        if self._replying():
            if not self.config.server.barge_in:
                logger.warning(
                    "session %s: dropping an utterance, a reply is already streaming",
                    self.session_id,
                )
                return
            if endpointed:
                gated = await self._gate_barge_in(pcm, speech_ms)
                if gated is None:
                    return
                pcm, result = gated
            else:
                logger.info(
                    "session %s: barge-in, cancelling the reply in flight",
                    self.session_id,
                    extra=self._event(
                        "barge_in", speech_ms=speech_ms, **self._speaking_ms_field()
                    ),
                )
                await self._cancel_reply()
        logger.info(
            "session %s: utterance of %.1f s",
            self.session_id,
            len(pcm) / 2 / PIPELINE_SAMPLE_RATE,
        )
        self._reply_pcm = pcm if result is None else None
        self._reply_task = asyncio.create_task(self.runtime._reply(pcm, result))

    async def _gate_barge_in(
        self, pcm: bytes, speech_ms: int
    ) -> tuple[bytes, AsrResult | None] | None:
        """Decide what an endpointed utterance may do to the reply in
        flight: None to drop it and let the reply live, or the PCM to
        answer (with its transcription, when confirming it already ran
        ASR). The gates exist because a reply is only cancelled on
        evidence of user speech; acoustics alone can at most pause it
        (see the ADR of that name).

        In order: too little classified speech is a noise blip and is
        dropped; a reply still inside ASR was transcribing the head of
        the user's own sentence, so it is cancelled and its audio
        prepended, one reply answering the whole sentence; right after
        playback starts, the onset transient the device's echo
        cancellation lets through is dropped; anything else pauses the
        outgoing frames and asks ASR, and only a non-empty transcript
        cancels. An empty one resumes the paced stream where it
        stopped, so a wrong pause costs one ASR latency, not a reply."""
        server = self.config.server
        if speech_ms < server.barge_in_min_speech_ms:
            logger.info(
                "session %s: barge-in suppressed, %d ms of speech is under the "
                "%.0f ms floor",
                self.session_id,
                speech_ms,
                server.barge_in_min_speech_ms,
                extra=self._event(
                    "barge_in_suppressed", reason="min_speech", speech_ms=speech_ms
                ),
            )
            return None
        if self._reply_pcm is not None:
            head = self._reply_pcm
            logger.info(
                "session %s: barge-in mid-transcription, merging the utterances",
                self.session_id,
                extra=self._event("barge_in_merged", speech_ms=speech_ms),
            )
            await self._cancel_reply()
            return head + pcm, None
        loop = asyncio.get_running_loop()
        if (
            self._speaking_started_at is not None
            and (loop.time() - self._speaking_started_at) * 1000
            < server.barge_in_refractory_ms
        ):
            logger.info(
                "session %s: barge-in suppressed inside the refractory window",
                self.session_id,
                extra=self._event(
                    "barge_in_suppressed", reason="refractory", speech_ms=speech_ms
                ),
            )
            return None
        assert self._providers is not None
        self._pause_speaking()
        try:
            # In the receive path on purpose: incoming frames buffer in
            # the socket for the duration, so ordering is unaffected.
            async with self.runtime._watching("asr", self._providers.asr):
                result = await self._providers.asr.transcribe(
                    pcm, PIPELINE_SAMPLE_RATE, language_hint=self._asr_language
                )
        except Exception:
            logger.exception("session %s: barge-in confirmation failed", self.session_id)
            self._resume_speaking()
            return None
        if not result.text.strip():
            logger.info(
                "session %s: barge-in suppressed, nothing transcribed",
                self.session_id,
                extra=self._event(
                    "barge_in_suppressed", reason="no_transcript", speech_ms=speech_ms
                ),
            )
            self._resume_speaking()
            return None
        logger.info(
            "session %s: barge-in, cancelling the reply in flight",
            self.session_id,
            extra=self._event("barge_in", speech_ms=speech_ms, **self._speaking_ms_field()),
        )
        await self._cancel_reply()
        # The pause belonged to the cancelled reply; the one about to
        # answer starts with the frames flowing.
        self._pace_paused_at = None
        self._pace_resume.set()
        return pcm, result

    def _speaking_ms_field(self) -> dict[str, int]:
        """The barge_in event's speaking_ms: milliseconds from
        speaking_started to the cancel decision, absent when the reply
        had not yet spoken."""
        if self._speaking_started_at is None:
            return {}
        elapsed = asyncio.get_running_loop().time() - self._speaking_started_at
        return {"speaking_ms": round(elapsed * 1000)}

    def _pause_speaking(self) -> None:
        """Hold the outgoing frame pacing before the next send. Audio
        stops within a frame either way; what a pause preserves is the
        option of resuming."""
        if self._pace_paused_at is not None:
            return
        self._pace_paused_at = asyncio.get_running_loop().time()
        self._pace_resume.clear()

    def _resume_speaking(self) -> None:
        """Let the frames flow again, with the pacing clock shifted by
        the pause so the stream picks up where it stopped rather than
        bursting to catch up on the frames the pause displaced."""
        if self._pace_paused_at is None:
            return
        if self._pace_start is not None:
            self._pace_start += asyncio.get_running_loop().time() - self._pace_paused_at
        self._pace_paused_at = None
        self._pace_resume.set()

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

    async def _begin_speaking(self) -> None:
        """Tell the device a reply is starting, once per reply.

        Sent when the first sentence is about to be spoken rather than
        when transcription finished. It puts the device into its
        speaking state, and the state is what the display shows and
        what decides that a conversation-button press means "stop
        talking": sent before the model has answered, it makes the
        board claim to be speaking through the whole of a slow
        generation (#55)."""
        if self._tts_started:
            return
        self._tts_started = True
        await self.websocket.send_text(messages.tts_message(self.session_id, "start"))

    def _arm_filler(self) -> None:
        """Start this turn's latency mask, when any agent this session
        could become has one: a timer from the transcription that plays
        a cached filler clip if the reply's first audio has not started
        in time.

        Any bound agent, not just the active one, because a handover
        mid-turn can move the conversation to an agent with fillers
        before the reply first speaks: armed only for the starting
        agent, a filler-less receptionist handing over to a masked
        specialist would leave the specialist's slow greeting unmasked
        even though the fire-time lookup already resolves the active
        agent. The delay is the active agent's own where it has one,
        and the earliest configured among the bound agents otherwise;
        at fire time an active agent with no clip quietly plays
        nothing. A session bound only to filler-less agents still
        skips the timer entirely.

        Armed once per turn and never re-armed, so a first-token
        watchdog retry does not earn a second filler: the filler is the
        soft early threshold, the watchdog the hard late one, and a
        stalled round hears one "let me see" before the watchdog gives
        the round up."""
        reachable = [self._fillers[name] for name in self._agents if name in self._fillers]
        if not reachable:
            return
        own = self._fillers.get(self._agent or "")
        delay_ms = own.delay_ms if own is not None else min(c.delay_ms for c in reachable)
        self._filler_sounding = False
        armed_at = asyncio.get_running_loop().time()
        self._filler_task = asyncio.create_task(
            self._run_filler(delay_ms / 1000, armed_at)
        )

    async def _run_filler(self, delay_s: float, armed_at: float) -> None:
        """Wait out the delay, then mask the silence, unless the reply's
        first audio arrived first.

        The clip is chosen from the agent active at fire time, so a
        handover already made is spoken in the voice now talking, and
        an active agent with no clips of its own plays nothing,
        quietly: no event, no state, the turn proceeds unmasked. It
        goes out through the normal paced path: `_begin_speaking` moves
        the device into its speaking state (once per reply, so the real
        sentence that follows sends no second one), the frames land on
        capture channel 1, and `speaking_started` fires on the clip's
        first frame and counts as the turn's. No `sentence_start` is
        sent: the filler is a noise that buys time, not a sentence of
        the reply, and it stays out of the transcript everywhere.

        A device that went away mid-clip ends the clip, not the
        session; anything else unexpected is logged and swallowed,
        because a broken mask must never break the reply it masks.

        The mask yields to the user. A fire-time check skips the clip
        when the endpointer holds unresolved speech (the user is
        talking, or just trailed off into silence the endpointer has
        not yet resolved) and when a barge-in confirmation has the
        outgoing frames paused. Both mean the silence the timer set
        out to mask is not silence: the turn it would mask belongs to
        a premature endpoint, the reply in flight is about to be
        cancelled, and a clip played now talks over the user's own
        continuation. Field round 2 measured exactly this: 4 of 20
        fires landed 1.4 to 1.8 s into speech already underway, all
        in dictation-style turns. Skipped, not deferred: one filler
        per turn stays the rule, and the cancelled reply's successor
        arms its own timer."""
        await asyncio.sleep(delay_s)
        if self._speaking_started:
            return
        speech_ms = round(self._endpointer.speech_ms()) if self._endpointer is not None else 0
        if speech_ms > 0:
            logger.info(
                "session %s: filler skipped, the user is speaking (%d ms heard)",
                self.session_id,
                speech_ms,
                extra=self._event(
                    "filler_skipped",
                    agent=self._agent,
                    reason="user_speaking",
                    speech_ms=speech_ms,
                ),
            )
            return
        if self._pace_paused_at is not None:
            logger.info(
                "session %s: filler skipped, a barge-in is being confirmed",
                self.session_id,
                extra=self._event(
                    "filler_skipped", agent=self._agent, reason="barge_in_pending"
                ),
            )
            return
        clips = self._fillers.get(self._agent or "")
        if clips is None:
            return
        # Claimed synchronously between the checks above and the first
        # await below: from here `_filler_tail` waits for the clip's
        # tail instead of cancelling the timer.
        self._filler_sounding = True
        index = self._filler_fires % len(clips.clips)
        self._filler_fires += 1
        elapsed_ms = round((asyncio.get_running_loop().time() - armed_at) * 1000)
        logger.info(
            "session %s: no reply audio after %d ms, playing filler %d",
            self.session_id,
            elapsed_ms,
            index,
            extra=self._event(
                "filler_played",
                agent=self._agent,
                delay_ms=elapsed_ms,
                phrase_index=index,
            ),
        )
        try:
            await self._begin_speaking()
            resampler = Resampler(clips.sample_rate, OUTPUT_AUDIO.sample_rate)
            packets = (
                self._encoder.encode(resampler.process(clips.clips[index]))
                + self._encoder.encode(resampler.flush())
                + self._encoder.flush()
            )
            await self._send_frames(packets, from_filler=True)
        except (WebSocketDisconnect, RuntimeError):
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("session %s: filler playback failed", self.session_id)

    async def _filler_tail(self) -> None:
        """The reply's own audio is ready: an unfired timer loses (the
        silence it was going to mask is over), and a clip already
        sounding is waited out, so the first real sentence queues
        behind its tail rather than interleaving with it or cutting it
        mid-word."""
        task = self._filler_task
        if task is None or task.done():
            return
        if not self._filler_sounding:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _settle_filler(self) -> None:
        """End-of-reply cleanup, whatever path ended it: stand down an
        unfired timer, wait out a clip still sounding (a reply that
        failed silently still finishes its "let me see" before the
        closing tts stop), and see a cancellation through so nothing
        of this turn's filler outlives the turn."""
        task = self._filler_task
        self._filler_task = None
        if task is None:
            return
        if not self._filler_sounding:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self._filler_sounding = False

    async def _send_frames(self, packets: list[bytes], from_filler: bool = False) -> None:
        """Send Opus frames paced at the frame cadence, so a long reply
        cannot flood the device's playback queue. The clock starts at the
        first frame of the reply, not at ASR time.

        Reply audio first yields to a filler in flight (`_filler_tail`),
        so a clip that started sounding finishes and the reply queues
        behind its tail; the filler's own frames skip the gate, which is
        what keeps this from waiting on itself."""
        if not packets:
            return
        if not from_filler:
            await self._filler_tail()
        loop = asyncio.get_running_loop()
        if not self._speaking_started:
            # The `replied` event marks the last frame of a reply, so on
            # its own the logs cannot tell synthesis cost from speaking
            # time; this marks the first frame, making time-to-first-audio
            # measurable (#22).
            self._speaking_started = True
            self._speaking_started_at = loop.time()
            logger.info(
                "session %s: speaking started",
                self.session_id,
                extra=self._event("speaking_started", agent=self._agent),
            )
        frame_s = OUTPUT_AUDIO.frame_duration / 1000
        if self._pace_start is None:
            self._pace_start = loop.time()
        for packet in packets:
            await asyncio.sleep(self._pace_start + self._pace_count * frame_s - loop.time())
            # A barge-in being confirmed holds the stream here; resuming
            # shifts `_pace_start`, so the cadence survives the pause.
            await self._pace_resume.wait()
            await self.websocket.send_bytes(framing.wrap(self.protocol_version, packet))
            self._capture_reply(packet)
            self._pace_count += 1

    async def _close(self, code: int, reason: str) -> None:
        with contextlib.suppress(RuntimeError):
            await self.websocket.close(code=code, reason=reason)
