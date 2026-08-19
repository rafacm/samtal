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
