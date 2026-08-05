# Gate barge-in behind evidence of user speech

## Problem

Issue #28: barge-in cancelled the reply in flight on a single VAD
trip, in any phase of the reply. From the reporting deployment's logs,
three shapes: noise or TTS bleed cut replies 1.3 to 2.2 s into
playback; the acoustic aftermath of the user's own utterance
re-triggered the reset endpointer and the cancel landed in the
post-ASR, pre-first-frame window, so the user got silence; and a
barge-in landing while the reply was still transcribing destroyed the
head of the user's own sentence, keeping only the tail. The last is a
data-loss bug independent of noise.

## Changes

- The `Endpointer` protocol gained `speech_ms()`: milliseconds
  classified as speech since the last reset, reported by both
  implementations at their own granularity (Silero's 32 ms window, the
  mock's fed chunk), following the `speech_start()` pattern.
- An utterance that endpoints while a reply is in flight passes four
  gates, in order: minimum speech (below
  `server.barge_in_min_speech_ms` it is dropped and the reply lives);
  mid-ASR merge (a reply still transcribing is cancelled and its
  already-trimmed PCM prepended, so one reply answers the whole
  sentence and `heard`'s `duration_s` covers the combined buffer);
  refractory (within `server.barge_in_refractory_ms` of
  `speaking_started` it is dropped); transcript confirmation
  (otherwise the outgoing frame pacing pauses, ASR transcribes the
  interruption in the receive path, and only a non-empty transcript
  cancels; the new reply reuses the full `AsrResult`, language lock
  included, so ASR runs once and `heard` fires once; an empty
  transcript resumes the stream with the pacing clock shifted by the
  pause).
- The gates apply to endpointer-driven utterance ends only: a manual
  `listen stop` mid-reply keeps the unconditional cancel, and
  `server.barge_in: false` still drops frames before decode.
- New events `barge_in_suppressed` (`reason`: `min_speech`,
  `refractory`, or `no_transcript`, plus `speech_ms`) and
  `barge_in_merged` (`speech_ms`); `barge_in` gained `speech_ms` and
  `speaking_ms`. Events are a compatibility surface per the
  observability ADR; the decision itself is recorded in
  `docs/adr/2026-08-05-replies-cancel-only-on-evidence-of-speech.md`.
- The deployment profile documents both keys commented, names the VAD
  `threshold` as the companion knob for noisy rooms, and adds a
  caution to its `trailing_silence_ms` suggestion, since shorter
  trailing silence makes mid-sentence chopping likelier.

## Key parameters

| Option | Default | Notes |
|---|---|---|
| `server.barge_in_min_speech_ms` | 500 | least classified speech that may interrupt |
| `server.barge_in_refractory_ms` | 1000 | interruptions ignored after playback starts |

## Verification

- Endpointer units: `speech_ms()` counts only speech-classified
  windows or chunks, pauses add nothing, reset clears, both
  implementations.
- Session units, one per gate: a 240 ms blip is suppressed
  (`min_speech`) and the reply plays whole; sustained speech inside a
  raised refractory window is suppressed (`refractory`); a barge-in
  during transcription merges, with the merged length pinned exactly
  through the duration the mock ASR embeds (320 ms head plus 480 ms
  tail heard as 800 ms); an unconfirmed barge-in pauses the frames,
  resumes the same reply, and shifts the pacing clock by at least the
  pause; a confirmed barge-in runs ASR once (two calls total),
  reuses the transcript and the language lock, and `heard` fires once.
- Unchanged behavior pinned: a manual `listen stop` mid-reply cancels
  with every gate raised sky-high, and the existing
  `barge_in: false` test still passes untouched.
- Full unit suite green locally (501 passed); integration suite green
  (27 passed); lint clean.
- Not verified here, hardware or field only: paused-then-resumed Opus
  playback on the board, the noisy-room desk repro, and the operator
  re-measurement from the reporting deployment, for which the
  `speech_ms` on the new events is exactly the tuning data.

## Files modified

- `samtal-server/samtal_server/providers/base.py`
- `samtal-server/samtal_server/providers/silero.py`
- `samtal-server/samtal_server/providers/mock.py`
- `samtal-server/samtal_server/config/models.py`
- `samtal-server/samtal_server/session.py`
- `samtal-server/config.example.yaml`
- `samtal-server/config.deploy.example.yaml`
- `samtal-server/README.md`
- `samtal-server/tests/unit/test_providers_silero.py`
- `samtal-server/tests/unit/test_providers_energy_endpointer.py`
- `samtal-server/tests/unit/test_session.py`
- `samtal-server/tests/unit/test_session_barge_in.py`
- `docs/adr/2026-08-05-replies-cancel-only-on-evidence-of-speech.md`
- `CHANGELOG.md`
