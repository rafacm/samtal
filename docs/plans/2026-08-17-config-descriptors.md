# Single-source the domain config schema behind per-entity descriptors

## Goal

Implement issue #139: a domain entity today exists in up to 11
parallel encodings, and the number is measured rather than
estimated: adding `prompt_fragments` (the most recent entity)
touched 13 hand-edited config-surface files, and the per-entity
site count runs 15 to 19. Collapse the per-entity knowledge into
the pydantic models plus one descriptor per entity that store,
views, api, cli, and docgen derive from, unify the CLI's local and
HTTP dispatch so acknowledgements and notices come from one place,
and delete the hand-rolled CLI response predicates in favor of the
API's own response models.

This is a refactoring issue under a no-behavior-change contract,
and the plan is sized to the issue's letter: where the inventory
found adjacent defects or tempting extensions, they are recorded
and filed, not absorbed.

The companion implementation doc,
[`2026-08-17-config-descriptors-implementation.md`](2026-08-17-config-descriptors-implementation.md),
records what each milestone actually did, deviations from this
plan, and discoveries; a milestone with no deviations says so
explicitly.

## The issue's decisions, restated

Settled by issue #139 and not re-litigated here:

1. **One descriptor per entity**, fields decided in this plan
   (identity/addressing, store mapping hooks, view masking, API
   routing facts, CLI rendering facts, docgen prose hooks),
   consumed by store, views, api, cli, and docgen. The models stay
   the source of field truth; the descriptor carries what the
   model cannot.
2. **Store row mapping goes through `model_validate`/`model_dump`**,
   preserving the `model_fields_set` omission semantics
   `_mcp_from_row` needs.
3. **The CLI renders API responses from the same pydantic response
   models api.py declares**; the hand-rolled frozensets and
   predicates are deleted.
4. **`--local` stays exactly the current four commands**; local
   and HTTP branches unify behind one dispatch so acknowledgements
   and notices come from one place, making the #134 class of drift
   structurally impossible.
5. **The recursive fragment walkers consolidate where the
   descriptor makes that natural; behavior identical.**
6. **Out of scope**: #88 (typed provider option models) stays its
   own issue; the OpenAPI hand-assembly mechanics (`RawBody`,
   `_entity_schemas`) keep their current honest-document behavior,
   byte-identical; the server half (`ServerConfig`) documentation
   gap.

The no-behavior-change contract: `docs/reference/domain-config.md`
and `docs/reference/api-openapi.json` regenerate byte-identical
(the CI drift checks are the proof); the CLI acceptance test and
examples-driven tests pass unmodified; every refusal sentence and
notice is unchanged.

## Evidence, re-verified at plan time

The issue's evidence is pinned to main@8dd1a5f, 33 config commits
behind main@a1d5dd2, and `prompt_fragments` did not exist at the
pin. The full inventory (per-entity encoding map with anchors, the
dispatch map, the walker family, the command-string encodings, the
contract surfaces, the test terrain) was taken fresh at a1d5dd2
and is recorded in the implementation doc's preamble at M1; the
plan below cites its load-bearing facts:

- **Per-entity sites**: provider 18 sites/13 files, mcp-server
  19/14, prompt-fragment 15/12, agent 15/12, agent-defaults 14/12;
  the non-fragment surfaces (devices, default-agent, secrets)
  13 to 16 sites each. The issue's "11" was low.
- **Three store mappings `model_dump()` cannot express**, which
  decision 2's parenthetical anticipated for one:
  `_mcp_from_row` omits keys per six distinct per-column rules so
  `McpServerConfig`'s transport validator can read
  `model_fields_set`; `_layer_values`/`_layer_data` encode the
  tri-state None-inherits/empty-opts-out and re-emit each MCP
  grant in the form it was written so older readers keep reading
  the column; `_provider_from_row`/`_provider_values` split
  declared fields from the `options` extras of an `extra="allow"`
  model. `models.domain_fields` exists precisely to avoid a dump
  round-trip, and says so.
