# Declare typed option models for the first three provider types

Issue #88, third issue of the #265 CLI chain. Deviations,
resolutions and discoveries land in the companion
`2026-08-24-typed-provider-options-implementation.md`, one section
per milestone, appended in the change that ticks the milestone.

## Goal

Give the first three provider types a declared pydantic options
model each, so an option typo is refused at write with a message
naming the field, the options appear in the schema, the reference,
the OpenAPI document and the CLI help, and the type's builder reads
a validated object instead of popping keys off a dict. The three
types prove both ends of the pattern: `faster_whisper` (the richest
strict surface, a nested sub-model included), `elevenlabs` (whose
hand-rolled `read_voice_settings` is the precedent this
generalizes and deletes), and `openai_compatible` (the escape-hatch
type whose model must keep the door open that the type exists to
open). The remaining types follow as independent single-PR issues.

## The issue's decisions, restated

- Each provider type declares a pydantic options model in the
  registry; write-time validation through the same layer as the
  rest of the domain config; schema and documentation generation
  with `Field(description=...)` carried from the existing comment
  prose; an explicit statement per type of what it accepts.
- One PR per provider type is the digestible unit.
- `openai_compatible` and other base_url-shaped types keep an
  explicit escape hatch (`extra="allow"` on that model alone).
- Per #265: the first types land in-batch to prove the pattern end
  to end; per #243's settled decision, the body is the model dump,
  so an options model needs no storage change and no migration.

## The census, verified by tooling at `daad34e7`

- The registry (`providers/registry.py`) maps stage and type to a
  bare factory callable; `construct_provider` is its only
  consumer. `OptionsReader` pops keys and `finish()` refuses
  leftovers by NAMING the unknown keys, a policy the config layer
  does not share.
- `ProviderConfig` is `extra="allow"` with three declared fields;
  `options` is `model_extra`. The write funnel is `store._parsed`,
  which has the parsed entry (type included) and today cannot name
  an option typo because `safe_location` truncates every
  undeclared segment to the nearest declared parent (pinned by
  `test_config_api_problems.py:133-142`, the pair that inverts).
- Boot behavior today: unknown or mistyped options hard-fail at
  build (never silently), but only for entries a loaded agent
  references, and a reload's refusal deliberately withholds the
  option name from the API answer; write time is where the name
  can be said safely, which is the issue's point.
- The unchanged-value secret marker reaches into options via
  `is_secret_option`; a marked fragment defers parsing until after
  in-transaction substitution, so a typed model runs after
  substitution with no new ordering problem.
- `config.example.yaml` carries NO provider blocks (the issue's
  raw-material premise is stale); the prose lives in
  `examples/*.yaml`, whose fragments are already install-tested by
  `test_config_examples.py`, so examples and models stay honest by
  an existing suite.
- The #88 disclaimer text (`entities.py:79-85`) renders into three
  committed artifacts plus `examples/README.md`, with two tests
  asserting the literal "#88".
- The compatibility fixture `domain-bodies/provider/every-field.json`
  is `openai_compatible` carrying options no builder accepts;
  under this plan that type is the escape hatch, so the fixture
  stays parseable and no compatibility decision is forced.

## Decisions

### 1. The models live in `providers/options.py`, and the registry carries them

A new module `providers/options.py` declares the per-type model
CLASSES and nothing else: pydantic only, no engine, no
implementation module, which is what lets the config write path
import it without pulling `faster_whisper` or `httpx` into a
`config set`. The topology lives in ONE place, the registry:
`_factories()`'s values become a small frozen `Registration`
carrying the lazy factory and the optional options-model type, so
there is no second stage/type table to hold against the first
(the plan's earlier parallel `OPTION_MODELS` table is withdrawn;
the issue says the model is declared in the registry, and two
tables held together by a test are the design guide's pending
bug). Construction, write-time validation, documentation and the
enumeration of typed types all derive from that one table through
a dependency-light read: the registry module's table constructor
imports `options` (light) and defers the implementation imports
inside the factory closures exactly as today, so importing the
registry for its table stays cheap and a subprocess test pins
that a `config set provider` path loads no engine module.

