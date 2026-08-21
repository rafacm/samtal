---
name: external-review
description: Run an adversarial external review (codex with sol or terra by stakes; claude CLI as the fallback when the codex quota is exhausted) of a committed plan or a PR diff, record the findings, and drive per-finding amendments. Use before implementing a plan and before merging any milestone PR.
---

# External review

An independent model reads what this session produced and tries to
break it. Two modes: a plan review before any code exists, and a PR
review of a milestone's diff before it merges. Both use the codex
CLI with the prompts in this skill's directory.

## Which model, by stakes

Two tiers, decided per round (maintainer decision, 2026-08-19):

- `gpt-5.6-sol`, the default: every plan review, and every PR whose
  diff changes behavior. Sol's record here is P1s that need
  whole-repo rule synthesis or concurrency reasoning; do not trade
  those away for speed.
- `gpt-5.6-terra`, the fast tier: low-stakes rounds only, meaning a
  documentation-only diff, a mechanical follow-up (renames, moves,
  pin updates with no logic change), or a re-review of fixes whose
  original round was Sol's. When in doubt, it is not low-stakes.

`run-pr-review.sh` reads the model from `REVIEW_MODEL` (default
sol) and stamps whichever ran into the provenance header.

### Fallback when the codex quota is exhausted

When the ChatGPT plan behind codex runs out of weekly quota, set
`REVIEW_BACKEND=claude` and the script runs the claude CLI instead
(`claude -p`, default model `claude-opus-5`, read-only tools
enforced by an explicit deny list; an allowlist alone denies
nothing). This is a same-vendor review, so the independence property
is weaker: the reviewer still has fresh eyes and no session
context, but shares training and blind spots with the model that
wrote the code. Treat it as the interim tier, note the backend in
the recorded round, and return to codex when the quota resets.

## Mechanics that are not obvious

- Always `codex exec -m <model> --sandbox read-only -` with the
  prompt on stdin. Never `codex review` with a custom prompt: it
  ignores the prompt.
- The claude equivalent, used for manual (plan-mode) runs on the
  fallback backend, is the exact invocation in `run-pr-review.sh`'s
  claude arm: copy it verbatim, including `--setting-sources ""`,
  `--strict-mcp-config` and the `--disallowedTools` list. The deny
  list is what makes the run read-only; `--allowedTools` alone
  restricts nothing.
- Run it in the background from the worktree under review. Sol takes
  10 to 25 minutes and looks stuck; stderr shows file-reading
  activity, and only the final answer reaches stdout:
  `codex exec -m gpt-5.6-sol --sandbox read-only - < prompt.md > out.txt 2> err.txt`
- The reviewer has no network (codex by sandbox, claude by its tool
  allowlist). Paste the GitHub issue body into the prompt and name
  every repository file the reviewer should read; it can only see
  what is on disk in the worktree.
- Do not edit the worktree while a review runs: the reviewer reads
  files live, and a mid-run edit means it reviews a mixture.

The recurring lenses the reviews of the 2026-08-14 batch applied
(no-leak sentinels, pin-before-reshape, closed token sets at real
decision sites, honest seams, tooling-backed inventories) are
written into the `implement-issue` skill's Step 1; when assembling
a review prompt, name any of them the diff's territory touches, so
the reviewer confirms rather than discovers.

## Plan mode

1. The plan must already be committed on its feature branch.
2. Assemble the prompt: start from `plan-review-prompt.md` in this
   skill's directory, fill the placeholders, and append the issue
   body. Keep the reading list explicit and complete: the plan, the
   prior plans and implementation docs it builds on, the code it
   touches, AGENTS.md, `docs/architecture/principles.md`, the CI
   workflow, and the test assets it converts.
3. Run codex in the background; read stdout when it completes.
4. Record the findings as received, condensed but faithful, in a
   "Plan review round" section of the plan (its own commit), noting
   codex version, model, date, and the reviewed commit hash.
5. Address each finding with its own amendment commit, appending a
   `*Resolution*` note under the recorded finding. A finding you
   reject gets a resolution note saying why, never silence.

## PR mode

Use `run-pr-review.sh` in this skill's directory, which is
self-posting: it generates the diff, runs codex, and chains the
result into `gh pr comment` with a provenance header, so the review
lands on the PR even if the driving session dies mid-run.

```
run-pr-review.sh <worktree> <base-ref> <pr-number> "<pr-title>" "<context sentence>"
```

The context sentence names the milestone and the governing plan
(for example: "milestone 2 of docs/plans/2026-08-11-rest-api.md").
After the comment lands:

1. Fix every finding with its own commit; delegate to the milestone
   subagent that wrote the code when one exists, since it has the
   context.
2. Record the round in the implementation doc, in the house style
   of the existing "PR review round" sections.
3. Reply on the PR with per-finding resolutions and commit hashes,
   update the PR description, and wait for CI to go green again.
4. If a finding invalidates a claim already made on the PR, state
   the correction there transparently.

## Repository specifics

Every `gh` call passes `--repo rafacm/vinga` (worktrees under
`vendor/` make repository inference dangerous; see AGENTS.md).
Review comments and replies are never hard-wrapped: GitHub renders
comment newlines as line breaks, so one line per paragraph.
