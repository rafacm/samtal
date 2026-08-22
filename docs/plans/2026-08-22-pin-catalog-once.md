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
2. **The shape guarantee is held against the declarations, live.**
   The committed capture pinned channel, level, template, argument
   types, and payload key sets per path. Everything in that list is
   derivable from the declaration a produced record matches, and
   `matches()` already checks record against declaration; the live
   suite therefore holds every produced record to its declaration
   (template equality included) rather than to a snapshot of
   itself. What is genuinely given up, stated plainly: a pin that
   would catch the CATALOG and the code moving together in a way a
   reviewer did not intend (the committed file was a third opinion
   neither the code nor the declarations gave). That is the audit's
   priced decision: drift between generator and committed copy is
   cosmetic here, and `events.md` remains the reviewed artifact
   where a catalog change is a visible diff.
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
