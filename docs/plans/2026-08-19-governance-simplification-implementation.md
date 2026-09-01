# Simplify the governance surfaces: implementation

Companion to
[`2026-08-19-governance-simplification.md`](2026-08-19-governance-simplification.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the typed event foundation inside the package move

### What was done

Eight commits, in the order the plan's review forced.

**The move.** `vinga_server/events.py` became
`vinga_server/events/__init__.py`, whole and unedited, because a
package added beside the module would have shadowed it (review finding
1). The full unit suite passed byte-unchanged except for one line the
move itself forces: `test_event_surface_guard.py` exempts the one file
the surface is emitted from by path, and that path is now the package's
`__init__`.

**The vocabulary.** `events/values.py` holds the value types the five
converted paths use: `Identifier`, `MachineId` with `SessionId` and
`EventName`, `ClassName`, `Count`, `ConfiguredPath`, plus `Absent` for
a field a variant may omit. Each validates at construction and each
refuses without repeating the value it refused. `ConfiguredPath` is the
one whose two surfaces differ, because the event surface's own do: the
payload field carries the path as text and the sentence renders the
object. The class name pattern moved here from the emitter, which
imports it back.

No token `StrEnum`s were built, and no bounded descriptor type. None of
the five conversation-store paths carries a `TOKEN` or a `DESCRIPTOR`
field: the syntaxes they reference are `event_name` and `session_id`,
and their kinds are `IDENTIFIER`, `ID`, `CLASS_NAME` and `COUNT`. The
plan's brief says to build only what M1's paths use; the token enums
and the descriptor bound type arrive with M2 and M3, which is where the
first `TOKEN` and `DESCRIPTOR` fields convert.

**The catalog model and the first area.** `events/catalog.py` declares
one event per code holding a discriminated set of typed variants, each
a frozen dataclass owning its channel, level, exact payload shape and
rendering. A variant's dataclass fields ARE the payload's keys, so the
field table stops being a second structure; `ARGS` names the ordered
subset the template renders, which is the one thing field order cannot
say, and `declare()` checks its length against the template's `%`
positions at import. Requiredness and nullability are derived from the
annotation: `| None` is nullable, `| Absent` is not required, and the
two are separate columns in the golden (review finding 4). The catalog
reads its state through one installed `CatalogState`, which is the
scratch seam its own suite needs.

**The emitter extension and the guard.** `ServerEvents.emit()` takes a
construction thunk, so building, validating, rendering and serializing
all happen inside the guard (review finding 3). There is no second
enforcement step on this path: the declaration is the check, and the
only thing left at emit time is whether the variant was handed to an
emitter on the channel it declares. Strict raises; forgiving reports
once and dispatches the declared `schema_violation`. `SessionEvents`
did not gain `emit()`: the session channel's base carries a device id
whose value type arrives with that channel's conversion, so
`declare()` refuses a session-channel variant outright rather than
half-supporting it.

**The golden inventory.**
`vinga-server/tests/unit/data/event-catalog-golden.json`, asserted
against the catalog in both directions by
`tests/unit/test_event_golden.py`. Names, channels, levels, argument
order and types, field order, types, requiredness and nullability. No
wording, and a test says so.

**The record baseline.** `vinga-server/tests/tools/event_baseline.py`
drives every emit path in scope and captures (channel, levelno, msg
template, argument types, payload keys) per path into
`vinga-server/tests/unit/data/event-baseline.json`.
`tests/unit/test_event_baseline.py` holds two obligations: every emit
site the conformance walk finds in a scoped module must be claimed by a
driver (which retires with the walk), and every variant the catalog
declares on a scoped channel must be produced by a driver's run (which
outlives it). Captured green before the conversion; the file is
byte-identical across the conversion commit, which is the proof.

**The conversion.** The five sites construct typed variants; their
`EventSpec` entries left `events_schema.py`; their `PINNED_BY` entries
and `test_conversations_event_pins.py` are gone.

**The type-check lane.** mypy, strict, scoped to
`vinga_server/events/`, run in CI after Lint. It passes.

### Deviations from the plan

Five, each with its reason.

1. **One test file changed in the mechanical-move commit.** The brief
   asked for no test edit at all. `test_event_surface_guard.py`
   identifies its single exemption by relative path, and that path
   changes when the file does; the alternative was a failing lane. It
   is the move's own consequence and nothing else changed.

2. **Strict mode raises a sanitized `EventSchemaError` rather than
   re-raising the construction error.** The plan says strict
   "re-raises". Re-raising the thunk's own exception would put whatever
   it was holding on a lane's stderr and in the `__cause__` chain,
   which the no-leak rule forbids without exception. What strict does
   today for an untyped violation is raise `EventSchemaError` with a
   refusal naming registry-owned identifiers, and the typed path does
   exactly that, naming the exception's class and never its words. "Raise
   rather than recover" is preserved; the bytes are not.

3. **`events_docgen.py` renders from both sources.** The plan has the
   generator switching its source of truth to the catalog. While the
   conversion is in flight, only some events are there, so the
   generator reads one sequence built from both: `documented()` is the
   registry's unconverted production events, then the catalog's, then
   the internal recovery event last. The catalog answers the same
   `EventSpec` shape through `described()`, so neither source gets a
   rendering of its own and `documented()` goes when the registry does.
   The regenerated reference keeps its counts (58 events, 99 variants,
   57 production, 1 internal); the four converted sections move within
   the document and their content is unchanged.

4. **The golden records argument names and types beside the fields.**
   The plan enumerates names, channels, levels, field names, types,
   requiredness and nullability. Argument order and type are structure
   in the same sense and contain no wording, and a reordered or retyped
   `%` position is exactly the kind of loud diff the file exists for.

5. **The test-boundary rule gained one named exception.** The baseline's
   exhaustiveness obligation was generated from the conformance suite's
   static walk, which the plan requires by name, and
   `test_support_boundaries.py` forbids a test module importing another
   one. Extracting the walk into `tests/support/` would have been a
   three-hundred-line move of code M3 deletes outright, so the pair was
   exempted by both ends and by name.

   Withdrawn by the review round below. Finding 2 showed the borrowed
   walk could not see a converted site at all, so the harness reads the
   source itself now and imports nothing; the exemption went with the
   import, and the table it lived in stays behind, empty.

### Discoveries

**The catalog's derived description was byte-equal to the registry's
hand-written one.** Before the conversion commit, `described()` produced
`EventSpec` objects equal to `REGISTRY`'s four declarations, field notes
and argument kinds included. That equality was asserted as a test while
both sources held the events, and it is why the regenerated reference's
only content change is the position of four sections. The test retired
with the registry entries it compared against.

