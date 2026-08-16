# Shared test fakes and drift-pinning tests: implementation

Companion to
[`2026-08-16-test-fakes-and-drift-pins.md`](2026-08-16-test-fakes-and-drift-pins.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out.

## Milestone 1: the fakes package is born

`samtal-server/tests/support/llm_sdk.py` (new, 197 lines) holds the
fourteen SDK-shape fakes in two headed sections, the anthropic
messages-stream dialect (`FakeBlock`, `FakeUsage`, `FakeMessage`,
`FakeTextDelta`, `FakeStreamEvent`, `FakeStream`, `FakeMessages`) and
the openai chat-completions one (`FakeFunction`, `FakeFragment`,
`FakeDelta`, `FakeChoice`, `FakeChunkUsage`, `FakeChunk`,
`FakeCompletions`), plus the consolidated `Falsey` probe under a third
heading. Its docstring states the admission rule the later modules
inherit: a double for an object a vendor SDK hands the provider, not a
double for anything samtal owns, and each class carries only the
attributes the provider under test reads.

Three commits, in the order the milestone was built in:

1. `b8b7c66` Move the SDK dialect fakes into tests/support
2. `943d706` Consolidate the four falsey client probes into one
3. `db77bc0` Pin the falsey probe's falsiness where it lives

### The block move

The fourteen classes came out of
`tests/unit/test_providers_llm_tools.py` (lines 117-204 and 344-407 at
`c410af8`) by AST extraction rather than by hand, so the moved text is
the origin's text. That module now imports the ten names its own
bodies use; the four that only the moved classes reference
(`FakeUsage`, `FakeTextDelta`, `FakeStreamEvent`, `FakeChunkUsage`)
are not imported back, which ruff's unused-import rule established
rather than a judgement call. Its two local builders, `anthropic_with`
and `openai_with`, stay where they are: they are single-module and
outside the plan's move set.

**Dependency closure: empty.** Computed by walking each class body's
free names with `ast` and subtracting the names it binds itself. Every
free name resolves either to another moved class, to a builtin, or to
one of four stdlib imports (`contextlib`, `dataclass`, `field`,
`Any`), which the support module declares for itself. No module-level
constant, sentinel or helper had to travel with the block. With the
block gone, `contextlib`, `dataclass` and `field` became unused in the
origin module and were dropped from its imports; `Any` is still used
by a local helper and stays.

### The falsey consolidation

All four definitions were read before consolidating. They are literal
duplicates: each defines `__bool__` returning `False` and nothing
else. The module-level `FalseyClient`
(`test_providers_llm.py:117` at `c410af8`) is the strongest of the
four because it is the only one carrying the explanation of why the
seam matters, so its docstring is what support keeps. The plan's
consolidation rule about preserved behavioral differences did not have
to fire.

- `test_providers_llm.py` imports it as
  `from tests.support.llm_sdk import Falsey as FalseyClient`, so its
  two test bodies are untouched.
- The three nested classes
  (`test_providers_elevenlabs.py:438`, `test_providers_openai_tts.py:491`,
  `test_providers_openai_asr.py:814`) are deleted and each module
  imports `Falsey` at module level. These are the three permitted
  test-function edits the plan enumerates. The diff inside each of the
  three functions is exactly the removal of the nested `class`
  statement and the blank line before it: the docstring, the
  `given = Falsey()` line and the assertion are byte-identical.

### The contract test

`samtal-server/tests/unit/test_support_fakes.py` (new, one test,
`test_the_probe_answers_false_to_a_truth_test`) asserts
`bool(Falsey()) is False`. Its docstring gives the reason the plan's
review round gave: the four suites that inject the probe assert only
that the provider kept the object it was handed, an assertion any
object satisfies, so the seam exists only while the probe is falsey.

Proven by mutation even though M1 owes no mutation proof: flipping
`__bool__` to return `True` fails it with
`tests/unit/test_support_fakes.py:18: AssertionError`, and restoring
the file passes it again.

### Verification

Run from `samtal-server/`, with `PYTHONDONTWRITEBYTECODE=1` exported
for everything outside pytest.

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `2256 passed, 16 skipped in 304.87s`
- `uv run pytest tests/integration -q`: `55 passed in 190.65s`
- `uv run pytest tests/unit -q --collect-only | tail -1`: **2271
  before**, **2272 after**. The rise is exactly the one new contract
  test.
- Normalized AST comparison of every relocated definition against its
  origin at `HEAD`, by `ast.dump` with `include_attributes=False`, run
  through `uv run python`: **all fourteen pass.** The four falsey
  probes were compared the same way with any leading docstring dropped
  from both sides, since three of the four never had one: **all four
  pass**, which is the check that they really were duplicates.
- `git diff` inspected: outside import lines, the only diffs inside
  test functions are the three enumerated nested-class deletions.

One thing the runs turned up and did not fix:
`tests/unit/test_providers_llm_tools.py` is not `ruff format` clean at
`HEAD` either (four list comprehensions the formatter would rejoin).
Checked against the committed file before touching it, so this is
pre-existing and not something the move introduced. `ruff check`, the
lane CI runs, passes; reformatting would have rewritten test bodies
this issue may not touch.

### Deviations from the plan

None. The support module, the five touched provider modules, the
consolidated probe, the contract test and the empty closure are all as
the plan describes them.

### PR review round

External review of PR #161 (diff main...4395eb0) by codex 0.147.0
(model gpt-5.6-sol), 2026-08-16, posted to the PR by the review run
itself. Verdict: mergeable as is, no findings. The plan review
round's amendments (the enumerated test-function edit allowance, the
AST comparisons, the falsiness contract test) pre-answered the
lenses this diff touches, which is the outcome the pipeline's plan
reviews exist to buy.

