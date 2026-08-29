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

## M2: the command

PR #354.

### What landed

- **`vinga events tail`,** an `events` noun with one verb in `GROUPS`
  and `COMMANDS`, declared by `_tailed` with `--device`, `--session`,
  `--level` and `--follow`, and performing `_events_tail` as a
  `does=callable` row on the `ota-url` precedent. The three filters are
  the query's own words and travel only when they were written, the
  `_session_filters` rule, so the API's defaults stay said once.
- **`_streamed`,** the streaming sibling of `_sent` in `config/cli.py`.
  It carries the whole boundary across client construction, stream
  opening, iteration and teardown: the request loggers are quieted for
  the length of the stream rather than for its opening, an address that
  the library refuses is `_unopenable`'s sentence, a connect failure is
  `_unreachable`'s, a non-2xx is read whole and handed to `_answer` so a
  401 here says what a 401 says anywhere, and the close answers a
  sentence rather than raising. It always ends by raising, which is the
  shape of the thing: a stream that stopped is either a failure with a
  sentence or `STREAM_ENDED`, which is also one.
- **`STREAM_READ_TIMEOUT_S = None`,** with the paragraph the cli-guide's
  bound-every-wait practice now records beside `apply`'s: the answer
  never finishes arriving, so any finite number would end a healthy tail
  and report it as the server going away. The connect timeout is kept.
- **The line,** `_event_line` and the three functions under it. The
  clock time off `ts`, the level's name for everything but `INFO`, the
  event's name, then the payload's remaining fields as `key=value` in
  payload order. Values are compact JSON with `ensure_ascii`, so a
  newline, a quote or an escape sequence arrives escaped; numbers are
  bare and booleans are `true`/`false`.
- **The two streams and the two statuses.** Events on stdout, flushed
  per line; `dropped` counts as notices on stderr. Ctrl-C ends a follow
  with exit 0, caught in the command whose ordinary ending it is; a
  broken pipe answers `BROKEN_PIPE_STATUS`, caught at `main` for every
  command.
- **The lanes.** The unit suite is `tests/unit/test_config_cli_events.py`
  on the runner fixture with a streaming transport; the live lane opens
  the stream against the real uvicorn while a real board holds a
  conversation over a socket; the wheel lane drives the row from the
  installed binary against a real server.
- **The documents.** The cli-guide's second unbounded-read example, the
  README's "watching a deployment as it runs" subsection under Logging,
  the regenerated `docs/reference/cli.md`, the command-spellings
  manifest, and the changelog's dated entry.

### Deviations from the plan

Two, and one of them adds a module the plan did not name.

- **The SIGPIPE answer moved to a module of its own,**
  `vinga_server/broken_pipe.py`. The plan says the tail "follows the
  `events_cli` SIGPIPE pattern", and following it by copying would have
  meant two copies of a two-part trap: the shell's status for a process
  cut off by SIGPIPE, and the descriptor redirection that keeps the
  interpreter's own final flush from raising again where nothing can
  catch it. Importing `events_cli` was not available: it pulls
  `events_docgen` and the whole event catalog, and the configuration
  CLI's module-scope reach is an inventory a test holds
  (`test_cli_import_weight`). So the pattern has one home, stdlib only,
  and the inventory gains exactly one entry with its reason. The catch
  is at `main` rather than in the row, because `vinga export | head` is
  the same shape and had the same traceback waiting in it.
- **A mid-stream transport failure is `STREAM_ENDED`, not a transport
  sentence of its own.** The plan lists what an unexpected end is (a
  server restarting, a proxy dropping the connection, a deployment
  upgrade), and every one of those arrives here as an httpx error with
  the stream open rather than as a clean end of body. From this side the
  two are one thing, the tail going quiet, so they answer the same fixed
  sentence; a client that told them apart would be reporting a
  distinction it cannot make. A failure at CONNECT time still names the
  masked address, because which address was never reached is the whole
  of what a reader needs there, and the unit suite pins both halves.

### Discoveries

- **`iter_lines` is already an SSE reader's line source.** httpx strips
  the line endings and yields `""` for a blank line, which is exactly
  the frame boundary the format defines, so the parser is a state
  machine over three line shapes and nothing else.
- **A generator is the right shape for the boundary, and its `finally`
  is the whole cleanup contract.** A reader that has read enough leaves
  by closing the generator, which raises `GeneratorExit` at the `yield`;
  that runs the `finally` and gives the connection back, and it does not
  reach the `raise` after it, so a first-match exit is not an
  end-of-stream failure. The command wraps the generator in
  `contextlib.closing` so that leaving is a statement rather than a
  collection cycle.
- **A refusal on this route has to be read before it can be answered.**
  A streamed response has no body in hand, so `_refused_stream` calls
  `.read()` inside the boundary before handing it to `_answer`: the read
  is itself a request that can fail, and a failure there is
  `_unreachable`'s sentence rather than a traceback.
- **The buffered test client cannot drive this row at all.** It reads a
  whole body before handing it back and this body has no end, which is
  M1's discovery met from the client side. `tests/support/config_cli`
  grew `answering`, which puts a handler of the test's own behind a real
  `httpx.Client`: a handler that raises is a connection that never
  opens, and a body chunk that raises is one that dies mid-stream, which
  are the two moments the no-leak cases need.
- **A tail holds the quieting lock for as long as it watches, so it
  cannot share a process with what it is watching.** `logs.quieted`
  serializes on one process-global lock, because the state it holds is
  the process's: two threads quieting at once would restore each other's
  levels. Every request boundary in the package uses it, the simulator's
  socket half included, and this command's span is the length of the
  stream rather than the length of a request. The first live-lane case
  drove the conversation in the same process as the tail and deadlocked
  outright: the tail waited for an event the conversation could not
  produce, and the whole lane hung rather than failing. On a deployment
  the question does not arise, since a tail IS a process doing one
  thing, so the boundary keeps the span the no-leak contract needs and
  the lane runs the command as a subprocess, which also gives it a
  deadline it cannot outlive. `_streamed` says so where a reader of the
  code will meet it. A hung lane is worse than a red one, which is what
  `tests/support/commands.py` exists to say.
