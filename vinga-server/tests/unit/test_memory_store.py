"""The memory store: what it keeps, what it drops, and how it fails.

The schema's own suite next door is about the chain (the head, the
identity column, the index, the advisory key). This one is about the two
sentences a caller speaks, `read` and `remember`, and about the four
properties the storage move (#314) was made for: a fact survives the
process that stored it, the caps are applied inside the write
transaction rather than beside it, two writers through independent
connections cannot lose each other's fact, and a database that refuses
answers with a fixed sentence rather than with the connection string it
tried.

Nothing here reaches into the store for an engine. What it drives is
`read`, `remember`, and the stores `tests/support/stores.py` builds
through the same public constructor the opener uses; where a test has to
see the rows themselves, it opens its own connection, which is the point
of the independent-connection assertions below.
"""

import asyncio

import psycopg
import pytest

from tests.support.events import both_formats, only
from tests.support.stores import (
    STORED,
    a_planted_credential,
    holding_the_write_lock,
    memory,
    memory_that_cannot_read,
    memory_that_cannot_write,
    nowhere,
    the_lock_held,
)
from vinga_server.config.loader import ConfigError, DatabaseBusyError
from vinga_server.config.models import DatabaseConfig
from vinga_server.db import connection_url
from vinga_server.memory import MEMORY_CHAIN, open_memory
from vinga_server.memory import store as store_module
from vinga_server.runtime import prompt
from vinga_server.tools.builtin import remember, remember_tool


def _connection() -> psycopg.Connection:
    """A connection this suite owns, on nobody's engine.

    What proves a transaction: a read through the store's own reader
    would be a read through the pool the write went through, and the
    claim is about what another process would see.
    """
    url = connection_url(DatabaseConfig()).set(drivername="postgresql")
    return psycopg.connect(url.render_as_string(hide_password=False))


def _rows(agent: str) -> list[str]:
    holder = _connection()
    try:
        return [
            row[0]
            for row in holder.execute(
                "select fact from memory.facts where agent = %s order by id", (agent,)
            )
        ]
    finally:
        holder.close()


async def test_a_remembered_fact_is_read_back_for_that_agent() -> None:
    store = memory()
    await store.remember("poet", "the user is vegetarian")
    await store.remember("poet", "the user's dog is called Bosse")

    assert store.read("poet") == "- the user is vegetarian\n- the user's dog is called Bosse"
    # And another agent's memory is its own, not this one.
    assert store.read("tutor") == ""


async def test_memory_is_keyed_by_agent_not_by_device() -> None:
    # An agent is one entity across rooms: what a device told the poet
    # in one session is what the poet knows in every other.
    kitchen = memory()
    bedroom = open_memory(DatabaseConfig())
    try:
        await kitchen.remember("poet", "the user is vegetarian")
        assert "vegetarian" in bedroom.read("poet")
    finally:
        bedroom.close()


async def test_an_agent_name_that_is_not_a_filename_is_just_a_name() -> None:
    """A name that had to be sanitized into a filename is a column value
    now, and the store keeps it as the configuration spelled it."""
    store = memory()
    await store.remember("../poet in the kitchen", "a fact")

    assert store.read("../poet in the kitchen") == "- a fact"
    assert store.read("___poet_in_the_kitchen") == ""


async def test_a_fact_is_stored_as_one_line() -> None:
    store = memory()
    await store.remember("poet", "  a fact\nspread over  lines  ")
    assert store.read("poet") == "- a fact spread over lines"


async def test_remembering_nothing_is_refused() -> None:
    store = memory()
    with pytest.raises(ValueError):
        await store.remember("poet", "   ")


async def test_the_line_cap_drops_the_oldest_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "MAX_LINES", 3)
    store = memory()
    for index in range(5):
        await store.remember("poet", f"fact {index}")
    assert store.read("poet").splitlines() == ["- fact 2", "- fact 3", "- fact 4"]


async def test_the_byte_cap_drops_the_oldest_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "MAX_BYTES", 60)
    store = memory()
    for index in range(6):
        await store.remember("poet", f"a fact numbered {index}")
    lines = store.read("poet").splitlines()
    assert lines[-1] == "- a fact numbered 5"
    assert len(store.read("poet").encode("utf-8")) <= 60


async def test_concurrent_appends_keep_every_fact() -> None:
    # Two sessions can be talking to the same agent at once, and both
    # writes go through one advisory lock rather than one lock per
    # process.
    store = memory()
    await asyncio.gather(*(store.remember("poet", f"fact {index}") for index in range(20)))
    assert len(store.read("poet").splitlines()) == 20


async def test_a_fact_outlives_the_store_that_wrote_it() -> None:
    """The acceptance criterion the move was made for: a restart. The
    store that wrote the fact is closed, pools and all, and a store
    opened afterwards reads it."""
    first = open_memory(DatabaseConfig())
    try:
        await first.remember("poet", "the user is vegetarian")
    finally:
        first.close()

    second = open_memory(DatabaseConfig())
    try:
        assert second.read("poet") == "- the user is vegetarian"
    finally:
        second.close()


