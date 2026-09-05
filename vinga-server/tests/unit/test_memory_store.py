"""The memory store: what it keeps, what it drops, and how it fails.

The schema's own suite next door is about the chain (the head, the
identity column, the indexes, the checks, the advisory key). This one is
about the sentences a caller speaks, and about the properties they are
held to: a fact survives the process that stored it, the caps are
applied inside the write transaction rather than beside it, two writers
through independent connections cannot lose each other's fact, an id
reaches only what its owner may reach, a held fact is outside every cap
and still there to be brought back, and a database that refuses answers
with a fixed sentence rather than with the connection string it tried.

Every fixed sentence is compared by equality against the module's own
constant rather than searched for as a substring, which is what keeps a
refusal a contract instead of a phrase that happens to appear.

Nothing here reaches into the store for an engine. What it drives is the
store's own calls and the stores `tests/support/stores.py` builds
through the same public constructor the opener uses; where a test has to
see the rows themselves, it opens its own connection, which is the point
of the independent-connection assertions below.
"""

import asyncio
import contextlib
from collections.abc import Iterator

import psycopg
import pytest

from tests.support.events import both_formats, events, only
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
from vinga_server import db as db_module
from vinga_server.config.loader import (
    AgentRenameConflictError,
    ConfigError,
    DatabaseBusyError,
)
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations.store import CONVERSATIONS_CHAIN
from vinga_server.db import advisory_key, connection_url, write_engine
from vinga_server.memory import store as store_module
from vinga_server.memory.store import (
    MEMORY_CHAIN,
    MemoryScope,
    MemoryStore,
    open_memory,
)
from vinga_server.runtime import prompt
from vinga_server.tools import builtin
from vinga_server.tools.builtin import (
    MemoryContext,
    forget,
    recall,
    remember,
    remember_tool,
    restore_memory,
    update_memory,
)


def _connection() -> psycopg.Connection:
    """A connection this suite owns, on nobody's engine.

    What proves a transaction: a read through the store's own reader
    would be a read through the pool the write went through, and the
    claim is about what another process would see.
    """
    url = connection_url(DatabaseConfig()).set(drivername="postgresql")
    return psycopg.connect(url.render_as_string(hide_password=False))


def _rows(owner: str, scope: str = "agent") -> list[str]:
    """One owner's active facts within one scope, oldest first."""
    holder = _connection()
    try:
        return [
            row[0]
            for row in holder.execute(
                "select fact from memory.facts where scope = %s and owner = %s "
                "and forgotten_at is null order by id",
                (scope, owner),
            )
        ]
    finally:
        holder.close()


def _numbered(owner: str, scope: str = "agent") -> list[tuple[int, str]]:
    """One owner's active facts with the numbers they are addressed by,
    which is what a confirmation quotes back and what a lookup shows."""
    holder = _connection()
    try:
        return [
            (row[0], row[1])
            for row in holder.execute(
                "select id, fact from memory.facts where scope = %s and owner = %s "
                "and forgotten_at is null order by id",
                (scope, owner),
            )
        ]
    finally:
        holder.close()


def _held(owner: str, scope: str = "agent") -> list[tuple[str, str]]:
    """One owner's held facts, each with the conversation that forgot
    it. What a restore reaches, and what a prune must never take."""
    holder = _connection()
    try:
        return [
            (row[0], row[1])
            for row in holder.execute(
                "select fact, forgotten_in from memory.facts where scope = %s "
                "and owner = %s and forgotten_at is not null order by id",
                (scope, owner),
            )
        ]
    finally:
        holder.close()


def injected(store: MemoryStore, agent: str = "poet") -> str:
    """One agent's block as a reply is sent it.

    The prompt read with no device and no conversation, which is the
    whole of what an agent-keyed caller asks for and exactly what the
    preview speaks. It is the newest of the scope rather than the whole
    of it, so a test about what is stored reads the rows and a test about
    what the model is told reads this.
    """
    return store.read_for_prompt(agent, None, None).agent


def _state(conversation: str) -> list[tuple[str, str]]:
    """One conversation's ledger, by key, as the database holds it."""
    holder = _connection()
    try:
        return [
            (row[0], row[1])
            for row in holder.execute(
                "select key, value from memory.state where conversation = %s "
                "order by key",
                (conversation,),
            )
        ]
    finally:
        holder.close()


# The gate the two-writer case is arranged around
#
# An advisory key of this suite's own, in nobody's chain: what a test
# connection holds so that a transaction under test stops exactly where
# the test needs it to. `advisory_key` answers this application's own
# space, and the chains take 1, 2 and 3, so a number far outside that
# run cannot be mistaken for one of theirs.
GATE_KEY = advisory_key(9001)

# The fact the first writer stores, and the one its prune drops.
#
# The gate hangs on the prune rather than on the insert, and that is the
# whole of the arrangement: a writer parked after its insert but before
# its read would still read the other's committed rows when it resumed,
# because every statement of a READ COMMITTED transaction takes its own
# snapshot, and the arithmetic would come out right with no lock at all.
# Parked after the delete, it has already decided, and a second writer
# that decides beside it is deciding on the same rows.
GATED_FACT = "fact 3"
GATED_VICTIM = "fact 0"


@contextlib.contextmanager
def _the_prune_gate_held() -> Iterator[None]:
    """A trigger that parks whichever transaction prunes `GATED_VICTIM`,
    and the connection whose lock parks it.

    A test-only `AFTER DELETE` trigger rather than a sleep, because what
    this buys is an interleaving rather than a delay: the first writer
    is inside its transaction, past its insert, its read and its prune,
    and has not committed, for as long as this is entered.
    """
    gate = _connection()
    installer = _connection()
    try:
        installer.execute(
            "create function memory.hold_prune() returns trigger language plpgsql as "
            f"$$ begin if old.fact = '{GATED_VICTIM}' then "
            f"perform pg_advisory_xact_lock({GATE_KEY}); end if; return null; end $$"
        )
        installer.execute(
            "create trigger hold_prune after delete on memory.facts "
            "for each row execute function memory.hold_prune()"
        )
        installer.commit()
        gate.execute("select pg_advisory_xact_lock(%s)", (GATE_KEY,))
        yield
    finally:
        # The gate first: the trigger cannot be dropped while a
        # transaction is parked inside it.
        gate.rollback()
        gate.close()
        installer.execute("drop trigger if exists hold_prune on memory.facts")
        installer.execute("drop function if exists memory.hold_prune()")
        installer.commit()
        installer.close()


async def _waiting_on_a_lock(how_many: int, within_s: float = 5.0) -> bool:
    """Whether this many backends of this database are parked on a lock,
    asked of the database rather than assumed from a sleep.

    Any lock, not the advisory one alone, because which lock the second
    writer waits on is exactly what the arrangement is testing: the
    chain's advisory gate where the listener exists, and the row lock on
    the fact both writers chose to prune where it does not. Waiting for
    "parked" rather than for "parked on the right thing" is what keeps
    both runs prompt and lets the final state be the only verdict.

    Answers False on the deadline instead of raising, so a caller can
    treat not-parked as information.
    """
    watcher = _connection()
    deadline = asyncio.get_running_loop().time() + within_s
    try:
        while asyncio.get_running_loop().time() < deadline:
            (parked,) = watcher.execute(
                "select count(*) from pg_stat_activity where datname = "
                "current_database() and wait_event_type = 'Lock'"
            ).fetchone()
            watcher.rollback()
            if parked >= how_many:
                return True
            await asyncio.sleep(0.02)
        return False
    finally:
        watcher.close()


async def _closing(store: MemoryStore, within_s: float = 5.0) -> bool:
    """Whether a store with a fact in it has entered its close, asked
    through its own interface.

    A closing store refuses a read admission and answers empty, so a
    read that comes back empty from a store known to hold a fact is the
    transition, observed rather than slept for. It is the subject of the
    admission case below, borrowed here as the only signal this store
    publishes about where its close has got to.
    """
    deadline = asyncio.get_running_loop().time() + within_s
    while asyncio.get_running_loop().time() < deadline:
        if injected(store) == "":
            return True
        await asyncio.sleep(0.02)
    return False


async def test_a_remembered_fact_is_read_back_for_that_agent() -> None:
    store = memory()
    await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
    await store.add(MemoryScope.AGENT, "poet", "the user's dog is called Bosse", agent="poet")

    assert injected(store) == "- the user is vegetarian\n- the user's dog is called Bosse"
    # And another agent's memory is its own, not this one.
    assert injected(store, "tutor") == ""


async def test_memory_is_keyed_by_agent_not_by_device() -> None:
    # An agent is one entity across rooms: what a device told the poet
    # in one session is what the poet knows in every other.
    kitchen = memory()
    bedroom = open_memory(DatabaseConfig())
    try:
        await kitchen.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
        assert "vegetarian" in injected(bedroom)
    finally:
        bedroom.close()


async def test_an_agent_name_that_is_not_a_filename_is_just_a_name() -> None:
    """A name that had to be sanitized into a filename is a column value
    now, and the store keeps it as the configuration spelled it."""
    store = memory()
    named = "../poet in the kitchen"
    await store.add(MemoryScope.AGENT, named, "a fact", agent=named)

    assert injected(store, "../poet in the kitchen") == "- a fact"
    assert injected(store, "___poet_in_the_kitchen") == ""


async def test_a_fact_is_stored_as_one_line() -> None:
    store = memory()
    await store.add(MemoryScope.AGENT, "poet", "  a fact\nspread over  lines  ", agent="poet")
    assert injected(store) == "- a fact spread over lines"


async def test_remembering_nothing_is_refused() -> None:
    store = memory()
    with pytest.raises(ValueError):
        await store.add(MemoryScope.AGENT, "poet", "   ", agent="poet")


async def test_the_line_cap_drops_the_oldest_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "MAX_LINES", 3)
    store = memory()
    for index in range(5):
        await store.add(MemoryScope.AGENT, "poet", f"fact {index}", agent="poet")
    assert injected(store).splitlines() == ["- fact 2", "- fact 3", "- fact 4"]


async def test_the_byte_cap_drops_the_oldest_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "MAX_BYTES", 60)
    store = memory()
    for index in range(6):
        await store.add(MemoryScope.AGENT, "poet", f"a fact numbered {index}", agent="poet")
    lines = injected(store).splitlines()
    assert lines[-1] == "- a fact numbered 5"
    assert len(injected(store).encode("utf-8")) <= 60


