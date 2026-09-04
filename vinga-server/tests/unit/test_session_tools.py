"""The session's tool loop, against scripted fake providers.

The loop is what makes tools a conversation rather than a request: it
speaks, executes, feeds results back, and speaks again, with a cap that
guarantees the reply ends in speech. switch_agent is the case only the
session can serve, because the round after it goes to a different
provider entirely.
"""

import asyncio
import contextlib
import json
import logging
import sys
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

import vinga_server.runtime.pipeline as pipeline_module
from tests.support.configs import (
    BOTH_MAC,
    DEVICE_HELLO,
    POET_MAC,
    POET_TONE,
    STDIO_SERVER,
    TUTOR_TONE,
    base_config,
    registry_config,
)
from tests.support.device_tools import VOLUME, FakeDevice
from tests.support.events import events
from tests.support.mcp_stdio_server import SHADOWED_TOOL_ENV
from tests.support.providers import ScriptedLlm
from tests.support.records import only_record, recording_session
from tests.support.sessions import (
    call,
    drive_reply,
    events_of,
    history,
    run_reply,
    session_for,
    talking,
)
from tests.support.stores import memory as lane_memory
from tests.support.tools_mcp import Applying, reading
from tests.support.wire import connect, say_something, sentences, shake_hands, tone_strength
from vinga_server.app import create_app
from vinga_server.config import Config
from vinga_server.memory import store as store_module
from vinga_server.memory.store import MemoryScope, MemoryStore
from vinga_server.providers import ToolCall, Turn
from vinga_server.tools import builtin
from vinga_server.tools.builtin import switch_agent_tool
from vinga_server.tools.mcp import McpServers


