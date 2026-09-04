# Implementation notes: a failed reply says so

Companion to
[`2026-09-04-reply-failure-fallback.md`](2026-09-04-reply-failure-fallback.md),
one section per milestone, appended in the change that ticks the
milestone there.

## M1: the failure arm gets a voice

Built as the plan describes, amendments included. The deviations below
are the places where the plan left a choice open or where the code
disagreed with the plan's reading of it; everything not listed here is
the plan's own shape.

### Deviations and decisions

**`nothing_sayable` is deferred to M2, and the catalog permits it.** The
plan's M1 line makes this conditional: declare the second variant now
only if the catalog forbids adding one later. It does not. A
`Declaration` holds a tuple of variant classes and `declare()` is an
ordinary call at import, so M2 adds `NothingSayable` to
`REPLY_FALLBACK`'s tuple and a member to `FallbackReason` with no
migration of any kind. The enumeration is declared with one member and a
note saying the second is coming, which is what keeps the `reason` field
honest: a closed set with one token today reads as "this is the reason",
not as "there is only one".

**No `views.py` change was needed for `phrase`.** The plan asks for the
filler's `views.py` treatment. That treatment exists because
`FillerConfig.phrases` defaults to an empty list, and `_absent` hides a
default that means absence (null, an empty list, an empty mapping).
`FallbackConfig.phrase` defaults to a real sentence, so it is shown at
whatever it holds by the rule that was already there. Adding it to the
`shown` tuple would have been a line with no reader.

**The diff gained a reported half, not just the predicate pair.** The
plan's finding 8 names `_FALLBACK_FIELDS` and `same_fallback`. A
predicate nothing reads is dead code, and the reload-staleness claim the
Tests section asks for (`toggling the filler alone does not touch the
fallback clip, and the reverse`) is only observable to an operator if
the comparison reports it. So `AgentsDiff` grew a fourth sub-section,
`FallbackDiff`, beside `filler`. This cost nothing beyond the model: the
CLI renders diff and apply sections by walking the response models, so
the new section and the three new `FillersReload` lists print without a
line of rendering code.

**`FillersReload` grew three fields rather than gaining a sibling
model.** The plan says "the reload result and its response model grow
the per-kind outcome fields", and `Fillers` names them
`fallback_resynthesized`, `fallback_reused`, `fallback_degraded`. One
model with six lists keeps the two structures that have to agree down to
one; a second model would have been a second shape for a caller to
learn. The three are required, like their siblings, and the two suites
that construct the model by hand were updated.

**`build_agent_fillers` keeps its name.** It builds both kinds now, and
the name says one. Renaming would have moved the baseline driver
identity (`vinga_server.filler:build_agent_fillers #1`), every call site
and every test import, for a word; the module docstring and the
function's own now say what it builds. `Fillers` was already the name
for "one world's clips" rather than "one world's filled pauses", so the
type names did not have to move either.

**No new per-reply state on the runtime.** The plan asks for the state
inventory to be updated "for any new per-reply state". M1 adds none: the
failure arm reads the world's cache through the runner it already holds.
The class docstring says so explicitly, beside the inventory, because
"nothing was added here" is the fact a reader of that list needs. The
reply-wide facts finding 5 asks for belong to M2's empty-reply check.

**The UNTYPED playback line is driven by the `reply_fallback` driver.**
The closed set in `test_event_baseline.py` is asserted in both
directions against what the drivers actually produced, so a row added
without a producer fails the lane. One driver therefore produces both:
its world caches a phrase at a sample rate nothing can resample from, so
the typed record goes out (it is emitted before any audio work, the way
`filler_played` is) and the resample that follows raises into the arm
that writes the untyped line. A `DeviceGone` there would have produced
neither, since that is swallowed by contract. The driver's docstring
says all of this.