async def test_the_remember_tool_confirms_what_it_stored() -> None:
    store = memory()
    answer = await remember(store, "poet", {"text": "the user is vegetarian"})
    assert answer == "Remembered: the user is vegetarian"
    assert "vegetarian" in store.read("poet")


async def test_the_remember_tool_refuses_a_call_without_text() -> None:
    with pytest.raises(ValueError, match="text"):
        await remember(memory(), "poet", {"fact": "wrong key"})


def test_the_tool_asks_for_one_short_fact() -> None:
    tool = remember_tool()
    assert tool.name == "remember"
    assert tool.input_schema["required"] == ["text"]


async def test_remembered_facts_reach_the_model_through_the_prompt() -> None:
    """What the store holds, as the assembler injects it. The assembly
    itself is `test_runtime_prompt.py`; what this pins is that the two
    ends meet: one agent's rows are one agent's facts."""
    store = memory()
    half = prompt.know_how("POET")
    assert prompt.with_memory(half, store.read("poet")).text == "POET"

    await store.remember("poet", "the user is vegetarian")
    assembled = prompt.with_memory(half, store.read("poet")).text
    assert assembled.startswith("POET")
    assert prompt.MEMORY_HEADING in assembled
    assert "- the user is vegetarian" in assembled
    # Another agent's prompt is untouched by it.
    assert prompt.with_memory(prompt.know_how("TUTOR"), store.read("tutor")).text == "TUTOR"


# The caps, applied where a crash cannot land between them
#
# The file store read, appended, capped and renamed, so a process that
# died mid-write left whatever the filesystem had. The insert and the
# prune are one transaction now, which is a claim about what an
# independent connection can see: never an over-cap state, and never a
# fact that survived a prune that rolled back.


async def test_a_prune_that_fails_takes_the_insert_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The transactional claim, forced rather than waited for.

    The store is filled to its line cap, so the next `remember` has to
    delete a row; a test-only `BEFORE DELETE` trigger then raises,
    which is a failure after the insert and during the pruning. What an
    independent connection sees afterwards is the exact pre-call state:
    the insert rolled back with the prune rather than surviving it.
    """
    monkeypatch.setattr(store_module, "MAX_LINES", 3)
    store = memory()
    for index in range(3):
        await store.remember("poet", f"fact {index}")
    before = _rows("poet")

    holder = _connection()
    try:
        holder.execute(
            "create function memory.refuse_delete() returns trigger language plpgsql as "
            "$$ begin raise exception 'no deletes in this test'; end $$"
        )
        holder.execute(
            "create trigger refuse_delete before delete on memory.facts "
            "for each row execute function memory.refuse_delete()"
        )
        holder.commit()

        with pytest.raises(ConfigError) as refusal:
            await store.remember("poet", "fact 3")
    finally:
        holder.execute("drop trigger if exists refuse_delete on memory.facts")
        holder.execute("drop function if exists memory.refuse_delete()")
        holder.commit()
        holder.close()

    assert str(refusal.value) == store_module.UNWRITABLE
    assert _rows("poet") == before
    assert store.read("poet") == "\n".join(f"- {fact}" for fact in before)


async def test_two_writers_at_the_cap_cannot_lose_each_others_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two stores over separately opened engines, writing at once, with
    the store prefilled exactly at the pruning boundary.

    Arranged so the lock is what makes the answer right. At the line
    cap, each write drops exactly one row, so two writes that both read
    the pre-write state would agree on the same victim, leave the other
    victim behind, and end four rows deep or with the wrong survivor
    set. Under the chain's advisory lock the second write reads what the
    first committed, and the survivors are the last three facts said.
    """
    monkeypatch.setattr(store_module, "MAX_LINES", 3)
    monkeypatch.setattr(store_module, "MAX_BYTES", len(b"- fact 3\n- fact 4\n- fact 5"))
    filling = memory()
    for index in range(3):
        await filling.remember("poet", f"fact {index}")

    first = open_memory(DatabaseConfig())
    second = open_memory(DatabaseConfig())
    try:
        await asyncio.gather(
            first.remember("poet", "fact 3"), second.remember("poet", "fact 4")
        )
        rendered = first.read("poet")
    finally:
        second.close()
        first.close()

    surviving = _rows("poet")
    assert set(surviving) == {"fact 2", "fact 3", "fact 4"}
    assert len(surviving) == 3
    assert rendered.splitlines() == [f"- {fact}" for fact in surviving]
    assert len(rendered.encode("utf-8")) <= store_module.MAX_BYTES


