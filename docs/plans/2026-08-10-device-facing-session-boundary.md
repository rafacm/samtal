# Device-facing session boundary plan

## Goal

Extract the device-facing boundary from `samtal_server/session.py`
(2,138 lines) as an explicit interface pair, per issue #85: a session
input surface the device edge feeds, and a device output surface the
runtime drives. The bespoke pipeline becomes the first conversation
runtime behind that boundary by construction, not by wrapping. Pure
refactor: no behavior change, the existing tests keep passing, and the
JSON event stream keeps the same event names, fields, and reasons,
including the `logger` field on every record (see below).

The design constraints are fixed by
[the principles page](../architecture/principles.md) and issue #85;
this plan is the concrete how. The litmus test for which side any
piece of code lands on: would it still exist if the backend were a
telephone call to a human?

Sequencing: this lands before #31 (the streaming transcription
rewrite, which must land against this boundary, not inside
`session.py`), and it is independent of the turn-taking cluster
(#28, #80, #81); those tune decisions, this moves walls, and nothing
here changes a threshold, a gate order, or a filler rule.

A companion implementation doc,
`2026-08-10-device-facing-session-boundary-implementation.md`, will be
created during implementation, one section per milestone, recording
deviations and discoveries as usual.

## Current structure of session.py, honestly

One class, `Session`, owns both sides plus everything between:

- **Device edge**: the hello handshake and its rejections, close
  codes, MAC normalization and agent-binding lookup, the serve loop,
  Opus decode of mic frames and encode of reply frames, binary
  framing, `listen`/`abort`/`mcp` message dispatch, the `stt` and
  `tts start`/`sentence_start`/`stop` messages, outgoing frame pacing
  with its pause/resume clock, the device MCP transport
  (`DeviceToolClient` discovery and envelopes), session limits, the
  idle watchdog, and `request_shutdown` (the drain's entry point).
- **Runtime middle**: the endpointer and the utterance tail buffer
  with its trim, ASR calls and the session language lock, the LLM
  tool loop with its round cap and first-token watchdog, sentence
  splitting, the TTS sentence lookahead (`_Synthesis`), per-agent
  resamplers, conversation history (`_turns`), agent activation and
  handover, memory injection, and tool dispatch across builtins,
  device tools, and MCP servers.
- **Owned decision sites**: the gate ladder (`_gate_barge_in`, with
  the mid-ASR merge marker `_reply_pcm` and the refractory window)
  and the filler (arm, fire, yield, tail, settle).
- **Cross-cutting**: structured events via `_event` (which also feeds
  the capture's decision track), and the capture hooks for mic audio,
  paced reply audio, VAD samples, and dropped frames.

## Target layout

```
samtal_server/device/
    __init__.py
    boundary.py     SessionInput and DeviceOutput protocols,
                    PlayableAudio, DeviceGone, RuntimeFactory,
                    PIPELINE_SAMPLE_RATE
    events.py       SessionEvents: the pinned session logger,
                    structured events, the capture decision-track
                    hook, and the active-agent attribution field
    session.py      DeviceSession: handshake, serve loop, codecs,
                    framing, pacing and its pause clock, capture,
                    idle watchdog, limits, shutdown, device MCP
                    transport, listen-mode policy
samtal_server/runtime/
    __init__.py
    pipeline.py     PipelineRuntime and its factory: endpointer and
                    utterance buffer, gate ladder, filler runner,
                    ASR/LLM/TTS orchestration, tool loop, history,
                    handover
    speech.py       _Synthesis and the sentence lookahead machinery
```

`samtal_server/session.py` is removed, not shimmed: `ws.py` and the
tests update their imports in the same commits that move the code.
`filler.py` (the boot-time clip cache builder) stays where it is; it
is provider plumbing run at startup, not session machinery, and
moving it buys nothing. `capture.py`, `protocol/`, `audio/`,
`providers/`, `tools/`, `auth.py`, `ota.py` are untouched. Files
that change beyond the moves themselves, exhaustively: `ws.py` (one
import, session construction), `app.py` (builds the runtime factory,
below), and `registry.py` (its `TYPE_CHECKING` import of
`samtal_server.session.Session` at line 18 follows the class to
`samtal_server.device.session`).

## Composition root and lifecycle

The device edge must not depend on pipeline machinery, so the
runtime's dependencies do not pass through `DeviceSession`:

- `runtime/pipeline.py` exports
  `bespoke_runtime_factory(config, agent_providers, mcp_servers,
  memory, fillers) -> RuntimeFactory`. The returned callable has the
  boundary-level signature

  ```python
  RuntimeFactory = Callable[
      [DeviceOutput, SessionEvents, Sequence[str]],
      SessionInput,
  ]
  ```

  where the third argument is the device's bound agent names.
- `app.py` builds it at startup, after the objects it closes over
  exist: the mutable `agent_fillers` dict (created empty, filled by
  the lifespan, exactly the object `ws.py` hands to every session
  today), the MCP servers, and the memory store all precede the
  factory, which is then stored as `app.state.runtime_factory`.
- `DeviceSession(websocket, config, runtime_factory, captures=None,
  device_facts=None)` is the new constructor; `ws.py` passes
  `state.runtime_factory`. The edge keeps `config` (limits, barge-in
  switches for its guards, agent bindings, capture and manifest
  fields, which are read from configuration, never from provider
  objects).

Construction point and cleanup preserve today's `run()` ordering
exactly (accept, normalize MAC, resolve agents, activate the first
agent, revive MCP servers, hello, capture, server hello,
`session_open`, discovery, watchdog, serve):

- Bad MAC and no-agent rejections return before the factory is
  called; no runtime ever exists on those paths, as today nothing
  agent-shaped has been built yet beyond `_activate_agent`, which
  the factory call now subsumes.
- The factory is called where `_activate_agent(agents[0])` runs
  today: after the agent list resolves, before the hello. The
  runtime's constructor performs the initial activation (creating
  the first endpointer) and the MCP `revive`, in that order, and
  spawns no tasks; that is true of today's code too (the reply task
  is created on the first utterance, discovery is edge-owned), and
  it is what makes the next rule safe.
- A bad or missing hello returns without calling
  `SessionInput.close()`, matching today's early return before the
  `try`/`finally`. `close()` is called exactly where
  `_cancel_reply()` runs today: in the serve region's `finally`,
  after the idle watchdog stops and before discovery stops, the
  `session_closed` log, and the capture close.

This is deliberately not a config-selectable runtime registry: one
runtime exists, and a selection mechanism with one option is surface
without a reader. The factory is the seam the #84 spike and a native
realtime bridge later plug into.

## The interface pair

Both live in `samtal_server/device/boundary.py`, as
`typing.Protocol`s (runtime-checkable, so tests can assert
conformance). All PCM crossing the boundary is s16le mono. The
boundary is inline awaited calls, not a frame queue: `audio()` is
awaited from the serve loop, which preserves today's ordering and
backpressure exactly (the gate ladder's confirmation ASR still runs
in the receive path, and incoming frames still buffer in the socket
meanwhile). This is a seam, not a pipeline.

### SessionInput, fed by the device edge

```python
class SessionInput(Protocol):
    """One conversation runtime behind the device edge. PCM is
    s16le mono at PIPELINE_SAMPLE_RATE (16 kHz)."""

    async def audio(self, pcm: bytes) -> None:
        """One decoded mic frame. Only called while the device is
        listening and the edge's guards passed."""

    async def listen_started(self) -> None:
        """The device asked to listen: reset utterance state. The
        listening mode is edge policy and does not cross."""

    async def listen_stopped(self) -> None:
        """Manual end of utterance. Mid-reply this is a deliberate
        act and cancels unconditionally."""

    async def device_aborted(self, reason: str | None) -> None:
        """Device abort: cancel the reply, reset the utterance."""

    def replying(self) -> bool:
        """A reply is in flight, generating or speaking."""

    async def drain(self, grace_s: float) -> bool:
        """Wait for a reply in flight to finish, whether it is
        already speaking or still generating; never cancels it,
        and swallows its failure (a reply that failed is a reply
        that finished). True when it finished within grace_s, or
        when none was in flight."""

    async def close(self) -> None:
        """The session is over: cancel the reply, release state."""
```

Device tool results deliberately do not cross this surface as
events. The MCP envelope transport stays on the device edge
(`DeviceToolClient` speaks over the websocket); the runtime sees
only `ToolDef` and a `(content, is_error)` answer through the output
surface below. `replying()` is a query the edge's own jobs need (the
barge-in-off frame guard, the idle watchdog), and it passes the
litmus: whether the far end is mid-answer is knowable on a phone
call too.

