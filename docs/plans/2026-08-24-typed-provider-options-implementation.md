# Declare typed option models for the first three provider types: implementation

Companion to
[`2026-08-24-typed-provider-options.md`](2026-08-24-typed-provider-options.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the machinery and faster_whisper

### What was done

Ten commits: the options module and its sanitizer, the registry's one
table, the faster_whisper conversion, the mechanical test repair the
table forced, the store's two gates, the schema selector and the
disclaimers, the tests, this record, and then the two that resolved the
deviation below: the declaration's move to the configuration side, and
the three renderings its old address had put out of reach.

**The options module.** `config/provider_options.py` holds the model
classes, the `DECLARED_OPTIONS` mapping and two functions. It was
`providers/options.py` for the first seven commits, and deviation 1
below is the whole story of why it is not.
`FasterWhisperOptions` declares all fourteen options
the builder read, each carrying the example fragment's own factual
sentence as its `Field(description=...)`, with `extra="forbid"`.
`VadParameters` is nested and keeps `extra="allow"`, with the reason
stated in its docstring: the engine's VAD tuning has always been
forwarded unread, the fragment documents one key of it, and closing the
hatch on that evidence would make a running deployment's valid setting
unreadable on upgrade.

`checked_options(headline, stage, type, options)` is the sanitizer: it
resolves the model from the declaration and hands it to `validated()`,
which builds the sentence and the `FieldProblem` tuple inside the
handler and raises `OptionsRefused` outside it, so the
`ValidationError` (which holds the whole rejected mapping in its
`errors()`) is discarded with neither a cause nor a context. Each caller
wraps it in the refusal of its own surface, and the build path calls
`validated()` directly with the model its registration already
resolved.

**One walk, moved.** The rendering of a failed validation into a
sentence and a set of field problems was `store._validation_problems`,
with `_error_problems` and `_refusal_line` under it. It is
`models.validation_problems` and `models.refusal_line` now, beside
`safe_location` whose rule it applies. The move is what lets the
sanitizer produce the repository's exact wording without importing the
repository: it is a pydantic-and-standard-library function and the write
path must be able to reach it from a module that pulls in no database
driver. `store.py` imports it; no wording changed, which the API
problems suite holds differentially rather than by golden.

**The registry's one table.** `_factories()` is `_registrations()`, and
its values are a frozen `Registration(factory, options)` whose second
half is read out of `DECLARED_OPTIONS` as the table is built rather
than written beside the factory. `registration(stage, type)` is the read
the build path goes through; the enumeration every rendering wants,
`declared_options()`, is on the declaration's own side, where a document
can reach it. `construct_provider` validates when the registration
carries a model, raises `ProviderError` outside the handler on a
refusal, and calls `factory(label, config, options)` for a typed type
and `factory(label, config)` for a model-less one. The two shapes are
decided by `options` being present rather than by a flag.

**faster_whisper.** The builder's `OptionsReader` ladder and its
hand-rolled `language_detect` membership check are gone;
`LANGUAGE_DETECT_MODES` with them, since the model's `Literal` is the
same statement and pydantic's message already names both modes.
`build(label, config, options)` is the one translation the seam needs,
`vad_parameters` from a model to the mapping `WhisperModel.transcribe`
takes, dumped with `exclude_unset=True`. `FasterWhisperAsr.__init__` is
untouched, so what an engine receives is unchanged and the existing
engine-facing cases assert it unchanged.

**The two gates.** `_Storage` gained a third fact, `inside_read`, beside
`before_parse` and `inside_write`, and the provider kind fills both
write and read. The write check runs the options first and the
URL-credential rule after, refusing with `ConfigError` and the field
problems. The read check runs in `_from_row`, refusing with
`StorageError`; `_from_row` is where the row's stage is structurally in
hand, which is the whole reason it is not in `_body`, whose location is
a dotted string a reader would have to parse back apart.

Both call the sanitizer at module scope. They were written with the
import deferred inside the call, because `config/cli.py` imports this
module and is held to loading no `vinga_server.providers`; the move in
deviation 1 retired the deferral, since what they import now is a
configuration module. A subprocess test asserts the result: a real
`store.set_provider` of a faster_whisper entry loads no engine, and no
provider module either.

**The four renderings.** `docgen.schema(name, stage, type)` renders a
typed model's JSON Schema, and the CLI gained two optional positionals,
so `vinga-server config schema provider asr faster_whisper` prints the
contract. The reference gains a subsection per typed type under the
provider kind, addressed by stage and type together and rendered
recursively, so `min_silence_duration_ms` appears rather than only the
name of the section holding it. The OpenAPI document gains each model
as a component named for the same pair (`AsrFasterWhisperOptions`),
injected beside the entity models with its nested definitions hoisted;
the provider PUT takes its body unread and so cannot carry a
discriminated request schema, and what connects the two is a mapping in
its description, derived from the declaration, with a test that walks
from the route to each named component and finds the leaves. The `set
provider` epilog lists the same fields, nested ones at the path a
fragment writes them at. The `#88` note in `entities.py` reads its list
of declared types out of the declaration, and the bullet in
`examples/README.md` says the same thing in prose it owns.

### Deviations from the plan

Three, and the first is the one that matters.

**1. The option models are declared on the configuration side, and the
registry derives from that declaration.** RESOLVED; the milestone ships
all of decision 5. What follows is the collision as it was found, the
decision that resolved it, and the reasoning, kept in full so the PR
review round can re-litigate it with everything in view.

*The collision.* Decision 5 assumed the documentation surfaces could
reach the registry's table. Two committed import-weight pins say they
cannot, and both were measured rather than reasoned about:

- `tests/unit/test_config_docgen.py::test_the_reference_and_the_schema_render_from_the_models_alone`
  runs `docgen.reference()` and `docgen.schema()` in a child interpreter
  and asserts the set of loaded `vinga_server` modules is EXACTLY
  `ALLOWED_IMPORTS`, and that none of sqlalchemy, cryptography, fastapi
  or httpx is loaded. Importing `vinga_server.providers.options` adds
  `vinga_server.providers`, `.base`, `.registry`, `.world`,
  `vinga_server.egress` and `vinga_server.config.secrets`, and with them
  cryptography. Because the assertion is set EQUALITY, no arrangement of
  lazy imports helps: `reference()` is one of the two calls the pin
  makes, so anything it renders from the registry widens the pin.
- `tests/unit/test_onboarding_import_weight.py::test_rendering_the_api_document_loads_no_conversation_either`
  calls `document()` and asserts `vinga_server.providers` is not loaded.
  Injecting the typed models into `components.schemas` happens inside
  that call, so it would trip this pin the same way.

The third rendering is blocked by the same shape: `cli.py` builds every
`set` command's epilog at module scope, so a per-type listing would load
the provider package on a bare `import vinga_server.config.cli` and trip
the second pin.

*The decision.* The model classes, the stage-and-type mapping and the
sanitizer live in `config/provider_options.py`, which imports pydantic
and `config.models` and nothing else. The provider registry DERIVES its
`Registration` from that mapping when its table is built
(`Registration(factory, DECLARED_OPTIONS.get((stage, type)))`), so there
is one topology, one direction, and no second copy to drift; a key
declaring options for a stage-and-type no factory has is caught by a
one-way test rather than by silence. `ALLOWED_IMPORTS` gains exactly
`vinga_server.config.provider_options` and nothing else, in the docgen
pin and in the registry pin beside it, each with the reason written on
it; the assertion that no heavy dependency loads is untouched and still
passes empty.

*The reasoning, recorded because it supersedes a review resolution.*
Plan review finding 4 put the models' declaration in the registry, and
this supersedes the letter of that while keeping its spirit. The
finding's objection was two stage-and-type tables held together by a
one-way test, which is the design guide's pending bug; its remedy was
one topology with everything derived from it, and that is exactly what
this is. The finding's own text left the door open ("providers/options.py
can still own the lightweight model definitions"). What decides the
direction is that the two pins are load-bearing architecture rather than
hygiene: documentation renders from the models alone, `document()` loads
no providers package, and the CLI imports no engine. Those are promises
about where this code can run, and the letter of the finding cannot
satisfy them, because reaching anything inside `vinga_server.providers`
runs a package `__init__` that re-exports the engine base classes and
the provider world and pulls in cryptography through the secret store.
So the declaration sits where the light readers can reach it, and what
stays on the provider side is the half that is genuinely the provider
layer's: which factory builds a type, and the reading of a validated
instance inside a builder.

One consequence worth naming: `construct_provider` validates against the
model its own registration resolved (`validated()`) rather than looking
the pair up a second time. A second lookup can answer differently from
the first, and a test that replaced the registry's table with a fake
type proved it immediately.

**2. The sanitizer takes the refusal's headline.** The plan describes it
as taking stage, type and the options mapping. It also requires each
surface to refuse in its own words (`invalid <location>:` at a write,
the unreadable-row sentence on read-back), and the sanitizer is what
composes the lines under that first line, so the headline is a
parameter. Nothing else about its contract moved.

**3. The write gate runs inside the kind's `inside_write` rather than
in front of it.** `_parsed` has no per-kind arm and gaining one to run a
provider-only check would have been a second dispatch beside `_STORAGE`.
The provider's entry composes the two checks in the order the plan
states (options first, then the URL-credential rule), so the refusal an
operator meets is the same one; what differs is which table the ordering
is written in.

### Discoveries

**The read-back gate makes the reload's typed-options case unreachable
from the store.** A stored row carrying an option the type refuses is
now refused when it is read, so `construct_provider` never sees one on
the reload path. The reload case that pins the withholding therefore
composes its configuration in memory, which is also how the egress
refusal beside it is written.

**Pydantic's Literal message names the choices and not the input.** The
builder's own membership check existed to produce
`must be one of: every_utterance, once`. `Input should be
'every_utterance' or 'once'` says the same thing, is value-free, and
comes free with the annotation, so the check is deleted rather than
reimplemented as a validator.

**The bodies suite's provider identity had to become per-fixture.**
Every provider body was planted under the `llm` stage, and a
faster_whisper body planted there is a body nothing checks, since the
model is the one the stage AND type declare. The stage is now read off
the registry from the body's own `type`, which keeps that mapping in one
place.

### Coercion parity, inventoried call by call

The reader call the builder made, what it accepted, and what the model
does now. Held by `PARITY` in `tests/unit/test_provider_options.py`,
which runs in every lane.

| Option | Reader call | Accepted then | Accepted now | Same? |
| --- | --- | --- | --- | --- |
| `model` | `string(..., "small")` | a string; absent, null or empty gives the default | `StrictStr`, default `small`, blank read as unwritten | yes |
| `language` | `string(...)` | a string or null | `StrictStr \| None` | yes |
| `device` | `string(..., "cpu")` | a string; absent, null or empty gives the default | `StrictStr`, default `cpu`, blank read as unwritten | yes |
| `compute_type` | `string(..., "int8")` | a string; absent, null or empty gives the default | `StrictStr`, default `int8`, blank read as unwritten | yes |
| `download_dir` | `string(...)` | a string or null | `StrictStr \| None` | yes |
| `language_fallback` | `string(...)` | a string or null | `StrictStr \| None` | yes |
| `beam_size` | `integer(..., 1)` | an int; never a bool, a float or `"5"` | `StrictInt`, default 1 | yes |
| `cpu_threads` | `integer(..., 0)` | an int; never a bool, a float or `"3"` | `StrictInt`, default 0 | yes |
| `vad_filter` | `boolean(..., False)` | true or false; never `1` or `"yes"` | `StrictBool`, default false | yes |
| `condition_on_previous_text` | `boolean(..., True)` | true or false; never `0` | `StrictBool`, default true | yes |
| `language_confidence_floor` | `number(..., 0.6)` | an int or a float, never a bool or a string, normalized to float | `Number` (a before-validator with the same rule) | yes |
| `temperature` | `numbers(...)` | a number as a list of one, a non-empty list of numbers, or absent; never a bool, an empty list, a string or a list holding either | `Numbers` (the same rule as a before-validator) | yes |
| `vad_parameters` | `mapping(...)` | a mapping, absent or null; never a list or a scalar | `VadParameters`, `extra="allow"`, null read as unwritten | yes |
| `language_detect` | `string(...)` plus a membership check | `every_utterance` or `once`; absent, null or empty gives the first | `Literal[...]`, blank read as unwritten | yes |
| any other key | `finish()` | refused, naming the unknown keys | `extra="forbid"`, refused without naming them | refusal is now value-free and earlier |

One deliberate divergence, in the changelog: an unknown key is refused
at write rather than at build, without echoing the key, which is the
issue's point and the repository's standing rule about keys.

The blank rows were the PR review round's second finding, and the row
they landed in is worth reading twice. The first version of this table
recorded "null tightens" for four options and called it deliberate. It
was not: `options.string("model", "small") or "small"` also swallowed
the EMPTY STRING, so the tightening was wider than the note admitted,
and it was a tightening on the one axis this batch has no reason to
touch, an operator's existing file rather than an operator's typo. A
model-level before-validator drops those keys instead, which reads them
as unwritten rather than as the default written out, and the five cases
are in `PARITY`.

### Artifact churn

Three of the six committed artifacts moved, and the two the plan says
must not move did not.

- `docs/reference/domain-config.md` (+43, -9): the rewritten `#88`
  note, and the new `#### \`asr\` options for \`type: faster_whisper\``
  subsection with its fourteen-row table and the nested
  `vad_parameters` table under it. No existing table row changed.
- `docs/reference/api-openapi.json` (+150, -2): the
  `AsrFasterWhisperOptions` and `VadParameters` components, the
  provider PUT's description with the component mapping under it, and
  the same note in `info.description`.
- `docs/reference/cli.md` (+68, -4): the `config schema` usage line
  gains `[STAGE] [TYPE]` with two argument rows, and the `set provider`
  epilog gains the per-type option listing and its narrowed trailer.
- `docs/reference/events.md`, `docs/reference/conversations-schema.md`:
  byte-identical, checked at the tip.

### Verification

From `vinga-server/`, at the tip of the milestone.

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q -n auto --dist loadfile`:
  `3244 passed, 18 skipped in 43.31s`
- `uv run pytest tests/integration -q`: `126 passed in 195.55s`
- `uv run mypy`: `Success: no issues found in 4 source files`
- The six drift checks as CI runs them: all six clean, including the
  two that must not move.
- `uv sync --frozen`: `Checked 99 packages`, nothing resolved.
- The three import-weight pins that forced deviation 1: green, with
  `ALLOWED_IMPORTS` widened by exactly one name in the two places that
  have one, and `test_onboarding_import_weight.py` untouched. A
  subprocess case in `test_provider_options.py` now asserts the
  stronger fact the move bought: a real `store.set_provider` of a
  faster-whisper entry loads no `vinga_server.providers` module at
  all.

The faster-whisper extra is not installed in this environment, so the
eleven engine-facing cases in
`tests/unit/test_providers_faster_whisper.py` skip, exactly as they do
in CI. That is what the review round's finding 11 is about, and why the
model, sanitizer and dispatch cases were written not to need them: 59
of them run in the ordinary lane.

### PR review round

External review of PR #275, 2026-08-24. Four findings, verdict
mergeable after fixes; each fix is its own commit and each is recorded
here with what it changed and what proves it.

**4 (P2), taken first because it restructures what the others touch:
the placement fix left a parallel topology.** `DECLARED_OPTIONS` keyed
by `(stage, type)` sat beside the registry's own stage-and-type keys,
bridged by a one-way test, which is the shape deviation 1 was supposed
to end rather than relocate.

*Resolution.* One table, `PROVIDER_TYPES`, in the light module, keyed
by stage and then by type, each entry carrying both facts a surface
asks about a type: where its factory lives (a module name and an
attribute, resolved at construction time) and the options model it
declares. `providers/registry.py` derives its registrations by
resolving those names, the known-types refusal counts the same keys,
and `declared_options()` and `component_name()` read the same entries.
`DECLARED_OPTIONS` and the bridge test are gone; what replaces the test
is an invariant rather than a bridge, that the resolved registry IS the
table (same keys, same models), plus one holding the table's stages to
the pipeline's. The three allow-list widenings are unchanged and needed
no further names: a factory is named rather than imported, so the
module still weighs pydantic and `config.models`.

*The coordinator's earlier compromise, superseded.* Deviation 1 above
put the models on the configuration side and left the factories in the
registry, deriving only the model half. That was the right half of the
answer: it is what made the documentation possible at all. What it got
wrong is that a type is one thing, so splitting it across two tables
keyed the same way reproduced the duplication one field lower down.
This round's synthesis keeps the address the pins force and puts the
whole type at it.

**1 (P1): library tracebacks were still reachable through the exception
chain.** The generic factory-failure wrapper raised `ProviderError ...
from exc`, and the missing-extra refusal chained its ImportError. Both
sentences are printed to an operator as they are, and a chain is a
rendering surface like any other.

*Resolution.* Both capture what is safe inside the handler, the class
name or nothing, and raise after it has closed, so the refusal carries
neither a cause nor a context.

*The decision this reverses, said out loud.* Keeping the original as
`__cause__` was not an oversight: PR #188's round decided it
deliberately on 2026-08-18, with the comment "the exception itself is
this one's `__cause__` for whoever has a debugger" and a test requiring
`isinstance(excinfo.value.__cause__, OSError)`. This round supersedes
that under the discipline established since, which the #244, #245 and
#194 rounds each enforced on their own refusal paths: what a diagnosis
reads is the log, which records the class and the entry, and an
exception chain reaches every renderer that walks one. The test that
required the cause now requires its absence and looks for the planted
sentinel through the whole chain.

*Revert proof.* Restoring either `from exc` fails
`test_a_provider_that_fails_to_construct_names_the_entry` on the
`__cause__ is None` assertion, and the missing-extra case on its own.

**2 (P2): the parity inventory missed the blank spellings.** Four
options ended the reader's ladder with `or <default>`, which swallowed
the empty string as well as the null, and `vad_parameters` was read
through a call that answered `{}` for a missing key.

*Resolution.* A model-level before-validator drops those five keys when
they hold `None` or `""`, so they read as unwritten and the field's own
default applies; the parity table above is corrected, the five cases
are in `PARITY`, and an engine-facing case asserts `model: ""` reaches
the engine as `small` and a null VAD section is not forwarded.

*Revert proof.* Removing the validator fails eight parity rows and both
of the blank-defaulted cases.

**3 (P2): the published temperature schema contradicted its
validator.** The annotation describes what comes out of a
`BeforeValidator`, so the document said "array of numbers, or null"
while the validator took a bare number and refused an empty array.

*Resolution.* The validator declares its `json_schema_input_type`: a
number, an array of at least one (`minItems: 1`), or null. The
committed OpenAPI document moved by four lines, and the assertion runs
over the rendered surfaces rather than the annotation, checking the
same three forms through the validator.

*Revert proof.* Dropping the input type fails
`test_the_published_schema_says_what_the_validator_accepts` on the
missing number branch, and the OpenAPI drift check on the four lines.

### Verification after the review round

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q -n auto --dist loadfile`:
  `3262 passed, 19 skipped in 43.34s`
- `uv run pytest tests/integration -q`: `126 passed`
- `uv run mypy`: `Success: no issues found in 4 source files`
- The six drift checks: clean; `api-openapi.json` moved by the four
  lines finding 3 adds and nothing else moved.
- `uv sync --frozen`: `Checked 99 packages`, nothing resolved.
- The three import-weight pins: green, with no name added beyond the
  one this milestone already added to two of them.

## M2: elevenlabs

### What was done

Six commits: the model, the builder's conversion, the three documents,
the tests, this record with the changelog and the tick, and then the one
the rebase asked for. The first five were written against M1 as it went
to review; the section below on the rebase says what the review round's
restructuring changed under them, and every count, diff and result in
this section is read at the tip that came out of it.

**The model.** `ElevenlabsOptions` declares the six options the builder
read, with `extra="forbid"` and the example fragment's own sentence on
each field. Two rules the builder held by hand are stated on the fields
that have them: `voice_id` is `Nonblank`, which is what
`required_string` demanded, and `output_format` is `PcmFormat`, which is
`parse_sample_rate`'s refusal moved to where the field is declared. The
rate itself is a `@property` off the validated string rather than a
field: it is not an option, it is what `output_format` means to
everything past the request, and it can be read without a failure of its
own because the validator is what admitted the string.

`VoiceSettings` is nested and shuts its door, which is the one place M2
differs from M1's nested model. `VadParameters` keeps `extra="allow"`
because an engine reads more keys than vinga documents; the vendor's
voice tuning is a fixed five that `read_voice_settings` already listed
in full and already refused a sixth of, so declaring it and closing it
is the same contract rather than a new one. The four numeric keys are
`OptionalNumber`, a new annotation beside `Number`: the hand check
skipped a key whose value was null and let it travel, and a knob whose
absence means the vendor's own default is one an operator may legitimately
write as an explicit null.

**The builder.** `build(label, config, options)` reads attributes.
`read_voice_settings`, `_VOICE_SETTING_NUMBERS`, `_VOICE_SETTING_FLAGS`,
`parse_sample_rate`, `_PCM_FORMAT`, `DEFAULT_MODEL` and
`DEFAULT_OUTPUT_FORMAT` are deleted: every one of them said something
the model now says. What is left is two translations and a lookup, the
rate, `voice_settings` dumped `exclude_unset=True` into the request body
exactly where the old dictionary went, and the credential, which is the
one thing an options model cannot hold. `ElevenLabsTts.__init__` is
untouched, so what reaches the API is unchanged and the request-shape
cases assert it unchanged.

**The registration.** One argument on one line: the `("tts",
"elevenlabs")` entry of `PROVIDER_TYPES` gains `options=
ElevenlabsOptions`. `providers/registry.py` is byte-identical to main's,
which is the clearest thing this milestone says about the shape the
review round left behind: converting a type touches the table and the
builder, and nothing in between.

**The four renderings.** Nothing was written for them. Every surface
reads `declared_options()`, so that one argument moved three committed
documents and the fourth (`config schema provider tts elevenlabs`)
started answering. That is the machinery M1 paid for, and this milestone
is the first evidence it works for a type it was not written against.

### Deviations from the plan

None. The milestone is decision 2's second half and the plan's M2 as
written.

### Discoveries

**A prose convention collided with the uncomment-and-install case.**
`test_config_examples.py` uncomments every commented `# key:` line of a
typed type's fragment and installs the result, which is what stops a
fragment documenting a key its model refuses. Three fragments open a
paragraph with `Default: <value>` and one with `API: <what it is>`, and
`KEY_LINE` read those as documented keys: the uncommented elevenlabs
fragment submitted `Default` as an option and was told it is not one.
The pattern now requires a lower-case initial, which is a rule about
this repository rather than about YAML (every key any model here
declares is snake_case, and a sentence of prose starts with a capital).
Narrowed rather than reworded: the comment above `KEY_LINE` already said
prose is not coverage, and bending documentation around a scan is the
wrong direction. It also de-mines M3, whose fragment carries the `API:`
line.

**The nested-refusal pointer shapes had no test until this type.**
Decision 3 states four pointer shapes and M1 could assert three of them:
`VadParameters` forwards what it does not declare, so an unknown key
under a typed type's nested section is never refused there at all. The
first closed nested model is this one, so `/voice_settings` for an
invented key and `/voice_settings/stability` for a declared one are
asserted here, at the model and again as request-body pointers in
`test_config_api_problems.py`.

**An explicit null used to be an assertion failure rather than a
refusal.** The old builder read `model` and `output_format` with
`options.string(key, default)`, which returns None for an explicitly
null key, and then said `assert model is not None  # defaults are
strings`. So `model: null` reached an `AssertionError` from inside the
builder, and under `python -O` would have passed None to the request. It
is a refusal at write time now, which is the same tightening M1 recorded
for its own defaulted options and a better starting point than the one
it replaces.

### Coercion parity, inventoried call by call

The reader call the builder made, what it accepted, and what the model
does now. Held by `ELEVENLABS_PARITY` in
`tests/unit/test_provider_options.py`, which runs in every lane.

| Option | Reader call | Accepted then | Accepted now | Same? |
| --- | --- | --- | --- | --- |
| `voice_id` | `required_string(...)` | a non-blank string; absent, null or blank refused as "is required" | `Nonblank` (`StrictStr` plus a not-blank rule), required | yes, and the two refusals are now separate sentences |
| `model` | `string(..., "eleven_flash_v2_5")` | a string, `""` included and travelling as an empty model id; absent gives the default, explicit null reached an `assert` | `StrictStr`, default `eleven_flash_v2_5` | null tightens, from a crash to a refusal; `""` unchanged, because this reader had no `or <default>` |
| `output_format` | `string(..., "pcm_24000")` plus `parse_sample_rate` | a `pcm_<rate>` string; anything else refused with the value quoted, `""` included | `PcmFormat` (`StrictStr` plus the same pattern), default `pcm_24000` | null tightens the same way; the refusal is value-free now |
| `language_code` | `string(...)` | a string or null | `StrictStr \| None` | yes |
| `timeout_s` | `number(..., 30.0)` | an int or a float, never a bool, a string or a null, normalized to float | `Number` (a before-validator with the same rule), default `30.0` | yes |
| `voice_settings` | `mapping(...)` | a mapping, absent, or null, which answered `{}`; never a list or a scalar | `VoiceSettings`, `extra="forbid"`, with null dropped as unwritten | yes; `""` widens from refused to unwritten, per the rebase note below |
| `voice_settings.stability` | `read_voice_settings` | an int or a float or null, never a bool or a string | `OptionalNumber` | yes |
| `voice_settings.similarity_boost` | `read_voice_settings` | the same | `OptionalNumber` | yes |
| `voice_settings.style` | `read_voice_settings` | the same | `OptionalNumber` | yes |
| `voice_settings.speed` | `read_voice_settings` | the same | `OptionalNumber` | yes |
| `voice_settings.use_speaker_boost` | `read_voice_settings` | true, false or null; never `1` | `StrictBool \| None` | yes |
| any other `voice_settings` key | `read_voice_settings` | refused, naming the unknown keys and the known ones | `extra="forbid"`, refused at `/voice_settings` without naming them | refusal is now value-free and earlier |
| any other option | `finish()` | refused, naming the unknown keys | `extra="forbid"`, refused without naming them | refusal is now value-free and earlier |

The deliberate divergences, all in the changelog: the explicit nulls on
`model` and `output_format`, the unknown key refused at write rather
than at build and without echoing the key, and the output format refused
by its rule rather than quoted back.

The one option whose absence had a second spelling is `voice_settings`,
and the two beside it deliberately do not: the reader read them with
`string(key, default)` and nothing after it, so a blank there was a
value rather than a way of writing nothing. That is the whole of the
parity decision the rebase asked for, and the section below records how
it was reached.

### The rebase onto the restructured M1

M1 merged as PR #275 after a review round that restructured the base
this milestone was written against, so the five commits above were
rebased onto it and a sixth was written for what the restructuring
asked. Everything else in this section is read at the resulting tip.

What the round changed, and what each meant here:

- **`DECLARED_OPTIONS` and the per-type factory closures are gone.**
  One table, `PROVIDER_TYPES`, keyed by stage and then by type, each
  entry a frozen `ProviderType(module, attribute, options, extra)` that
  NAMES its factory rather than closing over an import.
  `providers/registry.py` resolves those names and derives its
  registrations by comprehension. The two conflicts this produced
  resolved by re-landing: the model classes are unchanged, and the
  registration is now `options=ElevenlabsOptions` on the existing
  `("tts", "elevenlabs")` entry. The registry file ends up
  byte-identical to main's, which is the reconciliation's own receipt:
  a converted type costs one argument on the table and nothing on the
  provider side.
- **A build refusal now carries an empty chain.** No M2 case asserted a
  kept cause, so none had to be inverted; the two build-path cases this
  milestone touches already asserted the no-leak shape.
- **`Numbers` gained a `json_schema_input_type`.** Kept as merged. The
  `OptionalNumber` annotation this milestone adds needs no equivalent
  and says so where it is declared: what its validator takes and what
  its annotation states are both "a number, or null", so there is
  nothing for a published schema to get wrong.
- **Blank spellings of an absent option read as unwritten.** This is
  the one that asked for work, and its answer is in the commit and in
  the parity table above: `voice_settings` has the history and the two
  string options do not. The walk itself became a shared function,
  because which keys a type lists is the type's own fact while what
  happens to a blank one is not, and a second copy of the
  comprehension would be the second statement of one rule.

The rebase moved no artifact. All six documents were regenerated on the
restructured base and none of them differs from what the milestone's own
artifact commit produced, which is what the blank rule being deliberately
absent from the published schema means in practice.

### Artifact churn

The same three of the six moved, measured at the tip against
`origin/main`, and the two the plan says must not move did not.

- `docs/reference/domain-config.md` (+33, -8): the `#88` note's list of
  declared types gains `tts elevenlabs` (which reflowed the paragraph,
  which is the eight deletions), and the new `#### \`tts\` options for
  \`type: elevenlabs\`` subsection with its six-row table and the nested
  `voice_settings` table under it. That nested table has no `| ... |`
  pass-through row where `vad_parameters` has one, which is the two
  models' `extra` setting rendering itself.
- `docs/reference/api-openapi.json` (+126, -2): the
  `TtsElevenlabsOptions` and `VoiceSettings` components, and the
  provider PUT's description gaining the second row of its mapping. The
  two deletions are the two descriptions that carry the type list.
- `docs/reference/cli.md` (+27, -0): the `set provider` epilog's
  `options for tts type elevenlabs:` listing, six fields and five nested
  ones at the path a fragment writes them at.
- `docs/reference/events.md`, `docs/reference/conversations-schema.md`:
  byte-identical, checked at the tip by regenerating both and by
  `git diff origin/main HEAD`, which lists neither.

The reference's stage-then-type grouping holds: `asr faster_whisper`
comes before `tts elevenlabs` on the page, which
`test_the_typed_options_are_grouped_by_stage_and_then_by_type` asserts
against `PROVIDER_STAGES` rather than against the declaration's
insertion order.

### Verification

From `vinga-server/`, at the tip of the milestone.

- `uv run ruff check .`: `All checks passed!`
- `uv run mypy`: `Success: no issues found in 4 source files`
- `uv run pytest tests/unit -q -n auto --dist loadfile`:
  `3325 passed, 19 skipped in 42.38s`
- `uv run pytest tests/integration -q`: `126 passed in 174.16s (0:02:54)`
- The six drift checks as CI runs them: all six clean, including the two
  that must not move.
- `uv sync --frozen`: `Checked 99 packages in 1ms`
- The three import-weight pins deviation 1 forced: green, and their
  files untouched by this milestone (`git diff origin/main HEAD` lists
  none of the three). No allow-list moved: `ElevenlabsOptions` lives in
  the module M1 already added to them, which is what one home for the
  declaration buys per type from here on.

### PR review round

External review of PR #276, 2026-08-24. Four findings, verdict mergeable
after fixes; each fix is its own commit and each is recorded here with
what it changed and what proves it.

**1 (P2): the published schemas accepted what the model refuses.**
`Nonblank` and `PcmFormat` are `AfterValidator`s, and an annotation
carrying one describes an unrestricted string, so the document told a
client that any `voice_id` and any `output_format` would do while the
server refused a blank one and a non-PCM one. The same contradiction
M1's own round resolved for the temperature ladder, in the shape an
`AfterValidator` produces it.

*Resolution.* Each rule is one constant used twice: the validator runs
it and `WithJsonSchema` publishes it, as `\S` and `^pcm_[0-9]+$`.
Published rather than enforced a second time, because pydantic's pattern
message names a regex and what an operator should read is "must not be
blank"; the format pattern lost its capture group with nothing lost,
since the rate is read with `removeprefix`. Asserted on both published
surfaces and against the patterns themselves, so a drift either way
fails: what each pattern matches is accepted and what it excludes is
refused. `api-openapi.json` moved by the two lines this adds.

**2 (P2): `examples/README.md` still named one declared type.** A reader
editing `tts-elevenlabs.yaml` was told its options are passed through
when they are checked at the write and refused by name. The reference's
own note could not lag that way because a test reads the declaration for
it; this prose had no such test, which is exactly why it lagged.

*Resolution.* The sentence lists the stage-and-type pairs, and a new
case in `test_config_examples.py` holds it to the declaration with the
same two halves `test_config_entities.py` uses on the note: every
declared pair is named, and no type that declares nothing is. Checked by
removing the type from the sentence, which fails the case. M3's type
cannot lag the same way.

**3 (P2): the request cases bypassed the converted handoff.** They
construct `ElevenLabsTts` directly, which asks what the class does with
what it holds and cannot ask whether the builder gives it what the entry
says. So the one thing this milestone changed was the one thing they did
not cover.

*Resolution.* Two cases go from a fragment through `build_entry` to the
bytes on the wire, with the transport put in by replacing the client
class the builder reaches for rather than by reaching into the object it
built, which keeps `build` the real one and puts `timeout_s` in reach
(a timeout is not a body field; it is what a request carries from the
client it was made by). Every value is deliberately non-default, so a
dropped forwarding shows up as the default arriving.

The proof, run by breaking one forwarding at a time and running the two
new cases beside the two old ones:

| Broken | The new pair | The old pair |
| --- | --- | --- |
| `model` not forwarded | 1 failed, 1 passed | 2 passed |
| `output_format` not forwarded | 1 failed, 1 passed | 2 passed |
| `language_code` not forwarded | 1 failed, 1 passed | 2 passed |
| `timeout_s` not forwarded | 1 failed, 1 passed | 2 passed |
| `voice_settings` not forwarded | 1 failed, 1 passed | 2 passed |
| `exclude_unset` dropped | 2 failed | 2 passed |
| `sample_rate` not forwarded | 1 failed, 1 passed | 2 passed |
| nothing (restored) | 2 passed | 2 passed |

The right-hand column is the finding, measured: seven ways of deleting
this milestone's work, and the cases that were there before catch none
of them.

**4 (P2): the one widening was recorded and not pinned.** Sharing one
notion of blank with `vad_parameters` took `voice_settings: ""` from
refused to unwritten. This document said so; no parity row and no
changelog line did, so the only thing in the milestone that accepts more
than the reader did was the one thing a reader of either could not find.

*Resolution.* A parity row, a `model_fields_set` assertion beside the
null that did not move, and a changelog sentence. It stays deliberate
and cheap: a mapping written as `voice_settings:` with nothing after it
is a null in YAML, so the spelling this widens is one nobody writes.

### Verification after the review round

From `vinga-server/`, at the tip.

- `uv run ruff check .`: `All checks passed!`
- `uv run mypy`: `Success: no issues found in 4 source files`
- `uv run pytest tests/unit -q -n auto --dist loadfile`:
  `3330 passed, 19 skipped in 43.76s`
- `uv run pytest tests/integration -q`: `126 passed in 175.02s (0:02:55)`
- The six drift checks as CI runs them: all six clean. Only
  `api-openapi.json` moved across the round (+2, -0), which is finding
  1's two patterns; the two that must not move did not.
- `uv sync --frozen`: `Checked 99 packages in 1ms`
- The three import-weight pins: green, and their files untouched by the
  round (`git diff 14ed7f7f HEAD` lists none of the three).

## M3: openai_compatible

### What was done

Six commits: the model, the builder and the forwarding, the tests, the
three documents with the fragment, this record with the changelog and
the tick, and the one the fragment's own prose asked for.

**The model.** `OpenaiCompatibleOptions` declares the three options the
builder read, with the example fragment's own sentence on each field,
and it is the one model in the file with `extra="allow"`. Two of the
three are `Nonblank`, which is what `required_string` demanded of
`base_url` and `model`; `max_tokens` is a `StrictInt` with the cap the
kit has always applied, stated as the number for the reason
`ElevenlabsOptions.timeout_s` is (the kit speaks httpx, and this module
is on three paths that load no client library) and pinned against
`DEFAULT_MAX_TOKENS` by a case on the side that may import both.

The hatch is stated on the model's own docstring, which is what the
JSON Schema carries and, in its first paragraph, what the reference
prints above the table. The component the OpenAPI document gains says
the same thing structurally: `additionalProperties: true`, where the
other two declared types say `false`.

**The reserved set.** `RESERVED_REQUEST_FIELDS` names the seven fields
the type composes for every request, and a passthrough key taking one of
them is refused by a model-level validator that raises
`FieldProblemsError`, so the name reaches the pointer as well as the
sentence. Naming it is the one exception to the rule that a caller's key
is never printed, and the exception is argued where the set is declared:
these are the repository's own seven words, published in the schema on
the model, so saying which one was written tells an operator what to
remove without repeating anything they invented. Two of the seven
(`model`, `max_tokens`) are declared fields, so a passthrough key cannot
take those names at all; they are in the set because what the set states
is which fields the request composes, not which of them this model
happens to declare.

**The builder, and the hatch taking effect.** `build(label, config,
options)` reads attributes; the reader ladder and its `finish()` are
gone. `finish()` is the part that could not simply be deleted: it
refused every leftover, so no option ever reached configuration and went
nowhere, and dropping it without forwarding would have made the hatch
silently ignored configuration, which is the failure the issue exists to
remove. So `model_extra` travels to the provider and rides into the
outgoing request through the SDK's `extra_body`, which is merged into
the JSON at the top level and is the only way to send a parameter this
dialect does not name: `create` takes the dialect's fields by name and
nothing else, so a merged `**request` would be a TypeError on the first
server-specific key.

The provider drops the reserved names from what it was handed rather
than trusting that validation caught them. That is not belt and braces,
it is the direction of the SDK's own merge: `_merge_mappings(json_data,
extra_json)` puts `extra_body` OVER the fields the caller set, so
"merged under the fixed fields" is kept by never letting one of those
names into the passthrough, on both sides of the seam.

**What stayed where it was.** `parse_base_url` is still called in the
builder. The model replaces `required_string` and nothing more: what
`parse_base_url` asks is a question about an endpoint rather than about
a string, all three stages speaking this dialect ask it, and it lives in
the module that owns that question. The URL-credential rule is still the
write path's, because it reads values at every depth rather than fields.
Both are pinned as acceptance at the model, so this model quietly
gaining a rule that already has a home is a failure rather than a
silent duplication.

**The registration and the renderings.** One argument on one line, and
`providers/registry.py` is untouched again. All four renderings picked
the type up from `declared_options()`.

### Deviations from the plan

Two, and both are readings of the plan's own words recorded rather than
departures from its intent.

**1. `max_tokens` is a `StrictInt` with no numeric bound.** The plan
says "bounded int with today's default". Read as the option that bounds
the reply rather than as an option with bounds of its own, because the
reader it replaces was `integer("max_tokens", DEFAULT_MAX_TOKENS)`,
which took any int, and the standing discipline of this batch is that a
converted type accepts exactly what its reader accepted, with every
tightening decided and written into the changelog. There is no evidence
here for a floor or a ceiling, and inventing one would be a tightening
on the axis this batch has no reason to touch, an operator's existing
file rather than an operator's typo.

**2. The example fragment does not document `max_tokens`.** It cannot,
and the reason is discovery 1 below.

### Discoveries

**`max_tokens` cannot be written into a provider entry at all, and
never could.** The name contains "token", so
`secret_option_fragment("max_tokens")` matches and `ProviderConfig`'s
`_reject_inline_secrets` refuses the key as an inline secret. This is
not new and it is not this milestone's: it is true on `main`, for every
provider type, through every door a `ProviderConfig` is validated
behind, so it holds for the YAML file, the API and the CLI alike. The
`anthropic` type has read a `max_tokens` option through its own ladder
the whole time, and no fragment could ever supply one.

It surfaced here because this is the first milestone to document the
option: the example fragment gained a commented `# max_tokens: 1024`
line, `test_every_documented_option_of_a_typed_type_installs`
uncommented it and installed it, and the write refused it. The commented
key came back out, since a fragment must not document a key that cannot
be installed, and that suite is exactly the promise that it does not.

