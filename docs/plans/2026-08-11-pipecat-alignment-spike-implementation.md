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

Measured, with the serializer's own pacing disabled: 2103 packets over
126.06 s of wall clock, median inter-send interval 60.0 ms, 100% of
intervals within 5 ms of the frame cadence. The transport supplies the
playback clock the plan doubted, and the open question "whether
pipecat's websocket transport paces audio out at frame cadence or
writes as fast as encoding allows" is answered: it paces.

Two caveats belong with the answer. The cancellation only holds for
mono: `audio_out_channels = 2` would make `audio_chunk_size` four times
the sample count and the transport would send at half real time, so the
formula is right by coincidence rather than by construction. And the
clock is not exact: across the 126 s run the send times accumulated
58 ms ahead of a perfect 60 ms clock, about -27.6 ms per minute, so the
transport sent 126.12 s of audio in 126.06 s of wall clock.

The consequence for gate 2 is that the pacing layer the spike added is
**redundant, not forced**. It is off by default and kept behind a flag;
the plan's clause about counting added pacing as adapter glue does not
apply, and gate 2 counts the adapter without it.

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
- `AudioBufferProcessor` was asked for 16 kHz and verified to deliver
  it: every delivery in the run reports `rate = 16000`, and
  `compose.py` refuses to build a pair if it ever reports anything
  else. Its own single stateful `create_stream_resampler()` per track
  does that conversion, once over the session.
- The tap is converted 24 kHz to 16 kHz by one `resample_poly(2, 3)`
  call over the whole continuous track, never per packet: 3155427
  samples in, 2103618 out, which is the exact 2:3 ratio.

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

For `runs/stock`: span 131.5 s; 538 buffer deliveries totalling 2471720
samples, of which 393391 samples (15.9%) landed on positions already
written and were overwritten by the later delivery; 2103 tap packets
with 11668 samples of placement overlap; the turn track one delivery of
2017400 samples with no overlap at all.

The 15.9% buffer overlap is not a composition bug and is the first
visible symptom of the milestone 3 finding: the delivered track
accumulates audio faster than wall clock, so placing each delivery to
end at its arrival stamp makes successive deliveries collide.

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
turns on the fact that neither is what an echo measurement needs. The
figures come from `fidelity.py`, which compares each against the
*uniform decode*: the tapped Opus packets decoded and laid end to end
with no timestamps involved. That is the right yardstick because a
device plays through a DAC on its own clock and its jitter buffer
absorbs arrival jitter, so packet arrival times never survive into the
sound.

**The delivered track** (`on_track_audio_data`, the only one whose
arrivals can be timestamped) is corrupted. Against the 126.180 s the
wire actually carried it runs 154.482 s, 28.302 s longer, and it
carries 24.00 s of silence in 256 blocks inserted *inside* otherwise
continuous reply audio, 252 of them exactly 1500 samples (93.75 ms).
No single lag aligns it with the audio sent: the best correlation over
every admissible lag is r = 0.022.

The mechanism is in pipecat's source and was confirmed against the
data: every one of those silence blocks ends exactly on a delivery
boundary. While the device streams microphone audio during a reply,
each bot frame runs `_sync_buffer_to_position(user, len(bot))`, padding
the user track up to the bot's position, and then the user's own frames
extend it further, so the user track outruns the bot track. At each
delivery `_align_track_buffers()` pads the shorter track to the longer,
which writes that accumulated difference into the *bot* track as
silence. A device that streams continuously is not an edge case for
samtal, it is the requirement barge-in imposes, so this is the normal
operating condition rather than a corner.

**The turn track** (`on_bot_turn_audio_data`) is faithful:
r = 0.990 against the uniform decode, contiguous, no interior padding,
because `_bot_turn_audio_buffer` is extended only with the bot's own
audio. It is 0.092 s shorter than the audio sent, a tail loss at the
end of the turn. But it arrives **once**, when the bot stops speaking,
which is the plan's named fallback finding: a track with no
per-delivery observation point that can be timestamped independently.

So the choice an adopter faces is not between a good and a bad
configuration. It is between a track that can be placed in time but is
not the audio that was played, and a track that is the audio that was
played but arrives as one aggregate at the end of the turn.

### The wire, and what the tap's own placement costs

The inter-send distribution answers the plan's open question directly.
With the transport's stock clock: median 60.0 ms, p5 59.1 ms,
p95 60.9 ms, max 62.4 ms, and **100%** of intervals within 5 ms of the
60 ms frame cadence. Per-interval jitter is sd 1.40 ms. There is no
bursting to hide behind the simulator's receive buffering.

