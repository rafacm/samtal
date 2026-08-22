# Emit one shape per decision site and move assembly out of the orchestrator

## Goal

Implement issue #240, the third issue of the events phase of #246.
Lines 166 to 420 of `runtime/pipeline.py`, about 260 lines and a
third of the module's executable code, select between "with config
entry" and "of entry" twins of the same events. The three twin pairs
(`LlmRound`/`LlmRoundOfEntry`, `LlmRetry`/`LlmRetryOfEntry`,
`ProviderFailed`/`ProviderOfEntryFailed`) collapse to one variant
each, and the assembly helpers that remain move out of the
orchestrator into `events/`, so telemetry shape logic stops living
in the reply path. The coordination note in the issue is settled by
history: #235 landed first (PR #236) and its `match` over the
provider stream is untouched here; the two changes are adjacent in
the file and disjoint in what they touch.

The companion implementation doc,
[`2026-08-22-one-shape-per-site-implementation.md`](2026-08-22-one-shape-per-site-implementation.md),
records what the milestone actually did, deviations from this plan,
and discoveries; no deviations says so explicitly.

## The issue's decisions, restated

1. **Each twin pair collapses to a single event shape.**
2. **Whatever assembly helpers remain move out of the orchestrator
   into `events/`.**
3. #235 coordination is moot: it merged first, and this lands on
   top of the `match` dispatch it introduced.

## The measurement this plan stands on

Each pair shares a byte-identical `TEMPLATE` and `ARGS` tuple
(verified by reading `events/catalog.py` around lines 1195 to
1360). The `llm_round` and `llm_retry` pairs differ only in their
carried payload fields (the OfEntry half carries the entry quartet:
`provider`, `type`, and already absent-able `host` and `model`).
The `provider_failed` pair ALSO differs in two required rendered
fields: `named` is `Nothing` on one side and `QuotedProvider` on
the other, `where` is `Nothing` versus `ReachingHost`. That second
difference is what the collapse must engineer around, because a
union of two value types is refused at `_read`, a rendered field
cannot default to `ABSENT`, and `QuotedProvider("")` refuses at
construction.
The entry-less twin fires only for a provider the registry never
built, which in practice is test fixtures; production emits one
shape. No test file references any OfEntry name directly; the
baseline harness produces the entry-less shapes through its fixture
providers.

## Design decisions this plan makes

1. **The collapsed variant carries the quartet as absent-able
   fields.** `provider: Identifier | Absent` and `type: Identifier |
   Absent` join the already absent-able `host` and `model`, each
   `value(default=ABSENT)`, placed to reproduce
   `LlmRoundOfEntry`'s carried order exactly (the quartet sits
   after `duration_s` and before `input_tokens`), so production
   JSON key order does not move. Payloads are byte-identical on
   both paths by construction: `payload()` drops absent keys,
   which IS today's entry-less record, and carries them when
   present, which IS today's of-entry record. Templates and ARGS
   are unchanged, so rendered sentences and `record.msg` are
   unchanged everywhere.
   The prose reconciliation is itemized, since it is the surviving
   sections' visible diff in the reference: the no-entry sentence
   is a variant-level `NOTE` on `LlmRetry` and `ProviderFailed`
   and is replaced on each survivor by the atomicity sentence
   re-homed from the OfEntry side (the quartet is answered whole
   or not at all; absent means the registry never built the
   provider); the `llm_round` pair's five field notes (`round`,
   `turns`, `first_token_ms` on one side; `model` with its GenAI
   sentence and `input_tokens` on the other) each land on the
   surviving field unchanged; and the survivors' class docstrings
   drop "on a provider with no configured identity", which stops
   being true.
2. **Provider and type stay atomic as a pair, as a type fact
   inside the assembly module.** The frozen quartet type (decision
   3) has required `provider`/`type`, so nothing but it can
   produce the four values and "answered whole or not at all"
   survives structurally. What is accepted and stated: the
   COLLAPSED VARIANT itself admits a half-quartet if constructed
   by hand, since `verify()` accepts absent optionals; the pin for
   that is the quartet type being the only producer plus the
   assembly unit test, and a declaration-time entanglement check
   is out of scope for this issue and for #241 (a pin thinning,
   not a machinery enlargement); if one is ever wanted it is its
   own issue.