### DeviceOutput, driven by the runtime

The output side cannot be a single `send_audio(pcm)`: the Opus
encoder buffers partial 60 ms frames (`audio/opus.py`), so a PCM
chunk may yield no packet at all (the mock TTS yields 20 ms
chunks), and today's `_send_frames` returns before touching the
filler when nothing playable was produced. The filler arbitration
is a runtime-owned decision site, so the runtime must know whether
a chunk became playable before it arbitrates. The boundary
therefore hands back an opaque batch:

```python
class PlayableAudio:
    """An opaque batch of encoded, ready-to-send device audio.
    Produced and consumed only by the device edge; the runtime may
    test it for emptiness (__bool__) and concatenate batches
    (__add__), and never reads the contents. This is the local
    `packets` list of today's code, given a name; it is not a
    queue, and it never grows runtime-shaped methods."""
```

```python
class DeviceOutput(Protocol):
    """What the device can do for a runtime. Implemented by
    DeviceSession. Async methods that reach the socket raise
    DeviceGone when the device has disconnected."""

    @property
    def output_sample_rate(self) -> int:
        """The rate encode_audio expects (24 kHz today)."""

    async def show_transcript(self, text: str) -> None:
        """The 'stt' message: what the user was heard to say."""

    async def begin_speaking(self) -> None:
        """The 'tts start', once per reply, idempotent."""

    async def sentence_started(self, text: str) -> None:
        """The 'tts sentence_start': display what is about to be
        heard."""

    def encode_audio(self, pcm: bytes) -> PlayableAudio:
        """Feed reply PCM at output_sample_rate into the edge's
        Opus encoder; the batch holds every packet that filled,
        possibly none. Synchronous, sends nothing."""

    def flush_encoder(self) -> PlayableAudio:
        """Pad the encoder's pending partial frame with silence
        and encode it. Called at the end of every agent leg and
        after a filler clip, exactly as today; never called on
        cancellation, and the encoder object is never reset
        between replies (its few milliseconds of lookahead stay
        inside, which is what keeps it reusable)."""

    async def send_audio(self, batch: PlayableAudio) -> None:
        """Pace the batch out at frame cadence, recording each
        packet on capture channel 1 as it goes. On the reply's
        first non-empty batch, stamps and emits speaking_started
        (attributed via SessionEvents.agent) BEFORE the first
        pacing sleep, the pause-gate wait, and the socket send,
        exactly as today. A cancel mid-batch abandons the unsent
        remainder, as today's local list did."""

    async def finish_speaking(self) -> None:
        """End of reply: 'tts start' if none was sent, then
        'tts stop'; marks conversational activity for the idle
        watchdog. A reply that never spoke still sends the pair."""

    def reply_started(self) -> None:
        """A new reply: reset per-reply speaking state (the
        started flag, the stamp, the tts-start latch). Does not
        touch the encoder."""

    def restart_pacing(self) -> None:
        """A new agent leg: restart the pacing clock."""

    def pause_output(self) -> None:
        """Hold the paced stream before its next frame."""

    def resume_output(self) -> None:
        """Resume, shifting the pacing clock by the pause so the
        stream picks up where it stopped."""

    def speaking_started_at(self) -> float | None:
        """The speaking_started stamp for this reply; None before.
        Read by the refractory gate and the filler."""

    def user_turn_ended(self) -> None:
        """The runtime decided the utterance ended. The edge
        applies its listen-mode policy: auto stops listening until
        the device re-arms, realtime keeps listening."""

    def device_tools(self) -> Sequence[ToolDef]:
        """The device's discovered MCP tools, possibly empty."""

    async def call_device_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> tuple[str, bool]:
        """Invoke one device tool; (content, is_error)."""
```