**Field notes and argument notes are separate facts.** The first
derivation gave a `%` position the note of the field it renders, which
changed two argument tables. The old registry declares them
independently (`conversations_failed`'s field carries "The exception's
class name, never its message" and its argument carries nothing), so
`value()` takes `note` and `rendered_note` separately.

**A thunk cannot close over an `except ... as` name.** `_prune`'s
failure path builds its variant from the exception, and `except ... as
exc` unbinds `exc` when the block ends, so a closure reaching for it
afterwards finds nothing; ruff catches it as an undefined name. The site
binds the exception to an ordinary local first, which keeps the
construction inside the guard.

**`session` is an ordinary field on a server channel.** The first
version of `declare()` refused any variant declaring `session` or
`device`, which is the session channel's rule.
`conversations_dropped` declares `session` legitimately, so the check
now asks the channel what its base is.

### The inventory, after M1

Production event machinery: 4,738 lines
(`events/__init__.py` 1,397, `events/values.py` 292,
`events/catalog.py` 593, `events_schema.py` 2,314,
`events_docgen.py` 534, `events_cli.py` 141), against 4,287 before.
It grew, as expected for a milestone that builds a second mechanism
before deleting the first: the registry has lost only its four smallest
declarations (72 lines) and every line of the enforcement machinery is
still needed by the 76 unconverted paths. The plan's "roughly half of
4,287" is a claim about the end of M3, and it is the number to hold this
work to there.

New test assets: 1,326 lines
(`test_event_values.py` 168, `test_event_catalog.py` 233,
`test_event_typed_emit.py` 338, `test_event_golden.py` 127,
`test_event_baseline.py` 152, `tests/tools/event_baseline.py` 273,
`tests/support/catalog.py` 35), against 309 retired with
`test_conversations_event_pins.py`.

Transitional apparatus, which falls as the conversion proceeds and is
annotated where it is asserted: the walk finds 76 emit sites (was 81)
across 53 events (was 57); `PINNED_BY` holds 76 identities (was 81);
the classifier reads 70 field and 47 argument positions (was 72 and 49);
`events_schema.py` declares 53 production events plus the internal one
(was 57 plus one), and the catalog declares the other 4.

### Verification

Run from `vinga-server/`, at the last commit of the milestone.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files.
- `uv run pytest tests/unit -q`: 3,166 passed, 16 skipped. (Baseline
  before the milestone: 3,126 passed, 16 skipped.)
- `uv run pytest tests/integration -q`: 60 passed.
- The four documentation drift checks, regenerated and diffed against
  `../docs/reference/`: `config reference`, `conversations schema`,
  `events reference` and `config openapi` are all clean.
  `docs/reference/events.md` is regenerated and committed inside the
  conversion.
- The record baseline is byte-identical across the conversion commit:
  `vinga-server/tests/unit/data/event-baseline.json` does not appear in
  that commit's file list, which is the identity proof.

Not verified here, and not claimed: the container image and the smoke
lane. The image job does not run on pull requests at present, and
nothing in this milestone touches the image's plumbing.

### PR review round (2026-08-19)

External review of PR #217, read-only against commit b7faab99. Verdict:
mergeable after fixes. Findings condensed but faithful; each carries
its resolution and the commit that made it.

**1 (P1). A construction refusal could print a secret.** The guard put
`type(exc).__name__` into the forgiving complaint and the strict
refusal alike. An exception's class name is caller-controlled:
`type(name, (Exception,), {})` accepts any string as a name, validation
included, so a thunk raising an exception built from far-side bytes
carries those bytes in its class name and the refusal renders them.

*Resolution.* Adopted (`92fe677a`). `construction_failed` carries no
detail at all in either mode, and the handler does not bind the
exception: what is never looked at cannot leak later. Both sentinel
branches are now driven twice, once with the credential-shaped sentinel
as a refused value and once with the same sentinel as the raised class's
NAME, asserted absent from the sentence, the arguments, the fields, both
shipped log formats, the exception's own `str`, `repr` and `args`, both
exception chains, and an attached tap.

Noted rather than fixed, because it is neither this PR's change nor its
scope: the untyped path's two neighbouring reports, the failed-tap
sentence in `_offer` and the last-resort `GUARD_MESSAGE`, still render
an exception class name and are reachable by the same construction. They
predate #210 and are pinned by suites this milestone does not touch;
M3 rewrites both paths and is where they should go.

**2 (P1). The baseline's exhaustiveness obligation was vacuous.** It
compared the conformance walk's sites in scope against the drivers with
`walked <= claimed`, and that walk recognizes only
`events.<level>(..., event=...)`. Once the store converted it found zero
sites in scope while the harness claimed five: every one of nothing is
claimed.

*Resolution.* Adopted (`9731e73b`), by the second of the two routes
offered. The harness reads the scoped modules itself and recognizes both
shapes, the untyped call and the typed `emit(lambda: Variant(...))`
thunk, numbering them in one sequence within their enclosing scope so an
identity stays stable while a module is half converted. Emitter calls
are told from a tap's own `emit` by the receiver, read off the module's
`ServerEvents(...)` binding, and a thunk shape the walk cannot read is
an error rather than a skip. The assertion is equality in both
directions, so a path with no driver and a driver with no path fail the
same way; proved by mutation, since adding a sixth typed emit to the
store turns the lane red. The walk also reads which event each path
emits, so each driven path is held to producing its own record. The walk
itself is proved on planted sources rather than trusted.

**3 (P2). `declare()` accepted any string as an event name** while the
payload documents `event` as an `EventName`, so a catalog could declare
an event its own base field would refuse.

*Resolution.* Adopted (`03fdb451`). The name is asked of `EventName`
itself rather than of a pattern restated in the catalog, and the check
runs before any refusal echoes the name: every later message prints it,
which is safe exactly because a name that got that far is one the
`event_name` syntax admits. A name that did not is caller-supplied
bytes, so its refusal states the rule and never the value, asserted by
equality with a credential-shaped spelling.

**4 (P2). The frozen check was not a frozen check.** `_check` asked
`is_dataclass()` while its own error text, the plan and every
declaration said frozen.

*Resolution.* Adopted (`ccd7ea65`). The check reads
`__dataclass_params__.frozen`, and writing its test found a second half:
it ran after the fields were read, and reading them is what needs a
dataclass, so a plain class raised `TypeError` rather than
`CatalogError`. It moved in front of the read, and both refusals are
pinned.

**5 (P3). Stale links after the package move.**
`runtime/pipeline.py` and `device/session.py` still linked to the
removed `../events.py`, and `events/values.py` pointed at an ADR path
resolving inside `vinga-server/` rather than at the repository root.

*Resolution.* Adopted (`32fbe99d`). The package's own `__init__` was
wrong the same way, one level short before the move and two after it,
so it is corrected too. Every link touched was resolved against the tree
from the file that holds it rather than counted by eye.

### Verification, after the review round

Run from `vinga-server/`, at `32fbe99d`.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files.
- `uv run pytest tests/unit -q`: 3,182 passed, 16 skipped (3,166 at the
  end of M1; the 16 new tests are the review round's).
- `uv run pytest tests/integration -q`: 60 passed.
- The four documentation drift checks: all clean, and
  `docs/reference/events.md` is unchanged by this round.
- The record baseline is still byte-identical: no commit in this round
  touches `vinga-server/tests/unit/data/event-baseline.json`.

The image and the smoke lane remain unverified here, for the reason
given above.

## M2: convert the session channel

### What was done

Seven commits, in the order the plan's pin-before-reshaping lens forces:
the vocabulary, the mechanism, the baseline, the declarations, the
conversion, the pins, the sentinels.

**The vocabulary.** `events/values.py` gains the fourteen value types
and eight enumerations the session channel is written in. `DeviceId` is
the one that unblocked the rest: `declare()` refused a session-channel
variant outright because the session base carries a device MAC and no
value type could describe it. Beside it, `LanguageTag`, `Whole`, `Real`,
`Flag`, `AgentNames`, `Descriptor` with `ClientId`, `PromptSources`, a
`TokenValue` base with seven token types, and a `Fragment` base with
eight formatted-fragment types.

Two of those carry a decision worth naming. A token is a member of an
enumeration rather than a string a site writes and a registry restates,
and where one variant admits fewer members than its enumeration holds
the narrowing is a subclass that refuses the rest at construction
(`UnnamedToolSource`), which is a stronger claim than the registry's: a
`tool_call` that names nothing cannot be built for a builtin. And each
formatted fragment carries its grammar and its builder together, so a
site cannot assemble a shape the declaration does not describe;
`events_schema.py`'s grammars now name those builders, which is the
generated reference's only content change beyond position.

**The mechanism.** Three additions to `catalog.py` and the emitter.
The session base is three values rather than one, built inside the guard
through a thunk of its own, because a session id can refuse like
anything else and has to refuse where the variant's own values refuse.
`ARGS` may name a base value as well as one of the variant's own, since
every conversation sentence opens with "session %s" and the session id
is the emitter's to know rather than a value thirty sites restate;
declaring a base name is still refused, because rendering is not owning.
And `value(fixed=...)` states a value the variant IS, taking it out of
the constructor entirely: `session_rejected` says one reason per shape,
and the declared token set is that one member rather than the four a
shared enumeration would have widened it to.

**The declarations.** Twenty events, thirty variants on the session
channel plus the one that rides `vinga_server.ws`. The transcription was
proved rather than reviewed: a temporary test held every event declared
in both sources equal to its old declaration, field notes and argument
kinds included, and it retired with the entries it compared against.

**The baseline.** Widened from five paths to thirty-one, with the prose
pin suite's own drivers ported: those tests drove every one of these
paths onto its own decision, and driving is exactly what a baseline
needs. Three changes the session channel forced, each recorded as a
deviation below.

**The conversion.** Twenty-seven emit sites across five modules
(`device/session.py` 8, `runtime/pipeline.py` 9, `runtime/turntaking.py`
6, `runtime/filler_runner.py` 3, `ws.py` 1). Their `EventSpec` entries
left `events_schema.py`, their `PINNED_BY` and `TOKEN_SOURCES` entries
are deleted, and six of the nine spread builders went with them.

**The pins.** `test_event_surface_pins.py` goes from thirty-four tests
to nine. Every case that plants a credential-shaped sentinel is kept and
widened; the rest restated what the golden inventory and the record
baseline now hold.

**The sentinels.** Both construction-guard branches are driven again on
the session channel with a tap and a capture attached.

### Deviations from the plan

Six, each with its reason.

1. **One server-channel site converted with the session channel.**
   `session_rejected` is emitted on both scopes: the session channel for
   the refusals a session makes after the accept, and `vinga_server.ws`
   for the one the endpoint makes before a session can run at all. An
   event is the unit of declaration, so leaving that variant behind
   would have split one declaration across two sources, which is the
   shape this plan exists to remove. Its record is unchanged and stays
   pinned by `test_server_event_pins.py` until M3 converts that channel.

2. **A driver's capture is its own path's records.** M1's harness kept
   every record a driver produced on a scoped channel. A session driver
   reaches its decision by holding a whole conversation, so its run
   emits every neighbouring path's records too; keeping them would
   record the same shapes several times over and make the committed file
   move whenever an unrelated path's timing did. The capture is filtered
   to the event the walk says that path emits, and every neighbour has a
   driver of its own.

3. **A variant is matched by its keys as well as its sentence.** M1
   identified a produced variant by event, channel, level and template.
   Several session events say one sentence about two shapes: `llm_round`
   reports a provider the registry built out of a configured entry and
   one it never built with the same words, and those four dimensions
   would have let either stand in for both. A variant is now matched by
   a record carrying everything it always carries and nothing it never
   declares, and the drivers that reach such a site run both scenarios.
   This is a strengthening rather than a workaround: under the old rule
   the identity-less shapes of `llm_round`, `llm_retry` and
   `provider_failed` were claimed by records of their siblings, and
   nothing drove them at all.

4. **The baseline's walk reads a chooser one level deep.** Three sites
   pick their shape rather than knowing it, and the choice stays in the
   module that owns it. The walk therefore accepts a thunk naming a
   function in the same module and reads that function for the variants
   it constructs, holding it to exactly one event; a shape it cannot
   read is an error rather than a skip, as before. One level and no
   further, deliberately: a chooser reaching through another chooser
   would be a path whose event the walk could only guess at.

5. **`test_conversations_session.py` migrated a milestone early.** The
   plan has M3 moving it off the registry. It derives the permitted
   stored-field surface from the declarations, and half of those moved
   here, so it reads `events_docgen.documented()` instead: the one place
   that already answers both sources while the conversion is in flight.
   Recreating that union in the test would have been exactly the second
   structure the conversion exists to remove.

6. **The generated reference moves its converted sections.** M1's
   regeneration was byte-identical because a converted event kept its
   position: `{**registry, **catalog}` replaces a value and keeps the
   original key's place. With the registry entries gone the session
   events appear where the catalog declares them, after the unconverted
   ones. The plan excepts the reference from byte identity and holds it
   to semantic completeness, which `test_event_docs.py` asserts; its
   counts are unchanged (58 events, 99 variants, 57 production, 1
   internal).

### Discoveries

**The derived description was byte-equal to the registry's again.**
All twenty-four catalog declarations, the store's four and the session
channel's twenty, produced `EventSpec` objects equal to the registry's,
notes and argument kinds included. That equality is why the reference's
only content change is the composed grammars' builders.

**A shared enumeration would have widened the contract.** The untyped
registry declares `reason` per variant with the one member that variant
emits: `{"bad_device_id"}`, `{"no_agent"}`, and so on. A field typed as
`RejectionToken` would have declared all four everywhere. The fix
tightened the contract instead of loosening it: a fixed value is
`init=False`, so the caller cannot pass it at all, and the declared set
is that one member.

**`init=False` fields are exempt from the default-ordering rule.**
CPython's `_init_fn` only tracks defaults for fields it puts in
`__init__`, so a fixed field may sit anywhere in a variant's field
order. The `Absent`-defaulted ones are not exempt, which is why every
variant lists its omittable values last and its rendered-only values
before them.

**One driver per path, several scenarios per driver.** The harness's
identity rule is one driver per emit path, and three of those paths emit
two or three shapes. Rather than loosen the rule, those drivers run
several scenarios: `drive_tool_call` makes a builtin call, an invented
one and an MCP one, and the provider drivers run once against a
configured entry and once against a provider the registry never built.

**A driver that builds an app needs a database of its own.** The first
app a run builds migrates the configuration database; the next app to
find a migrated one resolves device bindings from it rather than from
the configuration it was built with, which turned every session after
the first into a rejection. Under pytest the per-test fixture hides
this; inside one function driving thirty-one paths it does not.

**A test module may import the baseline harness.** The boundary rule
names a test module by its `test_` prefix, so `tests/tools/` is support
rather than tests. The privacy suite therefore drives its sessions
through the same functions the baseline does instead of keeping a second
copy of them.

### The inventory, after M2

Production event machinery: 6,273 lines
(`events/__init__.py` 1,484, `events/values.py` 834,
`events/catalog.py` 1,594, `events_schema.py` 1,686,
`events_docgen.py` 534, `events_cli.py` 141), against 4,738 after M1 and
4,287 before it. It is still growing, as expected for the middle of a
milestone sequence that builds a second mechanism before deleting the
first: `events_schema.py` has lost 628 lines of declarations and keeps
every line of the enforcement machinery its 49 remaining paths need, and
`events/__init__.py` holds both paths. The plan's "roughly half of
4,287" is a claim about the end of M3.

Test assets: `test_event_surface_pins.py` falls from 1,392 lines to 425;
`tests/tools/event_baseline.py` grows from 447 to 1,044, which is where
those pins' drivers went; `test_event_schema_conformance.py` falls from
2,695 to 2,547.

Transitional apparatus, which falls as the conversion proceeds and is
annotated where it is asserted: the walk finds 49 emit sites (76 after
M1, 81 before) across 33 events (53, 57); `PINNED_BY` holds 49
identities (76, 81); `TOKEN_SOURCES` 16 entries (22); `SPREAD_INVENTORY`
3 (9); the classifier reads 53 field and 34 argument positions (70 and
47); `events_schema.py` declares 33 production events plus the internal
one (53 plus one), and the catalog declares the other 24 in 36 variants
(4 in 5).

### Verification

Run from `vinga-server/`, at the last commit of the milestone.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files.
- `uv run pytest tests/unit -q`: 3,073 passed, 16 skipped. (3,182 at the
  end of M1's review round; the twenty-five prose pins and the
  hundred-and-thirty-five conformance parametrizations that retired with
  twenty-seven emit sites outnumber the thirty-eight tests the
  vocabulary, the mechanism and the sentinels added.)
- `uv run pytest tests/integration -q`: 60 passed.
- The four documentation drift checks, regenerated and diffed against
  `../docs/reference/`: `config reference`, `conversations schema`,
  `events reference` and `config openapi` are all clean.
  `docs/reference/events.md` is regenerated and committed inside the
  conversion; its counts are unchanged (58 events in 99 variants, 57
  production and 1 internal).
- The record baseline is byte-identical across the conversion:
  `vinga-server/tests/unit/data/event-baseline.json` was written before
  the conversion commit and appears in no commit after it, which is the
  identity proof.

Not verified here, and not claimed: the container image and the smoke
lane, for the reason M1 gives.

### PR review round (2026-08-20)

External review of PR #218, read-only against commit `d0de84ac`. Three
P1 findings; two adopted with the commit that made each, one declined
with its reasons.

**1 (P1). A recovery was built from the identity it was handed.** The
typed session path passed the emitter's raw base into `_replacement()`
after identity construction failed, so `SessionEvents("sk.test.secret")`
emitted a `schema_violation` carrying that spelling under `session`. The
test beside it expected exactly that, which is how it survived review:
it pinned the leak rather than the rule. The device identity had the
same shape.

*Resolution.* Adopted (`af91fdbf`). A recovery is no longer built from
the base at all. Each identity is a thunk plus the answer a recovery
gives where the value could not be stated, the emitter builds them under
one guard each, and the replacement payload is assembled from the ones
that validated. The session's fallback is `UNSTATED_SESSION`, this
module's own word and one the `session_id` syntax admits, so the
recovery event is the shape its declaration says it is; the device's is
the null the surface already uses for "none was understood". One guard
each rather than one around the pair, because a device id that refuses
must not take a lawful session id down with it: a recovery is a record
an operator reads, and the identity it can still state is the half that
makes it readable. An emission whose identity could not be built is
still refused whole, since a conversation record missing the session it
belongs to is a shape the declaration denies exists. The offending test
is replaced by three, and the sentinel suite drives all three
combinations of an unusable session and an unusable device with
credential-shaped spellings, through the exception's text and both
chains, both shipped formats, the arguments behind them, the session's
tap and the capture's decision track.

**2 (P1). A declared type was an annotation and nothing more.** A
variant serialized whatever object sat in a field through that object's
own `carried()`, without asking whether it was the type the field
declared. `declare()` reads the annotations, which is a different
question from what a caller passed; mypy runs strict over the events
package and no further; and a frozen dataclass takes whatever it is
handed. `Identifier` is deliberately permissive, so one handed to a
field declaring `LanguageTag` would put a far side's answer on the
surface under a name that promises a bounded code.

*Resolution.* Adopted (`6f05f2b3`). `Variant.verify()` holds every
supplied, default and fixed value to its declared type, and the emitter
calls it inside the guard before anything is rendered or serialized, so
a mismatch is refused exactly the way a refused value is. Three checks,
which are the three things an annotation states and a runtime does not:
the type by `isinstance`, so a narrowing subclass still satisfies the
field it narrows; a null only where the field is nullable; and an
absence only where the field is not required, since `Absent` is an
ordinary value at runtime and one passed to a required field would drop
a key the golden inventory says is always there. The refusal names the
variant, the field and the declared type, all three the catalog's own,
and never what it was holding. Driven in both modes with a
credential-shaped value in the wrong field.

**3 (P1). Declined: the `%` arguments of an unconverted path.**

*Resolution.* Declined as out of this milestone's scope, and the design
question filed as issue #219. Four reasons, which are the standing
resolution for pre-existing retained-surface behavior moved verbatim.
The milestone is behavior-preserving by construction: every converted
path emits the record it emitted before, and the committed record
baseline is the evidence rather than the claim, byte-identical across
the conversion and untouched by this round. The semantics in question
predate #210 and are the surface's, not this change's, so altering them
here would be a behavior change smuggled into a milestone whose whole
proof is that behavior did not change. Whether they should change at all
is a design decision with its own consequences for what operators read,
which is what #219 is for. And the two untyped-path renders already
parked for M3 by PR #217's round, the failed-tap sentence in `_offer`
and the last-resort `GUARD_MESSAGE`, stay parked there: they are the
same question on the same path, and splitting them across milestones
would leave one half of it fixed and the other half live.

### Verification, after the review round

Run from `vinga-server/`, at `6f05f2b3`.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files.
- `uv run pytest tests/unit -q`: 3,089 passed, 16 skipped (3,073 at the
  end of M2; the 16 new tests are this round's).
- `uv run pytest tests/integration -q`: 60 passed.
- The four documentation drift checks: all clean, and
  `docs/reference/events.md` is unchanged by this round.
- The record baseline is untouched: no commit in this round names
  `vinga-server/tests/unit/data/event-baseline.json`, which is what says
  the two fixes moved no lawful record.

The image and the smoke lane remain unverified here, for the reason
given above.

## M3: convert the server channels and delete the reconciliation apparatus

### What was done

Thirteen commits, in the order the pin-before-reshaping lens forces: the
vocabulary, the declarations, the baseline, one correction, the
conversion, then the four deletions, the two obligations that outlive
them, the prose that had to stop describing a module that is gone, and
this section's own arithmetic.

**The vocabulary.** `events/values.py` gains what the remaining
thirty-three events are written in: a Device-Id header as the firmware
spelled it and an activation code as two more machine forms, what a
board says it is and what firmware it runs as two more descriptors, the
session ids a prune removed as a list, a group of exception class names
joined into one, two more formatted fragments, and eleven closed sets
nothing had closed but a tuple in the untyped registry.

One of those sets moved home rather than being restated. The OTA
endpoint's three fixed refusals were literals in `ota/reply.py` and a
tuple in `events_schema.py`, which is two homes for one closed set; the
endpoint reaches for `OtaRefusal`'s members now, so what the set holds
and what a rejection says are one fact.

**The declarations.** Fifty variants across the twelve server channels,
transcribed into the catalog and proved rather than reviewed: a
temporary test held all thirty-three events equal to their old
declarations, channel, level, template, argument kinds and constraints,
field order, requiredness, nullability, token sets, syntaxes, bounds and
notes included. It passed on the first run and retired with the entries
it compared against.

**The baseline.** Widened from thirty-one paths on two channels to
eighty-one on all fourteen, with fifty drivers ported from
`test_server_event_pins.py`, which drove every one of those paths onto
its own decision already. Captured green before a line of the conversion
was written.

**The conversion.** Forty-nine emit sites across sixteen modules. Their
declarations left `events_schema.py`, three spread builders went with
the sites that spread them, and `test_event_schema_conformance.py` went
with the duplication its three reconciliation claims existed to
reconcile. The record baseline is byte-identical: it appears in no
commit of this milestone after the one that captured it.

**The deletions.** The seventeen fault codes, the two-step judging, the
variant matching, the field and argument kind checks, the recovery
rebuild and the four keyword methods on each emitter, and with them the
registry seam they read their declarations through; then
`events_schema.py` itself, once its vocabulary had moved to
`events/values.py` and its channels to `events/catalog.py`. `EventSpec`,
`EventVariant`, `EventField` and `ArgSpec` were not moved at all: they
described a declaration, and a declaration that IS its emission needs no
description, so `events_docgen.py` reads the variants themselves.

**The parked fix.** PR #217's review round left two reports naming an
exception's class name. `_offer`'s says nothing about the exception now,
and the handler does not bind it; the last-resort `GUARD_MESSAGE` was
resolved by deletion, since it belonged to the untyped path's guard.

**The obligations.** The static walk retired with the last conversion,
as the plan said it would, and what the drivers are held to instead is
the catalog: all eighty-five constructible variants have to be produced
by some driver's run, proved by mutation.

### Deviations from the plan

Five, each with its reason.

1. **The production line count is not "roughly half of 4,287"; it is
   6,203.** The plan's expectation was that the catalog, the vocabulary,
   the emitters and the generator would land at roughly half of the
   registry-era machinery. They did not, and the reason is legible in
   the diff rather than hidden in it: about nine hundred lines of the old
   registry's prose notes moved into the catalog rather than
   disappearing, the value vocabulary that replaced the seventeen-fault
   runtime matrix is a module of types where the matrix was a hundred
   lines of branching, and a typed variant spells one value per line with
   its own name, its own type and its own note where a registry literal
   packed a variant's whole field table into a nested dict. What the plan
   was really buying is in the other two numbers: the structures that
   must agree per event fell from nine to two, and event-focused tests
   fell from 10,757 lines to 7,236.

2. **The conformance suite was deleted inside the conversion commit
   rather than in one of its own.** The plan lists the deletions as
   separate commits. This one had nothing left to check the moment the
   last registry declaration went: its walk found no sites, its sidecars
   named no paths, and its coherence claims read an empty registry.
   Deleting it in the commit that emptied it is what keeps every commit
   in this milestone green.

3. **One commit corrects an earlier one rather than the surface.**
   Moving the OTA refusals into `OtaRefusal` gave the sentence's one
   argument a new type: a `StrEnum` member renders exactly as its value,
   so the log line did not move, but `Emission.args` reaches every tap as
   the object itself and a consumer that had always met a plain string
   would have started meeting an enumeration member. The baseline caught
   it, and the fix is its own commit before the conversion, so the
   conversion's own diff still touches no baseline.

4. **`declare()` now admits a value rendered in two positions.** Two
   sentences on this surface say one value twice: an activation code is
   shown and then repeated inside the command an operator is told to
   type, and so is the MAC beside it. The check that refused it would
   have made a site pass one value under two names to say one thing.

5. **The internal event's fourteen variants are built rather than
   written out.** They differ in exactly one class-level fact, and a
   variant is a declaration rather than behavior, so fourteen
   hand-written classes would be one fact restated fourteen times. The
   declaration carries an `internal` flag, which is what the
   documentation prints and what the every-variant-is-driven obligation
   exempts it by, in place of the walk's exemption by name.

### Discoveries

**The transcription was right on the first run.** All thirty-three
events' derived descriptions were equal to the registry's, which is the
third time this has held (M1's four, M2's twenty, M3's thirty-three) and
the reason none of the three conversions moved a record.

**A thunk built inside a loop needs its values bound as defaults.** Two
sites emit from inside a loop, and a closure over the loop variable
would read whichever agent or tool the loop had reached by the time the
guard called it. `ruff`'s B023 finds it; the fix is a default argument,
which still satisfies a zero-argument callable.

**A driver that builds an app needs a database of its own, again.** M2
recorded this for the session channel and it holds for every OTA and
onboarding driver too: the next app to find a migrated database resolves
its device bindings from it rather than from the configuration it was
built with, which turns a check-in into a different answer entirely.

**The descriptor bounds could come home after all.** The untyped
registry restated `config/models.py`'s three limits because it imported
the standard library and nothing else. `events/values.py` can import
them, because `config/models.py` reaches only `runtime/prompt.py` and
`tools/names.py` and neither touches the event surface. One home for the
number, and the restatement the conformance suite existed to hold equal
is gone.

**Two reports one line apart, and only one of them was in scope.**
`_offer`'s failed-tap sentence is fixed here. `runtime/pipeline.py`'s
turn-recorder report has exactly the same shape, a `logger.warning`
naming `type(exc).__name__` for a consumer that raised, and it is not
fixed: it is not the events package's, it was not on PR #217's parked
list, and it is the same design question as the class-name FIELDS that
issue #219 owns. Recorded here so it is not mistaken for having been
looked at and left.

### The inventory, after M3

Production event machinery: 6,203 lines
(`events/__init__.py` 900, `events/values.py` 1,555,
`events/catalog.py` 3,062, `events_docgen.py` 545, `events_cli.py` 141),
against 6,273 after M2 and 4,287 before the plan. `events_schema.py` is
gone. Deviation 1 above says why the plan's halving did not happen and
what did.

Event-focused tests: 7,236 lines across 13 unit files and 3 support
modules, against 10,757 before the plan. Deleted outright:
`test_event_schema_conformance.py` (2,558), `test_event_enforcement.py`
(1,357), `tests/support/schema.py` (34). Cut down:
`test_server_event_pins.py` from 1,931 to 495,
`test_event_enforcement_sentinels.py` from 982 to 734. New since the
plan: `tests/tools/event_baseline.py` (1,564), which is the record
baseline's harness and its eighty-one drivers.

The structures that must agree, per event: **one declaration and one
golden line**. Before the plan there were nine: the `EventSpec`
declaration, the emit site's spelled-out template, its positional args,
its `event=` name, its keyword fields, its `PINNED_BY` entry, its
`TOKEN_SOURCES` entry where it had a token, its `SPREAD_INVENTORY` entry
where it spread a dict, and its prose pin. The four sidecars do not
exist.

The surface itself is unchanged: 58 events in 99 variants across 14
channels, 57 production and 1 internal.

### Verification

Run from `vinga-server/`, at the last commit of the milestone.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files.
- `uv run pytest tests/unit -q`: 2,627 passed, 16 skipped. (3,089 at the
  end of M2's review round; the forty-seven prose pins, the
  hundred-odd enforcement-matrix cases and the conformance suite's
  parametrizations that retired with the untyped path outnumber what the
  vocabulary, the descriptor rewrite and the tap sentinels added.)
- `uv run pytest tests/integration -q`: 60 passed.
- The four documentation drift checks, regenerated and diffed against
  `../docs/reference/`: `config reference`, `conversations schema`,
  `events reference` and `config openapi` are all clean.
  `docs/reference/events.md` is regenerated and committed; its counts are
  unchanged.
- The record baseline is byte-identical across the conversion:
  `vinga-server/tests/unit/data/event-baseline.json` was written before
  the conversion commit and appears in no commit after it.
- The exhaustiveness obligation was proved by mutation rather than
  trusted: dropping one driver turns the lane red on both the
  every-variant-is-produced check and the one-driver-per-path check.

Not verified here, and not claimed: the container image and the smoke
lane, for the reason M1 gives.

### PR review round (2026-08-20)

External review of PR #220, read-only against commit `2fec740d`. Verdict:
not mergeable until the P1. Four findings, all adopted, each with the
commit that made it.

**1 (P1). A strict refusal leaked the exception it was sanitizing
through `__context__`.** Half the converted sites emit from inside an
`except` arm, because that is where a failure is known, and Python
attaches whatever exception is being handled to any exception raised
while it is. So the `EventSchemaError` this module raises about a thunk
it deliberately never looked at arrived at a lane's stderr with the
original bolted to it: its message, its class name, its own cause, and
everything they render as. `raise ... from None` does not fix it; it
suppresses the default traceback's printing of a context that is still
attached and still reachable through one attribute.

*Resolution.* Adopted (`1ce1574b`), by the second of the two routes the
finding offers, and the audit is why. Twenty-nine places in the package
can reach an emit while an exception is being handled, eleven of them
lexically inside the arm and eighteen through one call from it, and
every future one would have to remember. So it is closed at the raise,
which is the one place strict mode lets anything out: the refusal is
raised, caught there, scrubbed of both chain links, and re-raised bare,
since a bare `raise` re-raises the exception already being handled and
attaches no context. Seven tests, four of them driving production paths,
one per shape the audit found: `build_agent_fillers` and the
configuration API's sanitizing middleware emit inside the arm, and a
failed capture write reaches its emit a frame further up. Each plants an
exception whose class name IS the credential-shaped sentinel and asserts
both chain links are None and the sentinel reaches no record, no
argument, no shipped format, no tap and no capture's decision track.
Proved by mutation: `from None` in place of the scrub fails four of
them.

**2 (P2). The activation outcome mapping lost its exhaustiveness
survivor.** `Unbound.outcome` admits four literals and `_activation`'s
`match` names all four, but nothing held it to that, and its docstring
still named the deleted conformance suite as the guard.

*Resolution.* Adopted (`9bee116f`), by the focused-test route rather
than `assert_never`: a runtime wildcard arm would raise on a request
path, and extending the mypy scope to reach that module is a decision of
its own rather than a side effect of a fix. The survivor lives beside
the decision it is about, in `test_unbound.py`, reads the `match` out of
the source, holds its arms equal to the literal's members both ways, and
refuses a wildcard or a capture pattern, since `case _` would handle a
fifth outcome by definition. Proved by mutation twice. The docstring
names it.

**3 (P2). `CaptureWrite` had an unchecked second source of truth.**
`_disable` took an unrestricted `str`, its two call sites wrote the
words as literals, and the test restated them a third time; the only
thing that could refuse a fourth was the emit.

*Resolution.* Adopted (`94c48055`). `_disable` takes a `CaptureWrite`,
its call sites name members, and the emission wraps at the boundary. The
sweep over the twelve enumerations this milestone introduced found three
sites of that shape and fixed all three: the failed write's track,
`refusal_reason`'s answer in `ws.py`, and `Origin.source` in the
onboarding banner. The other nine stay restated with a test holding them
equal to their site, for two stated reasons: three are `fixed=` on their
variants so no site passes them at all, and the rest are somebody else's
vocabulary to name (the configuration's `Literal`, the MCP package's
state words, and two members that are f-strings over the pending table's
own numeric bounds). The tests now say which arrangement each set is in.

**4 (P3). The baseline harness described a walk that no longer
exists.** Its docstring still explained the static source reading and
the planted-source proofs deleted with the last conversion.

*Resolution.* Adopted in the commit that appends this record. It
describes what is there: the catalog-variant coverage that replaced the
walk, the driver identity and count assertion beside it, why the walk
existed at all, and what `identity` and `event` are for.

### Verification, after the review round

Run from `vinga-server/`, at the last commit of the round.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files.
- `uv run pytest tests/unit -q`: 2,637 passed, 16 skipped (2,627 at the
  end of M3; the 10 new tests are this round's).
- `uv run pytest tests/integration -q`: 60 passed.
- The four documentation drift checks: all clean, and
  `docs/reference/events.md` is unchanged by this round.
- The record baseline is untouched: no commit in this round names
  `vinga-server/tests/unit/data/event-baseline.json`, which is what says
  the four fixes moved no lawful record.

## M4: the entity registry sheds its hooks

### What was done

Seven commits, ordered so that each hook group leaves with the code that
consumed it and the byte proofs stay green throughout.

**The store's own four.** `from_row`, `to_row`, `before_parse` and
`inside_write` were filled onto the descriptors by `store.py` and read
back by `store.py`, which is a module talking to itself through a
global. They become `_STORAGE`, a private per-kind table of a frozen
dataclass whose four fields carry real signatures rather than
`Callable[..., object]`. One call convention changed: `before_parse` is
handed the name it checks, the last addressing parameter, instead of a
splatted identity tuple, which is what its one implementation reads.

**The body builder.** `views.py` filled a `partial` onto all five
descriptors at import and called it back through them.
`views.entity_body` has taken a descriptor since #207, so the three call
sites pass one directly.

**The summary lines.** The five `_*_summary` functions stay in `cli.py`
and the summary tree reaches them through `_SUMMARY`, an explicit
mapping typed as what it holds.

**The effect timing.** `notice` stays a descriptor fact, per the plan
review's finding 7, and stops being filled: the three sentences move
from `writes.py` to the registry, beside the kinds that name them, and
each descriptor declares its own. `writes.py` re-exports them, because
it is the module both write paths already import their vocabulary from,
and keeps the two answers a kind cannot give on its own,
`binding_notice` and `secret_notice`. The field is now required rather
than `str | None`, since every commanded kind has an answer.

**The routes.** Twenty-two routes were synthesized from `Endpoint`
tuples: a factory that set `__name__`, `__doc__` and `__signature__` on
a generated function out of data the registry carried only so it could
hand it back to FastAPI. Every byte of that data is the committed
OpenAPI document. Each route is now an ordinary decorated function with
its own name, docstring, parameters, response model and problem
statuses, in `_entity_reads` and `_entity_writes`. `Endpoint`, `Verb`,
the six verb constants, `_METHOD`, `READS`, `WRITES`, `_entity_routes`,
`_install`, `_path`, `_handler`, `_parameters`, `_act` and `_collection`
are gone. No route helper was added: the explicit spellings share
registration boilerplate and nothing else, which is the condition the
plan set.

**The verb hooks.** With the routes written out, `read`, `write` and
`delete` had one consumer left, the CLI's break-glass paths. Those
become one small function per kind calling `ConfigStore`'s typed method
by name, reached through two explicit tables, so no `EntityAccess` layer
was added (the plan review's finding 6).

**The mechanism.** `fill()`, its `object.__setattr__`, the `Hook` alias
and `fields_in_help` (zero consumers, on both tiers) are deleted.

### Deviations from the plan

Two, both small.

1. **`before_parse` changed shape rather than moving unchanged.** The
   plan said the four store-internal hooks move into a typed table. A
   table whose entries carry real signatures cannot hold a check called
   as `hook(*identity)` when identity is a tuple of unknown length, so
   the one implementation is now `Callable[[str], None]` taking the name
   and is called with `identity[-1]`. Behavior is unchanged for the one
   kind that has one; what changed is that the type says what it takes.

2. **The three notice sentences moved module.** The plan said `notice`
   is declared inline like every other data fact. It could not be while
   its strings lived in `writes.py`, which imports the registry, so
   `RESTART_NOTICE`, `BINDING_NOTICE` and `MCP_RELOAD_NOTICE` moved to
   `entities.py` and `writes.py` re-exports them. Nothing outside the
   package changed its import, and no sentence changed a byte.

### Discoveries

**The OpenAPI document was byte-identical on the first regeneration.**
The explicit routes reproduce the synthesis exactly because FastAPI
derives `operationId` from the endpoint function's `__name__` and its
path, `summary` from that name title-cased, and `description` from
`inspect.cleandoc` of the docstring. Writing a real function with the
same name and the same docstring text produces the same bytes, which is
why the factory's three assignments existed at all. That is also the
whole argument against the factory: it was carrying a document's prose
as data in order to reconstitute a function that would produce the
document.

**The import-graph claim was never tested, and could not be.** The
registry's docstring has always said it is readable on a machine with no
database, no encryption key and no FastAPI. That was true of the import
and false of the contents: four modules wrote to the descriptors at
their own import, so what one held depended on what had been imported.
The new test in `test_config_entities.py` imports the registry in a
child interpreter that has loaded nothing else and holds two things
against it, the loaded module set and the per-descriptor set of unset
facts, the second compared against this process with all five consumers
explicitly imported first. A planted `object.__setattr__` in `views.py`
fails it.

**`test_config_entities.py` had no hook expectations to lose.** The
plan's brief expected some. The registry's own suite pinned data-fact
relations only, and every one of them survives untouched.

### The inventory, after M4

`EntityDescriptor` carries **19 dataclass fields**, against 31 before.
Twelve left: the 11 hooks (`from_row`, `to_row`, `before_parse`,
`inside_write`, `read`, `write`, `delete`, `body`, `summary`, `wrote`,
`deleted`) and the `endpoints` tuple, which is 31 - 12 = 19. What
remains is 18 data facts and `secret_key`, the one data-shaped
predicate. `fields_in_help` is not in either count: it was a `ClassVar`
on both tiers, so `dataclasses.fields` never listed it, and it is
deleted as well.

**Zero `fill()` statements and zero installations**, against 27
statements installing 49 values at import (44 callables and 5 notice
strings; 45 keyword arguments statically, one of which was a loop body
that ran five times). `store.py` had 16 statements, `writes.py` 5,
`cli.py` 5, `views.py` 1.

Line counts, before and after: `entities.py` 990 → 651, `api.py`
2,067 → 2,202, `cli.py` 2,284 → 2,361, `writes.py` 195 → 147,
`store.py` 2,160 → 2,160, `views.py` 456 → 456. The configuration
package as a whole: 12,460 → 12,285. `api.py` gained 22 explicit entity
routes (13 route decorators before, 35 after) and lost ten generation
helpers; `entities.py` lost the 22 `Endpoint` literals that carried
their prose.

Import-order coupling: **none**. `api.py`, `cli.py`, `store.py`,
`views.py` and `writes.py` no longer depend on each other having been
imported for a descriptor to be whole, and importing the registry alone
pulls in `vinga_server.config.models` and what that reaches, and none of
the five.

### Verification

Run from `vinga-server/`, at the last commit of the milestone.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files. (The lane
  is scoped to the events package; nothing in this milestone is in it.)
- `uv run pytest tests/unit -q`: 2,638 passed, 16 skipped. (2,637 before
  the milestone; the one added is the registry-wholeness test.)
- `uv run pytest tests/integration -q`: 60 passed.
- The four documentation drift checks, regenerated and diffed against
  `../docs/reference/`: `config reference`, `conversations schema`,
  `events reference` and `config openapi` are all clean.
  `docs/reference/api-openapi.json` and `docs/reference/domain-config.md`
  are **byte-identical**, which is this milestone's core proof, and
  neither file is touched by any commit in it.
- The response-byte, CLI-rendering, acknowledgement and reference-doc
  suites pass unmodified: `test_api_openapi.py`,
  `test_config_api_reads.py`, `test_config_api_writes.py`,
  `test_config_cli_rendering.py`, `test_config_cli_local.py`,
  `test_config_reads.py` and `test_config_docgen.py` are unchanged in
  this milestone's diff. The only test file that changed is
  `test_config_entities.py`, which gained a test and lost none.
- The wholeness test was proved by mutation rather than trusted: an
  `object.__setattr__` planted in `views.py` turns it red, and removing
  it turns it green again.

Not verified here, and not claimed: the container image and the smoke
lane, for the reason M1 gives.

### PR review round (2026-08-20)

External review of PR #221, read-only against commit `bd639c96`.
Verdict: mergeable after fixes. Three findings, all adopted, each with
the commit that made it.

**1 (P2). The wholeness test compared the wrong thing.** It recorded
which of a descriptor's facts were None and compared only those sets, so
it saw a fact installed over a default and missed one installed over a
declared value: a consumer running
`object.__setattr__(entry, "notice", "different")` passed it. It was
also blind to the dataclass quietly losing `frozen=True`, after which
ordinary assignment does the same thing and both processes end up
agreeing on the same wrong value.

*Resolution.* Adopted (`8f01b765`). Every field of every entry of all
three tiers is serialized and compared against the child interpreter,
with models and predicates normalized to their qualified names, and a
second test assigns to each entry and expects `FrozenInstanceError`. The
two serializers travel into the child as their own source, so what a
fact compares as has one definition rather than one per side. Proved by
mutation three ways: a planted `object.__setattr__` over an unset fact,
one over a declared value, and dropping `frozen=True` from the four
dataclasses each turn the suite red, and only the middle one was
invisible before.

**2 (P3). The inventory's arithmetic summed to neither number.** It
said 19 dataclass fields against 31 and then decomposed the change as
20 data facts, `secret_key` and `endpoints`, mixing what remains with
what left.

*Resolution.* Adopted (`e70d7e2c`). Stated as the subtraction it is:
twelve fields left, the 11 hooks named one by one plus `endpoints`, and
31 - 12 = 19; what remains is 18 data facts and `secret_key`.
`fields_in_help` is named as what it was, a `ClassVar` that
`dataclasses.fields` never listed, so it is in neither count.

**3 (P3). Two comments in `cli.py` still described the old shape.** The
acts section said what a delete answers is a descriptor fact filled by
the modules that own it, and `_act`'s docstring said the local path
prints what the kind's descriptor says the API answers. Neither survived
the milestone.

*Resolution.* Adopted (`f5e87d6e`). Rewritten along the line the change
actually drew: where a kind is on the API, what addresses one entry,
which section it occupies and when a write takes effect are still read
straight off the descriptor, and what an act does is written out per
kind. `_act`'s docstring names the two homes its answer is built from,
which are the two the API's own route builds its answer from. Two
comments in `entities.py` went the same way while the file was open:
`secret_key` and `missing` contrasted themselves with a hook type the
module no longer declares.

### Verification, after the review round

Run from `vinga-server/`, at the last commit of the round.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files.
- `uv run pytest tests/unit -q`: 2,639 passed, 16 skipped (2,638 at the
  end of M4; the one new test is finding 1's frozen check).
- `uv run pytest tests/integration -q`: 60 passed.
- The four documentation drift checks: all clean.
  `docs/reference/api-openapi.json` and
  `docs/reference/domain-config.md` are still byte-identical, and no
  commit in this round or in the milestone touches either file.

## M5: the OpenAPI client spike

The code this milestone wrote is no longer in the tree. It was
throwaway by its own README, so once the evaluation below was written
down the code stopped being the artifact of record; it was archived on
2026-09-01 under the annotated tag `spike/openapi-ts-client`, on the
last commit that contains it, and it is also in PR #222. Recover it
with `git checkout spike/openapi-ts-client --
spikes/2026-08-20-openapi-ts-client`. The paths named below are the
paths it had, and this section is otherwise unedited: it is a record of
what was true on 2026-08-20.

### What was done

Four commits, and no Python. Nothing under `vinga-server/` is touched
by this milestone: it reads `docs/reference/api-openapi.json` and
writes only under `spikes/`.

The spike lived at `spikes/2026-08-20-openapi-ts-client/`,
with its own README saying what it is, that none of it ships, and how
to re-run it. Two sub-projects, each a self-contained npm project with
exact pinned versions, a committed lockfile, a generation script and a
strict-mode consumer fixture:

- `hey-api/`: `@hey-api/openapi-ts` 0.99.0, driven by
  `openapi-ts.config.ts`, generating an SDK function per operation, a
  type per schema and its own fetch client.
- `openapi-typescript/`: `openapi-typescript` 7.13.0 writing one
  declaration file, consumed at runtime by `openapi-fetch` 0.17.0.

Both pin TypeScript 5.9.3, and `tsx` 4.23.12 for the one probe that
runs. The vocabularies the fixtures are written in are shared
(`shared/expect.ts`, the compile-time one, 45 lines;
`shared/observe.ts`, the runtime one, 70); the fixtures themselves are
not, because the two client shapes are not: one calls generated
functions, the other calls a client keyed by path template and HTTP
method. Each fixture makes the same six claims in the same order, so
the pair can be read side by side.

Five of the six claims are compile-time, and `npx tsc --noEmit` is
their whole test run. The authentication claim is not, because what a
type says about an `auth` option is not what a client puts on the wire,
so each sub-project also has a `probe.ts` that runs against an injected
fetch and asserts the header it observes. `npm run check` is the two in
order.

Nothing under either `generated/` directory was edited by hand. The
only two knobs turned are `openapi-ts.config.ts` (which plugins to run,
and where to write) and the `--default-non-nullable false` flag on the
`openapi-typescript` command line, whose reason is finding 2 below.

### Output sizes

| | Hey API | openapi-typescript |
| --- | --- | --- |
| Files generated | 16 | 1 |
| Lines | 5,391 | 3,971 |
| Bytes | 205,537 | 173,584 |
| Of which types | 104,441 (`types.gen.ts`) | 173,584 (the whole file) |
| Of which per-operation code | 37,282 (`sdk.gen.ts`, 38 functions) | none |
| Of which vendored runtime | 52,943 (`client/` and `core/`) | none |
| Runtime library to install | none | `openapi-fetch`, 17,235 bytes of ESM |

The two totals are close, and they are made of different things. Hey
API writes 53 kB of HTTP client into the repository, which the project
then owns as generated code and an upgrade rewrites. openapi-typescript
writes none and takes a 17 kB published dependency instead. Hey API's
extra 37 kB is the one function per operation that its call-site
criterion is bought with.

The fixtures are 538 lines (Hey API, 6 refusal probes) and 553 lines
(openapi-fetch, 8), plus a 61-line and a 71-line runtime probe, sharing
45 lines of compile-time vocabulary and 70 of runtime vocabulary.

### Determinism

Each generator was run three times, deleting the output directory
between runs, and the outputs diffed pairwise. Both are byte-identical
across all three runs: `diff -r` on the Hey API tree and `diff` on the
single openapi-typescript file report nothing, twice each. Neither
generator writes a timestamp, a version banner or an absolute path into
its output.

### The acceptance criteria, one by one

| # | Criterion | Hey API | openapi-typescript + openapi-fetch |
| --- | --- | --- | --- |
| 1 | Compiles under strict TypeScript | Pass | Pass |
| 2 | Every operation under its `operation_id` name | Pass (all 38) | Partial (all 38 as types, none at a call site) |
| 3 | Five entities need no handwritten mirror | Partial | Partial |
| 4 | No server internals beyond the document | Pass | Pass |
| 5a | Probe: authentication | Pass (observed, not inferred) | Fail (observed) |
| 5b | Probe: read, write, delete for five entities | Pass (14 of 14; the agent defaults have no delete) | Pass (same) |
| 5c | Probe: typed non-2xx problem responses | Pass | Pass |
| 5d | Probe: optional versus nullable | Pass | Pass with a flag |
| 5e | Probe: provider extension properties | Pass (read is exactly `unknown`) | Pass (same) |
| 6 | Pinned generator versions | Pass | Pass |
| 7 | Deterministic output | Pass | Pass |
| 8 | No manual edits to generated code | Pass | Pass |

**1. Compiles under strict TypeScript.** Both. `tsconfig.json` in each
sub-project sets `strict: true`, `noEmit: true` and
`verbatimModuleSyntax: true`, and `npx tsc --noEmit` is clean over the
fixture and the whole generated output, `skipLibCheck` deliberately
off. Both also compile under TypeScript 7.0.2, checked with
`npx --package typescript@7.0.2 tsc --noEmit -p tsconfig.json`. Two
things had to be right rather than guessed: Hey API's generated
`core/queryKeySerializer.gen.ts` iterates `URLSearchParams.entries()`,
so `lib` needs `DOM.Iterable` and not just `DOM`; and generation, as
opposed to compilation, is where Hey API needs the older TypeScript
(finding 1).

**2. Every operation surfaces under its stable `operation_id` name.**
Hey API passes: the document's 38 operations become 38 exported SDK
functions, each named as the camel case of its `operationId`, so
`read_provider_providers__stage___name__get` is
`readProviderProvidersStageNameGet`. openapi-typescript is partial: the
ids survive verbatim as keys of the generated `operations` interface,
so every operation's request and response types are reachable by the
document's own name, but a call is addressed by path template and HTTP
method and the id never appears at one. The fixture pins both halves,
and pins that the path table and the operation table agree
(`paths["/providers/{stage}/{name}"]["put"]` is exactly
`operations["write_provider_providers__stage___name__put"]`). Finding 3
is about the word "stable" in the criterion, and it applies to both.

**The evidence is exhaustive rather than sampled** (review finding 2).
An earlier draft called the fifteen operations the five entities need
and claimed the result for all thirty-eight. Each fixture now carries
the whole inventory, compared in both directions so an addition is as
loud as a loss: Hey API against `keyof typeof sdk`, with each name also
mapped through the namespace so a type of that name and no function
would not satisfy it; openapi-typescript against `keyof operations`,
and against `keyof paths` as well, since with that candidate the path
is the call site and a renamed operation id would break neither. The
counts are asserted beside the lists, so the 38 operations and 23 paths
this section quotes are checked rather than counted by hand. Deleting
one entry from either list reddens two assertions in that file.

**3. Request and response types for the five entities need no
handwritten mirror.** Partial for both, identically, and the cause is
the document rather than either generator. The write bodies are fully
typed: `ProviderConfig`, `McpServerConfig`, `PromptFragmentConfig`,
`AgentConfig` and `AgentDefaults` all arrive as models a form can bind
to. An addressed read does not. Every one of them answers `Envelope`,
whose `entity` is declared `additionalProperties: true` with no
properties, because the masked shape a read returns is not a value the
entity model would accept (the `********` mask is not a valid provider
option), so both generators render it `{ [key: string]: unknown }`. An
admin UI reading one entity in order to edit it therefore receives an
untyped bag and has to narrow it to the write model itself. That is one
narrowing per entity, not a mirror of the field lists, and it is the
same in both candidates. Finding 4 records it as work for #129.

**4. Nothing leaks server internals beyond the document.** Both pass.
The generated trees were scanned for absolute paths, `vinga_server`,
Python module names, ORM and framework names, and hostnames. Two hits,
both verbatim from the document's own prose: `records.py` in a
description of the conversation store, and `localhost` in the MCP
egress description. The base URL both clients default to is `/api`,
which is the document's `servers` entry. Nothing else crosses.

**5a. Authentication.** The one criterion that separates the two.
The document declares a single `bearerToken` HTTP scheme and applies it
to every operation. Hey API carries it into the output: each SDK
function emits `security: [{ scheme: 'bearer', type: 'http' }]`, the
client config takes an `auth` value or a synchronous or asynchronous
callback, and the generated `core/auth.gen.ts` writes the
`Authorization` header and the `Bearer ` prefix. A consumer never
spells either. openapi-fetch has no notion of security schemes:
openapi-typescript renders them as documentation, and the fixture has
to install a middleware that sets the header and the prefix by hand.
The fixture pins the consequence rather than describing it: a client
built with no middleware at all is the same type as one with it, so
forgetting authentication compiles and fails at runtime with 401 on
every call. That is what a failing probe looks like, and it is recorded
rather than worked around.

**This is the one criterion settled by running rather than by
compiling**, because what a type says about an `auth` option is not
what a client puts on the wire, and the recommendation turns on the
difference (review finding 1). Each sub-project's `probe.ts` injects a
fetch that records the request and answers from memory, invokes
`GET /providers/{stage}/{name}`, and asserts the header it observed. It
is hermetic: no network, a host that does not exist, and a token that
is a literal in the file. What the four runs observed, verbatim:

| Client | `authorization` observed |
| --- | --- |
| Hey API, `auth: () => "spike-token"` | `Bearer spike-token` |
| Hey API, no `auth` option | none |
| openapi-fetch, types alone | none |
| openapi-fetch, hand-written middleware | `Bearer spike-token` |

All four requests went to
`https://vinga.example/api/providers/llm/main` as `GET`, which is
asserted too, so the header observation is about a request that really
carried the path and method the operation declares. The second row is
what makes the first evidence about the `auth` option rather than about
a constant somewhere in the generated client, and the third is the
openapi-fetch failure stated as an observation rather than as a
prediction: that call is fully typed, it compiles, and it is
unauthenticated. Both probes were proved to bite by mutation: dropping
the token from the Hey API client turns the run red and non-zero.

**5b. Read, write and delete for the five entities.** Both pass what
there is to pass, which is not fifteen operations but fourteen. The
result is per entity:

| Entity | Read | Write | Delete |
| --- | --- | --- | --- |
| Provider | Pass | Pass | Pass |
| MCP server | Pass | Pass | Pass |
| Prompt fragment | Pass | Pass | Pass |
| Agent | Pass | Pass | Pass |
| Agent defaults | Pass | Pass | Not applicable |

Each call's response is annotated with the type it must return
(`Envelope` for a read, `Acknowledgement` for a write or a delete) so
that a client typing its answers `unknown` would fail to compile.

**The agent defaults have no delete, by design.** The document declares
GET and PUT on `/agent-defaults` and nothing else: the defaults are a
singleton that always exists, so there is no state a DELETE could
reach and nothing for it to mean. That third of the probe is therefore
not applicable to this entity rather than passing, and neither fixture
substitutes another resource for it. Both instead assert the absence:
the Hey API fixture pins that the only two exports matching
`*AgentDefaults*` are the read and the write, and the openapi-fetch
fixture pins `paths["/agent-defaults"]["delete"]` as `undefined` and
keeps a `@ts-expect-error` on the call itself. A delete appearing later
is a loud diff in both.

`/default-agent` is a separate resource and is probed as one. The
defaults are the provider references every agent inherits; the default
agent is which agent covers a device with no binding of its own. It has
all three operations, and its delete is the one an admin UI's "no
default agent" control would call. An earlier draft of this section
counted that delete as the agent defaults', which was wrong: it is a
different resource with a different meaning, and the correction is
review finding 3.

One more thing the probe turned up: `holds<T>(x)` would also accept
`any`, so the provider read in both fixtures additionally pins `data`
and `error` to exactly `Envelope | undefined` and `Problem | undefined`
with an invariant type equality, which is the assertion that would
catch a generator handing back `any`.

**5c. Typed non-2xx problem responses.** Both pass. Every refusal in
the document is `application/problem+json` carrying the RFC 9457
`Problem` from #192, and both generators type it as `Problem` rather
than `unknown`. Both return a discriminated `{ data, error }` pair:
the fixtures prove the discrimination bites by reading `result.data`
before checking `error` under `@ts-expect-error`. The `Problem` shape
itself is `additionalProperties: false`, and both refuse the `type` key
the server deliberately omits. Where they differ is granularity. Hey
API emits an `Errors` type keyed by status code, so
`WriteProviderProvidersStageNamePutErrors` is exactly
`{ 401: Problem; 409: Problem; 422: Problem; 500: Problem }` and the
read's has 404 as well, and then collapses it to one union at the call
site. openapi-typescript keeps the statuses apart in
`operations[...]["responses"]` and openapi-fetch collapses them the
same way. Both fixtures pin the per-status table, which is what a
consumer wanting to branch on 404 versus 409 would read.

**5d. Optional versus nullable.** The document has fields of all three
characters and the fixtures pin one of each:

| Character | Example | Document |
| --- | --- | --- |
| Optional, not nullable | `AgentConfig.prompt` | not in `required`, `type: string` |
| Required, nullable | `DefaultAgent.name` | in `required`, `anyOf` with `null` |
| Optional and nullable | `ProviderConfig.api_key_env` | neither |

Hey API distinguishes all three straight from the document:
`prompt?: string`, `name: string | null`, `api_key_env?: string | null`.
The fixture proves the distinction in both directions, asserting that
an agent with no prompt is valid, that an agent with a `null` prompt is
refused, that `{ name: null }` is a valid `DefaultAgent` and that `{}`
is not.

openapi-typescript needed the flag of finding 2 to get there, and with
it the same six assertions hold. Under the generator's own defaults it
fails: every property carrying a JSON Schema `default` loses its `?`,
turning `prompt?: string` into `prompt: string` and
`api_key_env?: string | null` into `api_key_env: string | null`, so a
provider write demands two keys the server treats as optional and an
agent write demands a prompt. The fixture was not contorted to
accommodate that; the generation script passes
`--default-non-nullable false` and the finding is recorded.

**5e. The provider entries' extension properties.** Both pass, by
different renderings of the same fact. `ProviderConfig` is the one
entity with `additionalProperties: true`, because a provider carries
whatever options its `type` takes and the server passes them through.
Hey API emits `[key: string]: unknown` on the object; openapi-typescript
emits an intersection with `{ [key: string]: unknown }`. Both admit an
entry carrying `base_url`, `model`, `temperature` and `extra_headers`
beside the declared keys, which is what makes a provider form writable
at all. Three further things the probe checked and both got right: a
declared key keeps its declared type through the passthrough
(`egress: "false"` is refused in both), an extension property reads
back as exactly `unknown` so the consumer has to narrow it, and the
entities that declare `additionalProperties: false` do refuse invented
keys, so the passthrough is a provider fact rather than a hole in every
model.

The word "exactly" is load-bearing and is asserted as a type equality
rather than as an annotation. `any` is assignable to `unknown`, so a
generator that had emitted `[key: string]: any` would have satisfied an
annotated read while switching type checking off for every option a
provider carries, which is the opposite of what this probe exists to
establish. Both fixtures pin `ProviderConfig["base_url"]` and the type
of the read expression itself as `unknown` under the invariant
equality, and inverting either to `any` reddens the run. That
correction is review finding 4.

**6. Pinned generator versions.** Both. `@hey-api/openapi-ts` 0.99.0,
`openapi-typescript` 7.13.0, `openapi-fetch` 0.17.0, `typescript`
5.9.3, all exact, no `^` and no `~`, with `package-lock.json` committed
in each sub-project so `npm ci` reproduces the tree.

**7. Deterministic output.** Both, three runs each, recorded above.

**8. No manual edits to generated code.** Stated and true. No file
under either `generated/` directory has been edited; the determinism
check is what makes that claim checkable, since a hand edit would show
up as a diff on the next run. The two knobs are named in the "What was
done" section.

### Findings

**1. `@hey-api/openapi-ts` 0.99.0 cannot run under TypeScript 7.0.2,
while declaring a peer range that admits it.** The first run of the
generator, with TypeScript pinned to the current 7.0.2, died before
writing a file: `TypeError: Cannot read properties of undefined
(reading 'AnyKeyword')`, from the generator's own initialization
reading `ts.SyntaxKind`. The generator builds its output through the
legacy TypeScript compiler API, which the 7.x native port does not
expose, and its declared peer range is
`>=5.5.3 || >=6.0.0 || 6.0.1-rc`, which 7.0.2 satisfies by semver. The
resolution is to pin 5.9.3, which is what the sub-project does. Worth
knowing rather than alarming: it constrains the TypeScript used to
*generate*, not the one used to build the application, and the
generated output type-checks under 7.0.2 unchanged. openapi-typescript
7.13.0 has no such constraint.

**2. `openapi-typescript` erases optionality for every field with a
default.** Its `defaultNonNullable` option is on by default and strips
`?` from any property declaring a JSON Schema `default`. The reasoning
is sound for a response, which a server fills from the default, and
wrong for a request body, which is what all five entity schemas in this
document are: it made `AgentConfig.prompt`,
`ProviderConfig.api_key_env`, `ProviderConfig.egress` and
`McpServerConfig.tool_timeout_s` required. Passing
`--default-non-nullable false` restores the document's own shape
exactly. The flag is global, which is harmless here because the entity
models appear only as request bodies: an addressed read answers
`Envelope`, whose `entity` is untyped (criterion 3). A frontend
adopting this generator has to know about the flag, and would find out
by having its provider form reject valid input.

**3. The document's `operation_id`s are FastAPI's generated ones, so
"stable" is weaker than the criterion assumes.** They read
`read_provider_providers__stage___name__get`: the route function's
name, the path with its separators flattened, and the method. Renaming
the Python function, moving the route, or changing the path template
renames the client symbol, and the OpenAPI drift check would pass the
rename through as an ordinary diff. This affects both candidates, and
Hey API more, since its call sites carry the names. Declaring explicit
`operation_id`s on the routes would make the client's names a stated
contract that a route refactor cannot move; it is a small change to
`api.py`, out of scope for a spike that must not touch Python, and
recorded here as work to do before #129 builds on the names.

**4. An addressed read answers an untyped entity.** Recorded under
criterion 3 above and repeated here because it is the one place a
frontend still writes type code by hand. The document says so
deliberately, so this is not a defect to fix in either generator: what
#129 needs to decide is where the narrowing from
`{ [key: string]: unknown }` to the write model lives, and whether it
is a runtime validation or a cast. Note that the mask makes a plain
cast a lie in one direction: a read may carry `********` where the
model declares a real value.

**5. Nothing else surprised.** Both generators ran offline apart from
`npm install` fetching packages, both read the committed document by a
relative path without a resolver step, and neither needed a
preprocessing pass over the document. `npm ci` from the committed
lockfiles reproduces both trees and both type-checks.

### The recommendation for #129

**Hey API's `@hey-api/openapi-ts`.** Four reasons, in the order they
weigh.

First, authentication. It is the one acceptance criterion the two
candidates split on, and it splits on the thing the admin UI does on
every single request. Hey API carries the document's security scheme
into the client; openapi-fetch leaves the `Authorization` header and
the `Bearer ` prefix to a middleware the consumer writes, and a client
that forgot it compiles. This reason rests on an observation and not on
a reading of the types: the two clients were run against an injected
fetch, and the header that arrived was `Bearer spike-token` from Hey
API given nothing but a token, and absent from openapi-fetch built from
the generated types alone.

Second, optionality without a corrective flag. Hey API reads the
document's `required` list as written. openapi-typescript needs
`--default-non-nullable false`, and the failure mode when a future
contributor regenerates without it is a form that rejects valid input,
which is exactly the class of unusable-but-compiling the plan's review
warned about.

Third, the operation criterion is met literally: 38 named functions,
one per operation, so a document change that renames or removes an
operation is a compile error at the call site rather than a runtime
404. With openapi-fetch a removed operation is caught (the path table
shrinks) but a renamed `operation_id` is not.

Fourth, Angular. Both are framework-independent, which is the honest
headline: Hey API's default client and openapi-fetch are both plain
`fetch` wrappers with no framework coupling, and either would work in
#129's Angular and spartan stack. Beyond that, Hey API additionally
ships an `@hey-api/client-angular` plugin that generates against
Angular's `HttpClient`, so `HttpInterceptor`-based auth and error
handling and `HttpTestingController` keep working, plus a
`@tanstack/angular-query` plugin; openapi-fetch sits outside
`HttpClient` entirely, so an Angular app adopting it rebuilds its
interceptor story in openapi-fetch middleware. The Angular client
plugin was not exercised in this spike, since it needs `@angular/common`
as a peer and the spike deliberately installs no framework; it is
recorded as an option, not as a verified result.

The costs of the choice, stated so the decision is not sold: 53 kB of
HTTP client is generated into the consuming repository rather than
installed, and an upgrade rewrites it; the generation step pins
TypeScript below 7 until Hey API stops using the legacy compiler API;
and the generated symbol names inherit FastAPI's verbose auto ids,
which spike finding 3 says to fix at the source anyway.

**The fallback is not needed.** The plan's recorded fallback was
handwritten types over the document if both generators failed the
criteria. Neither did: openapi-typescript would also be a defensible
choice for a project that preferred no generated runtime, and the seam
claim holds under either, since the document is CI-drift-checked
whatever consumes it.

**Before #129 builds on this**, two decisions from the findings:
declare explicit `operation_id`s on the entity routes (spike finding
3), and decide where an addressed read's `Envelope.entity` is narrowed
to its write model (spike finding 4).

### Deviations from the plan

One, and it is an addition rather than a departure. The plan says to
run both generators against the committed document with pinned versions
and a checked-in strict-mode consumer fixture, and that is what
happened. The addition is the `--default-non-nullable false` flag on
the openapi-typescript command line: without it that candidate fails
the optional-versus-nullable probe outright, and evaluating it under a
setting that makes every request body wrong would have compared the
wrong thing. The default's behaviour is recorded as spike finding 2 rather
than hidden by the flag.

### Verification

Run from `vinga-server/`, at the last commit of the milestone. This
milestone changes no Python, so these pass trivially; they were run
anyway, which is the only way "trivially" is a fact rather than an
assumption.

- `uv run ruff check .`: all checks passed.
- `uv run pytest tests/unit -q`: 2,639 passed, 16 skipped.
- `uv run pytest tests/integration -q`: 60 passed.
- The four documentation drift checks, regenerated and diffed against
  `../docs/reference/`: `config reference`, `conversations schema`,
  `events reference` and `config openapi` are all clean.
  `docs/reference/api-openapi.json` is byte-identical and no commit in
  this milestone touches it, which is what makes the spike a consumer
  of the seam rather than a participant in it.

In the spike, run from each sub-project directory:

- `npm ci` then `npm run check`: clean in both. The typecheck half also
  passes under TypeScript 7.0.2, checked separately.
- `npm run generate` three times each with the output deleted between
  runs: byte-identical every time.
- The probes were proved to bite rather than trusted. Inverting one
  nullability claim (`Nullable<AgentConfig, "prompt">` from `false` to
  `true`) turns the run red with `TS2344`, and restoring it turns it
  green; deleting one entry from an operation inventory reddens two
  assertions; inverting the extension read from `unknown` to `any`
  reddens one; dropping the token from the Hey API runtime probe turns
  it red and non-zero; and an unused `@ts-expect-error` is itself an
  error, so all six refusal probes in the Hey API fixture and all eight
  in the openapi-fetch one are load-bearing rather than decorative.

Not verified here, and not claimed: any of this running against a live
server. The runtime probes answer from an injected fetch and never open
a socket, which is the point of them; the first real request will be
#129's.

### PR review round (2026-08-20)

External review of PR #222, read-only against commit `cf0ea0e1`.
Verdict: mergeable after fixes. Four findings, all P2, all adopted,
each with the commit that made it. Every one of them is the same
complaint in a different place: the evaluation asserted more than its
evidence established.

**1 (P2). The authentication probe only type-checked the `auth`
option.** It never observed a request, so it would have passed a client
whose generated operations ignored the document's security scheme, or
built the header with the wrong name or without the `Bearer ` prefix.
Authentication is the one criterion the two candidates split on and the
first reason the recommendation gives, which made the weakest evidence
in the spike load-bearing for its conclusion.

*Resolution.* Adopted (`0266dd55`). Each sub-project gains a `probe.ts`
that runs: it injects a fetch which records the request and answers from
memory, invokes `GET /providers/{stage}/{name}`, and asserts the header
it observed. Hermetic by construction, with no network, a host that
does not exist and a token that is a literal in the file. `tsx` 4.23.12
runs it, pinned like everything else, and `npm run check` is typecheck
then probe so it is executable rather than described. The four observed
headers are in the 5a section above: `Bearer spike-token` from Hey API
given nothing but a token, nothing from the same operation with the
token removed, nothing from openapi-fetch built from the generated
types alone, and `Bearer spike-token` from openapi-fetch once the
hand-written middleware is installed. Proved by mutation: dropping the
token from the Hey API client turns the probe red and non-zero.

**2 (P2). The fixtures pinned fifteen operations and the doc claimed
thirty-eight.** The round trips covered what the five entities and the
default agent need, so a document that lost or renamed a device route,
a secret slot or a conversation read would have left both fixtures
green while the criterion said every operation surfaces.

*Resolution.* Adopted (`bd40fce6`). Each fixture inventories the whole
surface with exact key equality in both directions, so an addition is
as loud as a loss: Hey API against `keyof typeof sdk`, with each name
also mapped through the namespace since a union of keys would also be
satisfied by a type of that name and no function; openapi-typescript
against `keyof operations`, and against `keyof paths` as well, because
with that candidate the path is the call site. The counts are asserted
beside the lists, so the 38 operations and 23 paths quoted above are
checked rather than counted by hand. Proved by mutation: deleting one
entry from either list reddens two assertions.

**3 (P2). `DELETE /default-agent` was counted as the agent defaults'
delete.** `/agent-defaults` declares GET and PUT only, and
`/default-agent` is a different resource: the defaults are the provider
references every agent inherits, the default agent is which agent
covers a device with no binding of its own. The criterion's Pass and
the changelog's "read and a write and a delete for each of the five
entities" were therefore both wrong.

*Resolution.* Adopted (`2d70db52`). The result is recorded per entity,
fourteen operations rather than fifteen, with the agent defaults'
delete marked not applicable rather than passing, because a singleton
that always exists has no state a DELETE could reach. Both fixtures
assert the absence instead of substituting another resource for it:
Hey API pins that the only exports matching the defaults are its read
and its write, openapi-fetch pins the path item's `delete` as
`undefined` and keeps a `@ts-expect-error` on the call itself.
`/default-agent` keeps its round trip, labelled as the separate
resource it is and given its own read and write beside the delete. The
5b section, its table row and the changelog sentence are corrected.

**4 (P2). The extension-property probes assigned to `unknown`.** `any`
is assignable to `unknown`, so a generator emitting
`[key: string]: any` would have passed both probes while switching type
checking off for every option a provider carries, which is the opposite
of what the probe exists to establish.

*Resolution.* Adopted (`f4061428`). Both fixtures pin it as an
invariant type equality, on the indexed type and on the read expression
itself, in the vocabulary the optional-versus-nullable probes already
use. It matters slightly more for openapi-typescript, where the index
signature arrives through an intersection rather than on the object.
Proved by mutation: inverting either assertion to `any` reddens the
run.

**What did not change.** No generated file was touched in this round,
so the determinism result stands untouched as well; regenerating both
after the round reproduces the committed output byte for byte, which
also confirms that adding `tsx` moved nothing. No Python was touched in
this round or in the milestone. The recommendation is unchanged, and is
now argued from an observation rather than from a reading of the types.

### Verification, after the review round

Run at the last commit of the round.

- Both sub-projects, `npm ci` then `npm run check`: typecheck clean and
  probe passed, in both. The typecheck half is clean under TypeScript
  7.0.2 as well.
- Both generators re-run with the output deleted: `git status` on
  `spikes/` is clean, so the committed output is still exactly what the
  pinned generators write.
- `git diff --name-only c7d0fbff..HEAD -- vinga-server/` is empty: the
  milestone and its review round change no Python at all.
- Run from `vinga-server/`, for the same reason as before, that
  "trivially" is only a fact when it has been run: `uv run ruff check .`
  all checks passed, `uv run pytest tests/unit -q` 2,639 passed and 16
  skipped, `uv run pytest tests/integration -q` 60 passed, and the four
  documentation drift checks all clean with
  `docs/reference/api-openapi.json` byte-identical.
## M6: the reach-in sweep

### What was done

Twelve commits, in the order the census made obvious: the tool, then
the support module, then the files in descending order of how many
sites they held, then the long tail, then the pointers.

**The instrument.** `vinga-server/tests/tools/reach_ins.py` is the
plan's tokenizer walk, committed where the suite's other tools live so
the after-walk runs the same code as the before-walk. A site is a NAME
token spelled `_x` immediately after an `OP` dot, anywhere under
`tests/`; `self._x` and `cls._x` are a file's own state and are counted
separately rather than silently dropped. It runs as
`uv run python -m tests.tools.reach_ins`, with `--by-site` and `--json`
for the census this section's table is built from.

**The fresh inventory.** The plan's before-numbers (445 sites, 82
names, 51 files) predate M1 through M4, which deleted or rewrote several
of the largest files. The walk against this milestone's base commit
finds **440 sites over 85 names across 55 files**, and the six biggest
are not the six the plan named: `tests/tools/event_baseline.py` (69) is
new since the plan, `test_event_surface_pins.py` (79 before) has fallen
to 5, and `tests/support/sessions.py` still routes more than two hundred
invocations through 22.

**The support module first.** Six of its 22 had a public route already.
The engines a test wants substituted are a parameter of the composition
root, so `agent_providers()` applies scripts and stage overrides before
a session is built, and `realtime_session` and `reply_with` hand their
ASR and their unreachable stage in through it rather than replacing a
live runtime's providers afterwards. `start_reply` is the runtime's own
public entry point, so the helper calls it and answers nothing; the one
caller that held the task asks `replying()` instead.

The rest stay and say what cannot be established publicly, where it
happens. `device_session` transcribes two lines of `run`, which no
public caller but the edge ever needs. `open_session` waits on the
handshake's only record. The listen mode arrives in a wire message these
sessions have no serve loop to receive. And the reply drivers carry one
note for the pair: `drain()` swallows a reply's failure by contract, and
`start_reply` takes an utterance rather than a transcript, so neither
the survival of a reply nor the decision it made for a known sentence
has a public form.

**The seams that did the work.** Four of the five the plan's inventory
mapped carried most of the rewriting, and two more turned up:

- `ScriptedLlm.seen` answers what the model was handed, which is where
  a tool snapshot goes and what the conversation history is for. It
  gained `systems` beside `seen`, the way `RecordingLlm` keeps it, so a
  suite about tools can ask both questions of one round.
- `sockets.spoken()` is new: a reply announces every sentence to the
  device before its audio, so the `tts sentence_start` messages are the
  device-facing view of what was said. Announced rather than heard,
  which is why the filler suite asserts the `replied` event beside it.
- `sessions.history()` drives one more round and answers the turns it
  was handed, minus the utterance it is answering. The history is not a
  surface; it exists so the next round is written against what was
  said, and the model is the thing that receives it.
- `sessions.talking()` reads the active agent off the events object,
  where both sides of the boundary read it.
- The providers' public `client=` parameter, with `Falsey` rewritten to
  wrap the client a test would have injected and forward to it, so the
  injection is proved by driving a call rather than by reading a field.
- `stores.stored_rows()` and `stores.planted()` for the database, which
  are white-box and say so once for twenty-eight sites.

**What the count did.** 440 sites over 85 names across 55 files became
**162 over 75 names across 46 files**. Ten files lost every reach-in
they had; one support module joined the census with two, which is
where a consolidated justification lives. (These are the numbers after
the PR review round below, which retired three more sites; the sweep
itself ended at 167 over 77 across 48.)

### The complete inventory

Every one of the 440 before-sites has a disposition, grouped by file and
private name as the plan permits. The classes are the issue's:
**(a)** enduring invariant kept as is, **(b)** obsolete guard deleted,
**(c)** caller-visible behavior rerouted through a seam that exists,
**(d)** white-box safety invariant kept with the justification written
at the reach. A group whose sites split across classes says so.

The arithmetic: **(a) 0, (b) 10, (c) 138, (d) 292, summing to 440.**
(a) is empty, and necessarily: the plan review's finding 8 permits a
surviving reach-in only as a white-box safety invariant, so a site kept
without a stated justification has no class to be in. Every (d) site
carries one where it happens; a pointer to the sentence one screen up
counts, a docstring alone does not.


**`tests/support/sessions.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_providers` | 6 | 0 | (c) | built through the composition root's own providers parameter (`agent_providers`) |
| `_reply` | 3 | 1 | (c) 1, (d) 2 | start_reply is public; the two drivers name the reply body in one place |
| `_reply_task` | 2 | 1 | (c) | `runtime.start_reply` creates and holds the task itself |
| `_agents` | 2 | 2 | (d) | run's own wiring, transcribed; no public caller but the edge needs it |
| `_listen_mode` | 2 | 1 | (d) | the mode arrives in a listen start on a wire these sessions have no loop for |
| `_turns` | 2 | 2 | (d) | the history `_reply` writes around the same call, for a known transcript |
| `_events` | 1 | 2 | (d) | a session builds its events object and publishes no accessor |
| `_mac` | 1 | 1 | (d) | the identity a handshake would have read off the Device-Id header |
| `_opened_at` | 1 | 1 | (d) | the handshake's completion, recorded nowhere else |
| `_speak` | 1 | 1 | (d) | the one seam that turns a sentence into audio, skipped |
| `_speak_reply` | 1 | 1 | (d) | a reply for a transcript the test names, which nothing public takes |

**`tests/tools/event_baseline.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_turntaking` | 26 | 0 | (d) | the floor, reached through the named helpers in sessions.py |
| `_reply_task` | 11 | 0 | (c) | start_reply and reply_in_flight; the creation half is public |
| `_reply` | 9 | 0 | (d) | the named driver, whose justification is in sessions.py |
| `_utterance` | 9 | 0 | (d) | plant_utterance: what the gates measure at an instant the driver chose |
| `_providers` | 2 | 0 | (c) | the unreachable stage is a stage the session is built with |
| `_events` | 1 | 0 | (c) | SessionEvents.attach through the named accessor |
| `_mac` | 1 | 0 | (d) | with_device, whose justification is in sessions.py |
| `_engine` | 2 | 2 | (d) | an accepted write the database then refuses needs a broken engine |
| `_prune` | 1 | 1 | (d) | a retention pass that could not delete, driven where it fails |
| `_reloading` | 2 | 2 | (d) | a reload refused for overlapping; the public overlap is a race |
| `_pause_output` | 1 | 1 | (d) | the confirmation pause, put on at the instant the fire rule reads it |
| `_resume_output` | 1 | 1 | (d) | the other half of the same plant |
| `_wav` | 1 | 1 | (d) | a write that fails needs a file that cannot be written to |
| `_max_total_mb` | 1 | 1 | (d) | a store over its budget; the public route there is recording gigabytes |
| `_session` | 1 | 1 | (d) | an MCP call that raises after dispatch, which no server does |

**`tests/unit/test_session_filler.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_reply` | 11 | 0 | (d) | drive_reply, the named driver |
| `_filler` | 9 | 1 | (d) | the runner's leftover state: armed, sounding, phrases spent |
| `_turns` | 8 | 0 | (c) | sockets.spoken() plus the replied event: announced and recorded |
| `_reply_task` | 6 | 0 | (c) | start_reply and drain |
| `_turntaking` | 4 | 0 | (d) | the endpointer and pause plants, through turn_taking() |
| `_speak_reply` | 1 | 0 | (d) | the handover turn is driven as a reply now |
| `_agent` | 1 | 0 | (b) | the played clip's own agent field already says it |
| `_pause_output` | 1 | 1 | (d) | the pause the fire rule reads, at a chosen instant |
| `_resume_output` | 1 | 1 | (d) | the other half of the same plant |

**`tests/unit/test_session_barge_in.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_turntaking` | 14 | 0 | (c) 5, (d) 9 | feed is SessionInput.audio; the endpointer comes from ScriptedVad; the endpointed end has no device message |
| `_reply_task` | 11 | 0 | (c) 8, (d) 3 | start_reply and drain; identity survives through reply_in_flight |
| `_reply` | 3 | 0 | (c) | start_reply is the runtime's own entry |
| `_turns` | 3 | 0 | (c) | ScriptedLlm.seen plus sockets.spoken() |
| `_pace_start` | 3 | 3 | (d) | the clock's shift; observing it publicly is a race against a wall clock |
| `_pace_resume` | 2 | 2 | (d) | the gate that holds the paced stream, same reason |
| `_asr_language` | 1 | 1 | (d) | the lock rides on a next transcription this scenario never makes |

**`tests/unit/test_session_record.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_providers` | 9 | 0 | (b) 2, (c) 7 | stages= on the builder; two were `is not None` guards in front of it |
| `_events` | 4 | 0 | (c) | session.session_id and SessionEvents.attach through events_of |
| `_mac` | 3 | 0 | (d) | with_device, stated once in sessions.py |
| `_speak` | 3 | 1 | (d) | the speaking step drained rather than paced, stated once in the builder |
| `_clock` | 2 | 0 | (d) | stamp_with, stated once in sessions.py |
| `_reply` | 1 | 0 | (c) | start_reply takes the pre-made transcription |
| `_device_tools` | 1 | 1 | (d) | a board's tools arrive from a discovery run over a wire |

**`tests/unit/test_session_tools.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_agent` | 5 | 0 | (c) | talking(): the events object is where the active agent lives |
| `_tool_snapshot` | 5 | 0 | (c) | ScriptedLlm.seen carries the tools the model was offered |
| `_system_prompt` | 3 | 0 | (c) | ScriptedLlm.systems carries the prompt the model was sent |
| `_turns` | 3 | 0 | (c) | history(): the conversation the next round is written against |
| `_providers` | 1 | 0 | (b) | an `is not None` guard in front of a rewritten assertion |
| `_turn` | 2 | 2 | (d) | the reservation a reload must not reroute, inside one window |
| `_reserve_tools` | 1 | 1 | (d) | the window itself; a reply around it would not hold the reload |
| `_run_one` | 1 | 1 | (d) | the dispatch inside that window |
| `_classified` | 1 | 1 | (d) | which entry a name was reserved against |
| `_timeout_for` | 1 | 1 | (d) | a bound observable only by waiting it out, and then not by entry |

**`tests/unit/test_session.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_providers` | 4 | 0 | (b) 2, (c) 2 | the voice is built with the providers; two were `is not None` guards |
| `_turns` | 1 | 0 | (c) | history() |
| `_reply` | 1 | 0 | (c) | start_reply and cancel_reply, both public |
| `_agent` | 1 | 0 | (c) | talking() |
| `_listen_mode` | 1 | 0 | (d) | listening_in, stated once in sessions.py |
| `_know_how` | 2 | 0 | (c) | the half a refused switch left alone is what the next round is sent |
| `_activate_agent` | 1 | 1 | (d) | the bound-list guard, reachable only by calling the swap |
| `_speak` | 2 | 2 | (d) | which sentences a cancellation leaves recorded, inside one sentence |
| `_handle_audio` | 1 | 1 | (d) | the edge's decode path, so the buffered bytes are a device's |
| `_turntaking` | 1 | 1 | (d) | the bounded buffer: memory a process holds is on no surface |
| `_utterance` | 1 | 1 | (d) | the same read |

**`tests/unit/test_session_prompt.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_know_how` | 13 | 0 | (c) | CountingServers.asked, the prompt_assembled event, RecordingLlm.systems and prompt.know_how, which is what the activation itself calls |

**`tests/unit/test_session_events.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_providers` | 3 | 0 | (b) 1, (c) 2 | stages= on the builder; one was an `is not None` guard |
| `_reply` | 3 | 0 | (d) | drive_reply, the named driver |
| `_speak` | 1 | 1 | (d) | two whole replies at the speed of their scripts, not of pacing |
| `_pace_start` | 1 | 1 | (d) | a restart inside one reply; the public restart is a second reply |

**`tests/unit/test_session_watchdog.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_reply_task` | 3 | 0 | (c) | start_reply and drain |
| `_reply` | 2 | 0 | (c) | start_reply |
| `_listen_mode` | 1 | 0 | (d) | listening_in_realtime, stated once in sessions.py |

**`tests/unit/test_session_limits.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_reply_task` | 1 | 1 | (d) | the shapes a reply in flight can have, which a real one gives one of |

**`tests/unit/test_session_characterization.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_turns` | 2 | 0 | (c) | sockets.spoken(): what the device was told |
| `_encoder` | 2 | 2 | (d) | feed order into the shared encoder leaves no trace in its frames |

**`tests/unit/test_session_close_reason.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_closed_reason` | 3 | 3 | (d) | the latch: first cause wins, and a race is not reproducible from outside |
| `_latch_close` | 2 | 2 | (d) | the same latch, written at the one place it is written |
| `_cleanly` | 1 | 1 | (d) | a cleanup step that refuses, which nothing public produces |
| `_stop_idle_watchdog` | 1 | 1 | (d) | a close held inside one step so a drain arrives mid-close |

**`tests/unit/test_session_reply_failures.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_speak` | 1 | 1 | (d) | a device vanishing at the instant a sentence is being spoken |

**`tests/unit/test_tts_lookahead.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_providers` | 6 | 0 | (b) 2, (c) 4 | the slow voice and the dawdling model are stages; two were guards |
| `_agent_providers` | 1 | 0 | (c) | the two-voice handover builds both agents' entries and hands them over |
| `_agents` | 1 | 0 | (c) | the bound list comes from the device the session is built for |
| `_speak_reply` | 3 | 1 | (d) | a reply for a named transcript with the real audio path under it |
| `_run_tools` | 1 | 1 | (d) | a tool round as a fixed point between two sentences' audio |

**`tests/unit/test_boundary_contract.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_device_tools` | 3 | 3 | (d) | discovery runs from inside run, after a hello this session never got |
| `_last_activity` | 3 | 3 | (d) | the idle clock; observing it publicly means waiting out a timeout |
| `_mark_activity` | 1 | 1 | (d) | the same clock, set to a known instant |
| `_send_mcp` | 1 | 1 | (d) | the edge's own MCP send, under a client discovery would have built |
| `_turns` | 1 | 1 | (d) | a third round to read the history would end the two-reply scenario |
| `_listen_mode` | 1 | 0 | (d) | listening_in, stated once in sessions.py |

**`tests/unit/test_event_surface_pins.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_reply` | 2 | 0 | (d) | drive_reply, the named driver |
| `_events` | 1 | 0 | (c) | SessionEvents.attach through events_of |
| `_turns` | 1 | 0 | (c) | sockets.spoken(): the sentinel really reached the device |
| `_device_tools` | 1 | 1 | (d) | a device-published name on the surface, with no socket to discover on |

**`tests/unit/test_event_typed_emit.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_report` | 1 | 1 | (d) | the last-resort report; a swallow that worked leaves no record |

**`tests/unit/test_event_values.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_disable` | 1 | 1 | (d) | a signature's annotation is where a closed set is named at its site |

**`tests/unit/test_server_event_pins.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_disable` | 1 | 1 | (d) | a real failed write raises whatever it raises; the sentinel is planted |

**`tests/unit/test_event_enforcement_sentinels.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_mic` | 1 | 1 | (d) | an audio writer whose exception class name is the sentinel |

**`tests/unit/test_config_store.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_engine` | 16 | 0 | (d) | stored_row/stored_rows and planted, justified once in support/stores.py |
| `_read_domain` | 1 | 1 | (d) | two writers meeting inside one write; a real race cannot be scheduled |
| `_held` | 1 | 1 | (d) | the same pacing, one function further in |

**`tests/unit/test_config_api_reads.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_engine` | 4 | 0 | (d) | planted, justified once in support/stores.py |

**`tests/unit/test_config_refusals.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_engine` | 3 | 0 | (d) | planted |

**`tests/unit/test_config_reads.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_engine` | 2 | 0 | (d) | planted |

**`tests/unit/test_config_round_trip.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_engine` | 1 | 0 | (d) | planted |

**`tests/unit/test_config_api.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_engine` | 1 | 1 | (d) | one engine per process; identity is what says it, a pool is what shows |

**`tests/unit/test_config_cli.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_fragment` | 2 | 2 | (d) | the exception's chain, which the CLI never prints |

**`tests/unit/test_config_cli_rendering.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_status_listing` | 1 | 1 | (d) | the same chain |
| `_prompt_listing` | 1 | 1 | (d) | the same chain |

**`tests/unit/test_config_cli_transport.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_permitted` | 1 | 1 | (d) | the same chain |

**`tests/unit/test_config_cli_onboarding.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_device_url` | 1 | 1 | (d) | the same chain |
| `_canonical_slash` | 1 | 1 | (d) | a rule between two httpx distributions, per the test's own docstring |

**`tests/unit/test_config_cli_grammar.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_parser` | 2 | 0 | (c) | the help an operator gets by typing --help |

**`tests/unit/test_conversations_session.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_events` | 3 | 0 | (c) | attached_taps, whose one reach is in sessions.py |
| `_taps` | 3 | 0 | (d) | who is attached has no public reader, deliberately |
| `_record` | 2 | 2 | (d) | a released collaborator is invisible, which is the point of releasing it |
| `_capture` | 1 | 1 | (d) | the same release |
| `_opened_at` | 1 | 1 | (d) | the handshake's completion, per open_session |
| `_engine` | 1 | 1 | (d) | a database going away between a turn and the close after it |

**`tests/unit/test_conversations_store.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_engine` | 3 | 3 | (d) | pragmas on the writer's connection; an accepted write the base refuses |
| `_in_flight` | 2 | 2 | (d) | work not yet committed is by definition not in the database |
| `_batches` | 1 | 1 | (d) | the same batch |
| `_queue` | 1 | 1 | (d) | the same queue |
| `_thread` | 1 | 1 | (d) | joining a writer wedged on a gate the test holds |

**`tests/unit/test_conversations_boot.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_thread` | 5 | 5 | (d) | whether boot started a writer and shutdown joined it |
| `_stopped` | 1 | 1 | (d) | the same lifecycle |

**`tests/unit/test_conversations_retention.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_truncation_due` | 2 | 2 | (d) | the writer's own record that a prune ran |
| `_engine` | 1 | 1 | (d) | a checkpoint against the writer's own connection |

**`tests/unit/test_conversations_cli.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_delete_sessions` | 3 | 3 | (d) | a delete raising with the database's words in it |

**`tests/unit/test_app_lifespan.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_engine` | 4 | 4 | (d) | a released resource has no public form, which is the point |
| `_thread` | 3 | 3 | (d) | the same release, for the writer |
| `_stopped` | 1 | 1 | (d) | the same release |

**`tests/unit/test_drain.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_serve` | 4 | 4 | (d) | serve() runs uvicorn against a real socket for a process's life |
| `_drain_task` | 5 | 5 | (d) | task ownership: a task nobody holds is exactly the bug |

**`tests/unit/test_providers_llm.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_client` | 8 | 4 | (c) 4, (d) 4 | four injections proved by driving; four reads of the built client's bounds |

**`tests/unit/test_providers_openai_asr.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_client` | 9 | 4 | (c) 1, (d) 8 | the falsey injection drives a call; the built client's transport is swapped |
| `_min_audio_s` | 2 | 0 | (c) | what an endpoint does with a short clip |

**`tests/unit/test_providers_openai_tts.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_client` | 5 | 4 | (c) 1, (d) 4 | same as the ASR's |

**`tests/unit/test_providers_elevenlabs.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_client` | 1 | 0 | (c) | the falsey injection drives a call |

**`tests/unit/test_providers_faster_whisper.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_engine` | 1 | 1 | (d) | the model a deployment gets is built inside the provider |

**`tests/unit/test_providers_silero.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_detector` | 2 | 1 | (c) 1, (d) 1 | the scripted model is handed back; the endpointer takes audio, not scores |

**`tests/unit/test_secret_resolution.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_client` | 6 | 1 | (d) | the credential goes nowhere a reader could reach, which is the property |
| `_resolve` | 1 | 0 | (c) | what a request actually carries, read off the stub server |

**`tests/unit/test_auth.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_sign` | 3 | 3 | (d) | an expired token cannot be issued, only aged |

**`tests/unit/test_ws_auth.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_sign` | 1 | 1 | (d) | the same aging |

**`tests/unit/test_auth_boot.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_expire_s` | 1 | 1 | (d) | what a lifetime does is refuse a token that much older |

**`tests/unit/test_registry.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_max_sessions` | 1 | 0 | (c) | a configured cap driven through sockets, as the cap of one above is |

**`tests/unit/test_tools_device.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_pending` | 1 | 1 | (d) | a map that grows per timed-out call is memory nothing reports |

**`tests/unit/test_tools_mcp.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_resolve` | 1 | 1 | (d) | the resolved value is kept on no object, which is the claim around it |

**`tests/unit/test_tools_mcp_prompts.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_discovered` | 1 | 1 | (d) | decisions taken against a server no cooperating one imitates |
| `_rendered` | 1 | 1 | (d) | what was never built |
| `_injectable` | 1 | 1 | (d) | a block one character past the cap |
| `_pages` | 1 | 1 | (d) | a listing this client cannot read |

**`tests/unit/test_capture.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_wav` | 2 | 2 | (d) | a file closed out from under the writer is the pod being killed |
| `_events` | 1 | 1 | (d) | the same descriptor |

**`tests/unit/test_build_info.py`**

| name | before | after | class | why |
| --- | --: | --: | --- | --- |
| `_GIT_TIMEOUT_S` | 1 | 0 | (b) | read to shape a fake exception nothing asserted |

Six reaches exist that did not before, all in the two support modules,
and all of them are the consolidation: `stamp_with` and `attached_taps`
and `turn_taking` and `plant_utterance` in
`tests/support/sessions.py`, and `stored_rows` and `planted` in
`tests/support/stores.py`. 156 + 6 is the 162 the after-walk finds.

### Deviations from the plan

Four, each with its reason.

1. **The before-numbers are not the plan's.** The plan says 445 sites
   over 82 names across 51 files; the fresh walk finds 440 over 85
   across 55. M1 through M4 are the difference, and so is the baseline
   harness, which did not exist when the plan was written and is the
   largest single file in the census. The plan asked for the walk to be
   re-run, and this is what it answered.

2. **(a) is empty.** The plan's four classes include "enduring
   invariants (kept)", and no surviving site is in it. Finding 8 is why:
   it permits a keep only for a white-box safety invariant with a stated
   justification, which is (d). A site that would have been (a) under
   the plan's first draft is (d) here, with the sentence written.

3. **`run_reply` and `drive_reply` stay white-box, and the plan
   anticipated it.** The brief allows the reply-driving cluster to
   survive as a documented keep if the runtime has no public way to
   inject an utterance and await the reply. It half has one:
   `start_reply(pcm, result)` plus `drain(grace_s)` is public and is now
   what `start_reply` in the support module calls. What it cannot do is
   the two things those sixty-odd tests need, and both are contract
   rather than oversight: `drain` answers that a reply finished and
   never how, because to the edge a reply that failed is a reply that
   finished; and the transcript a reply answers is whatever the
   configured ear made of the PCM. Adding a reply that reports its own
   failure, or one that takes a transcript from outside, would be
   production surface with no production caller.

4. **Two support helpers changed shape rather than only moving.**
   `Falsey` wraps a client instead of being an empty shell, and
   `ScriptedLlm` keeps the system prompt of every round. Both are
   additions to a fake rather than to production, and both exist because
   a rewrite needed an observation the fake was not making yet.
   `test_support_fakes.py`, which pins what the shared fakes promise,
   was updated in the same change and gained the second half of the
   premise its users now depend on.

### Discoveries

**No production interface was added.** The plan's rule was that a new
one needs a non-test caller or it is not added, and the count is zero.
Every rewrite went through a name that already existed for a production
reason: `SessionInput.audio`, `ReplyControl.start_reply`,
`SessionInput.drain`, `SessionEvents.attach`, the composition root's
`providers` parameter, the providers' `client=`, `VadProvider`, and the
`tts sentence_start` message the device already receives.

**The seam a rewrite finds is often stronger than the read it
replaced.** The barge-in history assertion is the clearest: `_turns`
said what the list held, and `ScriptedLlm.seen` says what the next round
was written against, which is the reason the list exists. The round
after a barge-in seeing no assistant turn IS the claim that the cut
sentence never became history, and it is the same claim from the side
that consumes it.

**A public read is not always a stronger read.** Two rewrites had to be
strengthened to stay honest. A sentence is announced to the device
before its audio, so `spoken()` alone would have passed where the old
history read failed; the filler suite asserts the `replied` event
beside it, which counts only sentences whose audio went out. And the
ASR minimum test now drives both endpoints rather than reading a field
on each.

**The database's stored form is a compatibility surface in its own
right.** The twenty-eight engine reads looked like duplication of the
public reads beside them, and they are not: a value kept verbatim and
one this build encodes and decodes are identical through the accessor,
and the column is what a migration, a backup restored under another
build and the upgrade path this project supports all read. That is why
they are (d) and not (c), and M7's ADR is about the same fact.

**Consolidating a reach is not rewriting it.** Sixty-nine of the
baseline harness's sites became fourteen without a single claim
changing: they moved onto four named helpers whose justification is
written once. The table counts those before-sites as (d), not (c),
because what the sweep bought there is legibility rather than a public
route, and calling it (c) would overstate the rewriting by a factor of
two.

### Behavior safety

No production code changed. The diff touches `tests/` and this document
and nothing else, which `git diff --stat` says: no path under
`vinga-server/src/` appears in any commit of this milestone.

Six rewrites were proved by mutation rather than trusted, chosen as the
ones whose claim moved furthest from the read it replaced:

1. **The falsey anthropic client.** Rewriting the constructor from
   `client is not None` to `client` turns the rewritten test red: the
   provider builds a real client and the driven round never reaches the
   double.
2. **The falsey OpenAI TTS client.** The same mutation, the same result.
3. **The barge-in history.** Appending a sentence to the spoken list
   before its audio rather than after, which is what putting a cut
   sentence into the history looks like, turns
   `test_a_confirmed_barge_in_reuses_the_transcript_and_the_lock` red
   through `ScriptedLlm.seen`.
4. **The tool snapshot.** Offering `switch_agent` to a device bound to
   one agent turns `test_switch_agent_is_offered_only_where_there_is_somewhere_to_go`
   red through `seen[0][1]`.
5. **The filler suite's "the reply arrived".** Sending a spoken sentence
   to a list nobody reads, which is what losing a reply's own record
   looks like, turns six of the rewritten tests red, and would have been
   invisible to the announcement alone. This is the mutation that made
   the `replied` assertion necessary.
6. **The history helper.** Dropping the leg's assistant turn in
   `_speak_reply` turns `test_the_old_agents_words_stay_its_own_turn`
   red through `history()`.

The committed record baseline is the seventh proof and covers the whole
harness: `vinga-server/tests/unit/data/event-baseline.json` was
regenerated after the harness was rewritten and is byte-identical, which
is what says no driver drives anything different.

### Verification

Run from `vinga-server/`, at the last commit of the milestone.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files. (The lane
  is scoped to the events package; nothing in this milestone is in it.)
- `uv run pytest tests/unit -q`: 2,640 passed, 16 skipped. (2,639 at the
  end of M4's review round; the one added is the second half of the
  falsey probe's pin.)
- `uv run pytest tests/integration -q`: 60 passed.
- The four documentation drift checks, regenerated and diffed against
  `../docs/reference/`: `config reference`, `conversations schema`,
  `events reference` and `config openapi` are all clean, and no commit
  in this milestone touches any of the four files.
- The record baseline is byte-identical, regenerated and diffed after
  the harness was rewritten.
- The after-walk is the before-walk's own code:
  `uv run python -m tests.tools.reach_ins` reports 167 sites over 77
  names across 48 files, against 440 over 85 across 55. (The review
  round below re-runs it at 162 over 75 across 46.)

Not verified here, and not claimed: the container image and the smoke
lane, for the reason M1 gives.

### PR review round (2026-08-20)

External review of PR #224, read-only against commit `8655b19c`.
Verdict: mergeable after fixes. Four P2 findings, all adopted, each
with the commit that made it.

**1 (P2). A rewritten wait walked past a reply's failure.**
`wait_for_reply` waited through `drain()` alone, and `drain`'s contract
is that a reply which failed is a reply which finished: it answers True
for a task holding an exception, deliberately, because that is what the
edge needs to know. Every site that used to await the task itself
stopped seeing a failure that arrived late.

*Resolution.* Adopted (`f93360ec`). `drain` is the bound and nothing
else now: the task is held before the wait, because a barge-in landing
in between replaces it, and awaited after, which is the line that
raises. All thirteen callers want that, since each awaited the task
itself before this milestone. Proved by mutation: a raise at the end of
the reply's own `finally` turns three filler tests red and passed
silently before.

**2 (P2). The barge-in histories stopped proving the finished turn.**
The three rewrites say what the second model round was handed and what
the device was announced, which between them cover the utterances and
the sentences and not the completed assistant turn. Deleting the append
at the end of the reply would have left all three green.

*Resolution.* Adopted (`d83e5bee`). Each drives a probe round through
`history()` and holds the turns that round was handed against the whole
conversation, the assistant turn included. The device-facing half stays,
because the two say different things: one is what the user heard, the
other is what whoever answers next is written against. Proved by
mutation: dropping the assistant append turns all three red, and turned
none of them red before.

**3 (P2). The prompt tests compared lengths where they had compared
text.** Losing the reach-ins cost them their exact comparisons: a
length, a prefix and two substrings agree about prompts that differ, so
interior whitespace could drift and a total could be a separator short
with all of it green.

*Resolution.* Adopted (`1b3dd921`). The exact value was available from
the module that owns the question: `runtime.prompt.know_how` is public
and is the call the activation makes, so the tests ask it what this
agent's half is and hold the recorded model prompt, the event's
character count and its per-block sizes against that text, that total
and those sizes. What is compared is the session's claim rather than a
second spelling of the joining rule, which is `test_runtime_prompt.py`'s
question. Proved by mutation: reporting the sum of the block sizes as
the total, which is the separator-short count the old arithmetic was
built from, turns three tests red.

**4 (P2). Two survivors failed the plan's own rule.** A reach-in
survives only where public observation cannot establish the property,
and neither of these met it.

*Resolution.* Adopted (`c657578f`), and both are reclassified from (d)
to (c) in the table above. The session cap was guarded by reading the
number off the registry, with a note claiming a configured cap is
observable only by opening that many sessions plus one; the file opens
sessions, and proves the cap of one three tests up. So the configured
three is driven the same way, three conversations and a fourth turned
away. Proved by mutation: a registry that ignores its argument and caps
at one turns this test and its two neighbours red. And the prompt
identity claimed nothing reports the cached half's text, which
`know_how` does, while `CountingServers.asked` answers whether it was
rebuilt, since a rebuild is what asks; both fakes were already in the
test.

A third went with them, unasked but by the same argument: the refused
agent switch in `test_session.py` read the cached half to say nothing
was swapped, under a note claiming the half has no reader. It has one,
and the same one, so the test drives a round and holds what the model
was sent against `know_how`. Proved by mutation: setting the active
agent before the bound-list check turns it red. Leaving it would have
been a table row asserting, one screen from this record, exactly the
claim the record retires.

The design guide named the second one as its worked example of a
reach-in with a stated reason. Its paragraph now says what the sweep
found, because a rule whose example no longer exists is worse than no
example, and the lesson that survives is the rule's own: a stated
reason is the flag answered rather than an exemption from it, and an
answer can turn out to be wrong later, which is what re-asking the
question across a whole suite is for.

### The inventory, after the review round

The walk, re-run rather than adjusted by hand: **162 sites over 75
names across 46 files**, against 167 over 77 across 48 at the end of
the sweep and 440 over 85 across 55 before it. `test_registry.py` and
`test_session_prompt.py` now hold none at all.

The dispositions move with them: **(a) 0, (b) 10, (c) 138, (d) 292**,
still summing to 440. Five sites left (d) for (c), which is the
finding's whole point: (d) is a claim about what cannot be observed,
and a claim like that is falsifiable.

### Verification, after the review round

Run from `vinga-server/`, at the last commit of the round.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files.
- `uv run pytest tests/unit -q`: 2,640 passed, 16 skipped (2,640 at the
  end of the sweep; the round rewrote tests and added none).
- `uv run pytest tests/integration -q`: 60 passed.
- The four documentation drift checks: all clean, and no commit in this
  round touches any of the four files.
- The record baseline is untouched: no commit in this round names
  `vinga-server/tests/unit/data/event-baseline.json`.
- Production source is still untouched: `git diff --stat origin/main --
  vinga-server/src` is empty across the whole branch.

## M7: the compatibility floor ADR

### What was done

Doc-only, as planned. The record is
[`docs/adr/2026-08-20-database-upgrades-have-a-compatibility-floor.md`](../adr/2026-08-20-database-upgrades-have-a-compatibility-floor.md):
upgrades supported from the first beta image onward; best-effort from
revision `0001` until a beta is declared, which the existing upgrade
tests already prove; history never rewritten as a cleanup, with
squashing defined as a compatibility decision requiring a superseding
record and a tested reset path; every schema change a reviewed
migration. The follow-up audit the plan and the review both name
(classifying the other compatibility branches: old MCP grant forms,
previously accepted provider URL shapes, legacy-name recovery) is
filed as issue #225 and scoped out of this plan.

The principles pointer question resolved the other way in review:
this section first declined it as an operational promise, and the PR
round judged the beta guarantee externally falsifiable and therefore
a product promise by the page's own definition, which is precisely
the conditional the plan's M7 wrote. `principles.md` now carries "A
beta database is never left behind" citing the ADR, added by the
round's fix.

### PR review round (2026-08-20)

codex-cli 0.147.0, gpt-5.6-terra, against aa1fae9c. One finding.

**1 (P2). The operator-facing upgrade guarantee is missing from the
principles index.** The ADR's beta promise is externally falsifiable
and meets the page's definition of a product promise; the plan's M7
required the pointer exactly when review judged that condition true.

*Resolution.* Adopted. The product-promise entry above was added and
this section's earlier reasoning corrected rather than erased.

### Deviations

None. The milestone is the ADR, the filed issue, and this record.
