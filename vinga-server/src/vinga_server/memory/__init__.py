"""Agent memory: the facts an agent was asked to remember.

The `memory` schema sits beside `domain` and `record` in the database
`server.database` names, and holds one table, `facts`. A store that
owns a schema is a domain concept rather than a tool helper, which is
why this is a package of its own: the `remember` builtin keeps calling
the store it is handed, and the store keeps its own migrations beside
the tables they shape.

The pieces, in the order they are met:

- `schema.py`: the `facts` table and the index the ordered read walks.
- `migrations/`: the chain's Alembic environment and its baseline,
  `2001_agent_memory`.
- `store.py`: `MEMORY_CHAIN`, the two engines, and `open_memory`.

What this package holds today is the chain and the engines, and nothing
reads or writes a row: the storage move (#314) lands the schema first,
so that a milestone leaves a releasable `main` with an empty, migrated,
unread schema, exactly the state the conversation record already ships
in when recording is off. `read` and `remember` arrive with the cutover,
on the store this package already owns.

Nobody but the server reads this schema. `deploy/postgres-init.sql`
creates it with `AUTHORIZATION` to the server role and grants the
read-only analyst role nothing on it, because the operator read surface
for remembered facts is #83's deliberate design (addressed by scope,
over the API) and granting the raw tables early would freeze a contract
#83 is about to reshape.
"""

from vinga_server.memory.store import MEMORY_CHAIN, MemoryStore, open_memory

__all__ = [
    "MEMORY_CHAIN",
    "MemoryStore",
    "open_memory",
]
