# Cut the CI critical path with parallel lanes and shrunk harness waits: implementation

Companion to
[`2026-08-22-ci-quick-wins.md`](2026-08-22-ci-quick-wins.md). One
section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: split the test job into parallel unit and integration jobs

### What was done

Two commits: the workflow split (with the `AGENTS.md` sentence the
plan's decision 1 pairs with it), and the changelog entry.

**The split.** `.github/workflows/vinga-server.yml` loses the `test`
job and gains `unit` and `integration`, both `runs-on: ubuntu-latest`,
neither carrying a `needs:` or an `if:`, so they start together on
every event the workflow already fired on. The `on:` block, the
`defaults.run` working directory and the `PYTHONDONTWRITEBYTECODE`
env are untouched: the split changes where each step runs, never
whether it runs.

`image` changes `needs: test` to `needs: [unit, integration]`, and the
comment block above it that explained `needs: test` as the publishing
gate now explains the list and says why both lanes have to be named.
Nothing else in `image` moved; it keeps its `if: github.event_name !=
'pull_request'`.

Both pytest invocations gained `--durations=25`. That is not
cosmetic here: one job's wall time was a usable regression signal, and
two lanes take that away, so each lane's log has to name its own slow
tail instead.

**Where each of the ten inventoried steps landed.** This is the plan's
named control against a silently dropped step, so it is recorded as a
list rather than asserted in prose. The order within each column is
the order the steps run in.

| Step (as inventoried in the plan) | Old job | New job |
| --- | --- | --- |
| `actions/checkout@v7` | `test` | **both** `unit` and `integration` |
| `astral-sh/setup-uv@v9.0.0` | `test` | **both** `unit` and `integration` |
| Install dependencies (`uv sync --frozen`) | `test` | **both** `unit` and `integration` |
| Lint (`uv run ruff check .`) | `test` | `unit` |
| Type-check the events package (`uv run mypy`) | `test` | `unit` |
| Unit tests | `test` | `unit`, plus `--durations=25` |
| Integration tests | `test` | `integration`, plus `--durations=25` |
| Check the committed domain reference is current | `test` | `integration` |
| Check the committed conversations reference is current | `test` | `integration` |
| Check the committed event reference is current | `test` | `integration` |
| Check the committed OpenAPI document is current | `test` | `integration` |
| Migrate a fresh database from the built wheel | `test` | `integration` |

The plan counts the four drift checks as one inventory item, which is
why ten items expand to twelve rows. The first three rows are the
duplication the plan calls for by design: a job is its own runner with
its own empty workspace, so each lane fetches and installs for itself.
What keeps both installs cheap is the uv cache carried over from
earlier runs; the lanes start together, so neither warms the other,
and on a run where `uv.lock` changed the key is cold for both. Only
the unit lane saves that key (`save-cache: false` on the integration
lane's setup-uv step), since two jobs writing identical bytes under
one key on the same run is a race whose loser reports a failed
reserve. Every other step appears in exactly one lane,
and every step that existed still exists: the parse check below lists
the two lanes' steps in full, and those two lists, with the three
duplicated setup steps counted once, are exactly the old job's twelve
steps.

The moved steps kept their bodies and their comments byte for byte.
The whole workflow diff is 55 insertions and 6 deletions, and the six
deletions are the `test:` job key, the two pytest `run:` lines, the
two lines of the old `needs: test` comment, and the `needs: test` line
itself.

**The prose.** `AGENTS.md`'s Commands section described CI as running
"the same lint, unit, and integration steps"; it now names the two
jobs and what each holds.

**The changelog.** A `### Changed` entry under the existing
`## 2026-08-22` heading, first in the section, covering what the old
queue cost, the new lanes, the unchanged publishing gate and the
durations flag.

### Deviations from the plan

Two, both small and both additive.

1. **The `AGENTS.md` sentence also gained an accurate account of when
   CI runs.** The plan asked for that sentence to be checked for
   staleness in the same commit. It was stale in a way the plan did
   not anticipate: it said the workflow "only triggers on changes
   under `vinga-server/` or to the workflow file itself", and the
   `on:` block disagrees three times over. It has matched
   `docs/reference/**` since the generated references started being
   diffed; its `push` trigger is restricted to `main`; and
   `workflow_dispatch` ignores the path filters altogether, which is
   how M1's own gate was proved on a branch. The sentence now names
   all three events. Correcting one clause and leaving the others
   wrong was not worth the smaller diff.

2. **Both new jobs carry a comment block.** The plan's module layout
   only names the `image` comment as prose to update. This workflow
   explains every non-obvious decision in place, and "why two jobs",
   "why the first three steps are duplicated" and "why
   `--durations=25`" are exactly the questions a reader of the split
   file will have. The blocks say so above `unit` and `integration`.

### Discoveries

- The plan's inventory of ten steps counts the four drift checks as
  one item. Expanding them makes the landing table twelve rows, which
  is the form that can actually be checked against a job log.
- `--durations=25` cannot see inside the event baseline harness's
  module-scoped `capture()` fixture; it reports one setup line for the
  whole thing. That is the plan's stated reason for M2's separate
  timing script, and M1's durations tables will show it as a single
  large setup entry rather than as the four slow drivers.

### Verification

- **The workflow parses.** `uv run python -c "import yaml; ..."` from
  `vinga-server/` loads the file and reports `jobs: ['unit',
  'integration', 'image']`, `defaults: {'run': {'working-directory':
  'vinga-server'}}`, the unchanged `on:` block (push and pull_request
  over `vinga-server/**`, `docs/reference/**` and the workflow file,
  plus `workflow_dispatch`), `image needs: ['unit', 'integration']`
  with its `if:` intact, and the two lanes' step lists in full. The
  step lists are what the landing table above was checked against.
- **The publishing gate was exercised before merge, and held.** GitHub
  Actions cannot be run from this checkout, so the proof is the
  `workflow_dispatch` run the plan prescribes, run 32588063442 on this
  branch: `unit` success, `integration` success, `image (default,
  latest)` success, `image (slim, slim, -slim)` success. The two image
  matrix jobs started only after both lanes had succeeded, which is
  `needs: [unit, integration]` doing its work on a real run rather
  than in a parse. A dispatch run builds and smokes but never
  publishes, so this cost nothing at the registry. That closes the
  plan's "image publishes with half the suite green" risk before
  merge; the read of the first push-to-main run after merge stays as
  confirmation and is still owed.
- **The lanes run at the same time, and both report their slow tail.**
  The PR's own CI run 32588127826 shows `unit` at 7m50s and
  `integration` at 5m0s running in parallel, so the critical path is
  the unit lane rather than the sum the old job paid, and each lane's
  log carries its "slowest 25 durations" table. The unit lane's
  duration is the number M2 is aimed at.
- **No test suite is affected.** M1 touches no Python, so the unit and
  integration suites were not re-run for it; the lanes that run them
  are what M1 changes, and only CI can show that.

## M2: shrink the four slow drivers' waits in the baseline harness

### What was done

Three commits: the harness shrink, the timing script, and the changelog
with these notes.

**The shrink.** `tests/tools/event_baseline.py` gains a harness-local
`FILLER_STALL_S = 0.5` above the three filler drivers, which now hand
`StallingLlm([FILLER_STALL_S])` to `masked_session` instead of the
shared 30 s `STALL_S`. The bound is untouched for them, as the plan's
decision 2 says: `masked_config()` still carries no server section, so
those drivers still run at the production default of 10 s, and the
point is that they no longer reach it. `tests/support/providers.py` is
not touched at all, so the watchdog suites that never wait out their
30 s keep it.

`unregistered()` gains a fourth parameter, `config: Config | None =
None`, defaulting to the `base_config()` it used to hardcode, and
`drive_llm_retry`'s second scenario passes `watchdog_config()` so both
halves run at the same 0.05 s bound.

Each of the four drivers carries the scenario truth it has to keep, per
the plan's decision 3, and `FILLER_STALL_S` carries the shared half of
the filler drivers' one.

**The timing script.** `tests/tools/driver_times.py`, run as `uv run
python -m tests.tools.driver_times`. It runs `driven()`'s loop (one
temporary directory and one `listening()` per driver, coroutine drivers
awaited on their own loop) and prints the slowest first with a total
and a driver count. It asserts nothing, which is deliberate and said in
its docstring: what a driver may take is a judgement about the machine,
and a threshold here would either never fire or fail on a loaded
runner. `tests/unit/test_event_baseline.py` is what holds the drivers
to anything.

### The scenario truths, checked by reading

The plan's decision 3 is explicit that an unmoved baseline file proves
the paths still fire and not that they were reached the same way, so
each shrunk driver's scenario was read against the runtime code rather
than inferred from an empty diff.

- **The three filler drivers: the filler fires before the reply's first
  audio.** `FillerRunner._fire` (`runtime/filler_runner.py`) sleeps the
  configured delay, then returns early if
  `self._output.speaking_started_at()` is not None. The delay is
  `masked_config()`'s `DELAY_MS = 60.0` ms; the reply's first audio
  cannot exist before the model's first token, which the stall holds
  for 500 ms, so the fire lands with the reply still silent by a factor
  of eight. Past that check, all three records are emitted before
  `_fire` touches the reply or the device at all: the speech skip and
  the barge-in skip emit and return, and `filler_played` is emitted
  before `begin_speaking()` is awaited. What the stalled round
  eventually does, succeed at 0.5 s as it does now or be given up after
  two 10 s watchdog windows as it did before, is therefore invisible to
  the record each driver keeps. The two skip drivers' own orderings
  hold with the same margins: the endpointer is fed and the output
  paused 20 ms in (`DELAY_MS / 1000 / 3`), and the pause comes off at
  80 ms, so the 60 ms fire is inside the paused window and both are
  inside the 500 ms stall.
- **`drive_llm_retry`: the watchdog times out on the first attempt of
  each half and the retry recovers.** `PipelineRuntime._watchdog_stream`
  loops over `("first", "retry")` with an `asyncio.timeout(timeout_s)`
  around the first event. The invariant here is the opposite of the
  filler drivers': the stall must EXCEED the bound. It does, by six
  hundred times, `StallingLlm(delays=[STALL_S, 0.0])`'s 30 s against
  `watchdog_config()`'s 0.05 s, and the 30 s is never waited out
  because the timeout cancels the sleep. The second entry of the delays
  list is 0.0, under the bound, so the retry delivers and the round
  recovers: one `llm_retry` per half and no `provider_failed`, which is
  what the second half used to spend 10 s producing. Nothing else in
  `watchdog_config()` differs from `base_config()`, which is what makes
  it safe to hand to `unregistered()`.

### Deviations from the plan

None. The four drivers, the two invariants, the `unregistered()`
parameter and the timing script all landed as decisions 2, 3 and 4 and
finding 9's resolution describe them.

### Discoveries

- The measured saving on the whole unit lane is about 65 s rather than
  the roughly 70 s the harness itself gives up (338.51 s to 273.17 s on
  this machine, against 70.13 s to 1.69 s across the four drivers). The
  difference is that `tests/unit/test_event_baseline.py` also holds the
  drivers, so a couple of seconds of the harness's own work is counted
  once in the fixture and the rest of the lane's variance covers the
  remainder. The plan's "roughly 70 seconds" is met within that noise.
- The `~16 s` tail the plan accepted is real and unchanged: after the
  shrink the slowest drivers are two barge-in gates at 2.48 s each, a
  third at 1.18 s and a drain at 1.08 s, all of them waiting on
  behaviour rather than on a provider timeout. Chasing them is #254's
  distribution problem, not this milestone's.

### Verification

- **Per-driver timing, before and after** (`uv run python -m
  tests.tools.driver_times` from `vinga-server/`, this machine). The
  four named drivers and the totals; every other driver's number is
  within its own run-to-run noise of where it was.

  | Driver | Before | After |
  | --- | --- | --- |
  | `runtime.filler_runner:FillerRunner._fire #1` | 20.02s | 0.53s |
  | `runtime.filler_runner:FillerRunner._fire #2` | 20.02s | 0.52s |
  | `runtime.filler_runner:FillerRunner._fire #3` | 20.02s | 0.52s |
  | `runtime.pipeline:PipelineRuntime._watchdog_stream #1` | 10.07s | 0.12s |
  | **TOTAL over 81 drivers** | **86.82s** | **17.94s** |

  The tail after the shrink, which is what the ~17 s floor is made of:

  | Driver | After |
  | --- | --- |
  | `runtime.turntaking:TurnTaking._gate_barge_in #4` | 2.48s |
  | `runtime.turntaking:TurnTaking._gate_barge_in #3` | 2.48s |
  | `runtime.turntaking:TurnTaking._gate_barge_in #5` | 1.18s |
  | `registry:SessionRegistry.drain #2` | 1.08s |
  | `runtime.pipeline:PipelineRuntime._llm_round_done #1` | 0.75s |
  | `runtime.turntaking:TurnTaking._gate_barge_in #1` | 0.67s |
  | `runtime.turntaking:TurnTaking._gate_barge_in #2` | 0.67s |
  | `runtime.turntaking:TurnTaking.finish_utterance #1` | 0.66s |
  | `device.session:DeviceSession.send_audio #1` | 0.60s |
  | `device.session:DeviceSession.run #6` | 0.60s |

- **The committed baseline did not move.** `uv run pytest
  tests/unit/test_event_baseline.py -q`: 8 passed in 20.17 s, with
  `tests/unit/data/event-baseline.json` untouched in `git status`. `uv
  run python -m tests.tools.event_baseline` run twice in a row leaves
  the tree clean of it both times.
- **The full unit suite is green and shorter.** `uv run pytest
  tests/unit -q`: 2819 passed, 20 skipped in 273.17 s, against 338.51 s
  for the same command with `event_baseline.py` temporarily restored to
  its pre-M2 state (same counts).
- **Lint and typing.** `uv run ruff check .`: all checks passed. `uv
  run mypy`: no issues in 3 source files.
- **The integration suite is green.** `uv run pytest tests/integration
  -q`: 61 passed in 193.67 s.
- **Not verified here: that the shrunk waits hold on a CI runner.** The
  margins are the plan's decision 4 (8x for the filler drivers, 600x
  for the retry driver, no value under 50 ms), but only a CI run shows
  them holding on a slower box. The M2 PR's own run is what says so.
