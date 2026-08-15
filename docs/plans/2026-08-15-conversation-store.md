# Conversation store plan

## Goal

Implement issue #120: a second SQLite database, `conversations.db`,
beside `samtal.db` in `server.database.dir`, holding sessions, turns,
tool invocations and events as the queryable record of what was said,
fed off the audio path from the event tap #138 built and from a new
content channel beside it; privacy switches, a stated retention
default, pruning and a purge command; REST read endpoints on the
gated `/api` sub-application with cursor pagination; a generated
schema reference; and, as the final milestone, the cut the 2026-08-15
ADR fixes: `heard`, `replied` and `agent_said` lose their `text`
fields, ending the JSON log's transcript-store role.

The issue's decisions are settled and this plan does not re-litigate
them; it makes them concrete. The issue's open questions (the content
path into the store, turns as table or view, the retention default,
the household question) are resolved below, each with its reasons,
along with the smaller decisions the issue leaves to the plan.

The companion implementation doc,
[`2026-08-15-conversation-store-implementation.md`](2026-08-15-conversation-store-implementation.md),
records what each milestone actually did, with deviations from this
plan, resolutions of its open questions, and discoveries; a milestone
with no deviations says so explicitly.

## The issue's decisions, restated for reference

Fixed by issue #120 (as revised 2026-08-14 and 2026-08-15), one line
each:

1. A separate database file, `conversations.db`, in
   `server.database.dir`, sharing the `db/` infrastructure (Alembic,
   WAL, engine helpers) but with its own metadata, migration chain
   and engine.
2. The schema mirrors the existing event vocabulary: a `sessions`
   table (manifest-shaped), an `events` table (monotonic id, session
   reference, `t_ms`, name, fields as JSON), and a per-turn rollup.
3. Audio never enters the database; per-frame `vad` and per-second
   drop records stay in the capture JSONL; the capture triplet is
   unchanged. The database is the queryable record; capture is the
   recording.
4. The sink is off the audio path: events queue in memory, a
   background writer commits at turn boundaries and session close,
   never per event; a slow or locked database drops rows and logs a
   warning, and a stalled reply is never acceptable.
5. Two privacy switches (metrics, and conversation text), a store
   `enabled` switch defaulting off, a retention window pruned by the
   server, and a purge command as the only new CLI.
6. Read paths: no analysis CLI; SQL over a WAL-safe copy of the
   file; a generated schema reference under the ADR's compatibility
   promise; REST read endpoints (session list, session detail, turn
   timeline) on the gated `/api` sub-application, cursor-paginated
   from the first version, in the committed OpenAPI document.
7. Push-readiness, not push: monotonic ids usable as cursors, and
   commits at turn boundaries. No SSE, no WebSocket, no
   notifications.
8. The events lose their text when this store lands, and this issue
   owns that cut, as its final milestone, recorded as a breaking
   change.
9. Tool and MCP calls land with their arguments and results as
   first-class records, under the same switch as conversation text.
10. Usage fields adopt the OTel GenAI vocabulary adapted to the
    existing field style.
11. Retention, deletion and consent are design: per-session deletion
    exists, the retention default is stated, and the household
    question is named and resolved at the policy level this issue
    can reach, precluding nothing.
12. Live view stays out: the admin UI's live view will be an event
    tap consumer later; this store is the historical record.

## Resolved open questions

### The content path: a recorder beside the tap, from day one

The issue offers two routes for content: sequence the transition
inside the issue (the store reads `text` off the events, then the
narrowing lands last and the store switches sources), or give the
runtime a dedicated content channel next to the tap. This plan
chooses the dedicated channel, built first, for three reasons:

- **Tool arguments and results never rode the events at all.**
  `tool_call` carries `tool`, `duration_ms` and `is_error`
  (`runtime/pipeline.py:993`); the arguments and the result exist
  only inside `_tool_loop`'s working list (`pipeline.py:887-891`)
  and reach no surface today. A content channel has to exist for
  decision 9 regardless, so building the text path on the events
  first would mean building content ingestion twice and throwing
  the first one away in the last milestone.
- **The store never depends on the field that is about to be
  removed.** With content arriving on its own channel, the
  narrowing milestone changes emit sites and pins and nothing in
  the store, which is the property that makes the final milestone
  reviewable as a pure surface change.
- **It is the shape the ADR names**: "the runtime hands the store a
  content-bearing record alongside the metadata event". The
  surfaces stay separate in the code, not only in the document.

Concretely, the pipeline assembles one `TurnRecord` per completed
utterance-and-reply cycle and hands it to an injected recorder in
`_reply`'s `finally`, where `replied` is emitted today
(`pipeline.py:716-747`). The record carries the utterance text and
its language fields, the joined reply, the per-agent legs (what
`agent_said` carries per handover), the tool invocations of the
reply (name, source, arguments, result, `is_error`, `duration_ms`),
and the turn's measured numbers (rounds, summed token counts,
`first_token_ms`, summed LLM round durations, and the ASR elapsed
where one was measured this turn). A reply that was cancelled or
failed records what its `finally` sees, exactly as `replied` does
today; an utterance that produced no transcript produces no turn,
mirroring the events.

The recorder reaches the runtime through the composition root, not
through a boundary change: `bespoke_runtime_factory`
(`runtime/pipeline.py:1519`) already closes over everything that
outlives one connection, and it gains the store as one more closed
value; its `build` derives a per-session recorder from
`events.session_id`. The `RuntimeFactory` type
(`device/boundary.py:236`) is untouched. The recorder is optional
and compared `is not None`, never by truthiness.

### Turns are a table, maintained by the writer

Not a view. Three reasons, each sufficient:

- After the narrowing, a view over `events` cannot hold text at
  all: the content is not in the events. The rollup's content half
  has to be stored, so the rollup is stored.
- The turn rollup's numbers come from correlating several events
  per turn plus measurements that are not events (the summed token
  counts, the ASR elapsed). The pipeline holds all of it in hand at
  the turn boundary; a view would re-derive it from JSON extraction
  on every query, fragile against exactly the field evolution the
  store exists to survive.
- Push-readiness requires monotonic ids on `turns` (decision 7),
  and a view has none to offer.

### The three controls, concretely

