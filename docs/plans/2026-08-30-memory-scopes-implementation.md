# Memory scopes and editing: implementation

The companion to [`2026-08-30-memory-scopes.md`](2026-08-30-memory-scopes.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations
says so explicitly.

## M1: the scoped schema and the store's new sentences

PR #359.

### What landed

In the order the commits tell it: the package's import graph, the
schema and its migration, the migration proved on rows, the events'
scope, the store's seams and its new sentences one family at a time,
the sentinels, and the documents.

- **`memory/scopes.py`,** a module of its own holding `MemoryScope`
  (`conversation`, `agent`, `device`) and `FACT_SCOPES`, its
  two-member subset. Everything derives from it: the facts table's
  check constraint, the store's caps and rendering, the events' `scope`
  field, and M3's tool enum. It imports nothing but `enum`, which is a
  tier constraint rather than a preference (see the deviations).
- **`memory/schema.py`,** grown to the shape #83 needs. `facts` now
  carries `scope` and `owner` where it carried `agent`, plus
  `forgotten_at` and `forgotten_in`, the held pair. Two check
  constraints state what the store relies on: `ck_facts_scope`, built
  from `FACT_SCOPES` so the constraint and the vocabulary cannot
  disagree, and `ck_facts_forgotten`, which keeps the pair null
  together or set together. The index becomes `ix_facts_scope` on
  `(scope, owner, id)`, the access path the ordered read, the prune and
  the lookup filter all walk, with a partial `ix_facts_forgotten` on
  `(forgotten_in, id) WHERE forgotten_in IS NOT NULL` for the paths
  that address held rows by their thread. `state` is new:
  `(conversation, key, value, updated_at)`, primary key
  `(conversation, key)`, every column with its `comment=`.
- **`migrations/versions/2002_memory_scopes.py`,** autogen-produced and
  then reviewed. Three things were written by hand, and each is stated
  in its docstring: the column rename (autogenerate proposes a drop and
  an add, and a database that took that pair would have lost every
  row); the two check constraints, which autogenerate cannot see; and
  the order, so that the `scope` backfill runs under a server-side
  default which is then dropped, since the tables declare none and
  every insert names its scope.
- **`tests/integration/test_memory_upgrade.py`,** the seeded
  preservation case the plan's finding 12 asks for: a database stamped
  at `2001_agent_memory`, three facts written through the baseline's
  own columns, brought to head the way a boot does, and read back for
  the bytes, the scope, the null held pair, the empty ledger and
  identity continuing past the seeded maximum.
- **The events.** `memory_unreadable` and `memory_unwritable` keep
  their names, channel and level and each gains `scope`, which their
  sentences now name. `memory_cleanup_failed` is new, WARNING, carrying
  the failure's class name and nothing else: the sweep and the
  session-close purge act for no agent and on no scope, so neither of
  the other two could report them honestly. `docs/reference/events.md`
  is regenerated, the README index gains its row, and the driver
  inventory grows to 85.
- **`memory/store.py`,** deepened in place around two guarded seams and
  a third for cleanup. `_read` holds one connection on the read engine
  and says `memory_unreadable` once per scope the read could not
  answer; `_written` holds one transaction on the write engine and
  classifies what leaves it, this module's own `_Refused` becoming the
  `ValueError` the tool layer rephrases and everything else being the
  database, by class and never by message; `_cleaned` contains a
  cleanup and says `memory_cleanup_failed`. On top of them:
  - `add(scope, owner, fact, agent=)` answering the row id, with
    `remember` as that call on the agent scope with the id dropped;
  - `update`, `forget` and `restore`, each addressed by `_addressed`,
    the id AND the pair that owns it, with one fixed refusal per
    operation shared by a missing id and an inaccessible one;
  - `recall(agent, device, query)`, a bounded case-insensitive
    substring lookup over both scopes' active facts, newest first, with
    ids, the pattern language escaped out of the query, and a fixed
    continuation sentence;
  - `set_state` and `clear_state`, the conversation ledger, with three
    refusals naming the three bounds a write can cross;
  - `read_for_prompt(agent, device, conversation)`, a frozen
    `PromptMemory` of three rendered blocks in one connection;
  - `purge(connection, threads)`, the seam that lets a caller's
    transaction own the deletes, `purge_threads(threads)` for a caller
    that holds none, and `sweep(grace)`, whose anti-join reads
    `record.conversations`.
- **The caps, per scope and read at call time.** `MAX_LINES` and
  `MAX_BYTES` stay 200 and 8192 (see the deviations), `DEVICE_LINES`
  and `DEVICE_BYTES` are 30 and 2048, `CORE_LINES` and `CORE_BYTES` 40
  and 4096, `RECALL_LINES` and `RECALL_BYTES` 20 and 2048,
  `STATE_KEYS` and `STATE_BYTES` 50 and 4096. Held rows are outside all
  of the arithmetic and are never pruned; every mutating transaction
  re-prunes and never takes the row it just wrote.
- **The lane and CI plumbing.** `tests/conftest.py` needed nothing: it
  reads the tables off the metadata, so `state` is truncated between
  tests by construction. The wheel step's memory block moves its head
  literal to `2002_memory_scopes`, expects both tables, and now checks
  the columns of each, so a wheel carrying the migration without the
  rename fails there too.
- **The documents.** `CHANGELOG.md` gains the dated entry with the
  stop-then-start order, and `vinga-server/README.md`'s upgrade section
  states it where an operator is already reading about upgrading. The
  command-spellings census was regenerated after both README edits, as
  the standing rule requires. Nothing else was stale: the concepts page
  and the glossary describe behavior this milestone does not change.

### Deviations from the plan

Five, one of them a sequencing decision the plan left to be made here
and one an amendment to the plan itself.

- **The storage cap stays at 200 lines and 8192 bytes.** The plan grows
  the agent scope to `MAX_LINES = 1000` and `MAX_BYTES = 65536`
  alongside the core/remainder split. Raising it here would balloon
  every prompt in the interim, because `read()` still injects the whole
  scope and nothing yet reads the core; the two numbers therefore move
  in M3, in the same change that makes the injected block the core.
  The names are unchanged, so the cap suites keep their monkeypatch
  shape and the growth is a two-line diff when the split lands.
- **The scope vocabulary lives in `memory/scopes.py`, not in
  `memory/schema.py`.** The plan says the schema module, and the
  difference is a tier rather than a preference: the events package is
  client-half, and `vinga-server events reference` renders from it on
  an install with no database driver at all. A `scope` field whose
  closed set came from the tables would have carried SQLAlchemy into
  that install, which `test_tier_closure.py` caught as an ImportError
  traceback on the one entry point whose every other answer is a
  sentence. The vocabulary is still declared exactly once and is still
  a fact of memory; only its module moved, and that module says why.
- **`vinga_server.memory` no longer re-exports the store.** The
  package's `__init__` imported `store.py`, which imports the events
  package, so anything the events package imported from inside
  `vinga_server.memory` was a circular import (`events/__init__` is
  half-built when `store.py` asks it for `ServerEvents`). The package
  is now docstring-only and callers name `vinga_server.memory.store`,
  which is the discipline `vinga_server.config` already keeps about its
  boot path: importing the package pulls in neither the driver nor the
  migrations. Seventeen import lines moved; nothing else did.
- **The two events' `scope` field and `memory_cleanup_failed` landed
  here rather than in M2, and the plan was amended to say so.** The
  review flagged the mismatch: the plan listed both under M2 while this
  milestone shipped them. What moved is the placement, not the code.
  The emit sites are the operations M1 implements, and shipping an
  event whose shape changes one release later serves no reader: an
  operator's parser would meet `memory_unwritable` without a scope in
  one release and with one in the next, for paths that never behaved
  differently. M2 still owns everything the coupling needs; it no
  longer owns the vocabulary its emitters were written with. The
  changelog says the field arrived, since the event payload is a
  compatibility surface and "nothing above the store changed" was not
  true of it.
- **Every mutating call takes the acting agent as a keyword.** The
  plan's operations are addressed by `(scope, owner)`, which under
  device scope makes the owner a MAC while the events' `agent` field
  means the agent that was speaking. Rather than let that field carry a
  MAC, or make the field optional, the store's mutations and its
  lookups take `agent=` beside the address. Under agent scope the two
  are the same string, and the call site says so twice, which is honest:
  they are two different facts that happen to be equal.

### Discoveries

- **A prune can undo the mutation that provoked it.** A restored fact
  keeps the id it always had, so it is usually the oldest active row in
  its scope; the ordinary oldest-first prune, running in the same
  transaction, would have deleted it and answered success. The same
  holds for a correction that grows the oldest fact. `_prune` therefore
  takes the id the transaction just wrote and never chooses it, which
  is the smallest rule that makes "every mutation re-prunes" safe. The
  mutation that protects nothing fails two cases.
- **A lookup's query is pattern syntax unless it is escaped.** The
  query is the model's own text, so a lookup for `%` would have
  answered with every fact the agent has. `_containing` escapes the
  backslash, the per cent and the underscore, and the case is pinned.
- **One connection makes per-scope containment moot.** A failed
  statement poisons the transaction the other two scopes would have run
  in, so `read_for_prompt` loses all three together whatever the
  containment is written as. That is why `_read` takes the scopes it
  reached for and reports every one of them, rather than guarding each
  block separately: the honest report is per lost scope, and the honest
  structure is one guard.
- **uv's build cache can serve a stale wheel to the tier lane.** The
  tier environments are built with `uv sync --no-editable`, and after
  an edit to `events/values.py` the lane kept installing the previous
  build; `uv cache clean vinga-server` is what makes a local run of
  that lane mean anything. CI starts from a cold cache and never sees
  it.

### Verification

- `uv run ruff check .`: clean.
- `uv run mypy`: clean (5 source files, the events package).
- `uv run pytest tests/unit -q`: 4645 passed, 19 skipped.
- `uv run pytest tests/unit -q -n auto --dist loadfile`, the shape CI
  runs: same.
- `uv run pytest tests/integration -q`: 226 passed.
- `uv run vinga-server events reference` regenerated
  `docs/reference/events.md`; `uv run python -m
  tests.unit.test_command_spellings` regenerated the census after the
  README edits; `python scripts/check_doc_links.py .` checked 172 files
  with no failures. The five generated documents (`domain-config.md`,
  `conversations-schema.md`, `events.md`, `api-openapi.json`, `cli.md`)
  each regenerate to their committed bytes.
- Each guard was run against the mutation it exists for before being
  trusted: the migration's rename against the autogen candidate's
  drop-and-add; the ownership predicate without its `(scope, owner)`
  half; the prune protecting nothing; an active read that counted held
  rows; per-scope caps collapsed to one pair; the ledger's byte bound
  unchecked; the query's wildcards unescaped; the sweep without its
  grace period and without its anti-join; and `read_for_prompt` reading
  each scope on a connection of its own.
- Not verified: the `image` job, which builds and smokes the container.

### PR review round

External review of the branch as pushed to PR #359, at `a73f7b41`
against `origin/main`: backend codex (codex-cli 0.151.0), model
gpt-5.6-sol, read-only sandbox, 2026-08-30, runtime 8m20s. Four
findings, two P1 and two P2, verdict as received: mergeable after the
listed fixes. Condensed below as received, each with its resolution and
the commit that landed it.

Three of the four share a shape worth naming: a bound or a boundary
stated in prose and not enforced in code. A refusal that was documented
as sanitized and was not; two caps that were named in the plan and were
over-run by a line or a sentence; a vocabulary that was declared closed
and was left to the database to close. The fourth is the opposite, a
released behavior that changed while the milestone said it would not.

1. **P1: the cross-store purge exposed raw database exceptions.** It
   executed both deletes with no failure boundary and said so in its
   docstring, so a SQLAlchemy error travelled to the caller carrying
   the statement it ran and the parameters bound into it. Those
   parameters are thread ids, which is what an erasure exists to
   remove. The no-leak family covered `purge_threads`, which `_cleaned`
   contains, and drove the bare `purge` only on success. Catch inside,
   build a fixed refusal outside the handler, re-raise so the caller's
   transaction still rolls back, and add a sentinel-bearing failing
   test that walks the chain.

   *Resolution* (`75cf806c`): adopted whole, with two sentences of its
   own rather than the two a model reads out, because what asks for a
   purge is an erasure or a retention prune and its reader is an
   operator. Chosen by class through `db.is_busy`, built inside the
   handler and raised after it, cause and context severed. The test
   binds a credential-shaped thread id into a delete a trigger refuses
   and hunts it through the sentence, the chain and both log formats;
   the mutation that removes the boundary lets the driver's error out
   with the id in it.

2. **P1: M1 changed behavior the milestone promised to keep.** Two
   parts. `remember` delegated to `add` and so began refusing a single
   fact larger than `MAX_BYTES`, where #314 kept it (the prune never
   goes below one fact); and the events' `scope` field and
   `memory_cleanup_failed` were the plan's M2 items, shipped here,
   while the changelog claimed nothing above the store changed.

   *Resolution*, in two commits. The behavior half (`81426b49`):
   adopted whole. `remember` writes through the same transaction and
   stops short of that one refusal, with the reason on the sentence; a
   compatibility test stores an over-cap fact and reads it back, and
   the refusal stays on `add` and `update`, which nothing calls yet.
   The tool's own changeover arrives in M3, where its changelog
   announces it. The event half (`6d9782bc`): the placement moved
   rather than the code. The emit sites are the operations M1
   implements, and shipping an event whose required fields change one
   release later would hand an operator's parser `memory_unwritable`
   without a scope in one release and with one in the next, for paths
   that never behaved differently. M1's milestone bullet gains the two
   event items, M2's loses them, the deviation is recorded above, and
   the changelog now states that the two events gained a field and that
   a third arrived.

