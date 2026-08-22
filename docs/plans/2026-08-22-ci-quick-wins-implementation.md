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

1. **The `AGENTS.md` sentence also gained the `docs/reference/`
   trigger path.** The plan asked for that sentence to be checked for
   staleness in the same commit. It was stale in a second way the plan
   did not anticipate: it said the workflow "only triggers on changes
   under `vinga-server/` or to the workflow file itself", while the
   `on:` block has matched `docs/reference/**` since the generated
   references started being diffed. Correcting one clause and leaving
   the other wrong in the same sentence was not worth the smaller
   diff.

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