The issue settles three controls: the store's `enabled` switch,
default off, and under it two separate storage switches, metrics
(events and timings) and conversation text, both defaulting on
when the store is enabled. The section has four keys:

```yaml
server:
  conversations:
    # Record conversations into conversations.db.
    enabled: false
    # Store the structured events and every measured number
    # (durations, token counts, timings). With this off, no events
    # rows land and the numeric columns on turns and tool
    # invocations are null.
    metrics: true
    # Store conversation text and tool names, arguments and
    # results. With this off, rows still land with the content
    # columns null, so timing analysis survives the stricter
    # setting.
    text: true
    # Prune sessions older than this many days; 0 keeps forever,
    # and must be chosen deliberately.
    retention_days: 90
```

The two storage switches are independent, and every combination is
a supported configuration: metrics without text is the issue's
stated stricter setting; text without metrics stores the
conversation record (turns with their text, tool invocations with
their content) with the numbers nulled and no events rows, which
is a transparency-first deployment that keeps what was said
without keeping behavioral telemetry. `sessions` rows land in
every enabled configuration: they are the record's spine, and
retention, purging and the read API all key on them. Timestamps
(`started_at`, `closed_at`) survive both switches because
retention and pruning are impossible without them; `duration_s`
and the drop count are metrics and follow the metrics switch.

The section is optional and `ConversationsConfig | None = None` on
`ServerConfig`, mirroring `CaptureConfig` (`config/models.py:446`):
absent, or present with `enabled: false`, means no store, no file,
no behavior change. `metrics`, `text` and `retention_days` are
read only when the store is enabled and default to the values
above, so enabling the store alone gives the issue's stated
defaults. `extra="forbid"` like every server section. The switches
are deployment-wide; the shape deliberately leaves room for
per-user and per-agent layers later (the UI's per-user controls
are a stricter filter applied above this layer, never a
replacement for it).

### The retention default is 90 days, and 0 is the explicit opt-out

The Langfuse OSS lesson the ADR records: a store without a policy
retains indefinitely by default. 90 days spans a field-test cycle
and its follow-up analysis with margin, and keeps a forgotten
deployment from growing without bound. `retention_days: 0` keeps
everything and is documented as a deliberate choice, not a default.
Pruning deletes whole sessions (the session row and its turns, tool
invocations and events) whose `started_at` is older than the
cutoff, runs in the writer (serialized with writes by construction)
at store start and at each session close, and emits
`conversations_pruned` with a count. Capture files are a separate
instrument with their own pruning and are never touched.

### The household question, resolved at the reachable level

Who may read or delete whose sessions inside a family, and what a
shared living-room device stores about guests, is a v3 design
decision. What this issue does is keep it a query filter rather
than a migration, and preclude nothing:

- Sessions are keyed by device MAC. When v3 users own devices, "a
  member reads only their own devices' sessions" is a WHERE clause,
  and a stricter per-user storage switch is a layer above the
  deployment-wide one.
- The right-to-delete unit is a session and a user. Sessions are
  deletable directly (`--session`). Users do not exist in this
  system yet anywhere, so a stored ownership column would be a
  guess at v3's data model, exactly the half-built per-family
  shape the #86 plan refused for `agent_defaults`; when v3 users
  arrive they own devices (the product decision this issue's
  revision records), so "delete a user's history" is the purge of
  that user's devices, composed through the ownership mapping that
  will live where users live, with no migration of this store. The
  honest limit is stated rather than papered over: on a shared
  device, attributing a session to one member of the household
  needs voiceprint identification (the product vision's v3
  territory), and until then the enforceable per-user deletion is
  per-session deletion, which is why `--session` exists and why
  the session id is surfaced everywhere (the read API, the capture
  correlation, the events).
- Nothing aggregates across devices or builds profiles keyed to a
  person; the schema holds conversations, not people.
- The deployment-wide switches are the only policy layer this
  release has, and the reference documentation says so out loud:
  until per-user controls exist, enabling text storage on a shared
  device stores what guests say to it, which is the same statement
  the capture documentation already makes for audio.

## Further decisions the issue leaves to the plan

### Module layout

```
samtal_server/db/
    __init__.py     gains parameterized engine/migration helpers
                    (filename, migrations dir, metadata) used by
                    both databases; open_database() keeps its
                    signature and behavior for samtal.db
samtal_server/conversations/
    __init__.py     ConversationStore and the public surface
    schema.py       its own MetaData: sessions, turns,
                    tool_invocations, events; every column carries
                    comment=..., the source of the reference doc
    records.py      TurnRecord and ToolInvocation frozen
                    dataclasses; imported by runtime/ and store.py,
                    imports neither
    store.py        the writer thread, the bounded queue, the
                    per-session sink, pruning and purging
    api.py          the /api routes and their response models
    cli.py          the `samtal-server conversations` command group
    docgen.py       the schema reference renderer
    migrations/     its own Alembic environment and versions
```

The db/ change extracts what `_create_engine`, `_upgrade_to_head`,
`_failure` and `_database_path` (`db/__init__.py:148-247`) already
do into helpers taking the filename, the migrations directory and
the metadata as parameters, with `open_database` calling them with
today's values. Behavior-preserving: the existing `test_db_open.py`
suite passes unmodified, and the `ConfigError` messages naming
`server.database.dir` stay byte-identical. The conversations
migration environment follows `db/migrations/env.py`'s pattern
(connection injected via `config.attributes`, no offline mode) with
its own version table by virtue of its own database file.

`main.py` gains a second subcommand beside `config`:
`samtal-server conversations ...` dispatches to
`conversations/cli.py` and exits.

### The schema

One baseline migration. Typed columns carry identity, references
and the numbers queries filter on; JSON carries what the pydantic
and manifest layers already own. Referential integrity is enforced
by the writer, not with SQLite foreign keys, for the reason the
domain schema gives (`db/schema.py:7-11`): validation belongs in
one layer, and a per-connection pragma would be a second, weaker
place. The writer is not the only writer (purge and retention
delete), so the mechanism that keeps its inserts honest against a
concurrent deletion is stated below in the write path, not assumed
from a single-producer claim that is not true. All timestamps are UTC ISO-8601 text; all offsets are
integer milliseconds aligned with the capture's `t_ms` (both derive
from the session loop clock reading taken at session open).