### The send path, ordering preserved exactly

Today, for one reply chunk (`session.py`, `_speak` into
`_send_frames`):

1. packets = encoder.encode(resampler.process(chunk))
2. if not packets: return, without touching the filler
3. await `_filler_tail()` (reply audio only; filler frames skip it)
4. if the reply has not spoken: stamp `_speaking_started_at`, emit
   `speaking_started` with the active agent
5. per packet: pacing sleep, `_pace_resume.wait()`, socket send,
   capture channel 1, count

After the split, in the runtime's send wrapper:

1. `batch = output.encode_audio(resampler.process(chunk))`
2. `if not batch: return` (runtime; the filler is not consulted)
3. `await self._filler_tail()` (runtime-owned arbitration; the
   filler's own path calls `send_audio` directly, which is today's
   `from_filler=True`)
4. and 5. `await output.send_audio(batch)`: the edge stamps and
   emits `speaking_started` on the reply's first non-empty batch,
   then paces

The agent-leg drain (`_tool_loop` line 1843) becomes
`batch = output.encode_audio(resampler.flush()) +
output.flush_encoder()` followed by steps 2 to 5; it runs at the
end of every agent leg, as today, not only at reply end.

The filler's encoder discipline is pinned. Today the filler builds
one packet list in a single synchronous expression (clip, resampler
tail, encoder flush) and only then awaits the send; the reply task
encodes each chunk before it awaits `_filler_tail`, so the shared
encoder can be fed by the reply between any two filler awaits. The
filler therefore encodes everything before its first await, as one
combined batch, then sends once:

