# Give the _Synthesis test constructions a real failure callback

## Goal

Implement issue #135: `tests/unit/test_session.py` constructs
`_Synthesis(sentence, tts, session.session_id)` in two places inside
`test_only_a_sentence_whose_audio_finished_counts_as_spoken` (lines
640 and 648 at main@3ff58e7), but the third parameter of `_Synthesis`
is `report_failure: Callable[[BaseException, float], None]`
(`runtime/speech.py`). A string is passed where a callable is
required, so the mistake only surfaces when a TTS failure path
actually invokes the callback. Fix both constructions to pass a real
recording callback, make the tests assert on it, and check whether
the affected test was passing for the wrong reason.

The companion implementation doc,
[`2026-08-15-synthesis-test-signature-implementation.md`](2026-08-15-synthesis-test-signature-implementation.md),
records what the milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by issue #135 and not re-litigated here:

1. **Both `_Synthesis` constructions pass a callable matching the
   signature.**
2. **The tests assert on the callback being invoked (or not) as
   appropriate for each case.** Passing a recording callback and
   never looking at it would fix the type without strengthening the
   test.
3. **The full unit lane stays green.**
4. **Check whether the affected tests were silently passing for the
   wrong reason**, and record the answer.

Evidence in the issue is pinned to main@8dd1a5f (it cites lines 640
and 648 and `runtime/speech.py:51-56`); re-verified at main@3ff58e7
for this plan: the two constructions are still at
test_session.py:640 and 648, both inside
`test_only_a_sentence_whose_audio_finished_counts_as_spoken`, and
the signature is at speech.py:52-56. The shape is exactly as the
issue describes.

## What the wrong argument actually does, for reference

Verified against `runtime/speech.py` at main@3ff58e7. The callback
is invoked in exactly one place: `_drain`'s `except Exception`
handler (speech.py:86), after `self._failure` has been recorded and
before the `finally` enqueues the end-of-audio sentinel. With a
string there, a TTS failure would raise `TypeError: 'str' object is
not callable` inside the handler; the `finally` still delivers the
sentinel, `chunks()` still re-raises the original failure, but the
drain task dies with an unobserved `TypeError` and the failure
event an operator would correlate is never reported. So the test's
happy path and barge-in path work today by luck: the affected test
uses a working fake TTS, and the callback is dead code in it unless
synthesis fails, which is precisely the situation the parameter
exists for.

## Decisions this plan makes

### The callback records, and the test asserts it stayed silent

Both constructions get the same recording callback: a
`failures: list[tuple[BaseException, float]]` local and a function
(not a lambda, so the signature is spelled out) appending to it.
The test then asserts `failures == []` at the end: the sentence
that finished and the sentence that was cancelled mid-send were
both synthesized by a working provider, and a reported failure in
either would mean the test no longer covers what its name says.
This is the "or not" arm of the issue's second criterion, and it is
the honest one for this test: its subject is what counts as spoken,
not how failure is reported.

The invoked arm already has real coverage where it belongs:
`tests/unit/test_tts_lookahead.py` exercises the failing-sentence
path end to end through the pipeline
(`test_a_failing_sentence_still_lets_the_earlier_ones_be_heard`),
which passes the pipeline's real callback. No new failure-path test
is added here: duplicating that coverage against a directly
constructed `_Synthesis` would pin the private constructor harder
for no new information. If the review disagrees, that is a plan
question, not an implementation one.

### Why the test was passing, answered in the implementation doc

The check the issue asks for is a reading plus a demonstration: run
the affected test with a TTS that fails, once with the old string
argument and once with the fix, and record what each does. The
expected answer, from the reference section above: with the string,
the failure is still re-raised by `chunks()` (so `_speak` fails)
but the drain task dies on an unobserved `TypeError` and no failure
is reported; with the callback, the same failure is recorded by the
callback. The demonstration is throwaway evidence for the
implementation doc, not a committed test, for the reason above: the
committed suite keeps covering the failing path through the
pipeline, where the real callback lives.

### No production code changes

`runtime/speech.py` and `runtime/pipeline.py` are correct and
untouched. The issue is a test bug; the diff is one test function
plus the docs. A type annotation or runtime check in `_Synthesis`
to reject non-callables is deliberately not added: the constructor
is private to the runtime package, its one production caller passes
the pipeline's bound method, and mypy-style enforcement is not part
of this repository's toolchain today.

