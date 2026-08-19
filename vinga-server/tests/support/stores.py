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

from sqlalchemy import text

from vinga_server.capture import CAPTURE_RATE, CaptureStore
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
