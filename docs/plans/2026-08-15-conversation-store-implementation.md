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

### PR #156 review round

One external review of the milestone as first pushed. Seven findings,
four P1, two P2 and one P3; verdict mergeable after fixes. All seven
adopted, one commit each, in the order the findings imply: the
checkpoint first, because two of the tests below assert on the bytes it
moves, then the two refusal paths, then the scrub, then the bound, then
the two test-strength findings.

1. **P1: the truncating checkpoint never ran.** It went through the
   engine, whose begin listener takes `BEGIN IMMEDIATE` before the
   first statement, and SQLite refuses to checkpoint inside a
   transaction; the suppressed exception turned that into silence. The
   busy result row was also ignored, and the sentinel test stopped the
   writer first (which checkpoints on the way out) and skipped sidecars
   that were absent.
   *Resolution*: adopted in `b86e9da`. Measured before it was changed:
   through the engine the pragma raises "database table is locked"
   every single time, and with a long reader held open it answers
   `(1, 25, 20)` after the full ten-second busy timeout. It now runs on
   a raw DBAPI connection outside any transaction and reads its answer.
   The wait is a quarter of a second rather than ten, because by then
   the deletion is committed and durable and truncating is tidying up.
   A blocked truncation is owed rather than lost: the store retries at
   every marker it commits and once more at stop, which is what "the
   next quiet moment" means concretely. A purge has no next marker in a
   process about to exit, so it answers whether it truncated and the
   command says so, naming what will take it. Three tests: the sentinel
   proven present in the real `-wal` file before the purge and gone
   after, a reader holding the store's own prune off its truncation
   until a later marker takes it, and a reader holding a purge's off
   until a later purge does.
2. **P1: database failures escaped the purge command as tracebacks.**
   `purge()` let connection, `BEGIN` and `DELETE` failures propagate
   and the CLI catches only `ConfigError`; a SQLAlchemy error holds the
   statement it failed on together with the parameters bound to it, and
   a purge binds the selector it was given.
   *Resolution*: adopted in `1b674b2`. Classified inside the store into
   the refusals the CLI already speaks: `DatabaseBusyError` with the
   retryable sentence for the lock that did not clear inside the busy
   timeout, `StorageError` naming `server.database.dir` for everything
   else, the file that vanished between the existence check and the
   open included. Neither carries the driver's line, unlike
   `db.migration_failure`, because a purge's bound parameters are the
   operator's own selectors. Built in the handler and raised outside
   it, so the library's exception is not reachable through
   `__context__`; `from None` would have suppressed the display and
   left the reference. Four tests: a held write lock, a poisoned driver
   message hunted through stdout, stderr and both log formats, the
   absent chain, and the preflight race.
3. **P1: argparse quoted the rejected command back.** The parser
   special-cased unrecognized arguments and interpolated argparse's own
   text for everything else, and an unknown command comes back as
   `invalid choice: 'x'`.
   *Resolution*: adopted in `3255fa1`. Every shape this grammar can
   produce maps to a fixed sentence of its own, and an unrecognized
   shape maps to a general one, because a message this code has not
   seen is exactly the one that might carry a value. The refusal for a
   word that is not a command still names the words that are, from the
   same tuple the parser is built from. The test plants the sentinel as
   the command word, as an extra argument and as a missing option
   value.
4. **P1: `tool_call` kept a peer-chosen tool name in `events.fields`
   under text-off.** The switch nulled the name on `tool_invocations`
   and left the same name on the event beside it.
   *Resolution*: adopted in `56f22ea`, in the form the direction gave:
   the scrub is not a switch at all. One table, `EVENT_CONTENT`, names
   the content each event carries and one place consults it, stripping
   `text` from `heard`, `replied` and `agent_said` and `tool` from
   `tool_call` whatever the switches say, because the events table is
   metadata-only by construction. The event keeps what this deployment
   configured or measured, so "this entry was called, it was routed
   this way, it took this long" still answers. The store's docstring
   records that milestone 5 narrows the events themselves and turns
   this into defense in depth. The switch test runs under both text
   settings with peer-name sentinels.
