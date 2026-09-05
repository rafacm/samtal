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
byte-identical, every existing CI line and documentation sentence
stays true, and the new page is
`vinga-server config reference server`. The halves are one
structure, not three: `server_reference.py` declares an ordered
registry `HALVES: tuple[tuple[str, Callable[[], str]], ...]`
mapping `domain` to `docgen.reference` and `server` to its own
renderer (no import cycle: `docgen` never imports the new module),
with a `render(half)` beside it that looks the name up and refuses
an unknown one. The CLI's dispatch calls `render`, the positional's
help lists the registry's keys, and the fixed refusal names them
from the same tuple, so there is no catch-all branch that would
accept an accidental value and no second copy of the set anywhere:
adding a half is one registry row, per the derived-grammar rule in
the CLI guide. The byte-identical claim is
proven by sequencing, not asserted: the selector lands in a commit
that touches neither `docgen.reference()` nor
`docs/reference/domain-config.md`, so the existing committed-copy
pin (which compares the renderer's output against the committed
page) proves bare `reference` unchanged exactly because the
committed page did not move in that commit; the domain preamble's
pointer to the new page is a separate, later commit that changes
the content deliberately and regenerates `domain-config.md` with
it, reviewed as the intentional edit it is. A selector that names
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
copied: `type_name` and `default` are imported as they are;
`_nested_model` is promoted to `nested_model` in M1, because the M1
coverage test is its second caller and a test must reach public
names only; `_paragraph` and `_cell` are promoted to `paragraph`
and `cell` in M2, the change that gains each its second caller. No
test or module reaches an underscore at any point.

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
validator share one sentence. The renderer imports a registry
declared in `models.py` next to the sentences it collects, and the
registry is checked in both directions rather than only the easy
one. Each row carries the sentence and a provoking
misconfiguration (a dict of field values that triggers exactly that
refusal), so a row cannot be inert: the test validates each row's
misconfiguration and asserts the raised messages are exactly the
row's sentence, the rendered section is asserted to carry exactly
the registry's sentences, and the reverse direction is mechanized
by reflection rather than by memory: a sweep over every model
reachable from `ServerConfig` collects the declared model-level
validators (`__pydantic_decorators__.model_validators`) and asserts
each validator is named by at least one registry row, so a new
cross-field validator added without registry rows fails the sweep
instead of silently missing the page.

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

The names themselves get one home rather than four. Today the four
short spellings live in `loader.DATABASE_ENV_NAMES` (with
`DATABASE_ENV_PREFIX` beside them) and the two no-key names in
`db/__init__.py` (`URL_ENV`, `PASSWORD_ENV`); a renderer and tests
that restated them as literals would keep passing across a rename.
M2 moves the inert declarations to `config/models.py` beside
`DatabaseConfig`, whose docstring already carries their reasoning:
`DATABASE_ENV_PREFIX`, `DATABASE_ENV_NAMES`, and the two
credential-only names. `loader.py` and `db/__init__.py` import them
from there (models imports neither module, so no cycle), the
renderer reads the same constants without importing the database
package, and the tests derive their expected inventories from the
constants rather than from literals, so the page, the loader, the
database code and the tests cannot come to disagree about a name.

**The page's committed home and header.** Committed at
`docs/reference/server-config.md`, opening with the same
do-not-edit header the domain page carries, naming its regenerate
command in the canonical short spelling the generated documents
all use: `vinga reference server`, rendered from `PROGRAM` (which
is `vinga`; `SERVER_PROGRAM` is the long spelling and stays out of
generated pages) plus the selector word from the `HALVES` registry.
The CI step's error message keeps the long
`uv run vinga-server config reference server` spelling the way the
existing steps do, since that message is a command to paste into a
checkout, not a rendered page. The preamble states the split in one paragraph
and links both neighbours: `domain-config.md` for the stored half,
`config.example.yaml` as the annotated starting point. Prose wraps
at `docgen.PROSE_WIDTH`; everything is deterministic (declaration
order, no timestamps, no set iteration).

