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

### The PR review round (M1)

External review of PR #184 (codex-cli 0.147.0, gpt-5.6-sol,
2026-08-18, diff main...9425f5e): three findings, verdict
"mergeable after the listed fixes".

1. **P1: confirmation failures leak provider text and tracebacks**
   (the moved `logger.exception` in the gate ladder). Declined as
   a change to this PR, with the plan's own review round as the
   reason: the leak is pre-existing, the issue's settled contract
   is strictly behavior-preserving, and the arm moves verbatim
   with the fix and its sentinel pin tracked as #183. The plan's
   no-leak lens paragraph and the finding agree on the eventual
   fix shape.
2. **P1: the abort path logs a device-supplied `reason`
   unrestricted.** Same classification, newly discovered: the
   line is pre-existing and moved as is; filed as #185 (allowlist
   the reason tokens at the decision site, coarsen the rest,
   sentinel pin), same family as #183 and possibly one PR with it.
3. **P3: the module docstring said nothing here reads a
   transcript while the gate inspects the confirmation text.**
   Fixed in 45e1a3a: the docstring now claims what is true
   (nothing retained, nothing orchestrated, inspection happens).

## M2: the filler runner

`samtal_server/runtime/filler_runner.py` (277 lines) holds `FillerRunner`
and the `TurnView` Protocol. What moved, verbatim: `_arm_filler` (now
`arm`), `_run_filler` (now the private `_fire`), `_filler_tail` (now
`tail`), `_settle_filler` (now `settle`), and the four state fields
`_fillers`, `_filler_task`, `_filler_sounding`, `_filler_fires` with the
constructor comment that explains them. The clip send still goes
straight to `output.send_audio` rather than through the runtime's
`_send_reply_audio`, which is what keeps the arbitration from waiting on
itself, and the batch is still built by three contiguous encoder calls
with no await between them, all of them finished before the send is
awaited. That, rather than "before the first await", is the invariant:
`begin_speaking` is awaited before the encoding starts, in the moved
code as in the original, and what the reply's own encoder feed must not
be able to land in the middle of is the batch.

`TurnView` is the `_output_paused` mirror settled inside the runtime:
`speech_ms()` and a read-only `output_paused`, which the M1 `TurnTaking`
satisfies structurally and which the runner may only ask, never answer.
The two stand-down reads M1 had already adapted in place moved without
further change.

`PipelineRuntime` is 1,484 lines and 38 methods, against 1,820 at
3837753 and the ~1,450 the issue's 2026-08-18 amendment predicted. Its
four call sites are `self._filler.arm()` in `_reply`,
`await self._filler.tail()` in `_send_reply_audio`,
`await self._filler.settle()` in `_reply`'s `finally`, and
`self._filler.abandon()` in the `CancelledError` arm, which replaces the
two-line reach into the task field. The class docstring now carries the
inventory the amended acceptance criterion asks for: the two clusters
that left, and the seven pieces of state that still cross what stayed
(`_reply_task`, `_providers` and `_know_how`, `_asr_language`, `_turn`,
`_turns`, `_llm_round`, and the `_agent` property over the events
object).

The moved `except (DeviceGone, RuntimeError)` arm is byte-identical
except for its comment's last sentence, which pointed at "#141 rather
than #137" and now points at #182, where narrowing the tuple is decided.

### Conformance keys relocated

Four keys, all named by the suite's own exhaustive assertions rather
than by a hand count.

| Key after M1 | Key after M2 |
| --- | --- |
| `("filler_skipped", "reason")` → `…pipeline`, scope `PipelineRuntime._run_filler` (TOKEN_SOURCES) | `…filler_runner`, scope `FillerRunner._fire` |
| `(…pipeline, PipelineRuntime._run_filler, 1..3)` | `(…filler_runner, FillerRunner._fire, 1..3)` |

Three sidecar identities and one `TOKEN_SOURCES` entry. No spread key is
involved: none of the three filler emissions spreads anything.
`samtal_server/events_schema.py` was not touched, and no assertion in
`test_event_surface_pins.py` was touched. Its three filler tests needed
no relocation either: they reach into `_reply_task` and `_turntaking`,
which do not move in this milestone.

### Scenario mapping

`test_session_filler.py` has thirteen tests. Eight touch no moved member
and are byte-unchanged; the five below reached into
`_filler_task`/`_filler_sounding`/`_filler_fires` and were rewritten in
place through the observation properties. None was deleted and none lost
an assertion, so each maps to itself.

| Test after M1 | After M2 | How it changed |
| --- | --- | --- |
| `test_a_fast_reply_plays_no_filler` | same name, same assertions | `_filler_task is None` becomes `_filler.armed is False` |
| `test_a_fire_on_an_agent_without_clips_quietly_plays_nothing` | same name, same assertions | the same, plus `_filler_sounding` and `_filler_fires` becoming `_filler.sounding` and `_filler.fires` |
| `test_a_fire_into_live_user_speech_is_skipped` | same name, same assertions | `_filler_task` and `_filler_fires` become the properties; the `_turntaking` seeding M1 relocated stays as it is |
| `test_a_fire_during_a_barge_in_confirmation_is_skipped` | same name, same assertions | as above, with M1's `_turntaking._pause_output()` setup unchanged |
| `test_the_filler_composes_with_the_first_token_watchdog` | same name, same assertions | `_filler_task is None` becomes `_filler.armed is False` |