```python
batch = (
    output.encode_audio(resampler.process(clip))
    + output.encode_audio(resampler.flush())
    + output.flush_encoder()
)
await output.send_audio(batch)
```

Split across awaits, the filler's later `flush_encoder` could
consume reply audio the reply task fed in between. Batches are
caller-held values, so a filler sounding while the reply encodes
its first sentence can never send the reply's packets, exactly as
two local lists cannot mix today; the one-expression rule is what
keeps the shared encoder's feed order identical too. A commit-2
characterization test covers exactly this interleaving: a filler
sounding while a short reply chunk is encoded, asserting packet
content and call order.

### speaking_started attribution

`speaking_started` (and its stamp) is emitted at the pacing site,
which is the edge, but it carries `agent`, which is runtime state,
and it must name the agent active at fire time (a tool-only
handover before first audio, or a filler speaking first under the
new agent, must attribute to the new agent). The channel is
`SessionEvents.agent`: the runtime's `_activate_agent` writes it,
edge-emitted events read it. Both readers see the same activation
state at the same moments as today's `self._agent`, because
activation and emission run on the same event loop in the same
order. `speaking_started_at()` returns the stamp taken in step 4
above; the plan deliberately documents the stamp as taken before
the sleep, the pause wait, and the send, not "when the frame went
out".

### DeviceGone, wrapping, never narrowing

`DeviceGone` is a boundary exception the edge raises by translating
Starlette's `WebSocketDisconnect` (raised `from` the original) in
every output method that reaches the socket. It subclasses
`RuntimeError`, deliberately: every site that must swallow it
already catches `RuntimeError` broadly, so the translation can land
before any catch is respelled and every intermediate commit stays
green; the eventual `(DeviceGone, RuntimeError)` spelling documents
intent (and drops the Starlette import from the runtime) rather
than changing what is caught. The consequence is accepted and
stated: a broad `RuntimeError` catch also swallows a vanished
device, which is exactly how those sites treat the
`(WebSocketDisconnect, RuntimeError)` pair today. It wraps; it does
not narrow. Today `_reply` and `_run_filler` swallow
`(WebSocketDisconnect, RuntimeError)`, and the `RuntimeError` half
catches more than the socket (any runtime error raised while
speaking). The runtime therefore keeps catching the pair as
`(DeviceGone, RuntimeError)` in `_reply`, `_run_filler`, and the
closing-`tts stop` suppression, so exactly the same set of
provider, encoder, and resampler failures ends a reply quietly as
before. The only change is the spelling of the disconnect half; no
exception that reaches "reply failed" or "filler playback failed"
today is rerouted.

`SessionEvents` (in `device/events.py`) is handed to the runtime at
construction rather than put on `DeviceOutput`: session id, device
MAC, the active-agent attribution field, the pinned logger (next
section), the `_event` builder, and the capture decision-track
hooks (`event`, `vad`, `dropped`). Observability is orthogonal to
the boundary, and routing every event through one object is what
keeps the capture invariant intact: an event that is logged is an
event that is recorded, whichever side emits it.

