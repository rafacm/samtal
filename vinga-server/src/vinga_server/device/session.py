"""One device connection: the xiaozhi edge, and nothing behind it.

The session owns the handshake, the wire, and the appliance. It accepts
the socket, checks the device's identity and its agent binding, exchanges
hellos, decodes mic Opus, encodes and paces reply Opus, frames both,
carries the device's own MCP tool transport, records the capture,
enforces the session limits and the idle timeout, and closes politely.

What is said in the conversation it does not own. Behind it sits one
conversation runtime, built for this connection by the factory the
composition root handed in, reached only through the two protocols in
[`boundary`](boundary.py): this class is the `DeviceOutput` the runtime
speaks through, and the runtime is the `SessionInput` this class feeds.
The runtime is built after the device's agents resolve and before the
hello, which is where the first agent used to be activated by hand; a
connection turned away before that point never has one.

Two end-of-utterance triggers coexist because the firmware's listening
modes differ: manual mode sends `listen stop`, while auto and realtime
modes stream mic audio until the runtime decides the user finished. The
modes also differ in who re-arms the listening, and that is the one
turn-taking fact this side keeps: `user_turn_ended` stops the listening
in auto mode, where the device sends a fresh `listen start` after each
reply, and leaves it alone in realtime, where the device asked once and
is still streaming. A realtime session therefore hears the user through
its own speech, which is what makes barge-in possible; what an
interruption then does to the reply is the runtime's decision.
`server.barge_in` turns all of it off for a board whose echo
cancellation leaks its own voice back: those frames are dropped here,
before the decode, so the capture still records the evidence.

What happens in a conversation is logged twice over: as a human
sentence, and as structured fields (`event`, `session`, `device`, and
whatever the event carries) that the JSON log format emits as top-level
keys. Both are metadata: what was said is recorded in the conversation
store instead, under the same session id (#120). Both sides emit
through the session's `SessionEvents` ([events](../events/__init__.py)), so which
module a line came from is not visible in the record, and every consumer
attached to the session sees the same events.
"""

import asyncio
import contextlib
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import av
from starlette.websockets import WebSocket, WebSocketDisconnect

from vinga_server import __version__
from vinga_server.audio.opus import OpusDecoder, OpusEncoder
from vinga_server.audio.resample import Resampler
from vinga_server.build_info import revision
from vinga_server.capture import (
    CAPTURE_RATE,
    CaptureStore,
    DeviceFacts,
    SessionCapture,
)
from vinga_server.config.models import (
    CLIENT_ID_LIMIT,
    ServerConfig,
    bounded_descriptor,
    normalize_mac,
)
from vinga_server.config.views import provider_record
from vinga_server.conversations import ConversationStore, SessionSink
from vinga_server.device.bindings import DeviceBindings
from vinga_server.device.boundary import (
    PIPELINE_SAMPLE_RATE,
    DeviceGone,
    PlayableAudio,
    RuntimeFactory,
    SessionInput,
)
from vinga_server.events import SessionEvents, logger
from vinga_server.events.catalog import (
    RejectedAgentNotLoaded,
    RejectedBadDeviceId,
    RejectedNoAgent,
    SessionClosed,
    SessionIdle,
    SessionLimit,
    SessionOpen,
    SpeakingStarted,
)
from vinga_server.events.values import (
    AgentList,
    AgentNames,
    AlsoBoundTo,
    ClientId,
    CloseReasonToken,
    DeviceId,
    Identifier,
    Real,
    Whole,
)
from vinga_server.generation import Generations
from vinga_server.protocol import framing, messages
from vinga_server.protocol import mcp as mcp_protocol
from vinga_server.providers.base import ToolDef
from vinga_server.tools.device import DeviceToolClient

# How long to wait for the device hello; the firmware gives the server
# hello the same ten seconds.
HELLO_TIMEOUT_S = 10.0

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

