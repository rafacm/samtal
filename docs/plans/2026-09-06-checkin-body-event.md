# A check-in body becomes a debug event somebody can read

Plan for [#427](https://github.com/rafacm/vinga/issues/427).
Implementation notes land in the companion
`2026-09-06-checkin-body-event-implementation.md`, one section per
milestone, appended in the change that ticks the milestone here.

## Goal

A board's OTA check-in is the one moment it describes itself, and
almost everything it says is discarded: `OtaCheckResolved` keeps the
five operator-facing facts, `device_facts.record` keeps the board and
firmware, and the partition table, flash size and display block reach
no surface a person can read. Bringing up an unfamiliar board today
means repointing `ota_url` at a listener and back, two NVS writes and
a reset to learn what the server was already told.

The fix is the issue's obvious shape: a `DEBUG`-level structured
event carrying the body as the device sent it, emitted beside the
existing check-in event, so `vinga events tail` shows it when
somebody asks and nothing changes when they do not.

## The issue's decisions, restated

- **The body is attacker-controlled.** The endpoint is
  unauthenticated; anything printing a body bounds and escapes it the
  way every retained far-side value here does, with a test pinning a
  hostile body (control characters, a very long field).
- **It cannot become a flood.** A per-boot, per-schedule kilobyte of
  JSON is fine for the minutes somebody is watching and not fine as a
  default.
- **It is a diagnostic, not the record.** #96 builds the durable
  per-device record; this event is what lets somebody decide which
  facts that record should hold, and stays useful for a board nobody
  has seen before. `device_facts.record` and everything around it is
  untouched.

## Open questions, resolved

### How "off unless asked for" is mechanized: the DEBUG level is the switch

The surface already has exactly this mechanism, and no new switch is
built. The event is declared at `logging.DEBUG` on the OTA channel,
the precedent being `ActivationPending`. The live stream's default
filter is `INFO`, chosen there so the tail never shows more than the
retained log carries, so the event reaches nobody until an operator
runs `vinga events tail --level DEBUG`; the retained log at the
default threshold does not carry it either. Asking is one flag on the
command an operator already has, which is what the issue's "the
asking is documented" needs documenting.

### What the event carries: its own declaration, the said facts, and the body

A new event with its own identity, not a fifth variant under
`OTA_CHECK`: joining the existing declaration would emit two records
named `ota_check` per request and break every single-record
assertion. The identity, chosen here so the milestone builds rather
than decides:

- **Event code `ota_check_body`**, declaration constant
  `OTA_CHECK_BODY` beside `OTA_CHECK`, one variant class
  `OtaCheckBodyReported`, exported through the catalog's `__all__`
  the way its siblings are, with the declaration's note saying what
  the event is for (the whole of what a board reported, for whoever
  is bringing one up).
- **`LEVEL` is `logging.DEBUG`** on `OTA_CHANNEL`.
- **`TEMPLATE`** is `"device %s (%s, firmware %s) described itself;
  the body rides this event"`, **`ARGS`**
  `("said_device", "board", "firmware")`: the sentence interpolates
  only the bounded arguments the sibling templates already
  interpolate, and the body is a field, never a sentence.
- **Carried fields**: `device`, `client`, `board`, `firmware` (the
  same bounded `said` values the siblings carry, so a tail filtered
  on a device shows this beside the outcome event), the nullable
  `body`, and `said_device` as the rendered-only argument
  (`carried=False`), exactly as the siblings declare it. `agents`
  and `unloaded` are deliberately not repeated: they are the outcome
  event's facts, emitted in the same breath, and this event is about
  what the board said rather than what the server resolved.

It is emitted in `check_version` beside whichever of the four
existing events fires.

The body travels as a new value type in `events/values.py`, in the
far-side-retained class that is "bounded and sanitized at its
decision site and bounded again here":

- **The value is a compact serialization of the parsed check-in
  object**, preserving surviving key insertion order: what survives a
  JSON parse, re-encoded. Raw request bytes are deliberately not
  retained: the parse is what bounds the shape (an object), and a
  body that did not parse is a different fact below.
- **The exact transformation, at the decision site in `reply.py`**:
  `json.JSONEncoder(ensure_ascii=True, separators=(",", ":"))`
  driven through `iterencode`, accumulating chunks and **stopping as
  soon as the accumulated length reaches the bound**, so the work and
  the intermediate string are both O(bound) whatever the body's size.
  `ensure_ascii=True` is the printability mechanism: every character
  outside printable ASCII, control characters and lone surrogates
  included, leaves as a `\uXXXX` escape, so the result is printable
  by construction and no replacement pass exists to forget.
  Non-finite numbers keep the encoder's default (`NaN`, `Infinity`,
  printable ASCII words): this is a diagnostic representation of what
  the parser accepted, not a JSON document anything re-parses, and
  the policy is stated in the constant's comment.