The models are the type's contract stated once: each field
carries the example fragment's factual sentence as its
`Field(description=...)`, and the narrative prose (measurements,
tuning ladders) stays in the fragments per the standing
documentation decision.

### 2. One contract per type: the builder consumes the model

A per-type model beside a per-type `OptionsReader` ladder would be
two statements of what the type accepts, the drift the design
guide forbids. So a type that gains a model loses its ladder in
the same PR: `construct_provider` validates `config.options`
through the model when one is declared and hands the INSTANCE to
the factory (the factory signature for typed types becomes
`(label, config, options)`); the builder reads attributes. For
model-less types nothing changes: the factory keeps its
`(label, config)` shape and its `OptionsReader`, and the reader
retires naturally as the remaining types convert in their own
PRs. `read_voice_settings` and its two hand-rolled key tables are
deleted in the elevenlabs PR, replaced by a nested
`VoiceSettings` model carrying exactly the five keys the hand
check accepts today. `vad_parameters` becomes a nested
`VadParameters` model that declares the documented key and KEEPS
`extra="allow"`: the existing contract deliberately passes engine
VAD tuning through unchanged, the example documents a single key,
and closing the hatch on that evidence would make a running
deployment's valid faster-whisper setting unreadable on upgrade;
the model documents what we vouch for and the hatch stays stated
on it, mirroring decision 4's pattern in miniature.

Nested models cross two boundaries and both are stated: on the
way to an engine or a request body they are dumped with
`exclude_unset=True`, so an operator's explicit values (nulls
included) travel and injected defaults do not; in the
documentation they render recursively, so leaf names like
`min_silence_duration_ms` and `stability` are asserted present in
the schema, the reference, the OpenAPI components and the CLI
help, not just their parents.

Validation errors raised at BUILD time (boot, reload) keep
today's refusal channel (`ProviderError` naming the entry), with
the pydantic sentence reduced to field names and the rule, never
values, matching the reader's existing no-value discipline; the
reload path keeps withholding detail exactly as it does.

### 3. Write-time refusal names the field, by construction

