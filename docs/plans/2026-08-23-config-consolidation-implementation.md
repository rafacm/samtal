# Consolidate the config admin surface behind the OpenAPI contract: implementation

Companion to
[`2026-08-23-config-consolidation.md`](2026-08-23-config-consolidation.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: merge, fold, and shrink the structures

### What was done

Four code commits and this one, in the plan's order: the merge, the
registry shrink, the responses moves, the fold with its import
redirect. Each of the four was verified against the three committed
artifacts before it was made, so every commit of the milestone is one
a reviewer can check the byte-identity claim at.

**The merge** (`9ddc3bac`). `DomainConfig` is declared once, in
`config/models.py`, holding the seven domain sections, their three
field validators and the per-field comments the two declarations had
between them. `Config` subclasses it and adds `server`, `memory`, the
accessors and the boot-only `_check_domain`. `store.py` imports the
class rather than declaring it and keeps re-exporting the name in
`__all__`, which is honest: `Snapshot.domain` is typed by it, and
that is the type its callers hold. `docgen.py` reads it from `models`.

The subclass is what keeps two contracts intact. The store still
validates a write against `DomainConfig` and never against `Config`,
so write-time validation stays reference-half-only: `check_completeness`
is a rule about a runnable server, and running it at a write would
refuse the first `set agent` into an empty database. And the
seven-field model survives by name, so `config schema` and the
reference's whole-domain table render what they rendered before.

Two consequences are recorded beside the code that meets them.
`Config.model_fields` now orders the domain sections first and
`server`/`memory` last; nothing reads that order (verified below).
And the class may never gain an after-validator, because
`store._read_domain` assigns `agent_defaults` and `default_agent`
after construction, so a model validator would judge a half-read
snapshot. `DOMAIN_DESCRIPTIONS`' comment no longer says two models
carry the fields; it says which renderings read the prose and why it
sits beside the model rather than on it.

**The registry shrink** (`638217ba`). `DocumentedShape.leads_with` and
`DocumentedShape.always_shown` are deleted, with the two accessors
(`entities.leads_with`, `entities.always_shown`) and the `_BY_MODEL`
index that existed only to serve them. Each fact is now a literal in
`views.py`, its one consumer, with the reason the registry entry gave:
`("prompt",) if model is AgentConfig else ()` in `_order`, and
`("phrases",) if model is FillerConfig else ()` in `_declared`.
`test_every_display_fact_names_a_field_the_shape_declares` goes with
them; it was addressed to the two deleted fields. What it bought is
kept: `test_config_reads.py` asserts that `"prompt"` and `"phrases"`
are fields their models declare, in the suite that reads the display
path, with a comment naming the test it inherits from. That half is
worth the two lines wherever the literals live, because neither
failure is loud at the point it happens (a renamed `AgentConfig.prompt`
is a KeyError out of a read path, a renamed `phrases` silently stops
being shown).

**The responses moves** (`cac7e3f9`). `outcomes`, `flags` and
`RELOAD_SECTIONS` move to `cli.py` with `_section`, the helper that
unwraps an optional section. The three `*_DESCRIPTION` constants
inline into the `Field(description=...)` calls that read them.
`test_config_cli_transport.py` and `test_config_cli_rendering.py`
import the three relocated names from `config.cli` now; no assertion
changed.

**The fold** (`8eabf1b2`). `config/writes.py` is deleted. Its thirteen
f-string factories are written out at their call sites, its
constant-returning function is the constant it returned,
`binding_notice` moves to `api.py` as `_binding_notice` and
`secret_notice` moves whole to `cli.py` as `_secret_notice`, both with
their docstrings and one of the two with a rewritten last paragraph
(see the deviations below). `CLEARED_DEFAULT_AGENT` becomes
`_CLEARED_DEFAULT_AGENT`, a module constant of `api.py`, its only
reader, beside that file's other sentences. The
five notice constants the module re-exported are imported from
`entities.py` now by nine test files. The module docstring's
single-source rationale moves into
`test_a_local_write_acknowledges_what_the_api_acknowledges`, and the
four prose references to `writes.py` in `cli.py`, `api.py`,
`entities.py` and `responses.py` are rewritten to name that test or
the two decisions' new homes.

### The name dispositions

**`entities.py`.** Two names go, twenty-three stay.

| Name | Disposition | Reader |
| --- | --- | --- |
| `leads_with` (field + accessor) | **deleted** | was `views._order`; now a literal there |
| `always_shown` (field + accessor) | **deleted** | was `views._declared`; now a literal there |
| `table` | stays | `store.py`, resolving a kind to its schema table |
| `secret_slots` | stays | `store.py` and `api.py`, addressing stored secrets |
| `moved_key` | stays | `store.py`, `loader.py`, `docgen.py` |
| `missing` | stays | `store.py`'s reads, deletes and slot checks |
| `notice` | stays | `api.py` and `cli.py`, both write paths |
| `has_delete` | stays | `api.py` and `cli.py` route/subcommand fan-out |
| `route`, `addressing` | stay | `cli.py`'s URL building, `api.py`'s row construction |
| `name`, `title`, `location`, `model`, `purpose`, `notes`, `command`, `examples` | stay | `docgen.py` |
| `secret_key` | stays | `views.entity_body`, the masking rule for every displayed value |
| the five `NO_SUCH_*` sentences | stay | `store.py`, through `missing` |
| the five `*_NOTICE` sentences | stay | descriptors, `api.py`, `cli.py`, nine test files |
| `OPTIONS_NOTE`, `API_OPTIONS_NOTE` | stay | `docgen.py` and `api.py` |
| `descriptor`, `setting`, `ENTITIES`, `NESTED`, `SETTINGS` | stay | every consumer |

The masking path is unchanged: `secret_key` is still the injected
predicate `views.entity_body` applies at every depth, and no displayed
value takes a different route to a caller than it did before.

The one real duplication in this territory is left as the plan says:
`route` + `addressing` on descriptors versus `api.py`'s literal path
strings. The committed OpenAPI document renders the literals and the
CLI integration tests drive the descriptors, which is the pin pair
that keeps them agreeing.

**`responses.py`.** Six names leave the module, none of them a wire
shape.

| Name | Disposition |
| --- | --- |
| `outcomes` | **moved** to `cli.py`, its only caller |
| `flags` | **moved** to `cli.py`, its only caller |
| `RELOAD_SECTIONS` | **moved** to `cli.py`, its only caller |
| `_section` | **moved** to `cli.py` with `RELOAD_SECTIONS`, which is its only reader |
| `ADDED_DESCRIPTION` | **inlined** into `EntityDiff.added` |
| `REMOVED_DESCRIPTION` | **inlined** into `EntityDiff.removed` |
| `CHANGED_DESCRIPTION` | **inlined** into `EntityDiff.changed` |
| every model (`SecretSlot` … `SecretValue`), `Applies`, `McpStatusSource`, `ServableAgents`, `ConfigReloader`, `ConfigDiffReader` | stay: they are the contract |

**`writes.py`.** The whole module goes; the census below is what was
in it.

| Name | Disposition |
| --- | --- |
| `wrote_provider`, `deleted_provider` | inlined (api; delete also in cli) |
| `wrote_mcp_server`, `deleted_mcp_server` | inlined (api; delete also in cli) |
| `wrote_prompt_fragment`, `deleted_prompt_fragment` | inlined (api; delete also in cli) |
| `wrote_agent`, `deleted_agent` | inlined (api; delete also in cli) |
| `bound_device`, `wrote_default_agent` | inlined (api) |
| `deleted_device` | inlined (api and cli) |
| `wrote_secret`, `cleared_secret` | inlined (api and cli) |
| `wrote_agent_defaults` + `WROTE_AGENT_DEFAULTS` | collapsed to the literal `"agent-defaults"` at the one call site |
| `CLEARED_DEFAULT_AGENT` | moved to `api.py` as the module constant `_CLEARED_DEFAULT_AGENT`, its only reader being the route below it |
| `binding_notice` | moved to `api.py` as `_binding_notice`, last paragraph rewritten (a deviation, recorded below) |
| `secret_notice` | moved whole to `cli.py` as `_secret_notice` |
| `BINDING_NOTICE`, `BINDING_UNSERVED_NOTICE`, `RELOAD_NOTICE`, `RESTART_NOTICE`, `SNAPSHOT_NOTICE` | re-exports; readers redirected to `entities.py`, where they are declared |

Seven sentences now have two spellings, one per write path, exactly as
the plan priced it:
`test_a_local_write_acknowledges_what_the_api_acknowledges` runs each
of the nine mutating acts over the API and again through `--local` and
asserts the printed output is equal. That is a stronger guarantee than
the shared factory gave, because a factory could not have caught the
two paths choosing different notices for one act.

### `DomainSnapshot`: kept, and why

The delta review left this to the deletion test. Applied, it says
keep, for a reason the plan's own wording anticipated but the delta
review's did not weigh: the Protocol's readers are not only the two
production callers.

Under the subclass, `store.py` passes a `DomainConfig` and
`Config._check_domain` passes a `Config` that is one, so a nominal
annotation would cover both. But `check_references` and
`check_completeness` are also exercised directly by
`tests/unit/test_config_checks.py` and
`tests/unit/test_config_fragments.py`, which construct a small frozen
dataclass holding the seven sections, one of them deliberately `None`
where a `DomainConfig` would refuse it. Annotating the three functions
`DomainConfig` would make the annotation false about those callers
while changing nothing that runs (mypy is scoped to
`src/vinga_server/events`), which is a worse state than the Protocol:
a type that lies is not a simplification.

So the class survives with its docstring's reason rewritten. It no
longer says the checks are written against two unrelated classes; it
says they are written against the attributes a domain half has, names
the two production callers and the suites that supply a stand-in, and
so states the interface it actually is.

### Deviations from the plan

Two, both small, and the first is a discovery rather than a change of
direction.

**The `DomainConfig` docstring is `config schema`'s output.** The plan
says the no-after-validator constraint is "stated beside the class",
and the first attempt stated it, along with the subclass rationale, in
the class docstring. `config schema` printed a different document
immediately: `model_json_schema()` renders a pydantic model's
docstring as the schema's `description`, so the whole-domain schema
carries it. The docstring was restored verbatim and all of the added
rationale moved into a comment block above the `class` statement,
which is where it now sits, with a note saying which of the two an
editor is writing. Nothing else in the milestone came close to moving
an artifact. Note what this rendering is not: it is not a committed
artifact, nothing in CI or the suite diffs it, and the comment beside
the class says so (see the PR review round below, finding 1).

**`binding_notice`'s docstring did not move quite whole.** The plan
and the census below say the two branching decisions move "whole with
their docstrings", and `secret_notice` did. `_binding_notice`'s final
paragraph is rewritten: the original said the answer was written in
`writes.py` "rather than at the two call sites because this is already
where a device write's answer is decided", and neither half of that
sentence survives the fold. There are five call sites, all in
`api.py`, and the reason for a function rather than five inlined
branches is that this is where the answer is decided and there is no
second write path deciding it, the CLI's `--local` device delete
having no loaded server to ask. The first two paragraphs, which are
the decision itself, are unchanged.

Everything else landed as written: the subclass shape, the seven-field
model surviving by name, the store's docstring rewrite, the
`DOMAIN_DESCRIPTIONS` comment rewrite, the field-by-field registry
dispositions, the three responses moves, the fold with its two
decisions moved, and the nine-file import redirect with no assertion
changed. `docgen.py` losing its `store` import is the incidental win
the plan predicted, at its true size: importing `docgen` no longer
reaches the repository, so the markdown reference and the JSON Schema
render with nothing loaded but the models and the registry. The
`vinga-server config` commands still pay for SQLAlchemy and
cryptography, because `cli.py` imports `ConfigStore` for the paths
that open a database, and the plan's parenthesis about the class those
two arrived for is about the module edge rather than about the
commands. `test_config_docgen.py` pins the module claim in a child
interpreter, in the shape `test_config_entities.py` holds the
registry's own import set to, so it cannot regress silently.

### The inventory

Public names of `src/vinga_server/config/` (module-level classes,
functions, and non-underscored assignments), before at `014a00f2` and
after:

| Module | Before | After |
| --- | --- | --- |
| `api.py` | 58 | 58 |
| `boot.py` | 3 | 3 |
| `cli.py` | 44 | 47 |
| `diff.py` | 8 | 8 |
| `docgen.py` | 13 | 13 |
| `entities.py` | 25 | 23 |
| `loader.py` | 15 | 15 |
| `models.py` | 60 | 61 |
| `reload.py` | 6 | 6 |
| `responses.py` | 40 | 34 |
| `secrets.py` | 17 | 17 |
| `store.py` | 15 | 14 |
| `views.py` | 20 | 20 |
| `writes.py` | 18 | **gone** |
| **Total** | **342** | **319** |

`api.py` holds level, gaining `_CLEARED_DEFAULT_AGENT`, which is
private and so counts for nothing here; `cli.py` gains `outcomes`,
`flags` and `RELOAD_SECTIONS`; `models.py` gains `DomainConfig` and
`store.py` loses its declaration of it.

`wc -l src/vinga_server/config/*.py`, same two points:

| Module | Before | After |
| --- | --- | --- |
| `__init__.py` | 41 | 41 |
| `api.py` | 2571 | 2602 |
| `boot.py` | 99 | 99 |
| `cli.py` | 2351 | 2441 |
| `diff.py` | 332 | 332 |
| `docgen.py` | 461 | 464 |
| `entities.py` | 692 | 623 |
| `loader.py` | 365 | 365 |
| `models.py` | 2423 | 2467 |
| `reload.py` | 684 | 684 |
| `responses.py` | 1072 | 1016 |
| `secrets.py` | 527 | 530 |
| `store.py` | 2092 | 2040 |
| `views.py` | 456 | 474 |
| `writes.py` | 171 | **gone** |
| **Total** | **14337** | **14178** |

The plan's exit inventory, from `vinga-server/`:

```
$ grep -rnE "config\.writes|config import writes" src tests
$ echo $?
1
```

Nothing reads `Config`'s field order, which the plan requires
verifying before the inherited-first change is relied on.
`grep -rn "model_fields" src`, discounting `model_fields_set`, returns
thirteen readers and not one of them is `Config`: `store.py` twice (a
provider group's stages, a walked entity's fields), `models.py` once
(a nested annotation lookup), `docgen.py` three times (a documented
shape's table and its help epilog, both `DocumentedShape.model`),
`cli.py` four times (the answer-narrowing walk, and the three reload
readings this milestone moved there), `views.py` twice (a displayed
entry's fields) and `protocol/messages.py` once. `Config` is composed
by keyword and never rendered, and no test reads its order either.

### Verification

- `uv run ruff check .`: clean, at each of the four code commits.
- `uv run pytest tests/unit -q` (serial): 2855 passed, 20 skipped.
- `uv run pytest tests/unit -q -n 4 --dist loadfile`: the same, at
  each of the four code commits.
- `uv run pytest tests/integration -q`: 61 passed, at the merge commit
  and at the tip.
- `uv run mypy` (the scoped `events` lane): clean.
- `uv run vinga-server config openapi | diff - ../docs/reference/api-openapi.json`:
  empty, at each of the four code commits.
- `uv run vinga-server config reference | diff - ../docs/reference/domain-config.md`:
  empty, at each of the four code commits.
- `uv run vinga-server config schema` against the pre-milestone
  rendering: empty, at each of the four code commits, and the one
  place the milestone drifted before it was corrected (see the
  deviation above).
- The store's write-order suite (`tests/unit/test_config_store.py`),
  which is decision 1's behavioral pin, green throughout: a write into
  an empty database is still accepted, and completeness is still a
  boot rule.

## PR review round, M1 (PR #263)

External review of the PR diff: claude backend (the codex quota is
exhausted), claude CLI, model `claude-opus-5`, read-only tool set,
2026-08-23, [posted on the PR](https://github.com/rafacm/vinga/pull/263#issuecomment-5384646103).
Verdict as received: "Mergeable after fix 1; findings 2 to 6 are worth
folding in but none blocks." One P2 and five P3s. The round verified
the milestone's load-bearing claims itself and could not break them,
which is what makes the six findings the whole of what is left. As
received:

> The seven dual-spelled sentences match byte for byte across `api.py`
> and `cli.py`, and all seven acts are in `MUTATIONS`, so the
> differential pin really does replace `writes.py`'s guarantee. The
> three inlined `EntityDiff` descriptions concatenate to exactly the
> old constants, so the committed OpenAPI cannot move; the changed
> docstrings are not route descriptions. `Config.model_fields` order
> is genuinely inert: the 13 `model_fields` readers are all
> non-`Config`, and `compose_config` only ever passes already-validated
> submodels, so pydantic's declaration-order error listing has nothing
> to reorder. The two display literals match the descriptors they
> replaced; no unused imports survive; `grep -rnE "config\.writes"` is
> empty; the line and public-name inventories sum correctly and match
> the tree.

Every finding has its own commit.

1. **P2: the comment claiming the `DomainConfig` docstring is "a
   committed byte" promised a guard that does not exist.** No document
   under `docs/reference/` carries the `config schema` rendering, CI
   diffs only two generators, and no test asserts a schema's top-level
   `description`, so an editor who trusts the comment gets no failure
   from the coupling this milestone tripped over once. Fixed in
   `12ec040f`, taking the honest-comment option rather than committing
   a third artifact: the comment now says that nothing checks the
   rendering, that a docstring edit changes what `config schema` prints
   silently, and that #242 verified it by hand at every commit. Prose
   rather than a new regenerate-and-diff file because #241 has just
   thinned the event catalog's committed pins from three to one on the
   argument that each costs every change a regeneration and a review
   surface, and what is at risk here is schema prose rather than a
   contract; the two artifacts that ARE contracts keep their CI checks.
   The plan's decision-1 pin list is amended in the same commit to say
   what is true of the third: verified manually during the milestone,
   unpinned thereafter, stated at the class.

2. **P3: `_binding_notice`'s docstring said "the four call sites" and
   there are five, and its last paragraph was rewritten rather than
   moved whole.** Both true. Fixed in `f2217450`: the count is five,
   all in `api.py`, and the rewrite is recorded as the milestone's
   second deviation with what replaced it and why (neither half of the
   original sentence survives a module that no longer exists). The
   census row and the narrative say "moved" for this one and keep
   "moved whole" for `secret_notice`, which did.

3. **P3: the deleted display test's coherence half was lost with the
   two fields it was addressed to.** `views._order` emits `"prompt"`
   unconditionally and `_declared` indexes `model.model_fields[name]`,
   so a renamed `AgentConfig.prompt` is a KeyError out of a read path
   rather than a targeted failure, and a renamed `FillerConfig.phrases`
   is quieter still. Fixed in `c41ddb76`: two assertions in
   `test_config_reads.py`, the suite that reads the display path, with
   a comment naming the test they inherit from. The neighbouring filler
   test's docstring stops crediting the registry for a departure
   `views` declares now.

4. **P3: the docgen import win was overstated and nothing pinned it.**
   The module edge is genuinely gone, but the `vinga-server config`
   commands still pay for SQLAlchemy and cryptography, because `cli.py`
   imports `ConfigStore`; and unlike `entities.py`, whose import set has
   an allow list, nothing guarded docgen's. Fixed in `2b2dba86`, taking
   both halves of the suggestion: the claim is scoped to the module in
   the docstring and in the implementation doc, and
   `test_config_docgen.py` gains a child interpreter in the shape
   `test_the_registry_is_whole_on_its_own` uses, which imports `docgen`,
   renders the reference and the schema, and asserts the allow list and
   the absence of SQLAlchemy, cryptography, FastAPI and httpx.
   `openapi()` is not called there, being the deliberate exception that
   imports the application. Putting `import vinga_server.config.store`
   back into `docgen` fails it, which was checked rather than assumed.

5. **P3: `CLEARED_DEFAULT_AGENT` was a new public name with no reader
   outside its own module**, sitting between the two route registrars
   rather than with the module's sentences. Fixed in `a410a7a0`:
   `_CLEARED_DEFAULT_AGENT`, moved up beside the claim refusals. Done
   here rather than left to M2's privatization pass, so that the rename
   rides with the commit that created the name. The public-name
   inventory follows: `api.py` holds at 58, and the package total is
   319 rather than 320.

6. **P3: `docs/architecture/design-guide.md` still pointed at the old
   home of the relocated fact** (`RELOAD_OUTCOMES` in `responses.py`),
   in a doc AGENTS.md sends reviewers to. The name was stale before
   this branch; the file is stale because of this change. Fixed in
   `e7fd2c08`: the sentence names `outcomes` and `cli.py`. The
   remaining occurrences of the old name are in dated plan and
   implementation docs, which record what was true when they were
   written and are left as they are.

### Verification after the round

All from `vinga-server/`.

- `uv run ruff check .`: clean.
- `uv run mypy`: clean.
- `uv run pytest tests/unit -q -n 4 --dist loadfile`: 2857 passed, 20
  skipped (two more than before the round: findings 3 and 4).
- `uv run pytest tests/integration -q`: 61 passed.
- `uv run vinga-server config openapi | diff - ../docs/reference/api-openapi.json`:
  empty.
- `uv run vinga-server config reference | diff - ../docs/reference/domain-config.md`:
  empty.
- `uv run vinga-server config schema` against the pre-round rendering:
  empty. Unpinned from here on, which is finding 1's whole subject.

## M2: the prose to data and the wording retreat

### What was done

Five code commits and this one: the description files with their
loader, the wheel proof, the retreat in two slices, and the
privatization the retreat freed. Byte-identity of
`docs/reference/api-openapi.json` was checked before each was made, and
again mechanically at every commit of the branch afterwards, so any of
them is one a reviewer can check the claim at.

**The prose to data** (`f39c2b06`). The nine module-level description
constants leave `api.py` for `config/api_descriptions/`, one file each:
`api.md` for `API_DESCRIPTION` and one per member of the `*_DESCRIPTION`
family. A loader beside them reads a file at import, drops the single
trailing newline a text file ends with, and fills `$NAME$` sigils from
`_SUBSTITUTIONS`, which holds `MASK` and `API_OPTIONS_NOTE`, the two
values the literals interpolated. Sigils rather than `{}` fields because
the prose carries literal path braces (`/devices/pending/{code}`,
`/runtime/agents/{name}/prompt`) that a format call would read as
placeholders; no `$` occurs in the moved prose, which is what makes the
sigil unambiguous, and that was checked before the files were written.

Nothing else moved, which is the three fences the plan drew. Route
docstrings stay where they are, because FastAPI reads a docstring as
the operation description and prose in another file would make the
route harder to read rather than easier. The runtime refusal bodies
stay in code, being behavior an operator meets rather than a document.
`PROBLEM_DESCRIPTIONS` stays with its dual role stated in its comment,
which it already said.

A missing file or a sigil nothing fills raises `MissingDescriptionError`
at import, named and with a sentence saying a packaging fault is what
this is. `test_the_document_states_the_unchanged_value_marker` is
untouched and still closes the drift it was written for: the mask the
document states and the mask a read displays are one string, now
because the loader substitutes it rather than because an f-string did.

**The wheel proof** (`75c84c6f`). The plan's decision 8 extension. The
integration lane renders the OpenAPI document from the installed wheel,
with `-P` and from outside the checkout so the source tree cannot
answer, and diffs it against the committed copy. It reuses the venv the
migration step above it builds, so the wheel is built and installed
once. Run locally as far as a workflow step can be: the wheel was
built, installed into a throwaway venv, and the extracted script run
from a directory outside the checkout, and its output diffed clean
against the committed document. The negative was run too: deleting one
`.md` file from the installed package made the import refuse with
`MissingDescriptionError` naming `reload-held`, which is the sentence
the loader exists to produce.

**The retreat** (`909e5297`, then `30dde34c`). Twenty-three test files
in two slices: the four suites that exercise what the repository
refuses, and then the API, CLI, runtime and conversation suites. Two
support modules carry the whole vocabulary the suites now speak:

- `tests/support/problems.py` gains `refused(body, status)`, which
  asserts the members and no others, the status repeated in the body,
  the standard reason phrase as the title, a non-empty `detail`, and a
  string path and message on every entry of `errors`, then returns the
  sentence uncompared so the caller can assert what it carries. And
  `paths(body)`, the field paths a refusal names in order, which is
  semantic: a form marks the field a pointer addresses. `problem`, the
  helper that builds a whole body sentence and all, survives with
  exactly three callers, and all three are spelled
  `problem(status, str(refusal))`: the body held equal to the exception
  the repository raised, which is differential and not a golden.
- `tests/support/notices.py` is new and does the same for
  acknowledgements. A notice answers one question, when does this reach
  a running server, and there are four answers: `CHECK_IN`, `RELOAD`,
  `RESTART`, `STORE_BOOT`. `boundaries(notice)` is the one place that
  reads the prose, and it raises if a notice names no boundary at all,
  which is both a real failure and the way a suite would otherwise
  silently stop asserting anything. A binding to an agent this server
  is not serving names two at once, which is why the answer is a set.

Two claims the goldens were carrying are held better without them.
#192's compatibility claim, that the API answers the repository's own
sentence, is differential now: `test_the_api_answers_the_repository_s_own_words`
writes each refused fragment through the repository and again over
HTTP and compares `detail` and `errors` against the raised exception,
with neither written down. `test_the_cli_prints_the_same_sentence_for_a_problem_document`
does the same for the terminal. Four pointer tests in
`test_config_api_problems.py` folded into the refusal table that now
covers them case by case, their reasons moved into the table's
per-case comments.

**The privatization** (`e658000c`). Forty wording constants across five
modules had no reader left outside the module that raises them, and
each is underscored; the five that were listed in an `__all__` leave
it. `config/api.py` loses twenty-three (the refusal bodies, the
argument-body expectations, the claim refusals, the document
descriptions and `API_TITLE`), `config/store.py` three,
`vinga_server/app.py` six, `conversations/api.py` five, and
`config/reload.py` three.

### The survivors

No test in the config family imports a wording constant any more. What
does still read one is listed here in full rather than left to be
found, with what it reads it as:

| Name | Where a test reads it | Why it is not a wording pin | Counted below |
| --- | --- | --- | --- |
| `api.PROBLEM_TITLES` | `tests/support/problems.py`, `test_config_api_problems.py` | `problem_response` builds a body with it at runtime and the helper reads the same mapping, so the two agree by reading one source rather than by a copy. The second test holds it against RFC 9457's recommended reason phrases, which is a closed set and not this API's prose. | no, carved out |
| `api.PROBLEM_DESCRIPTIONS` | `test_config_api_problems.py`, `test_config_api_runtime.py` | Read as the shared description a route either inherits or overrides: every assertion is `== PROBLEM_DESCRIPTIONS[status]` or `!=`, which says which description a route carries and stays green when the prose is edited. The key-set test holds it equal to `PROBLEM_TITLES`, which is a closed-set claim. | no, carved out |
| `cli.LOCAL_NOTICE`, with the test-local `LOCAL_PREAMBLE` and `RESTART_TIMING` beside it | `test_config_cli_local.py`, nine references between them | The break-glass banner, which is neither a response nor an acknowledgement: it is printed before the command runs. Its equality with `LOCAL_PREAMBLE` is what ties that literal to the code for the exempt differential test, which pins the whole shape of what `--local` printed. The deviation below says why it did not retreat. | yes, nine |
| `entities.RESTART_NOTICE`, through `cli` | `test_config_cli_local.py`, one negative | Inside `test_a_local_write_says_what_the_api_says_for_the_same_act`, the differential pin decision 6 made exempt. The assertion is `not in`, a sentinel rather than a pin, and it is what keeps a preamble from reasserting restart timing ahead of a reload notice. | yes, one of the nine |

One reference sits outside this issue's territory and was left:
`tools/mcp`'s `RELOAD_REFUSED` at `test_tools_mcp_reload.py:449`, which
is the MCP tool surface rather than the config admin surface.

The registry's own sentences stay public and are not survivors in this
sense: `NO_SUCH_*` and the five `*_NOTICE` constants are declared in
`entities.py` and read by `store.py`, `api.py` and `cli.py`, which is
an ordinary cross-module reader and not a test.

### The retreat's count

The rule, stated so the numbers can be recomputed: a reference to a
wording constant or to a test-local golden sentence, inside a test
body, counted per file at the milestone's base (`abb20c1d`) and at its
tip. Import statements are excluded as whole syntax nodes rather than
by the shape of a line, so a name inside a parenthesised import counts
nowhere; so are the constants' own declarations and comments. Carved
out by name: `PROBLEM_TITLES` and `PROBLEM_DESCRIPTIONS`, for the
reason the table above gives, since a test reads them as the source of
truth rather than as prose.

| Suite | Before | After |
| --- | --- | --- |
| `unit/test_config_api_writes.py` | 41 | 0 |
| `unit/test_config_api_problems.py` | 26 | 0 |
| `unit/test_config_cli.py` | 22 | 0 |
| `unit/test_conversations_api.py` | 18 | 0 |
| `unit/test_config_api_runtime.py` | 11 | 0 |
| `unit/test_config_cli_local.py` | 11 | 9 |
| `unit/test_config_api_pending.py` | 9 | 0 |
| `unit/test_config_reload.py` | 9 | 0 |
| `unit/test_config_refusals.py` | 7 | 0 |
| `unit/test_config_round_trip.py` | 7 | 0 |
| `unit/test_config_store.py` | 7 | 0 |
| `unit/test_config_api_reads.py` | 6 | 0 |
| `unit/test_config_api.py` | 5 | 0 |
| `unit/test_config_diff_read.py` | 5 | 0 |
| `unit/test_config_reads.py` | 5 | 0 |
| `unit/test_config_snapshot_mode.py` | 5 | 0 |
| `unit/test_config_cli_secrets.py` | 3 | 0 |
| `unit/test_app_lifespan.py` | 2 | 0 |
| `unit/test_device_bindings.py` | 2 | 0 |
| `unit/test_tools_mcp_reload.py` | 2 | 1 |
| `integration/test_mcp_reload.py` | 2 | 0 |
| `integration/test_activation.py` | 1 | 0 |
| `integration/test_device_bindings.py` | 1 | 0 |
| **Total** | **207** | **10** |

The ten that remain are the last two rows of the survivor table: nine
in `test_config_cli_local.py`, all of them the break-glass banner and
the exempt differential test around it, and the one MCP tool reference
outside this territory.

### The inventory

Public names of `src/vinga_server/config/` (module-level classes,
functions, type aliases and non-underscored assignments), at the
milestone's base and at the tip. The same counting as M1's table, one
short in `api.py` because M1's own review round privatized the
acknowledgement constant this milestone would otherwise have taken.

| Module | Before | After |
| --- | --- | --- |
| `api.py` | 58 | 36 |
| `reload.py` | 6 | 3 |
| `store.py` | 14 | 11 |
| every other module | unchanged | unchanged |
| **Total** | **319** | **291** |

Outside the package, `app.py` loses six public names and
`conversations/api.py` five, by the same rule and in the same commit.

`wc -l src/vinga_server/config/api.py`: 2602 before, 2487 after. The
nine literals were 204 lines of string between them (144 of them
`API_DESCRIPTION`); what replaces them is the 80-line loader block (its
comments, the substitution table, the error class, the function and the
first assignment) and eight one-line assignments, so the net is 115.
`config/api_descriptions/` holds 9 files and 13,316 bytes.

### Deviations from the plan

Four, three of them judgements the plan left open rather than changes
of direction, and one a correction to a figure.

**The "~550 lines of description literals" figure was the issue's, and
it counted the docstrings.** The nine module-level constants decision 3
actually moves are 204 lines, of which `API_DESCRIPTION` is 144. The
rest of the document's prose is route docstrings, which fence one keeps
in place, so the larger figure was never what this milestone moves.
Recorded here because the first commit's message repeats it.

**The data files are verbatim, and the loader unwraps nothing.** The
plan says the move "preserves every byte including line wrapping". The
alternative considered was a loader that treats a newline inside a
paragraph as a soft wrap and joins it, which would let the files be
hard-wrapped and reviewable in a diff. It was rejected: it makes the
file's bytes and the contract's bytes two different things, which is
exactly the property the byte-identity proof rests on, and a paragraph
could then never carry a deliberate line break. So `api.md` holds eight
long lines separated by blank ones, which is what the constant held,
and the loader's only transformations are the sigils and the trailing
newline. `test_a_description_carries_the_file_through_unchanged` states
that as a test.

**The loader's two refusals are tested through underscored names.** The
plan asks for a named error at import and CI proves the missing-file
path only when it does not happen. The three tests added to
`test_api_openapi.py` reach `api._description` and `api._DESCRIPTIONS`,
which AGENTS.md flags as a review point. It is deliberate, and the
shape `test_build_info.py` already has for `_CHECKOUT`: what is being
exercised is a refusal raised at import, and the only way to reach it
without the reach-in would be to move the real directory aside under a
suite that runs four workers over one filesystem. The alternative,
making the loader public so a test could call it, would add an
interface for the test's benefit alone.

**`test_the_local_preamble_makes_no_timing_claim_of_its_own` did not
retreat.** It asserts `cli.LOCAL_NOTICE == LOCAL_PREAMBLE`, an exact
sentence. It stays because the preamble is neither a response nor an
acknowledgement (it is the break-glass banner, printed before the
command runs), and because that equality is what ties `LOCAL_PREAMBLE`
to the code for the exempt differential test, which asserts the whole
shape of what `--local` printed. Removing it would leave a floating
copy able to drift from the constant and fail the exempt test for a
reason that has nothing to do with what it holds.

Everything else landed as written: the three fences, the sigil
substitution, the named import-time refusal, the wheel proof, the
per-test retreat under decision 4's one rule, the exempt differential
pin and the no-leak sentinels untouched, and the no-residue
privatization with the survivors listed.

### Verification

- `uv run ruff check .`: clean, at each of the five code commits and at
  the tip.
- `uv run mypy` (the scoped `events` lane): clean.
- `uv run pytest tests/unit -q` (serial): 2861 passed, 20 skipped,
  measured before the branch was rebased onto main. The count is 2863
  after it, M1's own review round having added two.
- `uv run pytest tests/unit -q -n 4 --dist loadfile`: the same, and at
  each of the five code commits.
- `uv run pytest tests/integration -q`: 61 passed.
- `uv run vinga-server config openapi`, diffed against the committed
  document: empty at every one of the milestone's six commits, this one
  included. Checked mechanically rather than by recollection, by
  extracting each commit's `src` on its own and rendering the document
  from it.
- `uv run vinga-server config reference | diff - ../docs/reference/domain-config.md`:
  empty at the tip. The milestone touches nothing it renders, which is
  why it is checked once rather than per commit.
- The wheel proof, run locally: `uv build --wheel`, installed into a
  fresh venv, the workflow's script run with `-P` from outside the
  checkout, its output diffed clean against the committed document. And
  the negative: one description file deleted from the installed package
  makes the import refuse with `MissingDescriptionError` naming it.
- The mutation, which is what says the byte-identity check is load
  bearing: one word changed in
  `config/api_descriptions/reload-held.md` makes the OpenAPI diff
  non-empty on that description and turns
  `test_the_committed_document_matches_the_routes` red. Reverted by
  copy-back and `touch`, and both go green again.

## PR review round, M2 (PR #264)

External review of the PR diff: claude backend (the codex quota is
exhausted), claude CLI, model `claude-opus-5`, read-only tool set,
2026-08-23, posted on the PR. Verdict as received: "Mergeable after fix
1; findings 2 to 5 are worth folding in and none blocks." One P2 and
four P3s. The round verified the milestone's load-bearing claims itself
and could not break them, which is what makes the five findings the
whole of what is left. As received:

> I could not break the milestone's load-bearing claims: the loader's
> two refusal paths are both exercised, the sigil substitution is
> unambiguous against the prose (no `$` in any description file,
> `re.sub` with a callable does not reprocess the replacement), the
> byte-identity of `docs/reference/api-openapi.json` is held by the
> pre-existing checkout drift step *and* the new wheel step, the wheel
> step fails closed on both a missing data file (import raises before
> the diff) and altered prose, and the 40 privatized names have no
> reader left anywhere in `src` or `tests`.

It also verified the wheel step's mechanics against the workflow (the
venv and the run directory exist from the step above, the heredoc
terminator's column, `docgen.openapi()` ending in a newline so it can
diff against the committed file, and hatchling shipping non-`.py` files
under `src/vinga_server`, with `script.py.mako` as the precedent).

Every finding has its own commit.

1. **P2: the conversations argument refusal lost its quotes-nothing
   proof for every row but the sentinel ones.** The retreat replaced a
   whole-body comparison, whose own comment said there is nowhere for
   what was sent to be, with "the argument is named". Six of the nine
   rows carry values that are not the sentinel, and nothing asserted
   those were absent; and since `refused()` does not pin `errors`
   empty, a refusal that grew a field entry quoting the value would
   have passed on every row. Fixed in `5232eab3`, taking both halves of
   the suggestion. `paths(body) == []` is the structured half: these
   rules are about a query argument rather than a field of a body, so
   naming no field is the honest answer and the value has nowhere to
   be. `value not in response.text` is the other half, per row, which
   is why the zero row is spelled `000`: a bare `0` occurs inside the
   bounds the sentence names, so looking for it would have proved
   nothing. The comment now says what the assertions hold.

2. **P3: four tests named for a fixed sentence asserted only the body
   shape.** Nothing held the claim in their own names, that two
   different stored-side causes answer with one detail that names no
   location. Fixed in `d2fa5ded`, taking the differential option where
   it applies and the carried-token option where it does not. The two
   tests called "refuses the same way", in `test_config_reload.py` and
   `test_config_diff_read.py`, now drive both causes over one store,
   the planted provider and then the credential that will no longer
   open with the row put back, and assert the two answers carry the
   same `detail`: a claim neither could make before, and one a copied
   sentence cannot make at all, since a copy agrees with itself
   whatever the two refusals do. The two beside them assert
   "deliberately not said here", which only the fixed sentence carries.
   No test needed renaming afterwards; the planting and the restoring
   moved into named helpers.

3. **P3: `"store"` did not identify the snapshot-mode refusal.** It is
   satisfied by the other 409 the comparison read answers, the held
   write lock, whose sentence says the stored half could not be read.
   Fixed in `796aff49`: `"will not help"`, which the snapshot refusal
   alone carries and which is the whole difference between the two, and
   which the sibling assertion and the runtime suite already used.

4. **P3: the retreat census was not computed by the rule it stated,
   and the survivor list was not exhaustive.** Three mismatches, all
   fixed in `b055179e` by recomputing rather than by editing numbers.
   Import statements were excluded by the shape of a line, so a name
   inside a parenthesised import counted as a body reference: that is
   what made the MCP row two rather than one, and it inflated most of
   the before column. They are excluded as whole syntax nodes now, and
   both columns are recomputed, 207 before and 10 after. `LOCAL_NOTICE`
   and the test-local golden beside it were disclosed only in the
   deviations, where decision 4 asks for a survivor entry; they are a
   fourth row now, cross-referencing that deviation, and their nine
   references are counted. And the two status mappings were listed as
   survivors while the files reading them showed zero, so the carve-out
   is stated and the table says per row whether a survivor is counted.

5. **P3: the CHANGELOG undercounted the retreat.** Twenty-three test
   files carry it, three of them the integration lane's, which is what
   the census table lists; the support modules and the document suite
   are not retreats. Fixed in `88748890`.

Beside the five, the recorded commit hashes in this milestone's section
were re-anchored: the branch was rebased onto main after M1 merged, so
every hash the section named was of a commit no longer in its history.
The inventory's base moved with them, and its `api.py` column is one
lower at both ends than it was: M1's own review round privatized the
acknowledgement constant this milestone would otherwise have taken.

### Verification after the round

All from `vinga-server/`.

- `uv run ruff check .`: clean.
- `uv run mypy`: clean.
- `uv run pytest tests/unit -q -n 4 --dist loadfile`: 2863 passed, 20
  skipped. The round added no test, findings 1 to 3 sharpening
  assertions inside tests that already existed; the two above the
  milestone's own 2861 came with the rebase onto merged main, where
  M1's review round had added them.
- `uv run pytest tests/integration -q`: 61 passed.
- `uv run vinga-server config openapi | diff - ../docs/reference/api-openapi.json`:
  empty, and empty at every commit of the branch, checked by rendering
  the document from each commit's extracted `src`.
- `uv run vinga-server config reference | diff - ../docs/reference/domain-config.md`:
  empty.
