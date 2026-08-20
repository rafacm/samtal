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
from pathlib import Path

import pytest

from tests.support.configs import BOTH_MAC, POET_MAC, base_config
from tests.support.providers import CountingServers, RecordingLlm, ScriptedLlm
from tests.support.sessions import call, run_reply, session_with
from vinga_server.config import Config
from vinga_server.runtime.prompt import (
    Guidance,
    ServerInstructions,
    ServerPrompt,
    guidance_heading,
    server_instructions_heading,
    server_prompt_heading,
)
from vinga_server.tools.memory import MemoryStore

GUIDANCE = "Ask before unlocking the door."

FRAGMENT = "The bins go out on Tuesday."


# The activation cache


async def test_the_know_how_half_is_assembled_once_per_activation() -> None:
    servers = CountingServers((Guidance("home", GUIDANCE),))
    session = session_with(servers, {"poet": ScriptedLlm(["One.", "Two."])})
    assert servers.asked == ["poet"]

    await run_reply(session, "hello")
    await run_reply(session, "again")

    # Neither reply asked a second time. Assembling the half is what
    # asks, so one question is one assembly.
    assert servers.asked == ["poet"]


async def test_an_agent_switch_re_assembles_the_half() -> None:
    servers = CountingServers((Guidance("home", GUIDANCE),))
    tutor = RecordingLlm(["Hi."])
    session = session_with(
        servers,
        {
            "poet": ScriptedLlm([[call("switch_agent", agent="tutor")]]),
            "tutor": tutor,
        },
        mac=BOTH_MAC,
    )

    await run_reply(session, "get me the tutor")

    # Asked again, and what the new agent was sent is the new agent's
    # half rather than the one the activation cached for the poet.
    assert servers.asked == ["poet", "tutor"]
    (system,) = tutor.systems
    assert system.startswith("TUTOR")


async def test_the_granted_guidance_reaches_the_model() -> None:
    llm = RecordingLlm()
    session = session_with(CountingServers((Guidance("home", GUIDANCE),)), {"poet": llm})

    await run_reply(session, "hello")

    (system,) = llm.systems
    assert system.startswith("POET")
    assert "home__" in system
    assert GUIDANCE in system


async def test_the_model_receives_exactly_the_blocks_that_are_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
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
    with caplog.at_level("INFO"):
        session = session_with(
            CountingServers((Guidance("home", "  Ask first.\n\n"),)), {"poet": llm}, config=config
        )
        await run_reply(session, "hello")

    (system,) = llm.systems
    (assembled,) = prompt_events(caplog)
    # The surface reports a size per block and a size for the whole,
    # and with no memory in the way the whole is what the model read.
    assert assembled.characters == len(system)
    assert sum(assembled.sources.values()) + len("\n\n") == len(system)
    # The interior of the guidance is what its author wrote; only the
    # end of the whole prompt was trimmed.
    assert system.startswith("POET")
    assert "Ask first." in system
    assert not system.endswith("\n")


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
    llm = RecordingLlm()
    with caplog.at_level("INFO"):
        session = session_with(
            CountingServers((Guidance("home", GUIDANCE),)),
            {"poet": llm},
            config=config_with_fragment(),
        )
        await run_reply(session, "hello")

    (system,) = llm.systems

    (assembled,) = prompt_events(caplog)
    assert assembled.sources == {
        "persona": len("POET"),
        "fragment:household": len(FRAGMENT),
        "instructions:home": len(guidance_heading("home")) + len(f"\n{GUIDANCE}"),
    }
    assert assembled.characters == len(system)


# The memory clock, which did not move


async def test_a_fact_remembered_between_replies_is_in_the_next_one(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path)
    llm = RecordingLlm()
    servers = CountingServers()
    session = session_with(servers, {"poet": llm}, memory=store)

    await run_reply(session, "hello")
    await store.remember("poet", "the user is vegetarian")
    await run_reply(session, "again")

    assert "the user is vegetarian" not in llm.systems[0]
    assert "the user is vegetarian" in llm.systems[1]
    # And the half was not rebuilt to notice it: rebuilding is what asks
    # the registry, and it was asked once.
    assert servers.asked == ["poet"]


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

    # The one reach-in in this file, and the design guide names it: what
    # public observation cannot establish is that the text the model was
    # sent IS the cached half rather than a rebuild that happens to
    # match it. Nothing reports the cached text: the event carries sizes
    # and no words, deliberately, and the operator-facing route
    # assembles a fresh preview that reads memory as a new session
    # would. Identity is the claim, so identity is what is asserted.
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
    llm = RecordingLlm()
    with caplog.at_level("INFO"):
        session = session_with(
            CountingServers((Guidance("home", GUIDANCE),)),
            {"poet": llm},
            memory=store,
        )
        await run_reply(session, "hello")

    (system,) = llm.systems
    (assembled,) = prompt_events(caplog)
    assert assembled.agent == "poet"
    # The event counts the know-how half and stops there: the memory
    # block is in the prompt and outside the count, which is what the
    # slice below says.
    assert "vegetarian" in system
    assert "vegetarian" not in system[: assembled.characters]
    assert system[: assembled.characters].startswith("POET")
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
