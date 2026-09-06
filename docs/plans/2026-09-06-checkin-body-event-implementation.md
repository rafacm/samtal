# A check-in body becomes a debug event: implementation

The companion to
[`2026-09-06-checkin-body-event.md`](2026-09-06-checkin-body-event.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations says
so explicitly.

## M1: the event, its value, its docs

PR #435.

### What landed

In the order the commits tell it: the vocabulary, then the emission,
then the tests, then the documents.

- **`events/values.py`** gains `CHECK_IN_BODY_LIMIT = 8192`,
  `CHECK_IN_BODY_TRUNCATED = "...[truncated]"`, the `Bounds` built from
  the first, and the `CheckInBody` value type, a `Descriptor` subclass
  like its three siblings and therefore held to the same two properties
  at construction: printable throughout and no longer than its bound.
  The limit is the one descriptor bound whose home is that file rather
  than `config/models.py`, because its decision site has no bound of its
  own to import, and the plan's reasoning (the headroom, the
  `ensure_ascii` printability mechanism, the non-finite policy) is the
  comment beside it.
- **`events/catalog.py`** gains `OtaCheckBodyReported` and the
  `OTA_CHECK_BODY` declaration, `ota_check_body`, at `logging.DEBUG` on
  `OTA_CHANNEL`, with the plan's exact `TEMPLATE` and `ARGS` and the
  six fields it names: `device`, `client`, `board`, `firmware`, the
  nullable `body`, and `said_device` as the rendered-only argument.
  `agents` and `unloaded` are deliberately absent, as the plan says.
- **`ota/reply.py`** gains `bounded_body`, the bounded serializer (an
  iterative capped walk after the review round below), and one
  unconditional emission after the four-way outcome chain.
  `check_version` now calls `_json_object` directly and derives the
  empty mapping itself; `_read_json_object` is untouched and stays on
  the package's export surface. The `said` mapping is split in two:
  `described` holds what the board said about itself, which both events
  carry, and `said` is `described` plus what this server resolved,
  derived from it rather than restated.
- **The tests**, in `tests/unit/test_event_descriptor_sanitization.py`
  beside the existing hostile-value cases: the sentinel driven through
  the handler, the carried case, the null case, the unconditional case,
  and the default-level case. Plus the baseline driver, its `CARRIED`
  row and the moved driver-count pin.
- **The documents**: the capture procedure in
  `docs/devices/README.md` with the tail-first ordering, the
  regenerated `docs/reference/events.md`, the `ota_check_body` row in
  `vinga-server/README.md`'s event index, a dated `Added` entry naming
  the event and pricing the already-at-DEBUG upgrade, and the
  regenerated census manifest.

### Deviations from the plan

Four, none of them a change of direction.

- **One existing no-leak pin had to be narrowed, and the plan did not
  foresee it.** `test_a_rejected_descriptor_reaches_no_retained_surface`
  plants its `REJECTED` sentinel past the BOARD bound, 64 characters in.
  The body bound is 8192, so the sentinel is comfortably inside it and
  the body event now carries it, by design. The retained log at the
  default level never sees the DEBUG record, so the `caplog` halves of
  that test still pass untouched, and that is the honest demonstration
  of the plan's two-filter claim. What did move is the TAP half: a tap
  receives every emission before its own filtering, exactly as the
  plan's emission section prices. `Tap.rendered()` therefore takes a
  `without=` naming one event, used by that one caller, with the reason
  in its docstring. This is the plan's stated behavior meeting an
  assertion written before the behavior existed, not a weakened claim:
  every other assertion in that file is still over everything the tap
  saw.
- **The event index in `vinga-server/README.md` is a third structure
  the plan's documentation footprint did not name.**
  `test_event_docs.py` holds that index equal to the declarations both
  ways, so a new declaration fails the lane until the row exists. One
  row added.
- **The sentinel test reads the tap and the live stream off their
  payloads rather than hunting a substring in their rendering.** An
  `ensure_ascii` body is full of backslashes and a `repr` doubles every
  one of them, so a substring search for the value answers no to a value
  that is plainly there. The negative half is still a substring hunt,
  which is what it has to be, and the sentinel spelling carries no
  backslash.
- **The cut is decided on strictly more than the limit**, so a body
  ending exactly on the bound is whole rather than marked truncated.