The clock is not exact, though: send times accumulated 58.0 ms ahead of
a perfect 60 ms clock over the run, about -27.6 ms per minute, so
126.120 s of audio left in 126.062 s of wall clock.

One figure prices the plan's own placement rule, and it belongs in the
findings so the gain numbers below are not misread as pure pipecat
error: the tap track, laid out per packet by send timestamp, scores
r = 0.755 against the uniform decode of those same packets. Placing
audio by real send times rather than by playback order costs about a
quarter of the achievable correlation, and that cost is a property of
the construction the plan froze, not of pipecat.

### The tap-injection runs

`inject.py` adds the tap-decoded track, delayed and attenuated to
-30 dB, into the microphone channel, leaving pipecat's recording as the
reference, and runs `scripts/echo_leakage.py` unmodified with an
explicit `--max-lag-s 2.0`. Both delays run offline over the same
captured pair, so the two verdicts differ only in the injection.

| reference | delay | detected | median lag | lag bias | median gain | lag IQR |
| --- | --- | --- | --- | --- | --- | --- |
| delivered | 250 ms | 125/125 | 172 ms | -78 ms | -41.8 dB | 21.8 ms |
| delivered | 1500 ms | 125/125 | 1420 ms | -80 ms | -41.4 dB | 27.4 ms |
| turn | 250 ms | 125/125 | 104.5 ms | -145.5 ms | -32.3 dB | 0.1 ms |
| turn | 1500 ms | 125/125 | 1354.5 ms | -145.5 ms | -32.3 dB | 0.1 ms |

Drift, by the statistic the plan fixed, over the 5 s to 129 s span:

| reference | delay | Theil-Sen slope | first vs last quartile | movement over span | at search boundary |
| --- | --- | --- | --- | --- | --- |
| delivered | 250 ms | +3.24 ms/min | +2.6 ms | 6.7 ms | 0 of 125 |
| delivered | 1500 ms | +3.75 ms/min | +2.0 ms | 7.8 ms | 0 of 125 |
| turn | 250 ms | -0.00 ms/min | +0.0 ms | 0.0 ms | 0 of 125 |
| turn | 1500 ms | -0.00 ms/min | +0.0 ms | 0.0 ms | 0 of 125 |

No measured lag landed at the search boundary in any run, so no figure
here is a truncated search reported as a measurement. The detectable
floor for the drift statistic on a 124 s span is set by the lag grid,
one sample at 16 kHz or 0.0625 ms, over that span: slopes below about
0.03 ms per minute cannot be distinguished from zero.

The turn track's bias is not an inference. `fidelity.py` measures the
composed turn-track reference sitting **+145.4 ms** after the composed
tap track, with r = 0.760, which matches the -145.5 ms bias the
injection runs report and the r of 0.78 they measure per window. Its
audit trail: the turn buffer is delivered 59.5 ms after the last wire
send and is 92 ms short of the audio sent, so placing it to end at its
delivery stamp starts it 94.0 ms late, and the wire clock's 58.0 ms
accumulated offset displaces the tap's per-packet placements across the
span. Both components are properties of the construction an adoption
would face.

### Gate 1: FAILED, at both delays, on both tracks

Against the plan's bar:

| criterion | bar | delivered track | turn track |
| --- | --- | --- | --- |
| detection rate | at least 90% | 100% pass | 100% pass |
| median lag | within 20 ms | -78 / -80 ms **fail** | -145.5 ms **fail** |
| median gain | within 3 dB | 11.8 / 11.4 dB **fail** | 2.3 dB pass |
| lag IQR | under 50 ms | 21.8 / 27.4 ms pass | 0.1 ms pass |
| drift | under 20 ms over span | 6.7 / 7.8 ms pass | 0.0 ms pass |

**Gate 1 fails.** Both references breach the 20 ms lag bar at both
delays, by four to seven times, and the delivered track breaches the
gain bar besides.

The shape of the failure matters more than the fact of it, and it is
exactly the shape the plan warned could not be caught by stability
criteria. The turn track's error is a **constant offset**: an IQR of
0.1 ms and a Theil-Sen slope indistinguishable from zero across two
minutes. It is not drift, not jitter, and not noise. A perfectly stable
145 ms lie is still a lie, and the plan said in advance that if a
constant bias breached the bar the finding would say exactly that,
because an adoption would face the same construction.

What that means for an adoption is concrete. Echo measurement and any
AEC reference need to know when the audio the microphone hears left the
machine. pipecat 1.7.0 records what was played but not when, and the
one track that could be timestamped is not what was played. Closing
that gap is not configuration: it needs a per-packet send timeline that
the framework does not expose, which is exactly the tap this spike had
to build, and an adopter would have to build and maintain it too.

