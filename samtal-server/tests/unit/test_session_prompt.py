"""What the running session sends as the system prompt, and when.

The assembler itself is `test_runtime_prompt.py`; what this file is
about is the two clocks. The know-how half (the persona, the fragments
the agent includes and the guidance of the entries it is granted) is
assembled once per
activation and cached, so a reply never rebuilds it and an agent switch
always does. The memory block keeps the clock it has always had, read
per round, and moves off the event loop rather than moving in time.
"""

import asyncio
import threading
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from samtal_server.config import Config
from samtal_server.providers import (
    LlmEvent,
    LlmProvider,
    TextDelta,
    ToolChoice,
    ToolDef,
    Turn,
)
from samtal_server.runtime.prompt import (
    Guidance,
    GuidanceBlock,
    ServerInstructions,
    ServerPrompt,
    guidance_heading,
    server_instructions_heading,
    server_prompt_heading,
)
from samtal_server.tools.mcp import McpServers
from samtal_server.tools.memory import MemoryStore
from tests.support.configs import BOTH_MAC, POET_MAC, base_config
from tests.unit.test_session_tools import ScriptedLlm, call, run_reply, session_for

GUIDANCE = "Ask before unlocking the door."

FRAGMENT = "The bins go out on Tuesday."


class CountingServers(McpServers):
    """A registry that answers a fixed set of guidance blocks and counts
    who asked. What it is for is the cache: every visible property of an
    assembled half looks the same whether it was assembled once or ten
    times, so the question has to be asked of the source."""

    def __init__(self, guidance: tuple[GuidanceBlock, ...] = ()) -> None:
        super().__init__({})
        self._guidance = guidance
        self.asked: list[str] = []

    def guidance_for_agent(self, agent: str) -> tuple[GuidanceBlock, ...]:
        self.asked.append(agent)
        return self._guidance


class RecordingLlm(LlmProvider):
    """A model that keeps the system prompt of every round it was asked
    for, which is the only place from outside a session that what the
    model received is visible."""

    def __init__(self, replies: Sequence[str] = ("Said.",)) -> None:
        self._replies = list(replies)
        self.systems: list[str] = []

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        self.systems.append(system)
        yield TextDelta(self._replies[min(len(self.systems) - 1, len(self._replies) - 1)])


def session_with(
    servers: McpServers | None = None,
    scripts: dict[str, Any] | None = None,
    memory: MemoryStore | None = None,
    mac: str = POET_MAC,
    config: Config | None = None,
):
    return session_for(
        config if config is not None else base_config(),
        mac,
        scripts,
        memory=memory,
        mcp_servers=servers,
    )


# The activation cache


async def test_the_know_how_half_is_assembled_once_per_activation() -> None:
    servers = CountingServers((Guidance("home", GUIDANCE),))
    session = session_with(servers, {"poet": ScriptedLlm(["One.", "Two."])})
    assert servers.asked == ["poet"]
    half = session.runtime._know_how

    await run_reply(session, "hello")
    await run_reply(session, "again")

    # Neither reply asked a second time, and the cached object is the
    # very one the activation built.
    assert servers.asked == ["poet"]
    assert session.runtime._know_how is half


async def test_an_agent_switch_re_assembles_the_half() -> None:
    servers = CountingServers((Guidance("home", GUIDANCE),))
    session = session_with(
        servers,
        {
            "poet": ScriptedLlm([[call("switch_agent", agent="tutor")]]),
            "tutor": ScriptedLlm(["Hi."]),
        },
        mac=BOTH_MAC,
    )
    half = session.runtime._know_how

    await run_reply(session, "get me the tutor")

    assert servers.asked == ["poet", "tutor"]
    assert session.runtime._know_how is not half
    assert session.runtime._know_how is not None
    assert session.runtime._know_how.text.startswith("TUTOR")


async def test_the_granted_guidance_reaches_the_model() -> None:
    llm = RecordingLlm()
    session = session_with(CountingServers((Guidance("home", GUIDANCE),)), {"poet": llm})

    await run_reply(session, "hello")

    (system,) = llm.systems
    assert system.startswith("POET")
    assert "home__" in system
    assert GUIDANCE in system


