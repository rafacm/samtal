# Architecture guidelines

How vinga's code keeps [the product promises](product-promises.md),
and where its internal boundaries lie. Read this before designing a
feature or deciding direction, so a boundary is never crossed one
reasonable-looking pull request at a time.

A guideline is revisable. Given new evidence any of them can change,
provided the promises still hold, and when a guideline and a promise
pull in different directions the promise wins. That is the difference
that matters between this page and that one: a promise is a
commitment to the person running vinga, a guideline is this project's
current best answer for keeping it.

Each guideline carries one example and one counterexample, because a
rule with neither is a rule nobody can apply to the change in front of
them. Where a guideline has a real condition that would reopen it,
that condition is written down under **Reconsider when**; the ones
without such a heading have no known trigger, not a hidden one. Each
cites the decision record or the issue where its reasoning lives, so
this page stays an index rather than a replacement for either. A
guideline changes the way any hard-to-reverse decision does, through a
new record in [`../adr/`](../adr/README.md), and this page updates to
cite that record in the same change.

"Must" and "never" are reserved here for protocol requirements and
security invariants. Everywhere else the guidelines say "prefer" and
"normally", which is what they mean.

## On this page

- [Identity](#identity): what vinga owns and what a conversation
  runtime owns, the umbrella the rest of the page sits under.
- [Thin device, smart server](#thin-device-smart-server): why the
  intelligence stays server-side and firmware work needs a reason.
- [Normalize the hardware edge while runtimes keep their native
  execution model](#normalize-the-hardware-edge-while-runtimes-keep-their-native-execution-model):
  where the seam sits, what its interface talks about, and why
  runtimes plug in beside each other.
- [Give every decision a reason, and know whose reason it
  is](#give-every-decision-a-reason-and-know-whose-reason-it-is):
  closed reasons for the decisions vinga owns, a runtime's own
  diagnostics for the decisions it owns.
- [A framework may own the shared pipeline, not vinga's own
  semantics](#a-framework-may-own-the-shared-pipeline-not-vingas-own-semantics):
  which half of the pipeline an adoption would actually replace, and
  what would reopen the question.

## Identity

vinga makes xiaozhi-class open hardware a first-class endpoint for
conversational AI runtimes. vinga owns the appliance: discovery and
OTA, device authentication, the xiaozhi WebSocket protocol, Opus
framing, the display, device-side tools, and eventually the firmware
experience. Conversation runtimes own the conversation: turn
orchestration, model streaming, context, tool loops, and when speech
synthesis may begin.

The identity is deliberately not "another pluggable VAD/ASR/LLM/TTS
server". The bespoke pipeline built exactly that, and building it
taught which concerns are inherently vinga's and which are generic
conversational infrastructure. That knowledge, not the pipeline, is
the durable asset.

When vinga explains itself, the mix leads: every stage the server
runs is a slot, and which service fills it is the user's choice,
stage by stage, revisable at will. Self-hosting is the ground that
makes the choice real rather than the headline, and a fully local
deployment is the limiting case of the blend. This is an emphasis
about the promise, not a soft retraction of the paragraph above: the
user is promised choice among engines, and vinga keeps that promise
by hosting engines behind stable slots, never by becoming a pipeline
framework
([decision record](../adr/2026-09-05-pluggability-leads-self-hosting-supports.md)).

Every guideline below is a consequence of that split, which is why it
opens the page rather than sitting among them.

## Thin device, smart server

The intelligence lives server-side, so behavior improves without
reflashing boards and cheap stock hardware stays sufficient. The
boards run upstream xiaozhi firmware with `vinga-esp32` as a thin
customization. Firmware work is undertaken when it enables something
vinga-specific, never because vinga happens to own the server.
Firmware has a proven ability to consume all available project time.

**Example.** Protocol extensions the server-side experience actually
needs: richer device events, better provisioning, display semantics,
latency instrumentation.

**Counterexample.** Taking over board support or the device audio
pipeline from upstream because owning more of the stack feels tidier.
That trades upstream's maintenance of dozens of boards for no
user-visible gain.

## Normalize the hardware edge while runtimes keep their native execution model

The xiaozhi protocol is the unusual thing vinga exists to normalize.
Conversation runtimes (the bespoke pipeline, a pipecat pipeline, a
native realtime session) are allowed to remain themselves behind that
edge. Three subrules follow from that, and they are one guideline
because each of them is unsafe to apply without the other two.

### The seam sits at the device boundary

Normalization happens where the hardware protocol stops, not in the
middle of the AI stack.

**Example.** A xiaozhi frame serializer that translates hello, listen
and tts messages plus binary Opus frames into plain audio and a small
set of semantic events. The runtime never learns what
`{"type": "tts"}` means; the device never learns what a pipeline
frame is.

**Counterexample.** Forcing a native speech-to-speech API such as
OpenAI Realtime through the `ASR -> LLM -> TTS` provider interfaces.
A realtime session owns turn detection, transcription, reasoning,
tool calls and voice as one stateful whole; transcripts are a side
effect, audio arrives before the response text is complete, and the
session truncates its own reply on interruption. Modeling that as
three providers glued together produces an abstraction that is leaky
on day one and false by design.

### The internal interface is device-facing

Interfaces at vinga's core describe what the device needs (audio in
and out, user and assistant speaking state, display text, device tool
calls, cancel current output), not how AI systems work (ASR, LLM,
TTS, prompts, context windows).

The litmus test for which side code belongs on: **would this code
still exist if the backend were a telephone call to a human?**

| Would still exist (vinga)     | Would not (runtime)                 |
| ------------------------------ | ----------------------------------- |
| decode xiaozhi Opus packets    | split LLM output into sentences     |
| authenticate a device          | route transcripts into the LLM      |
| OTA and configuration          | manage LLM context                  |
| show text on the display       | aggregate streamed tokens           |
| set speaker state, volume      | decide when TTS may begin           |
| handle device reconnect        | run the tool-call loop              |
| map device IDs to sessions     | decide interruption's consequences  |

**Example.** On a confirmed barge-in, the device layer flushes
outgoing audio and tells the device playback stopped. That much is
vinga's job under any runtime.

**Counterexample.** The same barge-in handler also cancelling the LLM
task, cancelling TTS, and deciding whether the half-spoken reply
enters conversation history. Those are the interruption's
conversational consequences, and they belong to the runtime; owning
them in the device layer is how a transport grows back into a
framework.

### Runtimes are siblings, not providers

A conversation runtime plugs in beside the others behind the
device-facing boundary; it is never wrapped as one more provider
inside another runtime's stage model, and vinga never defines a
universal interface all runtimes must fit. The
lowest-common-denominator realtime API is the trap: it either grows
into a home-grown, slightly wrong copy of every runtime's session
protocol, or it discards exactly the provider-specific capabilities
that were the reason to integrate that provider.

**Example.** Per-runtime configuration shapes. A pipecat runtime is
configured with stt, llm and tts sections; a native realtime runtime
with a model, a voice and turn-detection settings. The asymmetry is a
feature: it admits these are different execution models instead of
pretending one schema fits both.

**Counterexample.** A `ConversationBackend` interface that pipecat,
OpenAI Realtime and the bespoke pipeline must all implement. It
starts as `send_audio()` and `events()`, then sprouts
`commit_audio()`, `cancel_response()`, `truncate_response()`,
`set_turn_detection()`, until vinga maintains its own slightly
different version of everyone's realtime protocol.

Decision:
[ADR](../adr/2026-08-10-normalize-the-hardware-edge.md). Evidence and
tradeoffs: [issue #84](https://github.com/rafacm/vinga/issues/84).

## Give every decision a reason, and know whose reason it is

The most diagnostic events in the system exist because vinga owns the
code that makes the decision and annotates it with why. So the
question a decision site raises first is whose it is, and the answer
settles what diagnosis will have to work with.

A decision vinga owns emits a reason drawn from a closed set into the
structured log, which is the observability surface
([ADR](../adr/2026-08-04-json-logs-are-the-observability-surface.md)):
one event variant per reason, so the set is enumerable in
[`../reference/events.md`](../reference/events.md) rather than
discovered by grep. The surface carries metadata, never conversation
content, which lives in its own store
([ADR](../adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md)).

A decision a runtime owns is diagnosed with whatever that runtime
supplies, and vinga's job is to expose it rather than to reproduce
it. This is the cost side of letting runtimes keep their native
execution model, and it is why the interesting decision sites are
normally kept in vinga's own components: a reason vinga does not
compute is a reason vinga cannot promise to log.

**Example.** `barge_in_suppressed` carrying a reason and `speech_ms`,
and `filler_skipped` carrying why the clip stood down. Field-test
round 2's filler diagnosis was possible because those reasons were in
the events
([ADR](../adr/2026-08-05-replies-cancel-only-on-evidence-of-speech.md)).

**Counterexample.** Adopting a stock framework component as-is for a
decision that will someday need field diagnosis. An observer sees the
frames a component emits; a decision that emits no frame is
invisible, and "it interrupted, reason unknown" is not a debuggable
event.

## A framework may own the shared pipeline, not vinga's own semantics

The conversation pipeline splits into two buckets, and which bucket a
part falls in decides whether a framework could own it.

**The shared bucket** is what any streaming voice framework provides:
frame flow and orchestration, VAD and endpointing wiring,
[output pacing](../glossary.md#output-pacing),
[capture](../glossary.md#capture), and latency metrics. This is the
part an adoption would replace, and it is stable, small, and
measured: the pacing clock is edge-owned and reset per reply, and the
capture passes its own synthetic-echo controls
(`scripts/echo_leakage_control.py`).

**The owned bucket** is what no framework has: the
[gate ladder](../glossary.md#gate-ladder) semantics (transcript
confirmation, merge-mid-ASR, the
[refractory period](../glossary.md#refractory-period)), the
[filler](../glossary.md#conversational-filler) with its
yield-to-live-speech rule, reason-annotated decision events
(`barge_in_suppressed` with a reason, `filler_skipped` with a
reason), and the
[wire-true capture](../glossary.md#wire-true-capture) property. Under
any adoption these port as custom processors, so they are a
relocation cost, never a saving, and the decision-reasons guideline
above requires them to stay self-owned wherever they run.

That is also the pattern to watch as the code grows: bespoke growth
that duplicates a framework argues for adoption, bespoke growth in
semantics no framework has argues against it, and so far it has been
the second.

The standing answer, recorded on
[issue #84](https://github.com/rafacm/vinga/issues/84) (evaluate
adopting pipecat as the pipeline framework), is not now. The
measurements it rests on are dated and pinned to one pipecat release,
so they live with the spike that produced them,
[the alignment spike record](../plans/2026-08-11-pipecat-alignment-spike-implementation.md)
and the evidence comment on #84, and are re-measured rather than
carried forward.

**Example.** Taking VAD and endpointing wiring from a framework:
shared bucket, so the only question is whether it fits. The moment
that same component also decides whether a half-spoken reply is
cancelled, it has crossed into the owned bucket and owes the argument
this guideline asks for.

**Counterexample.** Reaching for a framework because the bespoke code
has grown, without asking which bucket it grew in. A total line count
cannot tell the two kinds of growth apart, and they point opposite
ways.

### Reconsider when

- **#31 goes active.** A bespoke event-driven streaming ASR pipeline
  is re-implementing a framework's core rather than its periphery,
  which is the line #84 draws.
- **A second runtime is actually wanted** (#92, stage 2). pipecat
  would arrive as a sibling runtime behind
  `SessionInput`/`DeviceOutput`, per-device selectable, never as a
  backend swap, per the sibling-runtimes subrule above.
- **#81 reaches its v2 stage** (continuous end-of-turn prediction
  over streaming input), which is itself sequenced behind #31. Its
  earlier stages deliberately need no framework: smart-turn v3 is
  consumable as a standalone ONNX model.
