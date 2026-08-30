"""The record one turn hands the conversation store.

The content channel is separate from the event tap on purpose (#120):
tool arguments and results never rode the events at all, and the events
are about to lose their text, so what the store is fed comes from the
reply path itself. What that path assembles is what these tests are
about, driven through the same session drivers every other suite uses
and read off a spy standing where the store will stand.

The channel is optional and dormant: nothing constructs a store yet, so
a session built without one must behave exactly as it did before this
existed. The last test here says so directly, and the event-assertion
and pin suites say it by passing unmodified.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from tests.support.configs import (
    BOTH_MAC,
    POET_MAC,
    POET_TONE,
    TUTOR_TONE,
    base_config,
    registry_config,
)
from tests.support.device_tools import STATUS, FakeDevice
from tests.support.providers import BrokenStreamingTts as BrokenTts
from tests.support.providers import ScriptedLlm
from tests.support.sessions import (
    call,
    drive_reply,
    events_of,
    session_for,
    stamp_with,
    start_reply,
    talking,
    talking_thread,
    wait_for_reply,
    with_device,
)
from tests.support.sockets import QuietSocket
from tests.support.stores import CONVERSATIONS_MANIFEST as MANIFEST
from tests.support.stores import rows
from vinga_server.config import Config
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations import schema
from vinga_server.conversations.records import TurnRecord
from vinga_server.conversations.store import ConversationStore
from vinga_server.device.session import DeviceSession
from vinga_server.memory.store import MemoryScope, MemoryStore
from vinga_server.providers import (
    AsrProvider,
    AsrResult,
    LlmEvent,
    LlmProvider,
    TextDelta,
    ToolCall,
    ToolChoice,
    ToolDef,
    TtsProvider,
    Turn,
    Usage,
)
from vinga_server.runtime import turns
from vinga_server.runtime.turns import tool_source
from vinga_server.tools.mcp import McpServers

# One frame of silence, which the mock ASR answers with the configured
# transcript: 640 bytes at 16 kHz, which is the 0.02 s the record
# reports as the utterance's duration.
UTTERANCE = b"\x00\x00" * 320


class SpyStore:
    """Where the store will stand, keeping what it is handed.

    It implements the producer half the runtime is given, session id and
    all, so the binding the factory does is exercised rather than assumed.
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, TurnRecord]] = []

    def record_turn(self, session_id: str, record: TurnRecord) -> None:
        self.records.append((session_id, record))


