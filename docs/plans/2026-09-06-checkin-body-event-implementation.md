# A check-in body becomes a debug event: implementation

The companion to
[`2026-09-06-checkin-body-event.md`](2026-09-06-checkin-body-event.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations says
so explicitly.

## M1: the event, its value, its docs

PR TBD.

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
- **`ota/reply.py`** gains `bounded_body`, the bounded serializer, and
  one unconditional emission after the four-way outcome chain.
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
- **The serializer's bound is on strictly more than the limit, and the
  work is bounded per accumulated string rather than per chunk.**
  `iterencode` yields one chunk per string value, so a single enormous
  string in the body is produced by the encoder as one chunk whatever
  the loop does; what the loop bounds is the accumulation, the value
  constructed, the copy every tap receives and everything downstream.
  The plan's paragraph says "the work and the intermediate string are
  both O(bound)"; the accurate statement is that the retained value and
  the accumulation are, and that the one cost proportional to the body
  is the parse, which exists today and does not grow. The `>` rather
  than `>=` is so that a body ending exactly on the bound is whole
  rather than marked truncated.

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

### What this milestone does not close

The issue's last Done-when box, the 2.4.0 Touch-LCD-1.54's real body
answered into #96, needs the physical board. The capture procedure is
merged; the capture is not.
