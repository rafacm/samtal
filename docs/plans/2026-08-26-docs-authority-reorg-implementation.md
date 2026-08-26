# Reorganize the documentation by authority: implementation

Companion to [the plan](2026-08-26-docs-authority-reorg.md). One
section per milestone, appended in the same change that ticks the
milestone's checkbox; each records what was done, deviations from
the plan, resolutions of open questions, and discoveries. A
milestone with no deviations says so explicitly.

## M1: One landing page per corpus, and the walkthrough moves out

PR #321.

### What landed

Six commits, in the order the tree needed them.

- **The diagram directories move.** `git mv` of
  `docs/architecture/excalidraw` and `docs/architecture/plantuml`
  under a new `docs/architecture/diagrams/`, with the callers of the
  old paths following in the same commit: the root README's overview
  image, `docs/README.md`'s diagram paragraph, and the architecture
  README's seven relative references (the census's count, confirmed
  exactly).
- **`docs/system-overview.md`.** The whole teaching walkthrough (The
  overview, One conversation turn in detail with both flows, and What
  this diagram leaves out) cut and pasted out of the architecture
  README, plus a purpose introduction and an On this page section.
  Its three current callers repoint in the same commit: the root
  README's pointer under the overview image, the regression suite's
  teaching-tour link, and `glossary.md:7`.
- **`docs/architecture/diagrams/README.md`.** One index over both
  tool directories, a section per question: the system at a glance,
  what leaves the host, one turn in detail, the same turn in
  sequence, the barge-in decision. It states outright that the two
  files named `vinga-architecture-overview` answer different
  questions, the Excalidraw one being the friendly picture the root
  README leads with and the PlantUML one colouring providers by
  declared egress.
- **`docs/architecture/README.md` recut as the router.** Five reader
  questions, each routing to the pages that answer it as they stand
  today: designing a feature or deciding direction (`principles.md`,
  `pipeline-ownership.md`, `adr/`), splitting a file or naming an
  interface (`design-guide.md`), adding a command (`cli-guide.md`,
  `reference/cli.md`), placing a datum
  (`observability-surfaces.md`, `reference/events.md`,
  `reference/conversations-schema.md`), and understanding a
  conversation end to end (`system-overview.md`, `diagrams/`,
  `xiaozhi-notes.md`). No On this page section: it is a navigation
  page.
- **`docs/README.md` declares the taxonomy.** The seven classes, the
  closed-set statement, central classification by directory for
  `adr/`, `plans/` and `features/` and page by page for everything
  else, index pages called out as routing rather than claiming, and
  audience restated in the opening paragraph as a routing concern
  that never settles authority. The Architecture section collapses to
  one pointer, the diagram paragraph keeps its content on the new
  paths, and Start here gains `system-overview.md`.
- **`CHANGELOG.md`.** Four bullets under the existing 2026-08-26
  `### Changed` section, one per thing a reader of the documentation
  now finds moved.

### What changed inside the moves, and what did not

The plan's pin-before-reshaping lens asks for this explicitly.

- The walkthrough's prose is byte-identical to what stood in the
  architecture README, verified by diffing the moved block against
  `git show` of the pre-move file. Six link targets changed because
  the page moved one directory up, and with one of them the visible
  label changed too, since the label was itself a path
  (`` `diagrams/plantuml/` `` became
  `` `architecture/diagrams/plantuml/` ``).
- New prose in `system-overview.md`: the purpose introduction and the
  On this page list. Nothing else was added, and the section heading
  "The overview" was left as it was rather than reworded to sit
  better under the new title.
- The Excalidraw scene-sync note moved into the diagrams index nearly
  verbatim. Two edits: the clause introducing "the hand-drawn pair
  below" is gone, because the pair is no longer on that page, and
  "That last part is the catch" became "That is the catch" for the
  same reason.
- `diagrams/plantuml/README.md` changed by exactly one line, the
  `cd` in its render command.
- The 14 moved files are renames; 13 are 100% similarity and the
  PlantUML README is 98%, for that `cd`.

### Deviations from the plan

Three, all in M1's own shape rather than its scope.

