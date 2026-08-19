# The no-leak sweep: three retained log lines that quoted the far side

**Date:** 2026-08-19

## Problem

The retained log is governed by the no-leak contract stated in the
[content-and-telemetry ADR](../adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md):
no secret and no far-side bytes on any surface that is kept. The event
tier holds that line by construction, and the sweep of the 2026-08-14
refactoring batch closed nineteen leak-shaped findings on it. Three
ordinary `logger` calls, none of them events, were left behind. They
came from the same family: a line that renders what something else
wrote.

1. **#183, the barge-in confirmation.** The gate ladder's catch around
   `confirm_transcript` was a `logger.exception`, so an ASR failure put
   the provider's own message, every exception in the chain behind it,
   and a traceback of the lot onto the log. What an ASR client raises
   is far-side text: an SDK that cannot authenticate quotes what it was
   given, one that cannot reach its endpoint quotes the URL.
2. **#182, the filler playback arm.** The catch-all had the same
   `logger.exception` shape, over a block that runs a codec across
   provider-synthesized audio. The arm above it made the leak worse by
   hiding its own trigger: it caught `(DeviceGone, RuntimeError)`, and
   `DeviceGone` subclasses `RuntimeError`, so the tuple was the base
   class, and every local bug in the block was returned on in silence
   as though the device had disconnected. The comment said so and
   deferred the decision to #182.
3. **#185, the device abort reason.** `device_aborted` interpolated the
   abort message's `reason` field into its line. That field is a string
   the far side writes, and the upstream protocol note is explicit that
   its values are implementation-defined, so a device (or anything
   speaking to this port) could put an arbitrary string on a kept
   surface by aborting. The review round found the same leak one layer
   earlier, through an abort whose `reason` is the wrong shape and so
   never reaches the runtime at all.