- **The five fragment entities are not uniform**: `agent-defaults`
  is a singleton with no delete anywhere (route, subparser,
  sentence) and a constant acknowledgement; `prompt-fragment` and
  `agent` write inline dicts with no `_values` helper;
  `prompt-fragment` checks its name before parsing for a
  documented ordering reason; `mcp-server` runs
  `check_mcp_entry_names` inside its write; `provider` alone runs
  URL-credential checks and is addressed by two path segments.
- **`docgen` already has three tiers**: 7 `ENTITIES` (five
  commanded, two nested with `command=None`), 2 `SETTINGS` with a
  different renderer, plus prose-only secrets.
- **The CLI must not import FastAPI**: `writes.py`'s docstring
  records that the CLI importing the API was rejected so
  `config schema`/`config reference` never pay for FastAPI, and
  `test_config_docgen.py` plus the CI docs lane enforce it. The
  response models decision 3 needs therefore cannot live in
  `api.py` for the CLI's purposes.
- **Deliberate non-derivations that must survive**:
  `views.provider_record` is built key by key so a new model field
  is absent from records until someone decides it belongs (its
  docstring says so, and `device/session.py` consumes it);
  `RawBody` plus `openapi_extra` exists because FastAPI validation
  echoes rejected input, which may hold a pasted credential.
- **The dispatch today**: one `--local` gate plus 14 per-command
  `if args.local:` branches; the notice constants are unified
  since #134 but are chosen at two call sites per act, with two
  single-sided nuances (the API alone computes
  `binding_notice(unloaded)`; the CLI alone maps
  `secret_notice(kind)` where the API splits it across four
  routes).
- **The shape bridge to delete**: five predicate constants at
  `cli.py:159-190`, nine predicate functions, four renderers, and
  `tests/unit/test_config_cli_shapes.py`, whose docstring says it
  exists to be deleted wholesale by this issue.
- **Command strings**: seven independent encodings (loader,
  docgen, config.example.yaml, examples headers, examples README,
  the deploy script, the smoke seed script) plus ~20 README
  occurrences; the loader/docgen pair is byte-identical
  placeholder text with no shared constant.
- **The acceptance surfaces**: `test_config_cli.py` (2305 lines,
  101 tests) and the examples-driven tests are the contract files;
  the two generated references have CI drift steps; the refusal
  constants number 20 symbols in writes.py, 21 strings landing in
  the OpenAPI document, and dozens of raise-site sentences across
  store, cli, models, secrets, loader.

## Decisions this plan makes

### The descriptor: three tiers, one module

`samtal_server/config/entities.py`, importing models (and nothing
above them), holding frozen dataclasses that EXTEND today's
`docgen.Entity` rather than inventing a parallel scheme:

- **`EntityDescriptor`** for the five commanded fragment entities:
  the existing docgen fields (name, title, location, model,
  purpose, command, examples, notes, fields_in_help), plus
  addressing (URL path segments and CLI argument names: provider's
  two segments are data, not a special case), store facts (table,
  a `to_row`/`from_row` hook pair defaulting to the
  model-validate/model-dump path and overridden exactly where the
  inventory proved a custom mapping is load-bearing, the
  pre-parse/inside-write check hooks the three quirky entities
  need), view facts (the body builder or the mask hook it needs),
  API facts (route prefix, has_delete, secret slots or none,
  and each endpoint's stable operation identity, exact
  description, response and status declarations, and parameter
  signature, because the committed OpenAPI document derives its
  summaries, operation ids and parameter ordering from today's
  named functions and docstrings and those bytes are contract),
  the missing-entity refusal builder (fragments answer the fixed
  `NO_SUCH_FRAGMENT` without repeating the unvalidated name, the
  others keep their exact sentences, the singleton has no missing
  case), CLI
  facts (subparser names/help, the summary renderer hook), writes
  facts (the wrote/deleted sentence builders, the notice chooser),
  and the loader moved-key command.
- **`NestedShape`** for `mcp-grant` and `filler`: what docgen's
  commandless entries carry today, nothing more.