# Scopes, and the caps each of them is held to
#
# One store, two kinds of owner: an agent, which is what memory has
# always been keyed by, and a device, whose notes every agent bound to it
# shares. The pair is addressed as `(scope, owner)` everywhere, and what
# the suite below pins is that the two are separate in every direction
# that matters: what one holds, what the other reads, and what a prune in
# one does to the other.


async def test_an_added_fact_answers_the_id_it_is_addressed_by() -> None:
    """The id is what update, forget and restore name a fact by, so it
    is what `add` has to answer with."""
    store = memory()
    first = await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
    second = await store.add(MemoryScope.AGENT, "poet", "the dog is Bosse", agent="poet")

    assert isinstance(first, int)
    assert second > first
    assert injected(store).splitlines() == ["- the user is vegetarian", "- the dog is Bosse"]


async def test_a_device_fact_is_not_an_agent_fact() -> None:
    """The scope separation, in both directions: what the device knows
    is not in the agent's block, and what the agent knows is not in the
    device's rows, even where the two names are the same string."""
    store = memory()
    await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
    await store.add(MemoryScope.DEVICE, "poet", "the kettle is loud", agent="poet")

    assert _rows("poet") == ["the user is vegetarian"]
    assert _rows("poet", scope="device") == ["the kettle is loud"]
    assert injected(store) == "- the user is vegetarian"


# The two things a JSON string may be and a database cannot hold, both
# written rather than pasted: a source file carrying either is one
# editors and terminals disagree about.
NUL = "a" + chr(0) + "b"

SURROGATE = "a" + chr(0xD800) + "b"


async def test_text_a_database_cannot_hold_is_refused_before_a_connection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A NUL character cannot live in a `text` column and a lone
    surrogate is not Unicode a driver can encode, so neither can even be
    bound as a parameter.

    Refused before a connection for the reason a conversation scope is:
    reaching the driver would arrive here as a database failure, and a
    caller's mistake would be reported to an operator as a healthy
    database refusing a write. Every door that takes text asks, which is
    what makes this a rule of the store rather than of one sentence.
    """
    store = memory()
    kept = await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
    await store.set_state(THREAD, "scene", "a forest", agent="poet")

    with caplog.at_level("WARNING"):
        for unstorable in (NUL, SURROGATE):
            for call in (
                lambda text=unstorable: store.add(
                    MemoryScope.AGENT, "poet", text, agent="poet"
                ),
                lambda text=unstorable: store.update(
                    MemoryScope.AGENT, "poet", kept, text, agent="poet"
                ),
                lambda text=unstorable: store.set_state(
                    THREAD, text, "a forest", agent="poet"
                ),
                lambda text=unstorable: store.set_state(
                    THREAD, "scene", text, agent="poet"
                ),
                lambda text=unstorable: store.clear_state(
                    THREAD, text, agent="poet"
                ),
                lambda text=unstorable: store.recall("poet", "aa:bb", text),
            ):
                with pytest.raises(ValueError) as refusal:
                    await call()
                assert str(refusal.value) == store_module.NOT_STORABLE

    # Nothing moved, and nobody was told the database refused anything.
    assert _rows("poet") == ["the user is vegetarian"]
    assert _state(THREAD) == [("scene", "a forest")]
    assert events(caplog, "memory_unwritable") == []
    assert events(caplog, "memory_unreadable") == []
    # And nothing of what was refused reached a log, in either format.
    assert NUL not in both_formats(caplog)
    assert SURROGATE not in both_formats(caplog)


async def test_the_operators_correction_refuses_it_at_its_own_door() -> None:
    """The rule is the store's rather than one door's.

    The API asks it of a body before it opens a transaction, so this
    would never be reached through that door; it is here because
    `correct` is a sentence of this module and every caller of it is
    entitled to the same refusal, and because a rule only one caller
    enforces is a rule the next caller will not.
    """
    from vinga_server.db import write_engine

    store = memory()
    kept = await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")

    engine = write_engine(DatabaseConfig(), MEMORY_CHAIN)
    try:
        with engine.begin() as connection:
            with pytest.raises(ConfigError) as refusal:
                store_module.correct(connection, MemoryScope.AGENT, "poet", kept, NUL)
    finally:
        engine.dispose()

    assert str(refusal.value) == store_module.NOT_STORABLE
    assert _rows("poet") == ["the user is vegetarian"]


async def test_a_fact_cannot_belong_to_a_conversation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The vocabulary is three and a fact carries two of them. The
    refusal is decided before a connection, so a caller's mistake stays
    a caller's mistake: reaching the table's own check would arrive here
    as a database failure, and a healthy database would be reported as
    broken to whoever reads the log."""
    store = memory()
    kept = await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")

    with caplog.at_level("WARNING"):
        for call in (
            lambda: store.add(
                MemoryScope.CONVERSATION, THREAD, "white to move", agent="poet"
            ),
            lambda: store.update(
                MemoryScope.CONVERSATION, THREAD, kept, "white to move", agent="poet"
            ),
            lambda: store.forget(
                MemoryScope.CONVERSATION, THREAD, kept, THREAD, agent="poet"
            ),
            lambda: store.restore(
                ((MemoryScope.CONVERSATION, THREAD),), THREAD, agent="poet"
            ),
        ):
            with pytest.raises(ValueError) as refusal:
                await call()
            assert str(refusal.value) == store_module.NOT_A_FACT_SCOPE

    # Nothing was written, nothing was taken, and no operator was told
    # that a database refused anything.
    assert _rows("poet") == ["the user is vegetarian"]
    assert _state(THREAD) == []
    assert events(caplog, "memory_unwritable") == []


async def test_an_add_too_long_for_its_scope_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pruning cannot answer this one: dropping every other fact would
    still leave it over the cap. The refusal is the module's own fixed
    sentence, compared by equality, and nothing is stored."""
    monkeypatch.setattr(store_module, "MAX_BYTES", 40)
    store = memory()
    await store.add(MemoryScope.AGENT, "poet", "a short fact", agent="poet")

    with pytest.raises(ValueError) as refusal:
        await store.add(MemoryScope.AGENT, "poet", "x" * 41, agent="poet")

    assert str(refusal.value) == store_module.TOO_LONG
    assert _rows("poet") == ["a short fact"]


async def test_each_scope_is_pruned_against_its_own_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact survivor sets on both sides. An agent at its cap drops
    agent facts and touches no device row, and a device at its own,
    smaller cap does the mirror: one pair of numbers over both would
    either starve the first or let the second grow into the prompt."""
    monkeypatch.setattr(store_module, "MAX_LINES", 4)
    monkeypatch.setattr(store_module, "DEVICE_LINES", 2)
    store = memory()
    for index in range(5):
        await store.add(
            MemoryScope.AGENT, "poet", f"agent fact {index}", agent="poet"
        )
    for index in range(3):
        await store.add(
            MemoryScope.DEVICE, "aa:bb", f"device fact {index}", agent="poet"
        )

    assert _rows("poet") == [f"agent fact {index}" for index in range(1, 5)]
    assert _rows("aa:bb", scope="device") == ["device fact 1", "device fact 2"]

    # And a device write past the device cap leaves the agent's rows
    # exactly where they were.
    await store.add(MemoryScope.DEVICE, "aa:bb", "device fact 3", agent="poet")
    assert _rows("poet") == [f"agent fact {index}" for index in range(1, 5)]
    assert _rows("aa:bb", scope="device") == ["device fact 2", "device fact 3"]


# Correcting, forgetting and bringing back
#
# The three operations that address a fact by the id `add` answered
# with. What they share is the address: the id AND the pair that owns
# it, which is what keeps one agent out of another's memory and one
# conversation out of another's undo.

THREAD = "9f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5"
OTHER_THREAD = "0123456789abcdef0123456789abcdef"

# Where a tool call is happening, as the session answers it: this board
# and this thread. What the executors read out of it is the owner a
# device fact is kept under and the conversation an undo belongs to,
# neither of which a model may name for itself.
HERE = MemoryContext(device="aa:bb", conversation=THREAD)

# The one memory most of the cases below reach into, as an operation
# addressed by a set of them takes it: the poet's own.
MINE = ((MemoryScope.AGENT, "poet"),)


async def test_a_correction_keeps_the_id_and_replaces_the_words() -> None:
    store = memory()
    fact_id = await store.add(MemoryScope.AGENT, "poet", "the dog is called Bose", agent="poet")

    await store.update(MemoryScope.AGENT, "poet", fact_id, "the dog is called Bosse", agent="poet")

    assert _rows("poet") == ["the dog is called Bosse"]
    # The id is stable, which is what makes it an address: forgetting by
    # the number the add answered with still reaches the corrected fact.
    assert await store.forget(
        MemoryScope.AGENT, "poet", fact_id, THREAD, agent="poet"
    ) == "the dog is called Bosse"


async def test_a_forgotten_fact_is_out_of_the_reading_and_can_come_back() -> None:
    """Soft, spoken and reversible: the removal answers with what it
    removed, the fact leaves every read, and bringing it back brings back
    the bytes rather than something rephrased."""
    store = memory()
    kept = await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
    gone = await store.add(
        MemoryScope.AGENT, "poet", "the user's dog is called Bosse", agent="poet"
    )

    spoken = await store.forget(MemoryScope.AGENT, "poet", gone, THREAD, agent="poet")

    assert spoken == "the user's dog is called Bosse"
    assert injected(store) == "- the user is vegetarian"
    assert _held("poet") == [("the user's dog is called Bosse", THREAD)]

    brought_back = await store.restore(MINE, THREAD, agent="poet")

    assert brought_back == "the user's dog is called Bosse"
    assert _held("poet") == []
    # In the place it always had, because the held area kept the row and
    # not a copy of its words.
    assert injected(store).splitlines() == [
        "- the user is vegetarian",
        "- the user's dog is called Bosse",
    ]
    assert _rows("poet")[kept - kept] == "the user is vegetarian"


async def test_the_last_thing_forgotten_is_what_comes_back_first() -> None:
    """No id means the last thing forgotten in this conversation, which
    is the shape a person actually asks for."""
    store = memory()
    first = await store.add(MemoryScope.AGENT, "poet", "fact one", agent="poet")
    second = await store.add(MemoryScope.AGENT, "poet", "fact two", agent="poet")
    await store.forget(MemoryScope.AGENT, "poet", first, THREAD, agent="poet")
    await store.forget(MemoryScope.AGENT, "poet", second, THREAD, agent="poet")

    assert await store.restore(MINE, THREAD, agent="poet") == "fact two"
    # And the id door reaches the other one, which is still held.
    assert (
        await store.restore(MINE, THREAD, first, agent="poet")
        == "fact one"
    )
    assert _held("poet") == []