- **The bound is `CHECK_IN_BODY_LIMIT = 8192` characters, final
  length included.** When the accumulation reaches it, the value is
  cut at `8192 - len(marker)` and the literal marker
  `...[truncated]` (printable ASCII, no ellipsis character) is
  appended, so a truncated value is visibly truncated, never longer
  than the bound, and carries nothing past the cut. A real check-in
  with a full partition table is one to two kilobytes; four times
  that is headroom for a richer firmware. The number is a named
  constant with this paragraph beside it.
- **The value class enforces the same two properties independently**:
  the new value type in `events/values.py` declares the `printable`
  charset rule and a maximum length of `CHECK_IN_BODY_LIMIT`, both of
  which the class checks at construction the way its siblings do, so
  a decision site that forgot the transformation is a refused value
  rather than a leaked one. The constant has one home (`values.py`,
  since the value class is the enforcing side) and the decision site
  imports it.
- **The tests assert the mechanism, not the intent**: the exact
  marker at the exact position, the final length never exceeding the
  bound, every character printable, and a sentinel planted past the
  cut absent from the value.

A body that could not be read as a JSON object is a real state of an
unfamiliar board and must not vanish: the variant's body field is
nullable, null meaning "the request carried no readable object",
which is the same honest-null shape `said_client` uses. The handler
learns the distinction by reading `_json_object` once and deriving
the empty mapping where it does today, so no request is parsed twice
and `_read_json_object`'s collapsing moves into the one caller that
wanted it.

### Where it emits: unconditionally, with the work bounded at the site

One emission per check-in, whatever the outcome, because the boards
this exists for are exactly the ones whose outcome is unpredictable.

The cost of that is stated rather than assumed, because the review
round showed the assumption wrong: `ServerEvents.emit` invokes the
closure and dispatches to every tap on every emission, the dispatch
deep-copies the payload before the live filter can reject it by
level, so a DEBUG event is built and copied per request whether or
not anyone is listening. What makes that acceptable is that every
piece of the added work is O(bound), not O(body): the serialization
below stops producing output at the bound, so the value constructed,
copied and dispatched is at most `CHECK_IN_BODY_LIMIT` characters
plus the marker, per check-in, which is the same order of work the
existing four bounded `said` fields already do. The one O(body) cost
on this path, reading and parsing the request, exists today and does
not grow. An emitter-level interest gate was considered and not
taken: it would be a new mechanism on the events seam for one
caller, and the bounded cost does not justify it; if a second
body-sized DEBUG event ever arrives, that is the moment to build
one, and this paragraph is where that trade is recorded.

### What the docs say, and where

`docs/devices/README.md`, the "Driving a board from a terminal
session" section, gains the procedure: point the board at the server
(or reset it), run `vinga events tail --level DEBUG`, and read the
check-in event, with one sentence saying this is how to see the whole
of what a board reports without repointing it. The generated
`docs/reference/events.md` regenerates through its generator, which
is where the event's fields are documented.

### What this does not close: the hardware box stays open on the issue

