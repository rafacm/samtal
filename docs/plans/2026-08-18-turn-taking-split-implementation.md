# Extract the turn-taking core and the filler runner from PipelineRuntime: implementation

Companion to
[`2026-08-18-turn-taking-split.md`](2026-08-18-turn-taking-split.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## The inventory, retaken at main@3837753

Issue #141's evidence is pinned to main@8dd1a5f, before #120's
recorder, #140's tool-source loop and #155's event conformance landed
on the file. The figures below were retaken at 3837753, the commit
this plan's branch is based on, and they are what the plan's evidence
section cites. Recorded here because both milestones' designs rest on
them, and because a number that moves under a later milestone is a
finding rather than a typo.

**1,820 lines, 47 methods on one class.** The issue's 33 was the count
at its own pin.

**The turn-taking core is about 240 lines**: the `SessionInput` head at
317-359 (`audio`, `listen_started`, `listen_stopped`,
`device_aborted`), plus the contiguous block `_finish_utterance`
through `_cancel_reply` at 1410-1606, with the gate ladder proper at
1469-1561.

**The filler runner is 168 lines** at 1608-1775 (`_arm_filler`,
`_run_filler`, `_filler_tail`, `_settle_filler`), essentially the size
the issue measured.

**Nine emit sites move**, all of them session-channel: one in
`_finish_utterance` (`barge_in`, the unconditional cancel), five in
`_gate_barge_in` (`barge_in_suppressed` three times, `barge_in_merged`,
`barge_in`), and three in `_run_filler` (`filler_skipped` twice,
`filler_played`). The first six move in M1, the last three in M2.

**The channel does not move with them.** `SessionEvents` dispatches
through the module-level logger named by the literal
`SESSION_LOGGER = "samtal_server.session"` (`events.py:111`), and the
conformance walk classifies a site as a session emission by its
receiver expression `self._events`
(`test_event_schema_conformance.py:84,311`), never by module. The
ordinary log lines in the moved code are on the same channel for the
same reason: `pipeline.py` imports `logger` from `samtal_server.events`
rather than building a module logger.

## M1: the turn-taking core

`samtal_server/runtime/turntaking.py` (339 lines) holds `TurnTaking`
and the `ReplyControl` Protocol. What moved, verbatim: the utterance
buffer with its tail cap and drop accounting and the `UTTERANCE_TAIL_S`
/ `UTTERANCE_TAIL_BYTES` constants, the endpointer feed and the VAD
sample, `finish_utterance`, the gate ladder, `_speaking_ms_field`,
`_trimmed_utterance`, `_reset_utterance` (now `restart`),
`_pause_output` / `_resume_output`, the `_reply_pcm` merge buffer and
the `_output_paused` mirror.

`PipelineRuntime` is 1,616 lines and 42 methods, and is the
orchestrator: it constructs `TurnTaking` in `__init__`, passing itself
as the `ReplyControl`; `audio`, `listen_started` and `listen_stopped`
are one-line delegations with today's guard semantics preserved inside
the moved bodies; `device_aborted` cancels and restarts. `_cancel_reply`
is now the public `cancel_reply`, `start_reply` wraps the
`asyncio.create_task(self._reply(...))` line so `_reply_task`,
`replying` and `drain` stay exactly where five test files expect them,
and `confirm_transcript` wraps `self._watching("asr", ...)` around the
transcription with `language_hint=self._asr_language`. That last one is
how the gate ladder keeps its confirmation ASR while the out-of-scope
provider-observability cluster and the session's language lock stay in
the runtime.

The filler runner stays in `pipeline.py` this milestone with its two
stand-down reads adapted in place, to `self._turntaking.speech_ms()`
and `self._turntaking.output_paused` (plan review finding 2). A
filler-enabled deployment works at the M1 merge, and M2 moves
already-adapted code.

### Conformance keys relocated

The suite asserts its maps exhaustively in both directions and names
every stale key itself, so the migration inventory was its own output
rather than a hand count. Relocated:

| Key at 3837753 | Key after M1 |
| --- | --- |
| `…pipeline:PipelineRuntime._speaking_ms_field` (SPREADS) | `…turntaking:TurnTaking._speaking_ms_field` |
| `("barge_in_suppressed", "reason")` → `…pipeline`, scope `PipelineRuntime._gate_barge_in` (TOKEN_SOURCES) | `…turntaking`, scope `TurnTaking._gate_barge_in` |
| `(…pipeline, PipelineRuntime._finish_utterance, 1)` | `(…turntaking, TurnTaking.finish_utterance, 1)` |
| `(…pipeline, PipelineRuntime._gate_barge_in, 1..5)` | `(…turntaking, TurnTaking._gate_barge_in, 1..5)` |

Six sidecar identities, one spread key, one `TOKEN_SOURCES` entry. The
`filler_skipped` reason entry and the three `_run_filler` identities
stay put until M2.

The `self._events.vad(...)` site carries no identity to move: it names
no `event=` keyword and `vad` is not one of the walk's levels, so it is
not an emit site at all as the conformance suite counts them. Checked
rather than assumed.

`samtal_server/events_schema.py` was not touched, and no assertion in
`test_event_surface_pins.py` was touched.

### Scenario mapping

`test_session_barge_in.py`'s three websocket-driven tests touch no
privates and are byte-unchanged. Its four private-access tests were
rewritten in place through the new interface, each keeping its name,
its scenario and every one of its assertions; none was deleted and none
lost strength, so each maps to itself.

| Test at 3837753 | After M1 | How its setup changed |
| --- | --- | --- |
| `test_a_barge_in_during_transcription_merges_the_sentence` | same name, same assertions | endpointer via `_turntaking.endpointer`; both buffer seedings via the public `_turntaking.feed(...)`; both finishes via `_turntaking.finish_utterance(...)` |
| `test_an_unconfirmed_barge_in_pauses_and_resumes_the_reply` | same name, same assertions | as above; the finish stays wrapped in `asyncio.create_task` so the pause window is still observed from outside |
| `test_a_failed_confirmation_is_reported_as_the_provider_failure_it_is` | same name, same assertions | as above |
| `test_a_confirmed_barge_in_reuses_the_transcript_and_the_lock` | same name, same assertions | as above |

Nine scenarios in the new `tests/unit/test_turntaking.py` drive the
same code with no `PipelineRuntime` constructed at all: one per rung of
the ladder (min-speech floor, mid-ASR merge, refractory window,
empty-confirm resume, confirmed cancel), one for the confirmation that
could not be run, and three for the arithmetic in front of the gates
(the tail cap, the pre-roll trim, and the trim mapped through what the
cap dropped). The last three are new coverage: `feed` had no direct
test before.

Mechanically relocated setup, assertion bodies and test names
untouched: `test_event_surface_pins.py` (14 sites),
`test_session_filler.py` (4), `test_session.py` (2, one of them the
`UTTERANCE_TAIL_BYTES` monkeypatch, which now patches the turntaking
module), `test_session_record.py` (2) and `test_session_watchdog.py`
(1) for the `cancel_reply` rename.

### Deviations from the plan

Three, all cosmetic, none touching behavior or the plan's decisions.

1. **`TurnTaking` keeps a public `session_id`**, which the plan's
   interface list does not mention. Every moved log line and event
   renders `self.session_id` as its first argument, and the conformance
   walk reads argument expressions from the source; keeping the
   expression identical was worth one attribute the plan did not
   enumerate.
2. **Two docstring identifiers were updated rather than moved
   verbatim.** `finish_utterance`'s docstring said `_cancel_reply`
   waits for the task it cancelled, and one pinned test's docstring
   named `_finish_utterance`; both now name the method that exists.
   Nothing else in a moved docstring changed.
3. **A stray comment was un-orphaned.** At 3837753 the
   `MAX_TOOL_ROUNDS` comment sat three lines above the
   `UTTERANCE_TAIL_S` comment with no constant between them, so moving
   the utterance constants out reunited `MAX_TOOL_ROUNDS` with its own
   comment. No text changed.

The plan's "considered and declined" items stayed declined: the doubled
`speaking_started_at()` reads were not consolidated, and no forwarding
shim was added to `PipelineRuntime`.

### Discoveries

- **`feed` is a complete substitute for the buffer reach-in.** The
  rewritten suites seed through the public `feed` rather than through
  `TurnTaking` internals, because every seeding site in
  `test_session_barge_in.py` starts from an empty buffer (an
  activation, or the `restart` that `finish_utterance` runs). The
  `_events.vad(...)` sample `feed` emits is capture-only and reaches no
  log record, so nothing a suite asserts on sees the difference. The
  plan allowed reaching inside where `feed` could not express the
  seeding; that case did not arise in this file.
- **`ScriptedEndpointer` needed one thing the plan did not name.** Its
  `speech_start()` answers None, which is right for the barge-in
  scenarios and useless for the pre-roll trim. `test_turntaking.py`
  subclasses it locally rather than growing the shared fake, since one
  suite needs the number and the plan named only the docstring change
  for `tests/support/providers.py`.
- **`tests/support/boundary.py`'s `FakeDevice` was already the
  recording `DeviceOutput` the direct tests needed**, down to
  `speaking_started_at()` becoming non-None on the first sent batch,
  which is exactly how the refractory window is armed. No new fake was
  written for M1.

### Verification

All from `samtal-server/`.

- `uv run ruff check .`: passed.
- `uv run pytest tests/unit -q`: passed.
- `uv run pytest tests/integration -q`: passed.
- `uv run samtal-server events reference` diffed against
  `docs/reference/events.md`: empty, as the channel invariance
  predicted.
- `test_boundary_contract.py` and `test_session_characterization.py`
  passed byte-unmodified, confirmed with `git diff --stat`.
