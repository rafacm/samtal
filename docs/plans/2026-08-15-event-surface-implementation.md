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

### The pin suite, first and unchanged

`samtal-server/tests/unit/test_event_surface_pins.py` (new, 835 lines,
25 tests covering 26 emit paths; `agent_said` and `handover` share a
test because one handover emits both). Per path it pins four things:
`record.name`, `record.levelno`, `record.getMessage()`, and the exact
set of nonstandard record attributes with their values. The
nonstandard set is read through `logs._STANDARD_ATTRIBUTES` rather
than through a list written in the test, so the suite and the JSON
formatter cannot come to disagree about what an event field is.

Two normalizations, both declared in the module docstring:

- **Per path**, the fields whose values move between runs (`session`
  always, plus `duration_s`, `duration_ms`, `first_token_ms`,
  `speaking_ms`, `delay_ms`, `revision`, and `speech_ms` on the one
  path that reads a real endpointer) are replaced by a placeholder.
  The key stays pinned; only that value does not.
- **In the sentence**, the session id and every numeric run become
  placeholders, because durations are rendered into the sentence too.
  What stays pinned there is every word, every argument's position,
  and the type of what was substituted. Numeric literals inside a
  sentence are therefore not pinned, which is the deliberate limit of
  this suite: the exact values live in the fields, where they are
  pinned.

Every one of the twenty-six paths is driven; none had to be
approximated. Most reuse the fixtures the existing session suites
already use (`session_for`, `run_reply`, `masked_session`,
`realtime_session`, `ScriptedEndpointer`, `GatedAsr`, `ConfirmingAsr`,
`StallingLlm`, `reply_with`). Two things were built for it:

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
