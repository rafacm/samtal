# Simplify the governance surfaces: typed events, a narrowed registry, tests at interfaces

## Goal

Implement issue #210: the external architecture review found selective
overengineering in the governance-heavy parts of the server, and this
plan is the focused simplification design the issue asks for. The
event contract keeps its promises with one typed declaration per event
instead of five structures that must agree; the configuration entity
registry shrinks to an immutable data spec while its behavior hooks
dissolve into typed code local to each consumer; the tests that pin
private implementation topology are classified and rewritten, kept, or
deleted by class; and the database compatibility floor is written down
as a decision rather than an assumption.

Nothing here changes what the server says or serves. The
metadata-only telemetry promise, the event identifiers and fields
operators consume, the HTTP API's OpenAPI document, and the CLI's
rendered output are held byte-identical or golden-verified through
every milestone. The `Emission` interface and `LogTap` are
preserved: every typed variant derives its logging specification,
the unrendered template and ordered args, from its own fields, so
the record a tap or a log reader sees is baseline-proven identical.
The one deliberate exception is the generated event reference,
whose format may change when its source of truth becomes the
catalog; it is regenerated, drift-checked as today, and held to a
semantic-completeness test (every event, variant, field, token,
bound, and note present), not to byte identity.

The companion implementation doc,
[`2026-08-19-governance-simplification-implementation.md`](2026-08-19-governance-simplification-implementation.md),
records what each milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by issue #210 and not re-litigated here:

1. **The metadata-only telemetry guarantee is preserved.** The
   simplification target is the implementation shape, never the
   contract. The `EventTap` seam stays.
2. **One typed event declaration owns everything about an event**:
   its code, its fields, its severity, and its rendering inputs.
   Callers provide domain inputs; they do not repeat the event name,
   level, message template, argument order, or field set.
3. **Event identifiers and typed fields are the compatibility
   contract; human-readable messages are presentation** unless a real
   external consumer parses them. The consumer survey (below) found
   none, so exact prose pins do not survive this plan.
4. **The AST reconciliation suite, the `PINNED_BY` sidecar, the
   historical migration guards, and the exact prose pins are removed
   once divergence is structurally impossible**, and not before.
5. **The entity registry keeps a small immutable specification** for
   facts truly shared across surfaces; row conversion, persistence
   policy, and write checks move into typed adapters local to the
   store; HTTP behavior moves into explicit `APIRouter` code; CLI
   behavior stays in explicit commands; import-time mutation and
   `Callable[..., object]` hooks are removed or narrowly justified.
6. **OpenAPI is the frontend client seam**, with client generation
   evaluated by a small spike with explicit acceptance criteria.
   No generic CRUD framework, no SQLModel.
7. **Tests are classified** as enduring invariants (kept), historical
   guards (deleted), caller-visible behavior reached through private
   state (rewritten through public interfaces or observable
   adapters), or white-box safety invariants (kept with a documented
   justification).
8. **Alembic and the four configuration migrations stay.** The
   compatibility floor is documented explicitly; no history is
   squashed or pruned.
9. **The preserve list is out of bounds**: `device/boundary.py`,
   `tools/source.py`, `runtime/turntaking.py`,
   `runtime/filler_runner.py`, `composition.py`, the small
   invariant-hiding modules, and the conversation store's writer and
   retention guarantees are not redesigned here.
10. **Milestones are independently reviewable**; the event work and
    the configuration work are never combined in one PR.

## The before-state, inventoried by tooling

Verified against main at the plan's base commit by AST walks and
line counts, not memory. These numbers are the "before" half of the
issue's before-and-after inventory criterion; the implementation doc
records the "after" half as milestones land.

