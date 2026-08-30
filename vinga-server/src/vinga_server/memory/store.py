"""The store behind an agent's remembered facts: one chain, two engines.

What this module hides from its callers is the whole of the storage: a
schema of its own and the migrations that build it, an advisory lock
that serializes writers across processes, a write engine that takes
that lock before it reads anything, a separate read-only engine so a
reply's memory read never waits on a writer, and a connection whose
failures are classified rather than quoted. A caller asks for a store
and disposes it; it never learns which schema, which lock key, which
isolation level, or what a psycopg failure looks like.

Nothing here reads or writes a row yet, and that is deliberate rather
than unfinished. The storage move (#314) lands the chain first so that
each milestone leaves a releasable `main`: what ships now is an empty,
migrated, unread schema, which is exactly the state the conversation
record already ships in when recording is off. `read(agent)` and
`remember(agent, fact)` arrive with the cutover, on this store and this
class, with the caps applied inside the write transaction; until then
the file-backed store in `tools/memory.py` is still the one the
composition builds, and nothing forwards between the two.

The chain is declared here rather than beside `db.open_at`, for the
reason the conversation store's is: which schema a store lives in is a
fact of that store, and the schema name is read off the metadata that
declares it.
"""

from pathlib import Path

from sqlalchemy import Engine

from vinga_server.config.models import DatabaseConfig
from vinga_server.db import StoreChain, advisory_key, open_at, read_engine
from vinga_server.memory import schema

# This store's own chain: the schema its table and its Alembic version
# table live in, its migrations, and the advisory key its writers
# serialize on.
MEMORY_CHAIN = StoreChain(
    schema=schema.SCHEMA,
    migrations=Path(__file__).resolve().parent / "migrations",
    # The third key in this application's half of the advisory lock
    # space. Distinct from the other two on purpose: a `remember` is a
    # tool call a human asked for mid-conversation, and sharing the
    # domain chain's key would make it wait behind an `apply`
    # transaction that is deliberately unbounded, while sharing the
    # record's would make it wait behind a turn being written.
    lock_key=advisory_key(3),
)


class MemoryStore:
    """One database's worth of remembered facts.

    Built through `open_memory`, which is where the migration happens,
    so a database the server cannot reach fails the boot rather than the
    first thing an agent is asked to remember.

    Two engines and not one, which is the property the read path is
    bought with. The write engine takes the chain's advisory lock on
    every `begin`, so two writers through independent connections
    serialize whole; the read engine takes nothing, is repeatable-read
    and read-only, and therefore answers while another connection holds
    the chain lock. That is what preserves the file store's property
    that reading is cheap and never waits.
    """

    def __init__(self, engine: Engine, reader: Engine) -> None:
        self._engine = engine
        self._reader = reader

    def close(self) -> None:
        """Let go of both connection pools.

        Registered on the application's exit stack the moment the store
        is opened, so a boot that fails later unwinds through it. Safe
        to call twice: disposing a disposed engine replaces a pool that
        has no connections in it.
        """
        self._engine.dispose()
        self._reader.dispose()


def open_memory(settings: DatabaseConfig) -> MemoryStore:
    """Open and migrate the memory schema, and answer the store on it.

    Always, and not behind any switch. The schema is migrated at every
    boot the way the record schema is, because migrating creates an
    empty table and an empty table is not a memory; a deployment that
    stored nothing has nothing in it and reads exactly as an empty file
    read.

    Every failure is a `ConfigError` carrying one of the fixed sentences
    `db` owns, including the one for a role that may not create the
    schema, which is what an existing least-privilege deployment meets
    if it starts this image before rerunning
    `deploy/postgres-init.sql`.
    """
    engine = open_at(settings, MEMORY_CHAIN)
    # The read engine connects to nothing here (SQLAlchemy connects
    # lazily), so what is guarded is the narrow case of a URL the write
    # engine accepted and this one did not. Bare `raise`, because what
    # would travel is already one of `db`'s own sanitized refusals and
    # re-wrapping it would add a chain rather than remove one.
    try:
        reader = read_engine(settings)
    except Exception:
        engine.dispose()
        raise
    return MemoryStore(engine, reader)


__all__ = [
    "MEMORY_CHAIN",
    "MemoryStore",
    "open_memory",
]
