# vinga events tail: implementation

The companion to [`2026-08-29-events-tail.md`](2026-08-29-events-tail.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations
says so explicitly.

## M1: the hub and the stream

PR TBD.

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
