# Simplify the governance surfaces: implementation

Companion to
[`2026-08-19-governance-simplification.md`](2026-08-19-governance-simplification.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the typed event foundation inside the package move

### What was done

Eight commits, in the order the plan's review forced.

**The move.** `vinga_server/events.py` became
`vinga_server/events/__init__.py`, whole and unedited, because a
package added beside the module would have shadowed it (review finding
1). The full unit suite passed byte-unchanged except for one line the
move itself forces: `test_event_surface_guard.py` exempts the one file
the surface is emitted from by path, and that path is now the package's
`__init__`.

**The vocabulary.** `events/values.py` holds the value types the five
converted paths use: `Identifier`, `MachineId` with `SessionId` and
`EventName`, `ClassName`, `Count`, `ConfiguredPath`, plus `Absent` for
a field a variant may omit. Each validates at construction and each
refuses without repeating the value it refused. `ConfiguredPath` is the
one whose two surfaces differ, because the event surface's own do: the
payload field carries the path as text and the sentence renders the
object. The class name pattern moved here from the emitter, which
imports it back.

No token `StrEnum`s were built, and no bounded descriptor type. None of
the five conversation-store paths carries a `TOKEN` or a `DESCRIPTOR`
field: the syntaxes they reference are `event_name` and `session_id`,
and their kinds are `IDENTIFIER`, `ID`, `CLASS_NAME` and `COUNT`. The
plan's brief says to build only what M1's paths use; the token enums
and the descriptor bound type arrive with M2 and M3, which is where the
first `TOKEN` and `DESCRIPTOR` fields convert.

**The catalog model and the first area.** `events/catalog.py` declares
one event per code holding a discriminated set of typed variants, each
a frozen dataclass owning its channel, level, exact payload shape and
rendering. A variant's dataclass fields ARE the payload's keys, so the
field table stops being a second structure; `ARGS` names the ordered
subset the template renders, which is the one thing field order cannot
say, and `declare()` checks its length against the template's `%`
positions at import. Requiredness and nullability are derived from the
annotation: `| None` is nullable, `| Absent` is not required, and the
two are separate columns in the golden (review finding 4). The catalog
reads its state through one installed `CatalogState`, which is the
scratch seam its own suite needs.

**The emitter extension and the guard.** `ServerEvents.emit()` takes a
construction thunk, so building, validating, rendering and serializing
all happen inside the guard (review finding 3). There is no second
enforcement step on this path: the declaration is the check, and the
only thing left at emit time is whether the variant was handed to an
emitter on the channel it declares. Strict raises; forgiving reports
once and dispatches the declared `schema_violation`. `SessionEvents`
did not gain `emit()`: the session channel's base carries a device id
whose value type arrives with that channel's conversion, so
`declare()` refuses a session-channel variant outright rather than
half-supporting it.

**The golden inventory.**
`vinga-server/tests/unit/data/event-catalog-golden.json`, asserted
against the catalog in both directions by
`tests/unit/test_event_golden.py`. Names, channels, levels, argument
order and types, field order, types, requiredness and nullability. No
wording, and a test says so.

**The record baseline.** `vinga-server/tests/tools/event_baseline.py`
drives every emit path in scope and captures (channel, levelno, msg
template, argument types, payload keys) per path into
`vinga-server/tests/unit/data/event-baseline.json`.
`tests/unit/test_event_baseline.py` holds two obligations: every emit
site the conformance walk finds in a scoped module must be claimed by a
driver (which retires with the walk), and every variant the catalog
declares on a scoped channel must be produced by a driver's run (which
outlives it). Captured green before the conversion; the file is
byte-identical across the conversion commit, which is the proof.

**The conversion.** The five sites construct typed variants; their
`EventSpec` entries left `events_schema.py`; their `PINNED_BY` entries
and `test_conversations_event_pins.py` are gone.

**The type-check lane.** mypy, strict, scoped to
`vinga_server/events/`, run in CI after Lint. It passes.

### Deviations from the plan

Five, each with its reason.

1. **One test file changed in the mechanical-move commit.** The brief
   asked for no test edit at all. `test_event_surface_guard.py`
   identifies its single exemption by relative path, and that path
   changes when the file does; the alternative was a failing lane. It
   is the move's own consequence and nothing else changed.

2. **Strict mode raises a sanitized `EventSchemaError` rather than
   re-raising the construction error.** The plan says strict
   "re-raises". Re-raising the thunk's own exception would put whatever
   it was holding on a lane's stderr and in the `__cause__` chain,
   which the no-leak rule forbids without exception. What strict does
   today for an untyped violation is raise `EventSchemaError` with a
   refusal naming registry-owned identifiers, and the typed path does
   exactly that, naming the exception's class and never its words. "Raise
   rather than recover" is preserved; the bytes are not.