Its device identity and capture hook attach in stages, mirroring
today's line ordering exactly:

- Created by the edge at accept, with the session id and no device
  identity, so the bad-Device-Id rejection event carries
  `device: None` as it does today.
- The edge writes the normalized MAC onto it at the point
  `self._mac` is assigned today, before the agent lookup, so the
  no-agent rejection already carries the MAC.
- The capture hook is None until the edge attaches the newly
  opened `SessionCapture` where `_start_capture` runs today: after
  the hello validates and before the server hello, so
  `session_open` is the first line of the decision track. The
  factory runs before the hello, but the runtime emits nothing
  before `session_open`, and it holds the same `SessionEvents`
  object whose hook the attach flips, so no runtime event can miss
  the decision track.
- The edge detaches the hook in the serve region's `finally`,
  after `session_closed` is emitted (so it is the last line of the
  track, as today) and before the capture is closed.

### The logger identity is pinned

`logs.py` emits `record.name` as the `logger` field of every JSON
record, and every session log line today carries
`samtal_server.session` because that is the module's
`logging.getLogger(__name__)`. Moving call sites into `device/` and
`runtime/` modules would silently change that field, which the
unchanged-stream promise forbids. So `SessionEvents` owns
`logging.getLogger("samtal_server.session")`, created by name and
documented as the session log channel (the name states what it is,
no longer which file), and every moved session-scoped log call goes
through it; neither new module calls `logging.getLogger(__name__)`
for conversation lines. A characterization test (commit 2) drives a
turn and asserts `record.name == "samtal_server.session"` on the
conversation events, so the pin cannot regress unnoticed; no
existing test catches this (`test_logs.py` formats a synthetic
record).

## Ownership decisions, with reasons

| State or job                | Side    | Why (litmus)                 |
| --------------------------- | ------- | ---------------------------- |
| Opus codecs, framing        | device  | wire format is the device's  |
| pace clock, pause/resume    | device  | realtime delivery to a       |
|                             |         | playback queue exists for a  |
|                             |         | phone call too               |
| `listening`, `_listen_mode` | device  | protocol facts about who     |
|                             |         | re-arms the mic              |
| endpointer, utterance       | runtime | a human hears you stop by    |
| buffer and trim             |         | themselves; #31 rewrites     |
|                             |         | exactly this behind the seam |
| gate ladder                 | runtime | interruption's consequences  |
|                             |         | are conversational; it emits |
|                             |         | `barge_in`,                  |
|                             |         | `barge_in_suppressed` with   |
|                             |         | reasons, `barge_in_merged`   |
|                             |         | unchanged, and drives the    |
|                             |         | device pause/resume/cancel   |
|                             |         | primitives                   |
| filler                      | runtime | it masks the AI middle's     |
|                             |         | latency and consults the     |
|                             |         | endpointer; `filler_played`  |
|                             |         | and `filler_skipped` with    |
|                             |         | reasons stay its events      |
| conversation history        | runtime | LLM context management       |
| agent activation, handover  | runtime | prompts and providers        |
| ASR language lock           | runtime | a fact about transcription   |
| tool loop and dispatch      | runtime | but the device MCP transport |
|                             |         | stays on the edge            |
| capture (all four tracks)   | device  | recording what crossed the   |
|                             |         | wire, on the edge's clock;   |
|                             |         | the VAD track is fed by the  |
|                             |         | runtime through              |
|                             |         | SessionEvents, which is the  |
|                             |         | decision-sites principle     |
|                             |         | applied, not a leak          |
| idle watchdog, limits,      | device  | appliance policy; it queries |
| shutdown grace              |         | `replying()` and `drain()`   |

Two calls worth spelling out:

- **`listening` is co-owned today** (`listen` messages set it, the
  runtime clears it at end of utterance in non-realtime mode). The
  split resolves this with `user_turn_ended()`: the runtime reports
  the conversational fact, the edge applies the mode policy. Same
  observable behavior, one owner per fact, and the runtime never
  learns the xiaozhi mode names.