class Speaking:
    """The reply's speaking step, stubbed down to what these tests need.

    The synthesis is drained rather than abandoned, so the first-audio
    measurement is a real one taken off a real provider; nothing reaches
    a device, so no audio is paced and a reply takes as long as its
    scripts do."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.spoke = asyncio.Event()

    async def __call__(self, synthesis: Any, resampler: Any, into: list[str]) -> None:
        async for _ in synthesis.chunks():
            pass
        into.append(synthesis.sentence)
        self.said.append(synthesis.sentence)
        self.spoke.set()


def speaking_session(
    conversations: Any,
    config: Config | None = None,
    mac: str = POET_MAC,
    scripts: dict[str, ScriptedLlm] | None = None,
    memory: MemoryStore | None = None,
    mcp_servers: McpServers | None = None,
    stages: dict[str, Any] | None = None,
) -> tuple[DeviceSession, Speaking]:
    """A session whose speaking is stubbed down to what these tests
    need, on a known device, built the way every other session in these
    suites is.

    The stub is white-box and the only reach-in this file keeps besides
    the two below it. What a recorded turn holds is decided sentence by
    sentence as each one's synthesis finishes, and the public route to a
    spoken sentence is the audio the device is paced: running it would
    make every one of these tests wait out a real reply's playback to
    read a field about how the reply was recorded. `Speaking` drains the
    same synthesis a device would, so the measurements a record carries
    are real ones off a real provider, and nothing is paced.
    """
    session = session_for(
        config if config is not None else base_config(),
        mac,
        scripts,
        memory=memory,
        websocket=cast(Any, QuietSocket()),
        mcp_servers=mcp_servers,
        conversations=conversations,
        stages=stages,
    )
    with_device(session, mac)
    speaking = Speaking()
    session.runtime._speak = speaking  # type: ignore[method-assign]
    return session, speaking


def recording_session(
    config: Config | None = None,
    mac: str = POET_MAC,
    scripts: dict[str, ScriptedLlm] | None = None,
    memory: MemoryStore | None = None,
    mcp_servers: McpServers | None = None,
    stages: dict[str, Any] | None = None,
) -> tuple[DeviceSession, SpyStore, Speaking]:
    """A session whose turns are recorded, built the way every other
    session in these suites is."""
    spy = SpyStore()
    session, speaking = speaking_session(spy, config, mac, scripts, memory, mcp_servers, stages)
    return session, spy, speaking


def only_record(spy: SpyStore) -> TurnRecord:
    assert len(spy.records) == 1, f"expected one record, got {len(spy.records)}"
    return spy.records[0][1]


def both_records(spy: SpyStore) -> tuple[TurnRecord, TurnRecord]:
    """The two records a reply that moved leaves behind.

    The turn that asked, on the thread it was asked on, and the turn the
    move opened on the other side. In this order because the first one
    is finished at the boundary, before anything is rebound, and the
    second when the reply itself ends."""
    assert len(spy.records) == 2, f"expected two records, got {len(spy.records)}"
    return spy.records[0][1], spy.records[1][1]


def heard_config(text: str) -> Config:
    """The suite's configuration, with the ASR answering this
    transcript."""
    return base_config(
        providers={
            "llm": {"mock": {"type": "mock", "reply": "{system} heard {text}."}},
            "asr": {"mock": {"type": "mock", "text": text}},
            "tts": {
                "tenor": {"type": "mock", "tone_hz": POET_TONE},
                "alto": {"type": "mock", "tone_hz": TUTOR_TONE},
            },
            "vad": {"mock": {"type": "mock"}},
        }
    )


# The single turn


async def test_a_plain_turn_records_what_was_said_and_what_it_cost() -> None:
    script = ScriptedLlm([["Hello there.", Usage(prompt_tokens=12, completion_tokens=4)]])
    session, spy, _ = recording_session(heard_config("how are you"), scripts={"poet": script})

    await drive_reply(session, UTTERANCE)

    session_id, record = spy.records[0]
    assert session_id == session.session_id
    assert (record.heard, record.reply) == ("how are you", "Hello there.")
    assert record.agent == "poet"
    assert record.heard_duration_s == 0.02
    assert record.at > 0
    assert record.legs == ()
    assert record.tools == ()
    assert record.rounds == 1
    assert (record.input_tokens, record.output_tokens) == (12, 4)
    assert record.llm_ms is not None and record.first_token_ms is not None
    assert record.first_token_ms <= record.llm_ms


async def test_a_turn_with_no_usage_reported_counts_no_tokens() -> None:
    """The absence is a fact about the endpoint, and null says so where
    zero would claim a free generation."""
    session, spy, _ = recording_session(scripts={"poet": ScriptedLlm(["Done."])})
    await drive_reply(session, UTTERANCE)

    record = only_record(spy)
    assert (record.input_tokens, record.output_tokens) == (None, None)
    assert record.rounds == 1


async def test_an_utterance_nobody_transcribed_records_no_turn() -> None:
    """Mirroring the events: no transcript, no `heard`, no turn."""
    session, spy, _ = recording_session(heard_config(""))
    await drive_reply(session, UTTERANCE)
    assert spy.records == []


async def test_the_reused_transcription_carries_its_language_and_no_asr_elapsed() -> None:
    """A confirmed barge-in transcribed the utterance to decide the
    cancel, so this reply runs no ASR of its own. Its elapsed belongs to
    that decision, at a different call site, and is not handed across:
    null is the honest answer to "how long did this turn's ASR take",
    where the confirming run's number would be a measurement of something
    else. The language fields ride along, because they are the
    transcription's rather than the call's."""
    session, spy, _ = recording_session(scripts={"poet": ScriptedLlm(["Understood."])})

    start_reply(
        session,
        UTTERANCE,
        AsrResult(text="turn the light on", language="en", language_confidence=0.876),
    )
    await wait_for_reply(session)

    record = only_record(spy)
    assert record.heard == "turn the light on"
    assert (record.language, record.language_confidence) == ("en", 0.88)
    assert record.asr_ms is None


class Clock:
    """The session's clock, under the test's control.

    `step` is how far it moves on every read, which is what tells a
    record that sampled the clock for itself from one that took the
    reading its event was stamped with. `advance` is the jump a scripted
    provider makes, which is how an interval the pipeline measures is
    exactly the interval the script says, with nothing sleeping.
    """

    def __init__(self, reading: float = 1000.0, step: float = 0.0) -> None:
        self.reading = reading
        self._step = step

    def advance(self, seconds: float) -> None:
        self.reading += seconds

    def __call__(self) -> float:
        reading = self.reading
        self.reading += self._step
        return reading


class SpyTap:
    """An event tap, which is where an emission's own reading is
    visible from outside."""

    def __init__(self) -> None:
        self.emissions: list[Any] = []

    def emit(self, emission: Any) -> None:
        self.emissions.append(emission)


async def test_the_turn_lands_on_its_heard_events_instant(tmp_path: Path) -> None:
    """The store measures a turn's offset and its events' offsets from
    one origin, so the two agree only if the turn carries the reading its
    `heard` was stamped with. A clock that moves on every read is what
    tells that apart from a second reading taken beside the emit, which
    lands in another millisecond whenever the two straddle a boundary."""
    session, spy, _ = recording_session(scripts={"poet": ScriptedLlm(["Noted."])})
    stamp_with(session, Clock(step=0.4))
    tap = SpyTap()
    events_of(session).attach(tap)

    await drive_reply(session, UTTERANCE)

    (heard,) = [one for one in tap.emissions if one.payload["event"] == "heard"]
    record = only_record(spy)
    assert record.at == heard.at

    # And through the store, which is what turns both readings into the
    # offsets a reader compares.
    store = ConversationStore(DatabaseConfig())
    store.start()
    try:
        store.open_session("s", 1000.0, MANIFEST)
        store.record_event("s", "heard", logging.INFO, {}, heard.at)
        store.record_turn("s", record)
        store.close_session("s", duration_s=1.0, reason="client")
    finally:
        store.stop()

    (turn,) = rows("turns")
    (event,) = rows("events")
    assert turn["t_ms"] == event["t_ms"]


class ScriptedAsr(AsrProvider):
    """A transcription that takes exactly as long as it says it does, by
    moving the session's clock rather than by sleeping. What the record
    reports is then the interval itself and not a lower bound on it."""

    egress = False

    def __init__(self, clock: Clock, elapsed_s: float, text: str = "turn it on") -> None:
        self._clock = clock
        self._elapsed_s = elapsed_s
        self._text = text

    async def transcribe(
        self, pcm: bytes, sample_rate: int, language_hint: str | None = None
    ) -> AsrResult:
        self._clock.advance(self._elapsed_s)
        return AsrResult(text=self._text)


ASR_ELAPSED_S = 0.25


async def test_the_transcription_this_turn_ran_is_timed() -> None:
    """The interval exactly: a nonnegative assertion would pass on a
    hard-coded zero, which is the one wrong answer worth ruling out for a
    latency column."""
    clock = Clock()
    session, spy, _ = recording_session(
        scripts={"poet": ScriptedLlm(["Understood."])},
        stages={"asr": cast(Any, ScriptedAsr(clock, ASR_ELAPSED_S))},
    )
    stamp_with(session, clock)

    await drive_reply(session, UTTERANCE)

    record = only_record(spy)
    assert record.asr_ms == round(ASR_ELAPSED_S * 1000)
    assert record.heard == "turn it on"


# What a handover records on each side of itself


async def test_a_handover_records_a_turn_on_each_thread_with_its_own_tokens() -> None:
    """A reply that hands the conversation on is spoken on two threads,
    and each of them records its own share of it.

    The turn that asked is finished at the boundary, so what it holds is
    what was heard and said before the move and what that agent spent
    saying it. The round the incoming agent is greeted with is the first
    turn of its own thread, with nothing heard on it: the user spoke on
    the thread they were handed off, and what they hear next is an
    answer.
    """
    poet = ScriptedLlm(
        [
            [
                "One moment.",
                call("switch_agent", agent="tutor"),
                Usage(prompt_tokens=5, completion_tokens=1),
            ]
        ]
    )
    tutor = ScriptedLlm([["Tutor here.", Usage(prompt_tokens=7, completion_tokens=2)]])
    session, spy, _ = recording_session(mac=BOTH_MAC, scripts={"poet": poet, "tutor": tutor})

    await drive_reply(session, UTTERANCE)

    asked, greeted = both_records(spy)
    # The agent that OWNS the turn, which is the one it started with:
    # the record is finished after the handover moved the active agent,
    # and reading the pair there would file this turn under the agent
    # the user was handed to.
    assert asked.agent == "poet"
    assert asked.reply == "One moment."
    assert [(leg.agent, leg.text) for leg in asked.legs] == [("poet", "One moment.")]
    assert [(leg.input_tokens, leg.output_tokens) for leg in asked.legs] == [(5, 1)]
    assert (asked.input_tokens, asked.output_tokens) == (5, 1)
    assert asked.rounds == 1
    # And the other side, which is a turn of its own on a thread of its
    # own: nothing was heard on it, because what the user said was said
    # before the move.
    assert (greeted.agent, greeted.heard, greeted.reply) == ("tutor", None, "Tutor here.")
    assert (greeted.input_tokens, greeted.output_tokens) == (7, 2)
    assert greeted.rounds == 1
    assert greeted.legs == ()
    assert greeted.conversation != asked.conversation


async def test_a_silent_leg_is_still_a_leg() -> None:
    """An agent that only asked for the handover said nothing and spent
    tokens all the same, and a leg with no text is what records that."""
    poet = ScriptedLlm(
        [[call("switch_agent", agent="tutor"), Usage(prompt_tokens=5, completion_tokens=1)]]
    )
    tutor = ScriptedLlm(["Tutor here."])
    session, spy, _ = recording_session(mac=BOTH_MAC, scripts={"poet": poet, "tutor": tutor})

    await drive_reply(session, UTTERANCE)

    asked, greeted = both_records(spy)
    assert [(leg.agent, leg.text, leg.input_tokens) for leg in asked.legs] == [
        ("poet", None, 5)
    ]
    assert asked.reply is None
    assert greeted.reply == "Tutor here."


# Which thread a turn belongs to


def a_handover_to_tutor(preamble: str | None) -> dict[str, ScriptedLlm]:
    """The two scripts a handover needs, in the two shapes it comes in.

    A tool-only handover asks for the switch and says nothing; a spoken
    one says a sentence first, which is the shape that puts words from
    two agents in one reply. Both are the same transition and the
    attribution rule has to hold for each.
    """
    asked: list[Any] = [call("switch_agent", agent="tutor")]
    if preamble is not None:
        asked.insert(0, preamble)
    return {
        "poet": ScriptedLlm([asked]),
        "tutor": ScriptedLlm(["Tutor here."]),
    }


@pytest.mark.parametrize("preamble", [None, "One moment."])
async def test_a_handover_turn_belongs_to_the_thread_it_started_on(
    preamble: str | None,
) -> None:
    """The boundary falls between turns and never inside one.

    The active agent moves mid-reply, so a record that read the pair
    when the reply ended would file the handover turn under the agent
    the user was handed TO, on a thread that turn did not begin. What
    that agent then says is a turn of its own, on its own thread, so
    neither thread is left holding the other's words.
    """
    session, spy, _ = recording_session(
        mac=BOTH_MAC, scripts=a_handover_to_tutor(preamble)
    )

    await drive_reply(session, UTTERANCE)

    asked, greeted = both_records(spy)
    assert (asked.agent, asked.heard) == ("poet", "hello")
    assert [leg.agent for leg in asked.legs] == ["poet"]
    # And the thread is the one the turn opened on: the session is
    # talking as the tutor, on the tutor's own thread, by the time this
    # record landed.
    assert talking(session) == "tutor"
    assert asked.conversation != talking_thread(session)
    # The greeting the tutor answered with is on the thread the session
    # is talking on now, which is the tutor's.
    assert (greeted.agent, greeted.conversation) == ("tutor", talking_thread(session))
    assert greeted.reply == "Tutor here."


async def test_each_agent_of_a_session_gets_a_thread_and_keeps_it() -> None:
    """"Sophia, let me talk to Nadia, back to Sophia" is one session
    touching two threads: the first activation of an agent mints one and
    every later activation continues it, so switching back returns to
    the conversation the agent was already on rather than to a third."""
    session, spy, _ = recording_session(
        mac=BOTH_MAC,
        scripts={
            "poet": ScriptedLlm([[call("switch_agent", agent="tutor")]]),
            "tutor": ScriptedLlm([[call("switch_agent", agent="poet")]]),
        },
    )

    poet = talking_thread(session)
    await drive_reply(session, UTTERANCE)
    tutor = talking_thread(session)
    await drive_reply(session, UTTERANCE)

    assert poet != tutor
    # Each turn is recorded on the thread it was spoken on: the utterance
    # on the thread that heard it, the greeting that answered the move on
    # the thread it landed on. The second handover returns the session to
    # the thread the first one left rather than minting a third.
    assert [record.conversation for _, record in spy.records] == [
        poet,
        tutor,
        tutor,
        poet,
    ]
    assert (talking(session), talking_thread(session)) == ("poet", poet)


async def test_a_thread_id_is_a_minted_token_and_not_the_session_id() -> None:
    """A conversation outlives the session it was begun in, so its id is
    its own: the same shape as a session id and never the same value."""
    session, spy, _ = recording_session(scripts={"poet": ScriptedLlm(["Hello."])})

    await drive_reply(session, UTTERANCE)

    record = only_record(spy)
    assert record.conversation != session.session_id
    assert len(record.conversation) == 32
    assert record.conversation.isalnum()


# Every call the model issued, whatever became of it


async def test_every_source_is_classified_and_positioned() -> None:
    """One round holding a call from every branch there is, recorded at
    the position the model issued it at rather than the order the loop
    happened to finish them in."""
    servers = McpServers.build(registry_config(granted=True))
    await servers.start_all()
    device = FakeDevice([{"tools": [STATUS]}])
    await device.client.discover()
    script = ScriptedLlm(
        [
            [
                call("remember", text="a fact"),
                call("self_get_device_status"),
                call("tools__secret_word"),
                call("ghost_tool"),
                ToolCall(id="c-broken", name="remember", malformed_arguments="{text: oops"),
            ],
            "That is all of them.",
        ]
    )
    session, spy, _ = recording_session(
        scripts={"poet": script}, mcp_servers=servers
    )
    # White-box: a device's own tools arrive from a discovery run the
    # edge starts over the wire after the hello, and this session has no
    # socket to run one on. What the record has to show is that a call
    # to one of them is attributed to the device, which needs the tools
    # to be there at all.
    session._device_tools = device.client
    try:
        await drive_reply(session, UTTERANCE)
    finally:
        await servers.stop_all()

    record = only_record(spy)
    calls = {invocation.position: invocation for invocation in record.tools}
    assert sorted(calls) == [0, 1, 2, 3, 4]
    assert [calls[position].source for position in sorted(calls)] == [
        "builtin",
        "device",
        "mcp",
        "unknown",
        "builtin",
    ]
    assert [calls[position].name for position in sorted(calls)] == [
        "remember",
        "self_get_device_status",
        "tools__secret_word",
        "ghost_tool",
        "remember",
    ]
    # Only an MCP call names an entry, and it names the configured one.
    assert [calls[position].entry for position in sorted(calls)] == [
        None,
        None,
        "tools",
        None,
        None,
    ]
    assert calls[0].arguments == {"text": "a fact"}
    assert calls[2].result == "rhubarb"
    assert not calls[2].is_error
    # An unknown name is refused rather than run, and the canned refusal
    # is what the record shows in place of a result.
    assert calls[3].is_error and "no tool called" in (calls[3].result or "")
    # A malformed call is classified by its name, flagged, and carries no
    # arguments: what arrived was the model's own bytes, not an object.
    assert calls[4].malformed and calls[4].arguments is None
    assert calls[4].is_error and "not a JSON object" in (calls[4].result or "")
    assert all(invocation.duration_ms is not None for invocation in record.tools)
    assert record.rounds == 2


async def test_a_refused_handover_is_recorded_with_its_refusal() -> None:
    poet = ScriptedLlm([[call("switch_agent", agent="stranger")], "I cannot reach that one."])
    session, spy, _ = recording_session(mac=BOTH_MAC, scripts={"poet": poet})

    await drive_reply(session, UTTERANCE)

    record = only_record(spy)
    (invocation,) = record.tools
    assert (invocation.position, invocation.source) == (0, "builtin")
    assert invocation.name == "switch_agent"
    assert invocation.is_error and "not bound to" in (invocation.result or "")
    # Nothing ran, so there is nothing to have taken any time.
    assert invocation.duration_ms is None
    assert record.legs == ()


async def test_a_successful_handover_is_recorded_with_no_result() -> None:
    """The switch answers the model nothing, so the row carries nothing
    in its place; without the row the handover would only be implied by
    the legs it produced."""
    poet = ScriptedLlm([[call("switch_agent", agent="tutor")]])
    tutor = ScriptedLlm(["Tutor here."])
    session, spy, _ = recording_session(mac=BOTH_MAC, scripts={"poet": poet, "tutor": tutor})

    await drive_reply(session, UTTERANCE)

    asked, _ = both_records(spy)
    (invocation,) = asked.tools
    assert (invocation.source, invocation.name) == ("builtin", "switch_agent")
    assert (invocation.result, invocation.duration_ms) == (None, None)
    assert not invocation.is_error


async def test_two_switches_in_one_round_keep_the_models_positions() -> None:
    """The second is refused for being the second the loop resolves, and
    recorded at the place the model actually issued it."""
    poet = ScriptedLlm(
        [[call("switch_agent", agent="tutor"), call("switch_agent", agent="poet")]]
    )
    tutor = ScriptedLlm(["Tutor here."])
    session, spy, _ = recording_session(mac=BOTH_MAC, scripts={"poet": poet, "tutor": tutor})

    await drive_reply(session, UTTERANCE)

    asked, _ = both_records(spy)
    assert [(invocation.position, invocation.is_error) for invocation in asked.tools] == [
        (0, False),
        (1, True),
    ]
    assert "already been handed over" in (asked.tools[1].result or "")


# What the finally saw


class HangingLlm(LlmProvider):
    """One round that speaks and asks for a tool, and a second round that
    never answers, so a reply can be cancelled at a known point."""

    def __init__(self) -> None:
        self.rounds = 0
        self.hanging = asyncio.Event()

    async def stream(
        self,
        system: str,
        history: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        self.rounds += 1
        if self.rounds == 1:
            yield TextDelta("First sentence.")
            yield ToolCall(id="c1", name="ghost_tool")
            yield Usage(prompt_tokens=9, completion_tokens=3)
            return
        self.hanging.set()
        await asyncio.sleep(30)
        yield TextDelta("never said")


async def test_a_cancelled_reply_records_what_its_finally_saw() -> None:
    """The record is handed over beside `replied` and behaves the same
    way: a barge-in leaves the part of the reply the user actually heard,
    the round that finished, and the tool that ran."""
    llm = HangingLlm()
    session, spy, _ = recording_session(scripts={"poet": cast(Any, llm)})

    start_reply(session, UTTERANCE)
    await asyncio.wait_for(llm.hanging.wait(), 5)
    await session.runtime.cancel_reply()

    record = only_record(spy)
    assert record.reply == "First sentence."
    assert record.rounds == 1
    assert (record.input_tokens, record.output_tokens) == (9, 3)
    assert [invocation.name for invocation in record.tools] == ["ghost_tool"]


class BlockingMemory(MemoryStore):
    """A memory whose write never returns, so a reply can be cancelled
    with a tool call demonstrably still in flight."""

    def __init__(self) -> None:
        # No engines, and it needs none: the write is replaced whole,
        # so nothing here reaches a connection.
        super().__init__(cast(Any, None), cast(Any, None))
        self.running = asyncio.Event()

    async def add(
        self, scope: MemoryScope, owner: str, fact: str, *, agent: str
    ) -> int:
        self.running.set()
        await asyncio.sleep(30)
        raise AssertionError("unreachable")


async def test_a_call_cancelled_while_it_ran_is_recorded_unexecuted() -> None:
    """Every call the model issues becomes an invocation, and a barge-in
    landing while one is running does not take it off the record: the
    reservation says what was asked, and the nulls say it never
    answered."""
    memory = BlockingMemory()
    script = ScriptedLlm([[call("remember", text="a fact")]])
    session, spy, _ = recording_session(scripts={"poet": script}, memory=memory)

    start_reply(session, UTTERANCE)
    await asyncio.wait_for(memory.running.wait(), 5)
    await session.runtime.cancel_reply()

    (invocation,) = only_record(spy).tools
    assert (invocation.position, invocation.source) == (0, "builtin")
    assert invocation.name == "remember"
    assert invocation.arguments == {"text": "a fact"}
    assert (invocation.result, invocation.duration_ms) == (None, None)
    assert not invocation.is_error


async def test_a_call_is_recorded_when_speech_fails_before_the_dispatch() -> None:
    """The calls arrive with the round that spoke, and the round's last
    sentence is awaited before anything is dispatched, so a synthesis
    that fails there used to take the whole round's calls with it."""
    script = ScriptedLlm([["Let me check.", call("ghost_tool")], "Never reached."])
    session, spy, _ = recording_session(
        scripts={"poet": script}, stages={"tts": cast(Any, BrokenTts())}
    )

    await drive_reply(session, UTTERANCE)

    record = only_record(spy)
    (invocation,) = record.tools
    assert (invocation.position, invocation.source) == (0, "unknown")
    assert invocation.name == "ghost_tool"
    assert (invocation.result, invocation.duration_ms) == (None, None)
    # Nothing was heard, so nothing is claimed to have been said.
    assert record.reply is None


