"""What a suite writes into a store, and how it reads one back.

Two places keep what a conversation produced: the capture directory and
the conversation record's schema, with the memory schema beside them.
What belongs here is the scaffolding around all three: the
manifest each kind of session is opened with, a store built where a
test can reach it, the audio a channel is filled with, a read through a
second engine, the second writer four suites need in order to prove the
retryable refusal, the thread store a session resumes through written
down as two dictionaries, and the memory stores whose two engines are
pointed at a database that is not there on purpose.

Nothing here asserts and nothing here drives a session. A helper returns
a store, a payload or a list of rows, and the suite says what it expects.

The two manifests are called `CAPTURE_MANIFEST` and
`CONVERSATIONS_MANIFEST` because they were both called `MANIFEST` where
they came from and they are not the same shape: one is what a capture
writes beside its WAVs, the other is the session row a conversation
opens with. Both importing suites keep their own spelling by alias.
"""

import contextlib
import struct
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import psycopg
import pytest
from pydantic import BaseModel
from sqlalchemy import text

from vinga_server import db as db_module
from vinga_server.capture import CAPTURE_RATE, CaptureStore
from vinga_server.config import entities
from vinga_server.config import store as config_store
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations import threads
from vinga_server.conversations.records import StoredTurn
from vinga_server.conversations.store import open_conversations
from vinga_server.db import (
    DOMAIN_CHAIN,
    StoreChain,
    connection_url,
    read_engine,
    write_engine,
)
from vinga_server.memory import MEMORY_CHAIN, MemoryStore, open_memory

# --- a second writer, holding the lock ---------------------------------

# Short enough that a blocked writer gives up inside a test run, and
# long enough that an unblocked one never sees it.
SHORT_LOCK_MS = 200


@contextlib.contextmanager
def holding_the_write_lock(
    monkeypatch: pytest.MonkeyPatch, chain: StoreChain = DOMAIN_CHAIN
) -> Iterator[None]:
    """A second process's write transaction, as far as the engine under
    test can tell: one connection holding the chain's advisory lock in a
    transaction that does not let go.

    The SQLite-era shape of this was a second `sqlite3` connection in
    `BEGIN IMMEDIATE`, repeated in four suites. What it proved then is
    what it proves now, and the mechanism is the only thing that moved:
    a writer takes the chain's `pg_advisory_xact_lock` before it reads,
    so a second one waits out `lock_timeout` and refuses retryably.

    The constant is shortened first, and that ordering is the scenario
    rather than a convenience: the timeout rides on a connection's
    startup options, so an engine opened under the packaged ten seconds
    keeps them for the life of its pool. Open the engine, or the
    application that owns one, only after entering this.
    """
    monkeypatch.setattr(db_module, "LOCK_TIMEOUT_MS", SHORT_LOCK_MS)
    yield


@contextlib.contextmanager
def the_lock_held(chain: StoreChain = DOMAIN_CHAIN) -> Iterator[None]:
    """The other half: the lock actually taken, once whatever is under
    test has its engine.

    Separate from the constant above because the two happen at different
    moments in every one of these scenarios, and a helper that did both
    would have to be entered where neither belongs.
    """
    url = connection_url(DatabaseConfig()).set(drivername="postgresql")
    holder = psycopg.connect(url.render_as_string(hide_password=False))
    try:
        holder.execute("select pg_advisory_xact_lock(%s)", (chain.lock_key,))
        yield
    finally:
        holder.rollback()
        holder.close()

# --- the capture directory --------------------------------------------


CAPTURE_MANIFEST = {"session": "abc", "barge_in": {"enabled": True}}


def tone(ms: int, value: int = 8000) -> bytes:
    """Mono s16le of a constant value, so a channel can be told apart
    from silence by looking at one sample."""
    return struct.pack("<h", value) * (CAPTURE_RATE * ms // 1000)


def store(tmp_path: Path, **kwargs: float) -> CaptureStore:
    options: dict[str, float] = {
        "max_session_s": 900.0,
        "max_total_mb": 2000.0,
        "min_free_mb": 0.0,
    }
    options.update(kwargs)
    return CaptureStore(tmp_path / "captures", **options)  # type: ignore[arg-type]


# --- the conversations database ---------------------------------------


CONVERSATIONS_MANIFEST: dict[str, Any] = {
    "started_at": "2026-08-15T10:00:00+00:00",
    "server": {"version": "0.1.0", "revision": "abc1234"},
    "device": {"mac": "aa:bb:cc:dd:ee:ff", "client": "test"},
    "protocol": "1",
    "agent": "sam",
    "agents": ["sam"],
    "providers": {"llm": {"name": "claude", "type": "anthropic"}},
}


def rows(table: str, **where: Any) -> list[dict[str, Any]]:
    """Read through a second engine, which is what a reader beside a
    running writer is."""
    engine = open_conversations(DatabaseConfig())
    try:
        clause = " and ".join(f"{name} = :{name}" for name in where)
        query = f"select * from record.{table}" + (
            f" where {clause}" if where else ""
        )
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(query), where).mappings()]
    finally:
        engine.dispose()


