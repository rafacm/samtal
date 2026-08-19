# Observability and conversation-data surfaces

The reference behind
[the 2026-08-15 ADR](../adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md):
which needs the design balances, the four surfaces and what each may
carry, and the external practice it was checked against. The ADR holds
the decision; this page holds the reasoning and the map, and changes
when the design does.

## The needs

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

## The four surfaces

| Surface | Carries | Serves | Retention and access |
| --- | --- | --- | --- |
| **Structured events** (`vinga_server/events.py`, the JSON log) | Metadata only: closed field sets, reason tokens from closed sets, trusted identifiers, counts, durations. No conversation text, no far-side bytes, no exception prose. | 1, feedstock for 5 | Operator log retention (weeks); the no-leak contract holds by construction once #155 lands |
| **Conversation store** (#120, `conversations.db`) | Content as system of record: turns, tool and MCP calls with arguments and results, keyed by session and user | 2, 5 (evals), 7 | Configured retention with a stated default; per-session and per-user deletion; access-controlled reads under `/api` |
| **Capture** (existing) | Raw audio plus decision track, explicit opt-in | 1 (deep diagnosis) | Short-lived, pruned, already governed |
| **Audit** (future, small) | Admin/config actions, auth refusals, reload invocations | 4 | Long, append-only, narrow content |

Live views (need 6) are fed from the event tap #138 built (a
WebSocket/SSE subscriber is one more `EventTap` consumer); the store
answers history. Same events, two transports, no polling.

## The external practice it was checked against

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

## Where each piece lands

- The decision: the
  [2026-08-15 ADR](../adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md).
- The store, its content path, tool results, retention, deletion, and
  the consent question: #120 (revision 2026-08-15).
- The registry that makes the event tier's contract structural: #155.
- Exporters and the audit surface: #66/#67 and a future audit issue,
  both consuming the same tap and vocabulary.
