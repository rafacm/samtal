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

A new module `providers/options.py` declares the per-type models
and one table, `OPTION_MODELS: Mapping[str, Mapping[str, type[BaseModel]]]`
(stage, then type). The module imports pydantic and nothing heavy:
no engine, no implementation module, which is what lets the config
write path import it without pulling `faster_whisper` or `httpx`
into a `config set`. `registry._factories()` keeps the factory
table it has; a unit test holds the two tables to consistency in
one direction (every OPTION_MODELS key names a registered type;
the reverse is deliberately not required, since types gain models
one PR at a time). The models are the type's contract stated once:
each field carries the example fragment's factual sentence as its
`Field(description=...)`, and the narrative prose (measurements,
tuning ladders) stays in the fragments per the standing
documentation decision.

Not in the registry dataclass-ification's way: `_factories()`
values stay callables; `construct_provider` looks the options
model up in `OPTION_MODELS` beside its factory lookup. One new
import edge (`registry` imports `options`), no cycle (options
imports no registry).

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
`VoiceSettings` model; `vad_parameters`, today an unvalidated
mapping, becomes a nested `VadParameters` model with
faster-whisper's documented keys and `extra="forbid"`.

Validation errors raised at BUILD time (boot, reload) keep
today's refusal channel (`ProviderError` naming the entry), with
the pydantic sentence reduced to field names and the rule, never
values, matching the reader's existing no-value discipline; the
reload path keeps withholding detail exactly as it does.

### 3. Write-time refusal names the field, by construction

`ProviderConfig` gains an after-validator that, when
`OPTION_MODELS` declares a model for `(the entry's stage is not
known inside the model, so:)` -- the dispatch cannot live on
`ProviderConfig`, which does not know its stage (`llm.local` and
`tts.local` parse through the same model). The hook is therefore
in the write funnel and the read-back path where the stage is in
hand:

- `store._parsed` (the write funnel) validates
  `entry.options` through `OPTION_MODELS[stage][entry.type]` when
  declared, after the entry parses and before the kind's
  `inside_write` check. A failure raises `ConfigError` with the
  same sentence-and-`FieldProblem` shape `_load` produces, the
  pointer being `/options/<field>` spelled directly from the
  per-type model's own declared names: `safe_location`'s
  truncation exists to avoid printing undeclared caller text, and
  a name the per-type model declares is by definition not caller
  text, so the sentence names it. Unknown keys render as the
  fixed `an unrecognized key is not permitted` sentence at the
  deepest DECLARED pointer, exactly the existing policy.
- `store._body` (read-back) and the loader run the same check, so
  a stored body predating a stricter model fails the bodies suite
  and forces the recorded compatibility decision that suite
  exists to force; with the three types chosen here, no committed
  fixture trips.
- Boot and reload get the check a third time through
  `construct_provider` (decision 2), which is not duplication but
  the same table consulted at each gate.

The masked-fragment path is unchanged: validation runs after
substitution inside the transaction, as it does today.

### 4. `openai_compatible` keeps the door open

Its model declares what the builder reads (`base_url` required,
`model` required, `max_tokens` bounded int with today's default)
with `extra="allow"`: a server-specific passthrough key remains
legal, is still checked by the inline-secret and URL-credential
rules that walk extras today, and the schema says so in one
sentence on that model alone. The compatibility fixture
(`every-field.json`) parses unchanged, which is the recorded
reason no bodies decision is needed in this batch.

### 5. The artifacts learn a per-type axis, minimally

- `docgen.schema("provider")` keeps rendering the envelope;
  `config schema provider <type>` becomes valid for a type with a
  model and renders that model's JSON Schema (the argument grammar
  already takes one optional entity word; it gains the optional
  second). `entity_names()` is untouched.
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
- Artifact churn per milestone, read and recorded:
  `domain-config.md`, `api-openapi.json` (the info description's
  contract paragraph plus, in M1, nothing structural),
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

M1: the pointer-inversion pair in `test_config_api_problems.py`
flips for a typed type's option (a declared field gets its
`/options/<field>` pointer; the undeclared-under-typed case gets
the unrecognized-key sentence at the deepest declared pointer) and
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

- [ ] **M1: the machinery and faster_whisper.**
  Decisions 1, 2, 3, 5 and the first conversion. Design
  footprint: adds `providers/options.py` (one home for what each
  type accepts); deepens `store._parsed` and `construct_provider`
  (both consult one table instead of trusting a ladder); deletes
  faster_whisper's reader ladder.
- [ ] **M2: elevenlabs.**
  The conversion plus the deletion of `read_voice_settings` and
  its key tables; the output-format rule moves into the model.
  Design footprint: deletes the hand-rolled precedent the pattern
  replaces.
- [ ] **M3: openai_compatible, the escape hatch.**
  Decision 4. Design footprint: the one model whose `extra`
  stays open, stated on the model, with the fixture untouched as
  the proof.
