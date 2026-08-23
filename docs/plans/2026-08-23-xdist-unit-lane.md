# Run the unit test lane under pytest-xdist

## Goal

Implement issue #254, inserted into the #246 queue ahead of the
config phase (maintainer decision, 2026-08-22, recorded on the
issue with the verification spike's results). The CI unit lane runs
under `pytest-xdist` with `-n 4 --dist loadfile`, taking it from
about 6.5 minutes to a projected 2 to 2.5 and the critical path
from ~7 minutes to roughly the integration lane's ~4.5. The spike
(issue comment, 2026-08-22) already measured 3.6x at CI-like
`-n 4` on a 14-core machine with all four configurations green and
none of the issue's hazards biting.

The companion implementation doc,
[`2026-08-23-xdist-unit-lane-implementation.md`](2026-08-23-xdist-unit-lane-implementation.md),
records what the milestone actually did, deviations from this plan,
and discoveries; no deviations says so explicitly.

## The issue's decisions, restated

1. **`-n auto --dist loadfile` for the unit lane in CI.** The
   runner has 4 cores, so `auto` and `4` coincide there; the flag
   is spelled `auto` so a future larger runner is used whole.
   Local runs keep their serial default; nothing forces xdist on a
   developer.
2. **The integration lane stays serial** (out of scope in #233 and
   here).
3. **The known hazards are addressed before the switch**, and the
   statistical criterion (green across several consecutive CI runs)
   is an ongoing watch, not a pre-merge gate: this PR's own runs
   start the count, the remaining #246 issues' runs continue it,
   and a checkbox stays open on issue #254 until a week of runs
   has passed, per the issue's own verification list. A flaky
   parallel lane is reverted by removing two tokens from one line,
   which is the cheap-revert property that licenses merging on the
   first green rather than after the week.

## Design decisions this plan makes

1. **The dependency is a dev dependency.** `uv add --dev
   pytest-xdist` (lockfile moves with it); the CI unit step becomes
   `uv run pytest tests/unit -q --durations=25 -n auto --dist
   loadfile`. `--durations` aggregates across workers in xdist and
   keeps reporting the slow tail. The integration step is
   untouched.
2. **`--dist loadfile` is the distribution, for the reason the
   spike softened but did not remove.** Module-scoped fixtures
   (the 81-driver capture, now ~18s) are paid once per file by
   construction; the spike showed default-dist no slower TODAY,
   but loadfile is the principled choice and costs nothing.
3. **The refusal ledger's behavior under workers is verified, not
   assumed.** #239's ledger arms from `pytest_configure`, which
   runs in every xdist worker; the per-test delta check runs
   in-worker and fails the owning test, which the spike exercised
   green. What the spike did not exercise: the RESIDUAL path (a
   refusal attributed to no test) under workers, where
   `pytest_terminal_summary` and `session.exitstatus` live in the
   worker and the controller aggregates worker exit codes. The
   milestone plants a residual refusal in a scratch run under
   `-n 4` and records what the operator actually sees, amending
   the ledger's summary hook if the signal is swallowed (a
   worker's nonzero exit fails the run either way; the question is
   the legibility of WHY, and the answer lands in the ledger's
   comment).
4. **The flake is hunted within a bounded budget, then explained.**
   `test_secret_like_option_names_are_rejected[password-password]`
   failed once in a serial full run on 2026-08-22 (#253's branch)
   and has passed in every run since, including the spike's four
   reshuffled parallel runs. The test body is a pure-function
   refusal check with no visible order dependence. The hunt: read
   the test's imports and the conftest's pydantic default rewrite
   for shared mutable state; run the unit lane thrice under
   `-n 4 --dist loadfile` and once serially with `-p
   no:cacheprovider`; if it does not reproduce, the issue's
   "fixed or explained" resolves to EXPLAINED: recorded in the
   implementation doc as one unreproduced failure with the reads
   that found no mechanism, and the week-of-runs watch is what
   would catch a recurrence. A reproduction, if one appears,
   stops the milestone until fixed.
5. **The fixed-port audit closes the issue's last hazard.** The
   unit files naming `localhost:<port>` are audited by grep and
   read: a name in config data is fine, a socket bind to a fixed
   port is not. The spike's green runs are strong evidence none
   binds; the audit makes it a stated fact with the file list in
   the implementation doc.
6. **The conftest's per-worker duplication is priced, not
   changed.** Env setup, the pydantic default rewrite, and the
   `__pycache__` clearing run once per worker; the clearing's
   `ignore_errors=True` race was tolerated by four spike runs and
   stays as is, with the audit note moving into the conftest
   comment so the next reader knows it was considered under
   workers.

## The standing review lenses, pre-answered

- **No-leak.** No observability surface changes; the one output
  that changes shape is pytest's own progress line under workers.
  The ledger's failure texts are already values-free.
- **Pin before reshaping.** Nothing behavioral is reshaped; the
  suite's own guarantees are the pins and they run identically
  under workers (the spike's four green runs, then this PR's).
- **Closed sets, honest seams.** Not this territory.
- **Inventories by tooling.** The port audit grep
  (`grep -rn "localhost:" tests/unit`) with its file-by-file
  disposition in the implementation doc; the CI diff is one line
  plus the lockfile.

## Module layout

- `vinga-server/pyproject.toml` + `uv.lock`: the dev dependency.
- `.github/workflows/vinga-server.yml`: the unit step's one line
  (and its comment, which explains the loadfile choice and the
  auto spelling in place).
- `vinga-server/tests/conftest.py`: the ledger's residual-path
  comment amended per decision 3 if needed; the pycache note per
  decision 6.
- `docs/plans/` + `CHANGELOG.md` (`### Changed`: the CI unit lane
  runs parallel; local behavior unchanged).

## Tests

The suite itself is the test. Specifically verified in the
milestone, recorded with outputs: three consecutive local
`-n 4 --dist loadfile` runs green; the residual-ledger scratch run
of decision 3; the serial lane still green (nothing forces xdist
locally); this PR's own CI run green with the durations table
present and the unit lane at its projected time.

## Milestones

- [ ] **M1: switch the lane.** (PR TBD) One milestone, one
  dependency and one workflow line plus the verifications above;
  `main` stays releasable trivially. Deepens nothing; the one
  thing a developer stops paying is five minutes per CI round
  trip.