3. **P2: the core and the lookup did not enforce the bounds they
   name.** `_core` stopped trimming at one line, so an accepted fact
   between `CORE_BYTES` and `MAX_BYTES` produced an over-cap prompt
   block; `_bounded` appended `MORE_MATCHED` outside `RECALL_BYTES`,
   and the byte-bound test asserted the overflow; and a single match
   longer than `RECALL_BYTES` answered with the refinement sentence
   alone, so the id the plan says recall must expose was unreachable
   for exactly the fact a model would want it for.

   *Resolution* (`ba2875ac`): adopted whole. The core trims to empty,
   which loses nothing (the fact is stored, looked up and corrected by
   its id) and is the only way the block's cap can be true. The
   lookup's bound is on the answer, sentence included, so each prefix
   is measured as the answer it would produce and the continuation
   counts against both limits. One match always survives, cut from the
   right so its id is intact, with a fixed marker saying it was cut.
   Three mutations, one per bound, each caught by its own case.

4. **P2: a conversation scope reached the database and was misreported.**
   The four fact operations accepted every member of the vocabulary,
   `_caps` treated anything not device as agent, and the row met the
   table's check constraint, whose violation the write seam maps to
   `memory_unwritable` and a storage failure. A caller's mistake was
   answered by telling an operator that a healthy database refused a
   write.

   *Resolution* (`4f51e82e`): adopted whole. Every fact operation asks
   `_only_a_fact_scope` before a connection and answers one fixed
   sentence naming the two members a fact may carry, never the one that
   was passed; `_caps` says why it knows about two scopes. The test
   drives all four operations and asserts no row moved and no
   storage-failure event was emitted, and the mutation that drops the
   guard reproduces the finding exactly, `IntegrityError` and all.