3. **The assembly helpers move to a new `events/assembly.py`, with
   plain-value signatures, and the module is justified by what its
   caller stops knowing.** One responsibility: one provider's
   identity and one call's classification, as the variant that
   describes it. The orchestrator stops naming variant classes,
   ordering field lists, or knowing which value type wraps which
   argument; that, not a vocabulary crossing, is the depth claim,
   because with plain values the signatures speak builtins in and
   `Variant` out. What makes the module more than forwarding is
   the frozen entry quartet type: today's `_Entry` moves INTO
   `assembly.py` intact, `provider` and `type` required, so
   atomicity stays a type fact at the only place the four values
   are built (this supersedes the draft's decision 2 framing; the
   assembly-level unit test pins the builder, and
   `test_event_surface_pins.py`'s emission-level
   `assert not hasattr(failed, "provider")`-style half survives
   unchanged). The builders (`llm_retried`, `llm_rounded`,
   `provider_failure`, the three tool-call constructors, the
   shared name fragment) are thin single-constructor functions and
   say so; the quartet type and the fragment logic are what earn
   the module its name. `assembly.py` imports the catalog and
   values, never `runtime/` or `providers/` types; `pipeline.py`
   unpacks its objects at the call. Module docstring states the
   contract.
   Two boundary cases the signatures must respect, named so they
   are not discovered mid-milestone: `_reported` takes `Usage | None`
   (a `providers/` type) and therefore STAYS in `pipeline.py`; the
   `llm_rounded` assembly function takes
   `input_tokens: int | None`, `output_tokens: int | None`,
   `first_token_ms: int | None` as plain values. And
   `provider_failure` keeps taking `BaseException` (a builtin, so
   it crosses lawfully) with `ClassName.of` still built from the
   exception itself inside the thunk.
4. **The tool-call SOURCE selection stays in `pipeline.py`, beside
   the classifier that produces the token; only construction
   moves.** `runtime/turns.py` spells its source constants locally
   on purpose and `TOOL_SOURCES` stays one structure: moving the
   `== BUILTIN` / `== MCP` branches into `events/` would force an
   upward import, a second home for the set, or an untested
   equality. So the orchestrator's branch picks which of the three
   assembly constructors to call (that is the decision site the
   closed-set lens names), and each constructor builds its variant.
   Otherwise `pipeline.py` keeps only decision sites: the entry
   quartet construction, the field ordering, and the fragment
   logic leave the module; `DEVICE_ABORT_REASONS` and anything
   else in the region that is not event assembly stays.
5. **The pins move deliberately, and the baseline is expected
   byte-still.** The golden loses three variants (each affected
   event goes from two variants to one; the survivor's field list
   gains the absent-able quartet); `docs/reference/events.md`
   loses the three OfEntry variant sections and their index
   arithmetic; the committed event baseline is EXPECTED
   byte-identical (templates, args, payload keys all unchanged on
   every driven path), and that expectation is verified rather
   than assumed; if it moves, the milestone stops and records why
   before proceeding. Regenerations run twice, second run clean.
6. **`provider_failed`'s fragments become optional the way
   `ReachingHost` already is, and one documented grammar loosens.**
   The survivor declares `named: QuotedProvider` and
   `where: ReachingHost`; `QuotedProvider.of` takes `str | None`
   and answers the empty rendering for `None`, exactly as
   `ReachingHost.of` does, and the `QUOTED_PROVIDER` grammar widens
   to its optional form with its description amended. This is the
   one place in the milestone where a documented constraint
   genuinely loosens: the reference's grammar table shows the
   widened pattern, and the surviving variant's argument rows move
   from `empty_fragment` to `quoted_provider`/`reaching_host`. The
   docgen code needs no change; its content moves in exactly these
   ways.

## The standing review lenses, pre-answered