async def test_a_fact_forgotten_permanently_does_not_come_back() -> None:
    store = memory()
    gone = await store.add(MemoryScope.AGENT, "poet", "the card number is 4111", agent="poet")

    spoken = await store.forget(
        MemoryScope.AGENT, "poet", gone, THREAD, agent="poet", permanently=True
    )

    assert spoken == "the card number is 4111"
    assert _rows("poet") == []
    # Nothing held, so there is nothing for a restore to find and
    # nothing left for an operator to read either.
    assert _held("poet") == []
    with pytest.raises(ValueError) as refusal:
        await store.restore(MINE, THREAD, agent="poet")
    assert str(refusal.value) == store_module.NO_FACT_TO_RESTORE


# What an id may not reach
#
# Ids are global and guessable, so every id-addressed operation is
# bounded by ownership in its WHERE clause. A missing fact and an
# inaccessible one are answered identically on purpose: a refusal that
# told them apart would confirm that somebody else's ids exist.


async def test_another_agents_fact_is_not_reachable_by_its_number() -> None:
    store = memory()
    theirs = await store.add(
        MemoryScope.AGENT, "tutor", "the user is learning Spanish", agent="tutor"
    )

    with pytest.raises(ValueError) as correcting:
        await store.update(MemoryScope.AGENT, "poet", theirs, "something else", agent="poet")
    with pytest.raises(ValueError) as forgetting:
        await store.forget(MemoryScope.AGENT, "poet", theirs, THREAD, agent="poet")

    assert str(correcting.value) == store_module.NO_FACT_TO_UPDATE
    assert str(forgetting.value) == store_module.NO_FACT_TO_FORGET
    # And the same sentence a fact that never existed gets, so the
    # refusal confirms nothing.
    with pytest.raises(ValueError) as missing:
        await store.forget(MemoryScope.AGENT, "poet", theirs + 10_000, THREAD, agent="poet")
    assert str(missing.value) == store_module.NO_FACT_TO_FORGET
    assert _rows("tutor") == ["the user is learning Spanish"]


async def test_another_devices_note_is_not_reachable_by_its_number() -> None:
    store = memory()
    theirs = await store.add(MemoryScope.DEVICE, "aa:bb", "the kettle is loud", agent="poet")

    with pytest.raises(ValueError) as elsewhere:
        await store.update(MemoryScope.DEVICE, "cc:dd", theirs, "quiet", agent="poet")
    # And the scope is half of the address too: the same number under
    # the agent scope is not this note.
    with pytest.raises(ValueError) as wrong_scope:
        await store.update(MemoryScope.AGENT, "aa:bb", theirs, "quiet", agent="poet")

    assert str(elsewhere.value) == store_module.NO_FACT_TO_UPDATE
    assert str(wrong_scope.value) == store_module.NO_FACT_TO_UPDATE
    assert _rows("aa:bb", scope="device") == ["the kettle is loud"]


async def test_a_fact_forgotten_in_another_conversation_stays_forgotten() -> None:
    """The conversation is part of a restore's address, which is what
    makes the id door mean what the no-id door means: this thread brings
    back what this thread forgot."""
    store = memory()
    gone = await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
    await store.forget(MemoryScope.AGENT, "poet", gone, OTHER_THREAD, agent="poet")

    with pytest.raises(ValueError) as by_id:
        await store.restore(MINE, THREAD, gone, agent="poet")
    with pytest.raises(ValueError) as by_last:
        await store.restore(MINE, THREAD, agent="poet")

    assert str(by_id.value) == store_module.NO_FACT_TO_RESTORE
    assert str(by_last.value) == store_module.NO_FACT_TO_RESTORE
    assert _held("poet") == [("the user is vegetarian", OTHER_THREAD)]
    assert _rows("poet") == []


# Looking something up
#
# The lookup is what makes a memory bigger than a prompt usable at all,
# and it is also how the model learns the number of a fact it wants to
# correct: the injected block shows no ids and this does.


async def test_a_lookup_answers_both_scopes_newest_first_with_their_ids() -> None:
    store = memory()
    first = await store.add(MemoryScope.AGENT, "poet", "the user likes cheese", agent="poet")
    second = await store.add(MemoryScope.DEVICE, "aa:bb", "the CHEESE drawer sticks", agent="poet")
    await store.add(MemoryScope.AGENT, "poet", "the user is called Rafael", agent="poet")

    found = store.recall("poet", "aa:bb", "cheese")

    # Case-insensitive, newest first, and each line carries the number
    # the fact is addressed by.
    assert found.splitlines() == [
        f"- [{second}] the CHEESE drawer sticks",
        f"- [{first}] the user likes cheese",
    ]


async def test_a_lookup_reaches_no_other_owner_and_no_held_fact() -> None:
    store = memory()
    mine = await store.add(MemoryScope.AGENT, "poet", "the user likes cheese", agent="poet")
    await store.add(MemoryScope.AGENT, "tutor", "the user likes cheese too", agent="tutor")
    await store.add(MemoryScope.DEVICE, "cc:dd", "the cheese drawer sticks", agent="poet")
    forgotten = await store.add(MemoryScope.AGENT, "poet", "the user hates cheese", agent="poet")
    await store.forget(MemoryScope.AGENT, "poet", forgotten, THREAD, agent="poet")

    assert store.recall("poet", "aa:bb", "cheese").splitlines() == [
        f"- [{mine}] the user likes cheese"
    ]


async def test_a_lookup_that_matches_nothing_answers_nothing() -> None:
    store = memory()
    await store.add(MemoryScope.AGENT, "poet", "the user likes cheese", agent="poet")

    assert store.recall("poet", "aa:bb", "sourdough") == ""
    with pytest.raises(ValueError) as refusal:
        store.recall("poet", "aa:bb", "   ")
    assert str(refusal.value) == store_module.NOTHING_TO_LOOK_FOR


async def test_a_wildcard_in_the_query_is_looked_for_rather_than_obeyed() -> None:
    """The query is the model's own text. Read as pattern syntax, a
    lookup for `%` would answer with every fact the agent has."""
    store = memory()
    marked = await store.add(MemoryScope.AGENT, "poet", "the battery is at 40%", agent="poet")
    await store.add(MemoryScope.AGENT, "poet", "the user is called Rafael", agent="poet")

    assert store.recall("poet", "aa:bb", "%").splitlines() == [
        f"- [{marked}] the battery is at 40%"
    ]


async def test_a_lookup_says_so_where_more_matched_than_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The line bound at an exact boundary, counted over the answer
    rather than over the matches: at two lines, one match and the
    sentence that says there were more is the whole of what fits."""
    monkeypatch.setattr(store_module, "RECALL_LINES", 2)
    store = memory()
    ids = [
        await store.add(MemoryScope.AGENT, "poet", f"a cheese fact {index}", agent="poet")
        for index in range(3)
    ]

    found = store.recall("poet", "aa:bb", "cheese")

    assert found.splitlines() == [
        f"- [{ids[2]}] a cheese fact 2",
        store_module.MORE_MATCHED,
    ]
    # And exactly at the bound, with nothing left out, it says nothing
    # and spends the line it would have spent saying it.
    monkeypatch.setattr(store_module, "RECALL_LINES", 3)
    assert store.recall("poet", "aa:bb", "cheese").splitlines() == [
        f"- [{ids[2]}] a cheese fact 2",
        f"- [{ids[1]}] a cheese fact 1",
        f"- [{ids[0]}] a cheese fact 0",
    ]


async def test_a_lookup_answer_is_inside_its_byte_bound_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The byte bound is on the answer, sentence included, so the exact
    total is what this asserts: room for one line and the continuation
    is an answer of exactly that size, not one over it by the length of
    the continuation."""
    monkeypatch.setattr(store_module, "RECALL_LINES", 10)
    store = memory()
    ids = [
        await store.add(MemoryScope.AGENT, "poet", f"a cheese fact {index}", agent="poet")
        for index in range(3)
    ]
    newest = f"- [{ids[2]}] a cheese fact 2"
    budget = len(f"{newest}\n{store_module.MORE_MATCHED}".encode())
    monkeypatch.setattr(store_module, "RECALL_BYTES", budget)

    found = store.recall("poet", "aa:bb", "cheese")

    assert found.splitlines() == [newest, store_module.MORE_MATCHED]
    assert len(found.encode()) == budget
    # One byte less and the line no longer fits beside the sentence, so
    # the answer is still inside the bound rather than over it.
    monkeypatch.setattr(store_module, "RECALL_BYTES", budget - 1)
    assert len(store.recall("poet", "aa:bb", "cheese").encode()) <= budget - 1


async def test_a_match_too_long_for_the_bound_comes_back_with_its_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case the sentence alone cannot answer. A single fact longer
    than the whole bound has nothing to narrow towards, so it comes back
    cut, with the id the model needs in order to correct or forget it."""
    monkeypatch.setattr(store_module, "MAX_BYTES", 4096)
    store = memory()
    long_one = await store.add(
        MemoryScope.AGENT, "poet", "a cheese fact " + "y" * 200, agent="poet"
    )
    monkeypatch.setattr(store_module, "RECALL_BYTES", 60)

    found = store.recall("poet", "aa:bb", "cheese")

    assert len(found.encode()) <= 60
    assert found.startswith(f"- [{long_one}] a cheese fact ")
    assert found.endswith(store_module.ELLIPSIS)
    # Nothing was left out of the answer, so nothing says there was.
    assert store_module.MORE_MATCHED not in found


async def test_an_injected_core_over_its_bound_is_empty_rather_than_over_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prune keeps a fact bigger than the whole cap, so the block
    has to be able to drop it: keeping it would put an over-cap block
    into every round's prompt, which is the one thing the block's bound
    exists to prevent. The fact is still stored and still found."""
    monkeypatch.setattr(store_module, "CORE_BYTES", 40)
    store = memory()
    long_one = await store.add(
        MemoryScope.AGENT, "poet", "a cheese fact " + "y" * 200, agent="poet"
    )

    read = store.read_for_prompt("poet", "aa:bb", THREAD)

    assert read.agent == ""
    assert len(_rows("poet")) == 1
    # Out of the block and still addressable, which is what makes the
    # empty block a bound rather than a loss.
    assert f"- [{long_one}] a cheese fact" in store.recall("poet", "aa:bb", "cheese")
    # And a fact that fits is injected as it always was, so the empty
    # block is the bound working rather than the block being broken.
    await store.add(MemoryScope.AGENT, "poet", "a short one", agent="poet")
    assert store.read_for_prompt("poet", "aa:bb", THREAD).agent == "- a short one"