## Milestone 2: the session family decouples

Eight new support modules, and no test module imports
`test_session*.py`, `test_tools_device.py` or
`test_boundary_contract.py` any more. The inventory grep at the M1 tip
listed **84** statements; it lists **33** at the milestone's end, and
every one of the 33 names a module M3 owns.

The milestone was built across two sessions, and the resumed one first
recorded 82 rather than 84, because the configuration commit had
already landed when it started and it took that commit for its
baseline. 84 is the number that means anything: it is the count before
this milestone changed a line. The two statements between the figures,
and the 25 definitions the configuration commit relocated, are inside
M2 and are verified here with the rest.

Ten commits, in the order the milestone was built in:

1. `d402952` Move the session configurations into tests/support
2. `6bf69fe` Move the caplog event readers into tests/support
3. `420258a` Move the scripted providers into tests/support
4. `3b724cb` Move the scripted device sockets into tests/support
5. `2577626` Promote the boundary pair as the seam-testing template
6. `ad18967` Move the device tool-channel fake into tests/support
7. `c723fba` Move the websocket driving helpers into tests/support
8. `291ed6b` Move the in-process session drivers into tests/support
9. `e4eefe7` Collapse the one-name imports the moves left behind
10. `6c94c55` Say what the providers module actually sits on

The order is the dependency order the plan's layering asks for:
`configs.py` imports only `samtal_server`; `events.py`, `providers.py`
and `sockets.py` sit on it; `wire.py`, `boundary.py` and
`device_tools.py` sit beside them; and `sessions.py`, which imports
from four of the others, comes last. Nothing under `tests/support`
imports a test module, checked by grep at the end.

### What went where

- **`configs.py`** (new, 236 lines): the configurations a session suite
  is built on and the constants they name, from five session modules.
  The device identity (`DEVICE_MAC`, `DEVICE_UUID`, `DEVICE_HELLO`),
  the audio shapes (`SAMPLE_RATE`, `OUTPUT_RATE`, `FRAME_MS`,
  `FRAME_BYTES`, `ENDPOINT_SILENCE_MS`, `SPEECH`, `LONG_REPLY`), the
  two personas (`POET_MAC`, `BOTH_MAC`, `POET_TONE`, `TUTOR_TONE`), and
  the builders with their thresholds (`config_with_agent`,
  `base_config`, `STDIO_SERVER`, `registry_config`, `TIMEOUT_S`,
  `watchdog_config`, `DELAY_MS`, `masked_config`, `capped_config`,
  `BACKSTOP_S`, `idle_config`). `POET_MAC` and `BOTH_MAC` had identical
  definitions in two modules; both copies are gone and one definition
  remains. It imports only `samtal_server` and the standard library,
  which is the layer the rest of the package sits on.
- **`events.py`** (new, 22 lines): `events`, `only`. Reading
  `caplog.records` for one event's records, and insisting on exactly
  one. It imports pytest and nothing else, which is what puts it under
  the session builders rather than beside them.
- **`providers.py`** (new, 259 lines): the scripted far sides a
  pipeline runs against, in sections. The models (`Step`,
  `ScriptedLlm`, `STALL_S`, `StallingLlm`, `RecordingLlm`), the ears
  (`GatedAsr`, `ConfirmingAsr`, `ScriptedEndpointer`), the voices
  (`BrokenTts`, `BrokenStreamingTts`), a stage no request reaches
  (`Unreachable`), and the registry a prompt's know-how half is
  assembled from (`CountingServers`).
- **`sockets.py`** (new, 84 lines): `RecordingSocket`, `LoopingSocket`,
  `QuietSocket`. Three rather than one configurable stand-in, because
  what each implements is exactly the calls its tests make on it.
- **`boundary.py`** (new, 176 lines): `OUTPUT_FRAME_BYTES`,
  `REPLY_PCM`, `StubRuntime`, `FakeDevice`, with the promoted pattern
  in its docstring: name the far side after the side it replaces, give
  it only the calls the near side makes, and keep the one fact the seam
  turns on.
- **`device_tools.py`** (new, 62 lines): the scripted board and
  `STATUS`. Its docstring says which seam it is, since `boundary.py`
  holds a differently-shaped `FakeDevice` for the other one.