5. **P2: the bound stopped counting at the queue.** An event was
   released the moment the writer took it off the queue, so a session
   that never reaches a marker could hold an unbounded number of them
   in its batch while the producer saw a fresh allowance for each one.
   *Resolution*: adopted in `b6e7239`. In flight now means not yet
   written off: released at commit, at rollback, at a tombstone's
   discard, and at a refusal, and counted everywhere else, batch
   included. The test runs a writer that drains everything it is given
   into a markerless batch and watches the producer start refusing,
   with the events demonstrably off the queue and in the batch.
6. **P2: the resurrection test bypassed the purge.** It deleted the
   session row with a hand-written `DELETE`, which exercises the half
   the suite already controls rather than the two-writer interaction
   the tombstone exists for.
   *Resolution*: adopted in `4fe7b72`. The real `purge()` runs against
   a live store at both interleavings the direction named: before the
   session's first turn marker commits anything, and after a commit
   with more records in flight. The conversation then finishes normally,
   because neither the runtime nor the device edge knows a purge
   happened, and all four tables are asserted empty for it.
7. **P3: the default pins were loose.** They asserted the bound's
   number and that the stop budget merely exceeded the busy timeout,
   which a budget that had drifted down to the timeout would satisfy.
   *Resolution*: adopted in `3551f9d`. The derived budget is pinned
   exactly, the busy timeout's own value with it, and the pragmas are
   read off a default store's connection rather than off the constants
   it was meant to be built from. Retention gets a case of its own that
   builds the store the way the server will, injects only the clock,
   and watches a session a day past the window go while its neighbour a
   day inside it stays.

Nothing was done differently from the direction given. Two notes worth
recording anyway:

- **The store's retry and the purge's report are two mechanisms, not
  one.** The direction for finding 1 named "a retry at the next marker
  commit or at stop", which is what the store does for deletions the
  store performs. A purge runs in another process and has no marker to
  wait for, so it gets a longer single attempt and answers whether it
  truncated; the command prints that, naming what will take the
  truncation. The alternative considered and declined was silence,
  which would have made the reference's promise unfalsifiable from the
  command that makes it.
- **One commit carried a correction that belonged to its predecessor.**
  Finding 1 changed the reference's deletion paragraph without moving
  the docgen assertions that pin it, which the finding 4 commit picked
  up and its message records. The cause was running the suites the
  change touched rather than the whole lane; the lane is what would
  have caught it, and did.

### Verification

Rerun after the review round, from `samtal-server/`, at the round's
last code commit `3551f9d`. The unit lane is eleven cases larger than
before it: the round added a checkpoint case, two deferred-truncation
cases, four refusal cases, a batched-bound case, a second interleaving
of the resurrection case, a second text setting of the events-content
case, and a retention-default case.

```
$ uv run ruff check .
All checks passed!
```

```
$ uv run pytest tests/unit -q
2114 passed, 15 skipped in 262.61s (0:04:22)
```

```
$ uv run pytest tests/integration -q
53 passed in 155.68s (0:02:35)
```

The acceptance criterion that nothing is wired:

```
$ git grep conversations samtal-server/samtal_server/app.py
$ echo $?
1
```

The integration lane has been green on every run since, the round's
included. One flake, seen once and not since: an earlier run of the
same lane failed
`test_smoke_seeds.py::test_an_interrupted_seeding_fails_and_leaves_no_server_behind`,
which boots a real server on a free port and interrupts it. It passes
alone, passes three times in a row alone, and passed in every other run
of the whole lane including the one above. Nothing in this milestone
touches that path, and the plan behind that suite already names booting
a server in a test as a thing that can flake. Recorded rather than
smoothed over.

The wheel step cannot be run by CI from this session, so it was run by
hand exactly as the workflow runs it, and again after the review round:
`uv build --wheel`, a fresh venv, `uv pip install` of the artifact, and
the step's script executed unmodified with `-P` from outside the
checkout against the installed package. Both halves printed their
success line, and the artifact's manifest lists
`samtal_server/conversations/migrations/env.py` and
`.../versions/0001_baseline_conversation_schema.py`. The step's own run
inside this PR's CI is what proves it on the runner, and is unchecked in
the PR's verification list until it goes green there.