async def test_a_reply_with_no_tool_calls_is_one_round() -> None:
    script = ScriptedLlm(["Nothing to look up."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    assert await run_reply(session, "hello") == ["Nothing to look up."]
    assert len(script.seen) == 1


async def test_a_whitespace_delta_is_not_a_tool_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The event the loop is indifferent to, which no other suite
    delivers to it.

    A whitespace-only `TextDelta` is speech that times no first token
    and still belongs to the sentence being assembled. The
    everything-else arm of the loop is what makes a tool call, so this
    event landing there would put a phantom on the turn's record and
    keep the round loop going after the model had stopped asking for
    anything.

    No `StreamStarted` is scripted: `_watchdog_stream` consumes the one
    an adapter yields first and consumes it exclusively, so this loop
    never sees one (`providers/base.py`), and scripting one here would
    drive a stream shape no adapter is allowed to produce.
    """
    script = ScriptedLlm(
        [
            ["   ", call("ghost_tool")],
            ["Two", " ", "words here. ", "And a second sentence."],
        ]
    )
    session = session_for(base_config(), POET_MAC, {"poet": script})
    with caplog.at_level("INFO"):
        spoken = await run_reply(session, "do it")

    # Every scripted sentence is spoken, and the whitespace delta was
    # assembled into the first of them: routed anywhere but the
    # splitter, the two deltas around it would run together.
    assert spoken == ["Two words here.", "And a second sentence."]
    # Two rounds, the second of them the reply: no phantom call
    # survived to ask for a third, and the record carries the one call
    # the model actually made.
    assert len(script.seen) == 2
    made = [c for turns, _, _ in script.seen for turn in turns for c in turn.tool_calls]
    assert [c.name for c in made] == ["ghost_tool"]
    # Whitespace is not a first token: the round that streamed only
    # whitespace and a call times none, the way a tool-only round does.
    tool_round, speaking_round = events(caplog, "llm_round")
    assert not hasattr(tool_round, "first_token_ms")
    assert speaking_round.first_token_ms >= 0


async def test_an_unknown_tool_comes_back_as_an_error_result() -> None:
    script = ScriptedLlm([[call("ghost_tool")], "I could not do that."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    assert await run_reply(session, "do it") == ["I could not do that."]

    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert "no tool called" in result.content


async def test_a_tool_that_never_answers_becomes_a_timeout_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline_module, "DEFAULT_TOOL_TIMEOUT_S", 0.05)
    script = ScriptedLlm([[call("remember", text="a fact")], "Sorry, that took too long."])
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=_HangingStore())
    assert await run_reply(session, "remember this") == ["Sorry, that took too long."]

    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert "did not answer in time" in result.content


class _HangingStore(MemoryStore):
    """A store whose writes never finish, so the loop's per-call timeout
    is the only thing that can end the reply.

    No engines, and it needs none: the write is replaced whole, so
    nothing here reaches a connection."""

    def __init__(self) -> None:
        super().__init__(cast(Any, None), cast(Any, None))

    async def add(
        self, scope: MemoryScope, owner: str, fact: str, *, agent: str
    ) -> int:
        await asyncio.sleep(30)
        raise AssertionError("unreachable")


async def test_the_round_cap_ends_the_reply_in_speech() -> None:
    # A model that keeps asking for tools must still stop talking: the
    # last permitted round forbids calling.
    script = ScriptedLlm([[call("ghost_tool")]] * 3 + ["All right, enough."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    assert await run_reply(session, "loop forever") == ["All right, enough."]

    choices = [choice for _, _, choice in script.seen]
    assert choices == ["auto", "auto", "auto", "none"]
    assert len(script.seen) == pipeline_module.MAX_TOOL_ROUNDS


async def test_history_keeps_the_speech_and_not_the_tool_exchange() -> None:
    script = ScriptedLlm([[call("ghost_tool")], "It did not work."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    await run_reply(session, "do it")

    assert await history(session, script) == [
        Turn("user", "do it"),
        Turn("assistant", "It did not work."),
    ]
    # The structured turns existed, but only inside the reply.
    assert any(turn.tool_calls for turns, _, _ in script.seen for turn in turns)


async def test_switch_agent_is_offered_only_where_there_is_somewhere_to_go() -> None:
    # What was offered is what the model was handed, which is where a
    # snapshot goes and the only place it is observable from outside.
    alone = ScriptedLlm(["Nobody to hand over to."])
    paired = ScriptedLlm(["Somebody to hand over to."])
    one = session_for(base_config(), POET_MAC, {"poet": alone})
    both = session_for(base_config(), BOTH_MAC, {"poet": paired})
    await run_reply(one, "hello")
    await run_reply(both, "hello")

    # Both offers whole, so the conditional tool is the entire
    # difference between them: the device bound to one agent has nowhere
    # to switch, and is offered everything else these agents are due.
    # The two conversation tools are unconditional on purpose, since
    # what a server that cannot resume anything answers with is a
    # sentence the agent reads out and a tool that is simply absent is a
    # tool a model invents (#190); the memory family is due because
    # neither agent's `memory` section says otherwise, which is the
    # condition `test_session_memory_policy.py` is about.
    assert [tool.name for tool in alone.seen[0][1]] == [
        "remember",
        "update_memory",
        "forget",
        "restore_memory",
        "recall",
        "set_state",
        "clear_state",
        "new_conversation",
        "resume_conversation",
    ]
    assert [tool.name for tool in paired.seen[0][1]] == [
        "switch_agent",
        "remember",
        "update_memory",
        "forget",
        "restore_memory",
        "recall",
        "set_state",
        "clear_state",
        "new_conversation",
        "resume_conversation",
    ]
    # The enum carries the device's full bound list, which is what lets
    # the agent answer "who can I talk to?".
    (tool,) = [t for t in paired.seen[0][1] if t.name == "switch_agent"]
    assert tool.input_schema["properties"]["agent"]["enum"] == ["poet", "tutor"]
    assert switch_agent_tool(["poet", "tutor"]).description.count("poet") == 1


async def test_a_successful_switch_hands_over_to_the_other_agent() -> None:
    poet = ScriptedLlm([[call("switch_agent", agent="tutor")]])
    tutor = ScriptedLlm(["Tutor here, hello."])
    session = session_for(base_config(), BOTH_MAC, {"poet": poet, "tutor": tutor})

    assert await run_reply(session, "get me the tutor") == ["Tutor here, hello."]
    assert talking(session) == "tutor"
    # Talking as the tutor means being sent the tutor's prompt.
    assert tutor.systems == ["TUTOR"]

    # The new agent saw its own thread and nothing else, which since
    # #190 is a thread that did not exist a moment ago: the seed telling
    # it to greet is the whole of what it was handed, and it is the
    # first line of the history it keeps. What the poet was told is the
    # poet's thread, and the tutor does not read it.
    (turns, _, _) = tutor.seen[0]
    assert [turn.content for turn in turns] == [pipeline_module.SWITCH_GREETING]
    kept = await history(session, tutor)
    assert kept[0].content == pipeline_module.SWITCH_GREETING
    assert all(turn.content != "get me the tutor" for turn in kept)


async def test_the_old_agents_words_stay_its_own_turn() -> None:
    # A preamble the poet spoke before handing over is its own assistant
    # turn: the switch happens between two turns, not inside one. Which
    # thread each of those turns is on is what #190 decided: the
    # preamble stays on the poet's, and the tutor's begins with the seed
    # that opened it.
    poet = ScriptedLlm([["One moment.", call("switch_agent", agent="tutor")]])
    tutor = ScriptedLlm(["Tutor here."])
    session = session_for(base_config(), BOTH_MAC, {"poet": poet, "tutor": tutor})
    assert await run_reply(session, "the tutor please") == ["Tutor here."]
    assert [turn.content for turn in await history(session, tutor)] == [
        pipeline_module.SWITCH_GREETING,
        "Tutor here.",
    ]
    assert [turn.content for turn in poet.seen[0][0]] == ["the tutor please"]


async def test_a_switch_to_an_unbound_agent_is_refused_by_the_agent_talking() -> None:
    poet = ScriptedLlm(
        [[call("switch_agent", agent="stranger")], "I cannot reach that one."]
    )
    session = session_for(base_config(), BOTH_MAC, {"poet": poet})
    assert await run_reply(session, "get me the stranger") == ["I cannot reach that one."]
    assert talking(session) == "poet"

    (result,) = [
        result for turns, _, _ in poet.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert "not bound to" in result.content
    # The refusal names what the device can reach, so the model can say.
    assert "poet" in result.content and "tutor" in result.content


async def test_a_switch_to_the_agent_already_speaking_is_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Naming the current persona mid-conversation used to hand over to
    # it: a second LLM round that only greeted a user already talking.
    poet = ScriptedLlm([[call("switch_agent", agent="poet")], "I am the poet already."])
    session = session_for(base_config(), BOTH_MAC, {"poet": poet})

    with caplog.at_level("INFO"):
        assert await run_reply(session, "let me talk to the poet") == ["I am the poet already."]
    assert talking(session) == "poet"
    # No handover was announced, and the second round continued the
    # reply rather than greeting a user who is already mid-conversation.
    assert not [record for record in caplog.records if getattr(record, "event", "") == "handover"]
    assert all(
        turn.content != pipeline_module.SWITCH_GREETING
        for turns, _, _ in poet.seen
        for turn in turns
    )

    (result,) = [
        result for turns, _, _ in poet.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert "already speaking as this assistant" in result.content


async def test_only_one_handover_happens_per_reply() -> None:
    poet = ScriptedLlm([[call("switch_agent", agent="tutor")]])
    tutor = ScriptedLlm([[call("switch_agent", agent="poet")], "Staying put, then."])
    session = session_for(base_config(), BOTH_MAC, {"poet": poet, "tutor": tutor})

    assert await run_reply(session, "keep switching") == ["Staying put, then."]
    assert talking(session) == "tutor"
    (result,) = [
        result for turns, _, _ in tutor.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert "already been handed over" in result.content


async def test_two_switches_in_one_round_honour_the_first_and_refuse_the_rest() -> None:
    poet = ScriptedLlm(
        [[call("switch_agent", agent="tutor"), call("switch_agent", agent="poet")]]
    )
    tutor = ScriptedLlm(["Tutor here."])
    session = session_for(base_config(), BOTH_MAC, {"poet": poet, "tutor": tutor})
    await run_reply(session, "both please")
    assert talking(session) == "tutor"


async def test_remembering_is_offered_and_executed() -> None:
    store = lane_memory()
    script = ScriptedLlm(
        [[call("remember", text="the user is vegetarian")], "I will keep that in mind."]
    )
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=store)

    assert await run_reply(session, "remember I am vegetarian") == ["I will keep that in mind."]
    assert [tool.name for tool in script.seen[0][1]] == [
        "remember",
        "update_memory",
        "forget",
        "restore_memory",
        "recall",
        "set_state",
        "clear_state",
        "new_conversation",
        "resume_conversation",
    ]
    assert "the user is vegetarian" in facts(store)

    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert not result.is_error


async def test_a_builtin_asked_with_arguments_it_cannot_use_comes_back_as_an_error() -> None:
    """The refusal as the model receives it, which is the shape any
    builtin's bad arguments take: an error result it reads and can call
    again from, rather than an ended reply. `remember` is offered here
    and its argument validation refuses before the store is touched, so
    what is under test is the loop's rendering rather than any store."""
    store = lane_memory()
    script = ScriptedLlm([[call("remember", text=None)], "Let me put that another way."])
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=store)
    await run_reply(session, "remember nothing in particular")

    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert 'needs a "text" argument' in result.content
    assert facts(store) == ""


async def test_a_remembered_fact_is_in_the_next_replys_prompt() -> None:
    store = lane_memory()
    await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
    script = ScriptedLlm(["Noted."])
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=store)
    await run_reply(session, "what do you know about me?")

    (system,) = script.systems
    assert "the user is vegetarian" in system
    assert system.startswith("POET")