**`fallback_degraded` needed a driver of its own, sharing a body.** One
driver names one event, and a voice that refuses degrades both kinds, so
`build_agent_fillers #2` drives the same voiceless world as `#1` and
keeps the other record. The driver count is now 89.

**One existing sentinel test changed shape.** A voice that refuses is
now caught in two arms of one build, so
`test_a_filler_that_will_not_synthesize_refuses_carrying_nothing` sees
two refusals rather than one. Both have to carry nothing, so `reported`
and `carries_nothing` gained a count and the test asserts two. Reading
one of them would have been reading half the evidence.

**The failure-arm log matcher was tightened.** `reply_failure` in
`test_session_reply_failures.py` matched the substring `reply failed`,
which the new event's own sentence ("the reply failed, so this agent's
fallback phrase went out") also contains. It now matches
`": reply failed: "`, the arm's own punctuation.

### The two pins that did not move

Both "ends quietly" characterization cases
(`test_a_reply_ends_quietly_when_the_send_path_raises` and
`test_a_filler_ends_quietly_when_the_send_path_raises`) are untouched,
per finding 3: what they drive is a socket disappearance translated to
`DeviceGone`, which must stay silent whatever the configuration says.
The new behaviors got their own general-exception case beside them,
parameterized over the switch.

`test_one_turn_has_the_control_message_order_the_firmware_expects` is
untouched too, and the failure turn's order is pinned beside it.

Per finding 11, the filler's no-`sentence_start` pin landed green in its
own commit (`f1f47110`) before any of the fallback work, so the
deliberate difference is a diff against a pin.

### Shape notes

`OrderedSocket` moved into `tests/support/sockets.py` rather than being
written twice. It is the module's third reading of a turn and the note
there says why the other two cannot answer these questions:
`RecordingSocket` counts frames rather than placing them, and `spoken`
reads the text messages with the frames already dropped, so neither can
say a sentence was announced before or after a clip.

`config_with_agent` in `tests/support/configs.py` gained an `agent`
argument: the four stages are always named there, so a caller adds the
section it is about rather than restating the world around it.

### Bounds

The per-phrase synthesis deadline is `FALLBACK_SYNTHESIS_TIMEOUT_S` in
`filler.py`, ten seconds, with the reasoning beside it: generous for one
short sentence through a cloud voice on a cold connection, bounded
enough that a hung provider delays a boot by seconds per agent. The
filler's own build stays unbounded, and the constant's note says why
(opt-in and pre-existing; bounding it here would be an unasked-for
behavior change riding along).

### Verification

From `vinga-server/`:

- `uv run ruff check .` clean.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: 5370 passed,
  19 skipped.
- `uv run pytest tests/integration -q`: see the PR's verification list.
- `python3 scripts/check_doc_links.py .` from the repository root: 184
  files, 0 failures.

Generated references were regenerated through their generators:
`config reference`, `config openapi`, `config cli-reference`, `events
reference`, and the command-spellings manifest through
`uv run python -m tests.unit.test_command_spellings`.

### M1 PR review round