- `sessions`: `id` (integer autoincrement primary key: the list
  cursor), `session` (text, unique: the uuid hex, the join key and
  the correlation key to capture files), `device` (MAC), `client`,
  `agent`, `agents` (JSON list), `protocol`, `started_at`,
  `closed_at` (null until close), `duration_s` (null until close),
  `close_reason` (null until close; closed token set below),
  `server_version`, `revision`, `providers` (JSON: the same
  resolved per-stage entries the capture manifest carries, built by
  `_provider_manifest`, `device/session.py:478`), `metrics` and
  `text` (booleans: which storage switches were on for this
  session, so a null column is distinguishable from disabled
  storage), `dropped` (integer, default 0, following the metrics
  switch: rows this session lost to the bounded queue, written at
  close).
- `turns`: `id` (integer autoincrement primary key: the timeline
  cursor), `session` (indexed), `t_ms` (the utterance's offset,
  aligned with its `heard` event), `agent`, `heard` (text, null
  under text-off), `heard_duration_s`, `language`,
  `language_confidence`, `reply` (text, null under text-off),
  `legs` (JSON list of `{agent, text}`, null under text-off; only
  present when a handover split the reply), `asr_ms` (null where no
  ASR elapsed was measured this turn), `first_token_ms`, `llm_ms`
  (summed round durations), `rounds`, `input_tokens`,
  `output_tokens` (summed across rounds; null when the provider
  reported no usage), `tool_calls` (integer count, always present).
- `tool_invocations`: `id`, `turn` (the parent turn's id, resolved
  by the writer inserting both in one transaction), `session`
  (indexed, denormalized for purge and session-scoped queries),
  `position` (order within the turn), `source` (closed set:
  `builtin`, `device`, `mcp`, decided where `_dispatch` already
  routes, `pipeline.py:1007`), `entry` (the owning MCP entry's
  configured name for `mcp`, null otherwise), `name` (the called
  tool's name, null under text-off), `arguments` (JSON, null under
  text-off), `result` (text, null under text-off), `is_error`,
  `duration_ms`.
- `events`: `id` (integer autoincrement primary key: the reconcile
  cursor), `session` (indexed), `t_ms`, `name` (the event), `level`
  (the numeric logging level), `fields` (JSON: the payload minus
  `event`, `session` and `device`, which live on the row and the
  session; field names are the event vocabulary's own, which is the
  contract).

Tool names, arguments and results all sit under the text switch as
one rule: a tool's name originates off this server (a device's
self-description, an MCP far side) just as its result does, and one
rule admits no partial leak-class carve-outs. `entry`, `source`,
`position` and the numbers survive text-off, so "this MCP service
was called, it took this long, it failed" remains answerable; what
was asked and answered does not, which is the point of the switch.

The `events` table is metadata-only **by construction, from the
first row**: the writer strips the `text` key from the three
text-bearing events' fields before the row lands, because content
has its own tables and its own switch. Until the final milestone
this is a live guard (the events still carry text); after it, a
no-op kept as defense in depth. This is what makes the store's
behavior identical on both sides of the narrowing.