## Module layout

- `config/models.py` (M1): descriptions and docstrings on the
  server-half models; `NOTHING_DISCOVERABLE` hoisted;
  `BOOT_REFUSALS` declared beside the refusal sentences (M2); the
  database environment-name constants move here from `loader.py`
  and `db/__init__.py` (M2), with both importing them back.
  Deepened at the fields' one home; no new module.
- `config/docgen.py` (M1: `_nested_model` promoted to
  `nested_model` for the coverage test; M2: `_paragraph` and
  `_cell` promoted to `paragraph` and `cell`); the domain
  preamble sentence that says the server half "is documented
  there, in `config.example.yaml`" now points at the new page.
  Nothing else moves.
- `config/server_reference.py` (M2, new): `reference() -> str`,
  the server-half page. Reads the models and the refusal registry
  and nothing else: no database, no configuration file, no key, no
  application import. Callers stop having to know how model
  metadata becomes markdown.
- `config/cli.py` (M2): `reference` gains the optional `HALF`
  selector (a new declare in the `_of_an_entity` style, scoped to
  the `reference` row so `openapi` and `cli-reference` do not grow
  it), dispatching through `server_reference.render(half)`; the
  positional's help and the fixed unknown-half refusal both derive
  from the `HALVES` registry's keys, and there is no catch-all
  branch.
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
  (by reflection through `docgen.nested_model`, promoted public in
  this same milestone, never a hand list and never an underscore
  reach-in) and
  assert every field carries a nonempty description and every model
  a nonempty docstring. This is the guard that keeps a future field
  from rendering `**(undescribed)**`.
- **Renders from the models alone**: the child-interpreter pin from
  `test_config_docgen.py`, applied to
  `server_reference.reference()` imported directly (never through
  `config.cli`, which reaches `store.py` and so SQLAlchemy and
  cryptography by the recorded edge in `docgen.py`'s docstring),
  with the harness's import allowlist and heavy-module denylist. A
  separate CLI-level case proves the command's behavior rather than
  its import list: `reference server` runs with no database
  reachable, no configuration file and no key in the environment,
  and succeeds.
- **Deterministic**: two renders are byte-equal.
- **Names every field, per section**: the test splits the page at
  its section headings and asserts, per section, the exact
  reflected field sequence of that section's model (declaration
  order, all rows, nothing extra), and asserts the section
  inventory itself equals the reflected model graph. Unscoped
  name-appears-somewhere checking is explicitly not enough here:
  the server models repeat `enabled`, `port` and `max_session_s`
  across sections, so one rendered row could mask a missing row or
  a whole missing section elsewhere.
- **Constraints render, completely**: a reflection sweep asserts
  that for every field reachable from `ServerConfig`, every
  `FieldInfo.metadata` bound appears rendered in that field's row
  inside its own section (scoped, not page-wide), so a bound cannot
  exist unrendered anywhere; `port` and `resumption_budget_tokens`
  stand as the readable examples, derived not restated. Beside the
  sweep, one semantic rendering assertion per validator-enforced
  rule holds the prose to stating each rule where metadata cannot:
  the environment-name shape on every `EnvName` field, the base32
  onboarding-key shape, the five log levels by name, every
  `ota_path` restriction (the slash shape, the reserved API and
  onboarding mounts, the probe paths), and both URL contracts
  (scheme set, no userinfo, and `public_url`'s no query or
  fragment).
- **The refusals section**: bidirectional. Forward: the rendered
  section carries exactly the registry's sentences, each verbatim.
  Backward: each row's provoking misconfiguration raises exactly
  its sentence when validated, and the reflection sweep over every
  reachable model's declared model-level validators asserts each
  validator is claimed by at least one row, so the registry cannot
  fall behind the validators (the resumption pair's existing tests
  stand beside this; a discoverability boot-refusal pin is added if
  none exists).
