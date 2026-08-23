# Run the unit test lane under pytest-xdist

## Goal

Implement issue #254, inserted into the #246 queue ahead of the
config phase (maintainer decision, 2026-08-22, recorded on the
issue with the verification spike's results). The CI unit lane runs
under `pytest-xdist` with `-n auto --dist loadfile` (4 workers on
today's runner), taking the unit lane from its last measured 6m31s
to a projected 2 to 3 minutes and the critical path to roughly the
integration lane's measured ~5. The spike (issue comment,
2026-08-22) measured 3.0x to 3.6x at `-n 4` on a 14-core machine
with all four configurations green; what it could not exercise,
contention on 4 real cores, is this plan's fourth hazard (decision
2).

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
   has passed, per the issue's own verification list. The watch
   has an owner, a record, and a trigger: the driving session
   appends each subsequent #246 PR's unit-lane run id and outcome
   as a running comment on issue #254; a candidate flake is a
   unit-lane failure that passes on re-run with no code change;
   the trigger is two candidates inside the week, or any single
   one that reproduces under `-n` and not serially, and it fires
   the graded response of decision 2 (`-n 3`, then `-n 2`, then
   the flag off) with the issue reopened at whichever step ends
   it. The cheap-revert property (the two appended tokens of
   decision 1) is what licenses merging on the first green rather
   than after the week; the operative cost of a wrong call is
   developer misattribution on an intermittently red lane, which
   the named trigger bounds at two events.

## Design decisions this plan makes

1. **The dependency is a dev dependency, and the step keeps its
   verbosity.** `uv add --dev pytest-xdist` (lockfile moves with
   it); the CI unit step becomes `uv run pytest tests/unit -v
   --durations=25 -n auto --dist loadfile`, changing ONLY by the
   two appended tokens: `-v` stays (the lanes' verbosity is a
   deliberate, documented difference from the local `-q`, and
   under workers the per-test lines interleave but remain
   greppable). The revert is therefore exactly the removal of
   ` -n auto --dist loadfile`. `--durations` aggregates across
   workers and keeps reporting the slow tail. The integration
   step is untouched. The real diff set, stated whole: the
   workflow line and its comment, `pyproject.toml`, `uv.lock`,
   the conftest changes of decisions 3 and 6, `AGENTS.md` and the
   README command notes of decision 7b, and `CHANGELOG.md`.
2. **`--dist loadfile` is the distribution, for the reason the
   spike softened but did not remove.** Module-scoped fixtures
   (the 81-driver capture, now ~18s) are paid once per file by
   construction; the spike showed default-dist no slower TODAY,
   but loadfile is the principled choice and costs nothing.
   And a FOURTH hazard joins the issue's three: CPU contention on
   4 real cores, which the 14-core spike structurally could not
   exercise (4 workers with ten idle cores is headroom the runner
   does not have, and each worker also runs server threads, pool
   workers, and stdio-MCP subprocesses). The exposed set is the
   timing-margin work #233's M2 deliberately shrank: the
   20/60/80ms filler windows, the 0.3s connect and idle bounds,
   the 1.2s drain. A 20ms descheduling flips a filler driver's
   shape. The plan's honest position: the local runs are a SMOKE
   CHECK only, and the PR's own CI runs are the first evidence
   that bears on this hazard; the graded response, tried in order
   before any revert, is `-n 3` then `-n 2` (contention is a
   headroom problem, and shrinking the worker count is the lever
   that matches it), and only then the flag comes off.
3. **The refusal ledger's residual path is REPAIRED for workers,
   not merely verified.** The per-test delta check runs in-worker
   and fails the owning test, which the spike exercised green. The
   residual path (a refusal attributed to no test) is expected to
   be silently DISABLED under xdist, by mechanism: the stash is
   worker-local, xdist unregisters the worker's terminal reporter,
   and the controller derives the run's exit status from test
   reports, so none of the three process-local pieces reaches the
   operator and a residual refusal disappears entirely. The repair
   ships in this milestone as code: the worker side writes the
   residual descriptions into `config.workeroutput` from
   `pytest_sessionfinish`; the controller side collects them in
   `pytest_testnodedown`, prints them from its own
   `pytest_terminal_summary`, and fails the run there. Serial
   behavior is unchanged (the workeroutput path is absent and the
   existing hooks stand). The proof is a planted residual, and the
   plant is named so the experiment is reproducible: a scratch
   session-scoped fixture whose TEARDOWN emits a variant the
   schema refuses, run once serially (the existing section prints
   and the run fails, today's behavior) and once under `-n 4` (the
   controller's section prints and the run fails, the repaired
   behavior); both transcripts recorded in the implementation
   doc.
