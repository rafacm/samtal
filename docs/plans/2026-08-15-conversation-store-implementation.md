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

## Milestone 2: the content record

The pipeline now assembles one `TurnRecord` per completed turn and hands
it to an optional recorder where `replied` is emitted. Nothing injects
one: `bespoke_runtime_factory`'s new parameter defaults to None, `app.py`
does not mention it, `device/session.py` is untouched, and the
`RuntimeFactory` type is byte-identical. A deployment behaves exactly as
it did, which the whole unit lane passing unmodified beside this is the
evidence for.

Six commits. The one that lands this section and ticks the milestone is
the seventh:

1. `42cfdc9` Stamp a turn's offset where the clock is known
2. `7549ecb` Give the runtime an optional turn recorder
3. `019c2b8` Assemble one turn's record while the reply runs
4. `e9b141a` Time the reply's first audio at the synthesizer
5. `f8fe1c7` Record the content record in the changelog
6. `470df00` Stamp a turn off the session's own clock

### The seam

Three things in `conversations/records.py`, which still imports nothing:
`TurnRecorder`, the one-method protocol a runtime holds; `TurnStore`, the
same channel keyed by session, which is what a store offers; and
`SessionTurns`, the frozen binder between them. `bespoke_runtime_factory`
closes over a `TurnStore | None` beside `mcp_servers` and `memory`, and
its `build` binds `events.session_id` into a recorder when the store is
not None. Every comparison is `is not None`.

Two protocols rather than one because the two shapes are genuinely
different objects: the composition root holds one store for every
session, and a runtime must not know which session it is one of. The
binder is where the identity stops travelling.

### The assembly

`runtime/turns.py` is new: `TurnUnderway`, the accumulator the reply path
writes into, and `tool_source`, the classifier. It imports `records.py`
and `tools/names.py` and nothing else.

`PipelineRuntime` holds one `TurnUnderway`, replaced at the top of
`_reply` and read once in its `finally`. Always present rather than
optional, because the reply path writes into it from half a dozen places
and a guard at each would be six chances to forget one; a session with no
recorder assembles a record nobody reads, which costs a few list appends
per turn.

Where each half is filled:

- `_reply` stamps the turn with the reading its `heard` emission
  answers with, and times its own `transcribe` call into `asr_ms` off
  the same clock.
- `_llm_round_done` folds the round into `rounds`, `llm_ms`,
  `first_token_ms` and the token sums, so all four describe the rounds
  that finished.
- `_speak_reply` closes a leg at each handover.
- `_speak_after` numbers the reply's syntheses and binds the index into
  the first-audio callback.
- `_tool_loop` reserves a place on the record for every call the round
  issued, the moment those calls exist; `_run_one` and `_run_tools` fill
  in what became of each.
- `_reply`'s `finally` builds the record and hands it over, beside
  `replied` and under the same guard an event tap gets.

### The classification

`tool_source(name, device_tools, owner)` answers a source and, for `mcp`,
its entry. It is the routing `_dispatch` applies, hoisted and consulted
before anything runs, which is what closes the set over the paths the
routing hides: a malformed call is classified by its name and flagged, an
unknown name is recorded with its canned refusal, and a handover is
recorded from `_run_tools`, refusals with their error results and a
successful switch with neither result nor duration.

It classifies names rather than outcomes, and the docstring says so: a
`remember` call where no memory is configured is a builtin that was asked
for and refused, not an `unknown`. Builtins are checked first, which puts
the namespace's precedence in one readable place.

### Deviations from the plan

Nine, each with its reason.

1. **`TurnRecord.t_ms` became `at`, and the store stamps the offset.**
   The plan has the record carry "the utterance's offset from session
   open", which the pipeline cannot answer: a runtime is constructed at
   `device/session.py:310`, before the hello that opens the session, so
   it never learns the reading offsets are measured from. The record now
   carries the session loop's clock reading, the same thing an `Emission`
   carries, and `ConversationStore.record_turn` turns it into `t_ms`
   against the reading it was opened with. One origin, held by the object
   that has it. The `turns.t_ms` column and every read of it are
   unchanged; milestone 1's switch-combination case still asserts 1200 ms
   and now proves the stamping as well.
2. **`TurnLeg.agent` is nullable.** It was `str`, and the only value the
   pipeline could offer for an unactivated session was `""`, which is a
   lie in a row. Widened to match `TurnRecord.agent`, which was already
   nullable for the same reason.
3. **A leg is recorded for every agent that took part, not only for one
   that spoke.** An agent that only asked for the handover said nothing
   and spent tokens all the same, and the leg is the only place those can
   be attributed to the agent that spent them, which is the whole reason
   the plan gives for per-leg counts. The `turns.legs` column comment
   moved from "one entry per agent that spoke this turn" to "took part
   in", and `docs/reference/conversations-schema.md` was regenerated;
   the drift test is green.
4. **`runtime/turns.py` is a module the plan's layout does not name.**
   The plan puts `records.py` under `conversations/` and says nothing
   about where the assembly lives. `pipeline.py` was already 1547 lines,
   the classifier needs one home that both the record and milestone 5's
   narrowed `tool_call` can reach, and `records.py` may not import
   `runtime/`. A leaf module under `runtime/` is the only place all three
   hold.
5. **The seam is two protocols and a binder rather than one protocol.**
   See above; the plan says "an injected recorder" and leaves the shape
   open.
6. **`asr_ms` is null on the barge-in reuse path.** The plan allows the
   confirming run's elapsed "where one was measured this turn". It is
   measured, in `_gate_barge_in`, as part of deciding whether to cancel a
   different reply, and threading it across `_finish_utterance` would put
   a number measured for one decision in a column that answers a
   question about another. Null is "not measured this turn", which is
   what `records.py` says null means everywhere else.
7. **The hand-off is guarded.** The plan does not say what a recorder
   that raises should cost. The line after it is the closing
   `finish_speaking`, which in auto mode is what re-arms the device's
   listening, so a raising consumer could strand a board. It is caught
   and reported by class name on the session channel, no event, exactly
   as `events.py` guards a tap, and for the reason stated there.
8. **`_refuse_handover`'s `position` parameter is now `order`.** It was
   never the model's call-list position: it counts which switch_agent of
   the round the loop is resolving, which is what a second one is refused
   for. Recording positions beside it would have made two different
   numbers share a name at one call site.
9. **The turn's stamp comes out of the emitter, which grew for it.**
   The plan says a turn's `t_ms` is aligned with its `heard` event, and
   the store derives both from the reading the session opened at, so the
   two have to be one reading rather than two that agree. A session's
   emit therefore answers the reading it stamped, and the reply takes
   the turn's from there; `now()` reads the same clock for the one
   interval the record measures outside an event. No event, field, level
   or channel moves. The seam is the deviation; that the first pass
   sampled beside the emit instead of taking its answer is finding 2 of
   the review round below.

One thing the plan left to the implementation, recorded rather than
deviated from: `rounds` is the count of rounds that produced an
`llm_round` event, not of rounds started, so a reply cancelled
mid-generation reports the rounds whose durations and token counts are
also in the record. That is what makes milestone 3's cross-check of turn
rows against event rows exact.