Ten scenarios in the new `tests/unit/test_filler_runner.py` drive the
runner with no `PipelineRuntime` constructed at all: the fire that masks
a slow reply, the reply that spoke first, a session bound only to
filler-less agents, an agent bound but with no clip of its own, both
stand-downs, an unfired timer the reply's own audio stands down, a
sounding clip that audio waits out, an abandoned filler, and the phrase
rotation. The seventh and eighth are new coverage in kind: the
session-level suite can see that a clip played, but not that a clip
mid-flight was waited out rather than cancelled.

### Deviations from the plan

Four, three cosmetic and one a naming decision the plan left open.

1. **`FillerRunner` keeps a public `session_id`**, for the same reason
   `TurnTaking` does (M1's deviation 1): every moved log line and event
   renders `self.session_id` as its first argument, and the conformance
   walk reads argument expressions from the source.
2. **The moved private method is `_fire`, not `_run_filler`.** The plan
   names the public four and calls the private one "fire" in prose; a
   `_run_filler` inside a class called `FillerRunner` says the word
   twice. The four state fields keep their `_filler_` prefix, because
   the plan lists them as moving verbatim and their names are what the
   rewritten suite's mapping is read against. The conformance identities
   therefore read `FillerRunner._fire`.
3. **One inline comment identifier was updated rather than moved
   verbatim**, the same class of change as M1's deviation 2: the
   claimed-synchronously comment said `_filler_tail` waits for the
   clip's tail, and now names `tail`. The `_run_filler` docstring's
   `_begin_speaking` reference was already stale before this milestone
   and was left alone.
4. **The runner is handed the constructor's `agents` parameter**, not
   the runtime's `self._agents` list, which is built from it two lines
   later. Equal by construction, and it keeps the construction free of
   an ordering constraint between two fields.

The plan's "considered and declined" items stayed declined: no
forwarding shim was added to `PipelineRuntime`.

### Discoveries

- **The `agent=` field needed no shim.** M1 kept a `session_id`
  attribute so a moved argument expression stayed identical; the
  parallel worry here was `agent=self._agent`, which is a property on
  the runtime. The walk classifies an emission by its receiver
  expression and reads keyword expressions only where a field is a
  closed token set, and `agent` is neither, so the runner reads
  `self._events.agent` directly as the plan specified. Checked in the
  walk rather than assumed, and the suite agrees.
- **`FakeDevice` needed one subclass to make the arbitration
  observable.** The recording device from `tests/support/boundary.py`
  serves the runner as it served `TurnTaking`, but its send returns
  immediately, so a clip is never in flight long enough to catch. A
  ten-line `HeldDevice` that holds one send open until the test releases
  it is what turns "waited out rather than cancelled" into an assertion.
- **`armed` is exactly what the five relocated assertions meant.** Each
  of them read `_filler_task is None` to mean "no filler left over",
  which is what the property answers, so the rewrite is a rename and not
  a weakening.

### Verification

All from `samtal-server/`.

- `uv run ruff check .`: passed.
- `uv run pytest tests/unit -q`: 2,967 passed, 16 skipped.
- `uv run pytest tests/integration -q`: 55 passed.
- `uv run samtal-server events reference` diffed against
  `docs/reference/events.md`: empty, as the channel invariance
  predicted.
- `test_boundary_contract.py` and `test_session_characterization.py`
  passed byte-unmodified against the M1 branch, confirmed with an empty
  `git diff --stat feature/turn-taking-split-m1` over both files.

### The PR review round (M2)

External review of PR #186 (codex-cli 0.147.0, gpt-5.6-sol,
2026-08-18, diff main...cee10a2): three findings, verdict
"mergeable after the listed fixes".

1. **P1: the catch-all filler failure arm leaks exception text**
   (the moved `logger.exception`). Declined here, same
   classification as the M1 round's pair: pre-existing behavior
   moved verbatim under the issue's contract. Recorded as a third
   facet on #182 (the filler path touches no provider, so the
   exposure is in-process text rather than wire bytes, and the
   classification decision there should settle what this arm may
   render, with a sentinel pin).
2. **P2: the abandonment test passed with `abandon()` removed**,
   because `settle()` stands an unfired timer down on its own.
   Fixed in f6c225a: the test now holds a clip mid-send, abandons
   while sounding, and asserts the settle finished without
   waiting for the held send, no audio delivered, no state left.
   Verified to fail without the `abandon()` call. Discovery kept
   in the commit: a `wait_for(settle(), ...)` phrasing also
   passes without `abandon()`, because `settle()` suppresses the
   `CancelledError` that `wait_for` delivers, which is why the
   assertion reads "already finished" rather than "did not time
   out".
3. **P3: "encoded whole before the first await" was imprecise**
   (in the moved code as in the original: `begin_speaking()` is
   awaited first). Fixed in 1d73b9c: comment, plan phrase, and
   implementation-doc sentence now state the actual invariant,
   three contiguous encoder calls completing before the send is
   awaited.
