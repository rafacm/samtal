# Separate liveness and readiness probes

Plan for [#318](https://github.com/rafacm/vinga/issues/318).
Implementation notes land in the companion
`2026-09-03-liveness-readiness-probes-implementation.md`, one section
per milestone, appended in the change that ticks the milestone here.

## Goal

The server answers one health question today, `/healthz`, and it is
the wrong single answer for the two things a supervisor wants to
know. "Is this process alive enough to leave running" and "may this
process be handed a new device conversation" diverge exactly when it
matters: during a drain, a healthy process is finishing its
conversations while refusing new ones. This plan keeps `/healthz` as
the liveness answer, byte for byte what it is now, and adds `/readyz`
as the admission answer, backed by the one flag that already decides
admission. The container image, the compose file and the operator
documentation then each name which probe they use and why.

## The issue's decisions, restated

- Liveness and readiness are separate endpoints with documented
  semantics: liveness says the process runs and serves its control
  surface, readiness says it may admit a new device conversation.
- Readiness is false during startup until the server can admit a
  session, becomes false before or atomically with the transition
  into draining, and stays false while new sessions are refused.
- Liveness stays true while an otherwise healthy process drains its
  existing sessions.
- Recoverable provider and MCP reachability failures stay visible
  through diagnostics and do not fail process readiness.
- The container healthcheck and the compose file explicitly choose
  the appropriate probe for their purpose.
- Tests cover startup, normal serving, recoverable dependency
  failure, the drain transition, and shutdown.
- Operator documentation says which probe an orchestrator points at
  restart and which at traffic admission.

## Where the facts already live

The admission decision has one home. `SessionRegistry.try_add`
(`registry.py`) refuses when `self._draining` is true or the session
count is at capacity, and `SessionRegistry.drain` sets `_draining`
as its first statement, before any grace period runs. The registry
already exposes the flag as the public `draining` property.
`DrainingServer` (`serving.py`) is the thing that calls `drain` on
SIGTERM, and `/healthz` (`app.py`) answers status, version and
revision with no knowledge of any of it. The composition, and
through it the registry, is on `app.state.composition` from the
moment the lifespan finishes building.

Two facts about the surrounding machinery bound what this plan has
to build:

- Uvicorn binds its listener only after the lifespan's startup has
  completed, and the lifespan is what builds the composition (a
  provider loading a model can hold it for minutes). During startup
  there is no listener, so any probe gets a connection failure,
  which every prober treats as not ready. The starting branch below
  is therefore for apps described but never built (a `TestClient`
  outside its context manager, an external ASGI runner with the
  lifespan protocol off), not a state a deployed prober normally
  observes; the documentation says so rather than leaving it to be
  rediscovered.
- The OpenAPI drift check (`tests/unit/test_api_contract.py`)
  compares the committed document against the configuration API
  alone, and `/healthz` is deliberately not in its exclusion set
  because the document carries no such operation. `/readyz` sits
  beside `/healthz` on the server application, outside the mounted
  API, so the committed document and the exclusion set both stay
  untouched.

## Open questions, resolved

**Names: keep `/healthz`, add `/readyz`.** The pair is the
convention every orchestrator documents, and `/healthz` has
consumers that must not move: the image `HEALTHCHECK`
(`Dockerfile:158`), the smoke lane (`tests/smoke`), the compose
comments, and the version-skew procedure in
`docs/reference/cli.md`, which tells an operator to curl it. Renaming
the existing endpoint would buy a tidier noun at the price of a
breaking change to every one of them; adding a sibling costs
nothing. `/healthz`'s body stays byte-identical, and the existing
pins in `tests/unit/test_health.py` staying green untouched is the
proof.

**Readiness reads the registry's `draining` flag, nothing else.**
The handler answers ready exactly when `app.state.composition` is
present and `composition.sessions.draining` is false. This is the
atomicity the issue asks for, got by locality rather than by
ordering: readiness and admission read the same flag from the same
home, so they cannot disagree. The transition into draining is
`SessionRegistry.drain`'s first statement, so readiness is false
before the first session is asked to finish and stays false for the
rest of the process's life (the registry never clears the flag).
The window between the SIGTERM arriving and the scheduled drain
task's first statement running is a window in which sessions are
still admitted, and readiness truthfully still says so; the
criterion is "before or atomically with the transition into
draining", and the transition is that statement.

**A full server stays ready.** `try_add` also refuses at
`max_sessions`, and readiness deliberately does not reflect that.
Capacity refusal is a per-connection, instantly recoverable answer
(the refused device retries on its own), and a readiness that
flapped on a session count would make an orchestrator pull the pod,
and with it the configuration API, out of traffic because
conversations are going well. The issue's criteria enumerate
lifecycle states (startup, serving, drain, shutdown) and dependency
failures, not capacity, and the documented semantics say
"may admit" means "is not draining and is past startup" so the
choice is visible rather than implied.

**The response is a closed set with the decision at the read.**
`/readyz` answers `200 {"status": "ok"}` when ready and
`503 {"status": "starting"}` or `503 {"status": "draining"}` when
not. Three literals, chosen at the one site that classifies (the
handler reading composition presence, then the flag), no message
text, no provider names, no dependency detail; there is nothing in
the body a probe log could leak. `starting` versus `draining` is
worth distinguishing because they are read at opposite ends of a
process's life by an operator debugging opposite problems.

**Provider and MCP state stay out of readiness by construction.**
The handler consults composition presence and one registry flag,
so a provider that stops answering or an MCP server that drops off
the network cannot flip readiness: the code never asks them. Their
diagnostics stay where they already live: `doctor` for the
device-facing path, the configuration API's status read for the
managers, and the event stream for per-decision reasons. The test
below proves the non-coupling positively rather than leaving it as
an absence.

**The image `HEALTHCHECK` stays on `/healthz`, said out loud.**
Docker has one health slot and it is not a restart trigger: an
unhealthy container is surfaced and gated on (`--wait`,
`depends_on: service_healthy`), not replaced. A draining container
going unhealthy would turn every redeploy into a reported failure.
So the image keeps probing liveness, the Dockerfile comment says
that is a choice and why, and the compose `depends_on` comment
says what its gate means (the process is serving, which at boot
also means admitting, since a freshly started server is not
draining). Orchestrators with two probe slots point restart at
`/healthz` and traffic admission at `/readyz`; the README carries
that sentence for them.

## Module layout

No new module. The deletion test rejects a `probes.py`: two
handlers of a few lines each, next to the `/healthz` handler that
is already in `create_app`, would leave behind a file that forwards
to a registry flag. `/readyz` registers in `create_app` beside
`/healthz`, reads `app.state.composition` defensively
(`getattr(..., None)`, compared `is not None`, the same discipline
`DrainingServer._close_live` uses for the same reason), and reads
the public `draining` property. The admission fact keeps its one
home in the registry; the handler is the adapter that lets an
orchestrator stop having to know that `/healthz` says nothing
about admission.

## Tests

Existing assets carry most of the weight; nothing they pin is
restated.

- **Normal serving and the drain transition, end to end**:
  `tests/integration/test_app_boot.py` already boots the composed
  app and reads `/healthz`; it grows the readiness half. Ready
  while serving (`/readyz` 200, body `{"status": "ok"}`); then,
  with the composition's own registry asked to drain (the public
  `drain` coroutine, zero grace, no sessions to wait for),
  `/readyz` answers 503 `draining` while `/healthz` still answers
  200 with its unchanged body, which is the liveness-through-drain
  criterion in one assertion pair.
