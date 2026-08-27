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

PR #322.

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

### PR review round

External review of the branch on PR #322. Two findings, condensed as
received, each with the resolution that landed for it. Both are about
the same page and the same failure mode: a promise page stating more
than its supporting record grants.

1. **P2: the in-place floor claimed a guarantee the ADR withholds.**
   The bullet ended "Every later revision upgrades a database stamped
   at or after them", a universal statement about future images. The
   compatibility-floor ADR grants no such thing before a beta: its
   second Decision bullet promises reviewed migrations, best-effort
   from the baseline forward, and says outright that what a
   pre-release deployment does not get is a promise that no future
   decision will ever require a reset, only that such a decision
   would be recorded. The #283 addendum moves where "forward" starts
   and leaves that bound untouched.

   *Resolution.* Adopted. The bullet now states the current floor as
   a floor: where "forward" starts today, upgraded best-effort
   through the reviewed migration every schema change arrives as, and
   explicitly subject to the recorded-reset licence the page already
   carried two bullets down, which it now names rather than leaving
   the reader to reconcile. The beta obligation in the paragraph
   above is unchanged, because that sentence is the guarantee and is
   the ADR's own.

2. **P3: the wheel-migration cadence claim overstated.** The promise
   said the check runs "on every build". The server workflow is
   path-filtered: pull requests and pushes to `main` run it only when
   the change touches `vinga-server/`, `docs/reference/` or the
   workflow file, plus a `workflow_dispatch`. A change touching none
   of those ships without the step having run.

   *Resolution.* Adopted, in the first of the two forms offered:
   the sentence now credits the CI wheel-migration step with
   exercising the fresh-database path whenever the server workflow
   runs, rather than dropping the claim, because what the step proves
   is worth stating and only its cadence was wrong.

Both fixes are wording inside one page, in one commit each. Re-read
against the ADR afterwards: the promise's letter now tracks Decision
bullets one to three and the 2026-08-26 addendum without adding to
any of them, and no other sentence on the page makes a cadence or
future-image claim. The link-and-anchor script was re-run on the
finished branch: `checked 157 files, 0 failures`.

## M3: The guides lead with their interfaces

PR #323.

### What landed

Seven commits.