- **The serializer's work was bounded per accumulated string rather
  than per chunk**, and this entry is kept as it was written because
  the PR review round below is what came of it. It recorded that
  `iterencode` yields one chunk per string value, so a single enormous
  string was produced whole whatever the loop did, and narrowed the
  plan's O(bound) claim to the accumulation and the retained value
  instead of fixing the mechanism. The round's first P1 is that
  narrowing refused: the requirement was the plan's and it was right,
  so the mechanism moved. Nothing in this position is O(body) any more,
  and the entry stands as the moment the defect was seen and priced
  rather than removed.

### Discoveries

- **httpx serializes `json=` with `ensure_ascii=False`.** A lone
  surrogate in a test payload therefore fails in the test's own encoder
  and never reaches the server. The sentinel case posts raw bytes with
  an explicit content type instead, which is also the more honest
  request: it is what a stranger's curl puts on the wire.
- **The live hub's `subscribe` needs a running loop**, so the sentinel
  case is an async test that drives the sync `TestClient` inside it.
  Nothing deadlocks: the portal has a loop of its own and the wakeup is
  a `call_soon_threadsafe`.
- **A check-in emits its outcome first and its body second**, so a
  reader at DEBUG is handed two events and the test picks the one it is
  about rather than assuming which arrived first.

### PR review round

PR #435. Three findings, all accepted, all fixed on the branch. Two of
them are one defect seen from two sides, and the side they share is the
plan's own prescribed mechanism, so the plan was amended rather than
merely implemented differently.

1. **P1: `bounded_body` allocated the body's size, not the bound's.**
   It appended whole `iterencode` chunks and checked the limit
   afterwards, and CPython emits a scalar string as ONE chunk, so an
   eight-megabyte field was eight megabytes built and appended before
   the bound could bite. On an unauthenticated endpoint that is a cost
   a stranger picks.

   *Resolution*: accepted. The serializer is now an explicit stack walk
   emitting compact JSON tokens into a capped accumulator, escaping
   scalar strings in slices of at most the remaining budget plus one
   character. Measured: an eight-megabyte field peaks at about 25 KB of
   traced allocation, a ratio of 0.003, and the test holds it under a
   hundredth of the input.

2. **P1: a body the parser accepted could `RecursionError` through the
   handler.** `iterencode` recurses once per level and ran outside the
   guarded thunk, so a deeply nested object that `json.loads` took
   became a 500 with a library traceback and an aborted check-in.
   Reproduced before the fix at 970 levels through `TestClient`, with
   the traceback's deepest frames in `json/encoder.py:_iterencode_list`
   and the parse having already succeeded; 970 to 995 all raised, 966
   and below answered.

   *Resolution*: accepted. The walk carries its own stack, so depth
   costs a list entry and the input is handled by construction rather
   than by a limit. `bounded_body` also keeps a one-line belt mapping
   anything the walk cannot represent to the nullable body, retaining
   nothing of the failure. The same depths that raised now answer 200.

3. **P2: `docs/devices/README.md` blurred the two filters.** It said
   the server "keeps all of it" and that the tail's `INFO` default is
   "what the retained log carries"; retention follows
   `server.log_level` independently, which is the distinction the plan
   settled.

   *Resolution*: accepted. The page now says the server emits the
   event, that `--level DEBUG` is what makes a tail show it, and that
   watching and keeping are separate settings, with a deployment at
   `DEBUG` writing down a bounded copy whether or not anybody watches.

**Plan amendment.** The plan's "exact transformation" bullet named
`iterencode` and claimed O(bound) work; findings 1 and 2 are that
mechanism failing both of the requirements the bullet exists to state.
The requirements were right, so the bullet now prescribes the iterative
capped serializer and says in one sentence why the first mechanism was
withdrawn. The bound, the marker and their arithmetic did not move.

**Consequences recorded elsewhere.** The nullable body gained a second
meaning, "the serializer could not walk it" beside "the request carried
no readable object", so the variant's field note and the call site's
comment say both; both mean the one thing the field says, which is that
this server has no representation of what the board sent.

**Tests added.** The multi-megabyte field, asserting the value bounded
with the marker and the traced peak under a hundredth of the input; the
twenty-thousand-deep structure, built with a loop because `json.loads`
cannot make one, asserting a bounded value and no exception; and a
four-hundred-deep body through the handler, asserting an ordinary reply
and the body carried whole. The deep case is pinned on the serializer
rather than at the depth that reproduced the failure, deliberately: the
parser and a recursive encoder spend the same interpreter stack from
the same handler, so the band between them is a few dozen levels wide
and moves with whatever is above them, and a test sitting in it would
fail for the stack rather than for the thing it is about.

### What this milestone does not close

The issue's last Done-when box, the 2.4.0 Touch-LCD-1.54's real body
answered into #96, needs the physical board. The capture procedure is
merged; the capture is not.