### The suite

`tests/unit/test_session_record.py`, 23 tests, driven through
`session_for` and `drive_reply`/`start_reply` against a spy standing
where the store will stand. `device_session` and `session_for` gained one
optional `conversations` parameter, and every other suite calls them
exactly as before. The only other change to an existing suite is
`test_session.py`'s two direct `_Synthesis(...)` constructions, which
now pass the first-audio callback beside the failure one, and the tool
stub in `test_tts_lookahead.py`, which took the reserved slots with its
calls; the suites
that pin the event surface and the ones that assert events are untouched
and green, which is what says this milestone changed no behavior.

- The single turn: text, agent, the utterance's duration, the token sums
  and their absence, no transcript recording nothing, the reused
  transcription's language fields with a null `asr_ms`, a timed one
  whose interval is asserted exactly, and a stamp that has to equal its
  `heard` event's instant against a clock moving on every read.
- The legs: a handover's per-agent text and per-agent tokens summing to
  the turn's totals, and a silent leg recorded with its tokens and no
  text.
- The calls: one round holding a builtin, a device tool, an MCP tool, an
  unknown name and a malformed call, asserted by the position the model
  issued each at; a refused handover with its refusal and no duration; a
  successful one with neither result nor duration; and two switches in
  one round keeping the model's positions while the second is refused for
  being the second resolved.
- The finally: a reply cancelled mid-second-round recording the sentence
  the user heard, the round that finished and the tool that ran; a call
  cancelled while it was running and a call whose round's speech failed
  before the dispatch, both on the record with their positions and
  honest about never having answered.
- The synthesizer: a voice whose latency depends on the sentence, run in
  both completion orders so that neither taking whichever synthesis
  answered first nor overwriting with a later one passes, and a reply
  that only ever asked for tools timing nothing.
- The classifier: the closed set asserted equal to `schema.TOOL_SOURCES`,
  each branch at its one site, and a builtin name staying a builtin
  whatever else claims it.
- The dormancy: the same reply with a recorder and without producing the
  same speech and the same events, and a recorder that raises costing the
  reply nothing but a warning naming a class.

### Discoveries

- **The record could not carry an offset, and only writing the test
  showed it.** The store computes an event's `t_ms` from the reading the
  session opened at; the runtime has neither that reading nor any way to
  get it, because it is built before the hello that supplies it. Hence
  deviation 1.
- **A synthesis starts draining the provider the moment it is
  constructed**, so the first-audio measurement fires whether or not
  anybody consumes the audio. The suite's speaking stub therefore drains
  the synthesis instead of cancelling it, which is what makes the
  measurement a real one rather than a race with the cancel.
- **`_refuse_handover`'s "position" was two numbers wearing one name.**
  Passing the model's call-list index into it would have refused the
  first handover of a round whenever the model happened to issue it
  second, which is a behavior change nothing in the suite would have
  caught. Hence deviation 8, made before the first test ran.
- **`asyncio.gather` returns in order and finishes in whatever order it
  likes**, so the invocations land in the accumulator by completion. The
  suite asserts a position-keyed mapping rather than a list, which is the
  honest shape of what the record promises: the position is the model's
  order, the row order is not.

### Verification

From `samtal-server/`, at `470df00`:

```
$ uv run ruff check .
All checks passed!
```

```
$ uv run pytest tests/unit -q
2123 passed, 15 skipped in 260.55s (0:04:20)
```

```
$ uv run pytest tests/integration -q
53 passed in 155.32s (0:02:35)
```

The unit lane was 2103 at milestone 1 and is 2123 here: the twenty new
tests, and no other suite changed count.

The dormancy, by tooling. Nothing in the composition root, the device
edge or the boundary moved:

```
$ git diff dc02141 --stat -- samtal_server/app.py samtal_server/device/session.py \
    samtal_server/device/boundary.py
$ echo $?
0
$ git grep conversations samtal_server/app.py
$ echo $?
1
$ grep -n "RuntimeFactory =" samtal_server/device/boundary.py
236:RuntimeFactory = Callable[[DeviceOutput, SessionEvents, Sequence[str]], SessionInput]
```

The suites this milestone's acceptance names are untouched, which is
what says the reply path behaves as it did:

```
$ git diff dc02141 --stat -- tests/unit/test_event_surface_pins.py \
    tests/unit/test_server_event_pins.py tests/unit/test_event_surface_guard.py \
    tests/unit/test_events.py tests/unit/test_session_events.py \
    tests/unit/test_session_characterization.py
$ echo $?
0
```

And the plan's own inventory pointer, unchanged at three emit sites,
since the narrowing is milestone 5's:

```
$ grep -c "text=" samtal_server/runtime/pipeline.py
3
```

### Rebase onto the merged milestone 1

The branch was cut at the milestone 1 tip before that milestone's PR
#156 review round, so the seven fixes it landed were not underneath it.
After #156 rebase-merged, the milestone was replayed with
`git rebase --onto origin/main`. The hashes listed above are the ones it
was built at, as milestone 1's own list is; replayed they are `9964e95`,
`f988442`, `5addda6`, `3b42ae7`, `82be402`, `ef97aaf` and `6f85d1e`.

One conflict, in `test_conversations_store.py`. Finding 6 rewrote the
resurrection case to drive the real `purge()` against a live store at
two interleavings, and its new lines sit exactly where the `at` rename
touched the old ones. Resolved by taking the rewritten case whole and
converting its two `t_ms=` overrides to the readings that produce the
same offsets, which the suite makes mechanical: every session in it
opens at `100.0`, so `t_ms=N` is `at=100.0 + N/1000`. The same
conversion was applied to the one override the fix round added
elsewhere in that file, which merged without conflict and would
otherwise have kept a field that no longer exists.

Nothing else conflicted, no behavior changed, and nothing of the fix
round moved: the milestone's whole change to `store.py` is still the
four hunks that stamp a turn's offset, with `_release`, `_settle`, the
raw-connection `_checkpoint`, `Deletion`, `_refusal` and `EVENT_CONTENT`
untouched beneath them.

One interaction is worth stating, because the two halves were written
apart and have to agree. Finding 4 made the events table metadata-only
unconditionally, stripping `tool` from the `tool_call` event whatever
the switches say, on the grounds that a tool's name is a peer's
vocabulary and belongs on `tool_invocations` where the text switch
decides its fate. This milestone is what puts it there: the record
carries the name, and the writer nulls it under text-off. The event
keeps what this deployment configured or measured, the record keeps
what was called, and neither keeps the other's half.

Re-run from `samtal-server/` at `33d02ca`, on `origin/main` at
`899d265`:

```
$ uv run ruff check .
All checks passed!
```

```
$ uv run pytest tests/unit -q
2134 passed, 15 skipped in 263.06s (0:04:23)
```

```
$ uv run pytest tests/integration -q
53 passed in 155.49s (0:02:35)
```

The unit lane's twenty-test difference from `origin/main` is this
milestone's own module, unchanged by the rebase. The dormancy checks and
the untouched-suite checks above were re-run against `origin/main` and
answer the same way.

