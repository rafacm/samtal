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
- `store.py`: `MEMORY_CHAIN`, the two engines, `open_memory`, and the
  two sentences a caller speaks, `read(agent)` and
  `remember(agent, fact)`.

Memory is on whenever the server runs (#314). There is no section to
configure and no store to build: the schema is migrated at every boot,
an agent that has been told nothing reads as the empty string, and the
`remember` tool is always offered. Per-agent control over what an agent
may remember is #83's, and so are scopes, update, delete and the
operator read surface.

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
