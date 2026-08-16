"""The bespoke conversation runtime: VAD, ASR, an LLM tool loop, and TTS.

One utterance at a time, run behind the device-facing boundary. It talks
as one agent at a time, the active agent, picked at connect from the
agents the device is bound to; prompt, providers, and endpointer all come
from that agent, so swapping it swaps all three.

While the device listens, decoded mic audio feeds the agent's
endpointer; when the utterance ends, ASR transcribes it, the LLM streams
a reply into sentences, and TTS speaks each sentence back through the
device. Conversation history lives here, one list of turns per
connection.

The reply is a tool loop, and the loop lives here rather than in a
provider because only the runtime can change agents between rounds. Per
reply it snapshots the tools the active agent may use, streams, executes
whatever the model asked for, feeds the results back, and streams again,
up to a small cap whose last round forbids calling so a reply always
ends in speech. History stays text-only: the structured tool turns exist
in a working copy inside one reply, and what survives is what was
actually said aloud.

An utterance that ends while a reply is streaming cancels that reply and
is answered, which is what barge-in is. An endpointer-driven cancel is
gated: a reply is only cancelled on evidence of user speech (enough
classified speech, a transcript when in doubt), because acoustics alone
are as often noise or the reply's own bleed as the user (#28). A manual
`listen stop` mid-reply is a deliberate act and cancels unconditionally.

What happens in a conversation is logged twice over: as a human
sentence, and as the structured fields the JSON log format emits as
top-level keys. Both halves go out through the session's
`SessionEvents` ([events](../events.py)), so that every record carries
the same channel and the same identity whichever side of the boundary
emitted it, and so that every consumer of the events sees it.
"""

import asyncio
import contextlib
import functools
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from samtal_server.audio.resample import Resampler
from samtal_server.config import Config
from samtal_server.conversations.records import (
    SessionTurns,
    ToolInvocation,
    TurnRecorder,
    TurnStore,
)
from samtal_server.device.boundary import (
    PIPELINE_SAMPLE_RATE,
    DeviceGone,
    DeviceOutput,
    PlayableAudio,
    RuntimeFactory,
    SessionInput,
)
from samtal_server.events import SessionEvents, logger
from samtal_server.filler import FillerClips
from samtal_server.providers import (
    AgentProviders,
    AsrResult,
    Endpointer,
    StreamStarted,
    TextDelta,
    ToolCall,
    ToolChoice,
    ToolDef,
    ToolResult,
    TtsProvider,
    Turn,
    Usage,
)
from samtal_server.runtime import prompt
from samtal_server.runtime.speech import _Synthesis, speak_after
from samtal_server.runtime.turns import BUILTIN, MCP, TurnUnderway, tool_source
from samtal_server.text import SentenceSplitter
from samtal_server.tools import builtin, names
from samtal_server.tools.mcp import McpServers
from samtal_server.tools.memory import MemoryStore

# How many times one reply may stream, call tools, and stream again.
# The last permitted round forbids calling, so a reply always ends in
# speech rather than in a tool nobody hears the result of.
# How much recent mic audio the utterance buffer keeps. A realtime
# session listens through the silences too, so without a bound the
# buffer would grow for the whole session (about 115 MB at the one-hour
# cap). Well above the endpointer's 10 s `max_utterance_ms`, so what a
# trim can ever drop is silence nobody is going to transcribe.
UTTERANCE_TAIL_S = 30
UTTERANCE_TAIL_BYTES = UTTERANCE_TAIL_S * PIPELINE_SAMPLE_RATE * 2

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


def _tool_named(classified: ToolInvocation) -> tuple[dict[str, str], str]:
    """What a `tool_call` event may name about the call it describes, as
    its fields and as the fragment its sentence renders.

    Only what this application authored. A builtin's name is this
    server's own; an MCP entry's name is what the operator wrote in
    their YAML. A device tool's name is the board's vocabulary and an
    unknown name is whatever the model invented, and the retained
    surface admits no far-side bytes whichever peer sent them (#154, the
    content-and-telemetry ADR). Those two events therefore name nothing:
    `source` says which namespace was reached into, and the full name is
    on the store's `tool_invocations` row, where the text switch decides
    whether it is kept."""
    if classified.source == BUILTIN:
        return {"tool": classified.name}, f' "{classified.name}"'
    if classified.source == MCP and classified.entry is not None:
        return {"entry": classified.entry}, f' from entry "{classified.entry}"'
    return {}, ""


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