- **`Setting`** for devices and default-agent: docgen's `SETTINGS`
  facts plus their API/CLI/writes anchors, without pretending they
  are fragment entities (no envelope, their own body shapes).

The singleton-without-delete is a descriptor fact
(`has_delete=False`), not a code branch; the constant
acknowledgement stays a constant, referenced by the descriptor.
The descriptor module has one registry tuple per tier, and docgen
consumes them (its own `Entity`/`SETTINGS` definitions retire).

### Store mapping: the default path and the three proven overrides

Per decision 2, the default `from_row`/`to_row` goes through
`model_validate`/`model_dump` and is what `prompt-fragment` and
`agent`'s inline dicts collapse into. The three mappings the
inventory proved unexpressible stay as explicit named hooks on
their descriptors, moved but not rewritten: the MCP omission rules
(all six), the layer tri-state with as-written grant re-emission,
and the provider field/options split. The hooks live beside the
descriptor so a new entity sees the default path and pays for a
hook only when its model demands one, which is the per-field cost
the acceptance criterion measures. `_read_domain`, the per-entity
CRUD, and the secret plumbing generalize over descriptors; the
`EntityKind` two-member literal for secrets stays exactly two
members (a descriptor fact `secret_slots`, None for the other
three).

### The response models move below FastAPI

Decision 3 collides with the recorded constraint that the CLI must
not import FastAPI. Resolution: the pydantic response models
(`Envelope`, `ConfigDocument`, `PendingDevice`, `McpServerStatus`,
`McpReloadResult`, `PromptBlock`, `AssembledPrompt`,
`DefaultAgent`, `SecretSlot`, `StoredSecretLocation`, `Problem`,
`Acknowledgement`, and the three request models) move to
`samtal_server/config/responses.py`, importing pydantic only.
`api.py` imports them from there (its OpenAPI output must stay
byte-identical, which pydantic model identity by name preserves;
the drift check is the proof), and `cli.py` renders every HTTP
answer through ONE response-validation helper: strict-mode
`model_validate` against the matching response model, ignoring
unknown response fields exactly where today's predicates tolerate
a newer server's extras, rendering validated fields only, and on
failure discarding the `ValidationError` inside the handler and
raising the existing fixed `UNRECOGNIZED_ANSWER` sentence only
after the `except` block exits, `__cause__` and `__context__`
both empty, because `ValidationError.errors()` retains the
rejected input and the acceptance suite requires malformed bodies
to reach no surface. That helper is what deletes the frozensets,
the nine predicates, and `test_config_cli_shapes.py`. The rendering functions keep their
exact output sentences and column layouts: the change is where the
shape knowledge comes from, not what is printed. The docs-lane
tests (no FastAPI, no database, no key) keep passing because
`responses.py` is pydantic-only.

### One dispatch table for the CLI

Each CLI act becomes a row: the HTTP method and path builder, the
local fallback (present for exactly the four recovery commands,
absent otherwise), the acknowledgement rendering, and the notice
choice, in one place per act. The 14 `if args.local:` branches
collapse into one dispatcher reading the row; the `--local` gate,
`LOCAL_NOTICE`, and `LOCAL_SUBSET` stay as they are. The two
single-sided nuances are preserved as row facts, not re-derived:
the local device delete keeps printing plain `BINDING_NOTICE`
(it has no loaded-agent set to compute against, exactly as today),
and the secret rows carry `secret_notice(kind)` on the CLI side
while the API keeps its static per-route choice. The per-act proof
compares the shared acknowledgement and the act's notice
byte-for-byte after the unchanged local-only preamble
(`LOCAL_NOTICE` prints on every local invocation by design, so
whole-invocation equality is not the claim), porting the
acceptance suite's existing local-versus-API proof.

### The walkers: consolidate two, leave four, file one finding