async def test_the_model_receives_exactly_the_blocks_that_are_reported() -> None:
    """The session's side of the surface's one promise, over the inputs
    that make a lazy assembler lie: a persona written with leading
    whitespace, and guidance whose author left blank lines at the end of
    it. What the provider was handed is the blocks joined, character for
    character, so what the surface reports is what the model read."""
    config = base_config(
        agents={
            "poet": {"prompt": "  POET  \n", "tts": "tenor"},
            "tutor": {"prompt": "TUTOR", "tts": "alto"},
        }
    )
    llm = RecordingLlm()
    session = session_with(
        CountingServers((Guidance("home", "  Ask first.\n\n"),)), {"poet": llm}, config=config
    )

    await run_reply(session, "hello")

    half = session.runtime._know_how
    assert half is not None
    (system,) = llm.systems
    assert system == "\n\n".join(block.text for block in half.blocks)
    assert system == half.text
    assert len(system) == half.characters
    # The interior of the guidance is what its author wrote; only the
    # end of the whole prompt was trimmed, and the block says so.
    assert "Ask first." in half.blocks[1].text


async def test_an_agent_granted_nothing_is_sent_its_persona_alone() -> None:
    """The byte-equality case, seen from the session: with no guidance
    and no memory, the prompt is the agent's prompt field and nothing
    else."""
    llm = RecordingLlm()
    session = session_with(CountingServers(), {"poet": llm})

    await run_reply(session, "hello")

    assert llm.systems == ["POET"]


# The fragments an agent includes


def config_with_fragment(includes: list[str] | None = None) -> Config:
    """The two agents, with a shared fragment the poet includes."""
    return base_config(
        prompt_fragments={"household": {"text": FRAGMENT}},
        agents={
            "poet": {
                "prompt": "POET",
                "tts": "tenor",
                "prompt_includes": ["household"] if includes is None else includes,
            },
            "tutor": {"prompt": "TUTOR", "tts": "alto"},
        },
    )


async def test_an_included_fragment_reaches_the_model() -> None:
    llm = RecordingLlm()
    session = session_with(
        CountingServers(), {"poet": llm}, config=config_with_fragment()
    )

    await run_reply(session, "hello")

    assert llm.systems == [f"POET\n\n{FRAGMENT}"]


async def test_an_agent_that_includes_nothing_is_sent_its_persona_alone() -> None:
    """The other half of the opt-out: the fragment exists and this agent
    does not carry it."""
    llm = RecordingLlm()
    session = session_with(
        CountingServers(), {"poet": llm}, config=config_with_fragment([])
    )

    await run_reply(session, "hello")

    assert llm.systems == ["POET"]