PR [#390](https://github.com/rafacm/vinga/pull/390). Backend codex
(codex-cli 0.153.0), model `gpt-5.6-sol`, read-only sandbox,
2026-09-04, against `11be9575`, runtime 9m43s. Five findings, two P1
and three P2, each fixed in a commit of its own.

Two of the five are the same failure seen twice, and it is worth naming:
**the notice was written as if the moment it runs in were quiet.** It is
not. The failure arm runs with a provider exception still active and
with a barge-in able to land at any await inside it, and both findings
are what that costs: an exception the arm took care to reduce to a class
name riding out on a cancellation, and a cancellation swallowed into a
wait so the notice walks on into frames the same barge-in has paused.
Neither is visible from the arm's own code; both are visible from the
state the arm is standing in.

1. **P1: a barge-in while settling the filler is swallowed and can wedge
   the session.** The failure arm waits in `FillerRunner.settle()`
   before it starts the notice, and `settle()` suppressed every
   `CancelledError`, including a cancellation of the reply task itself.
   The confirmation ladder pauses the outgoing frames before it cancels
   and resumes them only after the cancel has been awaited
   (`runtime/turntaking.py`), so a swallowed cancellation lets the arm
   reach a paced send that cannot complete until the cancel it is
   blocking returns (`device/pacing.py`). Fix: tell a cancellation of
   the child clip task apart from one of the current task and re-raise
   the latter, with a test combining a sounding filler, a terminal reply
   failure and a confirmed barge-in.

   *Resolution* (`00eb1416`): accepted in full. The two are told apart
   by counting cancellation requests against the current task rather
   than by asking which task ended up cancelled, since a cancel
   delivered to a task awaiting another one cancels that other one too
   and the clip's own state therefore says nothing about who asked. The
   count is taken before the await, which also keeps a reply that was
   ALREADY cancelled from re-raising and losing the closing `tts stop`
   its `finally` still owes the device. `tail()` got the same treatment,
   beyond what the finding asked: it is the identical wait, and a
   barge-in landing while the reply queues behind a clip's tail was
   being swallowed into the reply the same way. The test drives the real
   ladder with the confirmation held until the reply has failed, which
   is what puts the cancel inside the wait; without the fix it reports
   the phrase going out in the middle of a confirmed barge-in.

2. **P1: mid-fallback cancellation leaks the provider exception through
   `__context__`.** The notice ran inside the `except Exception` arm, so
   the provider's exception was the active one when `speak_fallback()`
   re-raised a `CancelledError`, and Python attached it, message and
   causes included. Fix per the reviewer: record that a notice is owed,
   leave the handler, and only then settle and play, so nothing is
   active when the cancellation propagates; extend the cancellation test
   with sentinel-bearing messages and causes and assert the propagated
   chain carries none of them.

   *Resolution* (`1fdc8cd6`): accepted in full and implemented as
   described. The arm sets `unanswered` and returns; the notice is said
   from the statement after the handler. The body is nested inside a
   second `try` so the outer `finally` still owns the closing `tts
   stop`, which a cancellation raised from the notice must not take
   away. The test walks the whole `__cause__`/`__context__` chain rather
   than its first link, since a wrapped vendor error has both, and fails
   against the previous shape.

3. **P2: `reply_fallback` recorded successful audio before any device
   operation succeeded.** The record was written with `audio` read off
   cache presence alone, before `begin_speaking`, `sentence_started`,
   the encode and the send, so a disconnect, a cancellation or a
   playback failure produced a record claiming a phrase that never went
   out, and the baseline driver enshrined exactly that. Fix: track what
   was actually delivered, write the record from it, emit nothing for a
   pre-delivery `DeviceGone` or cancellation, split the typed-success
   and untyped-playback-failure drivers, and add refusal cases.

   *Resolution* (`97738c54`): accepted in full. Two flags track the
   display send and the audio send as they happen, and the record is
   written in a `finally` from what they answered: nothing at all where
   nothing was delivered, and `audio` saying whether the phrase was
   heard as well as seen where the display send landed. The `finally` is
   what keeps a cancellation arriving after the display send from
   erasing the fact that the user saw it. The baseline gained a second
   driver so one path no longer stands for both, and three refusal cases
   landed beside the existing ones: a device gone before the notice
   (nothing recorded), a device gone between the display and the speaker
   (recorded, `audio` false), and an encode that raises after the
   display (recorded, `audio` false, the failure reported by class).

4. **P2: the documents called a per-start cost a one-time upgrade
   cost.** Every start calls `build_agent_fillers(..., previous=None)`,
   so nothing is reused across processes. Fix the README, the CHANGELOG
   and the governing plan's own wording: every start synthesizes one
   phrase per enabled agent, and unchanged clips are reused only across
   applies within one process.

   *Resolution* (`187e70d7`): accepted in full, all three moved
   together. The wording now says the cost is paid again at every
   restart, redeploy and container replacement, and names where the
   reuse actually lives. The plan's risk section and its finding 12
   resolution are amended in place rather than annotated, with a
   sentence saying this round corrected them.

5. **P2: the reload contract promised a retry the cache prevents.**
   `fallback_degraded`'s description said "The next reload tries again",
   but `_kept_fallback()` reuses an unchanged clip even when it holds no
   audio, so an ordinary apply reports `fallback_reused` and retries
   nothing. Fix the source description, regenerate the references, and
   pin the unchanged degraded outcome with a test.

   *Resolution* (`e0bece17`): accepted in full. The description names
   the two things that do retry one, an edit to its own section and a
   change to the voice that would speak it, and adds that a restart
   retries every one of them since a start keeps nothing from the
   process before it. `_kept_fallback`'s own note says the same from the
   other side and names the difference from the filled pause, whose
   failed synthesis leaves nothing in the mapping and is therefore
   retried by the very next build. Two tests pin the pair, and the API
   document was regenerated through its generator.

The behavior was left alone in finding 5 rather than made to retry, per
the reviewer's own framing: keeping the words without the audio is what
the display half needs, and there is no way to hold a usable display
half and a retriable audio half in one entry without a caller having to
ask which of the two it is looking at.

## M2: the sentence guard

Built as the plan describes, its review round's amendments included.
The deviations below are the places where the plan left a choice open
or where the code disagreed with the plan's reading of it; everything
not listed here is the plan's own shape.

### Deviations and decisions

**The session cases got their own file.** The plan puts them in
`test_session_tools.py`. They are not about the tool loop: they are
about which sentences survive it, what replaces a reply that said
nothing, and which surfaces a withheld sentence may not reach, and the
last of those drags in a store, a socket that keeps order and a planted
secret. `tests/unit/test_session_withheld.py` is the new domain concept
getting its own module rather than a thousandth line in a file about
something else, which is the design guide's own rule. The guard's own
suite is `tests/unit/test_speech_guard.py` where the plan puts it, and
each file's docstring names the other.

**`nothing_sayable` added no emit path, and rides the numbering M1's
review round left behind.** The second reason is the same emit site
asked with the other argument. That site already carries two driver
identities, since finding 3 of the M1 round split the notice's own
paths into one heard and one only shown, so what an identity under one
method names is a path through a site rather than a line of source, and
`nothing_sayable` is the third: `FillerRunner.speak_fallback #3`, a
reply whose whole answer is a leaked call. The other new identity is
`PipelineRuntime._report_withheld #1`, whose driver runs three replies
for the three `sentence_withheld` shapes, exactly as `_run_one #1` runs
three for `tool_call`'s. The count is 92.

**The reason reaches the runner as an argument.** The plan says the
empty-reply check plays "the same fallback (its event carrying the
other reason)" and does not say how. `speak_fallback` now takes a
`FallbackReason`, and a module-level `_fallback_record` selects the
variant from it, filler-style. The alternative, a second method on the
runner, would have duplicated the whole playback recipe to change one
word in a record.

