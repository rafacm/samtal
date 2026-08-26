# Reorganize the documentation by authority

## Goal

Implement issue #310 and its children #311, #312 and #313: reorganize
the documentation tree around a stated authority taxonomy, give the
architecture corpus one landing page organized by reader question,
split the standing promises from the revisable guidelines, recut the
two long guides around their review interfaces, make the domain
concepts page distinguish implemented behavior from decided
direction, restructure the Xiaozhi notes under an explicitly accepted
shape, and finish with an audit that pins every summary to its
authoritative source. No server or firmware behavior changes; the
one generated-reference touch (#311's backlinks) goes through the
generators.

The companion implementation doc,
[`2026-08-26-docs-authority-reorg-implementation.md`](2026-08-26-docs-authority-reorg-implementation.md),
records what each milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issues' decisions, restated

1. **Three reader-facing concepts organize the tree**: authority
   (promises, guidelines, maintained maps, generated references,
   ADRs, dated records, research notes), audience (user-facing
   versus maintainer-facing, a routing concern, never an authority
   claim), and reader question (one architecture landing page that
   routes by task). `docs/README.md` owns the repository-wide
   authority taxonomy; `docs/architecture/README.md` is the sole
   detailed architecture index.
2. **The conversation walkthrough moves** out of the architecture
   README into user-facing `docs/system-overview.md`, with current
   links updated.
3. **Promises split from guidelines.** `product-promises.md` holds
   the externally falsifiable commitments (stock-firmware floor,
   fully local first-class, the database promise corrected to agree
   with the compatibility-floor ADR as amended); `guidelines.md`
   holds the revisable defaults, gaining "Thin device, smart server"
   from the promises. `principles.md` stays as a small compatibility
   page for historical links. "Must" and "never" are reserved for
   promises, protocol requirements and security invariants.
4. **The three hardware-edge principles consolidate** into one
   guideline (normalize the hardware edge while runtimes keep their
   native execution model) with concise subrules for the seam
   placement, the device-facing interface, and sibling runtimes,
   citing the normalization ADR once. Decision ownership is recast
   as a guideline: vinga-owned decisions produce a closed reason,
   runtime-owned decisions expose that runtime's diagnostics.
5. **`pipeline-ownership.md` retires.** Its durable owned-versus-
   shared distinction and the reopen conditions move to
   `guidelines.md`; the dated Pipecat measurements stay only in the
   spike implementation record; transient line counts leave current
   guidance.
6. **The CLI guide recuts around its review interface**: checklist
   near the top, each item linked to its rationale, current command
   spellings linked to `reference/cli.md` rather than restated, the
   dated audit of external CLI guidance moved to one dated home with
   a concise source-and-disposition summary retained.
7. **Observability surfaces becomes a current data map**: the
   four-surface table first with a current status column, then the
   invariants, with exact event variants and schema columns linked
   to their generated references; the 2026-08-15 needs assessment
   and external survey become a clearly dated decision-evidence
   appendix. The ADR remains the decision record.
8. **Diagrams move under `docs/architecture/diagrams/`**, keeping
   `excalidraw/` and `plantuml/` as separate subdirectories with
   their tool-local rendering and synchronization instructions, and
   gaining one index organized by reader question that also states
   the distinct purpose of the two overview diagrams.
9. **Links migrate by authority.** Current callers link the new
   sources of truth directly; dated ADRs, plans, implementation
   records and feature records are not rewritten and keep resolving
   through the compatibility page; governance pages update where
   they describe the current relationship between document types. A
   link that resolves is not necessarily correct: verification
   classifies every moved-path match as current, intentionally
   historical, or an error.
10. **On this page navigation is selective**: added to long
    maintained pages that support non-linear use (roughly five or
    more substantial second-level sections), after the purpose and
    authority introduction, with descriptive labels; never added to
    navigation pages, short pages, generated references, or dated
    records.
11. **#311**: `docs/concepts.md` becomes an explicitly maintained,
    future-aware domain model. Implemented behavior and decided
    direction are visibly distinguished at the section or claim
    level, each decided direction cites its owning issue or
    decision, protocol and configuration mechanics link to their
    authoritative sources instead of being restated, and the future
    Conversation entity is unambiguous against the current
    session-oriented store everywhere the two meet, including
    backlinks from `reference/domain-config.md` and
    `reference/conversations-schema.md` made through their
    generators.
12. **#312 begins with a human-reviewed structure choice** for
    `xiaozhi-notes.md` (one combined document with explicit section
    authority, or a split), implemented end to end only after
    explicit acceptance, with the stable path preserved as the
    maintained document or a compatibility index, clone commands
    easy to find, board-specific behavior owned by device guides,
    and activation, wake-word, MCP-discovery, listening-mode, OTA
    and compatibility claims reconciled across current
    documentation.
13. **#313 audits the summaries last**: root, server and firmware
    READMEs, `AGENTS.md`, glossary, the regression suite, device
    guide introductions and generated-reference introductions stay
    concise, agree with the authoritative sources, link them
    directly, and introduce no unique commitment.
14. **Out of scope, from #310**: behavior changes, reconsidering
    accepted decisions beyond correcting documentation that
    contradicts them, rewriting dated records, fully restructuring
    concepts, the glossary or every device guide, tables of contents
    everywhere, redesigning diagram content, or one big document.

## Positions taken against the issues, maintainer-confirmed

The issues came from an external reviewer, and two of their calls
were re-litigated with the maintainer on 2026-08-26 before this plan
was written; both outcomes are settled inputs here.

- **The diagrams directory move is implemented as written.** This
  plan's author argued for keeping the tool directories in place and
  hosting the question index on the landing page; the maintainer
  chose the issue's structure. The move is a pure `git mv` (renders
  and sources byte-identical), and the Excalidraw scene-mapping note
  moves with it.