The issue's last Done-when box (the 2.4.0 Touch-LCD-1.54's real body
answered into #96) needs the physical board. The PR ticks the three
software boxes; the issue stays open with a comment saying the
capture procedure is merged and the hardware capture remains, so
closing it is the capture's job, not this plan's.

## The standing review lenses

- **No-leak.** The body is the one new retained far-side value, and
  it goes through the value class whose whole job is this: bounded at
  a named constant, unprintables replaced, on every surface an event
  reaches. The sentinel test plants a body with an escape sequence, a
  lone surrogate and a field longer than the bound, and asserts the
  neutralized form on the JSON log, the plain rendering and the live
  stream, and that nothing of the raw form survives anywhere,
  including exception chains. The template interpolates only the
  bounded `said` arguments the sibling events already interpolate,
  never the body: the body is a field, not a sentence.
- **Pin before reshaping.** The `_read_json_object` fold-in is the
  one behavior-preserving move; the existing malformed-body cases in
  the OTA suites pin it (a malformed body still answered, activation
  checks still telling None from empty), and they stay green
  unchanged.
- **Closed sets.** No new token, no new reason. The variant joins the
  catalog, whose own checks (declared levels, channel, args) hold it.
- **Honest seams.** No new injectable. The variant's nullable body
  states the unreadable case as data.
- **Inventories by tooling.** The emission sites for check-in events:
  `grep -n "events.emit" src/vinga_server/ota/reply.py` (six today,
  one added). The surfaces a body could reach:
  `grep -rn "OtaCheckBodyReported" src tests`, asserted
  by the sentinel test rather than by reading.

## Module layout

No new module. `events/values.py` gains one value type (the far-side
class it already declares), `events/catalog.py` one variant beside
its siblings, `ota/reply.py` one emission and the `_json_object`
read-once fold. The deletion test keeps the value type in
`values.py`: that file is the home of exactly this vocabulary.

## Tests

- The sentinel test above, in the OTA event suite beside the
  existing hostile-header cases, driven through the handler with a
  planted body rather than by constructing the variant.
- The body-carried case: a check-in whose body holds a partition
  table and a display block answers as today and emits the event
  carrying both, byte-comparable after the bounding.
- The null case: an unreadable body emits the event with a null body
  and the response is unchanged (the existing malformed-body pins
  prove the second half already).
- The level: the variant declares DEBUG, held by the catalog's own
  checks; one case asserts the live stream's default filter drops it
  and `--level DEBUG` admits it, if the live suite does not already
  pin that pair generically (reuse the existing case if it does).
- The generated events reference regenerates and its drift check
  holds it.

## Risks

- **The bound truncates a body somebody needed whole.** Mitigated by
  the headroom (4x a real body today) and by the truncation being
  visible rather than silent; the record #96 builds is where a chosen
  fact gets durable, unbounded-by-policy storage.
- **A second parse of the request.** Avoided by the read-once fold;
  the pins named above hold the collapse's behavior still.
- **The events reference and the census.** The reference regenerates
  through its generator; the census manifest regenerates through its
  module for this plan document itself.

## Milestones

- [ ] **M1: the event, its value, its docs** (PR TBD). The value type
  with its bound and this plan's reasoning beside it; the variant at
  DEBUG with the `said` fields and the nullable body; the emission
  and the read-once fold in `check_version`; the sentinel, carried,
  null and level cases; `docs/devices/README.md`'s procedure;
  `docs/reference/events.md` regenerated through its generator; the
  census manifest; a CHANGELOG `Added` entry; the implementation-doc
  section; a comment on #427 recording that the hardware box stays
  open. One milestone, because the pieces are one feature and each
  alone is releasable but useless. Design footprint: deepens the
  events vocabulary and the OTA handler; no new module, no new seam.
  Documentation footprint: `docs/devices/README.md` (maintained board
  procedures), `docs/reference/events.md` (generated, through its
  generator), `CHANGELOG.md`, the census manifest.

