# One name, rewritten everywhere it is still read: implementation

The companion to [`2026-09-05-agent-rename.md`](2026-09-05-agent-rename.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations says
so explicitly.

## M1: one transaction, three schemas

PR #415.

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
- **`tests/unit/test_agent_rename.py`,** 24 cases: the sentinel sweep
  and its converse, the result type, the operator-vocabulary assertions,
  the held fact, the inventory pin, the seven refusals one case per
  state, the mapping pin, atomicity from the last statement,
  reversibility as a byte-identical round trip and again with a stranger
  present, one competing-write pin per store that checks a destination,
  and the statement-order pin those three cannot make (see the review
  round below).
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
  `pg_locks` whether that writer is really parked on the chain's key
  while the rename is between its two statements. Verified to bite:
  removing `take_the_chain_lock` from `rename_owner` fails the memory
  pin on `writer.queued`, removing it from the record's `rename_agent`
  fails the record pin the same way, and both were restored.

- **The sweep bites in both directions.** Verified by mutation:
  replacing the memory crossing with `facts = 0` leaves
  `("facts", "owner")` carrying the sentinel and fails the equality;
  adding an UPDATE of `turns.agent` to the record half takes
  `("turns", "agent")` out of the answer and fails it the other way.
  Both mutations were reverted.

- **A `begin` listener cannot see a connection before it blocks.**
  Naming the competing writer's backend needs its pid recorded before
  the chain's lock is asked for, and a second `begin` handler registered
  with `insert=True` did not run in front of the write engine's own: the
  pid arrived only once the lock had been granted, which is exactly when
  a blocked writer stops being blocked. The `connect` event is strictly
  earlier than any transaction and answers, which is what the pins use.

### The review round

Backend codex, model `gpt-5.6-sol`, against PR #415: 2 P2 and 1 P3,
mergeable after the fixes. All three are fixed on the branch, in one
commit each.

1. **P2: the competing-write pins did not prove the lock behavior they
   claimed.** Two holes, and both were real. The waiter predicate
   counted ANY ungranted waiter on the chain key, and this lane runs its
   files across worker processes against one instance, so a suite next
   door writing to the same chain would have satisfied it; and each pin
   releases its writer at the statement that WRITES, which is after the
   destination check, so all three would still have passed with a
   chain's lock taken between the check and the update rather than
   before both. Fixed as prescribed: each competitor now runs on an
   engine of its own whose backend pid is recorded on `connect`, and the
   predicate requires that exact pid; and a new case asserts the
   execution order directly, off `before_cursor_execute`, each chain's
   lock before the first statement naming that chain's table, with the
   ascending order read from the same list. Verified by moving
   `take_the_chain_lock` below the destination SELECT in both helpers:
   the three competing-write pins stayed green, which is the finding
   reproduced, and the order assertion failed on each half in turn
   (`assert 10 < 9` for the record chain, `assert 13 < 12` for the
   memory chain). Both mutations were reverted, and the earlier
   lock-removal mutation was re-run against the pid-precise predicate to
   confirm it still bites.

2. **P2: the memory collision named the listing that would not answer.**
   `memory list agent` is who is remembering anything and
   `memory list agent <name>` is what one of them remembers, the same
   words one level apart, and the refusal told a caller to read what is
   stored under the destination name with the first. The sentence now
   names the second. The `<name>` is a placeholder rather than a value:
   the no-echo rule the sentence itself states is unchanged, and the
   census manifest moved with it through its own module.

3. **P3: the two spellings of the PR number disagreed.** The plan's
   milestone tick said PR #415 and this document's header still said
   PR TBD. It says #415.

### Open questions the plan left, and what M1 answers

None. M1 carries no open question of its own; the plan's own questions
were resolved before it, and the in-flight protocol, the route, the
boundary sentence and the verb are M2 to M4.

### Verification

Re-run after the review round's fixes, which is where these numbers are
from.

- `uv run ruff check .`: clean.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: 5756 passed,
  19 skipped. The command-spellings manifest stales on every change to a
  tracked file and is regenerated through its own module.
- `uv run pytest tests/integration -q`: 243 passed.
- `scripts/check_doc_links.py .`: 206 files, 0 failures.
- The generated-document drift checks: all seven current, none
  regenerated. M1 touches no generated document; the two migrations and
  the regenerated references are M3's.

## M2: the order that covers the sessions in flight

PR #417.

### What landed

In the order the commits tell it: the writer's half, the publication
that reaches it, and the suite that arranges every interleaving it
claims.

- **`conversations/store.py` gains `renamed(old, new)`,** `erased()`'s
  sibling: it reaches the same register of writers with a different
  fact, is called after the renaming transaction has committed and
  inside `erasure_order()`, and skips a no-op rename. The comment above
  `_erasure_order` gains the rename as its second holder and says what
  the property both holders need really is, which is that every store
  change a live writer must observe atomically is published under it;
  `erasure_order()`'s own docstring now names both pairings. The lock
  keeps the erasure's name, per the plan.
- **`ConversationStore` gains the translation.** One map per recording
  session, `_renames`, guarded by the lock the producer state is guarded
  by, because a publication writes it from a request thread and the
  writer reads it inside its durable transaction. `open_session`
  seeds the map from what this session's world has not heard (see the
  review round below); `translate(old, new)` is `forget`'s sibling and
  marks every session live at that instant; both fold a rename in
  through `_compose`, which is the one rule for both; `_retire` drops a
  session's map where `_devices` is dropped, at the Close marker and at
  the tombstone.
