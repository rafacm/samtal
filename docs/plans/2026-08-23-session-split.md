# Split device/session.py and delete the seams with no second side

Issue #245, from the 2026-08-22 complexity audit (#246). Deviations,
resolutions and discoveries land in the companion
`2026-08-23-session-split-implementation.md`, one section per
milestone, appended in the change that ticks the milestone.

## Goal

Two cuts the audit priced separately and this plan lands as two
milestones. First the deletions: six one-implementation protocols,
the v2 frame timestamp nothing reads, the `StreamStarted` skip no
production path reaches, and the `random_number` demo tool. Then the
split: `DeviceSession`'s three separable clusters (reply pacing plus
encoder, the capture taps, the idle watchdog) move into their own
modules, behavior-preserving, with the session suites unchanged as
the pin.

## The issue's decisions, restated

- Extract three clusters from `device/session.py`: pacing plus
  encoder, capture taps, watchdogs.
- Delete `ReplyControl`, `TurnView`, `FillerCache`, `Served`,
  `ToolClaim`, `McpManager`; the v2 frame timestamp; the
  `StreamStarted` defensive skip in `_tool_loop`; the
  `random_number` tool.
- Keep `RuntimeFactory` (second side is #84/#92), the earned seams
  (`SessionInput`/`DeviceOutput`, `ToolSource` with its three
  implementations, `Endpointer`, provider ABCs), and leave the
  `DeviceFacts` promotion to #96.

## The census, verified by tooling at `3076407b`

All six delete targets are `typing.Protocol`s; none is
`runtime_checkable`, and no `isinstance` check against any of them
exists in `src/` or `tests/`. Per type:

- `ReplyControl` (`runtime/turntaking.py:55`): one implementor
  (`PipelineRuntime`, structurally), one annotation site
  (`TurnTaking.__init__`), one test double (`FakeReply`,
  `test_turntaking.py`).
- `TurnView` (`runtime/filler_runner.py:41`): one implementor
  (`TurnTaking`), one annotation site (`FillerRunner.__init__`), one
  test double (`FakeTurn`, `test_filler_runner.py`).
- `FillerCache` (`runtime/filler_runner.py:58`): restates the read
  half of `Mapping`; no nominal implementor, no test reference. A
  vestigial guard rides on it: `pipeline.py:351` checks
  `fillers is not None` against an annotation that is not optional.
- `Served` (`filler.py:58`): one implementor (`Generation`), two
  annotation sites in `filler.py`, exported in `__all__`, zero test
  references.
- `ToolClaim` (`tools/source.py:46`): one implementor
  (`conversations/records.ToolInvocation`), thirteen annotation
  sites all inside `source.py`, zero test references; callers
  (`pipeline.py`) already annotate the concrete type.
- `McpManager` (`tools/mcp/manager.py:149`): one implementor
  (`McpServerManager`, same file), ~15 annotation sites across
  `registry.py`, `reload.py`, `slice.py`, the package `__all__`; one
  full test double (`SlowStopManager`, `test_tools_mcp_reload.py`).

The v2 timestamp: `Frame.timestamp` (`protocol/framing.py:34`) is
packed at `wrap` (always the default 0; the one production caller,
`session.py:1183`, passes none) and unpacked at v2 `unwrap`; no
`frame.timestamp` read exists anywhere in `src/`; two assertions in
`test_protocol_framing.py` are its only consumers.

The `StreamStarted` skip (`pipeline.py:1042`): `_tool_loop` reaches
the stream only through `_watchdog_stream`, which consumes the one
announcement each adapter yields first, so the case is production-
unreachable. One test reaches it deliberately:
`test_session_tools.py:69` scripts a `StreamStarted` mid-list.

`random_number`: offered unconditionally
(`tools/source.py:135-139`), named in `names.BUILTIN_TOOL_NAMES`,
described in prose inside `config/models.py:1733`, which renders
into BOTH byte-pinned committed artifacts
(`docs/reference/api-openapi.json` lines 4795/4918,
`docs/reference/domain-config.md` lines 254/287). Hand-written
spelling sites: `vinga-server/README.md` (builtin trio, four
sites), two `examples/mcp-server-*.yaml` comments. Tests:
`test_tools_random.py` (whole file), `test_tool_names.py` reserved
list, `test_session_tools.py` exact-list assertions,
`test_conversations_session.py` stored-record cases,
`test_tools.py` integration `DUE_BUILTINS`. The feature doc
`docs/features/2026-08-19-random-number-tool.md` and `CHANGELOG.md`
entries are history and stay.

`DeviceSession` (1,229 lines): the three clusters' exact members
are in the census; production importers are `ws.py` (the one
constructor call) and a TYPE_CHECKING import in `registry.py`; the
test hub is `tests/support/sessions.py`.

## Decisions

### 1. Each deleted protocol's annotation gets a named destination

Deleting a structural Protocol deletes a name, not a seam: the test
doubles keep working because nothing ever checked the nominal type.
What each annotation site becomes, decided per type:

- `TurnTaking.__init__`'s `reply` annotates the concrete
  `PipelineRuntime` under `TYPE_CHECKING` (a runtime import would
  cycle: `pipeline` imports `turntaking`). `FakeReply` no longer
  matches the annotation; no checker runs on that suite (mypy strict
  is scoped to `events/`), and the docstring names the double, so
  the relationship stays written down where it is real.
- `FillerRunner.__init__`'s `turn` annotates `TurnTaking` the same
  way (same cycle direction), same note for `FakeTurn`.
- `FillerCache` sites become `Mapping[str, FillerClips]`, which is
  what every satisfying object already is; the vestigial
  `is not None` guard at `pipeline.py:351` goes with it.
- `Served` sites become `"Generation | None"` under `TYPE_CHECKING`
  (`generation.py` imports `FillerClips` from `filler.py`, so the
  runtime import would cycle); `filler.py`'s `__all__` shrinks.
- `ToolClaim` sites become `records.ToolInvocation`; `tools/`
  importing `conversations.records` is a new runtime edge, taken
  under `TYPE_CHECKING` to keep the tools package free of the
  conversations package at import time; `source.py`'s `__all__`
  shrinks.
- `McpManager` sites annotate `McpServerManager` directly
  (`registry.py` and `reload.py` already import from `manager.py`,
  no cycle); the package `__all__` loses the name.
  `SlowStopManager` keeps its docstring sentence, updated to name
  the class it stands in for.

The kept discipline is behavioral, not nominal: `registry.py` and
`reload.py` keep touching only the public members the Protocol used
to list, and that is enforced the way it always really was, by
review, since structural typing never enforced it either.

### 2. The v2 timestamp leaves the dataclass, not the wire

`Frame` loses its `timestamp` field and `wrap` its parameter; the
v2 header struct is unchanged and `wrap` packs a literal 0 where it
always packed a default 0, so every wire byte is identical. v2
`unwrap` discards the parsed value. The two framing-test assertions
that touched the field are updated in the same commit, and the
byte-level `wrap` expectations in that suite stand as the wire pin.

### 3. The `StreamStarted` skip goes, and the invariant moves to the adapters' pin

The `_tool_loop` case is deleted. The scripted mid-list
`StreamStarted` in `test_session_tools.py:69` is removed from that
script in the same commit: it exists to exercise the skip, and a
mid-stream announcement is a thing no adapter emits (each yields it
once, first, and `_watchdog_stream` consumes exactly that one).
After the deletion a mid-stream `StreamStarted` falls to the match's
default arm like any other unexpected event, which is the honest
behavior for a violated adapter contract. The contract itself is
already pinned where it lives: the adapter suites assert the
announcement leads each stream, and `test_session_watchdog.py`
pins that `_watchdog_stream` consumes it.

### 4. `random_number` is removed whole, a recorded behavior change

The tool, its `ToolDef`, its `names.py` constant (the
`BUILTIN_TOOL_NAMES` tuple shrinks to two), its dispatch arm, and
the sentence about it in `config/models.py:1733` all go. The two
committed artifacts regenerate under their byte pins, a deliberate
artifact change recorded in the implementation doc with the
regeneration commands. README's builtin-trio prose and the two
example-YAML comments update. `CHANGELOG.md` gets a `### Removed`
entry: this is the one user-visible change in M1, an agent loses a
tool it used to be offered.

Tests: `test_tools_random.py` is deleted whole;
`test_tool_names.py` drops the reserved entry (the name stops being
reserved, and an MCP tool may now claim it, which the reserved-name
test's own framing already treats as the rule's content);
`test_session_tools.py`'s exact-list assertions shrink by one
honestly (they are the pin that the offering is exactly what the
code offers); the integration `DUE_BUILTINS` set shrinks;
`test_conversations_session.py`'s stored-record cases re-anchor on
`switch_agent`, the builtin with the closest shape (arguments in,
string out, no external reach).

### 5. The split: three modules in `device/`, the session keeps the boundary

`DeviceSession`'s public surface (the `DeviceOutput` half, `run`,
`request_shutdown`) does not change, and no test that drives a
session changes: the existing session suites are the
characterization pin for the whole milestone.

