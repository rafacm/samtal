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
   over, in its own commit. Routing them through the catalog's
   accessors turned each assertion into the generator's own answer
   restated, which the review round's finding 1 caught: the two tests
   derive the kind from the declaration themselves now (`48c904a6`),
   so what they claim is what they claimed before the milestone.
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
  suite was removed. (The review round below adds three more and
  reruns everything.)
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

## PR review round (PR #252)

External review of the PR diff: claude backend (the codex quota is
exhausted), claude CLI, model `claude-opus-5`, read-only tool set,
2026-08-22, [posted on the
PR](https://github.com/rafacm/vinga/pull/252#issuecomment-5381088235).
Verdict: mergeable after the listed fixes, no P1. Six findings, all
fixed before merge, each in a commit of its own. Findings condensed but
faithful:

1. **P2: the docs suite's kind assertions stopped being a second
   opinion.** `test_event_docs.py:417,440` built the expected cell with
   `arg_kind_of` and `kind_of`, which is what the generator calls to
   fill it, so an accessor answering the wrong kind would move the
   document and the test together and the next regeneration would
   commit a wrong reference against a green suite. The file's own
   header and `check_constraint` claim the opposite, and so did this
   document's deviation 1.
   *Fixed in `48c904a6`*: `kind_named` and `arg_kind_named` read the
   declaration, answering `TOKEN` for an enumeration and the value
   type's own attribute otherwise, which leaves the accessors as the
   thing under test. Checked by mutation: an accessor answering `INT`
   for an enum field now fails with ``'`INT`' == '`TOKEN`'``.
2. **P2: the plan's named mitigation for `Literal` and enum inside
   unions shipped with no coverage.** Every migrated production field
   is fixed, hence required and not nullable, and no scratch variant
   was nullable or omittable, so `_read`'s union split and `verify()`'s
   null and absent branches were never taken by an enum field.
   *Fixed in `9e90d5c3`*: two more scratch variants, a nullable enum
   field and an omittable narrowed one, asserting the declared set, the
   requiredness and nullability, the payload's two answers, that
   `verify()` accepts null and absence, and that the narrowing still
   refuses a member outside it.
3. **P3: the golden's header and one test docstring contradicted the
   file.** Both said there is no wording in it at all, and three
   carried fields have worded members now in it verbatim.
   *Fixed in `6755b29a`*: the claim is narrower and true, no wording
   ABOUT a declaration, with a declared set as the exception that
   proves it; the header lists the sets among the recorded dimensions.
4. **P3: `_arg_kind` turned a missing `ARG_KIND` into a silently empty
   cell.** `AgentNames` and `PromptSources` declare a field kind and no
   argument kind, and the old eager read raised on them; nothing
   refused a variant rendering one at declaration.
   *Fixed in `f2c7864b`*: `_check` holds a rendered name to having an
   argument kind, beside the two rules it already holds it to, with a
   scratch refusal case in `test_event_catalog.py`. The generator's
   fallback stays as dead defence.
5. **P3: the three accessors were missing from `__all__`.**
   *Fixed in `b4c0c3d2`*: added, sorted, beside every other public
   reader.
6. **P3: this document said eleven wrapper imports were removed.**
   *Fixed in `e7bc0347`*: ten, which is what the migration commit's
   diff removes.

### Verification after the round

From `vinga-server/`, on the six fix commits:

- `uv run ruff check .`: **All checks passed!**
- `uv run mypy`: **Success: no issues found in 3 source files.**
- `uv run pytest tests/unit -q`: **2824 passed, 20 skipped** in 336 s,
  three more than before the round: the two union tests and the new
  refusal case.
- `uv run pytest tests/integration -q`: **61 passed** in 192 s.
- `uv run vinga-server events reference | diff - ../docs/reference/events.md`:
  **empty**.
- `uv run python -m tests.unit.test_event_golden` run twice more: the
  file is unchanged, and the committed baseline is still untouched.

## M2: Migrate the caller-passed fields, retype the decision sites, delete the wrappers

### What was done

Two code commits and this one. The first types the thirteen
caller-passed token fields whose set is the whole enumeration and moves
the ten emit sites that pass them; the second replaces the three
narrowed wrappers with their `Literal` aliases, deletes `TokenValue`
and its twenty-two subclasses, and moves the three remaining emit sites
and the suites.

**The aliases** (`events/values.py`). `UnnamedToolSource`,
`PendingRefusal` and `McpConnectFailure` keep their names and are
declared beside the sets they narrow, each with the reason the class
carried: what its variant may not say. The paragraph above the
enumerations says why they are plain assignments rather than PEP 695
`type` statements, which is the catalog's `get_type_hints` reading.

**The deletion** (`events/values.py`). `TokenValue` and all twenty-two
subclasses go, and `__all__` loses twenty names and keeps the three the
aliases now hold. The enumerations themselves did not move.

**The declarations** (`events/catalog.py`). Sixteen field annotations
name their enumeration, or the alias for the three narrowed ones, and
nine wrapper imports go with them.

**The emit sites**, in the two kinds the plan distinguishes. Seven
already held a member and only dropped the wrapper: `capture.py`,
`ws.py`, `onboarding/origin.py`, `ota/reply.py`'s `_bad_request`, and
`runtime/pipeline.py`'s two outcome expressions. Six hold a plain
string and gained the lookup at the emit construction, each with a
comment saying which vocabulary the string belongs to:
`device/session.py`, `tools/mcp/reload.py`, `tools/mcp/manager.py`
twice, `runtime/pipeline.py` and `ota/reply.py`. None of the modules
that own those strings was retyped, which is the plan's finding 1
resolution.

**The suites.** `test_event_values.py` loses the three construction and
narrowing tests with the classes they tested and two entries of the
refusal sweep, and keeps both cross-module drift guards, rewritten
against the enumerations and `get_args` of the aliases.
`test_event_catalog.py` migrates its three wrapper constructions.
`test_event_enum_fields.py` had to move too (see the deviations).

**The golden inventory** was regenerated in each of the two code
commits rather than once at the end, because a commit that retypes a
field and leaves the golden behind is a red commit. The union of the
two diffs is twenty-one `"type"` strings and nothing else: the sixteen
renames, and the three narrowed fields recording their parent
enumeration in the five places the file names their types. Their
narrowing is pinned by the `tokens` key M1 added.

### Deviations from the plan

Five, four of them additions to lists the plan drew.

1. **A sixth string-holding emit site.** `tools/mcp/manager.py`'s
   `McpConnected.transport` reads `self._config.transport`, which is
   the configuration model's `Literal["stdio", "streamable_http"]` and
   not an `McpTransport` member, so it gains the same lookup as the
   five sites the plan lists. The plan's Module layout had it in
   neither kind.
2. **`runtime/pipeline.py` imports the enumeration under a second
   name.** `tools/source.py` exports `ToolSource`, the protocol the
   three tool origins answer, and the pipeline holds a tuple of them;
   the payload's `ToolSource` is a different thing with the same
   spelling, so it is imported as `ToolNamespace` with the reason
   beside it. The wrapper's name had hidden the collision.
3. **`tests/unit/test_event_enum_fields.py` moved.** M1's suite
   declared a scratch variant through the wrapper and asserted the
   generated section was identical to the enum-typed one, which is
   exactly the claim that cannot be made once the wrapper is gone. The
   variant and the comparison go; the test states the enum field's two
   cells directly and says in its docstring that the equivalence over
   the real catalog is now carried by the byte-identity of
   `docs/reference/events.md` across the migration. The plan expected
   every suite but two to be untouched, and this one is M1's own.
4. **`test_event_catalog.py`'s narrowing test changed its claim.** It
   held that a field declaring the wider wrapper takes the narrower
   subclass. There is no subclassing left, so it holds that the field
   takes every member `get_args(UnnamedToolSource)` names, which is the
   same relation between the two shapes and is why the alias reads
   better here than a second enumeration would.
5. **`tests/tools/event_baseline.py` needed no change.** The brief for
   this milestone expected wrapper constructions in the drivers; there
   are none, because the harness drives the production emit paths
   rather than building variants. `auth.py` and `config/api.py`,
   likewise named as emit sites, construct no token values either.

### Discoveries

**One flaky failure, not reproducible.** The full unit run made before
the first commit reported
`test_config.py::test_secret_like_option_names_are_rejected[password-password]`
failed alongside the two expected golden failures. The test passes
alone and passed in both later full runs, and nothing in this milestone
reaches `config/cli.py`. Recorded rather than explained.

### Verification

From `vinga-server/`, on `feature/typed-event-enums-m2`, after the last
code commit.

- `uv run ruff check .`: **All checks passed!**
- `uv run mypy`: **Success: no issues found in 3 source files.**
- `uv run pytest tests/unit -q`: **2816 passed, 20 skipped** in 343 s.
  Five fewer than M1's 2821: three deleted tests and two entries of a
  parametrized refusal sweep, all of them claims about constructors
  that no longer exist.
- `uv run pytest tests/integration -q`: **61 passed** in 196 s.
- `uv run vinga-server events reference | diff - ../docs/reference/events.md`:
  **empty**, run after each of the two code commits.
- The committed event baseline (`tests/unit/data/event-baseline.json`)
  is unmodified, and its suite passes inside the unit run above.
- `uv run python -m tests.unit.test_event_golden` run twice after each
  regeneration: the second run leaves the file unchanged.
- `grep -rnE "Token\(|UnnamedToolSource\(|PendingRefusal\(|McpConnectFailure\(" src tests`:
  **no matches**. `grep -rn "TokenValue" src tests`: **no matches**
  (the one remaining mention was a sentence in a suite's docstring,
  reworded).

Nothing here needs hardware, so no verification step was left
unverifiable.

## PR review round (PR #253)

External review of the PR diff: claude backend (the codex quota is
exhausted), claude CLI, model `claude-opus-5`, read-only tool set,
2026-08-22, [posted on the
PR](https://github.com/rafacm/vinga/pull/253#issuecomment-5381394055). Verdict: mergeable after finding 1 is
fixed, no P1. Seven findings, all fixed before merge, each in a commit
of its own. Findings condensed but faithful:

1. **P2: the one seam this milestone adds that no drift guard pinned.**
   `test_event_values.py`'s reload test reads `reload.APPLIED` and
   `reload.REFUSED` for the outcomes and then restated the five refusal
   words as literals beside them, so its name's claim was half true.
   The emit site now looks the word up, so rewording `REFUSED_BUSY`
   would leave the suite green and a reload refused by a busy database
   would emit nothing: the lookup raises inside the thunk and
   `_construct` answers `construction_failed`.
   *Fixed in `f88611e9`*: held against `reload`'s five `REFUSED_*`
   constants, like the other five seams. Checked by mutation, with the
   file restored by copy and `touch`ed rather than by `git checkout`.
2. **P3: `EventValue.TOKENS` is dead after the deletion and its
   docstring still presented it as live.** `TokenValue.__init_subclass__`
   was the only writer, so `tokens_of`'s value-type branch could answer
   nothing but `None`, and a residual second way to declare a closed
   set outlived the milestone that deleted the first.
   *Fixed in `8de79d57`*: the classvar goes, `tokens_of` reads
   `declared.tokens`, and the three docstrings that named it say an
   enumeration is the only way to declare a set.
3. **P3: the module's no-leak property is false for its own
   enumerations and nothing said so.** A lookup outside the set raises
   the enumeration's `ValueError`, which names the value; the
   containment lived in the plan and in `_construct`'s docstring,
   neither of which a reader of `values.py` or of the six emit sites
   meets.
   *Fixed in `e25e75d8`*: one paragraph in the closed-sets header
   saying the lookup names the value, that this is why every site
   spells it inside the thunk, and that `_construct` is what contains
   it.
4. **P3: the ticked milestone still stated the claim F1 withdrew.** The
   M2 bullet promised the decision sites adopt the narrowed aliases;
   the classifiers keep their plain strings and the emit site converts.
   *Fixed in `f6ba821d`*: "the emit sites convert into them".
5. **P3: `ws.py` left in a state `ruff format` would rewrite.**
   Dropping the wrapper took the call to 69 columns and it stayed
   wrapped over three lines; the lane runs `ruff check` only, so
   nothing would have caught it.
   *Fixed in `4fb1947c`*: collapsed onto one line.
6. **P3: the changelog counted names it then said were kept.** Twenty
   three classes existed, three survive as aliases of the same name,
   `__all__` loses twenty; "about twenty-five" was the plan's estimate
   carried into an operator-facing document.
   *Fixed in `e84f4bfb`*: twenty.
7. **P3: the golden's structural pin on a narrowing covered carried
   fields only.** `_typed` recorded the type name alone for a rendered
   position, and a narrowed field declared `carried=False` (the shape
   `OtaRequestRejected.refusal` and the four `outcome` fields have)
   appears in `arguments` and nowhere else, so its narrowing would have
   had no committed structural pin: the single-pin situation the plan
   review's finding 10 was raised to prevent. Latent today, since all
   three narrowed fields are carried.
   *Fixed in `bc5eb9f5`*: both lists read the declaration through one
   helper, so a set is recorded the same way in each. The regenerated
   file's diff is purely additive, thirteen argument entries gaining
   the `tokens` their field entry already carried or the single member
   a fixed one says, and the fields list is untouched.

### Verification after the round

From `vinga-server/`, on the seven fix commits.

- `uv run ruff check .`: **All checks passed!**
- `uv run mypy`: **Success: no issues found in 3 source files.**
- `uv run pytest tests/unit -q`: **2819 passed, 20 skipped** in 337 s.
  Unchanged: the round added and removed no test, it rewrote one
  assertion and three docstrings.
- `uv run pytest tests/integration -q`: **61 passed** in 192 s.
- `uv run vinga-server events reference | diff - ../docs/reference/events.md`:
  **empty**, run after findings 2, 3 and 7.
- The committed event baseline (`tests/unit/data/event-baseline.json`)
  is still untouched, and its suite passes inside the unit run above.
- `uv run python -m tests.unit.test_event_golden` run twice after the
  regeneration finding 7 asks for: the second run leaves the file
  unchanged.
