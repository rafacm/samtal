"""Whether an agent may remember at all, and when the answer moves.

The section is `agents.<name>.memory`, in the `filler` mould: unset
inherits, naming one replaces it wholly, and `memory: {enabled: false}`
opts an agent out. What it decides is two things nothing used to
decide, and this suite is written around the two of them being one
answer:

- the tools the agent is offered, and refused again if it asks for one
  anyway;
- the scope blocks its prompt carries, its board's included, and the
  round trip that reads them.

The clock is the third subject. The tools are snapshotted per reply and
the blocks are assembled per round, so the policy is resolved once, on
the line the snapshot is taken on, and both halves read that one
answer. A reload landing between two rounds of one reply is the case
that says whether it is true, and the two interleavings below force it
rather than reason about it.

Sibling agents on one board are how the "whole" in "off is whole" is
proven: what one agent may not read is exactly what the one beside it
still is.
"""

from collections.abc import AsyncIterator, Callable, Sequence

import pytest

from tests.support.configs import BOTH_MAC, POET_MAC, base_config, world
from tests.support.providers import ScriptedLlm
from tests.support.sessions import agent_providers, call, run_reply, session_for
from tests.support.stores import memory as lane_memory
from tests.support.stores import memory_rows
from vinga_server.app import _prompt_preview
from vinga_server.config import Config
from vinga_server.generation import Generation, Generations
from vinga_server.memory.store import MemoryScope, MemoryStore, PromptMemory
from vinga_server.providers import (
    LlmEvent,
    LlmProvider,
    TextDelta,
    ToolCall,
    ToolChoice,
    ToolDef,
    Turn,
)
from vinga_server.tools.mcp import McpServers

FACT = "the user is vegetarian"

NOTE = "the kitchen radiator rattles"

# The seven the section switches, in the order they are offered, and the
# two beside them that it does not touch. Written out rather than read
# off `names.MEMORY_TOOL_NAMES`, because what these cases pin is the
# offer an agent actually receives: a list derived from the tuple the
# source loops over would agree with it however either of them moved.
MEMORY_TOOLS = [
    "remember",
    "update_memory",
    "forget",
    "restore_memory",
    "recall",
    "set_state",
    "clear_state",
]

CONVERSATION_TOOLS = ["new_conversation", "resume_conversation"]

OFF: dict[str, object] = {"memory": {"enabled": False}}

ON: dict[str, object] = {"memory": {"enabled": True}}


def paired(poet: dict[str, object] | None = None, tutor: dict[str, object] | None = None) -> Config:
    """The lane's two agents, with whatever memory section the case is
    about. The board bound to both of them is `BOTH_MAC`, which is what
    makes them siblings rather than two deployments."""
    return base_config(
        agents={
            "poet": {"prompt": "POET", "tts": "tenor", **(poet or {})},
            "tutor": {"prompt": "TUTOR", "tts": "alto", **(tutor or {})},
        }
    )


def offered(script: ScriptedLlm, round_index: int = 0) -> list[str]:
    """The tool names one round was handed, which is where a snapshot is
    observable from outside a session."""
    return [tool.name for tool in script.seen[round_index][1]]


# What the section decides, on one board


async def test_a_switched_off_agent_is_offered_nothing_its_sibling_reads() -> None:
    """The control case, both halves and both agents in one session.

    The two are on one board, so the device scope is a scope they share:
    the poet is switched off and the tutor is not, and the reply hands
    over from one to the other, which is what puts both offers and both
    prompts in front of one test. What the poet may not have is exactly
    what the tutor still does, its own facts and the board's notes
    alike.
    """
    store = lane_memory()
    await store.add(MemoryScope.AGENT, "poet", FACT, agent="poet")
    await store.add(MemoryScope.AGENT, "tutor", FACT, agent="tutor")
    await store.add(MemoryScope.DEVICE, BOTH_MAC, NOTE, agent="tutor")
    poet = ScriptedLlm([[call("switch_agent", agent="tutor")]])
    tutor = ScriptedLlm(["Tutor here."])
    session = session_for(
        paired(poet=OFF), BOTH_MAC, {"poet": poet, "tutor": tutor}, memory=store
    )

    await run_reply(session, "hand me over")

    # The offers, whole: the switch is structural and the two
    # conversation tools are unconditional, so the seven are the entire
    # difference between the agent that may remember and the one beside
    # it that may not.
    assert offered(poet) == ["switch_agent", *CONVERSATION_TOOLS]
    assert offered(tutor) == ["switch_agent", *MEMORY_TOOLS, *CONVERSATION_TOOLS]
    # And the prompts. The poet is sent its persona and nothing else,
    # although it has a fact of its own and stands on a board with a
    # note on it; the tutor is sent both blocks.
    assert poet.systems == ["POET"]
    assert FACT in tutor.systems[0]
    assert NOTE in tutor.systems[0]