### Verification after the review round

- `uv run ruff check .`: clean. `uv run mypy`: clean.
- `uv run pytest tests/unit -q`: 4650 passed, 19 skipped.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: the same.
- `uv run pytest tests/integration -q`: 226 passed.
- The five generated documents regenerate to their committed bytes, and
  the command-spellings census is current.
- Every guard above was run against its mutation before being trusted.

## M2: conversation state end to end

PR #360.

### What landed

In the order the commits tell it: the lock rule the coupling needs, the
two deletion paths made atomic, the refusal that closes resurrection,
the tools, the rendering, the composition, and the documents.

- **`db`'s ascending lock order,** stated beside `advisory_key` and in
  the module's own list of properties, because a transaction that writes
  both stores now exists. `take_the_chain_lock` is the statement the
  write engine's begin listener always issued, written once now that a
  second caller issues it for a second chain.
- **`memory.store.purge(connection, threads)`,** which takes the memory
  chain's lock itself and then deletes the threads' ledgers and held
  facts on a transaction its caller owns. A function rather than the
  method M1 wrote, for the reason in the deviations.
- **Retention names what it took.** `threads.prune` reads the doomed
  threads by name before deleting them and `Pruned` carries them, with
  the count derived from the names; `ConversationStore` is handed the
  purge at construction (an import would be a cycle), calls it inside
  the prune transaction, and publishes the ids through `erased()` inside
  `erasure_order()`, exactly as an API deletion does.
- **The erasure paths purge inside their own transaction.**
  `conversations/api.py`'s `_erasure` issues the memory deletes on the
  connection it already holds, before the commit, and both response
  models gain `state` and `held_facts` from that same transaction. The
  CLI's `ERASED_COUNTS` grows the pair, and `api-openapi.json` is
  regenerated.
- **The other half of the protocol, in the memory store.** A dead-thread
  set fed by a new `threads_erased`, a `_thread_keyed` seam that holds
  `erasure_order()` across every write addressed to a conversation
  (`set_state`, `clear_state`, a soft `forget`, a restore), and one more
  fixed refusal, `CONVERSATION_ERASED`. A permanent forget is not
  thread-keyed and is not held.
  `conversations/store.py` grows `erasures_announced_to`, a listener
  seam beside the writer register, because the memory store is in
  another package and is told by name rather than reached into.
- **The two state tools.** `set_state` and `clear_state` in
  `tools/builtin.py` and in `names.BUILTIN_TOOL_NAMES`, with
  descriptions written from the plan's scope rule of thumb and naming
  the promotion (`remember`) that outliving the conversation requires.
  `BuiltinTools` gains the zero-argument `MemoryContext` callable;
  `ToolSource` does not widen.
- **The rendering.** `with_memory` deepens into `with_scopes`, taking the
  store's `PromptMemory` and appending state, agent and device in that
  order, each under a heading that states its own rank and under a
  provenance token of its own. A scope with nothing in it renders no
  block, which is what keeps the agent-only prompt byte-identical.
  `PipelineRuntime` gains `_device` beside `_agent` and `_conversation`,
  and `_system_prompt` reads all three scopes in one worker-thread hop.
  The preview stays agent-keyed and its route description says why.
- **The composition.** Memory is opened before the conversation writer is
  constructed, so its close unwinds last and the writer's drain runs
  against an open store; the erasure subscription is entered there; the
  boot sweeps orphans older than the grace period; and the runtime is
  handed a session-close purge exactly where `conversations is None`.
- **The documents.** The concepts page's Memory section is the three
  scopes, the ledger's lifetime and the promotion it forces; the server
  README gains the tools in both spellings, the ledger's paragraph and
  the corrected block order; the overview's step 7 says what the model
  is sent; the changelog carries the behavior, the reserved-name growth
  and the two counts. The census was regenerated after the README edits.

### The pins that translated

Named one by one, because each is a contract that moved rather than a
test that was rewritten:

- the offered-tool ordered literals in `test_session_tools.py`, which
  grow `set_state` and `clear_state` between `remember` and
  `new_conversation`;
- `DUE_BUILTINS` in `tests/integration/test_tools.py`, the same two
  names;
- the reserved-entry-name parametrizations in `test_tool_names.py` and
  `test_config_tools.py`, which now walk the whole builtin set;
- every `prompt.with_memory` call in `test_runtime_prompt.py`,
  `test_session_prompt.py`, `test_config_api_runtime.py` and
  `test_memory_store.py`, which becomes `with_scopes` over a
  `PromptMemory` carrying the agent's scope alone: the byte-equality
  family, the identity-on-empty pin and the awkward-input parametrization
  all pass unchanged against it;
- `test_the_memory_read_happens_off_the_event_loop`, which now watches
  `read_for_prompt` and asserts one read per round rather than that a
  read happened at all;
- the erasure-count assertions in `test_conversations_erasure.py`,
  `test_conversations_namespace.py`, `test_config_cli_sessions.py` and
  `test_config_cli_conversations.py`, each gaining `state` and
  `held_facts`.