- **`docs/architecture/cli-guide-audit.md`,** new. The audit record
  and its four source subsections cut from the guide and pasted
  whole, under a header saying what it is (the 2026-08-24 walk for
  #285), that it is evidence rather than standard, and that the guide
  outranks it wherever the two disagree about the code today.
- **`docs/architecture/cli-guide.md`,** recut. The reviewer checklist
  is the first section after the navigation, each of its eleven items
  linked to the practice or grammar section that carries its
  reasoning, with the rule that outranks the list still attached to
  it. An On this page section sits above the checklist, per the
  policy and per the M1/M2 house shape (navigation directly after the
  introduction). Where the guide restated current command spellings
  as inventory it links `reference/cli.md`, and a concise
  source-and-disposition summary stands where the audit was.
- **`docs/architecture/observability-surfaces.md`,** recut. A purpose
  paragraph, an On this page section, the four-surface table with a
  Status column verified from the repository, four invariants, a
  routing list, and the 2026-08-15 needs assessment and external
  survey as a dated appendix.
- **`docs/architecture/design-guide.md`.** An On this page section
  after the introduction, five entries, nothing else touched.
- **`docs/architecture/README.md`** router rows for both recut
  guides, plus `cli-guide-audit.md` under the same reader question,
  labelled evidence; **`docs/README.md`** adds the audit to the
  taxonomy's research and field notes class.
- **`CHANGELOG.md`.** Three bullets under the existing 2026-08-26
  `### Changed` section.
- **This section and the milestone tick.**

### What changed inside the moves, and what did not

- **The audit block is byte-identical.** The 217 lines from
  `## The audit record` through the last row of the 12 Factor table,
  24,293 bytes, compared against `b7e691d8:docs/architecture/cli-guide.md`
  after the move: identical, no exceptions. Its internal "above" and
  "below" references now point across a file boundary, and rather
  than rewrite them the new file's header states once that they mean
  the practices in `cli-guide.md`, which is where the walk was
  written. A dated record is not edited to suit a later move.
- **The needs and the external-practice sections are identical but
  for their heading level**, demoted from `##` to `###` so they sit
  under the appendix heading. Bodies compared line for line against
  `b7e691d8`: no other difference, and the framing sentence the plan
  licenses is above them rather than inside them.
- **The checklist's eleven items keep their wording.** The only
  change to the list itself is the trailing link on each item and the
  one word in its introduction that pointed the reader the wrong way
  ("Each is a practice above" became "below", since the list moved to
  the top).

### The current-status verifications behind the four-surface table

Design decision 6 asks for these to be read from the repository and
cited rather than carried from the plan. Each row, and what was read:

- **Structured events: landed, and further than "#155 lands".**
  `vinga-server/src/vinga_server/events/` is a package of four modules
  (`__init__.py`, `assembly.py`, `catalog.py`, `values.py`), not the
  single `events.py` the old table named. `catalog.py`'s own opening
  says the registry it replaced described an emission while a catalog
  entry is one, and that a variant owns its whole emission: channel,
  level, payload shape and rendering. `docs/reference/events.md`
  states the same property from the other side ("an emission that is
  not one of these shapes cannot be built at all") and is generated by
  `vinga-server events reference`. So the old row's "the no-leak
  contract holds by construction once #155 lands" is present fact,
  and the table says so.
- **Conversation store: landed, with one claim corrected.**
  `vinga-server/src/vinga_server/conversations/` holds `schema.py`,
  `store.py`, `api.py`, `docgen.py` and `migrations/`.
  `config/models.py`'s `ConversationsConfig` gives `enabled: bool =
  False` with independent `metrics` and `text` switches;
  `store.py`'s retention path deletes whole sessions older than
  `retention_days`, and `conversations/docgen.py` states the default
  of 90. `api.py` registers three reads that answer 401, mounted on
  the configuration API. `deploy/postgres-init.sql` provisions the
  read-only `vinga_ro` role. The old row's **"per-session and
  per-user deletion" is not true of this release**: there is no
  delete route in `api.py` and no purge command,
  `docs/reference/conversations-schema.md` says erasing a named
  session on demand is not enforceable in this release and that the
  verb returns with #190, and per-user scoping needs a voiceprint
  identification that does not exist. The row now states the
  retention window as the whole deletion policy and names #190.
- **Capture: landed, off unless `server.capture.enabled` is true.**
  `vinga-server/src/vinga_server/capture.py` and
  `device/capture_audio.py` give the three files per session.
  `config/models.py`'s `CaptureConfig` is where the switch is:
  `enabled: bool = False`, with `dir` required even when disabled
  and the comment saying the flag rather than the section's presence
  is what switches it, because a field round records and then stops
  and the tuning is worth keeping across that.
  `tests/unit/test_config.py:test_capture_is_off_until_it_is_enabled`
  pins it, and `app.py`'s composition root builds a `CaptureStore`
  only when the section exists and the flag is set. The room-audio
  warning is `CaptureEnabled` in `events/catalog.py`, WARNING on the
  app channel and, in its own words, "said once at startup"; the
  per-session `capture_started` is INFO and says only which path a
  session is being written to. `config/models.py` carries the total
  directory budget and `capture.py`'s `prune` deletes oldest first,
  which is what the old row's "short-lived, pruned, already governed"
  meant.
- **Audit: still future, and unowned.** A case-insensitive grep for
  `audit` over `vinga-server/src/` returns one hit, an unrelated
  comment in `config/api.py`. No table, no route, no writer. The old
  row said "future, small" and named "a future audit issue"; the new
  row says future and that no issue owns it yet, which is what the
  tracker sweep in the plan's own text supports.

### The spelling restatements replaced in the CLI guide

Two enumerations were replaced by links, and four spellings that the
#223 re-cut retired were still written as current outside the
historical passages that own them.

- **The two-spellings section.** It restated `vinga` against
  `vinga-server config`, the three sibling groups, which spelling a
  live `--help` prints, and why generated documents carry the short
  one, all of which `reference/cli.md#the-two-spellings` says. It now
  links that section and keeps only the property the rest of the page
  depends on: everything after the dispatching word is identical, so
  this page uses the short spelling.
- **The periphery inventory** in "Why not verb first". It named
  `set-secret` and `clear-secret` as existing for two kinds, which is
  both an inventory and a stale one. It now names the shapes rather
  than the words and links
  `reference/cli.md#every-command`, whose half is generated from the
  command tree; the argument (the verb list is uneven and the noun
  set grows faster) is unchanged.
- **Four stale spellings corrected**, all outside the historical
  passages: `bind-device` to `device bind` in the identity-addressing
  paragraph; `set-secret provider llm claude api_key` to
  `provider secret set llm claude api_key` in the identity-depth
  paragraph; and `set-secret` to `secret set` in the four places the
  stdout/stderr, refusal and credential practices name the command an
  export writes or a value is typed after. Verified against
  `docs/reference/cli.md` (`vinga provider secret set`,
  `vinga device bind`) and against
  `config/cli.py:_set_secret_words`, which builds the exported
  command as `<program> <holder> secret set -- ...`.
- **Deliberately left alone**: the three historical passages that
  name the old spellings correctly (the pre-re-cut two-shapes
  paragraph, the twenty-one-word `config --help` counterexample, and
  the clig 61 row), and every audit row, since the audit moved
  byte-identical and a dated record is not edited into agreement with
  a later grammar.

### Deviations from the plan

- **The outranking rule travels with the checklist** rather than
  staying at the foot of the page. It is written as "the rule that
  outranks the list", so leaving it behind would have separated it
  from the list it qualifies and left the new interface without its
  capstone. Its wording and its `product-promises.md` link are
  untouched.
- **Four stale command spellings were corrected** beyond replacing
  inventories with links, listed above. Leaving them would have left
  the guide asserting a grammar the CLI does not answer to, in the
  milestone whose stated point is that current spellings come from
  the reference.
- **The observability page gained an invariants section that did not
  exist as a section.** The plan says the recut "keeps the
  invariants", and the old page stated them scattered: in the needs
  section's closing paragraph, in the live-views paragraph under the
  table, and in the external-practice bullets. The needs paragraph
  moved to the appendix with its section, so the four invariants are
  authored prose here, each citing the ADR or the amendment it comes
  from rather than restating a source.
- No other deviation.

### Discoveries

- **The guide had drifted against its own re-cut.** #223 turned the
  grammar around and the guide records that turn in its historical
  passages, but eight current-tense mentions of `set-secret` and
  `bind-device` survived the same edit. This is exactly the failure
  the milestone's own rule prevents: a page that restates spellings
  will drift, and a page that links the generated reference cannot.
- **The observability table's access claim was the load-bearing
  error, not its issue numbers.** Reading "#120, the conversations
  schema" as future is a harmless staleness a date fixes. "Per-session
  and per-user deletion" is a claim about what an operator can do with
  somebody's conversation record, and the release has neither. That
  is the kind of sentence a maintained map must not carry, and it is
  what design decision 6's re-verification was for.
- **`vinga_server/events.py` is a package now.** The old table gave a
  file path that has not existed since the events package split. A
  path in a table is a fact like any other and goes stale the same
  way.

### Verification

- The scratchpad link-and-anchor script on the finished branch:
  `checked 158 files, 0 failures`. It resolves every heading anchor,
  so the eleven checklist links, the three On this page lists and the
  ADR amendment anchor are checked rather than eyeballed.
- **The audit move**, diffed against
  `b7e691d8:docs/architecture/cli-guide.md`: byte-identical over all
  24,293 bytes of the moved block.
- **The appendix move**, diffed against
  `b7e691d8:docs/architecture/observability-surfaces.md`: identical
  but for the two demoted heading levels.
- `git diff --name-only b7e691d8..HEAD` filtered for `vinga-server/`,
  `docs/reference/` and `.github/`: no matches. Seven files changed,
  all documentation.
- Source review of every changed page: heading levels, hard wrapping
  (only the lines a single unbreakable link URL forces run past 75
  columns), and the table's rendering with its fifth column.
- No test lane was run and none was owed: this milestone touches no
  file under `vinga-server/`, `docs/reference/` or `.github/`.

### PR review round

External review of PR #323 (backend codex 0.149.1, model
`gpt-5.6-terra`, 2026-08-27). Verdict: mergeable after the fix. One
finding.

1. **P2: the capture row got both halves of its own status wrong.**
   "Off unless a directory is configured" is not the switch: `dir` is
   required even when capture is disabled, `enabled` defaults to
   false, and `app.py` builds a store only when the section exists
   and the flag is set, which
   `tests/unit/test_config.py:test_capture_is_off_until_it_is_enabled`
   pins. And the explicit room-audio warning is emitted once at
   startup (`capture_enabled`, WARNING), not on every session: the
   per-session `capture_started` is INFO and says only which path a
   session is being written to.

   *Resolution.* Adopted. The row now names
   `server.capture.enabled` as the switch, says why the flag rather
   than the section carries it, and distinguishes the startup warning
   from what the per-session event honestly says. The
   source-verification bullet above is corrected the same way, and
   now cites the four files the claim is actually read from rather
   than the module docstring.

   *What produced the error.* The status was read from
   `capture.py`'s module docstring, which says "It is off unless a
   directory is configured, and says so on every session it records".
   That sentence is loose against the configuration model and the
   composition root beside it, and a docstring is not where a
   deployment-facing switch is defined. The other three rows were
   verified against models, routes and generated references; this one
   was not, and it is the one that was wrong.

## M4: Concepts distinguishes today from direction

PR #324.

### What landed

Six commits.

- **`docs/concepts.md`,** recut, dated 2026-08-27. A new
  introduction naming the page's class (a maintained map,
  deliberately ahead of the code) and stating what outranks it and on
  what: the promises, the guidelines and the decision records, the
  owning issue for a direction, the generated references for exact
  current behavior, and the Xiaozhi notes for the wire. The glossary
  relationship sentence is kept verbatim. An On this page section
  follows, eleven entries, which the policy's five-or-more test
  clears comfortably. Every section then opens with a bold status
  line, and mixed sections mark their exceptions inline.
- **`vinga-server/src/vinga_server/config/docgen.py`** and
  **`vinga-server/src/vinga_server/conversations/docgen.py`,** one
  `CONCEPTS = "../concepts.md"` constant and one sentence of prose
  each, with **`docs/reference/domain-config.md`** and
  **`docs/reference/conversations-schema.md`** regenerated in the
  same commit.
- **`docs/glossary.md`,** the Conversation and Session entries, for
  the reason under Deviations below.
- **`docs/README.md`,** the maintained-maps class paragraph and the
  concepts reference row; **`docs/architecture/README.md`,** a new
  row for the page under the conversation question, where it had
  none.
- **`CHANGELOG.md`,** two bullets under a new 2026-08-27
  `### Changed` section.
- **This section and the milestone tick.**

### The owner mapping as applied

The census was built for this milestone from the tracker and from
`docs/adr/` and `docs/features/`, per design decision 7, and applied
as follows. Every issue number the page cites is in this table and
nowhere else.

| Claim on the page | Status marking | Owner |
| --- | --- | --- |
| The Conversation as a durable cross-session thread, and every semantic citing it | Decided direction | #190 |
| The durable, queryable per-device record of observed facts | Decided direction | #96 |
| Device activation and onboarding | Implemented today | #40, closed |
| The stored record of sessions and turns | Implemented today | #120, closed |
| Whether prebuilt images send the buffered wake-word audio | Open question | #112 |
| Memory scopes and tool operations | Decided direction | #83 |
| Memory storage moving to Postgres | Decided direction | #314 |
| One help agent, prompt composed per session from a shared part and a per-board facts block | Decided direction | #21 |
| The per-board device guides that feed it | Implemented today | #93, closed |
| Agent, not persona | Implemented today | `docs/features/2026-08-12-agent-not-persona.md` |