# --- the threads a session can find and resume ------------------------


class StoredThreads:
    """The thread store as a test writes it down, standing in for the
    read seam a runtime resumes through.

    The seam is two questions and neither of them raises, so a double is
    two dictionaries and a failure to answer with instead. Written here
    rather than in each suite because three of them ask the same two
    questions, and because what a test wants to say is what the store
    holds, not how it is asked.

    `asked` records the searches, which is how a suite proves that a
    refusal it expected happened before the store was ever consulted.
    """

    def __init__(
        self,
        found: dict[str, Any] | None = None,
        held: dict[str, Any] | None = None,
        failure: Any = None,
    ) -> None:
        self.found = found or {}
        self.held = held or {}
        self.failure = failure
        self.asked: list[tuple[str, str]] = []
        self.read: list[str] = []

    def candidates(self, agent: str, description: str) -> Any:
        self.asked.append((agent, description))
        if self.failure is not None:
            return self.failure
        return self.found.get(agent, threads.Candidates(matched=False))

    def backlog(self, conversation: str) -> Any:
        self.read.append(conversation)
        if self.failure is not None:
            return self.failure
        return self.held.get(conversation)


def a_candidate(
    conversation: str,
    title: str = "the andromeda galaxy",
    last_active_at: str = "2026-08-20T10:00:00+00:00",
    excerpt: str | None = None,
    score: int = 1,
) -> Any:
    return threads.Candidate(
        conversation=conversation,
        title=title,
        last_active_at=last_active_at,
        excerpt=title if excerpt is None else excerpt,
        score=score,
    )


def a_backlog(
    conversation: str,
    agent: str = "poet",
    said: Sequence[tuple[str, str]] = (),
    incomplete: bool = False,
    milestone: Any = None,
    first_id: int = 1,
) -> Any:
    """One thread as the store hands it back.

    The turns are numbered from `first_id`, because a recap records the
    range of ids it read and a suite about that range has to be able to
    say which ids it means. `milestone` is the checkpoint standing in
    front of them, and the pair is written the way the store answers it:
    where there is a checkpoint, `said` is what came after its coverage.
    """
    return threads.Backlog(
        conversation=conversation,
        agent=agent,
        incomplete=incomplete,
        milestone=milestone,
        turns=tuple(
            StoredTurn(id=first_id + index, heard=heard, reply=reply)
            for index, (heard, reply) in enumerate(said)
        ),
    )


def a_milestone(
    text: str = "we talked about galaxies",
    id: int = 7,
    from_turn: int = 1,
    after_turn: int = 4,
    parent: int | None = None,
) -> Any:
    """One recap checkpoint as a thread carries it."""
    return threads.Milestone(
        id=id,
        conversation="",
        from_turn=from_turn,
        after_turn=after_turn,
        parent=parent,
        created_at="2026-08-20T10:00:00+00:00",
        text=text,
    )


# --- what a database actually holds -----------------------------------


def stored_rows(store: Any, selection: Any) -> list[dict[str, Any]]:
    """What the database actually holds, row by row.

    White-box, deliberately, and the database is the reason. A public
    read answers what this build makes of a row, which cannot tell a
    value kept verbatim from one this build encodes on the way in and
    decodes on the way out. The stored form is a compatibility surface
    of its own: a migration, a backup restored under another build, and
    the upgrade path this project supports from the first revision
    forward all read the column rather than the accessor. So a suite
    that promises a field is kept byte for byte has to look at the
    bytes.
    """
    with store._engine.connect() as connection:
        return [dict(row) for row in connection.execute(selection).mappings()]


def stored_row(store: Any, selection: Any) -> dict[str, Any]:
    """The one row a query answers, as the database holds it."""
    (row,) = stored_rows(store, selection)
    return row


