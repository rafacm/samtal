"""The memory store and how it reaches the model."""

import asyncio
from pathlib import Path

import pytest

from tests.support.stores import STORED
from tests.support.stores import corrupt as _corrupt
from vinga_server.runtime import prompt
from vinga_server.tools import memory as memory_module
from vinga_server.tools.builtin import remember, remember_tool
from vinga_server.tools.memory import MemoryStore


async def test_a_remembered_fact_lands_in_the_agents_own_file(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    await store.remember("poet", "the user is vegetarian")
    await store.remember("poet", "the user's dog is called Bosse")

    assert store.read("poet") == "- the user is vegetarian\n- the user's dog is called Bosse"
    # A directory that did not exist is created on the first write.
    assert (tmp_path / "memory" / "poet.md").is_file()
    # And another agent has its own file, not this one.
    assert store.read("tutor") == ""


async def test_memory_is_keyed_by_agent_not_by_device(tmp_path: Path) -> None:
    # A persona is one entity across rooms: what a device told the poet
    # in one session is what the poet knows in every other.
    kitchen = MemoryStore(tmp_path / "memory")
    bedroom = MemoryStore(tmp_path / "memory")
    await kitchen.remember("poet", "the user is vegetarian")
    assert "vegetarian" in bedroom.read("poet")


async def test_an_agent_name_that_is_not_a_filename_still_gets_a_file(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path)
    await store.remember("../poet in the kitchen", "a fact")
    assert store.path_for("../poet in the kitchen") == tmp_path / "___poet_in_the_kitchen.md"
    assert (tmp_path / "___poet_in_the_kitchen.md").is_file()


async def test_a_fact_is_stored_as_one_line(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    await store.remember("poet", "  a fact\nspread over  lines  ")
    assert store.read("poet") == "- a fact spread over lines"


async def test_remembering_nothing_is_refused(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    with pytest.raises(ValueError):
        await store.remember("poet", "   ")


async def test_the_line_cap_drops_the_oldest_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory_module, "MAX_LINES", 3)
    store = MemoryStore(tmp_path)
    for index in range(5):
        await store.remember("poet", f"fact {index}")
    assert store.read("poet").splitlines() == ["- fact 2", "- fact 3", "- fact 4"]


async def test_the_byte_cap_drops_the_oldest_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory_module, "MAX_BYTES", 60)
    store = MemoryStore(tmp_path)
    for index in range(6):
        await store.remember("poet", f"a fact numbered {index}")
    lines = store.read("poet").splitlines()
    assert lines[-1] == "- a fact numbered 5"
    assert len(store.read("poet").encode("utf-8")) <= 60


async def test_concurrent_appends_keep_every_fact(tmp_path: Path) -> None:
    # Two sessions can be talking to the same agent, so appends are
    # serialized per agent and written atomically.
    store = MemoryStore(tmp_path)
    await asyncio.gather(*(store.remember("poet", f"fact {index}") for index in range(20)))
    assert len(store.read("poet").splitlines()) == 20


async def test_the_remember_tool_confirms_what_it_stored(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    answer = await remember(store, "poet", {"text": "the user is vegetarian"})
    assert answer == "Remembered: the user is vegetarian"
    assert "vegetarian" in store.read("poet")


async def test_the_remember_tool_refuses_a_call_without_text(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="text"):
        await remember(MemoryStore(tmp_path), "poet", {"fact": "wrong key"})


def test_the_tool_asks_for_one_short_fact() -> None:
    tool = remember_tool()
    assert tool.name == "remember"
    assert tool.input_schema["required"] == ["text"]


async def test_remembered_facts_reach_the_model_through_the_prompt(tmp_path: Path) -> None:
    """What the store holds, as the assembler injects it. The assembly
    itself is `test_runtime_prompt.py`; what this pins is that the two
    ends meet: one agent's file is one agent's facts."""
    store = MemoryStore(tmp_path)
    half = prompt.know_how("POET")
    assert prompt.with_memory(half, store.read("poet")).text == "POET"

    await store.remember("poet", "the user is vegetarian")
    assembled = prompt.with_memory(half, store.read("poet")).text
    assert assembled.startswith("POET")
    assert prompt.MEMORY_HEADING in assembled
    assert "- the user is vegetarian" in assembled
    # Another agent's prompt is untouched by it.
    assert prompt.with_memory(prompt.know_how("TUTOR"), store.read("tutor")).text == "TUTOR"


# A file that cannot be read
#
# A memory file is bytes on a volume, so a crash, a restore or a hand
# edit can leave one that will not decode. It is reached from the loop
# that builds a system prompt, so what must never happen is an exception
# travelling out of it: it would end the reply and put the decoder's own
# message, and a traceback, in the log.

def test_a_file_that_will_not_decode_reads_as_no_memory(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = MemoryStore(tmp_path)
    _corrupt(store, "poet")

    with caplog.at_level("WARNING"):
        assert store.read("poet") == ""

    (record,) = [r for r in caplog.records if getattr(r, "event", None) == "memory_unreadable"]
    assert record.agent == "poet"
    # The class of the failure and nothing else: the decoder's own
    # message quotes the byte it tripped on, and a traceback carries the
    # values that produced it.
    assert record.error == "UnicodeDecodeError"
    assert record.exc_info is None
    assert "0xff" not in record.getMessage()


def test_nothing_of_an_unreadable_file_reaches_any_log_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The sentinel over every record, not only the one this writes: a
    file nobody could decode may hold anything, including a credential
    somebody pasted into it."""
    store = MemoryStore(tmp_path)
    _corrupt(store, "poet")

    with caplog.at_level("DEBUG"):
        store.read("poet")

    for record in caplog.records:
        rendered = record.getMessage() + repr(record.args) + repr(record.__dict__)
        assert STORED not in rendered
        assert record.exc_info is None
        assert "Traceback" not in rendered


async def test_remembering_over_an_unreadable_file_leaves_a_readable_one(
    tmp_path: Path,
) -> None:
    """The file is appended to as an empty one, which is what the read
    says it is. Nothing a model could have been given is lost, and the
    `remember` tool keeps working rather than failing for as long as
    those bytes sit there."""
    store = MemoryStore(tmp_path)
    _corrupt(store, "poet")

    await store.remember("poet", "the user is vegetarian")

    assert store.read("poet") == "- the user is vegetarian"


async def test_a_reply_happens_over_an_unreadable_memory_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The whole point of containing it: the prompt is built without the
    memory block and the conversation carries on, rather than the read
    ending the reply from inside a worker thread."""
    from tests.support.providers import CountingServers, RecordingLlm
    from tests.support.sessions import run_reply, session_with

    store = MemoryStore(tmp_path)
    _corrupt(store, "poet")
    llm = RecordingLlm()
    session = session_with(CountingServers(), {"poet": llm}, memory=store)

    with caplog.at_level("DEBUG"):
        assert await run_reply(session, "hello") == ["Said."]

    assert llm.systems == ["POET"]
    for record in caplog.records:
        assert STORED not in record.getMessage() + repr(record.__dict__)
        assert record.exc_info is None