**The unnamed variant carries `source`, and an ambiguous match rides it
as `unknown`.** The plan says an argument-only match resolving to no
single tool rides the unnamed variant and does not say what its
`source` then reads. `unknown`, with the declaration's own `NOTE`
saying why: which namespace it came from is exactly what could not be
decided, which is the same answer `unknown` already gives for a name
nobody publishes. A device tool identified unambiguously still reports
`device` and names nothing, as it does on `tool_call`.

**The report callback takes a character count, not the sentence.** The
plan asks for the guard and its emission behind one helper. What
crosses back is `(tool | None, characters)`, so the emit closure in
`pipeline.py` never captures the withheld text at all. That is the
no-leak claim made structural rather than remembered: there is no
variable holding the bytes at the point where a payload is built.

**Two floors the plan did not state.** An empty object matches nothing,
because every schema trivially contains no keys and `{}` in a sentence
is punctuation; and a tool that declares no `properties` matches
nothing by arguments, because a non-empty key set cannot fall inside an
empty vocabulary. Both are cases in the guard's matrix.

**The reply-wide facts are fields, and the inventory says why.** The
plan calls them "two per-reply facts owned by `_speak_reply` and listed
in the runtime docstring's state inventory". `_reply_spoke` could have
been a local, since `_speak_reply` reads `spoken` at both points it
changes; `_reply_withheld` could not, because the withholding happens a
call away inside `_tool_loop`. One of each would have been two shapes
for one pair, so both are fields, reset per reply by the method that
owns the question, and both are in the inventory with the leg-clearing
reason spelled out.

