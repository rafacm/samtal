# First-class conversations plan

## Goal

Implement issue #190: promote the conversation to a first-class
entity, a durable thread between a user and exactly one agent that
spans sessions, with its own identity and title; align the
terminology now, pre v1.0, so sessions are sessions and conversations
are threads on every surface; split the store's write path so
thread-feeding turns are durable while telemetry stays lossy; make
retention thread-aware; add resumption as a configuration switch
requiring text storage, with discovery by spoken description,
selection tools, hydration under a token budget, and consent-gated
recap milestones; and absorb the CLI half of the no-local-database
rule (#281/#282): CLI verbs for sessions and conversations operate
over the API, and on-demand session erasure returns as an
authenticated endpoint with the CLI as its remote caller.

The issue's decisions (as amended 2026-08-21, 2026-08-22 and
2026-08-24) are settled and this plan does not re-litigate them; it
makes them concrete. The issue's open questions and the smaller
design decisions it leaves open are resolved below, each with its
reasons.

The companion implementation doc,
[`2026-08-28-first-class-conversations-implementation.md`](2026-08-28-first-class-conversations-implementation.md),
records what each milestone actually did, with deviations from this
plan, resolutions of its open questions, and discoveries; a milestone
with no deviations says so explicitly.

## The issue's decisions, restated for reference

Fixed by issue #190 and its amendments, one line each:

1. A conversation is a thread with exactly one agent: identity,
   title, created and last-active timestamps, spanning sessions; an
   agent can have many.
2. Turns reference both their conversation and their session; the
   session view and the conversation view are two projections of the
   same rows.
3. A handover turn belongs to the conversation that started it;
   per-leg attribution keeps the split honest; boundaries fall
   between turns, never inside one.
4. Terminology aligns now: a sessions namespace (connection records)
   and a conversations namespace (threads) on the API; CLI
   subcommands and generated references move in the same change.
5. Four deep modules: a thread store, a durable turn writer, a
   context hydrator, and thread selection builtin tools.
6. Discovery is part of the selection tool surface: the resume tool
   takes a spoken description and answers a bounded candidate list,
   newest first, with title, last activity and an opening excerpt;
   full-text search is a possible later upgrade, not this feature.
7. Disambiguation converges by construction: after candidates, the
   follow-up selection is by candidate identity, never a second
   free-text description.
8. Resumption is one flow: discovery, an optional disambiguation
   beat, then the recap offer when the backlog exceeds the hydration
   budget.
9. The runtime integrates at its single agent-activation seam; the
   logic lives in the modules, the pipeline calls them.
10. The durability split is internal to the store: thread-feeding
    turn content takes the acknowledged path, metrics stay on the
    never-blocking lossy channel, and dropped-records accounting
    keeps its telemetry meaning.
11. Resumption is a configuration switch that requires text storage
    on, validated at boot; off, or with text off, behavior is exactly
    today's session-scoped conversation, and the selection tools
    answer with a spoken refusal rather than an error.
12. Retention becomes conversation-aware: pruning follows thread
    inactivity, and never removes turns from a thread that remains
    resumable.
13. Hydration rebuilds context from stored utterance and reply text
    plus tool invocations, a stated approximation bounded by a token
    budget, preferring the latest milestone plus the turns after it.
14. A milestone is a summary checkpoint on the conversation, created
    only with the user's consent, at resume time, when the backlog
    exceeds the hydration budget; the spoken recap is the milestone
    text; declining stores nothing. Milestone text is conversation
    content: durable path, text switch, retained and deleted with its
    thread.
15. Default activation starts a fresh thread; resumption is always
    explicit; no silent auto-resume.
16. Conversations are agent-scoped with the originating device
    recorded; resumption works from any device bound to the agent;
    the owner concept waits for users, as an additive column.
17. Titles in v1 derive from the first utterance.
18. No backfill: threads are never derived from historically recorded
    turns (maintainer decision, 2026-08-21).
19. Generated references (OpenAPI, schema reference, CLI reference)
    regenerate under the existing drift checks in the same changes
    that move the contract.
20. From the 2026-08-24 amendment: the CLI commands for the
    refactored entities operate over the API, never a local database;
    the session-record purge becomes an authenticated endpoint with
    the CLI as its remote caller; thread-aware retention is the
    module that absorbs the purge rather than a rival bookkeeping
    path.

Out of scope, restated so its edges are visible while implementing:
users and ownership, LLM-generated titles and any automatic
summarization, full-text search, budgets and per-conversation
accounting, event-taxonomy changes beyond carrying the conversation
identity where the agent already appears, the admin UI itself,
cross-agent shared threads, and sub-agent delegation.

## Resolved open questions

### The entity is named `conversations`, everywhere

The glossary describes the entity as a thread, and a `threads` table
beside `sessions` was the obvious alternative. The word on every
retained surface is `conversation`: the API namespace the issue fixes
is `/api/conversations`, the CLI noun below is `conversation`, and
concepts.md teaches "conversations are threads" as a definition, not
a second identifier. Introducing `threads` as the storage name would
put two words on one entity with a mapping every surface then owes
(the API address `{conversation}` resolving to `threads.thread`).
One word wins: the new table is `conversations`, its join key column
is `conversation` (a uuid hex, the same shape and role as
`sessions.session`), and `turns` gains a `conversation` column. The
qualified name `conversations.conversations` repeats the schema name
once, in SQL only, and the schema reference explains it in one
sentence. This also lands the resolution docgen.py:45-49 already
anticipates: the store named `conversations` finally stores some.

### The schema ships as a re-cut baseline: a recorded reset, no backfill

The issue's 2026-08-21 amendment is followed as written: there is no
migration and no backfill, the store's schema is simply the new
schema, and pre-existing databases are deleted rather than upgraded.
Concretely, milestone 1 replaces `1001_postgres_conversations` with a
new sole baseline, `1002_conversation_threads`, carrying the whole
thread schema at once: the `conversations` table, `turns.conversation`
(not null: after a reset no pre-thread turn can exist, so the column
admits no legacy state), the `conversation_milestones` table (dormant
until milestone 5, and its reference documentation says so), and the
indexes the new query paths need, named here so the baseline carries
them from its first run:

- `conversations.conversation` unique, the join key, as
  `sessions.session` is;
- `ix_conversations_agent_activity` on
  `(agent, last_active_at, id)`: the agent-filtered listing and
  discovery, in the keyset order;
- `ix_conversations_last_active` on `(last_active_at, id)`: the
  unfiltered listing and retention's inactivity scan;
- `ix_turns_conversation` on `(conversation, id)`: the dialogue
  read and hydration's oldest-first walk (the existing
  `(session, id)` index stays for the session timeline);
- `ix_conversation_milestones_conversation` on
  `(conversation, id)`: the latest-milestone lookup.