1. **The router carries five reader questions, not the four the
   milestone lists.** "Splitting a file, adding a layer, or naming an
   interface" is separate from "designing a feature or deciding
   direction", because `design-guide.md` says in its own words that
   it is read at that moment, and folding it under direction-setting
   would have made one section route to four pages while another
   routed to one. The plan's list is prefixed "for example", so this
   is a choice inside its licence rather than against it.
2. **`docs/README.md`'s Start here gains a `system-overview.md`
   bullet**, which the milestone did not name. The taxonomy lists the
   page, but the taxonomy classifies rather than routes, and the
   global index would otherwise be the one place a reader could not
   find the walkthrough from.
3. **The changelog entry is four bullets rather than one.** Design
   decision 13 asks for a dated `### Changed` entry per milestone;
   four independently discoverable changes landed, and one bullet
   naming all of them would have been a paragraph pretending to be an
   entry.

Everything else is as planned: the promises and guidelines are not
split here, `principles.md`, `pipeline-ownership.md`, `cli-guide.md`,
`observability-surfaces.md`, `concepts.md` and `xiaozhi-notes.md` are
classified as they exist today and otherwise untouched, and no
document that a later milestone creates is mentioned anywhere.

### Discoveries

- **The old architecture README's title was the argument for doing
  the move and the recut in one milestone.** It was called
  "Architecture diagrams" and opened by listing which three of its
  entries were not diagrams. Moving the diagram directories out
  without recutting the page would have left a page named after the
  thing it no longer held.
- **A rename-only claim survives contact with exactly one file.**
  `plantuml/README.md` carries the `cd` a reader types before
  rendering, so the pure-`git mv` claim in the plan is true of the
  diagram assets and one line short of true for the directory's own
  README. Recorded here rather than left for a reviewer to notice in
  the similarity index.
- **Every remaining match of an old path is in a dated record.** The
  end-state search over `README.md`, `AGENTS.md`, `CHANGELOG.md`,
  `docs/` and `.claude/` for `architecture/excalidraw`,
  `architecture/plantuml` and links to the architecture README finds:
  two changelog entries from the day the diagrams were added and one
  from the day the walkthrough was written (historical, and the
  changelog is not rewritten); five lines of this reorganization's
  own plan describing the target structure (historical by the same
  rule); the diagrams index's own in-directory spellings and the
  three current pages that link the architecture README as an index
  rather than as a walkthrough (current, and correct). No current
  caller points at a moved path.

### Verification

- The scratchpad link-and-anchor script, run before every commit that
  moved a link and last on the finished branch:
  `checked 156 files, 0 failures`.
- `git diff -M --summary` against `main`: fourteen renames, thirteen
  at 100% and `plantuml/README.md` at 98%.
- Source review of every changed or new page: heading levels, link
  targets, and the On this page anchors, the last of which the script
  resolves as heading anchors rather than being taken on trust.
- No test lane was run and none was owed: this milestone touches no
  file under `vinga-server/`, `docs/reference/` or `.github/`.

### PR review round

External review of PR #321 (backend codex 0.149.1, model
`gpt-5.6-terra`, 2026-08-26, self-posted). One finding:

1. **P2: the taxonomy left the PlantUML guide unclassified.** The
   maintained-maps entry linked `architecture/diagrams/` only
   through its index, while the plan's classification rule reserves
   by-directory classification for homogeneous directories, leaving
   the moved `diagrams/plantuml/README.md` (current rendering and
   drift guidance) covered by nothing. *Resolution:* the entry now
   classifies the whole `architecture/diagrams/` tree explicitly:
   the index, each tool directory's authoring guide, and the
   diagram sources and renders they describe.

Verdict: mergeable after the listed fix.

## M2: Promises split from guidelines, and pipeline ownership retires

PR TBD.

### What landed

Six commits, each a page or a class of caller.

- **`docs/architecture/product-promises.md`.** A non-normative
  introduction (what a promise is, that promises outrank the
  guidelines, how one changes, and an explicit line saying the
  introduction is not itself a promise), then the three promises. The
  stock-firmware floor and the first-class local deployment moved
  unrewritten; the database promise was rewritten per design decision
  4. No On this page section: three second-level sections is under
  the policy's threshold, as design decision 11 anticipated.