### PR #157 review round

One external review of the milestone as first pushed. Three findings,
one P1 and two P2; verdict mergeable after fixes. All three adopted, one
commit each, in the order the findings imply: the calls that went
missing first, because the tests for the other two run through the same
reply path, then the stamp, then the two measurements that were not
being pinned.

1. **P1: a round that was cancelled or that failed lost every call the
   model issued.** The calls arrive with the round that spoke them, but
   the round's last sentence is awaited before anything is dispatched,
   so a synthesis failing there ended the reply with the whole round's
   calls unrecorded; and inside the execution the cancellation re-raised
   before the invocation was appended, so a call a barge-in landed on
   disappeared too. The settled rule is that every call the model issues
   becomes an invocation.
   *Resolution*: adopted in `736cb97`, in the form the direction gave.
   The record's place is taken the moment the calls exist, which is when
   the stream ends: both adapters assemble tool calls after their stream
   has ended, so that is the earliest point they can be reserved, and it
   is before anything between there and the dispatch can fail. The
   reservation carries what is already true then (the position, the
   classified source, the name, the arguments) and the execution
   replaces that entry with what became of it. An entry that was never
   executed keeps the nulls it was reserved with, which is what the
   record shapes already meant by no result and no duration. Two tests,
   both confirmed failing against the code before the change: a
   cancellation arriving while a tool was running, and a synthesis
   failing before the dispatch.
2. **P2: the turn's timestamp was sampled beside the emit rather than
   taken from it.** The store measures a turn's offset and its events'
   offsets from one origin, so two readings a microsecond apart put the
   turn and its `heard` in different milliseconds whenever they straddle
   a boundary.
   *Resolution*: adopted in `90e1173`. A session's emit answers the
   reading it stamped, and the reply takes the turn's from there.
   Nearly every call site ignores the answer, which costs nothing. The
   alternative considered was passing one reading into the emit, which
   was declined because an `at` keyword would sit in the same namespace
   as the event's own fields. `now()` stays for measuring an interval on
   that clock, which is what the ASR elapsed does, and its docstring
   says out loud that it is not how a record lands on an event's
   instant. The test drives a clock that moves on every read, asserts
   the record's reading equals the `heard` emission's, and then puts
   both through a real store and asserts the two rows' `t_ms` are equal.
3. **P2: neither timing test pinned its measurement.** The ASR case
   asserted a nonnegative value, which a hard-coded zero satisfies, and
   the TTS case gave both syntheses the same latency inside a range four
   times as wide as it, so it would have passed on either of the two
   ways of picking the wrong synthesis.
   *Resolution*: adopted in `12e551b`. The ASR case runs a scripted
   transcription that moves the session's clock rather than sleeping, so
   the interval is asserted exactly with nothing timing-dependent about
   it. The TTS case gives each sentence its own latency and runs both
   completion orders: the two syntheses of a reply overlap, so
   slow-then-quick catches taking whichever answered first and
   quick-then-slow catches letting a later one overwrite the first. Both
   were confirmed by making each mistake in turn and watching its own
   case fail while the other passed.

One commit that is not a finding: `09a650c` gives `test_tts_lookahead`'s
stub of `_run_tools` the argument the reservation added. It belongs with
`736cb97` and is separate only because this branch is not being
rewritten.

Three consequences worth naming rather than leaving to be found:

- **Every call the model issues now reaches the record, including ones
  that never ran.** That is the settled rule, and it means a cancelled
  or failed round contributes `tool_invocations` rows with null results
  and null durations, and counts in `turns.tool_calls`. Nothing is
  wired yet, so no deployment sees it; a reader of those columns should
  know that a row is a call that was issued, and that the duration is
  what says whether it ran.
- **A session's emit answers a float where it used to answer None.**
  Additive: no event, field, level, channel or sentence moves, and every
  existing call site ignores it.
- **Classification happens at the end of the round instead of just
  before the dispatch.** It reads the device's tool list and the MCP
  registry's ownership a moment earlier than it did. Both are stable
  across a reply by construction (the tool snapshot is taken once per
  reply, from the same two sources), so this changes no answer; it is
  named because the classification's whole point is that it agrees with
  the routing.

Re-run from `samtal-server/` at `09a650c`:

```
$ uv run ruff check .
All checks passed!
```

```
$ uv run pytest tests/unit -q
2137 passed, 15 skipped in 262.56s (0:04:22)
```

```
$ uv run pytest tests/integration -q
53 passed in 155.89s (0:02:35)
```

One integration failure, seen once and not on the re-run of the same
lane at the same commit:
`test_smoke_seeds.py::test_a_seeding_script_reports_a_server_that_will_not_start`,
which boots a real server on a port it picked itself and waits for it to
exit. It passes alone and passed in the re-run quoted above. It is the
same booted-server family as the flake milestone 1 recorded, one test
along, and nothing in this round goes near a boot. Recorded rather than
smoothed over.

The pin suites and the event-assertion suites are still untouched
relative to `origin/main`, which the same `git diff --stat` over the six
of them answers with nothing.

## Milestone 3: sessions, turns and events on the record

The switch exists, and it does everything its documentation says.
`server.conversations` turns recording on; `create_app` opens and
migrates `conversations.db` and hands the store to the runtime factory;
`DeviceSession` opens a session's row with the manifest the capture
gets, attaches a per-session sink after the capture's tap, and closes
the row after `session_closed`; and `session_closed` now says why a
conversation ended. A server with no section behaves exactly as it did,
which the rest of the unit lane passing unmodified beside this is the
evidence for.

Seven commits. The one that lands this section and ticks the milestone
is the eighth:

1. `0d2a79a` Give the conversation store a configuration key
2. `3f45eb1` Open the store when the server is asked for one
3. `5f1e9e8` Say what ended a session, in a token
4. `1868617` Record one session's events, turns and close
5. `47ab588` Record a whole conversation against a real server
6. `000a130` Say what the conversation store keeps, and how to read it
7. `cccad2c` Record the store going live in the changelog

### The key

`ConversationsConfig` sits beside `DatabaseConfig` in
`config/models.py`, with the four keys the plan specifies and comments
in the `CaptureConfig` style (on their own line, above the key they
describe). `retention_days` is `ge=0`, and a test pins its default equal
to the store's own `RETENTION_DAYS_DEFAULT` rather than to the literal
90, so the two places that carry the number cannot drift; importing the
constant into the config models would have been a cycle
(`conversations.store` imports `config.loader`).

Both example files carry the block commented out. `config.example.yaml`
gets the full one with the per-key comments and the 0-keeps-forever
caveat; `config.deploy.example.yaml` gets the compact one its own
conventions use for a key whose reasoning lives in the reference file,
which is what it already does for `llm_first_token_timeout_s`.

### The boot

`create_app` builds the store cold before the runtime factory, because
that closure is how a turn's record reaches it. Disabled or absent, it
builds nothing and calls `migrate_existing`, a new helper in `store.py`
that brings an existing file to head and creates none, so an upgraded
deployment that records nothing today still serves what it recorded last
month, and criterion 1 stays exactly true.

