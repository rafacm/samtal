# samtal-server v1 implementation notes

**Date:** started 2026-08-02

Companion to [`2026-08-02-samtal-server-v1.md`](2026-08-02-samtal-server-v1.md).
One section per milestone, appended in the same change that ticks the plan's
milestone checklist. Records deviations from the plan, resolutions of the
plan's open questions, and discoveries worth keeping. A milestone with no
deviations says so explicitly.

## M0 Skeleton (PR #1, merged 2026-08-02)

Deviations and additions relative to the plan:

- **Dev dependency `httpx` replaced by `httpx2`.** Starlette's test client
  (which FastAPI's `TestClient` re-exports) deprecated `httpx`; the suite now
  passes with deprecation warnings escalated to errors.
- **CI actions pinned newer than assumed.** GitHub deprecated Node 20
  actions, so the workflow uses `actions/checkout@v7` and
  `astral-sh/setup-uv@v9.0.0`. Note: setup-uv publishes no floating `v9`
  major tag; the exact tag is required.
- **Small unplanned additions**: a `/healthz` endpoint (gives the skeleton a
  testable contract) and a `samtal-server` console entry point reading
  `SAMTAL_HOST`/`SAMTAL_PORT`.
- **Process work rode along** (not part of the milestone as planned):
  AGENTS.md gained the small-commits rule, the PR verification task-list
  rule, and the plan milestone checklist; the repo logo was consolidated to
  a single transparent PNG.

Verified beyond the plan's acceptance criteria: a doc-only push to main
triggers no workflow run (path scoping observed working post-merge).
