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
## M2: split the session's three clusters

### What was done

Three commits, one per new module, each carrying its own session
delegation and the reach-in migrations that module's move forces, plus
this one for the documents. Decision 5's shape survived contact with the
code intact: no new module was dropped, added or renamed, and no
interface member the plan names went missing.

**`device/pacing.py`, `ReplyPacer`** (`1ed0d54a`). All eight pacing
facts the plan lists (`_encoder`, `_pace_start`, `_pace_count`,
`_pace_resume`, `_pace_paused_at`, `_speaking_started`,
`_speaking_started_at`, `_tts_started`), and the interface decision 5
specifies: `encode`, `flush`, `reply_started`, `restart`, `pause`,
`resume`, `speaking_started_at`, `tts_start_due`, `first_frame(now)`
and `transmit(packet, deliver)`. Two read-only properties are there
because the migration table requires them by name ("reads the pacer's
public pause state and clock"): `paused` and `cadence_start`.

`transmit` holds the pinned order of one transaction. The first-frame
stamp is the session's call before the loop, exactly where today's is
(`send_audio` stamps once per batch, not once per packet), and then per
packet: cadence sleep, pause wait, `await deliver(packet)`, count
advancement. The count moves only after `deliver` returns, which is
today's post-send increment. `deliver` is the session's closure and
does the socket send and then the capture feed, so the session keeps
both halves of what a frame's slot is spent on.

The `PlayableAudio` wrapping stayed in the session, per the plan:
`encode` and `flush` return `list[bytes]` and `encode_audio` /
`flush_encoder` wrap them. `first_frame` answers rather than announces,
so `speaking_started` is still emitted by `send_audio` and the pacer
has never heard of an event or an agent.

**`device/capture_audio.py`, `CaptureAudio`** (`d0125056`). The three
codec objects, the protocol version, and the close of the
`SessionCapture` it is handed; `microphone(data)`, `reply(packet)`,
`close()`. Both fail-open docstrings moved with their code unchanged,
and the module docstring states the rule they are two instances of:
nothing here raises. The session's field is `_capture_audio`, compared
`is not None`, and both hot-path calls are one guarded line.

The lifecycle is decision 5's, unchanged from today's: `_start_capture`
opens the capture, calls `self._events.attach_capture(capture)` on the
raw capture, then constructs `CaptureAudio` around it; the `finally`
emits `session_closed`, detaches the events capture, calls
`CaptureAudio.close()` and clears the field, in that order.

**`device/watchdog.py`, both watchdogs** (`d9160d8c`).
`HELLO_TIMEOUT_S` re-homed beside `first_contact()`, which reads the
module global at call time; the session imports the module
(`from vinga_server.device import watchdog`) and writes
`async with watchdog.first_contact():`, so patching
`watchdog.HELLO_TIMEOUT_S` still bites. Hello parsing and the
protocol-error close policy stayed in `_receive_hello`.

`IdleWatchdog` takes the task, the mark and the countdown arithmetic.
Its constructor is the plan's three narrow dependencies and not the
session: `timeout_s`, `defer`, `on_idle`. `start()`, `stop()`, `mark()`,
plus the read-only `marked_at` the migration table requires by name.
The loop calls `defer()` every iteration, so the two facts that change
mid-session stay live exactly as `_watch_for_idle` had them. The
session's two halves are named methods rather than lambdas, which is
where the moved prose went: `_idle_deferred` carries the realtime-only
paragraph, the reply-in-flight paragraph and the arriving-audio
paragraph, and `_idle_expired` carries the `session_idle` emission, the
normal-closure comment and the `request_shutdown` call.

Both hot paths kept their statement order: `_handle_audio` feeds the
capture before every guard, per its own comment, and `send_audio`
stamps the first frame before the pause gate.

### The test migration table

Decision 5's table, each row as it landed. No row changes what its
assertion means, and no test outside this table was edited.

| File | Old reach-in | New form | What the assertion still pins |
| --- | --- | --- | --- |
| `test_session_barge_in.py` | `session._pace_start` (three reads) | `session._pacer.cadence_start` | The pacing clock shifted forward by at least the pause, so the cadence survives a barge-in confirmation instead of bursting to catch up. |
| `test_session_barge_in.py` | `not session._pace_resume.is_set()` / `session._pace_resume.is_set()` | `session._pacer.paused` / `not session._pacer.paused` | The stream is held while the confirmation is in flight and flowing again after it. Equivalent by construction: `pause` clears the event and stamps `_pace_paused_at` together, and `resume` restores both. |
| `test_session_characterization.py` | `session._encoder = RecordingEncoder(session._encoder, log)` | `session._pacer._encoder = RecordingEncoder(session._pacer._encoder, log)` | The feed order into the one Opus encoder a reply and a filler share: the filler's clip, resampler tail and flush are one synchronous batch with no send between them. Still a white-box wrap, now of the object that owns the encoder. |
| `test_boundary_contract.py` | `session._mark_activity()`, `session._last_activity` (three reads) | `session._watchdog.mark()`, `session._watchdog.marked_at` | `user_turn_ended` marks conversational activity in both listening modes, so a runtime that answers nothing cannot leave the timer counting from before the user spoke. |
| `test_session_close_reason.py` | `session._stop_idle_watchdog = held` | `session._watchdog.stop = held` | A drain arriving while a close is already under way does not take a cause decided before it: the reason stays `client`. The held step is the same step, on the object that now owns it. |
| `test_conversations_session.py` | `parametrize("step", [..., "_start_idle_watchdog"])` with `setattr(session, step, boom)` | `parametrize` over two `(session, boom)` callables, the second `setattr(session._watchdog, "start", boom)`, with `ids` naming the steps | A failure at any step after the first attachment still reaches `session_closed`, the store's close, the sink's detach and the capture's close. Both parameter cases, both assertions, unchanged. |
| `test_conversations_session.py` | `assert session._capture is None` | `assert session._capture_audio is None` | The session gave back the capture it took: same meaning, new name for the field that holds it. |
| `test_session_events.py` | `session._pace_start = None` | `session._pacer.restart()` | `speaking_started` fires once per reply and not once per agent leg, with the leg's restart driven through the call a handover actually makes. The docstring's parenthesis was reworded to match. |
| `test_session.py` | `monkeypatch.setattr(session_module, "HELLO_TIMEOUT_S", 0.05)` | `monkeypatch.setattr(watchdog_module, "HELLO_TIMEOUT_S", 0.05)` | A client that connects and says nothing is closed with a protocol error and "no hello". The plan's hello-timeout retarget. |

`test_conversations_session.py` gained one import,
`collections.abc.Callable`, for the parametrized callables' annotation.
That is the only line in the M2 test diff that is not a row above.

### `wc -l src/vinga_server/device/session.py`

| | Lines |
| --- | --- |
| Before (`802c8d28`, M1's tip) | 1,229 |
| After | 1,148 |

Eighty-one lines net, which is a smaller number than the move looks
like: 390 lines of new module were written, and `session.py`'s own diff
is 204 lines removed against 123 added. Most of what came back is
prose rather than code, because most of the idle watchdog's docstring
was policy rather than arithmetic and stayed behind in
`_idle_deferred`. The split is visible in what the class holds rather
than in its length: fourteen fields became three, the seven pacing
methods became one-line delegations, and the two capture decode paths
and the four idle-watchdog methods left entirely.

| Module | Lines |
| --- | --- |
| `device/pacing.py` | 183 |
| `device/watchdog.py` | 115 |
| `device/capture_audio.py` | 92 |

### Deviations from the plan

**None in the split itself.** Every module, class name, interface
member and lifecycle order decision 5 pins is what landed, including
the two amendments that changed the shape (`transmit` in place of
`gate()`, and the watchdog's three narrow dependencies).

Two things the plan did not name had to be decided, and neither
contradicts it:

**Two read-only properties were added that the interface lists do not
mention.** `ReplyPacer.paused` and `cadence_start`, and
`IdleWatchdog.marked_at`. The migration table requires them in so many
words ("reads the pacer's public pause state and clock", "reads the
watchdog's public mark time"), so they are mandated by decision 5 even
though its interface bullets stop short of naming them. They are reads
with no writer.

**The encoder reach-in stayed a reach-in.** Decision 5 offers
"replaces the encoder on the pacer, or the whole pacer, whichever keeps
the assertion's subject", and the first was taken:
`session._pacer._encoder`. The alternative would have been a public
`encoder` attribute on `ReplyPacer` that no production caller wants,
which is the compatibility-alias shape the same decision forbids.
The test's subject is the shared encoder's feed order, which no public
surface reports, and the reach-in is documented in place as it was
before.

### Discoveries

**Two emit-path labels in the event-baseline harness now name a method
that no longer exists.** `tests/tools/event_baseline.py:854` declares
`Driver((EDGE, "DeviceSession._watch_for_idle", 1), ...)` and
`tests/unit/test_event_baseline.py:318` keys `CARRIED` on
`"vinga_server.device.session:DeviceSession._watch_for_idle #1"`. After
the split, `session_idle` is emitted from `DeviceSession._idle_expired`.

Nothing fails, and that is the point worth recording: the static walk
that used to hold these labels to the source retired with the last
event conversion (`tests/tools/event_baseline.py` says so in its own
comment), so `identity` is a declared label matched against nothing but
itself. The suite is green with the labels stale. They were left
untouched because M2's own pin is that its test diff is exactly the
migration table above, and this is not a row of it; the honest one-line
fix is to rename both to `DeviceSession._idle_expired`, and it belongs
to whoever takes the review flag rather than to a milestone whose
checkable property is the size of its test diff.

**The reach-in census moved by three sites and minus three names.**
`uv run python -m tests.tools.reach_ins` reports 183 sites over 82 names
before the split and 186 over 79 after. The census is lexical, so
`session._pacer.cadence_start` counts as two sites where
`session._pace_start` counted as one, which is where the three came
from; the three names are the ones the split retired from the tests'
vocabulary. No new name was reached for that the migration table does
not list.

**`filler_runner`-style cycle checks were unnecessary here.** All three
new modules import downward only (`audio`, `capture`, `protocol`,
`device.boundary`), and `device/session.py` imports all three, so there
is no direction in which a cycle could form. `device/__init__.py`
imports nothing, as M1 found for `runtime/__init__.py`.

**The pacer's frame duration is now stated once.** `send_audio`
computed `frame_s = OUTPUT_AUDIO.frame_duration / 1000` on every batch
while the encoder was constructed with the same `frame_duration` in
`__init__`: two readings of one fact. `ReplyPacer.__init__` takes
`frame_duration_ms` once and derives both, which is the locality rule
applied to a two-line duplication that had never been wrong.

### Verification

From `vinga-server/`, on `feature/session-split-m2` at `d9160d8c`, with
`PYTHONDONTWRITEBYTECODE=1` exported for everything outside pytest.

- `uv run ruff check .`: **All checks passed!**
- `uv run mypy` (the events lane): **Success: no issues found in 4
  source files**
- `uv run pytest tests/unit -q -n auto --dist loadfile`: **2859 passed,
  20 skipped** in 42.35 s. Exactly M1's counts: no test was added,
  deleted, skipped or renamed, which is the milestone's own claim about
  its diff.
- `uv run pytest tests/integration -q`: **61 passed** in 189.69 s, also
  M1's count.
- The generated-document drift checks, all four, run the way CI runs
  them: `domain-config.md`, `conversations-schema.md`, `events.md` and
  `api-openapi.json` all **diff clean**. Expected, and checked rather
  than assumed: this milestone changes no artifact source, and
  `events.md` in particular carries no emission sites, so moving one
  could not have moved it.

The grep decision 6 asks for after M2, over `src/vinga_server/device/session.py`:

- `_pace_start`, `_pace_count`, `_pace_resume`, `_pace_paused_at`,
  `_speaking_started`, `_speaking_started_at`, `_tts_started`,
  `_encoder`, `_last_activity`, `_idle_watchdog`, `_capture_decoder`,
  `_capture_reply_decoder`, `_capture_resampler`, `HELLO_TIMEOUT_S`,
  `_mark_activity`, `_watch_for_idle`, `_start_idle_watchdog`,
  `_stop_idle_watchdog`: **one hit**, `def flush_encoder`, which is the
  `DeviceOutput` boundary method's name and stays.

Across `src` and `tests`, the only surviving mentions of the moved
method names were the two stale event-baseline labels recorded under
Discoveries above; both were renamed to `DeviceSession._idle_expired`
in their own commit after the milestone's table-pinned diff, taken
deliberately rather than smuggled into the table.

Nothing in M2 needs hardware, so no verification step was left
unverifiable.

## PR review round (PR #269)

External review of the M2 diff, 2026-08-23. Two findings, verdict
mergeable after fixes. Both accepted and fixed, each in its own commit;
the second is a correction to this document's own verification record
and is recorded with it below.

1. **P1: a capture that cannot build its codecs is stranded open, and
   the library's own prose leaves through `run`.** `_start_capture`
   attaches the raw capture to `SessionEvents` before constructing
   `CaptureAudio` and assigns `_capture_audio` only after the
   construction returns. A PyAV failure inside the constructor therefore
   left `_capture_audio` None, and the close path releases the field, so
   the `finally` neither detached the consumer nor closed the open
   recording; the exception then left `run` untouched and reached
   `ws.py` as a library traceback.

   *A regression the split introduced, and named as one.* Before the
   split the three codecs were built after `self._capture` had been
   assigned, so the `finally` always had something to release. The move
   put the assignment last without noticing that it had been carrying
   the release.

   *Accepted, fixed.* The construction is guarded. On failure the events
   capture is detached, the `SessionCapture` is closed on the spot
   (detach first, so a close that fails in its own right still leaves no
   consumer writing into a capture on its way out), and the failure is
   reported by `logger.warning` naming `type(exc).__name__` and nothing
   else, which is the shape `_cleanly` uses forty lines above in the
   same class, and for the same stated reason: an exception's message on the way out of a
   session is one of the places a provider's or a device's bytes reach
   the retained surface, and which exception it was is not actionable
   anyway.

   *And the conversation continues, which is a deliberate delta rather
   than a restoration of the pre-split behavior.* Before the split a
   codec failure ended the session too, by propagating. The round
   sanctions the better behavior, and the reasoning is already written
   down one layer away: `CaptureStore.open` declines a directory it
   cannot use with "a conversation is worth more than a recording of
   it", and `SessionCapture`'s own docstring says writes are best effort
   by construction because "a capture that fails must never take a
   conversation down with it". Codec construction was the one step of
   starting a capture that had been left out of that rule. It is
   operator-visible, so `CHANGELOG.md` carries it as a `### Fixed`
   entry that tells a deployment with capture enabled which line says a
   session ran without being recorded.

   *One thing deliberately not done.* The two existing "capture did not
   start" signals are declared events, and the nearer of them,
   `CaptureDirectoryUnusable`, already reports its cause the same
   sanitized way, as a `ClassName`; this third sibling could be promoted
   to one beside it and `CaptureBelowFloor`. It was not, because a new catalog variant
   moves `docs/reference/events.md`, the driver count and the `CARRIED`
   table, and this is a fix in a milestone whose claim is that it moves
   no artifact. The promotion is a follow-up worth taking on its own.

   **The regression test.**
   `test_a_capture_whose_codecs_will_not_open_is_released_and_the_session_lives`
   in `tests/unit/test_capture_session.py` replaces `CaptureAudio` with
   a constructor that raises a `CodecUnavailable` chained from an
   `OSError`, both messages carrying the same credential-shaped
   sentinel, so what must not reach a record is the whole chain rather
   than only the outermost message. It drives a session through `run`
   with a real `CaptureStore`, then after the failure feeds a real
   framed Opus packet through `_handle_audio` and a whole reply through
   `send_audio`, which are the two hot paths the capture used to sit in.
   It asserts the session ran to a clean close, that `_capture_audio` is
   None and `attached_capture(session)` is None (a new reader beside
   `attached_taps` in the test hub, the same white-box shape for the
   capture slot that `attached_taps` is for the tap list), that the
   manifest's `capture.complete` is `True` (which is what says the file
   was closed rather than abandoned: a strand leaves the `False` its
   start wrote), that the warning line reads
   `recording could not start (CodecUnavailable)`, and that neither the
   sentinel nor the word `Traceback` appears in `both_formats(caplog)`
   or in stdout or stderr.

   *The fix proved load-bearing, both halves separately.* With the whole
   guard reverted and nothing else changed, the test fails on the leak,
   the exception reaching the test through `run`:

   ```
   src/vinga_server/device/session.py:415: in run
       self._start_capture(manifest)
   E   tests.unit.test_capture_session.CodecUnavailable: could not build
       the capture codecs for sk-live-3f9a21c7-never-a-real-credential
   ```

   With the guard kept but the two release lines removed, so nothing
   leaks, it fails on the strand instead:

   ```
   >   assert attached_capture(session) is None, "the events capture was left attached"
   E   AssertionError: the events capture was left attached
   E   assert <vinga_server.capture.SessionCapture object at 0x10fa30fe0> is None
   ```

   The file was restored by copy and `touch`ed after each, per the trap
   recorded in `AGENTS.md`, and the caches cleared.