**The event surface.** 58 events (57 production plus the internal
`schema_violation`), 99 variants, 81 emit sites across 21 modules,
14 channels. Production machinery is 4,287 lines
(`events_schema.py` 2,386, `events.py` 1,255, `events_docgen.py` 505,
`events_cli.py` 141). Event-focused tests are 10,757 lines across 12
unit files and 2 support modules. The structures that must agree
today, per event: the `EventSpec`/`EventVariant` declaration, the
emit site's spelled-out template, positional args, `event=` name and
keyword fields, the `PINNED_BY` sidecar entry (81 identities mapped
to pytest node IDs), the `TOKEN_SOURCES` sidecar (22 entries),
`SPREAD_INVENTORY` (10) and `CALL_ALTERNATIVES` (9), and the prose
pins in three pin suites (roughly 76 pin expectations restating
templates, args, and field sets the registry already declares). The
duplication-versus-privacy classification of every event test file is
recorded in the milestone descriptions below and drove the cut lines.

**The entity registry.** `config/entities.py` is 990 lines declaring
five commanded kinds, two nested shapes, and two settings.
`EntityDescriptor` carries 32 fields: 20 pure data facts, one
data-shaped predicate (`secret_key`), and 11 `Hook =
Callable[..., object] | None` behavior fields (`from_row`, `to_row`,
`before_parse`, `inside_write`, `read`, `write`, `delete`, `body`,
`summary`, `wrote`, `deleted`). Four modules install 44 hook values
plus five `notice` strings at import time through
`fill()`/`object.__setattr__` (`store.py` 16 invocations, `views.py`
5, `writes.py` 5, `cli.py` 5). `api.py` synthesizes handlers, names,
docstrings, and signatures from descriptors (`_handler` sets
`__name__`, `__doc__`, `__signature__`). Four of the eleven hooks
(`from_row`, `to_row`, `before_parse`, `inside_write`) are filled by
`store.py` and consumed only by `store.py`: a module talking to
itself through a global registry. `fields_in_help` has zero
consumers. `docgen.py` reads only data facts, which is the constraint
the whole import-time-fill design exists to satisfy.

**Test reach-ins.** 445 external reach-in sites over 82 distinct
private names across 51 files (tokenizer walk, `self._x` inside
test-local fakes excluded). Half the sites sit in six files
(`test_event_surface_pins.py` 79, `test_session_filler.py` 42,
`test_session_barge_in.py` 37, `test_session_record.py` 23,
`test_session_tools.py` 23, `test_config_store.py` 18). 22 sites live
in `tests/support/sessions.py`, through which more than 220 test
invocations route, so that one file is the highest-leverage fix.
Existing public seams already cover most rewrites: `ScriptedLlm.seen`
for `_turns` and `_tool_snapshot`, `RecordingLlm.systems` for
`_know_how`, the `DeviceOutput` protocol for `_speak`/`_speak_reply`
observation, `tests/support/wire.py` for reply driving, the providers'
public `client=` parameter for the 29 `_client` sites, and
`SessionEvents`' public `clock` parameter for the clock reach-ins.

**Migrations.** Four configuration revisions (`0001` baseline,
`0002`/`0003`/`0004` additive) and one conversations baseline. The
deployment model publishes images and documents persistent volumes,
so pre-release does not mean disposable data.

## The issue's open questions, resolved