async def test_a_withheld_memory_tool_is_refused_as_one_that_does_not_exist() -> None:
    """The offer is not the enforcement. A model that asks for a tool it
    was not shown is answered in the words a name nobody publishes gets,
    because for the length of this reply that is what it is, and the
    store is left exactly as it was.
    """
    store = lane_memory()
    script = ScriptedLlm([[call("remember", text=FACT)], "I could not do that."])
    session = session_for(paired(poet=OFF), POET_MAC, {"poet": script}, memory=store)

    assert await run_reply(session, "remember this") == ["I could not do that."]

    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert result.content == 'there is no tool called "remember"'
    assert memory_rows("facts", owner="poet") == []


@pytest.mark.parametrize("name", MEMORY_TOOLS)
async def test_a_withheld_tool_called_badly_is_still_a_tool_that_does_not_exist(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The refusal has to be the same answer whatever the model sent
    with the call, and a mangled argument list is the one thing that
    used to get a different one.

    "The arguments were not a JSON object" is what a tool that exists
    says to a bad call, so answering it for a withheld name would tell
    the model that the name is real and only its arguments were wrong,
    which is exactly the difference the fixed sentence exists to hide.
    All seven, because the answer is per name and a rule that held for
    one of them is not the rule.

    The retained log is the other half: a line saying a builtin got
    unparseable arguments is a line about a tool that was called, and
    for this reply there is no such tool to have been called.
    """
    broken = ToolCall(id="c1", name=name, malformed_arguments='{"text": "oops"')
    script = ScriptedLlm([[broken], "I could not do that."])
    session = session_for(paired(poet=OFF), POET_MAC, {"poet": script}, memory=lane_memory())

    with caplog.at_level("DEBUG"):
        assert await run_reply(session, "do it") == ["I could not do that."]

    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert result.content == f'there is no tool called "{name}"'
    assert not [record for record in caplog.records if "unparseable" in record.getMessage()]


async def test_an_agent_that_may_remember_still_hears_about_its_bad_arguments() -> None:
    """The other side of the reorder, which is what keeps it a
    reordering rather than a removal: a tool the agent does have,
    called with arguments the model never closed, is answered as it
    always was."""
    broken = ToolCall(id="c1", name="remember", malformed_arguments='{"text": "oops"')
    script = ScriptedLlm([[broken], "Let me try that again."])
    session = session_for(paired(), POET_MAC, {"poet": script}, memory=lane_memory())

    assert await run_reply(session, "do it") == ["Let me try that again."]

    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert "not a JSON object" in result.content


async def test_a_switched_off_agent_costs_no_read_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompt half is a skip rather than a filter. There is no block
    to assemble, so the round trip that would answer with one does not
    happen: a read whose answer is thrown away is a cost every round of
    every reply would pay for nothing.
    """
    store = lane_memory()
    await store.add(MemoryScope.AGENT, "poet", FACT, agent="poet")
    reads: list[str] = []
    real = MemoryStore.read_for_prompt

    def read(
        self: MemoryStore, agent: str, device: str | None, conversation: str | None
    ) -> PromptMemory:
        reads.append(agent)
        return real(self, agent, device, conversation)

    monkeypatch.setattr(MemoryStore, "read_for_prompt", read)
    session = session_for(
        paired(poet=OFF), POET_MAC, {"poet": ScriptedLlm(["Said."])}, memory=store
    )

    await run_reply(session, "hello")

    assert reads == []


async def test_the_preview_honours_the_section_of_the_agent_it_renders() -> None:
    """The inspection surface is the same answer asked a different way,
    so it is the same read: an operator previewing a switched-off agent
    is shown what that agent is actually sent, and the sibling's preview
    still carries its block."""
    store = lane_memory()
    await store.add(MemoryScope.AGENT, "poet", FACT, agent="poet")
    await store.add(MemoryScope.AGENT, "tutor", FACT, agent="tutor")
    config = paired(poet=OFF)
    preview = _prompt_preview(world(config), McpServers({}), store)

    assert (await preview("poet")).text == "POET"
    assert FACT in (await preview("tutor")).text


# Inheritance, in the shape every nested section has


def test_an_agent_under_neither_layer_may_remember() -> None:
    """The default, which is what every deployment written before this
    field has: nothing names a section anywhere, and the answer is the
    one the model declares."""
    assert base_config().memory_for_agent("poet").enabled


def test_the_agent_defaults_section_is_inherited() -> None:
    config = base_config(
        agent_defaults={"llm": "mock", "asr": "mock", "vad": "mock", **OFF}
    )
    assert not config.memory_for_agent("poet").enabled
    assert not config.memory_for_agent("tutor").enabled


def test_an_agents_own_section_replaces_the_inherited_one() -> None:
    """Both directions, because a replacement that only ever turned
    something off would be indistinguishable from a merge."""
    config = base_config(
        agent_defaults={"llm": "mock", "asr": "mock", "vad": "mock", **OFF},
        agents={
            "poet": {"prompt": "POET", "tts": "tenor"},
            "tutor": {"prompt": "TUTOR", "tts": "alto", **ON},
        },
    )
    assert not config.memory_for_agent("poet").enabled
    assert config.memory_for_agent("tutor").enabled


# One clock per reply
#
# The two halves are decided at two moments: the tools once, where the
# snapshot is taken, and the blocks on every round. So a reload that
# lands between two rounds of one reply is the case that says whether
# the policy was resolved once or twice, and the model below is what
# lands it: it applies the world while its own first round is
# streaming, which is after this reply took its snapshot and before the
# next round's prompt is assembled.


class ReloadingLlm(LlmProvider):
    """A model that installs a world between two rounds of one reply.

    The first round asks for a tool nobody publishes, which is what
    makes a second round happen without this needing a tool the policy
    under test may have withheld; every round after it speaks. What each
    round was offered and what it was sent are kept, because the claim
    is about the two of them together.
    """

    def __init__(self, apply: Callable[[], None]) -> None:
        self._apply = apply
        self.systems: list[str] = []
        self.offered: list[list[str]] = []

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        self.systems.append(system)
        self.offered.append([tool.name for tool in tools])
        if len(self.systems) == 1:
            self._apply()
            yield call("ghost_tool")
            return
        yield TextDelta("Said.")


def policies(llm: ReloadingLlm) -> list[tuple[bool, bool]]:
    """What policy each round observed, as the pair the section
    decides: whether the memory tools were offered, and whether the
    agent's own fact was injected."""
    return [
        ("remember" in names, FACT in system)
        for names, system in zip(llm.offered, llm.systems, strict=True)
    ]


def installed(generations: Generations, config: Config) -> None:
    """Put a world built from this configuration in front of new work,
    the way an apply does.

    Through `applying`, which is the only place a swap happens and
    therefore the last thing a reload does; what a full reload does
    before it is build engines this case never speaks through, since a
    session holds the providers it activated with.
    """
    current = generations.current()
    with generations.applying() as install:
        install(Generation(config, current.secrets, current.fillers, current.providers))


async def test_a_reload_that_switches_memory_off_lands_after_the_reply() -> None:
    """The whole-or-whole claim, from on to off. Both of the reply's
    rounds observe the policy it started under, tools and block
    together, and the next utterance is where the change lands."""
    store = lane_memory()
    await store.add(MemoryScope.AGENT, "poet", FACT, agent="poet")
    running = paired()
    llm = ReloadingLlm(lambda: installed(generations, paired(poet=OFF)))
    generations = world(running, providers=agent_providers(running, {"poet": llm}))
    session = session_for(running, POET_MAC, generations=generations, memory=store)

    await run_reply(session, "hello")
    assert policies(llm) == [(True, True), (True, True)]

    await run_reply(session, "again")
    assert policies(llm)[2] == (False, False)


async def test_a_reload_that_switches_memory_on_lands_after_the_reply() -> None:
    """And from off to on, which is the direction that cannot be passed
    by a runtime that simply never looks again."""
    store = lane_memory()
    await store.add(MemoryScope.AGENT, "poet", FACT, agent="poet")
    running = paired(poet=OFF)
    llm = ReloadingLlm(lambda: installed(generations, paired()))
    generations = world(running, providers=agent_providers(running, {"poet": llm}))
    session = session_for(running, POET_MAC, generations=generations, memory=store)

    await run_reply(session, "hello")
    assert policies(llm) == [(False, False), (False, False)]

    await run_reply(session, "again")
    assert policies(llm)[2] == (True, True)
