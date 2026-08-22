# Retire the event enforcement layer

## Goal

Implement issue #239, the second issue of the events phase of #246.
The strict and forgiving modes, the `schema_violation` recovery event
with its 14 generated per-channel variants, and the
`resolve_enforcement` wiring go away: roughly 370 lines of
`events/__init__.py` plus about 1,270 test lines guarding telemetry
that construction-time validation of every value, strict mypy over
the package, and the CI catalog diff already prevent. What stays is
the part that carries the promise: the emit guard (a telemetry bug
never costs a reply) and per-tap isolation (a broken consumer never
starves another or the log). A refused emission is dropped after the
existing one-sentence, no-leak report on the emitter's own channel.

The companion implementation doc,
[`2026-08-22-retire-event-enforcement-implementation.md`](2026-08-22-retire-event-enforcement-implementation.md),
records what the milestone actually did, deviations from this plan,
and discoveries; no deviations says so explicitly.

## The issue's decisions, restated

1. **The strict and forgiving modes, the `schema_violation`
   machinery, and the `resolve_enforcement` wiring in `app.py` and
   `main.py` are removed.**
2. **Per-tap isolation stays exactly as is**: a tap that raises is
   still caught per tap, reported once by class name, and the taps
   after it still run.
3. **The guarantee that mattered survives in the tap guards and the
   emit guard**; the belt-and-braces layer on top of three other
   guards goes away.

## Design decisions this plan makes

1. **A refused emission drops, after one plain report.**
   Identities and the variant are constructed under the guard, and
   a refusal is reported with the existing
   `_report(log, ERROR, REFUSAL_MESSAGE, label, code)` line, whose
   vocabulary is registry-owned only (`CONSTRUCTION_FAILED`,
   `WRONG_CHANNEL`, `UNBUILT_LABEL`, `REFUSAL_MESSAGE` stay).
   Nothing is dispatched for a refused emission: the recovery event
   was the forgiving mode's substitute, and with the modes gone
   there is nothing to substitute. The mechanics, stated so they
   are not discovered mid-milestone: `_built` answers
   `Checked | None`, both emitters grow an early-return drop branch
   on `None` before building an `Emission`, `SessionEvents.emit`
   reads `self._clock()` before the drop decision so it returns its
   timestamp either way, and `Checked`'s docstring drops its "or
   the recovery event's" clause. One stated, accepted loss: the
   report record carries no `session` or `device` field, where
   today the recovery event that followed it carried the validated
   identity. The reason is the same one that built the `unstated`
   machinery: the identity may itself be what refused, and only a
   validated one may be echoed; keeping the validated half alive
   solely to decorate one error line would preserve most of the
   recovery's machinery for a fraction of its value. A refusal
   remains diagnosable because it is deterministic: the lane guard
   of decision 6 makes it a failing test in development, which is
   where a schema bug is fixed.
2. **What existed only to feed the recovery payload goes with it.**
   `_replacement`, `SCHEMA_VIOLATION`, `SCHEMA_VIOLATION_MESSAGE`,
   the 14 generated `_violation_on` variants and their `internal`
   declaration, `Identity.unstated`, `UNSTATED_SESSION`, and the
   `safe` half of `_identities` (which built the recovery's payload
   from validated identities) are deleted. `Identity` itself is
   deleted, not shrunk: without `unstated` it is a frozen box
   around one callable, which fails the deletion test. The emitters
   hand `_identities` a `Mapping[str, Callable[[], EventValue |
   None]]`; it answers what was built and whether all of it was,
   under ONE guard around the loop rather than one per identity,
   because the per-identity guard existed so the recovery could
   still state the identity that validated, and with the recovery
   gone any identity failure refuses the emission whole. Its
   docstring is rewritten to say exactly that. The `internal` flag on `Declaration` is
   deleted WITH its only user unless another internal declaration
   exists (there is none today). `internal` has FOUR readers, all
   named here so none is discovered mid-milestone: the docgen
   header's counting sentence ("58 events in 99 variants, 57 ...
   and 1 internal" becomes a plain count of events and variants),
   `_index_row`'s `(internal)` suffix and `_event_section`'s
   "**Internal.**" paragraph (both branches deleted), and the
   baseline suite's internal-exemption filter. The docs test
   `test_the_internal_event_is_listed_as_internal` is deleted, not
   migrated.