async def test_a_fact_forgotten_mid_reply_is_out_of_the_next_rounds_prompt() -> None:
    """The clock the memory blocks have always kept, on the tool that
    takes something away: the know-how half is cached for the activation
    and the scopes are read every round, so a fact forgotten in one round
    of a reply is gone from the round after it, inside the same reply.

    The removal answers with the words it took, which is what the agent
    then says out loud so the user can ask for them back.
    """
    store = lane_memory()
    fact_id = await store.add(
        MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet"
    )
    script = ScriptedLlm([[call("forget", id=fact_id)], "That is gone now."])
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=store)

    assert await run_reply(session, "forget that I am vegetarian") == ["That is gone now."]

    asked, after = script.systems
    assert "the user is vegetarian" in asked
    assert "vegetarian" not in after
    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert not result.is_error
    assert result.content == f"Forgot [{fact_id}]: the user is vegetarian"


async def test_a_device_fact_reaches_the_next_agent_on_that_device() -> None:
    """What the device scope is for, at the seam where it shows: the poet
    keeps one note about the place and one about the person, and the
    tutor it hands the conversation to on the same board is sent the
    first and not the second.

    Driven through a handover because that is how a second agent gets to
    speak on one device, and the tutor's prompt is assembled after the
    switch, which is exactly the round under test.
    """
    store = lane_memory()
    poet = ScriptedLlm(
        [
            [
                call("remember", text="the kettle is loud", scope="device"),
                call("remember", text="the user is vegetarian"),
            ],
            [call("switch_agent", agent="tutor")],
        ]
    )
    tutor = ScriptedLlm(["Hello from the tutor."])
    session = session_for(
        base_config(), BOTH_MAC, {"poet": poet, "tutor": tutor}, memory=store
    )

    assert await run_reply(session, "the kettle here is loud") == ["Hello from the tutor."]

    # The poet's own next round carries both, under two headings.
    assert "the kettle is loud" in poet.systems[-1]
    assert "the user is vegetarian" in poet.systems[-1]
    # The tutor is sent the place's note and not the poet's own memory,
    # which is the scope separation from both sides at once.
    (handed,) = tutor.systems
    assert "the kettle is loud" in handed
    assert "the user is vegetarian" not in handed


