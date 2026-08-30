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
alone rejects it: the shape this test exists for is a completed
turn whose close code was the peer's.

The test therefore holds the client's own close until the peer's
close frame has been read, exactly the issue's second shape, and
the two sides are coordinated by a `threading.Event` rather than by
adjacency: the scripted peer sends `tts stop` and then waits on the
event (ten-second bound, outcome recorded) before closing with
4001, and the client-side wrapper sets the event on entry. The
disputed interleaving (client at its close, peer's 4001 still
unsent) is thereby forced on every run, not merely survived. The
seam is the module's own: the case monkeypatches
`conversation.connect` (the pattern the `opened` fixture documents:
replacing the module's name replaces the seam and not the library)
with a wrapper that, on the one socket this turn opens, replaces
`socket.close` with a function that waits until `socket.close_code`
is not `None` (bounded at ten seconds, the bound
`test_a_peer_that_goes_away_mid_utterance_is_a_sentence_not_a_traceback`
already uses when it polls the same attribute; that case is
precedent for the wait, not coverage of this path, since it closes
1011 after the first outgoing audio frame to test `_send_audio`)
before delegating to the real close.
The bound expiring is a synchronization failure with its own name,
not a quiet return to the race: the wrapper records the expiry in a
list the case holds, the case asserts that list empty after the
turn, and the socket is still closed for cleanup whatever the list
says. `_close` treats any normally returning wrapper as a completed
close, so without that assertion a pathological runner would pass
or fail by scheduling again, with the plan's own bound as the new
racing party. With the peer's close processed first, this side's
close is the echo's no-op, `socket.close_code` is 4001 on every
interleaving, and `_close_name` answers `UNKNOWN_CLOSE` by
construction.

The hold is honest about what the test claims: the case's subject is
"when the peer's close is the one this side read, its code is looked
up in the closed set and its reason is never relayed", so making the
peer's close the one that is read is the test finally saying what it
always meant. The normal-close verdict itself stays pinned by
`test_one_turn_reaches_the_end_of_the_reply` against
`CLOSE_NAMES[1000]`; that case cannot say which side initiated its
1000 (the support closes the connection when a script returns, and
both sides close normally), and this plan claims nothing more from
it than the verdict.

Two facts verified against the installed library (websockets 16.1.1)
rather than assumed: `Connection` defines no `__slots__`, so an
instance attribute may shadow `close`; and `close_code` becomes
non-`None` when the peer's close frame is processed, without this
side calling `close` at all, which is the same property the
mid-utterance case's `wait_for_the_close` already relies on.

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
- Bookkeeping outside the tree, done by the driving session, split
  by urgency. Recording the second sighting is an M1 completion
  criterion done before or with the PR, not at merge: the #254
  watch table gains run 33140677096's row with the diagnosis (a
  race in the test itself, demonstrated deterministically, and
  therefore not evidence against the parallel lane, since enough
  contention would lose the race serially too), so the watch's
  record is true even if the PR stalls. Only #328's closing
  comment, linking the PR, the demonstration and the second
  sighting, stays merge-time.

## Tests

The changed test is the deliverable; what verifies it:

- The committed test carries its own bite through the event: with
  the hold reverted (the `connect` monkeypatch removed), nothing
  sets the event, the peer sits out its bounded wait while the
  client completes a normal close, and the case fails
  deterministically with both sightings' exact assertion diff,
  rather than reverting to a mostly green flake. The bite is
  demonstrated once during implementation and recorded in the
  implementation doc.
- The peer's wait outcome and the wrapper's expiry are both
  asserted, so either side outliving its bound is a named
  synchronization failure.
- The original diagnosis stays recorded as spike evidence (a 0.3 s
  delay planted between `tts stop` and the close reproduces both
  sightings on a warm machine); it is not committed, because a
  pinned sleep is the shape this plan removes and the event now
  owns the interleaving.
- The fixed test, 10 consecutive single runs and the whole file,
  green; the full unit lane serial and under
  `-n auto --dist loadfile` (the CI shape), green.
