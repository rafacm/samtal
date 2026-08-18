# Extract the turn-taking core and the filler runner from PipelineRuntime

## Goal

Implement issue #141: `runtime/pipeline.py` is 1,820 lines at plan
time (the issue's figure was 1,442 at main@8dd1a5f; #120, #122,
#140 and #155 have since landed on the file), one 47-method class
holding roughly ten responsibilities tied together by cross-cutting
mutable fields. Extract the two clusters with the highest churn
ahead, the turn-taking core and the filler runner, into deep
modules with their own interfaces and test files, strictly
behavior-preserving; everything else stays.

The companion implementation doc,
[`2026-08-18-turn-taking-split-implementation.md`](2026-08-18-turn-taking-split-implementation.md),
records what each milestone actually did, deviations, and
discoveries; a milestone with no deviations says so explicitly. It
also carries the scenario-mapping table the issue requires (each
deleted private-access test mapped to its named replacement).

## The issue's decisions, restated

Settled by issue #141 and not re-litigated here:

1. **Strictly behavior-preserving.** The new interfaces are not
   pre-shaped for #31's event-driven contract; #31 remains its own
   ADR-mandated milestone against the cleaner module.
2. **Two extractions and only two**: the turn-taking core
   (endpointer feed, utterance buffer and tail trim, the barge-in
   gate ladder with its pause/confirm/merge decisions) and the
   filler runner (arm/fire/tail/settle plus the stand-down
   checks). `PipelineRuntime` remains the orchestrator implementing
   `SessionInput` and keeps reply orchestration, conversation
   history, the tool loop, and agent handover.
3. **The provider-observability cluster stays in place**
   (`_watching`/`_watchdog_stream`/`_llm_round`), an explicit
   non-goal. See "The `_watching` reach-in" below for the one place
   it touches an extraction target and how the seam handles it
   without moving the cluster.
4. **Every decision event byte-for-byte**: `barge_in`,
   `barge_in_suppressed`, `barge_in_merged`, `filler_played`,
   `filler_skipped` keep their names, fields, and firing
   conditions exactly (ADRs 2026-08-04 and 2026-08-05).
5. **The SessionInput/DeviceOutput seam does not change shape.**
   Known pressure points (the `_output_paused` mirror,
   `speaking_started_at` reads) may consolidate inside the runtime;
   the boundary protocol is untouched.

The no-behavior-change contract: `tests/unit/test_boundary_contract.py`,
`test_session_characterization.py`, and the integration lane pass
unmodified. `test_session_barge_in.py` and `test_session_filler.py`
are rewritten through the new module interfaces, preserving every
scenario and assertion strength.

## Evidence, re-verified at plan time

The full inventory was retaken at main@3837753 (recorded in the
implementation doc's preamble at M1). The load-bearing facts, and
where they correct the issue:

- **1,820 lines, 47 methods** (the issue's 33 was the count at the
  pin). The turn-taking core is ~240 lines today: the SessionInput
  head at 317-359 plus the contiguous block `_finish_utterance`
  through `_cancel_reply` at 1410-1606, with the gate ladder proper
  at 1469-1561. The filler runner is 168 lines at 1608-1775,
  essentially the size the issue measured.
- **The "drops below roughly 900 lines" criterion was stale, and
  the issue has been amended.** It was derived from the 1,442-line
  file at the pin; the file has since grown 378 lines from work
  the issue itself sequenced after it (#120's recorder, #140's
  tool-source loop, #155's event conformance), so the number and
  the settled two-extraction decision could no longer both hold.
  The plan review round ruled that a plan cannot redefine an
  acceptance criterion on its own authority, so issue #141 now
  carries a dated amendment (2026-08-18) re-baselining the
  criterion to what the number stood in for: both clusters leave
  pipeline.py entirely (landing around 1,450 lines), the gate
  ladder is testable without a PipelineRuntime, and the
  cross-cutting fields that remain are documented in the class
  docstring. This plan implements the amended criterion. The tool
  loop (945-1295, ~350 lines, freshly seamed by #140) is named in
  the amendment as the natural next extraction if the absolute
  count matters on its own; that is outside this issue's settled
  two-cluster scope and is not done here.
- **The session event channel cannot move.** `SessionEvents`
  dispatches through the module-level logger named by the literal
  constant `SESSION_LOGGER = "samtal_server.session"`
  (`events.py:100-113`, deliberately not `__name__`), and the
  conformance walk classifies a site as a session emission by its
  receiver expression `self._events`
  (`test_event_schema_conformance.py:84,311`), not by module. So
  unlike #140's `ServerEvents(__name__)` story, moving these emit
  sites changes no channel and no `events_schema.py` entry. Two
  consequences bind the design: the new modules MUST store the
  events object under the attribute name `self._events`, and the
  conformance sidecar identities (module, enclosing function,
  ordinal) for the nine moved emit sites plus the
  `_speaking_ms_field` spread key must be updated. The suite
  asserts both maps exhaustively in both directions, so it names
  every stale key itself; that assertion, not a hand count, is the
  migration inventory.
- **Cross-cutting fields beyond the four the issue names.** The
  issue lists `_reply_pcm`, `_output_paused`, `_filler_sounding`,
  `_asr_language`. Also crossing clusters today: `_endpointer`
  (written by `_activate_agent` on handover, read by the filler's
  stand-down check), `_reply_task` (created by the turn-taking
  side, read by `drain`/`replying` and five test files),
  `_filler_task` (created by the filler, cancelled by `_reply`'s
  `CancelledError` arm). `_filler_sounding` turns out to be the
  least entangled: it never leaves the filler cluster.
- **The `_watching` reach-in.** `_gate_barge_in` runs its
  confirmation ASR inside `self._watching("asr", ...)` with
  `language_hint=self._asr_language` (pipeline.py:1525-1533), so
  the out-of-scope observability cluster is invoked from inside an
  extraction target. Resolved below by injecting the whole
  confirmation call as one seam; the cluster itself does not move.
- **The recorder does not couple.** #120's `TurnUnderway` is fed
  exclusively from the reply path; neither extraction touches it.
- **Ordinary (non-event) log lines move logger channel.** The
  moved code's `logger.info`/`logger.warning`/`logger.exception`
  lines (the utterance-length line, the barge-in-off drop warning,
  the confirmation-failed line, the filler-playback-failed line)
  ride each new module's own `logging.getLogger(__name__)`, so
  their channel becomes `samtal_server.runtime.turntaking` or
  `.filler_runner`. Grep at plan time found no test or reference
  pinning these channels (the events reference covers declared
  events only, and every decision event rides the invariant
  `samtal_server.session` channel). Recorded here as the one
  observable-surface change this refactor makes, deliberate and
  bounded to ordinary diagnostics.

## Design

### Module layout

Two new sibling modules in `runtime/`:

- `runtime/turntaking.py`, class `TurnTaking`: the utterance
  buffer, tail cap and drop accounting, the endpointer feed and
  VAD sample emission, `finish_utterance`, the gate ladder with
  pause/confirm/merge, the merge buffer, the pre-roll trim, and
  the pause/resume mirror. The name sits next to `runtime/turns.py`
  (the turn *record* accumulator); both module docstrings open by
  naming the other and the distinction (turns.py records what a
  turn contained, turntaking.py decides who holds the floor).
- `runtime/filler_runner.py`, class `FillerRunner`: arm, fire,
  tail, settle, abandon, the stand-down checks, the fire counter
  and phrase rotation. Named `filler_runner` rather than `filler`
  because `samtal_server/filler.py` (clip loading, `FillerClips`,
  the boot-time `filler_disabled` emitter) already exists; a
  same-named runtime module would invite import confusion.
  `filler_disabled` stays where it is: boot-time, server channel,
  out of this issue's scope.

Both modules import only downward (`device.boundary`, `events`,
`providers`, `config`, `audio.resample`, `samtal_server.filler`);
nothing they import reaches back into `runtime/`, so no import
cycle is possible. Module constants `UTTERANCE_TAIL_S` and
`UTTERANCE_TAIL_BYTES` move to `turntaking.py`.

### TurnTaking and the ReplyControl seam

`TurnTaking` is constructed by `PipelineRuntime.__init__` with:

```python
TurnTaking(
    events: SessionEvents,   # stored as self._events, see above
    output: DeviceOutput,    # user_turn_ended, speaking_started_at,
                             # pause_output, resume_output
    server: ServerConfig,    # barge_in, barge_in_min_speech_ms,
                             # barge_in_refractory_ms, utterance_pre_roll_ms
    reply: ReplyControl,     # the runtime, structurally
)
```

`ReplyControl` is a Protocol declared in `turntaking.py`, the
narrow slice of the orchestrator the turn-taking side may touch:

```python
class ReplyControl(Protocol):
    def replying(self) -> bool: ...
    def start_reply(self, pcm: bytes, result: AsrResult | None) -> None: ...
    async def cancel_reply(self) -> None: ...
    async def confirm_transcript(self, pcm: bytes) -> AsrResult: ...
```

`PipelineRuntime` implements it structurally: `replying` exists;
`_cancel_reply` is renamed to `cancel_reply` (it is now a seam two
modules share, and the rename is what the four reach-in sites in
`test_session_watchdog.py` and `test_session_record.py` update
to); `start_reply` wraps the `asyncio.create_task(self._reply(...))`
line so the task object, `drain`, and `replying` stay exactly where
every reader and five test files expect them; `confirm_transcript`
wraps `self._watching("asr", self._providers.asr)` around
`transcribe(pcm, PIPELINE_SAMPLE_RATE, language_hint=self._asr_language)`.
That one injected call is how the gate ladder keeps its
confirmation ASR while the observability cluster and
`_asr_language` stay in the runtime untouched. The ladder's
`except Exception` around the call (log, resume, drop) moves with
the ladder.

State moving into `TurnTaking`: `endpointer` (a public attribute;
`_activate_agent` assigns it on handover exactly as it assigns
`_endpointer` today, and tests seed it the same way), `_utterance`,
`_utterance_dropped`, `_reply_pcm` (the mid-ASR merge buffer),
`_output_paused`.

Public interface (the runtime's SessionInput methods become
one-line delegations; nothing about the boundary protocol
changes):

- `async feed(pcm)`: the body of today's `audio` (None-endpointer
  guard, buffer, tail cap, endpointer feed, VAD sample, endpointed
  finish).
- `restart()`: today's `_reset_utterance`, called by
  `listen_started`, `device_aborted`, and `_activate_agent`.
- `async manual_stop()`: today's `listen_stopped` body (finish
  only if something is buffered).
- `async finish_utterance(endpointed=False)`: moves whole,
  including the `start_reply` handoff and merge-buffer write.
- `clear_pending()`: clears the merge buffer; called from
  `_reply` at the two sites that null `_reply_pcm` today (after
  its ASR returns, and in the `finally`).
- `speech_ms() -> int`: the rounded, None-safe endpointer read
  used by `finish_utterance` today and by the filler's stand-down
  check tomorrow.
- `output_paused -> bool` (read-only property): the pause mirror,
  written only by the ladder's own pause/resume, read by the
  filler.

Private and moving whole: `_gate_barge_in`, `_speaking_ms_field`,
`_trimmed_utterance`, `_pause_output`, `_resume_output`.

### FillerRunner and the TurnView seam

`FillerRunner` is constructed with:

```python
FillerRunner(
    events: SessionEvents,          # stored as self._events
    output: DeviceOutput,           # speaking_started_at, begin_speaking,
                                    # output_sample_rate, encode_audio,
                                    # flush_encoder, send_audio
    fillers: dict[str, FillerClips],
    agents: Sequence[str],          # the runtime's bound-agent list
    turn: TurnView,                 # the TurnTaking instance, structurally
)
```

`TurnView` is a Protocol declared in `filler_runner.py` with
exactly the two reads the fire-time stand-down makes:
`speech_ms() -> int` and `output_paused -> bool`. This is the
issue's `_output_paused` mirror pressure point resolved inside the
runtime: one writer cluster, one reader, one read-only property
crossing between them, and the DeviceOutput boundary stays free of
the query, as its comment demands. The active agent is read as
`self._events.agent`, the same source the runtime's `_agent`
property reads today.

State moving in: `_fillers`, `_filler_task`, `_filler_sounding`,
`_filler_fires`. Public interface, replacing the four private
methods:

- `arm()`: today's `_arm_filler`.
- `async tail()`: today's `_filler_tail`, called from
  `_send_reply_audio`.
- `async settle()`: today's `_settle_filler`, called from
  `_reply`'s `finally`.
- `abandon()`: the fire-without-await cancel `_reply`'s
  `CancelledError` arm does today (pipeline.py:805-806), so the
  task field stops being reached into from outside.
- `armed -> bool`, `sounding -> bool`, `fires -> int` (read-only
  properties): the observation surface the rewritten filler suite
  asserts through instead of `_filler_task is None`,
  `_filler_sounding`, and `_filler_fires` reach-ins.

The clip send keeps going straight to `_output.send_audio` (not
through the runtime's `_send_reply_audio`), preserving the
does-not-wait-on-itself arbitration, and the
batch-built-whole-before-the-first-await invariant moves verbatim;
`test_session_characterization.py` pins both and passes
unmodified. The `except (DeviceGone, RuntimeError)` arm moves
byte-identical; its comment's deferral now points at #182, filed
for the narrowing decision this refactor must not make.

### Considered and declined

- Consolidating the doubled `speaking_started_at()` reads in the
  refractory check and `_speaking_ms_field`: both pairs land on
  one loop tick, so the race is theoretical; touching it is a
  behavior edit with no bug to fix. Declined to keep the move
  verbatim.
- Forwarding shims on `PipelineRuntime` for the moved privates
  (`_finish_utterance`, `_endpointer`, `_utterance`) so old tests
  pass untouched: scaffolding that would preserve exactly the
  reach-in habit this issue exists to end. The suites the issue
  marks for rewrite are rewritten; other files relocate their
  setup expressions mechanically (inventory below).

## Tests

Three tiers, named per file:

**Byte-unmodified (the no-behavior-change proof):**
`test_boundary_contract.py`, `test_session_characterization.py`,
the whole integration lane, `test_event_surface_pins.py`'s
assertion bodies (its setup reach-ins relocate, below), and the
generated references (`docs/reference/events.md` regenerated and
diffed empty; the channel invariant predicts zero drift).

**Rewritten through the new interfaces (the issue's mandate):**

- `test_session_barge_in.py`: the websocket-driven tests (about
  half the file) touch no privates and stay as they are. The
  private-access tests re-seed state through `TurnTaking`
  (`runtime._turntaking.endpointer = ScriptedEndpointer(...)`,
  buffer seeding via the public `feed`, finishes via
  `finish_utterance`) and keep their end-to-end assertions
  (`runtime._turns`, event records) at full strength.
- `test_session_filler.py`: same treatment; task-state assertions
  move from `_filler_task`/`_filler_sounding`/`_filler_fires`
  reach-ins to the `armed`/`sounding`/`fires` properties.
- New `tests/unit/test_turntaking.py`: the gate ladder driven
  directly, no PipelineRuntime constructed, with a fake
  `ReplyControl` and `ScriptedEndpointer`; one scenario per rung
  (floor, merge, refractory, empty-confirm resume, confirmed
  cancel) plus the tail cap and pre-roll trim arithmetic through
  `feed`, which today has no direct test at all.
- New `tests/unit/test_filler_runner.py`: arm/fire/tail/settle
  and both stand-downs driven directly against a fake `TurnView`
  and a recording `DeviceOutput` fake.

Every deleted private-access test appears in the implementation
doc's scenario-mapping table with its named replacement.

**Mechanically relocated setup, assertions untouched:**
`test_event_surface_pins.py` (reach-ins to `_utterance`,
`_finish_utterance`, `_endpointer`, `_pause_output`,
`_resume_output` become their `_turntaking` counterparts; test
names must not change, the conformance sidecar addresses them),
`test_session.py` (one `_utterance` site),
`test_session_watchdog.py` and `test_session_record.py`
(`_cancel_reply` to `cancel_reply`). `tests/support/sessions.py`
touches only `_reply_task`/`_reply`, which do not move, and needs
no edit. Test assets from #144 (`ScriptedEndpointer`,
`ConfirmingAsr`, `GatedAsr`, `BrokenTts`, the config and session
helpers) are reused as they are; `ScriptedEndpointer` satisfies
the endpointer shape structurally and needs no registration.

## The standing review lenses, answered

- **No-leak.** No new retained surface is created and no message
  text changes. The moved `except Exception` arms keep their
  exact shape (the confirmation-failure line logs no exception
  text beyond `logger.exception`'s existing traceback on an
  in-process failure, unchanged from today; the reply path's
  class-name-only discipline is untouched). The sentinel suites
  from #144 pass unmodified.
- **Pin before reshaping.** The surface is already pinned by the
  #144/#155 machinery: every moved decision event has a named pin
  in `test_event_surface_pins.py` (asserting `record.msg` and
  typed args), the filler/encoder ordering is characterized, and
  the conformance suite maps every emit site. No new pins are
  needed before the move; the proof is those files passing with
  assertion bodies byte-unchanged.
- **Closed sets.** No reason token or event field is added,
  removed, or re-homed to a different decision site; the sidecar
  identity updates relocate the site labels only, and the
  conformance suite's two-way exhaustive assertions are the check.
- **Honest seams.** `ReplyControl` and `TurnView` are Protocols
  with no defaults to pin; every optional-shaped comparison moves
  verbatim (`is not None` throughout today's code). The
  `endpointer` attribute keeps its `None` initial state and
  None-safe reads exactly as today.
- **Inventories by tooling.** The reach-in relocation list above
  was produced by grep at main@3837753 and is re-verified in each
  milestone by `grep -rn "_finish_utterance\|_endpointer\|_utterance\b\|_filler_task\|_filler_sounding\|_filler_fires\|_pause_output\|_resume_output\|_cancel_reply\|_reply_pcm\|_output_paused" tests/`
  expecting only the new-form sites; the conformance suite's
  exhaustive maps are the emit-site inventory.

## Risks and mitigations

- **A missed conformance identity or spread key.** The suite
  asserts its maps exhaustively in both directions and fails
  naming the stale key; run it first after each move and follow
  its output, never a hand-maintained list.
- **The filler characterization pins are strict about encoder
  feed order across the moved boundary.** The clip pipeline moves
  verbatim in one commit with no reflow, and
  `test_session_characterization.py` runs before and after that
  commit in the milestone's own verification.
- **Handover writes turn-taking state mid-reply.** The
  `_activate_agent` sites become `turntaking.endpointer = ...`
  plus `turntaking.restart()`, the same two effects in the same
  order; the switch-mid-reply scenarios in the barge-in and
  session suites cover it.
- **Behavior hiding in delegation.** Every SessionInput method
  keeps its today's guard semantics (`listen_stopped`'s
  buffered-only finish, `audio`'s None-endpointer return) inside
  the moved body, so the delegating method adds no condition of
  its own.

## Milestones

- [ ] **M1: the turn-taking core.** Add `runtime/turntaking.py`
  (state, feed, tail cap, gate ladder, merge, trim, pause mirror,
  `ReplyControl`), delegate from `PipelineRuntime`
  (`start_reply`/`cancel_reply`/`confirm_transcript` seams,
  SessionInput one-liners), update the conformance sidecar
  identities and the `_speaking_ms_field` spread key, rewrite
  `test_session_barge_in.py`'s private-access tests through the
  new interface, add `tests/unit/test_turntaking.py`, relocate
  setup reach-ins in `test_event_surface_pins.py`,
  `test_session.py`, `test_session_watchdog.py`,
  `test_session_record.py`, regenerate the events reference
  expecting an empty diff, CHANGELOG entry, implementation-doc
  section with the M1 half of the scenario-mapping table.
- [ ] **M2: the filler runner.** Add `runtime/filler_runner.py`
  (`FillerRunner`, `TurnView`, the observation properties),
  delegate from `PipelineRuntime` (`arm`/`tail`/`settle`/
  `abandon` call sites), point the moved exception-arm comment at
  #182, rewrite `test_session_filler.py`, add
  `tests/unit/test_filler_runner.py`, write the class docstring's
  cross-cutting-field inventory for what remains in
  `PipelineRuntime`, record the final line count against the
  re-baselined criterion, CHANGELOG entry, implementation-doc
  section with the M2 half of the scenario-mapping table.

Each milestone is a stacked branch off the previous one
(`feature/turn-taking-split-m1` off this plan's branch, `-m2` off
`-m1`), merged to `main` by rebase via its own PR after its own
external review round; every merge leaves `main` releasable
because every milestone is behavior-preserving and fully tested.

## Plan review round

External review: codex-cli 0.147.0, model gpt-5.6-sol, 2026-08-18,
against commit ea5d34e. Six findings; verdict "not ready" pending
the amendments below.

1. **P1: the plan unilaterally drops an acceptance criterion.**
   The issue requires pipeline.py below roughly 900 lines; the
   plan substitutes a ~1,450 target on its own authority. The two
   requirements cannot both hold, but a plan cannot silently
   redefine an acceptance criterion; #141 itself must be amended
   explicitly before implementation.

   *Resolution*: issue #141 now carries a dated 2026-08-18
   amendment re-baselining the criterion (and its acceptance
   checkbox points at it); the plan's evidence section cites the
   amendment instead of redefining the number itself.

2. **P1: M1 removes state the still-in-place filler uses.** M1
   moves `_endpointer` and `_output_paused` into `TurnTaking`
   while the filler stays in `PipelineRuntime` until M2 and reads
   both directly (pipeline.py:1678, 1690); M1 also leaves
   `test_session_filler.py`'s setup reach-ins to those fields
   unrelocated. M1 as written raises `AttributeError` in
   filler-enabled deployments and fails the unit suite,
   contradicting per-milestone releasability. M1 must adapt the
   in-place filler reads to `self._turntaking.speech_ms()` and
   `.output_paused` and relocate those filler-test setup sites; M2
   then moves already-adapted code.

3. **P2: the conformance inventory omits the TOKEN_SOURCES map.**
   Beyond the sidecar identities and the spread key,
   `test_event_schema_conformance.py:1081-1095` pins the
   closed-token decision sources for `barge_in_suppressed.reason`
   and `filler_skipped.reason` to the old module and scopes; both
   entries go stale (one in M1, one in M2) and belong in the named
   inventory.

4. **P2: the ordinary-log channel claim is factually wrong.**
   pipeline.py imports `logger` from `samtal_server.events`
   (pipeline.py:60), so the moved diagnostics already ride the
   fixed `samtal_server.session` channel; the plan's plan to give
   the new modules `getLogger(__name__)` loggers would be the
   behavior change, not the preservation. The new modules must
   import the same shared logger.

5. **P2: the no-leak claim overstates coverage.** The
   confirmation-failure catch (`logger.exception`,
   pipeline.py:1534-1536) retains the provider exception's text
   and chain; the failed-confirmation test raises a message-free
   `TimeoutError` and searches no sentinel, and the #144 sentinel
   suites exercise `_reply`'s separate class-name-only arm. This
   is a pre-existing gap to track in its own issue and move
   verbatim, not a surface the plan may claim proven safe.

6. **P3: `ScriptedEndpointer`'s docstring becomes false.** It
   says feeding is never exercised; the rewritten suites and the
   new direct tests drive `feed`. The mechanical docstring update
   belongs in the plan.