4. **The flake is hunted from its recorded context, then, if
   unreproduced, ACCEPTED.** The record
   (`2026-08-22-typed-event-enums-implementation.md`) says the
   failure happened in a PRE-COMMIT full run on an in-flux
   mid-milestone tree, alongside two expected golden failures,
   and the failure text was not captured. An in-flux tree is the
   repository's own documented way for a run to lie about what it
   executed (the stale-bytecode trap in AGENTS.md), so the
   LEADING hypothesis is that the run executed a mixture, not
   that the test is order-dependent; what would confirm it is
   reproducing the failure only under a mid-edit tree, and what
   rules order out for xdist purposes is that loadfile preserves
   intra-file order exactly, so this switch cannot introduce the
   ordering the serial run already had. The hunt: read the test's
   imports and the conftest's pydantic default rewrite for shared
   mutable state (bounded, one pass); no rerun ritual is offered
   as evidence, since three loadfile runs explore less order
   space than the run that failed. If nothing reproduces, the
   issue's "fixed or explained" resolves to ACCEPTED: one
   unreproduced failure, leading hypothesis stale bytecode on an
   in-flux tree, no mechanism found in the reads, the
   week-of-runs watch as the net. A reproduction stops the
   milestone until fixed.
5. **The port audit is by what BINDS, not by what is spelled.**
   `grep -rnE "\.bind\(|ThreadingHTTPServer\(|socket\.socket\(|uvicorn" tests/unit tests/support tests/tools`
   from `vinga-server/`, every hit dispositioned one of three
   ways in the implementation doc: a name in config data (fine),
   a bind on port 0 (fine under workers, the kernel hands each a
   distinct port), or a bind-and-release free-port assumption
   (the one genuine parallel hazard). The known third-class site,
   `unused_url()` in `test_tools_mcp_http.py` (binds port 0,
   releases it, then asserts nothing listens there), is FIXED
   rather than accepted: it holds the socket open for the test's
   duration and closes it at the end, so no other worker or
   process can take the port meanwhile. Any further third-class
   hit gets the same treatment or an explicit acceptance
   sentence.
6. **The conftest's per-worker duplication is priced, not
   changed, with the mechanism stated.** Env setup, the pydantic
   default rewrite, the shared `mkdtemp` directory, and the
   `__pycache__` clearing each run once per worker plus once on
   the controller. The `mkdtemp` duplication is harmless and
   mildly isolation-improving; its price is N un-removed temp
   directories per local run instead of one, said in the conftest
   comment. The clearing's cross-worker race (one worker clearing
   while another imports) is safe by MECHANISM, not by the
   spike's four green runs: CPython's import machinery falls back
   to compiling from source when reading a cached `.pyc` raises
   `OSError`, so a half-deleted cache costs a recompile, never a
   wrong import. The comment's "once, before the first import"
   is corrected to "once per process", since that is what is now
   true.

## The standing review lenses, pre-answered

- **No-leak.** No observability surface changes; the one output
  that changes shape is pytest's own progress line under workers.
  The ledger's failure texts are already values-free.
- **Pin before reshaping.** Nothing behavioral is reshaped; the
  suite's own guarantees are the pins and they run identically
  under workers (the spike's four green runs, then this PR's).
- **Closed sets, honest seams.** Not this territory.
- **Inventories by tooling.** The port audit grep of decision 5
  (`grep -rnE "\.bind\(|ThreadingHTTPServer\(|socket\.socket\(|uvicorn" tests/unit tests/support tests/tools`),
  with every hit dispositioned in the implementation doc as a name in
  config data, a bind on port 0, or a bind-and-release free-port
  assumption; the diff set is the one decision 1 states whole.

## Module layout

- `vinga-server/pyproject.toml` + `uv.lock`: the dev dependency.
- `.github/workflows/vinga-server.yml`: the unit step's one line
  (and its comment, which explains the loadfile choice and the
  auto spelling in place).
- `vinga-server/tests/conftest.py`: the ledger's residual-path
  comment amended per decision 3 if needed; the pycache note per
  decision 6.
- `AGENTS.md` and `vinga-server/README.md`: one line each in the
  command blocks giving the parallel invocation
  (`uv run pytest tests/unit -q -n auto --dist loadfile`) as the
  way to reproduce the CI lane locally; without it, a red PR's
  lane is a configuration no documented command reproduces.
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

