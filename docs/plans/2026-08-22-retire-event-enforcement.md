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

1. **A refused emission drops, after one plain report.** `_built`
   keeps its shape: identities and the variant are constructed under
   the guard, and a refusal is reported with the existing
   `_report(log, ERROR, REFUSAL_MESSAGE, label, fault.rendered())`
   line, whose vocabulary is registry-owned only (`Fault`,
   `refusal_text`, `CONSTRUCTION_FAILED`, `WRONG_CHANNEL`,
   `UNBUILT_LABEL`, `REFUSAL_MESSAGE` all stay). Nothing is
   dispatched for a refused emission: the recovery event was the
   forgiving mode's substitute, and with the modes gone there is
   nothing to substitute. `SessionEvents.emit` still returns its
   timestamp either way.
2. **What existed only to feed the recovery payload goes with it.**
   `_replacement`, `SCHEMA_VIOLATION`, `SCHEMA_VIOLATION_MESSAGE`,
   the 14 generated `_violation_on` variants and their `internal`
   declaration, `Identity.unstated`, `UNSTATED_SESSION`, and the
   `safe` half of `_identities` (which built the recovery's payload
   from validated identities) are deleted. `Identity` keeps only its
   `build` callable; `_identities` answers what was built and
   whether all of it was. The `internal` flag on `Declaration` is
   deleted WITH its only user unless another internal declaration
   exists (there is none today); the baseline suite's
   internal-exemption filter goes with it.
3. **What raised, and everything spelled for raising, goes.**
   `EventSchemaError`, `_refuse` and its scrubbed-chain mechanics,
   `EventEnforcementError`, `STRICT`/`FORGIVING`/
   `ENFORCEMENT_MODES`/`ENFORCEMENT_ENV`/`_enforcement`,
   `enforcement()`, `set_enforcement`, `resolve_enforcement`. The
   no-leak property `_refuse` protected (nothing this module raises
   carries what it was handed) becomes vacuous: the module no longer
   raises at all on the emit path, and the report line already
   carries only fixed vocabulary. `main.py` and `app.py` lose the
   resolve calls and the `EventEnforcementError` handling around
   boot.
4. **`VINGA_EVENTS_ENFORCEMENT` disappears from the surface.** The
   env var is no longer read anywhere; a deployment that still sets
   it gets an inert variable, which is harmless and stated in the
   CHANGELOG entry. The infra deployment that may set it is a
   separate repository's cleanup and is recorded as a follow-up
   note in the implementation doc, not touched here.
5. **The pins move deliberately, all four.**
   `docs/reference/events.md` loses the `schema_violation` section,
   its per-channel variants, and the header prose that names the
   env var; the golden inventory loses the event; the committed
   event baseline loses nothing (the internal event was exempt from
   driving, verified in the milestone rather than assumed); the
   docgen loses whatever prose renders the enforcement mechanics.
   Each regenerated artifact is a reviewed diff produced by its own
   script, and the reference regeneration is CI-checked as always.
6. **The test surface follows the deleted surface down.**
   `test_event_enforcement_mode.py` is deleted whole.
   `test_event_enforcement_sentinels.py` is split by what each test
   actually pins: sentinels proving the REPORT line and the tap
   guard leak nothing (plant a credential-shaped value in a failing
   thunk, a hostile exception class name in a tap; assert absence
   from the report's sentence and args in both log formats) SURVIVE,
   rewritten against the drop-and-report path; tests that pin
   strict raising, forgiving substitution, mode resolution, or the
   recovery event's shape are deleted with the machinery.
   `tests/conftest.py` and `tests/support/apps.py` drop their
   enforcement-mode setup. Suites that only touched the layer
   incidentally (`test_event_typed_emit.py`,
   `test_event_surface_pins.py`, `test_event_docs.py`,
   `test_event_values.py`) migrate mechanically where they
   reference deleted names.

## The standing review lenses, pre-answered

- **No-leak.** The surviving surface is the report line and the tap
  report, both already fixed-vocabulary; the sentinel tests that
  prove it survive, rewritten, and are named in decision 6. The
  scrubbed-raise machinery is deleted because nothing raises, not
  because the property stopped mattering; the plan states this so a
  reviewer checks the emit path really cannot raise (the guard's
  blanket catches remain).
- **Pin before reshaping.** The three catalog pins move by design
  this time and each moves by regeneration script with a reviewed
  diff; the baseline is expected byte-still (internal exemption)
  and that expectation is verified, not assumed. Everything else
  (all non-violation events) must be byte-identical in all three
  pins, which the diffs prove.
- **Closed sets.** The refusal vocabulary (two fixed codes, one
  label) stays a closed set at its decision sites in the guard;
  nothing else changes.
- **Honest seams.** `Identity` shrinks to its `build` callable; no
  injectable defaults change.
- **Inventories by tooling.** `grep -rn "enforcement\|ENFORCEMENT"
  src tests` from `vinga-server/` bounds the removal (wiring,
  conftest, suites); after the milestone the same grep returns
  nothing, and `grep -rn "schema_violation\|SCHEMA_VIOLATION" src
  tests ../docs/reference` returns nothing.

## Module layout

- `events/__init__.py`: the deletions of decisions 1 to 3; `_built`
  keeps construct-report-drop; no new module and no new name.
- `events/catalog.py`: `SCHEMA_VIOLATION`, `_violation_on`, the
  generated declarations, and `Declaration.internal` go.
- `events_docgen.py`: enforcement prose out of the header material.
- `app.py`, `main.py`: the resolve calls and error handling out.
- Tests per decision 6.

## Milestones

- [ ] **M1: retire the layer whole.** (PR TBD) One milestone: the
  deletion is one behavior change and splitting it would leave a
  merge where the modes exist but nothing declares the recovery
  event, which is not a state `main` should release. Deepens
  `events/__init__.py` by subtraction: callers keep the same
  `emit(thunk)` interface and stop having to know that modes exist.
