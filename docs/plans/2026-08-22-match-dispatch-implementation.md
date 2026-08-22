# Dispatch the LLM event stream with structural pattern matching: implementation

Companion to
[`2026-08-22-match-dispatch.md`](2026-08-22-match-dispatch.md). One
section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: Pin the unpinned arms, then convert the loop to `match`

### What was done

Two code commits in the plan's order, the pin committed green against
the unchanged chain, plus this section and the ticked checklist entry.

**The pin.**
`vinga-server/tests/unit/test_session_tools.py` gains one test,
`test_an_announcement_and_a_whitespace_delta_are_not_tool_calls`. That
suite is the one whose subject is the reply round itself ("the
session's tool loop, against scripted fake providers"), and the two
events the pin is about are unpinned precisely because they must not
become tool calls. It scripts two rounds: the first streams a
whitespace-only delta, then a `StreamStarted` mid-stream, then one tool
call; the second streams a sentence in fragments with a second
mid-stream `StreamStarted` and a whitespace-only delta between two
words, and ends with a second sentence.

Four assertions, all through surfaces a caller reaches:

- Both scripted sentences are spoken, and the first of them is
  `"Two words here."` rather than `"Twowords here."`, which is the
  whitespace delta still reaching the splitter and participating in
  sentence assembly.
- Exactly two rounds were asked of the model (`len(script.seen) == 2`),
  so nothing phantom survived to defeat the loop's `if not calls:
  break`.
- The turns the model was handed carry exactly `["ghost_tool"]` as
  tool calls (`script.seen` is the model-facing surface; a phantom in
  a final round would force a third round and trip the
  `len(script.seen) == 2` assertion first).
- The `llm_round` event of the whitespace-and-call round carries no
  `first_token_ms`, while the speaking round's is present, so no first
  token was timed from whitespace.

The `StreamStarted` in each round is deliberately not in first
position: the watchdog consumes a first-position one
(`pipeline.py:816`), which is exactly why no existing suite ever
delivers the event to the loop.

**The fake.** `vinga-server/tests/support/providers.py`: `Step` gains
`StreamStarted` as a union member and the module imports it; the
existing `yield TextDelta(item) if isinstance(item, str) else item`
already passes a non-string item through unchanged, so nothing else in
`ScriptedLlm` moved. Its docstring names the new member.

**The conversion.**
`vinga-server/src/vinga_server/runtime/pipeline.py`: the `isinstance`
chain in `_tool_loop` is now `match event:` with the same four arms in
the same order, `case StreamStarted():` / `case TextDelta(text=text):`
/ `case Usage():` / `case _:`. The comments moved into the arms
unchanged, rewrapped to their new indentation and nothing else. The
first-token check stayed an inner `if first_token_at is None and
text.strip():`, never a case guard. `_watchdog_stream`'s `make_stream`
parameter and return type went from `AsyncIterator[Any]` to
`AsyncIterator[LlmEvent]`, with `LlmEvent` imported from
`vinga_server.providers` where the union is declared
(`providers/base.py:312`).

**`CHANGELOG.md`.** A new `## 2026-08-22` section above 2026-08-21,
one entry under `### Changed`: what moved, and that nothing an operator
sees changed.

### The inventory

`grep -n "isinstance(" src/vinga_server/runtime/pipeline.py`, over the
whole file as the plan's amended inventory lens requires, rerun after
the conversion. Four sites remain, one of them on an event type:

- `pipeline.py:390`, `isinstance(failure, TimeoutError)`: classifying a
  provider failure, not dispatching over the event union.
- `pipeline.py:416`, `isinstance(entry.host, Absent)`: a two-arm shape
  ternary, on the issue's rejected list.
- `pipeline.py:816`, `not isinstance(first, StreamStarted)`: the
  watchdog's own single negated boolean on the same type. It stays, as
  the plan decided: a one-arm boolean is not a dispatch.
- `pipeline.py:1422`, `not isinstance(target, str)`: a guard clause, on
  the issue's rejected list.

No other dispatch over the event union exists in the file, and none was
introduced.

### Deviations from the plan

One, of bookkeeping size: the plan's module layout named one test
file, and the pin also required extending the shared LLM fake
(`tests/support/providers.py`), whose `Step` union could not script
a `StreamStarted` without lying about the type. The PR review's
finding 2 caught the omission and the plan's module layout now names
the fake. Otherwise none: the pin was committed green against the
chain before the conversion touched the loop; the arms are spelled
as the plan's decisions section specifies; the comments moved
unchanged; the annotation landed in the conversion commit; the
changelog entry is under `### Changed`; and the six session suites
named in the plan's pin lens are byte-unchanged apart from the one
file the pin was added to.

### Discoveries

**Both failure modes the plan names were checked by mutation, not
argued.** The pin was run against two deliberately broken copies of the
loop before the conversion, each restored by copying the file back
rather than by `git checkout`, per AGENTS.md:

- Deleting the `StreamStarted` arm: the test fails with
  `AttributeError: 'StreamStarted' object has no attribute
  'malformed_arguments'`, raised where the phantom call is classified.
- Moving the whitespace check into the delta's own condition (the
  chain's equivalent of the forbidden case guard): the same failure with
  `'TextDelta' object has no attribute 'malformed_arguments'`.

So the arm's deletion and the guard spelling are both caught, and both
are caught loudly rather than as a changed sentence.

**A phantom call is not a silent wrong answer.** In both mutations the
reply dies inside `_reserve_tools`, because the everything-else arm's
consumers read `.name` and `.malformed_arguments` off whatever it
appended. That is worth recording because it bounds the production risk
the plan's finding 1 describes: a mid-stream `StreamStarted` reaching
the tool arm would end the reply as a bug on the record, not quietly
change what the user hears.

**`_watched_stream` keeps its `AsyncIterator[Any]`.** It sits under
`_watchdog_stream` and is the provider-failure reporting wrapper; the
plan's finding 4 resolution named `_watchdog_stream` alone, and
widening the annotation further was left out of a commit whose subject
is the dispatch.

### Verification

From `vinga-server/`, on `feature/match-dispatch`.

Before the conversion, with the pin committed against the unchanged
chain:

- `uv run ruff check .`: **All checks passed!**
- `uv run pytest tests/unit -q`: **2798 passed, 20 skipped** in 347 s.
- `uv run pytest tests/unit/test_session_tools.py -q -k announcement`:
  **1 passed, 25 deselected**.

After the conversion:

- `uv run ruff check .`: **All checks passed!**
- `uv run pytest tests/unit -q`: **2798 passed, 20 skipped** in 338 s.
  The same counts as before the conversion, which is the
  behavior-preserving claim in its cheapest form.
- `uv run pytest tests/integration -q`: **61 passed** in 193 s.
- The six session suites of the plan's pin lens, run together
  (`test_session.py`, `test_session_tools.py`, `test_session_events.py`,
  `test_session_record.py`, `test_session_characterization.py`,
  `test_session_watchdog.py`): **124 passed**.

The bytecode trap in `AGENTS.md` applies to the mutation runs above and
was handled the way it says: `PYTHONDONTWRITEBYTECODE=1` was exported
for them, the file was restored by copying the backup back rather than
with `git checkout`, and the restored file was `touch`ed. Everything
else ran through pytest, whose `conftest.py` writes no bytecode and
clears the caches it finds.

Nothing here needs hardware, so no verification step was left
unverifiable.

## PR review round (PR #236)

External review of the PR diff: claude backend (codex quota
exhausted), claude CLI, model claude-opus-5, read-only tool set,
2026-08-22, posted on the PR by the self-posting script. Verdict:
mergeable as is; four P3 findings, all documentation accuracy, all
folded in before merge.

1. **Stale plan line references** (the conversion's import shifted
   the file by one). Fixed in `c7a87c0`: the plan's live sections
   say 816 and 1267; the recorded review round keeps its numbers as
   received.
2. **The shared fake's edit was missing from the module layout and
   denied by the deviations section.** Fixed in `fd8867d`: both now
   name `tests/support/providers.py`.
3. **The third assertion's sentence claimed a surface the test does
   not read.** Fixed in `0d21ae3`: the claim is scoped to
   `script.seen`, with the reason a phantom still cannot slip past.
4. **A sibling dispatch the audit's sweep could not see**
   (`conversations/store.py:497-535`, sequential guards the
   `elif`-shaped grep never matches). Fixed in `1df068c`: the plan's
   rejected list names it as out of scope here, and the follow-up
   decision is recorded on issue #235.