Two owners the plan listed were applied with a correction rather than
as written. **#93** is cited as implemented (the guides exist and are
linked from `docs/devices/README.md`), not as the future source the
old text implied. **#40** was not cited on the page at all before this
milestone, because activation was not discussed; it is now, as
implemented, in the Device section.

### The #190 alignments, one by one

`docs/concepts.md` defers to #190 wherever the two disagreed. Each
change below is a change to the user-visible semantic the page
states, not a restatement of #190's implementation decisions, which
stay out of the page.

1. **Suspend-never-end is gone.** The page said "conversations are
   suspended, never ended", that there is no end in the model, and
   listed cleanup of old conversations as a consequence to build.
   #190 has an explicit model instead: a fresh activation starts a
   fresh thread, operators can list, read and delete threads, and
   retention becomes thread-aware. The page now states that model and
   says which formulation it replaces.
2. **Fresh-by-default replaces silent resumption.** The page implied
   a session attaches to an existing conversation as the ordinary
   case. #190 makes resumption always explicit and never silent, so
   the page states the fresh-thread default first and describes
   resuming as something the user asks for by describing the thread.
3. **The clean-switch default gains its owner.** "A switch starts
   clean by default" was unowned direction; it is #190's fresh-thread
   default applied to a handover, and it is cited as such. Its
   scoping and provider-leak reasoning is unchanged, and today's
   behavior (the session transcript carries across a switch) is now
   stated as the gap the direction closes rather than as a footnote.
4. **The warn-and-summarize idea is replaced by consent-based
   recap.** The page offered "a warning when one grows very long, and
   an offer to summarize it and start fresh from the summary". #190's
   decision is recap milestones offered for consent inside the thread,
   which is what the page now says, naming what it replaces.
5. **Two projections replace reconstruction.** The page had the
   session recording an ordered list of the conversations it touched,
   and the session transcript being reconstructed from that list plus
   the session events. #190 has turns referencing both their
   conversation and their session, so the two views read the same
   rows. The page now says that, and drops the ordered-reference
   machinery.
6. **The terminology alignment is stated.** #190 aligns the words:
   sessions are connection records, conversations are threads. The
   page says it in one sentence and uses "thread" wherever the
   distinction is doing work.
7. **Agent-scoped discovery is cited.** The page already decided
   conversation search is agent-scoped; that scoping is #190's, and
   the claim now cites it instead of standing alone.
8. **Resumption is a switch that needs the text.** New on the page,
   from #190: a thread cannot be resumed from rows that were never
   written, so resumption is available only where conversation text
   is stored, which is one of the two switches the store's reference
   documents.
9. **Cost is explicitly excluded from #190.** The page presented "how
   much has this conversation cost" as a consequence of the entity.
   Budgets and per-conversation accounting are out of #190's scope,
   so the claim is marked unowned and says why, in both the
   Conversation and the Meta capabilities sections.

### Directions with no owning issue or decision record

Marked on the page as `**Decided direction** (recorded on this page,
2026-08-21; no owning issue or decision record yet)`, and listed here
for the maintainer to adopt or file owners for. The date is 2026-08-21
rather than today's because that is when the page recorded them; the
recut did not decide them.

1. **The shared user profile.** A small profile (name, language,
   standing preferences) visible to all of one user's agents, as a
   deliberate hole in agent isolation. #83 covers neither this nor a
   user-bearing key.
2. **Meta capabilities as injected builtin tools.** The model of a
   small set of vinga-owned tools injected into every agent's tool
   set. The handover tool exists; the model around it is unowned.
3. **The cost question.** "How much has this conversation cost so
   far", and cost as a property of a thread. Explicitly out of #190's
   scope.
4. **Meta-turn recording.** A turn that is only a meta request
   belongs to the session and to no thread, and a mixed turn belongs
   to the thread.
5. **The (user, agent) memory key.** The refinement that arrives with
   users, so a household-shared agent remembers each person
   separately.
6. **Users and voiceprints, and their timing.** That users, budgets
   and voiceprint identification arrive together in a later stage,
   and that conversations, memory and the profile all gain a user in
   their key when they do.

"A switch lasts for the session" is deliberately **not** on this
list, and not marked as direction at all: see the discoveries below.

### Deviations from the plan

- **Two glossary entries were corrected, not just re-pointed.** The
  milestone's stated glossary work is re-pointing inbound links, and
  all four anchors the glossary uses survived the recut unchanged
  (`#binding`, `#conversation-and-session`, `#meta-capabilities`,
  `#configuration-changes-arrive-as-whole-worlds`), so no link needed
  moving. What did need moving is content: the Conversation entry
  asserted "suspended rather than ended" and the Session entry
  asserted the reconstruction model, both of which alignments 1 and 5
  above retire. Shipping a lookup page that contradicts the model it
  points at, in the milestone whose subject is authority, was the
  worse option. Two entries changed, no others, and the glossary's
  Date line is untouched, following M1, which also edited an entry
  without bumping it.
- **`docs/architecture/README.md` gained a row rather than having one
  updated.** The plan speaks of updating the concepts row; the
  architecture index had no concepts row at all. Its "Understanding a
  conversation end to end" section already routes to two pages
  outside `architecture/`, so the page's location was not what kept
  it out.
- **No anchors are used in the links to `xiaozhi-notes.md`.** Every
  such link is to the file. M5 restructures that document under the
  accepted combined shape, so anchors written now would be anchors
  M5 has to fix, and the section names it will use are not yet
  decided. The notes are linked five times and always as the file.
- No other deviation.

### Discoveries

- **"A switch lasts for the session" is implemented, not
  direction.** The plan's design decision 7 lists
  switch-for-the-session among the directions expected to have no
  owner. Reading the code, it is current behavior and follows from
  two things that already exist: `device/bindings.py` resolves the
  device's bound list and default when the device connects, and the
  handover state in `runtime/pipeline.py` lives on the session's
  pipeline object, which the next connection does not inherit. The
  page marks it implemented and says why, so nothing is left waiting
  for a feature that shipped.
- **"The thin-device promise" was a stale citation.** The page cited
  thin device as a promise in two places. M2 moved "Thin device,
  smart server" to `guidelines.md`, so the citations now point there.
  This is exactly the staleness M2's own footprint predicted for
  pages that cite the split documents, and the concepts page was not
  on M2's caller list because it links `principles.md` rather than
  either phrase.