### One milestone, one PR

The diff is one test function, a CHANGELOG entry, and the docs.
Nothing here can leave `main` unreleasable at any point.

## Files touched

```
samtal-server/tests/unit/test_session.py     the two constructions and the new assertion
CHANGELOG.md                                 2026-08-15 entry under Fixed
docs/plans/2026-08-15-synthesis-test-signature.md
docs/plans/2026-08-15-synthesis-test-signature-implementation.md
```

`config.example.yaml` is untouched: no configuration key changes.
No README claim depends on this test's shape.

## Verification

- `uv run ruff check .`, `uv run pytest tests/unit -q`,
  `uv run pytest tests/integration -q`, all from `samtal-server/`.
- The wrong-reason check: the affected test run against a failing
  TTS, before and after the fix, with both outcomes recorded in the
  implementation doc.
- No other test file is touched; no existing assertion is weakened.
  The new `failures == []` assertion only adds.

## Risks and mitigations

- **The recording callback masks a latent ordering assumption.**
  `report_failure` is called from the drain task, not the test
  task; a recorded failure could in principle land after the test's
  final assertion reads the list. Mitigation: in this test both
  synthesis runs are fully awaited or cancelled-and-awaited before
  the assertions run (`_speak` awaits the sentinel; `cancel` is
  followed by an awaited cancellation), so the drain task is
  finished either way; the implementation states this in the test
  comment only if the code does not already make it obvious.
- **Scope creep toward a failure-path test here.** The issue's
  criterion 2 could be read as requiring an invoked-arm test in
  this file. Mitigation: the plan resolves the reading explicitly
  (the invoked arm is covered through the pipeline in
  test_tts_lookahead.py) and leaves the decision visible for the
  plan review to challenge.

## Plan review round

One external review of the plan as first committed (2a4f00f): codex
CLI, model gpt-5.6-sol, read-only against this repository with the
issue #135 body supplied, 2026-08-15. Verdict: ready after the P2
amendments. Findings as received, condensed; each carries its
resolution once the amendment addressing it lands.

1. **P2: the plan predicts the wrong failure behavior and the wrong
   "passing for the wrong reason" conclusion.** `chunks()` raises
   the stored provider failure, but `_speak`'s `finally`
   (pipeline.py:1098-1105) then awaits the drain task through
   `wait_cancelled()`, which suppresses only `CancelledError`, so
   the drain task's `TypeError` propagates and REPLACES the
   original provider exception; the plan claimed the TypeError
   stays unobserved and the provider failure reaches `_speak`. And
   the affected test exercises successful and cancelled synthesis,
   neither of which invokes the callback, so its spoken-count
   assertions are not false positives; the fixture violates the
   constructor contract without changing those verdicts. Say both
   correctly.
2. **P2: the cited lookahead test does not verify callback
   invocation.** `test_a_failing_sentence_still_lets_the_earlier_ones_be_heard`
   asserts propagation, spoken sentences, audio and cancellation,
   never the callback; it would pass if `_drain` stopped calling
   `_report_failure`. The committed invoked-arm coverage is in
   `tests/unit/test_session_events.py`
   (`test_a_failing_tts_provider_is_reported_as_the_tts` and the
   once-only assertion, ~500 and ~523). Cite that, and describe
   the lookahead test as covering ordered failure propagation and
   cleanup only.
3. **P3: the production caller passes an adapter lambda, not a
   bound method.** `speak_after` receives
   `lambda exc, elapsed: self._provider_failed("tts", tts, exc, elapsed)`
   (pipeline.py:1054). Describe it accurately.

## Milestones

- [ ] **Fix the two _Synthesis constructions and pin the callback's
  silence** (PR TBD): both constructions pass a shared recording
  callback with the spelled-out `(BaseException, float)` signature;
  the test asserts `failures == []`; the wrong-reason check is run
  both ways against a failing TTS and recorded in the
  implementation doc; CHANGELOG entry under Fixed, 2026-08-15; the
  implementation doc section written in the change that ticks this
  box. Accept: lint and both lanes green; no other test edited.
