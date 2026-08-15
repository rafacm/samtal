# JSON log events are the observability surface and transcript store

**Status:** Accepted (recorded 2026-08-04, backfilling a decision made
in M7); the transcript-store role is superseded by
[2026-08-15-content-and-telemetry-are-separate-surfaces.md](2026-08-15-content-and-telemetry-are-separate-surfaces.md)
when the conversation store of #120 lands

## Context

M7 added structured logging: `server.log_format` (`json` is the
container image's default) and conversation events that carry structured
fields alongside their human sentence (`event`, `session`, `device`,
plus what the event holds). The changelog states the intent: retained
JSON logs filtered on `heard`/`replied`/`agent_said` and grouped by
session read back as transcripts, standing in for a conversation store
until v3. The server exposes no metrics endpoint.

This was then proven in the field. The operator brief in
[#22](https://github.com/rafacm/samtal/issues/22) measured the entire
pipeline from the logs alone: per-stage ASR latency, language detection
confidence, and before/after validation of config changes, across real
conversations, with no access to the code.

## Decision

Until v3 brings a conversation store and real telemetry, the structured
log events are the server's observability interface and its transcript
store. They are a public surface, not incidental logging.

## Consequences

- Event names, fields, and semantics are a compatibility surface.
  Renaming `heard` or moving a field breaks operator tooling and the
  transcript store, so such changes are breaking changes that belong in
  the changelog like any other.
- A gap in the events is an observability gap. `replied` fires at the
  last Opus frame, so time-to-first-audio is not measurable; #22 asks
  for a `speaking_started` event at the first frame.
- Semantic drift is a bug, not a nuance. The `heard` event's
  `duration_s` ceasing to mean "how long the user spoke" is part of the
  harm tracked in [#14](https://github.com/rafacm/samtal/issues/14),
  precisely because the logs are the transcript store.
- New pipeline stages should emit an event at every boundary a
  user-facing second can hide behind, at the moment the stage starts
  and ends, so the next operator brief can measure them without
  guessing.