- **"Activation" is overloaded and the page used both senses.** A
  device activation joins a deployment once, through the 6-digit
  ceremony (#40). An agent activation assembles a prompt at the start
  of a session or after a switch, which is what the reload section
  means by "prompt text is assembled at activation". Both senses were
  on the page with nothing distinguishing them; the Device section
  now names the collision in one sentence.
- **A generated paragraph can break a link across a line.** The
  conversation store's renderer wraps prose with `textwrap`, which
  split the first draft of the new sentence between the link text and
  its target. It renders correctly, but the scratchpad link checker
  reads links per line and would have skipped it silently. The
  sentence was rewritten to open with the link, which fits inside the
  78-column wrap whole. Worth knowing for M6, which may add prose to
  a generated introduction the same way.
- **Two unit tests were already failing at the branch point, and a
  docs-only merge is how they got there.**
  `test_command_spellings.py::test_the_manifest_is_the_census` and
  `::test_every_live_spelling_names_a_command_the_tree_has` failed on
  M3's tip before any change of this milestone: the spelling manifest
  did not know about `docs/architecture/cli-guide-audit.md`, which M3
  created, and the `AGENTS.md` line numbers it records had moved.
  Verified by running the file in a detached worktree at that commit,
  which failed identically. The mechanism is worth recording exactly,
  because it will recur: the spelling census scans the whole tracked
  tree including documentation, while the server workflow's path
  filter runs only on changes touching `vinga-server/`,
  `docs/reference/` or the workflow file, so a documentation-only
  merge can stale the census with no lane going red to say so. M3 was
  such a merge, and this branch is the chain's first pull request
  whose diff the filter admits, which is why the resynchronization
  lands here rather than where it was caused.
- **The audit rows needed a classification, not just a fresh scan.**
  Regenerating the manifest alone would have left the guard red: the
  six spellings M3 moved out of the guide were classified `historical`
  there by the program-word rule, and in their new file they fell
  through to the default `respell`, where five of them name commands
  the tree retired. `cli-guide-audit.md` is a dated record whole,
  unlike the guide, so it joins the paths classified `historical`
  outright and all six rows come back to the class they had.

### Verification

- The scratchpad link-and-anchor script on the finished branch:
  `checked 158 files, 0 failures`. Run after every commit that
  touched a link.
- `uv run ruff check .` from `vinga-server/`: `All checks passed!`.
- `uv run pytest tests/unit -q`: **2 failed, 4020 passed, 19
  skipped** in 451s on the first pass, both failures the inherited
  `test_command_spellings.py` pair described above; **4022 passed, 19
  skipped** after the commit that resynchronizes the manifest, with
  no failure left.
- `uv run pytest tests/integration -q`: **200 passed** in 267s.
- The development Postgres was started with `docker compose up -d
  --wait` from this worktree's own root, which both lanes need; the
  compose project is `wt-m4`, isolated from any other checkout's.
- **All five generated-document drift checks reproduced by hand**,
  with the workflow's own commands, after the regeneration commit:
  `config reference`, `conversations schema`, `events reference`,
  `config openapi`, and the two CLI steps (the whole-page rebuild
  through the markers and the recipe region against
  `cli.cli_recipes()`). Every `diff -u` was empty. Only the two
  intended files moved: four added lines on `domain-config.md`, four
  on `conversations-schema.md`, both exactly the generator change's
  output.
- **The spelling manifest regenerated, not hand-edited**, with the
  command its own module documents, from `vinga-server/`. Its diff is
  the shifted line numbers of the four files this branch lengthened,
  the rows for this record's own quoted spellings, and the six audit
  rows arriving under their new path in the class they left with.
- **Issue numbers on the page audited by grep.** The page cites #190,
  #120, #96, #93, #83, #40, #314, #112 and #21, and nothing else;
  each appears in the owner table above with the subject it is cited
  for.
- Source review of every changed page: heading levels, the eleven On
  this page anchors, and hard wrapping. Four lines on
  `concepts.md` exceed 75 columns and each is a single unbreakable
  link URL, the exception M3 recorded.

### PR review round

External review of the PR #324 diff (backend codex 0.149.1, model
`gpt-5.6-terra`, 2026-08-27). Verdict: mergeable after fixes. Four
findings, all P2, each fixed in a commit of its own.

1. **The page described the stored record unconditionally.** Two
   claims said the server records sessions and turns with no mention
   of the master switch, while the generated reference says recording
   is off unless a deployment enables it. A maintained map is never
   allowed to disagree with a generated reference about current
   behavior, so this was the milestone's own rule broken in the
   milestone that wrote it.

   *Resolution.* Adopted. Both claims name the switch, and the longer
   one stops summarizing the storage mechanics at all: whether the
   store records, what its content switches take away and how long a
   row is kept are the reference's to state, and are linked.

2. **"History lives only as long as its session" conflated two
   different things.** The claim was on both the concepts page and
   the glossary, and it is false about the store: an enabled store
   keeps a session's turns under its retention policy, which outlasts
   the session on purpose.

   *Resolution.* Adopted, in the two-part form the finding asked for.
   What is missing today is the entity, not the rows. The agent-side
   half was verified in `runtime/pipeline.py`, where the turn list is
   built per pipeline and never read back from the store, so an
   agent's working context does end with its session. The store-side
   half now points at the retention section of the reference. Both
   pages say it the same way.

3. **The summary paragraph left one claim unmarked.** Its own status
   line promises the exception is marked inline; it marked the
   conversation entity and left the users sentence bare, which a
   reader following the convention would read as implemented.

   *Resolution.* Adopted. The claim carries the recorded-here marking
   that the section developing it carries, so the summary and the
   section agree about who owns it.

4. **The architecture index erased the no-owner case.** Its new row
   said each decided direction names the issue that owns it, which
   contradicts the marking this milestone introduced and the global
   index's own description of it.

   *Resolution.* Adopted. The row now says a direction names its
   owning issue or says plainly that none exists yet, matching
   `docs/README.md`.

The four fixes are prose on `docs/concepts.md`, `docs/glossary.md`
and `docs/architecture/README.md`. Nothing under `vinga-server/` or
`docs/reference/` is touched by them, so no lane was re-run and none
was owed; the link-and-anchor script was run after each and reports
`checked 158 files, 0 failures`.

## M5: The Xiaozhi notes get the accepted shape

PR #325.

### The accepted shape, restated

The #312 structure gate was cleared during the plan review round, on
2026-08-26, and the plan records the decision: **one combined
`xiaozhi-notes.md` with authority-labeled sections**, not a split into
a maintained reference plus a dated research file. Two things came with
the acceptance and are implemented here. The maintained sections carry
an **upstream currency statement** naming the vendor-clone commits they
were last read against, the firmware versions actually observed on
boards, and the dates. And **board-specific current facts and
procedures leave the page** for the device guides, per the plan's
design decision 8 as amended by review finding 8: the notes keep shared
protocol behavior and dated field evidence, and a live board procedure
is not one of their sections. The gate was not re-asked.

### What landed

The commits, in the order the tree needed them.

- **The board procedures move.** `docs/devices/README.md` gains the NVS
  write with its carry-over rules, as a subsection of the onboarding
  section that used to point at the notes for it, and a
  `Driving a board from a terminal session` section holding the esptool
  reset with the DTR/RTS table, the boot-log reading and the NVS
  read-back. `docs/devices/waveshare-esp32-s3-touch-amoled-2.16.md`
  gains the silent portal save as its own observation. The three guides
  and the common page point at the new homes.
- **The notes are labeled and reordered.** A purpose introduction
  naming the four classes and what each means for the reader, the clone
  commands still on the first screen, then `Upstream currency`, an
  `On this page` section of six entries, and the content in reader
  order: the firmware and its one URL, the protocol vinga-server
  implements (check-in, ceremony, channel), what stock firmware costs
  the server, the upstream server as read on 2026-08-01, licensing.
- **`AGENTS.md`'s hardware context** points at the device area for the
  procedures and keeps the notes for the protocol and the upstream
  reply-language trap. Nothing else in that file was touched.
- **The claim sweep**, listed in full below.
- **The concepts anchors.** Three of the four links from
  `docs/concepts.md` name their section; the fourth is the authority
  line about the whole document.
- **`docs/README.md` and `docs/architecture/README.md`** describe the
  page as the mixed one it now is.
- **`CHANGELOG.md`,** two bullets under the existing 2026-08-27
  `### Changed`.
- **The spellings manifest,** regenerated, and this section with the
  milestone tick.

### What moved, and what changed inside the moves

Both blocks moved by cut and paste and were diffed against the pre-move
file:

- **The NVS write procedure** (the `nvs_input.csv` block, the
  `nvs_partition_gen`/`write_flash` pair, and the carry-over paragraph
  ending in the CRC32 comparison): **byte-identical** once the
  two-space indent it carried as a nested list item is removed.
- **The terminal-session block** (all four bullets, including the
  DTR/RTS table and the `-d written` warning): **byte-identical**.

The wording that is not identical, all of it authored deliberately:

- The section heading `Driving the board from a terminal session`
  became `Driving a board from a terminal session`, at `##` level on
  the common page, and gained a provenance sentence above it, per the
  device docs' convention: verified in hands-on use on the
  Touch-LCD-1.54 (2026-08-12/13), with the note that the reset behavior
  is the ESP32-S3's own USB port rather than a board fact. Inside it,
  "The port is `/dev/cu.usbmodem101`" became "The port was
  `/dev/cu.usbmodem101` ... on macOS", because a port name observed on
  one host is not a standing fact.