**`system-overview.md` needed the amendment its conditional allowed
for.** Step 8's heading is "Each sentence is spoken as soon as it
exists", which does claim every sentence is spoken, so the step now
names the one kind that is not.

**`catalog.py`'s `__all__` regained the M1 names.** `REPLY_FALLBACK`
and `ReplyFailedFallback` were never added to it, while every other
declaration and variant is there. The four new names went in, and so
did those two, since a list that is the export surface everywhere else
is a list with a hole in it otherwise. Nothing imports through
`import *`, so this changes no behavior.

### Bounds

The stated bound is in three places and says the same thing in each:
`runtime/speech.py`'s module docstring, which is where an implementer
meets it; the server README's own section, which is where an operator
does; and the changelog entry. A pretty-printed call is cut at its
newlines into fragments no decoder can read, and buffering to close
that would stall live speech on every ordinary `{`. The event is the
residue's visibility.

### Verification

From `vinga-server/`:

- `uv run ruff check .` clean.
- `uv run mypy`: no issues found in 5 source files.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: 5400 passed,
  19 skipped.
- `uv run pytest tests/integration -q`: 239 passed.
- `python3 scripts/check_doc_links.py .` from the repository root: 185
  files, 0 failures.

`docs/reference/events.md` was regenerated through
`uv run vinga-server events reference`, and the command-spellings
manifest through `uv run python -m tests.unit.test_command_spellings`.

### Addendum, 2026-09-04: rebased onto the merged M1

M1 merged after a review round that moved three things M2 sits on, so
this branch was rebased onto it and reconciled in the round's favour.
Three notes, none of which changes the section's story above.

- `speak_fallback` now tracks what actually went out and writes its
  record in a `finally` from that, emitting nothing where nothing was
  delivered. The reason argument rides that shape unchanged: the
  variant is selected from it and `audio` is the delivered-audio
  boolean, so a `nothing_sayable` record says whether the user heard
  the phrase or only saw it, exactly as its sibling does.
- The settle before the notice, which the failure arm has and the
  empty-reply check did not, was added here. It was already owed for
  the arm's own two reasons and this site has a third: a reply that
  spoke nothing never sent a batch, so the tail wait inside
  `_send_reply_audio` was never reached and this is the first thing in
  the turn that waits for the mask at all. It also puts this site under
  the round's finding 1, since the wait now re-raises a cancellation of
  the reply rather than swallowing it.
  `test_the_mask_is_waited_out_before_the_phrase` pins the order.
- The notice is said with nothing active here without arranging
  anything, which is the round's finding 2 seen from a quiet site: this
  is a statement in the ordinary flow of a reply rather than a
  statement inside an `except`, so a cancellation landing in it leaves
  with an empty chain behind it. The docstring says so where a reader
  of the failure arm's nested block would look for it.

### M2 PR review round