**Deviations from the plan.** Two. The plan expected one bot track and
found two, so the table has twice the rows. And the plan's expected
failure modes were drift, jitter and buffer quantization; the measured
failure is a fixed offset, with drift and jitter comfortably inside
their bars.

**A figure the plan asked for on a pass, recorded anyway**: the
constant bias an adoption would have to calibrate out is 145.5 ms for
the faithful track, stable to 0.1 ms IQR over two minutes.

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
| `serializer.py` | 171 | 78 | the frame translation itself |
| `control.py` | 113 | 62 | the handshake and every outbound control message, which the serializer contract cannot express |
| `edge.py` | 54 | 28 | the per-connection transport parameters and construction |
| **total as built** | **338** | **168** | |
| less `_pace` | -14 | -8 | redundant: the transport paces (checkpoint finding 4) |
| **adoption-required** | **324** | **160** | |

Reported separately, outside the comparison, as the plan requires:
measurement harness, `pipeline.py` (184), `drive.py` (202),
`make_audio.py` (94), `tap.py` (107), `compose.py` (248),
`inject.py` (147), `fidelity.py` (197) and `spike_env.py` (25),
**1204** physical lines. The canned reply service, the app wiring, the
tap and the composer are all in there; none of it is counted against
either side.

### The comparable slice

The full-file comparison is 324 against 899, but the spike's exchange
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
| against the whole file, physical | 324 | 899 | 0.36 |
| against the comparable slice, physical | 324 | 222 | 1.46 |
| against the comparable slice, code-only | 160 | 155 | 1.03 |

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

The plan asks for this either way, and there are three.

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
symptom, but measurement showed that layer was unnecessary and it is
gone; that is worth recording as how easily an adoption acquires one,
not as a standing charge.

On size, the adapter is **not evidence for adoption**. It is 324 lines
against 222 for the bespoke code doing the same job, or 160 against 155
counting code only: the same size, not smaller. Adoption would not
shrink the device edge, it would relocate it, add a framework
dependency, and leave thirteen of twenty-three seam obligations still
to write.

Gate 2 therefore does not produce the evidence for adoption the issue
looked for. It is not the decisive failure; gate 1 is. But it removes
the argument that would have survived a gate 1 failure, that adoption
at least buys a much smaller edge, and the measured answer is that it
does not.

### The verdict, and what it decides

**Gate 1 failed. Gate 2 did not pass.** Per issue #89's stated
outcome, #31 is built bespoke behind the #85 boundary, and the pipecat
question is settled by evidence rather than postponed again.

Two things are worth carrying forward whatever happens to #31. The
`SessionInput`/`DeviceOutput` seam survived contact with a foreign
framework: every obligation could be named and located, and the three
that could not be expressed were expressible as findings rather than
confusion, which is what a good boundary buys. And the reason samtal's
own capture is trustworthy is now stated precisely rather than
assumed: it records the packets at the moment they are sent, on one
clock, and that property is the thing a framework has to provide, not
the recording itself.

## PR review round

One external review of the branch as first pushed to PR #90: codex CLI
0.147.0, model gpt-5.6-sol, high reasoning, read-only against the
diff to `main`, 2026-08-11. Six findings, condensed below as received,
each carrying its resolution once the commit addressing it landed.

The overall verdict accepted the delivered-track corruption and its
`_align_track_buffers` mechanism, the 93.75 ms block signature, the
Theil-Sen calculations, the protocol-v1 exchange and the primary
comparable-slice counts. It rejected the gate 1 numbers as written,
because finding 1 invalidates the placement they rest on.

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
   filter delay and the input and output sample counts recorded.
3. **P2: the gate 2 numerator subtracted an incomplete feature.** Only
   the 14-line `_pace` method was deducted while the pacing feature's
   imports, its `_paced` and `_next_send` state, its constructor
   option, its conditional call and its edge wiring stayed counted as
   ambient adapter code.
   *Resolution*: the pacing experiment is gone from the adapter
   entirely and the adapter is recounted as it now stands, with the
   as-built figure reported beside it.
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
   prints the distribution, and the claim quotes its output.
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

