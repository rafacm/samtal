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

`EntityDescriptor` carries **19 dataclass fields**, against 31 before:
20 data facts, `secret_key`, and the `endpoints` tuple went, so the 11
hooks plus `Endpoint` are the twelve that left. `fields_in_help` was a
`ClassVar` on both tiers and is gone as well.

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