def planted(store: Any, *statements: Any) -> None:
    """Write rows the way an older, or a broken, build wrote them.

    White-box, deliberately: what these suites are about is a row no
    current write can produce, because the current write is the one
    whose output is not the thing under test. A row from before a
    normalization landed, and a row holding a value today's writer would
    have refused, are both rows a running deployment can have, arriving
    from another build or from a file something else corrupted, and
    reading one safely is the promise being kept.

    Since the entity tables became one body per row (#243) a plant is one
    of two things, and which one is a decision the test makes. A plant of
    lawful state, which the repository could have written itself, goes
    through `body` below so the row is the model's own dump. A plant of a
    malformed or old-shaped row is a hand-written string, deliberately:
    what such a test pins is the reader, and a dump would have to be
    talked into producing the very shape the reader is being asked to
    survive.
    """
    with store._engine.begin() as connection:
        for statement in statements:
            connection.execute(statement)


def body(entry: BaseModel) -> str:
    """One entity's row body, produced by the repository's own writer.

    Delegates rather than restating, and the delegation is the point.
    `config.store._to_row` owns the `exclude_unset` decision, which is
    load-bearing (`McpServerConfig` refuses a field of the other
    transport that is present in `model_fields_set`), and a copy of that
    decision here would be a second home for the one rule the round-trip
    test in `test_config_bodies.py` claims to pin. With a copy, removing
    `exclude_unset` from the shipped writer left that family green.

    The reach past the underscore is deliberate and is the smaller of two
    evils: the alternative is that the pair the suites say they pin is
    not the pair that ships. The descriptor is found by the entry's model,
    which is what `_to_row` keys on and the one thing a caller has in
    hand; an exact match rather than an `isinstance` one, because
    `AgentConfig` is an `AgentDefaults` and the two are different kinds.
    """
    descriptor = next(
        (kind for kind in entities.ENTITIES if kind.model is type(entry)), None
    )
    assert descriptor is not None, f"{type(entry).__name__} is not an entity model"
    return str(config_store._to_row(descriptor, entry)["body"])


# --- agent memory, reachable and unreachable --------------------------


# Not a real credential, and shaped so a substring check for it cannot
# match by accident. Planted in the database password and in the whole
# connection URL, which is where a psycopg failure would quote it: the
# driver's message names the DSN it tried, and a DSN carries a password
# in its authority and can carry another in its query.
STORED = "sk-test-3d7f10ba-never-a-real-credential"

# A port on loopback nothing listens on, which is the cheapest genuine
# connection failure there is: the kernel refuses it immediately, so a
# suite about a database that is not there does not wait out a timeout.
NO_BACKEND_PORT = 1


# The lane's own store, opened once per worker process.
#
# Through `open_memory`, migration and all, rather than by handing
# `MemoryStore` two engines: what a suite drives should be what a
# deployment runs. Once, because that call is an Alembic round trip and
# every session this lane builds asks for a store; the truncation
# between tests empties the table underneath it, which is what a store
# holding nothing but two pools does not notice.
_LANE_MEMORY: MemoryStore | None = None


def memory() -> MemoryStore:
    """The lane's memory store, opened the way a boot opens it."""
    global _LANE_MEMORY
    if _LANE_MEMORY is None:
        _LANE_MEMORY = open_memory(DatabaseConfig())
    return _LANE_MEMORY


def nowhere() -> DatabaseConfig:
    """Settings naming a backend that is not there."""
    return DatabaseConfig(port=NO_BACKEND_PORT)


def memory_that_cannot_read() -> MemoryStore:
    """A store whose reads fail and whose writes do not.

    The two engines are the whole difference between the two failure
    paths, so a suite that wants one of them without the other builds
    the store through its own constructor rather than reaching into an
    opened one. Reads take no advisory lock, so a held lock could never
    make one fail; this is the genuine failure that can.
    """
    return MemoryStore(
        write_engine(DatabaseConfig(), MEMORY_CHAIN), read_engine(nowhere())
    )


def memory_that_cannot_write() -> MemoryStore:
    """The mirror: writes fail on a backend that is not there, reads
    answer from the lane's own database."""
    return MemoryStore(
        write_engine(nowhere(), MEMORY_CHAIN), read_engine(DatabaseConfig())
    )


@contextlib.contextmanager
def a_planted_credential(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every door onto the database pointed at nothing, with a
    credential-shaped value in the password and in the URL.

    Both variables, because they are the two places a connection's
    secret lives and `VINGA_DB_URL` overrides the other four at once. A
    store opened inside this fails at every call, which is what a
    no-leak sentinel wants: the failure is the thing that would carry
    the value out.
    """
    monkeypatch.setenv(db_module.PASSWORD_ENV, STORED)
    monkeypatch.setenv(
        db_module.URL_ENV,
        f"postgresql+psycopg://vinga:{STORED}@127.0.0.1:{NO_BACKEND_PORT}/vinga",
    )
    yield