- **Guidelines keep the house idiom as their core.** Every guideline
  carries one example and one counterexample, as every principle
  does today and as `AGENTS.md` advertises; the issue's template
  fields (Default, Apply when, Reconsider when, Evidence) are added
  where they earn their place, which the acceptance criterion's
  "where applicable" licenses. Reconsider-when is expected wherever
  a reopen condition already exists (the pipeline-ownership
  triggers); a rigid five-field recast of every entry is not done.

## Design decisions this plan makes

1. **The CLI audit's dated home is
   `docs/architecture/cli-guide-audit.md`.** The issue offers the
   CLI recut implementation record, but that record is #223's and
   the audit was written for #285, which deliberately had no plan;
   grafting ~250 lines of source review onto another issue's record
   would misattribute it. `features/` has a fixed shape (Problem,
   Changes, Verification) that an audit does not fit, and `plans/`
   holds plans. So the audit becomes a research record beside its
   guide, dated 2026-08-24 in its header, classified under the
   taxonomy's research class, and linked from the guide's concise
   source-and-disposition summary.
2. **The Identity section opens `guidelines.md`, and the promises
   page's own introduction stays non-normative.** Identity assigns
   implementation ownership between vinga and the conversation
   runtimes, which is revisable direction, so it belongs with the
   guidelines it frames (it is the umbrella over the hardware-edge
   guideline). `product-promises.md` opens with a short
   introduction saying what a promise is, that promises outrank
   guidelines, and how one changes; its authoritative contents are
   exactly the three promises.
3. **The compatibility page carries no anchors.** No file in the
   repository links a `principles.md#fragment` (grepped 2026-08-26:
   zero hits), so the stub is a title, one paragraph saying what
   moved where and why the path survives, and the two links.