class PipelineRuntime:
    """One conversation, for one connection, behind the device edge.

    `output` is the device it speaks through, and it is the whole of
    what this runtime knows about the far end: no socket, no protocol,
    no codec. Built by `bespoke_runtime_factory` below, which is what
    the composition root hands the device edge.
    """

    def __init__(
        self,
        output: DeviceOutput,
        config: Config,
        events: SessionEvents,
        agent_providers: dict[str, AgentProviders],
        mcp_servers: McpServers,
        memory: MemoryStore | None,
        fillers: dict[str, FillerClips] | None,
        agents: Sequence[str],
        recorder: TurnRecorder | None = None,
    ) -> None:
        self._output = output
        self._config = config
        self._events = events
        self.session_id = events.session_id
        self._agent_providers = agent_providers
        self._mcp_servers = mcp_servers
        self._memory = memory
        # The conversation's content channel, beside the event tap and
        # separate from it on purpose: tool arguments and results never
        # rode the events, and the events are losing their text (#120).
        # None means nobody is listening, which is every deployment until
        # the store is wired, and the reply path then behaves exactly as
        # it did before the channel existed.
        self._recorder = recorder
        # The turn being assembled, replaced at the start of every reply
        # and read once at the end of it. Always present rather than
        # optional: the reply path writes into it from half a dozen
        # places, and a guard at each of them would be six chances to
        # forget one.
        self._turn = TurnUnderway()
        # The agents this device may talk to. The one it is talking to
        # now lives on the events object, because both sides of the
        # boundary attribute events to it.
        self._agents: list[str] = []
        self._providers: AgentProviders | None = None
        # The half of the system prompt that belongs to the agent rather
        # than to the moment: its persona and the guidance of the MCP
        # entries it is granted, assembled once per activation and held
        # here for the life of it. Nothing about it is recomputed per
        # reply; what is, is the memory block appended to it.
        self._know_how: prompt.Assembled | None = None
        self._turns: list[Turn] = []
        # Generation calls in the reply being spoken, counted across its
        # agents rather than per leg, so the one after a handover is a
        # round of its own in the logs.
        self._llm_round = 0
        # The language the ASR provider asked this session to reuse
        # (`AsrResult.lock_language`). Session-scoped on purpose: the
        # provider is shared between sessions and holds no per-session
        # state, and the speaker does not change on an agent switch.
        self._asr_language: str | None = None
        self._endpointer: Endpointer | None = None
        self._utterance = bytearray()
        # How much the tail cap has cut from the front of `_utterance`
        # since the last reset, which is what maps the endpointer's
        # speech-start offset (counted over everything fed) onto a
        # position in the buffer that remains.
        self._utterance_dropped = 0
        self._reply_task: asyncio.Task[None] | None = None
        # The PCM the reply task in flight was handed, held until its
        # ASR call returns. Still being set is the mid-ASR marker: a
        # barge-in landing then killed the head of the user's own
        # sentence, so this is also the merge source that reconstitutes
        # it in front of the continuation.
        self._reply_pcm: bytes | None = None
        # Whether this runtime is holding the device's outgoing frames
        # while a barge-in is confirmed. Tracked here rather than asked
        # of the device: the runtime is the only thing that ever pauses
        # the stream, so its own intent is the honest answer, and the
        # boundary stays free of a query only the filler would read.
        self._output_paused = False
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
        self._agents = list(agents)
        # The activation the connect used to do by hand, and the MCP
        # revive that followed it, in that order. No task is spawned
        # here: the reply task is created on the first utterance, and
        # discovery belongs to the edge.
        self._activate_agent(self._agents[0])
        # A server that was down at boot, or that dropped since, gets a
        # background reconnect now, so it is picked up by the time this
        # conversation needs it rather than at the next server restart.
        # Which entries those are is asked of the registry rather than
        # resolved from this session's configuration: a reload replaces
        # the grants along with the managers they name, and the
        # configuration here is the one that was loaded at boot.
        self._mcp_servers.revive_for_agents(self._agents)

    @property
    def _agent(self) -> str | None:
        return self._events.agent

    @_agent.setter
    def _agent(self, name: str | None) -> None:
        self._events.agent = name


    # --- SessionInput: what the device edge asks of this runtime -------

    async def audio(self, pcm: bytes) -> None:
        """One decoded mic frame, at `PIPELINE_SAMPLE_RATE`. Called only
        while the device is listening and the edge's guards passed, which
        is why the VAD sample below records the listening as true without
        asking."""
        if self._endpointer is None:
            return
        self._utterance.extend(pcm)
        if len(self._utterance) > UTTERANCE_TAIL_BYTES:
            excess = len(self._utterance) - UTTERANCE_TAIL_BYTES
            del self._utterance[:excess]
            self._utterance_dropped += excess
        endpointed = self._endpointer.feed(pcm)
        # After the feed, so the sample is the endpointer's opinion of
        # the audio just recorded rather than of the frame before it.
        self._events.vad(self._endpointer.speech_ms(), True, self.replying())
        if endpointed:
            await self._finish_utterance(endpointed=True)

    async def listen_started(self) -> None:
        """The device asked to listen. Which mode it asked in is the
        edge's business; what this side does is start a fresh
        utterance."""
        self._reset_utterance()

    async def listen_stopped(self) -> None:
        """A manual end of utterance. Nothing buffered means nothing was
        said, so there is nothing to answer."""
        if self._utterance:
            await self._finish_utterance()

    async def device_aborted(self, reason: str | None) -> None:
        """The device gave up on the answer: the reply in flight dies
        and the utterance starts over."""
        logger.info("session %s: device aborted (%s)", self.session_id, reason or "no reason")
        await self._cancel_reply()
        self._reset_utterance()

    def replying(self) -> bool:
        """Whether a reply is streaming right now, which is what both
        halves of the barge-in decision turn on, and what the edge's own
        jobs (the barge-in-off frame guard, the idle watchdog) ask."""
        return self._reply_task is not None and not self._reply_task.done()

    async def drain(self, grace_s: float) -> bool:
        """Let a reply in flight finish, whether it is already speaking
        or still generating, and answer whether it did within
        `grace_s`. Never cancels it."""
        reply = self._reply_task
        if reply is None or reply.done():
            return True
        # asyncio.wait rather than await: a reply that failed is a reply
        # that finished, and its exception is not this method's to raise.
        done, _ = await asyncio.wait([reply], timeout=grace_s)
        return bool(done)

    async def close(self) -> None:
        """The conversation is over."""
        await self._cancel_reply()

    # --- the device's outgoing audio, arbitrated against the filler ----

    async def _send_reply_audio(self, batch: PlayableAudio) -> None:
        """Send a batch of the reply's own audio.

        A batch with nothing in it is not audio and never reaches the
        arbitration: a chunk too short to fill a frame must not be read
        as "the reply is ready" and stand an unfired filler down. Once
        there is something to play, a clip already sounding is waited
        out so the first real sentence queues behind its tail. The
        filler's own frames go straight to the device, which is what
        keeps this from waiting on itself."""
        if not batch:
            return
        await self._filler_tail()
        await self._output.send_audio(batch)

    def _pause_output(self) -> None:
        self._output_paused = True
        self._output.pause_output()

    def _resume_output(self) -> None:
        self._output_paused = False
        self._output.resume_output()

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

        "First token" is the stream's first event of any kind. The
        adapters announce their first raw chunk off the wire as a
        `StreamStarted`, because both buffer tool-call fragments until
        the stream has ended: without the announcement a round that
        streams only a tool call (a handover does) would look exactly
        like a stalled request and be cancelled at the timeout while
        healthily delivering. The announcement is consumed here, being
        evidence rather than content, so nothing downstream sees it.

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
        timeout_s = self._config.server.llm_first_token_timeout_s
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
                self._events.warning(
                    "session %s: no first token after %.1f s, retrying round %d",
                    self.session_id,
                    elapsed,
                    self._llm_round,
                    event="llm_retry",
                    agent=self._agent,
                    round=self._llm_round,
                    duration_ms=round(elapsed * 1000),
                    **provider_fields("llm", provider),
                )
                continue
            if not isinstance(first, StreamStarted):
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
        self._events.info(
            "session %s: %s round %d took %.2f s over %d turns",
            self.session_id,
            self._agent,
            self._llm_round,
            elapsed,
            len(working),
            event="llm_round",
            agent=self._agent,
            round=self._llm_round,
            turns=len(working),
            duration_ms=round(elapsed * 1000),
            **provider_fields("llm", provider),
            **tokens,
        )
        # Counted here rather than where the round starts, so that the
        # turn's rounds, its summed duration and its token totals all
        # describe one set of rounds: the ones that finished, which is
        # the set an `llm_round` row exists for.
        self._turn.round_done(
            round(elapsed * 1000),
            tokens.get("first_token_ms"),
            None if usage is None else usage.prompt_tokens,
            None if usage is None else usage.completion_tokens,
        )

    def _provider_failed(
        self, stage: str, provider: object, exc: BaseException, elapsed: float
    ) -> None:
        """One `provider_failed` event, and the sentence that goes with
        it. A timeout is worded as one, because where traffic is
        dropped rather than refused the whole symptom is a wait.

        Which failure is a wait is a question of type. Every provider
        raises `ProviderCallTimeout` for its SDK's timeouts and that is
        a `TimeoutError`, as are `asyncio.TimeoutError` and the
        watchdog's own `FirstTokenTimeout`, so one `isinstance` covers
        the lot (#137). It used to be decided by looking for "Timeout"
        in the class name, because the SDKs' own classes agreed on
        nothing: `openai.APITimeoutError` is an `APIConnectionError` and
        `httpx.TimeoutException` inherits from neither.

        The class name is reported and the exception's message is not.
        The five real providers raise the request-time taxonomy, whose
        messages carry trusted metadata only (`providers/kit.py`), but
        this takes a `BaseException` from four call sites and one of
        them is the LLM stream, so anything an SDK or a transport
        raises can arrive here unwrapped, and an exception raised near
        a response body can embed one in its message. That would land
        in the sentence, in the record's arguments, and from there in
        front of every consumer attached to the session, which is the
        same reason `_reply`'s catch prints a class name and nothing
        else. What the class does not say, the fields do: the stage,
        the entry, its type, and the host.
        """
        fields = provider_fields(stage, provider)
        named = f' "{fields["provider"]}"' if "provider" in fields else ""
        where = f" reaching {fields['host']}" if "host" in fields else ""
        self._events.warning(
            "session %s: %s provider%s %s after %.2f s%s: %s",
            self.session_id,
            stage,
            named,
            "timed out" if isinstance(exc, TimeoutError) else "failed",
            elapsed,
            where,
            type(exc).__name__,
            event="provider_failed",
            agent=self._agent,
            error=type(exc).__name__,
            duration_ms=round(elapsed * 1000),
            **fields,
        )

    def _activate_agent(self, name: str) -> None:
        """Talk as this agent from now on: its prompt, its providers, and a
        fresh endpointer from its VAD, since the previous agent's endpointer
        carries the previous agent's tuning and mid-utterance state. Called
        once at connect, and again mid-reply when switch_agent hands the
        conversation over. The history carries across the switch: it is
        text-only, so nothing provider-specific leaks with it, and the
        new agent seeing what was said is what makes "switch to the
        tutor and explain what we just discussed" work.

        This is also where the know-how half of the system prompt is
        assembled, which is the whole of when it happens: at session
        open and again at an agent switch, and never per reply. Nothing
        is fetched here, because nothing needs to be: the persona is the
        configuration this process booted on, and the guidance is what
        the registry's slice holds, so a reload that landed since is
        picked up by the next session or the next switch rather than by
        a conversation in flight.

        The device's bound list is enforced here rather than left to
        callers, because the next caller is a tool whose argument a model
        chose: an agent that merely exists is not one this device may
        talk to. Nothing is swapped when the name is refused, so the
        session keeps the agent it already had."""
        if name not in self._agents:
            raise _not_allowed(name, self._agents)
        self._agent = name
        self._providers = self._agent_providers[name]
        self._know_how = prompt.know_how(
            self._config.prompt_for_agent(name),
            self._config.fragments_for_agent(name),
            self._mcp_servers.guidance_for_agent(name),
        )
        self._prompt_assembled(name, self._know_how)
        self._endpointer = self._providers.vad.new_endpointer()
        self._reset_utterance()

    def _prompt_assembled(self, agent: str, half: prompt.Assembled) -> None:
        """One `prompt_assembled` event: what this agent's know-how half
        was made of, and how big each piece of it is.

        The decision-site rule applied to prompt size. Every injected
        block competes with the rest for the budget of a small local
        model, and when one degrades in the field the retained logs
        should say what its prompt held without anybody reproducing the
        session.

        Memory is deliberately outside it. This fires where the
        know-how half is actually assembled, once per activation, while
        memory is read per round; emitting per round would double a
        round's log volume for a number that moves slowly, and
        `llm_round` already carries that round's token counts. The
        inspection surface reads memory fresh and answers its size on
        demand.
        """
        self._events.info(
            "session %s: assembled %d characters of prompt for %s",
            self.session_id,
            half.characters,
            agent,
            event="prompt_assembled",
            agent=agent,
            characters=half.characters,
            sources=half.sizes(),
        )

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
        self._output.reply_started()
        heard_s = round(len(pcm) / 2 / PIPELINE_SAMPLE_RATE, 2)
        self._turn = TurnUnderway()
        try:
            if result is None:
                # On the session's clock, which is the loop's: the
                # record's one duration measured outside an event is
                # read through the same thing that stamps the offsets it
                # sits beside.
                started = self._events.now()
                async with self._watching("asr", providers.asr):
                    result = await providers.asr.transcribe(
                        pcm, PIPELINE_SAMPLE_RATE, language_hint=self._asr_language
                    )
                # Only where this turn ran one. A reply handed a
                # transcription reuses a confirmed barge-in's, measured
                # at a different call site as part of a different
                # decision, and a null here says "not measured this
                # turn" rather than reporting somebody else's wait.
                self._turn.asr_ms = round((self._events.now() - started) * 1000)
            # ASR is done, so the mid-ASR marker comes down: from here a
            # barge-in has nothing of the user's left to destroy.
            self._reply_pcm = None
            if result.lock_language is not None:
                self._asr_language = result.lock_language
            transcript = result.text.strip()
            if transcript:
                await self._output.show_transcript(transcript)
                # Only engines that detected carry these; a mock or a
                # pinned language adds no noise to the record.
                language_fields: dict[str, Any] = {}
                if result.language is not None:
                    language_fields["language"] = result.language
                if result.language_confidence is not None:
                    language_fields["language_confidence"] = round(
                        result.language_confidence, 2
                    )
                # What was heard, never the words: the utterance is
                # content and the conversation store is where content
                # lives (#120, the content-and-telemetry ADR). What the
                # event keeps is what an operator measures with, which
                # is how long the user spoke and what language the
                # engine heard it in; the sentence renders exactly that,
                # so the two halves of this record say the same thing.
                heard_at = self._events.info(
                    "session %s: heard %.2f s of speech",
                    self.session_id,
                    heard_s,
                    event="heard",
                    agent=self._agent,
                    duration_s=heard_s,
                    **language_fields,
                )
                # The emission's own reading rather than a second one
                # taken beside it: the store measures both offsets from
                # the same origin, so two readings a microsecond apart
                # put the turn and its `heard` in different milliseconds
                # whenever they straddle a boundary.
                self._turn.heard_utterance(
                    heard_at,
                    transcript,
                    heard_s,
                    language_fields.get("language"),
                    language_fields.get("language_confidence"),
                )
            else:
                logger.info("session %s: nothing transcribed", self.session_id)
            if transcript:
                self._turns.append(Turn("user", transcript))
                self._arm_filler()
                await self._speak_reply(transcript, spoken)
        except DeviceGone:
            # The device went away mid-reply. Only this type: the edge
            # translates both of the transport's disconnect shapes into
            # it, so a bare `RuntimeError` arriving here is a bug in
            # this process (#137) and belongs on the record below rather
            # than being read as a disconnect and returned on in
            # silence.
            return
        except asyncio.CancelledError:
            # A barge-in or an abort is cancelling this reply, and the
            # filler is reply audio: it dies with the reply rather than
            # being waited out. The settle below still awaits the
            # cancellation through.
            if self._filler_task is not None:
                self._filler_task.cancel()
            raise
        except Exception as exc:
            # The class name, and nothing else. No `exc_info`, and no
            # `str(exc)`: since the catch above narrowed, this arm
            # catches every provider failure too, and what a failure
            # from the wire carries is untrusted. `providers/kit.py`
            # sanitizes the taxonomy's own message, but a traceback
            # rendered here would print the whole chain behind it, and
            # an exception raised anywhere near a response body can
            # embed one in its message. The logs the observability ADR
            # makes the retained surface are not the place to find that
            # out. What stays diagnosable: `provider_failed` names the
            # stage, the provider and the host for anything that failed
            # on the wire, and this line names the class for the rest.
            logger.error(
                "session %s: reply failed: %s", self.session_id, type(exc).__name__
            )
        finally:
            # Before the closing tts stop: an unfired timer is stood
            # down, and a clip already sounding finishes rather than
            # being cut mid-word by the stop.
            await self._settle_filler()
            self._reply_pcm = None
            # The other end the idle timeout counts from. In the finally,
            # so a reply that failed or was cancelled still resets the
            # clock: the user is owed the full silence before being hung
            # up on either way.
            if spoken:
                said = " ".join(spoken)
                self._turns.append(Turn("assistant", said))
                # What the reply was, not what it said (#120). The count
                # is the sentences whose audio actually went out, so a
                # reply cut short by a barge-in reports what the user
                # heard rather than what was generated, and it is the
                # one size on this event that is measured rather than
                # inferred.
                self._events.info(
                    "session %s: %s replied in %d sentences",
                    self.session_id,
                    self._agent,
                    len(spoken),
                    event="replied",
                    agent=self._agent,
                    sentences=len(spoken),
                )
            # Beside `replied` and for the same reason: this is where a
            # reply ends however it ended, so a cancelled or a failed one
            # records what its finally sees rather than nothing at all.
            if self._recorder is not None:
                self._record_turn(spoken)
            # Broad on purpose, and narrow in what it covers: the one
            # statement inside is a device send, so the `RuntimeError`
            # half can only be the transport's, and this closing pair
            # is not worth a report whichever way it fails.
            with contextlib.suppress(DeviceGone, RuntimeError):
                # A reply that never spoke still sends the pair. The
                # device leaves its speaking state on `tts stop`, and in
                # auto mode that is what re-arms its listening, so a
                # `stop` it was never told to expect is the one way this
                # could strand a device.
                await self._output.finish_speaking()

    def _record_turn(self, spoken: Sequence[str]) -> None:
        """Hand the finished turn to the content channel.

        Under the same guard an event tap gets, and for the same reason:
        a consumer nobody has met yet must not be able to cost the device
        the closing `tts stop` that follows this line, which in auto mode
        is what re-arms its listening. The class name and nothing else,
        because a recorder may be holding whatever a far side answered
        it with."""
        assert self._recorder is not None
        record = self._turn.record(self._agent, spoken)
        if record is None:
            return
        try:
            self._recorder.record_turn(record)
        except Exception as exc:  # noqa: BLE001 - a consumer never breaks a reply
            logger.warning(
                "session %s: the turn recorder failed and was skipped: %s",
                self.session_id,
                type(exc).__name__,
            )

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
            said = " ".join(spoken) if spoken else None
            if said is not None:
                self._turns.append(Turn("assistant", said))
                # This leg's share of the reply, in the same terms
                # `replied` reports the whole of it: which agent, and how
                # many sentences of it the user heard. Never the words,
                # which are the store's (#120).
                self._events.info(
                    "session %s: %s said %d sentences",
                    self.session_id,
                    self._agent,
                    len(spoken),
                    event="agent_said",
                    agent=self._agent,
                    sentences=len(spoken),
                )
                spoken.clear()
            previous = self._agent
            # Closed whether or not this agent spoke: a leg that only
            # asked for the handover still spent tokens, and the leg is
            # the only place they can be attributed to the agent that
            # spent them.
            self._turn.leg_ended(previous, said)
            self._activate_agent(target)
            switches_left -= 1
            self._events.info(
                "session %s: handed over from agent %s to %s",
                self.session_id,
                previous,
                target,
                event="handover",
                from_agent=previous,
                to_agent=target,
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
        resampler = Resampler(providers.tts.sample_rate, self._output.output_sample_rate)
        self._output.restart_pacing()

        switch_to: str | None = None
        for round_index in range(MAX_TOOL_ROUNDS):
            choice: ToolChoice = "none" if round_index == MAX_TOOL_ROUNDS - 1 else "auto"
            splitter = SentenceSplitter()
            leg: list[str] = []
            calls: list[ToolCall] = []
            # Where each of those calls is on the turn's record, filled
            # in the moment the calls are known and read after the block
            # below has ended one way or another.
            slots: list[int] = []
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
            # Resolved before the request is built, and per round rather
            # than per reply, because that is the memory block's clock.
            system = await self._system_prompt()
            try:
                async for event in self._watchdog_stream(
                    providers.llm,
                    functools.partial(providers.llm.stream, system, working, tools, choice),
                ):
                    if isinstance(event, StreamStarted):
                        # Liveness, not content. The watchdog consumes
                        # the one the adapters yield; this keeps the
                        # loop indifferent should one arrive anyway.
                        continue
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
                # The earliest point the model's calls exist: both
                # adapters assemble them after their stream has ended.
                # Reserved here rather than at the dispatch because
                # everything between the two can end the reply (the last
                # sentence's synthesis failing, a barge-in cancelling
                # mid-execution), and a call the model issued belongs on
                # the record whether or not it ever ran.
                slots = self._reserve_tools(calls)
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
            results, switch_to = await self._run_tools(calls, slots, switches_left)
            if switch_to is not None:
                break
            working.append(Turn("tool", "", tool_results=tuple(results)))

        # Drain the resampler's interpolation tail and the encoder's
        # partial frame, which flushing pads with silence.
        batch = self._output.encode_audio(resampler.flush()) + self._output.flush_encoder()
        await self._send_reply_audio(batch)
        return switch_to

    def _tool_snapshot(self) -> list[ToolDef]:
        """What the active agent may reach this reply: the builtins that
        apply, the device's tools once discovery has finished, and the
        tools of the MCP servers it is granted that are up.

        Taken per reply rather than per session, so a server that came
        back, a device that finished discovering and a reload that
        landed mid-conversation are all picked up on the next utterance.
        Which servers the agent is granted is the registry's answer
        rather than this session's configuration, for the same reason:
        the grants are what a reload swaps."""
        assert self._agent is not None
        tools: list[ToolDef] = []
        # A device bound to one agent has nowhere to switch, so it gets
        # no dead tool.
        if len(self._agents) > 1:
            tools.append(builtin.switch_agent_tool(self._agents))
        if self._memory is not None:
            tools.append(builtin.remember_tool())
        tools.extend(self._output.device_tools())
        tools.extend(self._mcp_servers.tools_for_agent(self._agent))
        return tools

    async def _run_tools(
        self, calls: Sequence[ToolCall], slots: Sequence[int], switches_left: int
    ) -> tuple[list[ToolResult], str | None]:
        """Execute one round of calls. Everything but switch_agent runs
        concurrently, since device and server tools are independent;
        switch_agent is resolved here instead, because a successful one
        ends the loop rather than producing a result the model reads.

        `slots` says where on the turn's record each of these calls was
        already reserved, index for index with `calls`, which is why
        both halves are split out of one enumeration rather than
        rebuilt: a handover the model asked for third keeps the third
        call's place, whatever order this method runs things in."""
        plain = [
            (slots[index], call)
            for index, call in enumerate(calls)
            if call.name != names.SWITCH_AGENT
        ]
        handovers = [
            (slots[index], call)
            for index, call in enumerate(calls)
            if call.name == names.SWITCH_AGENT
        ]
        results = list(
            await asyncio.gather(*(self._run_one(call, slot) for slot, call in plain))
        )

        switch_to: str | None = None
        for order, (slot, call) in enumerate(handovers):
            refusal = self._refuse_handover(call, switches_left, order)
            if refusal is not None:
                results.append(refusal)
                # An error result and no duration: nothing ran, and the
                # refusal is what the turn's record shows in place of it.
                self._turn.executed(slot, refusal.content, True, None)
                continue
            switch_to = str(call.arguments["agent"])
            # A successful switch answers the model nothing, so the
            # reservation is already the whole of its record: no result
            # and no duration. It stays on the record all the same,
            # because the handover is otherwise only implied by the legs
            # it produced.
        return results, switch_to

    def _refuse_handover(
        self, call: ToolCall, switches_left: int, order: int
    ) -> ToolResult | None:
        """Why this switch_agent cannot happen, as an error result the
        current agent phrases in its own voice and language, or None
        when it can.

        `order` is which switch_agent of this round it is, not its place
        in the model's call list: what a second one is refused for is
        being the second the loop resolves."""
        if switches_left <= 0 or order > 0:
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

    async def _run_one(self, call: ToolCall, slot: int) -> ToolResult:
        """One tool call, bounded and never raising into the loop. Every
        failure becomes an error result: the model explains it in its
        own words, where a canned apology would be fixed-language and
        would throw away whatever the model could still salvage.

        `slot` is where this call was reserved on the turn's record, and
        it is filled in below only once there is something to say about
        it. A cancellation on the way through leaves it as reserved,
        which is what a call the user talked over looks like."""
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
        fields, named = _tool_named(classified)
        self._events.info(
            "session %s: %s tool%s took %.2f s%s",
            self.session_id,
            classified.source,
            named,
            elapsed,
            " and failed" if is_error else "",
            event="tool_call",
            agent=self._agent,
            source=classified.source,
            **fields,
            duration_ms=round(elapsed * 1000),
            is_error=is_error,
        )
        self._turn.executed(slot, content, is_error, round(elapsed * 1000))
        return ToolResult(tool_call_id=call.id, content=content, is_error=is_error)

    def _reserve_tools(self, calls: Sequence[ToolCall]) -> list[int]:
        """Put every call this round issued on the turn's record, at the
        position the model issued it, and answer where each one landed."""
        return [
            self._turn.reserve(self._classified(call, position))
            for position, call in enumerate(calls)
        ]

    def _classified(self, call: ToolCall, position: int) -> ToolInvocation:
        """The half of a call's record that is known before it runs:
        where its name came from, and what the model asked with it.

        Classified here rather than at the dispatch, and so before
        anything can stop the dispatch from happening. That is also what
        closes the set over the paths the routing hides: a malformed
        call, whose arguments are the model's own bytes rather than a
        JSON object, is flagged and carries none of them, and its name
        is classified anyway, because a model that mangles its arguments
        still says which tool it meant."""
        malformed = call.malformed_arguments is not None
        source, entry = tool_source(
            call.name,
            {tool.name for tool in self._output.device_tools()},
            self._mcp_servers.owner_of(call.name),
        )
        return ToolInvocation(
            position=position,
            source=source,
            entry=entry,
            name=call.name,
            malformed=malformed,
            arguments=None if malformed else dict(call.arguments),
        )

    async def _dispatch(self, call: ToolCall) -> tuple[str, bool]:
        """Route a call by the structure of its name: builtins are bare,
        the device's tools are the ones it listed, and everything else
        carries its MCP server entry as a prefix."""
        if call.malformed_arguments is not None:
            # The size, not the bytes. What a model streamed instead of a
            # JSON object is its own output, which is content and belongs
            # to the store (#120); the length is what tells a truncated
            # object from a model that answered in prose, and it is the
            # half of this line anybody diagnosing ever used. The record
            # carries the same fact as its `malformed` flag.
            logger.warning(
                "session %s: tool %s got %d characters of unparseable arguments",
                self.session_id,
                call.name,
                len(call.malformed_arguments),
            )
            return "the arguments were not a JSON object; call again with valid ones", True
        if call.name == names.REMEMBER and self._memory is not None:
            assert self._agent is not None
            return await builtin.remember(self._memory, self._agent, call.arguments), False
        if any(tool.name == call.name for tool in self._output.device_tools()):
            return await self._output.call_device_tool(call.name, call.arguments)
        if self._mcp_servers.owner_of(call.name) is not None:
            assert self._agent is not None
            # The agent goes with the call: the registry checks its
            # grant again there, so a tool the snapshot withheld is
            # refused rather than run when a model asks for it anyway.
            return await self._mcp_servers.call(call.name, call.arguments, self._agent)
        return f'there is no tool called "{call.name}"', True

    def _timeout_for(self, name: str) -> float:
        """A server tool gets its entry's configured timeout; builtins
        and device tools the module default.

        Which entry owns the name is the registry's answer rather than
        this module's reading of the name, the same answer the dispatch
        below routes by, so a tool cannot be run against one entry's
        timeout and dispatched to another."""
        entry = self._mcp_servers.owner_of(name)
        if entry is not None:
            configured = self._mcp_servers.timeout_for(entry)
            if configured is not None:
                return configured
        return DEFAULT_TOOL_TIMEOUT_S

    async def _system_prompt(self) -> str:
        """The prompt this round is sent: the half cached at activation,
        plus whatever the agent remembers right now.

        The half is not rebuilt here. What this adds is the memory
        block, which keeps the clock it has always had: read on every
        round, so a fact remembered in one session is known to a
        concurrent one on its next reply, which is a contract that
        predates this split.

        The read itself is filesystem I/O and runs in a worker thread
        rather than on the loop every live conversation shares. It is
        resolved before the request is built, which is what lets the
        assembler stay a pure function of the text it is handed.
        """
        assert self._know_how is not None and self._agent is not None
        if self._memory is None:
            return self._know_how.text
        facts = await asyncio.to_thread(self._memory.read, self._agent)
        return prompt.with_memory(self._know_how, facts).text

    async def _speak_after(
        self,
        speaking: asyncio.Task[None] | None,
        sentence: str,
        tts: TtsProvider,
        resampler: Resampler,
        leg: list[str],
        spoken: list[str],
    ) -> asyncio.Task[None]:
        """The lookahead, with this session's failure reporting, its
        first-audio measurement, and its way of actually speaking a
        synthesis bound in.

        The measurement is bound to the synthesis's place in the reply
        rather than to the moment it answers: only the first request
        waited against silence, and a later one that happened to answer
        first spent its wait against playback already happening."""
        index = self._turn.synthesis_started()
        return await speak_after(
            speaking,
            sentence,
            tts,
            lambda exc, elapsed: self._provider_failed("tts", tts, exc, elapsed),
            lambda elapsed_ms: self._turn.first_audio(index, elapsed_ms),
            lambda synthesis: self._speak_and_record(synthesis, resampler, leg, spoken),
        )

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
        await self._output.begin_speaking()
        await self._output.sentence_started(synthesis.sentence)
        try:
            async for chunk in synthesis.chunks():
                await self._send_reply_audio(
                    self._output.encode_audio(resampler.process(chunk))
                )
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
        speech_ms = round(self._endpointer.speech_ms()) if self._endpointer is not None else 0
        pcm = self._trimmed_utterance()
        self._reset_utterance()
        # Reported before any of the gates below can drop the utterance:
        # somebody talked, whether or not it earns a reply, and the edge
        # counts the idle timeout from both ends of a turn.
        self._output.user_turn_ended()
        result: AsrResult | None = None
        if self.replying():
            if not self._config.server.barge_in:
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
                self._events.info(
                    "session %s: barge-in, cancelling the reply in flight",
                    self.session_id,
                    event="barge_in",
                    speech_ms=speech_ms,
                    **self._speaking_ms_field(),
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
        server = self._config.server
        if speech_ms < server.barge_in_min_speech_ms:
            self._events.info(
                "session %s: barge-in suppressed, %d ms of speech is under the "
                "%.0f ms floor",
                self.session_id,
                speech_ms,
                server.barge_in_min_speech_ms,
                event="barge_in_suppressed",
                reason="min_speech",
                speech_ms=speech_ms,
            )
            return None
        if self._reply_pcm is not None:
            head = self._reply_pcm
            self._events.info(
                "session %s: barge-in mid-transcription, merging the utterances",
                self.session_id,
                event="barge_in_merged",
                speech_ms=speech_ms,
            )
            await self._cancel_reply()
            return head + pcm, None
        loop = asyncio.get_running_loop()
        if (
            self._output.speaking_started_at() is not None
            and (loop.time() - self._output.speaking_started_at()) * 1000
            < server.barge_in_refractory_ms
        ):
            self._events.info(
                "session %s: barge-in suppressed inside the refractory window",
                self.session_id,
                event="barge_in_suppressed",
                reason="refractory",
                speech_ms=speech_ms,
            )
            return None
        assert self._providers is not None
        self._pause_output()
        try:
            # In the receive path on purpose: incoming frames buffer in
            # the socket for the duration, so ordering is unaffected.
            async with self._watching("asr", self._providers.asr):
                result = await self._providers.asr.transcribe(
                    pcm, PIPELINE_SAMPLE_RATE, language_hint=self._asr_language
                )
        except Exception:
            logger.exception("session %s: barge-in confirmation failed", self.session_id)
            self._resume_output()
            return None
        if not result.text.strip():
            self._events.info(
                "session %s: barge-in suppressed, nothing transcribed",
                self.session_id,
                event="barge_in_suppressed",
                reason="no_transcript",
                speech_ms=speech_ms,
            )
            self._resume_output()
            return None
        self._events.info(
            "session %s: barge-in, cancelling the reply in flight",
            self.session_id,
            event="barge_in",
            speech_ms=speech_ms,
            **self._speaking_ms_field(),
        )
        await self._cancel_reply()
        # The pause belonged to the cancelled reply; the one about to
        # answer starts with the frames flowing. Resuming rather than
        # clearing by hand shifts a pacing clock the next agent leg
        # restarts from scratch anyway.
        self._resume_output()
        return pcm, result

    def _speaking_ms_field(self) -> dict[str, int]:
        """The barge_in event's speaking_ms: milliseconds from
        speaking_started to the cancel decision, absent when the reply
        had not yet spoken."""
        if self._output.speaking_started_at() is None:
            return {}
        elapsed = asyncio.get_running_loop().time() - self._output.speaking_started_at()
        return {"speaking_ms": round(elapsed * 1000)}

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
        pre_roll = int(self._config.server.utterance_pre_roll_ms / 1000 * PIPELINE_SAMPLE_RATE) * 2
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
        if self._output.speaking_started_at() is not None:
            return
        speech_ms = round(self._endpointer.speech_ms()) if self._endpointer is not None else 0
        if speech_ms > 0:
            self._events.info(
                "session %s: filler skipped, the user is speaking (%d ms heard)",
                self.session_id,
                speech_ms,
                event="filler_skipped",
                agent=self._agent,
                reason="user_speaking",
                speech_ms=speech_ms,
            )
            return
        if self._output_paused:
            self._events.info(
                "session %s: filler skipped, a barge-in is being confirmed",
                self.session_id,
                event="filler_skipped",
                agent=self._agent,
                reason="barge_in_pending",
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
        self._events.info(
            "session %s: no reply audio after %d ms, playing filler %d",
            self.session_id,
            elapsed_ms,
            index,
            event="filler_played",
            agent=self._agent,
            delay_ms=elapsed_ms,
            phrase_index=index,
        )
        try:
            await self._output.begin_speaking()
            resampler = Resampler(clips.sample_rate, self._output.output_sample_rate)
            # Encoded whole before the first await, and sent once. The
            # reply task feeds the same encoder between its own awaits,
            # so a flush split off after an await could carry out audio
            # that belongs to the reply.
            batch = (
                self._output.encode_audio(resampler.process(clips.clips[index]))
                + self._output.encode_audio(resampler.flush())
                + self._output.flush_encoder()
            )
            await self._output.send_audio(batch)
        except (DeviceGone, RuntimeError):
            # Broader than the reply body's, and knowingly so. The `try`
            # above covers resampling, encoding and the encoder flush as
            # well as the send, so the `RuntimeError` half can still be
            # a local bug swallowed as a disconnect. Narrowing it means
            # deciding what a filler that fails to encode should do,
            # which is the filler path's own question and belongs to
            # #141 rather than to #137.
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


def bespoke_runtime_factory(
    config: Config,
    agent_providers: dict[str, AgentProviders],
    mcp_servers: McpServers,
    memory: MemoryStore | None,
    fillers: dict[str, FillerClips],
    conversations: TurnStore | None = None,
) -> RuntimeFactory:
    """The composition root's half of the seam: everything this runtime
    needs that outlives one connection, closed over once at startup.

    The device edge calls what comes back with a device to speak
    through, the session's observability, and the agents the device is
    bound to, and never learns what an LLM is. `fillers` is the mutable
    dict the boot fills once synthesis has run, so a factory built
    before the clips exist still sees them.

    `conversations` is closed over the same way, and is the reason the
    recorder reaches a runtime without the `RuntimeFactory` type moving:
    the store outlives every connection, and the per-session channel is
    derived here from the identity the edge already hands over. None
    means no store, which is every deployment that has not asked for one.

    Deliberately one function rather than a config-selectable registry:
    one runtime exists, and a selection mechanism with one option is
    surface without a reader. This is the seam a second runtime plugs
    into."""

    def build(
        output: DeviceOutput, events: SessionEvents, agents: Sequence[str]
    ) -> SessionInput:
        return PipelineRuntime(
            output,
            config,
            events,
            agent_providers,
            mcp_servers,
            memory,
            fillers,
            agents,
            None if conversations is None else SessionTurns(conversations, events.session_id),
        )

    return build
