# Device-facing session boundary: implementation

Companion to
[the plan](2026-08-10-device-facing-session-boundary.md). All three
milestones landed in one pull request, in the plan's ten-commit
sequence, with both lanes green after every commit.

## Boundary, ADR, and the safety net

Commits 1 to 4. The
[normalize-the-hardware-edge ADR](../adr/2026-08-10-normalize-the-hardware-edge.md)
went in first with its three principles-page citations, then the
characterization suite, then `device/boundary.py` and
`device/events.py`.

**No deviations from the plan's design.** Two things are worth
recording anyway.

The characterization suite
(`tests/unit/test_session_characterization.py`) was written against a
negative control rather than against the source: each of the three pins
that a reviewer cannot check by eye was verified to fail when the
behavior it covers is deliberately regressed. Moving the filler
arbitration ahead of the empty-batch return fails
`test_a_chunk_too_short_to_fill_a_frame_leaves_the_filler_armed`;
splitting the filler's batch across an await fails
`test_a_filler_sounding_never_sends_the_replys_packets`; moving the
`speaking_started` stamp after the socket send fails
`test_the_fillers_first_frame_stamps_and_attributes_speaking_started`.
The first two only bite with the right fixture shape: a sentence whose
spoken form is a single sub-frame chunk (the splitter's four-character
floor and its trailing-whitespace cut rule both have to be satisfied,
so "Ok." does not work and "Right. " does), and a filler clip long
enough to pace over several frames, which is what opens the window for
the reply to feed the shared encoder between two of the filler's sends.
A shorter clip passes the split-batch regression, which is why the
phrase in that fixture is half a second long and says so.

Two driving helpers (`drive_reply`, `start_reply`) were added to
`tests/unit/test_session_tools.py` so the characterization file names
the reply entry point in one place. That is the only reason the
characterization suite came through the extraction unmodified: the
helpers moved with the code, the tests that use them did not change.

`RuntimeFactory` landed in commit 3 with its observability argument
typed `Any`, narrowed to `SessionEvents` in commit 4 when the type
existed. The alternative was a `TYPE_CHECKING` import of a module that
did not exist yet, which is green at runtime but dishonest in a commit
meant to stand on its own. Record-keeping scale; the final shape is the
plan's.

## Runtime extraction

Commits 5 to 7. `runtime/speech.py`, then `runtime/pipeline.py`, then
endpointing, the gate ladder and the filler.