- **`wire.py`** (new, 198 lines): opening the channel (`connect`,
  `token_for`, `shake_hands`), sending (`speech_pcm`, `send_pcm`,
  `endpoint_silence`, `listen_realtime`), and reading the reply back
  (`wait_for_close`, `collect_until`, `collect_reply`, `is_reply_end`,
  `is_reply_start`, `is_transcript`, `sentences`, `heard_ms`,
  `tone_strength`, `assert_endpointed_speech`, `say_something`).
- **`sessions.py`** (new, 248 lines): building one (`device_session`,
  `session_for`, `session_with`, `served`, `open_session`,
  `masked_session`, `realtime_session`) and driving a reply through it
  (`call`, `run_reply`, `drive_reply`, `start_reply`, `_nothing`,
  `reply_with`).

### Dependency closure, per moved root

Computed by walking each definition's free names with `ast` and
subtracting what it binds itself, then resolving each remainder. A root
with nothing listed reads only `samtal_server`, the standard library
and builtins.

- `only` → `events` (moved with it).
- `ScriptedLlm` → `Step` (moved with it; it is read at definition time,
  in the `Sequence[Step]` annotation).
- `LoopingSocket` → `DEVICE_MAC`, `DEVICE_UUID`, `DEVICE_HELLO`, all
  already in `configs.py` from M1's tail, imported rather than copied.
- `StubRuntime` → `REPLY_PCM` → `OUTPUT_RATE` (`configs.py`).
- `FakeDevice` (boundary) → `OUTPUT_FRAME_BYTES` (moved, renamed
  below), `OUTPUT_RATE` (`configs.py`).
- `connect` → `token_for` (moved), `DEVICE_MAC`, `DEVICE_UUID`.
- `token_for` → `DEVICE_UUID`. `shake_hands` → `DEVICE_HELLO`.
  `speech_pcm` → `SAMPLE_RATE`. `collect_until` and `tone_strength` →
  `OUTPUT_RATE`.
- `endpoint_silence` → `send_pcm` (moved), `FRAME_BYTES`, `FRAME_MS`,
  `ENDPOINT_SILENCE_MS`.
- `assert_endpointed_speech` → `heard_ms` (moved),
  `ENDPOINT_SILENCE_MS`.
- `collect_reply` → `collect_until`, `is_reply_end` (both moved).
- `say_something` → `send_pcm`, `speech_pcm`, `collect_reply` (moved).
- `session_for` → `device_session` (moved), `ScriptedLlm`
  (`providers.py`).
- `session_with` → `session_for` (moved), `POET_MAC`, `base_config`.
- `served` → `LoopingSocket` (`sockets.py`); `open_session` → `served`
  (moved with it) and `LoopingSocket`.
- `masked_session` → `session_for` (moved), `RecordingSocket`.
- `realtime_session` → `device_session` (moved), `RecordingSocket`,
  `DEVICE_MAC`.
- `run_reply` → `_nothing` (moved).
- `reply_with` → `session_for`, `_nothing` (moved), `ScriptedLlm`,
  `Unreachable` (`providers.py`), `only` (`events.py`), `POET_MAC`,
  `base_config`.
- Everything else: nothing. `device_session`, `STALL_S`,
  `StallingLlm`, `RecordingLlm`, `CountingServers`, `Unreachable`,
  `GatedAsr`, `ConfirmingAsr`, `ScriptedEndpointer`, `BrokenTts`,
  `BrokenStreamingTts`, `RecordingSocket`, `QuietSocket`, the device
  `FakeDevice`, `STATUS`, `send_pcm`, the three predicates,
  `sentences`, `heard_ms`, `listen_realtime`, `wait_for_close`, `call`,
  `drive_reply`, `start_reply`, `_nothing`.

Inside `configs.py` the closures are all to its own siblings, which is
what made it the module the rest can sit on: `FRAME_BYTES` →
`SAMPLE_RATE`, `FRAME_MS`; `base_config` → `POET_MAC`, `BOTH_MAC`,
`POET_TONE`, `TUTOR_TONE`; `registry_config` → `base_config`,
`STDIO_SERVER`; `watchdog_config` → `base_config`, `TIMEOUT_S`;
`masked_config` → `base_config`, `DELAY_MS`, `POET_TONE`,
`TUTOR_TONE`; `capped_config` → `config_with_agent`; `idle_config` →
`config_with_agent`, `BACKSTOP_S`. The other eighteen read nothing but
`samtal_server` and the standard library.

No sentinel constant is in any of these closures: the sentinel-planting
tests in this family keep their sentinels where they are.

### The two renames

`BrokenTts` was defined twice and the two are not duplicates, which the
plan's review round settled: the filler one raises synchronously in
`synthesize()` with a class-level rate, the record one is an async
generator declaring `egress = False` that raises during iteration. Both
are in `providers.py`, the second as `BrokenStreamingTts`, and
`test_session_record.py` imports it as
`from tests.support.providers import BrokenStreamingTts as BrokenTts`,
so its test bodies are byte-identical.