# The synthesizer's first bytes


class SentenceTts(TtsProvider):
    """A voice whose time to first byte depends on which sentence it is
    given, so the two syntheses a reply runs at once can be told apart by
    which of their waits the measurement reports."""

    egress = False

    def __init__(self, latencies: dict[str, float]) -> None:
        self.sample_rate = 24000
        self._latencies = latencies

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        await asyncio.sleep(self._latencies[text])
        yield b"\x00\x00" * 240


# Far enough apart that scheduling jitter cannot carry one into the
# other's half of the range.
SLOW_S = 0.30
QUICK_S = 0.02


@pytest.mark.parametrize(("first_s", "second_s"), [(SLOW_S, QUICK_S), (QUICK_S, SLOW_S)])
async def test_the_first_synthesis_is_what_the_first_audio_times(
    first_s: float, second_s: float
) -> None:
    """The reply's first request, in both completion orders. The two
    syntheses of a reply overlap, so the second can answer first: taking
    whichever reported first fails the slow-then-quick case, and letting
    a later one overwrite fails the quick-then-slow one."""
    script = ScriptedLlm(["One here. Two here."])
    session, spy, _ = recording_session(
        scripts={"poet": script},
        stages={"tts": cast(Any, SentenceTts({"One here.": first_s, "Two here.": second_s}))},
    )

    await drive_reply(session, UTTERANCE)

    measured = only_record(spy).tts_first_audio_ms
    assert measured is not None
    assert abs(measured - first_s * 1000) < (SLOW_S - QUICK_S) * 1000 / 2


