# vinga events tail: the live event stream

Plan for [#342](https://github.com/rafacm/vinga/issues/342).
Implementation notes land in the companion
`2026-08-29-events-tail-implementation.md`, one section per milestone,
appended in the change that ticks the milestone here.

## Goal

The structured events, streamed to an authenticated operator over the
API and printed by `vinga events tail`, so the two moments that today
need `docker logs` on the server host (watching a board's `ota_check`
after provisioning; diagnosing a silently failed reply) are readable
from wherever the CLI already reaches. The content ADR blesses exactly
this shape: "live views (the admin UI's 'what is happening now') are
fed from the event tap, not from a store."

## The issue's decisions, restated

- A subscriber tap with a bounded per-client queue; a slow reader gets
  drop-oldest with a visible dropped count, never silence, and never
  back-pressure into the pipeline.
- An endpoint under `/api/runtime/events` behind the existing bearer
  token, filterable by device and session.
- `vinga events tail [--device MAC] [--session ID] [--follow]`.
- Generated documents move through their generators; events.md gains
  the note that the catalog is also a live surface.
- The transport must be one the admin UI (#129) can reuse as is;
  browsers consume SSE natively through EventSource, so SSE is the
  lean, with a plain since-read as a possible complement rather than
  the mechanism.
- Compose lifecycle and Postgres provisioning stay host-side.

## Open questions, resolved

**SSE, and not a websocket, and not polling.** Server-Sent Events over
a plain HTTP GET (`text/event-stream` via a streaming response). Three
reasons. First, the reuse constraint, stated precisely: native
EventSource cannot set an Authorization header, so the admin UI
consumes this exact route with `fetch()` streaming (reading the same
SSE body through a stream reader, a dozen lines and no library), and
a token never rides a query string, because URLs are retained
everywhere. The SSE wire format is still the right one: it keeps the
route consumable by native EventSource the day a same-origin
authenticated front end exists, and it costs the fetch reader
nothing today. Second, the
auth boundary: the API's bearer check is ASGI middleware that guards
`http` scopes and deliberately passes other scope types through
because the application declares none of them, so a websocket under
`/api` would arrive ungated as written; SSE rides the scope the gate
already guards and widens nothing. Third, polling cannot carry a tail:
there is no server-side event buffer to poll (events are dispatched
and forgotten; the durable record is the conversation store), and
inventing one is retention this surface must not have. For the same
reason there is no `--since` in this issue: a reconnecting client
resumes live and reads history from the conversation record when it
was enabled. The stream sends a comment-line keepalive every 15
seconds so idle streams survive proxies (the interval is an injected
seam so a test never waits it out), and it ends when the client
disconnects or when the server shuts down. Shutdown is explicit
rather than incidental, and it has two paths because the drain
does: with a positive `drain_s`, `serving.py` awaits
`sessions.drain()` and then closes the hub before uvicorn's own
shutdown; with `drain_s <= 0`, where today's code never drains and
calls uvicorn directly, the hub closes immediately before that
direct shutdown and `sessions.drain()` stays uncalled, so the
zero-drain semantics are genuinely unchanged. Either way the close
wakes and terminates every subscription, so a shutdown never hangs
on an open tail.
That ordering is verified at process level against the real draining
server, not inferred from a client disconnect.

**Where the subscriber hub lives, and how session events reach it.**
A `live.py` module inside the events package (strict-typed there by
the package's mypy scope): a `LiveEvents` hub whose `emit(emission)`
is the tap contract, holding the subscriber set. It must hear both
channels, and they arrive differently. Server events: the hub is
attached with `attach_server_tap` at composition time, and
`detach_server_tap` is registered on the application's exit stack in
the same breath, because the server hub is process-global and an
attachment that outlives the lifespan would deliver into a dead app
and double-deliver under a second lifespan; the registration rides
the same stack that already unwinds partial startups. Tests enter
two lifespans sequentially and fail a startup after attachment,
proving no duplicate delivery and no retained hub. Session
events: `SessionEvents` dispatches only to its own attached taps and
a session publishes no accessor, so the composition root hands the
hub to the device edge the same way the conversation store's sink
already travels (Composition field, `ws.py` construction). The
attach point is precise because the early events are the point: the
hub attaches immediately after `SessionEvents` is constructed in
`DeviceSession.__init__`, before any hello exchange, and detaches in
an outer finally that covers the entire `run()` lifetime; the one
path where a constructed session never runs (the capacity rejection
in `ws.py` between construction and `run()`) detaches explicitly on
that branch, with a capacity-rejection cleanup test pinning it,
because
the conversation sink's later attach point would miss exactly the
rejections and early refusals an operator tails for, and the
existing cleanup finally begins only after the hello. Tests cover an
early rejection and an event emitted during runtime construction,
not only an ordinary utterance. One hub
object, attached in both places; the emission's own payload says
which channel it rode. The hub is a leaf of the events package: it
imports the tap vocabulary and nothing of FastAPI, sessions, or the
API.

**Thread safety and the loop.** `emit` runs synchronously on whoever
emits, and server events include emits from the conversation store's
writer thread, so the hub takes a `threading.Lock` around its
subscriber set and each subscriber queue is a `deque(maxlen=capacity)`
guarded by the same lock, with the waiting reader woken through
`loop.call_soon_threadsafe` on an `asyncio.Event`. The tap contract
(never blocking, never raising) is kept by construction: append to a
bounded deque under a lock, count the overwrite when full, set the
event, return. No await and no I/O, with bounded synchronous work
(the wakeup scheduling and the dispatcher's per-tap copy are
allocations, so the claim is boundedness, not allocation-freedom).

**What a subscriber receives.** One JSON object per event: the
emission's payload (already plain builtins, metadata only by the
catalog's construction), plus `level` as the level name and `ts` as
wall-clock ISO-8601 stamped at enqueue. The stamp is the stream's own
field, added because `Emission.at` is a monotonic instant that means
nothing off the host; the JSON log's `ts` is likewise the formatter's,
not the payload's, so the stream is consistent with the retained
surface rather than novel. Filters (`device`, `session`, `level` as a
minimum threshold) are applied at enqueue against the payload's own
identity fields, and the query contract is complete rather than
implied. `device` accepts the MAC in any case with any of the usual
separators and canonicalizes to the lowercase-colon form the events
carry; `session` must be the hex id shape a listing prints, and an
invalid value of either refuses with the fixed sentence naming the
rule, echoing nothing back. `level` is one of DEBUG, INFO, WARNING,
ERROR, case-insensitive, and defaults to INFO: the tap hears
emissions before any logger threshold, and a default that admitted
DEBUG would stream events the retained log itself would not carry.
The CLI exposes `--level` alongside `--device` and `--session`, the
same words as the query. An event carrying no device or session key
passes such a filter only when no filter is set for it, so a
device-filtered tail shows that device's traffic and not the
server's whole life. The
dropped count rides the stream in-band as its own SSE event named
`dropped`, carrying how many were overwritten since the last delivery,
so a UI and the CLI render it the same way and silence never means
loss.

**The route is excluded from act coverage, deliberately.** The
contract test derives covered operations from `Act` rows and asserts a
single JSON 2xx per covered operation; a streamed `text/event-stream`
answer fits neither the `Act` shape (one buffered answer handed to one
renderer) nor that assertion. The operation therefore lands in the
contract test's `EXCLUDED` set with its reasoned entry, exactly the
mechanism the test provides for operations outside the act grammar,
and the literal path map in the OpenAPI suite gains the route. The
document's non-2xx responses stay `application/problem+json` like
every other route's, so the refusal-media pin holds untouched.

**The CLI command is a local function row, like `ota-url`.** The `Act`
dataclass is a buffered request-response shape, and pretending a
stream through it would deform the grammar's core for one command.
The `events tail` row is a `does=callable` command (the grammar's
existing second shape). It does not touch `build_client` bare: the
safety of every CLI request lives in `_call`/`_sent` (quieted request
loggers, sanitized address failures, no request URL in any exception
chain, close-failure handling, `Address.shown` as the only rendered
address), and a stream can fail after the response opens, where a
bare client preserves none of that. The row therefore goes through a
streaming sibling of `_sent` in `config/cli.py` that applies the same
address, logging, exception-chain, refusal-body and close rules
across client construction, stream opening, iteration and teardown,
and the callable reads SSE lines from it, printing one line per
event to stdout and `dropped` notices to
stderr. The two modes have exact exit contracts. Without
`--follow`, the command waits for the first matching event, prints
it, and exits 0: a scriptable "wait for the next X", which is the
only live-only reading a tail with no retained buffer can have. With
`--follow`, it streams until interrupted. In both modes an
unexpected end of stream (the server restarting, a proxy dropping
the connection, a deployment upgrade) exits 1 with a fixed "the
event stream ended" sentence on stderr; there is no automatic
reconnect, because a tail that silently rejoined across a gap would
look continuous while lying, and the operator who wants to resume
runs it again. The read timeout for the stream is deliberately
unbounded with the connect timeout kept, recorded the way `apply`'s
unbounded read already is (the cli-guide's bound-every-wait
practice, with the why written down); Ctrl-C ends it with exit 0 (an
interactive tail that was told to stop did its job), and a broken
pipe follows the `events_cli` SIGPIPE pattern so
`vinga events tail | head` cannot traceback. A new `events` noun group holds the row; the adjacent
`vinga-server events reference` dispatch group is a different
program's spelling and keeps its own home, which the cli-guide's
two-spellings section already explains.

**Line format.** One physical line per event on stdout, guaranteed
by encoding rather than by hope: the `ts` time-of-day, the level
name for every level other than INFO (DEBUG included, since an
admitted event must say what it is), the `event` name, then the
payload's remaining fields as `key=value` in payload order with
every non-numeric value rendered as its compact JSON encoding, so a
value carrying a newline, a quote or a terminal control sequence
arrives escaped instead of breaking the line or steering the
terminal (the identifier vocabulary explicitly permits such bytes,
and the output-determinism practice already forbids letting an
answer steer a terminal). Tests cover newline, escape, quote, list
and nested-map values and assert exactly one physical line. It is a rendering of
the same record the JSON log retains, not a new vocabulary; a reader
who needs the exact object pipes the JSON log instead, and the
events.md note says both things.

## Design footprint

- `events/live.py` is a new module and passes the deletion test: its
  callers (the composition root, the route, the session edge) get a
  subscriber hub whose locking, bounding and wakeup they cannot see;
  inlined into any one of them the other two could not reach it.
  Interface: attach points (the tap contract), `subscribe(filters) ->
  subscription`, an async iteration yielding emissions and dropped
  counts, `unsubscribe`. Strict-typed under the package's mypy scope.
- `composition.py`/`app.py` deepen: the hub is built once, attached to
  the server hub, handed to the device edge and to `ApiRuntime`.
- `device/session.py` deepens by one attach/detach pair beside the
  conversation sink's, same lifetime discipline.
- `serving.py` deepens: the shutdown sequence closes the hub
  between the session drain and uvicorn's shutdown.
- `config/api.py` deepens: one streaming route in `_runtime`,
  answering 503 standalone like prompt and diff (a live view of a
  server that is not there has no honest empty).
- `config/cli.py` deepens: the `events` group and the `tail` row as a
  local-function command on the existing client seam. No new CLI
  module; the streaming read lives beside the row the way `ota-url`'s
  derivation lives beside its row.

## Documentation footprint

- `CHANGELOG.md`: dated Added entries per milestone (the route; the
  command).
- Generated: `docs/reference/events.md` through `events_docgen.py`
  (the how-to-read section gains the live-surface note),
  `docs/reference/cli.md`, `docs/reference/api-openapi.json`, the
  command-spellings manifest (same-commit rule).
- Hand-maintained: `docs/architecture/cli-guide.md` records the
  unbounded stream read beside the apply precedent in the
  bound-every-wait practice; `vinga-server/README.md` gains a short
  "watching a deployment" paragraph naming `vinga events tail` where
  it today implies host logs; the content ADR is cited, not edited.
  The observability page (`docs/architecture/observability-surfaces.md`)
  gains the stream as a retained-adjacent surface with the same
  no-content contract as the log, in the milestone that ships the
  route.

## Tests

- Hub unit tests (strict-typed suite beside the package): a subscriber
  sees server and session emissions in order; a full queue overwrites
  oldest and the next delivery carries the dropped count; a slow or
  dead subscriber never blocks `emit` (emit from a foreign thread with
  the loop parked proves it); unsubscribe detaches; a raising
  subscriber internals path cannot exist because the tap never calls
  subscriber code during emit (enqueue only), and the existing tap
  guard covers the hub as it covers any tap.
- Filter tests at enqueue: device, session, level threshold, and the
  no-identity-event rule.
- Route tests: bearer 401 and 503 standalone go through the
  ordinary test client, whose buffered transport is fine for
  refusals; everything about a successful stream does not, headers
  included, because a live 200 stream never completes for the sync
  TestClient to observe, so the SSE headers are asserted from the
  first `http.response.start` in the direct ASGI send/receive
  harness, where the incremental body assertions also run (and the
  live lane covers the real server). The keepalive
  interval is injected so no test waits fifteen seconds.
  Unsubscription is asserted through the hub's public diagnostic
  (`LiveEvents.subscribers`, a documented count the info surface may
  some day also want), never a private reach-in.
- No-leak: the stream carries exactly the payload the log retains,
  pinned as: the streamed object minus its two stream-owned fields
  (`ts`, `level`) equals `fields_of` of the same emission's log
  record; direct whole-object equality is impossible because those
  two fields do not exist in the emission payload. No event value
  vocabulary change means no new leak surface, and the sentinel is
  that equality itself.
- CLI tests on the runner fixture with a streaming-capable fake
  transport: line rendering, dropped-to-stderr, Ctrl-C exit 0, broken
  pipe exit follows the SIGPIPE pattern.
- Live lane: one integration case opens the stream against the real
  server, presses the simulator through one utterance, and asserts an
  expected session event arrives on the stream; the driven-row
  bookkeeping extends to the new row.
- Contract/OpenAPI: the EXCLUDED entry with its five-word-minimum
  reason; the literal path map; refusal media pin untouched.

## Risks

- A tap on the hot audio path costs something whether or not anyone
  is watching, and the plan selects that cost knowingly rather than
  wishing it away: the dispatcher deep-copies the payload once per
  attached non-log tap and the hub takes its lock, on every event,
  subscribers or none (with no subscribers there is no deque append
  and no wakeup, but the copy and the lock stand). That is the same
  price the capture and conversation taps already pay per event, on
  payloads that are small dicts of scalars by construction. There is
  no attach-on-first-subscriber fallback: sessions publish no event
  accessor, so a late attach could never reach a session already in
  progress, which is the very reason the hub wires through
  construction. The per-subscriber queue capacity is fixed at 256
  events, documented on the module: at the log's own volume that is
  minutes of buffer for a stalled terminal, and past it the dropped
  counter is the honest answer.
- SSE through the mounted-app middleware stack: `_SanitizedErrors`
  swallows failures after a response starts, which is correct for a
  stream (a mid-stream failure ends the stream rather than inventing
  a body); the route test that kills the generator pins it.
- The shutdown drain: the stream generator ends on cancellation;
  pinned by the disconnect test, and `drain_s` semantics are
  unchanged (an open stream does not hold the server up).
- Stacked on nothing: this plan is cut from `main` and does not
  depend on #341's milestones; the two touch `_runtime` and the
  grammar tables in different rows, so whichever merges second pays a
  textual rebase.

## Milestones

- [x] [M1: the hub and the
  stream](2026-08-29-events-tail-implementation.md#m1-the-hub-and-the-stream)
  (PR #349). `events/live.py`, composition and
  session wiring, the `/api/runtime/events` route with filters,
  keepalive and dropped events, contract/OpenAPI bookkeeping,
  events.md live-surface note, observability page paragraph,
  changelog.
- [ ] M2: the command. The `events` group and `tail` row on the
  streaming client read, line rendering and stderr dropped notices,
  SIGPIPE and Ctrl-C behavior, cli-guide unbounded-read record,
  README watching paragraph, live-lane drive, generated references,
  changelog.

## Plan review round

External review of commit 77cc0232: backend codex (codex-cli
0.149.1), model gpt-5.6-sol, sandbox read-only, 2026-08-29. Verdict:
not ready as reviewed, ready after the P1/P2 amendments; all ten are
amended below with resolutions.

1. **P1: native EventSource cannot authenticate to this route.** The
   bearer gate reads an Authorization header EventSource cannot set;
   a query-string token is unacceptable because URLs are retained.
   Reconcile explicitly: fetch() streaming in the browser, or a
   same-origin auth mechanism.

   *Resolution*: adopted: the browser story is fetch() streaming of
   the same SSE body, no query-string token ever, with EventSource
   named as what the format keeps possible rather than what ships.

2. **P1: `--follow` has no defined behavior.** Define when the
   command exits in both modes, and unexpected EOF under `--follow`
   (visible, never a successful-looking silence).

3. **P1: the streaming CLI bypasses the no-leak request boundary.**
   `build_client` alone preserves none of `_call`/`_sent`'s
   guarantees (quieted loggers, sanitized addresses, chain rules,
   close handling); a streaming sibling of `_sent` must carry them
   across open, iteration and teardown, with planted-secret tests
   for connect-time and mid-stream failures.

4. **P2: the process-global server tap is never detached.** Register
   `detach_server_tap` on the application's exit stack; test two
   sequential lifespans and a failed startup after attachment.

5. **P2: the session attach point misses early session events.**
   Attach immediately after `SessionEvents` construction, detach in
   an outer finally over the whole `run()`; test an early rejection.

6. **P2: shutdown is asserted against the wrong lifecycle.** Put
   `serving.py` in M1: an explicit hub close after session drain and
   before uvicorn shutdown, `drain_s <= 0` included, verified at
   process level rather than by client disconnect.

7. **P2: the route-test mechanism cannot prove streaming.** The
   sync TestClient buffers; use a direct ASGI harness or a real
   server, inject the keepalive interval, verify unsubscription
   observably, and specify the no-leak comparison as stream minus
   ts/level equals the log record's fields.

8. **P2: the renderer does not guarantee one physical line.** Event
   values may carry newlines and control characters; require compact
   JSON escaping for non-numeric values with tests over hostile
   values.

9. **P2: the cost analysis is wrong.** The hub pays the dispatch
   deep copy and lock whether or not anyone subscribes, and the
   attach-on-first-subscriber fallback cannot reach already-live
   sessions; acknowledge the cost, drop the fallback, fix the
   no-subscriber claim, and fix the unspecified capacity.

10. **P2: the filter contract is underspecified.** Canonical MAC
    spellings, session-id validation without echo, case-insensitive
    level enum with INFO default, `--level` on the CLI, and every
    non-INFO level rendered.

## Plan review delta round

Terra re-review of the amended plan (backend codex, codex-cli
0.149.1, model gpt-5.6-terra, 2026-08-29, reviewed commit 5ece3ea9).
Verdict: ready after amendments; all four applied.

1. **P2: rejected-before-run sessions leak the attach.** The
   capacity rejection in ws.py returns without run(), so the outer
   finally never runs. *Resolution*: adopted; explicit detach on
   that branch with a cleanup test.

2. **P2: the shutdown ordering conflicted with zero-drain
   semantics.** *Resolution*: adopted; two paths stated, and
   sessions.drain() stays uncalled on the zero path.

3. **P2: stream headers cannot be observed through the buffered
   client.** *Resolution*: adopted; SSE headers assert from the
   first response-start message in the ASGI harness.

4. **P3: the allocation-free claim was inaccurate.** *Resolution*:
   adopted; the claim is bounded synchronous work.
