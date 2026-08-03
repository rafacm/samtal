# Realtime multi-turn and barge-in implementation notes

**Date:** 2026-08-03

Companion to
[`2026-08-03-realtime-multi-turn-and-barge-in.md`](2026-08-03-realtime-multi-turn-and-barge-in.md),
appended in the same change that ticks that plan's checklist. Records
deviations from the plan, resolutions of its open risks, and
discoveries worth keeping.

## Realtime multi-turn and barge-in (PR #13)

The plan's design was implemented as written, in the six commits it
describes and in that order. Every line-number anchor it gives against
`main` at ba9008e was checked before editing and every one matched, so
nothing had to be reconciled against the code.

Deviations and additions relative to the plan:

- **The plan's test bounds were wrong, because an endpointed utterance
  is longer than its speech.** The plan asserts `heard_ms` in
  [540, 660] and [180, 300], the ranges the existing `listen stop`
  tests use. An utterance the endpointer ends carries the trailing
  silence it sat through (700 ms, and the buffer keeps whole 60 ms
  frames), so 600 ms of speech transcribes as about 1320 ms. In
  realtime mode there is a second term: the session never stops
  buffering, so silence sent past one endpoint sits in front of the
  next utterance and counts toward it. The tests use an
  `assert_endpointed_speech(texts, speech_ms)` helper instead, a window
  around the speech length that both terms fit inside and that still
  separates the utterance lengths these tests tell apart.
- **The endpointer fires one frame later than the arithmetic says.**
  The first "silence" frame after a tone decodes with an RMS around
  2000, well over the mock endpointer's threshold of 500: it carries
  the codec's tail of the tone. So a helper sending exactly the 700 ms
  window never endpoints. `endpoint_silence` sends 840 ms, and the
  assertion above is a window rather than a number for the same reason.
- **`collect_reply` was generalized into `collect_until(websocket,
  predicate)`**, which the barge-in tests need: they read up to the
  first `sentence_start` (the reply is now audibly streaming, so there
  is something to interrupt) and up to the interrupting reply's `stt`.
  Stopping at the `stt` rather than the whole reply is what keeps the
  unit lane's cost to one full playthrough of the roughly 8 s mock
  reply, in the test that is about a reply completing.
- **`config_with_agent` gained a `server` kwarg**, which the plan
  offered as one of two options, for the `barge_in: False` test.
- **`_replying()` extracted**, since both halves of the barge-in
  decision (the drop guard in `_handle_audio` and the branch in
  `_finish_utterance`) ask the same question of the reply task.
- **The README was updated too**, beyond the plan's documentation list:
  a "Listening and barge-in" section, since `server.barge_in` is a flag
  an operator flips per board and the README is where the `server` keys
  are explained, and a `barge_in` row in the structured-log event
  table, which is a list that would otherwise go stale.

Discoveries:

- **Both regression tests were confirmed against the unfixed code**,
  not just written against the fixed code. With the conditional clear
  reverted, `test_realtime_mode_serves_a_second_utterance_without_listen_start`
  and `test_abort_in_realtime_mode_keeps_the_session_listening` hang
  (the deaf session never answers, so the test blocks on the reply that
  never comes), and the integration test fails on its `asyncio.wait_for`
  timeout. With the barge-in branch reverted to the old drop-and-warn,
  `test_realtime_barge_in_cancels_the_reply_in_flight` hangs. The unit
  hangs are the honest failure mode for this bug: silence from both
  ends is what the issue describes.
- **Barge-in cuts in fast.** In the barge-in test the first reply
  delivers about 60 ms of its 7.7 s before the interruption lands: the
  server reads mic frames while the reply is paced out frame by frame,
  so an interruption is acted on as soon as the endpointer trips, not
  at some frame boundary of the reply.
- **The shutdown drain is unaffected**, as the plan's non-changes
  section predicted: a barge-in during a drain cancels the snapshot's
  reply task early, and the new reply dies on the closed socket through
  the `WebSocketDisconnect`/`RuntimeError` the reply path already
  suppresses. Both `test_drain.py` files pass untouched.

Resolution of the plan's open risks:

- **Echo tail at reply boundaries**: still open by construction, since
  it can only appear on hardware. The signal to watch for at the
  checkpoint is unchanged: replies that answer nothing, arriving just
  after the previous reply finished speaking, mean that board wants
  `barge_in: false`. It is written up in the README so the operator
  meets it before the board does.
- **Timing-sensitive tests**: not flaky. The barge lands about two
  orders of magnitude inside the bound (60 ms spoken against a limit of
  half of 7.7 s), so the roughly 8 s reply did not need lengthening.
- **Barge-in mid-tool-round**: unchanged and not separately tested, as
  the plan accepted. Cancellation propagates through `_run_one`'s
  `CancelledError` re-raise.

Verification: `uv run ruff check .`, `uv run pytest tests/unit -q` (463
passed), and `uv run pytest tests/integration -q` (27 passed) all from
`samtal-server/`.

### Hardware checkpoint, 2026-08-03

Waveshare ESP32-S3-Touch-LCD-1.54, firmware 2.4.0, the board issue #10
was found on. The server ran from this branch rather than from the
image, on the local pipeline the earlier checkpoints used (Silero,
faster-whisper `small`, Ollama `gemma4:e4b`, Piper `lessac`), with
`barge_in` left at its default. One PWR press, four utterances, no
button and no `listen start` in between:

```
12:17:19  listening (realtime mode)
12:17:25  heard "Hey, what is the capital of Iceland?"
12:17:35  replied "The capital of Iceland is Reykjavík."
12:17:41  heard "And how many people live there?"
12:17:52  replied "It has an estimated population of around 120,000 people..."
12:17:59  heard "Tell me a long story about the lighthouse."
12:18:12  barge-in, cancelling the reply in flight
12:18:14  heard "Stop, what is 2 plus 2?"
12:18:20  replied "Two plus two equals four."
```

The one `listening (realtime mode)` line, at info where the diagnosis
had needed DEBUG, is the whole of the bug: the device says it once and
the session carried it across every turn after.

Discovered here and not visible from the tests: **every utterance after
the first carries the whole gap since the previous one as silent
lead-in.** The durations above are 3.8 s, then 15.6 s, 18.1 s, and
15.5 s, where only the first is the length of what was said. A realtime
session buffers continuously, so what reaches ASR is the reply's own
playback time plus the pause plus the speech. It is bounded, and the
30 s tail cap held, but faster-whisper is then transcribing 15 s where
3 s is speech: about a second of extra latency per turn on this
machine, five times the audio for an ASR priced by the minute, and one
language detection down at 0.52 confidence (still correct, and all four
transcripts were right). Not a defect in the design as planned, and out
of scope for this PR: the session cannot know where speech began, since
`Endpointer` is only `feed() -> bool` and `reset()`. Both endpointers
track it internally (`_speech_heard` in each), so the fix is to report
speech start across that protocol and trim to a short pre-roll, which
touches the mock and Silero implementations together. Filed as issue
#14 rather than widened into this branch.

The second checkpoint, a single-mic board with `barge_in: false`, could
not run: the Waveshare ESP32-S3-ePaper-1.54 on the desk still carries
factory demo firmware (LVGL plus a BLE GATT client demo), and its NVS
has wifi credentials but no `ota_url` and no application namespace, so
nothing on it reaches the OTA endpoint. Flashing and provisioning it is
board bring-up rather than part of this fix, and it comes with a
question worth answering first: a single-mic board may present `auto`
rather than `realtime`, in which case the flag has no board to run on
yet. Filed as issue #15, and PR #13's second box stays unchecked
pointing at it.