- **Startup**: `tests/unit/test_health.py` gains the described-app
  case: a `TestClient` outside its context manager has no
  composition, and `/readyz` answers 503 `starting`. The
  listener-not-bound half of startup is uvicorn's own documented
  behavior and is recorded in the README rather than re-proven
  against uvicorn.
- **Recoverable dependency failure**: a unit test hands the app a
  composition whose provider and MCP attributes are objects that
  fail the test on any attribute access, with a real registry
  beside them, and asserts `/readyz` answers 200. The stub is
  placed on `app.state.composition`, which is the seam the
  lifespan itself writes and the drain reads; no underscore is
  reached through.
- **The flag is the transition**: `tests/unit/test_registry.py`
  already exercises `try_add` refusing under drain; it gains the
  assertion that `draining` reads true from `drain`'s entry,
  before the grace period, if it does not already pin that.
- **Shutdown**: `tests/integration/test_drain.py` already proves
  the drain-then-exit order and stays untouched; readiness during
  shutdown is the draining state already tested above, and past
  uvicorn's exit there is no listener to probe.
- **The smoke lane**: `tests/smoke` grows one read of `/readyz`
  beside its `/healthz` read, so the image job proves the shipped
  container answers both.

## Risks

- **The unit lane must not need Postgres.** The readiness unit
  tests never run the lifespan; they use the described app and the
  stub composition. The composed-app cases live in the integration
  lane, which has the database. Mitigation is placement, verified
  by running the unit lane the way CI does.
- **Doc edits can stale the command-spellings census.** The README
  and reference edits are text, but the census sweeps every
  tracked file; `tests/unit/test_command_spellings.py` runs before
  the PR, and a stale manifest regenerates through its generator.
- **`/healthz` regressions would break consumers this plan chose
  to protect.** The existing pins in `test_health.py` are the
  guard; the plan changes nothing they assert.

## Consumers of `/healthz`, inventoried

By `grep -rn healthz` over the tracked tree, 2026-09-03: the
Dockerfile `HEALTHCHECK`, the smoke lane
(`tests/smoke/test_smoke.py`, `conftest.py`), the unit and
integration tests named above, `vinga-server/README.md` (the
exposed-paths sentence and the operations sections),
`docs/reference/cli.md` (the version-skew procedure),
`docker-compose.yml` (comments), and historical plans and feature
docs, which record what was true and are not updated. None move.

## Milestones

- [ ] **M1: readiness beside liveness.** `/readyz` on the server
  application with the closed status set above, backed by
  composition presence and the registry's `draining` property;
  the tests named above across startup, serving, dependency
  failure, drain and shutdown; the Dockerfile and compose comments
  naming their probe choice; the server README's exposed-paths
  sentence and shutdown section extended with the
  restart-versus-admission guidance; a CHANGELOG entry. Design
  footprint: deepens the health surface `create_app` already owns
  and adds no module; the admission fact keeps its one home in
  the registry, and the new handler is the adapter that spares an
  orchestrator from knowing that. Documentation footprint: the
  server README (exposed paths, the shutting-down section), the
  Dockerfile and compose comments, `CHANGELOG.md`; the root
  README makes no endpoint-level claims and the cli.md version
  procedure stays on `/healthz`, both confirmed rather than
  assumed during implementation.