**One design question the plan left implicit, resolved here.** The
filler's `barge_in_pending` skip reads "are the outgoing frames paused
right now". Before the split that was `self._pace_paused_at is not
None`, edge state the filler could see because it lived in the same
object. After it, the pause state is the edge's and `DeviceOutput` has
no query for it, deliberately: the plan's method list is exhaustive and
a boundary that grows a getter per runtime question is the trap the ADR
names. The runtime tracks its own intent instead (`_output_paused`, set
by the two wrappers around `pause_output` and `resume_output`), which
is exact rather than approximate: the runtime is the only thing that
ever pauses the stream.

**One behavior-preserving simplification, stated because it looks like
a change.** The gate ladder's confirmed-cancel path used to clear the
pause by hand (`_pace_paused_at = None; _pace_resume.set()`) rather
than resume, on the grounds that the pause belonged to the reply being
cancelled. Under the boundary that would need a third primitive. It is
an ordinary `resume_output()` now: resuming shifts the pacing clock by
the pause, and the reply about to answer calls `restart_pacing()` at
the top of its first agent leg, which sets that clock to None before
any frame moves. The shift is overwritten before it can be observed.

**The activity mark moved inside two boundary calls.** `finish_speaking`
marks conversational activity and `user_turn_ended` marks it too, which
is where `_mark_activity` used to be called directly at those two
sites. `finish_speaking` marks before it sends anything, so a device
that has already gone away still resets the idle clock on its way out,
which is what the unconditional call before the suppressed send used to
guarantee. (The `user_turn_ended` half of this was claimed here before
it was true; see the review round below.)

**The VAD capture sample records `listening` as true without asking.**
It is fed from the runtime's `audio()`, which the edge only calls after
the not-listening guard, so the flag was already invariably true at
that call site. The alternative was passing edge state across the
boundary for a capture track.

Everything else in these commits is a verbatim move. Test edits are
construction paths, patch targets, attribute paths and imports; no
assertion value changed anywhere.

## Narrowing, contracts, and docs

Commits 8 to 10.

**One resolved detail the plan did not name.** The edge resolved a
device's agents as `agents_for_device(mac)` filtered by membership in
the built `agent_providers` dict, which the edge no longer has. It
filters on `config.agents` instead. The two are the same set by
construction: `build_agent_providers` builds exactly one entry per
`config.agents` key, and a bad provider configuration fails the boot
rather than producing a partial dict. Which agents exist is
configuration, which is what the edge is allowed to read.

**`DeviceSession.runtime` is `SessionInput | None`.** The plan's
construction point (after the agent list resolves, before the hello)
means a connection rejected for a bad MAC or no agent never has one, so
every edge job that asks the runtime a question guards for it. The
`_replying()` helper answers False for a connection with no runtime,
which is what the idle watchdog and the barge-in-off frame guard want.

**Acceptance checks.** `samtal_server/runtime/` imports no starlette
and nothing from `protocol/`; `samtal_server/device/` imports no
provider LLM, ASR or TTS type (only `ToolDef`, which is a tool
definition and part of the boundary). The whole integration lane passes
with a single import line changed, which is the stock-protocol
compatibility floor demonstrated in CI rather than asserted. The
characterization suite (commit 2) is unmodified from the commit that
introduced it, and so is the contract suite (commit 9) apart from the
two tests the review round below added to it.

**Sizes.** `session.py` was 2,138 lines in one class. It is now
`device/session.py` at 883, `device/boundary.py` at 231,
`device/events.py` at 111, `runtime/pipeline.py` at 1,435, and
`runtime/speech.py` at 144.

## Codex review round

One external review of PR #87 (gpt-5.6-terra, against the diff to
`main`), two P2 findings, both on `device/session.py`. Each was first
checked against pre-refactor `main` to decide whether it was a
regression this branch introduced or a pre-existing behavior the new
contract exposes, because the two call for different answers and the
pure-refactor claim should not be blurred either way.

### Finding 1: the end of a user turn stopped marking activity

**A regression, and parity is restored.** Pre-refactor,
`_finish_utterance` called `_mark_activity()` as its first statement,
unconditionally, ahead of every gate, with a comment saying so: an
utterance that ended is somebody talking, whether or not it earns a
reply. Commit 8 deleted that line in the belief that the mark had moved
into `user_turn_ended`. It had not; it had only been added to
`finish_speaking`. So a realtime turn that produced no reply (the
min-speech gate dropping it, an empty confirmation transcript) left
`_last_activity` at whatever the previous reply set, and the idle
watchdog could hang up on a conversation the user was still having.

The mark now lives in `user_turn_ended`, which the runtime calls at the
same point the deleted line sat at, three non-awaiting statements later
and still ahead of every gate, so the restored behavior is exactly the
old one. The review's own argument for that site holds independently
and is worth keeping: the idle timeout is the appliance's policy, so
putting the mark at the boundary method means every runtime inherits it
by reporting the turn rather than by remembering to ask. The
explanatory comment followed the line to the edge.

Pinned by `test_the_end_of_a_user_turn_counts_as_activity`, which
covers both listening modes because the mark is unconditional where the
listening policy is not, and which fails on the code as the review
found it.

### Finding 2: a device tool call could leak the transport's exception

**A pre-existing behavior the new contract exposes, so fixing it is a
deliberate change, stated here.** `_send_mcp` was byte-identical to
`main`: it wrote to the socket directly, so a device that vanished
while a `tools/call` was going out raised starlette's
`WebSocketDisconnect` out of `DeviceToolClient.call`. That was true
before this branch and is not a regression. What is new is the promise
in `boundary.py` that a `DeviceOutput` method reaching the socket
reports a vanished device as `DeviceGone`, and `call_device_tool` was
the one method not keeping it.

`_send_mcp` now goes through `_send_text` like every other outgoing
message. **Old behavior:** a disconnect mid-tool-call raised
`WebSocketDisconnect`. **New behavior:** it raises `DeviceGone`. For
the bespoke runtime the observable outcome is unchanged, which is why
this was the fix chosen: `DeviceGone` subclasses `RuntimeError`, so the
tool loop's broad `except Exception` still catches it, still produces
an error result, and still emits the same `tool_call` event with the
same `is_error`. The only difference is the text of the result handed
to the model, from an empty exception string to "the device
disconnected", in a session whose device has already left. Discovery's
control flow is likewise unchanged, since it already swallows every
exception and reports "no device tools"; its warning now names the
disconnect instead of interpolating an empty string.

The edge's own handshake sends are deliberately left raw. The server
hello is written before the region that catches a disconnect, so
translating it would change how that failure leaves `run`, and it is
not a boundary method.

Pinned by `test_a_device_that_vanishes_mid_tool_call_reports_device_gone`,
which runs a real MCP discovery handshake over a scripted socket, then
makes the device vanish and calls the tool.

## Open questions from the plan

Both stand as the plan left them, and neither was forced by the work.

- Whether the runtime factory should become configuration: still
  deferred. `bespoke_runtime_factory` is one function, not a registry,
  and commit 9's stub runtime shows what plugging a second one in takes
  (assign `app.state.runtime_factory`). What selection needs to express
  is decided when there is a second runtime.
- Where `filler.py` belongs: unchanged and untouched. It is boot-time
  clip building, run once at startup, and moving it would have bought
  nothing. The runtime receives the clips dict it already received.

## Verification

- `uv run ruff check .` clean.
- `uv run pytest tests/unit -q`: 738 passed, 2 skipped.
- `uv run pytest tests/integration -q`: 27 passed.
- Both lanes run after every commit in the sequence; ruff and both
  lanes after commits 2, 4, 7, 8, 9 and 10, and after each of the two
  review-round commits.
- Both review-round fixes were checked against a deliberate regression:
  reverting either one fails exactly the test that covers it.
- The moves were checked as moves. `git diff --color-moved` sees very
  little here, because a moved body also changes receiver
  (`self._encoder` becomes `self._session.encode_audio`, `self.config`
  becomes `self._config`), and the default detector wants exact lines.
  Normalizing those renames and comparing removed against added lines
  gives the honest picture: commit 6 is 806 of 955 added lines
  identical to removed ones, commit 7 is 419 of 572, and commit 8 drops
  to 37, which is where the plan says the logic edits are.
- Board: nothing required, and nothing run. Wire bytes and message
  order are unchanged and the integration lane exercises the real
  protocol against the stock hello.