The boundary's `FRAME_BYTES` is `2880`, a 60 ms frame of 24 kHz reply
audio; `configs.FRAME_BYTES` is `1920`, a 60 ms frame of 16 kHz
microphone audio. Same name, different value, so per the plan's
collision rule support keeps one definition per distinct value: the
boundary's is `OUTPUT_FRAME_BYTES`, and the module docstring says why
the two exist. Its sibling `OUTPUT_RATE` was `24000` on both sides, the
same value, so `boundary.py` imports the one `configs.py` already has
rather than adding a second definition. Neither name is needed back in
`test_boundary_contract.py`: both were read only by the fakes that
moved.

### Verification

Run from `samtal-server/`, with `PYTHONDONTWRITEBYTECODE=1` exported
for everything outside pytest.

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `2256 passed, 16 skipped in 298.69s`
- `uv run pytest tests/integration -q`: `55 passed in 184.53s`
- `uv run pytest tests/unit -q --collect-only | tail -1`: **2272
  before**, **2272 after**. M2 adds no tests, so the count is the M1
  tip's exactly.
- The inventory grep: **84 statements at the M1 tip, 33 after**, and
  all 33 are M3's (`test_ota`, `test_tools_mcp*`,
  `test_conversations_*`, `test_config*`, `test_capture`,
  `test_device_bindings`, `test_drain`, `test_onboarding_*`,
  `test_ws_auth`, `test_tools_memory`). Zero name `test_session*`,
  `test_tools_device` or `test_boundary_contract`.
- `grep -rn "tests.unit" tests/support/`: no matches, so the dependency
  arrow points one way already, ahead of M3's guard test.
- Normalized AST comparison of every relocated definition against its
  origin at the milestone's own baseline, the M1 tip (the parent of the
  configuration commit, `74f0fba` on this branch), by `ast.dump` with
  `include_attributes=False`: **all 79 top-level moved nodes pass**,
  over 81 comparisons, because `POET_MAC` and `BOTH_MAC` each have two
  identical origins and were compared against both. That is the 54
  nodes of the seven later modules plus `configs.py`'s 25, listed per
  root below. The boundary rename was applied to the origin's own nodes
  before dumping, which is the plan's "names aside where a rename was
  decided".
- `git diff` inspected for edits inside test functions: the only added
  line anywhere inside a function body is the rewritten function-level
  import in `test_tools_memory.py`. Every other change is an import
  line or the removal of a module-level definition.

#### The AST comparison, per root

The seven later modules' 54 roots, all **PASS**, listed by the module
they landed in:

- `events.py`: `events`, `only`.
- `providers.py`: `Step`, `ScriptedLlm`, `STALL_S`, `StallingLlm`,
  `RecordingLlm`, `CountingServers`, `Unreachable`, `GatedAsr`,
  `ConfirmingAsr`, `ScriptedEndpointer`, `BrokenTts`,
  `BrokenTts` → `BrokenStreamingTts`.
- `sockets.py`: `RecordingSocket`, `LoopingSocket`, `QuietSocket`.
- `boundary.py`: `FRAME_BYTES` → `OUTPUT_FRAME_BYTES`, `REPLY_PCM`,
  `StubRuntime`, `FakeDevice`.
- `device_tools.py`: `FakeDevice`, `STATUS`.
- `wire.py`: `connect`, `token_for`, `shake_hands`, `speech_pcm`,
  `send_pcm`, `endpoint_silence`, `assert_endpointed_speech`,
  `collect_until`, `collect_reply`, `is_reply_end`, `is_reply_start`,
  `is_transcript`, `sentences`, `heard_ms`, `tone_strength`,
  `say_something`, `listen_realtime`, `wait_for_close`.
- `sessions.py`: `device_session`, `session_for`, `session_with`,
  `served`, `open_session`, `masked_session`, `realtime_session`,
  `call`, `run_reply`, `drive_reply`, `start_reply`, `_nothing`,
  `reply_with`.

`configs.py`'s 25 roots, all **PASS**, listed by the module they came
out of:

- from `test_session.py` (14): `DEVICE_MAC`, `DEVICE_UUID`,
  `DEVICE_HELLO`, `SAMPLE_RATE`, `OUTPUT_RATE`, `FRAME_MS`,
  `FRAME_BYTES`, `ENDPOINT_SILENCE_MS`, `LONG_REPLY`, `POET_MAC`,
  `BOTH_MAC`, `POET_TONE`, `TUTOR_TONE`, `config_with_agent`.
- from `test_session_tools.py` (5): `POET_MAC`, `BOTH_MAC`,
  `STDIO_SERVER`, `base_config`, `registry_config`.
- from `test_session_filler.py` (3): `SPEECH`, `DELAY_MS`,
  `masked_config`.
- from `test_session_limits.py` (3): `BACKSTOP_S`, `capped_config`,
  `idle_config`.
- from `test_session_watchdog.py` (2): `TIMEOUT_S`, `watchdog_config`.

`POET_MAC` and `BOTH_MAC` appear twice in that list on purpose: they
had a definition in each of two modules, the deduplication the
configuration commit performed rests on the two being identical, and
each was compared against both origins. Both comparisons pass for both
names, which is what makes the deduplication a checked claim rather
than a reviewed impression. Twenty-five roots, 27 comparisons.

### Deviations from the plan

Nine, none of them to the move rule, the no-cross-import end state or
an assertion.

