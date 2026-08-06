# Synthesize the next sentence while one is still speaking

## Problem

Issue #37: a reply is split into sentences, and each sentence was
synthesized and spoken before the next one was requested. Nothing
overlapped, so every sentence boundary in a multi-sentence reply exposed
the next sentence's full time to first byte as silence on the speaker.
On a fast local engine this is invisible. On a slow cloud provider it is
audible as a stutter, once per sentence, for the whole reply.

The path was strictly sequential: the round loop awaited
`_speak_and_record` per sentence, `_speak` synthesized *and* sent before
returning, and `_send_frames` paces to realtime, so "sent" means
"played". Sentence N+1's request only started once sentence N had
finished playing.

The pacer made the artifact worse than a plain pause. Its schedule is
absolute from the reply's first frame, so once a stall pushed it behind,
the following frames got a negative sleep and burst out to catch up. The
device got a dropout followed by a flood rather than a cleanly stretched
reply.

Found while verifying the OpenAI TTS provider on hardware, and reported
as "hiccups in the assistant's voice". Not a defect in that provider: it
is the first one slow enough for the existing gap to become audible.

## Changes

- A new `_Synthesis`: one sentence being turned into audio, started
  before the moment it is needed. A task drains the provider into a
  buffer as fast as the provider will give it; `chunks()` yields what
  has arrived and waits for the rest, so the first sentence of a reply
  still streams (nothing is held back waiting for a sentence to finish)
  while a sentence run ahead is simply already there.
- `_speak_after` starts the next sentence synthesizing and *then* waits
  for the sentence already being spoken. The first statement coming
  before the first await is the entire fix.
- Speaking is a task rather than an inline await, so it overlaps the
  model still streaming. This was a review finding and it mattered: the
  first attempt awaited inline, which meant a sentence was not spoken
  until the *next* one had been written, putting the model's thinking
  time in front of the first word of every reply and making a
  one-sentence reply wait for the whole stream to end. Time to first
  audio is the latency a listener actually meets and must not move.
- The synthesis buffer holds one chunk, not a sentence. Also a review
  finding: unbounded, a provider producing faster than realtime hands
  over a whole sentence of PCM at once and the paced consumer stops
  applying any backpressure. The bound is a semaphore over outstanding
  chunks rather than a `maxsize` on the queue, which was the first
  attempt and deadlocked: a full queue leaves the drain task blocked
  forever on an end-of-audio sentinel nobody is waiting for, because
  the consumer that would unblock it is the one that went away.
- One sentence of lookahead, not a queue. Every sentence here plays for
  longer than the next takes to start, so one closes the gap, and more
  would only mean more concurrent requests to the provider and more
  audio held for a reply a barge-in may throw away. Asserted directly:
  at most two syntheses are ever in flight.
- Resampling and encoding stay on the consuming side, in order, because
  the resampler and the encoder are stateful and belong to the stream
  rather than to a sentence. Only the provider call is moved.
- `sentence_start` still goes out when a sentence is about to be spoken,
  not when its synthesis began: it tells the device what is being said,
  and what is being said is what is about to be heard.

The four things the issue said the design had to keep working:

**Barge-in cancels mid-reply.** `_speak` still counts a sentence only
after its audio has gone out, and now also takes its own synthesis down
with it, so a sentence cut off stops pulling from the provider rather
than running on behind a reply that no longer exists. A sentence run ahead and then cancelled
is counted nowhere, in neither the turn the model is shown nor the
persisted history. The round's `finally` cancels a synthesis still in
flight, and `_speak_after` cancels the one it just started if speaking
the previous sentence raises. Tested, including that nothing is left
running behind a reply that no longer exists.

**Tool rounds.** The lookahead is drained at the end of each round
before the tools run, so it cannot run past the end of a round into text
that does not exist yet, and tools cannot run over the top of speech.

**Agent handover.** The provider is passed into `_Synthesis` explicitly
rather than read from `self._providers` when the audio is consumed, so a
sentence belongs to the agent leg that started it and a handover cannot
speak in the wrong voice. Tested with two distinct providers.

**The pacer's absolute schedule.** Confirmed rather than assumed, and it
turned out to matter more than expected. See below.

## Key parameters

None. No configuration surface changes: no config schema, no
environment variable, no per-provider option. The lookahead depth is one
sentence and is not tunable, because the argument for one is structural
(a sentence plays for longer than the next takes to start) rather than a
value that wants tuning per deployment.

## Verification