- **`Generations` keeps what each world has not heard,** and hears it
  through `renames_announced_to`, the rename's own listener register
  beside the erasure's, wired by the composition root. A world is
  stamped with its place in that list when it is installed and loses it
  when it is disposed of. `DeviceSession._start_recording` hands the
  store a thunk over its own generation, which the store calls at the
  instant it registers the session.
- **The resolution sits at the durable-write boundary.** `_write` reads
  the session's translations once for the whole marker (`_naming`) and
  resolves each turn's agent once, handing that one value to
  `_turn_row`, which now takes it as an argument, and to the `Landing`.
  `_leg` resolves its own agent through the same map. `_session_row` is
  untouched.
- **`config/store.py`'s `rename_agent` enters the order before it opens
  its transaction and publishes after the commit, still inside it.** Two
  lines and a docstring paragraph; nothing else about the write moved.
- **`tests/unit/test_agent_rename_in_flight.py`,** thirteen cases: the
  two defects with the writer parked and the rename committing in front
  of it, the ordering lock's own claim, the row read back whole, a leg
  naming a second renamed agent, an agent nothing renamed, a chain of
  renames, a batch queued behind a close, the freed name given to a new
  agent and that agent renamed in its turn, and the three the review
  round added: a rename landing between a served session's world and its
  registration, a session opened after a rename and before the apply,
  and a world installed after a rename having nothing to translate.
- **`tests/support/sessions.py`** grows two optional parameters and one
  helper, all additive: `served` and `open_session` take the holder for
  the suites that publish something to it, and `bound_to_its_world`
  waits for the capture the way `handshaken` waits for the handshake, so
  a test can hold a session in the window between them.

### Deviations from the plan

Two, both narrower than the sentence they refine, and one open question
the plan's own test list asks for that the code cannot answer.

1. **A leg is resolved on its own account, not with the turn's one
   value.** The plan says the resolution is "one lookup per turn"
   handed to `_turn_row`, the legs and the landing. Applied literally to
   a leg naming a DIFFERENT agent, that would file a handover leg under
   the turn's agent, which is a worse row than the one the amendment
   exists to prevent. So the turn's own agent is resolved once and
   shared with the landing, which is the whole of the P1 amendment, and
   each leg is resolved through the same map: a leg equal to the turn's
   agent therefore yields exactly the same value, and a leg naming a
   second agent that was also renamed carries the name that agent has
   now. `test_a_leg_naming_a_second_renamed_agent_moves_with_it` is the
   case.

2. **The map is read once per marker rather than once per turn.** The
   plan says the writer resolves "once per turn at the durable-write
   boundary", which is about WHERE the resolution happens rather than
   how often the map is looked up. `_write` runs inside the durable
   transaction, which is inside `_erasure_order`, and a rename holds
   that lock across its commit AND its publication, so the map cannot
   change while the transaction is open. One read for the whole marker
   is therefore the same answer as one per turn, and it is the reading
   that cannot produce a marker whose rows disagree with each other.

