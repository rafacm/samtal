# Classify capability rows by a declared kind

Plan for [#303](https://github.com/rafacm/vinga/issues/303).
Implementation notes land in the companion
`2026-09-04-capability-row-kinds-implementation.md`, one section per
milestone, appended in the change that ticks the milestone here.

## Goal

The capability table's both-ways pin classifies a row as a message
row by parsing its English: a `what` starting with `reading ` or
`sending ` is split on the prefix and matched against the wire
inventory. A prose row whose wording happens to begin with either
word is misread and held to the wrong assertion, which is why the
firmware row is deliberately worded around the trap with a comment
saying so. This plan makes classification a declared fact on the
row: each `Capability` states its own kind, the pins filter by it,
and the wording constraint on future prose rows disappears.

## The issue's decisions, restated

- Each row gets an explicit kind (message or prose), so
  classification is a declared fact rather than a parse of English.
- The both-ways pin matches message rows against the wire inventory
  by construction.
- The change is test-harness plus the table module, with no
  user-visible behavior.

## Where the facts already live

`Capability` (`simulator/capabilities.py:77-96`) carries `what`,
`side`, `reason`, `verb`; `side` is a `str` keyed to module
constants. Message rows are derived from `protocol/messages.py`
through `named_message(row, direction)`, whose `what` spelling is
`f"{direction} {message_type}"` with the direction literals passed
at the two `_message_rows()` call sites; the 20 prose rows are
written by hand in `_PROSE_ROWS`. Three tests parse wording today:
the both-ways pin (`every_declared_message_is_classified`,
`test_simulator_capabilities.py:34-47`), the read-side-closed case
(`:103-114`, the same defect the issue does not name but the fix
must cover), and two bite cases that select or invent rows by
prefix (`:132-140`, `:143-152`). The trap comment sits at
`capabilities.py:285-287`, and the #299 round recorded the trap it
left behind in the 2026-08-25 implementation doc. Nothing outside
the module and its test reads `Capability` fields except the two
`cli.py` epilog calls, and the epilog renders `side`, `what`,
`reason` and `verb` only, so a new field that changes no text
leaves `docs/reference/cli.md` and the census manifest untouched.

## Open questions, resolved

**The kind is a `Literal` alias with module constants, required on
every row.** `RowKind = Literal["message", "prose"]` with
`MESSAGE` and `PROSE` beside the side constants, following the
produced-on-this-side precedent (`Access` in `ota/reply.py`, chosen
there because a `Literal` catches an out-of-set value where the
values are authored locally, which is exactly this table). The
field is required like `side`, not defaulted: the issue's point is
that classification is declared, and a default is a classification
nobody wrote. The cost is one mechanical token on each of the 20
prose rows and the derived constructor; the constants join
`__all__`. The alias is spelled `RowKind` because `Kind` and
`ArgKind` are taken by the events vocabulary and grep should stay
unambiguous.

**The pins filter by kind; `named_message` keeps owning the
message spelling.** `every_declared_message_is_classified` selects
`row.kind == MESSAGE` and compares against the declared inventory,
so a prose row can begin with any words at all and an invented
message row must declare itself to be counted, which is the
by-construction matching the issue asks for. The read-side-closed
case filters by kind first and then reads the direction off the
spelling `named_message` owns; that is no longer a parse of
English, because the spelling of a message row's `what` has exactly
one author and the test says so where it splits. The two bite
cases that select or append message rows by prefix move to the
kind field, and the invented-entry bite declares its row
`MESSAGE`, with a comment noting that under declared kinds the
bite's row must classify itself, which is the point.

**The firmware row keeps its wording; only the trap comment
goes.** Rewording the row would reflow the epilog, which appears
twice in the generated CLI reference and shifts the census
manifest's line records, all for a sentence that is fine as it is.
Deleting the comment and leaving the words buys the same freedom
for future rows at zero generated-artifact churn, and the epilog
byte-identity is what the existing
`test_the_epilog_is_what_the_help_page_carries` and the docgen
freshness pin already assert.

**The new freedom gets its own pin.** A regression case adds a
prose row whose `what` begins `reading ` (the exact shape the trap
forbade), declared `PROSE`, and asserts every table assertion stays
green, so the constraint's removal is proven rather than claimed.

**The old record gets a dated pointer, not a rewrite.** The #299
round's trap note in the 2026-08-25 implementation doc stays as
the record of what that round left behind, gaining one dated line
pointing at this plan as the removal.

## Module layout

No new module. `simulator/capabilities.py` deepens: the table's
own docstring already claims everything here is declared, and the
kind makes the last derived fact declared too. The test file stops
knowing how message rows are worded.

## Tests

- The both-ways pin and the read-side-closed case re-anchored on
  `kind`, byte-identical in what they prove (the existing bite
  cases keep going red for the same reasons, now through the
  declared field).
- The freedom pin above: a `reading `-prefixed prose row passes
  every assertion.
- The existing five assertion helpers, milestone cases and bite
  cases stay green with their construction sites updated; the
  states-row pin from the #369 round is untouched.
- `test_the_epilog_is_what_the_help_page_carries` and
  `test_the_committed_cli_reference_matches_the_grammar` prove no
  rendered byte moved.

## Risks

- **A construction site missed.** `kind` is required with no
  default, so a missed site is a `TypeError` at import, caught by
  the first test run rather than by review.
- **Accidental text drift.** No `what`, `reason` or heading
  changes; the docgen freshness pin and the census both go red if
  a byte moves, and the plan changes none.

## Milestones

- [ ] **M1: the kind, declared and pinned.** `RowKind`, `MESSAGE`
  and `PROSE` on `capabilities.py` with the field required on
  every row and the trap comment deleted; the two wording parses
  and two bite cases moved to the kind field; the freedom pin; the
  dated pointer in the 2026-08-25 implementation doc; a CHANGELOG
  entry; the implementation-doc section. Design footprint: deepens
  `Capability` (a row states its own classification, and the pins
  stop parsing English); no new module, no interface widening
  beyond the one declared fact. Documentation footprint: none
  generated (no rendered byte changes, asserted by the existing
  freshness pins); `CHANGELOG.md` and the dated pointer only.