3. **What raised, and everything spelled for raising, goes.**
   `EventSchemaError`, `_refuse` and its scrubbed-chain mechanics,
   `refusal_text` (whose only caller is `_refuse`), the `Fault`
   dataclass (every surviving fault has no detail, so `_Refusal`
   carries the bare code string and the report renders it
   directly),
   `EventEnforcementError`, `STRICT`/`FORGIVING`/
   `ENFORCEMENT_MODES`/`ENFORCEMENT_ENV`/`_enforcement`,
   `enforcement()`, `set_enforcement`, `resolve_enforcement`. The
   no-leak property `_refuse` protected (nothing this module raises
   carries what it was handed) becomes vacuous in the guarded
   region: construction, identity building, the report, and each
   tap offer retain their blanket catches and contain no `raise` at
   all once `_refuse` goes. The claim is deliberately not broader:
   `deepcopy`, `replace`, `Emission(...)` and the clock run outside
   the guards, as they do today, and payload values are builtins by
   construction, so their failure modes are unchanged by this
   milestone. `main.py` and `app.py` lose the
   resolve calls and the `EventEnforcementError` handling around
   boot.
4. **`VINGA_EVENTS_ENFORCEMENT` disappears from the surface,
   including this repository's own image.** `vinga-server/
   Dockerfile` sets `ENV VINGA_EVENTS_ENFORCEMENT=forgiving` under
   a nine-line posture comment; both are deleted in this milestone,
   named in the module layout beside the wiring. Elsewhere a
   deployment that still sets the variable gets an inert one, which
   is harmless; only the infra repository's ConfigMap remains a
   separate cleanup, recorded as a follow-up note in the
   implementation doc. The CHANGELOG carries this as a `### Removed`
   entry naming all three surfaces an upgrader can notice: the
   `schema_violation` event a collector may filter on, the
   `VINGA_EVENTS_ENFORCEMENT` variable, and the boot-time refusal
   of a misspelled value.
5. **The pins move deliberately, all five.**
   `docs/reference/events.md` loses the `schema_violation` section,
   its per-channel variants, its index row, the header prose that
   names the env var, and the internal half of the header's
   counting sentence; the golden inventory loses the event; the committed
   event baseline loses nothing (the internal event was exempt from
   driving, verified in the milestone rather than assumed); the
   docgen loses whatever prose renders the enforcement mechanics.
   The fifth pin is hand-written: `vinga-server/README.md` carries
   a `schema_violation` row in its events index, which three docs
   tests hold against the catalog in both directions, and a Logging
   paragraph documenting the variable, both defaults, the recovery
   event and the boot refusal; the row and the paragraph go, and
   `test_an_unusable_enforcement_value_does_not_block_the_reference`
   is deleted with the machinery it exercises. Each regenerated
   artifact is a reviewed diff produced by its own script, and the
   reference regeneration is CI-checked as always.
