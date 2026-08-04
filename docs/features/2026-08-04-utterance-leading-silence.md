# Trim utterances to speech plus pre-roll

## Problem

Issue #14: a realtime session never stops listening, so the utterance
buffer accumulates everything between utterances (the reply's own
playback time, the pause while the user thinks) and hands all of it to
ASR. The endpointer correctly ignores silence before speech, so
nothing bounds the lead-in except the 30 s memory guard. On hardware
this produced utterances of 15 to 26 seconds carrying questions of a
few seconds; the operator measurements in #22 confirmed it from the
live deployment and tied all four language misdetections to these
silence-padded turns. `heard`'s `duration_s` had also stopped meaning
"how long the user spoke", which matters because the retained logs are
the transcript store.

The session could not fix this alone: only the endpointer knows where
speech began, and its protocol was `feed() -> bool` plus `reset()`.

## Changes

- The `Endpointer` protocol gained `speech_start()`: the byte offset
  into the fed stream (since the last reset) where speech was first
  heard, None while none has been. Both implementations already
  tracked the fact; now they report the position, to their own
  granularity (Silero's 32 ms window, the mock's fed chunk).
- The session tracks how much the 30 s tail cap has cut from the front
  of the buffer, maps the endpointer's offset onto the remaining
  bytes, and hands ASR the buffer from speech start minus a pre-roll.
  The trailing window the endpointer sat through stays, as before.
- `server.utterance_pre_roll_ms` (default 300) is the configured
  pre-roll from #14's open question. Trims never split a 16-bit
  sample, and an utterance whose speech starts inside the pre-roll is
  passed through untouched, which keeps auto mode (fresh buffer per
  turn) byte-identical to before.

## Key parameters

| Option | Default | Notes |
|---|---|---|
| `server.utterance_pre_roll_ms` | 300 | audio kept from before detected speech |

## Verification

- Endpointer units: silence then speech reports the silence boundary
  (window-granular for Silero via the scripted detector, chunk-granular
  for the mock); a later pause does not move the anchor; reset clears.
- Session acceptance, from #14's criteria: a realtime session sits
  quiet for five seconds, then speaks 600 ms; the mock ASR embeds the
  duration it was handed, and the test pins it to speech plus pre-roll
  plus the trailing window. The five silent seconds are gone.
- `heard`'s `duration_s` is computed from the trimmed audio, so it
  means the speech again.
- Full unit suite green locally (478 passed); lint clean; CI runs the
  same plus integration and the image smoke lane.
- Not verified here: the hardware checkpoint conversation (needs the
  board and a person at the desk); the operator behind #22 can measure
  the effect from the live deployment's logs.

## Files modified

- `samtal-server/samtal_server/providers/base.py`
- `samtal-server/samtal_server/providers/silero.py`
- `samtal-server/samtal_server/providers/mock.py`
- `samtal-server/samtal_server/config/models.py`
- `samtal-server/samtal_server/session.py`
- `samtal-server/tests/unit/test_providers_silero.py`
- `samtal-server/tests/unit/test_providers_energy_endpointer.py`
- `samtal-server/tests/unit/test_session.py`
- `samtal-server/config.example.yaml`
- `CHANGELOG.md`
