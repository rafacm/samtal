# Give the _Synthesis test constructions a real failure callback

Companion to
[`2026-08-15-synthesis-test-signature.md`](2026-08-15-synthesis-test-signature.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out.

## Milestone 1: fix the two _Synthesis constructions and pin the callback's silence

The two constructions in
`test_only_a_sentence_whose_audio_finished_counts_as_spoken` now pass a
shared recording callback instead of the session id, and the test says
that nothing was reported. The wrong-reason check was run both ways
against a failing voice and came out exactly as the plan's corrected
reference section predicts.

### What landed

**`samtal-server/tests/unit/test_session.py`.** One test function.
`failures: list[tuple[BaseException, float]]` and a `record_failure`
function with the signature spelled out (a function rather than a
lambda, so the parameter types are visible at the fixture) sit beside
the existing `spoken` list, and both `_Synthesis` calls take
`record_failure` where they took `session.session_id`. The second call
fits on one line again once the argument is shorter, which is the only
reformatting in the diff. A third assertion joins the two that were
there: `assert failures == []`, under a comment saying the mock voice
works, that one synthesis finished and the other was cancelled, and
that a failure reported by either would mean the test is no longer
about what counts as spoken.

The risk the plan names, a failure recorded after the assertion reads
the list, is not stated in the comment: the code already makes it
obvious. The first synthesis is awaited to its end by `_speak`, and the
second is cancelled and then awaited (`await cut` under
`contextlib.suppress`), so both drain tasks are finished before the
assertions run. `_speak`'s own `finally` awaits
`synthesis.wait_cancelled()` in both cases, which is the same guarantee
from the other side.

No existing assertion was weakened or removed, and no other test file
was touched.

**`CHANGELOG.md`.** A new `## 2026-08-15` section above 2026-08-14 with
one entry under `### Fixed`.

**Production code.** Untouched, as the plan decided.
`runtime/speech.py` and `runtime/pipeline.py` are correct; this was a
fixture bug.

### The wrong-reason check

Run as throwaway evidence, not committed. A probe file placed at
`samtal-server/tests/unit/test_zz_wrong_reason_probe.py` and deleted
afterwards rebuilt the affected test's setup (`two_persona_config`,
`device_session(config, TUTOR_MAC, ...)`, the same `Resampler`) and
called `session.runtime._speak` once per arm with a `FailingTts` whose
`synthesize` raises `ConnectionRefusedError("no route")` on the first
pull, modelled on `Unreachable` in `test_session_events.py`. The only
difference between the arms is the third argument to `_Synthesis`.

```
uv run pytest tests/unit/test_zz_wrong_reason_probe.py -q -s
```

Observed, both arms, from `samtal-server/`:

```
[string (the committed bug)] third argument='ed62d001e29043ddac0dd35bba23bdb9'
  raised out of _speak: TypeError: 'str' object is not callable
  failures recorded:    []
  spoken:               []

[recording callback (the fix)] third argument=<function arm.<locals>.record_failure>
  raised out of _speak: ConnectionRefusedError: no route
  failures recorded:    [('ConnectionRefusedError', 'no route')]
  spoken:               []
```

The traceback of the string arm, printed by a second run of the same
probe with `traceback.print_exception`, is the plan's account executed
line by line: `chunks()` (speech.py:99) re-raises the stored
`ConnectionRefusedError` into `_speak`'s loop (pipeline.py:1094), and
then, "during handling of the above exception", `_speak`'s `finally`
awaits `synthesis.wait_cancelled()` (pipeline.py:1105), which awaits
the drain task and receives the `TypeError` raised at speech.py:86,
where `self._report_failure(...)` calls a string. That `TypeError` is
what leaves `_speak`. The provider failure is gone, and nothing was
recorded.

So the answer to the issue's fourth criterion, in two parts:

- **The committed assertions were not passing falsely.** The test
  speaks one sentence to the end and cancels another mid-send. Neither
  path enters `_drain`'s `except Exception` handler, which is the only
  place the callback is invoked, so the string was never called and the
  spoken-count verdicts were earned. What was broken was the fixture's
  conformance to the constructor's contract, not the test's subject.
- **The argument was one failing voice away from lying.** In the string
  arm the caller sees a `TypeError` about a string instead of the
  refused connection, and the failure event an operator would correlate
  with a network policy is never reported. In the callback arm the
  failure is recorded with its elapsed time and the original
  `ConnectionRefusedError` propagates, which is what the production
  path does through the adapter lambda at pipeline.py:1054.

Neither arm counts the sentence as spoken, in both cases correctly:
`_speak` appends only after the audio has gone out, and no audio did.

### Deviations from the plan

None. The callback is a named function with the spelled-out
`(BaseException, float)` signature shared by both constructions, the
assertion is `failures == []`, no failure-path test was added here, no
production code changed, and the wrong-reason check was run both ways
and recorded above rather than committed as a test.

### Discoveries

**The string arm loses the failure twice over, not once.** The plan
predicted the wrong exception type reaching the caller. The probe shows
the other half as well: `failures` stays empty in that arm, so the
`provider_failed` event never happens either. Both halves come from the
same line, and both are what the recording callback restores.

**The probe needed no session surgery to reach the failure path.**
`_speak` takes a `_Synthesis`, and `_Synthesis` takes the provider
directly, so swapping in a failing voice is a constructor argument
rather than a `replace()` on the runtime's provider set the way
`reply_with` in `test_session_events.py` has to do it. That is also
what makes a committed failure-path test here so cheap that the plan
had to argue against it deliberately; the argument (it would pin the
private constructor harder for information the events lane already
carries) still holds.

### Verification

From `samtal-server/`, on the branch with the fix:

- `uv run ruff check .`: **All checks passed!**
- `uv run pytest tests/unit -q`: **1851 passed, 15 skipped** in 179 s.
  The same count as before the change: this milestone adds an assertion
  to an existing test rather than a test.
- `uv run pytest tests/integration -q`: **53 passed** in 154 s.

The affected test on its own,
`uv run pytest tests/unit/test_session.py -q -k only_a_sentence`:
**1 passed, 32 deselected**.

The bytecode trap in `AGENTS.md` does not apply to any of this:
everything above ran through pytest, whose `conftest.py` writes no
bytecode and clears the caches it finds, and no file was restored
mid-run.

### PR #148 review round

One external review of the PR diff (codex CLI, model gpt-5.6-sol,
read-only, 2026-08-15), posted on the PR by the review run itself.
Verdict: mergeable after the listed fix. One finding:

1. **P2: stale coverage claim contradicts the adopted plan
   resolution.** The plan's scope-creep risk paragraph still said
   the invoked callback arm is covered by `test_tts_lookahead.py`,
   which only checks propagation and cleanup; the plan's own review
   round had already placed the callback-observing coverage in
   `test_session_events.py`. Fixed in a652faf: the paragraph now
   cites each test for what it covers.

No code was touched by the round; the finding was documentation
consistency inside the plan.
