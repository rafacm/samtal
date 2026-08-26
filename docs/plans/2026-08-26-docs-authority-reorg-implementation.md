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
