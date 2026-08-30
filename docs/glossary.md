# Glossary

**Date:** 2026-08-21

The concepts, techniques, and technologies this project is built on:
a short definition of each as vinga uses it, with pointers for going
deeper. The [system overview](system-overview.md) teaches the
pipeline-shaped subset of these in order, with diagrams; this
page is for looking one thing up. Entries are alphabetical,
and each is a heading, so any of them can be linked directly from
anywhere in the project: `docs/glossary.md#opus`,
`docs/glossary.md#barge-in`,
`docs/glossary.md#vad-voice-activity-detection`.

### AEC (acoustic echo cancellation)

Signal processing that removes
the device's own speaker output from its microphone input, using the
played signal as the reference. On vinga's primary board it runs in
firmware on dedicated codec hardware (ES8311 playback, ES7210
capture), and it is why the field-measured echo leakage is below the
ambient floor. Whether a device has AEC decides its listening mode
and how much of the barge-in problem is acoustic.
More: [Speex/SpeexDSP AEC](https://www.speex.org/docs/manual/speex-manual/node7.html),
[WebRTC audio processing](https://webrtc.org/).

### Agent

A named configuration of prompt, providers, voice, and
MCP tools that holds conversations and accrues memory. A device is
bound to one or more agents; the default answers a fresh wake. Older
issues say "persona"; new writing says agent. The full domain model
is on [the concepts page](concepts.md).

### ASR (automatic speech recognition)

The stage that turns one
utterance of audio into text. Pluggable: a local engine
(faster-whisper) or a cloud endpoint (OpenAI transcription models).
The transcript is the only thing later stages ever see, so ASR
failures masquerade as reasoning failures.
More: [Whisper paper](https://arxiv.org/abs/2212.04356),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper).

### ASR prompt

Free-text context an ASR accepts to bias
transcription, used for vocabulary the model would otherwise mangle
("vinga"). Kept to vocabulary only, never agent names or anything
imperative, because the model can echo the prompt back as if the user
had spoken it; see prompt echo.

### Barge-in

Interrupting the assistant by talking over its reply.
Speech that endpoints mid-reply passes a gate ladder (minimum speech,
refractory window, transcript confirmation) before it cancels the
reply; suppressed attempts are logged with the gate that stopped
them. The design decision is recorded in
[the barge-in ADR](adr/2026-08-05-replies-cancel-only-on-evidence-of-speech.md).

### Binding

The link between a device and the agents reachable from
it, one of which is the designated default that answers a fresh
wake. Many-to-many: one agent can serve several devices, one device
can reach several agents. Today it is the device's agent list in
configuration, first entry the default. See
[the concepts page](concepts.md#binding).

### Capture

Per-session recording for field analysis: a stereo WAV
(microphone on channel 0, the paced reply on channel 1), an event
track (JSONL), and a manifest (JSON). The reply channel is
[wire-true](#wire-true-capture), which is what makes the echo
measurement trustworthy. Off by default; recording room
audio is a deliberate, temporary state. See
[the regression suite page](conversational-quality-regression-suite.md).

### Continuation

The rest of a user's sentence, arriving after the endpointer
wrongly declared the sentence over at a thinking pause. By the time
it arrives, a reply to the fragment is already in flight, so the
pipeline can only see the continuation as a barge-in attempt against
that reply; whether it survives the gate ladder decides whether the
user gets to finish their own thought. Example from field round 2:
"I'm here with my..." endpointed, drew the reply "Take your time!",
and the continuation "I'm here with my kids." then had to fight
that reply to be heard.

### Conversation

A dialogue between a user and exactly one agent,
living on the server independently of any device: a durable thread
that accrues a transcript and can be resumed later, including from a
different device, though a fresh wake starts a fresh thread and
resuming an old one is always asked for. What accumulates and what
you come back to, where a session is how audio reaches the server.
One conversation can span many sessions, and a turn names both the
thread it belongs to and the session it was spoken in, so the two
are views of the same rows. The entity exists (issue #190): a thread
takes its identity at an agent activation, its row with its first
stored turn, and its title from that turn's utterance, and retention
measures the window against its last activity. Where the deployment
has switched resumption on, an agent finds one of its own past threads
by description and carries on with it, rebuilding the context from the
stored dialogue under a token budget; off, an agent's working context
ends when its session closes. See
[the concepts page](concepts.md#conversation-and-session).

### Conversational filler

A short utterance that buys time in a
conversation without carrying content ("Hmm, let me see..."). In
linguistics, a filled pause; in voice interfaces the technique is
latency masking: playing a filler when the reply is slow so the
silence reads as thinking rather than deafness. Distinct from a
backchannel, which is the listener's feedback ("mm-hm") while the
other party speaks. Implemented in vinga as optional per-agent
latency masking, configured under an agent's (or `agent_defaults`')
`filler` section.
More: [filled pause](https://en.wikipedia.org/wiki/Filler_(linguistics)),
[backchannel](https://en.wikipedia.org/wiki/Backchannel_(linguistics)).

### Cross-correlation

Sliding one signal against another and
measuring similarity at each offset. Used on the two capture
channels to measure echo: the correlation peak's position gives the
acoustic delay and its gain the leakage. A null result is only
trusted alongside a positive control (a synthetic echo the method
must find).
More: [cross-correlation](https://en.wikipedia.org/wiki/Cross-correlation).

### Echo leakage

How much of the assistant's own voice survives the
device's AEC and arrives back at the server as microphone input,
expressed in dB relative to the played signal. The number barge-in
thresholds would have to defend against; field measurement found it
below the ambient floor on the primary board (issue #48).

### End-of-turn detection

The general problem the endpointer is
one answer to: deciding whether a pause means "your turn" or "still
thinking". Human listeners read three signal families at once, and
the same 700 ms pause reads differently under each: silence duration
(all vinga's endpointer uses today), prosodic cues, and semantic
completeness. After "...y toca el piano muy bien." the pause yields
the turn; after "guarda en la memòria que..." the same pause holds
it, and only the last two signal families can tell those apart.
Pretrained models exist for exactly this judgment; smart-turn is the
openly licensed one (BSD-2-Clause, audio-native, CPU inference).
More: [smart-turn](https://github.com/pipecat-ai/smart-turn).

### Endpointer

The logic that decides where an utterance ends:
accumulates VAD evidence per frame and declares the turn over after
enough trailing silence. Its trailing-silence bound is a
conversation-design tradeoff: too short chops sentences at thinking
pauses, too long makes every reply feel late. Silence duration is
the only signal it reads; end-of-turn detection is the general
problem, with the other two signal families.

### First token

The first piece of an LLM's streamed answer; the
time to it (TTFT) is the latency a user actually feels, as opposed
to the full generation time. A round that streams nothing at all is
a stall; the first-token watchdog bounds that wait, retries once,
and gives the round up as a silent turn rather than a wedged
session.

### Gate ladder

The ordered checks an endpointed utterance passes before it may
cancel a reply in flight: minimum classified speech (a noise blip
cancels nothing), a merge when the reply is still inside ASR (that
reply was transcribing the head of the user's own sentence), the
refractory period, and transcript confirmation (pause the outgoing
frames, run ASR, cancel only on a non-empty transcript). Each
suppressed attempt logs which gate stopped it (`barge_in_suppressed`
with a reason). In practice: a 32 ms noise blip dies at the speech
floor and costs nothing, while a real continuation that endpoints
inside the refractory window dies with it, which is the ladder's
known cost. The ladder enforces
[the barge-in ADR](adr/2026-08-05-replies-cancel-only-on-evidence-of-speech.md):
acoustics alone can at most pause a reply; only evidence of user
speech cancels it.

### Handover

Switching the active agent mid-session, requested by
name in conversation and executed by the LLM's `switch_agent` tool.
The tool's enum of bound agents is what maps a near-miss transcript
("Mark") onto the right agent (`marc`). Each agent of a session has
its own conversation, and the switch is a per-conversation context
switch: the incoming agent reads its own thread and nothing of the
outgoing agent's, and switching back returns an agent to the thread
it was on with what it said there. The turn the handover happens in
belongs to the thread it started on, and the greeting the incoming
agent answers with is the first turn of its own. Carrying context
across deliberately, on phrasing that asks for it, remains decided
direction (issue #190). See
[the concepts page](concepts.md#conversation-and-session).

### Help agent

The planned built-in agent bound to every device by
default. Answers how the device in front of the user works (from the
per-board device guide, selected by device model at runtime), what
vinga's concepts mean, and which voice commands the device itself
publishes as MCP tools. Knows whether its device has a wake word
enabled, and that the wake word wakes the device, not an agent. See
[the concepts page](concepts.md#the-help-agent), which names the
issue this direction belongs to.

### Idle timeout

A realtime session with no conversation for the
configured time is closed by the server; the device reconnects on
the next button press. Exists because a realtime device streams its
microphone continuously and would otherwise hold it open forever.

### Listening modes

How the device decides when the microphone is
live. Realtime: streams continuously, AEC removes playback, barge-in
is possible. Auto: microphone stops during playback, re-arms each
turn. Manual: push to talk. The firmware picks realtime whenever AEC
is on, so realtime is the normal mode for vinga's target hardware.

### LLM round

One generation call within a reply. Tool use makes a
reply multi-round: a round that calls tools speaks nothing, the next
round reads the results. Each round logs duration, time to first
token, and token counts (`llm_round`).

### Manifest

The JSON sidecar of a capture: the device identity (MAC
and client ID), the board model and firmware version cached from the
device's last OTA check-in when they are available, the session's
protocol version, resolved provider entries verbatim, completeness
flag. What makes a recording attributable to an exact stack long
after the fact.

### Marker phrase

A fixed, otherwise-improbable phrase ("banana,
banana, banana") said aloud the moment a field test misbehaves. It
timestamps the problem in the audio and the transcript, surviving
even a wrongly pinned ASR because three repeated bursts are visible
in the waveform.

### Memory

What an assistant keeps between replies, in three scopes named by
whose the remembered thing is: the **agent** scope, what this agent
knows about the user, keyed by the agent and not by the board it was
said to; the **device** scope, what is known about the place and its
household, shared by every agent bound to that device; and
**conversation state**, a keyed ledger of what is currently true in the
thread happening now, which dies with it. All three are injected into
the system prompt, in the order they take precedence in, which is the
conversation first, then the agent's facts, then the device's notes,
each under a heading stating its rank: what is most current wins. A
removed fact is held rather than erased until the conversation that
removed it ends, which is what makes "no, put that back" answerable. The
agent scope is larger than a prompt, so it splits in two: a small
injected core of the newest facts, and the rest reached by looking it
up. Distinct from what an assistant
appears to know inside one conversation, which is the dialogue it is
reading. See
[the concepts page](concepts.md#memory).

### MCP (Model Context Protocol)

The protocol vinga uses for tools
on both sides: the device publishes its own controls (volume,
screen) to the server over it, and the server connects outward to
configured MCP servers whose tools agents may call.
More: [modelcontextprotocol.io](https://modelcontextprotocol.io/).

### Meta capability

A vinga-owned tool injected into every agent's
tool set, so meta questions are answerable in any conversation:
conversation cost so far, searching and resuming the asking agent's
past conversations, switching agents. The handover tool and the two
conversation tools (start a new thread, find and resume an old one)
exist today; the cost question is planned rather than built.
Conversation
search is deliberately agent-scoped, and turns that are only meta
requests (device control, a cost question, the switch itself) are
recorded as session events, not conversation entries. See
[the concepts page](concepts.md#meta-capabilities).

### Opus

The audio codec on the device link, carrying 60 ms frames
in both directions. Chosen (by the upstream ecosystem) for quality
at low bitrate and graceful behaviour on lossy links.
More: [RFC 6716](https://www.rfc-editor.org/rfc/rfc6716),
[opus-codec.org](https://opus-codec.org/).

### OTA endpoint

The HTTP endpoint a device calls at boot with its
identity; the reply carries the WebSocket URL and the device's auth
token. The URL a board calls lives in its NVS flash, which is why a
board follows a deployment until that entry is rewritten.

### Output pacing

Sending reply audio at playback speed, one
60 ms Opus frame per 60 ms, instead of as fast as the network
accepts. The pacing clock is owned by the device edge and reset per
reply. Pacing keeps the device's small playback buffer from
overflowing, keeps a barge-in cut close to immediate (little audio
is in flight beyond what has played), and gives the capture its
timeline: each frame is recorded at the moment the clock sends it
(see [wire-true capture](#wire-true-capture)).

### PCM

Raw uncompressed audio samples, the working format between
pipeline stages once Opus is decoded: 16-bit mono, 16 kHz on the
input side.

### Premature endpoint

The endpointer declaring an utterance over at a pause that was
thinking rather than turn-yielding. Visible in the conversation
store as turns whose transcript trails off ("I'm here with my..."),
which the ASR itself marks with an ellipsis; on the events it shows
as a short `heard` whose continuation arrives as a barge-in. Each one starts a reply to a
fragment and turns the user's continuation into a barge-in attempt
against it. Dictation-style speech (telling an agent things to
remember) pauses longer between clauses than question-answer
exchanges do, which is what makes the trailing-silence bound a
per-conversation tradeoff rather than a constant.

### Pre-roll

A short stretch of audio kept from before the VAD's
speech onset when trimming an utterance, so a soft first syllable is
not clipped by the trim.

### Prompt echo

An ASR failure mode where the model returns the
transcription prompt itself as the transcript, most likely on very
short clips. The guard discards an exact echo rather than acting on
it, and a retry without the prompt recovers the cases where a real
short utterance was behind it (issues #54, #69).

### Prosodic cues

How speech sounds as it approaches a pause,
independent of the words: the melody and rhythm that tell a listener
whether the speaker is done. A finished turn typically ends with
falling pitch (or a rise for a yes/no question) and a stretched
final syllable; a thinking pause cuts off with the pitch left
suspended, which any listener hears as "more coming". Filled pauses
are prosody too, and explicitly turn-keeping: "um..." means the
speaker is holding the floor, yet a silence-only endpointer
transcribes it as a complete utterance and the assistant answers it
("Take your time!"), which field round 2 recorded verbatim.
More: [prosody](https://en.wikipedia.org/wiki/Prosody_(linguistics)).

### Refractory period

The window right after the assistant starts
speaking during which barge-in attempts are suppressed, absorbing
the acoustic aftermath of the user's own previous utterance. One
rung of the gate ladder; its cost is that a continuation which
endpoints inside the window is discarded with it.

### Semantic completeness

Whether the words heard so far form a
finished thought. "I'm here with my kids." stands alone; "I'm here
with my" ends on a possessive with no noun and no fluent listener
would treat it as finished, yet a silence-only endpointer cannot
tell the two apart. The pipeline already gets one semantic signal
for free: Whisper-family ASR marks a trail-off with a trailing
ellipsis in the transcript, which is how field round 2 counted 18
premature endpoints without listening to a single recording.
Transcript-based turn models make the same judgment with a
classifier instead of a heuristic.

### Sentence lookahead

Synthesizing the next sentence while the
current one plays, removing the dead air that otherwise appears at
every sentence boundary of a multi-sentence reply (issue #37). The
subtlety is ownership: a synthesized-ahead sentence belongs to the
agent leg that started it, which matters across a handover.

### Session

One connection episode from one device: wake (button
press or wake word) to close. A session attaches to conversations;
it is not a conversation. "Sophia... let me talk to Nadia... back to
Sophia" is one session touching two conversations. Its own
transcript (everything said and done from wake to close, across
every conversation touched plus the meta turns) is a view over the
turns that name it, the same rows a conversation reads by thread, so
no dialogue is stored twice. Belongs to the device side of the model
the way a conversation belongs to an agent. See
[the concepts page](concepts.md#conversation-and-session).

### Structured event

A named JSONL record the server emits at each conversational
decision point (`heard`, `barge_in`, `barge_in_suppressed`,
`filler_played`, `llm_round`, ...), carrying the numbers behind the
decision (`speech_ms`, `delay_ms`, a suppression's reason). The
event stream is what field analysis reconstructs timelines from; a
capture's event track is the same stream scoped to one session. The
few named here are examples: every event, every field one may carry
and every token a reason admits are in
[the event schema reference](reference/events.md), generated from
the declarations.

### Trailing silence

The stretch of non-speech the endpointer
requires before declaring an utterance over. The single most
consequential conversational constant: it sets where the system
believes a sentence ends. At the 700 ms default, question-answer
turns end cleanly but dictation ("remember that...") pauses longer
than that between clauses, so the same bound that feels responsive
in one conversation manufactures premature endpoints in the other.

### TTS (text-to-speech)

The stage that turns reply text into
audio, streamed sentence by sentence. Pluggable: local (Piper, a GPL
extra, never a core dependency) or cloud (ElevenLabs, OpenAI). Voice
choice is per agent, which is what makes a handover audible.
More: [Piper](https://github.com/OHF-Voice/piper1-gpl).

### Utterance

One stretch of user speech as delimited by the
endpointer: the unit ASR transcribes and the unit the conversation
advances by.

### VAD (voice activity detection)

Frame-by-frame classification of
audio as speech or not, the pipeline's first judgment. vinga uses
Silero VAD. Everything downstream sees only what the VAD passes, so
"the assistant never heard me" begins here.
More: [Silero VAD](https://github.com/snakers4/silero-vad).

### Wake word

An always-on, on-device trigger phrase that opens a
session hands-free. Supported by the firmware's ESP-SR models; the
primary test setup uses the conversation button instead. The wake
word wakes the device, never a particular agent: ESP-SR spots it
on-chip, the server takes no part in the decision and is at most told
which word fired, after the fact, and the device's default agent
answers. Builds with the firmware's send-wake-word-data option
enabled, the default in current upstream sources, also send the
buffered trigger audio as the conversation's first audio; whether our
prebuilt images do is unchecked on the wire (issue #112).
More: [ESP-SR](https://github.com/espressif/esp-sr).

### Wire-true capture

The property that makes a capture's reply
channel trustworthy as a measurement reference: channel 1 is decoded
from the very Opus packets paced out to the device, each placed at
the moment it was sent, contiguously, on one clock. A reference with
this property stands in for what the device actually played, which
is what the echo-leakage analysis cross-correlates against; a
recording made upstream of pacing, on its own timeline, can carry a
constant offset or a drift the analysis would misread as acoustics.
The property is proven, not assumed: the synthetic-echo control is
its acceptance test, and the pipecat alignment spike showed a
foreign pipeline reproduces it only with deliberate wiring
([the spike record](plans/2026-08-11-pipecat-alignment-spike-implementation.md)).

### World

One complete configuration state a running server
serves: a validated configuration snapshot, the stored credentials
opened behind it, and everything built from the pair (provider
engines, MCP installs, filler clips), frozen together. In code, a
`Generation`. The server boots into its first world, and applying
stored changes without a restart composes and builds the whole next
world before anything swaps, so a refused apply has changed nothing.
Live work then converges at its own boundary: tools per reply,
prompt text per activation, filler clips and provider engines when a
conversation opens. A world nothing serves and nothing holds retires
and releases what it built. See
[how a change reaches a conversation](concepts.md#configuration-changes-arrive-as-whole-worlds).
