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
  which every prober treats as not ready. The no-composition branch
  below is therefore for apps described but never built (a
  `TestClient` outside its context manager, an external ASGI runner
  with the lifespan protocol off) and for the moments after
  teardown has begun, not a state a deployed prober normally
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

**Admission latches closed in `handle_exit`, and readiness reads
the latch.** The registry gains one synchronous, idempotent
operation, `stop_admitting()`, which sets the same flag `try_add`
consults; `drain()` calls it as its own first statement, and
`DrainingServer.handle_exit` calls it before anything else on every
path where a composition exists: before scheduling the drain task,
before passing a second signal or a `drain_s <= 0` shutdown through
to uvicorn, and before giving up for want of a running loop.
Setting one bool under the GIL is safe from a signal context, and
idempotence is what makes calling it on every path free. Readiness
and admission still read one flag from one home, so they cannot
disagree; what the latch adds is that the flag turns the moment
shutdown begins rather than when the scheduled drain task first
runs, closing the two paths the plan's first cut left open: the
window between the signal and the task's first statement, and the
zero-drain configuration, which never calls `drain()` at all. The
registry never clears the flag, so readiness stays false for the
rest of the process's life.

**A full server is not ready, and one classifier says so.** The
issue's settled definition is "may the process admit a new device
conversation", and a process at `max_sessions` may not, so
readiness reflects capacity as well as lifecycle. The registry,
which owns both facts, gains a public `admission` property
answering one literal from a closed set: `admitting`, `draining`,
or `full`, with `draining` winning when both hold because it is the
terminal one. `try_add` refuses exactly when `admission` is not
`admitting`, so the predicate has one home and the probe cannot
disagree with the door. Readiness at capacity dips and recovers as
slots free; the README says so, and says why it is correct rather
than a flap: a refused device retries on its own, and an
orchestrator withholding new traffic from a full pod is what
readiness is for. The cost is documented beside it: under an
orchestrator that routes by readiness, a full pod's configuration
API leaves the traffic set too, which is worth knowing when sizing
`max_sessions`.

**The response is a closed set, classified where each fact lives.**
`/readyz` answers `200 {"status": "ok"}` when ready and 503 with
`{"status": "unavailable"}`, `{"status": "draining"}` or
`{"status": "full"}` when not. Two decision sites, each owning its
fact: the handler classifies composition presence, and the
registry's `admission` property classifies its own three states,
which the handler maps one to one (`admitting` to `ok`). Literals
only, no message text, no provider names, no dependency detail;
there is nothing in the body a probe log could leak.
`unavailable` means there is no serving composition, which is true
before the lifespan has built one and again after teardown has
released it; one literal for the one observable fact, rather than
a `starting` that would lie at the far end of the process's life.

**The composition attribute is scoped to the lifespan that built
it.** `_build_composition` assigns `app.state.composition` and
nothing today ever clears it, so a served-then-torn-down app would
answer ready with its resources already closed. The build registers
the clearing on its own exit stack, after every other registration,
so the unwind (last in, first out) clears the attribute before any
resource is released: no request can read a composition whose parts
are already closing. This is the same discipline the 2026-08-18
lifespan work applied to the API's installed runtime state, now
applied to the attribute the drain and the probes read.

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
- **The latch, through the real shutdown seam**:
  `tests/unit/test_drain.py` grows cases that drive
  `DrainingServer.handle_exit` itself, with a composition stub
  carrying a real registry on the app's state: on the normal path,
  admission is refused and readiness reads non-admitting the moment
  the call returns, before the loop has run the scheduled drain
  task; on a `drain_s = 0` server and on a second signal, the same
  holds even though `drain()` never runs or is already running.
  `tests/unit/test_registry.py` pins `stop_admitting` as
  idempotent and `drain` as latching through it first.
- **Startup, and the whole lifecycle on one app**:
  `tests/unit/test_health.py` gains the described-app case: a
  `TestClient` outside its context manager has no composition, and
  `/readyz` answers 503 `unavailable`. The integration lane drives
  the same application through all three phases: `unavailable`
  before the lifespan is entered, `ok` while serving, and
  `unavailable` again after the lifespan has exited, never `ok`
  with resources released. The listener-not-bound half of startup
  is uvicorn's own documented behavior and is recorded in the
  README rather than re-proven against uvicorn.
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
- **Capacity**: filled to `max_sessions` through `try_add`, the
  registry answers `admission == "full"` and `/readyz` answers 503
  `full`; after `remove` frees a slot, both read ready again. A
  draining registry at capacity answers `draining`, pinning the
  precedence.
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

