# Deepen the event surface into one emitter with an explicit tap

Companion to
[`2026-08-15-event-surface.md`](2026-08-15-event-surface.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out.

## Milestone 1: the emitter moves and the tap exists

`samtal_server/events.py` now holds the `Emission` envelope, the
`EventTap` protocol, the log and capture taps, `SessionEvents` and
`ServerEvents`, and the hub a server-scope consumer attaches to.
`device/events.py` is gone. The twenty-six structured session emit
sites moved to the emitter's own `.debug/.info/.warning/.error` in one
change, and the surface they produce did not move at all: the pin
suite committed before the reshape passes through it without a byte
changing, and so does every event-assertion suite next door.

Four commits, in this order, which is also the order the milestone was
built in:

1. `357e334` Pin every session event before moving the emitter
2. `0ed98be` Give the event surface a home and a tap
3. `4763e9c` Emit through the moved emitter, everywhere at once
4. `fd26337` Pin the contract between an emitter and its taps

### The pin suite, first, and unchanged through the migration

`samtal-server/tests/unit/test_event_surface_pins.py` (new, 25 tests
covering 26 emit paths; `agent_said` and `handover` share a test
because one handover emits both, and two sentinel cases joined them in
the review round below). Per path it pins the channel, the numeric
level, the sentence, and the exact set of nonstandard record attributes
with their values. The nonstandard set is read through
`logs._STANDARD_ATTRIBUTES` rather than through a list written in the
test, so the suite and the JSON formatter cannot come to disagree about
what an event field is.

Two normalizations, both declared in the module docstring:

- **Per path**, the fields whose values move between runs (`session`
  always, plus `duration_s`, `duration_ms`, `first_token_ms`,
  `speaking_ms`, `delay_ms`, `revision`, and `speech_ms` on the one
  path that reads a real endpointer) are replaced by a placeholder.
  The key stays pinned; only that value does not.
- **In the sentence**, the session id and every numeric run become
  placeholders, because durations are rendered into the sentence too.

That second one was too generous to catch what this paragraph
originally claimed of it, which the PR #152 review round found
(finding 6, below). The suite now pins `record.msg`, the unrendered
template, and `record.args` by value and by type, declaring the
argument positions that move rather than blurring every number; the
rendered sentence is kept as the readable half. What follows describes
the suite as it stands after that commit.

Every one of the twenty-six paths is driven; none had to be
approximated. Most reuse the fixtures the existing session suites
already use (`session_for`, `run_reply`, `masked_session`,
`realtime_session`, `ScriptedEndpointer`, `GatedAsr`, `ConfirmingAsr`,
`StallingLlm`, `Unreachable`). Two things were built for it:

- The four barge-in gates are driven through `_finish_utterance`
  directly rather than over a websocket, so `speech_ms` is the
  scripted endpointer's exact number instead of a range. The existing
  barge-in suite asserts `180 <= speech_ms <= 300`; a pin needs one
  value.
- The three rejections happen before a runtime exists, so they are
  driven through `DeviceSession.run()` against a socket that answers
  only `headers`, `accept` and `close`, with a bindings stub for the
  two that turn on a resolution. `agent_not_loaded` otherwise needs a
  real database (`test_device_bindings.py` boots one), and the
  resolution is not the emit path under test.

The suite was committed green against the unrefactored code:
`uv run pytest tests/unit -q` reported `1938 passed, 15 skipped` at
`357e334`, with the 25 new tests among them. After the migration
(`4763e9c`) the same command reports the same `1938 passed, 15
skipped`, and `git show --stat` for that commit lists no test file
except `test_boundary_contract.py`, whose one changed line is an
import.

### What landed in `events.py`

406 lines. The module docstring names the two invariants that shape
the dispatch (the capture ordering, and per-tap isolation), says the
tap contract is events only and why `vad`/`dropped` sit outside it,
and explains the clock split, citing #138 and the observability ADR.

- `Emission`: frozen dataclass carrying `payload`, `at`, `level`,
  `message`, `args`. One envelope, so `LogTap` can reproduce today's
  record and a consumer that wants only the structure reads one field.
- `EventTap`: a `Protocol` with `emit(emission)`.
- `LogTap`: `channel.log(level, message, *args, extra=payload)`, which
  is byte-for-byte what the call sites did by hand.
- `CaptureTap`: calls the capture's existing `event(payload, at)`. A
  wrapper rather than the capture itself, because `SessionCapture` is
  a recording and a tap is a consumer.
- `session_clock()`: the running loop's clock, falling back to
  `time.monotonic` where no loop runs (see the deviations).
- `_dispatch(taps, emission, channel)`: one loop, one guard per tap,
  one plain non-event warning naming the tap's class and the
  exception's class when a tap raises.
- `SessionEvents`: `.debug/.info/.warning/.error(msg, *args, event=,
  **fields)`, building `event`, `session`, `device`, then the fields,
  in that order; generic `attach`/`detach` with `attach_capture`/
  `detach_capture` as the capture's named wrappers; `vad()` and
  `dropped()` unchanged in behavior. `SESSION_LOGGER` and the module
  `logger` moved with it.
- `_ServerHub`, `attach_server_tap`, `detach_server_tap`,
  `server_emitters`, and `ServerEvents(channel, clock=time.monotonic)`
  with the same four levels and no session or device defaults. The
  class lands here so milestone 2 has something to migrate onto;
  nothing is migrated onto it in this milestone, and nothing attaches
  to the hub.

### The migration

`runtime/pipeline.py` (18 sites), `device/session.py` (8 sites, its
private `_event` helper deleted), `device/boundary.py` (the
`RuntimeFactory` type's import), and `tests/unit/test_boundary_contract.py`
(one import). `device/events.py` deleted. Both module docstrings had a
paragraph describing the events as "structured `extra=` fields"; both
now describe them as the structured fields the JSON format emits, sent
through the session's emitter, and point at `events.py`.

The diff is rename-shaped by construction: each site's `logger.X(msg,
args, extra=self._events.event("name", **fields))` became
`self._events.X(msg, args, event="name", **fields)` with the message,
its arguments, the level and the fields untouched, including the two
`**self._speaking_ms_field()` splats and the `**provider_fields(...)`
ones.

### The contract test

`samtal-server/tests/unit/test_events.py` (new, 337 lines, 12 tests).
Attach, fan-out and detach with the log unaffected either way; the
finished payload's key order; the unrendered message and args on the
`Emission`.

Ordering is measured rather than asserted about the source: the
capture-shaped spy records `len(caplog.records)` at the moment it is
told, and the test asserts that number is zero and that one record
exists afterwards, then asserts the payload the spy received is the
same object the record carries.

Isolation: a raising tap is called again on the next event (a consumer
that fails once is not a consumer that is gone), the taps after it and
the log still see both events, and the failure report is found by
being the one record with no `event` attribute at all, on the
emitter's own channel, at WARNING, naming both class names.

The hub: a tap attached before an emitter exists sees that emitter's
events, the emitter is in `server_emitters()`, the payload carries no
session or device, and a detach stops it. A separate case pins that a
broken server tap reports on the emitting subsystem's channel rather
than on the session one.

The clocks: a server event emits inside a synchronous test, which
first asserts there is no running loop to be had; a session event
inside an async test carries a reading within 50 ms of
`asyncio.get_running_loop().time()`; and both emitters take an
injected clock that is honored exactly, which is what makes the split
a stated dependency.

### Deviations from the plan

Five, none of them a departure from a decision.

**The pipeline has eighteen emit sites, not seventeen.** The plan's
milestone description says "pipeline.py (17 sites), device/session.py
(8 sites)" while the review round's finding 9, which produced those
numbers, says twenty-six production sites in total. Twenty-six is
right and the split was not: `grep -c "self._events.event("
runtime/pipeline.py` is 18, and 18 + 8 = 26. Nothing about the work
changes; the milestone's own arithmetic does.

**The session clock resolves the loop per emit rather than at
construction.** The plan says `SessionEvents` "binds the session
loop's clock at construction". Taken literally that breaks two
existing tests: `test_boundary.py::test_a_device_session_is_a_device_output`
and `test_session.py::test_a_session_refuses_an_agent_its_device_is_not_bound_to`
are synchronous and build a session, whose agent activation emits
`prompt_assembled` with no loop anywhere. Today that works because
`event()` only reads the clock when a capture is attached, and the
`Emission` always carries one. So the *dependency* is bound at
construction (`SessionEvents(session_id, clock=session_clock)`, and
the contract test pins that an injected clock is honored), while the
default callable resolves the running loop at emit time and falls back
to `time.monotonic` when there is none. The fallback is only ever
reached where no capture exists to compare the reading against, which
is what the function's docstring says.

**`SessionEvents.log` goes but the module `logger` stays.** The plan
makes deleting the handle conditional on nothing still using it. The
class attribute is gone; the module-level `logger` on
`samtal_server.session` is still imported by `pipeline.py` and
`session.py` for their plain sentences, which are not events (the
"nothing transcribed" line, the "utterance of %.1f s" line, the
framing warnings) and which the tap contract deliberately does not
cover.

**A record's `pathname`, `lineno` and `funcName` now name
`events.py`.** The logging call happens inside `LogTap`, so the
standard attributes that record the call site point here rather than
at `pipeline.py:662`. None of the three is output: `logs.py` emits
only `ts`, `level`, `logger`, `message` and the nonstandard
attributes, and the text format names none of them. It is visible in
pytest's captured-log display, which is where it will be noticed.
`stacklevel=` would fix it but the correct depth depends on the guard
wrapping, so it would be a fragile constant for something nothing
reads.

**Two prose mentions of `device/events.py` survive.** The milestone's
acceptance is written as `grep -rn "device.events" samtal_server tests`
being empty. The import grep is empty; the literal one finds three
lines, all deliberate: the new module's docstring and the pin suite's
say where the emitter used to live, which is why both exist, and
`tests/integration/test_mcp_reload.py:187` reads `device.events` off a
test double's attribute, which has nothing to do with this module.

### Discoveries

**A characterization pin has to choose what "the sentence" means.**
Pinning `getMessage()` verbatim pins the durations rendered into it,
which move every run. Normalizing every numeric run is the honest
alternative and it keeps what matters (the words, the argument
positions, the substituted types), but it also normalizes the client
uuid in `session_open` into something unreadable, which is why that
one literal is built by running the normalizer over `DEVICE_UUID`
instead of being typed out.

**The three rejection paths were never driven below the websocket
before.** `session_rejected` is covered today through a real app
(`test_session_events.py`) or a real database
(`test_device_bindings.py`). A socket answering three methods and a
bindings stub is enough for all three, which is worth knowing for
anything that has to reach them cheaply again.

**Ordering is testable without instrumenting the emitter.** The
capture-shaped spy reading `len(caplog.records)` while it is being
told turns "the capture is offered the payload before the record
exists" into a measurement. It works because pytest's caplog handler
is on the root logger and appends synchronously.

**Nothing outside the session scope emits through the new module
yet.** `ServerEvents` and the hub are exercised only by the contract
test in this milestone, which is the plan's intent: milestone 2 is
where the hand-built `extra=` sites migrate onto them.

### Verification

From `samtal-server/`, at `fd26337`:

```
uv run ruff check .                 All checks passed!
uv run pytest tests/unit -q         1950 passed, 15 skipped
uv run pytest tests/integration -q  53 passed
```

The unit count is 1938 before the contract test and 1950 with its
twelve, and the same 1938 ran green at `357e334` (pin suite, no
production change) and at `4763e9c` (migration complete). The
event-assertion suites named in the plan's verification section
(`test_session_events.py`, `test_session_filler.py`,
`test_session_barge_in.py`, `test_capture_session.py`) pass with no
diff: `git diff --stat main -- samtal-server/tests` lists only
`test_boundary_contract.py`, plus the two new files.

```
grep -rn "from samtal_server.device.events" samtal-server/samtal_server samtal-server/tests
                                                                        (no matches)
```

`git diff --stat main` touches only this milestone's files: the plan
and this document, `samtal_server/events.py` (new),
`samtal_server/device/events.py` (deleted),
`samtal_server/device/{boundary,session}.py`,
`samtal_server/runtime/pipeline.py`, and the three test files.
Milestones 2 and 3 own everything else and none of it moved.

### PR #152 review round

One external review of the milestone as first pushed. Six findings,
four P1 and two P2; verdict mergeable after fixes. All six adopted, one
commit each, applied in an order the findings themselves imply: the
pin-suite strengthening first, against unchanged behavior, so that the
two commits which deliberately change a pinned sentence are read
against a suite that actually pins one.

1. **P1: a rejected Device-Id is written to the log.** The
   bad-Device-Id rejection logged the validation exception, whose
   message quotes the header verbatim, so an unauthenticated caller
   could put a value of their choosing into the retained log surface,
   one line per connection attempt. The pin suite had recorded the leak
   as correct by pinning `"not-a-mac"` in the output.
   *Resolution*: adopted in `7e4ab42`. The sentence is fixed text
   carrying neither the header nor the exception; the reason token,
   the null `device`, and the guidance about what the header must hold
   all survive. This is a deliberate change to a pinned sentence, so
   the pin moved with it, and a sentinel case asserts a
   credential-shaped Device-Id reaches no sentence, no argument, no
   field, and no record at any level.
2. **P1: a provider's own words reach the log and every tap.**
   `_provider_failed` rendered `%s: %s` with the exception itself. The
   five real providers raise the metadata-only taxonomy after #137, but
   this method takes a `BaseException` from four call sites, one of them
   the LLM stream, so an SDK's or a transport's exception still arrives
   unwrapped and an exception raised near a response body can carry a
   body fragment in its message.
   *Resolution*: adopted in `bb96f22`. Only the class name is rendered,
   which is what the `error` field already carried and what `_reply`'s
   own catch has printed since #137. The second deliberate sentence
   change, so the pin moved with it, and its sentinel plants a value in
   the exception and checks the record and an attached consumer. That
   last check needed a driver that attaches a tap, so the pin test and
   the sentinel share one instead of borrowing `reply_with` from the
   neighbouring suite.
3. **P1: a mutating tap can corrupt or inject into the retained log.**
   `Emission` is frozen, which stops a tap rebinding a field and
   nothing else; the dict behind `payload` was shared with every
   consumer, so a tap could rewrite a nested value or add a key
   `logging` reserves before the log tap ran.
   *Resolution*: adopted in `824e738`. Non-log taps are handed a deep
   copy and the log the payload the emitter built. Deep rather than
   shallow because `prompt_assembled` already carries a nested dict;
   `args` are left alone, because they are rendered by `%` and never
   written back and copying an arbitrary argument is a copy that can
   fail. The contract test's vandal edits both levels and the reserved
   key, and fails without the fix: the record it looks for does not
   exist, because the logging call raised and the guard turned it into
   a one-line report.
4. **P1: `events.py` imports `capture.py`, which the plan forbids.**
   Three type annotations pointed an arrow back at a module that is
   about to point one here, since milestone 2 migrates `capture.py`
   onto `ServerEvents`; the pair would then import each other and the
   symptom at boot is a partially initialized module.
   *Resolution*: adopted in `9ac909c`. A local `SessionRecording`
   protocol describes the three methods this module calls. A capture
   reaches the emitter as an object, which is the point of the tap, so
   the shape was all that was ever needed. The contract test's spy now
   satisfies the protocol outright and dropped its two type ignores.
5. **P2: reattaching a capture leaves the previous one attached.**
   `attach_capture` overwrote the handle and appended a second adapter,
   so the first kept writing a decision track for a recording nobody
   would close, while `vad` and `dropped` followed the handle to the
   second.
   *Resolution*: adopted in `1869c47`. A second attach detaches the
   first. Replacing rather than refusing, because a capture rolling
   over at its size limit is a legitimate second attach and a refusal
   would make the sequencing a caller's problem. Nothing in the tree
   attaches twice today; the contract test does.
6. **P2: the pin suite does not pin the sentences it claims to.** Every
   numeric run in the rendered sentence became a placeholder, so a
   swapped argument, a changed value, and a `%d` turning into a `%s`
   all passed.
   *Resolution*: adopted in `0b9f252`, and first, so the two sentence
   changes above landed against a suite that pins one. The pin is now
   `record.msg`, the unrendered template, plus `record.args` by value
   and by type; a position whose value moves between runs is declared
   in `dynamic_args=` and keeps its type, so a duration that stopped
   being a float is a failure. Argument 0 is the session id in all
   twenty-six sentences and is normalized without being declared. The
   rendered sentence stays as the readable half, and the docstring now
   says which of the two is the pin. The claim in this document's
   pin-suite section is true as of this commit and was not before it.

Two surfaces changed for operators, both narrowing what a log line
says: `session_rejected`'s bad-Device-Id sentence and
`provider_failed`'s. Neither event name, level, channel nor field
changed, so a collector's queries are unaffected; only the human half
of two lines is shorter, and in both cases the part that went is the
part nobody wrote.

Verification after the six, from `samtal-server/` at `1869c47`:

```
uv run ruff check .                 All checks passed!
uv run pytest tests/unit -q         1954 passed, 15 skipped
uv run pytest tests/integration -q  53 passed
```

The unit count grows by four: two sentinel cases in the pin suite, and
the mutating-tap and reattach cases in the contract test. No file
outside milestone 1's list moved.

## Milestone 2: the server scope emits through it

Every hand-built structured `extra={...}` dict in the production package
is gone. Eleven modules now emit through a `ServerEvents` built on the
module logger name each of them already had, `openai_asr.py`'s private
`_echo_event` builder is dissolved, and the surface they produce did not
move: the pin suite committed before the migration passes through it
without a byte changing, and so does every event-assertion suite next
door.

Six commits, in the order the milestone was built:

1. `447c64a` Pin every server event before migrating it
2. `7be52b0` Emit the HTTP edge's events through ServerEvents
3. `903ccf3` Emit the server's own plumbing through ServerEvents
4. `d92d527` Dissolve _echo_event into the provider's emitter
5. `de8e89a` Guard the one emitter with the package's own AST
6. `0e66e29` Pin the templates and the arguments here too

The branch was stacked on milestone 1's while that was under review, so
these hashes moved with every rebase onto it and were refreshed once
more when milestone 1 merged as PR #152 and this branch was rebased onto
`main`. Their order and their titles never moved.

### The pin suite, first and unchanged

`samtal-server/tests/unit/test_server_event_pins.py` (new, 1742 lines,
50 tests: 42 emit paths, one each, plus the eight sentinel cases the PR
#153 review round added below). A sibling module rather than
an extension of `test_event_surface_pins.py`, because the two scopes
normalize differently and the session file must not be touched again:
milestone 1's evidence is that it did not move.

Per path it pins the same five things as the session suite:
`record.name`, `record.levelno`, `record.msg` (the unrendered
template), `record.args` (by value and by type), and the exact set of
nonstandard record attributes with their values, read through
`logs._STANDARD_ATTRIBUTES` rather than through a list written in the
test. The rendered sentence is carried alongside as the readable half a
reviewer reads in a diff, and the docstring says which of the two is the
pin and which is the courtesy.

Two normalizations, both declared in the module docstring:

- **Per path**, the fields whose values move between runs are named in
  `dynamic=` and replaced by a placeholder. There are seven of them
  across the whole suite: an activation `code`, a capture's `path` and
  `free_mb` and `total_mb`, the bindings failure's class name, the ASR
  retry's `retry_ms`, and the capacity refusal's `session`.
- **In the sentence**, strings named in `scrub=` (a `tmp_path`, an
  activation code) are replaced first, and then every numeric run
  becomes `<n>`. Scrubbing runs first because a temporary path is full
  of digits.

Unlike the session scope, `session` is pinned rather than normalized:
these paths are driven directly enough to name the session themselves
(`"s1"`), and a server event carries no session identity of its own.

The suite was committed green against the unmigrated code, and the
migration commits did not touch it: the only test file any of them
carries is the new guard.

It was touched once afterwards, deliberately, in `0e66e29`. The PR #152
review round landed on the session suite while this milestone was being
built, and its finding 6 applies here word for word: normalizing every
numeric run in the rendered sentence lets a swapped argument, a changed
value and a `%d` turning into a `%s` pass unnoticed. So `record.msg` and
`record.args` joined the pin, with sixteen argument positions declared
in `dynamic_args=` (a `tmp_path`, a duration, or one of five exceptions,
which can only be pinned by class since two exceptions carrying the same
message are not equal to each other).

Adding pins after a migration would ordinarily weaken the evidence they
exist to be. It does not here, because the strengthened suite was run
against the pre-migration tree as well: restoring `samtal_server/` at
`447c64a`, all forty-two pass, and they pass again with the migrated
tree back. Both halves of the "before and after" therefore hold for all
five dimensions, not only for the original four.

### Every path was driven, six of them with a planted failure

None had to be approximated. Most reuse the fixtures the existing suites
use (`test_ota.py`'s `post_system_info`, `test_onboarding_activation.py`'s
`check_in` and `activate`, `test_capture.py`'s `store` and `tone`,
`test_drain.py`'s `FakeSession`, `test_device_bindings.py`'s `booted`,
`test_ws_auth.py`'s `handshake`), which is what keeps a 42-path suite to
one file.

Six paths are reached only by a failure that cannot be provoked
portably, and each plants one instead, which is a decision rather than a
convenience:

- The three `capture_declined` reasons and `capture_failed` render an
  exception into their sentence. An unwritable volume answers with the
  operating system's own words, which differ between platforms, so
  `_free_mb` and `SessionCapture.start` are monkeypatched to raise a
  fixed `OSError`. `capture_failed` is the exception: it is driven for
  real, by closing the WAV handle under a live capture and writing past
  the flush lag, and it pins CPython's own `write to closed file`.
- `activation_not_offered` with reason `unreadable` needs a database
  that will not read, which `test_device_bindings.py`'s recipe supplies
  (boot an app on a real database, then overwrite the file); the same
  drive covers `device_bindings_unreadable`.
- `activation_not_offered` with the budget reason lowers `MINT_BUDGET`
  to zero rather than making thirty check-ins. What is under test is the
  line, not the counter, which `test_onboarding_activation.py` drives
  for real.

Two other choices worth naming. The onboarding banner and the two key
misses are driven with a **pinned** onboarding key rather than one
derived from a secret in the environment, so the URL in the sentence is
a literal instead of something recomputed by the code under test;
pinning is a supported configuration, for a secret rotation. And
`capture_enabled`/`capture_disabled` name a fixed absolute directory
rather than a `tmp_path`, because `CaptureStore` creates nothing until a
session opens and none opens in those tests.

### The migration

Eleven production modules, 42 emit sites, split into three commits by
subsystem rather than landed as one. Milestone 1's migration had to be
atomic (the payload factory it replaced could not survive a partial
move); this one does not, and three diffs of four, six and one file each
are three readable stories.

The diff at each site is mechanical, exactly as the plan says:

```python
logger.warning(msg, args, extra={"event": "capture_declined", "session": session_id, ...})
events.warning(msg, args, event="capture_declined", session=session_id, ...)
```

Two sites carried a dict across several calls and still do, minus the
`event` key the emitter now owns: `ota_check`'s common fields are
splatted into all four branches (`**fields`), and the three activation
refusals splat the pair they share. Field order is preserved at every
site, because `ServerEvents` builds `{"event": ..., **fields}` and
`event` was the first key of every hand-built dict.

The module `logger` goes from nine of the eleven modules, where every
logging call was an event. It stays in two: `filler.py` logs that a
filler was cached, and `openai_asr.py` logs the under-the-minimum debug
line and the one saying a retry is being sent. All three narrate
progress rather than record an outcome, which is the line milestone 1
drew for `pipeline.py` and `session.py`.

`_echo_event` became `_echo_fields`. The event's name went back to the
five call sites, beside the sentences it belongs to; what survives is
the three fields the outcomes share and the rule that `retry_ms` is
absent on a skip.

### The AST guard

`samtal-server/tests/unit/test_event_surface_guard.py` (new, 151 lines,
5 tests). The rule walks every module of the production package and
fails on a call whose function is an attribute with a logging method's
name and which carries an `extra=` keyword. One exception is enumerated
with its reason: `events.py`, where `LogTap` attaches the finished
payload, which is the concentration the rule exists to produce. Checked
by hand at the time of writing, `events.py:142` is the only hit in the
package, which is `LogTap.emit`.

Two tests keep the rule honest rather than merely green. One plants the
three shapes that evaded the grep this replaces (a one-line `extra={`, a
multi-line one, and `extra={**record, ...}`) and asserts all three are
caught. The other plants pydantic's `ConfigDict(extra="forbid")`,
`json_schema_extra=` and `openapi_extra=` and asserts none is, which is
why the rule is written for logging calls rather than for every call
with an `extra=` keyword.

The last two tests assert the absences: `_echo_event` appears in no
production module, and nothing imports or names `device.events`
(milestone 1's deviation about the two prose mentions does not apply
here, since both spell it `device/events.py`).

### Deviations from the plan

Three, none of them a departure from a decision.

**The import cycle the plan ruled out was real, and milestone 1 fixed
it.** The plan says `capture.py` importing `events.py` "is not a cycle
... because `events.py` imports nothing from `capture.py`". Milestone 1
shipped it importing `SessionCapture` for the annotations on
`CaptureTap` and `attach_capture`, which is harmless while nothing else
emits and is a genuine cycle the moment `capture.py` builds a
`ServerEvents`: importing the capture starts `events.py`, which imports
the capture back before `SessionCapture` exists. This milestone hit it,
fixed it here first behind `TYPE_CHECKING`, and then dropped that commit
in a rebase: milestone 1's own review round landed a better answer on
the parent branch, a structural `SessionRecording` protocol describing
the three methods this module uses, which is a shape rather than a
deferred name. Nothing of the cycle is left for this milestone to
carry.

**There are 42 server emit sites, not 23.** The plan's inventory counts
"ota 8, onboarding 4, capture 8, registry 3, ws 2, app 2, bindings 2,
filler 1, memory 1, config/api 2". Those are `extra={` counts, and two
things escape them: `ota.py`'s four `ota_check` branches attach a
prebuilt dict as `extra=event`, so ota has 12 sites rather than 8; and
`openai_asr.py`'s five `_echo_event` sites are named in the plan's prose
but absent from its arithmetic. 12 + 4 + 8 + 2 + 2 + 3 + 1 + 2 + 1 + 2 +
5 = 42, which is also the number of tests in the pin suite. Nothing
about the work changes; the milestone's own arithmetic does.

**The `ota_check` sentence carries the header, not the normalized MAC.**
Not a change, a discovery worth writing down, because the pin makes it
permanent: the four `ota_check` sentences interpolate `device_id`, the
raw `Device-Id` header, while the `device` field carries
`normalize_mac`'s answer. A board sending `AA:BB:CC:DD:EE:FF` therefore
produces a sentence in upper case and a field in lower. The pin fixes
both spellings, so a future tidy-up of one of them is a deliberate act.

### Discoveries

**A server event's sentence is where the platform leaks in.** Four of
the six planted failures exist because an exception's `str()` is
rendered into the sentence, and four of the events that carry a path
render an absolute one. The session scope has almost none of this: its
sentences are about a conversation. It is the reason this suite needed a
`scrub=` the session suite did not.

**The hub is now populated, and nothing is attached to it.** Eleven
`ServerEvents` are constructed at import time, so `server_emitters()`
answers with eleven channels rather than the empty tuple it answered
with at the end of milestone 1. That is the attachment point #66/#67
were promised, and it now has something to reach.

**A stacked branch pays for the review round above it.** Milestone 1's
PR was reviewed while this was being built, and six commits landed on
the parent branch during it, two of them in `events.py` itself. One of
those overlapped this milestone's own work closely enough that a rebase
resolved it wrongly and had to be redone by dropping the commit
outright. The lesson for the next link of the chain is to diff against
the parent branch after every rebase and read what moved, not only what
merged.

**`_STANDARD_ATTRIBUTES` is what makes a 42-path pin affordable.**
Reading the field set through `logs.py` rather than listing it means a
path's pin is written by naming what moves, not by naming what exists.
Sixteen of the 42 name a `dynamic=` field, sixteen a `dynamic_args=`
position and five a `scrub=` string; the rest pin every value they
carry, verbatim.

### Verification

From `samtal-server/`, at `0e66e29`:

```
uv run ruff check .                 All checks passed!
uv run pytest tests/unit -q         2001 passed, 15 skipped
uv run pytest tests/integration -q  53 passed
```

This milestone adds 47 unit tests and no integration test: the 42 of the
pin suite, all green before any production line moved, and the guard's
five. The migration commits changed no test file, so the count at
`447c64a` is this one less the guard's five, and `main`'s is that less
42. The review round below adds eight more and its own run.

`git diff --stat main` lists this milestone's files and nothing else:
the eleven migrated modules, the two new test files, the plan and this
document. `events.py` is not among them, which is the point of the
deviation above. Milestone 3 owns `tools/mcp.py`, the README table and
the CHANGELOG, and none of them moved.

### PR #153 review round

One external review of the milestone as first pushed. Four findings,
three P1 and one P3; verdict mergeable after fixes. All four adopted,
one commit each.

Three of the four are the same shape, and it is a shape this milestone
invited. A byte-compatible migration faithfully preserves what it moves,
and these events were carrying things nobody would put in a log if they
were writing the line today; the pins then wrote each of them down as
correct. So the review's real finding is that a characterization suite
records a leak as precisely as it records anything else, and that
migrating a surface is the moment to read what is on it. Each fix is
therefore a deliberate narrowing, made in the same commit as the pin
that has to move with it, and none of them touches an event's name, its
level, its channel, or a field that is not the leak.

1. **P1: the onboarding events expose the derived route key and the
   keys that missed.** The startup banner emitted the complete short
   URL, key and all, in its sentence, its arguments and a `url` field;
   the key miss emitted the rejected attempt beside this server's own
   key. The key is the path segment standing in front of the endpoint
   that issues device tokens, and both lines are retained records.
   *Resolution*: adopted in `816f4d8`. The banner names the origin, its
   source, whether onboarding is on, and whether a key stands in front
   of the short route at all, and points at `samtal-server config
   ota-url`; the miss reports the length of what arrived, with the
   event's own name saying which kind of miss it was. The operator
   workflow is intact because that command was already the documented
   route to the URL: it derives the same string from the same file and
   the same secret, contacts nothing, and prints to a terminal rather
   than to a log. It is now the only route, which the README says. The
   attempted key goes with the expected one, because a near miss of a
   real key is a hint at the real key.
2. **P1: an unauthenticated client controls `auth_rejected.device` and
   its sentence.** The refusal read the Device-Id header before anything
   had authenticated. The endpoint is reachable by whatever finds it and
   a refusal costs the caller one socket open, so the retry loop was
   theirs: a value of their choosing, one record per attempt.
   *Resolution*: adopted in `57e0c9b`. A fixed sentence naming an
   unidentified client, and `device=None`. The header is read only after
   the refusal check, which makes the rule structural rather than a
   comment. The capacity refusal on the same handler keeps its device
   and is not an inconsistency: capacity is checked after the token.
3. **P1: capture and filler failures broadcast unsanitized exception
   objects.** Four sites passed the exception itself as a `%` argument.
   The catches are broad, around a write or around a whole synthesis, so
   the messages carry paths, phrases, bytes and response fragments; and
   `Emission.args` is deliberately not copied for a tap, so a consumer
   was handed the live exception and its chain before the log tap turned
   anything into text.
   *Resolution*: adopted in `249a480`. Only `type(exc).__name__` is
   rendered, and the three capture events gain a `failure` field
   carrying it, as `device_bindings_unreadable` has since it was
   written. A vandal tap in the sentinel cases proves the second half:
   with the fix reverted it rewrites the exception and the log renders
   "injected by a tap".
4. **P3: the milestone 2 record is stale after the rebase.** The six
   commit ids predated milestone 1's merge, the pin suite's line count
   predated its own strengthening, and `events.py`'s one `extra=` call
   had moved.
   *Resolution*: adopted in `5fbc9d7`. All three refreshed against the
   branch as it stands, plus the verification block's unit count and the
   diff it names, both of which were against the old parent branch.

Eight tests joined the pin suite, in the commits that changed what they
watch: a planted key hunted on the banner and both keys hunted on a
miss, a credential-shaped Device-Id on a no-token and a bad-token
handshake, and an exception whose message and whose chained cause both
carry a sentinel, planted at the capture's write failure, the capture's
decline, and the filler's. Each asserts absence in five places rather
than one: the sentence, the arguments, the fields, every record in both
shipped formats, and an attached server tap. That last one is what a
pin cannot reach, and it is where the #66/#67 exporters will be.

The two suites next door that had recorded the old behavior moved with
the fixes rather than being worked around:
`test_onboarding_banner.py` now pins the origin and its provenance and
asserts the key appears in no line at all, and `test_onboarding.py`'s
two miss tests assert both keys absent from both shipped formats.
`test_config_cli_onboarding.py` no longer compares the banner's URL with
the command's, because there is no longer a banner URL to compare; it
asserts instead that the command prints it, the banner does not, and the
two still agree about the origin.

Verification from `samtal-server/`, at `5fbc9d7`:

```
uv run ruff check .                 All checks passed!
uv run pytest tests/unit -q         2009 passed, 15 skipped
uv run pytest tests/integration -q  53 passed
```

The eight new tests are the whole of the growth from 2001. No test was
deleted and none was weakened: the three suites above assert more after
the change than before it, because "this value is not here" is a
stronger claim than "this value is here".

## Milestone 3: the MCP lifecycle speaks

`tools/mcp.py` builds a `ServerEvents` on the channel it already logged
on and emits the plan's five events from eight sites. Five of them are
sentences that were already there and kept their wording; three are new
lines. The reason tokens are two closed sets, both chosen where a
failure is classified and neither ever built from an exception's
message, and `_run` grew the phase tracking that makes the first of them
honest.

Four commits, in the order the milestone was built, plus the six the
review round below added:

1. `54759e6` Give the MCP lifecycle its five events
2. `cb4a9de` Assert each MCP event through a running manager
3. `69adb5c` Write the five MCP events into the surface
4. `2caa96e` Record milestone 3 and tick it in the plan

Milestone 2's own review round was landing on the parent branch while
this was built, which is the lesson that milestone recorded: diff
against the parent after every rebase and read what moved. Three of its
commits arrived mid-milestone, each narrowing a record that quoted
something it should not, and all three are covered by the changelog
entry this milestone carries, because that entry is the issue's rather
than this milestone's. Milestone 2 has since merged, so this branch is
stacked on `main` rather than on it.

### Every sentence, and what became of it

The plan asks for both columns, so here are all thirteen `logger.*`
calls the module had, plus the three sites that had none. The rule
applied is the one milestones 1 and 2 drew: a line that records an
outcome becomes an event, a line that narrates progress stays prose.

| site | sentence, in short | became |
| --- | --- | --- |
| `_run`, after publication | "connected with N tool(s)" | `mcp_connected` |
| `_run`, the failure handler | "is unavailable, its tools are absent" | `mcp_down` |
| `_run`, the `finally` | (new line) | `mcp_down`, reason `stopped` |
| `_mark_down` | (new line) | `mcp_call_dropped` |
| `_mark_down` | "dropping the connection after a failed call" | `mcp_down`, reason `call_failed` |
| `McpServers._reachable` | "dropping published tool N" | `mcp_tool_shadowed` |
| `McpServers._apply` | "mcp servers reloaded: ..." | `mcp_reload`, outcome `applied` |
| `McpServers._refused` | (new line) | `mcp_reload`, outcome `refused` |
| `_warn_about_unpublished` | "allowed by a grant but not published" | prose: a mismatch between two configurations, not a lifecycle transition, and nothing about the connection changed when it was said |
| `stop`, past the bound | "did not close within N s; cancelling it" | prose: the stop is still in progress, and its outcome is the `mcp_down` above |
| `stop`, past the cancel bound | "has not finished unwinding" | prose, for the same reason |
| `ensure_reconnecting` | "reconnecting in the background" | prose: an attempt beginning, whose outcome is `mcp_connected` or `mcp_down` |
| `_capture` | "its own configured values could not be resolved" | prose: the optional half failed and the connection stands, so no lifecycle field would be true of it |
| `_too_long` | "the block it shipped is N characters" | prose: guidance, not lifecycle |
| `_skipped` | "nothing is injected for position N" | prose, for the same reason |
| `_announce_shipped` | "shipped guidance: N characters" | prose, for the same reason |

Eight prose lines and eight emit sites. No site does both: a line that
became an event is one call, and the two calls in `_mark_down` are two
events rather than an event and a sentence, which is the pairing the
plan makes contract.

### The phase marker, and the two type rules above it

`_run` puts three quite different failures behind one handler, and by
the time an exception arrives there they are indistinguishable: any of
them can be the same wrapped `ExceptionGroup`. So a local `phase`
starts at `transport_failed`, `_connect` advances it to
`initialize_failed` once the transport's streams are in hand, and `_run`
advances it to `discovery_failed` after the handshake returns. A local
with a `reached` closure passed into `_connect`, rather than a field on
the manager: it means nothing between two runs, and `_connect` is the
only code that can see the boundary it marks.

Two type rules sit above the marker, both recursing into exception
groups (`_carries`) and neither reading a message:

- a `TimeoutError` anywhere inside is `connect_timeout`, whichever call
  was outstanding when the envelope's bound expired;
- an `httpx.TransportError` anywhere inside is `transport_failed` even
  when it surfaces during the handshake.

The second one exists because of a discovery below, and without it the
most common HTTP failure there is would be misfiled.

`call_failed` and `stopped` need no classification: they are decided by
the code that emits them.

The refusal set is the second closed set, and it is the one the plan
left to be fixed at implementation. The refusals that exist are all
`ConfigError` subclasses whose type the API already turns into a status
code, so the classification is the same one and reads from the most
specific down: `in_progress`, `database_busy`, `unreadable`, `invalid`,
and `unexpected` as the net under them, for a `read` callable that fails
in a way the configuration layer has no type for. Nothing in the tree
raises the fifth today; a closed set with no fallback is a set that
opens quietly the first time something does.

### What each event carries, and what it does not

`mcp_connected` names the transport and counts the tools. The sentence
still lists the published names, which is deliberate and unchanged: a
published name has been through this application's own publishing rule,
it is what the model was given, and the status surface answers with it.
The field is a count because a list is not something a collector can
aggregate.

`mcp_down` carries `duration_ms` on the four reasons that failed on the
way up and on neither of the other two. The temptation was to give
`stopped` the connection's uptime, and it is the wrong answer: the field
would then mean two different things under one name. `stopped` is also
the only reason emitted at INFO, because a shutdown or a reload is not a
problem an operator has.

`mcp_reload` is emitted inside the refusal and inside the shielded apply
rather than at the caller, which is what makes it exactly once: a client
that disconnects cancels the handler awaiting the reload, and the apply
carries on behind its shield with the event as its last act. Counts
rather than names, for the reason `McpReload`'s own docstring gives:
which entries moved is the status surface's answer, taken in the same
breath by whoever asked.

`mcp_tool_shadowed` carries a position where a name would be, in the
fields as in the sentence it already had.

### The tests

Twenty-four new tests across four files, and not one existing test
touched. Each event is driven through a real manager against the servers
the MCP suites already run: the stdio child process, and the in-process
uvicorn one. (The review round below adds six more and moves three of
these, each where a finding changed the behaviour they pinned.)

- `test_tools_mcp.py` (nine): what the manager decides. The connect
  count, `transport_failed`, `discovery_failed` (through a class-level
  patch of `ClientSession.list_tools`, so the transport and the
  handshake really did succeed), `stopped` with its level and its
  absent duration, the call-drop pairing asserted in emission order,
  the shadow drop, and the credential sentinel.
- `test_tools_mcp_http.py` (four): what only a socket can say. The
  transport name, `transport_failed` over a refused connection,
  `connect_timeout` against a listening socket nothing reads (the
  kernel accepts into the backlog, so the handshake is sent and no
  answer comes), and `initialize_failed` with the malformed-handshake
  sentinel.
- `test_tools_mcp_reload.py` (six): applied with its counts, each of the
  four refusal tokens, the preparation half of a refusal, the
  already-running one, and the apply whose caller went away.
- `test_server_event_pins.py` (five): one pin per event, in the suite's
  established style.

Three helpers (`emitted`, `one_event`, `fields_of`) live in the stdio
suite and are imported by the other two, so "what an event carries" is
read one way in all three. `fields_of` reads the field set through
`logs._STANDARD_ATTRIBUTES`, like both pin suites, which is what makes
`assert fields == {...}` catch a field nobody meant to add.

### Deviations from the plan

Four. Three are choices this milestone made; the fourth is a decision
the PR review round below overruled, which supersedes the plan on that
point.

**`mcp_call_dropped` carries a position and not a tool name.** The
plan's table gives its fields as `entry` and `tool`, and this milestone
shipped that. The review's first finding rejected it, on the same
reasoning the plan itself used to strip the name from
`mcp_tool_shadowed`: a published name is half whatever the far side
called its tool, and sanitizing only replaces the characters both LLM
APIs refuse, so an alphanumeric credential goes through untouched. The
plan applied that reasoning to one event and not to the other two that
carry names, which is the inconsistency the review found rather than a
new argument. The fields are `entry`, `position` and `error`, and the
README's table is the surface of record.

**`mcp_down` classification is not the phase marker alone.** The plan
says the six tokens map one-to-one onto decision sites and that
classification is by exception type. Both are true, and they are two
rules rather than one: the marker decides, and two type rules override
it. The `httpx.TransportError` rule is not an embellishment, it is what
makes the most common HTTP failure honest (see the discoveries).

**The refusal reason set has five tokens, not four.** The four refusal
types that exist are `ReloadInProgressError`, `DatabaseBusyError`,
`StorageError` and `ConfigError`, and `_read` re-raises anything else a
`read` callable raises. Rather than let that one escape unreported, or
let the set quietly open later, `unexpected` is declared as the fifth
and documented as the net.

**`_connect` takes a callback.** The plan says "a local marker advanced
between the stack entries in `_run`'s envelope", which reads as the
stack entries being visible in `_run`. They are not: `_connect` owns
them, and its name appears in four test docstrings. So the marker stays
a local of `_run` and reaches `_connect` as a one-line `reached`
closure, which advances it at the one boundary only `_connect` can see.

### Discoveries

**The streamable_http transport is entered before it has spoken to
anything.** `streamable_http_client` starts its task group and yields
without contacting the server, so a URL nobody answers raises on the
first request of the handshake. The phase marker therefore says
`initialize_failed` for the single most common HTTP failure there is,
which would tell an operator the far side answered. This was found by
running the classification against the existing dead-URL test before
writing an assertion about it, and it is the whole reason for the
transport-error rule above. The stdio transport does not behave this
way: `stdio_client` spawns the child in `__aenter__`, so a bad command
raises on the way in.

**A credential-shaped tool name that is published reaches the log, and
always did.** The plan's finding 5 is about the shadowed line, and that
line is clean. But the sentinel test then found the same planted name in
the connect sentence, which lists the published names, and it would also
appear in the status surface's `tools` array. That is the documented
rule (`mcp.py`'s module docstring: what an operator reads is "the entry
name, the outcome, and tool names that have been through the publishing
rule"), and sanitizing replaces only the characters an LLM API refuses,
so an alphanumeric secret survives it. Rather than assert an absence
that is not true, the test pins the exposure: exactly one record in the
whole drive carries the planted name and it is `mcp_connected`. Whether
a published name should be printed at all is a wider question than this
issue, and it now has a test that will notice if the answer changes.

**A pin suite has no "before" for a new event.** The forty-two server
pins are evidence because they ran green against the unmigrated tree.
These five cannot be: there was nothing to be identical to. What they
are instead is the README's table, executable, from the day it was
written, and the note at the head of the section says so rather than
letting a reader assume the stronger thing.

**Sentinel tests are sensitive to the level they capture at.** The first
version of the malformed-handshake pin captured the root logger at
DEBUG, and failed: at DEBUG, httpcore prints the headers of every
response any httpx client in the process receives, and the stub answers
with the sentinel in one of them. That measures somebody else's debug
logging rather than this surface, which is a point the existing test
next door already makes in a comment about its own scoping.

### Verification

From `samtal-server/`, at `a2eb111`, which is after the review round
below:

```
uv run ruff check .                 All checks passed!
uv run pytest tests/unit -q         2039 passed, 15 skipped
uv run pytest tests/integration -q  53 passed
```

The arithmetic, since an earlier draft of this section got it wrong and
the review's last finding caught it. Milestone 2 ended at 2001 and its
own review round took it to 2009, which is the number its section
records; this milestone adds twenty-four, and its review round adds six
more. 2009 + 24 + 6 = 2039. The 2030 an earlier draft reported was
measured before milestone 2's round had finished landing, against a
parent that was still moving, and the 2006 it explained itself with was
a guess at a number that turned out to be 2009. Both are gone rather
than reconciled: a measurement taken against a base that no longer
exists is not evidence of anything.

No integration test was added here: every one of these events is
reachable from a unit driver holding a real manager, which is where the
MCP suites already live.

`git diff --stat origin/main` lists this milestone's files and nothing
else: `tools/mcp.py`, `tools/publish.py`, the six test files, the stdio
test server, `samtal-server/README.md`, `CHANGELOG.md`, the plan and
this document. `events.py` is not among them, which is the point of the
two milestones above: the MCP subsystem emits through the same emitter
as everything else and needed nothing added to it.

### The issue, closed

All three milestones are in. `samtal_server/events.py` holds the
emitter, the tap and the hub; the session scope and the server scope
both emit through it and the surface they produce is byte-identical to
the one they produced before; the MCP lifecycle has events for the first
time. What #138 deliberately did not do is attach a consumer: the store
sink is #120's and the exporters are #66/#67's, and both now have one
interface to attach to rather than forty-two emit sites to touch.