- **`docs/architecture/guidelines.md`.** Identity as the framing
  preamble, then thin device and smart server, the consolidated
  hardware-edge guideline with three subrules, decision reasons
  recast around ownership, and the framework-versus-own-semantics
  guideline with a Reconsider when. An On this page section after the
  introduction, per the policy.
- **`docs/architecture/principles.md`** reduced to a title, one
  paragraph and two links, and
  **`docs/architecture/pipeline-ownership.md`** deleted, in one
  commit.
- **The callers.** AGENTS.md, `docs/README.md`,
  `docs/architecture/README.md`, `docs/concepts.md`,
  `docs/adr/README.md`, the two skills, and the five in-guide links.
- **`CHANGELOG.md`.** Two bullets under the existing 2026-08-26
  `### Changed` section.
- **This section and the milestone tick.**

### What changed inside the moves, and what did not

The plan's pin-before-reshaping lens asks for this explicitly.

- **The stock-firmware promise is byte-identical** to the text that
  stood under `principles.md`, its heading promoted from `###` to
  `##` and nothing else.
- **The first-class local promise is byte-identical** on the same
  terms.
- **Thin device, smart server is byte-identical** in its new home on
  the guidelines page, heading promoted.
- **Identity is byte-identical**, heading unchanged at `##`, with one
  sentence added after it saying why it opens the page rather than
  sitting among the guidelines.
- **The litmus table moved intact**, cell for cell, together with the
  paragraph introducing it and the barge-in example and
  counterexample around it.
- **The `ConversationBackend` counterexample moved intact**, as did
  the per-runtime-configuration example beside it and the
  lowest-common-denominator paragraph above them.
- **The frame-serializer example and the OpenAI Realtime
  counterexample moved intact.**