async def test_the_ledger_is_written_and_cleared_through_its_two_tools() -> None:
    """The two state tools end to end, in the order a conversation uses
    them: something becomes true, it changes, and then it stops being
    true. The ledger is read back through the store's own prompt read,
    keyed by the thread this session is on, because that is what the
    next round would be sent."""
    store = lane_memory()
    script = ScriptedLlm(
        [
            [call("set_state", key="scene", value="the tavern")],
            "You are in the tavern.",
            [call("set_state", key="scene", value="the docks")],
            "Off you go.",
            [call("clear_state", key="scene")],
            "The scene is over.",
        ]
    )
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=store)
    thread = events_of(session).conversation
    assert thread is not None

    await run_reply(session, "we are in a tavern")
    assert ledger(store, thread) == "- scene: the tavern"

    await run_reply(session, "we leave for the docks")
    assert ledger(store, thread) == "- scene: the docks"

    await run_reply(session, "that scene is over")
    assert ledger(store, thread) == ""


async def test_a_state_tool_confirms_what_it_did_in_the_models_own_words() -> None:
    """What the model reads back, which is what it phrases out loud: the
    note as it was written, and the honest answer for a name that held
    nothing."""
    store = lane_memory()
    script = ScriptedLlm(
        [
            [
                call("set_state", key="  hit  points ", value="9  of  12"),
                call("clear_state", key="scene"),
            ],
            "Noted.",
        ]
    )
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=store)

    await run_reply(session, "I take three damage")

    written, cleared = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert not written.is_error and not cleared.is_error
    # Normalized to the line the ledger stores, so the confirmation and
    # the next round's block say the same thing.
    assert written.content == "Noted hit points: 9 of 12"
    assert cleared.content == "Nothing was written down under scene"


@pytest.mark.parametrize(
    ("called", "arguments", "refusal"),
    [
        ("set_state", {"key": "scene"}, builtin.SET_STATE_NEEDS_BOTH),
        ("set_state", {"value": "the tavern"}, builtin.SET_STATE_NEEDS_BOTH),
        ("set_state", {"key": "  ", "value": "the tavern"}, builtin.SET_STATE_NEEDS_BOTH),
        ("clear_state", {}, builtin.CLEAR_STATE_NEEDS_A_KEY),
        ("clear_state", {"key": None}, builtin.CLEAR_STATE_NEEDS_A_KEY),
    ],
)
async def test_a_state_tool_asked_with_arguments_it_cannot_use_refuses(
    called: str, arguments: dict[str, Any], refusal: str
) -> None:
    """The ValueError shape every builtin's bad arguments take: an error
    result the model reads and can call again from. The sentence is
    compared by equality against the module's own constant, and nothing
    is written."""
    store = lane_memory()
    script = ScriptedLlm([[call(called, **arguments)], "Let me try that again."])
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=store)
    thread = events_of(session).conversation
    assert thread is not None

    await run_reply(session, "keep track of this")

    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    # The loop names the tool that failed and then says what it said, so
    # the sentence is the end of the result rather than the whole of it.
    assert result.content.endswith(refusal)
    assert ledger(store, thread) == ""


# Two writes to one thing in a round
#
# Both name the same thing by an identity the model chose, a ledger key
# or a fact's number, so what is true afterwards is whichever ran last:
# the model's order is the answer. The round dispatches everything else
# concurrently, which would leave the answer to whichever transaction
# reached the chain's lock first.
#
# The interleaving is forced rather than hoped for. The first write is
# parked until the second one arrives, with a bound so the ordered
# implementation is not deadlocked by its own correctness: under the
# concurrent dispatch the second overtakes it and the first commits
# last, and under the ordered one the first waits out the bound and the
# second never starts before it finishes.

# How long a parked write waits for the one that is meant to overtake
# it. Long enough that a concurrent dispatch always arrives inside it,
# short enough that the ordered dispatch pays it twice per test and
# nothing else.
OVERTAKE_S = 0.25


@contextlib.asynccontextmanager
async def the_first_write_parked(*written: str) -> Any:
    """The first of these store calls held until a second one arrives.

    Every entry point a case uses is gated, because what the cases need
    is one shape with different tools: whichever the model asks for
    first is the one parked, and whichever comes second is the one that
    releases it. The names are the store's own, since what is under test
    is which order two of its writes land in.
    """
    arrived = asyncio.Event()
    started = 0

    async def park() -> None:
        nonlocal started
        started += 1
        if started > 1:
            arrived.set()
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(arrived.wait(), OVERTAKE_S)

    def gating(real: Any) -> Any:
        async def gated(self: MemoryStore, *args: Any, **kwargs: Any) -> Any:
            await park()
            return await real(self, *args, **kwargs)

        return gated

    with pytest.MonkeyPatch.context() as patching:
        for name in written:
            patching.setattr(MemoryStore, name, gating(getattr(MemoryStore, name)))
        yield


async def test_two_writes_to_one_entry_in_a_round_land_in_the_model_s_order() -> None:
    """Set, then set again: the second is what the next round reads."""
    store = lane_memory()
    script = ScriptedLlm(
        [
            [
                call("set_state", key="scene", value="the tavern"),
                call("set_state", key="scene", value="the docks"),
            ],
            "Off to the docks.",
        ]
    )
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=store)
    thread = events_of(session).conversation
    assert thread is not None

    async with the_first_write_parked("set_state", "clear_state"):
        await run_reply(session, "we go from the tavern to the docks")

    assert ledger(store, thread) == "- scene: the docks"


async def test_a_write_and_a_clear_of_one_entry_land_in_the_model_s_order() -> None:
    """Set, then clear: the entry is gone. Run concurrently, the write
    can commit after the clear and leave the note standing, which is the
    reading a model would then be given as current."""
    store = lane_memory()
    script = ScriptedLlm(
        [
            [
                call("set_state", key="scene", value="the tavern"),
                call("clear_state", key="scene"),
            ],
            "That scene is over.",
        ]
    )
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=store)
    thread = events_of(session).conversation
    assert thread is not None

    async with the_first_write_parked("set_state", "clear_state"):
        await run_reply(session, "we are in a tavern, and then we are not")

    assert ledger(store, thread) == ""


