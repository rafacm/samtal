# The conversation store, as built

Companion to
[`2026-08-15-conversation-store.md`](2026-08-15-conversation-store.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## Milestone 1: store foundation

`samtal_server/conversations/` now holds the schema, its own Alembic
chain, the records the pipeline will hand over, the writer, retention,
purging, the reference renderer and the command group. `db/__init__.py`
is parameterized by file so both databases share one set of helpers.
Nothing constructs any of it: `git grep conversations
samtal-server/samtal_server/app.py` is empty, no configuration key
exists, and a server that is not asked for a store neither creates nor
opens one.

Ten commits, in the order the milestone was built in. The one that
lands this section and ticks the milestone is the eleventh:

1. `c75b9a7` Parameterize the database helpers by file
2. `3f8d176` Give the conversation store its schema and chain
3. `fd6b6b1` Write conversations from one thread off the path
4. `b170d86` Render the store's reference from its columns
5. `426a214` Add the conversations command group
6. `22ca88c` Prove the conversation store ships in the wheel
7. `86b01c8` Say why a refused record was refused
8. `7411735` Wrap the purge help where it is written
9. `3abfe67` Say what the deferred BEGIN buys, where it is chosen
10. `2cfed4f` Spell the empty tuple default like its neighbour

### The db/ extraction

`open_database` and `read_engine` keep their signatures and their
behavior and are now two-line callers of parameterized helpers:
`open_at(directory, filename, migrations, secure_delete)`,
`database_path(directory, filename)`, `write_engine(path,
secure_delete)`, `existing_engine(path, immediate, secure_delete)`,
`upgrade_to_head(engine, migrations)` and `migration_failure(exc,
path)`. `_MIGRATIONS_DIR` and `DATABASE_FILENAME` keep their names,
which `test_db_open.py` imports.

`existing_engine` is the generalization of what used to be inline in
`read_engine`: URI `mode=rw` (a WAL reader may extend the `-shm` index,
and `mode=rw` still refuses to create a missing file), the busy timeout,
no journal-mode pragma. It gained the two knobs the second database
needs of it, both defaulting off so the read path is unchanged:
`immediate`, because a purge takes the write lock before it reads, and
`secure_delete`.

Behavior-preserving, and the proof is that `test_db_open.py` is
untouched and green, its `ConfigError` assertions included.

### The schema

`conversations/schema.py`: its own `MetaData` with the domain schema's
naming convention, and four tables in declaration order, exactly the
plan's columns. Every column carries a `comment=`.

`sessions`, `turns` and `events` carry `sqlite_autoincrement=True`;
`tool_invocations` does not, because it is read through its parent turn
and is never paginated on its own. The indexes are the plan's six, named
in the migration so a later one can address them.

Two schema-level decisions the plan left open:

- **`tool_invocations.source` is a check constraint** over the four
  tokens. The whole value of the column is that a query may enumerate
  it, so the closed set is a property of the schema and not only of the
  classifier that will fill it in milestone 2. The baseline may still be
  amended in place until the config key ships, so a fifth token
  discovered while building that classifier costs nothing.
- **`sessions.close_reason` is not constrained.** Its five tokens are
  latched at five sites in the device edge, which is where they are
  enforced; a database that refused an unforeseen sixth would drop the
  session row rather than record a close it could not name. The token
  set is in the column comment.

### The writer

`conversations/store.py`, 784 lines, opening with the module docstring
that states the three decisions the off-the-audio-path contract forces.

- Producers (`open_session`, `record_event`, `record_turn`,
  `close_session`) only ever `put_nowait`. The queue is a
  `SimpleQueue`, unbounded; the bound is a producer-side in-flight count
  of `MAX_EVENTS_IN_FLIGHT = 1024` that applies to `Event` records
  alone.
- The writer thread drains into per-session `_Batch`es and holds no
  transaction while it waits. `Open`, `Turn` and `Close` are markers:
  each opens one `BEGIN IMMEDIATE` transaction, writes exactly that
  session's batch, and commits.
- Every marker except the open first confirms the session row exists.
  Absence is the tombstone: the batch and the session's writer state are
  dropped, and nothing in flight can recreate it.
- Both switches are applied in the row builders. Metrics-off nulls the
  numbers and writes no `events` rows; text-off nulls `heard`, `reply`,
  the legs' text, and the tool name, arguments and result together.
  `heard`/`replied`/`agent_said` lose their `text` field structurally
  before any row lands.
- Failure emits `conversations_failed` with the exception's class name
  and nothing else, drops and counts the batch, and keeps consuming. A
  failed close leaves the row open-shaped.
- `stop()` is idempotent, drains through a sentinel behind everything
  queued, and joins with `STOP_TIMEOUT_S`, which is derived from
  `BUSY_TIMEOUT_MS` rather than written twice.
- Retention runs in the writer at start and at each close; `purge()` is
  a module-level helper the CLI calls. Both delete children through a
  subquery rather than a list of ids, and both finish with
  `PRAGMA wal_checkpoint(TRUNCATE)` over a `secure_delete` database.

### Deviations from the plan

Five, each with its reason.

1. **The helpers are not parameterized by metadata.** The plan's module
   layout says "filename, migrations dir, metadata". Nothing in
   `db/__init__.py` reads the metadata: each Alembic environment imports
   its own, which is the pattern `db/migrations/env.py` set. Passing it
   through would have created a second way to reach the same value with
   no caller for it. Filename, migrations directory and the two engine
   modes are the parameters.
2. **`records.py` gained a third dataclass, `TurnLeg`.** The plan
   describes `legs` as a JSON list of `{agent, text, input_tokens,
   output_tokens}`. Those halves follow different switches, so the
   writer builds the entry rather than serializing it whole, and a typed
   leg is what makes that readable and checkable at the seam milestone 2
   fills.
3. **The producer consults the metrics switch too.** The plan puts both
   switches at write time, and the writer is still where the decision
   lands (`_write` skips the `events` insert). The producer consults the
   same predicate, `_stores_events()`, so that a deployment with metrics
   off pays no queue for records that were never going to be written
   and, more importantly, reports no drops of them: emitting
   `conversations_dropped` for events that a switch had already
   discarded would be a false report of incompleteness. One rule, one
   method, consulted twice.
4. **`sessions.dropped` counts failed batches as well as refused
   events.** The plan describes the column as what the bounded queue
   lost and separately says a failed batch is "dropped and counted". One
   column carries both, because both are the same fact from the reader's
   side: records this session lost. The comment says so.
5. **The JSON columns are `JSON(none_as_null=True)`.** SQLAlchemy's
   default persists a Python `None` in a JSON column as the four bytes
   `null` rather than as SQL NULL. The reference says these columns are
   null under their switch and a reader filtering on `IS NULL` has to
   find them, so the type says it. Found by the switch-combination test,
   which asserted `IS NULL` and got the string.

Two things the plan left to the implementation, recorded rather than
deviated from: the unknown-session refusal is a plain warning on the
store's channel rather than a fifth event (the event vocabulary is a
compatibility surface, and by construction this cannot happen from the
real call sites), and `purge()` raises `ValueError` when given no
selector, a guard against a caller since the CLI refuses it first.

