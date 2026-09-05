# Watch the upstream wire contract, and declare a supported-firmware floor

Plan for parts 1 and 2 of
[#399](https://github.com/rafacm/vinga/issues/399); part 3 rides #96
and is not this plan's. Implementation notes land in the companion
`2026-09-05-upstream-drift-watch-implementation.md`, one section per
milestone, appended in the change that ticks the milestone here.

## Goal

vinga speaks a wire contract it does not own, read from vendored
upstream sources at pinned commits, and nothing warns when upstream
moves: the currency table in `docs/xiaozhi-notes.md` stales silently,
and the first symptom of drift would be a board that stops conversing
after a firmware update. This plan commits a watch manifest naming
the upstream paths that carry the wire contract beside the SHA each
was last read at, adds a scheduled workflow that diffs those paths
against upstream HEAD and the latest release and opens or updates one
drift-report issue, and records the compatibility-floor ADR that
turns "support different versions" into a bounded, testable promise.

## The issue's decisions, restated

- Part 1 is a committed manifest plus a scheduled GitHub Actions
  workflow: pure git, no judgment in the pipeline, triage stays
  human, and the resolution loop is read, update the notes, bump the
  pinned SHA, adjust implementation and tests only where the wire
  actually moved. The manifest is what keeps the signal from being
  noise, since a firmware release touches dozens of board-support
  files per protocol change.
- The candidate paths are the ones the notes already cite: in
  78/xiaozhi-esp32, `docs/websocket.md`, `main/protocols/`,
  `main/ota.cc`, `main/application.cc`; in
  xinnan-tech/xiaozhi-esp32-server, the manager-api OTA controller
  and device service the activation ceremony was reconstructed from,
  and the xiaozhi-server handshake core.
- Part 2 is the compatibility-floor ADR in the shape of the
  database-floor precedent: which upstream firmware releases vinga
  promises to speak (today, the two observed images), giving every
  drift triage its question (does this move anything inside the
  floor?).
- Part 3 (fleet versions from the observed-device record) is #96's;
  this plan adds no storage and no doctor check.

## Open questions, resolved

**The manifest lives at `docs/upstream-watch.yaml`, and it is the
machine home of the pinned SHAs; the notes' currency table keeps its
prose role and a check holds the two to agreeing.** The currency
table in `docs/xiaozhi-notes.md` carries the two whole-clone SHAs
today as human context (what the maintained sections were read
against, with dates and the observed firmware versions beside them).
The manifest needs the same SHAs in a shape a workflow can read
without parsing prose. Two homes that must agree are one structure
with a bug pending, so: the manifest carries, per upstream
repository, the clone URL, the pinned SHA, the read date, and the
watched paths; the notes' table keeps its sentence-level context and
its SHA cells, and the docs workflow's link-check job gains a small
check (in the shape of `check_doc_links.py`) holding the two to
full agreement in both directions: the set of repositories is
identical (a row missing from either side, or duplicated in
either, is its own failure), each repository's full SHA matches,
and each repository's read date matches the table's "Clone read"
cell, so bumping any cell of one without the other turns the docs
lane red rather than lying quietly. The table is deliberately not
generated from the manifest: a generated region inside a research
note would cross the authority taxonomy (generated references are
`docs/reference/`'s class, and the notes are evidence with prose
context around the cells), and full bidirectional checking makes
disagreement equally impossible without moving the page's class.
The manifest is YAML because the repository's own tooling reads YAML
everywhere else, and it lives under `docs/` because it is a
documentation-process artifact: what it pins is what the
documentation was read against, not anything the server executes.

**The workflow diffs both distances, weekly, and writes one issue.**
`.github/workflows/upstream-drift.yml`, three triggers: `schedule`
(Mondays 06:17 UTC, an off-peak minute so the run does not land on
the hour), `workflow_dispatch` with a `dry_run` input defaulting to
false, and `pull_request` restricted by a paths filter to the
workflow file itself, the manifest and the script, always forced
into dry-run mode. The third trigger exists because GitHub accepts
`workflow_dispatch` only for a workflow already on the default
branch, so a new workflow's pre-merge evidence has to come from a
trigger the PR itself fires; it also means every future PR touching
the watch gets a free end-to-end dry run. Dry-run mode does
everything but write: clone, resolve, diff, build the report, and
print it to the step summary instead of touching the tracker. For each repository in the
manifest: a blobless clone (`--filter=blob:none`, full history, no
checkout) followed by an explicit all-tags fetch
(`git fetch --tags --filter=blob:none`, since a clone only brings
tags reachable from fetched history), resolve `origin/HEAD` and the
latest release tag under a
stated policy: tags matching `v<digits>.<digits>[.<digits>]`
exactly, no prerelease or suffix forms, highest by version sort; a
repository with no qualifying tag skips that half with a line
saying so. Each target is validated before it is diffed: the pinned
commit must be an ancestor of the target
(`git merge-base --is-ancestor`), and a target the pin is ahead of
or divergent from is reported as its own line naming the
relationship and is never diffed backward. Then
`git diff --name-status <pinned>..<target> -- <paths>` and
`git log --oneline <pinned>..<target> -- <paths>` for each
validated target.
Validation comes first: an unparseable manifest or a pin that does
not resolve in its clone fails the run before any diffing. Past
validation, when every diff is empty and every target is a
descendant of its pin, the run ends green and writes nothing; a
behind or divergent target is drift-adjacent news, so its line
makes the report non-empty and an issue is written while the run
itself still succeeds.
When any is not, the report (changed files and commit subjects per
repository and target, plus the manifest's pinned SHAs and the
resolution loop's three steps) becomes the body of one issue with
a stable identity: the exact title "Upstream drift report", the
`upstream-drift` label, and a machine marker comment in the body.
The workflow searches open issues by label and holds candidates to
the title and marker; exactly one match is updated, none means
create (the label created with `--force` first), and more than one
is a refusal that fails the run naming them rather than guessing
which to overwrite. Concurrent runs are serialized by a
workflow-level `concurrency` group with
`cancel-in-progress: false`, so a manual dispatch landing beside
the schedule queues instead of racing the search-then-create
window. The job's `permissions` grant
`issues: write` and `contents: read` and nothing else, and the
permissions alone authorize nothing `gh` consumes: the
issue-management step, and only that step, sets
`GH_TOKEN: ${{ github.token }}`, every `gh` invocation passes
`--repo rafacm/vinga`, and no step traces or prints its environment
(no `set -x`, no `env`). The job's own steps are
`actions/checkout@v7`, the repository's pinned
`astral-sh/setup-uv@v9.0.0` and `uv sync --frozen` (the same
provisioning the docs workflow uses, since the manifest parser runs
through the vinga-server environment), then git and
`gh api`/`gh issue`; the discipline is no additional unpinned or
drift-analysis action, not no action at all.

**Report content is names and subjects, never patch text.** The
issue body lists file paths and commit subjects, which is what
triage needs to decide whether to go read; it deliberately embeds no
diff hunks, both to keep one issue readable across updates and
because the resolution loop's first step is reading the source in a
clone, not reviewing a patch in an issue.

**The ADR rides this plan as M2, and the plan review is asked to
confirm that.** The queue decision (2026-09-05) was that part 2 rides
if the review agrees. The case for riding: the ADR is the drift
report's triage question, so shipping the watch without the floor
ships a signal with no rule to read it against; and the issue's own
text already names the floor's content (the two observed images), so
M2 re-litigates nothing. The ADR,
`docs/adr/2026-09-05-supported-firmware-is-a-declared-floor.md`, in
the database-floor precedent's shape: Context (the wire is not ours;
the two observed images, 2.2.4 factory on the AMOLED-2.16 and 2.4.0
prebuilt on the Touch-LCD-1.54, both observed 2026-08-12/13, are what
the promise is read against today), Decision (vinga promises to speak
the firmware releases observed on boards in the field, enumerated in
the record; today that set is those two; a drift-report triage asks
whether a change moves anything inside the floor; the floor widens
when a new version is observed on a board and the notes are re-read
against it, and narrows only by a recorded decision), Consequences
(bounded support instead of open-ended fear; the enumerated set is
part 3's query target once #96 lands). The stock-firmware promise in
`docs/architecture/product-promises.md` already states the version
target in prose; its section gains the ADR citation in the same
change, which is the promise page working as designed (it cites the
record that owns the reasoning) rather than a promise change.

## Module and artifact layout

- `docs/upstream-watch.yaml` (M1): the manifest. Keys per
  repository: `url`, `pinned`, `read` (date), `paths` (list). A
  header comment carries the resolution loop and the rule for adding
  a path (a path joins when a maintained section of the notes is
  read from it). The firmware repository's four paths are exact
  already (`docs/websocket.md`, `main/protocols/`, `main/ota.cc`,
  `main/application.cc`); the server repository's are descriptive
  today (the manager-api OTA controller and device service, the
  xiaozhi-server handshake core), so resolving them to exact
  repository-relative paths is an explicit M1 prerequisite: the
  milestone clones that repository bloblessly at the pinned SHA,
  locates `OTAController.java`, `DeviceServiceImpl.java` and the
  handshake module by listing the tree rather than by memory, and
  records the resolved paths in the manifest and the implementation
  doc. A candidate that does not exist at the pin is a finding to
  record, not a path to guess.
- `.github/workflows/upstream-drift.yml` (M1): the scheduled
  workflow above. It never parses the manifest itself and does no
  selection logic in shell: it calls `scripts/upstream_watch.py`'s
  `print`, `report` and `decide` subcommands, so exactly one parser
  and one decision site exist.
- `scripts/upstream_watch.py` (M1): one script, one parser, four
  subcommands, run through the vinga-server environment
  (`uv run --project vinga-server`, where PyYAML is already a
  dependency), so no runner-image Python detail is load-bearing and
  hand-rolled YAML parsing is off the table. `check` is the
  agreement check the docs workflow runs (in the shape of
  `check_doc_links.py`, fixed sentences naming the repository that
  disagrees); `report` takes the manifest plus a directory of
  already-fetched clones and builds the whole report (tag selection
  under the stated policy, ancestry validation, the per-target
  diffs, the body with its marker), writing it to a file so
  upstream-controlled paths and subjects travel as file content and
  quoted arguments, never through shell evaluation; `decide` takes
  the report and the JSON of open labeled issues from
  `gh issue list --json` and answers create, update (with the
  number) or refuse (naming the ambiguous matches), so the
  workflow's shell does no selection logic; `print` emits the rows
  the clone step iterates. The workflow is thereby thin: clone,
  `gh issue list`, and three script calls.
- `docs/xiaozhi-notes.md` (M1): the currency section gains one
  paragraph naming the manifest, the workflow and the loop; the
  table itself is unchanged.
- `docs/adr/2026-09-05-supported-firmware-is-a-declared-floor.md`
  (M2) and the promises-page citation (M2).
- `CHANGELOG.md`: an entry per milestone.

## Tests and verification

- `scripts/upstream_watch.py check` runs in the docs workflow
  beside the link checker, and the whole script is held by a
  deterministic unit suite, `tests/unit/test_upstream_watch.py` in
  vinga-server (the precedent is the link checker's own subprocess
  suite from #329): synthetic local git repositories exercise no
  change, changed watched paths, a deleted watched path, a
  qualifying tag behind the pin (reported, not diffed), a divergent
  tag, a missing pin SHA (a refusal naming the repository), and a
  repository with no qualifying tag; `decide` is exercised over
  fixture JSON for zero, one and two candidate issues, the
  two-candidate case a refusal naming both; the agreement check is
  exercised in both directions (the committed state passes; a
  mutated SHA, a mutated read date, a missing row and a duplicated
  row each fail naming the repository); and a report built from a
  synthetic repository whose path and subject carry shell
  metacharacters round-trips byte-identically through the file,
  pinning the no-shell-evaluation property.
- Pre-merge end-to-end evidence is the PR's own dry-run job: the
  `pull_request` trigger fires on the PR because the diff touches
  the workflow, the manifest and the script, and its run proves
  clone, resolve, diff and report construction against live
  upstream without writing anything. A `workflow_dispatch` before
  merge is impossible for a new workflow (the file must be on the
  default branch), which is exactly why the PR trigger exists. What
  live upstream proves is whichever branch is live that week; the
  deterministic branches are the synthetic-repository tests below.
  The first real scheduled run after merge is expected to open the
  drift issue if the watched paths moved since July, and that is
  the feature working, not a surprise; the M1 record says so.
- The census sweeps new files for quoted command spellings; the
  manifest regenerates the standard way when stale.
- Link checker covers the new notes paragraph and the ADR's links.

## Risks

- **The report becomes noise if the paths are too wide.** The
  manifest starts with exactly the issue's candidate list, and the
  header's rule keeps additions tied to maintained sections; the
  one-issue update discipline means even a noisy period is one page,
  not a pile.
- **Upstream renames a watched path.** Only the deletion side is
  dependable: a destination outside the watched pathspec is
  invisible to the diff, so the report shows a delete and nothing
  else, and locating the replacement is a human triage step. The
  manifest header names this as the reason a drift report can
  undercount and why the loop re-reads rather than trusts.
- **Issue-update races or a renamed label.** The workflow creates
  the label if missing (`gh label create --force`), and the
  open-or-update search is by label plus state, so a manually closed
  report stays closed until new drift opens a fresh one, which is
  the wanted behavior (closing the issue is triage saying done).
- **The promise page edit drifting into a promise change.** M2 adds
  a citation to an existing promise section, no semantic edit; the
  plan review is asked to hold it to that.

## Milestones

- [ ] **M1: the manifest, the check, and the drift workflow.**
  `docs/upstream-watch.yaml` with the issue's candidate paths at the
  currency table's pinned SHAs; `scripts/upstream_watch.py`
  with the agreement check wired into the docs workflow and the
  `print` subcommand the drift workflow parses with;
  `.github/workflows/upstream-drift.yml` (schedule plus dispatch,
  blobless clones, both distances, one labeled open-or-update issue,
  `--dry-run` input); the notes paragraph; the changelog entry; the
  census if staled. Design footprint: one parser for the manifest,
  used by the check and the workflow, so the fact has one home; no
  server code. Documentation footprint: `docs/xiaozhi-notes.md`'s
  currency section and `CHANGELOG.md`; the docs index needs no entry
  (the manifest is not a page and the notes route to it).
- [ ] **M2: the floor ADR and its citation.** The ADR above;
  the one-line citation in the stock-firmware promise section;
  the changelog entry; the implementation-doc section. Design
  footprint: none in code. Documentation footprint:
  `docs/adr/` (new record), `docs/architecture/product-promises.md`
  (citation only), `CHANGELOG.md`.

## Plan review round

Backend codex (codex-cli 0.153.0), model `gpt-5.6-sol`, sandbox
read-only, 2026-09-05, against commit b98267f0 of this plan; the
reviewer ran about 6 minutes. Verdict: not ready, on two P1
mechanics; the review raised no objection to the ADR riding as M2,
so it rides, per the queue decision's condition.

1. **P1: the required pre-merge dispatch run cannot be triggered
   for a new workflow.** GitHub accepts `workflow_dispatch` only for
   a workflow already on the default branch. Give the workflow a
   `pull_request` dry-run path or defer end-to-end verification to
   after merge and say so.

   *Resolution*: accepted, first option. The workflow gains a
   paths-filtered `pull_request` trigger forced into dry-run mode,
   which is both the pre-merge evidence and a standing free dry run
   for every future PR touching the watch.

2. **P1: the issue-writing commands have no authentication path.**
   `permissions:` authorizes the token; it does not make `gh`
   consume it. Set `GH_TOKEN` on the issue-management step alone,
   pass `--repo rafacm/vinga` on every `gh` call, and forbid
   printing the environment.

   *Resolution*: accepted in full. `GH_TOKEN` is scoped to the
   issue-management step alone, every `gh` call carries the repo
   flag, and the workflow bans environment tracing.

3. **P2: "no third-party action" conflicts with the declared parser
   runtime.** The vinga-server environment arrives via
   `actions/checkout` and the pinned `astral-sh/setup-uv` plus
   `uv sync --frozen`. Name those steps and narrow the claim to no
   additional unpinned or drift-analysis action.

   *Resolution*: accepted in full. The workflow names checkout, the
   pinned setup-uv and the frozen sync as its provisioning, and the
   claim is narrowed to no additional unpinned or drift-analysis
   action.

4. **P2: the server-side watched paths remain unresolved.** The
   plan repeats descriptive candidates instead of
   repository-relative paths, and the vendor clones are absent.
   List the exact paths or make confirming and recording them an
   explicit M1 prerequisite.

   *Resolution*: accepted, second option. Resolving the server-side
   paths from a blobless clone at the pinned SHA is an explicit M1
   prerequisite, recorded in the manifest and the implementation
   doc, with a missing candidate recorded rather than guessed.

5. **P2: the manifest and notes are two manually maintained
   structures whose dates can silently disagree.** Only the SHA was
   checked. Render the table from the manifest, or check repository
   identity, full SHA and read date in both directions, including
   missing and duplicate rows.

   *Resolution*: accepted, second option, strengthened as asked:
   the check covers repository identity, full SHA and read date,
   both directions, with missing and duplicate rows as their own
   failures; the generated-table alternative is declined with the
   authority-taxonomy reason stated in place.

6. **P2: highest `v*` tag is not a reliable latest release.** It
   can select prereleases or unrelated tags, and nothing defines
   behavior when the pin is ahead of or divergent from the tag.
   Define tag syntax and prerelease policy, validate ancestry, and
   report or skip targets behind the pin rather than diffing
   backward.

   *Resolution*: accepted in full. The tag policy is exact
   (`v<digits>.<digits>[.<digits>]`, no prerelease forms), ancestry
   is validated per target, and a behind-or-divergent target is a
   reported line, never a backward diff.

7. **P2: open-or-update can overwrite the wrong issue and race into
   duplicates.** A label alone is not an identity, and concurrent
   runs can both create. Use a stable identity (exact title plus a
   machine marker), refuse ambiguous matches, and serialize with
   workflow concurrency, cancellation disabled.

   *Resolution*: accepted in full. Identity is exact title plus
   label plus a body marker, ambiguity is a named refusal, and a
   concurrency group with cancellation disabled serializes runs.

8. **P2: the rename-behavior claim is false.** `git diff` never
   reports untracked files and an out-of-scope destination is
   invisible; only the deletion side is dependable. Say so, and
   leave locating the replacement to triage.

   *Resolution*: accepted in full; the risk text now claims only
   the deletion side and hands the replacement to triage.

9. **P2: the verification is nondeterministic and does not exercise
   issue management.** A dry run against live upstream proves one
   branch at best. Add deterministic tests over synthetic local git
   repositories (no change, changed, deleted, tag behind pin,
   missing pin, no tags), test the issue-selection and
   create-or-update decision separately from the network, and build
   the report through files and quoted arguments so
   upstream-controlled paths and subjects are never evaluated as
   shell.

   *Resolution*: accepted in full. The report and decision logic
   move into the script (`report` and `decide` subcommands), the
   deterministic synthetic-repository suite covers the named
   branches plus the metacharacter round trip, `decide` is tested
   over fixture issue JSON including the ambiguous case, and the
   workflow's shell is reduced to clone, list and three script
   calls.

### Delta re-review

Backend codex (codex-cli 0.153.0), model `gpt-5.6-terra`, sandbox
read-only, 2026-09-05, against commit 088ef320; a few minutes.
Verdict: all nine resolutions verified as delivered; ready after two
P2 amendments and one P3, all inside the amended territory.

1. **P2: the latest-release lookup may not see all tags.** A clone
   fetches tags reachable from fetched history, not necessarily a
   release branch's. Require an explicit all-tags fetch before tag
   selection, keeping the blob filter.

   *Resolution*: accepted; the clone step adds
   `git fetch --tags --filter=blob:none` before selection.

2. **P2: the no-diff success rule conflicts with the missing-pin
   refusal.** Define precedence: manifest and pin-resolution
   failures exit nonzero before the no-diff rule, and say whether
   behind or divergent targets appear in a successful report or
   fail the run.

   *Resolution*: accepted; precedence is now stated in the body:
   validation failures (unparseable manifest, a pin that does not
   resolve in its clone) fail the run before any diffing; a behind
   or divergent target is drift-adjacent news, so its line makes
   the report non-empty and an issue is written, and the run still
   succeeds.

3. **P3: `print` versus `--print` is inconsistent.**

   *Resolution*: accepted; `print` the subcommand, everywhere.