- **The mic frame guards stay on the edge.** The `not_listening`
  and `barge_in_off` drops happen before Opus decode today, and
  their capture `dropped` records are part of the evidence #42
  exists for; moving them behind the boundary would reorder decode
  and drop. The edge keeps them, using its own `listening` flag and
  `input.replying()`.

The barge-in litmus example from the principles page holds by
construction: on a confirmed barge-in the runtime cancels its own
reply task (the conversational consequence, including what enters
history) and the edge's primitives stop the audio. There is no
device-side output queue beyond the frame in flight, because pacing
awaits inline; "cancel queued output" is therefore realized as
cancelling the reply task (abandoning the unsent remainder of the
batch in flight) plus resetting the pause state, and the ADR says
so.

## What explicitly does not change

- Wire bytes and message order: hello exchange, `stt`, `tts`
  start/sentence_start/stop, MCP envelopes, Opus framing, close
  codes.
- Event names, fields, and reasons, which log line carries them,
  and the `logger` field (`samtal_server.session`) on every record.
- Configuration schema, provider interfaces, the registry's
  behavior, the drain, auth, OTA.
- Gate ladder thresholds and order, filler rules, pacing math,
  encoder lifecycle (per-leg flushes, no flush or reset on
  cancellation), watchdog and limit values. The turn-taking issues
  own those.

## ADR and documentation

The PR writes `docs/adr/2026-08-XX-normalize-the-hardware-edge.md`
(dated the day it is written), four sections per
[`docs/adr/README.md`](../adr/README.md):

- **Status**: Accepted.
- **Context**: the #84 evaluation, the 2,138-line session owning
  both sides, #31 pending, the phone-call litmus.
- **Decision**: interfaces at samtal's core describe device
  capabilities; the xiaozhi edge is normalized once; runtimes stay
  themselves behind it; no universal ConversationBackend; the
  decision sites (gate ladder, filler) live in samtal's runtime
  components and keep their reasoned events; playable audio crosses
  the boundary as an opaque batch so the runtime never learns Opus.
- **Consequences**: #31 lands runtime-side; the #84 spike's
  serializer implements this same boundary so its size is
  comparable evidence; the trap to refuse is the boundary sprouting
  runtime-shaped methods (`commit_audio`, `set_turn_detection`) or
  the batch handle growing introspection.

