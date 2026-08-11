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

**No, and this is the spike's first load-bearing finding.**
`write_audio_frame` sends the chunk and then awaits
`_write_audio_sleep`, whose interval is set in `start()` as

```python
self._send_interval = (self.audio_chunk_size / self.sample_rate) / 2
```

that is, **half** the chunk's own duration. `audio_chunk_size` is
`audio_out_10ms_chunks` (default 4) times 10 ms of audio, so the
default is a 40 ms chunk every 20 ms. Whatever the chunk size, a
backlog of TTS audio therefore leaves the socket at twice real time,
and the docstring is explicit that the sleep exists "to emulate an
audio device", not to reproduce a playback clock.

The consequence for the measurement is not a detail. There is no
wall-clock timeline on which twice-real-time audio can be laid out:
two minutes of reply occupies one minute of wall clock, so successive
packets' decoded spans overlap by half their duration, and any capture
built on send timestamps is self-overlapping rather than merely
skewed. samtal's capture is trustworthy precisely because its packets
are paced at 60 ms and recorded as they are sent; pipecat's transport
supplies no such clock.

The plan anticipated this ("the spike either adds
production-representative pacing and counts it as adapter glue in gate
2, or records the absence as gate evidence"). The spike does both: it
measures the stock inter-send distribution as gate 1 evidence, and it
adds 60 ms pacing to the serializer for the measurement capture,
counted as adapter glue in gate 2. *Confirmed live; the measured
distributions are in milestone 3.*

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
adapter code that the framework does not absorb: a handshake
processor and, because of finding 4, a pacing layer.

### What was built

(filled in with the exchange itself)

## Milestone 2: instrumentation and a well-formed pair

## Milestone 3: the alignment verdict

## Milestone 4: the size verdict and the paper trail