The lifespan starts the writer first and stops it last. `stop()` is
idempotent, so an app built and never entered leaks nothing, and the
boot test asserts the thread is unstarted at `create_app` and stopped
after a startup failure.

### The session

`DeviceSession` gained one optional collaborator, shaped exactly like
`self._captures`. The manifest is now built once in `run` and handed to
both consumers, which is what "manifest-shaped" means concretely: the
session row and the capture's manifest file are compared to each other
in the test rather than to a hand-written expectation.

`SessionSink` (in `store.py`) is the per-session `EventTap`. It pops
`event`, `session` and `device`, which live on the row and on the
session, and hands the store the rest with the reading the emission was
stamped at; the offset is the store's to compute, since only it knows
what its session was opened at. Attached after the capture, so dispatch
stays capture first, store second, log last, and the record is the
decision track: `session_open` through `session_closed`.

### The reason

`session_closed` gained `reason`, latched once by the first termination
to fire. The three cleanup steps ahead of the event are each guarded by
`_cleanly`, which reports a failure by class on the session channel and
latches `error` if nothing else was latched, so the event, the store's
close and the capture's close always happen.

### Deviations from the plan

Five, each with its reason.

1. **The sink wears one hat rather than two.** The plan describes "one
   object wearing both hats", an `EventTap` that is also the runtime's
   recorder. Milestone 2 built the recorder binder (`SessionTurns`) in
   the composition root, where a runtime is constructed and where the
   session id is first known; the device edge never sees a `TurnRecord`
   and has nothing to hand one to. A `record_turn` on the sink would
   therefore be surface with no reader. Both binders name the same store
   and the same session id, which is the property the plan's single
   object was for.
2. **`request_shutdown` takes the token rather than each caller latching
   before it calls.** The plan says callers latch before they begin
   closing, and a parameter is that, implemented once: the latch is
   ahead of the reply drain and the close by construction rather than by
   three call sites remembering an order. The cost is one keyword on the
   three callers and on `test_drain.py`'s two fake sessions, which now
   also assert that a drain names itself.
3. **The error latch catches `BaseException`, not `Exception`.** The
   plan's wording is "the `finally` ran with anything else propagating",
   and a cancellation is as much not-an-ordinary-end as an exception is:
   uvicorn's fail-close after the drain bound cancels sessions, and a
   session that ended that way did not end in a conversation. It is
   re-raised untouched, and first-cause-wins means a drain that already
   latched keeps `drain`.
4. **The lifespan nests a region rather than moving the existing startup
   work into one.** The plan's finding 16 asks for the store's start to
   sit inside a guarded region so a later startup failure still reaches
   its stop. Putting the filler synthesis and the MCP connects inside
   that same region would also change what happens to *them* when
   startup fails (today a failure there bypasses `stop_all`), which is a
   behaviour change this milestone does not need and no test asks for.
   So the store's `try` wraps the existing shape unchanged.
5. **`_start_capture` takes the manifest instead of the client id.** The
   plan asks for the store to be opened with the same manifest dict the
   capture gets; that requires the dict to exist before either call, so
   `run` builds it and both helpers receive it.

### Discoveries

- **A mock provider's options are part of the session row, so a sentinel
  planted as the transcript is in the file whatever the text switch
  says.** The manifest records the resolved provider entries verbatim
  (deliberately: the exact model string is the only handle on a hosted
  model that changed underneath), and in the unit lane the mock ASR's
  configured `text` *is* the transcript. That is configuration, not
  conversation, and it is not under either switch. The switch test
  therefore plants the sentinel as the agent's prompt and has the mock
  speak it back through `{system}`, so the only thing carrying it into
  the record is conversation text, and text-off is then provably absent
  from the file's bytes and its sidecars.
- **`sessions.protocol` is TEXT, so the manifest's integer reads back as
  `'1'`.** This milestone is the last that may amend the baseline in
  place, so it was considered and declined: the column is an identifier
  of a wire format rather than a number a query ranges over, the
  committed reference already documents the type, and changing it would
  move that reference and four suites' fixtures for no query anybody
  would write. Recorded here so the next reader does not take it for a
  defect.
- **A websocket conversation with a real tool call needs no scripting
  below the wire.** The mock LLM's `tool_when`/`tool_name` options plus
  a configured memory directory give the unit lane a turn that calls the
  `remember` builtin and speaks its result, which is what makes the
  turn-and-invocation assertions run through `create_app` rather than
  through a hand-built runtime.
- **The sdk listens in realtime mode**, so two utterances on one
  connection are one session with two turns. `conftest.converse` holds
  one turn and hangs up, which would have been two sessions; the
  integration test drives the client itself for that reason.

### The suites

Four modules, 39 new tests.

- `tests/unit/test_config.py` (7 added): absent by default, off until
  enabled, the stated defaults with the retention number pinned against
  the store's own constant, `retention_days: 0`, a negative window and
  an unknown key refused, and both example files leaving it off.
- `tests/unit/test_conversations_boot.py` (8): an enabled boot creating
  and migrating the file and saying so with the path; the file open and
  the thread unstarted at `create_app`; three shapes of recording-off
  creating nothing and saying nothing (there is deliberately no
  disabled-mode event); an existing file behind head migrating on a boot
  that records nothing; and a startup failure after the writer started
  still stopping it.
- `tests/unit/test_session_close_reason.py` (10): the five tokens at
  their five sites, driven through a test client for the three a device
  can produce and through `run` itself for the drain and the failure;
  the competing-causes race; a runtime whose `close()` raises, reported
  by class with its message hunted for; the latch and the default at
  their one site; and the token set asserted equal to the schema's.
- `tests/unit/test_conversations_session.py` (14): the session row
  compared field by field against the capture's manifest for the same
  session; the events rows compared against a spy standing *at* the sink
  position (a subclass of it, so there is no second dispatch between
  them), names and values verbatim, with every stored field name checked
  against the README event table parsed out of the file; the turns and
  their invocations with the measured numbers; the turn rows and the
  event rows cross-checked; the session row readable from the open and a
  mid-session read stopping at the last completed turn; the four switch
  combinations with the sentinel on the file's bytes; and the
  wedged-writer acceptance in its three parts (a queue whose blocking
  `put` raises, a parked writer under an event-loop heartbeat with a
  fixed bound, and the queue-full and failed-marker paths through the
  bound and the engine seams).
- `tests/integration/test_conversations.py` (2): a real server, the sdk
  as the device, two utterances on one connection and a tool the device
  itself serves, landing one session, two turns, a device-sourced
  invocation under each and a decision track that agrees with them; and
  the same deployment with no section leaving no file.

### Verification

From `samtal-server/`, at `cccad2c`:

```
$ uv run ruff check .
All checks passed!
```

```
$ uv run pytest tests/unit -q
2173 passed, 15 skipped in 292.74s (0:04:52)
```

```
$ uv run pytest tests/integration -q
55 passed in 164.40s (0:02:44)
```

The unit lane was 2134 at milestone 2 and is 2173 here, which is exactly
the 39 tests listed above, and the integration lane was 53 and is 55,
which is the two; no other suite changed count. One pin moved,
deliberately and in the same commit as the field it pins:
`test_event_surface_pins.py::test_session_closed` now expects
`reason: "client"`, which is what an ordinary end is.

