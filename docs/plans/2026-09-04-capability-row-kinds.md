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
leaves `docs/reference/cli.md` byte-identical. The census manifest
is a different matter: it records physical line positions across
every tracked file, `CHANGELOG.md` and the 2026-08-25
implementation doc included, so this plan's own CHANGELOG entry
and dated pointer stale it and it regenerates through its module.

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
`__all__`. The `Literal` is honest about what it is here: this
module is outside the one package the type checker runs on, and a
dataclass validates no annotation at runtime, so the closed set is
enforced by a test invariant rather than assumed from the alias:
every canonical row's kind is one of the two declared values, the
two constants are exactly the `Literal`'s members (one encoding,
not two), and a bite case hands the invariant a table carrying a
row with an out-of-set kind and watches it go red, the same
held-to-going-red discipline every other helper in the file keeps. The alias is spelled `RowKind` because `Kind` and
`ArgKind` are taken by the events vocabulary and grep should stay
unambiguous.

**The pins filter by kind; `named_message` keeps owning the
message spelling.** `every_declared_message_is_classified` selects
`row.kind == MESSAGE` and compares against the declared inventory,
so a prose row can begin with any words at all and an invented
message row must declare itself to be counted, which is the
by-construction matching the issue asks for. The read-side-closed
case filters by kind first and then still reads the direction off
the `what`, and the plan says so plainly rather than claiming
otherwise: what moves entirely to `kind` is the message-versus-
prose classification, while the direction extraction remains
string inspection of the spelling `named_message` owns, done
deliberately and commented as such at the split. A direction field
on `Capability` was considered and rejected: it would widen the
row beyond what the issue asks for the sake of one test that
already has a single-author spelling to read. The two bite
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

**The new freedom gets its own pin, scoped to the assertion that
had the trap.** A regression case hands
`every_declared_message_is_classified` a table augmented with a
prose row whose `what` begins `reading ` (the exact shape the trap
forbade), declared `PROSE`, and asserts it stays green, which
directly proves the issue's fix. The rendering helper is
deliberately out of the case's reach: `epilog()` reads the
canonical `rows()` rather than a supplied tuple, so an appended
local row is absent from the page by construction, and patching
the module's public table to include a synthetic row would test
the patch rather than the freedom.

**The old record gets a dated pointer, not a rewrite.** The #299
round's trap note in the 2026-08-25 implementation doc stays as
the record of what that round left behind, gaining one dated line
pointing at this plan as the removal.

## Module layout

No new module. `simulator/capabilities.py` deepens: the table's
own docstring already claims everything here is declared, and the
kind makes the last derived fact declared too. The test file stops
classifying rows by their wording; the one place it still inspects
a message row's spelling reads the single-author `named_message`
form and says so.

## Tests

- The both-ways pin and the read-side-closed case re-anchored on
  `kind`, byte-identical in what they prove (the existing bite
  cases keep going red for the same reasons, now through the
  declared field).
- The freedom pin above: a `reading `-prefixed prose row passes
  the both-ways completeness assertion, the one that had the trap.
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
  changes; the docgen freshness pin goes red if a rendered byte
  moves, and the plan changes none. The census manifest stales on
  this plan's own doc edits regardless and regenerates through
  `uv run python -m tests.unit.test_command_spellings`, verified
  before the unit lane.

## Milestones

- [x] **[M1: the kind, declared and pinned](2026-09-04-capability-row-kinds-implementation.md#m1-the-kind-declared-and-pinned)** (PR TBD). `RowKind`, `MESSAGE`
  and `PROSE` on `capabilities.py` with the field required on
  every row and the trap comment deleted; the two wording parses
  and two bite cases moved to the kind field; the freedom pin; the
  dated pointer in the 2026-08-25 implementation doc; a CHANGELOG
  entry; the implementation-doc section. Design footprint: deepens
  `Capability` (a row states its own classification, and the pins
  stop classifying by English); no new module, no interface widening
  beyond the one declared fact. Documentation footprint:
  `docs/reference/cli.md` stays byte-identical (asserted by its
  freshness pin); the census manifest regenerates through its
  module because the CHANGELOG entry and the dated pointer move
  recorded line positions; `CHANGELOG.md` and the dated pointer.

## Plan review round

Backend codex (codex-cli 0.153.0), model `gpt-5.6-sol`, sandbox
read-only, 2026-09-04, against commit `f14766cb`; the reviewer ran
about 6 minutes. Verdict: ready after the P1/P2 amendments.

1. **P1: the census manifest cannot remain untouched.** The
   manifest records physical line positions across every tracked
   file, `CHANGELOG.md` entries and the 2026-08-25 implementation
   doc included, so the milestone's own CHANGELOG entry and dated
   pointer move recorded locations even though
   `docs/reference/cli.md` stays byte-identical. Say so, add the
   regeneration to the footprint, and verify with the generator
   module before the unit lane.

   *Resolution*: accepted in full. The no-byte-moves claim is now
   scoped to `docs/reference/cli.md`; the facts section, the risk
   and the milestone footprint all carry the manifest regeneration
   with its reason and the verification order.

2. **P2: the freedom pin does not work with the helpers as
   shaped.** `every_row_renders_on_the_side_it_declares` renders
   `capabilities.epilog(WIDTH)`, which reads the canonical
   `rows()` rather than the supplied tuple, so an appended local
   row fails the rendering assertion by being absent from the
   page. Scope the regression to
   `every_declared_message_is_classified`, which directly proves
   the issue's fix, rather than implying the augmented tuple
   exercises all five assertions.

   *Resolution*: accepted in full; the pin is scoped to the
   both-ways assertion, with the reason the rendering helper is
   out of reach stated in the plan.

3. **P2: `Literal` does not enforce the closed set here.** Only
   the events package is type-checked; dataclasses do not validate
   annotations at runtime, so a misspelled kind could render
   normally while the message pin ignores it. Add a runtime or
   unit-test invariant that every canonical row's kind is one of
   the two declared values, a bite case for an invalid kind, and
   an assertion that the constants agree with the `Literal`
   members rather than being a second unchecked encoding.

   *Resolution*: accepted in full. The plan now states the alias
   is unchecked in this module and adds the test invariant (every
   canonical kind in the closed set, the constants exactly the
   `Literal`'s members) with its own out-of-set bite case.

4. **P2: the read-side design still parses wording, contrary to
   the plan's claims.** Filtering by kind prevents prose
   misclassification, but the direction extraction is still string
   parsing. Narrow the claim: classification moves entirely to
   `kind`, while the read-side case deliberately keeps inspecting
   the `named_message`-owned spelling; do not add a direction
   field, which would widen `Capability` beyond the issue.

   *Resolution*: accepted in full; the claims are narrowed
   throughout (classification moves to `kind`, the direction
   extraction stays and says so where it splits), and the
   direction field is recorded as considered and rejected.
