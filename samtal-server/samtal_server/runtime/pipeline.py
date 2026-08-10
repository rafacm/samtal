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
sentence, and as structured `extra=` fields that the JSON log format
emits as top-level keys, through the session's `SessionEvents` so that
every record carries the same channel and the same identity whichever
side of the boundary emitted it.
"""

import asyncio
import contextlib
import functools
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from starlette.websockets import WebSocketDisconnect

from samtal_server.audio.resample import Resampler
from samtal_server.config import Config
from samtal_server.device.boundary import PIPELINE_SAMPLE_RATE
from samtal_server.device.events import SessionEvents, logger
from samtal_server.protocol import messages
from samtal_server.providers import (
    AgentProviders,
    AsrResult,
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
from samtal_server.runtime.speech import _Synthesis, speak_after
from samtal_server.text import SentenceSplitter
from samtal_server.tools import builtin, names
from samtal_server.tools.mcp import McpServers
from samtal_server.tools.memory import MemoryStore

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

    `session` is the device it speaks through. It is still the concrete
    session while the extraction is in flight; the narrowing commit
    replaces it with the `DeviceOutput` half of the boundary, which is
    all that is left of it by then.
    """

    def __init__(
        self,
        session: Any,
        config: Config,
        events: SessionEvents,
        agent_providers: dict[str, AgentProviders],
        mcp_servers: McpServers,
        memory: MemoryStore | None,
    ) -> None:
        self._session = session
        self._config = config
        self._events = events
        self.session_id = events.session_id
        self._agent_providers = agent_providers
        self._mcp_servers = mcp_servers
        self._memory = memory
        # The agents this device may talk to, and the one it is talking
        # to now. The active one lives on the events object, because
        # both sides of the boundary attribute events to it.
        self._agents: list[str] = []
        self._providers: AgentProviders | None = None
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

    @property
    def _agent(self) -> str | None:
        return self._events.agent

    @_agent.setter
    def _agent(self, name: str | None) -> None:
        self._events.agent = name

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
                logger.warning(
                    "session %s: no first token after %.1f s, retrying round %d",
                    self.session_id,
                    elapsed,
                    self._llm_round,
                    extra=self._events.event(
                        "llm_retry",
                        agent=self._agent,
                        round=self._llm_round,
                        duration_ms=round(elapsed * 1000),
                        **provider_fields("llm", provider),
                    ),
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
        logger.info(
            "session %s: %s round %d took %.2f s over %d turns",
            self.session_id,
            self._agent,
            self._llm_round,
            elapsed,
            len(working),
            extra=self._events.event(
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
            extra=self._events.event(
                "provider_failed",
                agent=self._agent,
                error=type(exc).__name__,
                duration_ms=round(elapsed * 1000),
                **fields,
            ),
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

        The device's bound list is enforced here rather than left to
        callers, because the next caller is a tool whose argument a model
        chose: an agent that merely exists is not one this device may
        talk to. Nothing is swapped when the name is refused, so the
        session keeps the agent it already had."""
        if name not in self._agents:
            raise _not_allowed(name, self._agents)
        self._agent = name
        self._providers = self._agent_providers[name]
        self._session._endpointer = self._providers.vad.new_endpointer()
        self._session._reset_utterance()

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
        self._session._speaking_started = False
        self._session._speaking_started_at = None
        self._session._tts_started = False
        heard_s = round(len(pcm) / 2 / PIPELINE_SAMPLE_RATE, 2)
        try:
            if result is None:
                async with self._watching("asr", providers.asr):
                    result = await providers.asr.transcribe(
                        pcm, PIPELINE_SAMPLE_RATE, language_hint=self._asr_language
                    )
            # ASR is done, so the mid-ASR marker comes down: from here a
            # barge-in has nothing of the user's left to destroy.
            self._session._reply_pcm = None
            if result.lock_language is not None:
                self._asr_language = result.lock_language
            transcript = result.text.strip()
            if transcript:
                await self._session.websocket.send_text(
                    messages.stt_message(self.session_id, transcript)
                )
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
                    extra=self._events.event(
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
                self._session._arm_filler()
                await self._speak_reply(transcript, spoken)
        except (WebSocketDisconnect, RuntimeError):
            return  # the device went away mid-reply
        except asyncio.CancelledError:
            # A barge-in or an abort is cancelling this reply, and the
            # filler is reply audio: it dies with the reply rather than
            # being waited out. The settle below still awaits the
            # cancellation through.
            if self._session._filler_task is not None:
                self._session._filler_task.cancel()
            raise
        except Exception:
            logger.exception("session %s: reply failed", self.session_id)
        finally:
            # Before the closing tts stop: an unfired timer is stood
            # down, and a clip already sounding finishes rather than
            # being cut mid-word by the stop.
            await self._session._settle_filler()
            self._session._reply_pcm = None
            # The other end the idle timeout counts from. In the finally,
            # so a reply that failed or was cancelled still resets the
            # clock: the user is owed the full silence before being hung
            # up on either way.
            self._session._mark_activity()
            if spoken:
                said = " ".join(spoken)
                self._turns.append(Turn("assistant", said))
                logger.info(
                    'session %s: replied "%s"',
                    self.session_id,
                    said,
                    extra=self._events.event("replied", agent=self._agent, text=said),
                )
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                # A reply that never spoke still sends the pair. The
                # device leaves its speaking state on `tts stop`, and in
                # auto mode that is what re-arms its listening, so a
                # `stop` it was never told to expect is the one way this
                # could strand a device.
                await self._session._begin_speaking()
                await self._session.websocket.send_text(
                    messages.tts_message(self.session_id, "stop")
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
            if spoken:
                said = " ".join(spoken)
                self._turns.append(Turn("assistant", said))
                logger.info(
                    'session %s: %s said "%s"',
                    self.session_id,
                    self._agent,
                    said,
                    extra=self._events.event("agent_said", agent=self._agent, text=said),
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
                extra=self._events.event("handover", from_agent=previous, to_agent=target),
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
        resampler = Resampler(providers.tts.sample_rate, self._session.output_sample_rate)
        self._session._pace_start = None
        self._session._pace_count = 0

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
        packets = self._session._encoder.encode(resampler.flush()) + self._session._encoder.flush()
        await self._session._send_frames(packets)
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
        if self._session._device_tools is not None:
            tools.extend(self._session._device_tools.tools())
        tools.extend(self._mcp_servers.tools_for(self._config.mcp_for_agent(self._agent)))
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
            extra=self._events.event(
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
        if self._session._device_tools is not None and self._session._device_tools.knows(call.name):
            return await self._session._device_tools.call(call.name, call.arguments)
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
        """The lookahead, with this session's failure reporting and its
        way of actually speaking a synthesis bound in."""
        return await speak_after(
            speaking,
            sentence,
            tts,
            lambda exc, elapsed: self._provider_failed("tts", tts, exc, elapsed),
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
        await self._session._begin_speaking()
        await self._session.websocket.send_text(
            messages.tts_message(self.session_id, "sentence_start", text=synthesis.sentence)
        )
        try:
            async for chunk in synthesis.chunks():
                await self._session._send_frames(
                    self._session._encoder.encode(resampler.process(chunk))
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
