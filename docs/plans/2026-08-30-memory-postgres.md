# Move agent memory from files to Postgres

Plan for [#314](https://github.com/rafacm/vinga/issues/314), the
storage half of #83. Implementation notes land in the companion
`2026-08-30-memory-postgres-implementation.md`, one section per
milestone, appended in the change that ticks the milestone here.

## Goal

Remembered facts move from Markdown files under `memory.dir` into
Postgres, in a schema and migration chain the server owns, while
every caller keeps the interface it has: `read(agent)` returning the
same rendered fact lines, `remember(agent, fact)` with the same
normalization, the same refusal, and the same caps. Memory joins the
transactional durability, backup, and multi-process concurrency
model the other two stores already live in: a fact written through
one database connection cannot be lost by a concurrent write through
another, which the per-agent `asyncio.Lock` plus tmp-file rename
could never promise across processes. The file-backed implementation
and the `memory.dir` deployment requirement retire. Scopes, stable
fact identity, update, delete, undo, lookup, and the operator
surface remain #83's, untouched here.

## The issue's decisions, restated

- This is the storage foundation for #83, not the scopes, update,
  delete, lookup, or operator-surface work described there.
- The caller interface and the user-visible `read` and `remember`
  semantics are preserved; callers see no database vocabulary.
- Bounded append and pruning happen transactionally, with limits
  equivalent to today's or an explicitly documented replacement.
- Hard cutover: existing memory files are never imported, silently
  or otherwise, and the pre-release reset behavior is stated
  plainly. #83 already decides against migrating them.
- Memory survives restart and is covered by the database backup
  story.

## Open questions, resolved

**Memory gets its own schema and its own chain.** Schema `memory`,
chain `MEMORY_CHAIN` with `advisory_key(3)`, baseline
`2001_agent_memory` opening the unused `2xxx` numbering block.
Neither existing schema fits, and the code states the deciding rule
twice ("which schema a store lives in is a fact of that store"):
`domain` holds configuration entities and its advisory lock
serializes writers whole, so a `remember` riding that chain would
wait behind an `apply` transaction that is deliberately unbounded;
`record` holds what was said, is shaped by session and thread
retention that memory does not have, and is granted to the analyst
role, which the next resolution declines for memory. A third chain
is fully supported by the `StoreChain` seam (#283) and gives #83's
schema growth (scopes, tombstones, identity) a seat whose migrations
live beside the store they shape. The chain's floor consequence is
handled honestly: `product-promises.md` names the current baselines
in-place upgrades begin at, and it gains `2001_agent_memory` as the
third, citing this plan as the record; adding a chain is an ordinary
forward extension, not a priced exit, because nothing existing is
re-cut.

**An existing deployment upgrades by running the provisioning file
before the M1 image, and a boot that cannot create the schema
refuses with the fix.** `upgrade_to_head` creates a missing schema
itself, which needs `CREATE` on the database; the compose
development loop has it (the server role owns the database), but
the provisioning file's own contract supports a restricted runtime
role that does not, and that file runs only when a data directory
initializes, so an existing least-privilege deployment would meet
M1 as a boot that cannot create `memory`. The choreography is
therefore stated, documented in the deployment docs and the
changelog in M1: rerun the updated `deploy/postgres-init.sql`
administratively (it is repeatable by design, #283) before
deploying the M1 image, exactly the rerun-after-reset instruction
the recovery docs already carry; the file creates the schema with
`AUTHORIZATION` to the server role, and boot then verifies rather
than creates. A boot whose schema creation is refused by the
database answers with a fixed value-free sentence naming the
provisioning rerun, through the migration-failure classification
that already owns that surface. The integration lane proves the
whole path: provision the previous two-schema shape, store rows in
both, run the updated file as the administrator, then migrate and
boot all three chains as the restricted role with the rows intact;
the existing schema-ownership and restricted-role assertions extend
to the third schema alongside the `vinga_ro` denial.

**The analyst role does not read memory.** `deploy/postgres-init.sql`
creates the `memory` schema with `AUTHORIZATION` to the server role
and grants `vinga_ro` nothing on it, with the same explicit `REVOKE`
the `domain` schema carries. The reason is not that remembered facts
are more sensitive than the transcripts `vinga_ro` already reads;
it is that the operator read surface for memory is #83's deliberate
design (addressed by scope, over the API), and granting the raw
tables early would freeze a contract #83 is about to reshape.
Narrowing a granted read later breaks someone; widening later is one
additive line in the same file. The comment beside the revoke names
#83 as the read door.

**Memory is on whenever the server runs; the `memory:` section
retires whole.** Today the section's presence is the toggle: no
`memory:` means no `remember` tool and no injected block, because a
file store needed a directory an operator had to choose. The
database needs no such choice, the schema is migrated at every boot
like the record schema is, and `product-promises.md` already names
memory a core capability of the local pipeline. So `MemoryConfig`,
the `memory` field on `FileConfig` and `Config`, and the
`Composition.memory: MemoryStore | None` optionality all retire
together: the store is always built, `remember` is always offered,
and an agent with no facts stored gets no block, exactly as an
empty file renders today. This is the one deliberate behavior
change in the plan, flagged for review as such: a deployment that
chose to run without memory loses that choice until #83's per-agent
control arrives, and the changelog says so under Changed rather
than burying it in the storage move. A configuration file still
carrying `memory:` refuses the boot through the loader's existing
moved-section machinery, one fixed value-free sentence saying
remembered facts live in the database now and the section retired;
without that arm, the pydantic extra-key refusal would answer with
a validation error that never says what happened to the section.
The environment spellings retire the same loud way: `VINGA_MEMORY`
and every case-insensitive `VINGA_MEMORY__...` variable is today a
valid door onto the section, and once the field is deleted
pydantic-settings would ignore them silently, `extra="forbid"`
notwithstanding, which is exactly the hole
`_check_moved_environment` exists to close. It gains the memory
arm: the refusal names the variable's name and the retirement, and
never its value, with tests proving the name is reported, the
value is absent from every surface, and a mixed-case spelling
cannot slip through unrefused.

**The store moves to its own package, and the event channel moves
with it.** `vinga_server/memory/` with `schema.py`, `store.py`
(`MemoryStore`, `MEMORY_CHAIN`, `open_memory`, the cap constants),
and `migrations/`; `tools/memory.py` deletes. A chain needs a
package to hold its migrations, and a store that owns a schema is a
domain concept, not a tool helper: the `remember` tool in
`tools/builtin.py` stays where it is and keeps calling the store it
is handed. The events catalog's `MEMORY_CHANNEL` becomes
`vinga_server.memory.store`, because the channel is the emitting
module's logger name and a channel that names a deleted module
would be a lie; `memory_unreadable` keeps its name, level,
template, and `(agent, error)` fields. The characterization
baseline pins the emission site by module and qualname, so the move
is a recorded baseline regeneration, not a silent drift.

**Caps are unchanged, enforced in the write transaction.**
`MAX_LINES = 200` and `MAX_BYTES = 8192`, the same constants with
the same names, applied by the same algorithm (keep the newest 200,
then drop oldest while the rendered lines exceed the byte cap,
never below one fact). `remember` inserts and prunes in one
transaction, so no reader ever sees an over-cap state and a crash
between insert and prune cannot leave one. The constants stay
module-level and are read at call time, which is what lets the
existing cap tests keep their monkeypatch shape.

**Concurrency is the chain's advisory lock; reads never take it.**
The write engine from `open_at(settings, MEMORY_CHAIN)` takes
`pg_advisory_xact_lock(advisory_key(3))` on begin, exactly the
single-writer discipline the other stores translated to in #283, so
two `remember` calls through independent connections serialize
whole and the read-count-prune arithmetic is correct without any
isolation-level reasoning. A writer that cannot take the lock
inside `LOCK_TIMEOUT_MS` fails busy, classified by the one
classifier `db/` already owns. Reads go through a separate
`read_engine` (repeatable read, read-only), so a reply's memory
read never waits on a writer, which preserves the file store's
property that reading is cheap and unblocking. Both engines are
built once in `open_memory` and disposed on the application's exit
stack; the per-agent `asyncio.Lock` map retires, its job done
better by the database.

**`read` keeps its signature and its rendering, byte for byte.**
Synchronous, returning `"- {fact}"` lines joined by newlines with
no trailing newline, empty string for an agent with nothing stored,
called off the event loop by the two callers that already do
(`_system_prompt` per round, the prompt preview). The per-round
freshness semantics (`with_memory` re-reads while the know-how half
stays cached) are pinned by the existing session suites and do not
move. Rows render in insertion order, `ORDER BY id`, which is the
file's line order.

**Failure containment translates, and the write path gains the
event the read path already has.** `read` on an unreachable
database emits `memory_unreadable` with the failure's class name
and returns `""`, never raising into the prompt path and never
rendering driver text, which is the same contract the corrupt-file
arm keeps today, with the same reason: psycopg failures quote the
DSN they tried, and the DSN carries the password. `remember` on a
database failure emits a new `memory_unwritable` variant (WARNING,
`agent`, `error` as class name, its decision site the one `except`
arm in `remember`) and raises a sanitized error built inside the
handler and raised after it, cause severed, so what reaches the
tool-result text the model sees and speaks is a fixed sentence
carrying no value. The `ValueError` for an empty fact keeps its
exact sentence; it is a caller mistake, not a database failure, and
the existing tool-refusal test pins it.

**A fact row carries a timestamp.** Columns: `id` (BigInteger
identity, primary key: ids are never reused, which #83's stable
identity and tombstones will lean on), `agent` (text), `at`
(ISO-8601 UTC text, the house timestamp shape), `fact` (text), with
one index on `(agent, id)` for the ordered read and the prune walk.
`at` is not read by anything in this issue; it exists because a row
written without its moment cannot recover it later, an operator
inspecting orphaned memory (agent rename still orphans rows, as it
orphaned files, and the docs keep saying so) deserves to know when
it accrued, and #83's tombstone lifetime question needs it. That is
the whole schema; scope columns are #83's to add.

**No generated schema reference for memory, this issue.** The
record store's generated page exists because operators query that
schema as `vinga_ro`; nobody but the server can read `memory`, so
the page would document a surface with no external reader while
freezing a raw-table shape #83 reshapes. The server README's backup
section says the schema exists and `pg_dump` covers it, which is
the whole operator-facing fact. When #83 builds the operator
surface, its API reference is the document that earns generating.

## Design footprint

- `vinga_server/memory/` is a new package and passes the deletion
  test: its callers (the composition root, the reply path, the
  builtin tools, the preview route) get read-and-remember over a
  migrated, advisory-locked, capped store whose engines, SQL,
  transaction shape and failure classification they cannot see;
  inlined into any one caller, the other three would reacquire
  database vocabulary the interface exists to hide.
- `MEMORY_CHAIN` reuses the `StoreChain` seam exactly as #283 cut
  it: one declared type carrying schema, migrations and lock key,
  beside the store that owns it. Boot deepens by one `open_memory`
  in the same shape as the two opens beside it.
- `composition.py`, `app.py`, `tools/source.py`,
  `runtime/pipeline.py` shed an Optional: four surfaces stop
  carrying `MemoryStore | None` and the is-it-configured branch,
  and `BuiltinTools` stops deciding whether to offer `remember`.
- `tools/memory.py`, `MemoryConfig`, the `memory` recombination in
  `loader.py`/`boot.py`, and `path_for` (file vocabulary on the
  interface) delete.

## Documentation footprint

- **M1** (the chain, no behavior change):
  `docs/architecture/product-promises.md` gains the third baseline
  in the floor bullet, citing this plan, and its CI sentence stops
  saying "both chains"; every other current-count statement moves
  in the same milestone that falsifies it: `db/__init__.py`'s
  module contract ("two stores and one database"), the autogen
  entry point's two-chains documentation and usage, and
  `docs/README.md`'s `postgres-init.sql` bullet counting the
  schemas;
  `deploy/postgres-init.sql` comments carry the #83 pointer;
  `CHANGELOG.md` Added.
- **M2** (the cutover): `vinga-server/README.md` (the builtins
  list's `remember` row, the `memory: dir:` block and the
  one-file-per-agent prose replaced by the database story, "memory
  is configured or it is not" retired, the backup sentence inverted
  since memory is now in the database `pg_dump` covers, the three
  agent-rename-orphan sentences kept true and reworded to rows, the
  capture-budget sentence dropping memory from the volume, and the
  reset stated in the operator's own terms, in the README and the
  changelog both: existing files under the old `memory.dir` are not
  read, not imported and not deleted by this release, database
  memory starts empty, and archiving or deleting the old files is
  the operator's deliberate act);
  `config.example.yaml` and `config.deploy.example.yaml` lose their
  `memory:` blocks in the same change as the schema change, per
  standing rule; `docs/concepts.md`'s whole Memory section is
  brought current, not just its storage sentence: both places it
  says memory is configured through the configuration reference
  come out, and the section states that memory is always present
  until #83 adds per-agent control;
  `docs/architecture/diagrams/plantuml/architecture-overview.puml`
  moves the memory side-store into the database; generated pages
  through their generators only (`docs/reference/domain-config.md`
  via `docgen.py`'s file-half prose and the agent entity note,
  `docs/reference/api-openapi.json` via the route descriptions,
  `docs/reference/events.md` via the catalog change);
  `CHANGELOG.md` Changed and Removed. The command-spellings census
  is re-run after the README edits per standing rule.

## Tests

- **Pins that hold unchanged, run green before and after**: the
  prompt assembly suite (`with_memory` is pure and untouched), the
  session prompt clock tests (a fact written between replies
  appears in the second, the half is not rebuilt, the read happens
  off the loop), the session tools suite (`remember` offered,
  executed, refusing bad arguments, fact in the next reply), the
  cancelled-invocation record test, the preview API's `memory`
  block, `prompt_assembled` still excluding memory.
- **Pins that retire with their mechanism, named one by one in the
  implementation doc**: filename sanitization, lazy directory
  creation, tmp-file rename atomicity, the corrupt-file byte
  fixtures, the `memory=None` no-tool and no-hop cases (no such
  configuration exists after M2), the example-file `memory:`
  mentions.
- **Pins that translate**: the cap tests keep their monkeypatch
  shape against the same constant names; the unreadable family
  swaps a corrupt file for an unreachable database and keeps every
  assertion (class name only, no exc_info, planted credential
  absent from every record in both log formats); the integration
  fact-persists test reads the store back instead of the file.
- **New, named for the acceptance criteria**: persistence across a
  store close and reopen (restart); transactional pruning (the store
  prefilled to its cap, a test-only `BEFORE DELETE` trigger on the
  facts table raising, `remember` called through its public
  interface and failing sanitized; an independent connection then
  sees the exact pre-call count and rendering, proving the insert
  rolled back with the pruning rather than surviving it); independent concurrent writers (two `MemoryStore`
  instances over separately opened engines, the store prefilled
  exactly at the line and byte pruning boundary, both writes
  started concurrently; the assertion is the exact final survivor
  set, row count, rendering and byte bound, arranged so a missing
  chain lock would leave an over-cap state or a wrong survivor set
  rather than a passing test);
  database refusal, split by path because the paths differ: `read`
  fails only on a genuine read failure (the store handed a read
  engine whose backend is gone, or `USAGE` on the schema revoked
  mid-run) and answers `""` plus `memory_unreadable`, never
  raising; `remember` under the held `MEMORY_CHAIN` lock past
  `LOCK_TIMEOUT_MS` raises the sanitized busy failure and the tool
  result carries the fixed sentence; and the inverse property the
  design claims is pinned in the same family: `read` still answers
  the stored facts while another connection holds the chain lock,
  because reads never request it; the no-leak sentinel (credential-shaped database
  password and URL, driven through read failure, write failure and
  boot, asserted absent from tool-result text, every log record's
  `__dict__`, both log formats, and the exception chains).
- **Chain plumbing, in the siblings' shape**: a `test_db_open`
  analogue pinning `2001_agent_memory` as head and the expected
  columns; metadata-drift comparison against a migrated database;
  the autogen entry point taking the third chain; the lane's
  template migration and `_application_tables` covering the third
  metadata; the CI wheel step's third block (head literal, table
  inventory, identity column); the init-script integration
  assertions extended with `vinga_ro` having no `USAGE` on
  `memory`.
- The event-catalog baseline regenerates for the channel move and
  the new variant, as its own commit with the regeneration command
  in the message.

## Risks and mitigations

- **The reply path gains a database read per round.** Bounded by
  the cap (at most 200 short rows on an indexed walk through a
  pooled engine) and off the event loop like the file read was; the
  latency is accepted knowingly for a store that cannot be torn.
  If field use ever disputes it, caching is the wrong fix (the
  per-round freshness is pinned semantics); batching the read into
  the round's existing database traffic would be the follow-up.
- **A held advisory lock turns `remember` into a ten-second wait
  then a spoken failure.** That is the translation of the
  single-writer discipline, the wait is bounded by
  `LOCK_TIMEOUT_MS`, the failure is sanitized and retryable, and
  memory write volume (a tool call a human asked for) makes
  contention rare; the two-writer test proves the behavior rather
  than assuming the rarity.
- **The always-on decision is a behavior change riding a storage
  issue.** It is stated in its own resolution, its own changelog
  entry and its own review flag rather than implied; if review
  wants the toggle kept, the fallback is a boolean
  `server.memory.enabled` defaulting on, which changes one
  resolution without moving the schema.
- **Two milestones must each leave `main` releasable.** M1 ships a
  migrated, empty, unread schema, which is exactly the state the
  record schema already ships in when recording is off; M2 is the
  atomic cutover, one behavior change alone in review, the #283
  shape at a fraction of the size.
- **Literals move in CI.** The wheel step's hard-coded head set
  gains the third chain in M1; the workflow comment names the rule
  and the PR body links the green run.

## Milestones

- [x] **[M1: the memory chain exists](2026-08-30-memory-postgres-implementation.md#m1-the-memory-chain-exists)** (PR #357). `memory/schema.py` (the
  `facts` table and its index), `memory/migrations/` with `env.py`
  in the record chain's shape and baseline `2001_agent_memory`,
  `memory/store.py` in its permanent home from the first commit,
  holding `MEMORY_CHAIN`, the two engines, `open_memory` and the
  exit-stack disposal while staying behaviorally dormant (no `read`
  or `remember` yet, and no temporary forwarding anywhere), boot
  opening (and thereby migrating) the chain through it,
  `deploy/postgres-init.sql`'s third schema
  with the revoke and the #83 comment, the lane fixtures and the
  wheel step covering the third chain, the autogen flag, the drift
  and open suites, the upgrade choreography (deployment docs and
  changelog naming the provisioning rerun, the fixed
  cannot-create-schema refusal, the old-shape upgrade integration
  test), the promises-page baseline bullet, `docs/README.md`'s
  bullet, changelog. No caller changes; the schema is dormant. Design footprint: the `StoreChain` seam
  carries a third chain with no opener changes, which is the seam
  doing its job.
- [x] **[M2: the store cutover](2026-08-30-memory-postgres-implementation.md#m2-the-store-cutover)** (PR #358). `memory/store.py` deepens in
  place (`MemoryStore`'s `read` and `remember` over the engines M1
  already owns, caps in the write transaction, containment and
  sanitized failures), the catalog's channel move and
  `memory_unwritable`, the composition and caller rewiring off
  Optional, `tools/memory.py` and `MemoryConfig` and the
  recombination sites deleted, the `memory:` moved-section refusal,
  the test translations and the new acceptance families, the M2
  documentation footprint, changelog. Design footprint: the memory
  package deepens to own storage entirely; four surfaces shed
  `MemoryStore | None`.

## Plan review round

External review of commit dfc480d3: backend codex (codex-cli
0.151.0), model gpt-5.6-sol, sandbox read-only, 2026-08-30, runtime
~7m. Verdict as received: ready after the P1/P2 amendments.
Findings condensed but faithful; each is amended below with its
resolution.

1. **P1: existing least-privilege deployments cannot create the new
   schema.** The plan calls the chain an ordinary forward extension
   and has M1 open it at boot, but `deploy/postgres-init.sql` runs
   only when a data directory initializes and deliberately gives the
   runtime role no database-level `CREATE`; `upgrade_to_head`
   attempts `CREATE SCHEMA` when the schema is absent, and the
   provisioning suite explicitly models a restricted runtime role.
   Define the upgrade choreography for an existing two-schema
   deployment: the administrative rerun of the updated provisioning
   file before deploying M1, a fixed actionable startup refusal, and
   an integration test that provisions the old shape, preserves
   rows, upgrades as administrator, and boots all three chains as
   the restricted role; extend the schema-owner and restricted-role
   assertions, not only the `vinga_ro` denial.

   *Resolution*: adopted whole, as its own resolved question: the
   administrative rerun of the repeatable provisioning file before
   the M1 image is the documented order, the boot that cannot
   create the schema refuses with a fixed sentence naming that
   rerun, and the integration lane drives the old-shape upgrade
   end to end as the restricted role with rows preserved.

2. **P1: the proposed read-refusal test exercises a lock that reads
   never request.** Reads use `read_engine` without the chain
   listener, so holding the advisory lock cannot make `read` fail.
   Use a genuine read failure; reserve the held lock for the
   write-busy test; add the inverse assertion that `read` succeeds
   while another connection holds `MEMORY_CHAIN`.

   *Resolution*: adopted; the refusal family in Tests is split by
   path, `read`'s failure driven by a genuinely failing read
   backend, the held lock reserved for `remember`, and the
   nonblocking read under a held lock pinned as its own case.

3. **P1: the transactional rollback test fails before the insert.**
   The write engine takes the advisory lock on `begin`, so a
   transaction under the held-lock harness never reaches its
   insert. Force a deterministic failure after insertion and during
   pruning (a test-only raising trigger), then verify through an
   independent connection that the insert and all pruning rolled
   back, leaving the exact pre-call rendering and count.

   *Resolution*: adopted with the reviewer's mechanism verbatim:
   prefill to the cap, a raising test-only `BEFORE DELETE` trigger,
   the public `remember`, and the independent-connection assertion
   of the exact pre-call state.

4. **P2: the concurrent-writer test can pass without
   serialization.** Plain concurrent inserts below the pruning
   boundary preserve every fact even with the lock listener absent.
   Prefill at the line and byte boundary, run two concurrent writes
   through separately opened stores, and assert the exact final
   survivor set, count, rendering and byte bound, arranged so a
   missing chain lock produces a wrong result.

   *Resolution*: adopted; the case is rewritten around the pruning
   boundary with exact final-state assertions whose failure without
   the lock is the arrangement's own property.

5. **P2: retired environment configuration would be silently
   ignored.** `VINGA_MEMORY__DIR` is a valid spelling today, and
   once the field is deleted pydantic-settings ignores unknown
   prefixed variables even under `extra="forbid"`, which is why
   `_check_moved_environment` exists. Refuse `VINGA_MEMORY` and
   every case-insensitive `VINGA_MEMORY__...` spelling with the
   same value-free retirement sentence, with tests that the name is
   reported and the value never is.

   *Resolution*: adopted; the always-on resolution now retires both
   spellings through `_check_moved_environment`'s new memory arm,
   with the name-not-value and mixed-case tests named.

6. **P2: the milestone split has no permanent owner for the chain
   opener.** M1 must declare and open the chain while `store.py` is
   assigned to M2, forcing a wrong module, a forwarding opener, or
   composition-root knowledge that relocates in M2. Give M1 a
   permanent `memory/store.py` holding `MEMORY_CHAIN`, engine
   ownership, `open_memory` and disposal, behaviorally dormant
   until M2; no temporary pass-through, no interface change between
   milestones.

   *Resolution*: adopted; both milestone bullets are rewritten so
   `memory/store.py` is M1's from the first commit with the chain,
   engines, opener and disposal, and M2 deepens it in place with
   `read` and `remember`.

7. **P2: the hard-cutover documentation is not concrete enough for
   the reset criterion.** Require explicit README and changelog
   language: existing `memory.dir` files remain untouched, this
   release never imports them, Postgres memory starts empty, and
   operators archive or delete the files themselves. Update the
   full Memory section of `docs/concepts.md`, which twice claims
   memory is configured through the configuration reference.

   *Resolution*: adopted; the M2 footprint now requires the
   not-read, not-imported, not-deleted, starts-empty,
   operator-archives language in both the README and the changelog,
   and the concepts Memory section is brought current whole.

8. **P3: M1 leaves current two-chain design claims false.** The
   product promise says CI migrates a fresh database to both chain
   heads; the database module contract says two stores and one
   database; the autogen entry point says two chains. Add every
   current-count statement to M1's footprint.

   *Resolution*: adopted; the M1 footprint enumerates the promise's
   CI sentence, the database module contract and the autogen
   documentation beside the baseline bullet.