3. **`events_docgen.py` renders from both sources.** The plan has the
   generator switching its source of truth to the catalog. While the
   conversion is in flight, only some events are there, so the
   generator reads one sequence built from both: `documented()` is the
   registry's unconverted production events, then the catalog's, then
   the internal recovery event last. The catalog answers the same
   `EventSpec` shape through `described()`, so neither source gets a
   rendering of its own and `documented()` goes when the registry does.
   The regenerated reference keeps its counts (58 events, 99 variants,
   57 production, 1 internal); the four converted sections move within
   the document and their content is unchanged.

4. **The golden records argument names and types beside the fields.**
   The plan enumerates names, channels, levels, field names, types,
   requiredness and nullability. Argument order and type are structure
   in the same sense and contain no wording, and a reordered or retyped
   `%` position is exactly the kind of loud diff the file exists for.

5. **The test-boundary rule gained one named exception.** The baseline's
   exhaustiveness obligation is generated from the conformance suite's
   static walk, which the plan requires by name, and
   `test_support_boundaries.py` forbids a test module importing another
   one. Extracting the walk into `tests/support/` would be a
   three-hundred-line move of code M3 deletes outright, so the pair is
   exempted by both ends and by name, and the exemption goes with the
   walk.

### Discoveries

**The catalog's derived description was byte-equal to the registry's
hand-written one.** Before the conversion commit, `described()` produced
`EventSpec` objects equal to `REGISTRY`'s four declarations, field notes
and argument kinds included. That equality was asserted as a test while
both sources held the events, and it is why the regenerated reference's
only content change is the position of four sections. The test retired
with the registry entries it compared against.

**Field notes and argument notes are separate facts.** The first
derivation gave a `%` position the note of the field it renders, which
changed two argument tables. The old registry declares them
independently (`conversations_failed`'s field carries "The exception's
class name, never its message" and its argument carries nothing), so
`value()` takes `note` and `rendered_note` separately.

**A thunk cannot close over an `except ... as` name.** `_prune`'s
failure path builds its variant from the exception, and `except ... as
exc` unbinds `exc` when the block ends, so a closure reaching for it
afterwards finds nothing; ruff catches it as an undefined name. The site
binds the exception to an ordinary local first, which keeps the
construction inside the guard.

**`session` is an ordinary field on a server channel.** The first
version of `declare()` refused any variant declaring `session` or
`device`, which is the session channel's rule.
`conversations_dropped` declares `session` legitimately, so the check
now asks the channel what its base is.

### The inventory, after M1

Production event machinery: 4,738 lines
(`events/__init__.py` 1,397, `events/values.py` 292,
`events/catalog.py` 593, `events_schema.py` 2,314,
`events_docgen.py` 534, `events_cli.py` 141), against 4,287 before.
It grew, as expected for a milestone that builds a second mechanism
before deleting the first: the registry has lost only its four smallest
declarations (72 lines) and every line of the enforcement machinery is
still needed by the 76 unconverted paths. The plan's "roughly half of
4,287" is a claim about the end of M3, and it is the number to hold this
work to there.

New test assets: 1,326 lines
(`test_event_values.py` 168, `test_event_catalog.py` 233,
`test_event_typed_emit.py` 338, `test_event_golden.py` 127,
`test_event_baseline.py` 152, `tests/tools/event_baseline.py` 273,
`tests/support/catalog.py` 35), against 309 retired with
`test_conversations_event_pins.py`.

Transitional apparatus, which falls as the conversion proceeds and is
annotated where it is asserted: the walk finds 76 emit sites (was 81)
across 53 events (was 57); `PINNED_BY` holds 76 identities (was 81);
the classifier reads 70 field and 47 argument positions (was 72 and 49);
`events_schema.py` declares 53 production events plus the internal one
(was 57 plus one), and the catalog declares the other 4.

### Verification

Run from `vinga-server/`, at the last commit of the milestone.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files.
- `uv run pytest tests/unit -q`: 3,166 passed, 16 skipped. (Baseline
  before the milestone: 3,126 passed, 16 skipped.)
- `uv run pytest tests/integration -q`: 60 passed.
- The four documentation drift checks, regenerated and diffed against
  `../docs/reference/`: `config reference`, `conversations schema`,
  `events reference` and `config openapi` are all clean.
  `docs/reference/events.md` is regenerated and committed inside the
  conversion.
- The record baseline is byte-identical across the conversion commit:
  `vinga-server/tests/unit/data/event-baseline.json` does not appear in
  that commit's file list, which is the identity proof.

Not verified here, and not claimed: the container image and the smoke
lane. The image job does not run on pull requests at present, and
nothing in this milestone touches the image's plumbing.