Decision 5 says consolidate where natural, behavior identical.
Natural: `_nonfinite` is a strict subset of `_untransportable`'s
float branch and both live in store.py; they merge into one walker
with a mode flag, byte-identical messages. Not natural, so not
touched: `check_no_inline_secrets` (a model validator with its own
raise type and `_env` rule), `masked_option`/`recorded_option`
(near-twins whose twin-ness is the deliberate non-derivation
`provider_record` documents), and `shown_values` (flat on purpose
or by accident, but changing it changes behavior). The inventory's
finding that `shown_values` does not mask a nested MCP secret
where provider options are masked recursively is a REAL surface
question this issue must not answer silently: it was filed as
issue #171 during M1.

### The command strings: single-sourced where code reads them

The descriptor's `command` field becomes the one source for the
two code encodings (docgen prose and `loader.MOVED_KEY_COMMANDS`
derive from descriptors). The five non-code encodings (example
headers, the example README, `config.example.yaml`, the deploy
script, the smoke seed script) are concrete invocations a person
copies, already drift-checked by the tests that execute them; they
stay as text, and the plan deliberately does not invent a
generator for them. The bare `"samtal-server config set"` refusal
in store.py stays byte-identical (naming the full command would
change a pinned sentence; noted for a docs follow-up, not done).

### The test split: #144's inherited buckets, executed here

Per the coordination settled in #144's plan, `test_config_cli.py`
splits along this issue's produced boundaries into the inherited
buckets: the acceptance spine stays in `test_config_cli.py`;
transport/client behavior, response rendering, the recovery
subset, secrets, and parser grammar each get their file, named in
the `test_config_api_*` style. The split is a move: assertions
byte-identical, counts non-decreasing, performed AFTER the
dispatch and rendering land so the boundaries match the code that
exists. The `#144` support-module rule holds (no test imports a
test module; shared helpers to `tests/support`).

### What is deliberately not done

