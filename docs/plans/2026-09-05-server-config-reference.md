# Generate the server-half configuration reference

Plan for [#396](https://github.com/rafacm/vinga/issues/396).
Implementation notes land in the companion
`2026-09-05-server-config-reference-implementation.md`, one section
per milestone, appended in the change that ticks the milestone here.

## Goal

The domain half of the configuration has a generated, CI-diffed
reference (`docs/reference/domain-config.md`); the server half has
none. Its only operator surface is the commented
`config.example.yaml` and README prose, so anyone deciding what a
deployment can configure reads a 378-line example file instead of a
reference page, and the deployment guide (#397) has no page to link
for "what can be configured". This plan adds a generated
`docs/reference/server-config.md`, rendered from the `ServerConfig`
models and held by the same regenerate-and-diff discipline as the
other generated references: every key, type, default and constraint,
the cross-field boot refusals, and the keys that deliberately have no
configuration form (`VINGA_DB_PASSWORD`, `VINGA_DB_URL`).

## The issue's decisions, restated

- A generated `docs/reference/server-config.md`, rendered from the
  `ServerConfig` models the way `domain-config.md` is rendered from
  the domain models, under the same CI drift check as the other
  generated references. Correcting the page means changing its
  generator.
- `config.example.yaml` stays: it is the annotated, copyable
  starting point; the generated page is the complete contract. The
  two describe the same models, so they may differ in coverage,
  never in fact.
- The capture section is about to change shape under #393;
  whichever lands second regenerates the page, which is the point of
  generating it.

## Where the facts already live

- The server-half models: `ApiConfig`, `AuthConfig`,
  `OnboardingConfig`, `LimitsConfig`, `CaptureConfig`,
  `DatabaseConfig`, `ConversationsConfig` and `ServerConfig`, all in
  `vinga-server/src/vinga_server/config/models.py`. Unlike the
  domain models, their field facts live in `#` comments, not in
  `Field(description=...)`: today exactly 1 of the 47 fields
  reachable from `ServerConfig` carries a description
  (`api.secret_env`; counted by the same reflection walk the M1
  coverage test will run, 2026-09-05), and `ServerConfig` is the one
  reachable model with no docstring, so the domain renderer pointed
  at `ServerConfig` would print a page of `**(undescribed)**`
  cells.
- The rendering vocabulary: `docgen.type_name` and `docgen.default`
  are public and shared already; `_paragraph`, `_cell` and
  `_nested_model` are module-private.
- The cross-field boot refusals: `RESUMPTION_NEEDS_RECORDING` and
  `RESUMPTION_NEEDS_TEXT` are module-level constants
  (`models.py:516`); the discoverability refusal (`ota_path` null
  with onboarding off) is an inline string in
  `_check_something_is_discoverable`.
- The keys with no configuration form: the `DatabaseConfig`
  docstring carries the `VINGA_DB_PASSWORD` / `VINGA_DB_URL`
  reasoning; `loader._check_database_environment` refuses the
  generic `VINGA_SERVER__DATABASE__*` spellings; `loader.
  _with_database_environment` applies the four `VINGA_DB_*` names
  over the section.
- The environment override scheme: `FileConfig` is the
  pydantic-settings model (`env_prefix="VINGA_"`,
  `env_nested_delimiter="__"`, env beats file beats defaults); the
  database section is the recorded exception.
- The drift-check pattern: the integration job of
  `.github/workflows/vinga-server.yml` regenerates and diffs the
  four committed artifacts; `test_config_docgen.py` holds the
  unit-lane freshness pin (`test_the_committed_reference_matches_
  the_models`), the determinism pin, the no-database child
  interpreter pin, and the names-every-field sweep. The example
  file's own coverage guard exists too:
  `test_config_examples.py` walks `ServerConfig` and insists
  `config.example.yaml` mentions every field.
- The CLI: `reference` is a flat verb declared by `_rendered`
  (no arguments); `schema` is the merged precedent for a renderer
  with an optional selector defaulting to the whole
  (`_of_an_entity`). The committed `docs/reference/cli.md` is
  generated from the command tree, so a grammar change regenerates
  it.

## Open questions, resolved

**The command is `vinga reference server`, an optional selector on
the existing verb, not a new verb.** The act is the same act the
flat verb already names: render a configuration reference out of the
models. A second flat verb (`server-reference`) would be two words
for one act, which the CLI guide's verb rules forbid;
`cli-reference` is not a precedent for it, because the CLI reference
is a document about the command tree rather than about a half of the
configuration, and its renderer lives with the tree. The merged
precedent for "one renderer, a selector for which document" is
`schema [ENTITY]`, whose bare invocation renders the whole domain.
So `reference` gains one optional positional, `HALF`, choices
`domain` and `server`, default `domain`: the bare invocation stays
byte-identical (the committed-copy pin proves it), every existing CI
line and documentation sentence stays true, and the new page is
`vinga-server config reference server`. A selector that names
neither half is refused with a fixed sentence naming the two that
exist, in the shape `docgen.entity()` refuses an unknown entity:
what exists is the useful half, and what was typed is never quoted
back.

**The renderer is a new module,
`config/server_reference.py`.** The two references change for
separate reasons: `docgen.py` renders the domain registry and moves
when entity kinds and provider options move; the server-half page
moves when `ServerConfig` does. `docgen.py` is a thousand lines
whose docstring names the domain configuration as its subject, and
the design conventions say a new concept gets its own module rather
than a thousandth line. The module's one sentence: callers get the
committed server-half reference as a string without knowing how
model metadata, docstrings and refusal constants become markdown.
It is not a pass-through: it owns the page structure, the
constraints column, the docstring rendering and the refusals
section. The generic rendering vocabulary is shared rather than
copied: `type_name` and `default` are imported as they are, and
`_paragraph`, `_cell` and `_nested_model` are promoted to public
names in `docgen` (`paragraph`, `cell`, `nested_model`) in the
change that gains them a second caller, so no test or module
reaches an underscore.

**Constraints get a column, rendered from the metadata pydantic
already holds.** The issue asks for every constraint, and the
server half is where numeric bounds are operator facts (ports 1 to
65535, positive seconds, `ge=512` on a token budget). The table is
`| Key | Type | Default | Constraints | Description |`, with the
constraints cell rendered from `FieldInfo.metadata`
(annotated-types `Ge`/`Gt`/`Le`/`Lt`, rendered as `>= 1`,
`1 to 65535`, and empty where a field has none) rather than
restated in prose, so a bound cannot be documented and enforced as
two numbers. Validator-enforced shape constraints that have no
metadata form (the five log levels, the `/`-wrapped `ota_path`, the
URL shapes, the base32 onboarding key) are stated in the field's
description in M1, which is the same single-home rule applied to
prose. The domain reference's 4-column table is untouched: reshaping
it would churn `domain-config.md` for no reader gain, and the two
tables are two renderings, not one structure written twice.

**The page renders model docstrings as section prose, so the page's
explanations have the same one home as its tables.** Each
section: a heading with the section's path (`server`,
`server.conversations`, ...), the model's docstring rendered whole
(normalized and wrapped the way `_paragraph` wraps, paragraph by
paragraph), then the field table, recursing into nested models in
declaration order the way `_options_tables` does. `ServerConfig`
itself has no docstring today; M1 writes one (what the server half
is, where it lives, when it is read). This is why M1 moves the
comment prose into descriptions and docstrings rather than leaving
it beside them: prose that renders is prose that cannot silently
rot, and two copies of the same sentence is the bug the generated
references exist to prevent.

**The cross-field boot refusals are rendered from the code's own
sentences.** A "Refused at boot" section lists the fixed sentences
the validators actually raise: `RESUMPTION_NEEDS_RECORDING`,
`RESUMPTION_NEEDS_TEXT`, and the discoverability refusal, whose
inline string is hoisted to a module-level constant
(`NOTHING_DISCOVERABLE`) in the same change so the page and the
validator share one sentence. The renderer imports the constants
and a registry tuple beside them
(`BOOT_REFUSALS: tuple[str, ...]`) declared in `models.py` next to
the sentences it collects, so adding a cross-field refusal without
adding it to the page is a one-file diff a reviewer sees whole. A
test walks the tuple and asserts each sentence appears verbatim in
the rendered page, and each constant keeps its raising site (the
existing resumption tests and a new discoverability pin already
exercise them; the docgen-side test only holds the page to the
tuple).

**The keys with no configuration form get their own section, with
the environment override scheme beside them.** Two fixed prose
blocks owned by the renderer, the way `docgen.reference()` owns the
domain page's preamble prose:

- *Environment overrides*: any key overridable as
  `VINGA_SERVER__<PATH>` with `__` joining nesting
  (`VINGA_SERVER__PORT=9000`), env beats file beats defaults, a
  `.env` beside the working directory works, and the database
  section is the recorded exception: `VINGA_DB_HOST`, `VINGA_DB_PORT`,
  `VINGA_DB_NAME`, `VINGA_DB_USER` are the documented spellings and
  the generic `VINGA_SERVER__DATABASE__*` forms are refused at boot
  with the sentence naming the short one.
- *What deliberately has no key*: `VINGA_DB_PASSWORD` and
  `VINGA_DB_URL`, environment-only, with the one-sentence reason
  (a credential in a config file is what the no-secrets-in-YAML
  stance exists to prevent, and a URL carries one in its authority
  and can carry another in its query).

The facts behind both blocks are load-bearing in
`DatabaseConfig`'s docstring and `loader.py`'s two functions; the
section states them at the page's altitude and the tests hold the
page to naming the four short spellings, the two forbidden names
and the refused generic prefix, so the section cannot silently stop
covering them.

**The page's committed home and header.** Committed at
`docs/reference/server-config.md`, opening with the same
do-not-edit header the domain page carries, naming its regenerate
command (`vinga-server config reference server`, written with the
canonical `PROGRAM` constant plus the selector word) and where the
descriptions live. The preamble states the split in one paragraph
and links both neighbours: `domain-config.md` for the stored half,
`config.example.yaml` as the annotated starting point. Prose wraps
at `docgen.PROSE_WIDTH`; everything is deterministic (declaration
order, no timestamps, no set iteration).

## Module layout

- `config/models.py` (M1): descriptions and docstrings on the
  server-half models; `NOTHING_DISCOVERABLE` hoisted;
  `BOOT_REFUSALS` declared beside the refusal sentences (M2).
  Deepened at the fields' one home; no new module.
- `config/docgen.py` (M2): `_paragraph`, `_cell`, `_nested_model`
  promoted to `paragraph`, `cell`, `nested_model`; the domain
  preamble sentence that says the server half "is documented
  there, in `config.example.yaml`" now points at the new page.
  Nothing else moves.
- `config/server_reference.py` (M2, new): `reference() -> str`,
  the server-half page. Reads the models and the refusal registry
  and nothing else: no database, no configuration file, no key, no
  application import. Callers stop having to know how model
  metadata becomes markdown.
- `config/cli.py` (M2): `reference` gains the optional `HALF`
  selector (a new declare in the `_of_an_entity` style, or
  `_rendered` widened; whichever reads better in place), routing
  `server` to the new module and everything else through the
  existing path; the unknown-half refusal is a fixed sentence.
- `docs/reference/server-config.md` (M2): the committed page.
- `.github/workflows/vinga-server.yml` (M2): one more
  regenerate-and-diff step beside the domain one.

## Tests

Reuse the docgen suite's harness and style; the new module gets its
own `tests/unit/test_server_reference.py` rather than rows in
`test_config_docgen.py`, since the interface under test is the new
module's.

- **M1 coverage test** (in `test_config.py` or beside the models'
  own tests): walk every model reachable from `ServerConfig`
  (by reflection through `nested_model`, never a hand list) and
  assert every field carries a nonempty description and every model
  a nonempty docstring. This is the guard that keeps a future field
  from rendering `**(undescribed)**`.
- **Renders from the models alone**: the child-interpreter pin from
  `test_config_docgen.py`, applied to
  `vinga-server config reference server`: no database driver, no
  key, no configuration file read.
- **Deterministic**: two renders are byte-equal.
- **Names every field**: every field path reachable from
  `ServerConfig` appears in the page (reflection sweep, the
  `test_the_reference_names_every_field_of_every_entity` shape).
- **Constraints render**: the bounded fields render their bounds
  (`port` shows `1 to 65535`, `resumption_budget_tokens` shows
  `>= 512`), asserted from the metadata rather than as literals
  where the sweep can derive them.
- **The refusals section**: every sentence in `BOOT_REFUSALS`
  appears verbatim; the tuple's three members each have a raising
  site (the resumption pair's existing tests stand; a
  discoverability boot-refusal pin is added if none exists).
- **The no-key section**: the page names `VINGA_DB_PASSWORD`,
  `VINGA_DB_URL`, the four `VINGA_DB_*` spellings and the refused
  `VINGA_SERVER__DATABASE__` prefix.
- **The committed copy matches**: the freshness pin, mirroring
  `test_the_committed_reference_matches_the_models`, with the
  regenerate command in the failure message.
- **The selector**: `reference` bare and `reference domain` are
  byte-equal to the domain page (the existing committed-copy pin
  already holds bare `reference` to `domain-config.md`, which is
  the behavior pin for the reshape); an unknown half is one fixed
  sentence, exit 1, naming `domain` and `server` and quoting
  nothing back, with a planted credential-shaped selector asserted
  absent from stderr, stdout and the exception chain.
- **cli.md**: the committed CLI reference regenerates in the same
  change (the grammar grew a positional), proven by its own
  freshness pin staying green.

## Risks

- **M1 rewords 47 fields' worth of prose with no rendered reader
  yet.** Mitigated by ordering: the coverage test lands with M1, so
  the descriptions are load-bearing the day they are written, and
  M2's sweep test would catch a field whose description was lost in
  a rebase. The example file's own coverage test
  (`test_config_examples.py`) keeps `config.example.yaml` honest
  independently.
- **Description drift against `config.example.yaml` comments.** The
  two are different documents on purpose (contract against
  starting point); facts moved into descriptions in M1 are trimmed
  from example comments only where the example would otherwise
  state a bound or default the models now render, keeping the
  example's own voice. No mechanical sync is attempted, matching
  the issue's coverage-not-fact framing.
- **`_rendered`'s other two commands (`openapi`, `cli-reference`)
  must not grow the selector.** The declare change is scoped to the
  `reference` row; the CLI tests that enumerate help pages plus the
  regenerated `cli.md` make an accidental widening visible.
- **Generated-artifact churn in M1.** `domain-config.md`,
  `api-openapi.json` and `cli.md` are expected byte-stable under
  M1 (descriptions on server models render nowhere yet); the
  freshness pins assert it rather than the plan claiming it.
- **The census.** The new page quotes command spellings, and M2
  moves documentation, so `test_command_spellings.py` runs in both
  milestones and the manifest is regenerated with
  `uv run python -m tests.unit.test_command_spellings` when stale,
  never by hand.

## Milestones

- [ ] **M1: the server-half models carry their own
  documentation.** Descriptions on every field reachable from
  `ServerConfig`, docstrings on every reachable model
  (`ServerConfig` gains one), validator-enforced shapes stated in
  the descriptions of the fields they bound, the coverage test, and
  `NOTHING_DISCOVERABLE` hoisted to a constant. Behavior-neutral:
  no validation change, and the generated-artifact freshness pins
  prove nothing committed moved. Design footprint: deepens
  `models.py` at the fields' one home; no new module, no seam.
  Documentation footprint: none staled; `config.example.yaml` is
  touched only where a comment restates a bound the description now
  owns, and the milestone says so in the implementation doc.
- [ ] **M2: the generator, the command, the committed page and the
  drift check.** `config/server_reference.py`; the promoted
  rendering helpers; `BOOT_REFUSALS`; the `reference` selector with
  its fixed-sentence refusal; `docs/reference/server-config.md`
  committed; the CI step; the test suite above; the regenerated
  `cli.md` and, if staled, the census manifest; the cross-links
  (the domain preamble's server-half sentence now points at the
  page and `domain-config.md` regenerates; `config.example.yaml`'s
  header names the page as the complete contract; `docs/README.md`
  gains the reference bullet; `vinga-server/README.md`'s
  "Configuration" section links the page beside the example file);
  a CHANGELOG entry. Design footprint: one new module with the
  one-sentence interface above; `docgen` deepens into the shared
  rendering vocabulary instead of a second copy growing beside it.
  Documentation footprint: `docs/README.md`,
  `vinga-server/README.md`, `config.example.yaml`,
  `CHANGELOG.md`, and the regenerated `domain-config.md`,
  `cli.md` and census manifest.
