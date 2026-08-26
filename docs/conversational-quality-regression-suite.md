# A regression suite for conversational quality

**Date:** 2026-08-08

Unit and integration tests prove the server does what its code says.
They cannot prove a conversation feels right: that replies come when a
person expects them, that interrupting works, that a quiet "yes,
please" is heard. Those properties only exist on a real device, in a
real room, with a real person talking, so they are verified by field
tests: structured on-device sessions, recorded and analyzed after the
fact.

This page turns what the first field-test rounds learned into a
starting point for anyone setting up and analyzing such a round, and
it names the central idea: treated with a little discipline, field
tests stop being one-off debugging expeditions and become a
**regression suite for conversational quality**. The same tests, run
on a changed system against a recorded baseline, say whether the
change helped, hurt, or did nothing, in the only terms that matter to
a user.

The concrete protocols live in the tracker (round 1: issue #48, now
closed; round 2: issue #73). This page is what stays true between
rounds.

## The shape of a turn

Every test in the suite probes some step of the same loop. The
step-by-step teaching tour with diagrams is in
[system-overview.md](system-overview.md); this is the compact version,
with the structured event each step emits, since those events are what
the analysis reads. The events named below are the ones a turn walks
through; what each one carries, and the whole set of them, is in
[the event schema reference](reference/events.md), generated from the
declarations.

1. **Session open.** The device connects over WebSocket and is bound
   to its agents; `session_open` records the agent, the device, and
   the build revision, so every recording is attributable.
2. **Listening.** The device streams its microphone continuously as
   Opus frames (realtime mode); the server decodes them to PCM.
3. **Endpointing.** The VAD scores each frame for speech; the
   endpointer accumulates the evidence and declares the utterance
   over after enough trailing silence. Continuous `vad` records carry
   what it believed, frame by frame.
4. **Transcription.** The utterance audio goes to the ASR; `heard`
   carries the utterance duration and the language the engine heard,
   and the transcript itself is on the conversation store's turn (the
   events carry no conversation text).
5. **Generation.** The LLM produces the reply, possibly over several
   rounds when tools are called; each round emits `llm_round` with
   its duration and time to first token. A `switch_agent` call emits
   `handover`.
6. **Synthesis and playback.** The reply is synthesized sentence by
   sentence (with lookahead) and paced to the device as Opus frames;
   `speaking_started` marks the first audible frame, `replied` the
   completed reply.
7. **Barge-in.** Speech that endpoints while the assistant is
   replying goes through a gate ladder; `barge_in` marks a confirmed
   interruption, `barge_in_suppressed` a rejected one, with the
   reason.
8. **Session close.** A button press, the idle timeout
   (`session_idle`), or a session limit; a clean close finalizes the
   capture.

Any conversational failure a field test can surface is a failure of
one of these steps, and the first analysis question is always which
one. The three failure classes found in round 1 map cleanly: a reply
that never starts (step 5 stalled with nothing bounding it), speech
never detected (step 3 saw nothing), speech detected but discarded
(step 4 returned nothing).

## What a field test needs before anyone leaves the desk

A session that cannot be attributed or reproduced is an anecdote.
The preflight below is what makes it evidence.

- **A pinned stack.** Providers (LLM, ASR, TTS, VAD), voices, and
  firmware version, recorded before the run. Calibration findings do
  not transfer across stacks (see the layers below), so the stack a
  number was measured on is part of the number.
- **A confirmed build.** `/healthz` reports the revision; it must
  match the image the run believes it is testing.
- **Capture on.** Per-session recording (see below). Capture is a
  deliberate, temporary state: it records room audio, so it is turned
  on for the round and off when the round ends.
- **Self-labelling sessions.** The first thing said in every session
  is the test number ("Starting test three"). Round 1 skipped this in
  8 of 11 sessions and attributing them needed forensics.
- **A marker phrase.** One fixed phrase ("banana, banana, banana"),
  said aloud the moment something misbehaves. Three repetitions
  survive a wrong ASR language pin and are findable in the waveform
  by eye. Round 1 never used it and paid for that in scrubbing time.
- **One session per test**, opened and closed with the conversation
  button, never mid-reply (a device abort bypasses the barge-in
  ladder and contaminates the test).
- **A control.** A quiet ordinary session on the same build and day
  as the interesting ones. Without it, "the room was noisy" and "the
  build regressed" cannot be told apart.