1. **`LockingAsr` stayed in `test_session_events.py`.** The plan's
   layout lists it in `providers.py`, but it is defined and used in one
   module and nothing imports it, so the move rule's own boundary
   ("locality is a feature") keeps it where it is. The rule wins over
   the module list, which is how the milestone brief asked for this
   kind of conflict to be resolved.
2. **`audio_ms` stayed in `test_session.py`**, for the same reason: the
   plan lists it under `wire.py`, but no other module imports it and no
   moved root's closure reads it. Its neighbour `expected_tone_ms`
   stayed with it.
3. **Thirteen names the plan's M2 list did not name moved anyway**,
   because the inventory grep or a closure required them and they are
   defined in session-family hubs: `tone_strength`, `listen_realtime`
   and `wait_for_close` (into `wire.py`); `served`, `masked_session`,
   `realtime_session` and `session_with` (into `sessions.py`); `Step`,
   `GatedAsr`, `ConfirmingAsr`, `ScriptedEndpointer`, `RecordingLlm`
   and `CountingServers` (into `providers.py`).
4. **`CountingServers` is in `providers.py` rather than M3's
   `mcp.py`.** It is an `McpServers` subclass, not a pipeline stage, so
   it is not obviously a provider; but it is what a session's prompt
   half asks for guidance, `test_tools_memory.py` imports it across a
   boundary today, and it is defined in a session hub, so deferring it
   to M3 would leave the milestone's end state unreached. It sits under
   its own heading, and the module docstring is written to admit it.
5. **`VOLUME` stayed in `test_tools_device.py`** while `STATUS`, its
   neighbour in the same two-line block, moved. Only `STATUS` crosses a
   module boundary. This reads oddly beside its sibling and is
   deliberate: the rule moves what is shared, not what looks like it
   belongs with what is shared.
6. **One comment was reworded.** `STALL_S` carried "well past the
   test-scale timeout below", which was true of the watchdog module and
   is false in `providers.py`. The code is byte-identical and the AST
   comparison is unaffected; this is the only prose change to a moved
   definition.
7. **`LoopingSocket`'s docstring says "the tests below"** and now sits
   in `sockets.py` where there are no tests below it. It was left
   byte-identical rather than reworded, because a docstring is part of
   the AST the comparison checks, and preserving the body exactly was
   worth more than the sentence.
8. **Two moved names have unrelated local namesakes**, which stayed
   local: `test_session_limits.py` has its own `session_with` (a
   `FakeWebsocket` builder) and `test_session_characterization.py` has
   its own `masked_session`. Neither is imported anywhere, so neither
   moved, and neither module imports the support one.
9. **No fixture turned up.** Every moved name is a plain callable,
   class or constant, so no `tests/unit/conftest.py` was needed, which
   is what the plan expected.

### PR review round

External review of PR #162 (diff main...d17e864) by codex 0.147.0
(model gpt-5.6-sol), 2026-08-16, posted to the PR by the review run
itself. One finding:

1. **P2: M2 verification starts after the configuration move.** The
   recorded inventory (82 before) and the 54-root AST comparison
   both took the configuration commit as their baseline, but that
   commit is itself the first M2 commit, so `configs.py`'s 25
   relocated definitions were never recorded as verified against
   their origin; the true M1-tip inventory is 84.

   *Resolution*: accepted, fixed in cf25dc9. The whole comparison
   was rerun from the M1-family tip (`74f0fba`): 79 top-level moved
   nodes, 81 comparisons (the twice-defined `POET_MAC` and
   `BOTH_MAC` compared against both origins), 79 pass, 0 fail; the
   inventory reads 84 to 33; origin modules were determined from
   the commit's own before/after node sets so same-named constants
   elsewhere could not stand in for an origin.

Verdict as posted: mergeable after the listed fix.

## Milestone 3: the feature suites decouple and the guard lands

Four new support modules, four existing ones extended, and the unit
lane's cross-import count is zero. The inventory grep listed 33
statements at the milestone's start and lists **0** at its end, in the
unit lane and everywhere else. `tests/unit/test_support_boundaries.py`
is what keeps it there.

Nine commits, in the order the milestone was built in:

1. `639b1fb` Move the shared config builders into tests/support
2. `0862c38` Move the check-in scaffolding into tests/support
3. `7d44de4` Read one event one way across every suite
4. `9c02f1d` Move the MCP entry builders into tests/support
5. `ddd4ee8` Move the store scaffolding into tests/support
6. `7fcca26` Move the writer waits into tests/support
7. `44fa61c` Move the handshake halves into tests/support
8. `76e23d5` Move the device registers into tests/support
9. `4bfa103` Make the one-way import rule a test

The order is the layering: `configs.py` first, since `checkin.py` and
`registry.py` read the device identity from it, then the modules that
sit beside each other, then the guard.

### What went where

- **`configs.py`** (+101 lines): `config_with` (the minimal valid
  configuration, from `test_config_tools.py`), `load_config_from_data`
  (a mapping through the whole boot composition, from `test_config.py`),
  `recording_config` (a server that records, from
  `test_conversations_session.py`) and `BOUND_MAC`. It now imports
  `yaml`, which the loader itself uses; the docstring says so.
