# Pipecat alignment spike implementation

Companion to
[`2026-08-11-pipecat-alignment-spike.md`](2026-08-11-pipecat-alignment-spike.md).
One section per milestone, recording what was actually built, the
deviations from the plan, the resolutions of its open questions, and
the measured numbers.

The pinned version under test throughout is **pipecat-ai 1.7.0**, with
xiaozhi-sdk 0.5.1, on CPython 3.13.12, macOS 15 (darwin 25.6.0,
arm64). Every figure below is against that version; pipecat's
websocket transport and audio buffer processor are both areas the
project changes often, so none of it transfers to another release
without re-measuring.

## Milestone 1: one full exchange

### The feasibility checkpoint

The plan's risk section requires this before any serializer code
exists, because the plan named its components from documentation
rather than from running code. The answers below come from reading the
installed 1.7.0 sources (paths are relative to the spike venv's
`site-packages/pipecat/`), and the ones marked *confirmed live* were
re-checked against the running exchange in milestone 2.

**1. Does the serializer contract carry mixed JSON text and binary
audio in both directions?**

Yes, in both directions, with no wrapper type to disambiguate.
`serializers/base_serializer.py` declares `serialize(frame) -> str |
bytes | None` and `deserialize(data: str | bytes) -> Frame | None`.
On the way out, `transports/websocket/fastapi.py`'s
`FastAPIWebsocketClient.send` dispatches on the payload's Python type:
`bytes` becomes `send_bytes`, anything else `send_text`. On the way
in, `_WebSocketMessageIterator.__anext__` returns `message["bytes"]`
when present and `message["text"]` otherwise, so the serializer's
`deserialize` receives exactly the kind of message the peer sent. This
is what xiaozhi needs (JSON control messages plus binary Opus frames
on one socket) and it needs no transport subclass. *Confirmed live.*

**2. Where can the xiaozhi hello handshake run, relative to pipeline
start?**

Only inside the running pipeline, or outside pipecat entirely. The
serializer is handed nothing it could send through: its only lifecycle
hook is `setup(StartFrame)`, which carries sample rates and no
transport, client or websocket reference. It cannot answer a message
on its own, so the client hello has to become a frame and the server
hello has to come back as a frame.

The route that exists without touching pipecat internals is:
`deserialize` maps the client hello to an `InputTransportMessageFrame`,
which the input transport broadcasts; a spike processor answers with
an `OutputTransportMessageUrgentFrame`; the output transport's
`send_message` hands that frame back to `serialize`, which renders the
server hello JSON. Ordering works out because the input transport only
starts its receive task inside `start(StartFrame)`, so no device
message is read before the pipeline is running. The cost is one
extra processor that exists purely to answer a handshake, and it
counts in gate 2's numerator. *Confirmed live.*

**3. Does the output path expose a per-packet awaited send to tap?**

Yes. `FastAPIWebsocketOutputTransport._write_frame` awaits
`self._client.send(payload)`, and `FastAPIWebsocketClient.send` awaits
`self._websocket.send_bytes(payload)` on the FastAPI `WebSocket`
object the transport was constructed with. Because that object is a
constructor argument, the tap does not need private access or a
subclass: the spike passes a thin proxy that delegates every attribute
and timestamps immediately after the awaited `send_bytes` returns.
That is the latest observable boundary the plan asks for, one
timestamp per Opus packet, downstream of serialization, framework
queueing and pacing alike. *Confirmed live.*

**4. Does the transport pace audio out at frame cadence?**

**Yes, at real time. The first answer recorded here said no, and it was
wrong; measurement corrected it.** The correction is left visible
rather than quietly rewritten, because the wrong answer was the one
that shaped the serializer, and because it is a worked example of the
plan's own instruction to read running code rather than documentation:
reading the source alone was not enough either.

`write_audio_frame` sends the chunk and then awaits
`_write_audio_sleep`, whose interval is set in `start()` as

```python
self._send_interval = (self.audio_chunk_size / self.sample_rate) / 2
```

The first reading took that as half the chunk's duration and concluded
that a backlog of reply audio would leave the socket at twice real
time. `audio_chunk_size` is in **bytes**, not samples
(`audio_bytes_10ms = int(sample_rate / 100) * channels * 2`), so for
16-bit mono the division by the sample rate already yields twice the
chunk's duration and the `/2` cancels it exactly. For the spike's
configuration, 6 chunks of 10 ms at 24 kHz, the interval works out at
(2880 / 24000) / 2 = 0.06 s: precisely one 60 ms frame per 60 ms.

Measured, with nothing in the adapter pacing anything: 2103 packets
over 126.06 s of wall clock, median inter-send interval 60.0 ms, 99.8%
of intervals within 5 ms of the frame cadence. The transport supplies the
playback clock the plan doubted, and the open question "whether
pipecat's websocket transport paces audio out at frame cadence or
writes as fast as encoding allows" is answered: it paces.

Two caveats belong with the answer. The cancellation only holds for
mono: `audio_out_channels = 2` would make `audio_chunk_size` four times
the sample count and the transport would send at half real time, so the
formula is right by coincidence rather than by construction. And the
clock is not exact: across the 126 s run the send times accumulated
57.7 ms ahead of a perfect 60 ms clock, about -27.5 ms per minute, so
the transport sent 126.120 s of audio in 126.062 s of wall clock.

The consequence for gate 2 is that the pacing layer the spike added is
**redundant, not forced**. It has been removed from the adapter
entirely rather than merely disabled, so the plan's clause about
counting added pacing as adapter glue does not apply and gate 2 counts
an adapter an adoption would actually ship.

**5. What are `AudioBufferProcessor`'s delivery and timing
semantics?**