- **The no-key section**: the page carries the values of the moved
  constants (the two credential-only names, the four short
  spellings, the refused generic prefix), and the test derives its
  expected strings from those same constants rather than from
  literals, so a renamed variable fails the test instead of
  passing against a stale page.
- **The committed copy matches**: the freshness pin, mirroring
  `test_the_committed_reference_matches_the_models`, with the
  regenerate command in the failure message.
- **The selector**: `reference` bare and `reference domain` are
  byte-equal to the domain page, and the reshape commit is ordered
  so the pin means something: the selector commit leaves
  `docgen.reference()` and the committed `domain-config.md`
  untouched, so the committed-copy pin passing there is the proof
  that bare output did not move, and the preamble edit follows as
  its own commit with its own regeneration; an unknown half is one fixed
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
  the descriptions of the fields they bound, the coverage test
  (with `_nested_model` promoted to `docgen.nested_model` as its
  public walker), and
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

## Plan review round

Backend codex (codex-cli 0.153.0), model `gpt-5.6-sol`, sandbox
read-only, 2026-09-05, against commit 3d587012 of this plan; the
reviewer ran 5m03s. Verdict: ready after the P1/P2 amendments.

1. **P1: the compatibility pin cannot prove byte-identical bare
   output.** The plan promises bare `reference` stays byte-identical
   while the same milestone changes and regenerates its domain
   preamble; the freshness test compares against the newly committed
   page, not the pre-reshape output. Sequence explicitly: pin the
   current bytes, add the selector proving bare output unchanged,
   then make the preamble update a separate intentional content
   change.

   *Resolution*: accepted in full. M2's commits are now ordered in
   the plan: the selector commit touches neither `docgen.reference()`
   nor the committed `domain-config.md`, so the existing
   committed-copy pin passing on that commit is the proof that bare
   `reference` did not move; the preamble pointer and its
   regeneration follow as their own commit, reviewed as an
   intentional content change.

2. **P2: the proposed child-process isolation test is impossible as
   written.** Applying the models-only pin to
   `vinga-server config reference server` cannot pass: importing
   `config.cli` reaches `store.py` and therefore SQLAlchemy and
   cryptography, which is recorded in `docgen.py`'s own docstring;
   the existing isolation test imports `docgen` directly. Test
   `server_reference.reference()` directly in a fresh interpreter
   with an import allowlist and heavy-module denylist, and prove the
   CLI command opens no database, reads no configuration file and
   needs no key in a separate CLI-level test.

   *Resolution*: accepted in full. The isolation pin now targets
   `server_reference.reference()` imported directly under the
   existing harness's allowlist and denylist, and a separate
   CLI-level case proves `reference server` succeeds with no
   database reachable, no configuration file and no key set.

3. **P2: the halves selector is three hand-maintained closed
   sets.** The accepted names, the routing, the help and error
   contents and the tests are each specified separately, and
   "server to the new module and everything else through the
   existing path" gives the default branch a catch-all. Introduce
   one ordered mapping from `domain` and `server` to their
   renderers; dispatch, help text and the fixed unknown-half refusal
   derive from its keys, with no catch-all branch.

   *Resolution*: accepted in full. `server_reference.py` declares
   the ordered `HALVES` registry and a `render(half)` that refuses
   an unknown name; the CLI dispatch, the positional's help and the
   refusal all derive from the registry's keys, and the catch-all
   routing sentence is gone from the plan.

