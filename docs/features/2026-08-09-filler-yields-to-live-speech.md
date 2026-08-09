# The filler yields to live user speech

**Date:** 2026-08-09

## Problem

Field round 2 (the first two days of the latency-masking deployment
from issue #74) found the filler interrupting the user. The timer is
armed only after ASR transcribes an utterance, so every fire follows
an endpointer decision that the turn was over; but when that decision
lands on a mid-sentence thinking pause (a premature endpoint, common
in dictation-style turns), the user is still talking when the delay
expires. `_run_filler` checked exactly one thing before playing,
whether reply audio had started, and never consulted the endpointer,
so the clip played into the user's continuation.

Analysis of 48 h of structured events: 20 fires, of which 4 started
1.4 to 1.8 s into user speech already underway (measured from the
following `barge_in` event's time minus trailing silence minus
`speech_ms`), all in dictation-style turns. In the worst case the
continuation had already endpointed and was inside the gate ladder's
ASR confirmation when the clip fired, 52 ms before that confirmation
cancelled the reply the clip was masking. The other 16 fires (6
handover masks, the rest slow greetings and tool-call turns) worked
as designed. The #74 design considered a user talking over the
filler ("interrupting the reply, the correct reading") but not the
filler starting inside a turn the user had never finished.

## Changes

Two fire-time checks in `_run_filler`, between the existing
`speaking_started` check and the clip lookup, each standing the
timer down with a `filler_skipped` event:

- `user_speaking`: the endpointer holds unresolved speech
  (`speech_ms() > 0`, which resets when an utterance resolves, so
  nonzero means speech underway or just trailed off unresolved).
- `barge_in_pending`: a barge-in confirmation has the outgoing
  frames paused (`_pace_paused_at` set), meaning the continuation
  already endpointed and the reply this clip would mask is about to
  be cancelled.

Skipped, not deferred: one filler per turn stays the rule, the skip
consumes no phrase from the rotation, and the reply that answers the
completed sentence arms its own timer.

## Key parameters

None new. The checks read existing state; `delay_ms`, the phrase
rotation, and the arming rule are unchanged.

## Verification

- Unit: the two new cases in `tests/unit/test_session_filler.py`
  (live speech at fire time, paused pacing at fire time), plus the
  existing eleven filler tests unchanged. Full unit and integration
  suites pass (710 + 27).
- Field: needs a deploy and a dictation-style session; the signature
  of success is `filler_skipped` events where round 2 produced the
  overlapping `filler_played` ones.

## Files modified

- `samtal-server/samtal_server/session.py`
- `samtal-server/tests/unit/test_session_filler.py`
- `samtal-server/README.md`
- `CHANGELOG.md`
