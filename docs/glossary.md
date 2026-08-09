# Glossary

**Date:** 2026-08-09

The concepts, techniques, and technologies this project is built on:
a short definition of each as samtal uses it, with pointers for going
deeper. The [architecture walkthrough](architecture/README.md)
teaches the pipeline-shaped subset of these in order, with diagrams;
this page is for looking one thing up. Entries are alphabetical,
and each is a heading, so any of them can be linked directly from
anywhere in the project: `docs/glossary.md#opus`,
`docs/glossary.md#barge-in`,
`docs/glossary.md#vad-voice-activity-detection`.

### AEC (acoustic echo cancellation)

Signal processing that removes
the device's own speaker output from its microphone input, using the
played signal as the reference. On samtal's primary board it runs in
firmware on dedicated codec hardware (ES8311 playback, ES7210
capture), and it is why the field-measured echo leakage is below the
ambient floor. Whether a device has AEC decides its listening mode
and how much of the barge-in problem is acoustic.
More: [Speex/SpeexDSP AEC](https://www.speex.org/docs/manual/speex-manual/node7.html),
[WebRTC audio processing](https://webrtc.org/).

### Agent

One configured persona: a system prompt, an LLM, a voice,
an ASR language pin, and a tool set. A device is bound to one or
more agents; the first is active at connect. Older issues say
"persona"; new writing says agent.

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
("samtal"). Kept to vocabulary only, never agent names or anything
imperative, because the model can echo the prompt back as if the user
had spoken it; see prompt echo.

### Barge-in

Interrupting the assistant by talking over its reply.
Speech that endpoints mid-reply passes a gate ladder (minimum speech,
refractory window, transcript confirmation) before it cancels the
reply; suppressed attempts are logged with the gate that stopped
them. The design decision is recorded in
[the barge-in ADR](adr/2026-08-05-replies-cancel-only-on-evidence-of-speech.md).

### Capture

Per-session recording for field analysis: a stereo WAV
(microphone on channel 0, the paced reply on channel 1), an event
track (JSONL), and a manifest (JSON). Off by default; recording room
audio is a deliberate, temporary state. See
[the regression suite page](conversational-quality-regression-suite.md).

### Continuation

The rest of a user's sentence, arriving after the endpointer
wrongly declared the sentence over at a thinking pause. By the time
it arrives, a reply to the fragment is already in flight, so the
pipeline can only see the continuation as a barge-in attempt against
that reply; whether it survives the gate ladder decides whether the
user gets to finish their own thought.

### Conversational filler

A short utterance that buys time in a
conversation without carrying content ("Hmm, let me see..."). In
linguistics, a filled pause; in voice interfaces the technique is
latency masking: playing a filler when the reply is slow so the
silence reads as thinking rather than deafness. Distinct from a
backchannel, which is the listener's feedback ("mm-hm") while the
other party speaks. Implemented in samtal as optional per-agent
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

### Endpointer

The logic that decides where an utterance ends:
accumulates VAD evidence per frame and declares the turn over after
enough trailing silence. Its trailing-silence bound is a
conversation-design tradeoff: too short chops sentences at thinking
pauses, too long makes every reply feel late.

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
with a reason). The ladder enforces
[the barge-in ADR](adr/2026-08-05-replies-cancel-only-on-evidence-of-speech.md):
acoustics alone can at most pause a reply; only evidence of user
speech cancels it.

### Handover

Switching the active agent mid-session, requested by
name in conversation and executed by the LLM's `switch_agent` tool.
The tool's enum of bound agents is what maps a near-miss transcript
("Mark") onto the right agent (`marc`).

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
is on, so realtime is the normal mode for samtal's target hardware.

### LLM round

One generation call within a reply. Tool use makes a
reply multi-round: a round that calls tools speaks nothing, the next
round reads the results. Each round logs duration, time to first
token, and token counts (`llm_round`).

### Manifest

The JSON sidecar of a capture: device, firmware,
resolved provider entries verbatim, completeness flag. What makes a
recording attributable to an exact stack long after the fact.

### Marker phrase

A fixed, otherwise-improbable phrase ("banana,
banana, banana") said aloud the moment a field test misbehaves. It
timestamps the problem in the audio and the transcript, surviving
even a wrongly pinned ASR because three repeated bursts are visible
in the waveform.

### MCP (Model Context Protocol)

The protocol samtal uses for tools
on both sides: the device publishes its own controls (volume,
screen) to the server over it, and the server connects outward to
configured MCP servers whose tools agents may call.
More: [modelcontextprotocol.io](https://modelcontextprotocol.io/).

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

### PCM

Raw uncompressed audio samples, the working format between
pipeline stages once Opus is decoded: 16-bit mono, 16 kHz on the
input side.

### Premature endpoint

The endpointer declaring an utterance over at a pause that was
thinking rather than turn-yielding. Visible in the logs as `heard`
transcripts that trail off ("I'm here with my..."): the ASR itself
marks the trail-off with an ellipsis. Each one starts a reply to a
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

### Refractory period

The window right after the assistant starts
speaking during which barge-in attempts are suppressed, absorbing
the acoustic aftermath of the user's own previous utterance. One
rung of the gate ladder; its cost is that a continuation which
endpoints inside the window is discarded with it.

### Sentence lookahead

Synthesizing the next sentence while the
current one plays, removing the dead air that otherwise appears at
every sentence boundary of a multi-sentence reply (issue #37). The
subtlety is ownership: a synthesized-ahead sentence belongs to the
agent leg that started it, which matters across a handover.

### Structured event

A named JSONL record the server emits at each conversational
decision point (`heard`, `barge_in`, `barge_in_suppressed`,
`filler_played`, `llm_round`, ...), carrying the numbers behind the
decision (`speech_ms`, `delay_ms`, a suppression's reason). The
event stream is what field analysis reconstructs timelines from; a
capture's event track is the same stream scoped to one session.

### Trailing silence

The stretch of non-speech the endpointer
requires before declaring an utterance over. The single most
consequential conversational constant: it sets where the system
believes a sentence ends.

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
audio as speech or not, the pipeline's first judgment. samtal uses
Silero VAD. Everything downstream sees only what the VAD passes, so
"the assistant never heard me" begins here.
More: [Silero VAD](https://github.com/snakers4/silero-vad).

### Wake word

An always-on, on-device trigger phrase that opens a
session hands-free. Supported by the firmware's ESP-SR models; the
primary test setup uses the conversation button instead.
More: [ESP-SR](https://github.com/espressif/esp-sr).