- The NVS block's parenthetical about partition offsets and sizes
  became the sentence introducing the new subsection, unchanged in
  content: `0x9000`, `0x4000` on the Touch-LCD-1.54, `0x6000` on the
  AMOLED-2.16 factory image, read the partition table at `0x8000`
  rather than assuming.
- The captive-portal bullet's three field-observed cautions were split
  by owner. The redirect intolerance stayed in the notes, promoted from
  a sub-bullet to a bullet, with "Three field-observed cautions"
  becoming "which is field-observed rather than hypothetical" inside
  it. The silent portal save moved to the AMOLED guide, which already
  carried a vaguer version, and gained the detail the notes had (the
  WiFi fields were submitted and nothing was persisted, nothing on
  screen said so). The AMOLED's unreachable portal was already in that
  guide in the same words; the notes' general conclusion, that the NVS
  route rather than the portal is the recovery path to count on, moved
  to the common page's onboarding section, where it is about boards
  rather than about one board.
- The seven entries of "What running stock firmware costs the server"
  became `###` headings. Each heading is that entry's bolded lead
  sentence with the trailing period dropped, and the body is the rest
  of the entry rewrapped, unchanged in wording, except the last one,
  whose body opened with "but that is our choice" and now opens with
  "That is our choice" because the sentence it continued is now the
  heading.

Nothing was dropped without a home except one bullet, deliberately:
"Interaction on this board: short-press PWR toggles the conversation;
long-press powers off. Wake word in prebuilt builds is Chinese ...
an English model (`wn9_hiesp`, 'Hi ESP') is available when building
from source." Every clause of it is already carried, better, elsewhere:
the PWR gestures in the Touch-LCD guide's Controls table, the wake word
in that guide's Wake word section, and `wn9_hiesp` in
`vinga-esp32/README.md`'s planned customizations. Its generalization
about prebuilt images was also wrong, which the next section records.
The board hardware parenthetical (ST7789, CST816S, ES8311 + ES7210,
16 MB / 8 MB) was likewise dropped rather than moved: the part numbers
are in `vinga-esp32/README.md`'s hardware table and the memory sizes in
`AGENTS.md`'s hardware context, so moving it would have made a third
copy. The Kconfig board directory itself stayed in the notes, as the
worked example of the compile-time board-support fact.

### The claim sweep, one row per claim

Every claim #312 names, checked across the root README,
`vinga-server/README.md`, `vinga-esp32/README.md`, the three device
guides and their common page, `docs/concepts.md`,
`docs/system-overview.md`, `docs/glossary.md` and the notes.

| Claim | Page | Verdict |
| --- | --- | --- |
| Activation and onboarding | `xiaozhi-notes.md`, ceremony section | **Fixed.** "Omitting it, which is what vinga-server does today" predated #40 landing; the reply carries an `activation` section for a device that resolves to nothing. |
| | `xiaozhi-notes.md`, OTA check-in | **Fixed.** "Nothing in the OTA response says the device is unwelcome" is true only with onboarding off; the bullet now says which of the two cases an empty token is. |
| | root README, features bullet and the paragraph above it | **Fixed.** "No account, no activation" contradicted step 7 of the same page, where an unbound board shows a six-digit activation code. The bullet now says vendor cloud; the prose says nobody else's activation. |
| | root README, step 7 | Consistent already: code shown and spoken, polled while it waits, one command binds it. |
| | `vinga-server/README.md`, Onboarding a device | **Contradiction found, not fixed.** See Deviations. |
| | `concepts.md`, Device | Consistent already, and it cites #40 as implemented. |
| | AMOLED guide | Consistent already: portal onboarding verified hands-on, code shown, claimed by command. |
| Wake-word data (#112) | `xiaozhi-notes.md` | Consistent already: the build option, the report, and the question left open. Unchanged. |
| | Touch-LCD guide | **Fixed.** It said the protocol notes "describe only the after-the-fact word report", which they do not; it now says what is unchecked and links the section. |
| | `concepts.md`, `glossary.md` | Consistent already; both cite #112 and neither claims to know. |
| | `devices/README.md` | Consistent already: it makes no claim about the trigger audio. |
| MCP tool discovery and its race | `xiaozhi-notes.md` | Consistent already; the entry now has an anchor. |
| | `devices/README.md` | Consistent already, and it now links the notes rather than only asserting the race. |
| | three device guides, `concepts.md` | Consistent already; each states the caveat and defers. |
| | `system-overview.md`, step 7 | Consistent already: it describes device tools without claiming discovery is synchronous. |
| Listening modes | `xiaozhi-notes.md` | Consistent already; the entry now has an anchor. |
| | `devices/README.md` | Consistent already, and it now links the notes for the fact the server cannot change the mode. |
| | three device guides | Consistent already: realtime on the two AEC boards, auto on the ePaper, manual used by none. |
| | `glossary.md`, Listening modes | Consistent already: "the firmware picks realtime whenever AEC is on". |
| | `vinga-server/README.md`, Listening and barge-in | Consistent already. |
| | `system-overview.md`, step 11 | Consistent already: auto re-arms, realtime never stopped, idle timeout closes. |
| OTA routing and the redirect | `xiaozhi-notes.md` | Consistent already; the finding stayed and is now its own bullet. |
| | `vinga-server/README.md`, Onboarding a device | Consistent already: both spellings served, no redirect, because the firmware follows none. |
| | AMOLED guide (`code=307`), `glossary.md` (OTA endpoint), root README steps 5 and 6 | Consistent already. |
| Device-token behavior | `xiaozhi-notes.md` | **Fixed**, with the activation row above: `200 OK` plus an empty token, refused at the handshake with `auth_rejected` / `no_token`, verified against `ota/reply.py` and `events/values.py`. |
| | `glossary.md`, OTA endpoint | Consistent already: a summary that says the reply carries the token, claiming nothing about the empty case. |
| | `concepts.md`, Device | Consistent already: the devices map doubles as an allowlist. |
| Stock-firmware compatibility | `xiaozhi-notes.md`, costs section | Consistent already, and re-verified: `vinga-esp32/` still ships no code, it is one README. |
| | `architecture/product-promises.md`, root README, `vinga-esp32/README.md`, `devices/README.md` | Consistent already. |
| Which wake word a prebuilt image carries | `xiaozhi-notes.md` | **Fixed by removal.** "Wake word in prebuilt builds is Chinese" is contradicted by the AMOLED guide's hands-on "Sophia"; the guides already carry the honest version, that prebuilt and vendor-shipped are channels rather than versions. |

Five links followed the procedures rather than the claims: the root
README's flashing step, `vinga-esp32/README.md`'s NVS sentence, and the
three device guides' pointers into the notes, plus the AMOLED guide's
partition-size comparison.

### Deviations from the plan

- **`vinga-server/README.md` carries a contradiction this milestone
  found and did not fix.** Its onboarding section ends "The ceremony
  above has been driven end to end against a simulated device and a
  served server. The checkpoint on a factory-firmware board ... is
  still open", which the notes contradict: the ceremony was validated
  on hardware on 2026-08-13, on the Waveshare factory AMOLED-2.16 and
  on the stock Touch-LCD-1.54, and the notes have recorded it since
  `e000264d`. The README paragraph is from `b10c9027`, dated
  2026-08-12, so it is stale by one day and has been since. It is not
  fixed here because this milestone's brief confines
  `vinga-server/` to the spellings manifest and verifies that boundary
  by `git diff --name-only`. M6 audits the server README and owns this;
  the correction is one paragraph, replacing the still-open sentence
  with the 2026-08-13 validation and a link to the ceremony section.
- **The notes are linked four times from `concepts.md`, not five.**
  M4's section says five; the count is four, and three of them name a
  section now. The fourth is the authority bullet in the page's
  "what outranks this page" list, which is about the document as a
  whole, so it stays a link to the document.
- **The devices common page gained a developer procedure.** Its stated
  subject is the hardware in front of a user, and an `esptool`
  invocation is not that. It goes there anyway because the page already
  told the reader to write NVS over USB and then sent them elsewhere
  for how, and because the alternative homes are worse: repeating the
  procedure in three guides, or leaving it on the page whose subject is
  the wire. Every moved section says where its facts come from, which
  is the convention the guides already hold themselves to.
- No other deviation. The combined shape, the currency markers, the
  clone commands' position, the labels, the anchors and the
  `On this page` section are as the plan specifies.

### Discoveries

- **The census's second failure is M3's, and regeneration cannot fix
  it.** `test_command_spellings.py` had two failures at the M4 tip.
  Regenerating the manifest fixed one. The other,
  `test_every_live_spelling_names_a_command_the_tree_has`, reports five
  `respell` rows in `docs/architecture/cli-guide-audit.md` naming
  commands the tree does not have. That page is M3's new dated record,
  and the test classifies by path: it needs an entry in the test's
  `_HISTORICAL_PATHS`, beside `docs/plans/` and `docs/adr/`, which is a
  change to `vinga-server/` that this milestone may not make. Named
  here so M6 does not rediscover it.
- **One census row is a false positive and always was.**
  `AGENTS.md:164 respell vinga-server config schema` is the sentence
  "When the vinga-server config schema changes, update
  `config.example.yaml`", which is prose about a schema and not an
  invocation. It passes the guard because `config schema` is still a
  registered spelling, so it costs nothing; it is worth knowing before
  somebody edits that line to satisfy a scanner.
- **The wake-word overgeneralization outlived its own correction.** The
  2026-08-12 device-guide work established that "the upstream prebuilt"
  and "the vendor's shipped image" are channels rather than versions,
  and the AMOLED guide records `wn9_sophia_tts` on the shipped image.
  The notes' firmware bullet still said prebuilt builds carry the
  Chinese word. The sweep in that milestone was for wake-word
  *audio* claims, which is why this one survived it.
- **A dated observation and a maintained fact can be the same
  sentence,** which is why the classes are not sections. The
  captive-portal field, the trailing slash, the ceremony validation:
  each is a current protocol fact whose warrant is a day in August. The
  page keeps them where the fact is and makes the date visible, rather
  than moving the evidence away from what it supports, which the M5
  brief explicitly licenses.

### Verification

- **The scratchpad link-and-anchor script**, after every commit that
  touched a link and on the finished branch:
  `checked 158 files, 0 failures`.
- **The moved blocks diffed against the pre-move file**: the NVS
  procedure byte-identical modulo its former list indent, the
  terminal-session block byte-identical. Recorded above.
- `uv run pytest tests/unit/test_command_spellings.py -q` before the
  doc work: **2 failed, 33 passed**, both failures pre-existing at the
  M4 tip. After the doc work and the regeneration: **1 failed, 34
  passed**, the remaining failure the `cli-guide-audit.md`
  classification gap described under Discoveries.
- `uv run pytest tests/unit -q`: **1 failed, 4021 passed, 19 skipped**
  in 434s, that same pre-existing failure. Postgres for the lane was
  started with `docker compose up -d --wait` from this worktree's root
  (compose project `wt-m5`) and stopped after.
- `uv run ruff check .` from `vinga-server/`: `All checks passed!`.
- **The integration lane was not run.** This milestone changes no
  server code, no generator and nothing under `docs/reference/`, so the
  drift checks have nothing to catch; `git diff --name-only` against
  the M4 tip lists no path under `docs/reference/` or `.github/`, and
  exactly one under `vinga-server/`, the regenerated manifest.
- **The two reconciled server behaviors were read from the code**, not
  inferred: `onboarding/unbound.py` for when an `activation` section is
  offered, `ota/reply.py`'s `token_for` for the empty token, and
  `events/values.py` for `no_token`.
- Source review of every changed page: heading levels, the six
  `On this page` anchors, the three new inbound anchors, and hard
  wrapping. The lines over 75 columns in the notes are the two currency
  table rows, four single unbreakable link URLs, two headings, and text
  that arrived already wrapped at 78 in blocks this milestone did not
  rewrap.

### PR review round

External review of PR #325 (backend codex 0.149.1, model
`gpt-5.6-terra`, 2026-08-27). Verdict: mergeable after fixes. Three
P2 findings, each fixed in its own commit.