async def test_a_reply_that_spoke_nothing_times_no_audio() -> None:
    """A model that only ever asks for tools reaches the round cap
    without speaking, so there was no synthesis to time and no reply to
    record."""
    session, spy, _ = recording_session(scripts={"poet": ScriptedLlm([[call("ghost_tool")]])})

    await drive_reply(session, UTTERANCE)

    record = only_record(spy)
    assert record.tts_first_audio_ms is None
    assert record.reply is None
    assert len(record.tools) == 4


# The classifier, at its one site


def test_the_source_set_is_the_one_the_column_admits() -> None:
    """One closed set, spelled in the runtime and constrained in the
    schema; drift between them would be a row the database refuses."""
    assert turns.TOOL_SOURCES == schema.TOOL_SOURCES


def test_where_a_tool_name_comes_from() -> None:
    assert tool_source("switch_agent", (), None) == ("builtin", None)
    assert tool_source("remember", (), None) == ("builtin", None)
    assert tool_source("self_get_device_status", {"self_get_device_status"}, None) == (
        "device",
        None,
    )
    assert tool_source("home__inside__turn_on", (), "home__inside") == ("mcp", "home__inside")
    assert tool_source("ghost_tool", (), None) == ("unknown", None)


def test_a_builtin_name_is_a_builtin_whatever_else_claims_it() -> None:
    """Names, not outcomes: a builtin whose feature is switched off is
    still the namespace the model reached into, and the precedence the
    dispatch applies is visible here in one place."""
    assert tool_source("remember", {"remember"}, "remember") == ("builtin", None)
    assert all(
        tool_source(name, {"a_device_tool"}, owner)[0] in turns.TOOL_SOURCES
        for name in ("remember", "a_device_tool", "entry__tool", "ghost")
        for owner in (None, "entry")
    )