### Discoveries

- **The retirement's only observable is the batch queued behind the
  close.** Retiring a session's translations when its close is ENQUEUED
  rather than when it COMMITS takes them from a turn still queued in
  front of the close, and that turn then lands under the old name and is
  refused; that is the case, and it is what the pin asserts. Retiring
  them LATER than the plan says, or never, has no behavioural
  consequence at all, because a session id is never reused: the cost is
  a leak rather than a defect. So the placement is pinned in the one
  direction a test can see, and the other direction is the plan's design
  bound rather than an assertion.

- **A landing-only translation really does pass the thread-owner
  assertions.** Verified by mutation rather than reasoned about: with
  `_turn_row` and `_leg` reading the record while the landing is
  translated, the two defect cases still pass, and what fails is the
  case that reads the row back. That is the terra P1 finding reproduced
  as a test result, and it is why the pin reads `turns.agent` and every
  `legs[].agent` rather than the thread's owner.

- **The publication reaches the writer register and not the listener
  register beside it.** The memory store attaches to the second one for
  erasures, so "not a second subscriber" is a single line in `renamed()`
  rather than a decision spread over the call site: the function names
  itself as the hook a change of that decision would attach to.

### The review round

Backend codex against PR #417: `gpt-5.6-sol` found 2 P1, and the
`gpt-5.6-terra` delta that followed the fixes found 1 P2. All three are
fixed on the branch, in one commit each, and the first of them moved a
design decision the plan had made.

1. **P1: a session could miss a rename before it was registered.** The
   writer marked the sessions it already had, and a served session
   registers long after it has decided which names it speaks:
   `device/session.py` captures its generation, builds the runtime from
   it with no await in between, and only then awaits the device's hello,
   which is several awaits before `open_session`. A rename published in
   that window reached nothing, and the same hole is open far wider
   between a rename's commit and the apply that installs a world built
   from it, which is every conversation begun while an operator has not
   applied yet. The reuse case in this suite had encoded the wrong
   premise with it: it assumed a session opened after the publication
   took its name from the store, which no served session does.

   *Fixed as prescribed, and the code decided the shape.* The anchor is
   the generation, because that is the object that decides which names a
   conversation speaks and it is the only thing that knows when a world
   began. `Generations` keeps every rename published in this process,
   oldest first, and each living world's place in that list; a rename
   reaches it through `renames_announced_to`, a register beside the
   erasure's, wired where the erasure subscription already is. A world
   installed after a rename joins at the end of the list, so it has
   nothing to translate, which is what an apply produces and what keeps
   the freed name safe: the recreated agent is servable only from a
   world that knows both names. A world's place goes where the world
   goes, in `dispose`.

   Two smaller decisions inside it. The seed is a thunk the store calls
   under the lock a publication takes, rather than a list the caller
   reads first: reading it at the call site leaves a window between the
   two statements in which a rename can mark every registered session,
   not yet including this one, and then be missed by a list already
   read. And the per-session map stays, seeded from the world rather
   than replaced by it, because the composition a session accumulates
   after it registers is the same rule folded over the same stream;
   `_compose` is that one rule, and being idempotent is what lets a
   rename that arrives through both doors be applied once.

   The plan's own reasoning survives this and its wording does not: it
   said the publication marks "the sessions live at that instant", and
   what it marks is the sessions whose WORLD predates it. The
   counterexample the plan used to refuse a process-wide map, a freed
   name given to a new agent, is exactly what the world boundary
   answers.

