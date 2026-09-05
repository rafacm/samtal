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
check (`scripts/check_upstream_watch.py`, stdlib only, in the shape
of `check_doc_links.py`) asserting the SHA printed in the notes'
table equals the manifest's for each repository, so bumping one
without the other turns the docs lane red rather than lying quietly.
The manifest is YAML because the repository's own tooling reads YAML
everywhere else, and it lives under `docs/` because it is a
documentation-process artifact: what it pins is what the
documentation was read against, not anything the server executes.

**The workflow diffs both distances, weekly, and writes one issue.**
`.github/workflows/upstream-drift.yml`, `schedule` (Mondays 06:17
UTC, an off-peak minute so the run does not land on the hour) plus
`workflow_dispatch` for on-demand runs. For each repository in the
manifest: a blobless clone (`--filter=blob:none`, full history, no
checkout), resolve `origin/HEAD` and the latest release tag (highest
`v*` tag by version sort; the firmware repo tags releases, and a
repository with no tag skips that half with a line saying so), then
`git diff --name-status <pinned>..<target> -- <paths>` and
`git log --oneline <pinned>..<target> -- <paths>` for each target.
When every diff is empty, the run ends green and writes nothing.
When any is not, the report (changed files and commit subjects per
repository and target, plus the manifest's pinned SHAs and the
resolution loop's three steps) becomes the body of one issue: the
workflow searches for an open issue labeled `upstream-drift`,
updates its body if one exists, creates it (with the label) if none
does, and never opens a second. The job's `permissions` grant
`issues: write` and `contents: read` and nothing else. All of it is
`gh api`/`gh issue` plus git; no third-party action, matching the
repository's pinned-tool discipline.

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
  read from it).
- `.github/workflows/upstream-drift.yml` (M1): the scheduled
  workflow above. It never parses the manifest itself: it calls
  `scripts/check_upstream_watch.py --print`, so exactly one parser
  exists and the workflow reads shell-friendly rows.
- `scripts/check_upstream_watch.py` (M1): the manifest's one parser,
  run through the vinga-server environment
  (`uv run --project vinga-server`, where PyYAML is already a
  dependency), in both workflows, so no runner-image Python detail
  is load-bearing and hand-rolled YAML parsing is off the table.
  Two modes: the default checks notes-table agreement (in the shape
  of `check_doc_links.py`, fixed sentences naming the repository
  that disagrees); `--print` emits `repo url pinned path` rows for
  the drift workflow.
- `docs/xiaozhi-notes.md` (M1): the currency section gains one
  paragraph naming the manifest, the workflow and the loop; the
  table itself is unchanged.
- `docs/adr/2026-09-05-supported-firmware-is-a-declared-floor.md`
  (M2) and the promises-page citation (M2).
- `CHANGELOG.md`: an entry per milestone.

## Tests and verification

- `scripts/check_upstream_watch.py` runs in the docs workflow beside
  the link checker, and its agreement check is exercised in both
  directions locally before the PR: the committed state passes, and
  a mutated SHA in a copied-aside manifest fails naming the
  repository.
- The drift workflow cannot run on a PR (it is schedule and dispatch
  only), so M1's PR carries a `workflow_dispatch` run against the
  branch as evidence: one honest full run, expected to report drift
  (the pins are from July and upstream has moved), whose issue
  creation is pointed at a dry-run mode for the evidence run
  (`--dry-run` input: print the would-be body to the step summary
  instead of touching the tracker), so the evidence run writes no
  issue while proving clone, resolve, diff and report end to end.
  The first real scheduled run after merge is expected to open the
  drift issue, and that is the feature working, not a surprise;
  the M1 record says so.
- The census sweeps new files for quoted command spellings; the
  manifest regenerates the standard way when stale.
- Link checker covers the new notes paragraph and the ADR's links.

## Risks

- **The report becomes noise if the paths are too wide.** The
  manifest starts with exactly the issue's candidate list, and the
  header's rule keeps additions tied to maintained sections; the
  one-issue update discipline means even a noisy period is one page,
  not a pile.
- **Upstream renames a watched path.** `git diff --name-status`
  against a path that no longer exists still reports the deletion
  side; the report shows the rename as a delete plus untracked new
  path, and triage's read-the-source step catches it. The manifest
  header names this as the reason a drift report can undercount and
  why the loop re-reads rather than trusts.
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
  currency table's pinned SHAs; `scripts/check_upstream_watch.py`
  with the agreement check wired into the docs workflow and the
  `--print` mode the drift workflow parses with;
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
