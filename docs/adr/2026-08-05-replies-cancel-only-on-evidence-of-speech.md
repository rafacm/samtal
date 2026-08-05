# A reply is only cancelled on evidence of user speech

**Status:** Accepted

## Context

Barge-in as first built cancelled the reply in flight on a single VAD
trip: any utterance that endpointed while a reply was streaming
replaced it, in any phase of the reply. Field logs from the reporting
deployment ([#28](https://github.com/rafacm/samtal/issues/28)) showed
what that costs. Noise and playback bleed cut replies off seconds into
playback; the acoustic aftermath of the user's own utterance cancelled
replies in the post-ASR spin-up window and replaced them with silence;
and a barge-in landing while the reply was still transcribing destroyed
the head of the user's own sentence, a data-loss bug independent of
noise. The surveyed systems converged on the same lesson: LiveKit
Agents gates on a minimum sustained-speech duration and resumes speech
when an interruption produces no transcript, pipecat gates on
transcript content while the bot speaks, and upstream xiaozhi's non-AEC
path only ever aborts after a full transcript exists.

The underlying trade-off is real: cancelling fast on acoustics alone
gives the snappiest interruption, but every false positive kills a
reply the user wanted, and a killed reply cannot be un-killed. A pause
can.

## Decision

A reply is only cancelled on evidence of user speech; acoustics alone
can at most pause it.

Concretely: an endpointer-driven utterance end during a reply must
carry a minimum of speech-classified audio, must fall outside the
refractory window after playback starts, and, when nothing cheaper
decides it, must produce a non-empty transcript before the reply dies.
While that transcript is pending, the outgoing frames pause rather than
stop: audio halts just as fast either way, but a wrong decision now
costs one ASR latency of silence instead of a reply. The one exception
is deliberate action: a manual `listen stop` mid-reply is the user
holding the button and speaking, which is evidence enough, and cancels
unconditionally. A reply still inside ASR is a special case of the same
rule: what it holds is the user's own speech, so a barge-in there
merges the audio instead of destroying it.

## Consequences

- Interrupting the assistant costs slightly more than a VAD trip: a
  real interjection must sustain `server.barge_in_min_speech_ms` of
  speech, and one that needs transcript confirmation waits roughly one
  ASR pass before the old reply dies. That is the accepted price of
  never killing a reply on noise.
- Every gate decision is a structured log event (`barge_in`,
  `barge_in_suppressed`, `barge_in_merged`, with `speech_ms`), because
  the thresholds are field-tunable numbers and the retained logs are
  the observability surface
  (see [the observability ADR](2026-08-04-json-logs-are-the-observability-surface.md)).
- The frame pacer must be pausable and resumable without disturbing
  the cadence, which is why the pacing clock shifts by the pause
  duration on resume.
- Future echo defenses (audio-domain correlation, comparing a
  confirmed transcript against the assistant's own recent sentences)
  slot in as additional evidence checks behind the same rule rather
  than replacing it.