2. **P2 (the terra delta): a world could be installed between a
   rename's commit and its publication.** The rename commits before it
   announces, and the install stamped a new world with the ledger as it
   stood at that moment, so an apply that read the store inside that
   interval built a world FROM the renamed configuration and was then
   told it had not heard the rename. Inert while the freed name stays
   free, and a misattributed turn the moment an operator gives that name
   to a second agent.

   *Fixed with the second of the two options terra offered, because the
   code refuses the first.* Serializing the INSTALL with the
   commit-to-publication interval closes only one direction: the
   install happens long after the snapshot it installs was read, with
   the providers built and the speech synthesized in between, so a
   rename committing inside THAT interval would leave the opposite
   error, a world whose snapshot predates the rename told it already
   knows one. `reload.py` has no re-read to catch that, and
   `RunningConfigMovedError` guards the diff rather than the apply. So
   the watermark is taken where it means something, which is the read:
   `_in_order` takes the stored half and `Generations.watermark()`
   together, under the order a rename holds across its commit and its
   publication, and `applying(known=...)` carries that number to the
   install. A rename is then wholly before a world's reading or wholly
   after it.

   What that costs is one small read's worth of the ordering lock, paid
   in the worker thread the read already runs in, which is the same coin
   retention and a rename already spend and never the event loop's. It
   cannot cycle: the order is taken outside the read's own transaction,
   which is the discipline every other holder keeps.

   The forced interleaving holds the publication back on a thread of its
   own, gives the freed name to a second agent inside that window, and
   shows the read parked on the order rather than racing it.

3. **P1: reusing and renaming a freed source name corrupted older
   sessions.** Composition assigned the old name as a source
   unconditionally, so a session holding `sam -> poet` was rewritten to
   `sam -> bard` by a later rename of a recreated `sam`, and its next
   turn was filed under a stranger's new name on a thread its own agent
   owns. The thread guard refuses that and the writer drops the marker's
   whole batch, which is the loss this milestone exists to prevent.

   *Fixed as prescribed:* the arm that moves an entry whose current
   value is the old name is unchanged, and the old name is entered as a
   source of its own only when nothing is filed under it yet. A name
   that already means something to a session goes on meaning it.

### Open questions the plan left, and what M2 answers

None left open. The follow-up the plan says this milestone files is
filed: the memory store's untranslated window, with
`conversations.store.renamed()` named as the hook, the asymmetry
restated from the plan, and what a fix would have to decide. It carries
no number in any committed sentence, per the repository's rule that
landing code refers to a decision rather than to a tracker.

### Verification

- `uv run ruff check .`: clean.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: 5764 passed,
  19 skipped.
