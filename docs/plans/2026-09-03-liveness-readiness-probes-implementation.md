# Separate liveness and readiness probes: implementation

Companion to
[`2026-09-03-liveness-readiness-probes.md`](2026-09-03-liveness-readiness-probes.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: readiness beside liveness

### What was done

`registry.py`. A module-level `Admission` literal type
(`admitting`, `draining`, `full`), a public `admission` property
classifying the two facts this object already owned, and a synchronous
`stop_admitting()` that latches the same flag `draining` reads.
`try_add` now refuses exactly when `admission` is not `admitting`, so
the predicate has one home, and `drain` shuts the door through
`stop_admitting()` as its first statement rather than assigning the flag
itself.

`serving.py`. `DrainingServer.handle_exit` calls a new
`_stop_admitting()` before anything else, which is what makes it apply
to every path out: the normal one, `drain_s <= 0`, a second signal, and
the one where there is no running loop to schedule on. The composition
is read defensively, exactly as `_close_live` reads it, and the whole
operation is one bool set under the GIL, which is the safety argument
for calling it from a signal handler.

`app.py`. `/readyz` registers in `create_app` beside `/healthz`,
returning `200 {"status": "ok"}` or 503 with `unavailable`, `draining`
or `full`. Composition presence is classified in the handler
(`getattr`, compared `is not None`); the registry's `admission` is
mapped one to one. `/healthz` is untouched. `_build_composition` now
assigns `app.state.composition` after every other exit-stack
registration and registers `delattr` for it in the same breath, so the
LIFO unwind clears the attribute before any resource is released. The
route-count comment above the `FastAPI(...)` call names the two probes.

Tests. `tests/unit/test_health.py` gained a module docstring saying
which half of the cases live there, an `Unreachable` stand-in that fails
the test on any attribute access, a `serving()` helper that puts a
composition on `app.state.composition` directly, and four cases:
dependencies that stopped answering do not fail readiness, a described
app answers `unavailable`, a full server answers `full` and is ready
again when a slot frees, and a draining full server answers `draining`.
`tests/unit/test_registry.py` gained the classifier pins (full and back,
draining over full), `stop_admitting` idempotence, and a drain whose
first session records `registry.admission` when it is asked to stop, so
the latch is pinned as happening before the wait.
`tests/unit/test_drain.py` gained three cases driving
`DrainingServer.handle_exit` (normal path before the loop runs the drain
task, `drain_s = 0`, and no running loop) and one assertion on the
existing second-signal case; its three scripted registries now share a
`ScriptedRegistry` base carrying the door the shutdown latches.
`tests/integration/test_app_boot.py` gained ready-while-serving, the
drain transition with `/healthz` unchanged beside it (the registry's
public `drain` coroutine run on the serving loop through the test
client's own portal), and one application read before, during and after
its lifespan. `tests/smoke/test_smoke.py` gained one `/readyz` read.

Waiters moved to `/readyz`, per the plan's inventory:
`tests/smoke/serve.sh`, `tests/smoke/conftest.py`,
`tests/integration/test_smoke_seeds.py`,
`tests/integration/test_cli_live.py`, and the workflow comment that
describes the first of them. Everything the inventory classifies as
liveness or revision inspection stayed put, and no site turned out to
want something other than what the plan predicted.

Documents. The server README's two exposed-paths sentences, its smoke
lane paragraph, and its "Shutting down drains" section (which probe to
point at restart and which at traffic admission, the four statuses as a
table, why the capacity dip is correct and what it costs, why
`unavailable` is narrower than it sounds, and why the image healthcheck
stays on liveness); the Dockerfile `HEALTHCHECK` comment; the compose
`depends_on` comment; the exclusion-set commentary in
`tests/unit/test_api_contract.py`; `CHANGELOG.md` under the existing
`## 2026-09-03` heading, one `### Added` entry for the probe and three
`### Changed` ones; and the census manifest, regenerated through its
generator after each set of edits that moved a quoted line.

Confirmed rather than assumed, as the plan asks: the root `README.md`
makes no endpoint-level claim at all (`git grep` finds no `healthz` in
it), and the version-skew procedure in `docs/reference/cli.md` is a
revision read and stays on `/healthz`. `config.example.yaml` did not
change: there is no schema change. Nothing generated changed except
`vinga-server/tests/unit/command-spellings.txt`, which was regenerated
with `uv run python -m tests.unit.test_command_spellings` and never by
hand; `docs/reference/api-openapi.json` is untouched, which is the
plan's claim that a route on the server application is outside the
configuration API's document.

The two claims in that paragraph did not survive the review round below,
and are corrected here rather than left standing. `server.ota_path` now
refuses the two probe paths (finding 4), which is a schema behavior
change even though no key moved; `config.example.yaml` still does not
change, because its `ota_path` comment claims no path is allowed and
names neither of the reservations that already existed.
`docs/reference/events.md` changed too, through its own generator, for
the new rejection variant (finding 5).

### Deviations from the plan

None in substance. Two decisions the plan left to implementation:

**The latch is one private helper called once, rather than four calls.**
The plan asks for `stop_admitting()` before anything else on every
`handle_exit` path with a composition. A single `self._stop_admitting()`
as the method's first statement is that, and it reads the composition
defensively itself, so the paths do not each repeat the guard. The
no-composition path calls it too and it is a no-op there, which is what
"where a composition exists" means from inside.

**The composition assignment moved down beside its clearing.** The plan
says to register the clearing after every other registration. The
assignment moved with it, so the attribute is installed and its removal
registered in one breath; leaving the assignment where it was would have
left a window in which a failure in `mcp_servers.start_all()` unwound
the stack with the attribute still set.

### Discoveries

**Two existing tests were reading a composition after teardown, and one
of them was serving a device request with it.**
`test_doctor.py::test_a_current_server_answers_both_spellings_with_no_redirect`
ran its `doctor` probe after its `TestClient` context had exited, so the
OTA reply it asserted on was composed from a closed database and closed
providers; it now probes inside the `with`, which is what "a current
server" means.
`test_conversations_api.py::test_a_store_that_records_nothing_today_still_serves_what_it_recorded`
asserted on `app.state.composition.conversations` after teardown, and
that read moved inside too. Both are evidence for the plan review's
finding 3 rather than casualties of it: the attribute really was
outliving what it described.

**The unit lane needs a Postgres on the port the model defaults to.**
Tests that compose a `Config` in Python get `DatabaseConfig()`'s
defaults, which are 127.0.0.1:5432 and are not the `VINGA_DB_PORT` the
conftest's provisioning honors, so a development instance started on
another port leaves twenty-five unit tests failing to connect while the
rest of the lane passes. Worth knowing before concluding that a change
broke something.

**A `/readyz` waiter keeps waiting through a 503.**
`urllib.request.urlopen` raises `HTTPError`, a subclass of `URLError`,
so the smoke fixture's existing `except` already treats "answered, not
ready" the same way it treats "did not answer", which is the behavior a
readiness wait wants. Nothing had to change for it.

### Verification

Run from `vinga-server/`, against the repository's compose Postgres
started under a project name of this session's own.

- [x] `uv run ruff check .`: passed.
- [x] `uv run pytest tests/unit -q`: 4954 passed, 19 skipped, 1 failed.
  The failure was `test_command_spellings.py::test_the_manifest_is_the_census`,
  stale because the CHANGELOG edit had moved the lines it quotes; the
  manifest was regenerated through its generator and the lane is green,
  as the CI-shaped run below shows.
- [x] `uv run pytest tests/unit -q -n auto --dist loadfile`: 4955
  passed, 19 skipped, run after the regeneration.
- [x] `uv run pytest tests/integration -q`: 236 passed.
- [ ] The smoke lane and the image build: CI's, not run here.

### PR review round

External review of the branch as pushed to PR #373, at `9b800ab3`
against `origin/main`: backend codex (codex-cli 0.153.0), model
gpt-5.6-sol, 2026-09-03, runtime 9m52s. Sol rather than the fast tier
because the diff changes what a supervisor reads and when a device is
turned away. Five findings, three P1 and two P2, verdict as received:
mergeable after the listed fixes. All five were confirmed against the
sources before being fixed, three of them by running the defect; none
rejected.

Two of the three P1s are the same shape, and it is worth naming: **a
decision and the act it authorizes were separated by something that can
run in between.** In `try_add` it was a signal handler between two
bytecodes; in `handle_exit` it was a whole lifespan between the signal
and the registry it would go on to publish. The latch M1 added closed
the window the plan named and left both of these, because both are
windows the plan did not name.

1. **P1: the trailing-slash redirect quotes the request back.** The
   server application keeps FastAPI's slash redirects, so
   `GET /readyz/?token=x` answers 307 with the query and the Host copied
   into `Location`. The repository already turned redirects off in
   `config/api.py` for exactly this leak. Fix: answer the trailing-slash
   spelling without a redirect, treat both probes the same way, and add
   a sentinel test over every surface.

   *Resolution* (`326ed303`): confirmed by running it, `/healthz/`
   included, which had leaked since that endpoint existed. Both probes
   now register both spellings through `ota.spellings`, the helper the
   device-facing routes already answer their two spellings with, so
   there is no `Location` to carry anything. Scoped to the probes rather
   than turning `redirect_slashes` off for the whole application: the
   websocket path has one spelling and is not this milestone's to
   change. The sentinel test asks both spellings of both probes with a
   credential-shaped query value and hunts it in the headers, the body,
   the log in each rendering a deployment keeps, and the live stream.

2. **P1: admission was a check and then an act.** `try_add` classified
   and then mutated, and `stop_admitting` runs in a signal handler, so a
   SIGTERM landing between the two admitted a conversation to a process
   already shutting down. Fix: make admission linearizable against the
   latch, with a deterministic test that fires the latch in the window.

   *Resolution* (`0eb6feda`): confirmed by reading and then by test. The
   operation is `admit` now: it reads the flag again after the insertion
   and gives back the slot it took when the signal won, keeping a slot
   an earlier call took, because a conversation admitted before the
   shutdown is one the drain has to reach. It answers the classifier's
   own word rather than a boolean, which is what finding 5 needed. The
   test occupies the window through a test-local subclass whose
   classification fires the latch, and fails with the recheck removed.

3. **P1: a signal during a build was forgotten.** `handle_exit` passed a
   signal straight to uvicorn when there was no composition, and uvicorn
   runs the lifespan's startup first, binds its listener the moment it
   returns, and only then notices it was told to stop. A build a
   provider held for minutes therefore published a fresh admitting
   registry after the shutdown had begun. Fix: persist the intent where
   both sides meet and apply it at publication.

   *Resolution* (`c91a7479`): confirmed in uvicorn's own `startup` and
   `_serve`, which bind before the `should_exit` check. The application
   gained `stop_admitting(app)`: it records the intent on the seed the
   build reads and shuts a composition that is already serving, and
   `_build_composition` applies the intent in the same step as the
   publication, with no await between them, so no interleaving is left
   open. `DrainingServer` lost its own helper. The test signals the
   server before entering the lifespan and reads the published
   registry; it fails with the application half removed.

4. **P2: `ota_path` accepted a probe path.** The probes register before
   the OTA router, so an endpoint configured at `/readyz/` or
   `/healthz/` would never be reached, and after finding 1 both
   spellings answer the probe. Fix: reserve them in the validator, with
   refusal tests, and correct the implementation doc's no-schema-change
   claim.

   *Resolution* (`a1ff404a`): confirmed. The two paths are constants in
   `config/models.py`, `app.py` registers the routes from them, and the
   validator refuses them beside the API mount and the onboarding
   prefix, naming the rule and not the value the way those two do. The
   claim above is corrected in place. `config.example.yaml` needs no
   edit: its `ota_path` comment claims no path is allowed and names
   neither existing reservation either.

5. **P2: a drain's refusals were reported as capacity.** `ws.py` emitted
   `RejectedAtCapacity` for both refusals, so every rolling restart read
   as a load problem at exactly the moment `/readyz` said `draining`.
   Fix: emit a distinct variant, regenerate the event reference through
   its generator, and follow the trails a new variant has to update.

   *Resolution* (`12b12c2f`): confirmed. `RejectedWhileDraining` joins
   the `session_rejected` declaration with a `draining` token in the
   rejection set and a sentence naming the shutdown, and the endpoint
   reports the word `admit` returned rather than guessing. The trails,
   followed rather than edited by hand: `docs/reference/events.md`
   regenerated with `uv run vinga-server events reference`, a driver in
   `tests/tools/event_baseline.py` that produces the new variant (the
   catalog is what makes the driver suite exhaustive, so a variant no
   driver produces fails), and the baseline's carried-shape map and path
   count. A handshake refused after `stop_admitting` is pinned to the
   new reason and sentence.

### Verification after the review round

Run from `vinga-server/`, against a compose Postgres of this session's
own, torn down afterwards.

- [x] `uv run ruff check .`: passed.
- [x] `uv run mypy`: passed (the typed events package, which the new
  variant is in).
- [x] `uv run pytest tests/unit -q`: 4964 passed, 19 skipped.
- [x] `uv run pytest tests/unit -q -n auto --dist loadfile`: 4964
  passed, 19 skipped.
- [x] `uv run pytest tests/integration -q`: 236 passed.
- [x] `python3 scripts/check_doc_links.py .`: 178 files, 0 failures.
- [x] `uv run pytest tests/unit/test_command_spellings.py -q`: passed,
  the manifest regenerated through its generator after the document
  edits.
- [ ] The smoke lane and the image build: CI's, not run here.
