# Split device/session.py and delete the seams with no second side: implementation

Companion to
[`2026-08-23-session-split.md`](2026-08-23-session-split.md). One
section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: delete the seams with no second side

### What was done

Thirteen commits, one logical change each, in the plan's own order:
decision 1's six protocols, then decision 2's timestamp, decision 3's
`StreamStarted` case, decision 4's tool split into source, artifacts,
tests and hand-written prose, and two small follow-ups the census grep
and the file's own wrapping asked for (`480c6dc7`, `13748edd`).
`device/session.py` was not touched at all, which is what M1 promised
and what makes M2's diff about the split alone.

**The six protocols** (`a0d35a5c`, `b5259b2d`, `a27a3f17`, `249ae654`,
`fee82a67`, `7574e3ea`). Each annotation site got the named destination
decision 1 chose for it, and each Protocol's docstring was read for
what was still true before it went:

- `ReplyControl` → `TurnTaking.__init__`'s `reply` annotates
  `"PipelineRuntime"` under `TYPE_CHECKING`. The Protocol's narrowness
  paragraph (four members, and why the confirmation is asked for whole
  rather than assembled in the ladder) moves into `TurnTaking`'s class
  docstring beside the parameter it describes. `pipeline.py`'s two
  mentions and `test_turntaking.py`'s module docstring now say "four of
  its methods"; `FakeReply`'s docstring says which class it stands in
  for.
- `TurnView` → `FillerRunner.__init__`'s `turn` annotates
  `"TurnTaking"` under `TYPE_CHECKING`. The two reads and the
  read-only rule move into `FillerRunner`'s class docstring.
  `FakeTurn`'s docstring names `TurnTaking`.
