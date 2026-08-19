# Principles

The standing fundamentals of vinga: what the project promises, where
its boundaries lie, and what it must not become. Read this before
designing a feature or deciding direction, so a boundary is never
crossed one reasonable-looking pull request at a time.

The page holds two kinds of principle, and the distinction carries a
precedence rule. **Product promises** are commitments to the person
running vinga, falsifiable from outside; breaking one does not
refactor vinga, it changes what vinga is. **Architecture
principles** are how the code keeps those promises; given new
evidence, any of them can be revised, provided the promises still
hold. When the two conflict, the promise wins.

Issues hold evidence, ADRs hold decisions, plans hold execution, and
this page holds direction. It is an index, not a replacement: each
principle cites the decision record or issue where its reasoning
lives, and a principle whose supporting decision has not been recorded
yet cites the issue that tracks it. An architecture principle changes
the way any hard-to-reverse decision does, through a new record in
[`../adr/`](../adr/README.md); a product promise changes the same way,
but only as a deliberate product decision, and rarely. This page
updates to cite the record in the same change.

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

## Product promises

### Stock xiaozhi firmware is the compatibility floor

An ESP32-S3 board running upstream xiaozhi firmware, pointed at a
vinga server, holds a conversation without a reflash. If vinga
ships its own firmware one day, that raises the ceiling, never the
floor: protocol extensions are additive and negotiated, the server
never requires vinga firmware for ordinary conversation, and vinga
firmware never drifts into a private dialect a stock board cannot
join.

The promise is bounded three ways, and the bounds are part of it:

- It covers the transport vinga implements: the WebSocket channel.
  Upstream also speaks an MQTT-plus-UDP pairing; vinga does not
  promise every transport upstream carries.
- Its version target is the firmware actually running on boards in
  the field. Upstream protocol changes are absorbed as shipped
  devices adopt them, not chased at upstream's commit log.
- It is a floor, not a ceiling: ordinary conversation, not every
  vinga feature.

**Example.** Onboarding a stock board is repointing one NVS
`ota_url` entry at the vinga server
([xiaozhi-notes](../xiaozhi-notes.md)); everything after that is the
standard OTA fetch and hello exchange.

**Counterexample.** "Cleaning up" the hello exchange in a way stock
firmware does not parse; or a vinga-firmware-only message becoming
load-bearing for ordinary conversation, so stock boards quietly stop
being full citizens.

The promise has a named cost, paid knowingly: server features are
constrained to what stock firmware can express, and some rough edges
(onboarding a device by typing a long OTA URL on a phone) are the
price of meeting devices where they are.

Evidence and tradeoffs:
[issue #84](https://github.com/rafacm/vinga/issues/84).

### A fully local deployment is first-class

Every core conversational capability is reachable with local
providers; a cloud provider is an upgrade, never a requirement. A
`server.local_only: true` server that starts can hold a complete
conversation, the way the original all-local chain (Silero,
faster-whisper, Ollama, Piper) did from the beginning.

The enforcement mechanism is declared egress: every provider declares
whether it sends session data off the host, and `local_only` refuses
at startup to build one that does. A provider type that cannot answer
for itself must say so explicitly (an OpenAI-compatible base URL is
equally a vendor or an Ollama on localhost, so the configuration
states which). The guarantee is enforced, not documented.

**Example.** An inherently-cloud runtime (a native realtime session)
arriving as a sibling runtime is fine and expected; the local
pipeline remains complete without it.

**Counterexample.** A core capability (memory, end-of-turn detection,
barge-in quality) implemented only against a cloud API, so local
deployments drift into the second-class configuration nobody chose to
demote. Nobody would delete the local path; features would just stop
landing on it. Also: assuming locality from a provider's shape, or a
new provider type skipping the egress declaration because it is
"obviously" local; an undeclared provider is a hole in the guarantee.

### Thin device, smart server

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

## Architecture principles

### Normalize the hardware edge, not the AI middle

The xiaozhi protocol is the unusual thing vinga exists to normalize.
Conversation runtimes (the bespoke pipeline, a pipecat pipeline, a
native realtime session) are allowed to remain themselves behind that
edge.

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

Decision:
[ADR](../adr/2026-08-10-normalize-the-hardware-edge.md). Evidence and
tradeoffs: [issue #84](https://github.com/rafacm/vinga/issues/84).

### The internal boundary is device-facing

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

Decision:
[ADR](../adr/2026-08-10-normalize-the-hardware-edge.md). Evidence and
tradeoffs: [issue #84](https://github.com/rafacm/vinga/issues/84).

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

### Own the decision sites, and give every decision a reason

The most diagnostic events in the system exist because vinga owns
the code that makes the decision and annotates it with why. Whatever
framework runs the pipeline, the interesting decision sites stay in
vinga's own components so their reasons keep flowing into the
structured log, which is the observability surface
([ADR](../adr/2026-08-04-json-logs-are-the-observability-surface.md)).
The surface carries metadata, never conversation content, which lives
in its own store
([ADR](../adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md)).

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