- **`device/pacing.py`, class `ReplyPacer`.** Owns the encoder and
  every pacing fact: `_encoder`, `_pace_start`, `_pace_count`,
  `_pace_resume`, `_pace_paused_at`, `_speaking_started`,
  `_speaking_started_at`, `_tts_started`. Interface:
  `encode(pcm)`, `flush()` (the `PlayableAudio` wrapping stays in
  the session, which owns the boundary vocabulary), `reply_started()`,
  `restart()`, `pause()`, `resume()`, `speaking_started_at`,
  `tts_start_due()` (the once-per-reply latch `begin_speaking`
  reads), `first_frame(now)` (stamps and answers whether this frame
  is the reply's first, so the session emits `SpeakingStarted` and
  the pacer never sees an event or an agent name), and
  `async delay_for_next(now)` folded into
  `async def gate(self)` (the sleep to cadence plus the resume
  wait, called once per packet by the session's send loop). The
  one sentence: the session stops knowing how a reply's cadence,
  pause bookkeeping and per-reply latches relate; it feeds PCM in,
  sends what comes out, and asks one gate before each frame.
- **`device/capture_tap.py`, class `CaptureTap`.** Built by
  `_start_capture` only when a capture opens; owns the
  `SessionCapture`, all three codec objects and the protocol
  version; interface `microphone(data)`, `reply(packet)`,
  `close()`. The session holds `self._tap: CaptureTap | None`,
  compares `is not None`, and its two hot-path calls become one
  line each. The fail-open rules (a frame the capture cannot read
  is not a reason to stop capturing) move with their docstrings.
  The one sentence: the session stops knowing that recording needs
  its own decode path with its own codecs.
- **`device/watchdog.py`, both watchdogs.** The issue names two and
  the module owns two. `IdleWatchdog` owns the task,
  `_last_activity`, and the countdown arithmetic of
  `_watch_for_idle`. Its constructor takes narrow dependencies,
  never the session: the timeout, a read-only defer predicate the
  session builds from its realtime flag and `runtime.replying()`
  (the loop reevaluates both every iteration), and the async
  `on_idle` callback that keeps the policy in the session (the idle
  event emission and the `request_shutdown` call), so the watchdog
  is timing and the session is what idleness means. Interface:
  `start()`, `stop()`, `mark()`. The first-contact watchdog moves
  too: `HELLO_TIMEOUT_S` re-homes to `watchdog.py` beside a
  `first_contact()` helper wrapping `asyncio.timeout`, read at call
  time through the module so the existing `test_session.py` patch
  site retargets to `watchdog.HELLO_TIMEOUT_S` and still bites;
  the session keeps hello parsing and the protocol-error close
  policy. Mode changes and a reply crossing the deadline stay
  pinned at the session surface by the existing idle suites.

The manifest builders (`_manifest`, `_provider_manifest`) and the
conversation-store recording (`_start_recording`,
`_stop_recording`) stay: they are the session's knowledge of who is
talking, shared by capture and store, and moving them would be a
second responsibility for the tap.

New modules get no new test files by default: the interface is the
test surface, the session's suites reach these through the session,
and a direct suite would pin details the split exists to hide. The
one exception is `ReplyPacer`'s pause arithmetic (the clock shift on
resume), which today is pinned through session-level tests and keeps
being pinned there; if a gap shows up in coverage during the move it
is closed at the session surface, not by a reach-in.