- `FillerCache` → both sites become `Mapping[str, FillerClips]`, and
  the vestigial `fillers is not None` guard and the `FillerCache`
  import go with it. The Protocol's one sentence that was not a
  restatement of `Mapping` (a plain dictionary is as much a cache as a
  generation's mapping, so a test need not build a world) moves into
  `FillerRunner`'s docstring.
- `Served` → both sites become `"Generation | None"` under
  `TYPE_CHECKING`, `filler.py`'s `__all__` loses the name, and the
  three reads move into `_kept`'s docstring, which is where the three
  reads happen. `ProviderWorld` became an unused import and went.
- `ToolClaim` → all twelve annotation sites become
  `"records.ToolInvocation"`, with `from vinga_server.conversations
  import records` under `TYPE_CHECKING`; `source.py`'s `__all__`
  shrinks. What the three properties documented moves into
  `ToolSource`'s class docstring, which is where every question taking
  a claim is declared.
- `McpManager` → every site annotates `McpServerManager` directly
  (`registry.py` five, `reload.py` four, `slice.py` two under its
  existing `TYPE_CHECKING` guard, the package import and `__all__`,
  and `_stopped` in `manager.py` itself). The package comment stops
  offering the name as a public seam. `McpServerManager`'s docstring
  keeps the list of the fourteen members a registry and a reload touch
  and says plainly that the discipline is a review of those two
  modules, which is what it always really was.

**The v2 timestamp** (`5bcc51d8`). `Frame` loses the field and `wrap`
the parameter; the `_V2_HEADER` struct is untouched and `wrap` packs a
literal `0` where it packed a default `0`, so the wire bytes are
identical. v2 `unwrap` parses the field into `_` because the header
still has to be read past to reach the payload. The two replacement
pins are decision 2's: `test_version_2_lays_out_the_firmware_struct`
now asserts the literal zero in the unchanged twenty header bytes, and
a new `test_a_stock_firmwares_timestamp_is_read_past_and_dropped`
hand-builds an incoming frame carrying `0x01020304` and asserts it
unwraps to its payload.

**The `StreamStarted` case** (`e1ed30da`). The `_tool_loop` arm is
gone; `providers/base.py`'s docstring now says the contract that is
true and enforced ("yielded at most once, first", consumed exclusively
by `_watchdog_stream`, nothing downstream sees one) and names what
would go wrong if an adapter broke it, which is the everything-else arm
recording a call the model never made. The real pins are untouched: the
adapter suites still assert the announcement leads each stream and
`test_session_watchdog.py` still asserts `_watchdog_stream` consumes
it.

**`random_number`** (`7bdafc29` source, `dae9e90a` artifacts,
`7eb05917` tests, `21fb5097` prose and changelog). The `ToolDef`, the
executor, `_bound`, the three constants and the `secrets` import;
`names.RANDOM_NUMBER` with `BUILTIN_TOOL_NAMES` shrinking to two; the
unconditional `snapshot` append and the `dispatch` arm in `source.py`;
and the sentence in `config/models.py`. Both committed artifacts
regenerated under their byte pins, the four README sites and the two
example-YAML comments rewritten, and a `### Removed` entry under the
existing `## 2026-08-23` changelog heading. `docs/features/2026-08-19-random-number-tool.md`
and the older changelog entries are history and were left alone.

### The artifact diff, read

Regenerated with the two commands CI diffs against, from
`vinga-server/`:

```
uv run vinga-server config reference > ../docs/reference/domain-config.md
uv run vinga-server config openapi > ../docs/reference/api-openapi.json
```

`git diff --stat docs/reference/` after them:

```
 docs/reference/api-openapi.json | 4 ++--
 docs/reference/domain-config.md | 4 ++--
 2 files changed, 4 insertions(+), 4 deletions(-)
```

Four changed lines, and they are one prose site rendered four times.
`AgentConfig.mcp` and `AgentDefaultsConfig.mcp` share the description
string, and each of the two documents renders both layers, which is
exactly the four line numbers the plan's census named
(`api-openapi.json` 4795 and 4918, `domain-config.md` 254 and 287). On
every one of them the change is the same clause deleted: "and
random_number under none at all, " leaves the sentence, which then
reads "... memory configured) rather than by grant." Nothing else in
either file moved: no field, no ordering, no schema, no header
arithmetic.

Both files were regenerated a second time and byte-compared against
the committed copies, which is the drift check as CI runs it. Both
diffed clean. SHA-256 of the committed artifacts:

- `docs/reference/domain-config.md`:
  `63ed33b5ab42f256fd11b0aed8e0a4fa79e17a080e3a7db5472a563005115792`
- `docs/reference/api-openapi.json`:
  `8e56afd1431052f86c434d53064110b7d2b1a3c44e8d885c1045106f26371b6f`

### What else the mid-list `StreamStarted` script pinned

The plan's risk 2 requires this recorded before the edit, so the whole
test was read first. `test_session_tools.py`'s
`test_an_announcement_and_a_whitespace_delta_are_not_tool_calls`
scripted two rounds:

```python
["   ", StreamStarted(), call("ghost_tool")],
["Two", StreamStarted(), " ", "words here. ", "And a second sentence."],
```

Four assertions ride on that script, and the scripted `StreamStarted`
is load-bearing for none of them:

1. `spoken == ["Two words here.", "And a second sentence."]`. What this
   pins is that a whitespace-only `TextDelta` reaches the sentence
   splitter: the deltas `"Two"`, `" "` and `"words here. "` have to run
   together into one sentence. The `StreamStarted` sits between the
   first two and is not what joins them; removing it leaves three text
   deltas assembling identically.
2. `len(script.seen) == 2`. Two rounds, the second the reply, so no
   phantom call asked for a third. Driven by the one `ToolCall` in
   round one.
3. `[c.name for c in made] == ["ghost_tool"]`. The record carries the
   one call the model made, which is the assertion an announcement
   landing in the everything-else arm would break. It is the assertion
   the deleted case existed for, and after the deletion the announcement
   cannot reach the loop at all, which is what makes the script's
   version of the scenario unreachable rather than merely passing.
4. `not hasattr(tool_round, "first_token_ms")` and
   `speaking_round.first_token_ms >= 0`. Timed off the whitespace delta
   in round one and the real text in round two. Untouched by the
   announcement.

So the whitespace half of the test is the half that survives and is
still the only place that shape is delivered to the loop. The test is
renamed `test_a_whitespace_delta_is_not_a_tool_call`, its script keeps
the whitespace deltas in both rounds and drops both announcements, and
its docstring says why nothing scripts one any more. No equivalent
non-`StreamStarted` event was needed in the script, per the plan's
conditional.

### Deviations from the plan

Four, each small.

**The plan named one scripted `StreamStarted` and there were two.**
Decision 3 and the census both point at `test_session_tools.py:69`,
which is round one's. Round two carried a second one on line 70. Both
had to go: leaving one would keep a script driving the stream shape
whose impossibility the reworded contract now asserts. The readout
above covers both.

**`ScriptedLlm`'s `Step` type dropped its `StreamStarted` arm.** Not in
the plan's list. With the last scripted announcement gone,
`Step = str | list[str | StreamStarted | ToolCall | Usage]` in
`tests/support/providers.py` was vocabulary for a stream shape no
adapter may produce and no test builds, which is the same
contradiction decision 3 exists to remove, one level down. The arm and
the now-unused import went, and `ScriptedLlm`'s docstring says why.

**One of the two `random_number` session tests was re-anchored rather
than deleted.** Decision 4 lists the exact-list assertions and the
whole-file deletion but says nothing about the two tests in
`test_session_tools.py` whose subject was the tool.
`test_a_random_number_is_offered_with_no_configuration_and_drawn_when_asked_for`
was deleted: its subject is the unconditional builtin, which no longer
exists, and the empty offering is pinned by the shrunk exact-list
assertion beside it.
`test_a_random_range_that_cannot_be_drawn_from_comes_back_as_an_error`
was kept and re-anchored on `remember` as
`test_a_builtin_asked_with_arguments_it_cannot_use_comes_back_as_an_error`,
because its subject is not the tool: it is the shape any builtin's bad
arguments take, an error result the model reads and calls again from
rather than an ended reply, and nothing else in the session suites
pins that. It also asserts the store was not written, which is what
makes it the reply-shape pin rather than a second copy of the stored-
record case in `test_conversations_session.py`.

**The `ToolClaim` annotation sites were twelve, not thirteen.** The
census counted thirteen; the thirteenth is the `__all__` entry or the
class statement itself, depending on how one counts. Twelve `claim:
ToolClaim` parameters were retargeted, plus the export.

### Discoveries

**A runtime `filler_runner` → `turntaking` import would not have
cycled.** Decision 1 says `FillerRunner`'s `turn` takes its annotation
under `TYPE_CHECKING` for "the same cycle direction" as `ReplyControl`.
Checked against the code: `turntaking.py` does not import
`filler_runner`, and `runtime/__init__.py` imports nothing, so a
runtime import would have been legal. `TYPE_CHECKING` was used anyway,
as the plan directs and because the runner has no use for the floor
module at import time; the comment beside the import says that rather
than claiming a cycle that is not there. The `ReplyControl` and
`Served` cycles the plan names are real, and were confirmed:
`pipeline` imports `turntaking`, and `generation` imports `FillerClips`
from `filler`.

**The Protocol member docstrings were a second home for facts the
concrete classes already documented.** `McpManager` declared fourteen
members, every one of which `McpServerManager` implements with its own
docstring twenty lines further down the same file. The same is true of
`ToolClaim` against `ToolInvocation`'s field comments. Deleting the
protocols removed a duplicate that could go stale, which is worth more
than the deleted lines: the four docstring paragraphs that were NOT
duplicates are the ones that had to be re-homed, and they are named in
the per-protocol list above.

**A prose "Served" survives the census grep and is unrelated.**
`config/responses.py:878` says "Served as `application/problem+json`",
an ordinary English participle. `grep -rn '\bServed\b'` finds it, which
is why the grep below is spelled against the Protocol's use sites
rather than the bare word.

**The `random_number` prose renders four times, not two.** The plan's
Risks section expects "exactly two rendered sites" while its own census
names four line numbers. The census is right, and the reason is that
one description string is shared by the agent and agent_defaults
layers.

### Verification

From `vinga-server/`, on `feature/session-split` at `13748edd`, with
`PYTHONDONTWRITEBYTECODE=1` exported for everything outside pytest.

- `uv run ruff check .`: **All checks passed!**
- `uv run mypy` (the events lane): **Success: no issues found in 4
  source files**
- `uv run pytest tests/unit -q -n auto --dist loadfile`: **2859
  passed, 20 skipped** in 42.72 s. One test file (`test_tools_random.py`,
  169 lines) left the lane and the framing suite gained a case, which
  is the count's whole movement.
- `uv run pytest tests/integration -q`: **61 passed** in 191 s.
- The generated-document drift checks, all four, run the way CI runs
  them (regenerate to a temporary file, diff against the committed
  copy): `domain-config.md`, `api-openapi.json`, `events.md` and
  `conversations-schema.md` all **diff clean**. The first two are the
  ones this milestone regenerated; the other two are checked to say the
  removal moved nothing in them.

The census greps decision 6 requires, from `vinga-server/`, over `src`
and `tests`:

- `grep -rn "ReplyControl\|TurnView\|FillerCache\|ToolClaim\|McpManager" src tests`:
  **nothing**.
- `grep -rn "Served" src tests`: **one line**,
  `config/responses.py:878`, "Served as `application/problem+json`",
  an ordinary English participle unrelated to the deleted Protocol.
  Nothing in the codebase names the type any more.
- `grep -rn "frame.timestamp" src tests`: **nothing**. `timestamp` as
  a bare word survives only in `framing.py`, in the struct comment and
  in the two comments saying the field is packed as zero and read past.
- `grep -rn "random_number" src tests`: **nothing**. This one cost a
  commit of its own: the honest comment in `test_tool_names.py` about
  what dropping a reserved entry permits wanted to name the instance,
  which would have put the string back in `tests/`. It states the rule
  instead ("a name that stops being a builtin becomes usable as an
  entry name, and an entry's tools publish qualified"), and the
  changelog names the instance, which is where a reader looking for it
  goes.

`random_number` survives, as the plan permits, in `CHANGELOG.md` (its
arrival entry and now its removal entry), in
`docs/features/2026-08-19-random-number-tool.md`, in
`docs/plans/`, and in the committed spike output under `spikes/`, which
is regenerated spike material and not CI-checked.

Nothing in M1 needs hardware, so no verification step was left
unverifiable.

## PR review round (PR #268)

External review of the M1 diff, 2026-08-23. Two P1 findings, verdict
mergeable after fixes. One fixed, one declined with the evidence
gathered rather than asserted.

1. **P1: malformed frame values enter an exception chain and a log
   record.** `framing.py` interpolated the declared payload size and
   the carried length into `FramingError`, and `device/session.py:934`
   logs that exception verbatim at warning level, which is a retained
   surface.

   *Accepted, fixed in `e0a3cb59`.* Every raise now carries a fixed
   sentence naming its category and interpolating nothing, integer
   lengths included: `SHORT_V2_FRAME`, `SHORT_V3_FRAME`,
   `V2_SIZE_MISMATCH`, `V3_SIZE_MISMATCH`, `UNSUPPORTED_VERSION`. They
   are module constants rather than literals at the raise, so the two
   suites that pin what reaches a log record read the sentence from the
   module that raises it and cannot keep a second copy that drifts. The
   exception type is unchanged, the `framing_error` drop-reason token
   beside the log call is untouched, and the logging call site itself
   was not restructured, since `session.py` is M2's territory: what
   changed is only what the exception renders.

   Two new pins, one per surface.
   `test_a_lying_v2_header_is_not_quoted_back_by_the_refusal` builds a
   v2 header announcing `987654321` payload bytes and asserts the
   exception's `str`, its `args`, and both chain slots carry none of it.
   `test_a_malformed_binary_frame_is_dropped_without_quoting_its_header`
   drives the same frame through a real v2 session, carrying `65521`
   actual payload bytes, and asserts the session keeps answering, the
   warning line says exactly `framing.V2_SIZE_MISMATCH`, and neither
   planted number appears anywhere in `both_formats(caplog)`. It sits
   beside the malformed-abort case it is the binary sibling of, which
   already held this rule one layer up.

   *The fix proved load-bearing.* With the v2 size-mismatch message
   reverted to its interpolating form and nothing else changed, both new
   tests fail, and the captured record shows the leak verbatim:

   ```
   WARNING vinga_server.session: session <id>: dropped binary frame:
   version 2 frame announces 987654321 payload bytes but carries 65521
   ```

   Both planted values on a warning-level record, which is what the
   finding said and what the sentences remove. The file was then
   restored by copy and `touch`ed, per the trap recorded in `AGENTS.md`.

2. **P1: rejected tool input is exposed through the conversation HTTP
   API.** The stored-record test keeps a credential-shaped rejected
   argument verbatim, `conversations/store.py:785` persists arguments
   even when `call.is_error`, and `conversations/api.py` serves every
   invocation column.

   *Declined, with the evidence checked rather than argued.* This is the
   content-and-telemetry split working as designed, and the same finding
   was already raised and declined on the same ground in the #82 review
   round, where the decline was pinned by the very test this milestone
   re-anchored. Four pieces of evidence:

   - **The behavior predates this branch.** `git log -L 785,785` over
     `store.py` names exactly one commit in that line's whole history,
     `b3d4f6f9` (2026-08-15), an ancestor of `main`; `is_error` has
     never gated the column, which is gated by the deployment's `text`
     switch and by `malformed`. `INVOCATION_COLUMNS` in `api.py` comes
     from `a434d15a` (2026-08-16), also on `main`.
     `git diff --stat 3076407b..HEAD -- src/vinga_server/conversations/`
     is empty: this branch changes nothing in the conversations package.
   - **Verbatim storage of a rejected argument was already the pin.** At
     the merge base the same test, under the same name, asserted
     `json.loads(call["arguments"]) == {"minimum": SENTINEL, "maximum": 6}`
     for a call the tool refused, and its docstring already carried the
     decline's reasoning: "an argument redacted because the tool refused
     it would hide the evidence of why the tool refused, which is the
     one question the record exists to answer."
   - **The docs classify tool arguments as content.**
     `docs/architecture/observability-surfaces.md`'s tier table:
     "**Conversation store** (#120, `conversations.db`) | Content as
     system of record: turns, tool and MCP calls with arguments and
     results, keyed by session and user", with "access-controlled reads
     under `/api`". The ADR's decision 2 says the store is "the system
     of record for content", against decision 1's "No conversation text,
     no far-side bytes, no exception message text" for the events. The
     store plan records that text-off "nulls `heard`, `reply`, the
     legs' text, and the tool name, arguments and result together",
     which is arguments classed with name and result under one switch.
     Nothing in `docs/adr/` or `docs/architecture/` requires redacting
     invalid input inside the content surface: all four masking and
     scrubbing mentions describe the external practice the ADR was
     checked against, and "source-side restriction over sink-side
     scrubbing" is scoped to the events surface, made mechanical by
     #155.
   - **The credential-shaped sentinel is not new.**
     `SENTINEL = "hunter2-not-a-real-credential-9f31c7"` predates the
     branch, commented "shaped like something an operator would be
     horrified to find", and the old case passed it as
     `{"minimum": SENTINEL, "maximum": 6}`. The re-anchor changed which
     tool refuses it and the shape of the refusal (a string where an
     integer belongs, now a list where a string belongs), not whether a
     credential-shaped rejected argument is stored verbatim and asserted
     to be.

   Nothing found contradicts the decline, and no store, API or
   assertion was modified for this finding.

### Verification after the review round

From `vinga-server/`, on the amended branch.

- `uv run ruff check .`: **All checks passed!**
- `uv run mypy`: **Success: no issues found in 4 source files**
- `uv run pytest tests/unit -q -n auto --dist loadfile`: **2861 passed,
  20 skipped**, two more than the milestone's, being the two new pins.
- `uv run pytest tests/integration -q`: **61 passed**.
