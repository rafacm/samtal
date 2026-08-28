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
`SessionEvents` ([events](../events/__init__.py)), so that every record carries
the same channel and the same identity whichever side of the boundary
emitted it, and so that every consumer of the events sees it.
"""

import asyncio
import contextlib
import functools
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from vinga_server.audio.resample import Resampler
from vinga_server.conversations.records import (
    Acknowledgement,
    SessionTurns,
    ToolInvocation,
    TurnRecorder,
    TurnStore,
)
from vinga_server.device.boundary import (
    PIPELINE_SAMPLE_RATE,
    DeviceGone,
    DeviceOutput,
    PlayableAudio,
    RuntimeFactory,
    SessionInput,
)
from vinga_server.events import SessionEvents, assembly, logger
from vinga_server.events.catalog import (
    AgentSaid,
    ConversationResumed,
    Handover,
    Heard,
    PromptAssembled,
    Replied,
    Variant,
)
from vinga_server.events.values import (
    ABSENT,
    ConversationId,
    Count,
    Flag,
    Fragment,
    Identifier,
    LanguageTag,
    PromptSources,
    Real,
)
from vinga_server.filler import FillerClips
from vinga_server.generation import Generation, Generations
from vinga_server.providers import (
    AgentProviders,
    AsrResult,
    LlmEvent,
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
from vinga_server.runtime import prompt, resumption
from vinga_server.runtime.filler_runner import FillerRunner
from vinga_server.runtime.speech import _Synthesis, speak_after
from vinga_server.runtime.turns import BUILTIN, MCP, TurnUnderway, tool_source
from vinga_server.runtime.turntaking import TurnTaking
from vinga_server.text import SentenceSplitter
from vinga_server.tools import builtin, names
from vinga_server.tools.mcp import McpServers
from vinga_server.tools.memory import MemoryStore
from vinga_server.tools.source import BuiltinTools, DeviceTools, McpTools, ToolSource

# How many times one reply may stream, call tools, and stream again.
# The last permitted round forbids calling, so a reply always ends in
# speech rather than in a tool nobody hears the result of.
MAX_TOOL_ROUNDS = 4

# How long a builtin or a device tool may take. Server tools use their
# own entry's tool_timeout_s. The device hears silence meanwhile, which
# is why this is not generous.
DEFAULT_TOOL_TIMEOUT_S = 15.0

# The synthetic user turn a conversation opens on when a reply moves to
# one, in the three shapes a move has. It exists because both APIs need
# a fresh completion to end on a user turn, and because a thread that
# began with an assistant greeting would read as an answer to nobody.
#
# Written into the target thread's history rather than kept for one
# round, which is what changed when histories became per thread (#190):
# it is that conversation's opening line, the model reads it on every
# later round of the same thread, and it says nobody said it out loud.
# The stored record is untouched by it: a turn is what a user said and
# what was replied, and none of these three was said by anyone.
SWITCH_GREETING = (
    "You have just taken over this conversation from another assistant. "
    "Greet the user briefly as yourself, in the language they have been "
    "speaking, and carry on from what was said above."
)

FRESH_GREETING = (
    "The user has asked to start a new conversation. Nothing that was "
    "said earlier is available to you here. Greet them briefly as "
    "yourself, in the language they have been speaking, and ask what "
    "they would like to talk about."
)

RESUMED_GREETING = (
    "You have just resumed an earlier conversation with this user, and "
    "what is above is what was said in it. Greet them briefly, say in "
    "one sentence what the two of you were talking about, and carry on "
    "from there, in the language they have been speaking."
)

# What a resumed conversation is additionally told about its own record,
# appended to the seed above where each applies. Fixed sentences from a
# closed set, chosen where the resume is decided: what they exist for is
# an agent that does not claim to remember what it cannot see.
RESUMED_FROM_RECENT = (
    " Only the most recent part of that conversation is above; what came "
    "before it is not available to you, so do not claim to remember the "
    "whole of it."
)
RESUMED_WITH_GAPS = (
    " Parts of that conversation were never stored, so there are gaps in "
    "what you can see; say so plainly if the user asks about something "
    "that is missing."
)

# How long a resume waits for the turn it has just recorded on the
# target thread to be durably written, before rebuilding that thread's
# context out of the store.
#
# The read-your-writes bound, and it exists for one scenario: switching
# away from a conversation and back to it inside one session, where the
# turn that ended the first leg is still in the writer's queue while the
# resume is already reading the rows. Short, because it is a wait on the
# device's own turnaround and the writer commits at the marker the turn
# itself is; and bounded, because a database that is not answering must
# cost a resumed conversation its last turn rather than cost the user
# their reply.
RESUME_ACKNOWLEDGEMENT_S = 2.0

# The abort reasons a device may send that this side knows by name. The
# firmware's `AbortReason` enum has exactly two members
# (`main/protocols/protocol.h` in 78/xiaozhi-esp32): `kAbortReasonNone`,
# which sends no `reason` field at all, and
# `kAbortReasonWakeWordDetected`, which sends the string below.
#
# Closed rather than passed through because the field is a string a
# peer wrote, and the upstream protocol note says as much: "`reason`
# may be `wake_word_detected` or other implementation-defined values"
# (`docs/websocket.md`). Anything outside the set is logged as `other`
# and its value is not repeated anywhere, which is the same rule the
# tool-name events follow (#154, #185).
#
# Absence is not a member. A device that sends no reason is reported as
# `none`, and a device that sends the word "none" is a device sending a
# reason the firmware has no member for, which is `other` like any
# other unknown: the two facts stay distinguishable on the line.
DEVICE_ABORT_REASONS = frozenset({"wake_word_detected"})


def _reported(usage: Usage | None) -> tuple[int | None, int | None]:
    """What a round says about its size.

    Token counts appear where the provider reported them; their absence
    is a fact about the endpoint rather than a zero. Plain numbers,
    because the same two answers are read twice over: the event wraps
    them in its own value types, and the turn's record counts them.
    """
    return (
        usage.prompt_tokens if usage is not None else None,
        usage.completion_tokens if usage is not None else None,
    )


def _tool_fragment(classified: ToolInvocation) -> Fragment:
    """The fragment a sentence about one call renders where its name
    would go, which is nothing at all for the two namespaces this
    surface may not name.

    One home for that decision. `events/assembly.py` renders whichever
    name it is handed and refuses nothing, deliberately: deciding is
    reading the classifier's source constants, which live next door in
    `runtime/turns.py`. So the decision is here, where those constants
    are, and it is here ONCE, so the plain line about unparseable
    arguments and the `tool_call` event beside it cannot come to
    disagree about which names are this application's to print.
    """
    return assembly.tool_fragment(
        classified.name if classified.source == BUILTIN else None,
        classified.entry if classified.source == MCP else None,
    )


def _tool_called(
    classified: ToolInvocation,
    agent: str,
    conversation: str,
    duration_s: float,
    is_error: bool,
) -> Variant:
    """Which of the three `tool_call` shapes describes this call.

    The selection is here rather than in `events/assembly.py` because it
    reads the classifier's own constants, and `runtime/turns.py` spells
    those locally on purpose: `TOOL_SOURCES` is one structure, and a
    second home for it in the event vocabulary would be a second
    structure that has to agree. What each shape is made of is the
    assembly module's; which one this call is, is the classifier's
    neighbour's.
    """
    if classified.source == BUILTIN:
        return assembly.builtin_tool_called(
            agent, conversation, classified.name, duration_s, is_error
        )
    if classified.source == MCP and classified.entry is not None:
        return assembly.mcp_tool_called(
            agent, conversation, classified.entry, duration_s, is_error
        )
    return assembly.unnamed_tool_called(
        agent, conversation, classified.source, duration_s, is_error
    )


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


@dataclass(frozen=True)
class _Transition:
    """What a tool loop ended for, and what happens at the boundary.

    One type for the three moves, because they differ in what they
    rebind and agree on everything else: each ends the loop, lets the
    turn finish on the thread it started on, and opens a round on the
    other side seeded with a synthetic user turn. Handing the loop back
    a type rather than an agent name is what lets a resume travel with
    the history it brings.

    `agent` is set by a handover and by nothing else; `conversation` by
    the two moves that change which thread the agent is on. Exactly one
    of the two is ever set, which is the whole of the shape.
    """

    seed: str
    agent: str | None = None
    conversation: str | None = None
    history: tuple[Turn, ...] = ()
    # What the resume found, for the event emitted at the boundary. None
    # for a handover and for a fresh conversation, neither of which read
    # anything back.
    resumed: resumption.Resumed | None = None


class PipelineRuntime:
    """One conversation, for one connection, behind the device edge.

    `output` is the device it speaks through, and it is the whole of
    what this runtime knows about the far end: no socket, no protocol,
    no codec. Built by `bespoke_runtime_factory` below, which is what
    the composition root hands the device edge.

    Two of the things it used to do are now modules of their own. Who
    holds the floor is `TurnTaking` ([turntaking.py](turntaking.py)),
    which reaches back into four of this class's methods and nothing
    else; the latency mask is `FillerRunner`
    ([filler_runner.py](filler_runner.py)), which reads the floor with
    two questions and never writes it. What stays here is the
    orchestration: the reply task, the conversation history, the tool
    loop, agent handover, and the provider observability.

    The mutable state that crosses those responsibilities, listed
    because it is what a reader has to hold in mind at once (#141):

    - `_reply_task`: created by `start_reply`, read by `replying` and
      `drain`, cleared by `cancel_reply`. The turn-taking side and the
      device edge both ask about the reply in flight, and both ask
      through those methods, so the field itself keeps one owner.
    - `_providers` and `_know_how`: written by `_activate_agent` alone,
      at connect and at a handover, and read by every leg of the reply
      that follows, `confirm_transcript` included.
    - `_asr_language`: written where the reply's own ASR answers, read
      by that call and by the confirmation the gate ladder asks for, so
      the session's language lock survives an interruption.
    - `_turn`: the record being assembled, replaced at the start of each
      reply, written from half a dozen places in the loop, and read once
      at the end by `_record_turn`. The pair it is stamped with at
      replacement is the pair the turn is attributed to.
    - `_conversations`: one thread per agent this session has activated,
      written by `_activate_agent` alone and read through
      `self._conversation`, which is a property over the events object
      for the reason `_agent` is.
    - `_histories`: one conversation history per thread, appended by the
      reply path and by each agent leg, read wherever a round is built,
      and reached through `_turns`, which answers with the history of
      the thread the active agent is on.
    - `_resumption`: this session's offered-candidate state and its way
      into the store, absent where the deployment did not ask for
      resumption, which is what makes both conversation tools answer a
      spoken refusal instead of moving anything.
    - `_llm_round`: reset per reply, counted up per round, read by the
      watchdog's retry line and by `llm_round`, which is what makes the
      generation after a handover a round of its own.
    - `_agent`: a property over `self._events.agent` rather than a field,
      because both sides of the boundary attribute events to whoever is
      talking, so the events object is the one place it can live.
    """

    def __init__(
        self,
        output: DeviceOutput,
        generations: Generations,
        generation: Generation,
        events: SessionEvents,
        agent_providers: Mapping[str, AgentProviders],
        mcp_servers: McpServers,
        memory: MemoryStore | None,
        fillers: Mapping[str, FillerClips],
        agents: Sequence[str],
        recorder: TurnRecorder | None = None,
        threads: resumption.ThreadReads | None = None,
    ) -> None:
        self._output = output
        # The world this runtime reads its configuration out of, asked
        # rather than kept: a reload replaces it, and the two reads
        # below are at the two moments the answer is allowed to change
        # (#191). An activation assembles the prompt from the generation
        # current at that instant; the reply's own timeout is a
        # restart-only setting that every generation carries the same
        # value of.
        self._generations = generations
        # And the world this conversation was built from, kept because
        # an activation may have to fall back to it: an apply can delete
        # an agent this device is bound to, and a handover to it has to
        # read the prompt world it was last served with rather than
        # index a current world that has never heard of it. Everything
        # else this runtime speaks through came out of this object at
        # construction, so keeping it costs nothing that was not already
        # being held.
        self._generation = generation
        # The file half, taken once because a generation never replaces
        # it: a reload composes the stored domain half onto this
        # process's own server section, so every generation carries the
        # same one.
        self._server = generations.current().config.server
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
        # Whether this server can find a past conversation and pick it
        # up again, and the whole of what says so: absent unless the
        # deployment asked for it, and compared `is not None` by the two
        # tools and by the interception alike. The switch is read here
        # rather than by whoever built the read seam, so the answer to
        # "can this session resume" has one home.
        #
        # Per session, because what it holds is what this session's
        # agents were offered. The bound it reads the store under is the
        # tool bound, since every read it makes happens inside a tool
        # call the user is waiting through.
        section = self._server.conversations
        self._resumption = (
            resumption.Resumption(
                threads, section.resumption_budget_tokens, DEFAULT_TOOL_TIMEOUT_S
            )
            if threads is not None and section is not None and section.resumption
            else None
        )
        # The handle the store gave for the last turn recorded on each
        # thread, so a resume can wait for its own writes before reading
        # the thread back. Nothing on the audio path ever waits on one:
        # they are created by the recorder and kept here, and the only
        # reader is the resume that is about to hydrate.
        self._acknowledged: dict[str, Acknowledgement] = {}
        # This session's threads, one per agent it has activated. The
        # first activation of an agent mints a conversation id and every
        # later one continues it, which is what makes "Sophia, let me
        # talk to Nadia, back to Sophia" one session touching two
        # threads. Minted here rather than by the store, because the
        # boundary is decided at the activation seam and the id has to
        # exist before the first turn it stamps.
        self._conversations: dict[str, str] = {}
        # The turn being assembled, replaced at the start of every reply
        # and read once at the end of it. Always present rather than
        # optional: the reply path writes into it from half a dozen
        # places, and a guard at each of them would be six chances to
        # forget one. Installed below, after the first activation, since
        # it carries the pair that activation decides.
        self._turn: TurnUnderway
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
        # One history per thread, keyed by the conversation it belongs
        # to and reached through `_turns` below. A session used to keep
        # one transcript, which is what carried the whole of it across a
        # handover; a conversation is a thread now, so what a round is
        # written against is the thread it is on and nothing else.
        self._histories: dict[str, list[Turn]] = {}
        # Generation calls in the reply being spoken, counted across its
        # agents rather than per leg, so the one after a handover is a
        # round of its own in the logs.
        self._llm_round = 0
        # The language the ASR provider asked this session to reuse
        # (`AsrResult.lock_language`). Session-scoped on purpose: the
        # provider is shared between sessions and holds no per-session
        # state, and the speaker does not change on an agent switch.
        self._asr_language: str | None = None
        # Who holds the floor: the mic feed, the utterance buffer and
        # the barge-in gates. It reaches back into four of this
        # runtime's methods and nothing else, so the reply task and the
        # conversation history stay on this side of the seam.
        self._turntaking = TurnTaking(events, output, self._server, self)
        self._reply_task: asyncio.Task[None] | None = None
        # This turn's latency mask, if any agent this device is bound to
        # has one. It reads the floor with two questions and answers it
        # nothing, so the one field the two clusters share (whether the
        # outgoing frames are paused) has one writer and one reader and
        # crosses as a question.
        self._filler = FillerRunner(
            events,
            output,
            fillers,
            agents,
            self._turntaking,
        )
        self._agents = list(agents)
        # The three places a tool can come from, asked in the order the
        # namespace gives them: builtins are bare, the device's tools
        # carry its prefix, an MCP server's carry their entry's, and
        # configuration forbids an entry from taking either of the other
        # groups' names, so no two of these can own one name and the
        # order settles nothing that was in doubt. Each is handed the
        # default bound rather than reading it, so how long a builtin or
        # a device tool may take stays this module's answer.
        self._sources: tuple[ToolSource, ...] = (
            BuiltinTools(
                self._agents, memory, DEFAULT_TOOL_TIMEOUT_S, self._resumption
            ),
            DeviceTools(output, DEFAULT_TOOL_TIMEOUT_S),
            McpTools(mcp_servers, DEFAULT_TOOL_TIMEOUT_S),
        )
        # The activation the connect used to do by hand, and the MCP
        # revive that followed it, in that order. No task is spawned
        # here: the reply task is created on the first utterance, and
        # discovery belongs to the edge.
        self._activate_agent(self._agents[0])
        self._turn = self._fresh_turn()
        # A server that was down at boot, or that dropped since, gets a
        # background reconnect now, so it is picked up by the time this
        # conversation needs it rather than at the next server restart.
        # Which entries those are is asked of the registry rather than
        # resolved from this session's configuration: a reload replaces
        # the grants along with the managers they name, and the
        # configuration this session holds is the world it opened in.
        self._mcp_servers.revive_for_agents(self._agents)

    @property
    def _agent(self) -> str | None:
        return self._events.agent

    @_agent.setter
    def _agent(self, name: str | None) -> None:
        self._events.agent = name

    @property
    def _conversation(self) -> str | None:
        """The thread the active agent is on. A property over the events
        object for the reason `_agent` is one: both sides of the
        boundary attribute their events to it, and the edge's pacer
        stamps `speaking_started` without ever having activated
        anything."""
        return self._events.conversation

    @_conversation.setter
    def _conversation(self, conversation: str | None) -> None:
        self._events.conversation = conversation

    @property
    def _turns(self) -> list[Turn]:
        """The history of the thread the active agent is on.

        A property over the map for the reason `_conversation` is one
        over the events object: there is one right answer at any moment
        and every reader should be unable to reach a stale one. The
        three append sites and the one read site are unchanged by the
        split; which list they reach is this line.

        `setdefault` rather than a lookup, because a thread's first
        history is empty and minting one at the activation seam would be
        a second place that knows a thread exists.
        """
        assert self._conversation is not None
        return self._histories.setdefault(self._conversation, [])

    def _fresh_turn(self) -> TurnUnderway:
        """A turn beginning, stamped with the pair that owns it.

        The one place that pair is read off the runtime. Everything
        after it reads the snapshot, which is what keeps a handover turn
        on the thread it started on rather than the one it ended on.

        Both halves are asserted rather than defaulted: an agent is
        activated before this runtime can be asked for anything, and the
        thread is minted in that same activation, so a turn beginning
        without either is a defect here and not a row for the store to
        make sense of.
        """
        assert self._conversation is not None and self._agent is not None
        return TurnUnderway(self._conversation, self._agent)


    # --- SessionInput: what the device edge asks of this runtime -------

    async def audio(self, pcm: bytes) -> None:
        """One decoded mic frame, at `PIPELINE_SAMPLE_RATE`, for whoever
        is deciding who holds the floor."""
        await self._turntaking.feed(pcm)

    async def listen_started(self) -> None:
        """The device asked to listen. Which mode it asked in is the
        edge's business; what this side does is start a fresh
        utterance."""
        self._turntaking.restart()

    async def listen_stopped(self) -> None:
        """A manual end of utterance."""
        await self._turntaking.manual_stop()

    async def device_aborted(self, reason: str | None) -> None:
        """The device gave up on the answer: the reply in flight dies
        and the utterance starts over.

        The reason is named only when it is one this side knows
        (`DEVICE_ABORT_REASONS`), because it arrives as a free string
        from the far side of the wire and this line is kept. An abort
        that carried no reason at all is `none`; anything else this
        side does not know is `other`."""
        # Absence is `none` and a value this side does not know is
        # `other`, which are different facts: `reason or "none"` would
        # report an abort carrying an empty string as though the field
        # had not been sent, and an empty string is something a device
        # chose to send.
        if reason is None:
            token = "none"
        else:
            token = reason if reason in DEVICE_ABORT_REASONS else "other"
        logger.info("session %s: device aborted (%s)", self.session_id, token)
        await self.cancel_reply()
        self._turntaking.restart()

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
        await self.cancel_reply()

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
        await self._filler.tail()
        await self._output.send_audio(batch)

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
        self, provider: object, make_stream: Callable[[], AsyncIterator[LlmEvent]]
    ) -> AsyncIterator[LlmEvent]:
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
        timeout_s = self._server.llm_first_token_timeout_s
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
                # The loop variable is read by a thunk the emitter calls
                # before this iteration ends, so there is no late binding
                # for B023 to be about.
                self._events.emit(
                    lambda: assembly.llm_retried(
                        self._agent,
                        self._conversation,
                        "llm",
                        provider,
                        self._llm_round,
                        elapsed,  # noqa: B023
                    )
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
        them; their absence is a fact about the endpoint. They are named
        `input_tokens` and `output_tokens`, the GenAI conventions'
        vocabulary adapted to this project's field style (#120), which
        is also what the store's `turns` columns have been called since
        their first migration. The `Usage` dataclass keeps the SDK-shaped
        names it is filled from: it is not surface.

        `first_token_ms` times the first spoken token, so a round that
        only asked for a tool carries none: there was no token, and
        timing the tool call instead would report the whole generation
        as its own time to first token, since both providers assemble
        calls after the stream has ended."""
        loop = asyncio.get_running_loop()
        elapsed = loop.time() - began
        first_token_ms = (
            None if first_token_at is None else round((first_token_at - began) * 1000)
        )
        inputs, outputs = _reported(usage)
        self._events.emit(
            lambda: assembly.llm_rounded(
                self._agent,
                self._conversation,
                "llm",
                provider,
                self._llm_round,
                len(working),
                elapsed,
                inputs,
                outputs,
                first_token_ms,
            )
        )
        # Counted here rather than where the round starts, so that the
        # turn's rounds, its summed duration and its token totals all
        # describe one set of rounds: the ones that finished, which is
        # the set an `llm_round` row exists for.
        self._turn.round_done(round(elapsed * 1000), first_token_ms, inputs, outputs)

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
        self._events.emit(
            lambda: assembly.provider_failure(
                self._agent, self._conversation, stage, provider, exc, elapsed
            )
        )

    def _activate_agent(self, name: str) -> None:
        """Talk as this agent from now on: its prompt, its providers, and a
        fresh endpointer from its VAD, since the previous agent's endpointer
        carries the previous agent's tuning and mid-utterance state. Called
        once at connect, and again mid-reply when switch_agent hands the
        conversation over.

        The history does not carry across the switch, and that is the
        behavior #190 changed here: a conversation is a thread between a
        user and exactly one agent, so binding the incoming agent to its
        own thread is also binding it to its own history. What it starts
        with is the seed the transition writes; what it comes back to on
        a second handover is what it said the first time. Agents are
        scoped on purpose, and a switch that handed the whole session
        over would leak around that scoping and would move words spoken
        to a local agent to whatever provider the incoming one runs on.

        This is also where the know-how half of the system prompt is
        assembled, which is the whole of when it happens: at session
        open and again at an agent switch, and never per reply. Nothing
        is fetched here, because nothing needs to be: the persona is
        configuration this server is already holding and the guidance is
        what the registry's slice holds, so a reload that landed since
        is picked up by the next session or the next switch rather than
        by a conversation in flight.

        The device's bound list is enforced here rather than left to
        callers, because the next caller is a tool whose argument a model
        chose: an agent that merely exists is not one this device may
        talk to. Nothing is swapped when the name is refused, so the
        session keeps the agent it already had.

        Which world the prompt is read out of is the one decision here
        that is not obvious (#191). The current one, because that is
        what an activation converges at and the whole reason a reload
        reaches a conversation at all; the session's own when the
        current one has never heard of this agent, which is exactly the
        state an apply that deleted it leaves behind. This device is
        still bound to it and this conversation is still allowed to hand
        over to it, so it goes on being served the prompt world it was
        opened with rather than raising a KeyError inside a tool call.
        """
        if name not in self._agents:
            raise _not_allowed(name, self._agents)
        self._agent = name
        # The thread this agent is on in this session: minted the first
        # time it is activated and continued on every later activation,
        # so switching away and back returns to the same conversation.
        # A uuid hex, the same shape and role as the session id.
        self._conversation = self._conversations.setdefault(name, uuid.uuid4().hex)
        self._providers = self._agent_providers[name]
        current = self._generations.current().config
        config = current if name in current.agents else self._generation.config
        self._know_how = prompt.know_how(
            config.prompt_for_agent(name),
            config.fragments_for_agent(name),
            self._mcp_servers.guidance_for_agent(name),
        )
        self._prompt_assembled(name, self._know_how)
        self._turntaking.endpointer = self._providers.vad.new_endpointer()
        self._turntaking.restart()

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
        self._events.emit(
            lambda: PromptAssembled(
                agent=Identifier(agent),
                conversation=ConversationId(self._conversation),
                characters=Count(half.characters),
                sources=PromptSources(half.sizes()),
            )
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
        self._turn = self._fresh_turn()
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
            self._turntaking.clear_pending()
            if result.lock_language is not None:
                self._asr_language = result.lock_language
            transcript = result.text.strip()
            if transcript:
                await self._output.show_transcript(transcript)
                # Only engines that detected carry these; a mock or a
                # pinned language adds no noise to the record. Absent
                # rather than null, which are different answers: an
                # engine that detected nothing leaves no key rather than
                # a key holding nothing.
                confidence = result.language_confidence
                # What was heard, never the words: the utterance is
                # content and the conversation store is where content
                # lives (#120, the content-and-telemetry ADR). What the
                # event keeps is what an operator measures with, which
                # is how long the user spoke and what language the
                # engine heard it in; the sentence renders exactly that,
                # so the two halves of this record say the same thing.
                heard_at = self._events.emit(
                    lambda: Heard(
                        agent=Identifier(self._agent),
                        conversation=ConversationId(self._conversation),
                        duration_s=Real(heard_s),
                        language=(
                            ABSENT
                            if result.language is None
                            else LanguageTag(result.language)
                        ),
                        language_confidence=(
                            ABSENT if confidence is None else Real(round(confidence, 2))
                        ),
                    )
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
                    result.language,
                    None if confidence is None else round(confidence, 2),
                )
            else:
                logger.info("session %s: nothing transcribed", self.session_id)
            if transcript:
                self._turns.append(Turn("user", transcript))
                self._filler.arm()
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
            self._filler.abandon()
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
            await self._filler.settle()
            self._turntaking.clear_pending()
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
                self._events.emit(
                    lambda: Replied(
                        agent=Identifier(self._agent),
                        conversation=ConversationId(self._conversation),
                        sentences=Count(len(spoken)),
                    )
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
            # The handle is kept and never waited on here: what it is
            # for is a resume that must not read past its own writes,
            # and the never-block contract is why this line reads it and
            # walks away. A recorder that answers nothing (every store
            # double, and any future consumer) leaves nothing to keep.
            landed = self._recorder.record_turn(record)
            if landed is not None:
                self._acknowledged[record.conversation] = landed
        except Exception as exc:  # noqa: BLE001 - a consumer never breaks a reply
            logger.warning(
                "session %s: the turn recorder failed and was skipped: %s",
                self.session_id,
                type(exc).__name__,
            )

    async def _speak_reply(self, transcript: str, spoken: list[str]) -> None:
        """One reply, which may be spoken by more than one agent and may
        end on a different conversation than it began on.

        `spoken` collects sentences as their audio goes out, so an abort
        or a barge-in leaves the history holding exactly the part of the
        reply the user heard, sentence by sentence.

        A move ends the current loop, whichever of the three it is: what
        was said so far becomes an assistant turn on the thread it was
        said on, the leg is closed there, the rebinding happens here at
        that boundary, and a fresh loop runs on the other side of it. So
        no turn is ever split across two conversations: the turn's
        record was stamped with the pair it began with, and the words it
        spoke before the move stay in the history of the thread it spoke
        them on.

        At most one move per reply, whichever kind, which is what the
        latch counts: two agents cannot ping-pong, and a model cannot
        resume its way through a user's history inside one answer."""
        switches_left = 1
        self._llm_round = 0
        while True:
            transition = await self._tool_loop(spoken, switches_left)
            if transition is None:
                return
            said = " ".join(spoken) if spoken else None
            if said is not None:
                self._turns.append(Turn("assistant", said))
                # This leg's share of the reply, in the same terms
                # `replied` reports the whole of it: which agent, and how
                # many sentences of it the user heard. Never the words,
                # which are the store's (#120).
                self._events.emit(
                    lambda: AgentSaid(
                        agent=Identifier(self._agent),
                        conversation=ConversationId(self._conversation),
                        sentences=Count(len(spoken)),
                    )
                )
                spoken.clear()
            # Closed whether or not this agent spoke: a leg that only
            # asked for the move still spent tokens, and the leg is the
            # only place they can be attributed to the agent that spent
            # them. A move to another thread closes one too, although
            # the agent has not changed: what a leg is for is the share
            # of a reply that belongs to one context.
            self._turn.leg_ended(self._agent, said)
            self._move_to(transition)
            switches_left -= 1
            # Every offer this session is holding goes with the move,
            # whichever kind it was. An id offered inside the
            # conversation that just ended is exactly the stale
            # selection the enforcement exists to refuse.
            if self._resumption is not None:
                self._resumption.forget()
            self._turns.append(Turn("user", transition.seed))

    def _move_to(self, transition: _Transition) -> None:
        """Apply one transition at the boundary the loop ended on, and
        say on the record which one it was.

        The two arms are the two things a move can rebind. A handover
        changes the agent, and the agent's own thread comes with it,
        minted at its first activation and continued at every later one.
        The other two change which thread the agent is on, which is one
        line of state and one history: nothing about the agent, its
        providers or its prompt moves, because it is the same agent
        answering.
        """
        previous = self._agent
        # Held before the rebinding replaces it, so the event can say
        # which thread was left as well as which one was joined.
        leaving = self._conversation
        if transition.agent is not None:
            target = transition.agent
            self._activate_agent(target)
            # Read by a thunk the emitter calls before this method
            # returns, the way the retry above is.
            self._events.emit(
                lambda: Handover(
                    from_agent=Identifier(previous),
                    to_agent=Identifier(target),
                    from_conversation=ConversationId(leaving),
                    to_conversation=ConversationId(self._conversation),
                )
            )
            return
        assert transition.conversation is not None and self._agent is not None
        self._conversations[self._agent] = transition.conversation
        self._conversation = transition.conversation
        # Installed rather than merged: a resume brings the thread's own
        # history and a fresh conversation brings none, and either way
        # what the thread held in memory before this line is what the
        # store has already been told about.
        self._histories[transition.conversation] = list(transition.history)
        found = transition.resumed
        if found is not None:
            self._events.emit(
                lambda: ConversationResumed(
                    conversation=ConversationId(found.conversation),
                    turns=Count(found.rendered),
                    skipped=Count(found.skipped),
                    over_budget=Flag(found.over_budget),
                )
            )

    async def _tool_loop(
        self, spoken: list[str], switches_left: int
    ) -> "_Transition | None":
        """Stream, run whatever tools the model asked for, and stream
        again, up to the round cap. Returns the move that ended it, or
        None when the reply is finished.

        The tool snapshot and the resampler are taken here rather than
        per round, because they belong to the agent speaking; the next
        agent gets its own. The history it works from is the active
        thread's, which after a move is the thread the move landed on,
        seed and all."""
        assert self._providers is not None
        providers = self._providers
        tools = self._tool_snapshot()
        working = list(self._turns)
        resampler = Resampler(providers.tts.sample_rate, self._output.output_sample_rate)
        self._output.restart_pacing()

        switch_to: _Transition | None = None
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
                    match event:
                        case TextDelta(text=text):
                            # Speech only, and speech that is not just
                            # whitespace. Both providers assemble tool
                            # calls and usage after their stream has
                            # ended, so timing from those would report a
                            # whole generation as its own time to first
                            # token, and a round that only calls a tool
                            # has no first token to time.
                            if first_token_at is None and text.strip():
                                first_token_at = loop.time()
                            for sentence in splitter.push(text):
                                speaking = await self._speak_after(
                                    speaking, sentence, providers.tts, resampler, leg, spoken
                                )
                        case Usage():
                            usage = event
                        case _:
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

        Each source answers for itself, in the fixed order they were
        built in, so the merged list is the same list it always was and
        this method holds no rule about any one of them.

        Taken per reply rather than per session, so a server that came
        back, a device that finished discovering and a reload that
        landed mid-conversation are all picked up on the next
        utterance."""
        assert self._agent is not None
        tools: list[ToolDef] = []
        for source in self._sources:
            tools.extend(source.snapshot(self._agent))
        return tools

    async def _run_tools(
        self, calls: Sequence[ToolCall], slots: Sequence[int], switches_left: int
    ) -> tuple[list[ToolResult], "_Transition | None"]:
        """Execute one round of calls. Everything that is not a move runs
        concurrently, since device and server tools are independent; the
        moves are resolved here instead, because a successful one ends
        the loop rather than producing a result the model reads.

        `slots` says where on the turn's record each of these calls was
        already reserved, index for index with `calls`, which is why
        both halves are split out of one enumeration rather than
        rebuilt: a move the model asked for third keeps the third call's
        place, whatever order this method runs things in.

        The moves are resolved in the order the model issued them, and
        that IS the precedence when it asks for more than one: the first
        wins and the rest are refused, whether they were the same kind
        of move or not. A rule about which kind outranks which would be
        a rule nobody could predict from the outside."""
        plain = [
            (slots[index], call)
            for index, call in enumerate(calls)
            if not self._moves(call)
        ]
        moves = [
            (slots[index], call) for index, call in enumerate(calls) if self._moves(call)
        ]
        results = list(
            await asyncio.gather(*(self._run_one(call, slot) for slot, call in plain))
        )

        transition: _Transition | None = None
        for order, (slot, call) in enumerate(moves):
            moving, refusal = await self._move(call, switches_left, order)
            if refusal is not None:
                results.append(refusal)
                # No duration: nothing ran, and the refusal is what the
                # turn's record shows in place of it.
                self._turn.executed(slot, refusal.content, refusal.is_error, None)
                continue
            transition = moving
            # A successful move answers the model nothing, so the
            # reservation is already the whole of its record: no result
            # and no duration. It stays on the record all the same,
            # because the move is otherwise only implied by the legs it
            # produced.
        return results, transition

    def _moves(self, call: ToolCall) -> bool:
        """Whether this call is one the loop resolves itself.

        Two of the three are decided by name alone. The third is
        `resume_conversation`, which is one tool doing two things: a
        call that names a conversation is a move and is this method's,
        and a call that describes one is a read that changes nothing and
        goes to the source that owns it like any other tool. A call that
        names neither is not a move either, so it reaches the dispatch
        and is answered by the sentence that asks for one of the two.
        """
        if call.name in (names.SWITCH_AGENT, names.NEW_CONVERSATION):
            return True
        if call.name != names.RESUME_CONVERSATION:
            return False
        chosen = call.arguments.get("conversation")
        return isinstance(chosen, str) and bool(chosen.strip())

    async def _move(
        self, call: ToolCall, switches_left: int, order: int
    ) -> tuple["_Transition | None", ToolResult | None]:
        """One move, as either the transition it is or the refusal it
        gets. Exactly one of the two comes back.

        The two vocabularies stay apart deliberately. A refused handover
        is an error result, which is what it has always been: the model
        asked for something about the agents this device is bound to and
        got it wrong. A refused selection is not an error, because the
        answer is a sentence a user is owed out loud (#190, decision
        11): a server that cannot resume anything, a conversation that
        is gone, an id nobody offered.
        """
        if call.name == names.SWITCH_AGENT:
            refusal = self._refuse_handover(call, switches_left, order)
            if refusal is not None:
                return None, refusal
            return _Transition(SWITCH_GREETING, agent=str(call.arguments["agent"])), None
        answer = await self._select(call, switches_left, order)
        if isinstance(answer, str):
            return None, ToolResult(call.id, answer, is_error=False)
        return answer, None

    async def _select(
        self, call: ToolCall, switches_left: int, order: int
    ) -> "_Transition | str":
        """Which thread this reply moves to, or the sentence saying why
        it does not.

        The latch is the same one a handover shares, and the same one it
        has always been: the first move of a reply wins. What a second
        one is refused for is being second, whichever kind the first was.
        """
        if switches_left <= 0 or order > 0:
            return builtin.ALREADY_MOVED
        if self._resumption is None:
            return builtin.RESUMPTION_UNAVAILABLE
        if call.name == names.NEW_CONVERSATION:
            # Minted here, exactly as an agent's first thread of a
            # session is: the boundary is decided at this seam, and the
            # id has to exist before the first turn it stamps.
            return _Transition(FRESH_GREETING, conversation=uuid.uuid4().hex)
        assert self._agent is not None
        chosen = str(call.arguments["conversation"]).strip()
        # Before the read and not after it: a thread the agent was not
        # offered is refused without the store being asked about it, so
        # an id a model invented cannot even be a query.
        if not self._resumption.offers(self._agent, chosen):
            return builtin.NO_SUCH_CANDIDATE
        await self._settled(chosen)
        found = await self._resumption.resumed(self._agent, chosen)
        if isinstance(found, str):
            return found
        return _Transition(
            self._resumed_seed(found),
            conversation=found.conversation,
            history=found.turns,
            resumed=found,
        )

    def _resumed_seed(self, found: "resumption.Resumed") -> str:
        """What the round on the other side of a resume is told.

        The base sentence, plus whichever of the two caveats are true.
        This is where the milestone-4 answer to an over-budget backlog
        lives: the tail is installed and the agent is told it is a tail,
        rather than being offered a recap it cannot make yet.
        """
        return "".join(
            [
                RESUMED_GREETING,
                RESUMED_FROM_RECENT if found.over_budget else "",
                RESUMED_WITH_GAPS if found.skipped or found.incomplete else "",
            ]
        )

    async def _settled(self, conversation: str) -> None:
        """Wait, briefly, for what this session last wrote to that
        thread to be durably written.

        Read-your-writes for one case, and it is the case a session
        produces by itself: leaving a conversation and coming back to it
        without the connection closing in between. The turn that ended
        the first leg is on the writer's queue while this line runs, and
        hydrating without it would rebuild the thread one turn short of
        what the user just said.

        A wait that expires is not an error and is not reported: the
        thread is rebuilt from what has landed, which is the same answer
        a slower database would have given a moment earlier. The wait
        itself is a blocking one on a handle the writer settles, so it
        happens off the loop every live conversation shares.
        """
        landed = self._acknowledged.get(conversation)
        if landed is None:
            return
        await asyncio.to_thread(landed.wait, RESUME_ACKNOWLEDGEMENT_S)

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
        # The classification the reservation already holds, read back
        # rather than taken again: the `tool_call` event below says
        # where the name came from, the row at this slot says the same,
        # and asking twice could answer twice (an MCP reload between the
        # reservation and now is enough to move a name's owner).
        classified = self._turn.reserved(slot)
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            async with asyncio.timeout(self._timeout_for(classified)):
                content, is_error = await self._dispatch(call, classified)
        except TimeoutError:
            content, is_error = f'the tool "{call.name}" did not answer in time', True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            content, is_error = f'the tool "{call.name}" failed: {exc}', True
        elapsed = loop.time() - started
        self._events.emit(
            lambda: _tool_called(
                classified, self._agent, self._conversation, elapsed, is_error
            )
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

    async def _dispatch(self, call: ToolCall, classified: ToolInvocation) -> tuple[str, bool]:
        """Hand a call to the source that owns it, or answer it here.

        Two answers are nobody's tool to give and stay: a call whose
        arguments the model never closed, which no source should be
        asked to run, and a name none of them claims, which is what the
        model invented one looks like.

        `classified` is the answer `_run_one` already has, passed in
        rather than recomputed, and it is the whole of what a source is
        told: the one line here that says anything about the call
        describes it exactly as its `tool_call` event does, two
        classifications of one call could disagree, and a source that
        resolved the name again could route around the reservation."""
        if call.malformed_arguments is not None:
            # A plain line and not an event, and it obeys the same rule
            # as the event beside it (#120): the size of what the model
            # streamed rather than the bytes, since those are content,
            # and a name only where this server authored one. A device's
            # tool name and a name nobody publishes are the peer's own
            # bytes on a retained surface whether the line carrying them
            # is structured or not. The length is what tells a truncated
            # object from a model that answered in prose, and the record
            # carries the same fact as its `malformed` flag.
            named = _tool_fragment(classified).carried()
            logger.warning(
                "session %s: %s tool%s got %d characters of unparseable arguments",
                self.session_id,
                classified.source,
                named,
                len(call.malformed_arguments),
            )
            return "the arguments were not a JSON object; call again with valid ones", True
        assert self._agent is not None
        for source in self._sources:
            if source.owns(classified):
                return await source.dispatch(classified, self._agent)
        return f'there is no tool called "{call.name}"', True

    def _timeout_for(self, classified: ToolInvocation) -> float:
        """How long this call may take, answered by the source that owns
        it: a server tool gets its entry's configured timeout, builtins
        and device tools the module default above.

        Asked of the same claim the dispatch routes by, so a tool cannot
        be run against one entry's timeout and dispatched to another.
        Asking the registry by name here is what used to make that
        possible: a reload landing between the two answers
        differently."""
        for source in self._sources:
            if source.owns(classified):
                return source.timeout_for(classified)
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

    def start_reply(self, pcm: bytes, result: AsrResult | None = None) -> None:
        """Answer this utterance, from now on.

        The task is created here rather than on the turn-taking side so
        that `_reply_task`, `replying` and `drain` stay one object's
        business: the reply in flight is what the edge's own jobs ask
        about, and a second owner of the field would be a second answer
        to the same question.

        `result` is a transcription that already exists, which a
        confirmed barge-in has and nothing else does."""
        self._reply_task = asyncio.create_task(self._reply(pcm, result))

    async def cancel_reply(self) -> None:
        """Cancel a reply in flight and see the cancellation through.
        Waiting matters: a fire-and-forget cancel leaves the task not yet
        done, and an utterance finishing in that window would be dropped."""
        if self._reply_task is None:
            return
        self._reply_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._reply_task
        self._reply_task = None

    async def confirm_transcript(self, pcm: bytes) -> AsrResult:
        """Transcribe an interruption, so that the gates in front of a
        barge-in can ask what was actually said.

        Injected into the gate ladder whole rather than assembled there,
        which is what lets the provider-observability cluster and the
        session's language lock stay here: the ladder needs an answer,
        not the machinery that produces one. Failures propagate, and the
        ladder's own catch decides what an unanswerable confirmation
        means."""
        assert self._providers is not None
        async with self._watching("asr", self._providers.asr):
            return await self._providers.asr.transcribe(
                pcm, PIPELINE_SAMPLE_RATE, language_hint=self._asr_language
            )


def bespoke_runtime_factory(
    generations: Generations,
    mcp_servers: McpServers,
    memory: MemoryStore | None,
    conversations: TurnStore | None = None,
    threads: resumption.ThreadReads | None = None,
) -> RuntimeFactory:
    """The composition root's half of the seam: everything this runtime
    needs that outlives one connection, closed over once at startup.

    The device edge calls what comes back with a device to speak
    through, the session's observability, the agents the device is bound
    to and the world to build all of that from, and never learns what an
    LLM is.

    Neither the engines nor the clips are closed over, and that is the
    one thing here worth reading twice. Both belong to a world rather
    than to a process, so both are read off the generation the edge
    hands in, which is the generation the edge is telling the registry
    this conversation holds (#191). What that decides is where a change
    converges: a conversation goes on speaking through the engines and
    masking with the clips it opened on, and the next session gets what
    the reload built.

    `mcp_servers` is closed over rather than read off the world, and
    that difference is the tool half's own convergence point: the
    registry is one object whose contents an apply replaces, so an
    utterance is answered with the tools that are running rather than
    with the ones that were running when the conversation began.

    `conversations` is closed over, and is the reason the recorder
    reaches a runtime without the `RuntimeFactory` type moving: the
    store outlives every connection, and the per-session channel is
    derived here from the identity the edge already hands over. None
    means no store, which is every deployment that has not asked for one.

    `threads` is the other direction through the same database, and it
    is closed over for the same reason and holds nothing per session:
    reading a stored thread is a connection opened for the read and
    disposed after it. Whether a conversation may actually be resumed is
    not decided here but in the runtime, off the section it already
    holds, so the switch and the keys it reads live in one place.

    Deliberately one function rather than a config-selectable registry:
    one runtime exists, and a selection mechanism with one option is
    surface without a reader. This is the seam a second runtime plugs
    into."""

    def build(
        output: DeviceOutput,
        events: SessionEvents,
        agents: Sequence[str],
        generation: Generation,
    ) -> SessionInput:
        return PipelineRuntime(
            output,
            generations,
            generation,
            events,
            generation.providers.agents,
            mcp_servers,
            memory,
            generation.fillers,
            agents,
            None if conversations is None else SessionTurns(conversations, events.session_id),
            threads,
        )

    return build