### The suites

Five new modules under `tests/unit`, 64 tests, covering the plan's
milestone 1 list and the write-path specifics its review round added.

- `test_conversations_schema.py` (10): open, migrate, reopen; a second
  file whose tables and version row stay out of the domain database's;
  the WAL, busy-timeout and `secure_delete` pragmas; `AUTOINCREMENT` in
  the stored DDL and the delete-maximum, reopen, insert case behind it;
  the six indexes; the closed source set refusing a fifth token; the
  migrations inside the package; and asking for the path creating
  nothing.
- `test_conversations_store.py` (20): the marker policy, through a gate
  the writer parks on rather than through timing, including the
  interleaved two-session case; the bound dropping events once per
  session with the count on the row, and control records surviving a
  bound of zero; the structural nonblocking assertion through a queue
  whose blocking `put` raises; the unknown-session refusal; the
  tombstone; the four switch combinations at row level; the text
  stripped from the three text-bearing events; the failed marker and
  the failed close with the sentinel hunted through both log formats,
  the process output, the event fields and an attached server tap;
  `stop()` idempotent and bounded against a writer that never lets go;
  and a read through the read engine while the write-ahead log holds
  uncheckpointed frames.
- `test_conversations_retention.py` (11): the cutoff asserted on the
  day either side of it, `retention_days: 0`, pruning at start,
  the pruned count; purge by each selector and combined, a purge that
  matches nothing, a purge with no selector; and the sentinel absent
  from the database and both sidecars after one.