- `uv run ruff check .` and the integration lane, unchanged and
  green.

## Risks

- The ten-second bound could expire on a pathological runner. The
  close still proceeds (cleanup is unconditional), but the expiry is
  asserted absent, so the failure mode is a named synchronization
  failure rather than either a hang or the old race deciding the
  verdict; the bound matches the file's existing precedent.
- Patching an instance method on a library object couples the case to
  `Connection` lacking `__slots__`; websockets is pinned by `uv.lock`,
  and the case fails loudly (an `AttributeError` at patch time), not
  silently, if an upgrade changes that.

## Milestones

- [ ] M1: the hold. The test change, the changelog entry, the
  implementation doc section, and the #254 watch-table row for run
  33140677096 posted before or with the PR; PR TBD.

## Plan review round

External review of commit 9111ba12: backend codex (codex-cli
0.151.0), model gpt-5.6-sol, sandbox read-only, 2026-08-30, runtime
about 5 minutes. Verdict: ready after the P1/P2 amendments. Findings
condensed but faithful; resolutions appended per amendment.

1. **P2: the timeout deliberately restores the original race.**
   After the ten-second bound the wrapper delegates to the real
   close and the old race decides the verdict, and `_close` treats
   any normally returning wrapper as a successful close, so a
   timeout can still pass or fail by scheduling, contradicting
   by-construction determinism. The wait's expiry must be an
   explicit synchronization failure even when the close code
   happens to be 4001.

   *Resolution*: adopted. The wrapper records an expiry in a list
   the case asserts empty after the turn; the close itself remains
   unconditional so cleanup never depends on the assertion.

2. **P2: the committed test has no deterministic bite.** The only
   deterministic pre-fix failure uses a transient `time.sleep(0.3)`
   the plan refuses to commit; the committed peer keeps the
   back-to-back `tts stop` and 4001 close, under which the unfixed
   test usually passes, so deleting the hold later would leave a
   mostly green test and restore the flake. Commit sleep-free
   coordination that forces the disputed interleaving: the peer
   waits on an event after `tts stop`; the close wrapper sets the
   event, waits for `close_code`, then delegates; both bounded
   waits expose asserted timeout markers.

   *Resolution*: adopted. The peer now waits on an event the close
   wrapper sets before sending its 4001, both bounded waits are
   asserted, and the bite (revert the monkeypatch, the test fails
   deterministically) is demonstrated during implementation and
   recorded in the implementation doc.

3. **P2: the second sighting is left outside every milestone.** The
   plan defers the #254 watch-table update to merge time, so if the
   PR stalls the watch remains knowingly false. Recording run
   33140677096 with its diagnosis must be an M1 completion
   criterion, done before or with the PR; only #328's closing
   comment stays merge-time bookkeeping.

   *Resolution*: adopted. The documentation footprint and the M1
   milestone now name the watch-table row as an M1 completion
   criterion posted before or with the PR.

4. **P3: the cited 1011 test neither exists under that name nor
   owns the mid-reply path.** The real case is
   `test_a_peer_that_goes_away_mid_utterance_is_a_sentence_not_a_traceback`,
   which closes after the first outgoing audio frame and tests
   `_send_audio`, not a close during `_read_until_reply_ends`.
   Reject the mid-reply alternative solely because it raises before
   a `Reply` exists, and cite the 1011 case only as precedent for
   polling `close_code`.

   *Resolution*: adopted. The rejection now rests on the missing
   `Reply` alone, and the mid-utterance case is cited by its real
   name as wait precedent, with what it actually tests stated.

5. **P3: the ordinary-turn test does not pin which side initiated
   the 1000 close.** The scripted peer's handler returns after
   `tts stop` and the support closes the connection when a handler
   returns; both sides use 1000, so the assertion cannot identify
   the initiator. Describe that test as pinning the normal-close
   verdict only.

   *Resolution*: adopted. The plan now claims only the verdict from
   the ordinary turn and says why the initiator is unknowable there.
