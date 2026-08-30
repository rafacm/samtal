# Observability and conversation-data surfaces

Where may this datum go? Five surfaces answer it, and this page is the
map of them: what each carries, which need it serves, how long it is
kept and who may read it, and what is true of it in the code today.
[The 2026-08-15 ADR](../adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md)
holds the decision itself and is not restated here; the evidence that
decision was taken on is dated at the foot of this page. The map
changes when the design does.

## On this page

- [The five surfaces](#the-five-surfaces): the table this page exists
  for, one row per surface, with its current status.
- [The invariants](#the-invariants): the four rules that decide where
  a new field goes, and that hold whatever the surfaces grow into.
- [Where each piece lands](#where-each-piece-lands): which document
  says what, and what is still open.
- [Decision evidence, gathered
  2026-08-15](#decision-evidence-gathered-2026-08-15): the seven needs
  and the external practice the design was checked against, as they
  were written on the day the decision was taken.

## The five surfaces

The Serves column numbers the needs in
[the appendix](#the-needs). The Carries column says what class of
thing a surface may hold, never the vocabulary itself: the exact
fields and columns are generated from the declarations and are linked
in the row.

| Surface | Carries | Serves | Retention and access | Status |
| --- | --- | --- | --- | --- |
| **Structured events** (`vinga_server/events/`, the JSON log) | Metadata only: closed field sets, reason tokens from closed sets, trusted identifiers, counts, durations. No conversation text, no far-side bytes, no exception prose. Every variant is in [`reference/events.md`](../reference/events.md) | 1, feedstock for 5 | Operator log retention (weeks) | **Landed.** Every emission is a typed variant declared in `events/catalog.py`, so a shape that is not declared cannot be constructed at all and the no-leak contract holds by construction rather than by review. Landed twice over since #342: the same emissions are also readable live over `GET /api/runtime/events`, under the same bearer token, which is a second transport over this surface and not a fifth surface. It keeps nothing, it carries the catalogued fields of an event plus a wall-clock stamp and the level's name, which is this row's vocabulary and no more of it than the log already writes, and it is therefore held to this row's no-content contract in full |
| **Conversation store** (the `record` schema) | Content as the system of record: the session spine, the turn timeline, the tool invocations a turn issued and the decision track under them, and the conversation each turn belongs to, which is a durable thread with exactly one agent and may span sessions. Beside them sit the recap checkpoints a thread accrues: one lands where a user consented to a recap of a conversation too long to resume whole, holding what the agent said out loud and the range of turns it read, and every later resume of that thread is rebuilt from it. Every column is in [`reference/conversations-schema.md`](../reference/conversations-schema.md). Audio never enters it | 2, 5 (evals), 7 | `server.conversations.retention_days`, 90 by default, measured against a thread's last activity rather than a session's age: a conversation past the window is deleted whole with its turns and their children, a session's telemetry goes by that session's own age whether or not the session outlives it, and a session record goes once no turn names it any more. On-demand erasure under `/api`, on either entity (`DELETE /api/sessions/{session}`, the selector purge `DELETE /api/sessions`, and `DELETE /api/conversations/{conversation}`, with `vinga session delete`, `vinga session purge` and `vinga conversation delete` in front of them); access-controlled reads under `/api`, one namespace per entity; live read-only SQL as `vinga_ro`, which reaches this schema and not `domain` | **Landed** (#120, threads #190), off unless `server.conversations.enabled` says otherwise, under two independent switches (`metrics`, `text`). Erasing either entity by name landed with #190's read verbs, and erasure outranks what the store derived: erasing a session takes its turns wherever their conversation is, a title recomputes or is nulled, and a conversation left with no turns is deleted whole; erasing a conversation takes its turns out of whatever sessions they were spoken in and leaves those sessions and their telemetry standing, with a gap in them. Neither ever comes back: a thread a deletion took is refused by the writer rather than recreated by a turn still on its way to it |
| **Capture** (`vinga_server/capture.py`) | Raw audio plus the decision track, three files per session sharing one timeline | 1 (deep diagnosis) | Bounded per session and by a total budget for the directory, oldest captures pruned first | **Landed**, and off unless `server.capture.enabled` is true. The flag is the switch rather than the section, so a field round can stop recording without losing the directory and the budgets. It writes room audio to disk, which is the opposite of what the rest of the project promises, so a server that boots with it on says so once at startup, at WARNING (`capture_enabled`); a session that is being recorded then says which path it is writing to (`capture_started`) |
| **Memory** (the `memory` schema) | Content as what an agent is told to keep, and the only surface here whose content is read back INTO a prompt. Three scopes: an agent's own facts about the person it talks to, a device's notes about the place and the household, shared by every agent bound to that board, and one conversation's ledger of what is currently true in it. Beside the active rows sits a held area: a fact an agent was asked to forget is kept until the conversation that forgot it ends, so the undo it exists for can reach it. Audio never enters it, and neither does a transcript: what lands is what a model chose to store through a tool | 2, 7 | **Facts until they are corrected**, capped per scope and pruned oldest-first at write, with no clock on them: an agent's memory is not telemetry and does not age out. **State and held facts until their thread ends**, which is the conversation record's own retention: a thread's erasure and its retention prune take both in the same transaction as its turns, and a boot sweep heals what no transaction covered. **The operator API is the deletion door**, scope-addressed under `/api/memory` with `vinga memory list`, `vinga memory set` and `vinga memory delete` in front of it: every listing shows orphaned owners, which is what a rename and a replaced board leave, and every deletion through it is a hard delete. No read-only SQL: `vinga_ro` is granted nothing on this schema, so the API is the surface | **Landed** (#314, scopes and editing #83), and unconditional: the schema is migrated at every boot, because an empty table is not a memory. Storage never leaves the deployment's own database; as prompt content it follows the active LLM provider's egress exactly as the transcript and the persona do, which is what `server.local_only` is the guard for, and a device note therefore reaches every sibling agent's provider on that board |
| **Audit** | Admin and config actions, auth refusals, reload invocations | 4 | Long, append-only, narrow content | **Future.** Nothing writes one today and no issue owns it yet |

## The invariants

Four rules decide where a new field goes. They are the ADR's, restated
as the questions a placement has to answer.

- **What a person said never rides the events.** Metadata on the log,
  content in the store. The line the
  [2026-08-17 amendment](../adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md#amendment-device-descriptors-are-metadata-2026-08-17)
  draws is between the two: what a device says about itself at
  check-in may ride the events once its decision site bounds and
  sanitizes it, and what a person said through the device may not,
  however it was recovered.
- **Restriction is at the source, not at the sink.** A field is
  lawful because it was declared, not because a scrubber failed to
  match it. On the events that is the catalog; on the store it is the
  schema and the two switches.
- **Live and history are two transports over one set of events, not
  two stores.** The admin UI's "what is happening now" is one more
  `EventTap` consumer of the tap #138 built, and the store is a tap
  too. Same events, two transports, no polling. Landed as
  `GET /api/runtime/events` (#342): the hub is that tap, attached to
  both channels at composition, and what it hands a reader is the
  record the log retains and nothing more. A live view keeping its own
  copy would be the retention question answered twice, which is why it
  keeps none: it ends with the reader, and with the server.
- **Every surface answers the retention question.** How long, who can
  see it, can it be deleted: a surface with no policy retains
  forever, which is why the store ships a default window rather than
  a later feature, and why the capture directory has a budget.

## Where each piece lands

- The decision, and why it was taken:
  [the 2026-08-15 ADR](../adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md),
  with its 2026-08-17 amendment on device descriptors.
- The exact vocabulary of the landed surfaces:
  [`reference/events.md`](../reference/events.md) and
  [`reference/conversations-schema.md`](../reference/conversations-schema.md),
  both generated from the declarations and diffed by CI, and, for
  memory, [`reference/api-openapi.json`](../reference/api-openapi.json)
  and [`reference/cli.md`](../reference/cli.md), which are the two
  documents that earn it: nobody but the server reads memory's raw
  tables, so what is published is the addressed surface rather than the
  columns behind it. Nothing on this page repeats a field name or a
  column name, so none of them can go stale here.
- Still open, each with its owner: exporters over
  the same tap and vocabulary (#66/#67), the audit
  surface (no issue yet), and the household-consent question #120 named
  and did not close.

## Decision evidence, gathered 2026-08-15

What follows was written on 2026-08-15, when the decision was taken,
and is kept as it was: the seven needs the design balances and the
external practice it was checked against. It is evidence about a
decision, not current guidance, and the map above is what a placement
is held to.

### The needs

Seven, gathered in the 2026-08-15 assessment:

1. **Operational diagnosis.** Enough technical record per session and
   conversation to analyze the steps and fix issues: stage timings,
   provider failures, barge-in decisions, MCP lifecycle.
2. **User transparency.** Enough per-conversation record to show a
   user what happened on their behalf: what was heard, what was
   answered, which MCP service was called and what it returned.
3. **Privacy, retention, and consent.** A household device that hears
   a room, with children as future users: every surface answers "how
   long is this kept, who can see it, can it be deleted", and content
   capture is consent-shaped, never ambient.
4. **Audit.** Admin and config actions, auth refusals, reload
   invocations: a narrow, long-lived, append-only record distinct
   from diagnostics.
5. **Metrics, evaluation, and budgets.** Aggregable usage: latency
   percentiles per stage, token counts per agent and user (the
   product vision's budgets cannot exist without usage records),
   field-test quality signals.
6. **Live view versus history.** "What is happening right now" (the
   admin UI) and "what happened" are different transports over the
   same events, not two stores.
7. **Per-user scoping.** Once family users arrive, a conversation
   record is attributable and access is a policy question the data
   model must make answerable.

Needs 2 and 3 pull against each other, and the resolution is the
design's core: transparency is served from an access-controlled store,
never from logs, so the log surface can hold the no-leak line without
starving the UI.

### The external practice it was checked against

Collected 2026-08-15 (a tavily research pass plus targeted
verification; links below):

- **OpenTelemetry GenAI semantic conventions.** Metadata attributes
  (`gen_ai.operation.name`, `gen_ai.request.model`,
  `gen_ai.usage.input_tokens`, ...) are emitted unconditionally;
  message content is a separate, explicitly opt-in event stream
  (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`, default
  `no_content`), correlated to spans by id rather than riding them.
  vinga adopts the vocabulary for its usage fields so exporters
  (#66/#67) and budget accounting consume events without mapping.
- **Store-separated LLM observability stacks** (Langfuse, LangSmith,
  Arize Phoenix). Conversation traces live in a purpose-built store
  with masking hooks, deletion APIs, and access control; application
  logs stay diagnostic. Self-hosted caution taken from Langfuse OSS:
  without a configured policy it retains indefinitely, so #120 ships
  retention as configuration with a stated default.
- **Source-side restriction over sink-side scrubbing.** The security
  guidance is unanimous that allowlisted, schema-restricted emission
  is the guarantee and pattern-scrubbing at the sink at most a net;
  #155 (the event registry) is that guarantee made mechanical.
- **Evidence gaps the sources left open**, owned by vinga's own
  design: household/family consent workflows (named open question in
  #120), audit-trail field standards (future issue), and live
  transport choice (decided by the admin UI work, not by this page).

References: OTel GenAI semantic conventions and instrumentation
(opentelemetry.io/blog/2024/otel-generative-ai,
github.com/open-telemetry/semantic-conventions-genai), content-capture
gating (docs.litellm.ai/docs/observability/opentelemetry_v2), sensitive
data handling (opentelemetry.io/docs/security/handling-sensitive-data),
GDPR-shaped pipelines
(oneuptime.com/blog/post/2026-02-06-opentelemetry-pipeline-gdpr-compliant/view),
PII leakage analysis
(systemshardening.com/articles/observability/otel-pii-leakage), Langfuse
self-hosted masking, retention and deletion
(langfuse.com/self-hosting/security/data-masking,
langfuse.com/docs/administration/data-retention,
langfuse.com/docs/administration/data-deletion), store-separation
surveys (arize.com/blog/the-role-of-opentelemetry-in-llm-observability,
patronus.ai/llm-testing/llm-observability).
