---
name: implement-issue
description: Implement a GitHub issue end to end with the vinga plan/review/implement pipeline - committed plan, external plan review, per-milestone subagents in stacked worktrees, a PR per milestone with its own external review round, rebase merges with stacked-PR retargeting. Use for any issue whose settled decisions are ready to be built.
---

# Implement an issue end to end

The pipeline that shipped #86 (PRs #95/#100/#99/#102) and #101.
Milestones and stacked PRs are not a token-saving device; they exist
because a bounded, single-purpose diff gets reviewed instead of
skimmed, because this repository publishes an image on every push to
`main` so every merge must be a valid release, because the milestone
checklist plus the implementation doc lets a fresh session resume
from the repository alone, and because a per-PR CI run and review
round attaches findings to the change that caused them. Stacking
lets milestone N+1 proceed while N sits in review; its price is the
retargeting discipline under "Merging".

## Preflight

- `git branch --show-current` must say `main`; `git pull --rebase`;
  stop and ask on any problem with either.
- `export GH_REPO=rafacm/vinga`, and still pass
  `--repo rafacm/vinga` on every `gh` call (AGENTS.md says why the
  flag stays even with the export).
- All branch work happens in `git worktree`s under the session
  scratchpad, never in the main checkout: another session may hold
  it, and worktrees are what let milestones proceed in parallel.
- Read in full before planning: the issue (its decisions are
  settled and not re-litigated; its open questions are the plan's
  to resolve), AGENTS.md, `docs/architecture/product-promises.md`
  and `docs/architecture/guidelines.md`, the plans and
  implementation docs of the work this builds on, and the code the
  issue touches.

## Step 1: the plan

`docs/plans/YYYY-MM-DD-<slug>.md` (today's date) on a
`feature/<slug>` branch, in the house style of the existing plans:
goal; the issue's decisions restated, not re-litigated; the issue's
open questions resolved, each with its reasons; the smaller design
decisions the issue leaves open, decided with reasons; module
layout; tests (reuse existing test assets, do not restate them);
risks with mitigations; milestones as an annotated checklist that
doubles as the milestone descriptions; the companion
`-implementation.md` convention named in the goal.

The plan decides the PR structure. The binding constraint: the
workflow publishes an image on every push to `main`, so every merge
in the stack must leave `main` releasable, with no state that
violates a settled decision (two co-equal write paths, a mandatory
variable CI does not set). Cut milestones so behavior changes sit
alone in review. Commit the plan.

Each milestone also names its design footprint: the modules it
deepens, the seams it adds, and for any new module the one sentence
saying what its callers stop having to know. A milestone that can
only say it puts a layer beside an existing module has not been
designed yet. The vocabulary and the worked examples are in
`docs/architecture/design-guide.md`.

Each milestone likewise names its documentation footprint: the
hand-maintained pages whose description of current behavior the
milestone falsifies (the root README's status and feature claims,
maintained maps and guides under `docs/`, board guides), beyond
what the generated-reference drift checks already catch. Name
pages by their role, and confirm where that role currently lives
before writing the plan: the authority taxonomy in `docs/README.md`
says which class a page belongs to and therefore what it may claim,
and `docs/architecture/README.md` routes the architecture corpus
by reader question. A milestone whose behavior change stales no
documentation says so explicitly rather than leaving the footprint
implied.

### The standing review lenses

The external reviews of the 2026-08-14 batch applied the same
lenses round after round; a plan that pre-answers them merges with
empty rounds instead of multi-finding ones. Address each where the
issue's territory touches it, in the plan and again in the
subagent brief:

- **No-leak, at every retained surface.** No secret, far-side, or
  untrusted bytes in any message, field, argument, exception text,
  or `__cause__`/`__context__` chain that reaches logs, events,
  CLI/stderr, or API bodies. Render exception classes, never their
  words; build the sanitized error in the `except` arm and raise it
  after the block. Plans name the sentinel tests (plant a
  credential-shaped value; assert absence from sentence, args,
  fields, records in both log formats, and any attached consumer).
  The full model is
  `docs/architecture/observability-surfaces.md`.
- **Pin before reshaping.** A behavior-preserving move is proven by
  characterization pins (`record.msg` and typed `record.args`, not
  normalized renderings) committed green before the move and
  byte-unchanged after; "existing tests pass" alone is not proof
  when they pin only a fraction of the surface.
- **Closed sets mapped to decision sites.** Reason tokens and event
  fields are literals from declared closed sets, chosen where the
  code actually classifies, by exception type (recursing into
  groups), never message text; verify each token has a reachable
  decision site in the code that exists, not the code as imagined.
- **Honest seams.** Injectable dependencies compare `is not None`,
  never truthiness; a seam's default-construction policy (timeout,
  retries) gets its own pin, since injected-client tests cannot
  prove it.