- `uv run pytest tests/integration -q`: 243 passed.
- `scripts/check_doc_links.py .`: clean, 206 files.
- `uv run mypy`: clean.
- **Every concurrency pin verified to bite, by mutation, with each
  mutation reverted:**
  - No translation at all (`_write` resolving against an empty map):
    eight of the nine cases fail, and the writer's log carries the
    `MisattributedTurn` batch drop the plan predicted. The ninth is the
    agent nothing renamed, which is the case that must keep passing.
  - Translation at the landing alone: five cases fail, the two
    thread-owner cases pass, which is the terra P1 finding exactly.
  - Publication moved outside `erasure_order()`: the ordering pin alone
    fails, on the writer never arriving at the order, and the log again
    carries the dropped batch.
  - Retirement moved into `close_session`: the queued-batch pin alone
    fails.
  - One map for the whole process instead of one per session: the freed
    name pin alone fails.
  - The freed name entered as a source unconditionally (the review's
    second finding, put back): the recreate-and-rename pin alone fails,
    on the older session's acknowledgement coming back false with
    `MisattributedTurn` in the log, which is the batch drop the finding
    described.
  - The world's own renames ignored at the open: both served-session
    pins fail, each on a thread created under the old name
    (`assert 'assistant' == 'poet'`), which is the detached live
    reference the rename set out to remove.
  - The session not saying which world it bound: the same two fail the
    same way, which is what says the wiring is load-bearing rather than
    the seam alone.
  - A world not stamped when it is installed: the pin that a world
    installed after a rename has nothing to translate fails, and it is
    the one that keeps a reused name safe.
  - The read and the watermark taken without the order (the terra
    delta's finding, put back): the new pin fails on the read never
    reaching the order. With that assertion relaxed as well, so the
    consequence can be seen rather than inferred, the world built from
    the renamed snapshot is handed `('sam', 'poet')` to translate, and
    with the watermark assertion relaxed too the second agent's thread
    is filed under `poet` instead of `sam`, which is the misattribution
    the finding predicted. Every probe was reverted.
- The generated-document drift checks: clean. M2 touches no generated
  document; the census manifest is regenerated in the last commit of the
  milestone as always.

## M3: the route, the boundary it announces, and the caveat it retires

PR #418.

### What landed

In the order the commits tell it: the sentence, the route that chooses
it, the two column comments, and the pages the rename makes false.

- **`config/entities.py` gains `RENAME_UNSERVED_NOTICE`,** the sixth,
  for a rename that moved a device binding or the default agent with the
  agent it renamed. Two boundaries at once, exactly as
  `BINDING_UNSERVED_NOTICE`, and it cannot borrow that one: that
  sentence is about one binding, written for the verb that writes one,
  and a rename may have moved several bindings and the default agent,
  none of which the operator just wrote. It names no command, per #386.
  `tests/support/notices.py` gains it too, since that module is where a
  printed sentence is mapped back to its boundaries.
- **`config/api.py` gains `POST /agents/{name}/rename`,** with
  `AgentRename` in `responses.py` as the body the document describes,
  `_to` as the exact-shape parser beside the other three, `_renamed` as
  the acknowledgement's line composer and `_rename_notice` as the
  three-arm choice. The arm is read off `Renamed`'s own fields and never
  off the loaded agents, which is the one place this differs from
  `_binding_notice` and the reason it is a function of its own.
- **`2003_rename_moves_memory` and `1003_rename_moves_ownership`,** one
  column comment each, with `schema.py` moved in the same commit and the
  chain heads moved in the four suites and the one CI step that pin
  them. `docs/reference/conversations-schema.md` regenerates from the
  second.
- **The agent descriptor's note becomes three notes,** which moves
  `docs/reference/domain-config.md`: what a rename moves, what it
  refuses and why that refusal is what keeps the act reversible, and
  what it leaves alone, the standing window included. The two
  `memory/api.py` docstrings, the server README's two orphaning
  passages and `docs/architecture/observability-surfaces.md`'s half
  sentence move with it, and `docs/reference/api-openapi.json`
  regenerates over both the route and those docstrings.
- **`tests/unit/test_config_api_writes.py` gains the route's section,**
  eleven cases plus the rename's row in the every-write parametrization
  and its malformed-body table: the line composed from the rows rather
  than from the request, two of the three boundary arms, the pin that
  the arm is not chosen by what the server is serving, the 404, the
  occupied destination's 409 with neither name in it, the same-name 422,
  the reachable no-leak case, and the composer's strip over a planted
  stored name. `tests/unit/test_config_snapshot_mode.py` carries the
  third arm, because that arm is the mode's rather than the route's.

### Deviations from the plan

Two, one of them forced by a limit the plan could not have known about
and one a placement.

1. **The record migration is `1003_rename_moves_ownership`, not
   `1003_rename_moves_thread_ownership`.** Alembic's
   `alembic_version.version_num` is `varchar(32)` and the plan's
   spelling is 34 characters, so the migration ran, altered the comment
   and then failed on the stamp, which is its own last statement:
   `value too long for type character varying(32)`. The shortened id
   says what moved, a thread's ownership, and the migration's docstring
   records the limit where the next author of one will read it. Nothing
   else about the migration differs from the plan.

2. **The third boundary arm is asserted in the snapshot-mode suite, not
   beside the other two.** The plan says the three arms are asserted on
   what the route answers in this milestone, and they are; what it does
   not say is where. The store-boot arm is not a fact about a rename at
   all, it is the answer every write in that mode gives, and
   `test_config_snapshot_mode.py` already holds the case that walks the
   writes it covers. Putting a second snapshot-mode fixture in the
   writes suite would have been a second home for one decision. The
   writes suite's section header names where the third one is.

### Discoveries

- **The contract suite needed a decision recorded for an operation with
  no verb.** `test_api_contract.py` holds every operation the committed
  document declares to be either covered by an `Act` or excluded with a
  reason, and this milestone adds an operation whose verb is the next
  one. So the exclusion table gains the row and says which milestone
  takes it away, which is the closure working rather than an escape from
  it: without the entry the suite fails, and with a stale one the
  covered set silently widens.

- **A no-echo assertion has to be made about a name that is not a word
  of English.** The same-name refusal explains that names are compared
  with the surrounding whitespace taken off, and `sam` is a substring of
  "the same name", so a case built on the fixture's own agent passed for
  the wrong reason. It runs on an agent called `gardener` instead.

- **The strip on the line takes the userinfo whole.**
  `without_url_credential` cuts at the last `@` rather than reassembling
  the authority, so a planted `https://user:<secret>@host/agent` renders
  as `https://host/agent` and not as `https://user@host/agent`. The
  composer's pin asserts the whole line, so it records that rather than
  only the absence.

### The review round

Backend codex, model `gpt-5.6-sol`, against PR #418: 4 P2, mergeable
after the listed fixes. All four are fixed on the branch, in one commit
each. Two of them are the same mistake at two altitudes, a sentence that
was true of the case it was written for and false of the other case its
chooser selects it for.

1. **P2: the refusal responses documented false retry semantics.** The
   route inherited the shared descriptions, and the shared 409 promises
   that the request can be retried. That is true of the three states it
   lists, a held write lock, a claim in flight, a reload already
   running, and false of the fourth this route adds: an occupied
   destination stays occupied however many times the request is made, so
   a generated client would have been describing a retry loop that
   cannot terminate. The shared 422 is about addressing and left out the
   same-name refusal, which is a refusal about a request that addressed
   everything correctly.

   *Fixed as prescribed:* two description files beside the reload's and
   the diff's, which carry their own for the same reason, passed through
   `_problems(..., instead=...)`; the document regenerated; and a pin on
   the distinction rather than on the paragraph, which also refuses the
   shared sentence's promise outright so it cannot come back unnoticed.

2. **P2: the sixth notice was false for a default-only rename.**
   `_rename_notice` chooses it when a device binding moved OR when the
   default agent did, and the sentence said a device BOUND to the agent
   reaches it at the check-in after the install. The default agent is
   what covers the boards that have no binding of their own, so a rename
   that moved the default alone moved the reference of precisely the
   devices that are not bound to the agent.

   *Fixed as prescribed:* it speaks of a device that resolves to the
   agent and names both ways one can, by its own binding or by the
   default agent. Still one sentence, because a caller cannot act on the
   difference. Nothing pinned the wording, which is why the finding was
   possible: the route's case reads the sentence off the constant and
   the respelling suite pins the binding's sentence rather than this
   one. The pin arrives with the fix.

3. **P2: the no-leak case did not read every surface the plan names.**
   Five, and it read the body, the headers and two formatter renderings.
   Both process streams were not read at all, and a formatter rendering
   is a weaker claim than the record itself: a formatter prints the
   message, so a value that reached a record as a stray attribute, as an
   argument no format string consumed, or on an attached exception is
   invisible to both formatters and still in the object another handler
   would serialize whole.

   *Fixed as prescribed:* stdout and stderr captured and asserted, and
   the walk over the records reads each of them three ways, as JSON, as
   text, and as the record's own message, attribute dictionary,
   arguments and exception. That is the #381-era shape rather than a new
   one. Both new surfaces were verified to bite by mutation, each
   reverted: a stderr line carrying the pasted value fails the case, and
   so does a record carrying it as an attribute.

4. **P2: the generated agent reference misstated post-rename history.**
   The descriptor's note said categorically that a renamed thread holds
   turns under the old name, which the in-flight protocol makes false
   for half of them: a turn a live session durably writes after the
   rename is translated at the write boundary and carries the current
   name, because it is a new write rather than an edit and a row may not
   disagree with the thread it lands on.

   *Fixed as prescribed:* the note draws the line the protocol draws, a
   row already written keeps its name and a row written after the rename
   carries the current one, with the session row keeping the name it
   opened with either way. The window that stays became a note of its
   own, since what a live conversation REMEMBERS in it is a different
   fact from what its turns are filed under, and the reference
   regenerated.

### Open questions the plan left, and what M3 answers

None. The route's shape, its body, its answer and its three arms were
all resolved in the plan; the verb, its `Act` and the end-to-end cases
through the registered command are M4's.

### Verification

Re-run after the review round's fixes, which is where these numbers are
from.

- `uv run ruff check .`: clean.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: 5792 passed,
  19 skipped.
- `uv run pytest tests/integration -q`: 243 passed.
- `scripts/check_doc_links.py .`: 206 files, 0 failures.
- `uv run mypy`: clean.
- **All seven generated-document drift checks: current**, each
  regenerated in the commit that moved it and re-diffed against the
  committed copy at the end. `domain-config.md` moved with the
  descriptor's note, `conversations-schema.md` with the record
  migration, `api-openapi.json` twice, once for the route and once for
  the memory docstrings; `server-config.md`, `events.md`, `cli.md` and
  the recipes inside it are untouched and were diffed anyway.
- **The CI wheel-migration step's chain-head half, run locally**, which
  is where the two new heads have to hold: the wheel built and installed
  into a venv of its own with `[serve]` and `psycopg[binary]`, a blank
  database migrated from the installed artifact, and the three heads
  read off the version tables as `3002_drop_max_tokens_secrets`,
  `1003_rename_moves_ownership` and `2003_rename_moves_memory`. The
  migrated comments were read back from `col_description` and are the
  new ones. What was not run locally is the rest of that step, the table
  and column inventories, which this change does not move.

One thing the re-run caught that is worth writing down, because it cost
a whole integration lane: the lane's own last fixture asserts that
nothing left a `__pycache__` under `vinga_server`, and the commands this
milestone runs outside pytest to regenerate documents write them. The
first re-run answered "243 passed, 1 error", the error being that guard
rather than anything in the product. `PYTHONDONTWRITEBYTECODE=1` on
every out-of-pytest command is what `AGENTS.md` already asks for, and it
is what the clean re-run above was taken with.

The first spelling of the record revision id is what caught the version
column's width, and it was caught by running the migration rather than
by reading about it: the migration altered the comment and then failed
on its own stamp.

## M4: the verb

PR #419.

### What landed

In the order the commits tell it: the verb with the two committed
artifacts it moves, and the suite that drives it.

- **`config/cli.py` gains one `Command` row and one `Act`.**
  `RENAME_AGENT` is a POST whose path comes off the agent's own
  descriptor (`_entity_path(entities.descriptor("agent"), "rename")`)
  rather than off a path written out beside it, so the noun the verb
  sits under and the address it reaches cannot come apart; `_new_name`
  is the body, one key, sent exactly as it was typed. The row carries
  `destroys=False` with the guide's reasoning in a comment on it, and
  `_renamed_to` is the argument shape, one address positional and one
  payload word behind it. `Invocation` gains `to`, spelled as the
  body's own key the way `agents` is and placed with the payloads
  rather than with the fields that address a row.
- **The API contract's exclusion row goes.** M3 added it with a note
  saying which milestone takes it away; the act now covers the
  operation, and the suite's closure is what would have failed had
  either half been done without the other.
- **`docs/reference/cli.md` grows the command's page**, regenerated
  through the generator and the marker-rebuild the CI step runs, and
  the agent noun's own listing grows the line beside it.
- **`tests/unit/test_config_cli_rename.py`,** ten cases in three
  groups: the request recorded off the wire (one POST, the address as
  one encoded segment, a body of exactly the one key), the three
  boundary arms each against a server that chooses that arm, and the
  refusals a terminal meets, which are the occupied destination naming
  neither name, the second run finding nothing, the reachable no-leak
  case and the absence of a confirmation.
- **`tests/support/config_cli.py`'s runner gains one flag**,
  `snapshot_only`, in the runtime table it already carries, so the
  third arm can be driven through the registered command against a
  server composed the way a handed configuration composes one.
- **Both installed-artifact lanes gain a case**, because each of them
  ends with a completeness test that holds the registration table to
  what actually ran. The live lane's binds a board of its own to an
  agent of its own, renames it, and reads the moved binding and the
  absent old name back through the verbs an operator would use; the
  wheel lane's makes the same act from a bare install, which is the
  packaging claim beside it. Both leave the store as they found it.

### Deviations from the plan

Two, and neither changes what the milestone ships.

1. **The respelling suite gains no licensed substitution.** The plan's
   test list says it gains one "for the new stderr text", which was
   written before it was clear where that suite's licence applies. The
   transcript is a fixed recording of the commands the #223 re-cut
   moved, captured on the commit before that rename, and the
   substitution table licenses text that MOVED inside a line one of
   those recorded steps prints. This milestone moves no such line: it
   adds a command the transcript does not drive, and every step in it
   prints what it printed. Verified rather than assumed, by running the
   suite: two cases, both green with the table untouched. A
   substitution added anyway would have been an entry nobody could name
   a reason for, which the suite's own docstring says is what the table
   must never grow.

2. **The shared runner learned the snapshot mode.** The plan says the
   three arms are driven end to end here and does not say what the
   third one is driven against; the runner builds the API per command
   and had no way to say the server around it was handed its
   configuration. One key in the runtime table it already keeps for the
   same kind of fact, defaulted to the ordinary deployment every other
   suite drives, was smaller than a second runner and keeps the three
   arms in one file where a reader compares them.

### Discoveries

- **The census guard is what makes the verb's arrival a single
  change.** M3 had to write two sentences in prose because the spelling
  named nothing; with the row registered, the same spelling in a code
  comment and in the changelog entry passes the guard, and it passes on
  the argument count too: the matcher reads a row's positional budget
  off the built tree, and `agent rename <new> <old>` fits the two this
  row declares.

- **A one-segment claim cannot be asserted on `httpx.URL.path`.** That
  attribute answers the decoded form, so a name holding a space reads
  back as `/agents/the poet/rename` and an assertion on it passes for
  a target no server would route. The claim is about what travels, so
  it is made on `raw_path`, which is the encoded bytes.

- **The body is asserted as a shape rather than as bytes.** How a JSON
  encoder spaces a pair is the client library's business and moves with
  its version, while what this milestone claims is the one key and
  nothing beside it.

- **A registered row owes the two lanes a case, and the plan's test
  list does not mention it.** Each installed-artifact lane ends with a
  completeness test whose inventory is `cli.COMMANDS` itself, so
  registering a row and stopping there fails both from the side the row
  joined. That is the closure working rather than an obstacle: it is
  what stops a command from shipping without ever having reached a real
  server, and it caught this one in the first integration run of the
  milestone.

- **The occupied destination's sentence holds neither fixture name.**
  M3 recorded that `sam` is a substring of "the same name" and moved
  its no-echo case onto `gardener`; the conflict sentence this file
  drives says "an agent already exists under the new name", so `sam`
  and `poet` are both honestly absent from it and the case reads the
  way it says it does.

- **The prose pages keep the wording M3 gave them**, and that is a
  decision rather than an oversight. The plan's documentation footprint
  puts the server README's two passages, the observability line, the
  two memory docstrings and the agent descriptor's note in M3, and M3
  corrected all of them; what it could not do was spell the verb, since
  the census guard holds a written invocation to naming a registered
  command. Adding the spelling to them now would be a second pass over
  pages this milestone's review is not looking at, and none of them is
  false without it: each says what a rename does, and the generated CLI
  reference is where the command it is typed as is published. Recorded
  here so a reviewer can overrule it deliberately rather than wonder
  whether it was considered.

### Open questions the plan left, and what M4 answers

None, and none are left for anything after it: M4 is the last
milestone, and the plan's own questions (the confirmation, the refusal
set, the route, the boundary arms, the in-flight protocol) were
resolved before M1. What remains open is what the plan states as
remaining rather than as pending: the window between the write and the
apply, and the follow-up M2 filed for the memory store's untranslated
half of it.

### Verification

- `uv run ruff check .`: clean.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: 5804 passed,
  19 skipped.
- `uv run pytest tests/integration -q`: 245 passed, which is the lane's
  243 and this milestone's two.
- `scripts/check_doc_links.py .`: 206 files, 0 failures.
- `uv run mypy`: clean.
- **All seven generated-document drift checks: current.** `cli.md` and
  the recipes inside it were regenerated in the commit that registered
  the verb; the other five are untouched by this milestone and were
  diffed anyway.
- **The verb's own suite verified to bite, by mutation:** sending the
  new name under a body key the route does not read fails eight of the
  ten cases, and the two that survive are the refusals, which are
  refused either way. The mutation was reverted.
- **A note on the numbers.** Two lanes were running against this
  machine's one Postgres for part of this milestone, which is what a
  worktree stack costs: a run made while the other one was up answered
  with connections terminated mid-connect rather than with failures of
  its own. The numbers above are from runs made with nothing else on
  the database, which is how the earlier ones in this document were
  taken too.
