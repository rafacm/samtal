# Agent memory in Postgres: implementation

The companion to [`2026-08-30-memory-postgres.md`](2026-08-30-memory-postgres.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations
says so explicitly.

## M1: the memory chain exists

PR #357.

### What landed

In the order the commits tell it: the schema, the chain's migrations,
the store in its permanent home, the boot open, the provisioning file
and the refusal that names its rerun, the lane and CI plumbing, the
suites, the documents.

- **`memory/schema.py`,** its own `MetaData` on schema `memory` with
  the naming convention its two siblings use, and one table. `facts`
  carries `id` (`BigInteger` identity, primary key), `agent`, `at` (UTC
  ISO-8601 text) and `fact`, all text and all not null, with one index,
  `ix_facts_agent` on `(agent, id)`, which is the one access path there
  is: an agent's rows in insertion order, walked by the ordered read
  and by the prune. Every column carries a `comment=`, not because a
  reference is rendered from them (none is, by the plan's own
  resolution) but because they are what `\d+` shows whoever reads the
  table through psql. `TABLES` is declared for the same reason
  `conversations.schema.TABLES` is: a caller enumerating the schema
  reads it off one home, and both the analyst-denial assertions do.
- **`memory/migrations/`,** `env.py` in the record chain's shape
  (connection and chain required off the config attributes, no offline
  mode, `version_table_schema` from the chain, `include_name` filtered
  to its own schema) and `versions/2001_agent_memory.py`, the baseline,
  `down_revision` None. It was produced by
  `python -m vinga_server.db.migrations.autogen --memory` against the
  metadata and reproduced as written apart from the revision identity,
  the docstring and the formatting, which is the sanctioned path and
  what makes the drift comparison below an assertion rather than a
  hope.
- **`memory/store.py`,** the permanent home from the first commit
  (review finding 6). `MEMORY_CHAIN` is the `StoreChain` with the
  schema read off the metadata, the packaged migrations directory and
  `advisory_key(3)`. `open_memory(settings)` opens the write engine
  through `open_at`, which is what migrates, builds the read engine
  beside it, and answers a `MemoryStore` that owns the two and a
  `close()`. There is no `read` and no `remember`, and nothing forwards
  anywhere: the file-backed store in `tools/memory.py` is still what
  the composition builds, untouched, and the cutover deepens this class
  in place.
- **The boot open.** `app.py`'s `_build_composition` opens the chain
  through `open_memory` beside the conversation record's open and
  registers `close` on the same exit stack, in the same breath, so a
  boot that fails after it unwinds through the stack. Unconditionally,
  and not behind the `memory:` section the file store still reads:
  migrating creates an empty table, and an empty table is not a memory.
  Nothing consumes the store.
- **`deploy/postgres-init.sql`,** a third `CREATE SCHEMA IF NOT EXISTS
  memory AUTHORIZATION :"server_role"`, and the explicit
  `REVOKE ALL ... FROM vinga_ro` pair the `domain` schema carries, with
  the comment beside it naming the reason: the operator read surface
  for remembered facts is #83's design, addressed by scope and served
  over the API, and granting the raw tables now would freeze a contract
  #83 reshapes. Narrowing a granted read later breaks somebody;
  widening later is one additive line in the same file.
- **The upgrade choreography** (review finding 1). `db` gains
  `SCHEMA_NOT_PERMITTED`, a fixed value-free sentence naming the
  administrative rerun of the provisioning file, classified by
  `_not_permitted`, which walks `orig` for
  `psycopg.errors.InsufficientPrivilege` exactly as `is_busy` walks it
  for the retryable set and never reads a message. Built inside its
  handler and raised after it, like every other refusal in that module.
  The integration lane drives the whole path: the previous two-schema
  shape provisioned by hand, a row written in each existing store as
  the restricted role, `open_memory` refusing with the new sentence,
  the committed file rerun by the administrator, all three chains
  opened as the restricted role, both rows read back through fresh
  opens, and `vinga_ro` refused on `memory` with 42501.
- **The lane and CI plumbing.** `tests/conftest.py`'s
  `_application_tables` enumerates the third metadata, so the
  truncation clears the new table and still cannot name a version
  table, and `_migrate` takes the template through the third chain.
  `db/migrations/autogen.py` gains `--memory` through a `SELECTORS`
  map. The wheel-migration step in `.github/workflows/vinga-server.yml`
  gains a third block in the style of the two above it: the head
  literal `{"2001_agent_memory"}`, the table inventory, and the
  identity-column assertion.
- **The suites.** `tests/unit/test_memory_schema.py` pins the head and
  the columns off a blank database, the three schemas and their three
  distinct version tables, the reopen, the idempotent close, the
  declared identity column and a deleted maximum id never reissued, the
  index, the chain's own advisory key in value and in effect, the
  packaged migrations, and the metadata-drift comparison against a
  migrated database. `tests/unit/test_db_open.py` gains the two pins on
  the new refusal: the classification, and that nothing the driver said
  travels.
- **The documents.** `product-promises.md`'s floor bullet names
  `2001_agent_memory` as the third baseline and cites this plan, and
  its CI sentence says every chain rather than both; `db/__init__.py`'s
  module contract says three stores and one database; the autogen entry
  point documents three selectable chains; `docs/README.md`'s
  `postgres-init.sql` bullet counts three schemas; the server README
  carries the upgrade order, the third schema in its provisioning
  contract and in what the analyst role cannot reach; `CHANGELOG.md`
  carries the dated Added entry with the rerun instruction in it.

### Deviations from the plan

Six, none of them a change to what the plan decided.

- **An existing refusal pin changed sentence.** The plan's finding 1
  asks for a new classification, and the case it is for already had a
  test: `test_a_deployment_on_the_previous_file_upgrades_by_rerunning_it`
  drives a restricted role at a missing `record` schema and asserted
  `UNREACHABLE`. It asserts `SCHEMA_NOT_PERMITTED` now. That is the
  finding landing rather than a regression, and it is named here
  because it is a changed pin on a released sentence: an operator
  meeting that refusal used to be told to check five connection
  variables that were all correct.
- **The suites reach the database through `db.read_engine` and
  `db.write_engine`, never into the store.** M1's store interface is
  the migration and the disposal, so there is no engine to ask it for,
  and adding an accessor for the tests would be the underscore
  reach-in with a public name on it. Both doors are already public and
  already documented as the way to read a database somebody else
  migrated; the wheel step uses `read_engine` for the same reason.
- **`open_memory` answers a store, where its two siblings answer an
  `Engine`.** The plan says so (the store owns two engines, and an
  opener that answered one of them would leave the other homeless),
  but it is worth naming as the one place the three chains do not look
  alike from outside.
- **The autogen flag became a map rather than a second `if`.** Two
  chained flag arms with a deferred import in each read as duplication
  that the third would have tripled, so `SELECTORS` names the flag and
  the thunk that imports the chain, and `main` looks the flag up. The
  deferred import stays, for the reason its comment gives.
- **More count statements moved than the plan enumerates.** The plan
  names four; the server README also said "both halves of what it
  stores", "creates the two schemas", "Both schemas travel in one dump"
  and "both halves live in one", each of which this milestone
  falsifies. They moved with the four, under the plan's own rule that a
  current-count statement moves in the milestone that falsifies it.
- **`pyproject.toml`'s E501 waiver covers a second baseline.** The
  memory baseline reproduces its column comments verbatim from the
  metadata for the same reason the conversations baseline does, so the
  per-file ignore lists both files and its comment says why.

### Discoveries

- **The privilege refusal is not memory-specific, and should not be.**
  `upgrade_to_head` asks whether a schema exists before it creates one
  precisely so a least-privilege deployment can provision its schemas
  externally, which means every chain reaches the same `CREATE SCHEMA`
  and the same `InsufficientPrivilege`. Raising the sentence there
  rather than in the memory opener is what makes it true for the next
  release that adds a schema, and it is why the older upgrade case
  changed its expected sentence for free. (It was first written as an
  arm of `migration_failure`, which was wider than that statement;
  finding 1 of the review round below moves it to the statement
  itself, which is where it belongs and where this discovery already
  pointed.)
- **A third chain cost the `StoreChain` seam nothing.** No opener
  changed, `db` gained no argument, and the only edits inside `db` were
  a docstring count and the new refusal, which is a different concern
  that happened to land in the same file. That is the seam (#283) doing
  what it was cut for.

### Verification

- `uv run ruff check .`: clean.
- `uv run pytest tests/unit -q`: green.
- `uv run pytest tests/integration -q`: green, against the compose
  Postgres already listening on 127.0.0.1:5432.
- The generated-document drift checks the workflow runs, and the
  command-spellings census, regenerated through its own generator in
  the commit that made the last documentation edit.
- Not verified here, and stated rather than claimed: the `image` job
  and the CI service containers, which this branch cannot run. The
  third block in the wheel-migration step is exercised for the first
  time by the workflow run the PR links.
- No device checkpoint: nothing in this milestone changes what a board
  sends or is sent, and nothing it adds is read by any code path a
  conversation reaches.

### PR review round

External review of the branch as pushed to PR #357, at `07f1c458`
against `origin/main`: backend codex (codex-cli 0.151.0), model
gpt-5.6-sol, 2026-08-30, runtime 6m21s. Four findings, all P2, verdict
as received: mergeable after the listed fixes. Condensed below as
received, each with its resolution and the commit that landed it.

Three of the four are one shape, and it is worth naming: a claim made
wider than the thing that backs it. The refusal prescribed a remedy for
failures the remedy does not reach; the milestone's own suites asserted
what an opener does and called it what the boot does; and three
sentences described a cutover that has not happened. The fourth is the
plainer omission of a flag nothing exercised.

1. **P2: the rerun sentence answered privilege failures a rerun cannot
   fix.** Every `InsufficientPrivilege` reaching `migration_failure`
   became `SCHEMA_NOT_PERMITTED`, but `deploy/postgres-init.sql`
   creates missing schemas and nothing else: its creates are
   `IF NOT EXISTS`, so a schema standing under the wrong owner, or a
   table-level grant a later revision wanted, would be answered with a
   command that changes nothing. Narrow it to the missing-schema
   `CREATE SCHEMA` in `upgrade_to_head`, and cover an existing schema
   owned by another role.

   *Resolution* (`5f86ed2c`): adopted whole. The refusal is raised at
   that one statement, built inside the handler and raised after it;
   anything else the statement is refused is re-raised untouched for
   the caller's handler to sanitize into the general sentence, which
   prescribes nothing. The sentence is reworded to what it now answers,
   a schema that is missing and a role that may not create one, and
   `migration_failure` is back to three arms. The integration lane
   drives both sides at real refusals: a schema present under the
   administrator's ownership, where Alembic is refused its version
   table and the answer is the general sentence, and a missing schema,
   where the answer names the rerun and carries no chain. The unit lane
   pins the absence, that the general classifier prescribes nothing.

2. **P2: the boot's open and close were not pinned.** The upgrade case
   called `open_memory` directly and the lane fixture migrated the
   schema before any test ran, so deleting the composition's open and
   its exit-stack callback left every suite green and would have
   shipped an image whose memory schema is never migrated. Drive the
   application lifespan against the restricted two-schema deployment
   before and after the rerun, and assert the closure on shutdown and
   on a later startup failure.

   *Resolution* (`8e343d00`): adopted whole. The unit lane enters the
   lifespan over a blank database and reads the chain at head
   afterwards, asserts the store is held while the server serves and
   let go when it stops, and asserts a boot that refuses after the
   store is opened lets go of it. The integration lane drives the
   choreography through the application rather than through the
   opener: refused against the two-schema shape with the sentence read
   where `main()` reads it and no chain on it, then entered again after
   the administrator's rerun and found at head. Both were checked
   against the mutation the finding names: with the composition's two
   lines removed, the unit cases see no store opened and the
   integration case does not raise at all.

3. **P2: three sentences said the cutover had happened.** The server
   README's upgrade paragraph called the new schema the place
   remembered facts are stored, the baseline's docstring said they stop
   being files and become rows, and the backup section implied a dump
   carries them, while the same README two sections down and the
   changelog correctly said the files remain.

   *Resolution* (`a8047444`): adopted, and applied past the three
   places named. All of them now say the schema is provisioned and
   migrated ahead of the store that writes to it, that nothing writes
   to it in this release, and that the files under `memory.dir` and
   their backup instructions stay current until the cutover. The same
   wording was corrected where it appeared more quietly, in the
   provisioning file's header, the promises page's baseline bullet, the
   schema module's docstring, the analyst paragraph and the changelog
   entry's title: one surviving copy of a sentence just found wrong is
   how it comes back.

4. **P2: the `--memory` selector had no test.** Removing it, or
   mapping it to the wrong chain, would have stayed green, since every
   case in that suite passed a chain in directly.

   *Resolution* (`00e4ba9d`): all three selectors are asserted by the
   spelling a maintainer types, the flagless domain default included,
   through `generate`, which is the public seam the command reaches its
   work through; the usage line is asserted to name both flags; and the
   memory chain is driven through the whole maintenance lifecycle once,
   because its environment refuses to run without a connection and a
   chain on the config's attributes and a revision file written is the
   only proof the command supplies both. The wrong-chain mutation the
   finding names fails the first of these.

One thing surprised us, in a test the fix itself had to drop. The
missing-schema refusal was asserted to carry no part of the database
password, and the assertion failed: the lane's password is `vinga` and
the sentence begins "the vinga database". A substring hunt for a
credential is only as good as the credential, which is why every
refusal in this project is compared for equality against the constant
that declares it, and why the chain is asserted empty beside it.