# The conversation's ledger
#
# Keyed, current-only, and shared with its thread's lifecycle. The key
# is the identity, so a write replaces rather than accumulating, and a
# write that would take the ledger past either of its bounds is refused
# rather than silently trimmed: a ledger that drops keys is one the
# model cannot trust.


async def test_writing_the_same_key_again_replaces_what_it_held() -> None:
    store = memory()
    await store.set_state(THREAD, "turn", "white to move", agent="poet")
    await store.set_state(THREAD, "board", "e4 e5", agent="poet")
    await store.set_state(THREAD, "turn", "black to move", agent="poet")

    assert _state(THREAD) == [("board", "e4 e5"), ("turn", "black to move")]


async def test_one_conversations_ledger_is_not_another_s() -> None:
    store = memory()
    await store.set_state(THREAD, "turn", "white to move", agent="poet")
    await store.set_state(OTHER_THREAD, "turn", "black to move", agent="poet")

    assert _state(THREAD) == [("turn", "white to move")]
    assert _state(OTHER_THREAD) == [("turn", "black to move")]


async def test_a_new_key_past_the_key_cap_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(store_module, "STATE_KEYS", 2)
    store = memory()
    await store.set_state(THREAD, "one", "first", agent="poet")
    await store.set_state(THREAD, "two", "second", agent="poet")

    with pytest.raises(ValueError) as refusal:
        await store.set_state(THREAD, "three", "third", agent="poet")

    assert str(refusal.value) == store_module.STATE_FULL
    assert _state(THREAD) == [("one", "first"), ("two", "second")]
    # And an overwrite of a key that is already there still lands, since
    # it takes no new room in the ledger.
    await store.set_state(THREAD, "two", "second again", agent="poet")
    assert _state(THREAD) == [("one", "first"), ("two", "second again")]


async def test_an_overwrite_that_grows_past_the_byte_cap_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case a key cap alone would miss: no new key, and the ledger
    still ends up bigger than it may be. The old value stands, which is
    what makes the refusal safe to act on."""
    # Room for the grown note on its own, and not for the ledger it
    # would leave behind, which is exactly the case a key cap misses.
    monkeypatch.setattr(
        store_module, "STATE_BYTES", len(b"- two: second and then some")
    )
    store = memory()
    await store.set_state(THREAD, "one", "first", agent="poet")
    await store.set_state(THREAD, "two", "second", agent="poet")

    with pytest.raises(ValueError) as refusal:
        await store.set_state(THREAD, "two", "second and then some", agent="poet")

    assert str(refusal.value) == store_module.STATE_TOO_MUCH
    assert _state(THREAD) == [("one", "first"), ("two", "second")]


async def test_a_single_note_too_long_for_the_ledger_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clearing everything else could not make room for this one, so it
    is refused before a connection is reached, with a sentence of its
    own: what the model should do is say less, not clear something."""
    monkeypatch.setattr(store_module, "STATE_BYTES", 40)
    store = memory()
    await store.set_state(THREAD, "one", "first", agent="poet")

    with pytest.raises(ValueError) as refusal:
        await store.set_state(THREAD, "two", "x" * 41, agent="poet")

    assert str(refusal.value) == store_module.STATE_ENTRY_TOO_LONG
    assert _state(THREAD) == [("one", "first")]


async def test_a_note_with_no_name_or_nothing_to_say_is_refused() -> None:
    store = memory()

    with pytest.raises(ValueError) as unnamed:
        await store.set_state(THREAD, "   ", "something", agent="poet")
    with pytest.raises(ValueError) as unsaid:
        await store.set_state(THREAD, "turn", "  ", agent="poet")

    assert str(unnamed.value) == store_module.NOTHING_TO_SET
    assert str(unsaid.value) == store_module.NOTHING_TO_SET
    assert _state(THREAD) == []


async def test_clearing_answers_how_much_it_took() -> None:
    store = memory()
    await store.set_state(THREAD, "one", "first", agent="poet")
    await store.set_state(THREAD, "two", "second", agent="poet")
    await store.set_state(OTHER_THREAD, "one", "elsewhere", agent="poet")

    assert await store.clear_state(THREAD, "one", agent="poet") == 1
    assert _state(THREAD) == [("two", "second")]
    # Clearing what is already clear is what the caller asked for, and
    # the count is what says nothing was there.
    assert await store.clear_state(THREAD, "one", agent="poet") == 0

    assert await store.clear_state(THREAD, agent="poet") == 1
    assert _state(THREAD) == []
    assert _state(OTHER_THREAD) == [("one", "elsewhere")]


# The prompt's own read
#
# One call, one connection, three rendered blocks. What the reply path
# needs of memory in a round is exactly this, and the number of round
# trips it costs is the reason the call exists at all.


async def test_the_prompt_read_answers_all_three_scopes() -> None:
    store = memory()
    await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
    await store.add(MemoryScope.DEVICE, "aa:bb", "the kettle is loud", agent="poet")
    await store.set_state(THREAD, "turn", "white to move", agent="poet")
    # Another agent, another device and another thread, none of which
    # this reply may see.
    await store.add(MemoryScope.AGENT, "tutor", "the user is learning Spanish", agent="tutor")
    await store.add(MemoryScope.DEVICE, "cc:dd", "the door creaks", agent="poet")
    await store.set_state(OTHER_THREAD, "turn", "black to move", agent="poet")

    read = store.read_for_prompt("poet", "aa:bb", THREAD)

    assert read.state == "- turn: white to move"
    assert read.agent == "- the user is vegetarian"
    assert read.device == "- the kettle is loud"


async def test_the_prompt_read_of_an_empty_memory_is_three_empty_blocks() -> None:
    store = memory()

    assert store.read_for_prompt("poet", "aa:bb", THREAD) == store_module.NOTHING_REMEMBERED