- **`checkin.py`** (new, 130 lines): what a board says about itself
  (`MOCK_PROVIDERS`, `MOCK_AGENT`, `SYSTEM_INFO`, `HEADERS`,
  `NORMALIZED`), the two servers it checks in against (`ota_client`,
  `unbound_config`, `activation_client`), the requests it makes
  (`post_system_info`, `check_in`, `activate`) and `Clock`, the clock the
  pending table is aged against.
- **`events.py`** (+22 lines): `fields_of`, and the consolidation
  described below.
- **`tools_mcp.py`** (new, 194 lines): `SHADOWED_POSITION`,
  `MANAGER_LOGGER`, the entry builders (`stdio_entry`, `entry_data`),
  the two configurations that grant an entry (`config_granting`,
  `reload_config`), the starters (`running`, `started`, `reading`), and
  `serving` with its `LIFECYCLE_TIMEOUT_S`.
- **`stores.py`** (new, 93 lines): the capture directory
  (`CAPTURE_MANIFEST`, `tone`, `store`), the conversations database
  (`CONVERSATIONS_MANIFEST`, `rows`) and the corrupted memory file
  (`STORED`, `CORRUPT`, `corrupt`).
- **`sessions.py`** (+59 lines): `WRITER_TIMEOUT_S`, `Gate` and `until`,
  under a fourth heading, "waiting on the writer behind it".
- **`wire.py`** (+16 lines): `handshake` and `device_headers`, the two
  halves of the handshake `connect` already made whole.
- **`registry.py`** (new, 136 lines): the sessions a drain walks
  (`FakeSession`, `registry_with`) and the bindings an operator writes
  (`BINDINGS_DEVICE_MAC`, `STAGES`, `AGENT`, `store_at`, `booted`,
  `check_in`).

### Dependency closure, per moved root

Computed by walking each definition's free names with `ast` and
subtracting what it binds itself, then resolving each remainder against
the origin module's bindings. A root with nothing listed reads only
`samtal_server`, third-party packages, the standard library and
builtins.

- `SYSTEM_INFO` → `DEVICE_MAC`, `DEVICE_UUID` (already in `configs.py`,
  same values, imported rather than copied).
- `post_system_info` → `SYSTEM_INFO` (moved), `DEVICE_MAC`,
  `DEVICE_UUID`.
- `unbound_config` → `BOUND_MAC` (moved to `configs.py`),
  `MOCK_PROVIDERS`, `MOCK_AGENT` (moved with it).
- `activation_client` → `unbound_config` → the same.
- `check_in` (activation) → `HEADERS` (moved), `SYSTEM_INFO`,
  `DEVICE_MAC`.
- `activate`, `NORMALIZED`, `HEADERS` → `DEVICE_MAC`.
- `one_event` → `emitted`, which is `events` in support; `fields_of` →
  nothing local (it reads `logs._STANDARD_ATTRIBUTES`, a production
  name, imported rather than copied).
- `stdio_entry` and `entry_data` → `STDIO_SERVER`, already in
  `configs.py` with an identical definition, imported rather than
  copied.
- `serving` → `LIFECYCLE_TIMEOUT_S` (moved with it).
- `Gate` → `TIMEOUT_S`, `until` → `TIMEOUT_S`: the same 30 seconds
  written out in both origin modules, one `WRITER_TIMEOUT_S` in support.
- `corrupt` → `CORRUPT` → `STORED`, both moved byte-identical: `STORED`
  is the sentinel the no-leak tests hunt for.
- `booted` → `store_at`, `STAGES`, `AGENT` (moved with it), `BOUND_MAC`
  (`configs.py`).
- `AGENT` → `STAGES`. `check_in` (bindings) → `BINDINGS_DEVICE_MAC`
  (moved, renamed below), `DEVICE_UUID` (`configs.py`, same value).
- `registry_with` → `FakeSession` (moved with it).
- Everything else: nothing. `MOCK_PROVIDERS`, `MOCK_AGENT`,
  `ota_client`, `Clock`, `SHADOWED_POSITION`, `MANAGER_LOGGER`,
  `running`, `config_granting`, `reload_config`, `reading`, `started`,
  `LIFECYCLE_TIMEOUT_S`, `CAPTURE_MANIFEST`, `tone`, `store`,
  `CONVERSATIONS_MANIFEST`, `rows`, `STORED`, `handshake`,
  `device_headers`, `FakeSession`, `STAGES`, `BINDINGS_DEVICE_MAC`,
  `store_at`, `config_with`, `load_config_from_data`,
  `recording_config`, `BOUND_MAC`.

### The collisions, and how each was settled

Six, all resolved the way the plan's collision rule asks: one support
definition per distinct value, named for the seam it serves, with every
importing site keeping its own spelling through an import alias.

- **`DEVICE_MAC` and `DEVICE_UUID` in `test_ota.py` are not a
  collision.** The plan expected different values from the session
  family's; they are character-for-character the same strings
  `configs.py` already holds. So nothing was redefined: `checkin.py` and
  the OTA suite read them from `configs.py`.