`docs/architecture/principles.md` updates in the same change: the
"Normalize the hardware edge, not the AI middle" section (and the
two neighbouring principles that today cite only issue #84) gain a
citation of the new ADR. `CHANGELOG.md` gets a `### Changed` entry.

## One pull request, small commits

One PR is right. Splitting would leave `main` holding half a
boundary between PRs, and the diff is large but almost entirely
verbatim moves; the commit sequence below is the review path, and
every commit is green under the full unit and integration lanes.
Review with `git diff --color-moved` per commit. Characterization
tests land before anything moves and are the net under every later
commit; the boundary contract tests land after the wiring they
exercise, because the factory seam they inject a stub runtime
through only exists once `ws.py` takes the factory.

1. **docs: the ADR and its citations.** The decision precedes the
   code. ADR file, principles.md citations, nothing else.
2. **Characterization tests, against the current module.** Pins of
   current behavior that no existing test holds, written to survive
   the refactor (they assert wire output, events, and log records,
   not internals): the `logger` field on conversation events; a PCM
   chunk too short to fill an Opus frame neither settles nor
   cancels a pending filler; the filler speaking first stamps and
   attributes `speaking_started` (and the stamp lands before the
   pacing sleep and socket send); a tool-only handover attributes
   `speaking_started` to the post-handover agent; the full control
   message ordering of one turn (stt, tts start, sentence_start,
   frames, tts stop); `request_shutdown` waiting out a reply that
   is generating but not yet speaking; a disconnect mid-reply and a
   RuntimeError mid-reply both ending the reply quietly, and the
   same for the filler; and the call order of a filler sounding
   while a short reply chunk is encoded (the shared-encoder
   discipline pinned above).
3. **device/boundary.py: the interface pair.** `SessionInput`,
   `DeviceOutput`, `PlayableAudio`, `DeviceGone`, `RuntimeFactory`;
   `PIPELINE_SAMPLE_RATE` moves here. No users yet; a unit test
   asserts the protocols are runtime-checkable.
4. **device/events.py: SessionEvents extracted.** The pinned
   `samtal_server.session` logger, `_event`, the capture
   decision-track hooks, and the agent attribution field move;
   `session.py` delegates. Behavior identical; the commit-2 logger
   test now guards the pin.
5. **runtime/speech.py: the lookahead moves.** `_Synthesis` and the
   speak helpers, parameterized on what they already take.
6. **runtime/pipeline.py: the reply engine moves.** Tool loop,
   dispatch, handover, history, watchdog, provider events, ASR
   language lock become `PipelineRuntime`; `Session` holds
   `self.runtime` and forwards; the runtime still reaches a few
   session internals directly. The largest commit; bodies move
   verbatim.
7. **Endpointing, gate ladder, and filler move.** The utterance
   buffer, `_gate_barge_in`, and the filler runner join the
   runtime; the session gains `user_turn_ended`, `encode_audio`
   and `flush_encoder` returning `PlayableAudio`, `send_audio`,
   `pause_output`, `resume_output`, `speaking_started_at`; the
   listen-mode policy stays on the edge.
8. **Narrow to the boundary and wire the seam.** The runtime's
   session reference becomes `DeviceOutput` plus `SessionEvents`;
   `DeviceGone` translation lands, green immediately because it
   subclasses `RuntimeError` and the existing broad catches
   already swallow it, and the catch pairs are respelled
   `(DeviceGone, RuntimeError)`; `session.py` moves to
   `device/session.py` as `DeviceSession`; the composition-root
   factory lands in `app.py` and `ws.py`; `registry.py`'s
   `TYPE_CHECKING` import follows; test imports update;
   conformance asserts land. The commit-2 suite is the net under
   this commit.
9. **Boundary contract tests, through the wired seam.** A stub
   runtime injected via the factory and driven over the TestClient
   websocket asserts the edge's wire behavior alone (hello,
   guards, pacing, tts framing, `DeviceGone` translation), proving
   a second runtime can exist; a fake `DeviceOutput` drives
   `PipelineRuntime` through one turn and a barge-in. Kept small;
   no scope growth.
10. **docs: changelog, plan tick, implementation doc.**
   `CHANGELOG.md` entry, milestone checkboxes ticked with the PR
   number, the implementation doc's sections.

## Tests

Honest inventory. "Mechanical edit" means imports, monkeypatch
target paths, direct-construction calls (the factory replaces the
provider arguments), and internal attribute paths
(`session._turns` becomes `session.runtime._turns`); assertion
values change nowhere, and a needed assertion change means the
refactor is wrong.

