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