PR [#391](https://github.com/rafacm/vinga/pull/391). Backend codex
(codex-cli 0.153.0), model `gpt-5.6-sol`, read-only sandbox,
2026-09-04, against `0cf480a0`, runtime 6m54s. Two findings, one P1 and
one P2, each fixed in a commit of its own.

The P1 is worth naming for what it says about where a bound lives.
**The guard's stated bound was about formatting and the hole was about
punctuation.** The milestone wrote down what it does not catch, a
pretty-printed call chopped at its newlines, and that sentence read as
if a compact call were safe. It was not: the splitter cuts at every
sentence ending followed by whitespace, and a JSON string is exactly
where a model puts a sentence. So the accepted bound covered one shape
and the contract was broken by another that looked like it was inside
the same excuse.

1. **P1: a compact call containing sentence punctuation bypasses the
   guard.** `text.py` cuts at every sentence-ending character followed
   by whitespace, punctuation inside a JSON string included, so
   `{"name":"remember","arguments":{"text":"SECRET. Store it"}}` reaches
   the guard as two fragments. Neither decodes, so both are spoken,
   displayed, kept in the history and stored. That is a single-line
   compact call rather than the accepted multiline bound, and it breaks
   the no-leak contract. Fix: preserve quoted JSON strings across
   sentence-boundary detection, or buffer a candidate compact object
   until it closes, bounded so an ordinary `{` or an unterminated string
   cannot hold live speech indefinitely, with the bound stated beside
   the existing one and an end-to-end sentinel test.

   *Resolution* (`6cd5f30c`): accepted in full, by the second arm with
   the first folded into it. The punctuation rule stands down while a
   brace is open, and braces are counted with a quoting walk so a `}`
   inside a value does not end the span early, which is both arms in one
   rule and the smallest one that hands the guard a whole object.
   Nothing about it knows what a tool is: an open brace is a span worth
   keeping together, which is a fact about text. Three bounds rather
   than the one the finding asks for, because a sentence held there is a
   sentence not yet being synthesized: a newline still cuts whatever is
   open, which is what leaves the pretty-printed call as the fragments
   the guard documents; `flush` releases everything at the end of the
   stream; and `MAX_HELD_FOR_A_BRACE` caps the span, so an unmatched
   brace in prose costs a bounded delay. Every decision stays local to
   the character it is made at, which is what keeps a delta per word and
   the whole string at once giving the same sentences, so every existing
   pin in `test_text.py` is untouched and green, the newline rule and
   that property among them. The sentinel case asserts the same six
   surfaces the whole-sentence case does and reports three spoken
   sentences against the unfixed splitter.

2. **P2: a concurrent tool refresh can misattribute the withheld
   sentence.** The guard matches against the reply-local snapshot and
   then answers a bare name, and `_report_withheld` reclassified that
   name against the live device and MCP registries, which can have
   moved: a board republishes its tools when a discovery finishes, and
   an apply replaces MCP ownership atomically. So a sentence matched
   against the old snapshot could be reported as `unknown` or attributed
   to a new entry. Fix: classify each offered tool when the snapshot is
   taken, pass that sanitized reply-local provenance through the report
   callback, and use `unknown` only for genuinely ambiguous
   argument-only matches, with device-refresh and MCP-reload regression
   cases.

   *Resolution* (`ce11713a`): accepted in full. `_offered_origins`
   classifies the snapshot on the line after it is taken, derived from
   it rather than gathered beside it so the two cannot disagree about
   which tools this reply has, and the frozen `_Origin` it answers holds
   the namespace and the configured entry and nothing a far side wrote.
   `unknown` goes back to meaning the one thing that is genuinely
   unknown. The two regression cases put the change in the one window
   that can cause the misattribution, when the model is asked to stream,
   which is after the snapshot and before the withholding; without the
   fix they report the board tool as `unknown` and the server tool as
   the entry an apply had just put there.

Neither finding moved a decision the plan had taken. The first widened
what the guard actually sees, which the plan wanted all along and had
mis-stated the limit of; the second moved when a question is asked
without changing the answer it may give.