The plan's inventory pointers, re-run:

```
$ grep -c "text=" samtal_server/runtime/pipeline.py
3
$ grep -rn "request_shutdown(" samtal_server
samtal_server/registry.py:98:                    session.request_shutdown(
samtal_server/device/session.py:402:            await self.request_shutdown(
samtal_server/device/session.py:442:    async def request_shutdown(
samtal_server/device/session.py:718:            await self.request_shutdown(NORMAL_CLOSURE, "idle timeout", close_reason="idle")
$ grep -n "RuntimeFactory =" samtal_server/device/boundary.py
236:RuntimeFactory = Callable[[DeviceOutput, SessionEvents, Sequence[str]], SessionInput]
```

Three call sites and the definition: the duration cap and the idle
watchdog name their tokens inline, the registry names `drain` in the
call that spans lines 98 to 102, and the boundary type is byte-identical
to the one milestone 2 left.

`docs/reference/conversations-schema.md` is untouched, because no column
and no comment moved; its drift test is green inside the lane above.

The acceptance criteria this milestone names: 1 holds (an absent or
disabled section creates no file, changes no event and leaves the lanes
unmodified, asserted in both lanes); 2, 3 and 4 hold through the session
and integration suites (the record is written off the audio path, audio
never enters it, and the switches decide what a row keeps); 6 holds
through the schema reference, which was generated in milestone 1 and
still regenerates byte-identically. Criterion 5's pruning and purge were
built in milestone 1 and are live from this release, and the README
section is where an operator reads about them.

### Rebase onto the merged milestone 2

The branch was cut at the milestone 2 tip before that milestone's PR
#157 review round, so its three fixes were not underneath it. After #157
rebase-merged, the milestone was replayed with `git rebase --onto
origin/main`. The hashes listed above are the ones it was built at, as
milestones 1 and 2 record theirs; replayed they are `07147dc`,
`a862160`, `5854885`, `16bf39f`, `7d7050d`, `bc31016` and `c4d44c8`.

One conflict, in this file, and it is the shape a stacked branch has
rather than a disagreement: the round appended its own section exactly
where this milestone appends its section. Resolved by keeping both, the
round's first, with each verification block left as its own run
reported it. Nothing else conflicted, and that is structural rather than
lucky: the round's fixes are in `runtime/turns.py`, `runtime/pipeline.py`
and `events.py`, and this milestone touches `app.py`,
`config/models.py`, `conversations/`, `device/session.py`,
`registry.py`, `ws.py` and suites of its own.

Two of the round's changes reach this milestone, and neither cost it
anything:

- **A session's emit now answers the reading it stamped.** The sink
  reads `emission.at`, which is that same reading arriving the other
  way, so nothing here moves; the device edge's own emits ignore the
  answer as every other site does.
- **Every call the model issues is reserved on the record at the end of
  its round and updated in place.** For a call that ran, the row is
  exactly what this milestone's suites assert, and they pass unchanged.
  For a call that never ran, the reservation's nulls are what the
  writer already stores as no result and no duration, and it counts in
  `turns.tool_calls`, which is `len(record.tools)` and needed no change.
  The store is where that shape was already defined, which is why the
  fix landed a milestone earlier without reaching it.

Re-run from `samtal-server/` at `c4d44c8`, on `origin/main` at
`33208e0`:

```
$ uv run ruff check .
All checks passed!
```

```
$ uv run pytest tests/unit -q
2176 passed, 15 skipped in 289.53s (0:04:49)
```

```
$ uv run pytest tests/integration -q
55 passed in 161.86s (0:02:41)
```

The arithmetic holds against the new parent rather than the old one:
`origin/main` is 2137 unit tests and 53 integration ones, and this
milestone's 39 and 2 make 2176 and 55. The only moved pin is still
`test_session_closed`, and `git diff origin/main` over the other pin and
event-assertion suites, over `events.py` and over `runtime/` answers
with nothing, which is what says this milestone layers on the round
rather than over it.

### PR #158 review round

One external review of the milestone as first pushed. Eight findings,
two P1, five P2 and one P3; verdict not mergeable until fixed. All eight
adopted, one commit each, in the order the findings imply: the
credential that reaches a file outliving everything first, then the two
structural holes in the close path, then the two smaller lifecycle ones,
then the three that are about saying what is true.

1. **P1: a credential in a provider URL reached the session row.**
   `base_url: https://user:password@host/v1` names nothing
   secret-shaped, so every inline-secret rule passed it, and
   `_provider_manifest` serialized the entry verbatim into a record that
   outlives the conversation.
   *Resolution*: adopted in `7c57e42`, both halves as the direction gave
   them. Writing such a URL is refused in the repository, so both write
   paths inherit it, for a user and password before the host and for a
   credential-shaped query parameter alike; the value is examined rather
   than its key, at every depth, since the whole point is that the key
   looks innocent. Write time only, the addressability rule's precedent
   and its wording: a row written before the rule still boots, still
   reads and can still be edited out. And the record is built through
   `views.provider_record` rather than `model_dump`, masking
   secret-shaped keys at every depth and taking the credential out of
   any URL-shaped value, which is the half that does not depend on every
   row having passed through the rule. Five tests: the refusals at write
   time with the sentinel hunted through the message and the exception
   chain, an ordinary URL still accepted, the load path proven untouched,
   the recorded representation, and an end-to-end case that holds a
   handshake against a real entry and finds the credential in neither
   the database's bytes, the capture manifest, the logs nor stdout.
2. **P1: the record was opened four steps before the guard that closes
   it.** A device vanishing while the server hello went out, or anything
   failing in the `session_open` emit, the discovery start or the
   watchdog start, left a capture nobody closed, a row nobody ended and
   a sink still attached.
   *Resolution*: adopted in `da4db30`, in the second of the two forms
   offered. The hello send moves above the opening and is now the last
   step outside the guard, so both invariants hold at once: nothing is
   open when a step can still fail, and `session_open` is still the
   first line of the decision track because both consumers attach before
   it is emitted. Three tests, all failing against the code before it,
   one of them by leaking an unclosed WAV.
3. **P2: a client disconnect could lose the first-cause race.** `client`
   was rendered at the end rather than latched at its site, so a drain
   arriving into a close already under way took a cause that had been
   decided before the drain existed.
   *Resolution*: adopted in `a942615`. The serve loop's return and the
   disconnect branch each latch it; the render in the `finally` stays as
   the backstop for a path that latches nothing. The test holds the
   cleanup open so the drain lands inside the window that used to lose.
4. **P2: a cancellation during cleanup skipped the rest of the close.**
   `_cleanly` caught `Exception`, and a cancellation is not one.
   *Resolution*: adopted in `93c521a`. It is caught and held rather than
   swallowed or obeyed on the spot: the remaining steps run, the record
   is finished, and the cancellation is re-raised after the close has
   landed, so the caller's task still ends cancelled. The test cancels
   the runtime's close and asserts the event, the closed row, the
   finished capture and a cancelled task.