# A database that refuses, split by the path that meets it
#
# The two paths fail differently on purpose. A read is contained: the
# agent remembers nothing this round and the reply happens. A write is
# refused: the model reads the refusal out, so the sentence is fixed and
# carries no value at all.


def test_a_read_that_cannot_reach_its_database_remembers_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = memory_that_cannot_read()

    with caplog.at_level("WARNING"):
        assert store.read("poet") == ""

    record = only(caplog, "memory_unreadable")
    assert record.agent == "poet"
    # The class of the failure and nothing else: a psycopg failure
    # quotes the DSN it tried, and a DSN carries a password.
    assert record.error == "OperationalError"
    assert record.exc_info is None


def test_a_read_answers_while_another_connection_holds_the_chain_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inverse of the write case below, and the property the second
    engine is bought with: reads never request the chain's advisory
    lock, so a write in flight cannot make one wait, let alone fail."""
    asyncio.run(memory().remember("poet", "the user is vegetarian"))

    with holding_the_write_lock(monkeypatch, MEMORY_CHAIN):
        # Opened under the shortened timeout, so a read that ever did
        # ask for the lock fails this in milliseconds rather than
        # hanging the lane on a wait that has no bound.
        store = open_memory(DatabaseConfig())
        try:
            with the_lock_held(MEMORY_CHAIN):
                assert store.read("poet") == "- the user is vegetarian"
        finally:
            store.close()


async def test_a_write_that_waits_out_the_lock_is_refused_retryably(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The held lock belongs to the write path, which is the only one
    that asks for it. The refusal is the retryable one, classified by
    exception class through the one classifier `db` owns."""
    with holding_the_write_lock(monkeypatch, MEMORY_CHAIN):
        store = open_memory(DatabaseConfig())
        try:
            with the_lock_held(MEMORY_CHAIN):
                with caplog.at_level("WARNING"):
                    with pytest.raises(DatabaseBusyError) as refusal:
                        await store.remember("poet", "the user is vegetarian")
        finally:
            store.close()

    assert str(refusal.value) == store_module.BUSY
    record = only(caplog, "memory_unwritable")
    assert record.agent == "poet"
    assert record.error == "OperationalError"
    assert record.exc_info is None


async def test_a_write_that_cannot_reach_its_database_is_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = memory_that_cannot_write()

    with caplog.at_level("WARNING"):
        with pytest.raises(ConfigError) as refusal:
            await store.remember("poet", "the user is vegetarian")

    assert str(refusal.value) == store_module.UNWRITABLE
    assert not isinstance(refusal.value, DatabaseBusyError)
    record = only(caplog, "memory_unwritable")
    assert record.error == "OperationalError"
    # Built inside the handler and raised after it, so the failure that
    # quoted the connection is on no chain a caller can walk.
    assert refusal.value.__cause__ is None
    assert refusal.value.__context__ is None


# The no-leak sentinel
#
# A credential-shaped password and a whole connection URL carrying it,
# driven through every path a database failure can reach: the read, the
# write, and the boot that opens the schema. The claim is "nowhere", so
# it is asked of the tool-result text, of every record in both shipped
# log formats, and of the exception chains.


async def test_nothing_of_a_connection_reaches_a_surface_a_model_or_an_operator_reads(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with a_planted_credential(monkeypatch):
        settings = DatabaseConfig(port=nowhere().port)
        with caplog.at_level("DEBUG"):
            # The boot, first: opening the chain is where a deployment
            # meets an unreachable database.
            with pytest.raises(ConfigError) as booting:
                open_memory(settings)

            store = memory_that_cannot_write()
            assert store.read("poet") == ""
            with pytest.raises(ConfigError) as writing:
                await store.remember("poet", "the user is vegetarian")

            spoken = f'the tool "remember" failed: {writing.value}'

    for surface in (spoken, str(booting.value), both_formats(caplog)):
        assert STORED not in surface
    for record in caplog.records:
        assert STORED not in repr(record.__dict__)
        assert record.exc_info is None
    for refusal in (booting.value, writing.value):
        walked: list[BaseException] = []
        cause: BaseException | None = refusal
        while cause is not None:
            walked.append(cause)
            cause = cause.__cause__ or cause.__context__
        assert all(STORED not in str(one) for one in walked)


async def test_a_reply_happens_over_a_memory_that_cannot_be_read(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The whole point of containing it: the prompt is built without the
    memory block and the conversation carries on, rather than the read
    ending the reply from inside a worker thread."""
    from tests.support.providers import CountingServers, RecordingLlm
    from tests.support.sessions import run_reply, session_with

    llm = RecordingLlm()
    session = session_with(
        CountingServers(), {"poet": llm}, memory=memory_that_cannot_read()
    )

    with caplog.at_level("DEBUG"):
        assert await run_reply(session, "hello") == ["Said."]

    assert llm.systems == ["POET"]
    for record in caplog.records:
        assert record.exc_info is None