## What a session yields

Each captured session produces three files, together sufficient to
re-derive everything the analysis needs:

- **`<session>.wav`**: stereo, 16 kHz. Channel 0 is the microphone
  as received, channel 1 what was paced to the speaker. Having the
  reply as its own channel is what turns echo measurement into a
  cross-correlation instead of a guess.
- **`<session>.jsonl`**: the event track, every structured event with
  a session-relative timestamp, including the continuous `vad`
  records that never reach the server log.
- **`<session>.json`**: the manifest: device, firmware, resolved
  provider entries verbatim, completeness flag. The manifest is what
  makes a recording comparable months later, because it answers
  "what exactly produced this" without asking anyone.

Analysis starts from the event track (reconstruct the turn timeline,
find the moment something went wrong), drops to the WAV only where
events run out (was there really speech the VAD missed?), and uses
the server log for anything session-spanning.

## The three layers, by how they age

Findings from a field round age at three very different rates, and
knowing which layer a finding lives in is what tells you what a
provider or hardware change invalidates.

### The instrument (stack-independent)

The machinery for asking questions, valuable regardless of every
provider and device choice:

- The capture format, the event vocabulary, and the manifest.
- The protocol discipline above (announcements, marker, control
  session).
- The analysis methods, with their validation. The echo measurement
  is the model: cross-correlation over windows where the assistant
  plays and the user is silent, proven against a synthetic injected
  echo before its null result was believed. Both live in
  [`scripts/`](../scripts/): `echo_leakage.py` is the measurement,
  `echo_leakage_control.py` the positive control that must pass
  before any null result is trusted.
- The failure taxonomy: pipeline wedged on a provider, speech never
  detected, speech detected but transcribed to nothing. Any stack
  can fail in these ways; tests that probe them are permanent.
- The architectural invariants the fixes encode: waits on providers
  are bounded, a failed round degrades to a silent turn, never a
  wedged session.

A stack change does not touch this layer; it is what measures the
change.

### The interaction layer (survives provider swaps)

Findings about how people talk and how this class of device hears,
independent of which cloud is on the other end:

- Short acknowledgements ("yes, please") are the turns most at risk,
  simultaneously the hardest clips for an ASR and the ones a user
  expects an instant reaction to.
- Mid-sentence thinking pauses fight the endpointer; the trailing
  silence bound decides whose sentence wins.
- Follow-up timing interacts with reply completion and the barge-in
  refractory window.
- Echo behaviour is a property of the device, not the voice: on
  hardware with acoustic echo cancellation the assistant's own voice
  comes back below the ambient floor, whoever is speaking.

This layer survives swapping any provider. It does not survive a
change of device (different or absent echo cancellation) or a
redesign of the input pipeline, which is why such changes re-run more
of the suite.

### The calibration (the current stack, and what to optimize on it)

Every tuned constant and every measured provider behaviour. Valid
precisely as long as the stack that produced it:

- Timeouts chosen against a provider's latency distribution (the
  first-token watchdog default).
- Workarounds for a provider's failure modes (the transcription
  prompt echoed back as the transcript, and the retry that recovers
  real speech from it).
- Language pinning behaviour, measured per ASR model.
- Endpointer and barge-in thresholds, tied to the VAD, the
  microphone, and the rooms measured.

Once a deployment settles on a stack, this is the layer with room to
optimize: the distributions the captures produce (first-token times,
utterance speech_ms on fired versus suppressed barge-ins, ASR retry
outcomes) are exactly the data those constants should be tuned
against, per stack, not in general.

## What a change invalidates

| Change | Re-measure | Still valid |
| --- | --- | --- |
| LLM provider or model | First-token distribution, watchdog default | Everything else |
| ASR provider or model | Prompt behaviour, language pinning, short-clip loss rate | Interaction layer, instrument |
| TTS provider or voice | Reply pacing feel; echo only if the device lacks AEC | Interaction layer, instrument |
| Device or firmware | Echo leakage, VAD and barge-in thresholds, listening-mode behaviour | Instrument, most of the interaction layer |
| Input-pipeline redesign | Endpointing findings, barge-in ladder behaviour | Instrument |

The working procedure for any such change: re-run the relevant
subset (the quiet baseline, the acknowledgement test, one noisy
session; the echo measurement when the device changed) and compare
against the recorded baseline. The manifests make every past
recording attributable to its exact stack, so the comparison is
always available.