5. **P2: the writer's start sat in front of the lifespan's guard.** The
   shape the plan's own review round rejected for everything else in
   there.
   *Resolution*: adopted in `6e63c30`. The start moved inside, and the
   store was made safe for the case that matters: the thread is kept on
   the store only once it is really running, so a `Thread.start()` that
   raises leaves nothing for `stop()` to join, and the event announcing
   that this server is recording is emitted after the thread is up
   rather than before.
6. **P2: a purge racing a queued open.** A purge arriving before the
   writer commits a session's open deletes nothing, and nothing said so.
   *Resolution*: adopted in `8805706`, in the second form the review
   offered: narrowed and documented rather than engineered away. A purge
   deletes what is recorded, and a session whose open has not committed
   is not yet a record; the window is queue latency, what the session
   then records is ordinary rows the same command deletes, and a durable
   tombstone for it would be machinery whose only reader is this race.
   Said in the purge help text and in the README's deletion section, and
   driven deterministically through the writer's gate: parked before the
   open the purge reports zero, released the record lands, and run again
   it deletes it.
7. **P2: the never-drops-a-close claim overstated the contract.** The
   README said the writer never drops a session's close and the
   changelog echoed it.
   *Resolution*: adopted in `b1fdb68`. Both now say what the plan
   settled: a close is never refused at the queue, and a close whose own
   transaction fails leaves the session row open-shaped, which is the
   same incomplete state a process killed mid-session leaves, readable,
   listed and pruned on `started_at` like any other. The milestone 1
   changelog entry is corrected in place, since it is the same
   unreleased entry making the same claim.
8. **P3: the package docstring still called itself dormant.**
   *Resolution*: adopted in `0285926`. It describes the lifecycle that
   now exists: off unless the section says otherwise, built cold at
   `create_app`, the writer thread owned by the lifespan from its
   guarded start to its drained stop, and the session opening its row
   with the capture's manifest and closing it after `session_closed`.

Three consequences worth naming rather than leaving to be found:

- **Finding 1 narrows an existing surface deliberately.** The same
  builder feeds the capture manifest, so a capture taken from here on
  records a provider address without its credential where it used to
  record it whole. The entry name, the type and the exact model string
  are untouched, which is what a manifest is kept for. It has its own
  changelog line for that reason.
- **A cleanup failure no longer rewrites the close reason.** With
  `client` latched where the device hangs up, a cleanup step that raises
  afterwards is reported by class and changes nothing about why the
  conversation ended, which is the honest reading: what ended it was
  decided before the cleanup ran. The case that used to assert `error`
  there now asserts `client` and keeps its no-leak assertions, and the
  backstop it used to cover has a case of its own at the one site where
  a cleanup failure is still the first cause.
- **The PR description carries the corrected claim too.** Finding 7's
  wording ("never drops a session's close") is in the pull request's own
  Verification prose as well as in the two files fixed here, and the
  description needs the same correction.

### Verification after the round

From `samtal-server/`, at `0285926`:

```
$ uv run ruff check .
All checks passed!
```

```
$ uv run pytest tests/unit -q
2192 passed, 15 skipped in 292.93s (0:04:52)
```

```
$ uv run pytest tests/integration -q
55 passed in 162.41s (0:02:42)
```

The unit lane is sixteen cases larger than it was before the round:
three write-time URL refusals and their neighbours, two on the recorded
representation, one end-to-end credential sentinel, three on the close
path's new boundary, one on a cancelled cleanup, two on the writer's
start, one on the purge window, and two on the close reason.

## Milestone 4: conversation reads under `/api`

The record is readable over HTTP: three gated GETs under
`/api/conversations`, in the committed OpenAPI document with the rest of
the contract, the two listings among them cursor-paginated on the
monotonic row ids the schema was built with and the session read
singular. This milestone is transport and nothing else, and the proof
is that it moved none of what it serves: `git diff` against the
milestone 3 tip is empty for `app.py`, `conversations/store.py` and
`conversations/schema.py`.

Four commits. The one that lands this section and ticks the milestone is
the fifth:

1. `4eafd7e` Serve the conversation record under /api
2. `2b109cc` Read the conversation record back over HTTP
3. `e7a4d7a` Say where the conversation record is read
4. `bfd17b5` Record the conversation reads in the changelog

### The routes, and where they are registered

`conversations/api.py` holds the route functions, the six response
models and the argument parsing; `config/api.py` registers them inside
`_application()`, one line after `_runtime(api)`. That is the whole of
finding 15's resolution: `document()` renders `_application()` directly,
so a route registered by `build_api` would be served while being
structurally absent from the contract. `tests/unit/test_api_openapi.py`'s
exact route inventory gains the three paths, and the byte-identical,
determinism, validity and reference-resolution cases then cover them by
construction.

Registration takes `_problems` as an argument rather than importing it.
The configuration API imports this module to register these routes, so
an import back would be a cycle; passing the describer in is what lets
the store's routes say their refusals in the API's own vocabulary
without either module owning the other.

The document's `info.description` gains a paragraph for the namespace,
beside the ones for `/devices/pending` and `/runtime`, because a client
reading the contract alone has no other way to learn that a 404 here can
mean "this deployment never recorded" rather than "no such session".

### The reader

`build_api` attaches one more runtime fact, `api.state.conversations`, a
per-request reader over the database directory it already held. It is
`store_dependency`'s shape and lifetime: open, yield, dispose, nothing
held between requests, which is what makes a store that was purged,
moved or restored under a running server met as it is now. The engine is
milestone 1's `read_conversations`, so the read behavior is the
parameterized one the plan requires: URI `mode=rw` with no create,
deferred `BEGIN`, the busy timeout, no migration and no pragma that
writes.

The existence check lives in the dependency rather than in each handler:
it is the same answer for all three, and a handler that reached the
engine would meet a driver error where a sentence belongs, since
`mode=rw` refuses to create a missing file. A missing file is
`UnknownEntityError(NO_STORE)`, which the existing refusal map turns into
a 404 whose body names `server.conversations.enabled`.

### The arguments

`limit`, `cursor` and `device` are taken as strings and parsed by the
routes. A limit is 1 to 200 and defaults to 50; a cursor is a row id
bounded by SQLite's integer range, so a very long number is the caller's
mistake rather than a driver error; a device is normalized the way every
other MAC in this project is. Each refusal is a fixed sentence naming
the rule, and none of them repeats what arrived.

A page is read one row longer than it holds. That extra row is what
makes `next_cursor` honest without a second count: it is null exactly
when there was nothing beyond the page at the moment it was read, so a
page that ends the listing exactly does not offer a cursor onto an empty
one.

### Deviations from the plan

Seven, each with its reason.

1. **The section is not injected, only the directory.** The plan has
   `build_api` inject "the database directory it already has, and
   whether the store section exists". Nothing needs the second: whether
   there is a store to read is whether the file is there, which is the
   plan's own rule (an existing file is served whether or not recording
   is on), and the 404 names `server.conversations.enabled` in every
   case because that is the key that would have created one. A section
   flag would be a second source for the same answer with no reader.
   The consequence is worth stating: `app.py` is byte-identical, so this
   milestone changed no composition root at all.
