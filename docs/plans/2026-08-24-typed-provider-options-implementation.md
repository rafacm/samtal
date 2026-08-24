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
| `model` | `string(..., "small")` | a string; absent or null gives the default | `StrictStr`, default `small` | null tightens |
| `language` | `string(...)` | a string or null | `StrictStr \| None` | yes |
| `device` | `string(..., "cpu")` | a string; absent or null gives the default | `StrictStr`, default `cpu` | null tightens |
| `compute_type` | `string(..., "int8")` | a string; absent or null gives the default | `StrictStr`, default `int8` | null tightens |
| `download_dir` | `string(...)` | a string or null | `StrictStr \| None` | yes |
| `language_fallback` | `string(...)` | a string or null | `StrictStr \| None` | yes |
| `beam_size` | `integer(..., 1)` | an int; never a bool, a float or `"5"` | `StrictInt`, default 1 | yes |
| `cpu_threads` | `integer(..., 0)` | an int; never a bool, a float or `"3"` | `StrictInt`, default 0 | yes |
| `vad_filter` | `boolean(..., False)` | true or false; never `1` or `"yes"` | `StrictBool`, default false | yes |
| `condition_on_previous_text` | `boolean(..., True)` | true or false; never `0` | `StrictBool`, default true | yes |
| `language_confidence_floor` | `number(..., 0.6)` | an int or a float, never a bool or a string, normalized to float | `Number` (a before-validator with the same rule) | yes |
| `temperature` | `numbers(...)` | a number as a list of one, a non-empty list of numbers, or absent; never a bool, an empty list, a string or a list holding either | `Numbers` (the same rule as a before-validator) | yes |
| `vad_parameters` | `mapping(...)` | a mapping or absent; never a list or a scalar | `VadParameters`, `extra="allow"` | yes |
| `language_detect` | `string(...)` plus a membership check | `every_utterance` or `once` | `Literal["every_utterance", "once"]` | yes |
| any other key | `finish()` | refused, naming the unknown keys | `extra="forbid"`, refused without naming them | refusal is now value-free and earlier |

Two deliberate divergences, both in the changelog. An explicit `null`
written where a defaulted option sits used to be read as the default
(`options.string("model", "small") or "small"`), and is refused now:
accepting it would also mean declaring `null` in the published schema
for a field that has no null meaning. And an unknown key is refused at
write rather than at build, without echoing the key, which is the
issue's point and the repository's standing rule about keys meeting.

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