Nothing else was changed for it, deliberately. The fix is a change to a
security rule shared with the MCP entries (a word-boundary match, or an
exemption for names a type's own model declares), which is a decision of
its own and not one to take inside a milestone that is converting a
provider type. What M3 leaves behind is a field the reference documents,
the builder reads and the write path refuses, which is an honest
statement of the contract with a defect visible beside it rather than a
contract quietly narrowed to fit the defect.

**An open door lets a sentence become an option.** The fragment's
uncommenting case is a scan, and a scan cannot tell a documented key
from a sentence that happens to contain one: a paragraph reading `That
is what the type is for: every server ...` wraps so that a line begins
`for: every server ...`, which is a `key:` line to the regular
expression. Under a type whose model shuts its door the mistake is
caught by the install, loudly, which is how M2 found the `Default:` and
`API:` paragraphs. Under this one it is not caught at all: the model
keeps what it does not declare, so `for` would have installed, and it
would have been sent to the endpoint on every request.

Found while writing the fragment, and fixed twice over: the sentence is
worded so no line begins that way, and
`test_an_open_doors_fragment_documents_only_real_options` compares the
keys that survive the uncommenting against what the type declares, with
the one documented passthrough (`top_p`) named rather than counted.
Restoring the earlier wording fails it on `for`. The rule this depends
on, that a documented key is a line of its own rather than a phrase
inside a sentence, is not held repository-wide: four other fragments
have prose lines the same scan misreads, all of them under types that
declare no model, so none of them is uncommented by anything today. A
general check belongs with whichever milestone converts one of those
types.

**The untyped-type scan in the options note matched on a substring.**
`test_the_options_note_names_the_types_that_declare_a_model` checked
that no untyped type name appears in `OPTIONS_NOTE`, by asking whether
the bare type name is in the sentence. `openai` is an untyped ASR and
TTS type and a substring of `openai_compatible`, so declaring this type
made the note "name" a type that declares no model. The scan asks for
the stage-and-type pair now, which is the spelling the note actually
uses and the one the declared half was already checked with.

**The URL-credential cases needed a model.** Three fragments in
`test_config_store.py` wrote an `openai_compatible` entry with a
`base_url` and no `model`, because nothing used to require one at write
time. The options check runs before the URL rule, so those fragments
started being refused for a missing field and proved nothing about the
rule under test. They carry a model now, and one case was added beside
them: a credential-bearing URL under a key this type does not declare,
which is the question the hatch raises and which the rule still answers,
because it reads values at every depth rather than declared fields.

### Coercion parity, inventoried call by call

The reader call the builder made, what it accepted, and what the model
does now. Held by `OPENAI_PARITY` in
`tests/unit/test_provider_options.py`, which runs in every lane.

| Option | Reader call | Accepted then | Accepted now | Same? |
| --- | --- | --- | --- | --- |
| `base_url` | `required_string(...)` | a non-blank string; absent, null or blank refused as "is required" | `Nonblank` (`StrictStr` plus a not-blank rule), required | yes, and the two refusals are now separate sentences |
| `model` | `required_string(...)` | the same | `Nonblank`, required | yes |
| `max_tokens` | `integer(..., 1024)` | an int; never a bool, a float or `"1024"`, and never a null, since it measured whatever it popped | `StrictInt`, default 1024 | yes |
| any other key | `finish()` | refused at build, naming the unknown keys | `extra="allow"`: kept, and forwarded into the request body | no, and this is the milestone |
| a key named for a request field | `finish()` | refused at build, naming it | refused at write by name, value-free | the refusal moves earlier and stays by name |

The one deliberate divergence is the fourth row, and it is in the
changelog as a loosening rather than a tightening: an option that
reached configuration used to fail the build and now reaches the
endpoint.

**The blank spellings, and why this type has none.** The first two
converted types each carry a list of options whose absence had a second
spelling, read off the readers they replaced. This reader had neither
shape. `base_url` and `model` were `required_string`, which refused a
blank as loudly as an absent key, so an empty string was never a way of
writing nothing there; `max_tokens` was `integer(key, default)` with
nothing after it, so a null was refused rather than swallowed. There is
therefore no `_OPENAI_BLANK_IS_UNWRITTEN` set and no
`_blank_reads_as_unwritten` validator on this model, and
`test_this_type_has_no_blank_spelling_of_an_absent_option` is where that
decision is written down.

### The compatibility fixture, and why no decision was needed

`tests/unit/data/domain-bodies/provider/every-field.json` is the
historical body for this type and needed no companion: it was written as
an `openai_compatible` entry carrying options no builder read, to prove
a body could hold anything and still parse. It parses unchanged through
the new model, and its four unread keys (`temperature`,
`max_reply_length`, `stop`, `connection`) are `model_extra` now, which
is what the provider forwards. That is asserted rather than argued, in
`test_the_options_no_builder_accepted_are_the_ones_that_now_travel`: a
model that shut its door would fail the suite's own floor case, and one
that kept the door open and dropped the keys would fail this one.

### Artifact churn

The same three of the six moved, measured at the tip against M2's, and
the two the plan says must not move did not.

- `docs/reference/domain-config.md` (+23, -8): the `#88` note's list of
  declared types gains `llm openai_compatible` at the front (which
  reflowed the paragraph, which is the eight deletions), and the new
  `#### \`llm\` options for \`type: openai_compatible\`` subsection with
  its three-row table. That table carries the `| ... |` pass-through row
  neither of the other two types' own tables has, which is the model's
  `extra` setting rendering itself; the only other place the row appears
  under a typed type is faster_whisper's nested `vad_parameters`, whose
  door is open for its own reason.
- `docs/reference/api-openapi.json` (+30, -2): the
  `LlmOpenaiCompatibleOptions` component, with
  `"additionalProperties": true` and the hatch stated in its
  description, and the provider PUT's description gaining the third row
  of its mapping. The two deletions are the two descriptions that carry
  the type list.
- `docs/reference/cli.md` (+12, -0): the `set provider` epilog's
  `options for llm type openai_compatible:` listing, three fields.
- `docs/reference/events.md`, `docs/reference/conversations-schema.md`:
  byte-identical, checked at the tip by regenerating both and by
  `git diff origin/main HEAD`, which lists neither.
- Beside the six: `examples/llm-openai-compatible.yaml` (+11) documents
  the hatch and `top_p` as its one commented example, and
  `examples/README.md` (+6, -2) names all three declared types and says
  which of them keeps its door open.

The reference's stage-then-type grouping holds and is now visibly
ordered by the pipeline rather than alphabetically: `llm
openai_compatible` comes before `asr faster_whisper`, which
`test_the_typed_options_are_grouped_by_stage_and_then_by_type` asserts
against `PROVIDER_STAGES`.

### Verification

From `vinga-server/`, at the tip of the milestone.

- `uv run ruff check .`: `All checks passed!`
- `uv run mypy`: `Success: no issues found in 4 source files`
- `uv run pytest tests/unit -q -n auto --dist loadfile`:
  `3367 passed, 19 skipped in 44.79s`
- `uv run pytest tests/integration -q`: `126 passed in 190.89s (0:03:10)`
- The six drift checks as CI runs them: all six clean, including the two
  that must not move.
- `uv sync --frozen`: `Checked 99 packages`
- The three import-weight pins deviation 1 of M1 forced: green, and
  their files untouched by this milestone. No allow-list moved:
  `OpenaiCompatibleOptions` lives in the module M1 already added to
  them, which is the second time a converted type has cost nothing
  there.
