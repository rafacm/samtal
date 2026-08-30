# The peer-close test stops racing the client's own close

Plan for [#328](https://github.com/rafacm/vinga/issues/328).
Implementation notes land in the companion
`2026-08-30-peer-close-test-race-implementation.md`, one section per
milestone, appended in the change that ticks the milestone here.

## Goal

`tests/unit/test_simulator_conversation.py::test_a_peer_close_reason_is_read_and_never_relayed`
asserts which of two racing closes wins, and under runner-grade
contention it sometimes loses: the client is correct either way, so
the fix is in the test, which must make the peer's close the winner by
construction rather than by timing.

## The issue's decisions, restated

- The graded response: one sighting is recorded, a second fires the
  bounded fix. The second sighting exists: run 33140677096
  (2026-08-28 04:03 UTC, `feature/conversations-m3` at `b87e96c4`, a
  branch that does not touch the simulator) failed with the identical
  assertion, `'the session ended normally' == 'the connecti...does
  not know'`. The first is run 33044449522 (2026-08-27, recorded on
  the issue and in the #254 watch table); the second was never
  recorded anywhere, which this plan's bookkeeping repairs.
- The fix is in the test, not the client. The client's behavior is
  correct on both sides of the race: when the peer's 4001 close is
  processed first the verdict is `UNKNOWN_CLOSE`, and when the
  client's own normal close completes first the honest verdict is
  "the session ended normally".
- The issue names two candidate shapes: have the scripted peer close
  mid-reply (before `tts stop`), or hold the client until the peer's
  close frame is read. Choosing between them is this plan's one open
  question.

## The mechanism, now demonstrated rather than suspected

The issue read the race from the code; this plan pinned it. Inserting
`time.sleep(0.3)` in the scripted peer between `tts stop` and
`connection.close(code=4001, ...)` reproduces the CI failure
deterministically on a warm machine, with the exact assertion diff
both sightings show. The interleaving: the client records `tts stop`,
reaches `REPLY_COMPLETE`, leaves `_turn`, and `_close` sends this
side's normal close; the peer's connection echoes it (websockets
answers a close frame as soon as it is processed), so the script's
own `close(code=4001)` arrives after the closing handshake is already
complete and is a no-op, and `socket.close_code` is 1000. On a quiet
machine the peer's two statements run back to back and 4001 is in
flight before the client reaches `_close`; under contention the peer
thread can be descheduled between them, which is why 25 serial and 8
xdist local runs stayed green while the two-core runner failed twice
in two days.

## Open question, resolved: hold the client

The mid-reply-close shape is rejected because it changes what the
test is about. A peer that closes before `tts stop` ends the turn
inside `_read_until_reply_ends`, so `_received` raises
`ConfigError(cannot_speak("ConnectionClosedError"))` and `converse`
never builds a `Reply`: the assertions this test exists for
(`reply.closed == UNKNOWN_CLOSE`, the code's number absent, the
reason absent from every surface) have nothing to attach to. That
shape is also already owned by
`test_a_disconnect_mid_utterance_is_contained_by_the_send_boundary`,
which closes 1011 mid-utterance and asserts the `ConfigError` path.

The test therefore holds the client's own close until the peer's
close frame has been read, exactly the issue's second shape. The
seam is the module's own: the case monkeypatches
`conversation.connect` (the pattern the `opened` fixture documents:
replacing the module's name replaces the seam and not the library)
with a wrapper that, on the one socket this turn opens, replaces
`socket.close` with a function that waits until `socket.close_code`
is not `None` (bounded at ten seconds, the bound the 1011 case
already uses for the same wait) before delegating to the real close.
With the peer's close processed first, this side's close is the
echo's no-op, `socket.close_code` is 4001 on every interleaving, and
`_close_name` answers `UNKNOWN_CLOSE` by construction.

The hold is honest about what the test claims: the case's subject is
"when the peer's close is the one this side read, its code is looked
up in the closed set and its reason is never relayed", so making the
peer's close the one that is read is the test finally saying what it
always meant. The other race outcome (this side's close completes
first and the verdict is the normal-close sentence) is the ordinary
turn's, already pinned by `test_one_turn_reaches_the_end_of_the_reply`
against `CLOSE_NAMES[1000]`.

Two facts verified against the installed library (websockets 16.1.1)
rather than assumed: `Connection` defines no `__slots__`, so an
instance attribute may shadow `close`; and `close_code` becomes
non-`None` when the peer's close frame is processed, without this
side calling `close` at all, which is the same property the 1011
case's `wait_for_the_close` already relies on.

## Design footprint

Test-only. No module changes, no new seams; the case rides the
`connect` seam the fixture layer already documents. The deletion test
admits no new helper: the hold is one nested function in the one case
that needs it, and a second user would be the moment to lift it
beside `opened` in the fixtures, not before.

## Documentation footprint

- `CHANGELOG.md`: one dated `### Fixed` entry saying the peer-close
  simulator test no longer races the client's own normal close under
  CI contention.
- No hand-maintained page describes this test's timing, so nothing
  else is falsified. The command-spellings census is untouched (no
  documentation moves, no command spellings).
- Bookkeeping outside the tree, done by the driving session at merge
  time: the #254 watch table gains the second sighting's row with the
  diagnosis (a race in the test itself, demonstrated deterministically,
  and therefore not evidence against the parallel lane, since enough
  contention would lose the race serially too); #328 gets a closing
  comment linking the PR, the demonstration, and the second sighting.

## Tests

The changed test is the deliverable; what verifies it:

- The deterministic reproduction: with the 0.3 s delay planted in the
  peer's script, the unfixed test fails with both sightings' exact
  assertion, and the fixed test passes, because the hold outwaits the
  delay. The planted delay is spike evidence recorded here and in the
  implementation doc, not a committed second test: committing it
  would pin a sleep, which is the shape this plan removes.
- The fixed test, 10 consecutive single runs and the whole file,
  green; the full unit lane serial and under
  `-n auto --dist loadfile` (the CI shape), green.
- `uv run ruff check .` and the integration lane, unchanged and
  green.

## Risks

- The ten-second bound could expire on a pathological runner, in
  which case the close proceeds and the old race decides the verdict:
  the failure mode is the status quo's flake, not a hang, and the
  bound matches the file's existing precedent.
- Patching an instance method on a library object couples the case to
  `Connection` lacking `__slots__`; websockets is pinned by `uv.lock`,
  and the case fails loudly (an `AttributeError` at patch time), not
  silently, if an upgrade changes that.

## Milestones

- [ ] M1: the hold. The test change, the changelog entry, the
  implementation doc section; PR TBD.
