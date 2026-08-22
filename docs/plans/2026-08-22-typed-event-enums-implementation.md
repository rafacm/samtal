# Type event fields with their StrEnums: implementation

Companion to
[`2026-08-22-typed-event-enums.md`](2026-08-22-typed-event-enums.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: The catalog admits enum-typed fields, proven on the fixed fields

### What was done

Five commits: the catalog machinery, the docgen's reads, the docs
suite's reads, the pilot migration with its regenerated golden, and the
two suites that pin the new shape.

**The machinery** (`events/catalog.py`). `_read` splits the annotation
question into `_declared_type`, which answers what a field carries and
the closed set it admits: an enumeration admits every member, a
`Literal` over one enumeration's members admits those, a value type
admits whatever its own `TOKENS` says, and a `Literal` mixing two
enumerations is refused because it names no set at all. `Declared`
gains `tokens: frozenset[str] | None` beside a `type` widened to
`type[EventValue] | type[StrEnum]`, and `fixed` widened the same way.

Two module helpers convert a held value in one place each: `_carried`
and `_rendered`, both `str()` for a member, carrying the note
`TokenValue.carried()` used to carry about why. `payload()` and
`logged()` call them. `verify()` holds an enum field to `isinstance` of
its enumeration and then to membership of `Declared.tokens`, refused as
"is a narrowed `<Enum>`", which names the variant, the field and the
declared type and nothing else. `_check` refuses at declaration a
`fixed=` member that is not an instance of the field's enumeration or
not within its narrowing.

Three accessors answer the documentation facts from the declaration
rather than from the type it names: `kind_of`, `arg_kind_of` and
`grammar_of`, answering `TOKEN`, `TOKEN` and `None` for an enumeration
and the type's own attribute otherwise. `tokens_of` keeps its
signature and reads `Declared.tokens` for an enum field, with the
fixed-member narrowing it already applied.

**The docgen** (`events_docgen.py`). `_variant_section`'s two cells,
`_field_constraint` and `_arg_constraint` read the accessors, which is
what the plan's finding 9 named: all three read `.KIND`, `.ARG_KIND`
and `.GRAMMAR` before any branch, so an enum-typed field would have
raised `AttributeError` whatever a token branch below did. The two
kind cells go through `_kind` and `_arg_kind` beside the two existing
constraint helpers. Nothing else in the file moved, and the rendered
document is byte-identical.

**The pilot migration** (`events/catalog.py`). All 27
`value(fixed=SomethingToken(Enum.MEMBER))` fields become
`field: Enum = value(fixed=Enum.MEMBER)`; ten wrapper imports go
with them. Eight declarations that were wrapped over three lines only
because of the wrapper's length are one line again. No emit site
changed, by construction: a fixed field is `init=False`.

**The golden inventory** (`tests/unit/test_event_golden.py` and its
data file). `inventory()` gains `_field`, which records `tokens` for a
field that declares a closed set, as `tokens_of` answers it and sorted.
The regenerated file's diff is exactly the two deliberate kinds: the
`type` renames of the migrated fields (in the `fields` lists and in the
`arguments` lists that name the same types), and the new `tokens` keys.

**The suites.** `tests/unit/test_event_enum_fields.py` is new, and
`tests/unit/test_event_baseline.py` gains the real-catalog pin the
plan's finding 8 asked for. Both are described in the commit that adds
them; what is worth recording here is that the baseline pin was checked
by mutation rather than argued (see Discoveries).

**`CHANGELOG.md`.** One entry under `### Changed` in the existing
`## 2026-08-22` section, which M2 extends rather than duplicates.

### Deviations from the plan

Two, both bookkeeping, and one discovery that forced a test to move
(recorded below rather than here, since it is a fact the plan could not
have known).

1. **The docs suite reads the accessors too.**
   `tests/unit/test_event_docs.py`'s two row tests build their second
   opinion of a cell from `declared.type.KIND` and `.ARG_KIND`, which
   an enum-typed field does not carry. The plan's module layout named
   `events_docgen.py` alone; the same routing was needed one file
   over, in its own commit. Nothing about what those tests claim
   changed: they still state the cell independently of the generator's
   own helpers.
2. **The baseline harness grew two names.**
   `tests/tools/event_baseline.py` gains `driven()` (the same run,
   kept as records rather than shapes) and `payload()` (a record's own
   fields, which `shape()` now uses for its key list), and `captured()`
   takes a run already made. The plan's Tests section described the
   new assertion without naming what it would need from the harness; a
   payload-value claim cannot be made from the committed baseline's
   key names, and driving the eighty-one paths twice in one file was
   not worth it.

Otherwise none. The machinery is as design decisions 1 to 4 and 8
specify, the golden carries `tokens` as decision 7 amended by finding
10 asks, the reference and the committed baseline are byte-identical,
no file outside `events/` and the suites moved, and no caller-passed
token field, wrapper class or line of `values.py` was touched: those
are M2's.

### Discoveries

**The golden's wording check had to learn about token sets.**
`test_the_inventory_carries_no_wording` asserts that no string in the
file holds a space, on the stated premise that every string in it is a
name, a type or a channel. Recording the declared sets breaks that
premise the moment a closed set has a worded member, and three carried
fields have one: `activation_not_offered`'s pending bounds are the
sentences their warning renders, `onboarding_banner`'s origin token
names a pair of configuration keys in a phrase, and `capture_failed`'s
`reason` is worded as the sentence renders it ("write audio"). The
check now reads everything but the `tokens` values, with the reason
written down beside it: those are declarations rather than prose about
declarations, and pinning them is the whole point of the key. What the
check still guards is unchanged, which is that no template, note or
message reaches the file.

**The payload-value pin bites.** Before it was committed, `_carried`
was edited to hand a member straight through and
`test_every_driven_record_carries_builtins` was run against it: it
fails, listing the paths and the enumeration each unconverted member
belongs to. The file was restored by copying a backup back rather than
with `git checkout`, and `touch`ed, per `AGENTS.md`. This is the
mutation the plan's risk section describes, so the risk is now covered
by evidence rather than by a test that has never seen the failure.

**`verify()`'s narrowing check is asked of every enum field.** The
plan words it as a check that runs "where `Declared.tokens` narrows
below the full enumeration". Asking it of every enum field is the same
check: a member of the enumeration is necessarily in the set built from
that enumeration's members, aliases included, so the unnarrowed case
passes by construction. It is spelled unconditionally, with that reason
in a comment, rather than as a second comparison deciding whether to
compare.

### Verification

From `vinga-server/`, on `feature/typed-event-enums-m1`.

- `uv run ruff check .`: **All checks passed!**
- `uv run mypy`: **Success: no issues found in 3 source files.**
- `uv run pytest tests/unit -q`: **2821 passed, 20 skipped** in 338 s.
  The fifteen the branch adds are the fourteen in
  `test_event_enum_fields.py` and the baseline's payload-value pin; no
  suite was removed.
- `uv run pytest tests/integration -q`: **61 passed** in 195 s.
- `uv run vinga-server events reference | diff - ../docs/reference/events.md`:
  **empty**, run after the machinery, after the docgen change and again
  after the pilot migration.
- The committed event baseline (`tests/unit/data/event-baseline.json`)
  is unmodified, and `test_the_capture_is_the_committed_baseline` and
  `test_the_committed_file_is_what_the_harness_writes` both pass
  against it.
- `uv run python -m tests.unit.test_event_golden` run twice: the second
  run leaves the file unchanged, and
  `test_the_committed_file_is_what_the_generator_writes` passes.

Nothing here needs hardware, so no verification step was left
unverifiable.
