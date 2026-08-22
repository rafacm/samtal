# Emit one shape per decision site and move assembly out of the orchestrator: implementation

Companion to
[`2026-08-22-one-shape-per-site.md`](2026-08-22-one-shape-per-site.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: collapse the twins and move assembly out

### What was done

Six commits, the last of them the changelog entry and this section,
in an order the plan did not fix but which the design guide's rule
about proving a move behavior-preserving does: the widening, then the
MOVE with the twins still intact, then the COLLAPSE, then the pins.
The middle step is the one worth the extra commit. After it the whole
unit suite is green, the record baseline and
the golden inventory untouched, which is the cheapest possible proof
that lifting 260 lines out of the reply path changed no shape; the
collapse commit after it then has a diff whose entire subject is the
collapse.

**The widening** (`befbbdd4`, amended to `ee142557`).
`vinga-server/src/vinga_server/events/values.py`: `QuotedProvider.of`
takes `str | None` and answers the empty rendering for `None`, exactly
as `ReachingHost.of` does, and `QUOTED_PROVIDER`'s pattern becomes
`(?: "[\s\S]+")?` with its description amended to say so and why.
`tests/unit/test_event_values.py` gains both halves: the empty
rendering is lawful, and an unquoted entry is still refused.

**The move** (`7d4a6b3e`). `events/assembly.py` is new and holds what
lines 170 to 426 of `runtime/pipeline.py` held: the frozen entry
quartet type, the reader that fills it from a provider's identity, the
tool-name fragment, and builders for `llm_retry`, `llm_round`,
`provider_failed` and the three `tool_call` shapes. Its signatures take
builtins and answer a `Variant`; it imports the catalog and the value
vocabulary and nothing else. The five call sites the plan names
(`_watchdog_stream`, `_llm_round_done`, `_provider_failed`, `_run_one`,
`_dispatch`) hand it plain values inside their thunks, or beside the
log call in `_dispatch`'s case.

**The collapse** (`0677c8dd`). `events/catalog.py`: `LlmRetryOfEntry`,
`LlmRoundOfEntry` and `ProviderOfEntryFailed` are deleted; `LlmRetry`,
`LlmRound` and `ProviderFailed` carry `provider` and `type` as
`Identifier | Absent` beside the already absent-able `host` and
`model`; `ProviderFailed`'s `named` and `where` become `QuotedProvider`
and `ReachingHost`; the three declarations name one variant each and
`__all__` loses three names. In `assembly.py` each of the three
builders becomes one constructor call.

**The pins** (`7662220f`) and **the suite** (`759f7f13`), below.

**`CHANGELOG.md`.** One entry under `### Changed` in the existing
`## 2026-08-22` section: the three events declared once, the assembly
out of the orchestrator, and the one grammar that genuinely loosened,
named as such.

### The inventory

`grep -n "def _" src/vinga_server/runtime/pipeline.py`, before at
`cee8be9a` and after, as the plan's inventory lens requires.

Before: 35 definitions in 1,783 lines. The eight module-level ones were
`_entry_of`, `_tool_fragment`, `_tool_called`, `_llm_retried`,
`_reported`, `_llm_rounded`, `_provider_failure` and `_not_allowed`.

After: 31 definitions in 1,547 lines. Four module-level ones remain:

- `_reported`, which unpacks a `Usage`, a `providers/` type the
  assembly may not take.
- `_tool_fragment`, which is the source selection for the one sentence
  that is not an event: four lines choosing which of a call's names the
  assembly is asked to render.
- `_tool_called`, which is the source selection for the event: three
  lines picking which assembly constructor describes this call.
- `_not_allowed`, which was never event assembly.

The four that left are `_entry_of`, `_llm_retried`, `_llm_rounded` and
`_provider_failure`, taking the frozen quartet type with them;
`_tool_fragment` stayed by handing its rendering over and keeping its
decision. The class's own 27 methods are unchanged in number and in
name; what moved out of them is the body of three thunks.

`grep -rn "OfEntry" src tests` from `vinga-server/` returns nothing.

### Deviations from the plan

Three, each small and each forced by something the plan could not see
from outside the code.

**The quartet's placement is the carried order, not the declaration
order.** The plan says the quartet lands "after `duration_s`, before
`input_tokens`", reproducing `LlmRoundOfEntry`'s carried order exactly.
Its of-entry twin declared `provider` and `type` BEFORE `duration_s`,
and that declaration order is impossible now: `duration_s` has no
default and the quartet does, and a dataclass refuses a non-default
field after a default one. So `duration_s` moves up to sit with the
other required fields and the quartet follows it, which is exactly what
the plan's own words describe and produces the carried order the plan
requires, since `duration_s` is `carried=False` and is not in that
order at all. The golden's field lists for the three survivors are
byte-unchanged, which is the measurement.

**The tool-name fragment splits into a rendering in `assembly.py` and
a decision in `pipeline.py`, and only the rendering moved.** The plan
has `assembly.py` export the fragment "serving both callers".
`BuiltinToolCall.named` is declared `QuotedToolName`,
`McpToolCall.named` is `FromEntry` and `UnnamedToolCall.named` is
`Nothing`, so a builder handed the general `Fragment` fails the type
check: for the three events, each variant's declaration IS the rule for
its own sentence and cannot be got wrong. The one sentence with no
variant to declare it is the malformed-arguments warning line, and its
answer has two halves that belong in different modules. WHICH of a
call's names may be printed is read off the classifier's source
constants, which decision 4 keeps beside the classifier; HOW each of
the three answers renders is the fragment vocabulary's. So
`assembly.tool_fragment(tool, entry)` renders whichever name it is
handed and refuses nothing, and `pipeline.py` keeps a four-line
`_tool_fragment(classified)` as the one named home of the decision,
which is what stops the warning line and the `tool_call` event beside
it from coming to disagree. Both docstrings say plainly which half they
are, and the assembly suite's fragment test is scoped to the rendering
it can actually pin; the decision is pinned at the sentence, in
`test_session_tools.py`, where a builtin's name reaches the warning
line and a name no namespace publishes does not.

**`_reported` lost a parameter.** The plan keeps it in `pipeline.py`
for taking `Usage`, and gives `llm_rounded` plain token counts. Once
the wrapping moved, `_reported`'s third return value was
`first_token_ms` handed back unchanged, so it now takes the `Usage`
alone and answers two plain numbers, and `_llm_round_done` passes
`first_token_ms` straight through. The two numbers feed the turn's
`round_done` as well, which had been unpacking the same `Usage` a
second time on the next line. No prose was lost: the paragraph about
timing the first spoken token was already in `_llm_round_done`'s own
docstring and in the field's note.

Otherwise the plan held as written. The five call sites are the five it
named; #235's `match` arms are untouched; the source selection stayed
in `pipeline.py`; `DEVICE_ABORT_REASONS` stayed; the exception still
crosses as a `BaseException` with `ClassName.of` built inside the
thunk; nothing in `assembly.py` imports `runtime/` or `providers/`.

### Discoveries

**No test named an OfEntry class, as the plan predicted, and the grep
that says so is worth recording**: `grep -rn "OfEntry" src tests` found
fifteen lines before the milestone, six in `pipeline.py` and nine in
`catalog.py`, and none anywhere else. The baseline harness produces
the entry-less shapes through fixture providers that carry no
identity, never by naming a class, which is why the collapse moved
nothing in it.

**The baseline could not have seen the fragment retype even if one had
gone wrong.** It records `argument_types` as the type of the plain
value a `%` position receives, which is `str` for `Nothing` and `str`
for `QuotedProvider`. This is the plan's finding 2 confirmed against
the file rather than argued: the golden's ordered argument lists,
naming the value classes, are the only committed pin on that retype,
and they show it.

**Four test docstrings named the twins** and became false with the
collapse. Two explained why payload keys are part of a variant's
identity by pointing at `llm_round`'s pair:
`tests/unit/test_event_baseline.py`'s module docstring and its
`matches` helper. The rule is unchanged and its last example is
`tool_call`, whose three variants share channel, level and template and
are told apart by their keys alone; a check over the whole catalog says
it is now the only such event, and both docstrings name it instead.

The other two called the entry-less record a second shape, and the PR
review found them after the first sweep had missed them:
`tests/unit/test_event_surface_pins.py`'s
`test_a_failing_provider_the_registry_never_built_says_even_less` said
"the other shape of the same event" and called its two rendered
positions empty fragments, which is now the one variant saying less
with the empty forms of `QuotedProvider` and `ReachingHost`; and the
baseline harness's `unregistered` helper called it "the variant beside
every provider event", which is now the same variant with the four
fields it cannot fill left absent. Both say the post-collapse fact.
Beside them, the harness comment about drivers running more than one
scenario says "record shape" where it said "variant", which is what the
two halves of the retry and round drivers produce.

The lesson for the next collapse is the cheap one: `grep -rn "OfEntry"`
finds the names and finds nothing that describes them, and prose about
a deleted shape does not mention the class it described.

**`UnnamedToolCall.source` needed a cast the untyped orchestrator never
did.** `mypy` runs strict over `events/` and the field is declared
`Literal[ToolSource.DEVICE, ToolSource.UNKNOWN]`, which no annotation
can carry across the classifier's plain string. `_namespace` casts, and
says why in its docstring: `ToolSource` refuses a word that is not one
of the four, and `verify()` refuses the two this shape may not say,
inside the emitter's guard. Checking it a third time in the builder
would move a refusal out of the guard that holds it.

### Verification

From `vinga-server/`, on `feature/one-shape-per-site-m1`.

After the move commit, with the twins still declared:

- `uv run pytest tests/unit -q`: **2785 passed, 1 failed, 20
  skipped** in 260 s, the one failure being `test_event_docs.py`'s
  reference diff, stale since the widening commit and regenerated at
  the pins commit. The
  record baseline and the golden inventory passed untouched, which is
  the behavior-preserving claim for the move.

After the collapse, before the pins: the two golden pins failed and
nothing else in the event suites did (**82 passed, 2 failed**), and
`test_event_baseline.py` passed **8 passed** with the committed capture
unchanged.

Final, on the whole branch:

- `uv run ruff check .`: **All checks passed!**
- `uv run mypy`: **Success: no issues found in 4 source files.**
- `uv run pytest tests/unit -q`: **2803 passed, 20 skipped** in 261 s.
- `uv run pytest tests/integration -q`: **61 passed** in 192 s.

The pins, regenerated twice each with `PYTHONDONTWRITEBYTECODE=1`
exported:

- `uv run python -m tests.unit.test_event_golden`, twice: the second
  run's file is byte-identical to the first
  (`2854923c8fa61204dd6859d6f5c22d48bb1d314b1b47fb3dba168256bceea8f4`).
- `uv run vinga-server events reference > ../docs/reference/events.md`,
  twice: likewise
  (`a455f0bdf3334287e9a2e26e5bcb892e190c03d55eb630049140b2ea7302bee8`).
- The committed record baseline, SHA-256 before the regeneration run
  and after it:
  `a7f750859c3a1da7ae2193080022260768d916aa5d1a2f3927e5b74339990f5f`,
  unchanged. The plan expected this and required it be measured rather
  than assumed; it was, and it did not move.

The golden's diff is three deleted variants and six changed lines:
`provider` and `type` go from `"required": true` to `"required": false`
on each of the three survivors. Nothing else moved, which says the
quartet landed in the order the of-entry twins carried it in and that
the surviving `provider_failed` kept the `QuotedProvider` and
`ReachingHost` argument rows while the `Nothing` pair left with its
variant.

The reference's diff is the three OfEntry sections and their "Variant
2" headings gone, the header's arithmetic from "85 variants" to "82",
the widened `quoted_provider` pattern and description, the index
counting one variant where it counted two for the three events, and the
re-homed notes: the atomicity sentence on `llm_retry` and
`provider_failed`, and the five `llm_round` field notes all present on
the one surviving variant.

Nothing here needs hardware, so no verification step was left
unverifiable.

## PR review round (PR #259)

External review of the PR diff: claude CLI backend, model
`claude-opus-5`, read-only tool set, 2026-08-22, [posted on the PR](https://github.com/rafacm/vinga/pull/259#issuecomment-5382845290).
Verdict: mergeable after findings 1 and 2 are fixed; two P2 and two P3,
all four folded in before merge.

The round is worth recording for what it did before it found anything.
It verified the milestone's mechanical claims itself rather than taking
them: that the carried field order reproduces the three OfEntry twins
on all three events, so the byte-still baseline is real and not merely
asserted; that `_reported`'s retype is value-for-value equivalent on
both the event and the `round_done` path; that the `tool_fragment` call
site is behaviourally equivalent to the deleted `_tool_fragment`; that
the `def _` inventory is 30 in 1,530 lines exactly as recorded; that
the widened `QUOTED_PROVIDER` still refuses an unquoted entry. All four
findings are what was left after that, and all four are about what the
code SAYS rather than what it does.

1. **P2: the merged fragment signature moved the naming rule into the
   reply path while three docstrings claimed it lived in `events/`.**
   `tool_fragment(tool, entry)` quotes whatever arrives as `tool` and
   knows nothing about a call's source, so the structural property the
   deleted `_tool_fragment(classified)` had, that a device tool's name
   could not be printed by it whatever the caller did, was gone. No
   live leak, since `_dispatch` is the only caller and it guards, but a
   weakened contract described as an intact one, and the new test could
   not have failed if the rule had broken.
   *Resolution* (`27db5ebc`): the reviewer's option (b). `pipeline.py`
   gets `_tool_fragment(classified)` back as the one named home of the
   decision, beside the classifier's constants; `assembly.tool_fragment`
   keeps the two-argument signature and its docstring says it renders
   whatever it is handed and refuses nothing; the module docstring and
   the builders' comment stop claiming the rule; the fragment test is
   renamed to what it pins and points at `test_session_tools.py`, which
   pins the decision at the sentence. Deviation 2 above is rewritten to
   record the split rather than the claim.

2. **P2: two more twin-era docstrings survived, and the doc said the
   sweep was complete.** `test_event_surface_pins.py`'s entry-less
   provider test called its subject "the other shape of the same event"
   with "two empty fragments", wrong twice over after the collapse, and
   in the file the plan itself names as the emission-level half of the
   atomicity pin; the baseline harness's `unregistered` helper called
   its record "the variant beside every provider event", eleven lines
   from a comment the milestone had already corrected.
   *Resolution* (`2f93e169`): both reworded to the post-collapse fact,
   and the Discoveries paragraph names four docstrings and how the
   first sweep missed these two.

3. **P3: `llm_round` was the one survivor whose newly optional
   `provider` and `type` carried no explanation.** Plan-conformant, since
   the note was re-homed only where an of-entry twin had one, and a gap
   the collapse itself created: the reference now states three optional
   fields with nothing saying two of them are one answer, which invites
   a query grouping rounds by `type` with `provider` missing.
   *Resolution* (`bba52723`): the same `NOTE` its two siblings carry.
   The reference gains three lines; the golden does not move, because it
   holds no wording by design; the baseline is byte-still.

4. **P3: the export pin asserted the `__all__` literal while claiming a
   namespace property.** `__all__` governs `import *` alone, so a public
   `entry_fields` added beside it would pass the check while being
   importable by anybody. The real pin, the builder test, does hold.
   *Resolution* (`f3028b42`): the test reads the module's public names,
   imports excluded, and holds `__all__` to the same list so the two
   cannot drift. Checked by mutation, restored by copy and `touch`.

### Verification after the review round

From `vinga-server/`, on the amended branch.

- `uv run ruff check .`: **All checks passed!**
- `uv run mypy`: **Success: no issues found in 4 source files.**
- `uv run pytest tests/unit -q`: **2803 passed, 20 skipped** in 262 s.
- `uv run pytest tests/integration -q`: **61 passed** in 193 s.
- `uv run python -m tests.unit.test_event_golden`, twice: unchanged by
  finding 3 and byte-identical between runs
  (`2854923c8fa61204dd6859d6f5c22d48bb1d314b1b47fb3dba168256bceea8f4`,
  the same file the milestone committed).
- `uv run vinga-server events reference > ../docs/reference/events.md`,
  twice: byte-identical between runs
  (`600507d06bbf5afe5eb5210787b79dfde287183f01b77eb6fec829c63aea40f4`),
  three lines longer than the milestone's for finding 3's note.
- The committed record baseline, SHA-256 before and after:
  `a7f750859c3a1da7ae2193080022260768d916aa5d1a2f3927e5b74339990f5f`,
  unchanged, as it has been through the whole milestone.
- `grep -rn "OfEntry" src tests`: nothing.

The `def _` inventory moved by one with finding 1: 31 definitions in
1,547 lines, `_tool_fragment` restored. The Inventory section above
records the amended figures rather than the reviewed ones.