## Plan review round

Backend codex (codex-cli 0.153.0), model `gpt-5.6-sol`, 2026-09-06,
against commit `201e3739`; the reviewer ran 5m25s. Verdict: not
ready, pending the P1 amendments.

1. **P1: the proposed quiet path is not quiet and performs
   attacker-sized work.** `ServerEvents.emit` always invokes the
   closure and dispatches to every tap; dispatch deep-copies the
   payload before `LiveEvents` rejects it by level, and
   `bounded_descriptor` scans its whole input before slicing. An
   unauthenticated caller could force a second attacker-sized
   representation plus copies on every request with no DEBUG
   subscriber. The plan must bound the serialization work itself and
   price the per-request cost honestly, or name an emitter-level
   interest gate and everything needed to make it correct.

2. **P1: the truncation and sanitization mechanism described does
   not exist.** `Descriptor` rejects rather than sanitizes;
   `bounded_descriptor` removes unprintables and slices silently.
   The plan must specify the exact transformation, the literal
   truncation marker, whether the limit includes it, how the
   decision site and the value class each enforce the bound, and
   tests asserting the marker, the maximum final length,
   printability and absence of material past the cut.

   *Resolution*: accepted in full; the hand-waved mechanism is
   replaced by a specified one. `ensure_ascii=True` JSON encoding is
   the printability mechanism (everything unprintable leaves as an
   escape, lone surrogates included), `iterencode` with an
   accumulation cap is the bounded producer, the literal marker and
   its arithmetic are stated with the bound including the final
   length, the constant lives in `values.py` with the value class
   enforcing charset and length independently of the decision site,
   and the tests assert marker, length, printability and
   nothing-past-the-cut exactly.

3. **P1: the event's catalog identity is unresolved.** Every variant
   belongs to a named declaration; joining `OTA_CHECK` would emit two
   records under one name and break single-record assertions. The
   plan must choose a separate event code and name the declaration
   constant, variant class, exact template, exact `ARGS`, carried
   fields and exports.

   *Resolution*: accepted in full. The event is `ota_check_body`,
   declaration `OTA_CHECK_BODY`, variant `OtaCheckBodyReported`, at
   DEBUG on the OTA channel, with the exact template, `ARGS`, carried
   fields and export path now written into the plan, and the
   explicit statement that it does not join `OTA_CHECK`. The
   inventory grep is re-keyed to the chosen name.

4. **P2: the event-baseline inventory work is missing.** The
   repository requires a driver per emit path and a `CARRIED` row per
   driver, with a driver-count pin; the four current `check_version`
   paths are inventoried and the fifth must be.

5. **P2: the plain-log assertion cannot pass as described.** The text
   formatter renders only the message; structured fields reach the
   JSON formatter alone. Positive assertions belong on the payload,
   the JSON formatter, an attached tap and the live stream; the text
   formatter and message arguments get negative assertions.

6. **P2: the documented capture procedure is ordered so the event can
   be missed.** The live stream retains nothing; the tail must be
   started and connected before the board is reset.

7. **P2: DEBUG is not one off-switch across surfaces.** Live
   filtering is per subscription after dispatch; retained-log
   filtering is `server.log_level`. A deployment already running at
   DEBUG starts retaining every check-in body at upgrade, and every
   attached tap receives the emission before its own filtering. The
   plan must distinguish the two filters, price the upgrade behavior,
   and stop claiming the event "reaches nobody".

8. **P2: folding away `_read_json_object` changes a deliberately
   preserved import surface.** The OTA package re-exports it as part
   of what stayed importable across the package split; a silent
   removal must not ride this feature.

9. **P3: "the body as sent" overstates parse-and-reserialize.**
   Escapes and numbers normalize, `ensure_ascii` applies, duplicate
   keys collapse to the last. Call it a compact serialization of the
   parsed object preserving surviving key insertion order, and state
   the `ensure_ascii` and non-finite-number policy.