`processors/audio/audio_buffer_processor.py` is a `FrameProcessor`, so
what it records depends entirely on where it sits in the pipeline, and
the conventional placement (immediately after `transport.output()`)
puts it **downstream** of the transport's pacing rather than upstream
as the plan assumed: `BaseOutputTransport`'s media sender awaits
`write_audio_frame` (serialize, send, pacing sleep) and only then
pushes the frame downstream. So the bot track's frames reach the
buffer processor just after the very sends the tap timestamps. That
placement is what makes a shared timeline constructible at all, and it
is a discovery worth stating plainly: *whether pipecat's own recording
is wire-aligned is a wiring decision the user makes, not a property of
the component.*

Delivery is event-driven and independently timestampable, so the
plan's fallback finding ("only an aggregate buffer") does not apply.
With `buffer_size` greater than zero, `on_track_audio_data(user, bot,
sample_rate, num_channels)` fires whenever either track reaches that
size, and the spike's handler stamps the monotonic clock and the
cumulative sample count on arrival, exactly as the plan's epoch rules
require.

Two behaviours matter for the composition and are recorded here rather
than discovered later:

- The processor keeps the two tracks the same length by construction.
  Before extending one track it pads the other to the same byte
  position (`_sync_buffer_to_position`), and it pads the shorter track
  again at delivery (`_align_track_buffers`). The tracks are therefore
  aligned to each other by arrival order, not by any timestamp.
- It inserts silence for wall-clock gaps of more than 200 ms since a
  track was last written (`_fill_buffer_silence_gap`), using
  `time.monotonic()` and the target sample rate. So the recording does
  carry idle time, but quantised by a 200 ms threshold.

The output resampler is a single stateful `create_stream_resampler()`
per track, which is the plan's requirement for the recording side.

**6. What are the serializer's lifecycle hooks?**

One: `setup(StartFrame)`. It is called twice per connection, once from
the input transport's `start()` and once from the output transport's
`start()`, on the same serializer instance, so any setup work must be
idempotent. There is no teardown hook: `FrameSerializer` extends
`BaseObject` and neither transport calls anything on the serializer at
stop, cancel or cleanup. A serializer holding a codec, a resampler or
a pacing clock has to release it on an `EndFrame`/`CancelFrame`
passing through `serialize`, or not at all. *Confirmed live.*

**Verdict on the checkpoint.** No custom transport is forced: the
serializer contract carries the protocol, and the tap and the hello
both have routes that need no pipecat internals. What is forced is
adapter code the framework does not absorb: a handshake processor, and
a translation processor for every outbound control message, because
the serializer can neither originate a message nor see the frames that
would carry one.

### What was built

`spikes/pipecat-alignment/` as its own uv project, pinned to
pipecat-ai 1.7.0 with its silero and websocket extras, xiaozhi-sdk
0.5.1, uvicorn 0.52.1, numpy 2.4.6, scipy 1.18.0 and opuslib 3.0.1.
The adapter is `serializer.py`, `control.py` and `edge.py`; the
pipeline and the harness around it are `pipeline.py`, `drive.py`,
`make_audio.py`, `tap.py`, `compose.py`, `inject.py` and
`fidelity.py`.

The pipeline is the plan's: Silero VAD through pipecat's bundled
analyzer, a canned-audio processor that pushes a fixed 24 kHz clip as
TTS frames when the user's turn ends, no LLM, no cloud, no keys. The
server hello announces 24 kHz output, the device sends 16 kHz mono
Opus in 60 ms frames, and `drive.py` runs the exchange with the
unmodified simulator against an ephemeral port.

Both clips are generated locally by `make_audio.py` with macOS `say`
and `afconvert`. The utterance is 3.3 s of real speech at 16 kHz,
because Silero is a speech detector and the integration lane's 300 Hz
sine would never trip it; the reply is 126.2 s of deliberately
non-repeating narration at 24 kHz, long enough for the window floor and
written so that no clause recurs, because the measurement
cross-correlates the clip against itself and repetition would create
ambiguity where the point is an unambiguous lag. Neither clip is
committed, and `.gitignore` keeps the runs, the WAVs and the JSONL out
of git.

**Accept, met.** The exchange runs end to end, locally, with no cloud
and no keys: the simulator completes the hello, speaks, and hears the
reply back. From the run used for every figure below (`runs/stock`):
2103 Opus packets sent over 126.06 s, the simulator decoded 2018880
samples at 16 kHz, and the device saw `stt`, `tts start`,
`tts sentence_start` and `tts stop` in that order.

**Scoped to xiaozhi protocol v1.** The serializer treats every binary
frame as bare Opus and emits bare Opus, without reading
`Protocol-Version` or the hello's `version`. That is exactly what
xiaozhi-sdk 0.5.1 speaks and what this exchange exercised, and it is
wrong for protocol v2 and v3, which carry binary headers. Every claim
in this document that the serializer "speaks stock xiaozhi" means
protocol v1 as exercised here, and version negotiation with v2/v3
framing is adapter work an adoption would still owe, on top of every
figure in gate 2.

**Deviations from the plan.** Four, all recorded above or below rather
than silently absorbed.

- Finding 4 of the feasibility checkpoint was wrong on first reading
  and is corrected in place; the pacing layer the plan authorised as
  adapter glue turned out to be redundant and is off by default.
- The plan assumed one bot track from `AudioBufferProcessor`. There
  are two, they fail in opposite ways, and reporting only the first
  would have invited the objection that the spike used the wrong API,
  so both are measured (milestone 3).