`uv run pytest tests/unit -q`: 617 passed, 2 skipped.
`uv run pytest tests/integration -q`: 27 passed. `ruff check` clean.

### Against the real providers

The issue's measurement, reproduced and then re-run with the fix. Same
three-sentence reply, same machine, same network.

| Provider | | boundary 1 | boundary 2 | total dead air | frames bursting to catch up |
|---|---|---|---|---|---|
| `gpt-4o-mini-tts` | issue's figures | 617 ms | 520 ms | 1138 ms | not measured |
| | control here | 884 ms | 478 ms | 1362 ms | 41 of 169 |
| | **with lookahead** | **0** | **0** | **0** | **5 of 169** |
| `eleven_flash_v2_5` | issue's figures | 131 ms | 111 ms | 242 ms | not measured |
| | control here | 129 ms | 139 ms | 268 ms | 2 of 139 |
| | **with lookahead** | **0** | **0** | **0** | **0 of 138** |

Zero means the next sentence's first frame arrived exactly one frame
after the previous sentence's last, 60 ms, which is the cadence rather
than a gap. The control is this branch with the two statements in
`_speak_after` swapped back, so it is the old behaviour rather than a
recollection of it.

### Why total playing time is not the measure

Both OpenAI runs played for about the same wall time, 10.14 s for the
control against 10.38 s with the fix, despite the control having 1.36 s
of dead air in it. That is the pacer catching up: its schedule is
absolute, so after each stall the following frames have target times
already in the past and go out back to back. The defect hides entirely
in the total and shows only per frame, which is why the tests measure
per gap and per interval. It is also the clearest evidence for the
issue's "dropout followed by a flood": 41 of 169 frames left faster than
half the cadence in the control, against 4 of 173 with the fix.

### Tests

Ten unit tests against a provider that is slow on purpose. Each was run
against a control with the relevant piece reverted:

| Test | Result without the change |
|---|---|
| a sentence starts synthesizing before it starts being spoken | fails: "'Two here.' was still being synthesized when it should already have been waiting" |
| the gap between sentences closes | fails: 307 ms of dead air before the second sentence |
| the frame cadence stays smooth | fails: 8 of 29 frames went out faster than the cadence |
| only one sentence is ever run ahead | fails: 1 in flight, not 2 |
| a sentence run ahead and never spoken is not recorded | fails: nothing was run ahead to begin with |
| lookahead stops at the end of a round | passes either way (guards what must not change) |
| a failing sentence still lets the earlier ones be heard | passes either way (guards what must not change) |
| a handover speaks in the new agent's voice | passes either way (guards what must not change) |
| the first sentence does not wait for the second | fails: "the first sentence waited for the model to write the second" |
| a sentence is not pulled from the provider faster than it plays | fails: 100 of 100 chunks pulled within a quarter second of playback |

Three of them are invariant tests rather than defect tests, which is why
they pass against the sequential control; they exist to catch what this
change could have broken. The last two came from the review and are
run against their own controls: the inline-await structure for the
first, and an unbounded buffer for the second.

One pre-existing behaviour is asserted rather than changed: a sentence
whose synthesis fails is still announced with `sentence_start` before
the failure surfaces. That was true before and is not this change's to
alter, since for a sentence still streaming it is not knowable at
announcement time whether the audio behind it will arrive.

Not verified here:

- **The ear test.** Every number above says the dead air is gone, and
  none of them is a person listening to a long reply through a speaker.
  That is Rafael's, on the board.

## On sequencing

The issue's own comment suggests landing this after #28, on the grounds
that "a lookahead design written before #28 lands is designing
cancellation against semantics that are about to change", while stating
that this issue is not formally blocked.

Done now anyway, deliberately. #28 settles *when* a barge-in fires,
which is an acoustic and tuning question; what the lookahead has to
honour is *what a cancel does to a reply in flight*, which is the
existing `_cancel_reply` contract and is untouched by that tuning. The
rule here is a single sentence long: a sentence is counted only after
its audio has gone out, so one that was never spoken is counted nowhere.
That holds however the decision to cancel is reached. #28 is also
currently blocked on the recording #42 exists to produce, so waiting
would mean waiting indefinitely on a defect that is audible regardless
of how barge-in is tuned.

## Files modified

- `samtal-server/samtal_server/session.py`
- `samtal-server/tests/unit/test_tts_lookahead.py` (new)
- `samtal-server/tests/unit/test_session.py`
- `samtal-server/tests/unit/test_session_events.py`
- `samtal-server/tests/unit/test_session_tools.py`
- `samtal-server/README.md`
- `CHANGELOG.md`
