You are reviewing an implementation plan before any code is written. You are running read-only in a checkout of the vinga repository, on branch __BRANCH__. You have no network access; everything you need is in this prompt or in the repository files named below.

## The task under review

The plan at `__PLAN_PATH__` implements GitHub issue #__ISSUE__, whose full body is pasted at the end of this prompt. The issue's numbered decisions are settled and are NOT up for review; the plan's job is to make them concrete and to resolve the issue's open questions plus the smaller design decisions the issue leaves to the plan. Review the plan for: contradictions with the issue's decisions, contradictions with the existing code, designs that cannot be implemented as written, missing work the plan's milestones would need but do not name, unsafe or leaky paths (this codebase has a strict no-leak contract for secrets), test plans that would not catch the failures they claim to, and operational traps for a running deployment that upgrades.

Apply the deletion test to every new module the plan proposes: if that module did not exist and its body were inlined into its only caller, would the caller get harder to read? A module whose stated job is to forward arguments to another one is a finding, and so is a split justified by a file's length rather than by a second responsibility in it. The repository's own statement of this is `docs/architecture/design-guide.md`.

## What to read

Read the plan in full first: `__PLAN_PATH__`.

Then the substrate it builds on:

__READING_LIST__

## Output format

Number every finding. For each: a priority (P1 blocks implementation, P2 should be amended before implementation, P3 worth noting), a one-line title, the evidence (file and section or line), and what the plan should say instead. End with a verdict: ready to implement as is, ready after the P1/P2 amendments, or not ready. Do not restate the plan back; report only findings. Be concrete and adversarial; a finding that survives scrutiny is worth ten observations.

## Issue #__ISSUE__, full body

__ISSUE_BODY__