async def test_a_correction_and_a_removal_of_one_fact_land_in_the_models_order() -> None:
    """Correct, then forget: what the removal answers with, and
    therefore what the agent says out loud, is the corrected words.

    Run concurrently the removal can overtake the correction, and then
    the agent reads out something the user has already replaced, while
    the correction meets a fact that is no longer there.
    """
    store = lane_memory()
    fact_id = await store.add(
        MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet"
    )
    script = ScriptedLlm(
        [
            [
                call("update_memory", id=fact_id, text="the user is vegan"),
                call("forget", id=fact_id),
            ],
            "Taken off the list.",
        ]
    )
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=store)

    async with the_first_write_parked("update", "forget"):
        await run_reply(session, "I am vegan now, and forget that too")

    corrected, removed = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert not corrected.is_error and not removed.is_error
    assert removed.content == f"Forgot [{fact_id}]: the user is vegan"
    assert facts(store) == ""


async def test_a_forgotten_fact_is_brought_back_through_the_tool_that_undoes_it() -> None:
    """The undo end to end, as a model reaches it: `forget` in one reply
    and `restore_memory` in the next, both as calls the session routes
    rather than as functions this suite imports.

    The dispatch arm is the whole of what this adds to the executor's own
    tests: a tool the runtime cannot route is a tool an agent cannot
    speak, whatever the function behind it does.
    """
    store = lane_memory()
    fact_id = await store.add(
        MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet"
    )
    script = ScriptedLlm(
        [
            [call("forget", id=fact_id)],
            "I have forgotten that you are vegetarian.",
            [call("restore_memory")],
            "It is back.",
        ]
    )
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=store)

    await run_reply(session, "forget that I am vegetarian")
    assert facts(store) == ""

    assert await run_reply(session, "no, put that back") == ["It is back."]

    forgotten, restored = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert not forgotten.is_error and not restored.is_error
    assert restored.content == "Brought back: the user is vegetarian"
    # And in the database, with the number it always had, which is what
    # makes it the fact rather than a new one saying the same thing.
    assert facts(store) == "- the user is vegetarian"
    assert store.recall("poet", POET_MAC, "vegetarian").startswith(f"- [{fact_id}]")


# A scope at its cap, where remembering is a mutation like any other
#
# Every write to a scope that is full also prunes it, so a `remember`
# and an edit of the oldest fact in one round decide each other even
# though neither addresses what the other named. Run out of order, the
# append deletes the row the edit just wrote and both answer success,
# which is the one shape a model cannot recover from: it was told the
# correction landed.
#
# The cap is one line here, which is the smallest arrangement that has
# an oldest fact and no room for a second.


def a_scope_of_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "MAX_LINES", 1)


async def test_remembering_before_a_correction_leaves_the_correction_pruned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model asks to remember something new and then to correct the
    fact that is already there, in that order, on a scope with room for
    one.

    In that order the new fact arrives, the prune takes the old one, and
    the correction meets a fact that is not there any more, which the
    agent can say. Reversed, the correction succeeds, the append prunes
    the row it just wrote, and the model has been told a correction
    landed that nothing kept.
    """
    a_scope_of_one(monkeypatch)
    store = lane_memory()
    fact_id = await store.add(
        MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet"
    )
    script = ScriptedLlm(
        [
            [
                call("remember", text="the user has a dog"),
                call("update_memory", id=fact_id, text="the user is vegan"),
            ],
            "Noted.",
        ]
    )
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=store)

    async with the_first_write_parked("add", "update"):
        await run_reply(session, "I have a dog, and I am vegan now")

    remembered, corrected = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert not remembered.is_error
    assert corrected.is_error
    assert corrected.content.endswith(store_module.NO_FACT_TO_UPDATE)
    assert facts(store) == "- the user has a dog"


async def test_remembering_before_a_removal_leaves_nothing_to_remove(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same reversal on the other edit, where what it costs is the
    undo: reversed, the removal holds the old fact for a restore that
    the append then makes meaningless, and the agent says out loud that
    it forgot something the prune was about to take anyway."""
    a_scope_of_one(monkeypatch)
    store = lane_memory()
    fact_id = await store.add(
        MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet"
    )
    script = ScriptedLlm(
        [
            [
                call("remember", text="the user has a dog"),
                call("forget", id=fact_id),
            ],
            "Done.",
        ]
    )
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=store)

    async with the_first_write_parked("add", "forget"):
        await run_reply(session, "I have a dog, and forget the other thing")

    remembered, removed = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert not remembered.is_error
    assert removed.is_error
    assert removed.content.endswith(store_module.NO_FACT_TO_FORGET)
    assert facts(store) == "- the user has a dog"
    # And nothing is being held for an undo that would bring back a fact
    # this scope has no room for.
    assert store.recall("poet", POET_MAC, "vegetarian") == ""


def ledger(store: MemoryStore, conversation: str) -> str:
    """One conversation's ledger as the next round would be sent it."""
    return store.read_for_prompt("poet", None, conversation).state


def facts(store: MemoryStore) -> str:
    """The poet's own remembered facts, as the next round would be sent
    them: the prompt read with no device and no conversation, which is
    the one scope this suite asks about."""
    return store.read_for_prompt("poet", None, None).agent