None of the three is an event, which is why the event registry (#155)
does not reach them and why they survived the sweep that closed the
event-tier findings.

## Changes

**#183.** The arm logs one fixed sentence plus the exception's class
name, with no `exc_info` and no `str(exc)`, which is the discipline the
reply body in `pipeline.py` already follows. Nothing diagnosable is
lost: the confirmation runs inside the runtime's own
`_watching("asr", ...)`, so a failure on the wire is already reported
as `provider_failed` carrying the stage, the provider entry and the
host, sanitized at that decision site. The resume-and-drop behavior is
unchanged.

**#182.** Two decisions, both recorded in the code:

- The disconnect arm narrows from `(DeviceGone, RuntimeError)` to
  `DeviceGone` alone. The edge translates both shapes of a vanished
  device into that type (#137), and it is what the block's two
  device-facing calls raise, so nothing a resample or an encode can go
  wrong with is caught there any more.
- The catch-all logs the class name and nothing else, and the mask
  stands down exactly as before: swallowed, the reply it masks
  unharmed.

The batch built whole before the first await, and the comment
explaining why, are untouched, so the characterization pin over the
shared Opus encoder's feed order still passes byte-unmodified.

**#185.** A closed reason-token set at the decision site,
`DEVICE_ABORT_REASONS` in `runtime/pipeline.py`. The firmware's
`AbortReason` enum has exactly two members (`main/protocols/protocol.h`
in 78/xiaozhi-esp32): `kAbortReasonNone`, which sends no `reason` field
at all, and `kAbortReasonWakeWordDetected`, which sends the string
`wake_word_detected`. The set holds the one spelling a device sends;
an abort that carried no reason renders `none`, and anything else
renders `other` without its value appearing anywhere. Absence is
classified before the set is consulted, so an empty string is `other`
(a device chose to send it) rather than `none`. The line keeps its
shape, `device aborted (%s)`.

**#185, the malformed path.** The closed set only covers an abort that
parses. A `reason` of the wrong shape is refused one layer earlier, in
`protocol/messages.py`, and that refusal wrapped pydantic's
`ValidationError` rendering, which carries `input_value=`; the session
edge logs the refusal verbatim, so `{"reason": ["sk-live-..."]}` put
the device's bytes on the retained line through a path the reason set
never sees. The refusal now names the message type, the field, and
pydantic's fixed error slug, all of them this side's vocabulary. Field
names are read from the model rather than from the error's `loc`, since
a `loc` inside a nested value can hold a key the far side wrote.

**Both arms report after the suite, not inside it.** Printing the class
name is only half of not printing the message: inside an active
`except` suite the caught exception is still the one being handled, so
a second failure raised there (`resume_output` on a wedged pacing
clock, or the logging call itself) escapes with the provider's
exception attached as its `__context__` and hands the message to
whoever catches it. Both sweep arms now capture the class name, leave
the suite, and report and clean up after it, which is the discipline
the device edge already follows where it builds `DeviceGone` inside the
arm and raises it outside.

## The one deliberate behavior change

A bare `RuntimeError` raised inside the filler playback block used to
be swallowed silently as a disconnect. It is now logged, by class name,
as the local bug it is. Nothing else about the filler moves: a device
that leaves mid-clip still ends the clip quietly, and every failure
still stands the mask down without touching the reply.

One rendering also changed, which is worth knowing when reading old
logs against new ones: an abort with no reason used to print
`device aborted (no reason)` and now prints `device aborted (none)`.
Two documents quote the old spelling as evidence in a diagnosis they
record (the 2026-08-06 ADR on `tts start`, and the provider
observability feature doc); they are historical records of what those
sessions saw and are left as they were.

## Key parameters

- `DEVICE_ABORT_REASONS` (`samtal_server/runtime/pipeline.py`):
  `frozenset({"wake_word_detected"})`. An abort with no reason renders
  `none`, classified before the set is consulted; everything else
  renders `other`. A firmware that grows a second reason adds it here,
  with the enum member as evidence.

No configuration keys, no event fields, and no event sentence changed.

One thing checked and deliberately left: the JSON-decode arm of
`parse_message` still renders `str(exc)` and chains the decode error.
`json.JSONDecodeError`'s message is a position report with no payload
text in it, and nothing logs the chain, so there is no leak to fix;
the exception's `doc` attribute does hold the whole frame, which is
worth remembering if a future caller ever renders that chain.

## Verification

- Lint: `uv run ruff check .` clean.
- Unit: `uv run pytest tests/unit -q`, 3008 passed and 16 skipped.
- Integration: `uv run pytest tests/integration -q`, 58 passed.
- The four committed-reference drift checks (domain config,
  conversations schema, events, OpenAPI) all diff empty, which is what
  says no event moved.
- Every new sentinel test was checked to bite: the fix was reverted in
  place, the test watched to fail for the right reason, and the file
  restored from a copy and touched (never `git checkout`, per
  `AGENTS.md`). The #183 test failed on the planted credential
  appearing in the rendered traceback; the #182 encode test failed on
  the same; the #182 `RuntimeError` test failed on there being no
  record at all; the #185 test failed on the sentinel being rendered
  verbatim into the abort line. From the review round: the three
  malformed-message tests failed on pydantic's `input_value=` rendering
  reaching the sentence and the log; the escaping-chain test failed on
  the sentinel being reachable through the second failure's
  `__context__`; the abort-reason pin failed on the empty string
  rendering as `none`.
- The suites that pin the event surface and the boundary
  (`test_event_schema_conformance.py`, `test_event_surface_pins.py`,
  `test_server_event_pins.py`, `test_session_characterization.py`,
  `test_boundary_contract.py`) pass unmodified. `test_boundary_contract.py`
  drives an abort of its own and is untouched by the reason
  classification, since the reason it sends is not what it asserts on.

## Review round, 2026-08-19

The external review of the PR returned four findings, all accepted and
all fixed here, one commit each.

1. **P1: a malformed abort reason still leaked.** The closed set covers
   an abort that parses, and `protocol/messages.py` refused the rest
   with pydantic's own rendering, `input_value=` included, which
   `device/session.py` logs verbatim. Fixed as described under "#185,
   the malformed path" above. Every other behavior was checked and
   kept: a malformed hello still closes with the same fixed reason, any
   other malformed message is still ignored with the session up (the
   new session-level test proves it by answering a turn afterwards),
   and the existing refusal test still matches, since the sentence
   still names the message type.
2. **P1: the sanitized arms did their work inside the `except` suite.**
   A second failure raised there escapes with the provider's exception
   attached as `__context__`, so the message the line took care not to
   print goes out anyway. Both arms now capture the class name, leave
   the suite, and report and clean up after it. The new test drives a
   secret-bearing confirmation failure into a device whose
   `resume_output` raises, and walks the escaping exception's chain.
3. **P2: `reason or "none"` folded the empty string into absence.**
   Absence is now classified first, `DEVICE_ABORT_REASONS` holds only
   what the firmware sends, and the test pins all four cases in order
   rather than asserting substrings that survive a collapse.
4. **P3: the `DeviceGone` docstring still claimed filler playback
   suppresses `RuntimeError`.** It names the one remaining broad site,
   the reply's closing `tts stop` pair, and records the filler's
   narrowing beside it.

## Files modified

- `samtal-server/samtal_server/runtime/turntaking.py`
- `samtal-server/samtal_server/runtime/filler_runner.py`
- `samtal-server/samtal_server/runtime/pipeline.py`
- `samtal-server/samtal_server/protocol/messages.py`
- `samtal-server/samtal_server/device/boundary.py`
- `samtal-server/tests/support/events.py`
- `samtal-server/tests/unit/test_turntaking.py`
- `samtal-server/tests/unit/test_filler_runner.py`
- `samtal-server/tests/unit/test_session.py`
- `samtal-server/tests/unit/test_protocol_messages.py`
- `CHANGELOG.md`
