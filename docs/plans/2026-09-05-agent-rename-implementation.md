# One name, rewritten everywhere it is still read: implementation

The companion to [`2026-09-05-agent-rename.md`](2026-09-05-agent-rename.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations says
so explicitly.

## M1: one transaction, three schemas

PR TBD.

### What landed

In the order the commits tell it: the type and its status, the memory
half, the record half, the verb that spans them, and the suite that pins
the whole of it before anything can reach it.

- **`config/loader.py` gains `AgentRenameConflictError`,** beside
  `DeviceAlreadyBoundError`, which is the same kind of fact about the
  world rather than about the request. `config/api.py`'s
  `REFUSAL_STATUS` gains its 409 row in the same commit, so the type and
  the code it means never exist apart, and the existing ordinal comments
  in that table were renumbered by one where the new row landed among
  them. One class for all three destination states, because what a
  caller does about them is the same.
- **`memory/store.py` gains `rename_owner(connection, scope, old, new)
  -> int`,** with three fixed sentences beside `PURGE_FAILED` and
  `PURGE_BUSY`: the occupied destination, the storage refusal and the
  retryable one. It takes the memory chain's advisory lock as its first
  statement, checks whether anything is filed under the destination,
  raises the typed conflict if so, and updates otherwise. Held rows move
  with the active ones, because a held fact carries `owner` like any
  other and a restore after the rename has to find it.
- **`conversations/store.py` gains `rename_agent(connection, old, new)
  -> int`,** the same shape with the record chain's lock and its own
  three sentences, addressing `conversations.agent` and nothing else.
  The module also gains `Connection`, `update` and `take_the_chain_lock`
  to its imports; nothing else in it moved.
- **`config/store.py` gains `ConfigStore.rename_agent(old, new) ->
  Renamed`,** which runs the four phases every write here runs and then
  crosses into the two foreign schemas in ascending key order. Beside it
  landed `Renamed` (the two names, the MACs whose bindings moved,
  whether the default agent moved, and the two row counts), `_Renaming`
  and `_stage_rename` (the staging pass, which moves the agent entry,
  rewrites every position of every binding that names it and moves the
  default agent, all in the candidate state `check_references` is then
  asked about once), `_rename_agent_row` (an UPDATE of the primary key
  rather than a delete and an insert, so the body and any column this
  table gains later travel with the row), and the module's two own
  refusals, `AGENT_EXISTS` and `SAME_NAME`.
- **`tests/unit/test_agent_rename.py`,** 23 cases: the sentinel sweep
  and its converse, the result type, the operator-vocabulary assertions,
  the held fact, the inventory pin, the seven refusals one case per
  state, the mapping pin, atomicity from the last statement,
  reversibility as a byte-identical round trip and again with a stranger
  present, and one competing-write pin per store that checks a
  destination.
- **`tests/unit/test_memory_store.py`** gains five direct cases for
  `rename_owner`: both areas move, the other scope is left alone, an
  occupied destination is refused and moves nothing, an owner holding
  nothing moves nothing, and a rename on a caller's connection belongs
  to that caller's transaction.
- **`tests/unit/test_memory_lifecycle.py`** gains the rename as the
  third path of the lock-order walk, `[1, 2, 3]`, and its `keys_taken`
  fixture now patches the record store's own reference to
  `take_the_chain_lock` alongside `db`'s and the memory store's.

### Deviations from the plan

Three, all of them placement or wording rather than behavior, and each
forced by something the plan itself states elsewhere.

1. **The collision sentences do not live in `config/store.py`.** The
   closed-set section says "each is one fixed sentence in
   `config/store.py` beside the sentences the other writes use", which
   was written before the sol review's finding 4 moved each destination
   check into the store that owns the rows. After that amendment the
   memory and record sentences are raised inside `memory/store.py` and
   `conversations/store.py`, and they cannot be read from
   `config/store.py`: that module imports both stores, so an import back
   would close a cycle the plan's own import note rules out. So each
   store owns the sentence for the destination it checks, and
   `config/store.py` owns the two states it can be in itself, the agent
   that already exists and the name that is already this agent's. The
   plan's later text ("raising the typed conflict through each store's
   classifier") is what was implemented.

2. **`rename_owner` sits beside `purge`, not beside `erase_facts`.** The
   module layout says "beside `erase_facts` which it mirrors", and the
   same bullet says "one statement under the chain's lock", which finding
   4 also superseded: it is two statements, and its shape is `purge`'s
   rather than `erase_facts`'s. `erase_facts` lives under the operator's
   door block, whose header states that its functions run on a
   connection a route opened for one request; this one runs inside
   another store's transaction. So it landed immediately after `purge`,
   with its docstring saying that its signature follows `erase_facts`'s.

3. **The old name is stripped but not checked for addressability.** The
   plan's refusal table applies `_identifier`/`_check_addressable` to
   the new name and says nothing about the old one, and the closed set
   has no state for an old name that is malformed. Running the
   addressability check on the source would have added an eighth state
   and would have made a legacy row less reachable than it is today, so
   the source is stripped (because every path here strips first) and
   then looked up; a source that is absent, blank included, meets
   `NO_SUCH_AGENT`.

### Discoveries

- **The domain half needs no delete-and-insert.** `db.schema.agents` is
  `(name, body)` and carries no `secrets` column, so the row moves under
  an UPDATE of its primary key. That is strictly better than the
  rewrite the plan's phases imply: it preserves the body byte for byte
  and it preserves any column the table gains later, which is what makes
  the reversibility pin's byte-identical claim hold without listing
  columns anywhere.

- **The sweep's recorded set is five pairs, and one of them is only
  there because the fixture puts it there.** `sessions.agent`,
  `sessions.agents`, `turns.agent`, `turns.legs` and `events.fields`.
  The last two exist because the fixture writes a split reply's legs and
  an event carrying the agent in its fields; nothing in a plain recorded
  turn puts an agent name inside JSON, so a fixture without them would
  have covered two fewer places while looking identical. That is the
  bound the plan states, met in practice on the first fixture.

- **A competing-write pin has to assert the queueing, not the final
  state.** With the chain lock removed from `rename_owner`, the
  competing writer commits between the check and the update and the
  final rows are the same as with the lock: the destination ends up
  holding both the moved rows and the intruder either way. What differs
  is only whether the second writer was made to wait, so each pin asks
  `pg_locks` whether an ungranted waiter is really parked on the chain's
  key while the rename is between its two statements. Verified to bite:
  removing `take_the_chain_lock` from `rename_owner` fails the memory
  pin on `writer.queued`, removing it from the record's `rename_agent`
  fails the record pin the same way, and both were restored.

- **The sweep bites in both directions.** Verified by mutation:
  replacing the memory crossing with `facts = 0` leaves
  `("facts", "owner")` carrying the sentinel and fails the equality;
  adding an UPDATE of `turns.agent` to the record half takes
  `("turns", "agent")` out of the answer and fails it the other way.
  Both mutations were reverted.

### Open questions the plan left, and what M1 answers

None. M1 carries no open question of its own; the plan's own questions
were resolved before it, and the in-flight protocol, the route, the
boundary sentence and the verb are M2 to M4.

### Verification

- `uv run ruff check .`: clean.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: 5754 passed,
  19 skipped, plus the command-spellings manifest, which stales on every
  change to a tracked file and is regenerated in the last commit of the
  milestone.
- `uv run pytest tests/integration -q`: 243 passed.
- `scripts/check_doc_links.py .`: clean.
- The generated-document drift checks: clean. M1 touches no generated
  document; the two migrations and the regenerated references are M3's.