- The plan put the `tts` and `stt` messages in the serializer. The
  transport does not deliver those frames to a serializer at all, so
  they live in a processor instead (milestone 4's obligation map).
- `fidelity.py` is not in the plan's file list. It exists because the
  injection run answers gate 1 only through the echo measurement, and
  a failure there does not say whether the reference was misplaced or
  corrupted. It is harness, and counted as harness.

**Open questions resolved.** xiaozhi-sdk accepts a direct websocket
URL, so no OTA stub was needed. The transport paces (finding 4). The
window floor is met by one long reply in one connection rather than
several turns, which is what the plan preferred.

## Milestone 2: instrumentation and a well-formed pair

### The tap, the recording, and the epoch

The tap is a proxy object around the FastAPI `WebSocket` that the
transport is constructed with. Because that websocket is a constructor
argument rather than something the transport creates, the tap needs no
subclass and no private access: it delegates every attribute and
timestamps immediately after the awaited `send_bytes` returns. That is
the boundary the plan's amendment requires, downstream of
serialization, of framework queueing and of pacing alike.

The epoch mapping is implemented literally as the plan fixes it, and
`compose.py` carries no onset detection and no correlation-based
shifting of any kind. One monotonic clock is read once by `Recorder`
before anything is recorded; t = 0 is sample 0 of both channels;
a buffer delivery of N samples observed at time t occupies
(t - N/rate, t]; a tap packet occupies its decoded duration ending at
its send timestamp; gaps are silence and leading silence is kept. The
raw per-send and per-delivery logs are written beside the capture
(`tap.jsonl`, `tap.opus`, `buffer.jsonl`, `turn.jsonl`, the raw track
bytes and `events.json`) and are not committed.

`AudioBufferProcessor` is wired **after** `transport.output()`. That
placement is the discovery worth stating plainly: the output
transport's media sender awaits `write_audio_frame`, which serializes,
sends and paces, and only then pushes the frame downstream, so the
buffer sees a chunk just after the send the tap timestamped. Placed
before the output transport it would record on the TTS service's
timeline instead, which is what the plan assumed. Whether pipecat's
own recording is wire-aligned is therefore a wiring decision the
adopter makes, not a property of the component.

### Rates and resampling, per observation point

- The simulator's utterance is 16 kHz, sent as 60 ms Opus frames.
- The tap's packets decode to 24 kHz, the announced output rate.
- `AudioBufferProcessor` is asked for 24 kHz, the output rate, and
  verified to deliver it: every delivery in the run reports
  `rate = 24000`, and `compose.py` refuses to build a pair if it ever
  reports anything else. Recording natively means pipecat resamples
  nothing, which is the point.
- Every track is laid out on the shared timeline at 24 kHz and
  converted to the 16 kHz capture rate exactly once, whole, through
  the same `resample_poly(2, 3)` call: 3155950 samples in, 2103967
  out, per track, four tracks, one call each. The converter's group
  delay, measured by resampling an impulse rather than assumed, is
  **-0.33 samples**, which is the sub-sample centring `resample_poly`
  does internally and is negligible against every figure here.

  The first version of this got it wrong, and the PR review round
  caught it: the buffer processor was asked for 16 kHz, so pipecat's
  own streaming SOXR resampler sat in the path, chunk by chunk and
  never flushed at end of stream, while the tap went through SciPy.
  Two implementations where the plan required one, and the unflushed
  one truncated the turn track by 92 ms, which fed straight into the
  placement bias the first gate 1 verdict rested on.

Where the live pipeline resamples was observed rather than inferred.
The canned clip is already 24 kHz and the pipeline's output rate is
24 kHz, so nothing resamples on the way out; the only resampling
inside the pipeline is the buffer processor's own 24 kHz to 16 kHz
conversion of the bot track, and the input transport's handling of the
16 kHz device audio, which is already at the pipeline's input rate.

### The composed pair, and its audit figures

`compose.py` writes `<session>.wav` (stereo 16 kHz s16le, channel 0 the
microphone as the buffer processor received it, channel 1 the reply
reference) and `<session>.jsonl` (`session_open`, one `heard` carrying
the utterance duration so the analysis masks the user's speech, and one
`speaking_started` stamped from the tap's first packet).

For `runs/stock`, laid out at 24 kHz: span 131.5 s; 539 buffer
deliveries totalling 3598470 samples, of which 486702 (13.5%) landed on
positions already written and were overwritten by the later delivery;
2103 tap packets placed contiguously with **no late sends and no
overwrites at all**; the turn track one delivery of 3026880 samples,
also with no overlap.

The 13.5% buffer overlap is not a composition bug and is the first
visible symptom of the milestone 3 finding: the delivered track
accumulates audio faster than wall clock, so placing each delivery to
end at its arrival stamp makes successive deliveries collide. The tap
has no overlap by construction now: under the corrected mapping a
packet starts at its send and never overwrites the one before it, and
because the wire clock runs slightly fast every send arrived early, so
no gap opened either.

### Accept: the stock control

The plan's acceptance is `echo_leakage_control.py` reporting PASSED on
a composed pair with the repository's scripts unmodified. Run at both
delays the plan requires, on both composed pairs, with no change to
`scripts/echo_leakage.py` or `scripts/echo_leakage_control.py`:

| pair | delay | detected | measured lag | measured gain | verdict |
| --- | --- | --- | --- | --- | --- |
| delivered-track ref | 250 ms | 125/125 | 250 ms | -30.0 dB | PASSED |
| delivered-track ref | 1500 ms | 125/125 | 1500 ms | -30.0 dB | PASSED |
| turn-track ref | 250 ms | 125/125 | 250 ms | -30.0 dB | PASSED |
| turn-track ref | 1500 ms | 125/125 | 1500 ms | -30.0 dB | PASSED |

Every window, lag and gain exact. The pair is well formed and the
measurement works on this data, which is the only thing this run
proves: both channels share whatever timeline error the composition
has, so it cannot and does not test alignment.

**Window accounting.** 128 windows fall in range on the 131.5 s
capture; 1 is discarded because the reference is below the -45 dBFS
activity threshold and 2 because the user-speech mask covers them,
leaving **125 candidate windows** per delay, above the plan's floor of
100. No script was modified, and no blocker required one.

**Deviations from the plan.** One: the plan named a single buffer
recording, and the composer builds two pairs because there are two bot
tracks. Otherwise the instrumentation is as specified.

## Milestone 3: the alignment verdict

### What the two bot tracks contain

`AudioBufferProcessor` offers two recordings of the bot, and gate 1
turns on the difference between them. The figures come from
`fidelity.py`, which compares each against the *uniform decode*: the
tapped Opus packets decoded and laid end to end with no timestamps
involved. That is the right yardstick because a device plays through a
DAC on its own clock and its jitter buffer absorbs arrival jitter, so
packet arrival times never survive into the sound.

**The delivered track** (`on_track_audio_data`, the one whose arrivals
can be timestamped) is corrupted. Against the 126.180 s the wire
carried it runs 149.936 s, 23.756 s longer, and it carries 13.00 s of
silence in 137 blocks inserted *inside* otherwise continuous reply
audio, 135 of them exactly 2280 samples (95.00 ms at the 24 kHz
recording rate). No single lag aligns it with the audio sent: the best
correlation over every admissible lag is r = -0.023.

The mechanism is in pipecat's source and audited against the data.
Every block ends on or within one sample of a delivery boundary
(`fidelity.py`: 137 of 137 within two samples, worst case one sample,
none exactly on one at this rate). While the device streams microphone
audio during a reply, each bot frame runs
`_sync_buffer_to_position(user, len(bot))`, padding the user track up
to the bot's position, and then the user's own frames extend it
further, so the user track outruns the bot track. At each delivery
`_align_track_buffers()` pads the shorter track to the longer, which
writes that accumulated difference into the *bot* track as silence. A
device that streams continuously is not an edge case for samtal, it is
what barge-in requires, so this is the normal operating condition.

The corruption is present in the raw recording, upstream of any
resampling, and it survived the resampler correction unchanged, as it
had to.

**The turn track** (`on_bot_turn_audio_data`) is faithful: r = 0.989
against the uniform decode, contiguous, no interior padding, because
`_bot_turn_audio_buffer` is extended only with the bot's own audio. It
is 0.060 s shorter than the audio sent, a tail the turn buffer closes
before the last chunk reaches it. It arrives **once**, when the bot
stops speaking.

### The wire, and the tap

The inter-send distribution answers the plan's open question directly.
With the transport's own clock and nothing added: median 60.0 ms,
p5 59.1 ms, p95 60.9 ms, max 70.7 ms, and 99.8% of intervals within
5 ms of the 60 ms frame cadence, per-interval jitter sd 1.46 ms. There
is no bursting to hide behind the simulator's receive buffering.

The clock is not exact: send times accumulated 57.7 ms ahead of a
perfect 60 ms clock over the run, about -27.5 ms per minute, so
126.120 s of audio left in 126.062 s of wall clock.

Under the corrected placement rule the tap is a faithful rendering of
the wire: r = 0.982 against the uniform decode of the same packets, up
from 0.755 under the ending-at rule the PR review corrected, which had
shifted every packet a frame early and overwritten audio wherever
placements collided.

### The tap-injection runs

`inject.py` adds the tap-decoded track, delayed and attenuated to
-30 dB, into the microphone channel, leaving pipecat's recording as the
reference, and runs `scripts/echo_leakage.py` unmodified with an
explicit `--max-lag-s 2.0`. Both delays run offline over the same
captured pair, so the two verdicts differ only in the injection.

| reference | delay | detected | median lag | lag bias | median gain | gain error | lag IQR | median r |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| delivered | 250 ms | 125/125 | 308 ms | +58 ms | -38.7 dB | 8.7 dB | 24.3 ms | 0.36 |
| delivered | 1500 ms | 125/125 | 1557 ms | +57 ms | -38.6 dB | 8.6 dB | 24.3 ms | 0.36 |
| turn | 250 ms | 125/125 | 251.2 ms | **+1.2 ms** | -30.2 dB | **0.2 dB** | 0.0 ms | 0.99 |
| turn | 1500 ms | 125/125 | 1501.2 ms | **+1.2 ms** | -30.2 dB | **0.2 dB** | 0.0 ms | 0.99 |

Drift, by the statistic the plan fixed, over the 5 s to 129 s span:

| reference | delay | Theil-Sen slope | first vs last quartile | movement over span | at search boundary |
| --- | --- | --- | --- | --- | --- |
| delivered | 250 ms | -0.00 ms/min | +15.6 ms | 0.0 ms | 0 of 125 |
| delivered | 1500 ms | +0.33 ms/min | +6.2 ms | 0.7 ms | 0 of 125 |
| turn | 250 ms | -0.00 ms/min | +0.0 ms | 0.0 ms | 0 of 125 |
| turn | 1500 ms | -0.00 ms/min | +0.0 ms | 0.0 ms | 0 of 125 |

No measured lag landed at the search boundary in any run, so no figure
here is a truncated search reported as a measurement. The detectable
floor for the drift statistic on a 124 s span is set by the lag grid,
one sample at 16 kHz or 0.0625 ms, over that span: slopes below about
0.03 ms per minute cannot be distinguished from zero.

The turn track's result is corroborated independently of the echo
machinery. `fidelity.py` measures the composed turn-track reference
sitting **-1.2 ms** from the composed tap track with r = 0.991, and the
placement arithmetic agrees: the turn buffer is delivered 2.9 ms after
the last packet's playout slot closes, and placing it to end at that
stamp starts it 5.2 ms after the wire's first send.

### Gate 1: PASSED on the turn track, at both delays

Against the plan's bar:

| criterion | bar | delivered track | turn track |
| --- | --- | --- | --- |
| detection rate | at least 90% | 100% pass | 100% pass |
| median lag | within 20 ms | +58 / +57 ms **fail** | +1.2 ms pass |
| median gain | within 3 dB | 8.7 / 8.6 dB **fail** | 0.2 dB pass |
| lag IQR | under 50 ms | 24.3 ms pass | 0.0 ms pass |
| drift | under 20 ms over span | 0.0 / 0.7 ms pass | 0.0 ms pass |

**Gate 1 passes.** A capture built on pipecat 1.7.0 recovers a known
echo's delay to 1.2 ms and its gain to 0.2 dB, in every one of 125
candidate windows, at both 250 ms and 1500 ms, with a lag IQR of zero
and no measurable drift across two minutes. That is the quality
samtal's own capture achieves, and it settles the question the spike
existed to answer: a shared timeline between pipecat's recording and
the wire **can** be constructed to cross-correlation grade.

**This reverses the verdict this document first recorded.** The first
run reported a constant 145.5 ms bias and called gate 1 failed. Two
errors in the spike's own instrumentation produced that number, both
found by the PR review round and both the spike's fault rather than
pipecat's: tap packets were placed ending at their send timestamp
instead of starting there, which shifted the whole wire track one frame
early and overwrote audio at collisions, and the bot track was
resampled by pipecat's unflushed streaming resampler instead of the
single converter the plan required, which truncated it by 92 ms.
Correcting both and rerunning everything downstream moved the bias from
145.5 ms to 1.2 ms. The measurement was wrong, not the framework, and
saying so is the whole point of keeping the record.

Three qualifications travel with the pass, and an adoption owns all
three.

- **The reference must be the turn track.** The delivered track, the
  one whose arrivals can be timestamped and the one a reader of the
  documentation would reach for first, is corrupted whenever the device
  streams during a reply. Nothing warns you: it looks like audio.
- **It is one aggregate per turn.** That is enough for offline echo
  measurement, which is what #48 does and what this gate tested. It is
  not a real-time AEC reference, because it does not exist until the
  turn is over.
- **The processor must record at the native output rate.** Asking it
  for the capture rate puts its streaming resampler in the path, and
  that resampler is never flushed, so the tail of every turn is lost.

**Deviations from the plan.** Two. The plan expected one bot track and
found two, so every table has twice the rows. And the plan's epoch
mapping was amended mid-flight, on the record and for reasons
independent of any measured lag, which the plan section states in full.

## Milestone 4: the size verdict and the paper trail

### Counting method and baselines

Physical lines (`wc -l`) throughout, as the plan fixes, and a
code-only count beside it wherever the two differ enough to matter.
Code-only excludes blank lines, comments and docstrings. Both are
reported because the spike's adapter carries unusually heavy
explanatory docstrings (each one a finding), and quoting only physical
lines would flatter the bespoke side while quoting only code lines
would hide real text a maintainer reads.

The denominator, both baselines the plan requires:

- **883 lines**, the figure issue #89 pins, a historical snapshot of
  `samtal_server/device/session.py`.
- **899 lines**, the same file's physical count at this branch's base
  commit, `891e257`. Code-only at that commit: **517**.

### The numerator: what an adoption would keep

Everything adoption-required, regardless of filename.

| file | physical | code-only | why it is adoption-required |
| --- | --- | --- | --- |
| `serializer.py` | 145 | 64 | the frame translation itself |
| `control.py` | 113 | 62 | the handshake and every outbound control message, which the serializer contract cannot express |
| `edge.py` | 54 | 28 | the per-connection transport parameters and construction |
| **adoption-required** | **312** | **154** | |

The pacing layer the spike first added is **removed**, not deducted.
The review round's third finding was that subtracting the 14-line
`_pace` method left its imports, its `_paced` and `_next_send` state,
its constructor option, its conditional call and its edge wiring
counted as ambient adapter code, so the 324 and 160 first reported here
were arithmetic on a feature that had not actually gone. It has gone
now, and the figures above are the adapter as it stands, measured
rather than adjusted. As built with the pacing feature present it was
338 physical and 168 code-only.

Reported separately, outside the comparison, as the plan requires:
measurement harness, `pipeline.py` (187), `drive.py` (191),
`make_audio.py` (94), `tap.py` (107), `compose.py` (331),
`inject.py` (147), `fidelity.py` (215) and `spike_env.py` (25),
**1297** physical lines. The canned reply service, the app wiring, the
tap and the composer are all in there; none of it is counted against
either side.

### The comparable slice

The full-file comparison is 312 against 899, but the spike's exchange
exercises a fraction of what the bespoke edge does, so the plan
requires one honest small-against-small number beside it. The bespoke
responsibilities the spike's exchange actually exercises are the hello
handshake (`_receive_hello`), the receive loop (`_serve`), inbound
audio and text (`_handle_audio`, `_handle_text`), the transcript and
speaking-state messages (`show_transcript`, `sentence_started`,
`begin_speaking`, `finish_speaking`), the codec
(`encode_audio`, `flush_encoder`), paced sending (`send_audio`,
`reply_started`, `restart_pacing`) and the two write helpers
(`_send_text`, `_send_frame`).

That slice is **222 physical lines, 155 code-only**.

| comparison | pipecat adapter | bespoke | ratio |
| --- | --- | --- | --- |
| against the whole file, physical | 312 | 899 | 0.35 |
| against the comparable slice, physical | 312 | 222 | 1.41 |
| against the comparable slice, code-only | 154 | 155 | 0.99 |

**For the work it actually does, the adapter is the same size as the
bespoke code it would replace.** The 0.36 figure is real but it
compares an adapter that speaks one exchange against a file that also
carries rejection paths, close codes, listening policy, pause and
resume, idle limits, capture and device tools.

### The seam-obligation map

Every `SessionInput` and `DeviceOutput` obligation from
`samtal_server/device/boundary.py`, with where it lands, whether the
spike exercises it, and what production code would remain. "Framework
absorbs it" requires observed behavior, not a documentation citation.

`SessionInput`, the device feeding the runtime:

| obligation | lands | exercised | evidence / what remains |
| --- | --- | --- | --- |
| `audio(pcm)` | serializer + framework | yes | `deserialize` decodes Opus to `InputAudioRawFrame`; the input transport carries it. Observed: VAD tripped on the real utterance and the reply followed |
| `listen_started()` | serializer, observed only | no | the `listen` message is turned into a frame nobody acts on. **Required, not implemented**: samtal's edge arms the turn on it |
| `listen_stopped()` | nowhere | no | **Required, not implemented**: manual end of turn. The spike ends turns by VAD only |
| `device_aborted(reason)` | serializer to `InterruptionFrame` | no | mapped but never fired; pipecat's interruption handling is **claimed, not evidenced** by this spike |
| `replying()` | nowhere | no | **Required, not implemented**. pipecat tracks bot speaking internally but the spike surfaces nothing |
| `drain(grace_s)` | nowhere | no | **Required, not implemented** |
| `close()` | nowhere | no | **Required, not implemented**: the spike relies on transport teardown |

`DeviceOutput`, the runtime driving the device:

| obligation | lands | exercised | evidence / what remains |
| --- | --- | --- | --- |
| `output_sample_rate` | edge/serializer constant | yes | announced in the server hello, accepted by the simulator |
| `show_transcript(text)` | control processor | yes | `stt` seen by the simulator |
| `begin_speaking()` | control processor | yes | `tts start` seen by the simulator |
| `sentence_started(text)` | control processor | yes | `tts sentence_start` seen |
| `encode_audio(pcm)` | serializer | yes | Opus encode. Shape mismatch: the boundary returns a batch, `serialize` may return one payload, so the serializer keeps an encode buffer |
| `flush_encoder()` | nowhere | no | **Required, not implemented**: the encode buffer's remainder is dropped at end of reply |
| `send_audio(batch)` | framework | yes | **Framework absorbs it.** Evidence: 2103 packets at a median 60.0 ms interval, 100% within 5 ms of cadence, with the serializer's own pacing off |
| `finish_speaking()` | control processor | yes | `tts stop` seen, after the last audio packet |
| `reply_started()` | nowhere | no | **Required, not implemented** |
| `restart_pacing()` | framework | no | the transport owns the clock; whether its reset semantics match samtal's is untested |
| `pause_output()` | nowhere | no | **Required, not implemented**: barge-in output gating |
| `resume_output()` | nowhere | no | **Required, not implemented** |
| `speaking_started_at()` | nowhere | no | **Required, not implemented**: the barge-in gate ladder needs it |
| `user_turn_ended()` | nowhere | no | **Required, not implemented** |
| `device_tools()` | nowhere | no | **Required, not implemented**: needs the device MCP transport |
| `call_device_tool(...)` | nowhere | no | **Required, not implemented** |

Totals: 23 obligations, **8 implemented and exercised**, 2 mapped but
not exercised, **13 required and not implemented**. The 324-line
adapter buys eight of twenty-three.

One obligation is absorbed with evidence (`send_audio`'s pacing). One
is absorbed by the framework in the ordinary sense that the transport
carries bytes (`audio`). The rest are the adapter's own or absent.

### What the seam could not express

The plan asks for this either way, and there are three. A fourth item
is not a seam limitation but belongs beside them, because it is
unbuilt adapter work the counts below do not include: the serializer
speaks xiaozhi **protocol v1 only**, with no version negotiation and
no binary-header framing for v2 or v3.

- **The serializer cannot originate a message.** Its only lifecycle
  hook, `setup(StartFrame)`, carries sample rates and no transport, so
  the hello handshake cannot live in it. It became a processor.
- **The serializer never sees outbound control frames.** A
  `TTSStartedFrame`, `TTSTextFrame` or `TranscriptionFrame` going
  downstream reaches `BaseOutputTransport.MediaSender._handle_frame`,
  which routes what it does not recognise to `write_transport_frame`, a
  no-op the FastAPI transport does not override; a `TTSStoppedFrame` is
  consumed earlier still, to raise "bot stopped speaking". So the whole
  `tts`/`stt` surface had to be re-emitted as transport messages from a
  processor. The plan expected these in the serializer.
- **`encode_audio` returns a batch, `serialize` returns one payload.**
  The boundary's `PlayableAudio` exists because one chunk of PCM can
  fill several Opus packets. The serializer works around it with an
  encode buffer and a transport chunk size chosen to make the mismatch
  never bite.

### Gate 2: not passed

Issue #89 sets the bar as evidence, not a threshold: "a small, durable
serializer plus adapter is direct evidence for normalize-the-hardware-
edge with pipecat behind it; an adapter that grows into an
impedance-matching layer is a finding against adoption".

On the plan's qualitative bar, the adapter as it would ship is
**clean**. It does not duplicate framework state to correct timing, and
it holds no per-reply state machine beyond message translation. It did
re-implement pacing the framework owns, which is precisely the named
symptom, but measurement showed that layer was unnecessary and it has
been removed; that is worth recording as how easily an adoption
acquires one, not as a standing charge.

On size, the adapter is **not evidence for adoption**. It is 312 lines
against 222 for the bespoke code doing the same job, or 154 against 155
counting code only: the same size, not smaller. Adoption would not
shrink the device edge, it would relocate it, add a framework
dependency, and leave thirteen of twenty-three seam obligations still
to write, plus the protocol-version negotiation the spike does not
implement at all.

Gate 2 therefore does not produce the evidence for adoption the issue
looked for. It does not produce evidence against adoption either: the
shape stayed translation, which was the thing that would have condemned
it. What it removes is the expectation that adopting a framework shrinks
the edge. It does not.

### The verdict, and what it decides

**Gate 1 passed. Gate 2 was neutral: clean in shape, no smaller in
size.** Per issue #89's stated outcome, that makes #31 "a genuine
tradeoff (porting the gate ladder, filler, and observer as custom
processors versus owning a streaming pipeline), decided with the
spike's measured numbers". It does not settle #31 by itself, and it
does not licence adoption either.

What the numbers say, put plainly for whoever decides #31:

- A trustworthy capture **is** constructible on pipecat 1.7.0, to
  1.2 ms of lag and 0.2 dB of gain. The worry that motivated the gate
  is answered, and answered in pipecat's favour.
- It is constructible only via `on_bot_turn_audio_data`, recorded at
  the native output rate, placed by its end stamp. The obvious
  configuration, the delivered track at the capture rate, silently
  produces a corrupted reference and a truncated one. An adoption
  carries that knowledge as a permanent maintenance obligation against
  a component the project changes often.
- It is a per-turn aggregate, so it serves offline echo measurement
  and not a real-time AEC reference. If #31 ever needs the latter, this
  spike did not test it and the delivered track cannot supply it.
- The adapter costs about what the bespoke edge costs for the same
  work, buys 8 of 23 seam obligations, and does not speak xiaozhi
  protocol v2 or v3 at all.
- The observability constraint from #84 is untouched by any of this:
  the reasoned decision events survive only in self-owned processors.

Two things carry forward whatever happens to #31. The
`SessionInput`/`DeviceOutput` seam survived contact with a foreign
framework: every obligation could be named and located, and the three
that could not be expressed came out as findings rather than
confusion, which is what a good boundary buys. And the reason samtal's
own capture is trustworthy is now stated precisely rather than
assumed: it places decoded audio at the moment it is sent, contiguously,
on one clock. That is the property a framework has to provide, and
reproducing it on pipecat is what the whole measurement turned on.

## PR review round

One external review of the branch as first pushed to PR #90: codex CLI
0.147.0, model gpt-5.6-sol, high reasoning, read-only against the
diff to `main`, 2026-08-11. Six findings, condensed below as received,
each carrying its resolution once the commit addressing it landed.

The overall verdict accepted the delivered-track corruption and its
`_align_track_buffers` mechanism, the padding-block signature, the
Theil-Sen calculations, the protocol-v1 exchange and the primary
comparable-slice counts. It rejected the gate 1 numbers as written,
because finding 1 invalidates the placement they rest on.

It was right to. Fixing findings 1 and 2 and rerunning moved the turn
track's measured bias from 145.5 ms to 1.2 ms and **reversed the gate 1
verdict from failed to passed**. The review is the reason this document
does not carry a wrong conclusion.

1. **P1: the tap placement matches neither device playout nor
   samtal's own capture.** `compose.py` placed each packet so that it
   *ended* at its post-send timestamp. samtal's capture places decoded
   audio *starting* at that timestamp and keeps packets contiguous when
   sends arrive early (`capture.py`, `at = max(channel.next_frame,
   self._frame_of(now), self._start_frame)`), and pipecat sends first
   and sleeps afterwards, so the timestamp marks the beginning of the
   60 ms playout slot rather than its end. The spike therefore shifted
   the tap 60 ms early and overwrote 11,668 samples where placements
   collided, dropping tap self-fidelity to r = 0.755. That manufactures
   much of the reported 145.5 ms faithful-track offset and invalidates
   the gate 1 verdict as written.
   *Resolution*: the plan's epoch mapping is amended, deliberately and
   on the record, to `start = max(previous_end, round(send_t * rate))`
   with gaps retained only for late sends and no overwrites;
   `compose.py` implements it and every downstream figure was rerun.
   Tap self-fidelity against the uniform decode rose from 0.755 to
   0.982, no placement overwrote another (and no send was late), and
   the turn track's bias fell to 1.2 ms, which is what flipped gate 1.
2. **P2: the resampling violated the plan's own method.**
   `pipeline.py` had `AudioBufferProcessor` resample the bot track
   internally with pipecat's streaming SOXR resampler while
   `compose.py` resampled the tap with SciPy `resample_poly`, so the
   two tracks did not go through one implementation and the filter
   delay was never recorded. It mattered: the unflushed streaming
   resampler is what left the turn track 92 ms short, feeding the
   placement bias directly.
   *Resolution*: the buffer processor now records both tracks at their
   native 24 kHz, and `compose.py` converts every full track to 16 kHz
   through the same `resample_poly(2, 3)` call, with the measured
   filter delay (-0.33 samples) and the input and output sample counts
   (3155950 in, 2103967 out, per track) recorded. The turn track's
   shortfall fell from 92 ms to 60 ms, and the delivered track's
   corruption survived untouched, as predicted: it is in the raw
   recording, upstream of any resampling.
3. **P2: the gate 2 numerator subtracted an incomplete feature.** Only
   the 14-line `_pace` method was deducted while the pacing feature's
   imports, its `_paced` and `_next_send` state, its constructor
   option, its conditional call and its edge wiring stayed counted as
   ambient adapter code.
   *Resolution*: the pacing experiment is gone from the adapter
   entirely and the adapter is recounted as it now stands: 312
   physical and 154 code-only, against 338 and 168 as built.
4. **P3: "speaks stock xiaozhi" overstates what was exercised.** The
   serializer treats every binary frame as bare Opus and emits bare
   Opus, without observing `Protocol-Version` or the hello's `version`.
   That is right for xiaozhi-sdk 0.5.1's protocol-v1 exchange and wrong
   for protocol versions 2 and 3, which carry binary headers.
   *Resolution*: every such claim is scoped to protocol v1 as
   exercised, here, in the obligation map and in the #84 draft, and
   the missing negotiation is named as adapter work an adoption would
   still owe.
5. **P3: "every silence block ends exactly on a delivery boundary" is
   too strong.** In the retained evidence 253 of 256 did; three ended
   one or two samples later.
   *Resolution*: `fidelity.py` now audits the boundary alignment and
   prints the distribution, and the claim quotes its output. On the
   rerun, at the native 24 kHz recording rate, none of the 137 blocks
   ends exactly on a boundary and all 137 end within one sample of
   one, so the text says that instead.
6. **P3: two non-gating counts were off by one.** `fidelity.py` was
   reported as 197 lines and the harness total as 1,204, where `wc -l`
   gives 196 and 1,203.
   *Resolution*: corrected, and every count in this document was
   re-taken from `wc -l` rather than carried forward.

### Draft comment for #84, not yet posted

Deliberately not hard-wrapped, unlike the rest of this file: GitHub
renders comment bodies with the `breaks` extension, so every newline
inside one becomes a literal line break. It is written to be copied out
and posted verbatim, and reflowing it here would shatter it there.

> **Pipecat alignment spike: both gates measured, gate 1 passed, gate 2 neutral**
>
> The spike from #89 ran against **pipecat-ai 1.7.0** (xiaozhi-sdk 0.5.1, CPython 3.13.12, macOS arm64). Minimal pipeline, Silero VAD plus a canned 126 s reply clip, no LLM and no cloud, behind a xiaozhi frame serializer on pipecat's FastAPI websocket transport, driven by the unmodified device simulator. Every figure is against that version; the transport and the audio buffer processor are both areas the project changes often, so none of it transfers to another release without re-measuring. Full detail: `docs/plans/2026-08-11-pipecat-alignment-spike-implementation.md`.
>
> **Gate 1 (capture alignment): PASSED, at both delays.** The bar is the existing control's: detection in at least 90% of candidate windows, median lag within 20 ms of the injection, median gain within 3 dB, lag IQR under 50 ms, drift under 20 ms across the span. The stock control passes exactly on the composed pair (125/125 windows, lag and gain exact, at 250 ms and 1500 ms), with `scripts/echo_leakage.py` and `scripts/echo_leakage_control.py` run unmodified.
>
> 125 candidate windows per delay, `--max-lag-s 2.0`, no lag at the search boundary:
>
> | reference | delay | detected | median lag | bias | median gain | lag IQR | drift over span |
> | --- | --- | --- | --- | --- | --- | --- | --- |
> | delivered | 250 ms | 125/125 | 308 ms | +58 ms | -38.7 dB | 24.3 ms | 0.0 ms |
> | delivered | 1500 ms | 125/125 | 1557 ms | +57 ms | -38.6 dB | 24.3 ms | 0.7 ms |
> | turn | 250 ms | 125/125 | 251.2 ms | **+1.2 ms** | **-30.2 dB** | 0.0 ms | 0.0 ms |
> | turn | 1500 ms | 125/125 | 1501.2 ms | **+1.2 ms** | **-30.2 dB** | 0.0 ms | 0.0 ms |
>
> A capture built on pipecat recovers a known echo's delay to 1.2 ms and its gain to 0.2 dB, in every window, at both delays, with zero lag IQR and no measurable drift over two minutes. The shared timeline the gate doubted **is** constructible.
>
> **Correction, and it matters.** An earlier version of this evidence reported gate 1 as failed on a constant 145.5 ms bias. That was the spike's own instrumentation, not pipecat: tap packets were placed *ending* at their send timestamp instead of starting there (samtal's own capture starts at the send and keeps packets contiguous; pipecat sends then sleeps, so the stamp opens the playout slot), and the bot track went through pipecat's unflushed streaming resampler instead of the single converter the plan required, losing 92 ms. An external review of PR #90 caught both. Corrected and rerun, the bias is 1.2 ms.
>
> **Three qualifications an adoption owns.** `AudioBufferProcessor` offers two bot tracks and only one works. The **delivered** track (`on_track_audio_data`), the one whose arrivals can be timestamped and the one you would reach for first, is time-corrupted: 126.180 s of reply comes back as 149.936 s with 13.00 s of silence inserted *inside* continuous speech in 137 blocks (135 of exactly 95.00 ms), correlating at r = -0.023 with the audio actually sent. The cause is `_align_track_buffers()` padding the bot track up to the user track at every delivery, which happens whenever a device streams mic audio during a reply, i.e. always, since barge-in requires it. The **turn** track (`on_bot_turn_audio_data`) is faithful (r = 0.989) and is what passes, but it arrives once per turn, so it serves offline echo measurement and not a real-time AEC reference. And the processor must record at the native output rate: asking it for the capture rate puts its never-flushed streaming resampler in the path and truncates every turn's tail.
>
> The transport itself paces at real time (median inter-send 60.0 ms, 99.8% of intervals within 5 ms of the 60 ms cadence), correcting a wrong reading we made early from its send-interval formula, whose `/2` cancels a bytes-per-sample factor for mono.
>
> **Gate 2 (adapter size and shape): not passed, not failed.** Adoption-required adapter, `wc -l`: **312 lines** (serializer 145, control processor 113, edge wiring 54), after removing a pacing layer measurement proved redundant. Against the bespoke edge at base commit `891e257`, 899 physical lines (883 in the issue's snapshot), that is 0.35. But against the **comparable slice**, the bespoke responsibilities this exchange actually exercises, it is **312 against 222**, or **154 against 155** counting code only: the same size, not smaller.
>
> The adapter buys **8 of the 23** `SessionInput`/`DeviceOutput` obligations. Two more are mapped but never exercised, and **13 are required and not implemented**, including `pause_output`/`resume_output`, `speaking_started_at`, `drain`, `flush_encoder` and the device tools. It also speaks **xiaozhi protocol v1 only**: bare Opus, no version negotiation, no v2/v3 binary headers, which is more adapter work the counts above exclude. Shape is clean, though: it stayed message translation, with no duplicated framework state and no per-reply state machine. Three things the seam could not express inside the serializer: it cannot originate a message (so the hello became a processor), it never sees outbound control frames (so the whole `tts`/`stt` surface became a processor), and `encode_audio` returns a batch where `serialize` returns one payload.
>
> **What this decides.** Per #89, gate 1 passing and gate 2 not condemning makes #31 a genuine tradeoff rather than a settled question: porting the gate ladder, filler and observer as custom processors, against owning a streaming pipeline. It does not licence adoption. The measured price is a framework dependency, an edge that is the same size as the bespoke one for the same work, 13 seam obligations still to write, protocol v1 only, and a permanent obligation to keep using exactly the right recording API against a component that changes often. The observability constraint from #84 stands: the reasoned decision events survive only in self-owned processors.
>
> Two things carry forward regardless. The `SessionInput`/`DeviceOutput` seam survived contact with a foreign framework: every obligation could be named and located, and the three it could not express came out as findings rather than confusion. And why our own capture is trustworthy is now precise rather than assumed: it places decoded audio at the moment it is sent, contiguously, on one clock. Reproducing that property is what the whole measurement turned on, and getting it wrong is what produced the first, retracted, verdict.