### Deviations from the plan

Four, and none of them changes what the milestone ships.

- **`purge` is a module-level function, not a method.** The plan calls it
  "the store's `purge(connection, threads)` M1 built". Neither caller has
  a store to call it on: the conversation writer is handed it because
  importing the memory store there would close a cycle (the memory store
  reads `record.conversations`), and a deletion through the operator API
  runs on a connection it opened for itself, precisely so erasure works
  with recording off where no store object exists. A function is what
  both of them can reach, and the SQL still has one home. It keeps
  everything M1's review round gave the method: the failure boundary
  that classifies through `db.is_busy` and answers `PURGE_BUSY` or
  `PURGE_FAILED` built inside the handler and raised after it, with the
  chain lock taken inside that boundary rather than in front of it,
  since a lock that does not arrive inside the timeout is exactly the
  contended case the retryable sentence is for.
- **Finding 7's "impossible order" is dissolved rather than only fixed.**
  The reorder the plan names is in (memory before the writer, exit
  callbacks so the writer drains first) and is pinned by test, but the
  failure it was written about cannot happen once `purge` is a function:
  a teardown retention purge runs on the record chain's connection and
  never touches the memory store's engines. The order is kept because it
  is the correct one and because the erasure subscription has to outlive
  the writer, not because a closed store would refuse.
- **The `MemoryContext` callable is a method on the runtime rather than a
  closure built in `bespoke_runtime_factory`.** The plan puts the closure
  in the factory, over the events object. `BuiltinTools` is constructed
  inside `PipelineRuntime.__init__`, and `_device` and `_conversation`
  are properties over that same events object, so the bound method reads
  exactly what the closure would have read and keeps those two answers in
  one place.
- **`read_for_prompt` takes `device: str | None`.** A session whose
  device never identified itself has no device scope, and saying so with
  None is honest where reaching for an owner of `""` would answer "no
  rows" by accident.

### Discoveries

- **A dead-thread set is process-scoped, which a suite has to respect.**
  The refusal is for the life of the process, exactly as the conversation
  writer's is, because ids are minted per session and never reused. A
  test that erases a fixed id therefore poisons every later test in the
  same worker; `test_memory_lifecycle.py` mints one per test, which is
  what a session does.
- **A test's `Config` does not point where `DatabaseConfig()` points.**
  The lane sets the model's defaults to the database it provisioned, but
  a `Config` parsed from a dictionary carries the packaged default baked
  into its parent's schema. The boot-sweep test names the lane database
  explicitly for that reason, and any later app test that asserts on rows
  will have to.