> **Pipecat alignment spike: both gates measured, gate 1 failed**
>
> The spike from #89 ran against **pipecat-ai 1.7.0** (xiaozhi-sdk 0.5.1, CPython 3.13.12, macOS arm64). Minimal pipeline, Silero VAD plus a canned 126 s reply clip, no LLM and no cloud, behind a xiaozhi frame serializer on pipecat's FastAPI websocket transport, driven by the unmodified device simulator. Every figure below is against that version; the transport and the audio buffer processor are both areas the project changes often, so none of it transfers to another release without re-measuring. Full detail: `docs/plans/2026-08-11-pipecat-alignment-spike-implementation.md`.
>
> **Gate 1 (capture alignment): FAILED, at both delays.** The measurement bar is the existing control's: detection in at least 90% of candidate windows, median lag within 20 ms of the injection, median gain within 3 dB, lag IQR under 50 ms, and drift under 20 ms across the span. The stock control passes exactly on the composed pair (125/125 windows, lag and gain exact, at 250 ms and 1500 ms), so the data is sound and the scripts ran unmodified.
>
> `AudioBufferProcessor` turned out to offer two bot tracks, and neither is what an echo measurement needs.
>
> - The **delivered track** (`on_track_audio_data`), the only one whose arrivals can be timestamped, is time-corrupted: 126.180 s of reply audio comes back as 154.482 s, with 24.00 s of silence inserted *inside* continuous speech in 256 blocks, 252 of them exactly 93.75 ms. Best correlation against the audio actually sent, over every admissible lag: r = 0.022. The cause is `_align_track_buffers()` padding the bot track up to the user track at every delivery, because a device streaming microphone audio during a reply makes the user track outrun it. Continuous mic streaming is not an edge case for us, it is what barge-in requires.
> - The **turn track** (`on_bot_turn_audio_data`) is faithful, r = 0.990, contiguous, no interior padding. But it arrives once, when the bot stops speaking, so it has no independently timestampable delivery point, which is the fallback finding the plan named in advance.
>
> Measured, 125 candidate windows per delay, `--max-lag-s 2.0`, no lag at the search boundary:
>
> | reference | delay | detected | median lag | bias | median gain | lag IQR | drift over span |
> | --- | --- | --- | --- | --- | --- | --- | --- |
> | delivered | 250 ms | 125/125 | 172 ms | -78 ms | -41.8 dB | 21.8 ms | 6.7 ms |
> | delivered | 1500 ms | 125/125 | 1420 ms | -80 ms | -41.4 dB | 27.4 ms | 7.8 ms |
> | turn | 250 ms | 125/125 | 104.5 ms | -145.5 ms | -32.3 dB | 0.1 ms | 0.0 ms |
> | turn | 1500 ms | 125/125 | 1354.5 ms | -145.5 ms | -32.3 dB | 0.1 ms | 0.0 ms |
>
> The failure is a **constant offset, not drift or jitter**: the faithful track's lag is stable to a 0.1 ms IQR and a Theil-Sen slope indistinguishable from zero over two minutes, and wrong by 145.5 ms. That is the failure mode we said in advance the stability criteria could not catch. An independent measurement of the composed reference against the composed tap track puts the offset at +145.4 ms, matching.
>
> The transport itself is fine: it paces at real time (median inter-send 60.0 ms, 100% of intervals within 5 ms of the 60 ms cadence), which corrects a wrong reading we made early from the source. What it does not do is record *when* audio left. pipecat 1.7.0 records what was played but not when, and the track that can be timestamped is not what was played.
>
> **Gate 2 (adapter size and shape): not passed.** Adoption-required adapter, `wc -l`: **324 lines** (serializer 171, control processor 113, edge wiring 54, less a 14-line pacing layer that measurement proved redundant). Against the bespoke edge at base commit `891e257`, 899 physical lines (883 in the issue's snapshot), that is 0.36. But against the **comparable slice**, the bespoke responsibilities this exchange actually exercises, it is **324 against 222**, or 160 against 155 counting code only: the same size, not smaller.
>
> The adapter buys **8 of the 23** `SessionInput`/`DeviceOutput` obligations. Two more are mapped but never exercised, and **13 are required and not implemented**, including `pause_output`/`resume_output`, `speaking_started_at`, `drain`, `flush_encoder` and the device tools. Shape is clean: it stayed message translation, no duplicated framework state, no per-reply state machine. Three things the seam could not express inside the serializer: it cannot originate a message (so the hello became a processor), it never sees outbound control frames (so the whole `tts`/`stt` surface became a processor), and `encode_audio` returns a batch where `serialize` returns one payload.
>
> **Decision.** Per #89's stated outcome, gate 1 failing means **#31 is built bespoke behind the #85 boundary**. Gate 2 removes the argument that would have survived a gate 1 failure, that adoption at least buys a much smaller edge; measured, it does not. The observability constraint from #84 stands regardless.
>
> Two things carry forward. The `SessionInput`/`DeviceOutput` seam survived contact with a foreign framework: every obligation could be named and located, and the three it could not express came out as findings rather than confusion. And why our own capture is trustworthy is now precise rather than assumed: it decodes the packets at the moment they are sent, on one clock. That property is what a framework has to provide, and this one does not.
