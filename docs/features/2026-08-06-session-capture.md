# Capture a session to disk so a real one can be analysed offline

## Problem

Issue #42: `barge_in` misfiring (#28) is an acoustic defect. It was
found on a moving device in traffic, 10 barge-ins across 12 turns with 5
of those turns never producing a complete reply. Nothing in the test
lanes can reproduce it: `tests/unit` feeds synthetic frames and
`tests/integration` drives the xiaozhi-sdk simulator over a websocket,
and both bypass the microphone, the board's echo cancellation, and the
room. The one session that showed the defect was not recorded, so all
that survives of it is an event timeline pasted into an issue.

The consequence is that the fix has to be tuned against a guess. The
parameter that decides whether the assistant interrupts itself is how
much of its own voice survives the ES8311's echo cancellation and
reaches the endpointer, and that number is unknown. Tune against an
invented leakage figure and the fix tests clean and fails on the street;
over-correct and barge-in stops working entirely, which no synthetic
test notices, because synthetic audio never leaks.

What was missing was not another test. It was the recording that lets
the tests be written against reality.

## Changes

Three files per session, all stamped against `_opened_at`, the monotonic
session origin the code already sets.

**`<session>.wav`, stereo 16 kHz s16le.** Channel 0 is the decoded
microphone as received, channel 1 is what `_send_frames` paced out,
decoded back from the Opus that actually went to the device and
resampled to the pipeline rate. Stereo rather than two files because
alignment then costs nothing: sample N in both channels is the same
instant, so echo leakage becomes a measurement (cross-correlate the
channels, read off gain and delay) instead of a guess, and the file
opens in any audio editor where the overlap is directly audible.

Audio is placed by when it arrived rather than by how much came before
it, so a channel that goes quiet becomes silence rather than sliding
everything after it out of time with the other channel and with the
events. This is the property the whole file exists for, and it has its
own control below.

**`<session>.jsonl`, the decision track.** Every `_event()` payload
plus a `t_ms` offset that indexes into the WAV. Hooked at `_event()`
itself, which every call site already goes through, so an event that is
logged is an event that is recorded. Two things the logs do not carry
are added:

- Frames dropped before decode, with the reason (`not_listening`,
  `barge_in_off`, `framing_error`, `not_opus`, `undecodable`),
  aggregated per second rather than per frame, because the guards drop
  whole seconds at a time and what explains a misfire is the rate.
- The endpointer's `speech_ms` sampled every frame rather than only at
  decision points. This is what turns "barge-in fired wrongly" into
  "the endpointer classified 340 ms of the assistant's own voice as
  speech starting at 12.7 s".

