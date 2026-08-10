# Normalize the hardware edge with a device-facing boundary

**Status:** Accepted

## Context

The evaluation in
[#84](https://github.com/rafacm/samtal/issues/84) asked what samtal
should own as conversation runtimes multiply: a pipecat pipeline, a
native realtime session, and the bespoke `VAD -> ASR -> LLM -> TTS`
pipeline that exists today. The answer it reached is that the unusual
thing samtal exists to normalize is the xiaozhi hardware edge, not the
AI middle.

The code said otherwise. `samtal_server/session.py` had grown to 2,138
lines in one class that owned both sides at once: the hello handshake,
Opus decode and encode, binary framing, frame pacing with its
pause/resume clock, the device MCP transport, session limits and the
idle watchdog, and, in the same object and often in the same method,
the endpointer and the utterance buffer, the barge-in gate ladder, the
filler runner, the LLM tool loop, conversation history, agent handover,
and the TTS sentence lookahead. Nothing separated a fact about the wire
from a decision about the conversation, so a second runtime could only
arrive by copying the device half or by wrapping the whole class.

The streaming transcription rewrite
([#31](https://github.com/rafacm/samtal/issues/31)) was queued to land
next, and it rewrites exactly the endpointing behavior. Landing it
inside `session.py` would deepen the entanglement in the same file the
next runtime would have to unpick.

The litmus test for which side a piece of code belongs on is on the
[principles page](../architecture/principles.md): **would this code
still exist if the backend were a telephone call to a human?** Opus
decoding, pacing, display text and device tool transport survive that
question. Sentence splitting, context management, and deciding when TTS
may begin do not.

## Decision

Interfaces at samtal's core describe device capabilities, not how AI
systems work. The xiaozhi edge is normalized once, behind an explicit
interface pair, and conversation runtimes stay themselves behind it.

Concretely:

- `SessionInput` is what a runtime offers the device edge: decoded mic
  audio, listen started and stopped, a device abort, whether a reply is
  in flight, a drain, and a close. `DeviceOutput` is what the device
  offers a runtime: show a transcript, begin speaking, announce a
  sentence, encode and send audio, finish speaking, pause and resume
  the paced stream, report the speaking stamp, report the end of the
  user's turn, and call the device's own tools.
- There is no universal `ConversationBackend`. The pair is device-shaped
  in both directions, and a runtime is admitted by implementing
  `SessionInput`, not by fitting a stage model. Runtimes are siblings,
  never providers of one another.
- The decision sites stay in samtal's own runtime components. The gate
  ladder and the filler live behind the boundary, in samtal's
  `PipelineRuntime`, and keep emitting `barge_in`,
  `barge_in_suppressed`, `barge_in_merged`, `filler_played` and
  `filler_skipped` with their reasons, because those reasons are the
  observability surface
  ([ADR](2026-08-04-json-logs-are-the-observability-surface.md)).
- Playable audio crosses the boundary as an opaque batch. The runtime
  hands PCM to `encode_audio` and receives a `PlayableAudio` it may
  test for emptiness and concatenate, and never reads. The runtime
  therefore never learns Opus, while the edge keeps the one fact the
  runtime does need: whether a chunk produced anything to play, which
  is what the filler arbitration turns on.
- Observability crosses as `SessionEvents`, not as a boundary method.
  It owns the session logger, pinned by name to `samtal_server.session`
  so the `logger` field of every JSON record survives the move, the
  structured event builder, the capture decision-track hooks, and the
  active-agent attribution both sides read.
- The runtime is built by a factory at the composition root, not by the
  device session. `app.py` closes over the providers, MCP servers,
  memory store and filler clips at startup and stores a
  `RuntimeFactory`; the edge calls it with a `DeviceOutput`, a
  `SessionEvents` and the device's bound agent names, and never learns
  what an LLM is.

The boundary is inline awaited calls, not a frame queue. Ordering and
backpressure are therefore unchanged: the gate ladder's confirmation
ASR still runs in the receive path, and incoming frames still buffer in
the socket meanwhile.

## Consequences

- The streaming transcription rewrite (#31) lands runtime-side, against
  `SessionInput`, and can change endpointing behavior without touching
  a line of wire handling.
- The #84 spike's serializer implements this same boundary, so its size
  is comparable evidence rather than an argument about two different
  shapes.
- The device edge is a compatibility surface with one implementation
  and a stated floor: stock xiaozhi firmware. Nothing behind the
  boundary can break that floor without going through the edge.
- `DeviceGone` subclasses `RuntimeError` on purpose. Every site that
  must swallow a vanished device already catches `RuntimeError`
  broadly, so the translation wraps rather than narrows, and the
  accepted consequence is that a broad catch also swallows a vanished
  device, exactly as it does with the
  `(WebSocketDisconnect, RuntimeError)` pair today.
- There is no device-side output queue beyond the frame in flight,
  because pacing awaits inline. "Cancel queued output" is therefore
  realized as the runtime cancelling its own reply task (which abandons
  the unsent remainder of the batch in flight) plus resetting the pause
  state, not as a flush primitive on the boundary.
- The trap to refuse, named here so a later pull request has to argue
  against it: the boundary sprouting runtime-shaped methods
  (`commit_audio`, `set_turn_detection`, `truncate_response`), or
  `PlayableAudio` growing introspection. Either is the
  lowest-common-denominator realtime API arriving one reasonable
  method at a time.
- Runtime selection is deliberately not configuration yet. One runtime
  exists, and a selection mechanism with one option is surface without
  a reader; the factory is the seam a second runtime plugs into, and
  what selection needs to express is decided when there is a second
  one.
