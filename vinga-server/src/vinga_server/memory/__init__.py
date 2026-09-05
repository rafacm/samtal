"""Agent memory: what an agent remembers, and where it belongs.

The `memory` schema sits beside `domain` and `record` in the database
`server.database` names, and holds two tables: `facts`, an ordered
id-addressed list per owner within a scope, and `state`, one keyed
ledger per conversation. A store that owns a schema is a domain concept
rather than a tool helper, which is why this is a package of its own:
the builtins keep calling the store they are handed, and the store keeps
its own migrations beside the tables they shape.

The pieces, in the order they are met:

- `scopes.py`: `MemoryScope`, the vocabulary everything else derives
  from, importing nothing but the standard library so that the client
  half can read it.
- `schema.py`: the `facts` table with its scope check, its held pair
  and its two indexes, and the `state` table.
- `migrations/`: the chain's Alembic environment, its baseline
  `2001_agent_memory`, and the forward migrations `2002_memory_scopes`
  and `2003_rename_moves_memory`.
- `store.py`: `MEMORY_CHAIN`, the two engines, `open_memory`, and the
  sentences a caller speaks, from `read_for_prompt` down to the purge.

Memory is available whenever the server runs (#314). There is no store
to build and nothing to switch on: the schema is migrated at every boot
and an agent that has been told nothing reads as the empty string.
Whether a particular agent may reach any of it is that agent's own
`memory` section (#83), which is on unless it says otherwise; an agent
switched off is offered none of the memory tools and is read none of
the scopes, and nothing under this package knows about it, because the
policy is resolved once per reply where the tools are offered.

Nobody but the server reads this schema. `deploy/postgres-init.sql`
creates it with `AUTHORIZATION` to the server role and grants the
read-only analyst role nothing on it, because what an operator reads is
the addressed surface over the API (`/api/memory`, `vinga memory`)
rather than the raw tables.

Deliberately without the store, which is the same discipline
`vinga_server.config` keeps about its boot path: importing this package
pulls in neither the database driver nor the migrations, so a module
that wants nothing but the tables gets nothing but the tables. It is
load-bearing rather than tidy. The scope vocabulary is declared once, in
`scopes.py`, and the events catalog derives its `scope` field from it;
the store imports the events package, so a package that imported the
store here would make that derivation a cycle, and it would put a
database driver into an install that has none. `from
vinga_server.memory.store import ...` is what a caller that wants the
store writes.
"""