- **The live lane has to check the board in BEFORE it opens the
  stream.** The case exists to prove the session attach, and a session
  event is the only thing that can: it reaches the hub because the hub
  is attached to `SessionEvents` at construction, where a server event
  would have reached it through the process-global tap that any test can
  drive. A check-in inside the stream's lifetime emits an `ota_check`
  that would be the first event admitted, and the case would then be
  proving the tap it is not about. The discriminator asserted is
  `session=`, which is the field a server event does not carry.
- **Nothing can observe the moment a subscription attaches**, from
  outside the process or in. Both lanes therefore drive their event in a
  loop until the tail has seen one, with a deadline: a stream that
  opened a millisecond after the only event would otherwise wait for a
  second one that never came.

### Verification

- `uv run ruff check .`: clean.
- `uv run mypy` (the events package's strict scope, as CI runs it): no
  issues; nothing in this milestone is inside that scope.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: green.
- `uv run pytest tests/integration -q`: green, against the compose
  Postgres already listening on 127.0.0.1:5432.
- The command-spellings census and `scripts/check_doc_links.py`: green.
- No device checkpoint: nothing in this milestone changes what a board
  sends or is sent. The live lane drives a real board through a real
  conversation against a real server, which is what this milestone's
  claim about session events needed.

### PR review round

One external review of the branch as pushed to PR #354. Four findings,
condensed below as received, each with its resolution and the commit
that landed it. The verdict was mergeable after the fixes.

Two of the four are the same shape, and it is the shape this milestone
should have been readiest for: a boundary that was written for what the
API sends rather than for what an address can answer. The stream is the
one surface here whose input is a socket from the first byte to the
last, and the reading end trusted a status code and a JSON parser to
stand in for a contract. The other two are a short circuit that read as
though it did two things, and a test that named the wrong path.

1. **P1: a non-SSE 2xx could put a stranger's values on stdout.** The
   read accepted any successful response without looking at what it
   was, any JSON object was a readable frame, and every field of one
   was printed; a proxy, a captive portal or a gateway answering 200
   with a body of its own had its values rendered to an operator's
   terminal.
   *Resolution* (`2ed1e1a1`): two checks in front of the read, neither
   of which reads anything to decide. The media type is compared before
   a line is taken, and it moves to `responses.py` beside
   `PROBLEM_MEDIA_TYPE`, which is where a wire fact both ends read
   belongs; the route reads it from there now rather than spelling it
   again. Then the frame envelope: an ordinary frame carries the three
   keys every streamed event carries, in the published shapes, and a
   `dropped` frame is its own small object and nothing else, with any
   other frame name outside the contract. What this half cannot check
   it says it cannot, since an event's own field names are the
   catalogue's and the client tier may not import it; past the envelope
   those fields are still rendered escaped. The check makes the
   renderers total, and the four level names they are held to are
   derived from `logging` the way `events/live.py` derives the
   server's, so the `--level` help is composed from them and comes out
   byte for byte what it was. Planted-value cases cover a valid object
   under the wrong media type and twelve malformed frames.
2. **P1: a deeply nested frame escaped as a library traceback.**
   `json.loads` does not reject a document nested a few thousand deep,
   it exhausts the stack and raises `RecursionError`, which is not a
   `ValueError` and was caught by nothing; rendering one can do the
   same.
   *Resolution* (`07796c5b`): a frame may nest eight deep, which is past
   anything an event carries and nowhere near what a decoder minds, and
   the walk applying it uses a stack of its own rather than recursion,
   because a recursive check would be a third way to blow the stack on
   the same input. `RecursionError` joins `ValueError` where a frame is
   decoded and again around rendering, built inside the handler and
   raised outside it. Both roads to the one sentence are driven: a frame
   just past the bound parses and is refused by it, and one twenty
   thousand deep never parses and is refused by the arm behind it.
   Neither leaves what it carried anywhere, chain included.
3. **P2: a transport failure skipped the client's teardown.**
   `problem = problem or _close_failed(...)` short circuits, so a
   failure recorded before the close meant no close at all, on exactly
   the paths where something had already gone wrong with the connection.
   *Resolution* (`41ed3668`): the call is unconditional and the first
   problem still wins, in both boundaries rather than only the one the
   review looked at, because `_sent` carried the identical line and
   fixing one of a pair is how the bug returns. The four ways out of the
   streaming boundary are driven and the client is asked whether it is
   closed, which its own interface answers; two of the four fail on the
   old shape. The close failure itself is read on the follow path, where
   it is reachable, and asserted to be this module's sentence with the
   transport's own words nowhere in it.
4. **P2: the unreadable-frame chain test never received a frame.** It
   named no runner fixture and a literal port, so nothing was listening
   and the command failed opening a connection: the assertion was about
   the connect-failure path under the decoding path's name, and would
   have passed with every sanitizing rule in that function deleted.
   *Resolution* (`484e2946`): it runs through the runner's streaming
   transport with a frame that really arrives and really cannot be read,
   and asks for the planted value's absence from the whole chain beside
   the two links being empty. Restoring the raise-inside-the-handler
   shape fails it, which is what says it now tests what it says it
   tests.

What the round changed about the milestone's own claims: the stream is
no longer described as a body this client reads, but as one it reads
only where two checks say it is this API's. The line format is
unchanged, and so are both exit contracts.