1. **P2: the root README's provisioning step contradicted the guides
   this milestone made authoritative.** Step 6 sent every reader to the
   portal's Custom OTA URL field and named the button that opens it,
   PWR on the Touch-LCD-1.54 and BOOT on the others. The Touch-LCD
   guide records that the 2.4.0 image tested on it has no such field,
   which is why the USB route is the one there, and the AMOLED guide
   records that its factory image ignored the BOOT provisioning gesture
   in hands-on use. *Resolution:* the step routes through the board's
   guide first, offers the portal only where the image exposes the
   field, names the NVS procedure for where it does not, and asserts no
   button. One paragraph, since the rest of that file is #309's.

2. **P2: the notes overstated when activation is offered.** Two
   passages said an enabled ceremony means a device resolving to no
   agent gets a code. `activation_for` withholds the offer in three
   further cases, all of which are answered with an empty token and no
   `activation` section: a device bound to an agent this process has
   not loaded yet (`resolution.unloaded`), a binding view that is the
   stale snapshot fallback rather than authoritative, and a pending
   table that refuses because it is at `PENDING_CAPACITY` or has spent
   its code budget. That is the exact shape the page told a reader to
   treat as a hard provisioning error. *Resolution:* the check-in
   bullet now lists what an empty token can mean and sends the reader
   to the check-in's own log line for which of them it is, and the
   ceremony bullet states the four conditions an offer is made under.
   Both were written against `onboarding/unbound.py` (lines 103 to
   128), `ota/reply.py`'s `token_for` and its event branches, and
   `onboarding/pending.py`'s two bounds.

3. **P2: deferring the `vinga-server/README.md` paragraph was
   rejected.** This section's Deviations recorded the stale
   factory-firmware sentence and left it for M6, on the milestone
   brief's `vinga-server/` boundary. The reviewer held that #312's own
   acceptance criterion requires onboarding claims reconciled, and the
   coordinator amended the boundary for exactly that paragraph.
   *Resolution:* it now states the 2026-08-13 validation on both
   boards, links the notes' ceremony section for the evidence, and
   sends the serial procedures to `docs/devices/README.md`, their home
   since this milestone. Nothing else in that README was touched, and
   the Deviations entry above stands as the record of what was decided
   and then overruled.

Verification after the three fixes: the link-and-anchor script,
`checked 158 files, 0 failures`; `uv run pytest
tests/unit/test_command_spellings.py -q`, **35 passed**, after
regenerating the manifest, which moved the notes' two recorded
spellings eight lines down and changed nothing else. The
`cli-guide-audit.md` classification failure this section reported under
Discoveries is gone: the rebase onto merged `main` brought the fix
with it, so the file's tests are green in full.

## M6: The summaries answer to their sources

PR #326.

### What landed

Eleven commits.

- **`vinga-server/README.md`,** two summaries pointed at what they
  answer to: the `local_only` section, which is how the server keeps a
  product promise and named it only as "the promise", and the Status
  section, which sent a reader after device setup to a page M5 emptied
  of board procedures.
- **`README.md` and `vinga-esp32/README.md`,** the three claims each
  stated in its own words: thin device and smart server (a guideline
  since M2), the loop that runs entirely on your own hardware (the
  first-class local deployment), and the project layout's docs row,
  which still called the architecture corpus "the architecture and its
  principles".
- **`AGENTS.md`,** the repository-layout entry for the protocol notes,
  which promised "ports, configuration keys, and the validated
  end-to-end demo procedure" after M5 moved every procedure out, plus a
  new entry for `docs/README.md`, the page that classifies the rest.
