# Conversation content and telemetry are separate surfaces

**Status:** Accepted (recorded 2026-08-15; the log-surface narrowing
took effect on 2026-08-16, when the conversation store of
[#120](https://github.com/rafacm/samtal/issues/120) landed with it)

## Context

The 2026-08-04 record made the structured JSON log events both the
observability surface and the transcript store, standing in for a
conversation store until one existed. That double duty has a measured
cost. The retained log is governed by the no-leak contract (no secret
or far-side bytes on any kept surface), and a surface that must carry
conversation-adjacent detail keeps colliding with that contract: of
the roughly thirty findings across the external review rounds of the
2026-08-14 refactoring batch (PRs #147 through #154), nineteen were
leak-shaped content on the retained log, each found, fixed, and
re-reviewed by hand.

The industry pattern, verified against the OpenTelemetry GenAI
semantic conventions and the self-hosted LLM observability stacks
(sources collected in the 2026-08-15 research pass, kept in
[../architecture/observability-surfaces.md](../architecture/observability-surfaces.md)): message content and telemetry are different data
classes. The OTel GenAI instrumentations emit metadata attributes
(model, provider, token counts, durations) unconditionally and gate
content behind an explicit opt-in
(`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`, default
`no_content`), carrying content as separate events correlated by
trace id rather than as span attributes. The trace/conversation
stores (Langfuse and peers) hold content as the system of record with
their own masking hooks, deletion APIs, and access control, while
application logs stay diagnostic. Redaction guidance across the
sources is unanimous that source-side restriction (closed schemas,
allowlists) is the guarantee and sink-side scrubbing at most a net.
The needs map, the tier table, and the sources live in
[../architecture/observability-surfaces.md](../architecture/observability-surfaces.md).

Meanwhile #138 built the machinery this decision needs: one emitter
(`samtal_server/events.py`), closed reason-token sets, pin suites over
every emit path, an AST guard, and a consumer tap the store attaches
to.

## Decision

samtal keeps four surfaces, each with its own content class,
retention, and access model:

1. **Structured events** (the log): metadata only. Closed field sets,
   reason tokens from closed sets, identifiers, counts, durations.
   Event field names adopt the OTel GenAI vocabulary where one exists
   (`gen_ai.usage.input_tokens` and kin adapted to the existing field
   style), so exporters (#66/#67) and cost accounting consume them
   without mapping. No conversation text, no far-side bytes, no
   exception message text.
2. **The conversation store** (#120): the system of record for
   content. Turns, tool and MCP calls and their results as
   first-class records, keyed by session and user, with per-user
   access scoping, retention policy, and deletion built in from the
   start.
3. **Capture**: the explicit opt-in rich channel (raw audio plus
   decision track), short-lived, already governed.
4. **Audit**: admin and config actions, auth refusals, reload
   invocations; narrow content, long retention, append-only
   expectations.

The transcript-store role of the JSON logs ended when #120's store
landed, which is what the 2026-08-04 record's follow-up note records.
Events remain a compatibility surface exactly as before;
this record changes what may ride on them, not how they are
versioned. Live views (the admin UI's "what is happening now") are
fed from the event tap, not from a store.

## Consequences

- The no-leak contract on the events becomes enforceable by
  construction: with no free-text fields, a leak is a schema
  violation rather than a review finding. A follow-up issue
  (schema-declared events) turns the convention into machinery.
- `heard`, `replied`, and `agent_said` lost their text fields when
  the store landed; that is a breaking change to the event surface,
  belongs in the changelog like any other, and is the point.
- The store inherits the obligations the logs carried implicitly:
  retention tiers, right-to-delete, per-user scoping, and the
  household-consent question, which #120's plan must resolve.
- Operator tooling that greps transcripts out of logs migrates to
  the store's query surface; the #22-style latency briefs keep
  working unchanged, since they read metadata the events keep.
- Self-hosting caution, learned from Langfuse's OSS tier: a store
  with no retention policy retains indefinitely by default. #120
  ships retention as configuration with a stated default, not as a
  later feature.