6. **The test surface follows the deleted surface down.**
   `test_event_enforcement_mode.py` is deleted whole.
   `test_event_enforcement_sentinels.py` is split by what each test
   actually pins: sentinels proving the REPORT line and the tap
   guard leak nothing (plant a credential-shaped value in a failing
   thunk, a hostile exception class name in a tap; assert absence
   from the report's sentence and args in both log formats) SURVIVE,
   rewritten against the drop-and-report path. The rest is
   classified by PROPERTY, not by mode, because three surviving
   behaviors have coverage only inside mode-shaped tests today.
   The properties that survive the deletion, each re-pinned
   against drop-and-report (one report record with
   `args == (label, code)`, nothing reaching taps or capture,
   `emit` returning normally): a wrong-channel emission, a failed
   construction thunk, a misplaced value (`verify()`'s wrong-field
   check), a descriptor past its declared bound at emit, an
   identity that fails building refusing the emission whole, and a
   broken log channel during the report costing nothing. Only
   tests whose whole claim is strict raising, forgiving
   substitution, mode resolution, or the recovery event's shape
   are deleted with the machinery. The `-O` subprocess pin in the
   mode suite is the suite's only guard of #155's no-assert rule
   and outlives the modes: it is rewritten against the drop path
   (under `python -O`, a refused construction still reports and
   dispatches nothing) and rehomed in the surviving sentinel
   suite; the no-assert comment at the top of the mode section
   moves to the guard region that keeps the rule.
   `tests/conftest.py` and `tests/support/apps.py` drop their
   enforcement-mode setup, and the lanes get a replacement for the
   loudness strict mode provided: an autouse fixture in
   `tests/conftest.py` that captures records on the
   `vinga_server.*` channels and fails the test if any record's
   `msg` is `REFUSAL_MESSAGE` (a refused emission anywhere in a
   lane is a failing test, not a silent drop under a green suite).
   The plan states this fixture, not strict mode, is what keeps the
   lanes loud from now on; a test that deliberately drives a
   refusal opts out by using the fixture's own allowance. Suites that only touched the layer
   incidentally (`test_event_typed_emit.py`,
   `test_event_surface_pins.py`, `test_event_docs.py`,
   `test_event_values.py`) migrate mechanically where they
   reference deleted names.

## The standing review lenses, pre-answered

- **No-leak.** The surviving surface is the report line and the tap
  report, both already fixed-vocabulary; the sentinel tests that
  prove it survive, rewritten, and are named in decision 6. The
  scrubbed-raise machinery is deleted because nothing in the
  guarded region raises any more, not because the property stopped
  mattering; the reviewer's check is that the guarded region
  (construction, identities, report, per-tap offer) contains no
  `raise` after the milestone.
- **Pin before reshaping.** The three catalog pins move by design
  this time and each moves by regeneration script with a reviewed
  diff; the baseline is expected byte-still (internal exemption)
  and that expectation is verified, not assumed. Everything else
  (all non-violation events) must be byte-identical in all three
  pins, which the diffs prove.
- **Closed sets.** The refusal vocabulary (two fixed codes, one
  label) stays a closed set at its decision sites in the guard;
  nothing else changes.
- **Honest seams.** `Identity` is deleted (decision 2); no
  injectable defaults change.
- **Inventories by tooling.** The removal is bounded by the exact
  identifiers, not the word: `grep -rnE
  "ENFORCEMENT_ENV|VINGA_EVENTS_ENFORCEMENT|resolve_enforcement|set_enforcement|EventSchemaError|EventEnforcementError|SCHEMA_VIOLATION|schema_violation"
  src tests Dockerfile README.md` from `vinga-server/` returns
  nothing after the milestone (four unrelated subsystems use the
  word "enforcement" and keep it). Four prose sites the identifier
  grep cannot find are named as work in the module layout: the
  dispatch-order justifications in `events_cli.py` and `main.py`
  that cite the variable, `values.py`'s strict/forgiving refusal
  explanation, and the baseline harness's "the emitters have to
  stay strict" environment note.

## Module layout

- `events/__init__.py`: the deletions of decisions 1 to 3; `_built`
  keeps construct-report-drop; no new module and no new name.
- `events/catalog.py`: `SCHEMA_VIOLATION`, `_violation_on`, the
  generated declarations, and `Declaration.internal` go.
- `events_docgen.py`: enforcement prose out of the header
  material, the counting sentence rewritten, and the `internal`
  branches out of `_index_row` and `_event_section`.
- `app.py`, `main.py`: the resolve calls and error handling out.
- `Dockerfile`: the `ENV VINGA_EVENTS_ENFORCEMENT=forgiving` line
  and its posture comment out.
- Prose rewrites the identifier grep cannot find: `events_cli.py`
  and `main.py` (dispatch-order justifications citing the
  variable), `events/values.py` (the strict/forgiving refusal
  explanation), `tests/tools/event_baseline.py` (the regeneration
  environment note).
- Tests per decision 6.

## Milestones

- [x] **[M1: retire the layer whole.](2026-08-22-retire-event-enforcement-implementation.md#m1-retire-the-layer-whole)**
  (PR #258) One milestone: the
  deletion is one behavior change and splitting it would leave a
  merge where the modes exist but nothing declares the recovery
  event, which is not a state `main` should release. Deepens
  `events/__init__.py` by subtraction: callers keep the same
  `emit(thunk)` interface and stop having to know that modes exist.

## Plan review round

External review of commit `434bfe46`, 2026-08-22. Backend: claude
CLI 2.1.239, model `claude-opus-5`, read-only tool set (the interim
fallback tier while the codex quota is exhausted). Verdict as
received: ready after the P1/P2 amendments; the shape (drop after
one report, no substitute, one milestone) is right, and what is
missing is the blast radius outside src/tests, the emitter-side
drop mechanics, and a property-based test plan. Findings condensed
but faithful:

1. **P1: `Declaration.internal` has four readers, not one.** The
   docgen's header counting sentence, `_index_row`'s `(internal)`
   suffix, `_event_section`'s Internal paragraph, and the baseline
   filter; plus `test_the_internal_event_is_listed_as_internal`.
   Implementing the layout as written leaves `reference()` raising.
   *Resolution* (`bcb90357`): all four readers named, the counting
   sentence rewritten, both docgen branches deleted, the docs test
   deleted; decision 5 and the module layout updated.

2. **P1: this repository's own Dockerfile sets
   `VINGA_EVENTS_ENFORCEMENT=forgiving`** with a nine-line posture
   comment; decision 4 said only another repo sets it, and the
   src/tests greps cannot see it.
   *Resolution* (`cf6c4c53`): the Dockerfile ENV line and its
   posture comment are milestone work, named in the module layout.

3. **P1: the README is an unnamed fifth pin.** A `schema_violation`
   index row that three docs tests hold against the catalog, and a
   hand-written Logging paragraph documenting the variable and the
   boot refusal; `test_an_unusable_enforcement_value_does_not_block_the_reference`
   must go too.
   *Resolution* (`fbe99750`): five pins; the README index row, the
   Logging paragraph, and the boot-refusal docs test named.

4. **P2: `_built` cannot keep its `-> Checked` shape and dispatch
   nothing.** It must answer `Checked | None` (or invert into the
   emitters), both emitters need drop branches, the clock read must
   survive the drop path, and `Checked`'s docstring goes stale.
   *Resolution* (`95f76c33`): `_built` answers `Checked | None`,
   both emitters grow the drop branch, the clock read survives,
   `Checked`'s docstring loses the recovery clause.

5. **P2: the per-identity guard and `Identity` survive only to
   serve the deleted recovery.** One guard around the loop is
   behaviorally identical once any identity failure refuses whole;
   `Identity` is a box around a lambda without `unstated`.
   *Resolution* (`7276535c`): `Identity` deleted, `_identities`
   takes a mapping of callables under one guard, docstring
   rewritten.

6. **P2: the lanes lose their only mechanism for making a
   construction bug visible.** conftest's strict mode existed so CI
   could not quietly relax; dropping it with nothing in its place
   lets malformed emissions drop silently under green suites.
   *Resolution* (`eae022d0`): an autouse conftest fixture fails any
   test whose run produced a refusal report; stated as what keeps
   the lanes loud.

7. **P2: decision 6 classifies tests by mechanism and deletes the
   only coverage of three surviving behaviors** (the wrong-channel
   branch, verify()'s misplaced-value check, the descriptor bound
   at emit), plus a broken-log test whose observable disappears.
   Classify by property and re-pin each against drop-and-report.
   *Resolution* (`1a3b8987`): tests classified by property; six
   surviving properties listed, each re-pinned against
   drop-and-report.

8. **P2: the `-O` pin dies as collateral.** The only `python -O`
   run in the suite pins #155's no-assert rule, which outlives the
   modes; rewrite it against the drop path and rehome it, along
   with the no-assert comment in the mode section.
   *Resolution* (`4fc24578`): the `-O` pin is rewritten against the
   drop path and rehomed with the no-assert comment.

9. **P2: kept vocabulary with no caller or no second value.**
   `refusal_text`'s only caller is `_refuse`; every surviving
   `Fault` has no detail, so the two-field record and `rendered()`
   conditional encode variation that no longer exists.
   *Resolution* (`6b59d111`): `refusal_text` and `Fault` go;
   `_Refusal` carries the bare code string.

10. **P2: the exit-criterion grep cannot pass and misses prose.**
    Four unrelated subsystems use the word "enforcement"; and
    `events_cli.py`, `main.py`, `values.py`, and the baseline
    harness carry prose about strictness no ENFORCEMENT grep finds.
    *Resolution* (`a14b1495`): identifier-scoped greps over src,
    tests, Dockerfile and README; the four prose sites named in the
    module layout.

11. **P3: "no longer raises at all on the emit path" is broader
    than the code.** `deepcopy`, `replace`, `Emission(...)` and the
    clock run outside guards; claim the guarded region only.
    *Resolution* (`8b334fda`): the claim is narrowed to the guarded
    region, with the outside-guard expressions named as unchanged.

12. **P3: the refusal report is unattributed once `safe` goes.**
    No session or device on the report record; state the accepted
    loss with its reason or keep the validated half solely for the
    report's extra.
    *Resolution* (`448e7fe3`): stated as an accepted loss with the
    recovery's own reason; the lane guard makes a refusal a failing
    test in development.

13. **P3: the CHANGELOG entry covers the inert variable but not the
    surface removal.** A declared event and a boot refusal
    disappear; that is a `### Removed` entry.
    *Resolution* (`d0d8c3ce`): a `### Removed` entry naming the
    event, the variable, and the boot refusal.