- `test_conversations_cli.py` (12): each selector end to end with its
  printed counts, the refusal with none, the missing store reported and
  not created, the malformed date, argparse's own usage errors leaving
  by the ConfigError door without echoing what was typed, the schema
  command needing no database, the help's two notes, and the word
  dispatch in `main.py`.
- `test_conversations_docgen.py` (11): every column commented,
  determinism, the committed copy byte-identical, every column in the
  document, and the claims a renderer cannot derive (the compatibility
  promise, both switches, the retention default, the deletion semantics
  with their two limits, the WAL-safe copy, the event vocabulary's
  authority, the GenAI rows).

### Discoveries

- **Closing the last connection to a WAL database checkpoints it.** The
  read-during-active-WAL test originally stopped the store and then
  read, which deleted the `-wal` file before the assertion could look at
  it. The test now parks the writer at a later marker, so the frames are
  demonstrably still in the log with the writer's connection open.
- **`secure_delete` plus the truncating checkpoint really does clear
  the bytes.** The sentinel test asserts the planted utterance is
  present in the file before the purge and absent from the database and
  both sidecars after it, which is a stronger check than the plan asked
  for and passed first time.
- **Swapping the store's engine mid-test races the writer.** The
  failure-path tests originally replaced `_engine` from the test thread
  without knowing where the writer was, and hit the open transaction
  instead of the turn's. They now park at the marker they mean to break,
  which is what makes "exactly one failure" assertable.

### Verification

From `samtal-server/`, at `2cfed4f`:

```
$ uv run ruff check .
All checks passed!
```

```
$ uv run pytest tests/unit -q
2103 passed, 15 skipped in 261.13s (0:04:21)
```

```
$ uv run pytest tests/integration -q
53 passed in 155.24s (0:02:35)
```

The acceptance criterion that nothing is wired:

```
$ git grep conversations samtal-server/samtal_server/app.py
$ echo $?
1
```

One integration flake, seen once and not since: an earlier run of the
same lane failed
`test_smoke_seeds.py::test_an_interrupted_seeding_fails_and_leaves_no_server_behind`,
which boots a real server on a free port and interrupts it. It passes
alone, passes three times in a row alone, and passed in every other run
of the whole lane including the one above. Nothing in this milestone
touches that path, and the plan behind that suite already names booting
a server in a test as a thing that can flake. Recorded rather than
smoothed over.

The wheel step cannot be run by CI from this session, so it was run by
hand exactly as the workflow runs it: `uv build --wheel`, a fresh venv,
`uv pip install` of the artifact, and the step's script executed with
`-P` from outside the checkout against the installed package. Both
halves printed their success line, and the artifact's manifest lists
`samtal_server/conversations/migrations/env.py` and
`.../versions/0001_baseline_conversation_schema.py`. The step's own run
inside this PR's CI is what proves it on the runner, and is unchecked in
the PR's verification list until it goes green there.