- **Mechanical edits, file by file**:
  - `test_session_events.py`: monkeypatches `_speak`,
    `_send_frames`, `_reply`, and providers on the session; the
    patch targets move to the runtime (and `_send_frames` becomes
    the edge's `send_audio`), plus construction updates.
  - `test_session_barge_in.py`, `test_session_filler.py`,
    `test_session_tools.py`, `test_session_watchdog.py`,
    `test_tts_lookahead.py`, `test_session.py`,
    `test_session_limits.py`, `test_capture_session.py`, unit
    `test_drain.py`: construction via the factory, runtime
    attribute paths, moved constants (`OUTPUT_AUDIO`,
    `MAX_TOOL_ROUNDS`, `SWITCH_GREETING`,
    `SHUTDOWN_REPLY_GRACE_S`), moved monkeypatch targets.
  - `tests/integration/test_drain.py`: one import line
    (`GOING_AWAY` moves to `device/session.py`).
- **Untouched entirely**: the rest of the integration lane
  (`conftest.py`, device simulator, tools, personas, auth, OTA,
  app boot import only `app` and `config`), and the provider,
  protocol, audio, config, tools, auth, OTA unit tests. The
  integration lane passing with that single import edit is the
  compatibility floor demonstrated in CI.
- **New**: the characterization suite (commit 2), the boundary
  contract tests (commit 9), and protocol conformance asserts.
  Nothing else; a boundary test suite that re-tests the pipeline
  would be scope growth.

## Risks and mitigations

- **A 2,000-line move reviewed as a rewrite.** Mitigated by the
  commit sequence: bodies move verbatim, logic edits are confined
  to commits 7 and 8, `--color-moved` makes the moves checkable.
- **The filler/packet race.** The opaque-batch design exists
  because a single `send_audio(pcm)` cannot preserve the
  no-packet-no-arbitration rule; the send-path ordering section
  above is the checklist the implementation and its review follow.
- **The stale bytecode trap** (AGENTS.md). This PR deletes and
  renames modules; anything run outside pytest can execute a stale
  `.pyc` whose size and whole-second mtime still match, and a
  restore mid-experiment re-arms the trap. Export
  `PYTHONDONTWRITEBYTECODE=1` for manual runs, clear `__pycache__`
  after moves, and if a result contradicts the source, suspect this
  first. The test suite is safe (`tests/conftest.py` clears caches).
- **Capture alignment must not shift.** Channel 1 is decoded from
  the very Opus paced out, on the edge's clock; that code does not
  move. The mic track still decodes from wire bytes before the
  guards. The VAD samples, dropped-frame records, and every event
  keep flowing through `SessionEvents` at the same call sites, so
  `test_capture_session.py` keeps its assertions unchanged.
- **Ordering and backpressure.** No queues are introduced; every
  boundary call is awaited inline from where the code runs today.
  The gate ladder's confirmation ASR stays in the receive path.
- **Error-path drift.** `DeviceGone` wraps, never narrows, and
  subclasses `RuntimeError`, so no intermediate commit can reroute
  a vanished device through the generic failure path; the broad
  `RuntimeError` halves of the reply and filler catches stay, and
  commit 2 pins both paths before anything moves.
- **Single-process assumptions hold.** Registry, drain, capture
  store, memory store all remain in-process singletons; the
  boundary adds no tasks, threads, or IPC, so the RWO deployment
  shape is untouched.
- **Entanglement with #28/#80/#81.** No threshold, gate, or filler
  rule changes ride along; land promptly to keep their rebase cost
  low.

## Verification

- [ ] `uv run ruff check .` clean.
- [ ] `uv run pytest tests/unit -q` passes; the commit-2
      characterization tests pass unmodified from before the
      extraction to after it, and no assertion value changed in
      any existing test.
- [ ] `uv run pytest tests/integration -q` passes with only the
      one-line `GOING_AWAY` import edit (stock-protocol
      compatibility, in CI).
- [ ] `git diff --color-moved` reviewed commit by commit; logic
      edits appear only where this plan says they do.
- [ ] Board: nothing required. Wire bytes and message order are
      unchanged and the integration lane exercises the real
      protocol. An optional desk conversation on the
      Touch-LCD-1.54 is a nice-to-have; if skipped, the PR's
      verification section says so with an unchecked box rather
      than pretending.

## Open questions

- Whether the runtime factory should become configuration is
  deliberately deferred until a second runtime exists; the #84
  spike will answer what the selection needs to express.
- `filler.py` (boot-time clip building) stays top-level for now;
  when a second runtime wants fillers, decide then whether clips
  are a runtime input or an agent asset.

## Milestones

All milestones land in one pull request; they are the review
stages, ticked together with that PR's number, each linking to its
section of the implementation doc when written.

- [ ] **Boundary, ADR, and the safety net**: the
  normalize-the-hardware-edge ADR with its principles-page
  citations, the characterization tests pinning today's behavior,
  `device/boundary.py`, and `device/events.py` with the pinned
  logger (commits 1 to 4). Accept: interfaces exist with
  docstrings, characterization suite green against the unmoved
  code, all existing tests pass untouched.
- [ ] **Runtime extraction**: `runtime/speech.py` and
  `runtime/pipeline.py` carved out, endpointing, gate ladder, and
  filler moved, session delegating (commits 5 to 7). Accept: full
  suite green with mechanical test edits only, characterization
  suite unmodified.
- [ ] **Narrowing, contracts, and docs**: runtime speaking only
  through the boundary, `DeviceSession` in its package, the
  composition-root factory in `app.py` and `ws.py`, conformance
  asserts, then the boundary contract tests through the wired
  seam, changelog and docs (commits 8 to 10).
  Accept: no runtime import of Starlette or `protocol/`, no device
  import of providers' LLM/ASR/TTS types, characterization and
  contract suites unmodified, verification section above fully
  run.
