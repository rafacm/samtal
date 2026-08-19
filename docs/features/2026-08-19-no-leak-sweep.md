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
   surface by aborting.

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
`wake_word_detected`. The set is those two, with `none` as this side's
name for the absent one; anything else renders as `other`, and its
value is not repeated anywhere. The line keeps its shape,
`device aborted (%s)`.

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
  `frozenset({"wake_word_detected", "none"})`. Everything outside it
  renders as `other`. A firmware that grows a third reason adds it
  here, with the enum member as evidence.

No configuration keys, no event fields, and no event sentence changed.

## Verification

- Lint: `uv run ruff check .` clean.
- Unit: `uv run pytest tests/unit -q`, 3004 passed and 16 skipped.
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
  verbatim into the abort line.
- The suites that pin the event surface and the boundary
  (`test_event_schema_conformance.py`, `test_event_surface_pins.py`,
  `test_server_event_pins.py`, `test_session_characterization.py`,
  `test_boundary_contract.py`) pass unmodified.

## Files modified

- `samtal-server/samtal_server/runtime/turntaking.py`
- `samtal-server/samtal_server/runtime/filler_runner.py`
- `samtal-server/samtal_server/runtime/pipeline.py`
- `samtal-server/tests/support/events.py`
- `samtal-server/tests/unit/test_turntaking.py`
- `samtal-server/tests/unit/test_filler_runner.py`
- `samtal-server/tests/unit/test_session.py`
- `CHANGELOG.md`