4. **The database promise is rewritten against the ADR as amended,
   not softened.** The promise's letter (beta starts the upgrade
   obligation; until then best-effort, forward-only, with rewrites
   only by recorded decision) is exactly the floor ADR's Decision
   and stays. What changes is the worked material: the current
   example is the priced exit exercised twice by recorded addenda
   (#243's squash, #283's Postgres re-baseline), and the
   counterexample is an unrecorded reset, since the recorded kind
   has now legitimately happened and the old counterexample text
   ("folding the four configuration revisions") describes it. The
   promise also states the operational floor as it stands: in-place
   upgrades begin at the two Postgres baselines
   (`3001_postgres_domain`, `1001_postgres_conversations`), the
   current build opens no SQLite file at all, a pre-beta recorded
   decision may still require a reset, recovery is export and
   reapply with secrets re-entered from the environment, and
   conversation history crossed the Postgres cutover only by manual
   archiving. It links the server README's upgrade and recovery
   procedure rather than restating it.
5. **Milestone order inside #310 is navigation first.** M1 lands
   the taxonomy and the landing pages while `principles.md` and the
   guides are still in their old shapes; the architecture router
   simply lists the pages that exist, and the milestone that
   reshapes a page updates its router row in the same change. Every
   merge leaves the docs coherent; none waits on a later milestone.
6. **Status claims in the observability map are re-verified from
   the repository**, not carried from this plan: the milestone
   subagent reads `vinga-server/src/vinga_server/events/` and the
   generated references to state what is current (the event
   registry and the conversation store are believed landed, the
   audit surface still future), and cites what it verified.
7. **Concepts status marking is a status line, not a badge
   system.** Each section of `concepts.md` that is not purely
   implemented opens with a bold status line, either
   `**Implemented today.**` or `**Decided direction**` naming the
   owning issue or decision; claims inside a mixed section carry
   the same marking inline. No tables, no icons, nothing a future
   editor has to maintain in two places. M4 opens by building the
   owner mapping with tooling (a `gh` sweep over the tracker plus a
   grep over `docs/adr/` and `docs/features/`) and committing it to
   the implementation doc before the page is edited. Owners known
   now: the durable device record (#96), activation (#40), the
   device guides feeding the help agent (#93), the wake-word-audio
   open question (#112), the conversation store's implemented
   session-and-turn records (#120), and the agent-not-persona
   naming (the 2026-08-12 feature doc). Directions with no
   accepted owner (expected: the cross-session Conversation
   entity's semantics, switch-for-the-session, suspend-never-end,
   clean-switch-by-default, the shared user profile, agent-scoped
   search, meta-turn recording, users and voiceprints) are marked
   honestly as `**Decided direction** (recorded on this page,
   YYYY-MM-DD; no owning issue or decision record yet)`, and the
   M4 PR enumerates them so the maintainer can adopt or file
   owners; #311 does not itself become a decision record, and this
   plan does not mint ADRs for domain semantics.
8. **The M5 recommendation brought to #312's gate is one combined
   document** with authority-labeled sections (maintained protocol
   facts; dated field observations; historical upstream research;
   licensing evidence), because the
   file's chief value is being the single "read this first"
   protocol entry point that `AGENTS.md`, the skills and thirteen
   current callers point at. The alternative put beside it: a split
   into a maintained protocol reference plus a dated research file,
   with `xiaozhi-notes.md` as a compatibility index. The maintainer
   chose the combined document during the plan review round.
   Board-specific current guidance is not one of its sections:
   live board facts and procedures (the Touch-LCD serial, reset
   and NVS procedures, per-board portal behavior, per-board
   hardware detail) move to the owning device guide or the device
   guides' common page, the notes keep only shared protocol
   behavior plus dated field evidence with provenance, and the
   AGENTS.md hardware-context pointer follows the procedures to
   their new home in the same milestone.
9. **Root README edits stay minimal until the end.** #309 is open
   and reshapes the root README's quick start; M1 touches only the
   overview image path and the architecture pointer, and M6, the
   summary audit, rebases onto whatever #309 landed before reading
   any summary.
10. **Link checking is a throwaway script, not a committed tool.**
    A short Python script in the session scratchpad resolves every
    relative link and heading anchor across `README.md`,
    `AGENTS.md`, `docs/`, `vinga-server/README.md` and
    `vinga-esp32/README.md`, and runs before every milestone PR;
    committing a checker into CI would touch the workflow for a
    docs issue and is left as a possible follow-up issue. The
    workflow's own five drift checks cover the generated documents.
11. **On this page sections land where the policy says and nowhere
    else**: `system-overview.md`, `guidelines.md`,
    `design-guide.md`, `cli-guide.md`, `observability-surfaces.md`
    (M3), the restructured Xiaozhi notes (M5), and
    `product-promises.md` only if its final length warrants one.
    `concepts.md` is judged by its post-#311 length in M4.
12. **The skills are current callers, and M6 leaves them
    current.** `.claude/skills/` files referencing
    `architecture/principles.md` update in M2 with the other
    callers, and M6 removes the implement-issue skill's two
    "being reorganized by #310 to #313" caveats, replacing them
    with a pointer to the authority taxonomy in `docs/README.md`,
    so the reorganization does not finish while live workflow
    guidance says it is underway. The broader skill refactor
    review the maintainer asked for stays follow-through after
    M6; only the staleness the reorg itself creates is #313's.
13. **Every milestone carries its changelog entry.** Each PR
    appends a dated `### Changed` entry to `CHANGELOG.md` naming
    what a reader of the documentation now finds moved or split
    (the authority taxonomy and landing pages in M1, the
    promises/guidelines split in M2, the guide recuts in M3, the
    concepts status marking in M4, the protocol-notes
    reorganization in M5, the summary audit in M6), per the
    AGENTS.md rule that notable changes update the changelog.

## The standing review lenses, pre-answered

- **No-leak.** Documentation-only, but the lens still binds what
  examples may show: no real device identifiers, no infrastructure
  hostnames, no credential-shaped values enter any page (the
  public-repo anonymization stance); the M4 generator prose carries
  titles and links only, no runtime values. Nothing here touches an
  exception path or an event field.
- **Pin before reshaping.** The analogue for documents: the diagram
  move is a pure `git mv` proven by `git diff --stat` showing
  renames only; generated references stay byte-identical in every
  milestone except M4, where the diff is exactly the generator
  change's output, reproduced by the drift checks; the walkthrough
  and the audit move by cut-and-paste, with any wording change
  called out in the PR rather than slipped into the move.
- **Closed sets mapped to decision sites.** The authority taxonomy
  is a closed set of seven classes declared once in
  `docs/README.md`, which classifies every page centrally, by
  directory where a directory is homogeneous (`adr/`, `plans/`,
  `features/`) and by page where it is not. A page-level authority
  introduction is carried only by maintained hand-written pages,
  and by generated Markdown only through its generator; dated
  records, generated JSON, and pages this reorganization does not
  otherwise touch are classified by the index alone and not
  edited.
- **Honest seams.** Not applicable to prose; the nearest analogue,
  summaries that quietly become second sources of truth, is exactly
  what #313 exists to close.
- **Inventories by tooling.** The current-caller census was grepped
  2026-08-26 and re-grepped the same day with relative-link-aware
  patterns after the review round caught the first pass missing
  in-directory links. `principles.md` has these current callers:
  AGENTS.md and the two skills (path references in prose),
  `docs/README.md`, `docs/adr/README.md`, `docs/concepts.md`, the
  architecture README, `design-guide.md` (three links),
  `cli-guide.md` (two links), and `pipeline-ownership.md` (one
  link, and the file retires in the same milestone).
  `pipeline-ownership.md` itself has one current caller
  (`docs/README.md`); the architecture README has four; the
  diagram paths appear in the root README's image plus
  `docs/README.md` and the architecture README's seven relative
  references; `cli-guide.md` has three callers,
  `observability-surfaces.md` two, `xiaozhi-notes.md` thirteen
  current files. No `principles.md#` anchor exists anywhere.
  Counts are refreshed after every rebase, censuses always use
  patterns that catch both path-prefixed and same-directory
  relative spellings, and every moved-path search result at the
  end is classified current, historical, or error.

## Module layout

```text
docs/
  README.md                    # global navigation + authority taxonomy
  system-overview.md           # NEW: the conversation walkthrough
  concepts.md                  # future-aware domain model (M4)
  glossary.md                  # lookup adapter (unchanged role)
  xiaozhi-notes.md             # restructured in M5 (combined, labeled)

  architecture/
    README.md                  # THE architecture index, by reader question
    product-promises.md        # NEW: the three promises
    guidelines.md              # NEW: identity + revisable defaults
    principles.md              # compatibility page (a few lines)
    design-guide.md            # unchanged + On this page
    cli-guide.md               # checklist-first (M3)
    cli-guide-audit.md         # NEW: the dated 2026-08-24 source audit
    observability-surfaces.md  # current data map first (M3)
    diagrams/
      README.md                # NEW: index by reader question
      excalidraw/              # moved as-is
      plantuml/                # moved as-is
```

`pipeline-ownership.md` is deleted in M2.

## Tests

- The five generated-document drift checks (domain-config,
  conversations-schema, events, api-openapi, the two generated
  halves of cli.md) via the integration lane; M4 changes their
  inputs through the generators, M6 may where its audit finds a
  generated introduction making an unsupported claim (also through
  the generators), and every other milestone leaves
  `docs/reference/` untouched.
- The scratchpad link-and-anchor script, run green before every PR.
- `uv run ruff check .`, `uv run pytest tests/unit -q`,
  `uv run pytest tests/integration -q` from `vinga-server/` for M4
  (the docgen change) and for M6 whenever it modifies a generator,
  together with regeneration and every drift check; the other
  milestones do not touch the package but state so rather than
  claiming runs they did not need.
- Rendered inspection of every changed page (headings, links,
  On this page anchors), recorded in each PR's verification list.

## Risks and mitigations

- **Historical-link erosion.** Dated records link `principles.md`
  and `xiaozhi-notes.md` by path; both paths survive (stub, stable
  entry point), and the end-state search classifies every remaining
  old-path reference on purpose.
- **The Excalidraw scene mapping.** Committed exports move but the
  workspace scenes do not; the sync note moves with the files and
  names the new paths, and the renders are byte-identical, so no
  re-export happens and no drift is introduced.
- **Collision with #309.** Its README rewrite may land mid-chain;
  M1's README touch is two lines, and M6 rebases before auditing.
- **Scope creep toward rewriting every page.** The taxonomy work is
  classification lines and link moves; #310's out-of-scope list is
  restated in every milestone brief, and a page not named by a
  milestone is not edited by it.
- **Transient index states between milestones.** Accepted by design
  decision 5; each milestone updates the router rows for the pages
  it moves, in the same change.

## Milestones

- [x] **[M1: One landing page per corpus, and the walkthrough moves
  out.](2026-08-26-docs-authority-reorg-implementation.md#m1-one-landing-page-per-corpus-and-the-walkthrough-moves-out)**
  (PR #321) `docs/README.md` gains the authority taxonomy (its seven
  classes, each with the pages that hold them) while keeping
  audience as the routing concern, and its Architecture section
  collapses to one pointer; `docs/architecture/README.md` becomes
  the architecture index organized by reader question (designing a
  feature, adding a command, understanding a conversation, placing
  data); the conversation walkthrough and its "what the diagram
  leaves out" coda become `docs/system-overview.md` with an
  On this page section; the diagram directories move under
  `architecture/diagrams/` with a question-organized
  `diagrams/README.md`; the root README's image path and
  architecture pointer, the regression suite's walkthrough link,
  the glossary's walkthrough link (`glossary.md:7`), and
  `docs/README.md`'s diagram paragraph update. Design
  footprint: `docs/README.md` deepens into the one authority
  interface; the architecture README becomes a pure router, so its
  callers stop having to know the tree; the walkthrough gets the
  module its audience implies. Documentation footprint: the pages
  named are the footprint; no behavior claim changes.
- [x] **[M2: Promises split from guidelines, and pipeline ownership
  retires.](2026-08-26-docs-authority-reorg-implementation.md#m2-promises-split-from-guidelines-and-pipeline-ownership-retires)**
  (PR #322) `product-promises.md` (a non-normative introduction,
  then the stock-firmware floor, fully local, and the database
  promise per design decision 4);
  `guidelines.md` (identity as the framing preamble; thin device;
  the consolidated hardware-edge
  guideline with its litmus table and `ConversationBackend`
  counterexample as subrules; decision ownership recast; the
  owned-versus-shared runtime guideline with the reopen triggers,
  absorbing `pipeline-ownership.md`, whose dated measurements
  remain only in the spike record and whose file deletes);
  `principles.md` becomes the compatibility page; every current
  caller links the new pages in this same milestone: AGENTS.md,
  `concepts.md`, `adr/README.md` as governance, the two skills,
  the architecture router rows, and the three `design-guide.md`
  and two `cli-guide.md` links, so no current caller routes
  through the compatibility page at any merge.
  Design footprint: two modules whose parts change for different
  reasons stop sharing a file; the stub is a deliberate adapter for
  immutable callers. Documentation footprint: AGENTS.md's design
  section and the ADR README's description of document types are
  the summaries this milestone stales, and both update here.
- [x] **[M3: The guides lead with their
  interfaces.](2026-08-26-docs-authority-reorg-implementation.md#m3-the-guides-lead-with-their-interfaces)**
  (PR #323) `cli-guide.md`
  puts the reviewer checklist after its purpose introduction with
  each item linked to its practice, links `reference/cli.md` for
  current spellings, keeps a concise source-and-disposition
  summary, and sheds the audit to `cli-guide-audit.md` (design
  decision 1); `observability-surfaces.md` leads with the
  four-surface current map (status column verified per design
  decision 6), keeps the invariants, links the generated references
  for exact vocabulary, and dates the needs assessment and external
  survey as a decision-evidence appendix; On this page sections
  land on `cli-guide.md`, `observability-surfaces.md` and
  `design-guide.md`. Design footprint: each guide's checklist or
  map becomes its small interface, with the evidence behind it
  instead of in front of it. Documentation footprint: the
  architecture router rows for both guides update in this change.
- [x] **[M4: Concepts distinguishes today from direction
  (#311).](2026-08-26-docs-authority-reorg-implementation.md#m4-concepts-distinguishes-today-from-direction)**
  (PR #324) The introduction states the page's class and its authority
  relative to promises, guidelines, ADRs, issues and generated
  references; every section or claim carries its status per design
  decision 7, each decided direction citing its owning record
  where one exists and carrying decision 7's recorded-here status
  line otherwise; device-fact,
  wake-word, listening-mode, tool-discovery, activation and
  reload mechanics link `xiaozhi-notes.md` and the configuration
  references instead of restating them; the future Conversation
  entity and the current session-and-turn store are disambiguated
  in both directions, the reference side through the generators;
  glossary inbound links re-point; the drift checks and the full
  test suite run. Design footprint: the page
  stops being a second protocol description and becomes the domain
  model with links where implementation facts live. Documentation
  footprint: `reference/domain-config.md` and
  `reference/conversations-schema.md` change through their
  generators; the glossary rows that pointed into replaced sections
  update.
- [x] **[M5: The Xiaozhi notes get the accepted
  shape.](2026-08-26-docs-authority-reorg-implementation.md#m5-the-xiaozhi-notes-get-the-accepted-shape)**
  (PR #325)
  Implement the accepted combined document with authority-labeled
  sections (the gate was cleared during the plan review round),
  including the upstream currency markers recording the vendor
  clones' commits, the observed firmware versions and the date;
  clone commands stay at the top; dated observations, upstream
  research and licensing evidence keep their dates and provenance; board-specific current facts
  and procedures move to the owning device guide or the common
  device page per design decision 8, with the AGENTS.md
  hardware-context pointer following them, and shared protocol
  behavior is linked from the guides rather than copied; activation, onboarding, wake-word-data (#112 stays open),
  MCP-discovery, listening-mode, OTA-routing and stock-firmware
  claims are reconciled across the current pages; On this page per
  the policy. Design footprint: current wire facts get one
  authoritative home that firmware work can cite without reading
  history. Documentation footprint: the pages whose claims the
  reconciliation corrects are enumerated in the PR, not discovered
  by its readers.
- [ ] **M6: The summaries answer to their sources (#313).** After a
  rebase over whatever #309 landed: the root, server and firmware
  READMEs, AGENTS.md, the glossary, the regression suite, the
  device-guide introductions and the generated-reference
  introductions are audited to summarize and link the authoritative
  source without introducing any unique commitment (a
  generated-introduction correction goes through its generator,
  and that PR then owes lint, both test lanes, regeneration and
  the drift checks); the
  authority-sensitive phrase search (`product promise`,
  `compatibility floor`, `fully local`, `thin device`, `decided
  direction`, `implemented today`) classifies every current hit as
  source or summary; the observability four-surface model and event
  vocabulary are confirmed hand-copied nowhere; the
  implement-issue skill's two reorg caveats are replaced with the
  taxonomy pointer per design decision 12; the final
  link-and-anchor run and moved-path classification close #310's
  verification list. Design footprint: none; this milestone deepens
  nothing and exists to prove locality. Documentation footprint:
  whatever the audit corrects, enumerated in the PR.

## Plan review round

External review of commit `15a0d37e` (backend codex 0.149.1, model
`gpt-5.6-sol`, 2026-08-26, runtime 4m50s, 201,815 tokens). Verdict:
not ready; ready after the P1/P2 amendments. Findings condensed but
faithful; resolutions appended per amendment commit.

1. **P1: Identity in `product-promises.md` recreates the authority
   mixing the issue removes.** The Identity section assigns
   implementation ownership between vinga and conversation runtimes
   (`principles.md:26`), which is revisable architecture direction,
   not an externally falsifiable promise; design decision 2 puts the
   whole section on the promises page. Move the appliance-versus-
   runtime ownership statements to `guidelines.md`; the promises
   page may keep only a short non-normative introduction, its
   authoritative contents the three promises.

   *Resolution.* Adopted. Design decision 2 now places Identity as
   the framing preamble of `guidelines.md`, and the promises page
   opens with a non-normative introduction whose authoritative
   contents are exactly the three promises; M2's deliverables are
   updated to match.

2. **P1: M4 assumes owning decisions exist for domain direction
   that has no owner.** Cross-session conversations, switch
   semantics, shared profiles, agent-scoped search and meta-turn
   recording (`concepts.md:117,157,235`) cite no issue or ADR, and
   the plan only says each direction will cite "its owner".
   Enumerate an owner mapping before implementation; where no
   accepted owner exists, either create an accepted decision record
   or mark the claim as unowned proposed direction. #311 cannot
   silently become the decision owner.

   *Resolution.* Adopted, in the marking form: design decision 7
   now requires M4 to build the owner mapping with tooling before
   editing, commits the known owners into the plan (#96, #40, #93,
   #112, #120, the agent-not-persona feature doc), and marks every
   direction without an accepted owner as recorded-here-with-no-
   owner, enumerated in the M4 PR for the maintainer to adopt or
   file. No decision records are minted by a documentation
   milestone.

3. **P2: The database promise still omits the operational
   compatibility floor.** "Best-effort, forward-only" misses that
   the floor now begins at the two Postgres baselines, that the
   build cannot open SQLite at all, and that conversation history
   is explicitly not migrated (floor ADR, 2026-08-26 addendum).
   The promise must state the current floor revisions, that
   pre-beta recorded decisions may require a reset, and the
   export/reseed path with manual archiving of history.

   *Resolution.* Adopted. Design decision 4 now requires the
   promise to state the two Postgres baseline revisions as the
   in-place floor, that the build opens no SQLite file, the
   possibility of a recorded pre-beta reset, the export-and-reapply
   recovery with environment-sourced secrets, and the
   manual-archiving fate of conversation history, linking the
   server README's procedure.

4. **P2: The `principles.md` caller census omits live architecture
   guides.** `design-guide.md` and `cli-guide.md` are current
   callers via relative links (three and two respectively) that the
   census's path-prefixed grep missed. M2 must update every link in
   both guides directly; deferring the CLI guide's to M3 routes a
   current caller through the compatibility page and contradicts
   the coherent-per-merge claim.

   *Resolution.* Adopted. The census is corrected in the lenses
   section with the relative-link-aware counts and a standing rule
   that censuses catch same-directory spellings, and M2 now names
   the five in-guide links among the callers it updates, so no
   current caller ever routes through the compatibility page.

5. **P2: M1 misses a current caller of the moved walkthrough.**
   `glossary.md:7` links the architecture README specifically as
   the teaching walkthrough; M1 names the root README and the
   regression suite only. Repoint the glossary in M1.

   *Resolution.* Adopted; M1 now names the glossary's walkthrough
   link among its updates.

6. **P2: M6's generated-reference audit conflicts with the declared
   change and test boundary.** The plan says only M4 changes
   generators, yet M6 audits generated-reference introductions,
   and a correction there (for example the compatibility sentence
   in `conversations-schema.md:17`) can only be made through
   `conversations/docgen.py`. Either M4 recuts every generated
   introduction exhaustively or M6 may modify generators and then
   owes lint, both test lanes, regeneration and every drift check.

   *Resolution.* Adopted in the second form: M6 may modify
   generators where its audit requires, and the Tests section and
   M6 both now carry the full obligations (lint, both lanes,
   regeneration, drift checks) for that case. M4 is not asked to
   pre-empt an audit that has not run yet.

7. **P2: The plan deliberately leaves current skill guidance stale
   at completion.** The implement-issue skill says the tree "is
   being reorganized by #310 to #313" in two places; postponing the
   caveat removal past M6 finishes the reorganization while live
   workflow guidance says it is underway, violating the
   current-versus-historical distinction. M6 should remove or
   rewrite those caveats as part of #313.

   *Resolution.* Adopted. Design decision 12 and M6 now replace
   the two caveats with a pointer to the taxonomy inside M6; the
   maintainer's broader skill-refactor review stays follow-through
   after the chain.

8. **P2: M5 does not enforce device-guide ownership of
   board-specific facts.** The combined-document recommendation
   keeps "validated device procedures", and M5 only says
   board-specific behavior "cites" device guides, while the notes
   hold live board facts (board hardware, portal behavior, the
   Touch-LCD serial procedure). Current board-specific guidance
   moves to the owning device guide; the notes may keep dated field
   evidence with provenance but not a competing maintained source.

   *Resolution.* Adopted. Design decision 8 and M5 now move live
   board-specific facts and procedures to the owning device guide
   or the common device page, with the AGENTS.md hardware-context
   pointer following them; the notes keep shared protocol behavior
   and dated evidence only.

9. **P2: The per-page classification invariant is impossible as
   stated.** "Every page under `docs/` is classified ... on the
   index line and the page's own introduction" collides with the
   prohibition on editing unnamed pages, with immutable dated
   records, and with generated JSON that cannot carry a Markdown
   introduction. Classify classes and directories centrally in
   `docs/README.md`; require page-level authority introductions
   only for maintained hand-written pages and for generated
   Markdown whose generator owns the introduction.

   *Resolution.* Adopted. The lens now states central
   classification in `docs/README.md` (by directory where
   homogeneous, by page where not), with page-level authority
   introductions only on maintained hand-written pages and on
   generated Markdown through its generator; dated records,
   generated JSON and untouched pages are classified by the index
   alone.

10. **P2: No milestone owns the required changelog update.**
    AGENTS.md requires `CHANGELOG.md` for every notable change;
    all six milestone footprints omit it. Add dated `### Changed`
    entries, at minimum for the authority split, the landing-page
    move, and the protocol-document reorganization.

    *Resolution.* Adopted, wider than asked: new design decision
    13 gives every milestone its own dated `### Changed` entry in
    the same PR.

### Maintainer decisions during the round (2026-08-26)

- **The #312 gate is cleared ahead of M5**: the maintainer accepted
  the recommended structure, one combined `xiaozhi-notes.md` with
  authority-labeled sections. M5 implements that shape; the gate is
  not re-asked, and the acceptance is recorded in the
  implementation doc's M5 section.
- **Upstream catch-up markers join M5's scope**: the maintained
  protocol sections gain an explicit currency statement recording
  which upstream `xiaozhi-esp32` and `xiaozhi-esp32-server`
  commits the facts were last read against (taken from the vendor
  clones' SHAs), which firmware versions were actually observed on
  boards, and the date, so what the notes have caught up to is a
  stated, bumpable fact. This matches the stock-firmware promise's
  version target (boards in the field, not upstream HEAD).

## Delta review round

Delta re-review of the ten amendments (backend codex 0.149.1, model
`gpt-5.6-terra`, 2026-08-26, on the amended plan branch). Verdict:
ready after amendments; seven of ten resolutions verified faithful
and sufficient, three localized consistency repairs found:

1. **P2: The module layout still labels `product-promises.md` as
   "identity + the three promises"**, contradicting the finding-1
   resolution that moved Identity to `guidelines.md`.

   *Resolution.* Fixed; the layout now labels the promises file
   with the three promises and the guidelines file with identity.

2. **P2: M4 still unconditionally requires "each decided direction
   citing its owner"**, though design decision 7 defines the
   no-owner marking for directions that have none.

   *Resolution.* Fixed; M4 now requires an owning record where one
   exists and decision 7's recorded-here status line otherwise.

3. **P2: Three stale gate references contradict the recorded
   maintainer decision**: M4 sends the #312 question to the
   maintainer, M5 permits either structure, and Risks says the
   gate can stall, though the gate was cleared during the plan
   round.

   *Resolution.* Fixed; the M4 gate action and the stall risk are
   removed, and M5 now implements the accepted combined
   authority-labeled document, naming the upstream currency
   markers in its body.