- **Inventories by tooling, not memory.** Site counts, migration
  lists, and "nothing else touches X" claims are backed by grep or
  an AST check named in the plan's verification; after any rebase,
  recorded hashes and counts are refreshed.

## Step 2: external plan review

Use the `external-review` skill in plan mode. Record the findings
as received in a "Plan review round" section (own commit), then
address each finding with its own amendment commit, appending a
`*Resolution*` note under the recorded finding.

## Step 3: implement, one subagent per milestone

One general-purpose subagent with model opus per milestone, each in
its own scratchpad worktree, on a branch stacked on the previous
milestone's branch. The subagent's brief states, verbatim where
possible:

- The reviewed plan is the authoritative spec, including its review
  round; where the brief and the plan disagree, the plan wins.
- uv only, never pip; everything runs from `vinga-server/`.
- Small commits: one logical change, imperative ~50-char title, a
  body explaining what and why, ending with the Claude trailer.
- No em-dashes anywhere. `config.example.yaml` updates in the same
  change as any server-section schema change. `CHANGELOG.md`
  date-based entries. The implementation-doc section is written in
  the change that ticks its milestone, ticked with "PR TBD" until
  the PR exists.
- The plan's documentation footprint for this milestone lands in
  the same milestone as the behavior it describes: update the page
  that owns the fact and leave summaries linking to it, never a
  second copy. Generated references change only through their
  generators. If a footprint page is not where the plan said, find
  its current home through the authority taxonomy in
  `docs/README.md` rather than editing a compatibility stub.
- Honest verification only: `uv run ruff check .`,
  `uv run pytest tests/unit -q`,
  `uv run pytest tests/integration -q`, and the doc drift checks,
  all from `vinga-server/`; anything unverifiable locally (the
  image, the smoke lane) stated plainly, never claimed.
- `PYTHONDONTWRITEBYTECODE=1` outside pytest (the stale-bytecode
  trap in AGENTS.md).
- No pushes and no GitHub commands from subagents.
- A documentation move can stale the command-spellings census,
  which scans every tracked file. The `docs` workflow now runs the
  census on the changes the server workflow ignores (the hole that
  once turned `main`'s unit lane red with no run going red,
  2026-08-27), but a PR should arrive synchronized rather than
  lean on CI to say so: after moving or renaming documentation,
  run `tests/unit/test_command_spellings.py`; when stale,
  regenerate the manifest with
  `uv run python -m tests.unit.test_command_spellings`, never by
  hand.

If a subagent dies mid-run (machine sleep), resume it with a status
recap verified from `git log`, not from memory; its commits
survive. If an agent must be stopped, freeze its branch explicitly
before touching the branch yourself.

## Step 4: a PR per milestone

Rebase the milestone branch onto its current base first, push, then
`gh pr create --repo rafacm/vinga`. Title: imperative verb, colon,
deliverables. Bodies and comments are NEVER hard-wrapped (GitHub's
breaks extension turns every newline into a line break): one line
per paragraph and per list item. The body covers what and why,
decisions and recorded deviations, and a Verification section as a
task list with honestly checked and unchecked boxes; an unchecked
box carries a note saying why it is not yet verifiable. Substitute
the PR number into the plan's milestone tick once the PR exists.
Wait for CI green before the review round. Two CI shapes to know:
a docs-only diff outside `docs/reference/` runs the `docs` workflow
(link check plus the command-spellings census), not the server
lanes, and its green is the check the PR waits for; and a
`pull_request` event that never registers a run is
a GitHub failure mode this repository has seen a whole day of
(2026-08-27): dispatch the workflow against the branch
(`gh workflow run vinga-server.yml --repo rafacm/vinga
--ref <branch>`, which runs everything but publishing) and link
the run on the PR as the evidence.

## Step 5: external PR review

Use the `external-review` skill in PR mode (the self-posting
script). Fix every finding with its own commit, delegating to the
milestone's subagent, which has the context. Record the round in
the implementation doc, reply on the PR with per-finding
resolutions and commit hashes, update the PR description, and wait
for CI again. A finding that invalidates a claim gets a transparent
correction on the PR.

## Merging

Merge each PR once its review round is fully resolved and CI is
green with no blocker; stop and ask only when something is red or
contentious. The stacked-PR trap, learned the hard way in #86: a
rebase merge that deletes the base branch auto-closes stacked
children unrecoverably. Retarget every child PR to `main` BEFORE
merging its parent, and rebase children with `git rebase --onto`
after each merge. When both sides of a rebase regenerated the same
generated artifact (a census manifest, a committed reference), the
textual merge is a state neither side produced: regenerate on the
rebased tree and prove it green before pushing.

## Finishing

Update the session's resume-point memory note as milestones land,
not only at the end. Finish with a summary: per-milestone status,
every review round's findings and resolutions, verification
results, and anything left open.
