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
seconds so idle streams survive proxies, and it ends when the server
shuts down (the generator is cancelled inside the drain window) or
the client disconnects.

**Where the subscriber hub lives, and how session events reach it.**
A `live.py` module inside the events package (strict-typed there by
the package's mypy scope): a `LiveEvents` hub whose `emit(emission)`
is the tap contract, holding the subscriber set. It must hear both
channels, and they arrive differently. Server events: the hub is
attached with `attach_server_tap` at composition time. Session
events: `SessionEvents` dispatches only to its own attached taps and
a session publishes no accessor, so the composition root hands the
hub to the device edge the same way the conversation store's sink
already travels (Composition field, `ws.py` construction,
`DeviceSession` attaches it for the session's lifetime). One hub
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
event, return. No await, no I/O, no allocation beyond the record.

**What a subscriber receives.** One JSON object per event: the
emission's payload (already plain builtins, metadata only by the
catalog's construction), plus `level` as the level name and `ts` as
wall-clock ISO-8601 stamped at enqueue. The stamp is the stream's own
field, added because `Emission.at` is a monotonic instant that means
nothing off the host; the JSON log's `ts` is likewise the formatter's,
not the payload's, so the stream is consistent with the retained
surface rather than novel. Filters (`device`, `session`, `level` as a
minimum threshold) are applied at enqueue against the payload's own
identity fields; an event carrying neither key passes a device or
session filter only when no filter is set for it, so a device-filtered
tail shows that device's traffic and not the server's whole life. The
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
existing second shape) that opens the streaming request on the same
client seam (`build_client`), reads SSE lines incrementally, prints
one line per event to stdout, and renders `dropped` notices to
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

**Line format.** One line per event on stdout: the `ts` time-of-day,
the level name when above INFO, the `event` name, then the payload's
remaining fields as `key=value` in payload order. It is a rendering of
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
- Route tests over the test client: bearer 401; 503 standalone;
  `Cache-Control: no-store` and `text/event-stream` headers; a
  streamed body yields the enqueued event and the keepalive comment;
  disconnect unsubscribes (subscriber count returns to zero).
- No-leak: the stream carries exactly the payload the log retains,
  pinned by comparing a streamed object against the JSON log record's
  non-standard attributes for the same emission (both_formats
  pattern); no event value vocabulary change means no new leak
  surface, and the sentinel is the equality itself.
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

- A tap that allocates per event on the hot audio path: mitigated by
  enqueue being append+count+wake and by the existing per-tap guard;
  the hub attaches to sessions only when built, and building it is
  unconditional but subscribing is what fans out work, so an idle hub
  is one deque append per event with no subscribers to wake. If even
  that offends the profile, the plan's fallback is attach-on-first-
  subscriber, but the simple always-attached shape ships first.
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

- [ ] M1: the hub and the stream. `events/live.py`, composition and
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
