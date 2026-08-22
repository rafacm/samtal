# Cut the CI critical path with parallel lanes and shrunk harness waits

## Goal

Implement issue #233 as rescoped on 2026-08-22: the two deterministic
quick wins that cut the CI `test` path from about 12.5 minutes toward
7, leaving the pytest-xdist work to #254. Milestone 1 splits the one
sequential `test` job into two parallel jobs, touching only the
workflow file. Milestone 2 shrinks the real waits inside the event
baseline harness, whose module-scoped `capture()` fixture is the
largest single item in the unit lane at 87 seconds. Every minute cut
lands on the 30 to 40 CI runs the rest of the #246 batch is projected
to make, so this pays back within the first issue that follows it.

The companion implementation doc,
[`2026-08-22-ci-quick-wins-implementation.md`](2026-08-22-ci-quick-wins-implementation.md),
records what each milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

1. **Two parallel jobs instead of sequential steps.** The 4m34s
   integration lane runs entirely under the 7m46s unit lane; the
   doc-drift checks and the wheel migration (seconds) ride with the
   integration job. `--durations=25` is added to both pytest lanes in
   the same change so CI keeps reporting the slow tail.
2. **The baseline harness's timers get faked or shrunk, not
   parallelized around.** The floor under every run, serial or
   parallel, and it lowers what #254 has to distribute.
