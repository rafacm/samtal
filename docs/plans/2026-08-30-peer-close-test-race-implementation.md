# The peer-close test stops racing the client's own close: implementation

The companion to
[`2026-08-30-peer-close-test-race.md`](2026-08-30-peer-close-test-race.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations says
so explicitly.

## M1: the hold

PR #355.

### What landed

Test-only, in `tests/unit/test_simulator_conversation.py`, plus the
changelog entry and this section. No module changed and no new seam was
added: the case rides the `connect` seam the `opened` fixture already
documents.

- **The scripted peer waits before it closes.** After
  `connection.send(tts_message(SESSION, "stop"))` it waits on a
  `threading.Event` the case owns, bounded at ten seconds, and appends
  the wait's outcome to a list the case reads. Only then does it call
  `connection.close(code=4001, reason=CLOSE_REASON)`.
- **This side's close sets that event and then waits for the peer's
  frame.** The case monkeypatches `conversation.connect` with a wrapper
  that replaces the one socket's `close` with a function that sets the
  event (entering it IS this side reaching its close), polls
  `socket.close_code` until it is not `None` under the same ten-second
  bound, records an expiry marker in a list if the bound runs out, and
  then delegates to the real close unconditionally. Cleanup never rides
  on the assertion.
- **Both bounds are asserted, before the verdict they decide.** The
  case waits for the peer's script to finish (the longer bound, so what
  it reports is the peer's outcome rather than a second race with it),
  then asserts the peer's wait was satisfied and the expiry list is
  empty. A runner that outlives either bound therefore says so by name
  instead of handing the verdict back to the race.
- **The existing assertions are unchanged**: `reply.closed ==
  conversation.UNKNOWN_CLOSE`, `"4001" not in reply.closed`, and
  `CLOSE_REASON` absent from stdout, stderr, the rendered log records
  and `reply.closed`.
- **The docstring says why the coordination is there**: the two closes
  race, this case's subject is the interleaving where the peer's close
  is the one this side read, so the peer holds its close until this side
  is standing at its own.

### Deviations from the plan

Two, both small, both in the plan's own direction rather than away from
it.

1. **The ten-second bound is a local name used three times rather than
   three literals.** `bound = 10.0` is read by the peer's wait, by the
   wrapper's deadline and, doubled, by the wait for the script to
   finish. The plan names the number once per waiter; writing it once
   is the same fact with nothing to drift.

2. **The case waits for the peer's script to finish before reading its
   outcome.** The plan asserts the peer's wait outcome and the expiry
   list; it does not say when the outcome becomes readable. It does not
   on its own: `peer()` joins the serving thread, not the handler, so
   the first draft asserted an empty `peer_waited` and reported
   `assert [] == [True]`, which names the wrong thing. The case now
   opens with `assert recorded.finished.wait(timeout=bound * 2)`, the
   file's own idiom for "the peer is done", used by the happy-path case
   at the top of the file, at twice the bound so it outlives the waits
   it reports on rather than expiring alongside them.

### The bite demonstration

Run twice, from `vinga-server/`, against the single case. Neither state
is committed.

**The hold reverted** (the `monkeypatch.setattr(conversation, "connect",
holding)` line replaced by `_ = holding`, so nothing sets the event).
Deterministic, and it names the synchronization failure:

```
        assert recorded.finished.wait(timeout=bound * 2)
>       assert peer_waited == [True], "the peer never saw this side reach its own close"
E       AssertionError: the peer never saw this side reach its own close
E       assert [False] == [True]
E
E         At index 0 diff: False != True
E         Use -v to get more diff

tests/unit/test_simulator_conversation.py:798: AssertionError
=========================== short test summary info ============================
FAILED tests/unit/test_simulator_conversation.py::test_a_peer_close_reason_is_read_and_never_relayed
1 failed in 10.95s
```

**The hold reverted and the three synchronization assertions removed
too**, which is the state the case was in before this milestone plus the
peer's unset event, and which shows the two CI sightings' exact diff:

```
>       assert reply.closed == conversation.UNKNOWN_CLOSE
E       AssertionError: assert 'the session ended normally' == 'the connecti...does not know'
E
E         - the connection closed with a code this client does not know
E         + the session ended normally

tests/unit/test_simulator_conversation.py:797: AssertionError
=========================== short test summary info ============================
FAILED tests/unit/test_simulator_conversation.py::test_a_peer_close_reason_is_read_and_never_relayed
1 failed in 0.89s
```

That second form is what runs 33044449522 and 33140677096 reported, now
produced on demand rather than waited for. The first is what the
committed case answers with instead, which is the point of asserting the
bounds: the failure names the synchronization rather than the verdict.

### Discoveries

- **The peer's own `Recorded.finished` is what makes a script's outcome
  readable.** `peer()` shuts the server down and joins the serving
  thread on exit, and the handler thread is a different one, so a list
  a script appends to may still be empty when the `with` block ends.
  Any case asserting something a script recorded at its end has to wait
  on `finished` first.
- **The verdict half of the race is genuinely instant.** With the hold
  in place the case runs in under a second; with it reverted the peer
  sits out its whole bound, which is why the bite takes eleven.

### Verification

All from `vinga-server/`, against a Postgres started from the
repository's own compose file with the committed defaults.

- `uv run pytest tests/unit/test_simulator_conversation.py -q`: 29
  passed.
- The single case ten times in a row: ten green runs, about 0.85s each.
- `uv run pytest tests/unit -q`: 4572 passed, 19 skipped.
- `uv run pytest tests/unit -q -n auto --dist loadfile` (the CI shape):
  4572 passed, 19 skipped.
- `uv run pytest tests/integration -q`: 218 passed.
- `uv run ruff check .`: clean.
- `python3 scripts/check_doc_links.py .` from the repository root, for
  this document's link back to the plan.
- Not verified here: nothing on hardware, and nothing in the image lane,
  which is CI's. M1 changes one unit test and adds no board or device
  procedure.