### 6. Milestones and order

M1 (deletions) lands first: it is self-contained, its only
session.py contact is none at all, and it shrinks nothing M2 needs.
M2 (the split) stacks on M1. Each leaves `main` releasable: M1's
behavior change is the removed tool, recorded and complete in one
PR; M2 changes no behavior.

## Module layout after the change

- `runtime/turntaking.py`, `runtime/filler_runner.py`,
  `filler.py`, `tools/source.py`, `tools/mcp/manager.py`,
  `tools/mcp/{registry,reload,slice,__init__}.py`: protocol
  deletions and annotation retargets per decision 1.
- `protocol/framing.py`: decision 2.
- `runtime/pipeline.py`: the `StreamStarted` case, the
  `FillerCache` import and the vestigial guard go.
- `tools/builtin.py`, `tools/names.py`, `tools/source.py`,
  `config/models.py`: decision 4, with both committed artifacts
  regenerated.
- `device/pacing.py`, `device/capture_tap.py`,
  `device/watchdog.py`: new, per decision 5; `device/session.py`
  shrinks to the handshake, the wire, the manifest, the close
  path, and delegation to the three.
- `CHANGELOG.md` under `## 2026-08-23`: `### Removed` for the tool,
  `### Changed` for the split noted as internal.

## Tests

