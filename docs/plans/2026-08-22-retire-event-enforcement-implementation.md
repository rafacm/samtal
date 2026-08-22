# Retire the event enforcement layer: implementation

Companion to
[`2026-08-22-retire-event-enforcement.md`](2026-08-22-retire-event-enforcement.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: retire the layer whole

### What was done

Six commits, in the plan's order: the guard rework, the catalog and
docgen deletions, the wiring and the two documents, the test surface,
the regenerated pins, and this section with the changelog.

**The guard.** `vinga-server/src/vinga_server/events/__init__.py` loses
the mode section whole (`ENFORCEMENT_ENV`, `STRICT`, `FORGIVING`,
`ENFORCEMENT_MODES`, `_enforcement`, `enforcement()`, `set_enforcement`,
`resolve_enforcement`, `EventEnforcementError`), the raise path
(`EventSchemaError`, `_refuse`, `refusal_text`), the recovery
(`_replacement`, `UNSTATED_SESSION`), the `Fault` dataclass, and
`Identity`.

What replaced them:

- `_built` answers `Checked | None`. Its docstring states the accepted
  loss the plan's decision 1 names: the report record carries no
  `session` and no `device`, because an identity may itself be what
  refused and only a validated one could have been echoed.
- Both emitters return early on `None`. `SessionEvents.emit` reads
  `self._clock()` before calling `_built`, so a refused emission still
  answers the instant it was made at.
- `_identities` takes a `Mapping[str, Callable[[], EventValue | None]]`
  and runs the loop under one guard, answering what was built and
  whether all of it was. Its docstring says what the single guard
  encodes: any identity failure refuses the emission whole.
- `_Refusal` carries `label` and a bare `code` string; `_construct`
  builds it with `WRONG_CHANNEL` or `CONSTRUCTION_FAILED` directly.
- `Checked`'s docstring drops its "or the recovery event's" clause.
- The no-assert note (#155) moved from the top of the deleted mode
  section to the head of the typed-path section, which is the guard
  region that keeps the rule.

`CONSTRUCTION_FAILED`, `WRONG_CHANNEL`, `UNBUILT_LABEL` and
`REFUSAL_MESSAGE` stay, as do `_report`, `_offer` and `_dispatch`,
untouched. The guarded region (construction, identity building, the
report, each tap offer) contains no `raise` at all after the milestone,
which is the reviewer's check from the plan's no-leak lens.

**The catalog.** `events/catalog.py` loses `SCHEMA_VIOLATION`,
`SCHEMA_VIOLATION_MESSAGE`, `_violation_on`, `VIOLATIONS`,
`SCHEMA_VIOLATION_DECLARATION` and their section comment, the three
`__all__` entries and `VIOLATIONS`'s, and `Declaration.internal` with
`declare`'s keyword for it.

**The docgen.** `events_docgen.py`: the counting sentence is a plain
count of events and variants, the two paragraphs about the variable and
its defaults are one sentence pointing at the README's Logging section,
`_index_row`'s `(internal)` suffix is gone and `_event_section`'s
Internal paragraph branch with it.

**The wiring.** `app.py` loses the import, the `resolve_enforcement()`
call and its comment, and `EventEnforcementError` from `BOOT_FAILURES`
(whose comment said "the four names below" and now says three).
`main.py` loses the import, the call and its comment, and the name from
the `except` tuple.

**The prose the identifier grep cannot see**, all four sites the plan
named: `events_cli.py` and `main.py` justified dispatching the events
group above the mode resolution, and now justify it against the boot;
`events/values.py` explained a construction refusal in terms of strict
and forgiving in three places (the module docstring's no-leak bullet,
the `IDENTIFIER_DOMAIN` note, and `EventValueError`'s docstring); and
`tests/tools/event_baseline.py` said the emitters have to stay strict
for a regeneration run.

**The image and the README.** `vinga-server/Dockerfile` loses the
`VINGA_EVENTS_ENFORCEMENT=forgiving` line and the nine-line posture
comment above the `ENV` block. `vinga-server/README.md` loses the
`schema_violation` row from its events index and the half of the
Logging paragraph documenting the variable, both defaults, the recovery
event and the boot refusal; what replaces it says the emission is
reported once and dropped.

**The tests.** `test_event_enforcement_mode.py` is deleted whole.

`test_event_enforcement_sentinels.py` keeps its name and every test
whose subject is what a refusal may SAY, each re-pinned against
drop-and-report: one report record with `args == (label, code)`, nothing
reaching a tap or a capture, and `emit` returning normally. The
strict/forgiving pairs collapse into one test each, since there is one
behavior now. Three helpers carry the shape: `reported` (exactly one
record whose unrendered `msg` is `REFUSAL_MESSAGE`, at ERROR, with the
fixed pair of arguments), `dispatched` (records carrying an `event`
field, empty for every refusal) and `carrying` (unchanged). The `-O`
subprocess pin is rehomed here, rewritten against the drop path: under
`python -O` a refused construction still reports exactly once and
dispatches to nobody.

Deleted from that suite: `test_a_strict_refusal_carries_neither_a_cause
_nor_a_context`, whose whole claim is the raised object. The three
production-path tests stay, driving real converted sites that emit from
inside an `except` arm, with the escaped-exception half replaced by the
report and the sentinel hunt kept whole.

`test_event_typed_emit.py`: the mode fixture goes, the two mode-shaped
refusal tests become one drop test, the forgiving-complaint test becomes
the report test (now asserting `msg` unrendered as well as `args`), the
three identity tests become two (refused whole, and the clock answered
either way), and the broken-log test is re-pinned on the emission after
the refusal, since a refused emission has no observable of its own left.

`tests/conftest.py` drops the `VINGA_EVENTS_ENFORCEMENT` block and gains
the lane guard. It was written as an autouse fixture that installed a
handler on the `vinga_server` channel tree for the length of one test;
the PR review round found the scope hole in that and it is now installed
once from `pytest_configure`, filling a session-long ledger, with the
autouse fixture reading a per-test delta off it and the residual
reported against the run. The section below records the round. Either
way the check is the same: a record whose `msg` is `REFUSAL_MESSAGE`
fails the test that produced it unless it requested the
`refusals_are_expected` fixture, and that guard, not a mode, is what
keeps the lanes loud. `tests/support/apps.py` drops "an unusable
enforcement mode" from its list of refusals that stay in the describe
phase. `test_event_values.py` and `test_event_docs.py` migrate their
references; `test_the_internal_event_is_listed_as_internal` and
`test_an_unusable_enforcement_value_does_not_block_the_reference` are
deleted.

**The pins.** `docs/reference/events.md` and the golden inventory are
regenerated by their own scripts, each run twice with the second run
clean. The committed record baseline is byte-untouched.

### Deviations from the plan

Two, both small.

1. **A fifth reader of `Declaration.internal`.** The plan's finding 1
   named four (the docgen's counting sentence, `_index_row`,
   `_event_section`, and the baseline suite's filter) and the module
   layout named the docgen. The baseline filter is in
   `tests/unit/test_event_baseline.py`, not in the harness under
   `tests/tools/`, and `test_every_catalog_variant_on_a_scoped_channel_is
   _produced` reads the flag to exempt the recovery event from the
   driving obligation. It is now unfiltered, with the docstring saying
   why nothing is exempt any more. The plan anticipated the reader; only
   its address was off.

2. **Two of the six surviving properties are re-pinned next door.** The
   plan's decision 6 lists six properties re-pinned against
   drop-and-report. Four land in the sentinel suite (a failed
   construction thunk, a misplaced value, a descriptor past its bound,
   an identity that refuses whole). The other two, a wrong-channel
   emission and a broken log during the report, have always been pinned
   in `test_event_typed_emit.py`, which owns the scratch declaration on
   another channel and the broken-handler fixture, so they were re-pinned
   there rather than moved.

### Discoveries

- **The `-O` pin needed a new observable.** Its old one was the raised
  `EventSchemaError`; the rewritten script installs a logging handler on
  the channel and a server tap, then prints whether the report arrived
  and how many emissions were dispatched. It is still a subprocess,
  because `__debug__` is fixed at interpreter start.

- **The production-path sentinels needed a laxer report filter.** Those
  paths log lines of their own around the refused emission (the filler
  cache line, for one), so "exactly one record with no `event` field" is
  false there through no fault of the emitter. The helper matches the
  unrendered `REFUSAL_MESSAGE` instead, which is a stronger claim about
  the record that matters and no claim at all about a neighbour's.

- **The lane guard found nothing, and the second time it was looking
  at more.** As first written it was function-scoped, so what it
  actually checked was the window between one test's setup and its
  teardown: both lanes were green, but the module-scoped baseline
  fixture that drives all eighty-one emit paths and the integration
  lane's module-scoped uvicorn boot were built outside it. Widened per
  the review round, so that it is armed from `pytest_configure` and
  every fixture scope and every teardown is inside it, both lanes are
  green again and the residual bucket is empty. That is the claim worth
  keeping: no test, no fixture at any scope, and no teardown in either
  lane drives a refusal.

### Follow-up

The infra repository's ConfigMap still sets `VINGA_EVENTS_ENFORCEMENT`.
It is inert from this milestone on, so nothing breaks, but the key
should be dropped there as a separate cleanup in that repository. A
ConfigMap change is inert without an explicit rollout restart, which is
worth remembering when it is made.

### Verification

- `uv run ruff check .`: clean.
- `uv run mypy`: clean (strict over the events package, which is what
  the project's mypy configuration covers).
- `uv run pytest tests/unit -q`: green.
- `uv run pytest tests/integration -q`: green.
- `uv run vinga-server events reference > ../docs/reference/events.md`,
  run twice: the second run left the file unchanged.
- `uv run python -m tests.unit.test_event_golden`, run twice: the second
  run left the file unchanged.
- The committed event baseline (`tests/unit/data/event-baseline.json`)
  is byte-untouched: the same SHA-256 before and after the milestone,
  and no entry in `git status`. Verified rather than assumed, as the
  plan required.
- The exit grep from `vinga-server/` over `src tests Dockerfile
  README.md` returns nothing.

## PR review round, M1 (PR #258)

External review of the PR diff: claude backend (codex quota exhausted),
claude CLI, model `claude-opus-5`, read-only tool set, 2026-08-22,
posted on the PR. Verdict: mergeable after the listed fixes, with the
behavior change itself confirmed correct and complete against the plan
(the guarded region contains no `raise`, both emitters drop on `None`,
the clock read survives the drop path, the identifier grep is clean, the
two generated pins match their generators, the baseline is untouched,
and all four prose sites were rewritten). What the round faulted was the
PROOF: two of the six properties the plan promised to re-pin were pinned
more weakly than claimed, and the fixture substituted for strict mode
had a scope hole over the one run that drives every emit path. Three P2s
and two P3s, each fixed with its own commit.

1. **P2: the `wrong_channel` refusal was pinned by no test at all.**
   The parametrized drop test drove the mismatch branch but asserted
   only the level and an empty tap list, so the label, the code, or both
   could have changed with the suite staying green; `wrong_channel`
   appeared nowhere outside the module that returns it. Fixed in
   `cccc453e`: the expected `(label, code)` pair rides the
   parametrization and the report is asserted unrendered and by
   equality. Checked by mutation: answering the mismatch with the other
   pair fails the test.
2. **P2: the lane guard did not see refusals from higher-scoped
   fixtures.** It was function-scoped, and pytest builds higher-scoped
   fixtures first and tears them down last, so the module-scoped
   baseline fixture that drives all eighty-one emit paths, the
   integration lane's module-scoped uvicorn boot, and every module
   teardown were outside its window. Strict mode covered those because
   it raised from inside the emitter at whatever scope was running.
   Fixed in `d7370ae2`: the handler is installed once from
   `pytest_configure` into a session-long ledger, the autouse fixture
   reads a per-test delta off it (so a module fixture's refusal is
   attributed to the test that first asked for it), and what is
   unaccounted for at the end of the run gets its own terminal section
   and fails the run. Both paths were driven with a throwaway probe.
   The doc's "the lane guard found nothing" claim was corrected to what
   is now actually checked in `4e865ba2`, after rerunning both lanes
   with the widened guard: still green, residual bucket empty.
3. **P2: the API production-path sentinel swallowed the escape it
   exists to detect.** Its request was wrapped in
   `contextlib.suppress(Exception)`, dead in the passing case and fatal
   in the failing one: a reintroduced `raise` after the report line
   would skip the handler's 500, leave the request with no response and
   send a traceback to an outer logger, and the test would still pass.
   Fixed in `fea4a7fa`: the suppression goes, the response is asserted
   500, and its body is asserted sentinel-free, which the hunt did not
   otherwise reach. Checked by mutation.
4. **P3: `reported()` claimed a channel it never checked.** Fixed in
   `9942165e`: the helper takes the channel and asserts `record.name`
   at every caller, `carries_nothing` passes the refusing subsystem's
   own, and the unused `said_on_the_session` helper goes.
5. **P3: the README said the refusal line "says what was refused",**
   which is the one thing it may not say. Fixed in `5ee8c0f2` with the
   CHANGELOG's own wording, which agrees with the generated reference
   the section points readers at.

### Verification after the round

- `uv run ruff check .`: clean.
- `uv run mypy`: clean.
- `uv run pytest tests/unit -q`: green, with the widened guard armed and
  no residual section.
- `uv run pytest tests/integration -q`: green, same.
- The reference and the golden inventory were regenerated again and
  neither moved: the round touched tests, the conftest and one README
  paragraph, none of which the generators read.
- The committed event baseline is still byte-untouched, by the same
  SHA-256.
- The exit grep still returns nothing.