- **The concepts page said conversation history carries across a
  handover.** It has not since threads became per agent (#190). The
  paragraph is in the section this milestone rewrote, so it was corrected
  rather than left; the correction is #190's drift, not this milestone's.
- **A straddle test can be sensitive to two mechanisms at once.** The
  before-the-erasure case is enforced by `erasure_order()` and by the
  memory chain's lock together, so removing either alone still leaves the
  property true; the after case is sensitive to both. Both were run
  against both mutations rather than assumed.

### Verification

- `uv run ruff check .`: clean.
- `uv run mypy`: clean (5 source files, the events package).
- `uv run pytest tests/unit -q`: 4686 passed, 19 skipped.
- `uv run pytest tests/unit -q -n auto --dist loadfile`, the shape CI
  runs: same.
- `uv run pytest tests/integration -q`: 228 passed.
- The five generated documents regenerate to their committed bytes
  (`api-openapi.json` is regenerated in this change; `domain-config.md`,
  `conversations-schema.md`, `events.md` and `cli.md` are unchanged), the
  command-spellings census was regenerated after the README edits, and
  `python scripts/check_doc_links.py .` checked 172 files with no
  failures.
- Every concurrency and lifecycle guard was run against the mutation it
  exists for before being trusted: the dead-set check removed; the
  erasure order not held by a memory write; the purge not taking the
  memory chain's lock; retention not purging; the erasure's purge
  committed in a transaction of its own; the session-close purge not
  called, and called where threads are recorded; the exit callbacks
  registered in the other order; and the boot sweep removed.
- Not verified: the `image` job, which builds and smokes the container.

### PR review round

External review of the branch as pushed to PR #360, at `06e4f692`
against `origin/main`: backend codex (codex-cli 0.151.0), model
gpt-5.6-sol, read-only sandbox, 2026-08-30, runtime 6m26s. Five
findings, one P1, three P2 and one P3, verdict as received: mergeable
after the listed fixes. Condensed below as received, each with its
resolution and the commit that landed it.

Two of them are the same mistake in two places: a milestone that made
the order of things load-bearing and then left an order to somebody
else. The reply loop let the database decide which of two ledger writes
was last, and the composition asked the record a question before
anything had built the record. The other three are things the plan asked
for and this milestone did not deliver: a test that tested the wiring
rather than the shape around it, a disclosure, and a count.

1. **P1: same-round state mutations could commit in reverse order.**
   The loop declares everything that is not a move independent and
   dispatches it with `asyncio.gather`, and both state tools ride that
   path. The database serializes the two transactions, but lock arrival
   is not issue order, so a `set` followed by a `clear` of one key, or
   two sets of it, could leave either result current.

   *Resolution* (`1ea58132`): adopted whole. The names are declared
   beside the builtins as `ORDERED_TOOL_NAMES`, where every other
   name-based routing rule already lives and with the reason on them:
   both write one conversation's ledger by a key the model chose, and
   nothing else in the set has that property (`remember` appends, the
   moves are resolved by the loop in issue order, and device and server
   tools touch different worlds). They run first and one at a time;
   everything else keeps its concurrency; the results are reassembled
   into the order the model asked in. Before the rest rather than beside
   them, which costs a round trip nothing was waiting on and leaves no
   dispatch running that nobody awaits when a barge-in lands. Both
   interleavings are forced with a first write parked until the second
   arrives, bounded so the ordered implementation is not deadlocked by
   its own correctness, and each was run against the mutation that
   restores the concurrent dispatch.

2. **P2: the boot sweep ran before the record schema it anti-joins was
   migrated.** On a blank database it therefore met a table that is not
   there, the containment turned that into `memory_cleanup_failed`, and
   the boot carried on having advertised a heal it had silently skipped.

   *Resolution* (`579c151a`): adopted whole. The sweep moved behind
   whichever branch materializes the record (the writer's constructor,
   or the migrate-and-dispose recording-off does) and in front of the
   writer's start, so the sweep and the writer's first retention pass do
   not reach for the same rows at once. Every ordering M2 already
   promised still holds and the comments now say which. The
   blank-database boot is what makes the ordering observable, so that is
   the test: no cleanup failure fires, and the mutation that puts the
   sweep back in front of the record is caught by it.

3. **P2: the teardown-retention tests did not exercise retention or the
   production wiring.** They recorded which store was let go first and
   nothing else, so removing `purge_memory=purge` from the composition
   left them green, and the retention coverage that did exist
   constructed its own store with the purge handed in.

   *Resolution* (`93cf58a2`): adopted whole. Both tests drive the
   composition a deployment runs. One makes a thread retention-eligible
   after the boot has finished, so the pass that can take it is the pass
   on a close marker the drain has to reach; the other plants it before
   a boot that then refuses, so the pass is the writer's own at start
   and the unwind follows. Each asserts the thread's ledger and the fact
   it forgot are already gone at the instant the memory store is closed,
   which is the only moment the question can be answered from. The
   watcher is scoped to the store the boot opened, because this file
   writes rows through stores of its own. Run against the purge wiring
   dropped, the exit callbacks reversed, and both at once.

4. **P2: the promised memory-egress disclosure was missing.** The plan
   requires the concepts Memory section and the README's security prose
   to say that storage is local while injected content follows the
   active LLM provider, with `server.local_only` as the guard; neither
   said it.

   *Resolution* (`ac4ba5e6`): adopted, scoped to what this milestone
   ships. Both places carry the same two-part answer: as storage, memory
   never leaves the deployment's own database; as prompt content, it is
   read into every reply and follows the provider's egress exactly as
   the transcript and the persona do, under the same `local_only` guard.
   The plan's fuller statement also covers the lookup and the
   sibling-agent consequence of device scope, and neither exists yet:
   those sentences arrive in M3 with the tools they describe, which is
   the one place this resolution is narrower than the finding.

5. **P3: the README still counted three unconditional builtins.** There
   are five since the state tools landed.

   *Resolution* (`9a8b51c1`): adopted whole, count and explanation
   both: the three memory tools are unconditional together, because an
   agent offered a ledger it cannot write would be read one it had no
   way to change.

### Verification after the review round

- `uv run ruff check .`: clean. `uv run mypy`: clean.
- `uv run pytest tests/unit -q`: 4689 passed, 19 skipped.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: the same.
- `uv run pytest tests/integration -q`: 228 passed.
- The five generated documents regenerate to their committed bytes, the
  census was regenerated after each README edit, and
  `python scripts/check_doc_links.py .` checked 172 files with no
  failures.
- Every guard above was run against its mutation before being trusted:
  the concurrent dispatch restored for the two state tools; the sweep
  put back in front of the record; and the purge wiring dropped, the
  exit callbacks reversed, and both at once.

## M3: scoped facts and the editing tools

PR #361.

### What landed

In the order the commits tell it: the prompt's read bounded to the block
it renders, the storage cap grown past it, and then the tool family one
pair at a time, the device scope proven end to end, and the documents.

- **The prompt read is bounded in the statement.** `_newest` asks the
  database for the newest `CORE_LINES` active facts of an owner, and
  `_core` is left with the byte bound alone. It is the two-tier shape
  made real rather than an optimization: the agent scope now holds a
  thousand facts and the block renders forty, so reading the scope to
  slice it would spend on every round exactly what the split exists to
  save.
- **`read_for_prompt` answers a caller with no device and no thread,**
  and reports only the scopes it reached for. That is what the prompt
  preview is, and it is now the preview's own call: `GET
  /runtime/agents/{name}/prompt` renders the block a reply would carry
  rather than the whole scope, which it had been over-reporting since
  the core existed.
- **`MAX_LINES` and `MAX_BYTES` are 1000 and 65536.** The two hundred
  lines in eight kilobytes they replace were a context budget wearing
  another name: every stored line was injected, so what an agent could
  accumulate was bounded by what a small model could be told. They bound
  accumulation now, and nothing else.
- **`remember` names a scope and answers a number.** The enum is
  `FACT_SCOPES`, the default is the agent's own, and the owner of a
  device fact comes off the session rather than out of the arguments,
  exactly as the ledger tools' thread does. The confirmation is
  `Remembered [id]: text`, because the number is otherwise nowhere: the
  injected block shows none.
- **`update_memory`, `forget`, `restore_memory` and `recall`,** with
  their names in `BUILTIN_TOOL_NAMES` and their descriptions written
  from the issue's worked examples: what belongs to the device, that
  the words a removal answers with are to be said out loud, that
  `restore_memory` with no number is the last thing forgotten, and that
  `recall` is where the numbers come from. A numbered call is tried
  against each memory the session can reach, the agent's own and then
  the device's, and the store's ownership predicates are what bound it.
  `recall` runs off the event loop like the prompt's own read.
- **The numbered three join `ORDERED_TOOL_NAMES`.** M2's review round
  made the two state tools run one at a time in the order the model
  issued them, because both address an entry by a key the model chose.
  A number is the same kind of identity: a correction and a removal of
  one fact in a round decide each other, and a removal that overtook the
  correction beside it would answer with words the correction had
  already replaced, which the agent then says out loud. `remember` is
  still unordered, since it appends, and `recall` changes nothing.
- **The device scope end to end.** Two agents on one board, one told
  something about the room, the other's own prompt carrying it, driven
  through the simulator across two server runs, since the binding order
  is the only lever a simulator conversation has over which sibling
  speaks.
- **The documents.** The concepts page's Device paragraph says what a
  board's own notes are and that moving hardware loses only those; its
  Memory section's remaining markers become current behavior and it
  answers the egress question; the glossary gains its Memory entry; the
  server README carries the family in both spellings, the caps, the
  block-and-lookup split and the storage-here-egress-with-the-prompt
  paragraph; the changelog announces the family, the cap growth, the
  refusal and the four reserved names. The census was regenerated after
  the README edits.

### The pins that translated

- the offered-tool ordered literals in `test_session_tools.py`, three of
  them, which grow the five names between `remember` and `set_state`;
- `DUE_BUILTINS` in `tests/integration/test_tools.py`, the same names;
- the reserved-entry-name parametrizations in `test_tool_names.py` and
  `test_config_tools.py`, which now walk `RESERVED_ENTRY_NAMES` instead
  of listing it: the set is what the rule is written from, and a listed
  copy is the second structure that has to agree with it;
- the `Remembered:` confirmation, which gains its number;
- `test_the_memory_read_happens_off_the_event_loop`'s replacement
  signature, for the conversation that may now be None;
- every whole-scope `read` assertion in `test_memory_store.py`, which
  became either the injected block (through one `injected` helper that
  speaks the preview's own call) or the rows, depending on which of the
  two the test was ever about;
- every `store.remember(...)` in the suites, which is
  `store.add(MemoryScope.AGENT, ...)` now;
- the prompt route's description in `api-openapi.json`, regenerated;
- M2's `the_first_write_parked` gate, which now takes the store calls to
  hold rather than naming the ledger's two, so the numbered case is the
  same arrangement with different tools.

### The pins that retired

Two, and both are a promise this project stops making.

- **`test_remembering_a_fact_over_the_byte_cap_still_keeps_it`.** #314's
  released edge: `remember` kept a single fact larger than the whole
  cap, because the prune never goes below one fact, which left that
  scope over its own bound for as long as the fact lived. M1 kept it
  deliberately and said the refusal would arrive in the milestone whose
  changelog announced it. This is that milestone: the refusal is on
  every door, the changelog says so, and the test goes with the promise.
- **The whole-scope read itself.** `read(agent)` was the sentence #314's
  callers spoke, and the plan let it live while callers existed. Its
  last caller was the preview, which had to start rendering the core to
  stay a preview of a real session, so it retired here rather than
  surviving as a sentence only tests speak. `MemoryStore.remember` went
  the same way in the same milestone: once the tool needed the number,
  `remember` was `add` with the id thrown away, and a pass-through that
  can hold a different rule than the door beside it is worse than no
  sentence at all.

### Deviations from the plan

Six, and none changes what the milestone ships.

- **A correction and a removal search the two memories in the tool
  layer; a restore is addressed by both of them at once.** The plan
  states the predicates as `(scope, owner)` on each store operation, and
  a model names a number and not a memory. For the two that address one
  row by its number the search is the tool's: `_wherever_it_is` tries the
  agent's memory and then the device's, each call keeping its own honest
  scope, and the last refusal travels because the store's sentence is
  identical whichever memory refused. A number names at most one row in
  the whole store, so trying them in turn cannot answer differently from
  asking about both at once.
  A restore with no number is not that: it is a choice among rows, and
  which memory holds the newest of them is the answer rather than the
  question. So `restore` takes the owners as a set and picks the newest
  held row across all of them, ordered by when it was forgotten with the
  id breaking a tie, in one transaction under the chain's lock. That is
  the milestone's own correction: it shipped for review deciding one
  memory at a time, which answered a conversation that had forgotten one
  fact and one note with the older of the two, and the review round is
  where that was found and fixed.
- **`_written` takes the scopes a write reached for, not one scope.**
  The shape `_read` already had, for the reason it had it: a restore
  addressed to both memories cannot name the one its statement never
  found, so a failure reports every scope it reached into and a report
  naming one would be a guess.
- **`read_for_prompt` takes `conversation: str | None`,** the widening
  M2 gave the device. A prompt assembled outside any conversation has no
  ledger, and saying so with None is what lets the preview speak this
  call at all.
- **`_device_of` asserts rather than refusing.** The handshake reads the
  device's identity before a connection can be accepted, so a tool call
  with no device behind it is a defect here rather than something to
  tell a model about, which is exactly the rule `_conversation_of`
  already keeps.
- **The result wordings the plan did not fix.** `Corrected [id]: text`
  and `Brought back: text`. The restore answers with no number on
  purpose: the no-number door does not know one until the row is found,
  and the store's restore answers with the words rather than the row.
- **Two arguments are read leniently.** A number sent as a string of
  digits is accepted, because a model reads it out of a lookup line and
  hands it back as it read it; `permanently` is honoured only when it
  arrives as an actual true, because a misread argument should fail
  towards the removal that can be undone.

### Discoveries

- **No unit-level session had a device at all.** `tests/support/sessions`
  transcribes `run`'s composition, and the one line it had never
  transcribed was the first one: the edge sets the MAC off the handshake
  before anything else can happen. Nothing had noticed, because nothing
  below the websocket had ever asked what device it was on. Setting it
  then failed on a second thing: `DeviceId` accepts only the canonical
  form `normalize_mac` answers with, one fixture MAC is written in
  capitals, and an identity that cannot be built refuses the whole
  emission, which the lanes fail any test for producing. The helper
  normalizes, exactly as the edge does.
- **The block's line bound was the slice and nothing else.** Moving the
  bound into the statement was only safe because `_core` stopped
  slicing; the mutation that drops the `LIMIT` renders the whole scope,
  and the boundary test catches it. Two places that both bound would
  have been the third structure to keep in agreement.
- **`__context__` alone does not catch a chained raise.** The refusal
  that travels out of the numbered search is built inside the arm and
  raised after it, and the mutation that raises `from` the caught one
  passes an assertion about `__context__`: `raise X from Y` sets
  `__cause__`. Both are asserted now.

### Verification

- `uv run ruff check .`: clean.
- `uv run mypy`: clean (5 source files, the events package).
- `uv run pytest tests/unit -q`: 4726 passed, 19 skipped.
- `uv run pytest tests/unit -q -n auto --dist loadfile`, the shape CI
  runs: the same.
- `uv run pytest tests/integration -q`: 229 passed.
- The five generated documents (`domain-config.md`,
  `conversations-schema.md`, `events.md`, `api-openapi.json`, `cli.md`)
  each regenerate to their committed bytes; `api-openapi.json` is
  regenerated in this change and the other four are unchanged. The
  command-spellings census was regenerated after the README edits, and
  `python scripts/check_doc_links.py .` checked 172 files with no
  failures.
- Every new guard was run against the mutation it exists for before
  being trusted: the ordered dispatch reduced to the ledger's two, which
  lets a removal overtake the correction beside it; the prompt read
  reporting the vocabulary rather than
  the scopes it reached; the core read without its bound; the numbered
  search reaching the agent's memory alone; that search raising the
  refusal from inside its handler; the scope argument ignored, against
  the device-note case end to end; and the lookup run on the event loop
  rather than off it.
- Not verified: the `image` job, which builds and smokes the container.

### PR review round

External review of the branch as pushed to PR #361, at `08792dc0`
against `origin/main`: backend codex (codex-cli 0.151.0), model
gpt-5.6-sol, read-only sandbox, 2026-08-30, runtime 4m39s. Five
findings, two P1, one P2 and two P3, verdict as received: mergeable
after the listed fixes. Condensed below as received, each with its
resolution and the commit that landed it.

The two P1s share a shape, and it is the one this milestone was most
exposed to: a rule about order stated over the wrong set. One said which
tool calls have to run in the model's order and left out the tool that
appends; the other said which memory to look in first and turned a
question about rows into a question about scopes. Both were written down
in the implementation doc as decisions, which is what made them
reviewable.

1. **P1: `remember` is reordered ahead of the fact edits at the cap.**
   `ORDERED_TOOL_NAMES` excluded it on the grounds that appending is
   order-free, while the loop runs every ordered call before the rest.
   At a one-line cap a model-issued `remember` followed by
   `update_memory` therefore ran as the update and then the append,
   whose prune deleted the row the correction had just written; both
   answered success, so the model was told a correction landed that
   nothing kept. `forget` inverts the same way. Include `remember` in
   the ordered lane and add cap-boundary tests for both pairs.

   *Resolution* (`0f824079`): adopted whole. Every write to a memory is
   in the lane now, which is the smallest rule that is true: what
   couples an append to an edit is the prune a full scope runs on every
   write rather than the address either of them names, and a rule that
   let one overtake the other would have to know which scope was full to
   know whether it mattered. `recall` stays out, since it reads. Two
   tests drive the pairs through a real round on a scope with room for
   one fact, and each fails against the mutation that takes remembering
   back out.

2. **P1: a restore with no number did not bring back the last thing
   forgotten.** The tool asked the agent's memory for its newest held
   row and the device's only if that refused, so a conversation that had
   forgotten a fact and then a note about the room brought the fact
   back. The implementation doc acknowledged the ordering and claimed it
   changed nothing shipped. Put the cross-scope decision in
   `MemoryStore`, select the newest eligible held row across both
   memories in one serialized transaction, and test the mixed case.

   *Resolution* (`a019baee`): adopted whole. `restore` takes the
   memories the caller may reach as a set and picks the newest held row
   across all of them, ordered by `forgotten_at` with the id breaking a
   tie, in one transaction under the chain's lock; it prunes the memory
   the row is actually in, and the id door is bounded by the same set.
   `_written` grew the shape `_read` already had, a sequence of scopes,
   because a statement that never found its row cannot name the memory
   it was in. The deviation note is corrected rather than softened: it
   now says the milestone shipped this wrong and the review round is
   where it was found. The mixed test is arranged so that neither the
   ids nor a per-memory order could answer it: the note is the older row
   and the newer removal.

3. **P2: the restore tool's production dispatch was untested.** The
   restore tests called the executor directly, so removing the dispatch
   arm left the suite green while the claimed "each tool offered,
   executed" coverage stood.

   *Resolution* (`7862ce2d`): adopted whole. The undo runs as a model
   reaches it, `forget` in one reply and `restore_memory` in the next,
   both as calls the session routes, with the tool result and the
   restored row asserted; it fails with the dispatch arm removed. A tool
   the runtime cannot route is a tool an agent cannot speak, whatever
   the function behind it does.

4. **P3: the glossary stated the precedence backwards.** It listed the
   scopes agent, device, conversation and then said they inject "in that
   order", which is the settled `conversation > agent > device` reversed.

   *Resolution* (`1333c2b3`): adopted. The entry names the order rather
   than pointing at the list it just gave.

5. **P3: the changelog overstated prompt compatibility.** It claimed any
   agent with fewer than 40 facts sends exactly the prompt it sent
   before, and the block bounds bytes as well as lines: fewer than 40
   facts can run past `CORE_BYTES` while fitting the former 8 KiB
   storage cap.

   *Resolution* (`1333c2b3`, with finding 4): adopted. The claim names
   both limits and says what the one moving case does, which is inject
   less and keep everything.

### Verification after the review round

- `uv run ruff check .`: clean. `uv run mypy`: clean (5 source files,
  the events package).
- `uv run pytest tests/unit -q`: 4730 passed, 19 skipped.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: the same.
- `uv run pytest tests/integration -q`: 229 passed.
- The five generated documents regenerate to their committed bytes, the
  command-spellings census was regenerated after the changelog and
  glossary edits, and `python scripts/check_doc_links.py .` checked 172
  files with no failures.
- Every guard above was run against its mutation before being trusted:
  remembering taken back out of the ordered lane, against both cap
  cases; the restore ordered by id alone, and decided one memory at a
  time, against the mixed case; and the restore dispatch arm removed.

## M4: the operator surface

PR #362.

### What landed

In the order the commits tell it: the page contract the second
paginated namespace needed, the routes, the noun in front of them, the
documents, and one refusal the framework was answering wrongly.

- **`paging.py`,** one home for what a page on the gated `/api` is: the
  two bounds, the row-id ceiling, the two refusal sentences, the
  whole-number parse and the one-row-more trick. The conversation
  namespace wrote all of that and a second copy beside the memory
  routes would have been the second structure that has to agree with
  it. `conversations/api.py` asks this module now and re-exports the
  two bounds under the names its suites already read them by; what
  stays with it is the one cursor this contract does not cover, the
  thread listing's pair, whose activity half is an instant.
- **The store's operator door,** eight functions beside `purge` and on
  a caller's connection for the same reason: `owners`,
  `conversations_holding_memory`, `facts_of`, `correct`, `erase_fact`,
  `erase_facts`, `ledger_of` and `clear_ledger`. Two rules run through
  them and both are this door being a different door rather than a
  second copy of the tools'. Every deletion is a hard delete, because
  the held area is the spoken undo and belongs to the conversation that
  forgot the fact. And a correction is held to the same cap invariant a
  tool's write is: refused where its own line will not fit, and the
  scope re-pruned inside the same transaction with the corrected row
  protected from it.
- **`memory/api.py`,** thirteen routes in the conversations-API shape,
  registered from `_application()` with `_problems` handed in. Three
  owner listings keyset-paginated on the owner text, two fact listings
  paginated on the id, a correction and two deletions per fact scope,
  and the conversation's ledger read whole and cleared one entry or all
  of it. `MEMORY_PROBLEMS_INSTEAD` says what each status means here,
  which for the 404 is a fact number or a ledger entry rather than an
  entity of the stored configuration.
- **The two values that never ride a URL.** A corrected fact and a
  ledger key travel in a request body, parsed by this module's own
  exact-shape readers, and the plan's finding 8 is what the assertions
  are written from: no request either the routes or the CLI build
  carries either in a path or a query string, asserted on the requests
  themselves rather than reasoned about.
- **`ApiRuntime` gains two fields,** a per-request reader and a
  per-request write-transaction factory, both built in
  `build_api_runtime` from the `DatabaseConfig` it already has. `app.py`
  needed no change: the composition root hands that config over
  already, which is what the conversation namespace's own two fields
  are built from.
- **The `memory` noun,** singular, three verbs, with the scope as the
  first address segment because it is the first segment of every one of
  the routes' own paths. `memory list agent` is who is remembering
  anything and `memory list agent poet` is what one of them remembers,
  the same words one level up; `memory set agent poet 7` reads the
  corrected text from `-f` or from standard input; `memory delete`
  takes a number or `--all`, and for a conversation reads one entry's
  name from standard input. Registered `destroys=True`, so it asks at a
  terminal and takes `--force`.
- **What it prints.** Owners and conversations are borderless tables,
  because every field of them is short. A fact and a ledger entry are
  blocks, and the value itself is printed whole through `printable`
  with no bound, which is the rule that module states for a value that
  IS what the reader came for and the one `agent preview` already
  draws: this command exists to show what an agent will be sent.
- **The documents.** `api-openapi.json` and the generated region of
  `cli.md` regenerate; the surfaces page counts five surfaces and
  memory's row answers the retention question in three parts; the
  server README's `vinga_ro` sentence is cashed in and the memory prose
  gains what an operator types; the changelog carries the whole door.
  `docs/README.md`'s reference bullets stay true and were checked
  rather than assumed: there is no new generated page, because nobody
  but the server reads memory's tables, and what is published is the
  addressed surface in the two documents that already have bullets.
  The census was regenerated after the README edit.
- **`test_api_contract.py` gains no exclusion.** All thirteen
  operations are covered by an act, which is what the three `selects`
  hooks are for: a row's `does` is every act it can reach and its
  `selects` is the one this invocation runs.
- **The two lanes that drive every registered command drive these
  three.** The live lane seeds memory through the store an agent writes
  through, reads the owners and one agent's facts back over a real
  uvicorn, pipes a correction in, and deletes a fact and a ledger; the
  wheel lane does the same from a bare install, which is where the
  piped correction is worth most, since what a subprocess reads on
  standard input is what a script pipes into one. The refusal table
  gains the family's own row: an addressed deletion of a number nothing
  has, whose sentence is the server's and repeats neither the number
  nor the owner.

### Deviations from the plan

Seven, and none changes what the milestone ships.

- **The operator operations are module-level functions, not methods on
  `MemoryStore`.** The plan says to give the store the operations it
  lacks, and this is M2's `purge` deviation again for the same reason:
  the routes open a connection for the length of one request through
  `db`, so there is no store object to call a method on, and a store
  would be a parameter no route has to give. The SQL still has one
  home, which is the rule the plan is stating.
- **The state deletion holds `erasure_order()` and consults no
  dead-thread set.** The protocol's two halves exist for different
  reasons and only one of them applies here. The order is taken,
  because it is what makes this deletion and a transaction erasing the
  same thread serialize rather than interleave, so the count answered
  is a count of rows this request took. The dead set is not consulted,
  because what it exists to stop is a write recreating a row for a
  thread that is gone, and every statement this door issues against a
  thread is a delete. It is also process state on the store instance,
  which a per-request connection has no way to reach.
- **`paging.py` is new, and the conversation namespace changed to use
  it.** The plan names neither. It is the design guide's own rule
  applied at the moment a second namespace paginates: two structures
  that must agree are one structure with a bug pending.
- **`request_body` moved from `config/api.py` to
  `config/responses.py`.** Both API modules build one now, and
  `memory/api.py` may not import `config/api.py`, which imports it to
  register these routes. Nothing in the function is a FastAPI concern,
  so it sits in the module that pays for pydantic alone; it gained a
  `required` argument for the one optional body, the state deletion's.
- **A fact's number is a string path parameter, parsed here.** Declared
  as an integer it left two refusals wrong, and both are recorded above
  in the commit that closed them: the framework's own refusal for a
  path that will not parse is the body-shaped sentence this API
  substitutes for its validation, and a number past the identity
  column's range reached the driver and was answered as a storage
  failure.
- **`memory set conversation` is refused with a sentence of its own.**
  The plan gives the ledger a read and a clear and no correction, and
  the missing verb is a refusal rather than an unknown-scope error: what
  is in a ledger is written by the agent as the conversation goes, and
  an operator's correction of a live position would be a move nobody
  made. The sentence says so and points at the clear.
- **`ERASED_COUNTS` grew `facts` rather than getting a second tuple.**
  What that tuple states is the order counts are read in, and there is
  one such order; a memory erasure answers one of them and the block
  prints the counts its answer carries.

### Discoveries

- **Two doors were normalizing a ledger key separately.** The tool's
  `clear_state` and the operator's `clear_ledger` each wrote
  `key == _one_line(key)`, which a mutation run found by accident: the
  harness replaced the first occurrence and the guard it was aimed at
  stayed green. `_clear_state` calls `clear_ledger` now, so the
  statement has one home and what differs between the two doors is the
  guard around it.
- **`_refuse_the_oversized` could not stay a refusal.** Two doors ask
  the same question and raise different things: a model's write leaves
  as the `ValueError` the tool layer rephrases, and an operator's
  correction leaves as the refusal this API answers a 422 with. It is a
  predicate now, `_oversized`, and each door raises its own.
- **A mutation harness has to assert its site is unique.** The one that
  is not is the one that proves nothing, silently, because it mutates
  something the selected test never runs. Every mutation below was
  re-run under a count assertion after that.

### The rebase onto M3 as it merged

M3 gained six commits after this branch was cut, five of them review
fixes, and this branch was rebased onto them. Three conflicts, and each
is recorded rather than summarized because two of them are semantics
rather than text.

- **`_clear_state`'s call into `clear_ledger`.** The one real merge.
  This milestone had made the tool's ledger clear delegate to the
  operator's statement, so the key's normalization has one home; M3's
  review round had widened `_written` to take the scopes a write reached
  for rather than one scope. Both are kept: the delegation stands and
  the call passes `(MemoryScope.CONVERSATION,)`. The guard was re-run
  against the delegation being dropped, and against `_written`'s own
  suite, before the resolution was trusted.
- **The census manifest**, regenerated on the rebased tree through its
  own generator rather than hand-merged, which is the only way a
  manifest of every quoted spelling in the repository can be right when
  both sides added spellings.
- **The implementation doc**, where M3's own review-round section and
  this section wanted the same lines. Both are kept whole and in order,
  M3's round before M4's milestone.

Nothing else conflicted, and the four things M3's post-branch commits
made true are true here: `remember` is in `ORDERED_TOOL_NAMES` beside
the edits, `MemoryStore.restore` takes the reachable memories together
and picks the newest held row across them, the glossary states the
conversation-agent-device order, and the changelog's compatibility claim
names both of the block's bounds. The first two were re-run against
their own mutations on the rebased tree.

### Verification

- `uv run ruff check .`: clean.
- `uv run mypy`: clean (5 source files, the events package).
- `uv run pytest tests/unit -q`: 4891 passed, 19 skipped.
- `uv run pytest tests/unit -q -n auto --dist loadfile`, the shape CI
  runs: the same.
- `uv run pytest tests/integration -q`: 233 passed.
- The five generated documents (`domain-config.md`,
  `conversations-schema.md`, `events.md`, `api-openapi.json`, `cli.md`)
  each regenerate to their committed bytes; `api-openapi.json` and
  `cli.md` are regenerated in this change and the other three are
  unchanged. The command-spellings census was regenerated after the
  README edit, and `python scripts/check_doc_links.py .` checked 172
  files with no failures.
- Every new guard was run against the mutation it exists for before
  being trusted, nineteen of them: each of the four cursors ignored;
  the correction not re-pruning, reaching a held fact, and accepting an
  over-cap line; the addressed deletion reaching any owner; both 404s
  dropped; the fact number left unparsed and unbounded; the board in
  the path not normalized; the ledger key not normalized; the
  whole-scope flag not required; a positional added for the corrected
  text; the stored value bounded to a cell; a number allowed to address
  a ledger entry; the scope left unchecked; the ledger correctable; and
  the key put back into the path. The one the rebase added is the
  ledger delegation dropped, which is what stands in front of the key's
  normalization now that both doors issue one statement.
- Not verified: the `image` job, which builds and smokes the container.