The dispatch cannot live on `ProviderConfig`, which does not know
its stage (`llm.local` and `tts.local` parse through the same
model). One dependency-light sanitizer therefore owns the whole
job: a function in `providers/options.py` (beside the classes it
dispatches over, reading the registry's one table) that takes
stage, type and the options mapping, validates when a model is
declared, and returns either the validated instance or a
value-free refusal. It builds the sentence and the `FieldProblem`
tuple INSIDE the handler and raises OUTSIDE it, discarding the
`ValidationError` entirely, so no cause and no context carries
the rejected input; the store wraps it as `ConfigError`, the
build path as `ProviderError`, and both surfaces get direct
assertions on `str`, `repr`, `__cause__`, `__context__`, the
structured field problems, the boot output and the reload log,
with planted secret-shaped keys AND values.

The pointer shapes, stated for the fragment that is actually
submitted (options are flat siblings of `type`, not children of
an `options` key): a declared top-level option points at
`/<field>`; a declared nested field at `/<parent>/<field>`; an
unknown top-level option falls back to the empty pointer; an
unknown nested key falls back to its deepest declared parent.
`safe_location`'s contract (never print an undeclared segment) is
untouched: the sanitizer names only names the per-type model
declares, and the unrecognized-key sentence stays fixed. The
pointer-inversion tests assert these exact request-body pointers.

The gates, all reading the registry's one table:

- `store._parsed` (the write funnel) runs the sanitizer after the
  entry parses and before the kind's `inside_write` check; the
  masked-fragment path is unchanged (validation after
  substitution inside the transaction, as today).
- Read-back runs it where row identity is structurally in hand,
  `_from_row` for providers (which has stage and name), NOT by
  parsing a dotted location inside `_body`. The bodies suite
  extends to carry stage and type per fixture and to run the same
  public validator production runs, with a historical body added
  for every converted type; a stored row a stricter model refuses
  is a recorded break whose recovery path (boot refusal sentence,
  reload refusal, `--local delete` of the offending entry) gets
  its own test, because the pre-release stance prices the
  BREAKAGE at zero, not the operator's way out of it.
- Boot and reload get the check through `construct_provider`
  (decision 2): the same sanitizer consulted at each gate, not a
  reimplementation.

### 4. `openai_compatible` keeps the door open

Its model declares what the builder reads (`base_url` required,
`model` required, `max_tokens` bounded int with today's default)
with `extra="allow"`: a server-specific passthrough key remains
legal, is still checked by the inline-secret and URL-credential
rules that walk extras today, and the schema says so in one
sentence on that model alone.

An accepted key must also take EFFECT, or the hatch is silently
ignored configuration, the exact failure the issue exists to
remove (today the builder's `finish()` refuses every leftover, so
nothing was ever ignored; dropping `finish()` without forwarding
would regress that honesty). The extras therefore forward into
the outgoing request through the API's own escape door: they ride
as the request body's `extra_body`-style top-level parameters,
merged UNDER the fixed fields so a passthrough key can never
override `model`, `messages`, `stream`, the tools array or the
options this model declares; the reserved-name set is stated on
the model and a passthrough key colliding with it is refused at
validation with the field named. The M3 fake-client case asserts
a server-specific option (`top_p`, say) reaches the outgoing
request body, and a reserved collision is refused. The
compatibility fixture (`every-field.json`) parses unchanged and
its options now demonstrably travel, which is the recorded reason
no bodies decision is needed in this batch.

### 5. The artifacts learn a per-type axis, minimally

- `docgen.schema("provider")` keeps rendering the envelope;
  `config schema provider <stage> <type>` becomes valid for a
  type with a model and renders that model's JSON Schema. The
  selector carries the stage because the registry is stage-keyed
  and already holds duplicate type names (`openai` in ASR and
  TTS, `mock` everywhere); a test uses one of the existing
  duplicates so the axis cannot regress, and the reference and
  help headings group by stage then type. `entity_names()` is
  untouched.
- The reference's provider section gains one options table per
  typed type, rendered by the same `_table`, under a heading
  naming the type; the `| ... |` passthrough row survives for the
  untyped remainder, and the #88 note rewrites to say which types
  are declared and where the rest are documented (keeping the
  issue reference until the last type lands, since the two tests
  that pin it are the honest tracker).
- `fragment_help("provider")` (the `set provider` epilog and
  cli.md) appends, per typed type, the type name and its option
  fields through the same deepened renderer M1 of #194 built
  (name, type, default, description); the "any other key" trailer
  narrows to the untyped types.
- The OpenAPI document gets the STRUCTURAL half, not prose alone:
  each typed model (nested sub-models included) is injected into
  `components.schemas` through the same injection path the other
  documented models ride (`api.py`'s component injection), named
  `<Stage><Type>Options` (`AsrFasterWhisperOptions`), and
  connected to the provider write contract by a documented
  mapping: the provider PUT's description names, per typed
  stage-and-type, the component that states its options, since
  the PUT deliberately takes RawBody and cannot carry a
  discriminated request schema without repeating the api's
  refusal-shaping rationale. Tests assert typed leaf fields and
  their descriptions are REACHABLE from the provider PUT: the
  description names each component, and each named component
  exists with the leaves.
- Artifact churn per milestone, read and recorded:
  `domain-config.md`, `api-openapi.json` (the injected components
  and the description mapping, plus the contract paragraph),
  `cli.md` (both regions), `examples/README.md` prose. `events.md`
  and `conversations-schema.md` must not move.

### 6. Milestones, one type each, machinery in the first

- M1: `providers/options.py`, the registry lookup, the store and
  loader hooks, the docgen per-type axis, the disclaimers
  rewritten, and `faster_whisper` converted whole (model with
  nested `VadParameters`, builder consuming it, ladder deleted).
- M2: `elevenlabs` converted; `read_voice_settings` and its key
  tables deleted; the `pcm_<rate>` output-format rule becomes the
  model's own validator.
- M3: `openai_compatible` per decision 4.

Each leaves `main` releasable: the machinery treats model-less
types exactly as today, so a merge between milestones is a
partially typed registry, which is the rollout state the issue
prescribes anyway.

## Tests

Two disciplines run through every milestone. Coercion parity: the
reader's accepted-and-rejected set survives per converted call,
inventoried field by field (booleans refused where numbers are
expected, numeric strings refused for integers, empty number
lists refused, a scalar temperature still accepted and normalized
to a one-element list, required strings nonblank), stated with
strict field types and normalization validators and held by a
table-driven parity test per type. And CI reality: the
option-model, sanitizer and registry-dispatch tests are
dependency-free and always run (the default lane installs no
optional extras, and the existing faster_whisper suites skip
without the engine); only real implementation-to-engine plumbing
stays under the extra guard, with a fake factory exercising the
typed handoff in the ordinary lane.

M1: the pointer-inversion pair in `test_config_api_problems.py`
flips for a typed type's option (a declared field gets its
`/<field>` pointer per decision 3's shapes; the
undeclared-under-typed case gets the unrecognized-key sentence at
the deepest declared pointer) and
keeps the truncation case on an untyped type; the faster_whisper
builder suite drops its reader cases for model cases with the same
subjects (wrong type named, unknown mode refused, defaults hold);
`test_config_examples.py` keeps every fragment installing, which
now validates the tuning ladder's commented keys when uncommented
(a new case uncomments each documented option of the typed types'
fragments and installs the result, so the docs cannot document a
key the model refuses); the docgen suites' `#88` assertions
update to the rewritten sentences; new pins for `config schema
provider faster_whisper` and the reference's per-type table.
M2/M3: the same shape per type; M2 keeps
`test_an_unknown_voice_setting_is_refused`'s subject through the
model. The write-refusal no-leak discipline: a planted
credential-shaped VALUE in a typed option is never rendered (the
sentence names fields and rules only), asserted at store, API and
CLI surfaces once in M1.

## Risks

- **The pointer policy is the subtle edit.** `safe_location`'s
  contract ("never print an undeclared segment") must survive
  exactly; the per-type path adds declared names, never relaxes
  the rule. The M1 review round should read that diff hardest.
- **Strictness meets stored reality.** A deployment that stored a
  passthrough key under a type that now declares a model gets a
  read-back failure. Pre-release stance prices this at zero
  (boards resettable, no third-party installs), the bodies suite
  is the gate, and the chosen three types trip no committed
  fixture; still, the changelog names the tightening per type.
- **The registry import edge.** `config/store.py` importing
  `providers.options` must not drag implementation modules; the
  import-weight proof is a subprocess test in M1 asserting a
  `config set provider` path loads no engine module.

## Milestones

- [x] [**M1: the machinery and faster_whisper.**](2026-08-24-typed-provider-options-implementation.md#m1-the-machinery-and-faster_whisper)
  (PR #275) Decisions 1, 2, 3, 5 and the first conversion. Design
  footprint: adds `config/provider_options.py` (one home for what each
  type accepts, at an address the documentation can afford to import,
  with the registry deriving its own table from it); deepens the
  provider kind's storage checks and `construct_provider` (both consult
  one declaration instead of trusting a ladder); deletes
  faster_whisper's reader ladder. The declaration's address is a
  recorded deviation from decision 1's letter, resolved with its
  reasoning and its measurements in the implementation doc, because
  three committed import-weight pins put anything inside the provider
  package out of reach of the surfaces that document it.
- [x] [**M2: elevenlabs.**](2026-08-24-typed-provider-options-implementation.md#m2-elevenlabs)
  (PR #276) The conversion plus the deletion of `read_voice_settings`
  and its key tables; the output-format rule moves into the model.
  Design footprint: deletes the hand-rolled precedent the pattern
  replaces, and adds the first nested model whose door is shut, which
  is what makes two of decision 3's four pointer shapes testable.
- [ ] **M3: openai_compatible, the escape hatch.**
  Decision 4. Design footprint: the one model whose `extra`
  stays open, stated on the model, with the fixture untouched as
  the proof.

## Plan review round

External review of commit `c4a75c3c`, 2026-08-24. Backend: codex
CLI 0.149.0, model `gpt-5.6-sol`, read-only sandbox, runtime
8m42s. Verdict as received: ready after the P1/P2 amendments.
Eleven findings; each amendment is its own commit with a
resolution note here.

1. **P1: the OpenAPI deliverable had no structural path.** The
   provider PUT takes RawBody referencing only `ProviderConfig`,
   and models held in a side table would be absent from
   components and undiscoverable from the write route.

   *Resolution* (this commit): decision 5 injects each typed
   model into `components.schemas` through the existing injection
   path, named `<Stage><Type>Options`, connected to the provider
   PUT by a documented mapping in its description, with tests
   asserting the leaves are reachable from the PUT.

2. **P1: the escape hatch never made passthrough take effect.**
   The current builder refuses every leftover and assembles
   requests from a fixed dictionary, so removing `finish()`
   without forwarding `model_extra` silently ignores
   configuration, the exact failure the issue exists to remove.

   *Resolution* (this commit): decision 4 forwards extras into
   the outgoing request merged under the fixed fields, states the
   reserved-name set on the model with collisions refused by
   name, and M3 gains the fake-client reach-the-wire case.

3. **P1: pydantic failures introduce a secret-bearing exception
   path the tests did not cover.** A ValidationError retains
   rejected input; the plan's tests asserted rendering surfaces
   only, not causes, contexts, boot output or reload logs.

   *Resolution* (this commit): one dependency-light sanitizer in
   `providers/options.py` owns validation, dispatch, pointers and
   the value-free sentence, discards the ValidationError before
   raising, and both wrappers get direct chain assertions with
   planted secret-shaped keys and values across store, API, CLI,
   boot and reload.

4. **P2: the parallel OPTION_MODELS table violates the registry
   decision and the locality rule.** Two stage/type tables held
   together by a one-way test are the design guide's pending bug.

   *Resolution* (this commit): withdrawn; the registry's values
   become one frozen `Registration` carrying factory and optional
   model, everything derives from it, and `providers/options.py`
   keeps only the model classes.

5. **P2: `/options/<field>` is not a pointer into a provider
   fragment.** Options are flat siblings of `type`; the pointer
   addressed a key absent from the submitted JSON.

   *Resolution* (this commit): decision 3 states the four pointer
   shapes for the flat fragment (`/<field>`, `/<parent>/<field>`,
   empty for unknown top-level, deepest declared parent for
   unknown nested), asserted as exact request-body pointers.

6. **P2: `config schema provider <type>` cannot represent the
   stage axis.** `openai` exists in ASR and TTS and `mock` in
   every stage.

   *Resolution* (this commit): the selector is
   `config schema provider <stage> <type>`, headings group by
   stage then type, and a test uses an existing duplicate.

7. **P2: nested models need explicit serialization and recursive
   documentation.** Engines and JSON take dictionaries;
   `_table` and `fragment_help` enumerate one level.

   *Resolution* (this commit): decision 2 dumps nested models
   with `exclude_unset=True` at both boundaries and renders them
   recursively, with leaf names asserted in all four artifacts.

8. **P2: coercion parity was unspecified.** The reader rejects
   booleans as numbers, numeric strings as integers, empty lists,
   and accepts scalar temperature; ordinary pydantic fields do
   not reproduce that set.

   *Resolution* (this commit): the Tests section requires a per
   converted call inventory, strict field types with
   normalization validators, and a table-driven parity test per
   type.

9. **P2: the stored-body gate bypasses typed validation.** The
   bodies suite validates through stage-blind `ProviderConfig`
   with a hard-coded `llm` identity, and `_body` has no stage.

   *Resolution* (this commit): read-back validation lands in
   `_from_row` where stage is structural; the bodies fixtures
   carry stage and type and run the production validator, a
   historical body joins per converted type, and the
   refused-stored-row recovery path gets its own test.

10. **P2: `VadParameters(extra="forbid")` closes an engine escape
    hatch on the evidence of one documented key.**

    *Resolution* (this commit): the nested model keeps
    `extra="allow"`, declaring the documented key and stating the
    hatch, decision 4's pattern in miniature; the existing
    pass-through contract survives.

11. **P2: the faster_whisper builder tests would be skipped in
    default CI**, which installs no optional extras.

    *Resolution* (this commit): the option-model, sanitizer and
    dispatch tests are dependency-free and always run; a fake
    factory exercises the typed handoff in the ordinary lane;
    only engine plumbing stays under the extra guard.
