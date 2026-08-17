# Take the transcript out of the echo guard's recovered sentence

Companion to
[`2026-08-17-asr-prompt-echo-transcript.md`](2026-08-17-asr-prompt-echo-transcript.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out.

## Milestone 1: the sentence narrows

The `asr_prompt_echo` `recovered` sentence stopped quoting what the
user said, its one pin moved with it, and a sentinel test in the
provider suite proves the absence on both retained surfaces.

### What landed

**`samtal_server/providers/openai_asr.py`.** One call, in
`_retry_without_prompt`'s success branch. The template becomes

```
"openai asr: the retry recovered %.2f s of audio "
"the echo guard would have discarded"
```

with `duration_s` as its only argument; `retry` is no longer passed.
The event name, the level (INFO, still the only one of the five
outcomes at INFO because it is the only one where the user was heard
after all) and the `_echo_fields("recovered", duration_s, retry_ms)`
payload are untouched. A comment above the call says why the transcript
is absent: naming that one exists would add no diagnostic the fields
lack, conversation-derived text is banned on the events without
exception by the content-and-telemetry ADR as amended 2026-08-17, and
what was said reaches the session on the next line and, when recording
is on, the conversation store. Nothing else in the module changed:
`return retry` still hands the recovered transcript back.

**`tests/unit/test_server_event_pins.py`.** One dictionary, inside
`test_asr_prompt_echo_recovered`: `template`, `args` (now `(1.0,)`) and
`sentence`. Its `fields`, `level` and `logger` are unchanged, as is its
docstring and the driver above it. No other pin in the file moved, and
`tests/unit/test_event_surface_pins.py` is byte-unchanged; the proof is
below.

**`tests/unit/test_providers_openai_asr.py`.** The sentinel, plus what
it needs.

- `RECOVERED = "sk-test-9d3a7b1c-never-a-real-credential"`, beside the
  file's existing error-body `SENTINEL` and deliberately a different
  value, so a hit says which path let it through: an error body the far
  side wrote, or a transcript a person spoke.
- `Tap`, a server-scope consumer keeping every `Emission` it is handed,
  with `saw(event)` and a `rendered()` that joins each emission's
  message, payload and arguments; and a `tap` fixture that attaches it
  with `attach_server_tap` and detaches it in a `finally`, since the
  hub outlives the test. Modeled on the server pin suite's `Consumer`,
  and carrying the same reason in its docstring: a clean `LogRecord`
  does not prove a clean consumer, because non-log taps are dispatched
  first and are handed the emission's own `args` tuple, whose members
  are deliberately not copied.
- `surfaces(record)`, which joins one record's every retained
  rendering: the unrendered template (`record.msg`), the sentence
  (`record.getMessage()`), the arguments, the structured fields (via
  `tests/support/events.py`'s `fields_of`, which reads `logs.py`'s own
  standard-attribute set) and both shipped log formats,
  `logging.Formatter(TEXT_FORMAT)` and `JsonFormatter`.
- `test_a_recovered_transcript_reaches_no_record_or_consumer`, in the
  `#69` retry section directly after the test that pins the recovery
  behaviour itself. The mock transport answers the first request with
  the prompt and the retry with `RECOVERED`. It asserts the
  transcription result still carries the text (the session is supposed
  to hear it), that two requests were sent, that every
  `asr_prompt_echo` record has `outcome == "recovered"` and holds the
  sentinel in none of its surfaces, that the tap saw the event and that
  the sentinel is absent from `Emission.message`, `Emission.args` and
  `Emission.payload` and from everything `rendered()` reaches, and
  finally that the diagnosis survives: `duration_s == 1.0` and
  `retry_ms >= 0`.
- Both halves of the sentinel assert they actually observed the event
  before asserting absence (`assert records, "the guard never fired, so
  this proves nothing"` and `assert consumed, "it reached no tap at
  all, so this proves nothing"`), which is the plan's requirement that
  it cannot pass vacuously.

Imports added to that file: `logging`, `collections.abc.Iterator`,
`Emission`/`attach_server_tap`/`detach_server_tap` from
`samtal_server.events`, `TEXT_FORMAT`/`JsonFormatter` from
`samtal_server.logs`, and `events as emitted`/`fields_of` from
`tests.support.events`.

**`CHANGELOG.md`.** Two edits under the existing `## 2026-08-17` /
`### Changed`. A new `**Breaking:**` bullet for #165 heads the section,
modeled on the 2026-08-16 narrowing entries: what the sentence now
says, what is unchanged (level, channel, field set), why the text was
never lawful there, and a **Migration:** clause pointing at the
conversation store keyed by the same session id. The existing
ADR-amendment bullet keeps its whole first half; only its final clause
changes, from the `asr_prompt_echo` violation being "scheduled for
removal by its own narrowing issue ahead of the #155 schema registry"
to its having been "removed by its own narrowing above (#165), ahead of
the #155 schema registry". That clause was made false by this change,
which is why the plan required both edits in one commit.

Nothing else was touched. `config.example.yaml` is unaffected (no
configuration key changes shape or meaning), and neither README
mentions the sentence.

### Blast radius, proved

`git diff` against the pre-change tree, restricted to the two pin
suites:

```
 samtal-server/tests/unit/test_server_event_pins.py | 6 +++---
 1 file changed, 3 insertions(+), 3 deletions(-)
```

`test_event_surface_pins.py` does not appear at all, which is the
byte-unchanged claim. The whole of the other file's diff is one hunk,
inside the one test:

```
@@ -1745,12 +1745,12 @@ async def test_asr_prompt_echo_recovered(caplog: pytest.LogCaptureFixture) -> No
         "logger": "samtal_server.providers.openai_asr",
         "level": logging.INFO,
         "template": (
-            'openai asr: the retry recovered "%s" from %.2f s of audio the echo guard would have '
+            "openai asr: the retry recovered %.2f s of audio the echo guard would have "
             "discarded"
         ),
-        "args": ("Yes, please.", 1.0),
+        "args": (1.0,),
         "sentence": (
-            'openai asr: the retry recovered "Yes, please." from <n> s of audio the '
+            "openai asr: the retry recovered <n> s of audio the "
             "echo guard would have discarded"
         ),
         "fields": {
```

The plan's risk (a second consumer of the wording) did not
materialize. After the change, the old template text survives in no
live code and no test. Run from the repository root, so the paths are
the ones written here:

```
$ grep -rn 'the retry recovered "' samtal-server/samtal_server samtal-server/tests; echo "exit=$?"
exit=1
```

Widened to every tracked file, with this record excluded, since the
document quotes the old template in the diff and the red-run excerpts
above and would otherwise match itself:

```
$ git grep -n 'the retry recovered "' -- . ':(exclude)docs/plans/2026-08-17-asr-prompt-echo-transcript-implementation.md'; echo "exit=$?"
exit=1
```

The exclusion is the whole of the difference: this file is the only
place in the repository the retired sentence still appears, which is
what a historical record is for.

### The sentinel, red then green

Checked against the old sentence before being trusted. With the
narrowed template temporarily replaced by the two-argument one (the
source file was then restored exactly, and `git diff` on it confirmed
empty), the sentinel fails on the log-record scan:

```
            assert record.outcome == "recovered"  # type: ignore[attr-defined]
>           assert RECOVERED not in surfaces(record)
E           assert 'sk-test-9d3...l-credential' not in 'openai asr:...etry_ms": 1}'
E
E             'sk-test-9d3a7b1c-never-a-real-credential' is contained here:
E               ecovered "sk-test-9d3a7b1c-never-a-real-credential" from 1.00 s of audio the echo guard would have discarded
E             ?           ++++++++++++++++++++++++++++++++++++++++
E               ('sk-test-9d3a7b1c-never-a-real-credential', 1.0)
E               {'event': 'asr_prompt_echo', 'outcome': 'recovered', 'duration_s': 1.0, 'host': 'api.openai.com', 'retry_ms': 1}
E               2026-08-17 02:15:00,651 INFO     samtal_server.providers.openai_asr: openai asr: the retry recovered "sk-test-9d3a7b1c-never-a-real-credential" from 1.00 s of audio the echo guard would ...
```

The three lines under the sentence are the arguments, the extracted
fields and the text-format rendering, so the failure names three of the
surfaces at once. It stops at the record scan, which runs before the
tap assertions; the tap surface would have caught it too, since the
transcript was a `%` argument and those reach a consumer as the object
itself. With the narrowed sentence back: `1 passed, 50 deselected`.

### Deviations from the plan

None. The sentence is the plan's exact wording, the pin edit is the one
dictionary it names, the sentinel covers both surfaces with the
observed-event assertions the plan's review round added, and the
changelog carries the `**Breaking:**` bullet beside the rewritten ADR
clause, in the same change.

### Verification

From `samtal-server/`, with `PYTHONDONTWRITEBYTECODE=1` exported for
everything run outside pytest:

- `uv run ruff check .`: **All checks passed!**
- `uv run pytest tests/unit -q`: **2272 passed, 16 skipped** in 297.92 s.
- `uv run pytest tests/integration -q`: **55 passed** in 158.09 s.
- Collected count, `uv run pytest tests/unit -q --collect-only | tail -1`:
  **2287 tests collected** before the change on this branch, **2288
  tests collected** after. Exactly the one sentinel test, as the plan
  requires.

The numbers above are the state at the PR's first push. The review
round below widens the sentinel's log scan and corrects two claims this
document and the changelog made; its own verification is recorded with
it.

### PR #166 review round

One external review of the pull request's diff, 2026-08-17. Three
findings, one P2 and two P3, each valid and each fixed in its own
commit. Findings as received, condensed, each with its resolution.

1. **P2: the no-leak sentinel ignores non-event log records.** The test
   filtered `caplog` to `asr_prompt_echo` before scanning, so an
   ordinary log line on the same path could retain the transcript with
   the sentinel still green. Suggested: keep the filtered records for
   the anti-vacuity and event-field assertions, and run the absence
   check over every captured record.
   *Resolution*: adopted in f6d37e0. The finding names a real gap
   rather than a theoretical one: `openai_asr.py` writes plain `logger`
   calls beside its events, and one of them (the retry announcement) is
   on the very branch that has the transcript in hand, so the filtered
   scan watched one line and blessed the file. `surfaces(record)` and
   the absence assertion now run over `caplog.records` whole, reporting
   `record.name` when they trip; the filtered list keeps the two jobs
   only it can do, the check that the guard fired at all and the
   event's own field assertions. The widened scan was checked by
   planting a `logger.info` carrying the transcript beside the event:
   it fails and names the channel, where the filtered scan passed. The
   probe was removed and `git diff` on the source confirmed empty.
   Nothing in the vendor libraries' own DEBUG output carries the
   sentinel, so the widening cost no exclusions. This also moves the
   scan above the event-field assertions, so the red-run excerpt quoted
   earlier in this document shows the ordering as it was at the time.
2. **P3: this record's whole-repository grep proof was false.** The
   claim was made from a command that ran in `samtal-server/`, so `.`
   was that subtree and never reached `docs/`, and the document itself
   quotes the old template three times.
   *Resolution*: adopted in 283c6d1. Both halves were wrong and both
   are corrected above: the proof is now a live-code-and-test search
   over `samtal-server/samtal_server` and `samtal-server/tests` with
   the paths written from the repository root, widened to every tracked
   file with this record excluded, and the exclusion is stated as what
   it is. Both commands were re-run against the tree they are recorded
   in and both exit 1.
3. **P3: the changelog overstated what the sentinel exercises.** The
   entry said the test asserts the transcript "reaches the session",
   where the test calls `OpenAiAsr.transcribe` and checks the
   `AsrResult` it answers with.
   *Resolution*: adopted in 52e3410, as a wording fix. The entry now
   says the transcript stays in the transcription result the provider
   answers with, and names the session as what goes on to hear it. The
   test is deliberately not extended through a session: the provider
   suite's subject is the provider, and the surfaces the entry is about
   are the ones it already covers.

### Verification after the review round

From `samtal-server/`, on the branch with all three fixes, with
`PYTHONDONTWRITEBYTECODE=1` exported outside pytest:

- `uv run ruff check .`: **All checks passed!**
- `uv run pytest tests/unit/test_providers_openai_asr.py -q`:
  **51 passed** in 1.26 s.
- `uv run pytest tests/unit -q`: **2272 passed, 16 skipped** in
  295.67 s.
- `uv run pytest tests/integration -q`: **55 passed** in 156.50 s.
- Collected count unchanged at **2288 tests collected**: the round
  widened an assertion rather than adding a test.
