# Classify capability rows by a declared kind: implementation

The companion to
[`2026-09-04-capability-row-kinds.md`](2026-09-04-capability-row-kinds.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations says
so explicitly.

## M1: the kind, declared and pinned

PR [#392](https://github.com/rafacm/vinga/pull/392). Closes
[#303](https://github.com/rafacm/vinga/issues/303).

### What landed

The whole issue, in five commits.

- `RowKind = Literal["message", "prose"]` on
  `simulator/capabilities.py`, with `MESSAGE` and `PROSE` beside the
  three side constants and the alias and both constants in `__all__`.
  The field is required on `Capability` with no default, so a
  construction site that forgets it is a `TypeError` at import rather
  than a row classified by nobody. All 22 construction sites carry it:
  the 20 `_PROSE_ROWS` and the two returns of `_classified`, which is
  the one place a derived message row is built. The trap comment above
  the firmware row is deleted and the row's own words are untouched,
  so no rendered byte moves.

- The two wording parses re-anchored.
  `every_declared_message_is_classified` now selects `row.kind ==
  MESSAGE` instead of testing the `what` against the two direction
  prefixes, which is the by-construction matching the issue asks for: a
  prose row may be worded any way at all, and an invented message row
  counts only if it declares itself. The read-side-closed case filters
  by kind first and then still reads the direction off the `what`, with
  a comment at the split saying that is deliberate: the spelling has
  one author, `named_message`, so what is being read is that module's
  own format rather than English. No direction field was added.

- The two message-row bite cases moved off prefixes. The removed-entry
  bite finds its row by `kind == MESSAGE` and names it through
  `named_message(("abort", NONE_DECLARED, NONE_DECLARED), "sending")`
  rather than by a `startswith`; the invented-entry bite declares its
  row `MESSAGE` and carries the comment saying that under declared
  kinds classifying itself is what puts it in front of the assertion at
  all.

- The closed-set invariant, with the reason it exists in a comment
  above it: this module is outside the one package the type checker
  runs on and a frozen dataclass validates no annotation at runtime, so
  the `Literal` is documentation here and nothing more. Three cases:
  every canonical row's kind is one of `get_args(RowKind)`, the two
  constants are exactly those members (one encoding, so a third kind
  added to one and not the other fails here), and a bite that
  misspells the kind of exactly one message row, `sending abort`, and
  watches the invariant go red. One row and not all of them, and one in
  the middle of the table, so that an invariant reading a single row
  and calling the set closed would fail the bite; the case asserts the
  needle is in the haystack exactly once before handing it over.

- The freedom pin: `every_declared_message_is_classified` is handed the
  canonical table plus a `PROSE` row whose `what` begins `reading `,
  the exact shape the trap forbade, and stays green. Scoped to that one
  assertion for the reason the plan's second review finding gives, and
  the docstring repeats it: the rendering helper reads the canonical
  `rows()` rather than the tuple it is handed.

- The 2026-08-25 implementation doc keeps its trap record and gains a
  dated line pointing here; a CHANGELOG entry under 2026-09-04
  `### Changed`; the census manifest regenerated through `uv run python
  -m tests.unit.test_command_spellings` after each set of document
  edits, because it records physical line positions and both the
  CHANGELOG entry and the dated pointer move them.

### Deviations from the plan

None. Every open question was resolved as the plan resolved it, and all
four amendments from the review round are in the tree: the manifest is
regenerated and verified before the unit lane, the freedom pin is
scoped to the both-ways assertion, the closed set is held by a test
invariant with its own bite case, and the read-side case's remaining
string inspection is stated where it happens rather than claimed away.

One thing worth recording that the plan did not name. The plan said the
20 prose rows and "the derived constructor" needed the token; the
derived constructor is `_classified`, which has two returns, so the
count of construction sites in the module is 22 rather than 21. Nothing
followed from it: `kind` being required meant a missed site could not
have survived the first import.

### Verification

From `vinga-server/` on the milestone head:

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q -n auto --dist loadfile`: `5380 passed,
  19 skipped in 83.58s`.
- `uv run pytest tests/integration -q`: `239 passed in 353.95s`,
  against the dev Postgres.
- From the repository root, `python3 scripts/check_doc_links.py .`:
  `checked 187 files, 0 failures`.
- `docs/reference/cli.md` is untouched, which `git status` shows and
  which its own freshness pin asserts.

The manifest was regenerated once for the CHANGELOG entry and the dated
pointer, and once more for this document and the ticked checkbox, so no
commit in the milestone leaves the drift check red.