# What ended a session: the token `session_closed` carries, and the one
# the conversation store copies to `sessions.close_reason`. A closed set,
# decided where the code already decides (the duration cap, the idle
# watchdog, the shutdown drain, the ordinary end, and anything else on
# its way out through the close path), never derived from a message.
#
# First cause wins. The token is latched exactly once, by whichever
# termination fires first, so a cause arriving into a close already under
# way (an idle timeout coming due while a drain closes the same session,
# a client disconnect surfacing behind either) leaves the recorded reason
# the one that initiated it.
CLOSE_REASONS = ("limit", "idle", "drain", "client", "error")

# What a session that nothing decided to end is closed for: the device
# closed the socket, or the serve loop simply returned. Latched where
# that happens rather than synthesized at the end, so a drain arriving
# into a close already under way cannot take a cause that was decided
# before it; rendering it in the `finally` as well is the backstop for a
# path that reaches the close having latched nothing at all.
DEFAULT_CLOSE_REASON = "client"

class DeviceSession:
    """The server side of one device connection: everything that would
    still exist if the backend were a telephone call to a human.

    It implements the `DeviceOutput` half of the boundary, and feeds the
    `SessionInput` half a runtime built for this connection by the
    factory the composition root handed it."""

    def __init__(
        self,
        websocket: WebSocket,
        generations: Generations,
        runtime_factory: RuntimeFactory,
        captures: CaptureStore | None = None,
        device_facts: DeviceFacts | None = None,
        bindings: DeviceBindings | None = None,
        conversations: ConversationStore | None = None,
    ) -> None:
        self.websocket = websocket
        # The world this server is serving, asked rather than kept: a
        # reload replaces it while sessions are open, and what this
        # class reads out of it is read where it is needed (#191). The
        # file half rides along inside it and never moves, which is what
        # lets the limits below come through the same door as the
        # provider entries a manifest records.
        self._generations = generations
        # The file half, taken once because it is the one half a
        # generation never replaces: a reload composes the stored domain
        # half onto this process's own server section, so every
        # generation carries the same one.
        self._server: ServerConfig = generations.current().config.server
        # Which agents this device may talk to, asked at connect. A
        # caller that has no live view (a test with a configuration in
        # hand and no database behind it) gets the snapshot-only one,
        # which resolves what the configuration says: one resolution
        # path, rather than a live one and a fallback one that could
        # come to disagree about a rule they both implement.
        self._bindings = (
            bindings
            if bindings is not None
            else DeviceBindings.snapshot_only(generations.current().config)
        )
        self._captures = captures
        # The conversation record, an optional collaborator exactly like
        # the captures above: absent unless the deployment asked for one,
        # compared `is not None`, and never something this class reaches
        # for on a path that has to work without it.
        self._conversations = conversations
        self._record: SessionSink | None = None
        self._device_facts = device_facts if device_facts is not None else DeviceFacts()
        self._capture: SessionCapture | None = None
        # Built only when a capture starts, so a server that is not
        # recording pays for none of this.
        self._capture_decoder: OpusDecoder | None = None
        self._capture_reply_decoder: OpusDecoder | None = None
        self._capture_resampler: Resampler | None = None
        self.session_id = uuid.uuid4().hex
        # Created at construction with the session id and no device
        # identity yet, so the bad-Device-Id rejection carries
        # `device: None` the way it does today; the edge writes the MAC
        # onto it as soon as one is understood.
        self._events = SessionEvents(self.session_id)
        # The conversation behind this connection, built once the device
        # has proved which agents it may talk to. Until then there is
        # nothing to build one for: the rejections in `run` happen
        # first, and no runtime ever exists on those paths.
        self._runtime_factory = runtime_factory
        self.runtime: SessionInput | None = None
        self.protocol_version = 1
        self.listening = False
        # The mode the last `listen start` asked for, kept because it
        # decides who re-arms the listening after an utterance: the
        # device, or nobody.
        self._listen_mode: str | None = None
        self._opened_at: float | None = None
        # The agents this device may talk to, resolved at connect and
        # kept for the handshake log line and the capture manifest.
        self._agents: list[str] = []
        self._decoder = OpusDecoder(sample_rate=PIPELINE_SAMPLE_RATE)
        self._encoder = OpusEncoder(
            sample_rate=OUTPUT_AUDIO.sample_rate,
            frame_duration_ms=OUTPUT_AUDIO.frame_duration,
        )
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
        # The frame pacer waits on this before each send. The
        # transcript-confirmation gate clears it to hold playback while
        # ASR decides whether anything was said; resuming shifts the
        # pacing clock by the pause, so the stream picks up where it
        # stopped instead of bursting to catch up.
        self._pace_resume = asyncio.Event()
        self._pace_resume.set()
        self._pace_paused_at: float | None = None
        # When this session last did any conversing, which is what the
        # idle timeout counts from. Set at the end of an utterance and at
        # the end of a reply, so "whichever is later" needs no
        # comparison: both just write the current time.
        self._last_activity: float | None = None
        self._idle_watchdog: asyncio.Task[None] | None = None
        # What ended this session, latched by the first termination to
        # fire and never overwritten. None until something decides, which
        # is what the ordinary end looks like from here.
        self._close_reason: str | None = None
        # A cancellation that arrived while the close was running, held
        # until the record has landed and re-raised then.
        self._cancelled: BaseException | None = None

    @property
    def output_sample_rate(self) -> int:
        """The rate reply audio has to arrive at to be encoded. Part of
        the device-facing boundary: it is a fact about the wire format,
        and the runtime resamples its voices to it."""
        return int(OUTPUT_AUDIO.sample_rate)

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

    def _replying(self) -> bool:
        """Whether the conversation behind this connection is mid-answer.
        A connection with no runtime yet is not."""
        return self.runtime is not None and self.runtime.replying()

    async def run(self) -> None:
        device_id = self.websocket.headers.get("device-id", "").strip()
        client_id = self.websocket.headers.get("client-id", "").strip()
        await self.websocket.accept()
        self._opened_at = asyncio.get_running_loop().time()

        try:
            mac = self._mac = normalize_mac(device_id)
        except ValueError:
            # One fixed sentence, carrying neither the header nor the
            # exception's message, which quotes it. What arrived in that
            # header is bytes an unauthenticated caller chose, and these
            # logs are the retained surface the observability ADR makes
            # them: a rejected value written into them is a value the
            # caller placed in an operator's retained logs. Nothing
            # diagnosable is lost, because nothing about the submitted
            # value was ever actionable: the reason token says which
            # rejection this is, `device` is null because none was
            # understood, and the sentence still says what the header
            # has to hold.
            self._events.emit(lambda: RejectedBadDeviceId())
            await self._close(POLICY_VIOLATION, "Device-Id must be the device MAC")
            return

        # Read from the live view rather than from the boot snapshot, so
        # a device bound while this server runs connects on its next
        # attempt; awaited off the event loop, because every other
        # conversation in this process is waiting on it. The view
        # filters to the agents boot loaded, the filter this line used
        # to apply itself: the registry builds exactly one entry per
        # `agents` key, so a name outside that set has no conversation
        # behind it.
        resolution = await self._bindings.resolve(mac)
        agents = list(resolution.agents)
        if not agents:
            if resolution.unloaded:
                self._events.emit(
                    lambda: RejectedAgentNotLoaded(
                        mac=DeviceId(mac),
                        unloaded=AgentList.of(tuple(resolution.unloaded)),
                    )
                )
            else:
                self._events.emit(lambda: RejectedNoAgent(mac=DeviceId(mac)))
            # One sentence for both: the difference is between two
            # things an operator does, and the device can act on
            # neither.
            await self._close(POLICY_VIOLATION, "no agent is configured for this device")
            return
        self._agents = agents
        # Where the first agent used to be activated by hand: the
        # runtime's constructor does that, and the MCP revive after it,
        # in that order, and spawns nothing.
        self.runtime = self._runtime_factory(self, self._events, agents)

        hello = await self._receive_hello()
        if hello is None:
            return
        self.protocol_version = hello.version
        # The server hello goes out before either consumer opens, and it
        # is the last thing that happens outside the guard below.
        #
        # Both halves of that matter. A device that vanishes here is a
        # connection that recorded nothing, rather than a capture nobody
        # closes and a session row nobody ends: the only way to promise
        # that a record which was started is always finished is for
        # nothing between the start and the `finally` to be able to fail
        # first. And the hello is the one step that has to precede the
        # opening, because everything after it is inside the guard.
        await self.websocket.send_text(messages.server_hello(self.session_id, OUTPUT_AUDIO))

        try:
            # Before the session_open event below, so that event is the
            # first line of the decision track rather than missing from
            # it, for the capture and for the store alike.
            #
            # One manifest, two consumers: the capture writes it beside
            # the audio and the store's session row is built from it,
            # which is what "manifest-shaped" means concretely. The
            # store attaches after the capture, so the dispatch order
            # stays capture first, store second, log last.
            manifest = self._manifest(client_id)
            self._start_capture(manifest)
            self._start_recording(manifest)
            # What the event says about the client id, which is not what
            # the manifest above says about it. The header is the device
            # UUID, unbounded and unvalidated: with device auth off
            # nothing has looked at it at all, and with it on the token
            # was verified against whatever string arrived. The manifest
            # keeps the header, because the capture and the conversation
            # store are the surfaces that hold what the device said;
            # the event carries a bounded copy, null where nothing
            # printable survived, and renders that same copy in its
            # sentence, since dropping a field would not un-render an
            # argument.
            said_client = bounded_descriptor(client_id, CLIENT_ID_LIMIT)
            self._events.emit(
                lambda: SessionOpen(
                    client=ClientId(said_client) if said_client else None,
                    agent=Identifier(self._agent),
                    agents=AgentNames(tuple(self._agents)),
                    protocol=Whole(self.protocol_version),
                    # The widest payoff for one field: the JSON logs
                    # already ship to a collector, so every session from
                    # here on is attributable to a build, not only the
                    # ones somebody thought to investigate.
                    revision=Identifier(revision()),
                    mac=DeviceId(mac),
                    said_client=ClientId(said_client or "unknown"),
                    bound_tail=AlsoBoundTo.of(tuple(self._agents[1:])),
                    sample_rate=Whole(hello.audio_params.sample_rate),
                    frame_ms=Whole(hello.audio_params.frame_duration),
                )
            )
            self._start_device_discovery(hello)
            self._start_idle_watchdog()
            # The cap on a session's total life. The idle watchdog is
            # what ends an abandoned realtime conversation long before
            # this; what is left for the cap is the session that keeps
            # talking, and the auto-mode device the watchdog leaves
            # alone.
            async with asyncio.timeout(self._server.limits.max_session_s):
                await self._serve()
            # The serve loop returned, which is the device having closed
            # the socket. Latched here rather than left to the render in
            # the `finally`: a drain reaching this session while its
            # close is already under way would otherwise take a cause
            # that was decided before the drain existed, which is
            # exactly what first-cause-wins is for.
            self._latch_close(DEFAULT_CLOSE_REASON)
        except TimeoutError:
            self._events.emit(
                lambda: SessionLimit(
                    duration_s=Real(self._open_duration_s()),
                    limit_s=Real(self._server.limits.max_session_s),
                )
            )
            # The firmware reads a close as the end of a conversation and
            # reconnects on the next wake word, so this is invisible in
            # normal use.
            await self.request_shutdown(
                NORMAL_CLOSURE, "session time limit reached", close_reason="limit"
            )
        except WebSocketDisconnect:
            # The same cause arriving as an exception rather than as a
            # return, and latched for the same reason.
            self._latch_close(DEFAULT_CLOSE_REASON)
        except BaseException:
            # Anything else is leaving through this frame, so the record
            # says the session ended in a failure rather than in a
            # conversation. Latched here and re-raised untouched: what
            # happened is the caller's to handle, and a cancellation on
            # the way out is as much "not an ordinary end" as an
            # exception is.
            self._latch_close("error")
            raise
        finally:
            # Each step guarded on its own, so an exception in one cannot
            # swallow the event, the store's close or the capture's
            # behind it. This is the close path's contract: it always
            # reaches the end.
            await self._cleanly("the idle watchdog", self._stop_idle_watchdog())
            if self.runtime is not None:
                await self._cleanly("the conversation", self.runtime.close())
            await self._cleanly("device tool discovery", self._stop_device_discovery())
            self._events.emit(
                lambda: SessionClosed(
                    duration_s=Real(self._open_duration_s()),
                    reason=CloseReasonToken(self._closed_reason()),
                    mac=DeviceId(mac),
                )
            )
            # Both after session_closed, so that event is the last line
            # of the decision track, the last row of the record, and the
            # WAV header is patched with a length covering everything.
            self._stop_recording()
            if self._capture is not None:
                self._events.detach_capture()
                self._capture.close()
                self._capture = None
            if self._cancelled is not None:
                # A cleanup step was cancelled, and now that the record
                # is complete the cancellation goes on its way: the
                # caller's task ends cancelled, as it asked, and nothing
                # of the close was lost to it.
                raise self._cancelled

    async def request_shutdown(
        self,
        code: int = GOING_AWAY,
        reason: str = "server shutting down",
        grace_s: float = SHUTDOWN_REPLY_GRACE_S,
        close_reason: str | None = None,
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

        `close_reason` is the caller's token from `CLOSE_REASONS`,
        latched here rather than at each call site, so it is recorded
        before anything begins closing and cannot be raced by a cause
        that arrives while the reply finishes. None is for a caller with
        no cause to name, which in production is nobody: the three that
        end a session this way (the cap, the idle watchdog, the drain)
        each name their own.
        """
        if close_reason is not None:
            self._latch_close(close_reason)
        finished = True if self.runtime is None else await self.runtime.drain(grace_s)
        await self._close(code, reason)
        return finished

    def _latch_close(self, reason: str) -> None:
        """Record what is ending this session, once.

        First cause wins, which is what makes the recorded reason
        deterministic rather than whichever site happened to run last: a
        drain that closes a session an idle timer was about to hang up on
        is a drain, and the idle timer coming due behind it says nothing
        about why the conversation ended.
        """
        if self._close_reason is None:
            self._close_reason = reason

    def _closed_reason(self) -> str:
        """The token `session_closed` carries. Nothing latched means
        nothing decided to end this session, which is the device closing
        the socket or the serve loop simply returning."""
        if self._close_reason is None:
            return DEFAULT_CLOSE_REASON
        return self._close_reason

    async def _cleanly(self, step: str, work: Any) -> None:
        """One cleanup step, guarded on its own.

        The close path has to reach `session_closed`, the conversation
        store's close and the capture's close whatever happened before
        it, so a step that raises is reported here and the next one runs.
        A failure also latches `error` if nothing else was latched: a
        session whose runtime would not close did not end in a
        conversation.

        Reported by class, and with the step named in this module's own
        words. An exception's message on the way out of a session is one
        of the places a provider's or a device's bytes could reach the
        retained surface, and which exception it was is not actionable
        anyway: what is actionable is that this step did not finish.
        """
        try:
            await work
        except asyncio.CancelledError as cancelled:
            # Not a failure of the step, and not something to swallow:
            # the task really is being cancelled and its caller is
            # entitled to see that. Held instead of either, so the steps
            # after it, the event, the store's close and the capture's
            # close all still run, and re-raised once they have. A
            # cancellation arriving here used to skip every one of them.
            self._cancelled = cancelled
        except Exception as exc:  # noqa: BLE001 - a close always completes
            self._latch_close("error")
            logger.warning(
                "session %s: %s did not stop cleanly (%s)",
                self.session_id,
                step,
                type(exc).__name__,
            )

    def _open_duration_s(self) -> float:
        """How long this session has been open, to one hundredth of a
        second. Zero before the socket was accepted."""
        if self._opened_at is None:
            return 0.0
        return round(asyncio.get_running_loop().time() - self._opened_at, 2)

    def _start_capture(self, manifest: dict[str, Any]) -> None:
        """Begin recording this session, when a directory is configured."""
        if self._captures is None or self._opened_at is None:
            return
        self._capture = self._captures.open(
            self.session_id, self._opened_at, manifest
        )
        if self._capture is None:
            return
        self._events.attach_capture(self._capture)
        self._capture_decoder = OpusDecoder(sample_rate=PIPELINE_SAMPLE_RATE)
        self._capture_reply_decoder = OpusDecoder(sample_rate=OUTPUT_AUDIO.sample_rate)
        self._capture_resampler = Resampler(OUTPUT_AUDIO.sample_rate, CAPTURE_RATE)

    def _start_recording(self, manifest: dict[str, Any]) -> None:
        """Begin this session's row in the conversation store, when one
        is configured.

        Opened with the same reading the capture was, so a `t_ms` in the
        database and a `t_ms` in the capture's decision track index into
        the same timeline, and with the same manifest, which is where the
        session row's device, agents, protocol and providers come from.

        The tap attaches after the capture's, so the store sees exactly
        what the capture's decision track sees, in the same order and
        from the same first event: this record is the decision track,
        `session_open` through `session_closed`. What the initial agent
        activation emitted before the hello is outside both, because no
        consumer can be attached before a session has a manifest to be
        opened with.
        """
        if self._conversations is None or self._opened_at is None:
            return
        self._conversations.open_session(self.session_id, self._opened_at, manifest)
        self._record = SessionSink(self._conversations, self.session_id)
        self._events.attach(self._record)

    def _stop_recording(self) -> None:
        """Close this session's row: its duration, what ended it, and
        what it lost. Called after `session_closed` is emitted, so that
        event is the last row of the record as it is the last line of the
        decision track."""
        if self._record is None or self._conversations is None:
            return
        self._events.detach(self._record)
        self._record = None
        self._conversations.close_session(
            self.session_id, self._open_duration_s(), self._closed_reason()
        )

    def _manifest(self, client_id: str) -> dict[str, Any]:
        """What this session was held against.

        Built once and handed to both consumers, so the capture's
        manifest file and the store's session row say the same things
        about one session rather than two shapes that could drift.

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
        server = self._server
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
        """The resolved provider entries, as a record may keep them.

        Through `provider_record` rather than a model dump: what lands
        here is written into a capture's manifest and into a
        conversation's session row, both of which outlive the session,
        so it is built key by key with the secret-shaped values masked
        and any credential a URL carries taken out. The write path
        refuses such a URL, and this is the half of that rule that does
        not depend on every row having passed through it.
        """
        if self._agent is None:
            return {}
        described: dict[str, Any] = {}
        # The world as it is now rather than as it was at connect. What
        # a manifest records is what served this session, and until
        # providers become part of a generation the entries a reload can
        # move are not the ones a session was built with; reading the
        # current world is the same answer with one door to change when
        # they are.
        config = self._generations.current().config
        for stage in ("llm", "asr", "tts", "vad"):
            name, _ = config.provider_for_agent(self._agent, stage)
            if name is None:
                continue
            entry = getattr(config.providers, stage).get(name)
            if entry is None:
                continue
            described[stage] = {"name": name, **provider_record(entry)}
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
        timeout = self._server.limits.idle_timeout_s
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
            self._events.emit(
                lambda: SessionIdle(
                    idle_s=Real(timeout), duration_s=Real(self._open_duration_s())
                )
            )
            # A normal closure rather than going away: the server is
            # fine, this conversation is simply over. The firmware reads
            # it as the end of one and reconnects on the next wake word.
            await self.request_shutdown(NORMAL_CLOSURE, "idle timeout", close_reason="idle")
            return

    def _start_device_discovery(self, hello: messages.DeviceHello) -> None:
        """Ask a device that advertised MCP for its tools. In the
        background: the handshake is three round trips, and the
        conversation must not wait on a board that may never answer."""
        if hello.features.get("mcp") is not True:
            return
        self._device_tools = DeviceToolClient(
            self._send_mcp, f"session {self.session_id}", "vinga-server", __version__
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
        """One MCP envelope out to the device. Through the translating
        helper like every other outgoing message, so that a device that
        vanishes mid-call reaches `call_device_tool`'s caller as the
        `DeviceGone` the boundary promises rather than as the
        transport's own exception."""
        await self._send_text(mcp_protocol.envelope(self.session_id, payload))

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
        if not self.listening:
            self._note_dropped("not_listening")
            return
        if not self._server.barge_in and self._replying():
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
        assert self.runtime is not None
        await self.runtime.audio(pcm)

    def _note_dropped(self, reason: str) -> None:
        self._events.dropped(reason)

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
                assert self.runtime is not None
                await self.runtime.listen_started()
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
                assert self.runtime is not None
                await self.runtime.listen_stopped()
            case messages.ListenMessage(state="detect", text=word):
                logger.debug("session %s: wake word reported: %s", self.session_id, word)
            case messages.AbortMessage(reason=reason):
                assert self.runtime is not None
                await self.runtime.device_aborted(reason)
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

    def pause_output(self) -> None:
        """Hold the outgoing frame pacing before the next send. Audio
        stops within a frame either way; what a pause preserves is the
        option of resuming."""
        if self._pace_paused_at is not None:
            return
        self._pace_paused_at = asyncio.get_running_loop().time()
        self._pace_resume.clear()

    def resume_output(self) -> None:
        """Let the frames flow again, with the pacing clock shifted by
        the pause so the stream picks up where it stopped rather than
        bursting to catch up on the frames the pause displaced."""
        if self._pace_paused_at is None:
            return
        if self._pace_start is not None:
            self._pace_start += asyncio.get_running_loop().time() - self._pace_paused_at
        self._pace_paused_at = None
        self._pace_resume.set()

    async def show_transcript(self, text: str) -> None:
        """The `stt` message: what the user was heard to say."""
        await self._send_text(messages.stt_message(self.session_id, text))

    async def sentence_started(self, text: str) -> None:
        """The `tts sentence_start`: what is about to be heard."""
        await self._send_text(messages.tts_message(self.session_id, "sentence_start", text=text))

    async def finish_speaking(self) -> None:
        """End of reply. The pair goes out even for a reply that never
        spoke: the device leaves its speaking state on `tts stop`, and
        in auto mode that is what re-arms its listening, so a stop it
        was never told to expect is the one way this could strand a
        device.

        The activity mark comes first, so a device that has already gone
        away still resets the idle clock on its way out."""
        self._mark_activity()
        await self.begin_speaking()
        await self._send_text(messages.tts_message(self.session_id, "stop"))

    def device_tools(self) -> Sequence[ToolDef]:
        """The device's own tools, once discovery has finished. Empty
        while it runs, and for a device that advertised none."""
        return () if self._device_tools is None else self._device_tools.tools()

    async def call_device_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Invoke one of them over the MCP envelope transport, which is
        the edge's: the runtime sees a name, arguments and an answer."""
        assert self._device_tools is not None
        return await self._device_tools.call(name, arguments)

    async def begin_speaking(self) -> None:
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
        await self._send_text(messages.tts_message(self.session_id, "start"))

    def encode_audio(self, pcm: bytes) -> PlayableAudio:
        """Feed reply PCM at `output_sample_rate` into the encoder; the
        batch holds every packet that filled, possibly none. Synchronous,
        and sends nothing."""
        return PlayableAudio(self._encoder.encode(pcm))

    def flush_encoder(self) -> PlayableAudio:
        """Pad the encoder's pending partial frame with silence and
        encode it. The codec object itself is never reset between
        replies: its few milliseconds of lookahead staying inside is what
        keeps it reusable."""
        return PlayableAudio(self._encoder.flush())

    def reply_started(self) -> None:
        """A new reply: nothing has been spoken and the device has not
        been told anything is coming. The encoder is deliberately left
        alone."""
        self._speaking_started = False
        self._speaking_started_at = None
        self._tts_started = False

    def restart_pacing(self) -> None:
        """A new agent leg: the pacing clock starts again at its first
        frame."""
        self._pace_start = None
        self._pace_count = 0

    def speaking_started_at(self) -> float | None:
        """When this reply's first frame was stamped, or None before it.
        Read by the barge-in refractory gate and by the filler."""
        return self._speaking_started_at

    def user_turn_ended(self) -> None:
        """The runtime decided the utterance ended.

        Somebody was talking, whether or not it earns a reply, so this
        is one of the two ends the idle timeout counts from. Marked here
        rather than inside a runtime because the timeout is the
        appliance's policy: every runtime behind this boundary inherits
        it by reporting the turn, and one that answers nothing (an empty
        transcript, an utterance its gates dropped) cannot leave the
        watchdog counting from before the user last spoke.

        Whether the mic stays armed is protocol, not conversation: auto
        mode stops listening until the device sends a fresh `listen
        start` after the reply's `tts stop`, while a realtime device
        asked once and is still streaming, so stopping here would leave
        nobody to re-arm it and the session would answer one utterance
        and go deaf."""
        self._mark_activity()
        if not self._realtime:
            self.listening = False

    async def send_audio(self, batch: PlayableAudio) -> None:
        """Send a batch of Opus frames paced at the frame cadence, so a
        long reply cannot flood the device's playback queue. The clock
        starts at the first frame of the reply, not at ASR time."""
        if not batch:
            return
        loop = asyncio.get_running_loop()
        if not self._speaking_started:
            # The `replied` event marks the last frame of a reply, so on
            # its own the logs cannot tell synthesis cost from speaking
            # time; this marks the first frame, making time-to-first-audio
            # measurable (#22).
            self._speaking_started = True
            self._speaking_started_at = loop.time()
            self._events.emit(lambda: SpeakingStarted(agent=Identifier(self._agent)))
        frame_s = OUTPUT_AUDIO.frame_duration / 1000
        if self._pace_start is None:
            self._pace_start = loop.time()
        for packet in batch.packets:
            await asyncio.sleep(self._pace_start + self._pace_count * frame_s - loop.time())
            # A barge-in being confirmed holds the stream here; resuming
            # shifts `_pace_start`, so the cadence survives the pause.
            await self._pace_resume.wait()
            await self._send_frame(framing.wrap(self.protocol_version, packet))
            self._capture_reply(packet)
            self._pace_count += 1

    async def _send_text(self, text: str) -> None:
        """One outgoing message, with a device that has gone away
        reported as the boundary's own failure rather than as
        starlette's, so no runtime imports a transport to catch one.

        A vanished device arrives in two shapes and both are translated.
        Starlette raises `WebSocketDisconnect` when it has seen the
        close, and a bare `RuntimeError` when the send comes after one
        ("Cannot call send once a close message has been sent"), which
        is a race a paced reply loses regularly. The `try` covers the
        socket call and nothing else, so the only `RuntimeError` caught
        here is one the socket raised: this is a translation, not a
        blanket. Untranslated, the second shape reaches a runtime as a
        bare `RuntimeError`, which is indistinguishable from a local
        bug and is why the reply body used to have to catch both."""
        gone: DeviceGone | None = None
        try:
            await self.websocket.send_text(text)
        except (WebSocketDisconnect, RuntimeError):
            gone = DeviceGone("the device disconnected")
        # Raised out here rather than in the except arm, the way a
        # provider raises the taxonomy (providers/kit.py): inside it,
        # the transport's exception becomes this one's `__context__`
        # even under `from None`, and a disconnect carries a close
        # reason the far end wrote. Nothing about which transport
        # exception it was is diagnosable anyway, since the boundary
        # exists precisely so no reader of this has to know.
        if gone is not None:
            raise gone from None

    async def _send_frame(self, data: bytes) -> None:
        gone: DeviceGone | None = None
        try:
            await self.websocket.send_bytes(data)
        except (WebSocketDisconnect, RuntimeError):
            gone = DeviceGone("the device disconnected")
        # Outside the arm, for the reason `_send_text` gives.
        if gone is not None:
            raise gone from None

    async def _close(self, code: int, reason: str) -> None:
        with contextlib.suppress(RuntimeError):
            await self.websocket.close(code=code, reason=reason)