async def test_a_prompt_read_with_no_device_and_no_thread_reads_one_scope(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What an agent-keyed caller asks for, which is what the preview
    is: this agent's own block, and no other scope reached for at all.

    Both halves matter. Reaching for a device nobody named would answer
    "no rows" by accident rather than saying there was nothing to read,
    and a failure that reported three lost scopes would name two this
    read never asked about.
    """
    store = memory()
    await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
    await store.add(MemoryScope.DEVICE, "aa:bb", "the kettle is loud", agent="poet")
    await store.set_state(THREAD, "turn", "white to move", agent="poet")

    assert store.read_for_prompt("poet", None, None) == store_module.PromptMemory(
        state="", agent="- the user is vegetarian", device=""
    )

    with caplog.at_level("WARNING"):
        assert memory_that_cannot_read().read_for_prompt("poet", None, None) == (
            store_module.NOTHING_REMEMBERED
        )
    assert {record.scope for record in events(caplog, "memory_unreadable")} == {"agent"}


async def test_the_prompt_read_takes_one_connection() -> None:
    """The property the call exists for. Three reads would be three
    round trips off the loop, and the reply path pays for one per
    round.

    Counted at the engine rather than reasoned about, through a store
    built with the same public constructor the opener uses.
    """
    from sqlalchemy import event as sqlalchemy_event

    from vinga_server.db import read_engine, write_engine

    reader = read_engine(DatabaseConfig())
    checkouts: list[int] = []
    sqlalchemy_event.listen(reader, "checkout", lambda *_: checkouts.append(1))
    store = MemoryStore(write_engine(DatabaseConfig(), MEMORY_CHAIN), reader)
    try:
        await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
        await store.set_state(THREAD, "turn", "white to move", agent="poet")
        checkouts.clear()

        read = store.read_for_prompt("poet", "aa:bb", THREAD)
    finally:
        store.close()

    assert read.agent == "- the user is vegetarian"
    assert len(checkouts) == 1


async def test_the_injected_core_is_the_newest_facts_and_the_rest_is_looked_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The boundary from both sides. What falls out of the block is
    still stored and still reachable by the lookup, and what is inside
    the block is reachable by the lookup too, with the number it is
    addressed by, which is the only way the model can correct it."""
    monkeypatch.setattr(store_module, "CORE_LINES", 2)
    store = memory()
    oldest = await store.add(MemoryScope.AGENT, "poet", "a cheese fact 0", agent="poet")
    middle = await store.add(MemoryScope.AGENT, "poet", "a cheese fact 1", agent="poet")
    newest = await store.add(MemoryScope.AGENT, "poet", "a cheese fact 2", agent="poet")

    read = store.read_for_prompt("poet", "aa:bb", THREAD)

    assert read.agent.splitlines() == ["- a cheese fact 1", "- a cheese fact 2"]
    # Beyond the core and still remembered, with the block showing no
    # ids and the lookup showing all of them.
    assert store.recall("poet", "aa:bb", "cheese").splitlines() == [
        f"- [{newest}] a cheese fact 2",
        f"- [{middle}] a cheese fact 1",
        f"- [{oldest}] a cheese fact 0",
    ]
    # And nothing was dropped to make the block fit: what falls out of
    # the core is out of the prompt, never out of the memory.
    assert len(_rows("poet")) == 3


def test_a_prompt_read_that_fails_loses_every_scope_and_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A statement that fails poisons the transaction the other two
    would have run in, so all three are lost together. Every one of them
    is reported, because a block that rendered empty with nothing said
    about it would be indistinguishable from a scope with nothing in
    it."""
    store = memory_that_cannot_read()

    with caplog.at_level("WARNING"):
        assert store.read_for_prompt("poet", "aa:bb", THREAD) == store_module.NOTHING_REMEMBERED

    reported = events(caplog, "memory_unreadable")
    assert {record.scope for record in reported} == {"conversation", "agent", "device"}
    assert {record.agent for record in reported} == {"poet"}
    assert {record.error for record in reported} == {"OperationalError"}
    assert all(record.exc_info is None for record in reported)


# What goes when a thread goes
#
# A thread's memory is its ledger and the facts it forgot; its active
# facts belong to the agent or the device and outlive every
# conversation. Two doors onto the same deletes: one on a connection the
# caller already holds, so an erasure takes both stores in one commit,
# and one of the store's own for a caller that holds none.


def _a_recorded_thread(conversation: str) -> None:
    """A thread with a row in the conversation record, which is what
    makes it real to the sweep's anti-join."""
    holder = _connection()
    try:
        holder.execute(
            "insert into record.conversations (conversation, agent, device, "
            "created_at, last_active_at) values (%s, 'poet', 'aa:bb', %s, %s)",
            (conversation, "2026-08-30T10:00:00+00:00", "2026-08-30T10:00:00+00:00"),
        )
        holder.commit()
    finally:
        holder.close()


def _age_the_memory_of(conversation: str, when: str) -> None:
    """Move one thread's ledger and held facts back in time, which is
    how a suite reaches a grace period without waiting out a day."""
    holder = _connection()
    try:
        holder.execute(
            "update memory.state set updated_at = %s where conversation = %s",
            (when, conversation),
        )
        holder.execute(
            "update memory.facts set forgotten_at = %s where forgotten_in = %s",
            (when, conversation),
        )
        holder.commit()
    finally:
        holder.close()


async def _a_thread_with_memory(store, conversation: str) -> None:
    await store.set_state(conversation, "turn", "white to move", agent="poet")
    gone = await store.add(
        MemoryScope.AGENT, "poet", f"a fact forgotten in {conversation}", agent="poet"
    )
    await store.forget(MemoryScope.AGENT, "poet", gone, conversation, agent="poet")


async def test_purging_a_thread_takes_its_ledger_and_what_it_forgot() -> None:
    store = memory()
    await _a_thread_with_memory(store, THREAD)
    await _a_thread_with_memory(store, OTHER_THREAD)
    kept = await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")

    taken = store.purge_threads([THREAD])

    assert taken == store_module.Purged(state=1, held_facts=1)
    assert _state(THREAD) == []
    assert [held for held, _ in _held("poet")] == [f"a fact forgotten in {OTHER_THREAD}"]
    assert _state(OTHER_THREAD) == [("turn", "white to move")]
    # And an active fact is nobody's thread: it belongs to the agent and
    # outlives every conversation it was said in.
    assert _rows("poet") == ["the user is vegetarian"]
    assert kept > 0


async def test_purging_nothing_reaches_no_connection() -> None:
    store = memory()
    await _a_thread_with_memory(store, THREAD)

    assert store.purge_threads([]) == store_module.NOTHING_PURGED
    assert _state(THREAD) == [("turn", "white to move")]


async def test_a_purge_on_a_callers_connection_belongs_to_its_transaction() -> None:
    """The seam the cross-store deletion is made of: the caller owns the
    transaction, so a rollback takes the memory deletes with it and
    there is no moment when a thread is gone while its state remains.

    On the record chain's own write engine, which is the caller this
    exists for: a transaction already holding key 2 reaches here and
    takes key 3, which is the ascending order the deadlock rule is."""
    store = memory()
    await _a_thread_with_memory(store, THREAD)

    engine = write_engine(DatabaseConfig(), CONVERSATIONS_CHAIN)
    try:
        with engine.connect() as connection:
            with connection.begin() as transaction:
                taken = store_module.purge(connection, [THREAD])
                transaction.rollback()
    finally:
        engine.dispose()

    assert taken == store_module.Purged(state=1, held_facts=1)
    # The counts were true of the transaction that was rolled back, and
    # the rows are exactly where they were.
    assert _state(THREAD) == [("turn", "white to move")]
    assert len(_held("poet")) == 1


# One owner's rows moved onto another name
#
# The memory third of an agent rename, which is `purge`'s seam widened by
# one verb. What the cross-schema transaction above it promises has a
# suite of its own (`test_agent_rename.py`); what belongs here is the
# function's own contract: which rows it moves, what it answers, what it
# refuses, and that its writes are the caller's transaction's.


async def test_renaming_an_owner_moves_its_active_and_its_held_rows() -> None:
    """Both areas, because a held row carries `owner` like any other and
    a restore after the rename has to find it. The count is the rows that
    moved, so a caller can say what the write did without reading the
    table back."""
    store = memory()
    await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
    await _a_thread_with_memory(store, THREAD)

    engine = write_engine(DatabaseConfig(), MEMORY_CHAIN)
    try:
        with engine.begin() as connection:
            moved = store_module.rename_owner(
                connection, MemoryScope.AGENT, "poet", "bard"
            )
    finally:
        engine.dispose()

    assert moved == 2
    assert _rows("poet") == [] and _held("poet") == []
    assert _rows("bard") == ["the user is vegetarian"]
    assert [fact for fact, _ in _held("bard")] == [f"a fact forgotten in {THREAD}"]


async def test_renaming_an_owner_leaves_another_scope_alone() -> None:
    """The address is the pair, not the name: a device whose canonical
    MAC happened to spell an agent's name would not be moved by an agent
    rename, and neither is any row of the other scope."""
    store = memory()
    await store.add(MemoryScope.AGENT, "poet", "an agent fact", agent="poet")
    await store.add(MemoryScope.DEVICE, "poet", "a device note", agent="poet")

    engine = write_engine(DatabaseConfig(), MEMORY_CHAIN)
    try:
        with engine.begin() as connection:
            store_module.rename_owner(connection, MemoryScope.AGENT, "poet", "bard")
    finally:
        engine.dispose()

    assert _rows("bard") == ["an agent fact"]
    assert _rows("poet", scope="device") == ["a device note"]


async def test_renaming_onto_an_occupied_name_is_refused_and_moves_nothing() -> None:
    """The rule the refusal comes from is one sentence: the destination
    has to be free. A merge could not be undone, because no second rename
    could tell the two owners' rows apart afterwards.

    The sentence names neither name, and the whole transaction is left
    for the caller to roll back.
    """
    store = memory()
    await store.add(MemoryScope.AGENT, "poet", "an agent fact", agent="poet")
    await store.add(MemoryScope.AGENT, "bard", "somebody else's fact", agent="bard")

    engine = write_engine(DatabaseConfig(), MEMORY_CHAIN)
    try:
        with engine.begin() as connection:
            with pytest.raises(AgentRenameConflictError) as refused:
                store_module.rename_owner(
                    connection, MemoryScope.AGENT, "poet", "bard"
                )
    finally:
        engine.dispose()

    assert str(refused.value) == store_module.RENAME_OCCUPIED
    assert "poet" not in str(refused.value)
    assert "bard" not in str(refused.value)
    assert _rows("poet") == ["an agent fact"]
    assert _rows("bard") == ["somebody else's fact"]


async def test_renaming_an_owner_that_holds_nothing_moves_nothing() -> None:
    """Not addressed at a row, so nothing is refused for being absent,
    which is the contract `erase_facts` keeps for the same reason."""
    engine = write_engine(DatabaseConfig(), MEMORY_CHAIN)
    try:
        with engine.begin() as connection:
            moved = store_module.rename_owner(
                connection, MemoryScope.AGENT, "poet", "bard"
            )
    finally:
        engine.dispose()

    assert moved == 0


async def test_a_rename_on_a_callers_connection_belongs_to_its_transaction() -> None:
    """The seam again, from the side that writes rather than deletes: the
    caller owns the transaction, so a rollback takes the rename with it
    and there is never a moment when an agent has been renamed and its
    memory has not.

    On the record chain's own write engine, which is one lock short of
    the caller this exists for and enough to show the crossing: a
    transaction already holding key 2 reaches here and takes key 3.
    """
    store = memory()
    await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")

    engine = write_engine(DatabaseConfig(), CONVERSATIONS_CHAIN)
    try:
        with engine.connect() as connection:
            with connection.begin() as transaction:
                moved = store_module.rename_owner(
                    connection, MemoryScope.AGENT, "poet", "bard"
                )
                transaction.rollback()
    finally:
        engine.dispose()

    assert moved == 1
    assert _rows("poet") == ["the user is vegetarian"]
    assert _rows("bard") == []


# A thread id is a value too, and a purge binds them
#
# The one write that runs on somebody else's connection, and therefore
# the one that cannot contain its failure: it has to take the caller's
# transaction down. What it must not take with it is the driver's own
# error, which carries the statement it ran and the ids bound into it.

PURGED = "sk-test-4f81c0d2-a-thread-nobody-should-repeat"


@contextlib.contextmanager
def _the_ledger_refusing_deletes() -> Iterator[None]:
    """A test-only `BEFORE DELETE` trigger on the ledger, which is the
    cheapest genuine failure a live connection can meet: a statement
    that runs and is refused, with the ids the caller bound still in the
    error the driver raises."""
    holder = _connection()
    try:
        holder.execute(
            "create function memory.refuse_state_delete() returns trigger "
            "language plpgsql as $$ begin raise exception 'no deletes in this "
            "test'; end $$"
        )
        holder.execute(
            "create trigger refuse_state_delete before delete on memory.state "
            "for each row execute function memory.refuse_state_delete()"
        )
        holder.commit()
        yield
    finally:
        holder.execute("drop trigger if exists refuse_state_delete on memory.state")
        holder.execute("drop function if exists memory.refuse_state_delete()")
        holder.commit()
        holder.close()


async def test_a_purge_that_the_database_refuses_quotes_nothing_of_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The refusal is this module's own fixed sentence, and the thread
    id the purge bound into its statement is nowhere: not in the
    sentence, not on the chain the caller can walk, and not in either
    log format. The caller's transaction is left to roll back, which is
    what the counts being true depends on."""
    from vinga_server.db import write_engine

    store = memory()
    await store.set_state(PURGED, "turn", "white to move", agent="poet")

    engine = write_engine(DatabaseConfig(), MEMORY_CHAIN)
    try:
        with caplog.at_level("DEBUG"), _the_ledger_refusing_deletes():
            with engine.connect() as connection:
                with connection.begin():
                    with pytest.raises(ConfigError) as refusal:
                        store_module.purge(connection, [PURGED])
    finally:
        engine.dispose()

    assert str(refusal.value) == store_module.PURGE_FAILED
    # Built inside the handler and raised after it, so the failure that
    # quoted the statement is on no chain a caller can walk.
    assert refusal.value.__cause__ is None
    assert refusal.value.__context__ is None
    walked: list[BaseException] = []
    cause: BaseException | None = refusal.value
    while cause is not None:
        walked.append(cause)
        cause = cause.__cause__ or cause.__context__
    for surface in (str(refusal.value), both_formats(caplog), *map(str, walked)):
        assert PURGED not in surface
    # And the rows are where they were: the caller's transaction rolled
    # back around a refusal it could act on.
    assert _state(PURGED) == [("turn", "white to move")]


async def test_the_sweep_takes_an_orphan_older_than_the_grace_period() -> None:
    """Narrowed to what no transaction covers. A thread the record
    knows is not the sweep's business however old it is, and one it does
    not know is not the sweep's business yet: state can precede its
    thread's first turn."""
    store = memory()
    await _a_thread_with_memory(store, THREAD)
    await _a_thread_with_memory(store, OTHER_THREAD)
    recorded = "abcdef0123456789abcdef0123456789"
    await _a_thread_with_memory(store, recorded)
    _a_recorded_thread(recorded)

    # One orphan aged past the grace period, one left where it is, and
    # the recorded thread aged too so that age alone cannot be what
    # takes it.
    long_ago = "2020-01-01T00:00:00+00:00"
    _age_the_memory_of(THREAD, long_ago)
    _age_the_memory_of(recorded, long_ago)

    taken = store.sweep()

    assert taken == store_module.Purged(state=1, held_facts=1)
    assert _state(THREAD) == []
    # The young orphan is still there, which is the whole reason the
    # grace period exists: a thread materializes at its first turn.
    assert _state(OTHER_THREAD) == [("turn", "white to move")]
    assert _state(recorded) == [("turn", "white to move")]
    assert [held for held, _ in _held("poet")] == [
        f"a fact forgotten in {OTHER_THREAD}",
        f"a fact forgotten in {recorded}",
    ]


def test_a_cleanup_that_cannot_reach_its_database_says_so_and_takes_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Neither a read nor a write in the sense the other two seams mean:
    nobody asked for it and no agent's reply depends on it, so the
    failure is said once, by class, with no agent and no scope."""
    store = memory_that_cannot_write()

    with caplog.at_level("WARNING"):
        assert store.sweep() == store_module.NOTHING_PURGED

    record = only(caplog, "memory_cleanup_failed")
    assert record.error == "OperationalError"
    assert record.exc_info is None
    assert not hasattr(record, "agent")
    assert not hasattr(record, "scope")


# The cap invariant, across every mutation
#
# Held rows are outside all of it: never counted, never pruned, still
# restorable. Everything else re-prunes inside the transaction that took
# the scope past its cap, so a grown correction and a restore into a
# scope that refilled both succeed rather than failing or overflowing.


async def test_a_correction_too_long_for_its_scope_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(store_module, "MAX_BYTES", 40)
    store = memory()
    fact_id = await store.add(MemoryScope.AGENT, "poet", "a short fact", agent="poet")

    with pytest.raises(ValueError) as refusal:
        await store.update(MemoryScope.AGENT, "poet", fact_id, "x" * 41, agent="poet")

    assert str(refusal.value) == store_module.TOO_LONG
    assert _rows("poet") == ["a short fact"]


async def test_a_correction_that_grows_a_fact_re_prunes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scope is inside its bound when the transaction ends, whatever
    the correction did to it: the oldest active fact goes, and the
    corrected one stays even where it is the oldest."""
    # Room for the corrected fact and one other, which is what makes the
    # correction fit while the three facts around it do not.
    monkeypatch.setattr(store_module, "MAX_BYTES", len(b"- aaa longer\n- ccc"))
    store = memory()
    oldest = await store.add(MemoryScope.AGENT, "poet", "aaa", agent="poet")
    await store.add(MemoryScope.AGENT, "poet", "bbb", agent="poet")
    await store.add(MemoryScope.AGENT, "poet", "ccc", agent="poet")

    await store.update(MemoryScope.AGENT, "poet", oldest, "aaa longer", agent="poet")

    # The corrected fact is the oldest, and it survives: a prune that
    # took it would answer success and change nothing.
    assert _rows("poet") == ["aaa longer", "ccc"]


async def test_held_facts_are_outside_every_cap_and_come_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole of what makes the undo a promise. A held fact is not
    counted against the cap, is not chosen by any prune however many
    writes go past it, and is still there to be brought back; and the
    restore itself re-prunes rather than overflowing."""
    monkeypatch.setattr(store_module, "MAX_LINES", 2)
    store = memory()
    first = await store.add(MemoryScope.AGENT, "poet", "fact 0", agent="poet")
    held = await store.add(MemoryScope.AGENT, "poet", "fact 1", agent="poet")
    await store.forget(MemoryScope.AGENT, "poet", held, THREAD, agent="poet")

    # Two writes that each take the scope past its cap, with the held
    # row sitting between them in id order.
    await store.add(MemoryScope.AGENT, "poet", "fact 2", agent="poet")
    await store.add(MemoryScope.AGENT, "poet", "fact 3", agent="poet")

    assert _rows("poet") == ["fact 2", "fact 3"]
    assert _held("poet") == [("fact 1", THREAD)]
    # The oldest active fact was taken by the prune while the older held
    # one was not, which no cap arithmetic that counted it could do.
    with pytest.raises(ValueError) as gone:
        await store.forget(MemoryScope.AGENT, "poet", first, THREAD, agent="poet")
    assert str(gone.value) == store_module.NO_FACT_TO_FORGET

    assert await store.restore(MINE, THREAD, agent="poet") == "fact 1"
    # Back in its own place, with the scope back inside its cap: the
    # restore re-pruned the oldest active fact rather than refusing or
    # leaving three where two fit.
    assert _rows("poet") == ["fact 1", "fact 3"]


async def test_concurrent_appends_keep_every_fact() -> None:
    # Two sessions can be talking to the same agent at once, and both
    # writes go through one advisory lock rather than one lock per
    # process.
    store = memory()
    await asyncio.gather(
        *(
            store.add(MemoryScope.AGENT, "poet", f"fact {index}", agent="poet")
            for index in range(20)
        )
    )
    assert len(injected(store).splitlines()) == 20


async def test_a_fact_outlives_the_store_that_wrote_it() -> None:
    """The acceptance criterion the move was made for: a restart. The
    store that wrote the fact is closed, pools and all, and a store
    opened afterwards reads it."""
    first = open_memory(DatabaseConfig())
    try:
        await first.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
    finally:
        first.close()

    second = open_memory(DatabaseConfig())
    try:
        assert injected(second) == "- the user is vegetarian"
    finally:
        second.close()


async def test_the_remember_tool_confirms_what_it_stored_and_its_number() -> None:
    """The number is in the confirmation because it is nowhere else: the
    injected block shows none, so a fact remembered a moment ago would
    otherwise have to be looked up before it could be corrected."""
    store = memory()

    answer = await remember(store, HERE, "poet", {"text": "the user is vegetarian"})

    ((fact_id, _),) = _numbered("poet")
    assert answer == f"Remembered [{fact_id}]: the user is vegetarian"
    assert "vegetarian" in injected(store)


async def test_the_remember_tool_keeps_a_device_fact_for_the_device() -> None:
    """The scope the model names decides which memory the fact lands in,
    and the owner comes off the session rather than out of the arguments:
    a model that could name a device would be writing into another
    household's notes."""
    store = memory()

    answer = await remember(
        store, HERE, "poet", {"text": "the kettle is loud", "scope": "device"}
    )

    ((fact_id, _),) = _numbered("aa:bb", scope="device")
    assert answer == f"Remembered [{fact_id}]: the kettle is loud"
    assert _rows("poet") == []
    assert _rows("aa:bb", scope="device") == ["the kettle is loud"]


@pytest.mark.parametrize(
    ("arguments", "refusal"),
    [
        ({"fact": "wrong key"}, builtin.REMEMBER_NEEDS_TEXT),
        ({"text": "   "}, builtin.REMEMBER_NEEDS_TEXT),
        ({"text": "a fact", "scope": "conversation"}, builtin.UNKNOWN_SCOPE),
        ({"text": "a fact", "scope": 7}, builtin.UNKNOWN_SCOPE),
    ],
)
async def test_the_remember_tool_refuses_a_call_it_cannot_act_on(
    arguments: dict[str, object], refusal: str
) -> None:
    """The ValueError shape a builtin's bad arguments take, by equality
    against the module's own sentences. A scope no fact can carry is
    turned away here rather than at the store, which is where the model
    can be told what it may have meant."""
    store = memory()

    with pytest.raises(ValueError) as refused:
        await remember(store, HERE, "poet", arguments)

    assert str(refused.value) == refusal
    assert _rows("poet") == []


async def test_the_remember_tool_refuses_one_fact_too_long_to_keep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal that #314 did not make, on the door a model speaks
    through. The prune never goes below one fact, so an oversized one
    used to be stored and left its scope over the cap for as long as it
    lived; the byte cap is a promise about the whole scope, so a fact
    that cannot fit inside it is refused wherever it arrives."""
    monkeypatch.setattr(store_module, "MAX_BYTES", 40)
    store = memory()

    with pytest.raises(ValueError) as refusal:
        await remember(store, HERE, "poet", {"text": "x" * 41})

    assert str(refusal.value) == store_module.TOO_LONG
    assert _rows("poet") == []


def test_the_tool_asks_for_one_short_fact_and_whose_it_is() -> None:
    tool = remember_tool()
    assert tool.name == "remember"
    assert tool.input_schema["required"] == ["text"]
    # The enum is the fact scopes and not the vocabulary: a conversation
    # is not something a fact can belong to, so it is not something the
    # model can be shown.
    assert tool.input_schema["properties"]["scope"]["enum"] == ["agent", "device"]


async def test_correcting_and_forgetting_reach_the_device_too() -> None:
    """The model names a number and not a memory, so the tool asks each
    memory this session can reach. The device's is what the second
    attempt exists for: the agent's own is tried first and refuses.
    """
    store = memory()
    theirs = await store.add(
        MemoryScope.DEVICE, "aa:bb", "the kettle is loud", agent="poet"
    )

    corrected = await update_memory(
        store, HERE, "poet", {"id": theirs, "text": "the kettle whistles"}
    )
    removed = await forget(store, HERE, "poet", {"id": str(theirs)})

    assert corrected == f"Corrected [{theirs}]: the kettle whistles"
    # The words are what the agent says out loud, so the removal answers
    # with them and with the number they were kept under.
    assert removed == f"Forgot [{theirs}]: the kettle whistles"
    assert _rows("aa:bb", scope="device") == []
    assert _held("aa:bb", scope="device") == [("the kettle whistles", THREAD)]


async def test_a_number_this_session_cannot_reach_is_one_refusal() -> None:
    """Another agent's fact and a number that names nothing are answered
    identically, which is what makes a refusal confirm nothing about
    what exists elsewhere."""
    store = memory()
    theirs = await store.add(
        MemoryScope.AGENT, "tutor", "the user is learning Spanish", agent="tutor"
    )

    with pytest.raises(ValueError) as correcting:
        await update_memory(store, HERE, "poet", {"id": theirs, "text": "something else"})
    with pytest.raises(ValueError) as forgetting:
        await forget(store, HERE, "poet", {"id": theirs})
    with pytest.raises(ValueError) as missing:
        await forget(store, HERE, "poet", {"id": theirs + 10_000})

    assert str(correcting.value) == store_module.NO_FACT_TO_UPDATE
    assert str(forgetting.value) == store_module.NO_FACT_TO_FORGET
    assert str(missing.value) == store_module.NO_FACT_TO_FORGET
    # Nothing of the attempts travels out on a chain: the refusal is
    # built inside the arm that caught one and raised after it.
    assert correcting.value.__context__ is None
    assert correcting.value.__cause__ is None
    assert _rows("tutor") == ["the user is learning Spanish"]


async def test_forgetting_for_good_is_asked_for_exactly() -> None:
    """Anything but true is the soft removal, which is the direction a
    misread argument should fail in: a held fact costs a row, and an
    erased one costs the fact."""
    store = memory()
    gone = await store.add(
        MemoryScope.AGENT, "poet", "the card number is 4111", agent="poet"
    )
    kept = await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")

    assert await forget(store, HERE, "poet", {"id": gone, "permanently": True}) == (
        f"Forgot [{gone}]: the card number is 4111"
    )
    await forget(store, HERE, "poet", {"id": kept, "permanently": "yes please"})

    assert _rows("poet") == []
    assert _held("poet") == [("the user is vegetarian", THREAD)]


@pytest.mark.parametrize(
    ("call", "arguments", "refusal"),
    [
        (update_memory, {"text": "a fact"}, builtin.UPDATE_NEEDS_A_NUMBER_AND_TEXT),
        (update_memory, {"id": "seven", "text": "a fact"}, builtin.UPDATE_NEEDS_A_NUMBER_AND_TEXT),
        (update_memory, {"id": True, "text": "a fact"}, builtin.UPDATE_NEEDS_A_NUMBER_AND_TEXT),
        (update_memory, {"id": 7}, builtin.UPDATE_NEEDS_A_NUMBER_AND_TEXT),
        (update_memory, {"id": 7, "text": "  "}, builtin.UPDATE_NEEDS_A_NUMBER_AND_TEXT),
        (forget, {}, builtin.FORGET_NEEDS_A_NUMBER),
        (forget, {"id": None}, builtin.FORGET_NEEDS_A_NUMBER),
        (forget, {"id": 1.5}, builtin.FORGET_NEEDS_A_NUMBER),
    ],
)
async def test_a_numbered_tool_asked_with_arguments_it_cannot_use_refuses(
    call: object, arguments: dict[str, object], refusal: str
) -> None:
    """The ValueError shape, decided before any memory is reached: what
    is missing is named rather than what arrived, since what arrived is
    the model's own and what it needs back is what to send instead."""
    store = memory()
    stored = await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")

    with pytest.raises(ValueError) as refused:
        await call(store, HERE, "poet", arguments)  # type: ignore[operator]

    assert str(refused.value) == refusal
    assert _numbered("poet") == [(stored, "the user is vegetarian")]


async def test_bringing_back_the_last_thing_forgotten_needs_no_number() -> None:
    """The shape a person actually asks for, and the one the number
    reaches. What comes back is answered with, because that is what the
    agent says out loud."""
    store = memory()
    first = await store.add(MemoryScope.AGENT, "poet", "fact one", agent="poet")
    second = await store.add(MemoryScope.AGENT, "poet", "fact two", agent="poet")
    await forget(store, HERE, "poet", {"id": first})
    await forget(store, HERE, "poet", {"id": second})

    assert await restore_memory(store, HERE, "poet", {}) == "Brought back: fact two"
    assert await restore_memory(store, HERE, "poet", {"id": first}) == (
        "Brought back: fact one"
    )
    assert _held("poet") == []
    assert _rows("poet") == ["fact one", "fact two"]


async def test_the_last_thing_forgotten_is_the_last_one_whichever_memory_it_was_in() -> None:
    """The mixed case the tool cannot decide for itself. A conversation
    that forgets a fact about the user and then a note about the room has
    forgotten the note last, and "put that back" means the note.

    Decided over both memories in one statement rather than by asking one
    of them first: an order that tried the agent's memory before the
    device's would answer with the older of the two whenever both are
    held, which is the one case the ordering is visible in at all.
    """
    store = memory()
    # The note is the older row and the newer removal, so nothing but
    # when it was forgotten can put it first: an order that read the ids
    # would answer with the fact, and so would one that asked the agent's
    # memory before the device's.
    theirs = await store.add(
        MemoryScope.DEVICE, "aa:bb", "the kettle is loud", agent="poet"
    )
    mine = await store.add(
        MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet"
    )
    assert theirs < mine
    await forget(store, HERE, "poet", {"id": mine})
    await forget(store, HERE, "poet", {"id": theirs})

    assert await restore_memory(store, HERE, "poet", {}) == (
        "Brought back: the kettle is loud"
    )

    # And the one before it is still held, so asking again reaches it:
    # the newest is a choice among rows rather than a memory to prefer.
    assert _held("poet") == [("the user is vegetarian", THREAD)]
    assert await restore_memory(store, HERE, "poet", {}) == (
        "Brought back: the user is vegetarian"
    )
    assert _rows("poet") == ["the user is vegetarian"]
    assert _rows("aa:bb", scope="device") == ["the kettle is loud"]


async def test_bringing_back_reaches_the_device_and_refuses_another_conversation() -> None:
    """Both edges of the undo at once: a note about the place is this
    session's to bring back, and a fact forgotten somewhere else is not
    this conversation's, which is the same sentence a number that names
    nothing gets."""
    store = memory()
    theirs = await store.add(
        MemoryScope.DEVICE, "aa:bb", "the kettle is loud", agent="poet"
    )
    await store.forget(MemoryScope.DEVICE, "aa:bb", theirs, OTHER_THREAD, agent="poet")

    with pytest.raises(ValueError) as elsewhere:
        await restore_memory(store, HERE, "poet", {"id": theirs})
    assert str(elsewhere.value) == store_module.NO_FACT_TO_RESTORE

    await store.restore(((MemoryScope.DEVICE, "aa:bb"),), OTHER_THREAD, agent="poet")
    await forget(store, HERE, "poet", {"id": theirs})
    assert await restore_memory(store, HERE, "poet", {}) == (
        "Brought back: the kettle is loud"
    )
    assert _rows("aa:bb", scope="device") == ["the kettle is loud"]


async def test_looking_something_up_answers_both_memories_with_their_numbers() -> None:
    """What the injected block leaves out, and the numbers it never
    shows: this is how the model reaches either."""
    store = memory()
    mine = await store.add(MemoryScope.AGENT, "poet", "the user likes cheese", agent="poet")
    theirs = await store.add(
        MemoryScope.DEVICE, "aa:bb", "the CHEESE drawer sticks", agent="poet"
    )

    found = await recall(store, HERE, "poet", {"query": "cheese"})

    assert found.splitlines() == [
        f"- [{theirs}] the CHEESE drawer sticks",
        f"- [{mine}] the user likes cheese",
    ]
    # Nothing matching is an answer rather than a refusal: the model
    # asked a question and there is nothing there.
    assert await recall(store, HERE, "poet", {"query": "sourdough"}) == (
        builtin.NOTHING_MATCHED
    )


@pytest.mark.parametrize(
    ("call", "arguments", "refusal"),
    [
        (restore_memory, {"id": "seven"}, builtin.RESTORE_TAKES_A_NUMBER),
        (recall, {}, builtin.RECALL_NEEDS_A_QUERY),
        (recall, {"query": "  "}, builtin.RECALL_NEEDS_A_QUERY),
        (recall, {"query": 7}, builtin.RECALL_NEEDS_A_QUERY),
    ],
)
async def test_the_undo_and_the_lookup_refuse_arguments_they_cannot_use(
    call: object, arguments: dict[str, object], refusal: str
) -> None:
    store = memory()

    with pytest.raises(ValueError) as refused:
        await call(store, HERE, "poet", arguments)  # type: ignore[operator]

    assert str(refused.value) == refusal


async def test_remembered_facts_reach_the_model_through_the_prompt() -> None:
    """What the store holds, as the assembler injects it. The assembly
    itself is `test_runtime_prompt.py`; what this pins is that the two
    ends meet: one agent's rows are one agent's facts, and one
    conversation's ledger is one conversation's."""
    store = memory()
    half = prompt.know_how("POET")
    assert prompt.with_scopes(half, store.read_for_prompt("poet", None, THREAD)).text == "POET"

    await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
    await store.set_state(THREAD, "scene", "the tavern", agent="poet")
    assembled = prompt.with_scopes(
        half, store.read_for_prompt("poet", None, THREAD)
    ).text
    assert assembled.startswith("POET")
    assert f"{prompt.MEMORY_HEADING}\n- the user is vegetarian" in assembled
    assert f"{prompt.STATE_HEADING}\n- scene: the tavern" in assembled
    # Another agent's prompt, and another conversation's, are untouched
    # by both.
    assert (
        prompt.with_scopes(
            prompt.know_how("TUTOR"),
            store.read_for_prompt("tutor", None, OTHER_THREAD),
        ).text
        == "TUTOR"
    )


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
        await store.add(MemoryScope.AGENT, "poet", f"fact {index}", agent="poet")
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
            await store.add(MemoryScope.AGENT, "poet", "fact 3", agent="poet")
    finally:
        holder.execute("drop trigger if exists refuse_delete on memory.facts")
        holder.execute("drop function if exists memory.refuse_delete()")
        holder.commit()
        holder.close()

    assert str(refusal.value) == store_module.UNWRITABLE
    assert _rows("poet") == before
    assert injected(store) == "\n".join(f"- {fact}" for fact in before)


async def test_two_writers_at_the_cap_cannot_lose_each_others_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two stores over separately opened engines, prefilled exactly at
    the pruning boundary, with the interleaving forced rather than hoped
    for.

    Starting both and waiting on them proves nothing: nothing makes the
    second transaction read before the first commits, and a runner that
    happens to run them in order gets the right answer with no lock at
    all. So the first writer is parked inside its own transaction, past
    its insert, its read and its prune and before its commit, by a
    test-only trigger that waits on a key this suite holds; only then is
    the second started, and the gate is released once the second has had
    its chance to move.

    That is what makes the chain lock the thing under test. At the line
    cap each write drops exactly one row, and both would choose the same
    victim. With the lock, the second writer never begins until the
    first has committed: it reads three rows plus its own, drops the
    next oldest, and the three newest facts are what is left. Without
    it, the second decides on the same rows the first decided on, both
    delete `GATED_VICTIM`, the second's delete finds it already gone,
    and what survives is four rows, over the cap, still carrying the
    fact the pruning was supposed to drop. The assertions below are the
    exact final state, which is what the mutation cannot pass.
    """
    monkeypatch.setattr(store_module, "MAX_LINES", 3)
    monkeypatch.setattr(store_module, "MAX_BYTES", len(b"- fact 3\n- fact 4\n- fact 5"))
    filling = memory()
    for index in range(3):
        await filling.add(MemoryScope.AGENT, "poet", f"fact {index}", agent="poet")

    first = open_memory(DatabaseConfig())
    second = open_memory(DatabaseConfig())
    try:
        with _the_prune_gate_held():
            parked = asyncio.create_task(
                first.add(MemoryScope.AGENT, "poet", GATED_FACT, agent="poet")
            )
            assert await _waiting_on_a_lock(1), "the first writer never reached the gate"

            behind = asyncio.create_task(
                second.add(MemoryScope.AGENT, "poet", "fact 4", agent="poet")
            )
            # The second writer parked too, wherever the arrangement
            # leaves it, so the gate is released against two decided
            # transactions rather than against a stopwatch.
            assert await _waiting_on_a_lock(2), "the second writer never started"
        await asyncio.gather(parked, behind)
        rendered = injected(first)
    finally:
        second.close()
        first.close()

    surviving = _rows("poet")
    assert set(surviving) == {"fact 2", GATED_FACT, "fact 4"}
    assert len(surviving) == 3
    assert rendered.splitlines() == [f"- {fact}" for fact in surviving]
    assert len(rendered.encode("utf-8")) <= store_module.MAX_BYTES


# A close, and the calls it meets
#
# The store is closed on the application's exit stack while the reply
# path may still be reading memory from a worker thread, and the
# shutdown drain is bounded, so a call outliving the close is reachable
# rather than theoretical. Disposing an engine under one leaves its
# connection in a pool nothing owns, which closes nothing when it is
# collected; letting one in after the close has decided opens a pool
# nobody will ever dispose. Both cases are arranged here rather than
# waited for.


async def test_a_close_waits_for_a_write_still_inside_its_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write parked on the chain's advisory gate, and a close that
    begins while it is parked: the close is still waiting when the write
    is released, and the write goes on to store its fact through the
    connection it was holding.

    Nothing here is timed. The write cannot move while another
    connection holds the chain lock, so "the close has not disposed
    under it" is asserted where the write is physically unable to have
    finished, and the lock timeout is widened rather than shortened so
    that the release is what ends the wait rather than a refusal. The
    timeout is set before the store is opened, because it rides on a
    connection's startup options and an engine keeps the ones it was
    built with.

    That the close really had begun is not assumed either. A `read` on a
    closing store is refused admission and answers empty, so a store
    with a fact in it that answers empty has entered its close.

    The write succeeding is the whole point. A pool disposed under it
    would have taken its connection with it, and what would come back
    is a failure rather than a fact.
    """
    monkeypatch.setattr(db_module, "LOCK_TIMEOUT_MS", 30_000)
    store = open_memory(DatabaseConfig())
    await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")

    with contextlib.ExitStack() as gate:
        gate.enter_context(the_lock_held(MEMORY_CHAIN))
        write = asyncio.create_task(
            store.add(MemoryScope.AGENT, "poet", "a second fact", agent="poet")
        )
        assert await _waiting_on_a_lock(1), "the write never reached the gate"

        close = asyncio.create_task(asyncio.to_thread(store.close))
        assert await _closing(store), "the close never began"
        assert not close.done(), "the close let go of the pools under a live write"

        gate.close()
        await write
        await close

    assert _rows("poet") == ["the user is vegetarian", "a second fact"]


async def test_a_call_arriving_during_a_close_is_refused_admission(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other half of the same lock: a call that arrives once the
    close has decided is turned away where it asks for admission, before
    the engine in its `with` is reached at all.

    Proven by the class name on the event rather than by the answer,
    because the answer alone cannot tell the two apart: a read that
    reached a disposed pool would also answer empty, and a write that
    reached one would also refuse. What separates them is what the
    failure was, and `StoreClosed` is a decision this module made rather
    than anything a driver said.
    """
    store = open_memory(DatabaseConfig())
    await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
    assert injected(store) == "- the user is vegetarian"

    store.close()

    with caplog.at_level("WARNING"):
        assert injected(store) == ""
        with pytest.raises(ConfigError) as refusal:
            await store.add(MemoryScope.AGENT, "poet", "a second fact", agent="poet")

    assert str(refusal.value) == store_module.UNWRITABLE
    assert only(caplog, "memory_unreadable").error == "StoreClosed"
    assert only(caplog, "memory_unwritable").error == "StoreClosed"
    # And the refused write reached no connection: the row it would have
    # written is not there, and the one that was is untouched.
    assert _rows("poet") == ["the user is vegetarian"]


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
        assert injected(store) == ""

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
    asyncio.run(memory().add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet"))

    with holding_the_write_lock(monkeypatch, MEMORY_CHAIN):
        # Opened under the shortened timeout, so a read that ever did
        # ask for the lock fails this in milliseconds rather than
        # hanging the lane on a wait that has no bound.
        store = open_memory(DatabaseConfig())
        try:
            with the_lock_held(MEMORY_CHAIN):
                assert injected(store) == "- the user is vegetarian"
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
                        await store.add(
                            MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet"
                        )
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
            await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")

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
            unreadable = memory_that_cannot_read()
            # Every door onto the database, because the claim is
            # "nowhere" and a door left out is a door nothing asserts
            # about. The reads answer empty and the cleanup answers
            # nothing; only the writes refuse.
            assert injected(store) == ""
            assert unreadable.read_for_prompt("poet", "aa:bb", THREAD) == (
                store_module.NOTHING_REMEMBERED
            )
            assert unreadable.recall("poet", "aa:bb", "cheese") == ""
            assert store.sweep() == store_module.NOTHING_PURGED
            assert store.purge_threads([THREAD]) == store_module.NOTHING_PURGED
            with pytest.raises(ConfigError) as writing:
                await store.add(MemoryScope.AGENT, "poet", "the user is vegetarian", agent="poet")
            for call in (
                lambda: store.add(
                    MemoryScope.DEVICE, "aa:bb", "the kettle is loud", agent="poet"
                ),
                lambda: store.update(
                    MemoryScope.AGENT, "poet", 7, "corrected", agent="poet"
                ),
                lambda: store.forget(MemoryScope.AGENT, "poet", 7, THREAD, agent="poet"),
                lambda: store.restore(MINE, THREAD, agent="poet"),
                lambda: store.set_state(THREAD, "turn", "white to move", agent="poet"),
                lambda: store.clear_state(THREAD, agent="poet"),
            ):
                with pytest.raises(ConfigError):
                    await call()

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


# A fact is content, and a refusal is read out loud
#
# The other half of the sentinel above. What a caller passes here is the
# user's own words, and every refusal this module makes is spoken by a
# model into a conversation that may be recorded. So the claim is that
# no refusal, no event and no log line repeats a word of what was
# offered, or the number that was addressed, and every sentence a caller
# meets is one of this module's own declared constants.

SPOKEN = "sk-test-2b9e41c7-a-fact-nobody-should-repeat"

ADDRESSED = 424_242


async def test_nothing_a_caller_offered_is_repeated_back_by_a_refusal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Every refusal a caller can reach, driven with a credential-shaped
    fact, a credential-shaped ledger value and an id that names nothing,
    and each sentence compared by equality against the constant that
    declares it rather than searched for as a substring."""
    monkeypatch.setattr(store_module, "MAX_BYTES", 40)
    monkeypatch.setattr(store_module, "STATE_BYTES", 40)
    working = memory()
    refused = memory_that_cannot_write()
    unreadable = memory_that_cannot_read()

    said: list[str] = []
    with caplog.at_level("DEBUG"):
        for call in (
            # Decided before a connection: the caps, and the empty forms.
            lambda: working.add(MemoryScope.AGENT, "poet", SPOKEN, agent="poet"),
            lambda: working.update(
                MemoryScope.AGENT, "poet", ADDRESSED, SPOKEN, agent="poet"
            ),
            lambda: working.set_state(THREAD, "secret", SPOKEN, agent="poet"),
            lambda: working.add(MemoryScope.AGENT, "poet", "  ", agent="poet"),
            lambda: working.set_state(THREAD, "secret", "  ", agent="poet"),
            # Decided where the rows are: an id that names nothing this
            # caller may reach.
            lambda: working.update(
                MemoryScope.AGENT, "poet", ADDRESSED, "short", agent="poet"
            ),
            lambda: working.forget(
                MemoryScope.AGENT, "poet", ADDRESSED, THREAD, agent="poet"
            ),
            lambda: working.restore(MINE, THREAD, ADDRESSED, agent="poet"),
            # Decided by a database that refused, on every write path.
            lambda: refused.add(MemoryScope.AGENT, "poet", "a short fact", agent="poet"),
            lambda: refused.set_state(THREAD, "turn", "white to move", agent="poet"),
            lambda: refused.clear_state(THREAD, agent="poet"),
        ):
            with pytest.raises((ValueError, ConfigError)) as refusal:
                await call()
            said.append(str(refusal.value))
        # And the two reads, which refuse nobody and answer empty.
        assert unreadable.recall("poet", "aa:bb", SPOKEN) == ""
        assert unreadable.read_for_prompt("poet", "aa:bb", THREAD) == (
            store_module.NOTHING_REMEMBERED
        )

    # Every sentence is one this module declared, by equality.
    declared = {
        store_module.TOO_LONG,
        store_module.STATE_ENTRY_TOO_LONG,
        store_module.NOTHING_TO_REMEMBER,
        store_module.NOTHING_TO_SET,
        store_module.NO_FACT_TO_UPDATE,
        store_module.NO_FACT_TO_FORGET,
        store_module.NO_FACT_TO_RESTORE,
        store_module.UNWRITABLE,
    }
    assert set(said) <= declared
    assert len(said) == 11

    # And nothing of what was offered reaches a surface, whether the
    # refusal was decided here or by the database.
    for surface in (*said, both_formats(caplog)):
        assert SPOKEN not in surface
        assert str(ADDRESSED) not in surface
    for record in caplog.records:
        assert SPOKEN not in repr(record.__dict__)
        assert str(ADDRESSED) not in repr(record.__dict__)


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