M1: the framing suite's two field assertions and the mid-list
script change deliberately, as decisions 2 and 3 record; the tool
deletion's test diff is decision 4's list, each edit the honest
shrink of an exact-list pin; every other suite is untouched and
green, which is the proof the protocol deletions changed nothing
observable. The two regenerated artifacts diff exactly at the two
prose sites, verified by reading the diff, and the drift checks
hold them from then on.

M2: zero assertion changes anywhere. The session suites
(`test_session.py`, `test_session_limits.py`,
`test_session_record.py`, `test_session_close_reason.py`,
`test_conversations_session.py`, `test_tts_lookahead.py`,
`test_generation_binding.py`, the support hub
`tests/support/sessions.py`, and the integration drain suite) run
byte-unchanged before and after the split; any edit to one of them
in M2's diff is a review flag by construction. `wc -l` of
`session.py` before and after is recorded in the implementation
doc.

## Verification

All from `vinga-server/`: `uv run ruff check .`,
`uv run pytest tests/unit -q -n auto --dist loadfile`,
`uv run pytest tests/integration -q`, the events mypy lane, and the
generated-document drift checks. Inventories by tooling: after M1,
`grep -rn` for each of the six protocol names, `frame.timestamp`,
and `random_number` across `src/` and `tests/` returns nothing
(`random_number` may remain in `CHANGELOG.md`, `docs/features/`,
`docs/plans/` and the committed spike output, which is regenerated
spike material and not CI-checked); after M2, the same grep
discipline for the moved attribute names in `session.py`. Counts
refreshed after any rebase.

## Risks

- **A protocol deletion that was load-bearing for a checker.** Only
  `events/` runs mypy strict; ruff does not check protocol
  conformance. The full suites are the guard, and the annotation
  destinations are typed the same shape the protocols declared.
- **The mid-list `StreamStarted` script hides a second
  assertion.** The commit that edits `test_session_tools.py:69`
  reads the whole test first and records in the implementation doc
  what else the script pins; if the scripted event turns out to be
  load-bearing for an unrelated assertion, the script keeps an
  equivalent event that is not `StreamStarted`.
- **The split moves a hot-path line subtly.** `send_audio` and
  `_handle_audio` are the two methods where clusters interleave;
  the move keeps their statement order (capture tap before guards
  in `_handle_audio`, per its own comment; first-frame stamp before
  the gate in `send_audio`) and the session suites pin both.