## Plan review round

Backend codex (codex-cli 0.153.0), model `gpt-5.6-sol`, sandbox
read-only, 2026-09-03, against commit `9299f2a9`; the reviewer ran
8m39s. Verdict: ready after the P1/P2 amendments; the reviewer
endorsed the no-new-module layout explicitly.

1. **P1: shutdown begins before the admission flag changes.** The
   plan defines the transition as `SessionRegistry.drain`'s first
   statement, but `DrainingServer.handle_exit` sets its own
   `_draining` and only schedules the registry drain; with
   `drain_s <= 0` it never calls it, and a second signal can enter
   uvicorn shutdown before the scheduled task runs. During those
   paths `/readyz` would stay `ok` and `try_add` would keep
   admitting after shutdown has begun, and the plan's tests call
   `registry.drain()` directly so they bypass the defect. The plan
   should add a synchronous, idempotent registry operation that
   latches admission closed, call it in `handle_exit` before any
   scheduling or delegation on every path, have `drain()` reuse it,
   and test through the real `handle_exit` seam.

   *Resolution*: accepted in full. The registry gains
   `stop_admitting()`, synchronous and idempotent; `drain()` calls
   it first, and `handle_exit` calls it before anything else on
   every path with a composition, including `drain_s <= 0` and the
   second signal. The readiness section now describes the latch,
   and the test plan drives `DrainingServer.handle_exit` itself
   with a real registry behind a composition stub, plus registry
   pins for idempotence and for `drain` latching through it.

2. **P1: "a full server stays ready" contradicts the settled
   readiness meaning.** The issue's settled definition is "may the
   process admit a new device conversation", and a process at
   `max_sessions` may not. Redefining "may admit" as "past startup
   and not draining" is an issue change the plan cannot make. The
   plan should derive readiness from a public registry admission
   predicate covering both lifecycle and capacity, have `try_add`
   use the same predicate, add a closed literal for the capacity
   state, and test readiness false at the cap and true again when a
   slot frees.

   *Resolution*: accepted in full. The registry gains a public
   `admission` property answering `admitting`, `draining` or `full`
   (draining wins), `try_add` refuses exactly when it is not
   `admitting`, and `/readyz` maps the three one to one, adding
   `full` to the closed status set. The capacity section now argues
   for the dip-and-recover behavior instead of against it, names
   the documented cost, and the test plan pins the cap, the
   recovery, and the draining-over-full precedence.

3. **P2: composition presence is not an honest lifespan-state
   seam.** `_build_composition` assigns `app.state.composition` and
   nothing ever clears it, so a previously served app would report
   ready after teardown, with its resources already closed; the
   fresh-`TestClient` startup test cannot catch that. The 2026-08-18
   lifespan record already treats clearing installed runtime state
   after teardown as necessary. The plan should scope the attribute
   to the active lifespan, clearing it before resource unwinding,
   and test the same application before entry, during serving, and
   after exit, with the final state a closed non-ready status,
   never `200 ok`.

   *Resolution*: accepted in full. The build registers the clearing
   of `app.state.composition` on its own exit stack after every
   other registration, so the LIFO unwind clears it before any
   resource is released. The `starting` literal is renamed
   `unavailable`, one literal for the one observable fact (no
   serving composition, before build or after teardown), and the
   test plan drives one application through all three phases.

4. **P2: the `/healthz` consumer inventory is incomplete.** The
   inventory omits live consumers in
   `.github/workflows/vinga-server.yml`, `tests/smoke/serve.sh`,
   `tests/integration/test_cli_live.py`,
   `tests/integration/test_config_api.py`,
   `tests/integration/test_smoke_seeds.py` and
   `tests/unit/test_config_api.py`, plus explanatory text in
   `docs/conversational-quality-regression-suite.md`, and the new
   unauthenticated route stales the route-count comment at
   `app.py` lines 995 to 998. The plan should record the complete
   `git grep -n healthz` inventory, separate historical mentions
   from live consumers, classify every live use as liveness,
   readiness or revision inspection, move readiness waiters to
   `/readyz`, and name every affected comment and assertion in M1.