- **The shared-bucket and owned-bucket paragraphs moved nearly
  verbatim** from `pipeline-ownership.md`. Two changes: the
  cross-reference "the decision-sites principle
  (`principles.md`)" became "the decision-reasons guideline above",
  because the target is now a section of the same page; and the
  standing-decision framing that opened that page ("The standing
  decision this inventory serves is recorded on issue #84 ... not
  now") was rewritten into a paragraph that says the same thing and
  adds why the measurements are not repeated here.
- **The reopen triggers moved verbatim** into Reconsider when, with
  one clause changed: "the runtimes-are-siblings principle" became
  "per the sibling-runtimes subrule above".

Changes beyond mechanical adaptation, listed so a reviewer does not
have to find them:

1. **The database promise is rewritten**, which design decision 4
   asks for. Its letter is the same, with one accuracy correction:
   the old text said best-effort forward-only upgrades are what "the
   upgrade tests prove", and those tests were deleted with the chain
   they were about (the ADR's 2026-08-23 addendum retracts that
   Consequences half by name). The new text credits the
   wheel-migration check, which is what actually still runs. The
   operational floor, the example and the counterexample are all new
   prose, per the decision.
2. **The consolidated hardware-edge guideline gained a framing
   sentence** ("Three subrules follow from that, and they are one
   guideline because each of them is unsafe to apply without the
   other two") and a one-line lead for the first subrule. Its three
   ADR citations and three issue-#84 citations collapsed to one of
   each at the foot of the guideline.
3. **Decision ownership is recast**, per design decision 4 of the
   issue's restated decisions. The example and counterexample are
   verbatim; the surrounding prose is new and says what the recast
   requires: a vinga-owned decision emits a reason drawn from a
   closed set (one event variant per reason, enumerable in
   `reference/events.md`), a runtime-owned decision is diagnosed with
   what that runtime supplies and vinga's job is to expose it. The
   two ADR citations (json-logs, content/telemetry separation) are
   kept, in the same sentences they were in.
4. **The framework guideline gained an example and a
   counterexample**, which `pipeline-ownership.md` never had and the
   house idiom requires. Both are written to be applicable rather
   than evidential: taking VAD and endpointing wiring from a
   framework against the same component deciding whether a reply is
   cancelled, and reaching for a framework on total line count
   without asking which bucket grew.
5. **Two "never" phrases were kept against the prefer-and-normally
   rule**: "it is never wrapped as one more provider" and "vinga
   never defines a universal interface all runtimes must fit", both
   inside text moved intact from the sibling-runtimes principle.
   They are the operative statement of an accepted ADR, and
   rewriting them would have been a wording change inside a move the
   plan asked to be faithful. Flagged here rather than made
   silently.

### Deviations from the plan

Two, both additive to what M2 names.

1. **`docs/README.md` gets a third touch beyond its two
   classification rows.** The compatibility page needed a class under
   a taxonomy that says its set is closed and covers every page under
   `docs/`, and it claims nothing of its own, so it is named on the
   index-pages line as routing rather than claiming. The Architecture
   section's one-line description of what sits behind the router also
   says "the principles" and now says "the guidelines".
2. **The architecture router says what became of both old paths**, in
   two sentences after the ADR bullet. The milestone asks for router
   rows for the split pages and the retirement; a reader arriving
   from a dated record needs to know the signpost is a signpost.

### Discoveries

- **`pipeline-ownership.md`'s "Where the growth lands" section holds
  a dated measurement that exists nowhere else in the repository**:
  the overlap surface at roughly 2,000 lines when #84 was written
  (2026-08-09) and roughly 5,100 at `9e8b2743` (2026-08-21), with the
  per-file breakdown. Grepped for `5,100`, `9e8b2743` and
  `2,000 lines` across all Markdown: one file, this one. The plan
  says transient line counts leave current guidance, and the brief
  says a dated fact living only here moves to the implementation
  record only if it is already there, otherwise it gets flagged. It
  is not there: the pipecat spike record measures the adapter (154
  against 155 code-only lines, 8 of 23 seam obligations) and the
  capture alignment, not the overlap surface's growth over time.
  **So this figure leaves the repository with this commit.** The
  durable half of that section, the pattern the numbers illustrated,
  is carried into the guideline in words: bespoke growth that
  duplicates a framework argues for adoption, bespoke growth in
  semantics no framework has argues against it. If the number is
  wanted back, it is recoverable from git history and belongs in a
  dated record rather than in current guidance.
- **The spike record does hold the evidence the guideline defers
  to**, verified by reading it: pipecat-ai 1.7.0 pinned throughout,
  gate 1 passing at +1.2 ms and 0.2 dB on the turn track, gate 2's
  312-against-222 and 154-against-155 counts, the seam-obligation
  map, and the draft #84 evidence comment. The pointer to that
  comment is kept in the guideline beside the pointer to the record.
- **The `principles.md` caller census in the plan was exact.** All
  eleven current callers were where the corrected census said, and
  `pipeline-ownership.md`'s single current caller was `docs/README.md`
  as stated. Its own outbound link to `principles.md` went away with
  the file.

### Verification

- The scratchpad link-and-anchor script on the finished branch:
  `checked 157 files, 0 failures`.
- **Moved-path census after the change**, grepping both
  `principles.md` and `pipeline-ownership` (path-prefixed and
  same-directory spellings, plus the prose forms "principles page"
  and "pipeline ownership") across `README.md`, `AGENTS.md`,
  `CHANGELOG.md`, `docs/`, `.claude/` and both component READMEs.
  Sixty matching lines in fourteen files, each classifying as one of
  three things and none as an error:
  - **The compatibility page itself, current and correct** (3 lines,
    2 files): `docs/README.md`'s index-pages line, and the
    architecture router's two lines about what became of the old
    paths. Both describe the signpost rather than routing a reader
    through it for content.
  - **Dated records, untouched** (57 lines, 12 files): five earlier
    `CHANGELOG.md` entries plus this milestone's own two,
    `adr/2026-08-10-normalize-the-hardware-edge.md`,
    and nine files under `docs/plans/`, of which this
    reorganization's own plan and implementation record are 37. These
    are what the compatibility page exists for.
  - **Errors** (0). No maintained page links `principles.md` for its
    content, and nothing anywhere links the deleted file.
- `git diff --name-only main...HEAD` filtered for `vinga-server/`,
  `docs/reference/` and `.github/`: no matches, so no generated
  document and no code changed.
- Source review of every changed and new page: heading levels, link
  targets, the guidelines page's five On this page anchors, which the
  script resolves as headings rather than taking on trust, and the
  litmus table's rendering.
- No test lane was run and none was owed: this milestone touches no
  file under `vinga-server/`, `docs/reference/` or `.github/`.
