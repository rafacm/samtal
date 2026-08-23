"""What a suite writes into a store, and how it reads one back.

Three things on disk keep what a conversation produced: the capture
directory, the conversations database, and an agent's memory file. What
belongs here is the scaffolding around all three: the manifest each
kind of session is opened with, a store built where a test can reach it,
the audio a channel is filled with, a read through a second engine, and
the way a memory file is made unreadable on purpose.

Nothing here asserts and nothing here drives a session. A helper returns
a store, a payload or a list of rows, and the suite says what it expects.

The two manifests are called `CAPTURE_MANIFEST` and
`CONVERSATIONS_MANIFEST` because they were both called `MANIFEST` where
they came from and they are not the same shape: one is what a capture
writes beside its WAVs, the other is the session row a conversation
opens with. Both importing suites keep their own spelling by alias.
"""

import struct
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text

from vinga_server.capture import CAPTURE_RATE, CaptureStore
from vinga_server.config import entities
from vinga_server.config import store as config_store
from vinga_server.conversations.store import open_conversations
from vinga_server.tools.memory import MemoryStore

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


def rows(directory: Path, table: str, **where: Any) -> list[dict[str, Any]]:
    """Read through a second engine, which is what a reader beside a
    running writer is."""
    engine = open_conversations(directory)
    try:
        clause = " and ".join(f"{name} = :{name}" for name in where)
        query = f"select * from {table}" + (f" where {clause}" if where else "")
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(query), where).mappings()]
    finally:
        engine.dispose()


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


# --- an agent's memory file, made unreadable --------------------------


# Not a real credential, and shaped so a substring check for it cannot
# match by accident. Written into the corrupt file, where a handler that
# logged the file or the exception's message would carry it out.
STORED = "sk-test-3d7f10ba-never-a-real-credential"

CORRUPT = f"- {STORED}\n- \xff\xfe not utf-8 at all\n".encode("latin-1")


def corrupt(store: MemoryStore, agent: str) -> None:
    path = store.path_for(agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(CORRUPT)
