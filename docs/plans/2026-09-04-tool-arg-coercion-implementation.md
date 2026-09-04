# Coerce lossless tool-call arguments at dispatch: implementation

Companion to
[`2026-09-04-tool-arg-coercion.md`](2026-09-04-tool-arg-coercion.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the coercion, wired and tested

### What was done

`tools/arguments.py`, new and stdlib-only.
`with_lossless_coercions(arguments, schema)` answers a new dict, never
touches its input and never raises. Per top-level property whose schema
entry declares a single `type` string: `integer` takes a `str` matching
`-?[0-9]+` once stripped and a `float` whose `is_integer()` holds;
`number` takes a `str` matching the ASCII JSON number grammar and then
holds the result to being finite and to
`Decimal(text) == Decimal(result)`; `boolean` takes exactly `"true"`
and `"false"`. Everything else passes through: other declared types, a
`type` given as a list, undeclared properties, schemas with no
`properties` or a `properties` that is not a mapping, entries that are
not mappings, and every nested value. Both grammars are spelled `[0-9]`
AND compiled `re.ASCII`, which says the same thing twice so that an
edit to `\d` cannot widen them silently. Every conversion runs inside
one guard (`_guarded`) that answers the original on any exception,
which is what covers Python's integer digit-conversion limit and keeps
a parser's message, which repeats what it rejected, out of a stored
tool result.

`runtime/pipeline.py`, at the site the plan's finding 2 settled.
`_tool_loop` takes `schemas = {tool.name: tool.input_schema}` off the
snapshot it already holds, and after `_reserve_tools` has filed the
originals and the working-history append has captured them, and before
`_run_tools` branches, derives the execution copies:
`[self._for_execution(call, slot, schemas) for call, slot in zip(...)]`.
`_for_execution` answers the original object where nothing converted
and a `dataclasses.replace` copy where something did, and it is where
the event is emitted. `_run_one` keeps its `(call, slot)` signature and
dispatches `replace(classified, arguments=...)`: the reserved claim
with the execution copy's arguments, and `None` where the claim is
malformed, so the record, the reservation, the events and the history
keep what the model sent. `_coercions(sent, executing)` is the count,
compared by type as well as by value because `100 == 100.0` and
`True == 1` in Python.

`events/catalog.py` and `events/assembly.py`. One declaration,
`tool_arguments_coerced`, one variant `ToolArgumentsCoerced` on the
session channel at INFO, carrying `agent`, `conversation`, `source`,
`coerced`, and `tool`/`entry` where the naming policy allows a name;
`named` is the rendered fragment. `assembly.tool_arguments_coerced`
builds it and `pipeline._tool_arguments_coerced` chooses the two names
from the classifier's own constants, exactly as `_tool_called` does.

The catalog discipline: a driver
(`PipelineRuntime._for_execution #1`, a builtin whose `id` the model
quoted), its `CARRIED` row, the driver count in
`test_event_baseline.py` raised to 87, `docs/reference/events.md`
regenerated through `vinga-server events reference`, and the
`vinga-server/README.md` event index row.

Fixtures moved to support so they have one home:
`VOLUME` into `tests/support/device_tools.py` (imported back by
`test_tools_device.py`), and the recording-session infrastructure
(`SpyStore`, `Speaking`, `speaking_session`, `recording_session`,
`only_record`) into the new `tests/support/records.py`, imported back
by `test_session_record.py`.

Tests. `tests/unit/test_tool_arguments.py` is the per-type matrix with
every refusal beside its acceptance, the parser-only spellings, the
Unicode digits, the inexact large integer, the underflowing exponent,
the tolerance cases, non-mutation and the four totality cases.
`test_session_tools.py` gains the wire/record/history split pin against
a device tool typed `integer`, the scripted firmware-style refusal for
`"a lot"`, the four-way `forget` permanence pin, and two event cases (a
device call names nothing, a builtin names itself, and a call that
needed nothing is silent). `test_event_surface_pins.py` gains the
no-leak sentinel: a secret-shaped value against a `number` property,
asserted to reach the far side unchanged and no result, record, format
or tap.

`CHANGELOG.md` records the coercion and the `forget` consequence under
today's date.

### Deviations from the plan

Two, both small, and one discovery about the plan's own rule is in the
next section.

**The recording-session infrastructure moved to support, which the plan
does not mention.** The plan asks `test_session_tools.py` to pin the
completed `TurnRecord` "through the recording-session infrastructure",
and that infrastructure lived in `test_session_record.py`, which
`tests/unit/test_support_boundaries.py` forbids importing from another
test module. It moved whole into `tests/support/records.py`, its
docstrings with it, and `test_session_record.py` imports it back. This
is the same move the plan already prescribes for `VOLUME`, applied to
the second fixture the same case needs.

**`_for_execution` is per call rather than per round.** The plan
describes `_tool_loop` deriving the copies; it does so in a list
comprehension over one method that answers one call, rather than
through a method that takes the list. A method taking the list and
mapping over another would have been a name that hides nothing, and the
comprehension puts the site in `_tool_loop` where the plan wants it
read.

### Discoveries

**The `number` equivalence check is stricter than the plan's examples
suggest, and it is worth a decision at review.** `Decimal(text) ==
Decimal(float(text))` holds only where the decimal has an exact double.
So `"0.5"`, `"2.25"`, `"100"`, `"1e3"` and `"2.5e+2"` convert, and
`"0.1"`, `"0.7"`, `"1.1"` and `"1E-3"` do not: they are left exactly as
the model sent them and fail as they do today. That is the plan's rule
applied literally, and the strict direction, so it is what shipped;
`test_a_decimal_with_no_exact_double_is_left_alone` states the line and
`_number`'s docstring explains it. The alternative, if the review
decides a quoted `0.7` should convert, is to compare against the
shortest round trip (`Decimal(text) == Decimal(repr(result))`), which
still refuses `"9007199254740993"`, `"1e-4000"` and `"1e400"` and every
parser-only spelling, since those are exactly the cases where `repr`
answers a different number. It is a widening, and the plan says a
widening arrives with its own evidence, so it is named here rather than
taken.

**One variant means the rendered fragment is declared as the base
type.** The three `tool_call` shapes make the naming policy structural
by each declaring its own fragment type. One variant covering four
namespaces cannot, so `named` is declared `Fragment`, which the
generated reference prints with an empty grammar cell; the field's
`rendered_note` says which three shapes it may take, and the payload
carries `source` plus the two optional names, so the policy is still
readable from the record alone. `assembly.py`'s module note says this
is the one event whose sentence may render any of the three.

**The lane's boot suites need Postgres on the port
`DatabaseConfig` defaults to.** Running the unit lane against an
instance on a non-default port fails nine cases in
`test_app_lifespan.py` and `test_conversations_boot.py`, which build a
`DatabaseConfig` in Python where `VINGA_DB_PORT` is not read. They pass
on 5432. Verified against the unchanged tree first, so nothing here is
implicated.

### Verification

Run from `vinga-server/`, against a development Postgres on 5432.

- `uv run ruff check .`: `All checks passed!`
- `uv run mypy` (the events package, as CI runs it):
  `Success: no issues found in 5 source files`
- `uv run pytest tests/unit -q`: `5318 passed, 19 skipped`
- `uv run pytest tests/unit -q -n auto --dist loadfile`, which is how CI
  runs the lane: `5318 passed, 19 skipped in 90.49s`
- `uv run pytest tests/integration -q`: `238 passed in 357.15s`
- `python3 scripts/check_doc_links.py .` from the repository root:
  `checked 180 files, 0 failures`

The session split pins were run against the unwired pipeline as well,
to check they fail without it: the wire pin and the `"true"` permanence
case both fail there, which is what makes them evidence.

`tests/unit/command-spellings.txt` was regenerated with
`uv run python -m tests.unit.test_command_spellings` after the document
edits, which is what moves its line numbers.
