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

### The schema change is an in-place migration, and no backfill

The issue's 2026-08-21 amendment says there is no migration and no
backfill, and that pre-existing databases are deleted rather than
upgraded. That decision was taken when the store was SQLite and any
schema change meant amending the baseline; since #283, the
conversations chain has a Postgres baseline
(`1001_postgres_conversations`) and the standing promise
([product-promises](../architecture/product-promises.md#a-beta-database-is-never-left-behind))
is that in-place upgrades begin there. The substance of the
amendment, no derivation of threads from historical rows, stands
untouched. The mechanism is now cheaper honored than broken: the
change ships as one additive migration per milestone that needs one
(`1002` adds `conversations` and `turns.conversation` in milestone 1,
`1003` adds `conversation_milestones` in milestone 5), old turns keep
a null `conversation` meaning "recorded before threads existed", and
no database is stranded. The CI wheel-migration step's exact chain
assertion (`vinga-server.yml`, the `1001_postgres_conversations`
line) moves in the same change as each migration, deliberately.

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
null the column on meta turns later.

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
- **CLI nouns are singular**: `session` and `conversation`, because
  both address one entry (`show`, `delete`), which is the cli-guide's
  own rule. The guide's illustrative spelling `vinga sessions list`
  predates verbs that address one entry and updates in the milestone
  that lands the noun (the guide marks unimplemented spellings as
  owed; this plan is the named work).

### One-session deletion, not selector-driven purge

The retired `conversations purge` took `--session`, `--device` and
`--before` selectors. The replacement is `DELETE
/api/sessions/{session}` and `DELETE
/api/conversations/{conversation}`, each deleting one named thing,
row and children together, plus thread-aware retention as the
age-based path. Reasons: retention is the module that owns bulk
age-based deletion (the 2026-08-24 amendment says the purge should be
absorbed by it, not rival it); a device-scoped or date-scoped bulk
erase is a loop over the list endpoint an operator can script, and
each selector combination would triple the test surface of a
destructive endpoint; and the deletion stories in the issue (16, and
the #282 record) are about erasing a named thing. The CLI verbs are
`session delete <session>` and `conversation delete <conversation>`,
destructive per the cli-guide (confirm at a terminal, `--force`).

Deletion semantics, from the #282 record and the Postgres cutover:
deletion is one transaction over the row and its children; a session
that is still running when its row goes stops being recorded (the
writer's tombstone rule already does this); MVCC means an in-flight
repeatable-read transaction still sees the rows until it ends, which
the schema reference already states and keeps stating. Deleting a
session deletes its turns even where they belong to a live thread:
erasure outranks thread continuity, the thread honestly keeps a gap,
and the conversation row itself survives unless it loses every turn,
in which case it is deleted too rather than left as an empty shell.
Deleting a conversation deletes its turns (and milestones) out of
their sessions' timelines the same way, and never touches session
rows or events. Both deletions run over a short-lived write engine
opened per request (deletion must work when recording is off and no
store object exists, exactly as reads already do), and the writer's
tombstone checks make them safe beside a live writer.

### The durable path: turns retry bounded, and writes acknowledge

Today a failed marker transaction rolls back, counts its batch as
lost, and moves on; correct for telemetry, a silent hole for product
state. The split (decision 10) lands inside the store:

- **Class, not configuration**: `Turn` records (and milestone records
  in milestone 5) are the durable class whenever the store records;
  `Event` records stay the lossy class. No mode fork on the
  resumption switch: one write behavior to test, and a deployment
  that enables resumption later is not haunted by holes from before
  the flip.
- **Bounded retry, in place**: a marker transaction that fails with
  turns in its batch retries in place up to `TURN_WRITE_ATTEMPTS`
  (3, a named constant with its reason beside it) when the failure
  is in the transient class the db classifier already names (lock
  and serialization failures); a non-transient failure or an
  exhausted budget falls through to today's behavior: the batch is
  dropped and counted, `WriteFailed` is emitted, and the hole is
  loud in the accounting (`sessions.dropped`) rather than silent.
  Retries run on the writer thread with no sleep between attempts:
  the transient class either clears at once or is not transient, and
  the writer must not stall other sessions' batches.
- **Acknowledgement**: `ConversationStore.record_turn` (and
  `TurnStore`/`SessionTurns`) return an `Acknowledgement`, a small
  handle with `wait(timeout) -> bool` resolved by the writer when the
  turn's marker transaction commits, or resolved false when the turn
  is dropped (tombstoned session, exhausted retries, writer
  shutdown). The pipeline's ordinary path ignores the return value,
  so its never-block contract is untouched (the handle is created,
  never awaited on the audio path). The consumers are milestone 4's
  resume path, which waits bounded on the target thread's latest
  acknowledgement before hydrating so a same-session switch-back
  cannot read past its own writes, and milestone 5's recap, which
  waits for the milestone row before speaking the recap as kept.
  In milestone 2 the handle exists and nothing consumes it, which is
  dormant machinery in the #120 sense; the store suite proves its
  semantics through the gate seam.

### Thread-aware retention, exactly

The cutoff is unchanged (`retention_days`, 90 by default, 0 keeps
forever); what changes is the unit. Rules, applied in one pass in the
writer where pruning already runs:

1. A conversation whose `last_active_at` is older than the cutoff is
   pruned: its milestones, its turns' tool invocations, its turns,
   then the row.
2. A turn with a null `conversation` (recorded before threads
   existed) follows its session's age, as today.
3. A session whose `started_at` is older than the cutoff is pruned
   (row and events) only when no turns reference it any more; a
   session kept alive by a live thread keeps its row and its events,
   so the cross-reference from a resumable thread's turns to their
   sessions never dangles.

With resumption unused, threads never span sessions, thread
inactivity coincides with session age, and the pass degenerates to
today's behavior, which is what story 11 requires of the stricter
deployments. `ConversationsPruned` gains a `conversations` count
beside `sessions` (additive). Milestone 1 ships rule 1 alone (so a
release between milestones never grows conversation rows without a
pruning rule); milestone 2 ships rules 2 and 3 and the event field.

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
  id for the active agent and resets that agent's in-session history.
  Offered always: it needs no storage at all (with the store off it
  still resets context; no row was going to land anyway), which is
  story 2 working on every deployment.
- **`resume_conversation`**: `description` (free text) or
  `conversation` (a candidate id) and, from milestone 5, `start_from`
  (`recap` or `recent`). With `description`, it answers a bounded
  candidate list (`RESUME_CANDIDATES = 5`, newest first: id, title,
  last activity, opening excerpt) for the model to read aloud; the
  tool's result text instructs the model to have the user pick, and
  the follow-up call carries `conversation`, which is decision 7's
  convergence by construction. With `conversation`, the runtime
  performs the resume. When resumption is off, text storage is off,
  the store is disabled, or the id matches nothing, the tool answers
  a fixed spoken-refusal result (`is_error` false, per decision 11:
  a refusal the model voices gracefully, not an error), each refusal
  a fixed sentence naming no user input.

Execution split follows the merged precedent exactly: discovery
(`description`) executes in `BuiltinTools.dispatch` through an
injected thread-store read seam (a fourth constructor argument,
compared `is not None`), because it is a read that changes nothing;
selection (`conversation`) and `new_conversation` are intercepted in
the runtime's tool loop the way `switch_agent` is (pipeline.py:1143),
because success swaps the conversation context, which only the
runtime owns. Store reads run under the existing per-source tool
timeout through `asyncio.to_thread`, so a slow database is a timed-out
tool call, never a stalled reply.

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
  approximation. Overflow truncates oldest first (milestone 4); with
  a milestone, hydration is the milestone text plus the turns after
  it (milestone 5), and overflow beyond that still truncates oldest
  first.
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
2. Consent: `resume_conversation(conversation=X, start_from="recap")`.
   The runtime hydrates the backlog for summarization (latest
   milestone plus everything after it, under a wider internal bound,
   `RECAP_INPUT_BUDGET_TOKENS`, its own named constant), runs one
   round against the active agent's own LLM provider with a fixed
   summarization instruction, stores the result as a milestone row
   (durable path; the acknowledgement is awaited bounded before the
   tool answers), installs milestone-plus-tail as the thread context,
   and returns the recap text as the tool result with the instruction
   to read it to the user. What is stored is what was offered to be
   spoken, which is the honest form of "speaking it and storing it
   are one act" a text model can provide; the model is instructed to
   read it verbatim and the approximation is stated in the reference
   documentation rather than implied away. A summarization failure
   (provider error, timeout) falls back to the recent-tail resume
   with a result that says the recap could not be made; nothing is
   stored.
3. Decline: `resume_conversation(conversation=X, start_from="recent")`
   hydrates the tail under the ordinary budget and stores nothing.

A milestone row is `conversation_milestones`: `id` (bigint identity),
`conversation` (text), `after_turn` (bigint, the last `turns.id` the
recap covers, which is its position in the timeline), `created_at`
(text, UTC ISO-8601), `text` (text, nullable under the text switch by
the uniform rule, though the flow that creates one cannot run with
text off). Milestone-aware hydration reads the latest row and the
turns with `id > after_turn`.

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
`ConversationsPruned` gains its additive count field in milestone 2.

## Module layout

```
vinga_server/conversations/
    schema.py       + conversations table (m1), conversation_milestones (m5),
                    turns.conversation column (m1)
    records.py      + conversation id on TurnRecord; Acknowledgement (m2)
    store.py        writer learns conversation rows, titles and
                    last-activity (m1), bounded turn retries and
                    acknowledgements (m2), milestone records (m5);
                    retention rules move here too (m1, m2)
    threads.py      NEW (m2): the thread store read-and-delete surface
                    over the engine settings: session deletion,
                    conversation deletion, thread listing and detail,
                    resume candidates, thread turns for hydration,
                    milestone reads. What callers stop knowing: the
                    five-table join topology, the deletion ordering
                    that keeps children and parents together, and the
                    MVCC caveats; api.py, cli handlers and the tools
                    all ask this module instead of composing SQL.
    hydration.py    NEW (m4): stored rows to LLM context under a
                    budget. What callers stop knowing: how dialogue,
                    tool notes, gaps, milestones and truncation
                    become a message list.
    api.py          sessions rename (m1), session delete (m2),
                    conversations namespace (m3)
    cli.py          untouched (the schema command stays)
    docgen.py       new tables and the retention prose follow their
                    milestones
    migrations/     1002 (m1), 1003 (m5)
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

The deletion test, answered for the two new modules: inlining
`threads.py` into `api.py` would put the join topology and deletion
ordering in a module whose job is transport, and the tools and CLI
would grow second and third copies; inlining `hydration.py` into
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
  the briefs). The workflow's chain assertions and expected-tables
  lists move with migrations 1002 and 1003, named per milestone.

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

- **Milestone 1**: migration 1002 up on a populated database (old
  turns readable, `conversation` null); conversation row lands with
  the first turn and not at activation; per-agent minting across a
  handover (two threads, the handover turn on the first); title
  derivation (truncation boundary, text-off null title);
  `last_active_at` moves with each turn; conversations pruned at the
  cutoff; the renamed API routes round-trip with pins moved and the
  route inventory updated; `conversation` on turn responses;
  events carry `conversation` beside `agent` (the event-assertion
  suites extend); both generated documents byte-green; the sentinel
  planted as an utterance shows up in the title and nowhere else.
- **Milestone 2**: acknowledgement resolves on commit (gate seam);
  resolves false on tombstone, exhausted retries and shutdown;
  transient-failure retry proven with a raising-then-working engine;
  non-transient failure keeps today's counting; thread-aware
  retention: a session past the cutoff whose turns belong to a
  live thread keeps its row, turns and events; a thread past the
  cutoff dies whole; null-conversation turns follow session age;
  DELETE session round trip (row and children gone, live thread
  keeps its other turns, empty conversation shells deleted);
  running-session deletion ends its recording (existing tombstone
  test extended over HTTP); 401 without the token; CLI `session
  list|show|delete` through the client seam, confirmation and
  `--force`, fixed refusal sentences, sentinel absent from stderr;
  spellings manifest green.
- **Milestone 3**: conversations list newest-first with agent filter
  and pagination edges (empty, one page, boundary, cursor past the
  end); detail; dialogue turns oldest-first with tool rows nested;
  DELETE conversation (turns leave their sessions' timelines,
  sessions and events untouched); CLI `conversation list|show|delete`;
  OpenAPI and spellings green.
- **Milestone 4**: boot refusals for both contradictory combinations,
  by fixed sentence and json pointer; hydrator input-to-output
  (budget edges, truncation order, tool-note rendering, text-off
  gaps counted, deterministic output); tools: candidates bounded and
  newest first, ambiguous description, selection by id, refusals
  when off (spoken result, not error), `new_conversation` resets
  context with and without a store; runtime: the clean switch (the
  incoming agent's provider never receives the outgoing agent's
  words, asserted on `RecordingLlm`), switch-back continuity, resume
  installs hydrated history and rebinds, read-your-writes through
  the acknowledgement on a same-session switch-back,
  `conversation_resumed` emitted; integration: two real sessions
  over websockets, resume by description in the second, the thread
  continues (the lane's one new end-to-end case).
- **Milestone 5**: migration 1003; over-budget resume offers instead
  of swapping; consent stores the milestone durably, installs
  milestone-plus-tail, and the stored text equals the tool result;
  decline stores nothing and resumes the tail; summarization failure
  falls back with nothing stored; milestone-aware hydration
  (latest milestone plus `id > after_turn`); milestones die with
  their thread (retention and delete); the API detail exposes
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
  over HTTP. Conversation deletion adds a conversation-level
  tombstone check to the writer: a turn for a conversation whose row
  is gone materializes a fresh row only if its session still exists
  and the turn is genuinely new; the review round should press here.
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

- [ ] **Sessions are sessions, conversations exist** (branch
  `feature/first-class-conversations`): migration 1002; the
  `TurnRecord` conversation id; minting at `_activate_agent` per
  session and agent; the writer's conversation rows, titles and
  `last_active_at`; the minimal conversation pruning rule; the API
  rename with moved pins and the regenerated OpenAPI document;
  `conversation` on turn responses and on the agent-bearing events;
  the workflow's conversations-chain assertion moved; generated
  references regenerated; concepts.md and glossary.md updated where
  the entity stops being direction ("decided direction (issue #190)"
  markers on what this milestone lands); changelog breaking entry
  for the API rename. Design footprint: deepens `schema.py`,
  `store.py`, `records.py` and the activation seam; no new module.
  Documentation footprint: `docs/reference/conversations-schema.md`,
  `docs/reference/api-openapi.json`, `docs/reference/events.md`
  (all generated), `docs/concepts.md`, `docs/glossary.md`,
  `CHANGELOG.md`.
- [ ] **Durable turns, thread-aware retention, session erasure**
  (branch `feature/conversations-m2`): `Acknowledgement` and the
  bounded transient retry; retention rules 2 and 3 and the
  `ConversationsPruned` field; `conversations/threads.py` with
  session deletion; `DELETE /api/sessions/{session}`; the `session`
  CLI noun (`list`, `show`, `delete`); the spellings manifest;
  cli-guide's owed spelling updated. Design footprint: deepens
  `store.py` (durability as an internal property); adds
  `threads.py` (callers stop knowing join topology and deletion
  ordering). Documentation footprint:
  `docs/reference/conversations-schema.md` (retention prose via its
  generator), `docs/reference/api-openapi.json`,
  `docs/reference/cli.md` (generated half),
  `docs/architecture/cli-guide.md`,
  `docs/architecture/observability-surfaces.md` (the store row's
  erasure sentence and the open-items row), `CHANGELOG.md`.
- [ ] **Threads readable** (branch `feature/conversations-m3`): the
  conversations API namespace (list, detail, dialogue turns, delete);
  the `conversation` CLI noun (`list`, `show`, `delete`); thread
  listing, detail and deletion in `threads.py`. Design footprint:
  deepens `threads.py` and `api.py`; no new module. Documentation
  footprint: `docs/reference/api-openapi.json`,
  `docs/reference/cli.md`, the spellings manifest, `CHANGELOG.md`.
- [ ] **Resumption** (branch `feature/conversations-m4`): the
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
- [ ] **Recap milestones** (branch `feature/conversations-m5`):
  migration 1003; the recap offer, consent and decline flow; the
  summarization round with its bounds and fallback; milestone-aware
  hydration; milestones on the API detail; `milestone_recorded`;
  the workflow's chain assertion moved again. Design footprint:
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