- [x] **[M1: switch the lane.](2026-08-23-xdist-unit-lane-implementation.md#m1-switch-the-lane)**
  (PR #261) One milestone, one
  dependency and one workflow line plus the verifications above;
  `main` stays releasable trivially. Deepens nothing; the one
  thing a developer stops paying is five minutes per CI round
  trip.

## Plan review round

External review of commit `ff678db2`, 2026-08-23.
Backend: claude CLI 2.1.239, model `claude-opus-5`, read-only tool
set (interim fallback tier). Verdict as received: ready after the
P1/P2 amendments; the direction and decisions are right, but the two
places the plan claims to run an experiment (the residual ledger,
the local runs) cannot produce the evidence they are offered as, and
the port audit aims at the wrong file set. Findings condensed but
faithful:

1. **P1: the residual ledger is silently DISABLED under xdist, not
   merely illegible.** The stash is worker-local, xdist unregisters
   the worker's terminal reporter, and the controller derives exit
   status from reports, so a refusal attributed to no test
   disappears entirely. Pre-commit to the repair (worker writes the
   residual into `config.workeroutput` at sessionfinish; controller
   collects in `pytest_testnodedown` and prints and fails from its
   own terminal_summary), put the code in scope, and name how the
   scratch residual is planted.
   *Resolution* (`63982e03`): the repair ships as code with the
   workeroutput mechanism and a reproducible planted-teardown
   proof in both modes.

2. **P1: the spike is not CI-like; contention on 4 real cores is a
   fourth hazard the prescribed local runs cannot exercise.** The
   timing-margin drivers #233's M2 shrank (the 20/60/80ms filler
   windows, the 0.3s connect and idle bounds, the 1.2s drain) are
   the exposed set; a 20ms descheduling flips a driver's shape.
   Constrain local runs to 4 CPUs or state they are a smoke check
   and only the PR's CI runs bear on this; add the graded response
   (`-n 3`/`-n 2` before revert) since contention is a headroom
   problem.
   *Resolution* (`4ed55d2b`): contention is the fourth hazard,
   the exposed timing margins are named, local runs are a smoke
   check, and the graded n3/n2 response precedes any revert.

3. **P2: the step silently changes `-v` to `-q`**, breaking the
   two-token revert claim and the one-line inventory; keep `-v` or
   decide the drop with a reason, and state the real diff set.
   *Resolution* (`9f023bbd`): -v stays, the step changes by
   exactly the two appended tokens, and the whole diff set is
   stated.

4. **P2: the port audit greps the files that cannot bind and misses
   every file that does.** Audit by what binds
   (`\.bind\(|ThreadingHTTPServer\(|socket\.socket\(|uvicorn`),
   with three dispositions; `unused_url()`'s bind-and-release
   free-port assumption is the one genuine parallel hazard and
   needs a verdict.
   *Resolution* (`6532bcfa`): the audit targets bind calls with
   three dispositions and unused_url holds its socket open.

5. **P2: the flake hunt drops the recorded context** (an in-flux
   mid-milestone tree with two concurrent expected failures, the
   repository's own documented way for a tree to lie), never quotes
   the failure text, and loadfile reruns explore LESS order space
   than the serial run that failed. Lead with the stale-bytecode
   hypothesis; call the likely outcome ACCEPTED, not EXPLAINED.
   *Resolution* (`d0b61ce5`): the hunt starts from the recorded
   in-flux-tree context with stale bytecode leading, rerun rituals
   are dropped as evidence, and unreproduced resolves to ACCEPTED.

6. **P2: the week-of-runs watch has no owner, method, or trigger.**
   Name the record, the candidate-flake rule, and the revert
   trigger, and who does the recording.
   *Resolution* (the F6 commit): owner, record, candidate rule,
   and the two-event trigger firing the graded response.

7. **P3: the per-worker inventory drops the `mkdtemp` the issue
   named**; complete it with its disposition (N un-removed temp
   dirs per local run).
   *Resolution* (the F7/F8 commit): mkdtemp completed with its
   disposition.

8. **P3: the pycache race's safety is a mechanism, not four green
   runs** (import falls back to source on a cache OSError), and the
   conftest's "once" becomes "once per process".
   *Resolution* (the F7/F8 commit): the import-fallback mechanism
   stated and the comment corrected to once per process.

9. **P3: three unsourced or inconsistent numbers in the Goal**
   (`-n 4` vs `auto`; 6.5 underived; 3.6x cherry-picked from the
   faster contended run). Spell `auto`, derive or drop, quote
   3.0x to 3.6x.
   *Resolution* (the F9 commit): auto throughout, measured times
   quoted, 3.0x to 3.6x.

10. **P3: no documented command reproduces a CI-only failure**;
    add the parallel invocation to AGENTS.md and the README's
    command blocks.
    *Resolution* (the F10 commit): both command blocks gain the
    parallel invocation.