async def test_malformed_arguments_come_back_as_an_error_result() -> None:
    broken = ToolCall(id="c1", name="remember", malformed_arguments="{text: oops")
    script = ScriptedLlm([[broken], "Let me try that again."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    assert await run_reply(session, "remember this") == ["Let me try that again."]

    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert "not a JSON object" in result.content


# Not real credentials. The first stands for whatever a model streams
# where a JSON object belongs; the second is shaped like a tool name, so
# both LLM APIs accept it and the publishing rule leaves it untouched,
# which is how a credential arrives as a name a peer chose (#154).
ARGUMENT_SENTINEL = "sk-test-4f8b2c9e-never-a-real-credential"
NAME_SENTINEL = "sk_test_4f8b2c9e_never_a_real_credential"


async def refuse_malformed(
    name: str, caplog: pytest.LogCaptureFixture
) -> tuple[str, logging.LogRecord]:
    """One call the dispatch turns away at its first line, and the
    warning it wrote about it."""
    arguments = f'{{"text": "{ARGUMENT_SENTINEL}"'
    broken = ToolCall(id="c1", name=name, malformed_arguments=arguments)
    script = ScriptedLlm([[broken], "Let me try that again."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    with caplog.at_level("DEBUG"):
        await run_reply(session, "do it")
    (line,) = [record for record in caplog.records if "unparseable" in record.getMessage()]
    return arguments, line


async def test_malformed_arguments_are_logged_by_length_and_never_by_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What a model streamed instead of a JSON object is its own output,
    and the retained logs keep no content (#120). The line that reports
    the refusal says how much of it there was, which is what tells a
    truncated object from an answer in prose, and the bytes reach no
    record at any level."""
    arguments, line = await refuse_malformed("remember", caplog)

    assert f"{len(arguments)} characters" in line.getMessage()
    # A builtin is the one name this server authored, so this line says
    # it, exactly as the `tool_call` event beside it does.
    assert 'builtin tool "remember"' in line.getMessage()
    assert not any(ARGUMENT_SENTINEL in record.getMessage() for record in caplog.records)
    assert not any(ARGUMENT_SENTINEL in str(record.args) for record in caplog.records)


async def test_a_malformed_call_under_a_name_a_peer_chose_names_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The same rule the narrowing gave `tool_call`, on the plain line
    that reports the refusal: a name no namespace publishes is the
    model's own invention and a device's is the board's vocabulary, so
    the warning says which namespace was reached into and nothing more.
    Both sentinels are hunted, since this call carries one of each."""
    arguments, line = await refuse_malformed(NAME_SENTINEL, caplog)

    assert line.getMessage().endswith(
        f"unknown tool got {len(arguments)} characters of unparseable arguments"
    )
    for record in caplog.records:
        assert NAME_SENTINEL not in record.getMessage()
        assert NAME_SENTINEL not in str(record.args)
        assert ARGUMENT_SENTINEL not in record.getMessage()
        assert ARGUMENT_SENTINEL not in str(record.args)


def shadowing_config(*, inner: bool) -> Config:
    """The entry `home`, publishing one tool under a name that the entry
    `home__inside` would own; `inner` adds that second entry.

    The test server lists whatever `VINGA_TEST_SHADOWED_TOOL` names, so
    under `home` the tool `inside__secret_word` publishes as
    `home__inside__secret_word`, which is exactly what `home__inside`
    publishes its own `secret_word` as. Reloading from one of these to
    the other moves a published name between entries without changing
    anything the model sees."""
    entry = {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(STDIO_SERVER)],
    }
    shadowed = {"env": {SHADOWED_TOOL_ENV: "inside__secret_word"}}
    entries: dict[str, object] = {"home": entry | shadowed}
    granted = ["home"]
    if inner:
        entries["home__inside"] = entry
        granted.append("home__inside")
    return base_config(
        mcp_servers=entries,
        agents={
            "poet": {"prompt": "POET", "tts": "tenor", "mcp": granted},
            "tutor": {"prompt": "TUTOR", "tts": "alto"},
        },
    )


async def test_a_name_that_changes_owner_between_calls_is_refused_not_rerouted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A reload can land between a call being resolved and being run,
    and a published name can move to a more specific entry when it does.

    Following the move would run one server's tool under another
    server's timeout and then record and log the entry that did not run
    it, which is the one thing the reservation exists to prevent. So the
    call is refused: the registry is told which entry the caller meant
    and answers that the name is no longer served by it. The window is
    driven directly, because it is the window and nothing about the
    surrounding loop is under test."""
    name = "home__inside__secret_word"
    servers = McpServers.build(shadowing_config(inner=False))
    await servers.start_all()
    session = session_for(base_config(), POET_MAC, mcp_servers=servers)
    try:
        assert servers.owner_of(name) == "home"
        # White-box for the four reads in this test, per the docstring:
        # the window is between a reservation and the dispatch it routes
        # by, and it is only a window because a reload lands inside it.
        # A reply driven around it would put a model round, a synthesis
        # and a device send between the two, so the reload would have to
        # be timed into a gap the test does not control, and what is
        # under test is the routing rather than any of that.
        (slot,) = session.runtime._reserve_tools([call(name)])
        assert session.runtime._turn.reserved(slot).entry == "home"

        await Applying(servers, shadowing_config(inner=False)).apply(
            reading(shadowing_config(inner=True))
        )
        # The move really happened, or this test proves nothing: the
        # name is the inner entry's now, and its tool answers
        # differently, so a reroute would be visible in the result.
        assert servers.owner_of(name) == "home__inside"

        with caplog.at_level("INFO"):
            result = await session.runtime._run_one(call(name), slot)
    finally:
        await servers.stop_all()

    assert result.is_error
    assert "rhubarb" not in result.content, "the call was rerouted to the new owner"
    assert 'no longer served by MCP server "home"' in result.content
    # The event and the row both still say the entry the call was
    # reserved against, and both say it failed.
    (logged,) = [
        record for record in caplog.records if getattr(record, "event", None) == "tool_call"
    ]
    assert (logged.source, logged.entry, logged.is_error) == ("mcp", "home", True)
    # White-box, per the note at the reservation above.
    executed = session.runtime._turn.reserved(slot)
    assert (executed.source, executed.entry, executed.is_error) == ("mcp", "home", True)
    assert executed.duration_ms is not None


def test_the_switch_agent_schema_is_json_serializable() -> None:
    # It goes over the wire to two different APIs; anything exotic in it
    # would fail there rather than here.
    json.dumps(switch_agent_tool(["poet", "tutor"]).input_schema)


async def test_a_device_bound_to_two_agents_is_answered_in_the_new_voice() -> None:
    # The M5 assertions, reused across a handover: the reply text comes
    # from the second agent's own prompt and the audio from its voice.
    config = base_config(
        providers={
            "llm": {
                "poetic": {"type": "mock", "reply": "{system} says hello."},
                "handover": {
                    "type": "mock",
                    "reply": "unused",
                    "tool_when": "tutor",
                    "tool_name": "switch_agent",
                    "tool_arguments": {"agent": "tutor"},
                },
            },
            "asr": {"mock": {"type": "mock", "text": "the tutor please"}},
            "tts": {
                "tenor": {"type": "mock", "tone_hz": POET_TONE},
                "alto": {"type": "mock", "tone_hz": TUTOR_TONE},
            },
            "vad": {"mock": {"type": "mock"}},
        },
        agent_defaults={"asr": "mock", "vad": "mock"},
        agents={
            "poet": {"prompt": "POET", "llm": "handover", "tts": "tenor"},
            "tutor": {"prompt": "TUTOR", "llm": "poetic", "tts": "alto"},
        },
    )
    with TestClient(create_app(config)) as client:
        with connect(client, device_id=BOTH_MAC) as websocket:
            shake_hands(websocket)
            texts, audio = say_something(websocket)

    assert sentences(texts) == ["TUTOR says hello."]
    assert tone_strength(audio, TUTOR_TONE) > 10 * tone_strength(audio, POET_TONE)


# What a reload changes for a conversation that is already running


async def test_a_reload_between_replies_changes_what_the_next_one_may_reach() -> None:
    """The promise the whole reload path exists for, at the seam where a
    session meets it: the snapshot is taken per reply and asks the
    registry by agent, so a grant written and reloaded mid-conversation
    is on offer in the next reply and no session was dropped."""
    servers = McpServers.build(registry_config(granted=False))
    await servers.start_all()
    script = ScriptedLlm(["First.", "Second."])
    session = session_for(base_config(), POET_MAC, {"poet": script}, mcp_servers=servers)
    try:
        await run_reply(session, "hello")
        offered_before = {tool.name for tool in script.seen[0][1]}

        await Applying(servers, registry_config(granted=False)).apply(
            reading(registry_config(granted=True))
        )
        await run_reply(session, "hello again")
    finally:
        await servers.stop_all()

    assert not any(name.startswith("tools__") for name in offered_before)
    assert "tools__secret_word" in {tool.name for tool in script.seen[-1][1]}


async def test_a_hello_without_mcp_gets_no_device_tool_client() -> None:
    with TestClient(create_app(base_config())) as client:
        with connect(client, device_id=POET_MAC) as websocket:
            websocket.send_text(json.dumps(dict(DEVICE_HELLO, features={})))
            websocket.receive_text()
            # A device that never advertised MCP sending one anyway is
            # logged and ignored rather than ending the session.
            websocket.send_text(json.dumps({"type": "mcp", "payload": {"jsonrpc": "2.0"}}))
            texts, _ = say_something(websocket)
    assert sentences(texts) == ["POET heard hello."]


async def test_a_tool_of_an_entry_whose_name_holds_the_separator_is_dispatched() -> None:
    """The session's own routing, on the name shape that used to break
    it: `home__inside` is a legal entry name, and reading the name by
    splitting at the first separator looked for a server called `home`,
    so the tool was offered in the snapshot and answered "there is no
    tool called" when the model asked for it. Both the dispatch and the
    per-entry timeout read the entry off the call's reservation, which
    is the registry's one answer about who owns the name."""
    config = base_config(
        mcp_servers={
            "home__inside": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(STDIO_SERVER)],
                "tool_timeout_s": 7.5,
            }
        },
        agents={
            "poet": {"prompt": "POET", "tts": "tenor", "mcp": ["home__inside"]},
            "tutor": {"prompt": "TUTOR", "tts": "alto"},
        },
    )
    servers = McpServers.build(config)
    await servers.start_all()
    script = ScriptedLlm([[call("home__inside__secret_word")], "The word is rhubarb."])
    session = session_for(base_config(), POET_MAC, {"poet": script}, mcp_servers=servers)
    try:
        assert await run_reply(session, "tell me") == ["The word is rhubarb."]
    finally:
        await servers.stop_all()

    offered = {tool.name for tool in script.seen[0][1]}
    assert "home__inside__secret_word" in offered
    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert not result.is_error
    assert result.content == "rhubarb"
    # The entry's own timeout, taken from the same reservation the
    # dispatch routes by rather than from a second reading of the name.
    # White-box: how long a tool may take is read off the reservation
    # the dispatch routes by, and nothing outside reports it. A timeout
    # observed by waiting one out would be a seven-and-a-half second
    # test that proves the bound fired, not which entry it came from.
    reserved = session.runtime._classified(call("home__inside__secret_word"), 0)
    assert session.runtime._timeout_for(reserved) == 7.5


# The arguments the far side is handed
#
# A small local model routinely sends an argument under the wrong JSON
# type, and the far side is entitled to refuse it (#383). One conversion
# happens on the way out, at the dispatch, for every source alike; the
# record, the API and the history keep what the model sent. What follows
# pins both halves of that split where each is actually read, and pins
# the line the conversion stops at.


# One frame of silence, which the mock ASR answers with the configured
# transcript. The two cases below drive a whole reply, because what a
# turn recorded is decided when the reply ends.
UTTERANCE = b"\x00\x00" * 320


def a_board_with_volume() -> FakeDevice:
    """A device listing the one tool whose schema declares an integer."""
    return FakeDevice([{"tools": [VOLUME]}])


async def test_a_quoted_integer_reaches_the_board_as_an_integer() -> None:
    """The split, at all three of the surfaces that read it.

    The wire is the point of the exercise: the firmware validates its
    own tools, so `"40"` is a call that fails and `40` is a call that
    works. The record and the next round are the other half, and they
    are the half a coercion applied one step earlier would have taken
    with it: what the model passed is what an operator diagnosing a
    marginal model reads, and it is what #383 itself was diagnosed
    from.
    """
    device = a_board_with_volume()
    await device.client.discover()
    script = ScriptedLlm(
        [[call("self_audio_speaker_set_volume", volume="40")], "Turned it up."]
    )
    session, spy, _ = recording_session(scripts={"poet": script})
    # White-box: a board's tools arrive from a discovery run the edge
    # starts over the wire after the hello, and this session has no
    # socket to run one on. The published tool and its schema are what
    # the case is about, so they have to be there at all.
    session._device_tools = device.client

    await drive_reply(session, UTTERANCE)

    # The wire, which is the device's own JSON-RPC frame.
    (sent,) = [one for one in device.sent if one.get("method") == "tools/call"]
    assert sent["params"]["arguments"] == {"volume": 40}
    assert isinstance(sent["params"]["arguments"]["volume"], int)
    # The durable record, read off the completed turn rather than off
    # the accumulator that was still being filled while the reply ran.
    (invocation,) = only_record(spy).tools
    assert invocation.arguments == {"volume": "40"}
    assert isinstance(invocation.arguments["volume"], str)
    # And the history the next round was written against, which is the
    # surface the model itself reads back.
    asked = [
        one
        for turns, _, _ in script.seen
        for turn in turns
        for one in turn.tool_calls
    ]
    assert [one.arguments for one in asked] == [{"volume": "40"}]


async def test_a_value_no_conversion_can_help_reaches_the_board_unchanged() -> None:
    """The other side of the line, with a far side that refuses.

    The fake device answers success for anything it was not scripted
    for, so a permissive board would let a guessed conversion pass
    unnoticed. This one answers the way the firmware answers a value it
    cannot use, which is what makes "left exactly as it arrived"
    different from "converted into something that worked".
    """
    device = a_board_with_volume()
    device.call_results["self.audio_speaker.set_volume"] = {
        "content": [{"type": "text", "text": "volume must be an integer"}],
        "isError": True,
    }
    await device.client.discover()
    script = ScriptedLlm(
        [
            [call("self_audio_speaker_set_volume", volume="a lot")],
            "I could not set the volume.",
        ]
    )
    session = session_for(base_config(), POET_MAC, {"poet": script})
    # White-box, for the reason the case above gives.
    session._device_tools = device.client

    assert await run_reply(session, "turn it up") == ["I could not set the volume."]

    (sent,) = [one for one in device.sent if one.get("method") == "tools/call"]
    assert sent["params"]["arguments"] == {"volume": "a lot"}
    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert result.content == "volume must be an integer"


@pytest.mark.parametrize(
    ("sent", "erased"),
    [
        ("true", True),
        ("false", False),
        ("True", False),
        ("1", False),
    ],
)
async def test_only_the_quoted_json_true_forgets_a_fact_for_good(
    sent: str, erased: bool
) -> None:
    """The one upgrade-visible consequence of the boolean coercion, and
    the reason it is pinned through the whole pipeline rather than at
    the store.

    `forget` erases permanently only when `permanently is True`, so
    before this change the string `"true"` chose the recoverable
    removal. It now erases, because `forget` declares the argument
    `boolean` and the model said true, quoted. Everything else still
    takes the recoverable path, which is the direction a misread
    argument must fail in: a held fact costs a row and an erased one
    costs the fact.
    """
    store = lane_memory()
    fact_id = await store.add(
        MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet"
    )
    script = ScriptedLlm(
        [
            [call("forget", id=fact_id, permanently=sent)],
            "Forgotten.",
            [call("restore_memory")],
            "There you go.",
        ]
    )
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=store)

    await run_reply(session, "forget that I am vegetarian")
    assert facts(store) == ""
    await run_reply(session, "no, put that back")

    _, restored = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert restored.is_error is erased
    assert facts(store) == ("" if erased else "- the user is vegetarian")