# The channel is optional


async def test_a_session_with_no_recorder_replies_exactly_as_before(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The dormancy, asserted rather than assumed: the same reply through
    a session with a recorder and one without produces the same speech
    and the same events."""

    async def one_reply(spy: SpyStore | None) -> tuple[list[str], list[tuple[str, Any]]]:
        script = ScriptedLlm([[call("ghost_tool")], "Nothing found."])
        session, speaking = speaking_session(spy, scripts={"poet": script})
        caplog.clear()
        with caplog.at_level("INFO"):
            await drive_reply(session, UTTERANCE)
        seen = [
            (record.event, getattr(record, "tool", None))
            for record in caplog.records
            if getattr(record, "event", None) is not None
        ]
        return speaking.said, seen

    spy = SpyStore()
    with_recorder = await one_reply(spy)
    without = await one_reply(None)

    assert with_recorder == without
    assert len(spy.records) == 1


async def test_a_recorder_that_fails_does_not_break_the_reply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The event tap's rule, applied to the content channel: the line
    after this hand-off is the closing `tts stop` the device waits for,
    so a consumer nobody has met yet must not be able to cost it."""

    class BrokenStore:
        def record_turn(self, session_id: str, record: TurnRecord) -> None:
            raise RuntimeError("a far side said something unrepeatable")

    session, speaking = speaking_session(
        BrokenStore(), scripts={"poet": ScriptedLlm(["All done."])}
    )

    with caplog.at_level("WARNING"):
        await drive_reply(session, UTTERANCE)

    assert speaking.said == ["All done."]
    (skipped,) = [
        record for record in caplog.records if "turn recorder failed" in record.getMessage()
    ]
    # The class name and nothing else: what a recorder was holding is not
    # this line's to repeat.
    assert "RuntimeError" in skipped.getMessage()
    assert "unrepeatable" not in skipped.getMessage()