Indexes: `events(session, id)`, `turns(session, id)`,
`tool_invocations(session)`, `tool_invocations(turn)`,
`sessions(device)`, `sessions(started_at)` (retention's scan).

The baseline migration may be amended in place only until the
milestone that ships the config section merges; before that no
released server can create the file, so no deployed database can
exist to migrate. From that merge on, schema changes are new
migrations.

### The write path: one writer thread, a bounded queue, markers

`ConversationStore` is built in `create_app` when the section is
enabled (beside the `CaptureStore` construction, `app.py:230-237`),
opens and migrates the database at construction through the shared
helpers, starts one daemon writer thread, and is stopped in the
lifespan's `finally` (`app.py:49-54`): stop accepts no new records,
drains what is queued under a bound, commits, joins the thread and
disposes the engine.

The queue is unbounded and the bound lives on the droppable class:
producers only ever `put_nowait`, so the session loop never blocks
on the store, which is the whole of the off-audio-path guarantee,
and it holds whether the database is locked, the disk is full, or
the writer is dead. `Event` records are the droppable class,
bounded by a producer-side in-flight count of 1024 (a named
constant with its reason beside it: a turn produces tens of event
records, so the bound is minutes of backlog, and a backlog that
deep means the database is wedged and dropping is the contract,
decision 4). `Open`, `Turn` and `Close` are control records and
are never dropped at the queue: they are the store's structural
truth, they arrive at human conversational pace (one `Turn` per
utterance, one `Open` and `Close` per session), so accepting them
unconditionally is bounded by the same thing that bounds sessions
themselves, and a dropped `Close` would make the store unable to
record its own incompleteness.

Queue items are typed: `Open` (session id, opened-at reading, the
manifest dict), `Event` (session id, `t_ms`, name, level, fields),
`Turn` (session id, the `TurnRecord`), `Close` (session id,
duration, close reason, dropped count). The writer drains the
queue continuously into **per-session in-memory batches** and
holds no database transaction while it waits: a session's `Open`,
`Turn` and `Close` are that session's markers, and reaching one
opens one short `BEGIN IMMEDIATE` transaction that writes exactly
that session's accumulated batch and commits. One queue, many
batches, so session A's marker never commits session B's
half-assembled turn, and no write lock is ever held across an
inter-turn interval where the purge CLI or a backup could be
waiting on it. That is decision 7's contract: a page opened
mid-conversation reads everything up to the last completed turn,
and the session row is visible from the open. The interleaving is
tested explicitly: two sessions' records interleaved on the queue,
session A's turn marker committed, and the database asserted to
hold none of session B's open turn. The writer refuses records for
a session it has not opened (drops them with one warning); by
construction that cannot happen from the real call sites, since
`Open` is enqueued before the runtime can produce anything, on the
same loop.

Deletion is a concurrent writer (the purge CLI, retention), so
every marker transaction begins by confirming its session row
still exists; a row deleted out from under a live session is a
tombstone by absence, and the writer discards that session's batch
and its per-session state and writes nothing further for it, so a
purged session cannot be resurrected as orphan turn or event rows
by records still in flight. A `Close` whose row is gone updates
nothing and is not an error. The consequence is documented on the
purge command: purging a session that is still running ends its
recording, and what the conversation says after the purge is not
recorded. The test purges an active session mid-conversation,
completes the conversation, and asserts no session, turn, tool or
event row for it exists afterwards.

Failure behavior, all of it metadata-only: an event beyond the
in-flight bound is dropped at the producer, the session's drop
count increments, and `conversations_dropped` is emitted (warning,
the session id, once per session at the first drop); the total
lands on the session row's `dropped` column at close, so the store
records its own incompleteness the way the capture manifest
records `complete`. A marker transaction that fails rolls back
atomically (SQLite's transaction is the unit); the batch it was
writing is dropped and counted, `conversations_failed` is emitted
(warning, the exception class name and nothing else), and the
writer keeps consuming. When the failed transaction was the
`Close` itself, the session row stays open-shaped (`closed_at`
null), which is the store's documented incomplete-session state:
readable, listed by the API with its null close, and pruned by
retention on `started_at` like any other row, the same semantics a
crash mid-session leaves behind. No exception text, no SQL, no row
content reaches any log, event field, or exception chain that
leaves the store; the sanitized report is built in the `except`
arm from the class name alone.

The per-session sink is one object wearing both hats: it implements
`EventTap` (attached to the session's `SessionEvents`, so every
event the capture's decision track sees, the store sees, at the
same tap position) and it is the recorder the runtime's
`TurnRecord`s arrive through. `DeviceSession` gains the store as an
optional collaborator exactly like `self._captures`: after the
hello, where `_start_capture` runs (`device/session.py:318`), it
opens the store session with the same manifest dict the capture
gets (one shape, two consumers, which is what "manifest-shaped"
means concretely) and attaches the sink after the capture, so the
dispatch order stays capture first, store second, log last.

The record's boundary is stated, not accidental: the runtime is
constructed at `device/session.py:310`, and its agent activation
emits `prompt_assembled` before the hello completes and therefore
before any consumer can attach; the capture's decision track
already begins at `session_open` for exactly this reason
(`events.py:233-236`). The store draws the same line: its events
record is the decision track, `session_open` through
`session_closed` inclusive, and the pre-attach `prompt_assembled`
of the initial activation is outside it, as it is outside the
capture. Mid-session activations (a handover's `prompt_assembled`)
are inside it. The issue's "one row per structured event" contract
is scoped accordingly, and the milestone test compares the stored
rows against the tap sequence the sink was offered (a spy attached
at the same position), not against an assumption that attachment
predates the session. In the
close path (`device/session.py:367-385`) it closes the store
session after `session_closed` is emitted, so that event is the
last row of the session's record as it is the last line of the
decision track.

Both storage switches are applied by the store, not the pipeline:
the pipeline always hands the full record, the writer nulls the
content columns when `text` is off and the numeric columns when
`metrics` is off, and skips events rows entirely when `metrics` is
off. Storage policy lives with storage; the runtime stays
policy-free, and the sentinel tests assert the policy where it
matters, on the file.

### `session_closed` gains a `reason`, a closed set at its sites

The issue's schema wants the close reason on the session row, and
today `session_closed` carries only `duration_s`
(`device/session.py:372`): the reason is only inferable from
whichever line preceded it. The event gains a `reason` field with a
closed token set, chosen where the code already decides:

- `limit`: the duration-cap branch (`device/session.py:353`)
- `idle`: the idle watchdog's shutdown (`device/session.py:565`)
- `drain`: the registry's shutdown drain (`registry.py`, via
  `request_shutdown`)
- `client`: the device closed the socket or the serve loop ended
  normally
- `error`: the `finally` ran with anything else propagating

`request_shutdown` callers pass their token; the `finally` renders
the recorded token or the default. This is an additive change to
the event surface (a new field, no rename, no removal): the README
table row updates, the CHANGELOG notes it, and the pin suite's
`session_closed` entries move with it in the same commit,
deliberately, the way #152's round moved pins with narrowed
sentences. The store copies the token to `sessions.close_reason`.

New server-scoped events, all metadata-only, all added to the README
event table: `conversations_enabled` (warning, like
`capture_enabled`, because it means the server is recording;
carries the database path), `conversations_disabled` (info, when
the section is present and off), `conversations_dropped` (warning,
`session`), `conversations_failed` (warning, `failure` class name),
`conversations_pruned` (info, `sessions` count). Their channel is
the store module's logger name, per the #138 convention.

### The read API: three GETs under the existing gate

Mounted on the same sub-application `build_api` builds, so the
bearer middleware, the sanitized handlers, and the committed
OpenAPI document cover them by construction. The route functions
live in `conversations/api.py` and are registered by `build_api`,
which gains the conversations section and the database directory it
already has.

```
GET /api/conversations                    the session list
GET /api/conversations/{session}          one session's detail
GET /api/conversations/{session}/turns    the turn timeline
```

- The list returns newest first, filtered by `?device=` when given,
  paginated by `?limit=` (default 50, max 200) and `?cursor=` (a
  `sessions.id`; a page holds rows with `id` strictly below it).
  The response is `{"items": [...], "next_cursor": <id or null>}`.
  Items are summaries: session, device, agent, started/closed,
  duration, close reason, and the turn count.
- The detail returns the full session row (providers included)
  plus its turn, event and dropped counts.
- The timeline returns turns ascending by `id`, `?cursor=` meaning
  strictly after (the reconcile direction decision 7 names), same
  limit rules, each turn carrying its columns and its tool
  invocations nested in position order. Events are deliberately
  not served over REST: the events table is the SQL surface, and
  pre-baking event queries is the analysis CLI the issue refuses.

Cursors are the monotonic ids and nothing else: no timestamps in
cursors, no opaque encodings to version. Response models are typed
transport models beside the existing ones in the API module's
style, so the committed document carries real schemas; the OpenAPI
document is regenerated in the same change and the existing drift
test covers it without modification.

Reads open the database per request through a read-only engine
(URI `mode=ro`, busy timeout, no migration), the API's per-request
precedent with the write half removed. When no `conversations.db`
exists, the routes answer 404 with a problem body naming
`server.conversations.enabled`; when the file exists, reads serve
it whether or not the store is currently enabled, because disabling
stops recording, not reading what was recorded, exactly as capture
files outlive the capture switch. Content columns come back as
stored: null when text storage was off, and the session's `text`
flag says which reading the nulls deserve.

### The CLI: purge and schema, direct to the file

```
samtal-server conversations purge --session <id>
samtal-server conversations purge --device <mac>
samtal-server conversations purge --before <YYYY-MM-DD>
samtal-server conversations schema
```

`purge` is the administrative escape hatch and deliberately works
the way `config --local` works: directly against the file named by
the composed `server.database.dir`, with no server required,
because deletion must work exactly when the server is broken or
gone. At least one selector is required; selectors combine with
AND when given together. The deletion runs in one `BEGIN IMMEDIATE`
transaction deleting sessions and their turns, tool invocations and
events together, prints the deleted counts, and is safe beside a
running server under WAL and the busy timeout (the writer's
transactions serialize with it; a busy database yields the
retryable error, nothing half-applies). A missing file is reported
plainly, not created. Errors name the location and the kind of
failure and never embed values, through the `ConfigError` shapes
the CLI already speaks. Purging does not touch capture files, and
says so in its help text; the session id is the correlation key for
whoever needs to remove the matching triplet.

API delete endpoints are deliberately not in this issue: deletion
over HTTP arrives with the admin UI's controls, where it will reuse
the same deletion helper pruning and purge share. Nothing here
precludes it.

`schema` prints the generated schema reference to stdout, the
committed copy of which lives at
`docs/reference/conversations-schema.md`. The renderer walks the
SQLAlchemy metadata in `conversations/schema.py`; every column
carries a `comment=` and the drift test fails on one that does not,
which is the same discipline `Field(description=...)` enforces for
the domain models. The committed copy regenerates byte-identically
under the existing CI pattern, and the workflow's
`docs/reference/**` path filter already covers it. The document
states the compatibility promise it inherits: table, column and
event field names change only as breaking changes.

### The narrowing, precisely

The final milestone ends the log's transcript role. Every change is
to the event surface and its documentation; the store does not
move, which the content-path decision above bought.

- `heard`, `replied` and `agent_said` lose their `text` field and
  their sentences stop rendering the text (`pipeline.py:668-677`,
  `:729-736`, `:770-778`): the sentence carries what the metadata
  half carries, in the shape the #152 round set for
  `session_rejected` and `provider_failed`. The pins move with the
  changes in the same commits, deliberately; sentinel cases plant a
  credential-shaped utterance and assert it reaches no sentence,
  argument, field, or record in either shipped format, and no
  attached tap.
- `llm_round` renames `prompt_tokens` to `input_tokens` and
  `completion_tokens` to `output_tokens` (`pipeline.py:493-514`),
  the OTel GenAI usage vocabulary adapted to the flat field style,
  per the ADR. The internal `Usage` dataclass keeps its names: it
  is not surface, and renaming it is pipeline churn this issue does
  not need. The `stage`/`provider`/`type`/`host` fields stay as
  they are: samtal's resolved-entry vocabulary, which OTel has no
  better names for, and the store's `turns` columns were named
  `input_tokens`/`output_tokens` from the first migration so the
  two surfaces already agree.
- `tool_call` stops carrying half-far-side bytes: for an MCP-owned
  call the `tool` field is replaced by `entry` (the trusted
  configured name), decided where `_dispatch` already routes;
  builtin and device tool names keep `tool` (first-party, and the
  authenticated device's own vocabulary). Exactly one of the two is
  present. #154 stripped far-side names from the MCP lifecycle
  events and recorded the published-name exposure on `tool_call`'s
  neighbor as a wider question; the store now records the full name
  as content, so the log no longer has a reason to. The sentence
  moves the same way.
- `_dispatch`'s malformed-arguments warning stops interpolating
  `call.malformed_arguments` (`pipeline.py:1012-1017`): model
  output on the retained surface, replaced by its length.
- The README event table rows for the four events move; the
  "Logging" section's transcript-store paragraph
  (`README.md:1778-1782`) is rewritten to point at the store; the
  2026-08-04 ADR gets its follow-up note recording that the
  supersession its status anticipated has happened; the CHANGELOG
  carries the breaking entries, including the operational
  migration in one line each (transcripts: query the store;
  token-count dashboards: two field renames).
- The capture's decision track inherits the narrowing, since it is
  a tap consumer: its JSONL keeps every event minus the text, and
  the WAV it sits beside carries the content, which is the
  division of labor the capture was built on. Stated in the
  CHANGELOG entry rather than discovered.

## The standing review lenses, answered

- **No-leak, at every retained surface.** The store's own emissions
  (`conversations_*` events, writer warnings) carry class names,
  counts, paths and ids, never row content, exception text or SQL;
  sanitized reports are built in the `except` arm. The API routes
  inherit the sanitized handlers; the CLI speaks `ConfigError`. The
  sentinel tests, named per milestone below: a credential-shaped
  utterance, tool argument, tool result and a poisoned exception
  message planted end to end, asserted absent from every log record
  in both formats, every event field, an attached server tap, API
  error bodies, and, with text off, from the whole database file's
  bytes after a checkpoint.
- **Pin before reshaping.** The db/ helper extraction is proven by
  `test_db_open.py` passing unmodified with its error messages
  byte-identical. Every deliberate surface change (the
  `session_closed` field, the four narrowed events) moves its pins
  in the same commit as the change, the #152 precedent, and touches
  nothing the pin suites cover otherwise.
- **Closed sets mapped to decision sites.** `close_reason` tokens
  map to the five sites named above; `source` tokens to
  `_dispatch`'s routing; the store's event names are enumerated
  here and in the README; nothing derives a token from message
  text.
- **Honest seams.** The store, the recorder and the section are
  optional dependencies compared `is not None`. The writer's
  defaults (the 1024 bound, the marker-commit policy, the busy
  timeout it inherits) each get their own pin, since tests that
  inject a queue or a clock cannot prove them.
- **Inventories by tooling.** The verification section's greps
  back the counts this plan asserts: three `text=` emit sites, the
  `prompt_tokens`/`completion_tokens` occurrences, the one
  `tool_call` emit site, the `RuntimeFactory` signature untouched,
  the `session_closed` site and the three `request_shutdown`
  callers.

## PR structure

Every push to `main` publishes an image, so every merge must be a
valid release. The store defaults off, which does most of the work;
the two ordering constraints are that no released image may let an
operator enable a store whose schema is still being amended in
place (the config key therefore lands after the schema has survived
its own milestone's review), and that the breaking surface change
sits alone in the last PR.

Five PRs, stacked, each independently green, each with its
CHANGELOG entry, each landing its implementation-doc section in the
change that ticks its milestone. The stacked-PR trap from #86 is
procedure: retarget every child to `main` before its parent merges,
rebase children with `git rebase --onto` after each merge.

## Tests

Reuse, do not restate: the integration lane's booted-app fixtures,
the session suites' drivers (`session_for`, `run_reply`, the
capture suites' store fixtures), the API acceptance seam
(Starlette's `TestClient` through the injected client factory), and
the pin suites, which are the regression net for every surface this
issue touches.

New coverage, by milestone:

- **Unit, milestone 1**: open-and-migrate for `conversations.db` on
  a tmp path and reopen; the writer's marker-commit policy (rows
  invisible before a marker, visible after); the bounded queue
  dropping under a held `BEGIN IMMEDIATE` write lock with the drop
  warning emitted once and the count landing at close; unknown-
  session records refused; retention pruning at the cutoff
  boundary; purge by each selector and combined, counts printed,
  refusal without a file; text-off nulling at the row level; the
  no-leak assertions on every writer and CLI failure path with a
  planted sentinel; the schema reference regenerating
  byte-identically with every column commented; the shared-helper
  refactor leaving `test_db_open.py` untouched and green.
- **Unit + integration, milestone 2**: enabled boot creates and
  migrates the file and emits `conversations_enabled`; disabled and
  absent sections create nothing and change nothing (the existing
  suites are the byte-for-byte proof, per #138's pins); a
  multi-turn integration conversation lands one `sessions` row and
  one `events` row per decision-track event with field names
  matching the README table; the session row is readable
  mid-session after the open commit; `session_closed` carries its
  token at each of the five sites; close updates the row.
- **Unit + integration, milestone 3**: a multi-turn conversation
  with tool calls lands turns and tool invocations with the
  measured numbers; the mid-session read shows everything up to
  the last completed turn and nothing of the open one; text-off
  end to end (the sentinel absent from the file's bytes); the
  wedged-writer acceptance: with the database locked from another
  connection, a scripted conversation's replies complete with
  latencies indistinguishable from the unlocked run, rows drop,
  the warning fires, the session row records the count.
- **Unit, milestone 4**: the three routes' round trips through the
  acceptance seam; pagination edges (empty store, one page, exact
  boundary, cursor beyond the end); the device filter; 404 without
  the file naming the key; 401 without the token (the gate covers
  the new routes); reads serving a file whose store is now
  disabled; the OpenAPI document regenerated and the drift test
  green; no-leak over the error paths.
- **Unit, milestone 5**: the moved pins, the sentinel cases named
  in the narrowing section, and the store's suites passing
  untouched, which is the proof the content path never depended on
  the events' text.

## Risks and mitigations

- **The writer thread against the session loop.** All producer
  paths are `put_nowait` plus in-memory assembly; nothing on the
  loop waits on the store. The wedged-writer test measures reply
  latency under a locked database rather than asserting an
  implementation detail.
- **Two sources of one truth.** Turns are assembled by the pipeline
  while events flow through the tap, and the two could drift. The
  milestone 3 integration test cross-checks a conversation's turn
  rows against its event rows (counts, token sums, agent names) so
  drift is a test failure, not a discovery in an investigation.
- **The narrowing breaks operator tooling.** By design and
  documented: the CHANGELOG breaking entries carry the migration
  (the store's SQL for transcripts, the two renamed fields), and
  the events keep everything the #22-style latency briefs read.
- **Schema regret after release.** The store's schema is a
  compatibility surface from the milestone that ships the config
  key. Mitigation: the reference document and the review round sit
  before that milestone merges, and Alembic exists for what they
  miss.
- **A stacked branch pays for the review rounds above it** (the #153
  lesson). Procedure: diff against the parent after every rebase
  and read what moved; the runbook forbids running milestones
  concurrently.
- **The stale bytecode trap** (AGENTS.md): `PYTHONDONTWRITEBYTECODE=1`
  outside pytest; when a result contradicts the source, suspect the
  cache first.

## Open questions

- Whether the admin UI wants an events endpoint (a session's raw
  decision track over REST) once it exists. Deferred to the UI
  issue; the SQL surface serves until then.
- API deletion endpoints, deferred to the admin UI's controls as
  decided above.
- Whether `speaking_started`-to-turn correlation deserves a stored
  `tts_ms` on turns. No success-path TTS latency measurement exists
  today, so there is nothing honest to store; if #22-style analysis
  wants it, that is a new measurement first and a column second.
- Per-user and per-agent storage switches, v3, shaped-for and not
  built, as the issue requires.

## Plan review round

One external review of the plan as first committed (88c60d1): codex
CLI 0.147.0, model gpt-5.6-sol, read-only against this repository
with the issue #120 body supplied, 2026-08-15. Verdict: not ready
as first committed; the P1 findings include contradictions with
settled issue decisions and write/read designs that could not meet
their stated contracts. Findings as received, condensed; each
carries its resolution once the amendment addressing it lands.

1. **P1: the plan removes a settled privacy switch.** The issue
   requires separate store-enabled, metrics-storage and
   text-storage controls; the plan collapses enabled and metrics
   into one switch, making content-only storage with metrics
   disabled unrepresentable.
   *Resolution*: adopted. The section is now "the three controls,
   concretely": `enabled`, `metrics` and `text` as independent
   keys, every combination supported and stated (text without
   metrics is the transparency-first deployment), sessions rows as
   the spine in every enabled configuration, timestamps surviving
   both switches because retention needs them, `duration_s` and
   the drop count following the metrics switch, and the sessions
   row recording both switch states. The writer applies both
   switches; the test lists cover the combinations.
2. **P1: per-user deletion is not designed.** The issue revision
   and the ADR require records keyed by session and user and
   deletion by user; the plan substitutes device deletion, offers
   only `--session`, `--device` and `--before`, and the schema has
   no ownership field. Introduce an ownership seam or distinguish
   future-shaped ownership from deletion enforceable today.
   *Resolution*: adopted in its second form; the ownership-column
   prescription is declined with reasons. No user exists anywhere
   in the system yet, so a stored owner would be a guess at v3's
   data model, the half-built shape #86 refused for
   `agent_defaults`. The household section now states the
   composition precisely: v3 users own devices, so user deletion
   is the purge of the user's devices through the mapping that
   will live where users live, no store migration required; and
   the enforceable per-user unit today is the session, with the
   shared-device attribution limit (voiceprint, v3) stated out
   loud instead of implied away.
3. **P1: attaching after the hello misses an existing session
   event.** The runtime is constructed at `device/session.py:310`
   and its agent activation emits `prompt_assembled` before the
   proposed attach at `:318`; the capture-based comparison cannot
   catch this because the capture is attached after that emission
   too. Attach or buffer earlier, or scope the claim.
   *Resolution*: adopted in its third form. The write-path section
   now states the record's boundary: the store's events record is
   the decision track, `session_open` through `session_closed`,
   the same line the capture draws and for the same structural
   reason; the initial activation's `prompt_assembled` is outside
   both, mid-session activations are inside. Buffering from
   construction was declined because it would make the store's
   record start earlier than the decision track it mirrors, and
   the sessions row cannot exist before the hello supplies the
   manifest anyway. The milestone test compares stored rows
   against a spy at the same tap position, not the capture track.
4. **P1: a global "next marker" cannot provide per-session turn
   commits.** With one queue, session A's marker commits session
   B's incomplete work, or inserting as records arrive holds a
   write transaction across the inter-turn interval. Require
   per-session in-memory batches and one short transaction per
   marker, with an interleaved two-session test.
   *Resolution*: adopted. The write path now specifies per-session
   in-memory batches drained from the one queue, a marker opening
   one short `BEGIN IMMEDIATE` transaction for exactly its
   session's batch, no transaction held between markers, and the
   interleaved two-session test asserting a marker exposes nothing
   of the other session's open turn.
5. **P1: queue markers can be dropped, invalidating the store's
   completeness claims.** All item types share the bounded
   `put_nowait` queue, so `Open`, `Turn` or `Close` can be
   rejected, and a dropped `Close` cannot persist the promised
   drop count. Define a non-droppable control path, transaction
   rollback behavior, and the semantics when closure cannot be
   persisted.
   *Resolution*: adopted. The queue is unbounded with the bound
   moved to the droppable class: `Event` records are bounded by a
   producer-side in-flight count, while `Open`, `Turn` and `Close`
   are control records accepted unconditionally, bounded by
   conversational pace. A failed marker transaction rolls back
   atomically, drops and counts its batch, and emits
   `conversations_failed`; a failed `Close` leaves the documented
   incomplete-session state (`closed_at` null, readable, pruned by
   `started_at`), the same shape a crash leaves.
6. **P1: purging beside a live writer is not safe as claimed.**
   The purge CLI is a second writer; purging an active session
   lets queued records recreate children without a parent or lets
   the close update affect no row. Serialize through the writer
   with tombstones, or refuse active sessions, or guarantee queued
   content cannot reappear, with resurrection tests.
   *Resolution*: adopted in its third form. Every marker
   transaction confirms its session row exists before writing;
   absence is the tombstone, the writer discards the batch and the
   session's state, and nothing in flight can recreate a purged
   session's children. Refusing active sessions was declined
   because a crashed session is indistinguishable from a live one
   from another process, and a refusal would wedge deletion in
   exactly the recovery situations purge exists for. The schema
   section's single-producer claim is corrected, and the
   resurrection test is named.
7. **P2: right-to-delete lacks physical-erasure semantics for
   SQLite WAL.** Ordinary deletes leave text in freelist pages and
   old WAL frames. State whether deletion is query-level or
   physical; for physical, specify `secure_delete`, checkpoint and
   truncation behavior, and sentinel checks over the database and
   sidecar files.
8. **P1: the cursor DDL does not guarantee monotonic, never-reused
   identifiers.** A plain `INTEGER PRIMARY KEY` reuses the deleted
   maximum rowid, especially after retention. Require SQLite
   `AUTOINCREMENT` (the SQLAlchemy table option) and test
   delete-maximum, reopen, insert.
9. **P1: the proposed read mode contradicts the repository's WAL
   implementation.** `db/__init__.py` uses URI `mode=rw` because a
   WAL reader may create or extend `-shm`; `mode=ro` cannot serve
   a live WAL database reliably. Parameterize the existing
   read-engine behavior instead, with a read-during-active-WAL
   test.
10. **P2: a disabled store can expose an unmigrated historical
    database.** Reads serve an existing file while migration only
    runs when the enabled store is constructed, so an upgrade with
    recording disabled serves an old schema. Migrate any existing
    conversations database at boot without creating one, and test
    a disabled boot against a prior revision.
11. **P1: milestone 2 publishes a misleading, incomplete enabled
    feature.** Every merge publishes an image; the milestone that
    ships `enabled` and `text: true` stores no conversation
    content because the content path lands a milestone later. Move
    construction, public configuration and documentation to land
    with the complete content path.
12. **P1: the required TTS timing has been deferred contrary to
    the issue.** The issue settles that turn rollups contain ASR,
    LLM and TTS timings; the plan's schema has none and defers it.
    Define and instrument a success-path TTS latency at the
    provider boundary, state its null semantics, and store it.
13. **P1: tool classification is neither closed nor leak-safe at
    the real decision sites.** `_dispatch` handles malformed calls
    before routing and has an unknown fallback; `switch_agent`
    bypasses `_dispatch` entirely; and the narrowing retains
    device tool names, which are peer-controlled far-side bytes.
    Centralize classification before execution covering malformed,
    unknown, handover, builtin, device and MCP paths, record
    handover results, preserve call positions, and emit only
    trusted source metadata, with sentinels in every branch.
14. **P1: provider and model vocabulary contradicts the settled
    GenAI decision.** The issue requires token counts and
    model/provider identifiers in turns and events to use the
    adapted `gen_ai.*` vocabulary; the plan renames only token
    counts, stores no model identity, and per-round attribution
    can collapse different agents and models into one ambiguous
    total.
15. **P1: routes registered only by `build_api` will be absent
    from OpenAPI.** `document()` builds `_application()` directly,
    so routes registered in `build_api` never reach the committed
    document, and the exact route inventory in
    `test_api_openapi.py` must change. Register routes
    unconditionally in `_application` and update the tests
    explicitly.
16. **P2: writer lifecycle cleanup is not guaranteed.** The
    lifespan performs startup work before its `try`, so a startup
    failure can bypass cleanup, and tests that never enter the
    lifespan can leak the thread. Start inside a guarded region,
    make stop idempotent and time-bounded, and test startup
    failure, wedged shutdown, and repeated cleanup.
17. **P2: `conversations_disabled` violates disabled-mode
    compatibility.** The acceptance criteria require absent or
    disabled behavior to remain byte-for-byte unchanged; a new
    event when the section is present and off breaks that. Remove
    it.
18. **P2: close-reason selection lacks deterministic state and a
    guaranteed close path.** Competing shutdown causes have no
    precedence rule, and the close path awaits three operations
    before `session_closed`, so a cleanup exception prevents both
    the event and the store's close. Specify a first-cause-wins
    latch and individually guarded cleanup.
19. **P2: the schema reference generator cannot document the
    promised event contract.** `events.fields` is opaque JSON, so
    a column-metadata renderer cannot derive event names or their
    fields. Provide a declared event-vocabulary input, or a
    checked inventory tied to emit sites, or name where that
    authority lives.
20. **P2: the wheel verification omits the new migration chain.**
    The installed-wheel CI step proves only the primary database's
    migrations ship. Extend it to open `conversations.db` from the
    packaged helper and assert its revision and DDL.
21. **P2: the stated inventory and pin strategy are not
    reliable.** The token grep also matches `Usage` and provider
    adapters; "pins move in the same commit" is not
    pin-before-reshape on its own; and stale transcript-store
    claims in `events.py` and `config/models.py` survive the
    narrowing. Scope the inventories, state the characterization
    baseline, and update every stale claim in the narrowing.
22. **P2: the wedged-writer latency test cannot establish the
    claimed guarantee.** "Indistinguishable latency" is noisy and
    can pass despite event-loop blocking. Combine a structural
    nonblocking-enqueue assertion, a gated writer, an event-loop
    heartbeat, and a fixed completion bound, with separate
    deterministic queue-full and marker tests.

## Milestones

One PR per milestone, ticked with its PR number, each linking to
its section of the implementation doc when written.

- [ ] **Store foundation** (branch `feature/conversation-store`):
  this plan; the db/ shared-helper extraction with `test_db_open.py`
  untouched; `samtal_server/conversations/` with schema, baseline
  migration, records, the writer (queue, markers, drops, failure
  behavior), pruning, purge; the `conversations` CLI group (purge,
  schema); the schema reference generator, the committed
  `docs/reference/conversations-schema.md` and its drift test.
  Dormant machinery: nothing constructs the store, no config key
  exists, no server behavior changes. Accept: the milestone 1 test
  list green; both lanes and lint green; `git grep conversations
  samtal_server/app.py` empty.
- [ ] **Sessions and events on the record**
  (`feature/conversation-store-m2`): `ConversationsConfig` on
  `ServerConfig` with the three keys and their `config.example.yaml`
  and `config.deploy.example.yaml` blocks; `create_app` building
  and the lifespan stopping the store; `DeviceSession` opening,
  attaching and closing the per-session sink with the manifest;
  the `session_closed` `reason` field with its five tokens, pins
  moved in the same commit; the five `conversations_*` events and
  their README table rows; the store's README section (what is
  stored, the switches, retention, the WAL-safe copy note).
  Accept: the milestone 2 test list green; acceptance criterion 1
  holds (disabled means no file and byte-for-byte behavior); lanes
  green.
- [ ] **The content path** (`feature/conversation-store-m3`):
  `TurnRecord` assembly in the pipeline (text, legs, tool
  invocations, measured numbers, the new ASR elapsed where
  measured); the recorder through `bespoke_runtime_factory`'s
  closure, `RuntimeFactory` untouched; turns and tool invocations
  written at turn boundaries; the text switch end to end; the
  wedged-writer and mid-session acceptance tests; the sentinel
  no-leak suite for the content path. Accept: the milestone 3 test
  list green; criteria 2, 3, 4 and 6 hold; lanes green.
- [ ] **Conversation reads under `/api`**
  (`feature/conversation-store-m4`): the three routes with typed
  response models and cursor pagination; the read-only per-request
  engine; 404-without-file naming the key; the regenerated OpenAPI
  document; the server README's API section rows. Accept: the
  milestone 4 test list green; criterion 7 holds; both drift checks
  green; lanes green.
- [ ] **The narrowing: content off the events**
  (`feature/conversation-store-m5`): the four event changes and the
  malformed-arguments line as specified above, pins and sentinels
  moved with them; the README event table and transcript paragraph;
  the ADR follow-up note; the CHANGELOG breaking entries; the
  store's suites untouched and green as the structural proof.
  Accept: the milestone 5 test list green; criteria 5 and 8 hold;
  lanes green.

## Verification

Per milestone, from `samtal-server/`: `uv run ruff check .`,
`uv run pytest tests/unit -q`, `uv run pytest tests/integration -q`,
plus the doc drift checks where the milestone touches a committed
artifact. The plan's inventory claims are backed by tooling at
implementation time and re-run after every rebase:

```
grep -n "text=" samtal_server/runtime/pipeline.py          # 3 emit sites
grep -rn "prompt_tokens\|completion_tokens" samtal_server  # llm_round only
grep -n "event=\"tool_call\"" samtal_server -r             # 1 site
grep -n "RuntimeFactory" samtal_server/device/boundary.py  # unchanged
grep -rn "request_shutdown(" samtal_server                 # the token sites
```

Anything unverifiable locally (the published image, the smoke lane)
is stated plainly in the PR's Verification section, never claimed.