- **`BOUND_MAC`** was defined twice, in `test_onboarding_activation.py`
  and `test_device_bindings.py`, with the same value and the same
  reason. One definition, in `configs.py`, with the reason stated once.
- **`client_for`** was two different functions: one defaults to a plain
  `Config()`, the other to a configuration whose device under test is
  unbound. They are `ota_client` and `activation_client`, the names
  `test_server_event_pins.py` had already given them.
- **`config_with`** was two different builders. The configuration one
  keeps the name in `configs.py`; the reload one is `reload_config` in
  `tools_mcp.py`.
- **`MANIFEST`** was two different mappings: `CAPTURE_MANIFEST` and
  `CONVERSATIONS_MANIFEST`.
- **`DEVICE_MAC` in `test_device_bindings.py`** is the normalized
  lowercase form the bindings table stores, a different value from
  `configs.DEVICE_MAC`. It is `BINDINGS_DEVICE_MAC` in `registry.py`.
- **`TIMEOUT_S`** would have been 30 seconds in `sessions.py` beside
  `configs.TIMEOUT_S`'s 50 milliseconds, two opposite meanings under one
  name across the package. It is `WRITER_TIMEOUT_S`.

### The caplog consolidation

`test_tools_mcp.py`'s `emitted` and `one_event` were the same two
functions as `events.py`'s `events` and `only`, which M2 had already
moved: same body, same message, differing only in a return annotation
and a docstring. The plan's category 3 (literal duplicates, strongest
definition kept) applies, so support keeps one pair, with the MCP
copy's record-typed annotations and its docstring, and the three MCP
modules import them under their own spelling. The alternative would
have been four functions in one 44-line module, two of them
indistinguishable from the other two.

### The guard

`tests/unit/test_support_boundaries.py` (new). It collects every path an
import could be naming anywhere in a file's AST, including inside a
function or a class body, and reports the line when one of them is
really a test module. `conftest` is not one, which is what decision 2
asks for: the docstring cites it, "support or a conftest", and names the
integration and smoke lanes as the reason. How a candidate is classified
changed in the PR review round, recorded below; what follows is the
milestone as it was first written.

Two of the four tests are the rules; the other two are planted sources,
following `test_event_surface_guard.py`, which keeps the mutation proof
in the suite rather than in a session's memory.

**Mutation proof.** Both branches, applied together, observed, reverted:

- `from tests.unit.test_capture import MANIFEST` added inside
  `test_drain.py`'s first test function, and
- `from tests.unit.test_capture import CAPTURE_RATE_ECHO` added to
  `tests/support/stores.py`.

Both tests failed, each naming the file and the line:

```
E       AssertionError: assert {'unit/test_drain.py': [31]} == {}
E       AssertionError: assert {'support/stores.py': [29]} == {}
```

Restored from copies taken beforehand (not `git checkout`, per
AGENTS.md), `touch`ed, and the four tests pass again.

#### The guard, after the PR review round

The review of PR #163 found the classification wrong in one direction
(P2): reading the spelling alone, every `from pkg import name` was
turned into the candidate `pkg.name` and any dotted part starting with
`test_` made it a test module, so a symbol that happened to be called
something test-shaped was refused. `from tests.support.configs import
test_data as data` and `from vendor import test_helper as helper` both
failed a guard they do not violate. Nothing in the lane is spelled that
way today, which is why the lane was green; the cost would have been
paid by whoever wrote the first such name.

The rule now resolves rather than guesses. Every candidate path an
import could name is turned into a filesystem path under
`samtal-server/` and looked up: it counts only when it is really a
`test_*.py` file or a `test_*` package directory. `from pkg import name`
still offers both `pkg` and `pkg.name`, but the filesystem decides which
of the two exists, so `from tests.unit import test_capture` is still
caught while `from tests.support.configs import test_data` is not.
Relative imports are resolved too, anchored at the importing file's own
directory and walked up one level per extra dot, since a relative import
is the obvious way around a rule written about dotted spellings.

Two limits the resolution introduces are stated in the docstring rather
than left to be discovered: an import naming a module this repository
does not hold is not reported (it cannot be a test-module import, and
collection already raises `ImportError` on it), and only static imports
are seen.

Two acceptance tests were added, taking the count from four to six:

- `test_the_rule_leaves_a_test_shaped_name_alone` plants the reviewer's
  two examples plus a `test_`-prefixed alias and a `test_`-prefixed
  production symbol, and expects no line reported.
- `test_the_rule_follows_a_relative_import_to_the_file_it_names` plants
  `from .test_ota import ...`, `from ..support.configs import ...` and
  `from . import test_capture` against a source treated as living in
  `tests/unit`, and expects lines 1 and 3.

Checked against the old rule directly: it flagged both of the reviewer's
imports, and the new one flags neither.

**Mutation re-proof.** The same two violations were planted again, in
`test_drain.py`'s first test function and in `tests/support/stores.py`,
and both branches still fail with the file and the line named:

```
E       AssertionError: assert {'unit/test_drain.py': [31]} == {}
E       AssertionError: assert {'support/stores.py': [29]} == {}
```

