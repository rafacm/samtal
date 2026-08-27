# A CI lane for documentation changes

**Date:** 2026-08-27 · **Issue:** #329

## Problem

The command-spellings census sweeps every tracked file, but the
server workflow's path filter admits only `vinga-server/**`,
`docs/reference/**`, the database plumbing and itself. A
documentation-only merge therefore ran no CI at all while still
being census surface: the #310 chain's M3 merge (PR #323) moved a
guide section, staled the committed manifest, and left `main`'s
unit lane red for hours with no run having gone red first. The
implement-issue skill gained a discipline step, but discipline only
covers work that goes through the skill.

## Changes

- `.github/workflows/docs.yml`: a complementary workflow whose
  `paths-ignore` mirrors the server workflow's `paths`, so every
  change runs the census in at least one workflow. One job: the
  internal link-and-anchor check, then the census suite against the
  unit lane's Postgres service (the lane's conftest provisions at
  collection and refuses without one). `workflow_dispatch` included
  as the dropped-event fallback.
- `scripts/check_doc_links.py`: the #310 chain's session link
  checker, committed. Markdown only; a link must sit whole on one
  source line to be seen; scanning Python docstrings is the known
  extension if a second docstring link goes wrong (PR #326 found
  one).
- The server workflow's paths list and the docs workflow's ignore
  list each carry a comment saying the two must move together.
- AGENTS.md's CI description and the implement-issue skill's
  CI-shapes sentence updated to the two-workflow reality.

## Key parameters

- Trigger complement: `paths-ignore` = exactly the five entries of
  the server workflow's `paths`.
- Cache: `save-cache: false`, the unit lane owns the key.
- Census invocation:
  `uv run pytest tests/unit/test_command_spellings.py -q`;
  regeneration stays
  `uv run python -m tests.unit.test_command_spellings`.

## Verification

- Link checker green locally (158 files, 0 failures) and in the
  workflow's own run.
- Census suite green locally against the worktree's compose
  Postgres and in the workflow's run.
- The PR's dispatch run of `docs.yml` proves the job end to end;
  the server workflow also runs on the PR because its own file
  gained the mirror comment.

## Files modified

`.github/workflows/docs.yml` (new),
`.github/workflows/vinga-server.yml` (comment only),
`scripts/check_doc_links.py` (new), `AGENTS.md`,
`.claude/skills/implement-issue/SKILL.md`, `CHANGELOG.md`, this
file.