The milestone-1 store suite asserts the baseline created them, the
way the current suite pins its own DDL facts. This is the priced exit the standing promise
grants
([product-promises](../architecture/product-promises.md#a-beta-database-is-never-left-behind)),
exercised the way #243 and #283 exercised it: the same milestone
appends an addendum to the 2026-08-20 ADR naming the databases it
strands (anything stamped `1001_postgres_conversations`) and the
tested path back (drop and recreate the database or the
`conversations` schema, rerun `deploy/postgres-init.sql`, boot), and
the changelog carries the breaking entry. A stranded database is
refused loudly, never half-read: Alembic cannot locate the deleted
revision, the db classifier maps that failure to a fixed sentence
naming the reset procedure and no value, and the boot refusal has its
own test against a database stamped `1001`. The CI wheel-migration
step's exact chain assertion (`vinga-server.yml`) moves once, in
milestone 1, to `['1002_conversation_threads']`, and its
expected-tables list gains the two new tables; milestone 5 ships no
migration at all.

### A conversation row lands with its first turn, not at activation

The pipeline mints the conversation identity at the activation seam
(decision 9: boundaries are decided there), but the row materializes
in the writer, inside the same marker transaction as the thread's
first turn. Three reasons: a wake that produces no transcript
produces no turn today and should produce no empty thread to clutter
listings and voice discovery; referential integrity stays where the
schema says it lives, in the writer, with the conversation row and
its first turn in one transaction; and the writer already holds the
per-session state (device, timestamps) the row needs. `record_turn`
carries the conversation id on the `TurnRecord`; the writer inserts
the conversation row if it does not exist yet, derives the title (
below), and updates `last_active_at` on every subsequent turn of the
thread in the turn's own transaction.

### Turn attribution: per session and agent, every recorded turn

Within one session, the first activation of each agent mints a fresh
conversation and a re-activation of the same agent continues it:
"Sophia, let me talk to Nadia, back to Sophia" is one session
touching two threads, exactly the concepts.md sentence. A handover
turn belongs to the conversation that started it (decision 3), which
the existing per-leg attribution already keeps honest. Every recorded
turn carries the active conversation; the concepts page's meta-turn
recording rule (a volume request belongs to no thread) is decided
direction with no owning issue, and v1 deliberately does not
implement it: the honest v1 statement, recorded in the schema
reference, is that every stored turn belongs to the thread that was
active when it was spoken. Nothing precludes the refinement; it would
relax the column for meta turns in a migration of its own later.

Attribution is decided when the turn begins, not when it ends. The
active agent changes mid-reply on a handover, and `_record_turn`
runs in the reply's `finally`, so reading the current agent there
would put the handover turn on the wrong thread. The runtime
therefore snapshots the owning pair (conversation id, agent) when
the turn starts (where `TurnUnderway` begins, at the confirmed
transcript), carries it immutably through the reply, and
`TurnUnderway.record` stamps both onto the `TurnRecord`; the writer
derives the conversation row from those stamped fields alone.
`turns.agent` becomes the owning agent (the one the turn started
with), its column comment moves accordingly, and the per-leg entries
keep the per-agent truth of a split reply. The tests drive a
tool-only handover and a spoken-preamble handover through the
session drivers and assert the turn's conversation and agent are the
starting pair while the legs carry both agents.

### The rename, precisely

- **API**: the three existing GETs move from `/api/conversations*` to
  `/api/sessions`, `/api/sessions/{session}` and
  `/api/sessions/{session}/turns`, with response models renamed
  (`SessionList`, `SessionSummary`, `SessionDetail`, `SessionTurn`,
  `SessionTurns`, `ToolInvocation` and `TurnLeg` keep their names).
  The operationIds and component schema names in the committed
  OpenAPI document follow. This is a breaking change made exactly
  when the issue says to make it, before v1.0 and before the UI
  client exists; the changelog carries it.
- **What keeps the name**: the Postgres schema `conversations`, the
  package `vinga_server/conversations/`, the config section
  `server.conversations.*`, the four `conversations_*` events, the
  command `vinga-server conversations schema` and the generated
  `docs/reference/conversations-schema.md`. All of them name the
  store, and the store keeps its name because after milestone 1 it
  honestly stores conversations; renaming the schema or the config
  section would churn every deployment for a vocabulary the issue
  already declares correct ("the store already carries the right
  name").
- **CLI nouns are singular**: `session` and `conversation`, decided
  in its own section below.

### The two CLI nouns, exactly

The nouns are singular, and that is an explicit revision to the
cli-guide, recorded as one. The guide today presents
`vinga sessions list` in its noun-first examples and names
`sessions` and `conversations` as the nouns this issue adds, and two
merged plural groups exist on the server entry point
(`vinga-server conversations`, `vinga-server events`), so singular
is not the status quo and this plan does not pretend it is. The
ground for the revision is the guide's own naming rule: singular
when a noun addresses one entry, and both new nouns do (`show`,
`delete` take an identity), exactly as the five configuration kinds
(`provider`, `agent`, `device`, `mcp-server`, `prompt-fragment`) are
singular for the same reason. The two merged plural groups address
no entry (one renders the store's schema document, the other the
event reference), so they keep their names under the same rule. The
milestone that lands the noun revises the guide deliberately: the
`sessions list` example spellings become `session list`, the
noun-first sentence naming this issue's nouns moves to the singular,
and the naming section gains a sentence recording the revision with
this plan as the record.

The signatures, all API-client verbs in the `GROUPS`/`COMMANDS`
registries:

```
vinga session list [--device MAC] [--limit N]
vinga session show <session>
vinga session delete <session> [--force]
vinga session purge [--session ID] [--device MAC] [--before YYYY-MM-DD] [--force]
vinga conversation list [--agent NAME] [--limit N]
vinga conversation show <conversation>
vinga conversation delete <conversation> [--force]
```

`--agent` and `--device` are flags, not address segments, because
they filter a listing rather than address one row, which is how
story 14 ("list an agent's conversations") is supplied. `--limit`
mirrors the API's default and maximum; the CLI deliberately exposes
no cursor flags: one invocation prints one page, and walking history
is the API's job (the flags exist there), stated in the help. The
listings are borderless tables in the merged style: sessions print
`SESSION DEVICE AGENT STARTED CLOSED REASON TURNS`, conversations
print `CONVERSATION AGENT TITLE LAST-ACTIVE TURNS` with a null
title rendered as a fixed `-`. `session show` prints the detail
block; `conversation show` prints a header block (id, agent, title,
created, last active) and then the dialogue oldest first as
speaker-labelled blocks, because dialogue lines wrap and a wrapped
column is not a column. Both `delete` rows are registered
destructive, so they confirm at a terminal and take `--force`. How
printed content is bounded is the next section's rule.

### Printed content cannot steer a terminal

Titles, excerpts, dialogue text and every other content-bearing
value these commands print originate in what a room said to a
device, so the cli-guide's determinism rule applies with no content
exemption: every such value passes through the merged `printable`
bounding (truncate to a named width, then replace every unprintable
character, ANSI escapes included, with a question mark) before it
reaches a table cell or a block line. Inside a table cell, a tab or
a newline is an unprintable and is substituted, because a cell that
wraps stops being a cell; in a dialogue block, embedded newlines are
substituted too and the block's own line structure comes only from
the renderer, so nothing an utterance carries can add lines, move
the cursor or recolor the terminal. Output is byte-identical at a
terminal and through a pipe (no color, no terminal-dependent
rendering anywhere in these commands). The tests plant
ANSI-escape-bearing, control-character, tab and newline content in
titles and dialogue and compare both streams byte for byte,
terminal and redirected.

### Named deletion, and the purge with its settled selectors

The retired `conversations purge` took `--session`, `--device` and
`--before` selectors, and the 2026-08-24 amendment makes that purge
an authenticated endpoint with the CLI as its remote caller, so the
selector semantics are settled scope, not this plan's to shrink.
Three deletion surfaces, in milestones 2 and 3:

- `DELETE /api/sessions/{session}` erases one named session, and
  `DELETE /api/conversations/{conversation}` (milestone 3) one named
  thread, row and children together: story 16 and the #282
  `--session` selector.
- `DELETE /api/sessions?session=...&device=...&before=...` is the
  purge: at least one of the three settled selectors, combined with
  AND when several are given, deleting every matching session in one
  transaction with exactly the per-session semantics and provenance
  cascades below applied to the whole set, and answering the deleted
  counts. The selector set and the date selector's day semantics
  carry over unchanged from the #282 record. A bare `session=`
  purge and the named `DELETE /api/sessions/{session}` do the same
  thing through the same `threads.py` helper, and the overlap is
  deliberate: the named form is the address the UI and the noun
  grammar want, the selector form is the settled purge surface, and
  neither is a second bookkeeping path.
- Retention remains the automatic age-based path, which is what the
  amendment means by the purge being absorbed rather than rivaled:
  the endpoint and the pruning pass share the deletion helpers in
  `threads.py`, one bookkeeping path.

The CLI verbs are `session delete <session>`,
`conversation delete <conversation>` and
`session purge [--session ID] [--device MAC] [--before YYYY-MM-DD]`
(at least one selector required, refused otherwise with a fixed
sentence), all
registered destructive per the cli-guide (confirm at a terminal,
`--force`).

Deletion semantics, from the #282 record and the Postgres cutover:
deletion is one transaction over the row and its children; a session
that is still running when its row goes stops being recorded (the
writer's tombstone rule already does this); MVCC means an in-flight
repeatable-read transaction still sees the rows until it ends, which
the schema reference already states and keeps stating. Both
deletions run over a short-lived write engine opened per request
(deletion must work when recording is off and no store object
exists, exactly as reads already do).

Erasure outranks every copy the store derived, not only the source
rows. Deleting a session deletes its turns even where they belong to
a live thread (the thread honestly keeps a gap), and the same
transaction erases what those turns fed: a conversation whose title
was derived from a deleted turn (the title source is the thread's
first turn, so this is decidable from surviving rows) has its title
recomputed from the earliest surviving turn or nulled; every
milestone whose recorded coverage (`from_turn` through `after_turn`)
intersects a deleted turn is deleted with it, and so is every
descendant along the `parent` lineage, because a summary of erased
content is that content whether it arrived directly or through an
earlier recap the summarizer consumed; and `last_active_at` is recomputed
from the surviving turns. A conversation that loses every turn is
deleted whole rather than left as an empty shell. Deleting a
conversation deletes its turns and milestones out of their sessions'
timelines the same way, and never touches session rows or events.
The milestone-2 sentinel test is the reviewer's: a credential-shaped
utterance whose text has become a title and, in milestone 5's suite,
a milestone, then the source session deleted, and the sentinel
hunted through every table and read surface.

Deletion of a conversation never resurrects. Absence of a row cannot
be the tombstone, because absence is also the ordinary
pre-first-turn state; the writer therefore keeps the distinction it
already needs anyway: it remembers the conversation ids it has
materialized this process, and the deletion endpoints mark deleted
ids dead through a store-level signal the writer consumes at its
next marker (a generation counter on the deletion path; the exact
mechanism is the implementation's, the contract is the writer's).
A turn arriving for a dead id is discarded, its acknowledgement
resolves false, and the id is never re-materialized; a turn for an
id the writer has never seen is a first turn and materializes as
designed, which is safe across restarts because runtimes do not
outlive the process and a resume of a deleted conversation fails its
row lookup with the fixed not-found refusal. The in-memory
conversation continues speaking (the user's experience is not the
eraser's to interrupt); its records stop landing, which is what
erasure means. Tested through the real endpoint with the gate seam:
a turn queued before the delete commits, and a turn produced after
it, both discarded with false acknowledgements and no new row.

### The thread listing's order and its cursor

`GET /api/conversations` orders by activity, and `last_active_at`
moves, so the immutable row-id cursor the session list uses cannot
paginate it. The contract is keyset pagination over the pair
(`last_active_at`, `id`): rows order by `last_active_at` descending
with `id` descending as the tie-break (UTC ISO-8601 text compares
lexicographically, which is why the pair is total), and a page after
the first is requested with two named query parameters,
`cursor_active` and `cursor_id`, carrying the previous page's last
row's pair; the page holds rows strictly below that pair. The
parameters come together or not at all, and a half-supplied or
malformed pair is the ordinary validation problem naming the
parameters. The #120 rule that a cursor is a plain value and never
an opaque encoding to version is kept by spelling the pair out as
two parameters. Under concurrent activity the semantics are stated
rather than implied: a thread whose activity moves it ahead of the
cursor between pages is missed by that listing pass and appears at
the head of a fresh one, and a page boundary never duplicates or
skips rows whose pair did not change. The tests drive equal
timestamps split across a page boundary, an activity change between
pages, and the ordinary edges (empty, one page, exact boundary,
cursor past the end).

### The durable path: turns retry bounded, and writes acknowledge

Today a failed marker transaction rolls back, counts its batch as
lost, and moves on; correct for telemetry, a silent hole for product
state. The split (decision 10) lands inside the store:

- **Class, not configuration**: `Turn` records (and milestone records
  in milestone 5), the conversation rows they materialize, and the
  `Open`/`Close` control records are the durable class whenever the
  store records; `Event` records stay the lossy class. No mode fork
  on the resumption switch: one write behavior to test, and a
  deployment that enables resumption later is not haunted by holes
  from before the flip.
- **Two transactions per marker**: the marker commit splits so
  product state and telemetry never share a fate. Reaching a marker,
  the writer commits the durable half of that session's batch first
  (session row work, conversation rows, turns, tool invocations,
  milestones) in one transaction, then the accumulated event rows in
  a second. An event-transaction failure drops and counts events
  exactly as today (`sessions.dropped`, `WriteFailed`) and touches
  no turn; a durable-transaction failure never takes events' fate
  either way.
- **Bounded retry, then a loud hole**: a failed durable transaction
  retries in place up to `TURN_WRITE_ATTEMPTS` (3, a named constant
  with its reason beside it) when the failure is in the transient
  class the db classifier already names (lock and serialization
  failures); retries run on the writer thread with no sleep, since
  the transient class either clears at once or is not transient and
  the writer must not stall other sessions. A non-transient failure
  or an exhausted budget drops the durable batch, and the hole stops
  being silent through the next point.
- **The incomplete latch is product state, not telemetry**: the
  `conversations` table carries `incomplete` (boolean, default
  false, in the baseline), deliberately outside the metrics switch,
  because `sessions.dropped` is zeroed under metrics-off and product
  state may not be. When a durable batch is dropped, the writer
  latches the affected conversation ids in memory. For a
  conversation whose row is already materialized, `incomplete =
  true` is written as its own small transaction, retried at every
  subsequent marker until it lands and again at session close. When
  the dropped batch held the thread's first turn there is no row to
  flag and none is created (an empty thread is worse than a pending
  flag): the pending id stays latched, and the first later
  successful turn of that thread materializes the row with
  `incomplete` already true, in that same row-and-turn transaction.
  A thread whose every write fails leaves no row at all, which is
  the honest record: there is nothing to resume and nothing claiming
  otherwise. The
  residual window is stated rather than implied away: if the
  database never recovers before the process ends, neither the tail
  turns nor the flag persist, and the stored thread simply ends
  earlier; the reference documentation states this bound. Resuming
  an incomplete thread is warned, not prevented: the resume result
  carries a fixed sentence saying the record has gaps, for the model
  to convey.
- **Acknowledgement**: `ConversationStore.record_turn` (and
  `TurnStore`/`SessionTurns`) return an `Acknowledgement`, a small
  handle with `wait(timeout) -> bool` resolved true by the writer
  when that turn's durable transaction commits, and false when the
  turn is dropped (tombstoned session, deleted conversation,
  exhausted retries, writer shutdown). An acknowledgement speaks
  only for its own turn: the resume path reads the thread's
  `incomplete` flag as well, so a later success never implies an
  earlier write landed. The pipeline's ordinary path ignores the
  return value, so its never-block contract is untouched (the handle
  is created, never awaited on the audio path). The consumers are
  milestone 4's resume path, which waits bounded on the target
  thread's latest acknowledgement before hydrating so a same-session
  switch-back cannot read past its own writes, and milestone 5's
  recap flow. In milestone 2 the handle exists and nothing consumes
  it, which is dormant machinery in the #120 sense; the store suite
  proves its semantics through the gate seam.

### Thread-aware retention, exactly

The cutoff is unchanged (`retention_days`, 90 by default, 0 keeps
forever); what changes is the unit. The whole ruleset lands
atomically in milestone 1, replacing the session-age pass in the same
change that creates thread rows, because the old pass and the new
rows cannot coexist in a release: session-age pruning would delete
the turns of a thread still active (once threads span sessions), and
a partial ruleset would leave either conversation rows or turn-less
sessions unpruned. Rules, applied in one pass in the writer where
pruning already runs:

1. A conversation whose `last_active_at` is older than the cutoff is
   pruned whole: its milestones, its turns' tool invocations, its
   turns, then the row. Turns die only here and through the explicit
   deletion endpoints, never by session age.
2. Events are session-scoped telemetry, not part of the projection:
   they are pruned by their session's `started_at` age alone,
   whether or not the session row itself survives, so a live thread
   never pins old telemetry indefinitely.
3. A session row whose `started_at` is older than the cutoff is
   deleted only when no turns reference it any more; a session kept
   alive by a live thread keeps its row (the minimal spine the
   turn-to-session cross-reference needs), with its events already
   gone under rule 2.

With resumption unused, threads never span sessions, thread
inactivity coincides with session age, and the pass degenerates to
today's behavior, which is what story 11 requires of the stricter
deployments. `ConversationsPruned` gains a `conversations` count
beside `sessions` (additive), in milestone 1 with the rules. The
milestone-1 tests include the reviewer's case: a session begun
before the cutoff holding a thread active after it keeps its row and
that thread's turns while losing its events.

### The resumption switch and its boot refusal

`server.conversations.resumption: bool = False`, beside a
`resumption_budget_tokens: int = 6000` (ge 512) it reads, flat keys
in the existing section's style. A `@model_validator` on
`ConversationsConfig` refuses `resumption: true` with `text: false`,
and refuses `resumption: true` with `enabled: false`, each with a
fixed sentence naming both keys and the fix, raised as
`FieldProblemsError` with json pointers per the `FillerConfig`
precedent (models.py:1639), so the refusal points at the field under
whatever layer holds it. Both keys land in milestone 4 with the
behavior they enable, never before it, which is the #120 plan's
finding-11 rule: the first release in which the switch exists is the
first in which it does everything its documentation says.
`config.example.yaml` and `config.deploy.example.yaml` move in the
same change.

### The selection tools

Two builtin tools, named in `names.BUILTIN_TOOL_NAMES` (which
reserves them against MCP entries by construction), declared in
`tools/builtin.py`, offered by `BuiltinTools.snapshot`:

- **`new_conversation`**: no arguments. Ends the agent's current
  thread and starts a fresh one: the runtime mints a new conversation
  id for the active agent and resets that agent's in-session history,
  through the same boundary transition as a resume. Decision 11 is
  applied to both tools alike: with resumption off, text storage off
  or the store disabled, behavior stays exactly today's
  session-scoped conversation, so the tool answers its fixed spoken
  refusal and mutates nothing. Both tools are offered always, which
  is what makes the refusal a spoken sentence the model can convey
  rather than a hallucinated answer to a tool that does not exist.
- **`resume_conversation`**: `description` (free text) or
  `conversation` (a candidate id) and, from milestone 5, `start_from`
  (`recap` or `recent`). With `description`, it answers a bounded
  candidate list (`RESUME_CANDIDATES = 5`: ordinal, id, title, last
  activity, opening excerpt) for the model to read aloud; the tool's
  result text instructs the model to have the user pick, and the
  follow-up call carries `conversation`, which is decision 7's
  convergence by construction. With `conversation`, the runtime
  performs the resume. When resumption is off, text storage is off,
  the store is disabled, or the id is not one it offered, the tool
  answers a fixed spoken-refusal result (`is_error` false, per
  decision 11: a refusal the model voices gracefully, not an error),
  each refusal a fixed sentence naming no user input.

**Discovery matches, it does not merely list.** The description and
each candidate's title and opening excerpt are normalized the same
way (casefold, punctuation stripped, whitespace-split into tokens),
a candidate's score is how many description tokens appear among its
tokens, and candidates order by score descending, then
`last_active_at` descending, then id descending, so the answer is
deterministic and a relevant older thread outranks a newer unrelated
one. The tool returns the top `RESUME_CANDIDATES` with a positive
score; when nothing scores, it returns the newest
`RESUME_CANDIDATES` with a result sentence saying nothing matched
the description, so the user can still pick or refine rather than
dead-end. This is deliberately not full-text search over turns,
which the issue defers; it is matching over what the listing already
carries. The tests include the reviewer's case: a relevant
conversation outside the five newest is found by its title tokens.

**Flow state is runtime-owned and enforced.** Tool results live only
inside one reply's working list, so the flow cannot lean on the model
remembering candidates: the runtime keeps a small per-agent pending
state across utterances. It holds the candidate ids of the agent's
last discovery answer with their ordinals, and, once an over-budget
resume has been offered, the one conversation awaiting the recap
decision. `conversation` is honored only when the id is in the
asking agent's offered set (anything else gets the fixed
unknown-candidate refusal), and `start_from` only when that
conversation holds the pending offer, so a model cannot invoke a
recap that was never offered. The state clears on a successful
transition, is replaced by a new discovery answer, and clears on
handover, on `new_conversation` and at session end. The tests drive
a stale id after a new search, an id offered to a different agent,
a direct `start_from="recap"` with no pending offer, and the
two-utterance flow where "the second one" arrives an utterance after
the candidates.

**A transition happens at a coherent boundary, never mid-turn.** A
successful selection follows the `switch_agent` precedent
(pipeline.py:1143) to its end: it ends the current tool loop, and
the initiating turn completes wholly on its origin thread, where the
turn-start snapshot already holds it. The runtime then applies the
transition (rebinding the agent to the target thread and installing
the hydrated or fresh history) and starts a new round on the new
context, seeded with a fixed synthetic user turn in the
`SWITCH_GREETING` shape, whose reply is the first turn recorded on
the target thread. At most one transition per reply, mirroring the
existing `switches_left` latch: the first successful selection or
handover wins and later calls in the same round answer the fixed
already-switched refusal, so no turn is ever split across
conversations and mixed calls have a stated precedence.

Execution split follows the merged precedent exactly: discovery
(`description`) executes in `BuiltinTools.dispatch` through an
injected thread-store read seam (a fourth constructor argument,
compared `is not None`), because it is a read that changes nothing;
selection (`conversation`) and `new_conversation` are intercepted in
the runtime's tool loop as above. Store reads run under the existing
per-source tool timeout through `asyncio.to_thread`, so a slow
database is a timed-out tool call, never a stalled reply. The seam
is a sanitized boundary, because `_run_one` embeds a raised
exception's own words into the model-visible and stored tool result:
every thread-store failure, on the dispatch path and the
interception path alike, is caught at the adapter, answered as a
fixed value-free spoken result chosen by exception class, and never
re-raised, so no driver wording, DSN, SQL or credential can reach
what the model sees or the store keeps. The poisoned-driver sentinel
test plants a credential-and-DSN-shaped message in a raising engine
and hunts it through the spoken reply, the model context, the stored
invocation row, both log formats, the events, an attached tap and
both process streams.

### Per-thread context, and the clean switch

Milestone 4 turns the runtime's single `self._turns` transcript into
per-conversation histories: a map from conversation id to `list
[Turn]`, plus a map from agent to its session-current conversation
id. `_activate_agent` binds the incoming agent to its current thread
(minting one on first activation), and the LLM context for a reply is
the active thread's history alone. This closes the gap concepts.md
records: today the whole session transcript carries across a
handover; after this milestone the incoming agent sees only its own
thread, with the `SWITCH_GREETING` seed as the fresh thread's first
turn. Switching back to an agent resumes that agent's session-current
thread with its in-memory history intact. An explicit resume rebinds
the agent's current thread to the resumed conversation and installs
the hydrated history. Nothing about turn-taking, pacing or capture
moves; the change is which list the three existing append sites and
the one read site use.

### Hydration

`conversations/hydration.py`, near-pure: rows in, `list[Turn]` out.

- Input: the thread's turns oldest first (heard, reply, tool
  invocation rows), the latest milestone when one exists (milestone
  5), and the token budget.
- Each stored turn renders as `Turn("user", heard)` and
  `Turn("assistant", reply)`; a turn's tool invocations render as one
  fixed-format parenthetical line appended to the assistant text
  naming tool names only (arguments and results stay in the store:
  the history a model needs is what was said and that tools ran, and
  names are already under the text switch). A turn whose text
  columns are null (recorded under text-off) contributes nothing and
  is counted as a gap; the hydrator reports how many turns it could
  not render so the resume path can say so.
- The budget is approximate by design: `ESTIMATED_CHARS_PER_TOKEN =
  4`, a named constant, and the reference documentation states the
  approximation. The unit of budgeting and of truncation is the
  whole stored turn (its user half, its assistant half and its tool
  note together), never a lone message, so a reply can never survive
  without the utterance it answered and the rendered history always
  alternates roles by construction. Overflow drops the oldest whole
  units first (milestone 4); with a milestone, the milestone text is
  pinned as the head and overflow drops the oldest units of the tail
  after it, never the milestone itself (milestone 5). When the
  single newest unit alone exceeds the budget it is included alone,
  because an empty resume would be a worse answer than an
  over-budget one and the budget is an estimate to begin with; the
  case is stated and pinned. The hydrator's suite asserts the
  resulting role sequences, not only counts.
- Output carries the turns, the count rendered, the count skipped,
  and whether the untruncated backlog exceeded the budget, which is
  the recap-offer trigger.

The hydrator does not read the database; the thread store hands it
rows. It is the module that owns "what does stored dialogue look like
as LLM context", and its suite is input-to-output with no engine.

### The recap flow, mechanically

Resume with a backlog inside the budget hydrates and answers with a
fixed result telling the model to continue the conversation. Over
budget, milestone 4 behavior (before recaps exist) hydrates the tail
under the budget and says so in the result; milestone 5 replaces that
with the offer:

1. `resume_conversation(conversation=X)` over budget does not swap
   context; its result instructs the model to offer the user a choice
   between a recap and simply continuing from recent turns.
2. Consent: `resume_conversation(conversation=X, start_from="recap")`,
   honored only against the pending offer, intercepted like every
   selection. The runtime, not the model, owns everything after the
   consent:
   - It hydrates the backlog for summarization (the latest milestone
     plus the turns after it, truncating oldest first under
     `RECAP_INPUT_BUDGET_TOKENS`, its own named constant) and records
     the range actually included: `from_turn`, the first covered
     turn's id, `after_turn`, the last, and, when a milestone was in
     the input, its id as `parent`.
   - It runs one round against the active agent's own LLM provider
     with a fixed summarization instruction, under its own timeout
     (`RECAP_ROUND_TIMEOUT_S`).
   - It speaks the recap itself: the summarizer's text is fed to the
     reply's synthesis path verbatim, with no second model round to
     rephrase it, so what the user hears is byte-for-byte the text
     that will be stored. The consent turn records on its origin
     thread like any turn, with the spoken recap as its reply.
   - It stores the milestone only after playback completes at the
     device-facing edge (the reply-completion point the runtime
     already has), on the durable path with a bounded
     acknowledgement wait. The ordering is the guarantee: before
     completion nothing is stored, so a barge-in, a synthesis
     failure, a disconnect or a crash mid-recap stores nothing and
     the next resume simply re-offers; a write that lands after an
     acknowledgement timeout is late but never unheard, because the
     write is not enqueued until playback finished. A storage
     failure keeps the installed context for this session and the
     next resume re-offers.
   - It installs recap-plus-tail as the thread context at the same
     transition boundary every selection uses.
   A summarization failure (provider error, timeout) falls back to
   the recent-tail resume with a result that says the recap could
   not be made; nothing is stored.
3. Decline: `resume_conversation(conversation=X, start_from="recent")`
   hydrates the tail under the ordinary budget and stores nothing.

A milestone row is `conversation_milestones`: `id` (bigint identity),
`conversation` (text), `from_turn` and `after_turn` (bigint, the
first and last `turns.id` the summarizer actually read, so a bounded
recap never claims turns it omitted), `parent` (bigint, nullable:
the milestone whose text was part of this recap's input, recording
that its content flows transitively into this one), `created_at`
(text, UTC ISO-8601), `text` (text, nullable under the text switch
by the uniform rule, though the flow that creates one cannot run
with text off). Milestone-aware hydration reads the latest row and the turns
with `id > after_turn`; turns at or before `from_turn` are outside
the recorded coverage, exactly as oldest-first truncation would have
dropped them, and the reference documentation states the boundary.

### Events

- Milestone 1: the `conversation` identity (a trusted server-minted
  identifier, metadata under the ADR) joins the events where `agent`
  already appears, via a `SessionEvents.conversation` field stamped
  by activation exactly as `SessionEvents.agent` is; the ~15
  agent-bearing declarations gain the field, `Handover` carries
  `from_conversation`/`to_conversation` beside its agent pair, and
  `docs/reference/events.md` regenerates. Additive.
- Milestone 4: `conversation_resumed` (conversation, hydrated turns,
  skipped turns, over-budget flag), emitted at the runtime's resume
  decision site.
- Milestone 5: `milestone_recorded` (conversation). Refusals need no
  event: a refused selection is a `tool_invocations` row and a spoken
  result, and inventing a reason vocabulary for it would duplicate
  the closed refusal sentences.

No event is renamed and none is removed; the four `conversations_*`
store events keep their names (they name the store), and
`ConversationsPruned` gains its additive count field in milestone
1, with the retention ruleset it counts for.

## Module layout

```
vinga_server/conversations/
    schema.py       + conversations table, conversation_milestones
                    (dormant until m5) and turns.conversation, all in
                    the m1 baseline
    records.py      + the (conversation, agent) owning snapshot on
                    TurnRecord (m1); Acknowledgement (m2)
    store.py        the queue writer: gains the two-transaction
                    marker, bounded turn retries and acknowledgements
                    (m2), and executes thread lifecycle and retention
                    by delegating to threads.py inside its own
                    transactions (m1)
    threads.py      NEW (m1): the thread store, owning what decision
                    5 assigns it: conversation lifecycle (row
                    materialization, title derivation,
                    last-activity), the retention ruleset, session
                    deletion and the selector purge with their
                    provenance cascades (m2), conversation deletion
                    and the dead-id rule (m3), thread listing,
                    detail and resume candidates (m2, m3), thread turns for hydration
                    (m4), and milestone policy (m5). What callers
                    stop knowing: the five-table join topology, the
                    deletion ordering that keeps children, parents
                    and derived copies together, and the MVCC
                    caveats. Its callers are the writer (which embeds
                    its lifecycle and retention operations in marker
                    transactions), the API handlers and the selection
                    tools.
    hydration.py    NEW (m4): stored rows to LLM context under a
                    budget. What callers stop knowing: how dialogue,
                    tool notes, gaps, milestones and truncation
                    become a message list.
    api.py          sessions rename (m1), session delete (m2),
                    conversations namespace (m3)
    cli.py          untouched (the schema command stays)
    docgen.py       new tables and the retention prose follow their
                    milestones
    migrations/     the re-cut baseline 1002_conversation_threads (m1)
vinga_server/tools/
    names.py        + NEW_CONVERSATION, RESUME_CONVERSATION (m4)
    builtin.py      + the two tool declarations and the discovery
                    executor (m4)
    source.py       BuiltinTools gains the injected thread-store read
                    seam (m4)
vinga_server/runtime/
    pipeline.py     conversation minting at _activate_agent (m1),
                    per-thread histories and the clean switch, resume
                    and new-conversation interception, hydration call
                    (m4), the recap round (m5)
vinga_server/config/
    models.py       resumption keys and the boot refusal (m4)
    cli.py          session noun (m2), conversation noun (m3), both
                    as API-client verbs in the GROUPS/COMMANDS
                    registries
```

The deletion test, answered for the two new modules: `threads.py`
has three callers with no shared parent (the writer, the API
handlers, the selection tools), so inlining it into any one would
spread the join topology, deletion ordering and provenance cascades
across the other two; inlining `hydration.py` into
`pipeline.py` would put a pure, separately-testable rendering rule
inside the module this repository has been shrinking since #245.
Everything else deepens an existing module rather than adding one
beside it; in particular there is no new "durable writer" module,
because decision 10 makes durability an internal property of the
writer the store already owns, and a wrapper would be a pass-through.

## The standing review lenses, answered

- **No-leak, at every retained surface.** Titles, excerpts and recap
  text are conversation content: they live in the store under the
  text switch, travel to the API and CLI read surfaces (which exist
  to show content) and into tool results (which are conversation
  surface), and never into events, logs, or any error sentence. The
  new events carry ids, counts and booleans only. Every new refusal
  (tool refusals, CLI sentences, API problems, boot refusals) is a
  fixed sentence built in the handler and raised after it, naming
  keys and never values. Sentinel tests per milestone plant a
  credential-shaped utterance and assert its absence from every log
  record in both formats, every event field, an attached tap, API
  error bodies and CLI stderr, while asserting its presence exactly
  where it belongs (the title it becomes, the dialogue read).
- **Pin before reshaping.** The store, session and event suites are
  the committed baseline. The renames (API paths, model names) and
  the milestone-4 history change are deliberate surface changes whose
  pins move in the same commits, the #152 precedent; the milestone-1
  writer change is proven additive by the existing store suite
  passing with only the new-column assertions added; the milestone-4
  context change lands behind characterization pins on what each
  agent's provider receives (`RecordingLlm.systems` and the history
  helpers already pin this; the handover-carry pins move deliberately
  with the changelog entry).
- **Closed sets mapped to decision sites.** No new reason tokens.
  The refusal sentences of the selection tools are a closed set of
  fixed strings chosen at the runtime's interception site and the
  dispatch site; `start_from` is a two-value closed set validated at
  the same site; the retry class is the db classifier's existing
  closed set, not message sniffing.
- **Honest seams.** The thread-store read seam in `BuiltinTools`, the
  recorder, and the milestone-5 summarization hook compare
  `is not None`. Defaults that injected seams cannot prove get their
  own pins: `TURN_WRITE_ATTEMPTS`, `RESUME_CANDIDATES`,
  `ESTIMATED_CHARS_PER_TOKEN`, the resume acknowledgement wait bound,
  and `RECAP_INPUT_BUDGET_TOKENS`.
- **Inventories by tooling.** The rename sweep is the census in this
  plan's review record plus scoped greps re-run at implementation
  time (`conversations` over `src`, `tests`, `docs/reference`,
  workflow), with the deliberate keepers enumerated as an allowlist
  (the store's own name: schema, package, config section, events,
  command, reference file). The command-spellings manifest
  regenerates in the same commit as the last documentation edit of
  any milestone that moves a spelling (the #309 lesson, verbatim in
  the briefs). The workflow's chain assertion and expected-tables
  list move once, with the re-cut baseline in milestone 1.

## PR structure

Five milestones, one PR each, stacked, every merge releasable.
Ordering constraints that cut them this way: the switch may not exist
before everything it documents (milestone 4, per #120's finding 11);
thread rows may not exist without a pruning rule (milestone 1 carries
its own minimal rule); behavior changes sit alone in review
(milestone 4 is the only one that changes what a conversation feels
like; 1 through 3 are descriptive, durable or read-only). The
stacked-PR trap from #86 is procedure: retarget children to `main`
before the parent merges, rebase with `--onto`, and regenerate
generated artifacts on the rebased tree whenever both sides moved
one.

## Tests

Reuse, do not restate: the store suite's gate and injected-queue
seams, `tests/support/sessions.py` drivers (`session_for`,
`run_reply`, `call`, `history`), `tests/support/stores.py`,
`RecordingLlm`/`ScriptedLlm`, the acceptance seam for the API, the
CLI suites' client seam, the integration lane's booted deployments,
and the per-worker database fixtures.

New coverage, by milestone:

- **Milestone 1**: the re-cut baseline migrates a fresh database and
  the wheel step proves it from the installed artifact; a database
  stamped `1001_postgres_conversations` is refused at boot with the
  fixed reset sentence; conversation row lands with
  the first turn and not at activation; per-agent minting across a
  handover (two threads, the handover turn on the first, driven both
  tool-only and with a spoken preamble, the legs carrying both
  agents); title
  derivation (truncation boundary, text-off null title);
  `last_active_at` moves with each turn; the retention ruleset (a
  thread past the cutoff dies whole; a pre-cutoff session holding a
  live thread keeps its row and turns and loses its events; a
  turn-less session past the cutoff goes); the renamed API routes round-trip with pins moved and the
  route inventory updated; `conversation` on turn responses;
  events carry `conversation` beside `agent` (the event-assertion
  suites extend); both generated documents byte-green; the sentinel
  planted as an utterance shows up in the title and nowhere else.
- **Milestone 2**: acknowledgement resolves on the durable commit
  (gate seam); resolves false on session tombstone, exhausted
  retries and shutdown (the deleted-conversation case is milestone
  3's, with its endpoint); transient-failure retry proven
  with a raising-then-working engine; an events-transaction failure
  drops events only while the same marker's turns land; a dropped
  durable batch latches `incomplete` and the flag lands on the next
  marker and survives metrics-off; an early failed turn followed by
  a later success leaves the flag true; DELETE session
  round trip (row and children gone, live thread keeps its other
  turns, empty conversation shells deleted, titles recomputed or
  nulled and `last_active_at` recomputed from survivors, the
  planted-title sentinel gone from every table and surface);
  the selector purge by session, by device, by date and combined,
  refused with no selector, its counts answered, cascades applied
  to the set;
  running-session deletion ends its recording (existing tombstone
  test extended over HTTP); 401 without the token; CLI `session
  list|show|delete` through the client seam, confirmation and
  `--force`, fixed refusal sentences, sentinel absent from stderr,
  planted control-and-ANSI content bounded byte-identically at a
  terminal and through a pipe;
  spellings manifest green.
- **Milestone 3**: conversations list ordered by activity with the
  agent filter and the keyset cursor (empty, one page, exact
  boundary, cursor past the end, equal timestamps split across a
  boundary, an activity change between pages, the half-supplied
  pair refused); detail; dialogue turns oldest-first with tool rows nested;
  DELETE conversation (turns leave their sessions' timelines,
  sessions and events untouched); conversation dead-id semantics
  through the real endpoint (queued-before and produced-after turns
  discarded with false acknowledgements, no row re-materialized); CLI `conversation list|show|delete`, with
  planted control-and-ANSI titles and dialogue bounded
  byte-identically on both streams and the null title rendered as
  the fixed `-`;
  OpenAPI and spellings green.
- **Milestone 4**: boot refusals for both contradictory combinations,
  by fixed sentence and json pointer; hydrator input-to-output
  (budget edges, whole-unit truncation order and role sequences,
  the pinned milestone with a trimmed tail, the one-unit-over-budget
  case, tool-note rendering, text-off gaps counted, deterministic
  output); tools: candidate scoring (normalization, token overlap, the
  (score, activity, id) order, a relevant older thread beating newer
  unrelated ones, the no-match fallback sentence), ambiguous
  description, selection by id, refusals
  when off for both tools (spoken result, not error, and no context
  mutation), `new_conversation` resets context and rebinds when
  resumption is on; the pending-state enforcement (stale id, foreign-agent
  id, recap with no offer, the two-utterance selection); the
  poisoned-driver sentinel through the tool seam (absent from
  speech, context, invocation rows, logs, events, taps and both
  streams); runtime:
  the clean switch (the
  incoming agent's provider never receives the outgoing agent's
  words, asserted on `RecordingLlm`), switch-back continuity, resume
  installs hydrated history and rebinds, read-your-writes through
  the acknowledgement on a same-session switch-back,
  `conversation_resumed` emitted; integration: two real sessions
  over websockets, resume by description in the second, the thread
  continues (the lane's one new end-to-end case).
- **Milestone 5**: over-budget resume offers instead
  of swapping; consent speaks the recap through the synthesis path
  and stores it only after playback completes, the stored text
  byte-equal to what was synthesized; a barge-in, a synthesis
  failure and a disconnect mid-recap each store nothing and the next
  resume re-offers; a storage failure after playback stores nothing
  and re-offers; decline stores nothing and resumes the tail;
  summarization failure falls back with nothing stored; a backlog
  wider than the recap input budget records its true `from_turn` and
  hydration never skips turns the recap did not cover;
  milestone-aware hydration
  (latest milestone plus `id > after_turn`); milestones die with
  their thread (retention and delete); the transitive erasure
  sentinel (a turn represented only through an earlier milestone
  consumed by a later recap: deleting its session deletes the parent
  and every descendant along `parent`, the sentinel hunted through
  every table and surface); the API detail exposes
  milestones; sentinel: recap text never reaches events or logs.

## Risks and mitigations

- **The milestone-4 context refactor touches the pipeline's hottest
  state.** Mitigation: the change is confined to which list four
  existing sites use; the session suites pin everything around it;
  the handover-carry pins are inventoried before the change and
  moved deliberately with it, never silently.
- **The recap adds an LLM round inside a tool path.** Bounded by its
  own timeout constant; failure falls back to the recent-tail resume
  with a spoken note and stores nothing; the round runs against the
  agent's own provider so no new egress surface appears.
- **Store reads from the event loop.** All selection-tool and
  hydration reads run in `asyncio.to_thread` under the existing tool
  timeout; the reply can time a tool out but never wait on a socket.
- **Two writers and a deleter.** The API deletions run beside the
  writer; the tombstone rule (`_alive` per marker) already covers a
  session deleted mid-flight, and milestone 2 extends the same test
  over HTTP. Conversation deletion is covered by the dead-id rule in
  the deletion section: discarded turns, false acknowledgements, no
  re-materialization, tested through the real endpoint.
- **Generated-artifact rebases.** Both sides of a stacked rebase can
  regenerate OpenAPI, events.md, the schema reference or the
  spellings manifest; regenerate on the rebased tree and prove green
  before pushing (the #86 procedure, restated in every brief).
- **The stale-bytecode trap** (AGENTS.md):
  `PYTHONDONTWRITEBYTECODE=1` outside pytest.

## Open questions

- Whether the meta-turn rule (a turn that is only a meta request
  belongs to no thread) gets an owner; v1 records every turn on the
  active thread and states it.
- Whether `sessions.agents`-style denormalization is wanted on
  conversations for cross-agent listing in the admin UI; deferred to
  the UI issue, the schema precludes nothing.
- Per-conversation cost, auto-resume defaults, retention refinements
  and the recap threshold: named revisitable by the issue, not
  reopened here.

## Milestones

One PR per milestone, ticked with its PR number, each linking to its
implementation-doc section when written.

- [x] [**Sessions are sessions, conversations exist**](2026-08-28-first-class-conversations-implementation.md#m1-sessions-are-sessions-conversations-exist)
  (PR #332, branch
  `feature/first-class-conversations`): the re-cut baseline
  `1002_conversation_threads` with its ADR addendum, stranded-database
  refusal and changelog breaking entry; the
  `TurnRecord` conversation id; minting at `_activate_agent` per
  session and agent; the writer's conversation rows, titles and
  `last_active_at`; `conversations/threads.py` owning lifecycle and
  the whole thread-aware retention ruleset, with the
  `ConversationsPruned` count field; the API
  rename with moved pins and the regenerated OpenAPI document;
  `conversation` on turn responses and on the agent-bearing events;
  the workflow's conversations-chain assertion moved; generated
  references regenerated; concepts.md and glossary.md updated where
  the entity stops being direction ("decided direction (issue #190)"
  markers on what this milestone lands); changelog breaking entry
  for the API rename. Design footprint: deepens `schema.py`,
  `store.py`, `records.py` and the activation seam; adds
  `threads.py`, the thread store of decision 5, whose deletion-test
  answer is in the module layout.
  Documentation footprint: `docs/reference/conversations-schema.md`,
  `docs/reference/api-openapi.json`, `docs/reference/events.md`
  (all generated), `docs/concepts.md`, `docs/glossary.md`,
  `CHANGELOG.md`.
- [x] [**Durable turns and session erasure**](2026-08-28-first-class-conversations-implementation.md#m2-durable-turns-and-session-erasure)
  (PR #334, branch `feature/conversations-m2`): `Acknowledgement` and the
  durable turn commits; session deletion and its
  provenance cascade in `threads.py`; `DELETE /api/sessions/{session}`
  and the selector purge; the `session`
  CLI noun (`list`, `show`, `delete`, `purge`); the spellings manifest;
  cli-guide's owed spelling updated. Design footprint: deepens
  `store.py` (durability as an internal property) and `threads.py`
  (semantic deletion with its provenance cascade). Documentation footprint:
  `docs/reference/conversations-schema.md` (retention prose via its
  generator), `docs/reference/api-openapi.json`,
  `docs/reference/cli.md` (generated half),
  `docs/architecture/cli-guide.md`,
  `docs/architecture/observability-surfaces.md` (the store row's
  erasure sentence and the open-items row), `CHANGELOG.md`.
- [x] [**Threads readable**](2026-08-28-first-class-conversations-implementation.md#m3-threads-readable)
  (PR #335, branch `feature/conversations-m3`): the
  conversations API namespace (list, detail, dialogue turns, delete);
  the `conversation` CLI noun (`list`, `show`, `delete`); thread
  listing, detail and deletion in `threads.py`. Design footprint:
  deepens `threads.py` and `api.py`; no new module. Documentation
  footprint: `docs/reference/api-openapi.json`,
  `docs/reference/cli.md`, the spellings manifest, `CHANGELOG.md`.
- [x] [**Resumption**](2026-08-28-first-class-conversations-implementation.md#m4-resumption)
  (PR #337, branch `feature/conversations-m4`): the
  `resumption` and `resumption_budget_tokens` keys with the boot
  refusals and example configs; `conversations/hydration.py`; the
  two selection tools with the discovery seam and runtime
  interception; per-thread histories and the clean switch;
  `conversation_resumed`; the integration resume case. Design
  footprint: adds `hydration.py` (callers stop knowing how rows
  become context); deepens `BuiltinTools`, the tool loop and the
  activation seam. Documentation footprint: `docs/concepts.md` (the
  resumption and clean-switch semantics stop being direction),
  `docs/glossary.md`, `docs/reference/events.md`,
  `config.example.yaml`, `config.deploy.example.yaml` (the server
  section's own documentation; `domain-config.md` covers the domain
  half and is untouched), the root and
  server READMEs where they state continuity behavior (inventoried
  by `rg -in "conversation|resum" README.md vinga-server/README.md`
  with dispositions recorded), `CHANGELOG.md` (behavior change: the
  clean switch).
- [x] [**Recap milestones**](2026-08-28-first-class-conversations-implementation.md#m5-recap-milestones)
  (PR #338, branch `feature/conversations-m5`):
  the recap offer, consent and decline flow; the
  summarization round with its bounds and fallback; milestone-aware
  hydration; milestones on the API detail; `milestone_recorded`.
  Design footprint:
  deepens `store.py`, `threads.py`, `hydration.py` and the resume
  interception; no new module. Documentation footprint:
  `docs/reference/conversations-schema.md`,
  `docs/reference/api-openapi.json`, `docs/reference/events.md`,
  `docs/concepts.md` (the recap consent semantics land),
  `CHANGELOG.md`.

## Verification

Per milestone, from `vinga-server/`: `uv run ruff check .`,
`uv run pytest tests/unit -q`, `uv run pytest tests/integration -q`,
plus the generated-document drift checks the milestone touches, and
the spellings suite after any documentation move. Inventory claims
are re-established at implementation time with scoped greps whose
expected non-surface matches are enumerated (the store-name
allowlist above), and re-run after every rebase. Anything
unverifiable locally (the published image, the smoke lane) is stated
plainly in the PR's Verification section, never claimed.

## Plan review round

One external review of the plan as first committed (cd7b8f5f): codex
CLI 0.149.1, model gpt-5.6-sol, read-only against this repository
with the issue #190 body and amendment comments supplied, 2026-08-27,
reviewer runtime 6m46s. Verdict as received: **not ready**. Twelve P1
and eight P2 findings, condensed but faithful; each carries its
resolution once the amendment addressing it lands.

1. **P1: the plan reverses the settled no-migration decision.** The
   issue requires no migration and deletion of existing databases;
   the plan introduces additive revisions 1002 and 1003 and upgrades
   populated 1001 databases, and the promise permits a pre-beta
   reset only through a recorded compatibility decision. Follow the
   issue with a documented and tested reset, or obtain an explicit
   amendment first; migration cannot be the concrete implementation
   of "no migration".
   *Resolution*: adopted in its first form. The schema section is
   rewritten as a recorded reset: one re-cut sole baseline
   `1002_conversation_threads` carrying the whole thread schema
   (milestones table included, dormant until milestone 5, so
   milestone 5 ships no migration), an addendum to the 2026-08-20
   ADR naming the stranded databases and the tested path back, a
   fixed boot refusal for a database stamped `1001`, the changelog
   breaking entry, and the wheel step's chain assertion moving once.
   A consequence adopted with it: `turns.conversation` is not null,
   since no pre-thread turn can exist after a reset.
2. **P1: the durable path still drops product state with
   telemetry.** Turns and events share one marker transaction, a
   failed batch is discarded whole after retries, ordinary recording
   ignores the acknowledgement, and `sessions.dropped` is zeroed in
   the supported metrics-off, text-on configuration. Separate
   durable conversation-content commits from lossy event commits;
   persist or latch an explicit incomplete-thread state that warns
   on resumption; a later acknowledgement must not imply earlier
   writes succeeded; test event failure, metrics-off, and an early
   failure followed by a later success.
   *Resolution*: adopted. The durable-path section now splits every
   marker into a durable transaction and an events transaction with
   independent fates, adds the `conversations.incomplete` latch as
   product state deliberately outside the metrics switch (written as
   its own retried transaction, with the never-recovered residual
   window stated), scopes an acknowledgement to its own turn with
   the resume path reading the flag, and names the three tests.
3. **P1: milestone 1 cannot ship with only retention rule 1.** The
   existing session-age pruning would remove a recently active
   thread whose session crossed the cutoff, and rule 1 alone stops
   pruning old null-conversation turns and empty sessions. Land the
   final retention rules atomically in milestone 1, with a test for
   a session begun before the cutoff holding a thread active after
   it.
   *Resolution*: adopted. The retention section lands the whole
   ruleset in milestone 1 in the same change that creates thread
   rows, with the crossing-cutoff test named; the reset resolution
   to finding 1 removes the null-conversation legacy case entirely,
   and milestone 2's scope loses retention.
4. **P1: handover attribution lacks a turn-start snapshot.** The
   active agent changes during the reply and `_record_turn` passes
   the ending agent to `TurnUnderway.record`, so "the handover turn
   belongs to the conversation that started it" is not implementable
   as written. Snapshot the originating conversation and owning
   agent when the turn begins, carry both on `TurnRecord`, derive
   the conversation row from those fields, and test tool-only and
   spoken-preamble handovers.
   *Resolution*: adopted. The attribution section now specifies the
   turn-start snapshot carried on `TurnUnderway` and stamped onto
   `TurnRecord`, moves `turns.agent` to mean the owning agent with
   its comment, and names the two handover tests.
5. **P1: candidate selection and recap consent have no enforced flow
   state.** Structured tool results exist only inside one reply's
   working list, so a later selection has no reliable candidate-id
   mapping, and a model can invoke `start_from="recap"` without a
   prior offer. Keep bounded per-agent pending state (offered
   candidate ids, ordinals, pending recap offer), accept ids only
   from that set and recap/recent only after the offer, clear on
   success, new search, switch or new conversation; test stale,
   foreign-agent, direct-recap and two-utterance selection flows.
   *Resolution*: adopted. The selection-tools section now specifies
   the runtime-owned per-agent pending state (offered ids with
   ordinals, the one pending recap offer), enforcement at both
   arguments, the clearing rules, and the four prescribed tests.
6. **P1: immediate tool-time rebinding splits a turn across
   conversations.** The tool loop snapshots its working list once;
   the user turn is appended before execution and the final speech
   after, so an in-loop rebind leaves the next round on the old
   context while final speech lands in the new history. Define a
   staged transition at a turn boundary (or an explicitly coherent
   loop restart), and precedence for mixed or repeated
   new/resume/switch calls in one round.
   *Resolution*: adopted in its second form. A successful selection
   ends the tool loop the way `switch_agent` does: the initiating
   turn completes on its origin thread, the transition applies at
   that boundary, and a seeded new round is the target thread's
   first turn; one transition per reply via the existing latch, with
   later calls refused.
7. **P1: `new_conversation` contradicts the disabled-mode
   decision.** The issue says that with resumption or text off the
   selection tools answer with a spoken refusal and behavior stays
   session-scoped; the plan offers `new_conversation` unconditionally
   and mutates context without a store. Make both tools fixed
   non-mutating refusals when the prerequisites are absent.
   *Resolution*: adopted. `new_conversation` now refuses under the
   same prerequisites as `resume_conversation`, mutating nothing;
   the unconditional context reset is withdrawn, and the tools stay
   offered so the refusal is spoken.
8. **P1: stored recap text is not guaranteed to equal what the user
   hears.** Returning the recap to another model round with a
   verbatim instruction permits paraphrase, and playback can fail
   after storage; the proposed test compares the row only with the
   tool result. Make recap playback runtime-owned; define pending
   versus final milestone semantics across write failure,
   acknowledgement timeout, TTS failure, interruption, disconnect
   and crash; a timeout must not let a late write create an unheard
   milestone; test spoken-text equality and the failure orderings.
   *Resolution*: adopted. The recap flow is runtime-owned end to
   end: the summarizer's text is fed to the synthesis path verbatim
   with no rephrasing round, the milestone is written only after
   playback completes at the device-facing edge, every earlier
   failure (barge-in, synthesis failure, disconnect, crash) stores
   nothing and re-offers, and a post-timeout write can be late but
   never unheard.
9. **P1: a bounded recap can falsely claim to cover omitted
   turns.** With a backlog above the recap input budget, oldest
   turns are omitted but `after_turn` still causes future hydration
   to skip everything through it. Guarantee coverage, chunk, or
   record an exact coverage boundary that does not hide omitted
   turns, with a backlog-larger-than-recap-budget test.
   *Resolution*: adopted in its third form. The milestone row gains
   `from_turn`, recording the true start of coverage; hydration
   treats turns at or before it as truncated rather than
   summarized, and the over-budget test is named.
10. **P1: conversation deletion can resurrect the forgotten
    identity.** A missing row is both the pre-first-turn state and
    the deletion tombstone, and the risk section permits
    rematerialization. Add explicit issued/deleted state or
    coordinate deletion with active runtimes; a deleted id must
    resolve pending acknowledgements false and never be recreated;
    test queued-before-delete and produced-after-delete through the
    real endpoint.
    *Resolution*: adopted. The deletion section now separates the
    two states: the writer remembers what it materialized and the
    deletion endpoints mark ids dead through a store-level signal;
    a dead id discards turns with false acknowledgements and never
    re-materializes, the restart case is argued from runtimes not
    outliving the process, and both interleavings are tested through
    the endpoint.
11. **P1: session deletion leaves copied session content behind.**
    A deleted session's first utterance can survive as a
    conversation title, its content inside a later milestone, and
    `last_active_at` derived from it. Cascade or track provenance:
    recompute or remove all derived content and activity metadata;
    test a credential-shaped sentinel copied into title and
    milestone, then delete its source session and inspect every
    table and read surface.
    *Resolution*: adopted. Session deletion now erases derived
    copies in the same transaction: the title recomputes or nulls,
    milestones whose coverage intersects a deleted turn are deleted,
    `last_active_at` recomputes, and the sentinel test is exactly
    the reviewer's.
12. **P1: store-backed tool failures can leak credentials.**
    Discovery runs through `BuiltinTools.dispatch`, whose failures
    `_run_one` embeds as `str(exc)` into the model-visible and
    stored tool result; driver exceptions carry DSNs and SQL. Catch
    and classify all thread-store failures at the adapter seam,
    discard cause and context, return fixed value-free results, and
    hunt a poisoned driver message across speech, context, stored
    invocation, logs, events, taps and both streams.
    *Resolution*: adopted. The execution-split section now names the
    sanitized adapter boundary (fixed value-free results by
    exception class, nothing re-raised, both dispatch and
    interception paths) and the poisoned-driver hunt across every
    surface the reviewer lists.
13. **P2: `threads.py` passes the deletion test but violates settled
    ownership.** Issue decision 5 assigns identity, lifecycle,
    listing, milestones and retention to the thread store; the plan
    leaves lifecycle, milestone writes and retention in `store.py`,
    and the claim that the CLI would otherwise duplicate SQL is
    false because the CLI is API-backed. Make the thread-store
    module own thread lifecycle, semantic deletion, milestone policy
    and retention, with the queue writer delegating transactionally.
    *Resolution*: adopted. `threads.py` is born in milestone 1
    owning lifecycle, title derivation, the dead-id rule, retention
    and (from milestone 2) semantic deletion and (from milestone 5)
    milestone policy, with the writer delegating inside its marker
    transactions; the false CLI-duplication sentence is corrected to
    the module's real three callers.
14. **P2: hydration truncation can produce incoherent history.**
    Dropping oldest items can orphan an assistant reply from its
    user turn and can drop the milestone before later turns. Budget
    atomic stored-turn units, preserve role ordering, pin the latest
    milestone while trimming its tail, define the
    one-unit-over-budget case, and test role sequences.
    *Resolution*: adopted. Budgeting and truncation now operate on
    whole stored-turn units, the milestone is pinned as the head and
    only its tail trims, the one-unit-over-budget case is included
    alone with the reason stated, and the suite asserts role
    sequences.
15. **P2: discovery has no matching algorithm.** "Five newest" is
    the only implemented rule named, so the ambiguous-description
    test cannot distinguish matching from recency. Specify
    normalization, title and excerpt matching, relevance and
    recency ordering, no-match behavior, tie-breaking and bounds;
    test a relevant older conversation outside the five newest.
    *Resolution*: adopted. The discovery paragraph now specifies
    normalization, token-overlap scoring over title and opening
    excerpt, the (score, activity, id) ordering, the no-match
    fallback with its stated sentence, and the older-relevant-thread
    test.
16. **P2: mutable activity ordering has no pagination contract.**
    Newest-first by `last_active_at` cannot be paginated with the
    immutable row-id cursor. Define keyset ordering and cursor
    validation (activity plus id), equal-timestamp and
    changed-between-pages behavior, and the accepted duplicate or
    skip semantics under concurrent activity.
    *Resolution*: adopted. A new section defines keyset pagination
    over (`last_active_at`, `id`) descending, spelled as the two
    named parameters `cursor_active` and `cursor_id` so the
    no-opaque-cursor rule holds, with validation, the stated
    miss-on-activity semantics, and the boundary tests.
17. **P2: the CLI grammar contradicts its governing guide.** The
    cli-guide names `sessions` and `conversations` as #190's nouns
    and spells `sessions list`; the plan chooses singular without
    recording a governing decision, and never settles the agent
    filter, signatures, pagination, output columns or null-title
    rendering.
    *Resolution*: adopted for everything unspecified, declined on
    the plural with reasons recorded in the new "two CLI nouns"
    section: the guide's naming rule (singular when a noun addresses
    one entry) outranks its pre-plan illustrative spellings, every
    merged noun is singular, and the guide is amended in the landing
    milestone to record the resolution rather than being silently
    overruled. Signatures, filters, limits, columns, block shapes
    and null-title rendering are now spelled out.
18. **P2: CLI tests omit terminal-control safety for conversation
    content.** Titles and dialogue are printed by design, and the
    guide requires that no server answer can steer a terminal.
    Specify deterministic bounded rendering (control characters,
    ANSI, tabs, newlines) and test terminal and redirected output
    byte for byte.
    *Resolution*: adopted. A new section routes every printed
    content value through the merged `printable` bounding with tabs
    and newlines substituted in cells and blocks, forbids
    terminal-dependent rendering in these commands, and names the
    byte-for-byte tests on both streams.
19. **P2: thread retention unnecessarily retains expired events.**
    Rule 3 keeps an old session's events whenever live-thread turns
    reference the session, though events are session-scoped
    telemetry not needed for the projection. Preserve only the
    minimal session spine and prune expired events independently.
    *Resolution*: adopted. Rule 2 now prunes events by session age
    alone, whether or not the row survives; rule 3 keeps only the
    spine row while turns need it, and the crossing-cutoff test
    asserts the events are gone.
20. **P2: new hot query paths have no supporting indexes.** Listing
    and discovery by agent and activity, retention by
    `last_active_at`, hydration by conversation and latest-milestone
    lookup name no indexes while existing paths are explicitly
    indexed. Name the concrete indexes in the migration and cover
    them.
    *Resolution*: adopted. The baseline section now names the five
    indexes (unique join key, agent-activity listing, activity scan,
    conversation-turn walk, latest milestone) and the store-suite
    assertion that the baseline created them.

## Plan review round, delta

A delta re-review of the amended plan (969ca1df): codex CLI 0.149.1,
model gpt-5.6-terra, read-only, 2026-08-28, runtime 4m56s, scoped to
whether the twenty amendments resolve their findings without
introducing new contradictions. Verdict as received: **not ready**.
Five P1 and two P2 findings, condensed but faithful; resolutions
appended as the amendments land.

1. **P1: the milestone table is scheduled in two incompatible
   milestones.** The baseline creates `conversation_milestones` in
   milestone 1 while the module layout still schedules its schema
   for milestone 5, though milestone 5 ships no migration. Say the
   table and its metadata land in milestone 1, dormant but
   documented, and milestone 5 adds only behavior.
   *Resolution*: adopted. The module layout now puts the table, its
   metadata and its documentation in the milestone-1 baseline,
   dormant, with milestone 5 adding behavior only.
2. **P1: the incomplete latch cannot cover a failed first turn.** A
   conversation row materializes only with its first turn, so a
   failed first durable batch leaves no row for the standalone
   `incomplete = true` transaction to update, and creating one would
   violate the no-empty-thread rule. Apply a pending incomplete id
   to the first later successful row-and-turn transaction;
   standalone latch writes apply only to materialized rows.
   *Resolution*: adopted. The latch paragraph now distinguishes the
   two states: standalone flag writes only for materialized rows, a
   failed first turn kept as a pending id that the first later
   successful turn materializes with `incomplete` already true, and
   the all-writes-failed thread honestly leaving no row.
3. **P1: milestone deletion does not erase transitive recap
   content.** A recap consumes the latest milestone plus later
   turns, but records only the raw turns it read, so deleting a turn
   represented only through the parent milestone deletes the parent
   and leaves the descendant that transitively contains its content.
   Record parent lineage or inherited coverage, delete descendants
   of erased content, and add the transitive sentinel test.
   *Resolution*: adopted. The milestone row gains `parent` (the
   consumed milestone's id, recorded at recap time), deletion
   removes descendants along that lineage wherever a covering
   milestone dies, and the transitive sentinel test is in the
   milestone-5 list.
4. **P1: the dead-conversation endpoint test is assigned before its
   endpoint exists.** Milestone 2 tests dead-id behavior through the
   real endpoint while `DELETE /api/conversations/{conversation}`
   lands in milestone 3. Move one or the other.
   *Resolution*: adopted. The dead-id contract and its
   endpoint-driven tests move to milestone 3 with the endpoint;
   milestone 2's list keeps the session-deletion and purge cases.
5. **P1: the plan declines the settled API-backed purge
   requirement.** The issue amendment makes the session-record
   purge, whose retired command supported `--session`, `--device`
   and `--before`, an authenticated endpoint with a remote CLI
   caller; the plan replaced it with one-session deletes and told
   operators to script the rest. Retain the settled selection
   semantics or obtain an issue amendment.
   *Resolution*: adopted. The deletion section is recut as named
   deletion plus the purge endpoint with the settled `--device` and
   `--before` selectors (AND-combined, one transaction, counts
   answered, cascades applied to the set) and the `session purge`
   CLI verb, sharing the `threads.py` helpers with retention so the
   absorbed-not-rivaled rule holds.
6. **P2: `ConversationsPruned.conversations` has conflicting
   milestone ownership.** Retention says milestone 1, the events
   section says milestone 2. Put the field, reference and tests in
   milestone 1 consistently.
   *Resolution*: adopted. The events section now says milestone 1,
   matching the retention section and the milestone-1 checklist.
7. **P2: the plural-noun decline relies on reasons that do not
   hold.** The guide presents `vinga sessions list` as settled
   grammar and `conversations` and `events` are existing plural
   command groups, so "every merged noun is singular" is false.
   Singular may still be chosen for nouns that support `show` and
   `delete`, but as an explicit revision to the guide, not as a
   consequence of an already-singular grammar.
   *Resolution*: adopted. The two-nouns section is rewritten as an
   explicit guide revision: it names the guide's current plural
   presentation and the two merged plural groups, grounds singular
   in the naming rule the new nouns actually meet, explains why the
   entry-less plural groups keep their names, and names the three
   guide edits the landing milestone makes.

## Plan review round, confirmation

A confirmation pass over the delta amendments (bcdf059d): codex CLI
0.149.1, model gpt-5.6-terra, read-only, 2026-08-28, runtime 3m40s.
Verdict as received: **ready after the listed amendments**. Two
findings, both applied in the same change that records this round:

1. **P1: conversation deletion still appears in milestone 2.** The
   module layout assigned it to m2 and the m2 tests still named the
   deleted-conversation acknowledgement case.
   *Resolution*: adopted. The layout now assigns session deletion
   and the purge to milestone 2 and conversation deletion with the
   dead-id rule to milestone 3, and the milestone-2 test list keeps
   only the session cases.
2. **P1: the purge still dropped the settled `--session`
   selector.**
   *Resolution*: adopted. The purge endpoint and `session purge`
   carry all three settled selectors, AND-combined per the #282
   record; the named `DELETE /api/sessions/{session}` remains as
   the address form, doing the same thing through the same helper,
   with the deliberate overlap stated.