4. **P2: `BOOT_REFUSALS` is checked only in the easy direction.**
   The tests prove every registry member is rendered and raisable,
   not that every cross-field validator sentence is registered; a
   new validator refusal could be omitted from the page while all
   tests pass. Require a bidirectional assertion: exercise every
   cross-field validator branch and compare the emitted sentences
   with the registry, then the registry with the rendered section.

   *Resolution*: accepted in full. Registry rows now carry the
   sentence, the owning validator and a provoking misconfiguration;
   the test asserts each provocation raises exactly its row's
   sentence, the rendered section carries exactly the registry's
   sentences, and a reflection sweep over every reachable model's
   declared model-level validators asserts each is claimed by a
   row, which is the reverse direction mechanized.

5. **P2: environment-variable facts remain duplicated and can drift
   together.** The renderer and its test would name six variables
   and a prefix as literals while the authoritative mapping is
   `DATABASE_ENV_NAMES` in `loader.py` and `URL_ENV`/`PASSWORD_ENV`
   in `db/__init__.py`; a loader rename would not fail a test
   checking the old literals in the page. Move the inert
   declarations to a safe configuration module and have the loader,
   the database code, the renderer and the tests derive their
   inventories from them; the renderer must not import the database
   package to obtain them.

   *Resolution*: accepted in full. The inert declarations
   (`DATABASE_ENV_PREFIX`, `DATABASE_ENV_NAMES`, the URL and
   password names) move to `config/models.py` beside
   `DatabaseConfig`; `loader.py` and `db/__init__.py` import them
   back, the renderer reads the constants without touching the
   database package, and the tests derive their expected
   inventories from the constants.

6. **P2: the constraints test does not establish complete
   coverage.** Two examples and a qualified sweep do not hold the
   page to the issue's "every constraint", and nonempty-description
   coverage cannot detect a description that omits or misstates a
   validator rule. Require every `FieldInfo.metadata` bound to
   appear in its field's scoped row, plus one semantic rendering
   assertion per validator-enforced rule: environment-name shape,
   onboarding-key shape, log levels, all OTA-path restrictions, and
   both URL contracts.

   *Resolution*: accepted in full. The sweep now asserts every
   metadata bound of every reachable field renders in its own
   section's row, and the semantic assertions enumerate the six
   validator-rule families by name, including the reserved OTA
   mounts and probe paths, so a description that omits a rule fails
   a test rather than a reviewer's eye.

7. **P2: the field-coverage test can pass on duplicate leaf
   names.** The cited domain test checks unscoped row names, and
   the server models repeat `enabled`, `port` and `max_session_s`,
   so one rendered row could mask a missing row elsewhere. Parse or
   delimit each section and assert its exact reflected field
   sequence there, and assert the reflected model-section inventory
   so a missing nested section cannot hide behind another's rows.

   *Resolution*: accepted in full. The coverage test now splits the
   page at its headings, asserts each section's exact reflected
   field sequence in place, and asserts the section inventory
   equals the reflected model graph, with the duplicate leaf names
   named as the reason unscoped checking is insufficient.

8. **P2: M1 depends on an interface not made public until M2.** The
   M1 coverage test must walk the model graph, but the promotion of
   `_nested_model` is assigned to M2, so the test as planned is an
   underscore reach-in. Promote the reflection helper in M1, or
   reuse the existing public walker, or specify another public
   model-graph interface.

   *Resolution*: accepted, first option. The `_nested_model`
   promotion moves from M2 to M1, since the coverage test is its
   second caller; `_paragraph` and `_cell` stay M2 promotions with
   their second callers.

9. **P3: the stated regenerate command uses the wrong constant.**
   The plan says the header renders `vinga-server config reference
   server` using `PROGRAM`, but `PROGRAM` is `vinga`; the long
   spelling is `SERVER_PROGRAM`. Generate the canonical short form
   with `PROGRAM`, consistent with the existing generated documents,
   or explicitly use `SERVER_PROGRAM` and say why this page is the
   exception.

   *Resolution*: accepted, first option. The header renders the
   canonical short spelling from `PROGRAM` (`vinga reference
   server`), consistent with every generated document; the CI error
   message keeps the long paste-into-a-checkout spelling the
   existing steps use.
