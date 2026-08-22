# Pin the event catalog once, not three times

## Goal

Implement issue #241, the closer of the events phase of #246. Three
committed, CI-enforced artifacts pin the same catalog:
`docs/reference/events.md` (byte-diffed in CI), the golden inventory
JSON (structural containment both directions), and the baseline JSON
(driven-record capture). Every event change costs three
regenerations and three review surfaces, and no second party
consumes the JSON artifacts. After this issue, `events.md` is the
single committed pin; the guarantee the baseline uniquely provides
(every declared variant is actually driven, with the shape its
declaration says) survives as a live assertion that needs no
committed fixture; the golden is deleted.

The companion implementation doc,
[`2026-08-22-pin-catalog-once-implementation.md`](2026-08-22-pin-catalog-once-implementation.md),
records what the milestone actually did, deviations from this plan,
and discoveries; no deviations says so explicitly.

## The issue's decisions, restated

1. **`docs/reference/events.md` stays the single committed,
   CI-diffed artifact**, the human contract.
2. **The baseline's guarantee converts to a plain test with no
   committed fixture**: every declared variant is actually driven.
3. **The golden JSON is dropped.**
4. **Non-goal**: `docs/reference/api-openapi.json` is untouched; it
   stays the API contract with the CLI (#194, #223) and the admin
   UI (#129).

## Design decisions this plan makes

1. **The harness stays; the committed capture goes.** The 81-driver
   harness in `tests/tools/event_baseline.py` is the machinery that
   makes the live guarantee checkable and it does not change. What
   is deleted is `tests/unit/data/event-baseline.json` and the
   tests that exist only to compare bytes against it
   (`test_the_capture_is_the_committed_baseline`,
   `test_the_committed_file_is_what_the_harness_writes`) plus the
   `__main__` regeneration path. What survives in
   `test_event_baseline.py`, now against the live capture instead
   of a file: every driver names a path of its own, every driven
   path produces the event it emits, every catalog variant on a
   scoped channel is produced, every driven record's payload values
   are builtins (#252's pin), and the shapes-not-values check
   becomes moot with no file to hold values (deleted with its
   subject). The suite keeps its module-scoped single drive.
2. **The shape guarantee is held against the declarations, live,
   with the one thing declarations cannot say held in a live
   table.** The committed capture pinned channel, level, template,
   argument types, and payload key sets per path. Channel, level,
   and template are derivable from the matched declaration and the
   live suite holds every produced record to them, template
   equality included. Payload key sets are NOT derivable:
   `matches()` asserts a range (`required <= keys <= declared`),
   and a path that stopped carrying its optional fields (a
   regressed entry quartet, dead usage plumbing) would pass every
   range check while `events.md` did not move. So the suite gains
   a live per-driver table, driver key to expected carried-key
   set, asserted exactly; it is a declaration inside the suite
   rather than a committed artifact, which is what the issue asked
   for, and updating it is part of changing what a path carries.
   What is genuinely given up, stated plainly: the committed
   file's third-opinion property, catching the catalog and the
   code moving together in a way a reviewer did not intend. That
   is the audit's priced decision: `events.md` remains the
   reviewed artifact where a catalog change is a visible diff.
3. **The golden's suite and data file are deleted whole.**
   `test_event_golden.py` and `event-catalog-golden.json` go; their
   unique guarantees (ordered field lists, argument type names,
   token sets recorded structurally) are consciously retired, since
   `events.md` renders the same facts for review and CI byte-diffs
   it. The regeneration command dies with the file, and the three
   prose sites that tell a developer to run it (the golden test's
   own header, `AGENTS.md` if it names it, any implementation-doc
   references are historical and stay) are corrected.
4. **`events.md` regeneration is unchanged**, and stays in CI as
   is. The reference's own docs suite (`test_event_docs.py`)
   already holds the committed file to the catalog line by line and
   is untouched.
5. **Recent history's proof style is acknowledged.** The last three
   issues used "baseline SHA unchanged" as their
   behavior-preservation proof; this issue deletes that instrument.
   Future refactors prove stillness with the live suite green plus
   the `events.md` byte diff, and where a byte-level record
   comparison is genuinely needed, a branch can regenerate the
   capture locally for the duration of the work (the harness still
   answers `captured()`); the implementation doc records this as
   the successor practice.

## The standing review lenses, pre-answered

- **No-leak.** No production surface changes at all; deletions are
  test-side and data-side. The sentinel suites are untouched.
- **Pin before reshaping.** This issue IS a pin change and is the
  audit's decision to make it; nothing behavioral is reshaped under
  it (src is untouched), so there is nothing the deleted pins were
  protecting mid-flight.
- **Closed sets, honest seams.** Not this issue's territory.
- **Inventories by tooling.** `grep -rn "golden\|baseline"
  vinga-server/tests vinga-server/src .github/workflows` before and
  after bounds the removal; after the milestone the only survivors
  are the harness module's own name (`tests/tools/event_baseline.py`,
  which keeps it: it is the driver inventory, not a baseline file),
  `test_event_baseline.py`'s live suite, and unrelated uses of the
  words (the CI wheel-migration comment's "baseline script" means
  Alembic and stays).

## Module layout

- `tests/unit/test_event_baseline.py`: byte-comparison tests and
  the file constant deleted; live-against-declaration checks
  survive or are strengthened per decision 2.
- `tests/tools/event_baseline.py`: the `__main__` regeneration
  block, `rendered()`, `committed()` deleted; `captured()`,
  `driven()`, `shape()`, the drivers, and `driver_times.py`'s
  imports untouched.
- `tests/unit/data/event-baseline.json`,
  `tests/unit/data/event-catalog-golden.json`,
  `tests/unit/test_event_golden.py`: deleted.
- `AGENTS.md` and any live doc naming the regeneration commands:
  corrected (historical plan/implementation docs stay as records).
- `CHANGELOG.md`: `### Removed` entry (two committed pins retired;
  the guarantees that survive and where).

## Milestones

- [ ] **M1: retire the two JSON pins.** (PR TBD) One milestone,
  test-and-data-side only; `main` stays releasable trivially (no
  production code moves). Deepens nothing and deletes two review
  surfaces; the one interface change a developer feels is that
  event work stops requiring two regeneration commands.

## Plan review round

External review of commit `2583f98a`, 2026-08-22 late. Backend: claude CLI 2.1.239, model `claude-opus-5`, read-only
tool set (interim fallback tier). Verdict as received: ready after
the P1 and P2 amendments; the deletions are sound and the issue's
decisions respected, but two of the plan's three load-bearing
justifications were wrong as written and the removal inventory
named three prose sites where the tree has about a dozen. Findings
condensed but faithful:

1. **P1: the derivability claim is false for payload key sets.**
   `matches()` asserts a RANGE (`required <= keys <= declared`),
   while the committed capture pinned each path's EXACT key set; a
   regressed `_entry_fields` or dead usage plumbing would pass
   everything that survives. Name per-site optional-field presence
   as a second loss and decide its replacement (a live in-test
   table of driver key to expected carried keys) or accept it.
2. **P1: `events.md` does not render argument identity.** The
   Argument cell is the ArgKind name; ordered field lists and token
   sets survive in the reference, the ordered argument list does
   not, and the one-shape implementation doc says the golden was
   the only committed pin on that retype. Reversing `Handover.ARGS`
   moves nothing committed. State the loss as its own priced
   decision or restore the pin inside the kept artifact by printing
   the declared field name in the Argument column.
3. **P1: the prose inventory is wrong.** About a dozen live sites
   name the deleted artifacts as the pin, two of them
   coverage-delegation claims (`test_event_catalog.py`'s header,
   the surface/server pin suites'); `AGENTS.md` names neither
   command; and two docstrings in `src/vinga_server/events/
   catalog.py` change, so "no production surface changes at all"
   needs qualifying.
4. **P2: the grep bound excludes the trees the plan says to
   correct.** Run it repo-wide and list the expected survivors
   (historical plans, the CI wheel comment's Alembic "baseline").
5. **P2: the two module docstrings that would become the largest
   false statements are not named as work**; both are rewritten,
   and the harness states what "baseline" means with no committed
   capture.
6. **P2: dead names in both directions.** `COMMITTED`,
   `REGENERATE`, `shape()`'s `argument_types` once unread,
   `captured(produced=None)`'s only caller being `__main__`, and
   the #210 walk relics (`MODULES`, `PACKAGE`, `LEVEL_METHODS`,
   `TYPED_METHOD`, `SESSION_RECEIVER`). List them, decide each.
7. **P2: no Tests section.** Name the mutations that must go red
   (a moved TEMPLATE, a changed LEVEL, an unmatched record, a
   half-quartet `llm_round`) and the suite's count change; the
   repository's practice on this suite was mutation, not argument.
8. **P2: `test_the_store_says_nothing_else` is in neither list.**
   It survives with its docstring corrected.
9. **P3: the successor practice contradicts the layout** (it says
   "regenerate" while deleting `rendered()` and `__main__`); pick
   capture-twice-in-memory and drop the word.
10. **P3: the harness has a second in-suite consumer**
    (`test_event_surface_pins.py` imports `Failing`,
    `failing_reply`, `turned_away`); name it in the inventory.
11. **P3: the no-leak lens ignores the surface that changes.** The
    live suite works from records carrying real values (a planted
    API token among them); state that the new assertions report
    channel, level, template, key names and type names only.