Restored from copies, `touch`ed, and the six tests pass again. Rerun
afterwards: `uv run ruff check .` is `All checks passed!`,
`uv run pytest tests/unit -q` is
`2262 passed, 16 skipped in 298.72s (0:04:58)`, and the collected count
is **2278**, two above the milestone's 2276 and exactly the two new
acceptance tests.

### Verification

Run from `samtal-server/`, with `PYTHONDONTWRITEBYTECODE=1` exported for
everything outside pytest.

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`:
  `2260 passed, 16 skipped in 299.67s (0:04:59)`
- `uv run pytest tests/integration -q`: `55 passed in 184.79s (0:03:04)`
- `uv run pytest tests/unit -q --collect-only | tail -1`: **2272
  before**, **2276 after**. The rise is exactly the guard's four tests.
- The inventory grep
  (`grep -rn "from tests\.unit\.test_\|import tests\.unit\.test_" tests/
  --include="*.py" | wc -l`): **33 before, 3 after**, and all three are
  inside `test_support_boundaries.py` itself: one in its docstring and
  two in the string it plants and parses. **Zero are import statements**,
  which is what the guard measures, since it reads the AST and a string
  literal is not an import. Excluding the guard file, the grep is 0.
- Normalized AST comparison of every relocated definition against its
  origin at `d17e864`, by `ast.dump` with `include_attributes=False`,
  with the origin's own nodes renamed first where a rename was decided:
  **all 53 pass.**
- `git diff d17e864` inspected for edits inside test functions: the only
  changed line inside any function body in the whole milestone is
  `test_capture_session.py`'s function-level import, rewritten from
  `tests.unit.test_ota` to `tests.support.checkin`. Everything else is
  an import line, a module-level definition removed, or a module-level
  comment.

### Deviations from the plan

Eight, none of them to the move rule, the no-cross-import end state or
an assertion.

1. **The support module is `tools_mcp.py`, not the plan's `mcp.py`.**
   This is not a preference. `mcp_stdio_server.py` sits in the same
   directory and is run as a script, which puts `tests/support` first on
   the subprocess's `sys.path`; a module named `mcp.py` there is what
   the server's `from mcp.server.fastmcp import FastMCP` finds instead
   of the SDK. With the plan's name, every stdio test failed with the
   manager unable to start, and running the server by hand reported
   `ModuleNotFoundError: No module named 'mcp.server'; 'mcp' is not a
   package`. The module docstring records the reason so the name is not
   "tidied" back later.
2. **`test_ota.py`'s `DEVICE_MAC` and `DEVICE_UUID` were not a
   collision.** The plan names them as one, with different values from
   the session family's. They are identical strings, so support gained
   no second definition and the OTA suite reads them from `configs.py`.
3. **`recording_config` went to `configs.py`, not `sessions.py`.** The
   plan's own layout section lists it under `configs.py`, and it is a
   `Config` builder that touches nothing else; the milestone brief
   allowed the choice and this is it.
4. **`emitted` and `one_event` were consolidated with `events` and
   `only` rather than added beside them**, per the plan's duplicate rule
   rather than its module list. Described above.
5. **`LIFECYCLE_TIMEOUT_S` moved out of `test_tools_mcp_http.py` even
   though nothing imported it**, because `serving`'s closure reads it
   and that suite's own bodies read it too: it is one definition in
   support, imported back, rather than a second copy.
6. **`serving`'s docstring says "Separate from the fixture above"** and
   now sits in `tools_mcp.py`, where the fixture is not above it. Left
   byte-identical, for M2's reason: a docstring is part of the AST the
   comparison checks.
7. **`AGENT`/`STAGES` in `registry.py` duplicate `MOCK_AGENT`'s value in
   `checkin.py`.** Same mapping, different names, different seams, and
   neither collides with the other, so the collision rule does not fire
   and neither module imports the other. Recorded because it is visible
   duplication that was left deliberately: merging them would have made
   the binding suite's configuration depend on the onboarding suite's.
8. **No fixture turned up**, again. Every moved name is a plain
   callable, class or constant, so no `tests/unit/conftest.py` was
   needed, which is what the plan expected.

### PR review round

External review of PR #163 (diff main...ca39cbc) by codex 0.147.0
(model gpt-5.6-sol), 2026-08-16, posted to the PR by the review run
itself. One finding:

1. **P2: the import guard misclassifies symbols as test modules.**
   `from pkg import name` was unconditionally offered as `pkg.name`
   and any `test_` component classified as a module, so a
   `test_`-prefixed symbol imported from a non-test module
   (`from tests.support.fixtures import test_data`) would trip the
   guard falsely.

   *Resolution*: accepted, fixed in 07831a1. The guard now resolves
   every candidate against the repository tree and counts an import
   only when the candidate really is a `test_*.py` file or `test_*`
   package, relative imports included; the reviewer's two examples
   are acceptance cases (old rule: flagged; new rule: clean), the
   guard grew from four tests to six, and both planted true
   positives still fail naming file and line. Details in the
   guard subsection above.

Verdict as posted: mergeable after the listed fix.
