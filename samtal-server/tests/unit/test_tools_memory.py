"""The memory store and how it reaches the model."""

import asyncio
from pathlib import Path

import pytest

from samtal_server.tools import memory as memory_module
from samtal_server.tools.builtin import MEMORY_HEADING, remember, remember_tool, with_memory
from samtal_server.tools.memory import MemoryStore


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
    store = MemoryStore(tmp_path)
    assert with_memory("POET", store, "poet") == "POET"

    await store.remember("poet", "the user is vegetarian")
    prompt = with_memory("POET", store, "poet")
    assert prompt.startswith("POET")
    assert MEMORY_HEADING in prompt
    assert "- the user is vegetarian" in prompt
    # Another agent's prompt is untouched by it.
    assert with_memory("TUTOR", store, "tutor") == "TUTOR"


def test_without_a_store_the_prompt_is_the_prompt() -> None:
    assert with_memory("POET", None, "poet") == "POET"