**Which exact event pins remain, and who consumes them?** None remain
as prose. The consumer survey found three production `EventTap`
consumers (`LogTap`, `CaptureTap`, the conversation store's
`SessionSink`), all of which read structured payloads and none of
which parse sentences; the generated reference and README index are
rendered from declarations; the server-tap hub has no production
consumer yet. The compatibility contract that replaces the pins is a
committed golden inventory: for every event, its name, its variants'
channels and levels, and its field names with their declared types,
their requiredness, and their nullability, asserted against the
catalog in both directions so a rename or a field or presence change
is a loud diff on a reviewable file. Absence and null stay distinct
facts: a conditionally absent field (today's `heard.language`)
becomes a separate variant or an explicit typed absence, never a
bare `| None` whose serialization is left to guesswork. Sentences remain tested where they
carry a promise (the no-leak sentinel suites assert what prose must
NOT contain), never for wording.

**Typed events as pydantic models or frozen dataclasses?** Frozen
dataclasses, for three reasons. First, the closed field set and the
token sets move into the type system itself (fields are the dataclass
fields; tokens become `StrEnum`s; bounded device descriptors become a
small value type that applies its bounds at construction), so most of
today's 17-fault runtime matrix becomes unrepresentable rather than
checked. Second, the one consumer that needs schema-shaped output is
the documentation generator, which introspects dataclass fields the
same way it introspects the registry today; no runtime JSON Schema is
needed. Third, events sit on hot paths and pydantic validation buys
nothing here that construction-site typing does not. The forgiving
production posture survives as one narrow wrapper: emit-side factory
construction runs under a guard that converts any construction error
into the declared `schema_violation` event, so a telemetry bug still
cannot take down a reply.

**How do 58 declarations avoid becoming a new 2,386-line wall?** The
catalog declares an event as one declaration per event code holding
a discriminated set of typed variants, because 99 variants exist and
at least `session_rejected` crosses two channels: each variant is a
frozen dataclass owning its channel, level, exact payload shape, and
rendering inputs, and a single-variant event declares exactly one.
Callers construct a specific variant; documentation and the golden
inventory derive from the enclosing declaration. The template and
the argument order stop existing as caller-side structures because
each variant derives its logging specification, the unrendered
template and ordered args `Emission` carries today, from its own
fields; `Emission` and `LogTap` are unchanged. The documentation
facts the reference renders beyond fields (variant notes, syntaxes,
bounds, token sets) are declaration metadata on the variant and the
vocabulary, not lost to introspection. The shared vocabulary
(token enums, syntaxes, the descriptor bound type) lives beside it
once. The expectation, stated so the review can hold the plan to it:
catalog plus vocabulary plus emitters plus docgen land at roughly half
of today's 4,287 production lines, and the sidecars do not exist at
all.

**What replaces the enforcement matrix?** Types replace most of it.
What must remain at runtime, because types cannot carry it: the
descriptor bounds (applied where the value is constructed, exactly as
today's four decision sites do), and the forgiving wrapper above. The
strict/forgiving mode machinery shrinks to that wrapper; the
17-fault taxonomy, two-step judging, variant matching, and recovery
rebuild go with the generic emitter that needed them.

**Where does the compatibility floor live?** In a new ADR, because it
is a hard-to-reverse product-facing decision: database upgrades are
supported from the first beta image onward; until a beta is declared,
a deployment that tracks `latest` is supported best-effort from
revision `0001` forward, which the existing upgrade tests already
prove; history is never rewritten, and squashing would be a new ADR
with a reset path, not a cleanup. The ADR also names the follow-up
audit the issue suggests (classifying the other compatibility
branches: old MCP grant forms, previously accepted provider URLs,
legacy-name recovery) as an issue to file, out of this plan's scope.

**What happens to #194, #193, #191?** They stay valid and resume
after this plan on the narrowed registry: #194's Typer layer wraps
explicit CLI commands instead of hook dispatch tables, and
#193/#191 sit on explicit routers. Nothing here changes their scope;
this order exists so their foundations are not rebuilt under them.

## Design decisions this plan makes

**The events module becomes a package that keeps its name.** The
pattern proven by #140 and #143: `vinga_server/events/` with
`__init__` preserving today's public names (`SessionEvents`,
`ServerEvents`, `EventTap`, `Emission`, `LogTap`, `CaptureTap`, the
hub functions), so no import site outside the package moves. Inside:
`values.py` (token enums, syntaxes, the bounded descriptor type),
`catalog.py` (the 58 typed declarations, grouped by area, each with
its `render()`), and the emitters in `__init__`. `events_docgen.py`
and `events_cli.py` keep their homes and switch their source of truth
to the catalog. The 14 channel names are logger names and therefore
operator-facing compatibility facts; they are data on the
declarations and pinned by the golden inventory, so the package move
cannot shift them.

**What a caller looks like after the change.** Today's 15-line
emit site (template, six positionals, name, nine keywords) becomes a
typed construction deferred into the emitter's guard:

    self._events.emit(lambda: LlmRound(
        session=self.session_id, agent=self._agent,
        round=self._llm_round, turns=len(working),
        duration_ms=round(elapsed * 1000), ...))

The thunk matters: construction, the value types' runtime validation
(token coercion, descriptor bounds), rendering, and serialization
all run inside the guarded boundary, so a construction failure on a
reply path is telemetry's problem and never the reply's. In strict
mode the guard re-raises; in forgiving mode it emits a fresh, safe
`schema_violation` built from registry-owned identifiers only, never
from caller-controlled names, values, exception messages, or
partially rendered text, which is today's recovery posture kept.
Because the repository runs no static type checker, the annotations
alone prove nothing at runtime; M1 therefore adds a strict type-check
CI step scoped to the events package (widening later is its own
decision), and the value types keep explicit runtime validation of
every untrusted input regardless. The emitter reads channel and
level off the constructed variant, renders the sentence through the
variant's own derivation, and hands the payload to the taps. The
interface a caller must know shrinks from "the whole registry entry,
restated correctly" to "the one variant named after the thing that
happened".

**The descriptor sheds behavior, keeps facts.** `EntityDescriptor`
keeps its 20 data facts, `secret_key`, and loses all 11 hook fields,
`fill()`, and the `object.__setattr__` site; `fields_in_help` is
deleted as unconsumed. Where each hook's behavior lands, by its
fill/consume shape: the four store-internal hooks (`from_row`,
`to_row`, `before_parse`, `inside_write`) become a private typed
per-kind table inside `store.py`, since the store both installs and
consumes them today. The three verb hooks (`read`, `write`, `delete`)
dissolve entirely: `ConfigStore` already exposes typed public
read, write, and delete methods per kind, so the explicit routes and
CLI commands call those directly, and no `EntityAccess` layer is
added, since it would only forward arguments to names that exist
(the deletion test, applied by the review to this plan's own first
draft). `body` becomes a
direct call into `views.py` (which already owns the derivation).
`summary` becomes plain functions in `cli.py` called from the summary
tree. `wrote` and `deleted` become typed functions exported by
`writes.py` and called directly. `notice` stays a descriptor fact:
it is effect-timing information, static and data-only, and the
issue's settled decision keeps effect timing in the immutable spec;
consumers keep rendering acknowledgement text from it, and it stops
needing `fill()` because it is declared inline like every other data
fact. After this, understanding one entity means reading
the registry entry for its facts and the consumer for its behavior,
with no import-order coupling; `docgen.py` is unchanged because it
never read a hook.

**Routes become explicit, and the OpenAPI document proves it.** The
`Endpoint` tuples, `_entity_routes`, `_handler`'s
`__name__`/`__doc__`/`__signature__` synthesis, `_parameters`, and
`_path` are replaced by explicit route registrations per entity, each
carrying its `operation_id`, summary, description, response model,
and problem statuses as literals. The committed
`docs/reference/api-openapi.json` is regenerated and must be
byte-identical; that diff, already enforced by CI and
`test_api_openapi.py`, is the whole proof that the synthesis and the
explicit spelling describe the same API. The `Endpoint` data leaves
the descriptor because `api.py` was its only consumer.

**Explicit repetition is accepted, and bounded.** Five entities'
routes spelled out will repeat a shape. That repetition is the price
the issue prices in ("a generic CRUD abstraction would probably move
those rules into a larger hook framework"), and it stays bounded by a
narrow typed route helper only if the explicit spelling turns out to
share more than registration boilerplate; the helper is permitted by
the issue, not required, and the milestone decides with the diff in
front of it. What is not permitted is a new `Callable[..., object]`.

## Milestones

Every merge leaves `main` releasable: the image publishes on every
push, so each milestone ends with both suites, lint, and the four
drift checks green, and any milestone that touches the event surface
carries its baseline proof inside its own PR.

- [x] **[M1: the typed event foundation inside the package move](2026-08-19-governance-simplification-implementation.md#m1-the-typed-event-foundation-inside-the-package-move).** (PR #217)
  First, the mechanical move: `events.py` becomes
  `events/__init__.py` whole, no line changed beyond the move,
  proven by the still-alive pin suites and conformance walk passing
  byte-unchanged, because a package added beside the module would
  shadow it (review finding 1). Then, beside it in the same package:
  `events/values.py` and `events/catalog.py` with the vocabulary
  and a first area of declarations (the conversations-store events,
  the smallest channel: 5 paths), the emitter extension that accepts
  typed construction, the forgiving construction guard, the golden
  inventory harness (catalog to committed inventory, both
  directions), and the record-baseline harness: a script that
  captures (channel, levelno, msg template, arg types, payload keys)
  for every emit path before and after a conversion, the #143 wire
  baseline pattern applied to log records. The baseline's path list
  is not self-claimed: it is generated from the conformance suite's
  static site inventory while that inventory still exists, so every
  statically known path must produce a record or the harness fails
  (review finding 10). The static inventory and this obligation
  survive until the last conversion lands; after M3, exhaustiveness
  is claimed over the catalog's legal variants, every one of which
  is constructible and therefore directly testable, and never over
  arbitrary call sites. The five converted sites
  lose their entries in `PINNED_BY` and their prose pins
  (`test_conversations_event_pins.py` retires; its five paths enter
  the golden inventory; its exhaustiveness claim moves to the
  baseline). Old machinery is untouched for every unconverted path.
  Design footprint: deepens `events` into a package whose callers
  stop knowing templates, argument order, and field sets; adds the
  catalog as the one home of "what may telemetry say". Deletion test:
  inlining the catalog into the emitters would put 58 declarations
  back inside dispatch code, and inlining the emitters into callers
  would spread tap dispatch over 21 modules.
- [x] **[M2: convert the session channel](2026-08-19-governance-simplification-implementation.md#m2-convert-the-session-channel).** (PR #218)
  All session-scope emit
  sites (the pipeline, the device session, turn-taking, the filler
  runner) construct typed events;
  their declarations move into the catalog; their `PINNED_BY` and
  `TOKEN_SOURCES` entries are deleted; the prose pins of
  `test_event_surface_pins.py` retire in favor of the golden
  inventory plus the baseline diff, while its 12 no-leak tests and
  the `assert_unnamed` family are rewritten against the typed path
  and kept. The baseline runs pre- and post-conversion inside the PR
  and is committed with the implementation doc section.
- [x] **[M3: convert the server channels and delete the
  reconciliation apparatus](2026-08-19-governance-simplification-implementation.md#m3-convert-the-server-channels-and-delete-the-reconciliation-apparatus).** (PR #220) The remaining server-scope sites
  convert; `events_schema.py`'s registry, the enforcement matrix,
  and the recovery machinery reduce to the catalog, the construction
  guard, and the descriptor bounds; `test_event_schema_conformance.py`
  with its three sidecars is deleted; `test_server_event_pins.py`'s
  prose pins retire, its no-leak tests are rewritten and kept;
  `test_event_surface_guard.py` keeps the single-emitter `extra=`
  rule and its planted-source self-tests, and deletes the
  `_echo_event` and `device.events` historical guards;
  `test_event_enforcement.py` shrinks to the construction-guard and
  mode-resolution behavior that still exists; the sentinel and
  descriptor-sanitization suites are rewritten against the typed
  path and kept whole; the three registry importers outside the
  event suites migrate by name: `tests/support/schema.py`'s
  `scratch_registry` seam becomes a scratch-catalog seam,
  `test_events.py`'s synthetic emissions construct through the new
  public declaration interface, and `test_conversations_session.py`
  derives its permitted stored-field surface from the catalog or the
  golden rather than recreating a second registry;
  `events_docgen.py` renders from the catalog and
  `docs/reference/events.md` regenerates under the existing CI
  drift step, held to the semantic-completeness test the goal
  section states. After this milestone the structures that must
  agree per event are one declaration and one golden line.
- [ ] **M4: the entity registry sheds its hooks.** As decided above:
  store-internal hooks inlined, routes and commands calling the
  store's existing typed methods directly, `body` and
  `summary` and `wrote`/`deleted` called directly, `notice` declared
  inline as the descriptor fact it is, explicit
  routes with the byte-identical OpenAPI proof, `fill()` and
  `object.__setattr__` deleted, `Endpoint` moved out of the
  descriptor, `fields_in_help` deleted. The registry's own suite
  (`test_config_entities.py`) keeps every data-fact relation and
  loses its hook expectations; the response-byte, CLI-rendering,
  acknowledgement, and reference-doc suites pass unmodified, which is
  the no-behavior-change contract. Design footprint: deepens
  `config/store.py` (it stops publishing its internals through a
  global registry) and restores locality to `api.py` and `cli.py`;
  the descriptor becomes what its docstring already claims to be,
  the home of facts a model cannot carry.
- [ ] **M5: the OpenAPI client spike.** Run Hey API's openapi-ts and
  openapi-typescript with openapi-fetch against the committed
  document, generator versions pinned and outputs required to be
  deterministic with no manual edits. Acceptance criteria, recorded
  with results in the implementation doc: the generated client
  compiles under strict TypeScript; every operation surfaces under
  its stable `operation_id` name; request and response types for the
  five entities need no handwritten mirror; nothing in the output
  leaks server internals beyond the document; and a strict-mode
  consumer fixture, checked in with the spike, exercises
  authentication, representative read, write, and delete operations
  for all five entities, typed non-2xx problem responses, optional
  and nullable field handling, and the provider entries' extension
  properties, because a client that merely compiles can still be
  unusable at exactly those edges. Deliverable is a decision
  and a recorded recommendation for #129, not frontend wiring; if
  both generators fail the criteria, the recorded fallback is
  handwritten types over the document, and the seam claim still
  holds because the document is CI-drift-checked either way.
- [ ] **M6: the reach-in sweep.** `tests/support/sessions.py` first:
  its 22 sites route 220+ invocations, and each helper either moves
  to an existing public seam (the wire, the boundary, the providers'
  recording fakes, `SessionEvents`' public `clock` and `attach`) or
  keeps its reach-in with a stated white-box justification in its
  docstring. Then the six concentrated files: (b)-class historical
  guards deleted, (c)-class assertions rerouted through the seams
  the inventory mapped (`ScriptedLlm.seen`, `RecordingLlm.systems`,
  `DeviceOutput` observation, `TestClient` wire driving), (d)-class
  timing and lifecycle tests kept with their justification written
  where the reach-in happens (utterance planting, pace clocks, drain
  ownership, store thread joins, pre-migration row planting). The
  classification is complete, not sampled: every one of the
  inventoried sites, across all 51 files and 82 names, receives a
  recorded disposition (behavioral rewrite, characterization
  retirement, documented white-box safety invariant, or obsolete
  guard deletion) in the implementation doc's inventory table, and
  a reach-in survives only as a white-box safety invariant whose
  justification states which property cannot be established through
  public observation; a docstring alone is not a license. The
  after-inventory reruns the same tokenizer walk and is recorded
  beside the before numbers. No new production interface is added
  unless a rewrite genuinely needs one, and each such addition names
  its non-test caller or is dropped.
- [ ] **M7: the compatibility floor ADR.** The ADR stated above,
  plus the pointer from `principles.md` if the review judges it a
  product promise, and the follow-up audit issue filed. Doc-only.

M1 through M3 and M4 stack in that order; M5 and M7 are independent
and small; M6 lands last because M2/M3 already delete or rewrite the
largest reach-in file and M4 touches the store tests.

## Test strategy

Reused assets: the golden inventory and baseline harness are new;
everything else reuses the suites named above, kept, shrunk, or
rewritten in place. The no-leak sentinel model
(credential-shaped value planted, asserted absent from sentences,
args, fields, records in both formats, and attached taps) is the
acceptance test for the privacy criterion and runs unchanged in
spirit against the typed path. The behavioral event suites
(`test_session_events.py` and friends) keep asserting which events
fire and with what values, through `caplog` and taps, exactly as now.

## The standing review lenses, pre-answered

**No-leak.** The typed path narrows the leak surface: fields are
declared types, tokens are enums, descriptors are bounded at
construction, and the construction guard's failure event carries only
registry-owned identifiers, as today's recovery does. The sentinel
suites are kept whole and rewritten, never weakened; M2 and M3 list
them by name in their briefs.

**Pin before reshaping.** The record baseline is captured green
before each conversion milestone and compared after, inside the same
PR; the golden inventory is committed before the first prose pin
retires. No pin is deleted before its replacement exists in the same
diff.

**Closed sets mapped to decision sites.** Token enums are constructed
at the same decision sites that choose tokens today; the M2/M3 briefs
carry the rule that a token value is chosen by exception type or
explicit branch, never message text. The descriptor bounds stay at
their four decision sites.

**Honest seams.** Optional injections compare `is not None`; the
construction guard's behavior gets its own pins since callers cannot
prove it.

**Inventories by tooling.** Every count above names its instrument
(AST walk, tokenizer walk, line counts); the implementation doc
refreshes them after each rebase; the after-inventories rerun the
same scripts, which are committed to the scratchpad and referenced
from the implementation doc.

## Risks and mitigations

- **The conversion milestones are wide.** 81 sites across 21 modules.
  Mitigation: the per-channel split bounds each PR; the baseline
  harness makes each conversion mechanical to verify; a site that
  resists typing (a spread whose keys vary) is converted with an
  explicit variant type per shape, which is what the variant
  structure already declares.
- **Deleting the conformance suite deletes real coverage along with
  duplication.** Mitigation: the classification is explicit; claim 4's
  registry-coherence checks become type-system facts or golden
  checks; claims 1 through 3 exist only to reconcile duplication that
  no longer exists. The privacy files are never deleted, only
  rewritten.
- **The explicit routes drift from the document.** They cannot
  silently: the OpenAPI byte-diff is in CI and in the suite, and it
  is the merge gate for M4.
- **The golden inventory ossifies wording.** It contains no wording:
  names, channels, levels, field names, types, requiredness and
  nullability only. Sentences are
  free to improve, which is the issue's stated intent while
  pre-release.
- **M6 tempts interface invention.** The rule in its brief: reroute
  through seams that exist; a new interface needs a non-test caller
  or it is not added, per the design guide's test-surface rule.

## Acceptance criteria, mapped

Each issue criterion, and where this plan answers it: modules
deepened and interfaces reduced are named per milestone (design
footprints); affected promises are the metadata-only telemetry
guarantee and operator observability, both held by the golden
inventory, the baseline proofs, and the kept privacy suites; the
preservation tests for privacy and observability are the sentinel,
sanitization, tap-isolation, and serialization suites plus the
golden inventory; one typed declaration owning code, fields,
severity, and rendering is M1's foundation; the pin survey and its
"none remain, no consumer parses prose" answer is recorded above;
configuration behavior consistency across storage, API, CLI,
frontend, and docs is held by the byte-identity proofs in M4;
client generation is M5 with its criteria; import-order mutation and
`Callable[..., object]` hooks are removed by M4 (the store-internal
table is typed and private, not a hook surface); the before
inventories are in this plan and the after inventories land in the
implementation doc; behavioral tests through public interfaces with
documented white-box exceptions are M6's rule; the compatibility
floor and reset policy are M7's ADR, documented with no pruning; and
the milestone split above keeps every step independently reviewable.

## Plan review round (2026-08-19)

External review: codex-cli 0.147.0, model gpt-5.6-sol, read-only
against commit ae18510a. Verdict: ready after the P1/P2 amendments.
Findings condensed but faithful; each carries its resolution.

**1 (P1). M1 cannot add an `events` package beside `events.py`.**
Python resolves `vinga_server.events` to the package, making the
module unreachable; the plan claimed the old machinery stays
untouched while adding the package.

*Resolution.* Adopted. M1 now opens with the mechanical move of
`events.py` into `events/__init__.py`, no line changed beyond the
move, proven by the still-alive pin suites and conformance walk
passing byte-unchanged; the catalog lands beside it inside the
package, never beside the module.

**2 (P1). One class-level channel and level cannot represent the
contract.** 99 variants exist and at least `session_rejected`
crosses two channels; the plan's single-channel dataclass model
contradicts its own risk section.

*Resolution.* Adopted. The catalog model is now one declaration per
event code holding a discriminated set of typed variants, each
variant a frozen dataclass owning its channel, level, payload shape,
and rendering inputs; callers construct a specific variant, and the
golden inventory and documentation derive from the enclosing
declaration.

**3 (P1). Typed construction happens before the proposed guard.**
`emit(LlmRound(...))` evaluates the constructor outside the guarded
boundary, so a construction failure escapes on a reply path; frozen
dataclasses do not enforce annotations at runtime and the repo runs
no static type checker in CI.

*Resolution.* Adopted. Callers pass a construction thunk; building,
validating, rendering, and serializing all happen inside the guarded
emitter boundary. Strict re-raises; forgiving emits a fresh safe
`schema_violation` carrying no caller-controlled content. M1 adds a
strict type-check CI step scoped to the events package, and the
value types keep explicit runtime validation of untrusted inputs.

**4 (P2). Absent-versus-null semantics lost.** The current schema
models `required` and `nullable` separately (`heard.language` is
conditionally absent); a `str | None` annotation says nothing about
key omission, and the golden did not pin requiredness.

*Resolution.* Adopted. Variants carry requiredness and nullability
explicitly; conditional absence becomes a separate variant or an
explicit typed absence; the golden pins both facts beside names and
types.

**5 (P2). `render()` contradicts the preserved `EventTap` seam and
the byte-identical reference claim.** `Emission` exposes unrendered
`message` and `args` that `LogTap` consumes; the generated reference
renders notes, constraints, requiredness and bounds that field
introspection alone cannot supply.

*Resolution.* Adopted, as the first offered contract: `Emission`
and `LogTap` are preserved, every variant derives its (template,
args) logging specification from its fields, and the record shape is
baseline-proven. Documentation facts beyond fields live as
declaration metadata. The generated reference is explicitly excepted
from byte identity and held to a semantic-completeness test instead;
the goal section now says so.

**6 (P2). `EntityAccess` fails the deletion test.** `ConfigStore`
already exposes typed public read/write/delete methods per kind;
the proposed surface would forward arguments to them.

*Resolution.* Adopted. `EntityAccess` is dropped; explicit routes
and CLI commands call the store's existing typed methods directly.
No replacement layer is added without naming the invariant it hides
and its non-forwarding consumers.

**7 (P2). Moving `notice` out of the spec contradicts the settled
effect-timing decision.** The issue requires the immutable spec to
retain effect timing; `notice` is static data-only timing prose.

*Resolution.* Adopted. `notice` stays a descriptor fact declared
inline; only the behavioral `wrote`/`deleted` functions move to
`writes.py` as direct typed calls.

**8 (P2). M6 does not complete the classification it claims.** Only
the support module and six files got dispositions; the rule allowed
any helper to keep a reach-in with a docstring, where the issue
permits that only for white-box safety invariants.

*Resolution.* Adopted. M6 now requires a recorded disposition for
every inventoried site across all 51 files and 82 names, and a
surviving reach-in must state which safety property public
observation cannot establish.

**9 (P2). M3 omits tests that break when `events_schema.py` goes.**
`tests/support/schema.py`, `test_events.py`, and
`test_conversations_session.py` import the registry; the last
derives permitted stored fields from it.

*Resolution.* Adopted. M3 names all three migrations: the scratch
seam becomes a scratch catalog, synthetic emissions use the public
declaration interface, and the conversation-storage surface derives
from the catalog or golden, never a second registry.

**10 (P2). The record baseline has no stated proof of path
exhaustiveness.** A runtime harness proves only paths it executes;
completeness today comes from the static site inventory.

*Resolution.* Adopted. The baseline's path list is generated from
the still-alive static inventory, every inventoried path must
produce a record, that obligation survives until the last
conversion, and afterwards exhaustiveness is claimed over the
catalog's constructible variants, not call sites.

**11 (P2). The OpenAPI spike can compile while producing an
unusable client.** Compilation and stable names do not catch
optional-versus-nullable handling, auth, problem responses, or the
provider extension properties.

*Resolution.* Adopted. M5 gains a checked-in strict-TypeScript
consumer fixture over auth, five-entity CRUD, typed problem
responses, optional/nullable handling, and provider extension
properties, with pinned generator versions and deterministic
output.
