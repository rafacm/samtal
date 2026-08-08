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
import functools
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
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
from samtal_server.protocol import framing, messages
from samtal_server.protocol import mcp as mcp_protocol
from samtal_server.providers import (
    AgentProviders,
    AsrResult,
    Endpointer,
    TextDelta,
    ToolCall,
    ToolChoice,
    ToolDef,
    ToolResult,
    TtsProvider,
    Turn,
    Usage,
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


def provider_fields(stage: str, provider: object) -> dict[str, Any]:
    """Which configuration entry a provider is, for an event that names
    it: the stage it serves, the entry an operator wrote in the YAML,
    its type, and the host it reaches.

    `host` is omitted for an engine that runs in this process, and the
    rest for a provider the registry did not build (a test's, a
    fixture's): an event that cannot name the entry says less rather
    than guessing."""
    identity = getattr(provider, "identity", None)
    fields: dict[str, Any] = {"stage": stage}
    if identity is None:
        return fields
    fields["provider"] = identity.name
    fields["type"] = identity.type
    if identity.host is not None:
        fields["host"] = identity.host
    return fields


def is_timeout(exc: BaseException) -> bool:
    """Whether a provider failure was a wait rather than an answer.

    Decided by class name as well as by type, because every SDK has its
    own: `asyncio.TimeoutError` is the builtin `TimeoutError`, but
    `openai.APITimeoutError` is an `APIConnectionError` and
    `httpx.TimeoutException` inherits from neither. Nothing hangs on
    getting it right beyond the wording of one sentence, since the
    event carries the exact class either way."""
    return isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__


class _Synthesis:
    """One sentence being turned into audio, started before the moment it
    is needed.

    A reply is spoken sentence by sentence, and frames are paced to
    realtime, so sending a sentence takes about as long as hearing it.
    Synthesizing only when the previous sentence has finished playing
    therefore puts the next sentence's whole time to first byte on the
    speaker as silence, once per sentence, for the whole reply. Measured
    on a three-sentence reply: 617 ms and 520 ms between sentences
    through `gpt-4o-mini-tts`, reported from a board session as "hiccups
    in the assistant's voice". Starting the work early spends that time
    against playback that is already happening (#37).

    A task pulls from the provider into a one-chunk buffer. `chunks()`
    yields what has arrived and waits for the rest, so a sentence run
    ahead has already paid its time to first byte by the time anyone
    asks for it, while the first sentence of a reply still streams:
    nothing is held back waiting for a sentence to finish.

    The buffer holds one chunk, not the sentence. Playback consumes at
    realtime and a provider can produce much faster than that, so an
    unbounded buffer would hold a whole sentence of PCM per sentence run
    ahead and remove the backpressure the paced consumer used to apply
    to the provider. One chunk is all the lookahead needs, because what
    it exists to absorb is the wait for the *first* chunk.

    A failure is held rather than raised where it happened, and re-raised
    from `chunks()` at the point the sentence would have been spoken.
    That keeps the order of what a caller sees: the sentences before a
    failing one are spoken, and the reply fails where it would have.
    """

    def __init__(
        self,
        sentence: str,
        tts: TtsProvider,
        report_failure: Callable[[BaseException, float], None],
    ) -> None:
        self.sentence = sentence
        self._buffer: asyncio.Queue[bytes | None] = asyncio.Queue()
        # The backpressure. Held per chunk waiting to be spoken and
        # released as each is taken, so the provider is asked for the
        # next chunk only once the previous has been picked up. The
        # bound is on data, not on the queue, so that the end-of-audio
        # sentinel below can always be delivered: a bounded queue that
        # is full when the consumer goes away leaves the drain task
        # blocked forever on a sentinel nobody is waiting for.
        self._room = asyncio.Semaphore(1)
        self._failure: BaseException | None = None
        self._report_failure = report_failure
        self._task = asyncio.create_task(self._drain(tts))

    async def _drain(self, tts: TtsProvider) -> None:
        started = asyncio.get_running_loop().time()
        try:
            async for chunk in tts.synthesize(self.sentence):
                await self._room.acquire()
                self._buffer.put_nowait(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised in chunks()
            self._failure = exc
            # Reported here rather than where it is re-raised: a
            # sentence run ahead can fail long before the moment it
            # would have been spoken, and the event an operator
            # correlates with a network policy should carry the time
            # the call actually failed at.
            self._report_failure(exc, asyncio.get_running_loop().time() - started)
        finally:
            self._buffer.put_nowait(None)

    async def chunks(self) -> AsyncIterator[bytes]:
        """The audio, in order, waiting only for what has not arrived."""
        while True:
            chunk = await self._buffer.get()
            if chunk is None:
                break
            self._room.release()
            yield chunk
        if self._failure is not None:
            raise self._failure

    def cancel(self) -> None:
        """Abandon a sentence that will never be spoken. Nothing to
        record: `_speak` counts a sentence only after its audio has gone
        out, so one dropped here was never counted anywhere."""
        self._task.cancel()

    async def wait_cancelled(self) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await self._task


class FirstTokenTimeout(TimeoutError):
    """The LLM produced nothing within the first-token watchdog window,
    twice in a row. The class name is what the `provider_failed` event
    carries in `error`, which is what makes a provider that stalls
    before answering distinguishable in the retained logs from one
    whose own SDK timed out."""


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
        captures: CaptureStore | None = None,
        device_facts: DeviceFacts | None = None,
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
        # Generation calls in the reply being spoken, counted across
        # its agents rather than per leg, so the one after a handover
        # is a round of its own in the logs.
        self._llm_round = 0
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
        # When this session last did any conversing, which is what the
        # idle timeout counts from. Set at the end of an utterance and at
        # the end of a reply, so "whichever is later" needs no
        # comparison: both just write the current time.
        self._last_activity: float | None = None
        self._idle_watchdog: asyncio.Task[None] | None = None

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
        JSON one.

        Every event goes through here, which is why the capture's
        decision track is hooked here too rather than at each call
        site: an event that is logged is an event that is recorded."""
        payload = {
            "event": event,
            "session": self.session_id,
            "device": self._mac,
            **fields,
        }
        if self._capture is not None:
            self._capture.event(payload, asyncio.get_running_loop().time())
        return payload

    @contextlib.asynccontextmanager
    async def _watching(self, stage: str, provider: object) -> AsyncIterator[None]:
        """Report a provider that fails, then let the failure carry on
        as before.

        A failing ASR, LLM or TTS call used to reach the operator as a
        traceback under "reply failed", with none of the fields every
        other conversation record is queried by: no `event`, no
        `session`, no provider, and above all no host, which is the one
        an egress policy is diagnosed from. The reply still ends the
        same way, and the traceback is still logged where it was; this
        adds the structured half the observability ADR says is the
        surface (#53)."""
        started = asyncio.get_running_loop().time()
        try:
            yield
        except Exception as exc:
            self._provider_failed(
                stage, provider, exc, asyncio.get_running_loop().time() - started
            )
            raise

    async def _watched_stream(
        self, provider: object, events: AsyncIterator[Any]
    ) -> AsyncIterator[Any]:
        """An LLM stream, with a failure raised by the stream itself
        reported as that provider's.

        A plain `async with` around the consuming loop would blame the
        LLM for a TTS failure raised while speaking what the model had
        already said, and report one failure twice. Pulling the stream
        by hand is what separates the two: what the consumer raises
        closes this generator rather than passing through the guard."""
        started = asyncio.get_running_loop().time()
        iterator = events.__aiter__()
        while True:
            try:
                event = await iterator.__anext__()
            except StopAsyncIteration:
                return
            except Exception as exc:
                self._provider_failed(
                    "llm", provider, exc, asyncio.get_running_loop().time() - started
                )
                raise
            yield event

    async def _watchdog_stream(
        self, provider: object, make_stream: Callable[[], AsyncIterator[Any]]
    ) -> AsyncIterator[Any]:
        """An LLM stream whose wait for the first event is bounded.

        Nothing used to bound the gap between sending the request and
        the first byte of the answer, so a provider that stalled there
        froze the pipeline: a 17 s stall held the session in replying,
        deaf to a user who politely waits, until a barge-in rescued it
        (#68). The bound covers only that gap. Once anything has
        arrived the stream is streaming and no timeout applies, because
        a long generation that is delivering is healthy: a 17.7 s story
        round with a 635 ms first token is fine.

        One timeout cancels the request and retries the round once,
        since the field data says the retry answers quickly (6.16 s
        total against the 17 s stall it replaced). A second timeout
        gives up: the failure is reported as the provider's, with
        `FirstTokenTimeout` telling it apart from the provider's own
        classes, and the reply ends the way any provider failure ends
        it, so the failure mode is a silent turn rather than a wedged
        session. Barge-in keeps working through the whole window: it
        cancels the reply task, and that cancellation lands in the wait
        here like in any other await.

        The provider's own timeout classes pass through untouched: the
        `expired()` check is what keeps an SDK timeout raised just
        before the watchdog's deadline from being retried as if the
        watchdog had fired."""
        timeout_s = self.config.server.llm_first_token_timeout_s
        loop = asyncio.get_running_loop()
        for attempt in ("first", "retry"):
            events = self._watched_stream(provider, make_stream())
            started = loop.time()
            try:
                async with asyncio.timeout(timeout_s) as watchdog:
                    first = await events.__anext__()
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                if not watchdog.expired():
                    raise
                elapsed = loop.time() - started
                if attempt == "retry":
                    failure = FirstTokenTimeout(
                        f"no first token within {timeout_s:.0f} s, twice"
                    )
                    self._provider_failed("llm", provider, failure, elapsed)
                    raise failure from exc
                logger.warning(
                    "session %s: no first token after %.1f s, retrying round %d",
                    self.session_id,
                    elapsed,
                    self._llm_round,
                    extra=self._event(
                        "llm_retry",
                        agent=self._agent,
                        round=self._llm_round,
                        duration_ms=round(elapsed * 1000),
                        **provider_fields("llm", provider),
                    ),
                )
                continue
            yield first
            async for event in events:
                yield event
            return

    def _llm_round_done(
        self,
        provider: object,
        working: Sequence[Turn],
        began: float,
        first_token_at: float | None,
        usage: Usage | None,
    ) -> None:
        """One `llm_round` event, which is where a slow reply becomes
        attributable.

        Stage latency was otherwise inferred from the gaps between
        events, and the gap between `heard` and `speaking_started`
        holds the LLM and the TTS time to first byte with nothing
        between them. A field session lost 19.04 s inside that gap
        against a session median of 1.18 s, and the logs could not say
        whether the payload or the vendor was responsible (#55).

        `turns` is the cheap proxy for payload size, and `round` counts
        the whole reply rather than one agent's leg, so the generation
        after a handover is a round of its own rather than another
        first round. Token counts appear when the provider reported
        them; their absence is a fact about the endpoint.

        `first_token_ms` times the first spoken token, so a round that
        only asked for a tool carries none: there was no token, and
        timing the tool call instead would report the whole generation
        as its own time to first token, since both providers assemble
        calls after the stream has ended."""
        loop = asyncio.get_running_loop()
        elapsed = loop.time() - began
        tokens: dict[str, Any] = {}
        if usage is not None and usage.prompt_tokens is not None:
            tokens["prompt_tokens"] = usage.prompt_tokens
        if usage is not None and usage.completion_tokens is not None:
            tokens["completion_tokens"] = usage.completion_tokens
        if first_token_at is not None:
            tokens["first_token_ms"] = round((first_token_at - began) * 1000)
        logger.info(
            "session %s: %s round %d took %.2f s over %d turns",
            self.session_id,
            self._agent,
            self._llm_round,
            elapsed,
            len(working),
            extra=self._event(
                "llm_round",
                agent=self._agent,
                round=self._llm_round,
                turns=len(working),
                duration_ms=round(elapsed * 1000),
                **provider_fields("llm", provider),
                **tokens,
            ),
        )

    def _provider_failed(
        self, stage: str, provider: object, exc: BaseException, elapsed: float
    ) -> None:
        """One `provider_failed` event, and the sentence that goes with
        it. A timeout is worded as one, because where traffic is
        dropped rather than refused the whole symptom is a wait."""
        fields = provider_fields(stage, provider)
        named = f' "{fields["provider"]}"' if "provider" in fields else ""
        where = f" reaching {fields['host']}" if "host" in fields else ""
        logger.warning(
            "session %s: %s provider%s %s after %.2f s%s: %s: %s",
            self.session_id,
            stage,
            named,
            "timed out" if is_timeout(exc) else "failed",
            elapsed,
            where,
            type(exc).__name__,
            exc,
            extra=self._event(
                "provider_failed",
                agent=self._agent,
                error=type(exc).__name__,
                duration_ms=round(elapsed * 1000),
                **fields,
            ),
        )

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
        if self._capture is not None:
            self._capture.dropped(reason, asyncio.get_running_loop().time())

    def _capture_vad(self) -> None:
        if self._capture is None or self._endpointer is None:
            return
        self._capture.vad(
            self._endpointer.speech_ms(),
            self.listening,
            self._replying(),
            asyncio.get_running_loop().time(),
        )

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
        self._reply_task = asyncio.create_task(self._reply(pcm, result))

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
            async with self._watching("asr", self._providers.asr):
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

    async def _reply(self, pcm: bytes, result: AsrResult | None = None) -> None:
        """Run one utterance through ASR, the LLM, and TTS. Cancelled by
        `abort`; provider failures end the reply but not the session. The
        closing `tts stop` is sent even then, because the device (in auto
        mode) waits for it before listening again.

        `result` is a transcription that already exists: a confirmed
        barge-in ran ASR to decide the cancel, and reusing its full
        result (language fields included) is what keeps ASR at one run
        and `heard` at one event per interruption."""
        assert self._providers is not None
        providers = self._providers
        spoken: list[str] = []
        self._speaking_started = False
        self._speaking_started_at = None
        self._tts_started = False
        heard_s = round(len(pcm) / 2 / PIPELINE_SAMPLE_RATE, 2)
        try:
            if result is None:
                async with self._watching("asr", providers.asr):
                    result = await providers.asr.transcribe(
                        pcm, PIPELINE_SAMPLE_RATE, language_hint=self._asr_language
                    )
            # ASR is done, so the mid-ASR marker comes down: from here a
            # barge-in has nothing of the user's left to destroy.
            self._reply_pcm = None
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
            if transcript:
                self._turns.append(Turn("user", transcript))
                await self._speak_reply(transcript, spoken)
        except (WebSocketDisconnect, RuntimeError):
            return  # the device went away mid-reply
        except Exception:
            logger.exception("session %s: reply failed", self.session_id)
        finally:
            self._reply_pcm = None
            # The other end the idle timeout counts from. In the finally,
            # so a reply that failed or was cancelled still resets the
            # clock: the user is owed the full silence before being hung
            # up on either way.
            self._mark_activity()
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
                # A reply that never spoke still sends the pair. The
                # device leaves its speaking state on `tts stop`, and in
                # auto mode that is what re-arms its listening, so a
                # `stop` it was never told to expect is the one way this
                # could strand a device.
                await self._begin_speaking()
                await self.websocket.send_text(messages.tts_message(self.session_id, "stop"))

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
        self._llm_round = 0
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
            # The sentence currently being spoken, which runs alongside
            # the model still streaming. At most one sentence is ever
            # run ahead of it: every sentence plays for longer than the
            # next takes to start, so one closes the gap, and more would
            # only mean more concurrent requests to the provider and
            # more audio held for a reply a barge-in may throw away.
            speaking: asyncio.Task[None] | None = None
            loop = asyncio.get_running_loop()
            began = loop.time()
            first_token_at: float | None = None
            usage: Usage | None = None
            self._llm_round += 1
            try:
                async for event in self._watchdog_stream(
                    providers.llm,
                    functools.partial(
                        providers.llm.stream, self._system_prompt(), working, tools, choice
                    ),
                ):
                    if isinstance(event, TextDelta):
                        # Speech only, and speech that is not just
                        # whitespace. Both providers assemble tool
                        # calls and usage after their stream has ended,
                        # so timing from those would report a whole
                        # generation as its own time to first token,
                        # and a round that only calls a tool has no
                        # first token to time.
                        if first_token_at is None and event.text.strip():
                            first_token_at = loop.time()
                        for sentence in splitter.push(event.text):
                            speaking = await self._speak_after(
                                speaking, sentence, providers.tts, resampler, leg, spoken
                            )
                    elif isinstance(event, Usage):
                        usage = event
                    else:
                        calls.append(event)
                self._llm_round_done(providers.llm, working, began, first_token_at, usage)
                tail = splitter.flush()
                if tail is not None:
                    speaking = await self._speak_after(
                        speaking, tail, providers.tts, resampler, leg, spoken
                    )
                # The round ends here, so the lookahead stops here too:
                # there is no next sentence to overlap with, and the
                # tools below must not run over the top of speech.
                if speaking is not None:
                    await speaking
                    speaking = None
            finally:
                # A barge-in cancels this coroutine anywhere above, and
                # the sentence being spoken must not outlive the reply it
                # belonged to. `_speak` takes its own synthesis down with
                # it, so cancelling the task is enough.
                if speaking is not None:
                    speaking.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await speaking
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
        # Handing over to the agent already speaking is a pure cost: the
        # leg ends, the same agent is re-activated, and a second round
        # runs only to greet a user who is already mid-conversation.
        if target == self._agent:
            return ToolResult(
                call.id,
                "you are already speaking as this assistant; answer as yourself instead",
                is_error=True,
            )
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

    async def _speak_after(
        self,
        speaking: asyncio.Task[None] | None,
        sentence: str,
        tts: TtsProvider,
        resampler: Resampler,
        leg: list[str],
        spoken: list[str],
    ) -> asyncio.Task[None]:
        """Start `sentence` synthesizing, wait for the sentence already
        being spoken to finish, then start speaking this one. Answers the
        task now speaking, for the next call to wait on.

        The first statement before the first await is the entire fix:
        the new sentence's time to first byte is spent against the
        previous sentence's playback, which is already happening, rather
        than against silence.

        Speaking is a task rather than an await so that it overlaps the
        model still streaming. Awaiting it here instead would mean a
        sentence is not spoken until the *next* one has been written,
        which would put the model's thinking time in front of the first
        word of every reply and make a one-sentence reply wait for the
        stream to end."""
        started = _Synthesis(
            sentence,
            tts,
            lambda exc, elapsed: self._provider_failed("tts", tts, exc, elapsed),
        )
        try:
            if speaking is not None:
                await speaking
        except BaseException:
            # The sentence just started will never be spoken now,
            # whether this was a provider failure or a barge-in
            # cancelling the reply.
            started.cancel()
            await started.wait_cancelled()
            raise
        return asyncio.create_task(self._speak_and_record(started, resampler, leg, spoken))

    async def _speak(
        self, synthesis: _Synthesis, resampler: Resampler, spoken: list[str]
    ) -> None:
        """Say one sentence, and count it as said only once its audio has
        gone out.

        The order is the point. Frames are paced, so sending a sentence
        takes about as long as hearing it, and a barge-in cancels this
        coroutine somewhere in the middle of that. Counted first, a
        sentence the user heard two frames of would go into the turn the
        round hands the model as its own preamble. A sentence synthesized
        ahead and never spoken is counted nowhere at all, which is the
        same rule seen from the other end.

        The audio arrives from `synthesis`, which may already have some
        or all of it buffered. Resampling and encoding stay here, in
        order, because the resampler and the encoder are stateful and
        belong to the stream rather than to a sentence.

        `sentence_start` goes out now rather than when synthesis began:
        it tells the device what is being said, and what is being said is
        what is about to be heard.

        This is also where the device is told speech is starting, which
        leaves one window open: a TTS provider slow to its first byte
        holds the device in its speaking state for that wait, and for a
        host that drops traffic that is the synthesis `timeout_s`.
        Closing it means holding `sentence_start` back until the first
        chunk, which reverses a decision #37 made deliberately (the
        announcement belongs to the sentence about to be spoken, and
        whether its audio will arrive is not known then), and changes
        the order of messages the firmware sees. Worth deciding on the
        board rather than here."""
        await self._begin_speaking()
        await self.websocket.send_text(
            messages.tts_message(self.session_id, "sentence_start", text=synthesis.sentence)
        )
        try:
            async for chunk in synthesis.chunks():
                await self._send_frames(self._encoder.encode(resampler.process(chunk)))
        finally:
            # A barge-in cancels this coroutine mid-sentence, and the
            # synthesis behind it is a separate task that would otherwise
            # keep pulling from the provider for a sentence nobody will
            # hear. After a sentence finishes normally this is a task
            # that is already done, so cancelling costs nothing.
            synthesis.cancel()
            await synthesis.wait_cancelled()
        spoken.append(synthesis.sentence)

    async def _speak_and_record(
        self, synthesis: _Synthesis, resampler: Resampler, leg: list[str], spoken: list[str]
    ) -> None:
        """Say a sentence and count it in both places at once: the
        round's own list, which becomes the turn the model is shown, and
        the reply's, which becomes the history.

        One call rather than two lists merged at the end of the round,
        because a barge-in cancels mid-round: merging later loses every
        sentence of that round, including the ones the user sat through
        and answered. Whoever speaks next then has no idea what was
        already said."""
        await self._speak(synthesis, resampler, leg)
        spoken.append(synthesis.sentence)

    async def _send_frames(self, packets: list[bytes]) -> None:
        """Send Opus frames paced at the frame cadence, so a long reply
        cannot flood the device's playback queue. The clock starts at the
        first frame of the reply, not at ASR time."""
        if not packets:
            return
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