- No typed provider options (#88), no OpenAPI mechanics changes,
  no `ServerConfig` docs work (the issue's own exclusions).
- No new entity kind, no schema or migration changes: the
  descriptor describes what exists.
- No masking behavior changes anywhere (the `shown_values` gap is
  filed, not fixed).
- No renaming of any command, route, field, sentence, or notice.
- The events registry (#155) is untouched; the config package
  emits no events except through `api.py`'s existing emitter, and
  no emit site moves.

### Five milestones, five PRs, stacked

Every merge leaves `main` releasable; each PR is one concern.

1. **M1, the descriptors exist and docgen consumes them**:
   `entities.py` with the three tiers populated for everything
   docgen renders today; docgen's own `Entity`/`SETTINGS` retire
   in favor of the registry; `domain-config.md` and
   `api-openapi.json` regenerate byte-identical (the proof this
   was a move); the `shown_values` finding filed as an issue; the
   fresh inventory recorded in the implementation doc. Milestone
   duties (implementation section, changelog, tick) as always.
2. **M2, store and views generalize over descriptors**: the
   default row path plus the three named hooks; per-entity CRUD
   and `_read_domain` driven by the registry; the walker merge;
   views' entity bodies descriptor-driven with `provider_record`
   explicitly left alone. Store and views tests pass; any test
   pinning a deleted private helper is ported at equal strength,
   listed one by one in the implementation doc.
3. **M3, the response models move and api.py generalizes**:
   `responses.py`; api.py's routes and `ENTITY_MODELS` derived
   from descriptors, the route factory installing each endpoint's
   descriptor-carried operation identity, description, response
   and status declarations and parameter signature explicitly,
   with `RawBody` and the schema hoisting byte-identical; the
   OpenAPI drift check green with no regeneration commit is the
   milestone's core proof.
4. **M4, the CLI unifies and renders from response models**: the
   dispatch table; the four local rows; rendering via the
   sanitized response-validation helper; the frozensets,
   predicates, and `test_config_cli_shapes.py` deleted; notices
   provably identical per act (the acceptance suite's existing
   notice tests are the pins, plus per-act proofs comparing
   acknowledgement and notice byte-for-byte after the local-only
   preamble where both paths exist).
5. **M5, the test split and the cost demonstration**: the
   inherited buckets executed as a pure move; the acceptance
   criterion demonstrated (add a scratch field to a copy of one
   model in a test or in the implementation doc's walkthrough and
   count the touches: model, schema/migration, descriptor at
   most); CHANGELOG closes the issue's entry.

## Files touched

New: `samtal_server/config/entities.py`,
`samtal_server/config/responses.py`, the M5 bucket test files,
this plan's implementation doc.

Modified: `samtal_server/config/docgen.py` (M1), `store.py` and
`views.py` (M2), `api.py` (M3), `cli.py` (M4), `loader.py` (M4,
moved-keys derive), `writes.py` only if a sentence builder gains a
descriptor reference without changing its output (any milestone),
`CHANGELOG.md` (per milestone), `tests/unit/test_config_cli.py`
(M5 split, move-only), plus ported tests M2/M4 name.

Deleted: `tests/unit/test_config_cli_shapes.py` (M4, as it was
built to be).

Untouched on purpose: every sentence and notice constant's text,
`db/schema.py` and all migrations, `models.py` except
`domain_fields`'s stale "six" docstring word if a milestone is
already in that file (cosmetic, no behavior), `provider_record`,
`RawBody` and the OpenAPI hand-assembly, `config.example.yaml`,
`examples/`, both generated references (byte-identical is the
contract), the events registry.

## Verification

Per milestone, from `samtal-server/`: `uv run ruff check .`,
`uv run pytest tests/unit -q`, `uv run pytest tests/integration
-q`; the two docs drift checks run locally exactly as CI runs them
(byte-identical, no regeneration commit anywhere in the branch);
collected counts recorded (never lower; M5's split is count-equal,
M4's notice tests add); the contract files
(`test_config_cli.py` until M5's move, `test_config_examples.py`
both lanes) byte-unchanged except where a milestone explicitly
names a port; grep proof at M4 that no `PENDING_FIELDS`-family
name survives outside history. `PYTHONDONTWRITEBYTECODE=1`
outside pytest.

## Risks and mitigations

- **The OpenAPI document shifts from moving models.** Pydantic
  schema generation keys on model names and field definitions,
  which the move preserves; the byte-identical drift check runs at
  M3 before anything else lands on top, and a mismatch is a
  design error to fix, never a regeneration to commit.
- **The custom store hooks quietly normalize.** Each of the three
  hooks moves as a named function with its existing tests; M2
  lists every store test it ports and the implementation doc
  records the port table; the as-written grant re-emission has its
  own store test today and keeps it.
- **CLI rendering through models changes a sentence.** The
  renderers keep their output code paths; only the shape checks
  change source. The acceptance suite's 101 tests are the pin, and
  M4 adds the per-act local-equals-HTTP byte tests.
- **The split scatters a failing test's context.** M5 is
  move-only, after everything else is green, with the #144
  support rules already enforced by the import guard.
- **Scope creep via review.** This plan was sized deliberately
  after #155's experience: the review prompt will state the
  no-behavior-change bar and ask findings to name broken behavior
  or contract violations, and adjacent hardening beyond the
  issue's letter is answered by filing, not absorbing.

## Milestones

- [x] [M1](2026-08-17-config-descriptors-implementation.md#milestone-1-the-descriptors-exist-and-docgen-consumes-them):
      the descriptors exist and docgen consumes them (PR #172):
      `entities.py` holds the three-tier registry with the static
      identity facts filled and every later group named for the
      milestone that fills it, docgen renders it and its own entity
      dataclass and data tables are gone, both references regenerate
      byte-identical with no regeneration commit, the `shown_values`
      finding is filed as issue #171, and the fresh inventory is
      recorded in the implementation doc.
- [x] [M2](2026-08-17-config-descriptors-implementation.md#milestone-2-store-and-views-generalize-over-the-descriptors):
      store and views generalize (PR #173): default row path plus the
      three named hooks, registry-driven CRUD, the walker merge,
      `provider_record` untouched; ported pins listed one by one (there
      are none, and the one test that reaches into the store's privates
      names a helper that survives); milestone duties in the same
      change.
- [x] [M3](2026-08-17-config-descriptors-implementation.md#milestone-3-the-response-models-move-and-apipy-generalizes):
      the response models move below FastAPI and api.py generalizes
      (PR #174): `responses.py` holds the fifteen shapes and imports
      pydantic and nothing else, the five kinds' twenty-two routes are
      built from `Endpoint` facts on their descriptors with each
      route's operation identity, description, response and statuses
      installed explicitly, `RawBody` and the schema hoisting are
      untouched, `ENTITY_MODELS` derives from the registry, and the
      OpenAPI document regenerates byte-identical with no regeneration
      commit; the port table is empty; milestone duties in the same
      change.
- [ ] M4: the CLI unifies and renders from response models: one
      dispatch table, four local rows, frozensets and predicates
      and `test_config_cli_shapes.py` deleted, per-act
      local-equals-HTTP proofs; milestone duties in the same
      change.
- [ ] M5: the inherited test split executed as a pure move and the
      per-field cost demonstrated; CHANGELOG closes the issue;
      milestone duties in the same change.

## Plan review round

External review of commit 28abab2 by codex 0.147.0 (model
gpt-5.6-sol), 2026-08-17, prompted with the plan, the issue body,
the config package, the contract surfaces, and the maintainer's
review bar (findings name broken behavior, unimplementable design,
contract violations, or factual errors; simplifications over
elaborations). Five findings, verdict ready after the amendments;
as received, condensed but faithful, each with its resolution:

1. **P1: response-model validation can retain rejected secrets in
   exception chains.** `model_validate` without a sanitized
   boundary lets `ValidationError.errors()` carry the rejected
   input, and the CLI tests require malformed bodies to appear
   nowhere in output or the chain.

   *Resolution*: accepted. M4 introduces one response-validation
   helper that catches `ValidationError`, discards it, and raises
   the existing fixed `UNRECOGNIZED_ANSWER` sentence only after
   the handler has exited, `__cause__` and `__context__` both
   empty, each renderer's wording unchanged. Amended in the
   response-models section.

2. **P1: the descriptor lacks the endpoint metadata byte-identical
   OpenAPI needs.** Summaries, operation ids, descriptions,
   response declarations and parameter ordering derive from the
   current named functions and docstrings, and those bytes are
   committed contract.

   *Resolution*: accepted. The descriptor's API facts grow to
   carry each endpoint's stable operation identity, exact
   description, response and status declarations, and parameter
   signature, and M3's route factory installs them explicitly so
   the generated document stays byte-identical. Amended in the
   descriptor and M3 sections.

3. **P2: default pydantic validation changes the CLI's acceptance
   rules.** The response models forbid extras while the CLI
   tolerates unknown fields from a newer server, and the
   predicates reject coerced types where pydantic coerces.

   *Resolution*: accepted. The CLI validates in strict mode
   against the shared models but ignores unknown response fields
   wherever the current predicates do, rendering only validated
   fields, so compatibility neither narrows nor loosens. Amended
   in the response-models section.

4. **P2: generic CRUD has no descriptor fact for the
   prompt-fragment no-leak 404.** Fragments must answer the fixed
   `NO_SUCH_FRAGMENT` without repeating the unvalidated name.

   *Resolution*: accepted. The descriptor gains a missing-refusal
   builder or constant used by both read and delete; fragments use
   `NO_SUCH_FRAGMENT`, the others keep their exact sentences, the
   singleton has no missing case. Amended in the descriptor
   section.

5. **P2: full local and HTTP output cannot be byte-identical while
   `LOCAL_NOTICE` is mandatory.** Local stderr is the preamble
   plus the notice, not the HTTP stderr byte-for-byte.

   *Resolution*: accepted. The per-act proof compares the shared
   acknowledgement and the act's notice byte-for-byte after the
   unchanged local-only preamble, and M4 ports the acceptance
   suite's existing proof rather than asserting whole-invocation
   equality. Amended in the dispatch and M4 sections.
