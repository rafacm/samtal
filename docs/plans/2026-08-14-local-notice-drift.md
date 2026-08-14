# Make the --local write notices agree with the API's answers

## Goal

Implement issue #134: three CLI commands on the `--local` break-glass
path print the generic restart notice where the API answers the MCP
reload notice for the identical act, so an operator is told to restart
the server for a change the reload command applies. The `--local`
branches of `delete mcp-server`, `set-secret`, and `clear-secret` in
`samtal_server/config/cli.py` fall through to `_report`'s default
(`RESTART_NOTICE`), while their API twins answer `MCP_RELOAD_NOTICE`
(`api.py`: `remove_mcp_server`, `write_mcp_secret`,
`remove_mcp_secret`). Fix the three branches, and pin every
`--local` write command's notice against what the API answers for
the same act, so the next drift fails CI.

The companion implementation doc,
[`2026-08-14-local-notice-drift-implementation.md`](2026-08-14-local-notice-drift-implementation.md),
records what the milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by issue #134 and not re-litigated here:

1. **The three local branches answer with the same notice constants
   their API twins answer.** `delete mcp-server` names the reload;
   `set-secret` and `clear-secret` name the reload when the secret
   lives on an MCP entry and keep the restart sentence when it lives
   on a provider, which is what the API's four secret endpoints
   answer.
2. **A test pins each `--local` write command's notice against the
   API constant for the same act.** No local-path notice is pinned
   today; the one command kept in step by hand (`delete device`)
   carries a comment admitting the manual sync.