- **`docs/glossary.md` and
  `docs/conversational-quality-regression-suite.md`,** the event names
  each carries, sent back to the generated reference; and the
  glossary's Help agent entry, which described a planned agent without
  linking the page that names its owner.
- **`vinga-server/src/vinga_server/egress.py` and
  `device/boundary.py`,** the two callers still routing through
  `principles.md` for content.
- **`vinga-server/src/vinga_server/config/docgen.py` and
  `conversations/docgen.py`,** one sentence each, with
  `docs/reference/domain-config.md` and
  `docs/reference/conversations-schema.md` regenerated in the same
  commit.
- **`.claude/skills/implement-issue/SKILL.md`,** the two "being
  reorganized by #310 to #313" caveats, replaced with the taxonomy
  pointer per design decision 12.
- **`CHANGELOG.md`,** one bullet under the existing 2026-08-27
  `### Changed` section, and the regenerated spellings manifest, this
  section and the milestone tick.

### The audit set, page by page

Every page the milestone names, what was fixed on it, and what was
checked and found already correct. A fix is a sentence; nothing here
rewrote a page.

| Page | Fixed | Checked and clean |
| --- | --- | --- |
| `README.md` (root) | The fully-local claim links the promise; "thin device and a smart server" links the guideline; the project layout's docs row named `principles.md`'s old contents | The WebSocket-only bound against the promise's own transport bound; the activation prose M5 reconciled; the hardware table against `vinga-esp32/README.md`'s, which lists the same three boards at the same statuses; the simulator bullet against `reference/cli.md`'s supported list (check-in, `--claim`, one turn, one packaged utterance) |
| `AGENTS.md` | The protocol-notes entry, which described a page M5 replaced; `docs/README.md` added to the layout | The promises and guidelines entries M2 wrote; the hardware-context pointer M5 moved; the design conventions, which link `design-guide.md`; the command conventions, which link `cli-guide.md` |
| `docs/glossary.md` | Structured event links `reference/events.md`; Help agent links `concepts.md#the-help-agent` | Conversation and Session as M4 corrected them; Listening modes, Wake word (#112) and OTA endpoint as M5's sweep left them; Idle timeout, which names no number; Capture, Barge-in and Gate ladder, which link the ADR; World, which links the concepts page |
| `docs/conversational-quality-regression-suite.md` | The turn walkthrough links the generated event reference | All nine events it names exist in that reference; `vad` is described as a record that never reaches the server log, which is what the reference says about the endpointer track |
| `docs/devices/README.md` | Nothing | It links the notes for each protocol fact it summarizes (the listening mode the server cannot change, the discovery race, the one URL); the idle timeout is stated as "two minutes with no conversation by default", which is `server.limits.idle_timeout_s`'s 120.0 and is not unique to this page |
| The three device guides | Nothing | Each introduction defers to the common page, states the provenance convention its sections follow, and names the listening mode its board starts in; the AMOLED's NVS-size warning links the common page's procedure instead of repeating it |
| `vinga-server/README.md` | The `local_only` section links the promise; the Status section points at `docs/devices/` for a board and keeps the notes for the wire | The Logging index names exactly the 56 events `reference/events.md` declares, no extras either way, and the reference itself designates that index as its human half; the conversation-store section agrees with `observability-surfaces.md` and the generated schema on both switches, the 90-day retention and the absent delete verb. Its onboarding paragraph is M5's, see Deviations |
| `vinga-esp32/README.md` | The thin-device sentence links the guideline | The hardware table against the root README's; the NVS and protocol links M5 repointed |
| `docs/reference/` introductions | `domain-config.md` and `conversations-schema.md` named a source file under a path the tree does not have, corrected through their generators | `events.md`, `cli.md` and `api-openapi.json`, whose introductions claim only what their generators can support |

### The authority-sensitive phrase sweep

Case-insensitive over the whole tree, for `product promise`,
`compatibility floor`, `fully local`, `thin device`, `decided
direction` and `implemented today`, plus the close variants
`first-class`, `local-first` and `thin fork`. Dated records
(`docs/plans/`, `docs/adr/`, `docs/features/`, `CHANGELOG.md`) are
excluded by the rule that they are not rewritten. Every remaining hit
is one of the rows below.

| Phrase | Where | Classification |
| --- | --- | --- |
| `product promise` | `architecture/product-promises.md`, title and introduction | **The source** |
| | `architecture/cli-guide.md:129`, `architecture/design-guide.md:338` | Summary: each says its page never outranks the promises, and links them |
| | `architecture/guidelines.md:3` | Summary: the guidelines open by naming what they keep |
| | `docs/README.md:23`, `architecture/README.md:16`, `concepts.md:14` | Summary: taxonomy and router rows, each linking |
| | root `README.md:169` | Summary, **fixed here**: the row named the retired page's contents |
| `compatibility floor` | `product-promises.md:23` | **The source** |
| | `architecture/README.md:16`, `principles.md:14`, `concepts.md:361` | Summary: each links the promise |
| | `architecture/cli-guide-audit.md:148` | Dated record (2026-08-24), untouched |
| | `tests/unit/test_onboarding_activation.py:370` | Summary: "the compatibility floor this project promises" attributes rather than defines |
| `fully local`, `first-class`, `local-first` | `product-promises.md:62` | **The source** |
| | root `README.md:23,32,45,114` | Summary; line 32 is the strongest of them and now links the promise, **fixed here** |
| | `vinga-server/README.md:80,85,1125,1771,2730` | Summary; the enforcement section links the promise, **fixed here** |
| | `egress.py:1,3`, `providers/openai_asr.py:13`, `providers/openai_tts.py:10`, `config.example.yaml:236`, `tests/local/test_real_conversation.py:3` | Summary; `egress.py` names the promises page, **fixed here** |
| | `architecture/README.md:16`, `principles.md:14`, `guidelines.md:51` | Summary: router lines and the identity preamble |
| `thin device`, `thin fork` | `guidelines.md:34,68` | **The source** |
| | root `README.md:40`, `vinga-esp32/README.md:6` | Summary, **fixed here**: both stated the premise and linked nothing |
| | `concepts.md:98`, `architecture/README.md:23`, `principles.md:16` | Summary: each links the guideline |
| | root `README.md:48` | Not this guideline: a status claim about the firmware fork, marked 🚧 |
| | `related-projects.md:106` | Not a vinga claim: another project's division of labour |
| `decided direction`, `implemented today` | `docs/concepts.md`, 26 lines | **The source** for each claim's status; the marking itself is declared in `docs/README.md:49,154` |
| | `glossary.md:103` | Summary: cites #190 and links the concepts page |
| | `architecture/README.md:94`, `docs/README.md:154` | Summary of the convention, both admitting the no-owner case as M4's review round required |

No current page states a promise, a guideline or a status that its
source does not, and no page but the sources states one without
linking.

### The observability check

Design decision 12's companion: the four-surface model and the exact
event vocabulary are hand-copied nowhere.

- **The four-surface model** appears in
  `architecture/observability-surfaces.md` (the table and its own
  navigation) and in `architecture/README.md`'s router row, which
  describes it in one line and links it. Nowhere else: the grep for
  `four surface`, `four-surface` and `observability surface` over
  current pages returns the ADRs that hold the decision, the
  guidelines' one citation of the json-logs ADR, `docs/README.md`'s
  reference row, and the generated `reference/events.md`.
- **The exact vocabulary** is `reference/events.md`, generated. The
  server README's Logging index is the one other place event names are
  written out, and it is not a second source: `reference/events.md`
  designates it by name as "the human overview, with one line per event
  saying when it fires", the README links back to the reference for
  what an event carries, and the two sets are identical. Verified by
  set difference: 56 events in the reference, 56 rows in the index, no
  name on either side that is not on the other.
- **Two summaries named events without their source**, the glossary's
  Structured event entry and the regression suite's turn walkthrough.
  Both now say their names are examples and link the reference.

### The moved-path classification

The final search #310's verification list asks for, over the whole
tree, for `principles.md`, "the principles page", `pipeline-ownership`,
`architecture/excalidraw`, `architecture/plantuml`, and links to
`architecture/README.md` as the walkthrough.

| Match class | Where | Classification |
| --- | --- | --- |
| `principles.md` as a signpost | `docs/README.md:98`, `architecture/README.md:34` | **Current and correct**: both describe the page as a signpost rather than routing a reader through it |
| `principles.md` by path or by name | `docs/adr/2026-08-10-normalize-the-hardware-edge.md` and nine files under `docs/plans/`, plus `CHANGELOG.md` | **Intentionally historical**: the paths these link are exactly what the compatibility page exists to keep resolving |
| `principles.md` from the server's source | `egress.py:3`, `device/boundary.py:10` | **Errors, fixed here**: both routed through the signpost for content. See Discoveries |
| `pipeline-ownership` | `architecture/README.md:35`, one sentence saying the file is gone and where its content went | **Current and correct** |
| | `CHANGELOG.md` and `docs/plans/` | **Intentionally historical** |
| `architecture/excalidraw`, `architecture/plantuml` | `CHANGELOG.md:4133,4144` (the day the diagrams were added) and this reorganization's own plan and record | **Intentionally historical** |
| Links to `architecture/README.md` | `docs/README.md:93,184`, `system-overview.md:12`, `SKILL.md:70` | **Current and correct**: every one links it as the index or the router, and none as the walkthrough, which is `system-overview.md` |

**Errors after this milestone: zero.** The two that existed were the
docstrings above, and they were outside every earlier census's reach.

### Deviations from the plan

1. **The onboarding contradiction is not fixed here.** M5's record
   named it and left it to M6, and this milestone's brief made it
   deliverable 2; it was implemented here and then dropped, because
   #325's review round rejected deferring the fix out of #312 and it
   landed in M5 instead. `vinga-server/README.md`'s onboarding
   paragraph, including the pointer that still names the notes for the
   serial procedures, is M5's; every other claim in that file was
   audited here.
2. **The audit reached two module docstrings and two generators**,
   neither of which is in the named audit set. Both came from #310's
   own final verification, which is a search over the repository rather
   than over the audit set, and both were errors by the plan's own rule
   that no current caller routes through the compatibility page.
   Changing the two generators is the case the plan's review finding 6
   provided for, and it carries the full obligations, which the
   Verification section below discharges.
3. **`AGENTS.md` gained an entry the milestone does not name**, for
   `docs/README.md`. Design decision 12 has the skill tell a session to
   confirm where a role lives through the taxonomy, and `AGENTS.md`
   listed three pages under `docs/` without ever naming the page that
   classifies them, so the pointer had nowhere to land.
4. **One glossary entry was linked beyond the phrase sweep's
   findings.** The Help agent entry describes a direction and named no
   owner; it links the concepts page, which carries the marking and the
   issue.
5. **The rebase over #309 was moot.** Design decision 9 and the M6
   milestone both begin "after a rebase over whatever #309 landed".
   #309 has not landed: it is not on `main` and nothing of it is on
   this chain, so the root README was audited exactly as M1 left it,
   and the two links this milestone adds to it are the whole of its
   diff here.
6. No other deviation.

### Discoveries

- **A caller census that stops at Markdown misses the callers that
  matter most.** M2's census covered `README.md`, `AGENTS.md`,
  `CHANGELOG.md`, `docs/`, `.claude/` and both component READMEs, and
  was exact over that set. It never looked in `vinga-server/src/`,
  where two module docstrings cited `principles.md`: one for the
  enforced-not-documented guarantee, one linking it for the
  telephone-call test. A docstring is read at the moment somebody is
  about to change the code the rule governs, which makes it the worst
  place for a pointer at a page holding nothing.
- **A generated introduction can be stale about the repository rather
  than about the model.** The drift checks compare a generated document
  against its generator, so a sentence that is wrong in the generator
  is green forever. Two of them told a reader that a field description
  lives in `vinga-server/vinga_server/config/models.py` and a column
  description in `vinga-server/vinga_server/conversations/schema.py`,
  paths that stopped existing when the package moved under `src/`. The
  events generator beside them says `src/` and has since that move, so
  the three disagreed about the same tree and nothing could notice.
- **The spellings census is a documentation tripwire.** It was green at
  this branch's start, which M5's regeneration had just made true, and
  went red on eight lines of prose: four added to `AGENTS.md` and four
  to `vinga-server/README.md`, above rows that record a line number.
  Seventeen rows moved and not one spelling changed. M4 recorded the
  mechanism; this is its cheapest possible instance, and it still costs
  a commit.
- **The server README's event index is the exception that proves the
  rule.** It writes out 56 event names, which is exactly what the
  hand-copy prohibition is about, and it is correct anyway: the
  generated reference names it as its own human half, it links back for
  everything it does not say, and the two sets are identical. A second
  copy is defined by what it can drift into, not by how much of the
  vocabulary it repeats, and this one is cross-declared in both
  directions.

### Verification

- **The scratchpad link-and-anchor script**, run after every commit
  that touched a link and last on the finished branch:
  `checked 158 files, 0 failures`.
- `uv run ruff check .` from `vinga-server/`: `All checks passed!`.
  `uv run mypy` (CI's events type check): `Success: no issues found in
  4 source files`.
- `uv run pytest tests/unit -q`: **4022 passed, 19 skipped** in 432s,
  after the manifest regeneration; before it, the one expected failure
  was `test_the_manifest_is_the_census` on the shifted line numbers.
- `uv run pytest tests/integration -q`: **200 passed** in 267s.
- **All five generated-document drift checks reproduced by hand**, with
  the workflow's own commands (`config reference`, `conversations
  schema`, `events reference`, `config openapi`, and the two CLI steps,
  the whole-page rebuild through the markers and the recipe region
  against `cli.cli_recipes()`). Every `diff -u` empty. The only moved
  bytes under `docs/reference/` are the two source paths, one line
  each, exactly the generator change's output.
- Postgres for both lanes was started with `docker compose up -d
  --wait` from this worktree's own root (compose project `wt-m6`) and
  stopped afterwards.
- **The spelling manifest regenerated, not hand-edited**, with the
  command its own module documents. Its diff is 95 moved line numbers
  and two added rows, the two drift-check commands this record quotes
  by name, both classified `historical` by the `docs/plans/` rule. No
  row removed, none reclassified, verified by comparing the two files'
  spelling-and-class pairs directly.
- **The event-name set difference**, computed rather than eyeballed:
  the 56 `### ` sections of `docs/reference/events.md` against the 56
  rows of `vinga-server/README.md`'s Logging index, symmetric
  difference empty.
- `git diff --name-only` against the M5 tip, by area: seventeen files.
  Six under `docs/` (the glossary, the regression suite, the two
  generated references, and this plan and its record), three at the
  repository root (`README.md`, `AGENTS.md`, `CHANGELOG.md`), one
  under `.claude/`, one under `vinga-esp32/`, and six under
  `vinga-server/` (its README, the two docstrings, the two generators,
  and the spellings manifest).
- Source review of every changed page: heading levels, link targets and
  hard wrapping. No line this milestone wrote exceeds 75 columns except
  the table rows above, which are cells rather than prose.

### PR review round (M6)

External review of PR #326 (backend codex 0.149.1, model
`gpt-5.6-terra`, 2026-08-27). One finding:

1. **P2: the device-boundary docstring linked a nonexistent
   path.** Both markdown links in `device/boundary.py` used
   `../../../docs/`, which from `src/vinga_server/device/` resolves
   under a nonexistent `vinga-server/docs/`; repository root is four
   levels up. The off-by-one predates this chain (the retired
   principles link carried it too) and the retargeting inherited it.
   *Resolution:* both links now use `../../../../docs/`, and the
   verified anchor survives. The reviewer's suggestion to teach the
   link checker to scan source docstrings is noted for the checker's
   possible future as a committed tool; the checker is a session
   scratchpad script by plan design decision 10.

Verdict: mergeable after the listed fix.
