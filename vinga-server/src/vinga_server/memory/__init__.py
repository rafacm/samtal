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
  `2001_agent_memory`, and the forward migration `2002_memory_scopes`.
- `store.py`: `MEMORY_CHAIN`, the two engines, `open_memory`, and the
  sentences a caller speaks, from `read_for_prompt` down to the purge.

Memory is on whenever the server runs (#314). There is no section to
configure and no store to build: the schema is migrated at every boot,
an agent that has been told nothing reads as the empty string, and the
`remember` tool is always offered. Per-agent control over what an agent
may remember is #83's, and so is everything above the store: the tools,
the injected blocks, the lifecycle coupling and the operator surface.

Nobody but the server reads this schema. `deploy/postgres-init.sql`
creates it with `AUTHORIZATION` to the server role and grants the
read-only analyst role nothing on it, because the operator read surface
for remembered facts is #83's deliberate design (addressed by scope,
over the API) and granting the raw tables early would freeze a contract
#83 is about to reshape.

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
