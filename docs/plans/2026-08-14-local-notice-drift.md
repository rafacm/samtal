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
takes the final stderr line of each invocation: the `--local` path
prints `LOCAL_NOTICE` first, and the notice is the line `_report`
prints last on both paths. The existing hand-synced comment in
`_delete_device` stays as it is; its claim is now enforced by this
test.

The fix lands before the test only in the sense that both are one
commit series; the test is written to the API's answers, so it fails
on the unfixed branches and passes after the three changes, which is
the honest order to commit them in (test alongside fix, red-to-green
stated in the PR body).

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
samtal-server/samtal_server/config/cli.py       three --local branches
samtal-server/tests/unit/test_config_cli.py     the two-path pin
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
  parametrization.** Mitigation: the test's docstring states it
  covers the complete `--local` mutating subset, and the
  parametrization is written from the same grammar table a new
  command would be added to; #139's dispatch unification removes
  the class of drift entirely.

## Milestones

- [ ] **Align the three --local notices with the API and pin every
  local write's notice**: `secret_notice` lands in `writes.py`;
  `_delete_mcp_server` passes `MCP_RELOAD_NOTICE`, `_set_secret`
  and `_clear_secret` pass `secret_notice(location.kind)`; the
  parametrized two-path test covers the complete `--local` mutating
  subset; CHANGELOG entry under Fixed, 2026-08-14; the
  implementation doc section written in the change that ticks this
  box. Accept: lint and both lanes green; the new test demonstrably
  red on the unfixed branches and green after.