**`<session>.json`, the manifest.** A capture outlives the code that
made it. It records the server revision (#41), the firmware version the
device reported at OTA check-in, the resolved agent and provider entries
verbatim, every `barge_in_*` threshold rather than a config hash, the
protocol version, and the wall-clock start. The thresholds matter most:
an old capture analysed after they change is misleading unless it states
its own. The provider entries are recorded verbatim because a hosted
model can change behaviour with no version bump on this side, so the
exact model string plus the capture date is the only handle on that.
They hold environment variable names rather than secrets, which the
config schema already enforces.

**The microphone is hooked at the very top of `_handle_audio`, before
every guard.** This is the point of the exercise. The session returns
early when it is not listening and when the `barge_in: false` guard is
closed, so capturing after the guards would discard precisely the frames
that explain a misfire. It uses its own decoder, because that one sees
every frame while the pipeline's sees only what got past the guards, and
pushing the guarded frames through the pipeline decoder would change
what the conversation hears.

**Firmware plumbing.** The manifest's firmware version is arguably its
most load-bearing field, since echo cancellation is firmware-side, and
the websocket handshake never carries it. `ota.py` extracts it at
check-in, so it is now kept against the MAC in a bounded `DeviceFacts`
and read back when the session opens. A device that reached the
websocket without checking in first, which a restarted server also
produces, simply has no firmware field rather than no capture.

## Key parameters

| Key | Default | Meaning |
|---|---|---|
| `server.capture.enabled` | `false` | The switch. Off by default, and the section on its own records nothing. |
| `server.capture.dir` | required | Where captures are written. Required even while disabled, so switching on is one word rather than one word and remembering where it writes. |
| `server.capture.max_session_s` | `900` | Stop recording a session after this long. The conversation carries on uncaptured. |
| `server.capture.max_total_mb` | `2000` | Budget for the directory. Whole captures are pruned, oldest first; two thirds of a capture is not a capture. |
| `server.capture.min_free_mb` | `1000` | Decline to start below this much free space, with a warning naming the reason. |

Storage is 64 kB/s, so one minute is 3.8 MB, a fifteen minute session is
58 MB, and the 2000 MB budget is around nine hours. Both bounds are
needed: `/data` also holds agent memory, `HOME`, and the model caches,
which grow underneath a byte budget, and filling it does not degrade
capture, it breaks those. FLAC through PyAV would roughly halve the
footprint and is not worth it at these volumes.

## Verification

`uv run pytest tests/unit -q`: 658 passed, 2 skipped.
`uv run pytest tests/integration -q`: 27 passed. `ruff check` clean.

Thirty-seven tests across two files: `test_capture.py` for what the
format guarantees, `test_capture_session.py` for the wiring, driven
through a real session over a websocket.

The load-bearing claims were each run against a control with the
relevant piece reverted:

| Claim | Control | Result without it |
|---|---|---|
| the microphone is recorded before the guards | hook moved after them | 307 ms of microphone audio recorded out of 1200 ms spoken, the missing 900 ms being exactly what the guard discarded |
| audio is placed by arrival, so a gap is silence | placed contiguously instead | three tests fail, including "the reply appears before the speech that prompted it" and a half-second offset between channels collapsing to zero |
| the audio covers the last event | padding removed | "the audio stops before the last event" |
| a capture still recording is never pruned | protection removed | the live capture is unlinked from under its own writer |
| the budget is enforced when a capture closes | prune-on-close removed | two over-budget captures left on disk |
| events stop at the limit too | limit check on events removed | an event an hour past the end of the audio |
| every offset indexes into the audio | clamp removed from the write path | the aggregate flushed on close lands past the end |

Against the issue's acceptance list:

- [x] **A captured WAV has the microphone and the reply on separate
  channels, and their overlap matches what was heard.** Read back
  through the `wave` module: two channels, 16 kHz, the microphone
  carrying audio where speech was sent and the reply carrying audio
  where `speaking_started` says it began.
- [x] **An event's `t_ms` lands on the corresponding audio to within one
  frame.** The reply channel's first loud sample is within 200 ms of the
  `speaking_started` event, and every event's offset lies inside the
  recorded audio.
- [x] **A capture interrupted by a pod stop is still readable, with the
  audio up to the interruption intact.** Simulated by dropping the
  descriptors without a clean close: the header still claims zero, the
  manifest says `complete: false`, and the PCM after the 44 byte header
  reads back with the right sample values. Writing this test is what
  showed that a hard stop would otherwise have lost whatever was still
  in a userspace buffer, so both files are now flushed as they go.
- [x] **Capture declines to start, with a warning naming the reason,
  when free space is below `min_free_mb`.**
- [x] **The directory stays under `max_total_mb` across enough sessions
  to trigger pruning,** and all three files of a pruned capture go
  together.
- [ ] **A real session recorded in a noisy environment,
  cross-correlated to produce the echo leakage figure #28 needs.** This
  is the entire point of the issue and it is not something a desk can
  produce: it needs the board, a street, and traffic. Rafael's.

Also verified: a conversation survives a capture directory it cannot
use, and nothing is written at all unless a directory is configured.

### Six findings, all fixed

`codex review` found five real defects across three passes, and CI later found a sixth, each with a
test that fails without its fix. Three of them were the same guarantee
leaking in different places, which is why the last fix moves it into the
write path rather than patching another call site:

1. **Event offsets could point past the end of the audio.** A session
   can be open through stretches with nothing decodable, and the
   decision track's whole contract is that `t_ms` indexes into the WAV.
   The file is now padded with silence out to the last event, bounded
   by `max_session_s`.
2. **Pruning could unlink a capture that was still being written.** Two
   concurrent sessions and a tight budget were enough: the older live
   one would be deleted from under its own open descriptors. Sessions
   still recording are now protected, and so is the newest finished
   capture, because a budget smaller than one session would otherwise
   delete the recording somebody just went out to make.
3. **The budget was only checked when a capture started.** A session
   that overran sat there until some later session happened to begin.
   Closing a capture now re-checks it, and says so in a warning when
   nothing more can be pruned.
4. **Events kept being written past `max_session_s`.** The audio is
   clamped to the limit on close, so a silent session left open for an
   hour would have written `session_closed` at 3600 s into a decision
   track whose audio ended at 900 s. The limit now ends the recording
   whichever way it is reached.
5. **An event landing on the last frame pointed one sample past it.**
   Found by CI rather than by the review, and only after merging: the
   test asserting the guarantee passed locally in the full suite and
   failed deterministically when run alone, because it depended on what
   the machine clock happened to read. A sample at index N exists only
   once N+1 frames are written, and `t_ms` rounds up to a tenth of a
   millisecond. The audio is padded one frame past the last event now,
   which covers both, and the test drives its own origin from zero
   rather than a clock reading.
6. **The aggregate flushed on close could still land past the audio.**
   The general form of the previous two. An event's offset is now
   derived from a frame index clamped to the same limit the audio is
   clamped to, so both halves come from one number and every record
   indexes into the WAV by construction rather than by each caller
   remembering to.

## A note on the switch

The first version made the presence of the section the switch, on the
grounds that capture should be impossible to enable by accident. Rafael
asked for an explicit `enabled: false` instead, and he was right on two
counts. The schema already works that way for `auth.enabled`, so a
reader has a precedent to expect. And the field workflow is to record,
then stop: with the section as the switch, stopping means deleting the
directory and the budgets along with it, and starting again means
restoring them. A flag makes it one word and keeps the tuning.

It is not weaker: the section still has to exist *and* the flag has to
say so, which is two deliberate acts rather than one. `dir` stays
required even while disabled, so there is no state where capture is on
with nowhere to write, and a section that is present but off says so
once at startup, because a configured capture that records nothing is
otherwise a silence to debug.

## On what this does not do

It does not analyse anything. It produces the three files; reading a
cross-correlation off them is a separate step, done off the server with
whatever tool suits. Adding an analysis path here would mean guessing
now at what the recording will turn out to show, which is the same
mistake the issue exists to avoid.

## Files modified

- `samtal-server/samtal_server/capture.py` (new)
- `samtal-server/samtal_server/session.py`
- `samtal-server/samtal_server/app.py`
- `samtal-server/samtal_server/ota.py`
- `samtal-server/samtal_server/ws.py`
- `samtal-server/samtal_server/config/models.py`
- `samtal-server/tests/unit/test_capture.py` (new)
- `samtal-server/tests/unit/test_capture_session.py` (new)
- `samtal-server/config.example.yaml`
- `samtal-server/README.md`
- `CHANGELOG.md`
