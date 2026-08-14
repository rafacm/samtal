# Make the --local write notices agree with the API's answers

Companion to
[`2026-08-14-local-notice-drift.md`](2026-08-14-local-notice-drift.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out.

## Milestone 1: align the three --local notices with the API and pin every local write's notice

The three break-glass branches that answered the restart sentence for an
act the reload applies now answer the reload's, the preamble no longer
contradicts them, and a parametrized test compares the two paths'
answers for every act the `--local` subset mutates.

### What landed

**`samtal_server/config/writes.py`.** One new function, `secret_notice`,
between the notice constants and the `wrote_*` sentences, returning
`MCP_RELOAD_NOTICE` for `mcp_server` and `RESTART_NOTICE` for anything
else. Its docstring says why the mapping follows the entity the
credential is stored on (the reload rebuilds MCP entries with their
credentials, a provider reads its credential as it is built at boot) and
why it exists at all: the API says the same by having four secret
routes, each statically one sentence, where one CLI command covers both
kinds. `EntityKind` is imported from `secrets.py`, which imports only
`loader` and `models`, so no cycle arises. `secret_notice` joins
`__all__`.

**`samtal_server/config/cli.py`.** Four changes.

- The `writes` import gains `MCP_RELOAD_NOTICE` and `secret_notice`.
- `_delete_mcp_server`'s local branch passes `MCP_RELOAD_NOTICE`, with a
  comment saying it is the sentence the API answers this delete with and
  why the row makes it true either way.
- `_set_secret` and `_clear_secret` pass `secret_notice(location.kind)`;
  the comment sits on the first of the pair.
- `LOCAL_NOTICE` drops its universal timing claim and keeps its identity
  claim, verbatim as the plan writes it, including the phrase
  "bypassing the configuration API" that the existing preamble test
  looks for. The comment above it, about why the line is printed rather
  than enforced, is untouched: that reasoning did not move.
- The module docstring's `--local` paragraph says the same as the
  revised constant: the boot-time snapshot is the default story, the
  exceptions the server side makes are the exceptions here too, and each
  write answers with the sentence the API answers that act with. The
  device delete stays as the named example and the MCP write joins it.

`api.py` is untouched, as the plan decided.

**`tests/unit/test_config_cli.py`.** One parametrized test,
`test_a_local_write_says_what_the_api_says_for_the_same_act`, in the
recovery-subset section directly after the preamble test, with seven
small seed helpers and a `LOCAL_MUTATIONS` list of nine cases: the five
deletes, and `set-secret` and `clear-secret` on a provider slot and on
an MCP slot. Each case carries what the act needs seeded, the act's
argv, and whether it is one a reload applies. The body seeds, runs the
act over HTTP, keeps that invocation's stderr as the expected value,
seeds again, runs the same act with `--local`, and asserts the local
stderr's lines are exactly `[LOCAL_NOTICE, <what the ordinary path
answered>]`. The three reloadable cases additionally assert
`RESTART_NOTICE` appears nowhere in that stderr. The section header
above the test states that the nine cases are manual coverage of the
current subset, kept complete by review rather than by machinery, which
is the plan's honest wording for the P3 finding.

No existing test was weakened, loosened or deleted. Nothing in the
suite pinned `LOCAL_NOTICE`'s exact text (the preamble test checks for
the phrase "bypassing the configuration API", which the revision
keeps), so no existing assertion needed rewording.

**`samtal-server/README.md`.** The four passages the plan names, plus a
fifth sentence found beside the fourth:

- the write-order section's description of what a `--local` invocation
  prints;
- the break-glass paragraph in the configuration API section, which now
  names the three applicability cases explicitly;
- the deployment section's "an edit applies at the next server start"
  trap paragraph, which becomes "most of an edit applies at the next
  server start" with both exceptions named;
- the deployment section's description of the `--local` stderr line;
- and the two-sentence closer under it, "Restart the server when the
  repair is done. Nothing written this way is observed until then." The
  first sentence stays and gains its reason; the second was the same
  false claim in its shortest form and is replaced by what the write
  actually says.

The surrounding paragraphs were rewrapped where the edits left a ragged
line, so the diff is a little wider than the sentences that changed.

**`CHANGELOG.md`.** One entry under a new `### Fixed` subsection of the
existing `## 2026-08-14` header, after `### Changed` as Keep a Changelog
orders them.

**`config.example.yaml`.** Unchanged, and the claim was checked rather
than assumed: no configuration key changes shape or meaning, and the one
comment in it about applicability (under `database`, lines 178-183)
already describes the boot-time snapshot with both exceptions and says
each write reports which case it is in. Its `--local` mentions say only
where the database is and that the path needs no server. Nothing in the
file was made false by this change.

### Deviations from the plan

One, and it adds to the plan rather than departing from it.

1. **A fifth README sentence moved with the four.** The plan names four
   passages. Immediately below the fourth, closing the break-glass
   procedure in the deployment notes, stood "Restart the server when the
   repair is done. Nothing written this way is observed until then."
   That is the same absolute claim the plan is removing, in a place the
   line-numbered list did not reach. Leaving it would have left the
   procedure ending on exactly the falsehood the milestone exists to
   remove, so it was revised the same way: the instruction to restart
   stays with its reason (the path exists for a server that will not
   start), and the claim that a running server observes none of it is
   replaced by what the write says.

Everything else is as planned: `secret_notice` where the plan puts it
with the signature it gives, the three call sites, `LOCAL_NOTICE`
verbatim, nine parametrized cases, whole-stderr comparison, the extra
`RESTART_NOTICE` assertion on the MCP cases, `api.py` untouched.

### Discoveries

**Nothing pinned the local preamble's wording, which is why the drift
was survivable in the first place.** The only assertion on it anywhere
is a substring check for "bypassing the configuration API" in
`test_every_local_invocation_says_what_it_is`. That is what allowed a
sentence promising restart-only visibility to sit above three notices
that would soon say otherwise, and it is why the new test compares the
whole stderr rather than a final line: the loose check would have passed
just as happily on the contradiction.

**The red run isolates exactly the three claimed branches.** With the
fix reverted, six of the nine cases pass and three fail, and the three
are precisely `delete mcp-server`, `set-secret mcp-server` and
`clear-secret mcp-server`. That is the issue's diagnosis executed: the
other six local branches were already answering what their API twins
answer, `delete device` included.

### Verification

Red-to-green, run from `samtal-server/`. The fix was reverted with
`git revert -n` (which restores both `cli.py` and `writes.py` together,
so the branch is consistently pre-fix) while the new test stayed in the
working tree, and restored with `git checkout HEAD --` on those two
paths only, which held no uncommitted edits of their own. The
bytecode trap in `AGENTS.md` does not apply here: everything below ran
through pytest, whose `conftest.py` writes no bytecode and clears the
caches it finds.

- Against the unfixed branches:
  `uv run pytest tests/unit/test_config_cli.py -q -k "says_what_the_api_says"`
  gives **3 failed, 6 passed, 140 deselected**. The three are
  `[delete mcp-server home]`,
  `[set-secret mcp-server home env.API_TOKEN]` and
  `[clear-secret mcp-server home env.API_TOKEN]`, each failing on the
  stderr comparison with the restart sentence where the reload sentence
  was expected.
- With the fix restored: the same command gives **9 passed, 140
  deselected**.

Full lanes, from `samtal-server/`:

- `uv run ruff check .`: **All checks passed!**
- `uv run pytest tests/unit -q`: **1850 passed, 15 skipped** in 178 s.
- `uv run pytest tests/integration -q`: **53 passed** in 154 s.
