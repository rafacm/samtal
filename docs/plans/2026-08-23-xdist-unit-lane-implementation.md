# Run the unit test lane under pytest-xdist: implementation

Companion to [`2026-08-23-xdist-unit-lane.md`](2026-08-23-xdist-unit-lane.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: switch the lane

### What was done

Four commits: the dependency and the lane, the refusal ledger's
worker-to-controller repair, the free-port fix, and the prose and docs.

**The dependency and the lane.** `uv add --dev pytest-xdist` put
`pytest-xdist>=3.8.0` in the dev group and `pytest-xdist` plus its one
transitive dependency `execnet` in `uv.lock`. The unit step is now

```
uv run pytest tests/unit -v --durations=25 -n auto --dist loadfile
```

changing by exactly the two appended tokens, as decision 1 requires:
`-v` stays, `--durations=25` stays, and the revert is the removal of
` -n auto --dist loadfile` and nothing else. The step's comment block
explains the three things a reader would otherwise have to reconstruct:
why `loadfile` (module-scoped fixtures are paid once per file by
construction, which the expensive ones assume), why `auto` rather than
`4` (the two coincide on today's 4-core runner and `auto` uses a larger
one whole), and the graded response if the lane turns intermittently
red (`-n 3`, then `-n 2`, then the flag off and #254 reopened). The
integration step is untouched.

**The refusal ledger's residual path.** Decision 3's repair, as code.
The worker side: `pytest_sessionfinish` asks its config for a
`workeroutput` (only xdist puts one there), and if it finds one it
writes the residual's description under `vinga_residual_refusals` and
returns without touching the exit status, because a worker's exit
status is not the run's. The controller side: a new
`pytest_testnodedown` reads that key off each finished worker and adds
it, prefixed with the worker's gateway id, to a list in the
controller's `config.stash`; the controller's own
`pytest_sessionfinish` sets `ExitCode.TESTS_FAILED` if that list is
non-empty, and `pytest_terminal_summary` prints the section, joined
across workers.

A serial run has no `workeroutput` and no node-down, so it takes the
same path it took before: its own residual goes into the same stash
list, the same hook fails the run, the same section prints. The stash
key's type went from `str` to `list[str]`, which is what lets one
controller carry several workers' residuals without deciding which of
them to keep.

**The free port.** `unused_url()` in `tests/unit/test_tools_mcp_http.py`
is now a `@contextmanager` that binds port 0 and holds the socket for
the block instead of binding, releasing, and returning the number. A
bound socket that never listens refuses connections exactly as an
unbound port does, so what its two tests observe is unchanged; what
changes is that no other worker's `port=0` server can be handed the
number in between. Both call sites became `with unused_url() as url:`.

**Prose.** Three conftest comments, per decisions 3 and 6:

- The pycache note's "once, before the first import" became "once per
  process", with the reason (under xdist the controller and every
  worker execute this file) and the mechanism that makes the
  cross-process race safe: CPython's import machinery falls back to
  compiling from source when reading a cached `.pyc` raises `OSError`,
  so a half-deleted cache costs a recompile and can never produce a
  wrong import.
- `SHARED_DATABASE_DIR` gained the `mkdtemp` disposition: one per
  process, so a parallel run leaves N un-removed temp directories
  instead of one, priced and kept because the isolation is if anything
  better than a shared directory's.
- The lane guard's preamble gained the paragraph explaining why the
  residual half needs a channel that the per-test half does not.

**Docs.** `AGENTS.md` and `vinga-server/README.md` command blocks each
gained `uv run pytest tests/unit -q -n auto --dist loadfile` as the way
to reproduce the CI lane locally, with the note that local runs stay
serial by default. `CHANGELOG.md` gained a `### Changed` entry under
`## 2026-08-23`.

### The port audit

`grep -rnE "\.bind\(|ThreadingHTTPServer\(|socket\.socket\(|uvicorn" tests/unit tests/support tests/tools`
from `vinga-server/`, run before any change. Every hit, dispositioned:

| Site | What it is | Disposition |
| --- | --- | --- |
| `test_tools_mcp_http.py:103-104` | `socket.socket()` bound to port 0, released, number returned as "nobody answers here" | **bind-and-release: FIXED.** Now holds the socket for the test's duration (see above). |
| `test_tools_mcp_http.py:443-445` | `socket.socket()` bound to port 0 and `listen(1)`, held for the `with` block | port-0 bind, held. Safe. |
| `test_tools_mcp_http.py:191, 289, 345, 489` | `ThreadingHTTPServer(("127.0.0.1", 0), Handler)` | port-0 bind, held by a running server. Safe. |
| `test_secret_resolution.py:278` | `ThreadingHTTPServer(("127.0.0.1", 0), Handler)` | port-0 bind, held by a running server. Safe. |
| `tests/support/tools_mcp.py:229-247` | `uvicorn.Config(..., port=0)`, port read back off the started server's socket | port-0 bind, held by a running server. Safe, and the comment there already said why. |
| `test_tools_mcp_http.py:5, 76` | the word "uvicorn" in a docstring | config-data name. Nothing binds. |
| `test_drain.py:167` | `uvicorn.Config(app)` handed to a `DrainingServer` whose `run`/`serve` is never called (the tests drive `handle_exit` directly, and the one test that reaches `serve` monkeypatches `DrainingServer.run`) | config-data name. Nothing binds. |
| `test_drain.py:19, 69, 78, 82, 192, 201, 205, 212-268, 332, 361, 399, 428, 445-452` | `uvicorn.Server._serve` monkeypatched, `uvicorn_config.port == 9001` asserted on a config object, prose | config-data name. Nothing binds. |
| `test_logs.py:340-400` | the logger name `uvicorn.error` | config-data name. Nothing binds. |
| `test_mcp_status_reflection.py:14` | the word "uvicorn" in a docstring | config-data name. Nothing binds. |

`tests/tools` produced no hits. One third-class site existed, it is the
one the plan named, and it is fixed rather than accepted; no further
bind-and-release hit needed an acceptance sentence.

### The residual-ledger proof

Decision 3's planted residual: a scratch
`tests/unit/test_scratch_residual.py` with one trivial test and a
session-scoped autouse fixture whose TEARDOWN emits
`ConversationsDropped(session=SessionId("has a space"))` on a real
`ServerEvents(CONVERSATIONS_CHANNEL)`. The session id is a value type
the schema refuses, so the emitter reports a refusal after the last
test of the session has run, which is a refusal no test owns. The file
was removed once the three runs below were recorded; the PR review
round below put the experiment back as
`tests/unit/test_lane_guard_residual.py`, which runs both of the
post-repair transcripts on every unit run.

**Before the repair, under `-n 4` (the disabled path the plan
predicts).** Run against the committed conftest, with the repaired copy
saved aside and restored afterwards:

```
$ uv run pytest tests/unit/test_scratch_residual.py -q -n 4 --dist loadfile
bringing up nodes...
.                                                                        [100%]
1 passed in 0.49s
$ echo $?
0
```

Nothing printed, exit 0: the refusal disappeared, exactly as decision 3
says it does.

**Serially, with the repair (today's behavior, unchanged).**

```
$ uv run pytest tests/unit/test_scratch_residual.py -q
.                                                                        [100%]
==================== event schema refusals outside any test ====================
the event schema refused an emission where no test owns it: vinga_server.conversations.store: ('an event that could not be built', 'construction_failed'). A refusal is a schema bug; the likely site is a module or session fixture's teardown.
1 passed in 0.03s
$ echo $?
1
```

**Under `-n 4`, with the repair (the repaired behavior).**

```
$ uv run pytest tests/unit/test_scratch_residual.py -q -n 4 --dist loadfile
bringing up nodes...
.                                                                        [100%]
==================== event schema refusals outside any test ====================
the event schema refused an emission where no test owns it: gw0: vinga_server.conversations.store: ('an event that could not be built', 'construction_failed'). A refusal is a schema bug; the likely site is a module or session fixture's teardown.
1 passed in 0.49s
$ echo $?
1
```

The section prints and the run fails in both modes. The `gw0:` prefix
is the only difference in the sentence, and it is the thing worth
knowing first when the files are split across workers.

### The flake: ACCEPTED

Decision 4's bounded read pass over
`test_config.py::test_secret_like_option_names_are_rejected[password-password]`,
the one unreproduced failure recorded in
`2026-08-22-typed-event-enums-implementation.md`. No rerun ritual was
run, since three loadfile runs explore less order space than the serial
run that failed.

What the read covered and found:

- The test's path is `load_config_from_data` → a YAML file in a fresh
  `TemporaryDirectory` → `load_file_config` → `compose_config`. The
  refusal it asserts on comes from `secret_option_fragment`, which
  reads `_SECRET_KEY_FRAGMENTS`, a module-level tuple of six string
  literals. `grep` finds no assignment to it anywhere but its
  definition, and a tuple cannot be mutated in place.
- The module's imports are `tests.support.configs` (module-level
  constants: strings, dicts built fresh per call inside the builder
  functions), `Config`/`ConfigError`/`load_file_config`, `DOMAIN_KEYS`
  (a tuple, never mutated) and `normalize_mac`, and
  `RETENTION_DAYS_DEFAULT` (an int). No shared mutable state on the
  path.
- The conftest's pydantic default rewrite touches
  `DatabaseConfig.model_fields["dir"].default` and rebuilds the
  validator. It is per-test, autouse, and restored at teardown, and
  `DatabaseConfig` is not on this test's path at all: the test never
  reaches `server.database.dir`, and the assertion it makes is about a
  refusal raised before any database field is defaulted.

No mechanism found. Resolved as **ACCEPTED**, per decision 4: one
unreproduced failure, leading hypothesis stale bytecode on the in-flux
pre-commit tree the record describes (which is the repository's own
documented way for a run to lie about what it executed), no mechanism
in the reads, and the week-of-runs watch as the net. `--dist loadfile`
preserves intra-file order exactly, so this switch cannot introduce an
ordering the serial run that failed did not already have.

### Deviations from the plan

**One, in where the controller fails the run.** Decision 3 says the
controller "prints them from its own `pytest_terminal_summary`, and
fails the run there". It prints there; it fails in
`pytest_sessionfinish` instead. The reason is an interface one:
`pytest_terminal_summary` is handed `terminalreporter`, `exitstatus`
and `config`, none of which is a handle on the session, and the only
route to one is `terminalreporter._session`, a private attribute.
`pytest_sessionfinish` receives the session as a hook argument, and it
is where the serial path already failed, so failing there is both
public and one code path instead of two. The observable behavior is
what decision 3 specifies: the section prints and the run exits
non-zero, in both modes, as the three transcripts above show.

Nothing else deviates.

### Verification

From `vinga-server/`, on `feature/xdist-unit-lane-m1`, after the last
commit. The local machine has 14 cores, so the parallel runs below are
a **SMOKE CHECK** and not evidence about the fourth hazard: 4 workers
with ten idle cores is headroom the 4-core runner does not have, and
contention on 4 real cores is exercised only by this PR's own CI runs.

- Three consecutive `uv run pytest tests/unit -q -n 4 --dist loadfile`:
  **2798 passed, 20 skipped** in 81.54 s, 78.84 s, 78.50 s.
- `uv run pytest tests/unit -q` (serial): **2798 passed, 20 skipped**
  in 279.43 s. Nothing forces xdist locally, and the same 2798 tests
  run either way. The local speedup is 3.4x to 3.6x, consistent with
  the spike's 3.0x to 3.6x.
- `uv run pytest tests/integration -q`: **61 passed** in 195.58 s. The
  integration lane is untouched and still serial.
- `uv run ruff check .`: **All checks passed!**
- `uv run mypy`: **Success: no issues found in 4 source files.**
- The workflow file parses, and the unit step's `run` is exactly
  `uv run pytest tests/unit -v --durations=25 -n auto --dist loadfile`
  (checked by loading the YAML and printing the step).
- The residual-ledger scratch runs of decision 3: three transcripts
  above, the pre-repair one showing the disabled path and the two
  post-repair ones showing the section and a non-zero exit serially and
  under `-n 4`.

- **PR #261's own CI run (run 32610208370): green**, and the one
  verification the plan's Tests section named that the local runs
  cannot stand in for, since the 4-core runner is where the fourth
  hazard (contention) actually lives. The durations table is present in
  the unit lane's log. The **unit lane ran in 2m25s**, against its
  measured 6m31s serially, which is the projection met: the lane is no
  longer the critical path, and the **integration lane at 4m54s** is
  now the one that finishes last. This run is also the first data point
  of the week-of-runs watch that decision 3 of the issue's restated
  decisions keeps open on #254.

Nothing here needs hardware, so no verification step was left
unverifiable.

## PR review round (PR #261)

External review of the PR diff: claude backend, claude CLI, model
`claude-opus-5`, read-only tool set, 2026-08-23, posted on the PR.
Verdict: mergeable after the listed fixes, **no P1**. Six findings, two
P2 and four P3, each fixed in a commit of its own, plus one addition
the maintainer's observation prompted.

The review's negative results are worth as much as its findings here,
since the repair's whole risk is a hook ordering that is nobody's
documented contract. It read xdist's, pluggy's and pytest's own sources
and could not break: the worker-to-controller handoff ordering
(`WorkerInteractor.pytest_sessionfinish` is a hookwrapper, so the
conftest's write into `config.workeroutput` always precedes
`sendevent("workerfinished")` whatever the registration order);
`pytest_testnodedown` firing for every node before the controller's
`pytest_sessionfinish`; `getattr(node, "workeroutput", {})` on a node
that errored down; `Stash.setdefault` existing; the serial path being
behaviorally identical and the printed sentence byte-identical; the
residual description carrying only registry-owned identifiers, so the
new cross-process channel adds no leak surface; and the bind-based port
audit being materially complete for `tests/unit` (no
`asyncio.start_server`, `HTTPServer(`, `TCPServer` or `websockets.serve`
sites exist, and no test asserts "nothing answers" at a fixed port).

Findings condensed but faithful:

1. **P2: the milestone's one piece of shipped code had no automated
   test, and its failure mode is silence.** The only proof was three
   transcripts produced by a scratch file the doc says was removed.
   Delete `pytest_testnodedown`, rename `_RESIDUAL_OUTPUT`, or let a
   future edit put an early return back in front of the `workeroutput`
   write, and 2798 tests still pass while the lane goes back to the
   silent hole plan-review finding 1 was raised about.
   *Fixed in `ad1e075a`*: `tests/unit/test_lane_guard_residual.py`, the
   deleted scratch experiment kept. `pytester` gives a scratch
   directory and a subprocess; a COPY of the real `tests/conftest.py`
   goes in it, so the subject is the shipped file rather than a
   paraphrase, beside one file whose session teardown emits what the
   schema refuses. Two runs, serial and `-n 2 --dist loadfile`, each
   asserting the printed section and a non-zero exit, the distributed
   one also asserting the `gw` prefix. Checked by mutation against the
   pre-repair conftest (`109b9347`): the distributed test fails there
   and the serial one passes, which is exactly the split the repair
   closed.
2. **P2: the Verification section was stale where the plan made it
   load-bearing, and two documents still said "projected".** The plan's
   Tests section lists this PR's own CI run as a required recorded
   verification, the milestone box was already ticked with #261, and the
   run had since happened and measured 2m25s, while `CHANGELOG.md` and
   the workflow comment still projected two or three minutes.
   *Fixed in `ac5c4eb0`*: run 32610208370 recorded in Verification
   (green, durations table present, unit lane 2m25s, integration lane
   4m54s and now the last to finish, first data point of the
   week-of-runs watch), and the measured figure replaces the projection
   in both other places.
3. **P3: the held-socket property was pinned by nothing.** Both
   `unused_url()` call sites see connection-refused whether the socket
   is held or bound-and-released, so a future cleanup could revert the
   one genuine parallel hazard decision 5 named and keep a green suite.
   *Fixed in `fa430046`*: a test that binds a second socket to the port
   inside the helper's block and asserts `EADDRINUSE`, which is true
   only while the port is held.
4. **P3: the plan's standing-lenses section still named the port-audit
   grep its own review round rejected.** `grep -rn "localhost:"
   tests/unit` sat in the same file as decision 5 and resolution
   `6532bcfa`, both of which say the audit is over bind calls precisely
   because a `localhost:` grep reads the files that cannot bind.
   *Fixed in `72761597`*: the parenthetical is the bind-based grep with
   its three dispositions.
5. **P3: thread oversubscription is a cheaper first lever than `-n 3`
   and the workflow did not use it.** pysilero-vad is a core dependency,
   so onnxruntime is in every worker, and its intra-op pool sizes to the
   core count: four workers on four cores ask for roughly sixteen
   threads. That amplifies exactly the descheduling the plan names as
   the exposed set, and the 14-core local smoke check structurally
   cannot see it.
   *Fixed in `b8065241`*: `OMP_NUM_THREADS`, `ORT_NUM_THREADS` and
   `OPENBLAS_NUM_THREADS` in the workflow's existing `env:` block, and
   the step comment names checking them as step 0 of the graded
   response, ahead of `-n 3`.
6. **P3: a changelog sentence described a regime that never existed.**
   "where under workers it used to vanish silently" implies past CI runs
   were losing residual refusals; the lane had never run under workers.
   *Fixed in `b28f46c2`*: said as the conditional it is, "without the
   repair a worker's residual would have vanished silently".

**7. Coordinator addition, not an external-review finding.** Prompted by
the maintainer spotting "Unable to reserve cache with key
setup-uv-2-...: another job may be creating this cache" in the unit
run's log. Diagnosed as this PR's own `uv.lock` change making the cache
key new, with the PR-creation run and the tick-commit run about a minute
apart both racing to save it; the loser logs and skips. Benign, but the
first run is entirely wasted, and this session's pipeline produces that
create-then-tick double-run on every PR.
*Fixed in `0e961e9e`*: a top-level `concurrency` block grouped on
workflow and ref, with `cancel-in-progress` as the expression
`github.event_name == 'pull_request'` rather than a plain `true`. A
superseded PR run is cancelled; a push to main never is, because that is
the event the `image` job publishes on and a merge cut short by the next
merge would leave `latest` pointing at whichever build survived. It is
the inter-run sibling of the intra-run cache race PR #256 fixed with
`save-cache: false`.

### Verification after the round

From `vinga-server/`, on `feature/xdist-unit-lane-m1`, after the last
commit of the round.

- `uv run ruff check .`: **All checks passed!**
- `uv run mypy`: **Success: no issues found in 4 source files.**
- `uv run pytest tests/unit/test_lane_guard_residual.py -q`: **2
  passed**, and the same file under `-n 2 --dist loadfile`: **2
  passed**. The new test drives both modes itself, and this runs it in
  both.
- `uv run pytest tests/unit -q -n 4 --dist loadfile`: **2801 passed, 20
  skipped**. Three more than before the round: the pytester pair and
  the held-port pin.
- `uv run pytest tests/unit -q` (serial): **2801 passed, 20 skipped**.
- `uv run pytest tests/integration -q`: **61 passed**.
- The workflow YAML parses, its `env:` block carries the three thread
  caps, and its `concurrency` block carries the group and the
  `cancel-in-progress` expression (checked by loading the YAML and
  printing all three).
