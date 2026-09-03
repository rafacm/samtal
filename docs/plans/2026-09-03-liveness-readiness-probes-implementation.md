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