2. **The three query arguments are strings in the signature.** Declared
   as integers they would be refused by FastAPI's own validation, which
   this application answers through `_malformed_request`: the sanitized
   body-shaped sentence, which is the wrong sentence for a query
   argument, and whose default document shape echoes the value it
   rejected. The plan requires a fixed sentence that never echoes, so
   the parsing is the route's. What the document loses is a numeric
   schema, and what it gains instead is the rule written out in each
   parameter's description, pinned by a test.
3. **A device filter that is not a MAC is refused rather than matched
   literally.** The plan says only "filtered by `?device=` when given".
   Normalizing is what makes the form an operator reads off a label
   (`AA-BB-CC-DD-EE-FF`) reach the sessions stored in canonical form,
   and refusing the rest beats an empty page, which would be a true
   answer to a question the caller did not mean to ask.
   `normalize_mac`'s own message quotes the value back, so the refusal
   is built from a fixed sentence and raised outside the `except` arm,
   where it would otherwise carry that message as its `__context__`.
4. **A summary carries `id`.** The plan's summary list does not name it.
   The cursor is the id and `next_cursor` offers only the last one, so a
   client resuming from a row it listed earlier (rather than from the
   page end) has nothing to resume with otherwise. The same id is what
   the timeline's cursor is, where it is one of the turn's columns
   anyway.
5. **A turn does not repeat its `session`, and an invocation does not
   repeat its `id`, `turn` or `session`.** The plan says each turn
   carries "its columns". These four are answered by the path the
   timeline was asked for and by the nesting itself; the rest of the
   columns are all there, and the detail read carries every column of
   `sessions` with a test that fails if the schema gains one.
6. **The nested list is `tool_invocations`, the table's own name.** A
   shorter name beside `tool_calls` (the count) would have been two
   words for one thing, and the schema reference is the vocabulary for
   this surface as much as for SQL.
7. **A database failure inside a read answers through the sanitized
   500, not through a classified refusal.** Milestone 1 classifies the
   purge's failures because a CLI prints them to a terminal; over HTTP
   the guarantee is the middleware that already covers every route here,
   which logs one line naming the exception's class and answers the
   fixed sentence. A file that vanishes between the existence check and
   the query is the case this covers, and the no-leak test pins it with
   a poisoned driver message.

One thing the plan left to the implementation, recorded rather than
deviated from: 404, 422 and 500 keep the shared `Problem` shape but get
their own descriptions through `_problems(..., instead=...)`, because
the shared sentences are about addressing an entity and reading stored
configuration, and neither is what these statuses mean here.

### Discoveries

- **A refusal that names its own boundaries cannot be hunted for its
  input.** The first version of the argument test asserted the rejected
  value absent from the body, and `limit=0` failed it: the sentence
  names the default, 50, which contains the zero. The check is now the
  whole body equal to the fixed sentence, which is the stronger claim
  anyway (there is nowhere for an echo to be), with the sentinel hunt
  beside it.
- **The test's own HTTP client logs the URL it asked for.** A sentinel
  planted in a path segment turns up in an `httpx` INFO record inside
  the test process, which is the caller's terminal rather than anything
  this server wrote. The hunt is scoped to `samtal_server` channels, the
  precedent `test_config_api_reads.py` already set for exactly this.
- **The typed tables deserialize the JSON columns on the way out.** The
  store's own suites read through raw SQL and `json.loads` what they
  find; selecting `schema.sessions` gives `agents`, `providers`, `legs`
  and `arguments` back as objects, so the response models carry the
  structures rather than strings holding them.
- **The store's close is queued as the websocket goes away**, so the
  round-trip test polls its own API until the session reads closed
  rather than assuming the writer got there first. Which is the read
  contract working as designed: the session row is visible from the
  open, and the close arrives at its own marker.

### The suite