- **Artifact regeneration surprises.** The `random_number` prose
  edit must change exactly two rendered sites; the diff of both
  artifacts is read, not assumed, before commit.

## Milestones

- [ ] **M1: delete the seams with no second side.**
  Decisions 1 to 4: six protocols with per-site annotation
  destinations, the v2 timestamp out of `Frame` with the wire
  byte-identical, the `StreamStarted` case with its scripted
  reacher, and `random_number` whole with both artifacts
  regenerated and the changelog `Removed` entry. Design footprint:
  no new modules; deepens `turntaking.py`, `filler_runner.py`,
  `filler.py`, `source.py` and the mcp package by making each
  annotation name the class that is really there.
- [ ] **M2: split the session's three clusters.**
  Decision 5, stacked on M1: `device/pacing.py`,
  `device/capture_tap.py`, `device/watchdog.py`; the session keeps
  the boundary, the manifest and the close path; zero test-file
  changes, which is the milestone's own pin. Design footprint: adds
  three modules whose one sentence each is stated in decision 5;
  deepens `device/session.py`, which stops carrying three
  implementations behind one class name.

## Plan review round

External review of commit `61bf2f44`, 2026-08-23. Backend: codex
CLI 0.149.0, model `gpt-5.6-sol`, read-only sandbox, runtime 6m45s.
Verdict as received: ready after the P1/P2 amendments. Ten
findings, condensed but faithful; each amendment is its own commit
with a resolution note here.

1. **P1: the plan contradicted the issue by leaving the
   first-contact watchdog in `session.py`.** The issue names both
   watchdogs; the hello wait (`asyncio.timeout(HELLO_TIMEOUT_S)`
   around the first receive) is the first-contact one, and the plan
   kept it behind.

2. **P1: M2's zero-test-change constraint was impossible.** Six
   session suites deliberately reach the names M2 moves:
   `test_session_barge_in.py` reads `_pace_start`/`_pace_resume`,
   `test_session_characterization.py` replaces `_encoder`,
   `test_boundary_contract.py` reads `_last_activity`,
   `test_session_close_reason.py` replaces `_stop_idle_watchdog`,
   `test_conversations_session.py` replaces `_start_idle_watchdog`
   and asserts `_capture is None`, `test_session_events.py` resets
   `_pace_start`. Compatibility aliases would defeat the split.

3. **P2: `gate()` could not preserve the sent-frame accounting.**
   The count advances only after send and capture succeed; a
   pre-send gate makes the pacer advance blind or demands an
   unnamed second call.

4. **P2: the idle watchdog's loop needs live inputs the interface
   omitted.** `_watch_for_idle` reevaluates realtime mode and
   `runtime.replying()` every iteration.

5. **P2: the framing amendment deleted the only nonzero-timestamp
   compatibility pin.** A default-wrap round trip exercises only
   zero; incoming stock-firmware v2 frames carry nonzero values.

6. **P2: deleting the skip alone leaves the provider contract
   contradictory.** `providers/base.py` requires consumers to
   tolerate `StreamStarted` anywhere, and `_tool_loop`'s default
   arm appends to `calls`, where reservation then reads `.name`.

7. **P2: re-anchoring the stored-record test on `switch_agent`
   abandons the path it protects.** `switch_agent` is special-cased
   in `_run_tools`, bypasses `_run_one`, and a refused handover
   emits no ordinary `tool_call` event.

8. **P2: capture attachment and teardown ownership was missing.**
   Startup attaches the raw `SessionCapture` to `SessionEvents`
   after opening; shutdown emits `session_closed`, detaches, closes,
   clears, in that order, and the proposed interface said nothing
   about any of it.

9. **P3: `CaptureTap` collides with `events.CaptureTap`,** the
   existing adapter that writes event emissions to a capture.

10. **P3: the reservation claim was wrong.** MCP tools are always
    published qualified (`<entry>__<tool>`); what the freed name
    permits is an MCP entry named `random_number`, never a bare
    tool of that name.