- **No-leak.** No new surface: the collapsed variants carry the
  same values the OfEntry twins carried, all validated at
  construction; assembly functions build values inside emit thunks
  exactly as the helpers do today. The `_entry_of` docstring's
  thunk rule moves with the quartet builder and stays true THERE;
  the name fragment is the stated exception and always has been:
  it has a second caller that is not an emit thunk (the
  malformed-arguments warning line in `_dispatch`), so
  `assembly.py` exports the fragment as its own function serving
  both callers, and its docstring states the rule for a fragment
  deliberately built beside a log line (a refusing construction
  there lands in the tool-error path, not the emitter's guard, and
  the fragment's own grammar is what bounds it).
- **Pin before reshaping.** The pins divide the proof honestly.
  The baseline proves channel, level, template, and payload key SET
  did not move on any driven path; it records sorted key sets and
  argument type names of rendered strings, so it cannot see a
  fragment retype or a misplaced quartet. The golden's ordered
  field and argument lists are the pin for exactly those two: its
  diff must show three deleted variants, the survivors' field
  lists in the stated order, and the `provider_failed` argument
  rows moving to the optional fragment types, and nothing else. The unit suites' existing pins on the events
  (surface pins, typed emit) keep passing unmodified except where
  they name an OfEntry class.
- **Closed sets.** The tool-call selection by `source` is a real
  decision site and keeps its three shapes; nothing about its
  closed set changes. No reason token moves.
- **Honest seams.** The new module's functions take plain values
  and return variants; nothing injectable, no default-construction
  policy.
- **Inventories by tooling.** `grep -rn "OfEntry" src tests` from
  `vinga-server/` returns nothing after the milestone; the region
  bound is `grep -n "def _" src/vinga_server/runtime/pipeline.py`
  before and after, recorded in the implementation doc.

## Module layout

- `events/assembly.py` (new): the adapter of decision 3, with the
  one-sentence contract: callers stop having to know how a
  telemetry event is put together, and the catalog stops being
  spoken to from the middle of the reply path.
- `events/catalog.py`: the three OfEntry variants deleted; the
  three survivors gain the absent-able quartet with merged notes;
  `__all__` updated.
- `runtime/pipeline.py`: the assembly region deleted; the five
  call sites that change are `_watchdog_stream`, `_llm_round_done`,
  `_provider_failed`, `_run_one`, and `_dispatch`, each handing
  plain values to `events/assembly.py` inside its thunk (or, for
  `_dispatch`'s warning line, beside it); #235's `match` arms call
  no assembly helper and do not change.
- Tests: a new unit suite for `events/assembly.py` (the quartet
  entanglement pin of decision 2, the tool-call selection, the
  builders' output compared against directly-constructed
  variants); existing suites migrate where they name OfEntry
  classes (none found by grep; verified during the milestone).
- Pins per decision 5; `CHANGELOG.md` entry under `### Changed`.

## Milestones

- [x] **[M1: collapse the twins and move assembly out.](2026-08-22-one-shape-per-site-implementation.md#m1-collapse-the-twins-and-move-assembly-out)** (PR TBD)
  One milestone: the collapse and the move touch the same lines,
  and a PR that collapsed without moving would rewrite the same
  helpers twice. Deepens `events/`: the orchestrator's interface
  to telemetry shrinks from nine shape constructors plus selection
  logic to a handful of assembly calls.

## Plan review round

External review of commit `be065bca`, 2026-08-22. Backend: claude
CLI 2.1.239, model `claude-opus-5`, read-only tool set (interim
fallback tier). Verdict as received: ready after the P1/P2
amendments; the spine (collapse the pairs, move assembly, baseline
still) is sound, but finding 1 is a design that cannot be declared
as written, findings 3 to 6 mean the module's contents and
signatures were not yet decided, and findings 2, 7, 8 point the
preservation claims at the wrong artifacts. Findings condensed but
faithful:

1. **P1: `provider_failed`'s twins differ in two required rendered
   fields.** `named: Nothing` vs `named: QuotedProvider` and
   `where: Nothing` vs `where: ReachingHost`; unions of two value
   types are refused at `_read`, rendered fields cannot be
   `ABSENT`, and `QuotedProvider("")` refuses at construction.
   `ReachingHost` already models the optional tail; `QuotedProvider`
   does not. The collapse needs `QuotedProvider.of(str | None)` and
   a widened `QUOTED_PROVIDER` grammar, which is a documented
   constraint genuinely loosening, so "the docgen needs no change"
   and "no new surface" are false as written.
   *Resolution* (`b549ae78`): the measurement paragraph and
   decision 6 rewritten; `QuotedProvider.of(str | None)`, the
   widened grammar, and the stated loosening.

2. **P2: the baseline is byte-still for unstated reasons and cannot
   see what could move.** It records sorted key sets and argument
   type names of rendered strs, so a fragment retype or a
   misplaced quartet is invisible to it; the golden's ordered lists
   are the pin for both. The quartet's placement must reproduce
   `LlmRoundOfEntry`'s carried order.
   *Resolution* (`bbbd8d26`): quartet placement stated, and the
   proof divided honestly between the baseline (sets, templates)
   and the golden (ordering, retypes).

3. **P2: the adapter justification contradicts the plain-value
   signature rule** (one vocabulary in, not two), the post-collapse
   builders are single constructor calls, and decision 4 moves out
   `_tool_called`, which the plan's own lens calls a real decision
   site. Justify by what the caller stops knowing, or design
   around the selection.
   *Resolution* (`cf0d9bb9`, with finding 8): the module is
   justified by what the caller stops knowing, the quartet type
   moves in whole as the structural depth, and the thin builders
   say they are thin.

4. **P2: no stated answer for the classifier constants.** Moving
   the source selection into `events/` forces an upward import, a
   second home for `TOOL_SOURCES`, or an untested equality; the
   cheapest honest answer is that the selection stays in
   `pipeline.py` and only construction moves.
   *Resolution* (`d86871a1`): the source selection stays beside
   its classifier; only construction moves.

5. **P2: `_reported` and the `Usage` type are unnamed work.** Under
   the plain-value rule the assembly cannot take `Usage`;
   `_reported` stays or the call site unpacks. `BaseException` is a
   builtin and may cross.
   *Resolution* (`bacaabc6`): `_reported` stays; the round builder
   takes plain token counts; `BaseException` crosses lawfully.

6. **P2: `_tool_fragment` has a second caller that is not an emit
   thunk** (the malformed-arguments warning line), which the merged
   signature erases and which already falsifies the
   thunk-only rule the lens claims moves intact.
   *Resolution* (`b67f1449`): the fragment is its own exported
   function serving both callers, with the beside-a-log-line rule
   stated.

7. **P2: #235's `match` is a different region.** Its four arms call
   no assembly helper; the five real call sites are
   `_watchdog_stream`, `_llm_round_done`, `_provider_failed`,
   `_run_one`, and `_dispatch`.
   *Resolution* (`8ef6f222`): the five real call sites named; the
   goal's overlap claim corrected.

8. **P2: the atomicity pin does not pin the variant, and the plan
   contradicts itself on `_Entry`.** After the collapse the variant
   admits a half-quartet; keep the frozen quartet type inside
   `assembly.py` with required `provider`/`type` so atomicity stays
   structural, re-home the NOTE onto the survivor, and name
   `test_event_surface_pins.py`'s emission-level half as surviving.
   *Resolution* (`cf0d9bb9`): the quartet type keeps atomicity
   structural inside `assembly.py`; the surface-pin half is named
   as surviving.

9. **P3: the note merge is undercounted and misnamed.** The
   no-entry note is a variant-level NOTE, five field notes need
   reconciling across the `llm_round` pair, and the survivors'
   docstrings stop being true.
   *Resolution* (`a0bbdc95`): the prose reconciliation itemized
   per note and docstring.

10. **P3: the plan assigns machinery to #241 that #241 is not
    scoped to carry.** State the loss and the pin; a
    declaration-time entanglement check is out of scope and worth
    its own issue if wanted.
    *Resolution* (`187d1f5c`): the entanglement check is out of
    scope here and for #241; the accepted looseness and its pin are
    stated.