3. **This is the immediate correctness fix only.** The wider
   unification of local and HTTP dispatch belongs to the config
   single-sourcing refactor (#139) and is out of scope here.
4. **CHANGELOG entry under Fixed.**

Evidence in the issue is pinned to main@8dd1a5f; re-verified against
main@133b41c for this plan: the `_report` calls without a notice
argument now sit at cli.py:373 (`_delete_mcp_server`), cli.py:554
(`_set_secret`), and cli.py:564 (`_clear_secret`); the API twins
answer `MCP_RELOAD_NOTICE` at api.py:1472, 1492, and 1504. The shape
is exactly as the issue describes.

## Decisions this plan makes

### The kind-to-notice choice for secrets lives in writes.py

`delete mcp-server` is statically an MCP act: its local branch passes
`MCP_RELOAD_NOTICE` directly, the same constant its API twin answers.

The secret commands are the only place a choice exists: one CLI
function handles both kinds, so it must branch on
`SecretLocation.kind` where the API expresses the same split as four
separate endpoints. That mapping (an MCP secret is applied by the
reload, a provider secret waits for a restart) is a sentence-choice,
and `writes.py`'s stated purpose is that the break-glass path and the
ordinary one cannot come to describe the same act differently. So the
mapping becomes a small function there:

```python
def secret_notice(kind: EntityKind) -> str:
    """When a stored credential takes effect, which follows the
    entity it is stored on: the reload rebuilds MCP entries with
    their credentials, and a provider reads its credential at
    boot."""
    return MCP_RELOAD_NOTICE if kind == "mcp_server" else RESTART_NOTICE
```

`_set_secret` and `_clear_secret` pass
`secret_notice(location.kind)` to `_report`. `EntityKind` is
imported from `secrets.py`, which imports nothing from `writes.py`,
so no cycle arises and `config schema`/`config reference` still pay
for no FastAPI.

`api.py` is untouched: its four secret endpoints are each statically
one kind, their constants are already pinned by
`test_config_api_writes.py` (`MCP_MUTATIONS` and
`PROVIDER_MUTATIONS`), and folding them into the helper is exactly
the dispatch unification the issue defers to #139. The anti-drift
mechanism for the pair of paths is the new CLI test, not a shared
call site.

### LOCAL_NOTICE stops making the timing claim the write answers

Fixing the three final notices is not enough on its own: every
`--local` invocation first prints `LOCAL_NOTICE` (cli.py:130), which
itself promises that a running server will not observe the change
until its next start, device bindings excepted. A fixed MCP write
would then print that sentence followed by the reload notice, two
mutually exclusive instructions in one invocation.

So `LOCAL_NOTICE` drops its universal timing claim and keeps its
identity claim. It still says what the path is and that it bypasses
the configuration API (the phrase the existing preamble test checks
for), and it now defers timing to the write itself:

```python
LOCAL_NOTICE = (
    "--local is the break-glass path: it reads and writes the database directly, "
    "bypassing the configuration API. Each write says separately when it takes "
    "effect, the same answer the API gives for the same act."
)
```

The module docstring's `--local` paragraph (cli.py:21-28) is revised
to match: the boot-time snapshot stays the default story, and the
sentence naming device bindings as the one exception becomes the
statement that each write answers with its own applicability notice,
the same one the API answers. The comment above `LOCAL_NOTICE`
(why it is printed rather than enforced) is unchanged; that
reasoning does not move.

### The pin compares the two paths' answers, not two copies of a constant

The new test runs each local-mutating act twice against the same
scratch database state: once through the ordinary HTTP path, once
through `--local`, and asserts the notice line printed on stderr is
identical. That is the issue's requirement stated as an executable
sentence: the API's answer for the act is the expected value, so a
future change to either side that the other does not follow fails
CI, with no third copy of the constant to also keep in step.

Concretely, one parametrized test in
`tests/unit/test_config_cli.py` covering every write command the
`--local` subset accepts:

- `delete provider` (restart today, correctly)
- `delete mcp-server` (the first fixed branch)
- `delete prompt-fragment` (restart, correctly)
- `delete agent` (restart, correctly)
- `delete device` (binding notice; already pinned by
  `test_a_local_device_delete_says_the_same_thing`, which stays,
  and covered again here so the parametrization is the complete
  subset rather than "the interesting ones")
- `set-secret` on a provider slot (restart) and on an MCP slot (the
  second fixed branch)
- `clear-secret` on a provider slot (restart) and on an MCP slot
  (the third fixed branch)

Each case seeds what the act needs through the existing `run`
fixture's HTTP path, captures the ordinary path's stderr notice for
the act, re-seeds, and captures the `--local` stderr. The comparison
does not stop at the final line: the `--local` invocation's stderr
is pinned as a whole shape, the revised `LOCAL_NOTICE` followed by
exactly the applicability notice the ordinary path answered for the
same act, and nothing else. Pinning the whole shape is what catches
the contradiction the final line alone would miss: a preamble that
reasserted restart timing ahead of a reload notice would fail the
equality, and to say so explicitly the MCP cases also assert
`RESTART_NOTICE` appears nowhere in the local invocation's stderr.
The existing hand-synced comment in `_delete_device` stays as it
is; its claim is now enforced by this test.

The fix lands before the test only in the sense that both are one
commit series; the test is written to the API's answers, so it fails
on the unfixed branches and passes after the three changes, which is
the honest order to commit them in (test alongside fix, red-to-green
stated in the PR body).

### The README's absolute restart claims move with the code

`samtal-server/README.md` states in four places that a `--local`
change (or any config edit) waits for the next server start, with
device bindings as the sole exception: the write-order section
(~line 1139), the break-glass section (~1365), the "an edit applies
at the next server start" trap paragraph (~2001-2007), and the
deployment section's description of the `--local` stderr line
(~2113). After the fix, each of those is false for MCP entries and
their secrets, which the README's own reload documentation says a
running server applies without a restart.

The four places are revised to describe the three applicability
cases as the write's own answer: most writes apply at the next
start, MCP entries and their stored secrets apply at the next
`config reload`, and device bindings are read live. Where the text
describes what the `--local` preamble says, it now describes the
revised sentence. The break-glass procedure still ends in starting
the server (the path exists for a server that will not start, and
starting it is the goal), but no longer claims a running server
cannot apply MCP changes through the reload.

### One milestone, one PR

The diff is three `_report` call sites, one helper in `writes.py`,
one parametrized test, and a CHANGELOG entry. Splitting it would
leave a merge where `main` tells an operator to restart for a
reloadable change, which is the bug, so the fix and its pin land
together. `main` stays releasable at the merge, as the image publish
on push requires.

## Files touched

```
samtal-server/samtal_server/config/writes.py    secret_notice helper
samtal-server/samtal_server/config/cli.py       three --local branches; LOCAL_NOTICE and the module docstring paragraph
samtal-server/tests/unit/test_config_cli.py     the two-path pin
samtal-server/README.md                         the four absolute restart claims
CHANGELOG.md                                    2026-08-14 entry under Fixed
docs/plans/2026-08-14-local-notice-drift.md
docs/plans/2026-08-14-local-notice-drift-implementation.md
```

`config.example.yaml` is untouched: no configuration key changes
shape or meaning. `api.py` is untouched by decision, above.

## Verification

- `uv run ruff check .`, `uv run pytest tests/unit -q`,
  `uv run pytest tests/integration -q`, all from `samtal-server/`.
- The new parametrized test fails on main before the fix (checked
  by running it against the unfixed `cli.py` during development)
  and passes after; the PR body records this.
- No test file named by any refactoring issue's no-behavior-change
  contract is edited here; this issue is a behavior fix, and the
  only test file it touches gains tests without weakening any
  existing assertion.

## Risks and mitigations

- **The two-path test is slower than a constant check.** Each case
  seeds and acts twice against a scratch database. Mitigation: the
  suite already runs every CLI test through the real grammar, real
  sub-application, and real repository per test; nine more
  parametrized cases of the same shape are marginal, and the unit
  lane is the right place for them.
- **Stderr-line comparison is looser than naming the constant.**
  If both paths drifted together to a wrong sentence, the test
  would still pass. Mitigation: that failure mode is the API
  answering the wrong notice, which `test_config_api_writes.py`
  already pins constant-by-constant (`_expected_notice`,
  `MCP_MUTATIONS`, `PROVIDER_MUTATIONS`); the pair of suites
  together closes both directions without a third copy of the
  sentences.
- **A future local command could be added without joining the
  parametrization.** The parametrization is a hand-maintained list;
  the grammar is imperative parser construction, so nothing fails
  mechanically when a new `local_ok=True` command skips the test.
  Stated honestly: the nine cases are manual coverage of the
  current `--local` mutating subset, kept complete by review, not
  by machinery. The drift class itself is removed when #139
  unifies local and HTTP dispatch, which is where a mechanical
  guarantee belongs; building an inventory walker over argparse
  internals for a subset that refactor dissolves would be scaffolding
  with a shorter life than its cost.

## Plan review round

One external review of the plan as first committed (5daf6ed): codex
CLI, model gpt-5.6-sol, read-only against this repository with the
issue #134 body supplied, 2026-08-14. Verdict: ready after the P1/P2
amendments. Findings as received, condensed; each carries its
resolution once the amendment addressing it lands.

1. **P1: the command would still print contradictory restart and
   reload guidance.** The plan changes only `_report`'s final
   notice, but every local invocation first prints `LOCAL_NOTICE`
   (cli.py:130), which says a running server observes no local
   change until restart, device bindings excepted. An MCP local
   write would therefore tell the operator both "until its next
   start" and "run config reload". Update `LOCAL_NOTICE` to make no
   universal timing claim (it bypasses the API, and each successful
   write reports separately when it takes effect), update the
   module docstring at cli.py:21-28, and name both changes in the
   milestone and file list.
   *Resolution*: adopted. A new decision section ("LOCAL_NOTICE
   stops making the timing claim the write answers") revises
   `LOCAL_NOTICE` to keep its identity claim and defer timing to
   the write's own notice, revises the module docstring paragraph
   to match, and the files-touched list and milestone name both
   changes.
2. **P2: the proposed final-line assertion deliberately misses that
   contradiction.** The test design discards all but the final
   stderr line, and existing local-preamble coverage only checks
   for the phrase "bypassing the configuration API", so the planned
   suite would pass while the command still gives mutually
   exclusive instructions. Retain the API-versus-final-notice
   comparison, but also pin the complete local stderr shape: the
   revised neutral `LOCAL_NOTICE` followed by the expected
   applicability notice; at minimum, MCP cases must assert that no
   preceding line claims restart is required.
   *Resolution*: adopted. The test decision now pins the local
   invocation's entire stderr shape (revised `LOCAL_NOTICE`, then
   exactly the ordinary path's notice for the act, nothing else),
   and the MCP cases additionally assert `RESTART_NOTICE` appears
   nowhere in that stderr.
3. **P2: operator documentation would remain false after the fix.**
   The file list excludes `samtal-server/README.md`, which
   repeatedly promises that every local change waits for restart
   (README.md:1139, 1365, 2113) and describes device binding as the
   sole exception (README.md:2001-2007), despite the documented MCP
   reload path elsewhere. Add the README to the milestone and
   revise the absolute claims to describe three applicability
   cases: restart, MCP reload, and live device binding; the
   break-glass procedure may still instruct restarting a server
   that is down, but must not claim a running server cannot apply
   MCP changes through reload.
   *Resolution*: adopted. A new decision section names the four
   README places and revises them to describe the three
   applicability cases (restart, MCP reload, live device bindings)
   as the write's own answer, keeping the break-glass procedure's
   ending but not its false claim; the files-touched list and
   milestone carry the README.
4. **P3: the claimed automatic completeness protection is not
   real.** The grammar is imperative parser construction and the
   parametrization is a separate hand-maintained list; adding
   another `local_ok=True` command would not fail the test. Either
   describe the nine cases honestly as manual coverage of the
   current subset, or add a mechanically enforced inventory check.
   A docstring alone is not a regression guard.
   *Resolution*: adopted, first option. The risk section now states
   the nine cases are manual coverage of the current subset, kept
   complete by review; the mechanical guarantee belongs to #139's
   dispatch unification, and an argparse-internals inventory walker
   for a subset that refactor dissolves is declined with reasons.

## Milestones

- [x] **[Align the three --local notices with the API and pin every
  local write's notice](2026-08-14-local-notice-drift-implementation.md#milestone-1-align-the-three---local-notices-with-the-api-and-pin-every-local-writes-notice)**
  (PR TBD): `secret_notice` lands in `writes.py`;
  `_delete_mcp_server` passes `MCP_RELOAD_NOTICE`, `_set_secret`
  and `_clear_secret` pass `secret_notice(location.kind)`;
  `LOCAL_NOTICE` and the module docstring paragraph drop the
  universal timing claim and defer to the write's own notice; the
  README's four absolute restart claims become the three
  applicability cases; the
  parametrized two-path test covers the complete `--local` mutating
  subset; CHANGELOG entry under Fixed, 2026-08-14; the
  implementation doc section written in the change that ticks this
  box. Accept: lint and both lanes green; the new test demonstrably
  red on the unfixed branches and green after.