3. **Out of scope**: parallelizing the integration lane, and
   pytest-xdist (extracted to #254 with its hazards).

## The measurements this plan stands on

Per-driver timing of the harness (2026-08-22, this machine): 86.4s
total over 81 drivers. The skew is four drivers: the three
`FillerRunner._fire` drivers at 20.02s each and the
`PipelineRuntime._watchdog_stream` driver at 10.07s; the remaining 77
drivers sum to about 16s. Both slow numbers are one mechanism: the
production default `llm_first_token_timeout_s = 10.0`
(`config/models.py`). The three filler drivers hold a reply from
`StallingLlm([STALL_S])` (`STALL_S = 30.0`,
`tests/support/providers.py`) and their 20.02s is two consecutive
watchdog windows, a 10s timeout, one retry, a second 10s timeout,
after which the round is given up; `masked_config()` does not
override the server section, so the default applies. The 10.07s
watchdog driver is `drive_llm_retry`'s second scenario, which runs
against `unregistered(...)`, and `unregistered()` hardcodes
`base_config()`, so the same 10s default applies there while the
scenario's first half already runs at `watchdog_config()`'s 0.05s.
The M2 fix for that driver is to let `unregistered()` take the
config its caller wants and pass `watchdog_config()`, bringing both
scenarios to 0.05s.

CI job timings (run 32583062338): unit 7m46s, integration 4m34s,
lint/mypy/doc-drift/wheel seconds; critical path 12m45s.

## Design decisions this plan makes

1. **Job layout.** `unit`: checkout, uv, lint, mypy, unit tests.
   `integration`: checkout, uv, integration tests, the four doc-drift
   checks, the wheel migration. `image` changes `needs: test` to
   `needs: [unit, integration]`, so publishing still waits for the
   whole suite. Nothing pins the old job name: the repository has no
   branch protection requiring a check named `test` (verified via the
   API), and the workflow itself is the only reader. M1 also updates
   the prose that explains what it changes: the comment block above
   `image` that names `needs: test` as the publishing gate, and the
   workflow description in `AGENTS.md`, checked for the same
   staleness in the same commit.
2. **The waits shrink by configuration, in the harness only, the
   way the sibling suite already does it.**
   `tests/unit/test_session_filler.py` drives these same three
   scenarios with a local `STALL_S = 0.5` and, where it needs the
   bound, `masked_config(delay_ms=..., server=
   {"llm_first_token_timeout_s": ...})`; the seam exists and no new
   parameter on `tests/support/configs.py` is needed. The three
   filler drivers take a harness-local 0.5s stall and leave the
   bound alone: their kept records are filtered to `filler_*`, so
   the only ordering that matters there is filler delay (60ms) well
   under the stall, and with a 0.5s stall the reply simply succeeds
   instead of being given up. `drive_llm_retry` is the opposite
   case: the watchdog only fires when the stall EXCEEDS the bound,
   so that driver keeps a stall above the shrunk 0.05s bound it
   gets by passing `watchdog_config()` through the `unregistered()`
   parameter of measurement-paragraph fame. The invariants are
   stated per driver, never globally, because the global form
   ("stall well under the bound") would stop `drive_llm_retry`
   firing at all. The shared `STALL_S` and production code do not
   change.
3. **What the committed baseline pins, said precisely.** `shape()`
   records channel, level, template, argument TYPES, sorted field
   KEYS, and the event name, and `driven()` records how many
   records each path produced; no timing value appears. So the byte
   comparison catches a shrink that stops a path firing (or fires
   it twice), which is the real risk here, but it cannot see a
   driver that reaches its event by a different route: a filler
   record looks the same whether the stalled reply was given up or
   succeeded after the filler played. M2 therefore states, per
   shrunk driver, what must still be true of the scenario (the
   filler fires before the reply's first audio; the retry driver's
   watchdog times out twice) and the implementation doc records
   that each was checked by reading the driver against the runtime
   code, not inferred from an empty diff.
4. **Margins against slow runners.** CI runners are slower than this
   machine, so the shrunk values keep at least a 5x ratio between
   each ordering pair (delay vs stall vs bound) and no shrunk value
   goes below 50ms. The three drivers' internal sleeps
   (`DELAY_MS / 1000 / 3` style) already scale with the config they
   read and need no change of shape.

## The standing review lenses, pre-answered

- **No-leak**: no observability surface changes; the workflow edit
  and harness constants touch no message or field.
- **Pin before reshaping**: the committed event baseline is the pin,
  it records no timing, and both its suite and a regeneration
  double-run must pass unchanged. The reference docs are untouched.
- **Closed sets, honest seams**: not this plan's territory; the one
  seam question (a bound with no config seam) is named in decision 2.
- **Inventories by tooling**: the per-driver timing script lands
  in M2 as `tests/tools/driver_times.py`, because it is the only
  way to see inside the module-scoped fixture (`--durations`
  reports one 87s setup line and nothing per driver), which is a
  second responsibility the durations report cannot carry. The M2
  section of the implementation doc records the before and after
  per-driver tables from it.

## Module layout

- `.github/workflows/vinga-server.yml`: the `test` job becomes
  `unit` and `integration`; `image.needs` updates; `--durations=25`
  on both pytest invocations. No other file in M1 but
  `CHANGELOG.md`: each milestone lands its own dated `### Changed`
  entry (M1: the test job splits into parallel lanes with
  durations reporting; M2: the baseline harness's slow drivers
  stop waiting out real provider timeouts), matching the
  repository's precedent for CI-only and test-only entries.
- `tests/tools/event_baseline.py`: the harness-local 0.5s stall
  for the three filler drivers, and `unregistered()` gaining a
  config parameter so `drive_llm_retry` passes
  `watchdog_config()`. No production module changes, and no change
  to `tests/support/configs.py`: the `masked_config(server=...)`
  seam already exists.

## Tests

- M1 is verified by its own PR's CI run: two jobs visible, both
  green, critical path roughly the unit job's duration, durations
  tables present in both logs.
- M2: the baseline suite (`tests/unit/test_event_baseline.py`)
  passes with the committed file byte-identical; regeneration
  double-run leaves the tree clean; the per-driver timing script
  shows the four named drivers each under one second and
  `capture()` setup around 17 seconds (the ~16s tail over the
  other 77 drivers is real work this milestone does not chase; the
  scope call is recorded here, and the issue's "single digits"
  verification box is corrected to match when M2 lands); the full
  unit suite passes and its wall time drops by roughly 70 seconds.

## Risks and mitigations

- **A shrunk wait races on a loaded CI runner.** Mitigated by the
  5x ratio floor and the 50ms minimum in decision 4; if a filler
  driver still proves flaky in CI, its stall alone is raised while
  keeping the others, and the number is recorded in the
  implementation doc.
- **The workflow split silently drops a step.** The step inventory
  is fixed: checkout, setup-uv, `uv sync --frozen`, lint, mypy,
  unit, integration, four doc-drift checks, wheel migration, with
  the first three duplicated into both jobs by design. The M1 PR
  description lists where each landed,
  and the two job logs are checked against that list before the
  review round.
- **`image` publishes with half the suite green.** Prevented by
  `needs: [unit, integration]`, exercised BEFORE merge with the
  `workflow_dispatch` run the workflow's own header prescribes for
  exactly this kind of edit, and confirmed by reading the first
  push-to-main run after merge.

## Milestones

- [x] **[M1: split the test job into parallel unit and integration
  jobs](2026-08-22-ci-quick-wins-implementation.md#m1-split-the-test-job-into-parallel-unit-and-integration-jobs).**
  (PR TBD) Workflow-only; `--durations=25` in both lanes;
  `image` needs both. Deepens nothing and moves nothing but YAML;
  `main` stays releasable because every step still runs, just in
  two lanes.
- [ ] **M2: shrink the four slow drivers' waits in the baseline
  harness.** (PR TBD) Harness-local stall values and the
  `unregistered()` config parameter per decisions 2 and 4;
  committed baseline byte-still; the four drivers each under one
  second and capture() setup around 17 seconds.

## Plan review round

External review of commit `dc3910e1`, 2026-08-22. Backend: claude
CLI 2.1.239, model `claude-opus-5`, read-only tool set (the interim
fallback tier while the codex quota is exhausted). Verdict as
received: ready after the P1 and P2 amendments; findings 1, 2 and 4
collapse into one correction, and finding 3 is a scope call for the
issue owner. Findings condensed but faithful:

1. **P1: the watchdog driver's 10s does not come from
   `watchdog_config`.** That config already sets 0.05s; the 10s is
   `drive_llm_retry`'s second scenario running against
   `unregistered(...)`, which hardcodes `base_config()` and so takes
   the production default `llm_first_token_timeout_s = 10.0`. The
   fix is to let `unregistered()` take a config and have the driver
   pass `watchdog_config()`, matching the scenario's first half.
   *Resolution* (`47604408`): the measurements paragraph now names
   the production default reached through `unregistered()` and the
   fix (a config parameter, `watchdog_config()` passed).

2. **P1: the filler drivers' 20s is two consecutive
   `llm_first_token_timeout_s` windows (10s, one retry), the same
   mechanism as finding 1, and the plan's global ordering invariant
   ("the stall well under the bound") would stop `drive_llm_retry`
   firing at all.** The invariant must be stated per driver: for
   `drive_llm_retry` the stall stays above the shrunk bound; for the
   three filler drivers only filler delay < stall matters.
   *Resolution* (`a20d0cee`): decision 2 states the invariants per
   driver; the filler drivers keep the bound and shrink only the
   stall, `drive_llm_retry` keeps its stall above the shrunk bound.

3. **P1: "capture() setup in single digits" is unreachable from the
   four named drivers.** 86.4s minus 70.1s leaves a ~16s tail over
   77 drivers, so the floor after M2 is ~17s and the saving ~70s,
   not 80. Either restate the acceptance or extend scope.
   *Resolution* (`58e439f7`): the ~17s floor is accepted; M2's
   acceptance restated (four drivers under 1s, capture around 17s,
   ~70s saved) and the issue's verification box gets corrected when
   M2 lands. The tail is left to #254's distribution.

4. **P2: the "no config seam" contingency is already answered.**
   `masked_config(server=...)` forwards a server section and
   `test_session_filler.py` already drives the same scenarios with a
   local 0.5s stall and `llm_first_token_timeout_s` overrides; the
   plan's shrunk-bound design is also the slower alternative (about
   9s across the filler drivers versus about 1.5s).
   *Resolution* (`4c852952`): the sibling suite's approach is
   adopted, the `configs.py` contingency and no-seam branch dropped.

5. **P2: decision 3's byte-stillness claim is self-defeating.** If
   the bytes cannot move by construction, the byte comparison proves
   nothing; what the file actually pins is shape and per-path record
   counts, which catches a driver that stops firing but not one that
   reaches its event by a different route. State per driver what
   must still be true of the scenario, checked by reading.
   *Resolution* (`b93d0d1a`): decision 3 now says what the file
   pins (shapes, per-path counts) and adds the per-driver scenario
   truths checked by reading.

6. **P2: the M1 step inventory drops `Install dependencies`** (and
   checkout and setup-uv); the control for "silently drops a step"
   was itself incomplete.
   *Resolution* (`8aba2ab0`): the inventory now lists all ten
   steps with the first three duplicated by design.

7. **P2: no CHANGELOG entry is named**, and the repository has
   precedent for CI-only and test-only entries.
   *Resolution* (`90afac7f`): per-milestone dated Changed entries
   named in the module layout.

8. **P3: the `image` job's `needs: test` comment would go stale**,
   and `AGENTS.md`'s workflow description deserves the same check.
   *Resolution* (`f9e4f26a`): the image comment and AGENTS.md are
   updated in the same commit as the `needs:` change.

9. **P3: M2's verification rests on an uncommitted timing script**
   that `--durations` cannot replace (it cannot see inside the
   module-scoped fixture). Commit it or record the exact command and
   tables.
   *Resolution* (`e560662e`): the script lands as
   `tests/tools/driver_times.py` in M2 with the second
   responsibility stated.

10. **P3: `image.needs` can be exercised before merge** via the
    `workflow_dispatch` path the workflow's own comment prescribes,
    not only by reading the first push-to-main run.
    *Resolution* (`9e194acf`): the workflow_dispatch run happens on
    the branch before merge, with the post-merge read kept as
    confirmation.
