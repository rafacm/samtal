# vinga events tail: implementation

The companion to [`2026-08-29-events-tail.md`](2026-08-29-events-tail.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations
says so explicitly.

## M1: the hub and the stream

PR #349.

### What landed

In the order the commits tell it: the hub, then the wiring to both
channels, then the shutdown, then the route, then the documents.

- **`events/live.py`,** the subscriber hub, strict-typed under the
  package's own mypy scope. `LiveEvents.emit` is the tap contract;
  `subscribe(filters)` answers a `Subscription` with a `deque(maxlen=256)`
  of its own; `unsubscribe` gives one back; `subscribers` is the count
  a diagnostic may want; `close()` ends every subscription at once. A
  reader takes items through `await subscription.next(timeout)` or by
  iterating the subscription, and what it receives is a `Streamed`
  (the payload plus `level` and `ts`) or a `Dropped` count.
- **The three filters,** as `Filters(device, session, level)` applied at
  enqueue against the payload's own identity fields, with the level as
  a threshold defaulting to `INFO`. An event carrying no device or no
  session key passes such a filter only when it is unset.
- **Both channels.** `app.py` builds the hub first in
  `_build_composition`, attaches it to the process-global server tap
  and registers `detach_server_tap` on the same exit stack in the next
  statement. `Composition` carries it, `ws.py` hands it to
  `DeviceSession`, and the session attaches it immediately after
  `SessionEvents` is constructed and detaches it in an outer `finally`
  over the whole of `run()`. `DeviceSession.detach_live()` is public
  because `ws.py`'s capacity rejection returns without ever running.
- **The shutdown,** in `serving.py`, with the two paths the plan
  names: after `sessions.drain()` and before uvicorn's shutdown on a
  positive `drain_s`, and immediately before the direct uvicorn
  shutdown with `sessions.drain()` uncalled when `drain_s <= 0`.
- **`GET /api/runtime/events`,** an SSE streaming response in
  `config/api.py`'s `_runtime`: `text/event-stream`, `Cache-Control:
  no-store`, the three query filters, a keepalive comment on an
  injected interval (15 s by default), the dropped count as its own
  `dropped` SSE event, 401 through the existing gate and 503 for an
  application with no server around it. The operation is in the
  contract test's `EXCLUDED` set with its reason and in the OpenAPI
  suite's literal path map; `docs/reference/api-openapi.json` is
  regenerated through its generator.
- **The documents.** `events.md` gains the live-surface note through
  `events_docgen.py`; `observability-surfaces.md` records the stream on
  the events row and turns the "live and history are two transports"
  invariant from a future into a landing; `CHANGELOG.md` carries the
  dated Added entry.

### Deviations from the plan

Three, none of them a change of behavior the plan states.

- **Where the query filters are parsed.** The plan describes the filter
  contract under the hub. The parsing landed in `config/api.py` beside
  the route, exactly where `conversations/api.py` parses its own device
  filter and for the same reason: what a filter is spelled as, and what
  a caller is told when it is not, is transport, and `events/live.py`
  is a leaf that must not learn what a query string or a 422 is. What
  crosses the seam is the hub's own `Filters` type. The consequence is
  that `normalize_mac` stays the single home of the MAC rule, so the
  spellings accepted are the ones this project accepts everywhere else
  (colon or dash, any case) rather than a wider set written a second
  time here; a bare twelve-hex MAC is refused, and refusing it is what
  keeps the rule in one place. `LEVELS` and `DEFAULT_LEVEL` stay in
  `live.py` and are derived from the catalog's own level set, because
  the hub is what stamps a level name on every streamed object.
- **`run()` is a wrapper.** The outer `finally` the plan asks for is a
  three-line `run()` around `_converse()`, rather than a second
  indentation level over two hundred lines of an existing method. The
  lifetime is identical and the diff is readable.
- **The status code is stated on the route.** `status_code=200` is on
  the decorator because FastAPI's document generator otherwise reads
  the default off the response class's `__init__` signature, and a
  streaming response takes its content and nothing else, which fails
  while rendering the document rather than at runtime.

### Discoveries

- **The hub's lock has to be reentrant.** `close()` runs from a signal
  handler on the shutdown path, and a signal handler runs on the main
  thread between bytecodes, which may be a thread already inside
  `emit` holding the lock. A plain `threading.Lock` deadlocks the
  shutdown there. The lock is an `RLock` and `emit` walks a list it
  took under it, so a reentrant close that empties the subscription set
  mid-walk cannot corrupt the walk.
- **The keepalive is a timeout on the wait, not a timer beside it.**
  `Subscription.next(timeout)` answers `None` both when the timeout
  elapsed and when the subscription is over, and `ended` tells the two
  apart. That is what let the route keep one loop with no second task
  to cancel on a disconnect.
- **A streamed 200 is unobservable through the sync test client.** Its
  transport buffers the whole body, and this body has no end, so both
  the headers and the incremental chunks are asserted from a direct
  ASGI send/receive harness. The refusals stay on the buffered client,
  where a buffered answer is exactly what they are.
- **A partial startup cannot be asked for its hub.** A build that
  refuses installs no composition, so the failed-startup test spies on
  `LiveEvents` construction to reach the hub the build had already
  attached, the same shape `opened_bindings` uses in that file.

### Verification

- `uv run ruff check .`: clean.
- `uv run mypy` (the events package's strict scope, as CI runs it): no
  issues.
- `uv run pytest tests/unit -q`: green.
- `uv run pytest tests/integration -q`: green, against the compose
  Postgres already listening on 127.0.0.1:5432.
- No device checkpoint: nothing in this milestone changes what a board
  sends or is sent. The live lane against a real server is M2's, where
  the plan puts it.

### PR review round

One external review of the branch as pushed to PR #349, plus one CI
failure the lane found on its own. Three findings, condensed below as
received, each carrying its resolution and the commit that landed it.
The verdict was mergeable after the fixes.

Two of the three are the same shape, and it is worth naming: a
lifecycle whose cleanup runs on the ordinary path and not on the
abrupt one. The stream gives its subscription back when a reader
leaves, and did not when the reader left before the response had
begun; the shutdown ends the tails when it drains, and did not when an
operator forced it past the drain. Both were found by asking what runs
when the usual path is skipped, which is the question this milestone's
own tests were not asking.

1. **P2: an immediate disconnect leaked a subscription permanently.**
   The handler subscribed and then built the response, while the
   cleanup lived in the streaming generator's `finally`. Starlette
   runs the body beside a disconnect listener and cancels the one when
   the other returns, so a client that goes away while
   `http.response.start` is in flight kills the response before its
   body is ever iterated, and a generator that never started runs no
   `finally` when it is closed. The queue stayed on the hub for the
   life of the process, on the hot path, read by nobody.
   *Resolution* (`43197355`): the subscription is taken inside the
   body, one statement above the `finally` that gives it back, so
   "subscribed" and "will be cleaned up" are the same moment and a
   request that never gets that far never subscribed. The filters stay
   the handler's, read before anything opens, because a filter that
   cannot be read is a refusal with a status rather than a stream that
   opens and dies. The harness case holds the response start while the
   disconnect lands; it fails on the old shape with one subscriber
   left behind.
2. **P2: a second shutdown signal bypassed the hub close.** The
   forced-exit branch called uvicorn directly with every stream still
   open, and the drain it interrupted was what would have closed them.
   *Resolution* (`8fa68baf`): the two paths that reach uvicorn
   directly, a second signal and a server configured not to drain, are
   one branch again with the close in front of it; neither drains a
   conversation, which is what `drain_s <= 0` has always meant, and
   the close is idempotent so the ordinary path closing them again
   costs nothing. The second-signal test holds an open subscription
   and reads the subscriber count at the moment uvicorn is invoked; on
   the old shape it reads one.
3. **P3: the published contract overstated what the stream carries.**
   It said the JSON object the retained log carries plus `ts` and
   `level`; the retained object also carries the channel and the
   rendered sentence, while the stream serializes the catalogued event
   fields and the two it owns.
   *Resolution* (`323338c6`): the route docstring, which is the
   operation's description, the document's own prose and the changelog
   entry say the catalogued fields plus stream-owned `ts` and `level`,
   and say what the log has beside them rather than implying it is
   there. The observability map's row said the identical thing and was
   corrected with them, deliberately beyond the finding's list: one
   surviving copy of a sentence just found wrong is how it comes back.
   The OpenAPI document and the command-spellings census are
   regenerated through their generators in the same commit.

Beside them, one CI failure with no finding attached
(`cb5aff4e`). `test_the_level_defaults_to_info_and_reads_in_any_case`
failed in the distributed unit lane alone, with `assert [] ==
['loud-one']`. An empty parse is what a keepalive yields: it is a
comment with no `data:` line, and the test read it as the event it was
waiting for. The injected interval was 0.05 s for every test in the
file, and that test opens a second application and drives a second
request between subscribing and reading, which a contended runner
takes longer than the deadline to do. The hub was cleared first: an
emission either lands before a reader clears its wakeup, under the
same lock, or schedules a set the reader meets after it parks, and
neither the same-loop nor the foreign-thread path loses one over
thousands of rounds. So the interval a test is not about became an
hour, the one test whose subject IS the keepalive injects the short
one it asserts, and the readers that assert what a stream carried skip
a comment frame rather than parsing it: with the interval forced to a
tenth of a millisecond, so a keepalive is written on every deadline,
the file still passes.