`tests/unit/test_conversations_api.py`, 34 tests, at two seams. One
holds a real conversation against a booted server (milestone 3's
`recording_config` and the session suite's websocket drivers) and then
asks that same server's API what it recorded, which is the only place
the pipeline's record, the writer's rows and these routes are checked
against one another rather than against a hand-written fixture. The
rest write the store through the real `ConversationStore` and read it
through `build_api`, because a page boundary, a cursor past the end and
a text-off row are properties of the reads.

- The round trip: the summary, the detail and the timeline of one
  conversation, with the reply compared sentence for sentence against
  what the device was told, and the builtin call the turn made.
- The listing: newest first with every summary field, the device filter
  in three spellings of one MAC, and a store recorded by one deployment
  served by another that records nothing.
- The pages: a walk through five sessions two at a time recovering the
  listing once and in order, a page that ends the listing exactly, an
  empty store, a cursor past the end on both paginated reads, and the
  timeline's forward cursor.
- The rows: the detail carrying every column `sessions` has (asserted
  against the schema, so a new column fails here), a turn's calls nested
  in the model's order rather than the order they landed in, one
  session's timeline holding no other session's turns, and an unknown
  session refused on both reads.
- The switches: text-off serving the content columns as nulls with the
  numbers and the routing intact, and metrics-off serving the numbers as
  nulls with no events row counted and the text intact.
- The refusals: the gate in front of all three routes, the 404 naming
  the key on all three with no file created by asking, nine refused
  arguments, a sentinel in a path segment, and a failure inside the read
  path answering the generic 500 with nothing of it in the body, the log
  or the process output.

`tests/unit/test_api_openapi.py` gains the three paths in its exact
inventory and two cases: every field of the six new schemas required
(nullable is not optional, which matters most here, where a null is what
a storage switch leaves behind), and the pagination arguments described,
since nothing about them is derived from a type.

### Verification

From `samtal-server/`, at `bfd17b5`:

```
$ uv run ruff check .
All checks passed!
```

```
$ uv run pytest tests/unit -q
2209 passed, 15 skipped in 299.76s (0:04:59)
```

```
$ uv run pytest tests/integration -q
55 passed in 164.06s (0:02:44)
```

The unit lane was 2173 at milestone 3 and is 2209 here: the 34 new
tests and the two added to the OpenAPI suite, and no other suite changed
count. The integration lane is unchanged at 55.

Both committed artifacts regenerate byte-identically, which is the same
pair CI diffs:

```
$ uv run samtal-server config openapi | diff - ../docs/reference/api-openapi.json
$ uv run samtal-server conversations schema | diff - ../docs/reference/conversations-schema.md
$ echo $?
0
```

And what this milestone did not touch, which is what says it is
transport only:

```
$ git diff b26793f --stat -- samtal_server/app.py \
    samtal_server/conversations/store.py samtal_server/conversations/schema.py
$ echo $?
0
```

Acceptance criterion 7 (read paths: REST read endpoints on the gated
`/api` sub-application, cursor-paginated from the first version, in the
committed OpenAPI document) holds: the three routes are the session
list, the session detail and the turn timeline the issue names, they
page on the monotonic ids from their first release, they are inside the
bearer gate by construction, and the document carries them with real
schemas. The events endpoint the plan leaves open is still open and
still deferred to the admin UI issue; API deletion is deliberately not
here.

### Rebase onto the merged milestone 3

The branch was cut at the milestone 3 tip before that milestone's PR
#158 review round, so its eight fixes were not underneath it. After #158
rebase-merged, the milestone was replayed with `git rebase --onto
origin/main`. The hashes listed above are the ones it was built at, as
the milestones before it record theirs; replayed they are `9a5d36f`,
`0000ee4`, `cfbee32`, `54b1f96` and `06fc851`.

One conflict, in this file, and the same shape milestone 3's rebase met:
the round appends its own section exactly where this milestone appends
its section. Resolved by keeping both, the round's first, with each
verification block left as the run that produced it reported it.

Nothing else conflicted, and that is structural rather than lucky. The
round's fixes are in `config/models.py`, `config/store.py`,
`config/views.py`, `device/session.py`, `app.py` and the store's
docstrings; this milestone is `conversations/api.py`, the registration
in `config/api.py`, the document and two suites, and it moved nothing it
serves.

Two of the round's changes reach this milestone, and neither cost it
anything:

- **A session row's `providers` is now a record rather than a model
  dump**: secret-shaped keys masked at every depth and any credential a
  URL carried taken out. These routes serve that column as stored, so
  the narrowing arrives here for free and is the right way round: what
  a read cannot answer is what was never written. The round-trip case
  asserts the entry name and the type, which the representation keeps
  deliberately.
- **The session open path moved inside the guard, and `client` is
  latched where the device hangs up.** The round trip reads a session
  through the API rather than out of the file, and it polls until the
  close has landed, so it depends on the row and its `close_reason`
  rather than on where either is written from. It asserts `client` and
  passes unchanged.

Re-run from `samtal-server/` at `06fc851`, on `origin/main` at
`7771c66`:

```
$ uv run ruff check .
All checks passed!
```

```
$ uv run pytest tests/unit -q
2228 passed, 15 skipped in 298.16s (0:04:58)
```

```
$ uv run pytest tests/integration -q
55 passed in 163.06s (0:02:43)
```

The arithmetic holds against the new parent rather than the old one:
`origin/main` is 2192 unit tests and 55 integration ones, and this
milestone's 34 new cases plus the two added to the OpenAPI suite make
2228, with the integration lane untouched. Both committed artifacts
still regenerate byte-identically against the merged parent, the
OpenAPI document included, which is what says the round moved nothing
the contract carries.

### PR #159 review round

One external review of the milestone as first pushed. Three findings,
one P1, one P2 and one P3; verdict mergeable after fixes. All three
adopted, one commit each, in the order the findings imply: the header
that quoted a request first, then the document's untyped structures,
then the sentence that promised pagination the detail read does not
have.

1. **P1: the trailing-slash redirect quoted the request back in a
   header.** FastAPI's router answers `/conversations/?limit=<value>`
   and `/conversations/<value>/turns/?cursor=<value>` with a 307 whose
   `Location` is the request's own path segments and query string. The
   no-leak cases never saw it: they ask for canonical paths and read
   bodies, and a client that follows the redirect lands on the clean
   answer.
   *Resolution*: adopted in `cbd0146`. `redirect_slashes=False` on the
   whole gated application rather than on these three routes, because
   the same 307 answers `/config/` and `/agents/<name>/`, where the name
   in the path is the caller's; this milestone's routes made an existing
   exposure worse rather than introducing it. A stray slash is now an
   unmatched path, which the token holder meets as a 404 (Starlette's
   own, `{"detail": "Not Found"}`, which quotes nothing) and everyone
   else as the gate's 401. Nothing relied on the redirect, and the
   checking is worth recording rather than asserting: `/api` and `/api/`
   resolve through `mount_api`'s explicit route, which rewrites the
   scope precisely because a redirect was not good enough for a gated
   namespace, and the outer app's own router still redirects as it did;
   the `config` CLI client addresses routes without trailing slashes;
   the committed document spells every path without one; and the API and
   CLI suites pass unmoved. Five new cases plant the sentinel as a query
   value and as a path segment with redirects unfollowed and hunt it
   through every response header as well as the body, both log formats
   and the process output, and one in `test_config_api.py` holds the
   property for the configuration routes, where the fix also lands. It
   has its own changelog entry under Changed, since a request that used
   to be redirected is now a 404.
2. **P2: the document left known structures untyped.** A call's
   `source` and a session's `close_reason` were plain strings, a
   handover's `legs` an array of unrestricted objects, and `agents` an
   untyped list, so a generated client cannot tell the four sources
   apart and treats a leg as an arbitrary map.
   *Resolution*: adopted in `9fca3d9`. The two closed sets are
   `Literal`s built from the tuples `conversations/schema.py` already
   declares (`TOOL_SOURCES`, `CLOSE_REASONS`), so the contract is
   sourced from the one authority and a token added there reaches the
   document by being added once. `TurnLeg` is a schema of its own whose
   four fields are held equal to the record the writer serializes key
   for key. `agents` is a list of strings. The OpenAPI suite asserts the
   enums against those tuples, the leg reference and its required
   fields, and the agent item type, and a round-trip case reads a real
   handover's legs back.
   One deliberate departure from the finding's letter: `close_reason` is
   the `Literal` *and* a string branch. The column is unconstrained on
   purpose, for the reason `schema.py` gives, that a database refusing
   an unforeseen sixth token would drop the session row rather than
   record a close it could not name; a read that refused one would drop
   a whole page over that row, which is the same mistake one layer up.
   The document says both halves: these five are what a server latches,
   and a token from a later release is served as it was stored.
3. **P3: "all three page" is not true of the detail read.** It takes
   neither `limit` nor `cursor`.
   *Resolution*: adopted in `e229b8a`. Corrected in the README, the
   changelog, the document's own `info.description` and this section's
   opening, since it was one sentence repeated rather than four
   different claims. The document had to be regenerated for it, which is
   what makes the description a place a wrong claim gets caught.

Nothing was done differently from the direction given. One note worth
recording: the redirect finding is the second time this milestone's
no-leak hunt had a blind spot that only a differently-shaped request
exposed (the first was a refusal sentence naming its own default, above),
and both were places the test looked exactly where the code was written
to be clean. The header hunt is now part of the sentinel cases rather
than a thing to remember.

### Verification after the round

From `samtal-server/`, at `e229b8a`:

```
$ uv run ruff check .
All checks passed!
```

```
$ uv run pytest tests/unit -q
2239 passed, 15 skipped in 299.52s (0:04:59)
```

```
$ uv run pytest tests/integration -q
55 passed in 162.27s (0:02:42)
```

The unit lane is eleven cases larger than it was before the round: five
slashed-path sentinel cases and three for the configuration routes
beside them, two in the OpenAPI suite (the typed structures, and the
leg-shape drift guard), and the handover round trip, with `TurnLeg`
joining the required-field case that already existed. Both committed
artifacts still regenerate byte-identically:

```
$ uv run samtal-server config openapi | diff - ../docs/reference/api-openapi.json
$ uv run samtal-server conversations schema | diff - ../docs/reference/conversations-schema.md
$ echo $?
0
```
