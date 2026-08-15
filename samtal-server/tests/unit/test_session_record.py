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
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from samtal_server.config import Config
from samtal_server.conversations import schema
from samtal_server.conversations.records import TurnRecord
from samtal_server.device.session import DeviceSession
from samtal_server.providers import (
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
from samtal_server.runtime import turns
from samtal_server.runtime.turns import tool_source
from samtal_server.tools.mcp import McpServers
from samtal_server.tools.memory import MemoryStore
from tests.unit.test_session import POET_TONE, TUTOR_TONE
from tests.unit.test_session_reply_failures import QuietSocket
from tests.unit.test_session_tools import (
    BOTH_MAC,
    POET_MAC,
    ScriptedLlm,
    base_config,
    call,
    drive_reply,
    registry_config,
    session_for,
    start_reply,
)
from tests.unit.test_tools_device import STATUS, FakeDevice

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


def recording_session(
    config: Config | None = None,
    mac: str = POET_MAC,
    scripts: dict[str, ScriptedLlm] | None = None,
    memory: MemoryStore | None = None,
    mcp_servers: McpServers | None = None,
) -> tuple[DeviceSession, SpyStore, Speaking]:
    """A session whose turns are recorded, built the way every other
    session in these suites is."""
    spy = SpyStore()
    session = session_for(
        config if config is not None else base_config(),
        mac,
        scripts,
        memory=memory,
        websocket=cast(Any, QuietSocket()),
        mcp_servers=mcp_servers,
        conversations=spy,
    )
    session._mac = mac
    speaking = Speaking()
    session.runtime._speak = speaking  # type: ignore[method-assign]
    return session, spy, speaking


def only_record(spy: SpyStore) -> TurnRecord:
    assert len(spy.records) == 1, f"expected one record, got {len(spy.records)}"
    return spy.records[0][1]


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
    assert session_id == session._events.session_id
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

    await session.runtime._reply(
        UTTERANCE,
        AsrResult(text="turn the light on", language="en", language_confidence=0.876),
    )

    record = only_record(spy)
    assert record.heard == "turn the light on"
    assert (record.language, record.language_confidence) == ("en", 0.88)
    assert record.asr_ms is None


async def test_the_turn_is_stamped_off_the_sessions_own_clock() -> None:
    """The store measures a turn's offset and its events' against one
    origin, so the reading has to come from the clock the events are
    stamped with rather than from one beside it. A session whose clock is
    not the loop's is what tells the two apart."""
    session, spy, _ = recording_session(scripts={"poet": ScriptedLlm(["Noted."])})
    session._events._clock = lambda: 4242.0

    await drive_reply(session, UTTERANCE)

    assert only_record(spy).at == 4242.0


async def test_the_transcription_this_turn_ran_is_timed() -> None:
    session, spy, _ = recording_session(scripts={"poet": ScriptedLlm(["Understood."])})
    await drive_reply(session, UTTERANCE)

    record = only_record(spy)
    assert record.asr_ms is not None and record.asr_ms >= 0


# The legs a handover leaves


async def test_a_handover_records_a_leg_per_agent_with_its_own_tokens() -> None:
    """A turn's totals blend agents that may run different models, so the
    legs are where the attribution stays honest."""
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

    record = only_record(spy)
    assert record.agent == "tutor"
    assert record.reply == "One moment. Tutor here."
    assert [(leg.agent, leg.text) for leg in record.legs] == [
        ("poet", "One moment."),
        ("tutor", "Tutor here."),
    ]
    assert [(leg.input_tokens, leg.output_tokens) for leg in record.legs] == [(5, 1), (7, 2)]
    # The totals are the legs summed, and the rounds are counted across
    # the whole reply rather than per agent.
    assert (record.input_tokens, record.output_tokens) == (12, 3)
    assert record.rounds == 2


async def test_a_silent_leg_is_still_a_leg() -> None:
    """An agent that only asked for the handover said nothing and spent
    tokens all the same, and a leg with no text is what records that."""
    poet = ScriptedLlm(
        [[call("switch_agent", agent="tutor"), Usage(prompt_tokens=5, completion_tokens=1)]]
    )
    tutor = ScriptedLlm(["Tutor here."])
    session, spy, _ = recording_session(mac=BOTH_MAC, scripts={"poet": poet, "tutor": tutor})

    await drive_reply(session, UTTERANCE)

    record = only_record(spy)
    assert [(leg.agent, leg.text, leg.input_tokens) for leg in record.legs] == [
        ("poet", None, 5),
        ("tutor", "Tutor here.", None),
    ]
    assert record.reply == "Tutor here."


# Every call the model issued, whatever became of it


async def test_every_source_is_classified_and_positioned(tmp_path: Path) -> None:
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
        scripts={"poet": script}, memory=MemoryStore(tmp_path), mcp_servers=servers
    )
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

    (invocation,) = only_record(spy).tools
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

    record = only_record(spy)
    assert [(invocation.position, invocation.is_error) for invocation in record.tools] == [
        (0, False),
        (1, True),
    ]
    assert "already been handed over" in (record.tools[1].result or "")


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
    await session.runtime._cancel_reply()

    record = only_record(spy)
    assert record.reply == "First sentence."
    assert record.rounds == 1
    assert (record.input_tokens, record.output_tokens) == (9, 3)
    assert [invocation.name for invocation in record.tools] == ["ghost_tool"]


# The synthesizer's first bytes


class DelayedTts(TtsProvider):
    """A voice with a real time to first byte, so the measurement has
    something to measure."""

    egress = False

    def __init__(self, latency_s: float) -> None:
        self.sample_rate = 24000
        self._latency_s = latency_s

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        await asyncio.sleep(self._latency_s)
        yield b"\x00\x00" * 240


LATENCY_S = 0.05


async def test_the_first_audio_of_the_reply_is_timed_at_the_synthesizer() -> None:
    script = ScriptedLlm(["One here. Two here."])
    session, spy, _ = recording_session(scripts={"poet": script})
    assert session.runtime._providers is not None
    session.runtime._providers = replace(
        session.runtime._providers, tts=cast(Any, DelayedTts(LATENCY_S))
    )

    await drive_reply(session, UTTERANCE)

    record = only_record(spy)
    assert record.tts_first_audio_ms is not None
    # The first request's own wait, not the second sentence's: that one
    # was started against playback already happening.
    assert record.tts_first_audio_ms >= LATENCY_S * 1000
    assert record.tts_first_audio_ms < LATENCY_S * 1000 * 4


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
        session = session_for(
            base_config(),
            POET_MAC,
            {"poet": script},
            websocket=cast(Any, QuietSocket()),
            conversations=spy,
        )
        session._mac = POET_MAC
        speaking = Speaking()
        session.runtime._speak = speaking  # type: ignore[method-assign]
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

    session = session_for(
        base_config(),
        POET_MAC,
        {"poet": ScriptedLlm(["All done."])},
        websocket=cast(Any, QuietSocket()),
        conversations=BrokenStore(),
    )
    session._mac = POET_MAC
    speaking = Speaking()
    session.runtime._speak = speaking  # type: ignore[method-assign]

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
