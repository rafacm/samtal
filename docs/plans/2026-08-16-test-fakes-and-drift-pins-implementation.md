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

Eight support modules, seven of them new here, and no test module
imports `test_session*.py`, `test_tools_device.py` or
`test_boundary_contract.py` any more. The inventory grep at the
milestone's start listed 82 statements (84 at `c410af8`, two fewer
once the configurations moved); it lists 33 at its end, and every one
of the 33 names a module M3 owns.

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

`d402952` is described in its own commit message and is not repeated
here: it moved the two shared `Config` builders and the constants they
name, and deduplicated `POET_MAC` and `BOTH_MAC`, which had identical
definitions in two modules.

### What went where

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
- The inventory grep: **82 statements before, 33 after**, and all 33
  are M3's (`test_ota`, `test_tools_mcp*`, `test_conversations_*`,
  `test_config*`, `test_capture`, `test_device_bindings`, `test_drain`,
  `test_onboarding_*`, `test_ws_auth`, `test_tools_memory`). Zero name
  `test_session*`, `test_tools_device` or `test_boundary_contract`.
- `grep -rn "tests.unit" tests/support/`: no matches, so the dependency
  arrow points one way already, ahead of M3's guard test.
- Normalized AST comparison of every relocated definition against its
  origin at `d402952`, by `ast.dump` with `include_attributes=False`:
  **all 54 pass.** The boundary rename was applied to the origin's own
  nodes before dumping, which is the plan's "names aside where a rename
  was decided".
- `git diff` inspected for edits inside test functions: the only added
  line anywhere inside a function body is the rewritten function-level
  import in `test_tools_memory.py`. Every other change is an import
  line or the removal of a module-level definition.

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