async def test_activation_logs_the_fragment_beside_the_persona(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The event grows a source per injected block, so what a prompt
    held is answerable from the retained logs without the session."""
    with caplog.at_level("INFO"):
        session = session_with(
            CountingServers((Guidance("home", GUIDANCE),)),
            {"poet": ScriptedLlm(["Said."])},
            config=config_with_fragment(),
        )
        await run_reply(session, "hello")

    (assembled,) = prompt_events(caplog)
    assert assembled.sources == {
        "persona": len("POET"),
        "fragment:household": len(FRAGMENT),
        "instructions:home": len(guidance_heading("home")) + len(f"\n{GUIDANCE}"),
    }
    assert assembled.characters == session.runtime._know_how.characters


# The memory clock, which did not move


async def test_a_fact_remembered_between_replies_is_in_the_next_one(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path)
    llm = RecordingLlm()
    session = session_with(CountingServers(), {"poet": llm}, memory=store)

    await run_reply(session, "hello")
    await store.remember("poet", "the user is vegetarian")
    await run_reply(session, "again")

    assert "the user is vegetarian" not in llm.systems[0]
    assert "the user is vegetarian" in llm.systems[1]
    # And the half was not rebuilt to notice it.
    assert session.runtime._know_how is not None
    assert "vegetarian" not in session.runtime._know_how.text


async def test_the_memory_read_happens_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`MemoryStore.read` is a synchronous file read reached from the
    loop every live conversation shares, so it runs in a worker thread.
    Proven by which thread it ran on rather than by reading the call
    site."""
    store = MemoryStore(tmp_path)
    await store.remember("poet", "the user is vegetarian")
    reads: list[int] = []
    real = MemoryStore.read

    def read(self: MemoryStore, agent: str) -> str:
        reads.append(threading.get_ident())
        return real(self, agent)

    monkeypatch.setattr(MemoryStore, "read", read)
    session = session_with(CountingServers(), {"poet": ScriptedLlm(["Said."])}, memory=store)

    await run_reply(session, "hello")

    assert reads, "the memory was never read"
    assert all(where != threading.get_ident() for where in reads)


async def test_a_session_without_memory_reads_nothing_at_all() -> None:
    """No store means no thread hop and no memory block: the cached
    half is the whole prompt."""
    llm = RecordingLlm()
    session = session_with(CountingServers(), {"poet": llm}, memory=None)

    await run_reply(session, "hello")

    assert session.runtime._know_how is not None
    assert llm.systems == [session.runtime._know_how.text]


# The event


def prompt_events(caplog: pytest.LogCaptureFixture) -> list:
    return [
        record for record in caplog.records if getattr(record, "event", None) == "prompt_assembled"
    ]


async def test_activation_logs_what_the_know_how_half_holds(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    store = MemoryStore(tmp_path)
    await store.remember("poet", "the user is vegetarian")
    with caplog.at_level("INFO"):
        session = session_with(
            CountingServers((Guidance("home", GUIDANCE),)),
            {"poet": ScriptedLlm(["Said."])},
            memory=store,
        )
        await run_reply(session, "hello")

    (assembled,) = prompt_events(caplog)
    assert assembled.agent == "poet"
    assert assembled.characters == session.runtime._know_how.characters
    assert set(assembled.sources) == {"persona", "instructions:home"}
    assert assembled.sources["persona"] == len("POET")
    # Memory is deliberately absent: this fires once per activation and
    # memory is read per round, and the event carries neither its size
    # nor a word of what it holds.
    assert "memory" not in assembled.sources
    assert "vegetarian" not in str(assembled.__dict__)


async def test_the_event_counts_the_server_shipped_blocks_without_quoting_them(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The two provenances a server's own guidance arrives under, sized
    in the event the way every other block is, and the bytes nowhere
    near it: this record lands in the JSON log a deployment collects."""
    shipped = "Call list_devices before anything else."
    published = "Answer in short sentences."
    with caplog.at_level("INFO"):
        session = session_with(
            CountingServers(
                (
                    ServerInstructions("home", shipped),
                    ServerPrompt("home", 1, "house_style", published),
                )
            ),
            {"poet": ScriptedLlm(["Said."])},
        )
        await run_reply(session, "hello")

    (assembled,) = prompt_events(caplog)
    assert set(assembled.sources) == {
        "persona",
        "server_instructions:home",
        "server_prompt:home:1",
    }
    assert assembled.sources["server_prompt:home:1"] == len(
        server_prompt_heading("home")
    ) + len(f"\n{published}")
    written = "".join(record.getMessage() for record in caplog.records) + str(
        assembled.__dict__
    )
    assert shipped not in written and published not in written
    assert "house_style" not in written


async def test_the_shipped_guidance_reaches_the_model(
    tmp_path: Path,
) -> None:
    """Under a heading that says the server is the one talking, which is
    the trust boundary made legible to the one reader that cannot see a
    provenance."""
    llm = RecordingLlm()
    session = session_with(
        CountingServers((ServerInstructions("home", "Call list_devices first."),)),
        {"poet": llm},
        memory=None,
    )

    await run_reply(session, "hello")

    (system,) = llm.systems
    assert server_instructions_heading("home") in system
    assert "Call list_devices first." in system


async def test_a_switch_logs_the_half_it_assembled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO"):
        session = session_with(
            CountingServers(),
            {
                "poet": ScriptedLlm([[call("switch_agent", agent="tutor")]]),
                "tutor": ScriptedLlm(["Hi."]),
            },
            mac=BOTH_MAC,
        )
        await run_reply(session, "get me the tutor")

    assert [record.agent for record in prompt_events(caplog)] == ["poet", "tutor"]


async def test_one_reply_logs_no_second_assembly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The event fires where the assembly happens, so a reply of several
    rounds is one activation and one record."""
    script = ScriptedLlm([[call("ghost_tool")], "Answered anyway."])
    session = session_with(CountingServers(), {"poet": script})
    with caplog.at_level("INFO"):
        await run_reply(session, "hello")

    assert len(script.seen) > 1
    assert prompt_events(caplog) == []


async def test_the_event_survives_a_session_built_off_the_loop() -> None:
    """A runtime is built inside the connect handler today, and the
    event fires from a synchronous method: nothing about it may need a
    running loop, or a change of call site would turn an activation into
    a traceback."""
    config = base_config()
    await asyncio.to_thread(session_with, CountingServers(), None, None, POET_MAC, config)
