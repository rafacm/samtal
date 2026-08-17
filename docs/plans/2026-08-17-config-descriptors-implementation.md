# Single-source the domain config schema behind per-entity descriptors: implementation

Companion to
[`2026-08-17-config-descriptors.md`](2026-08-17-config-descriptors.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out.

## The inventory, taken fresh at main@a1d5dd2

The issue's own evidence is pinned to main@8dd1a5f, 33 config commits
behind this branch's base, and `prompt_fragments` did not exist at that
pin. The inventory below was taken at a1d5dd2, which is the commit this
branch is based on, and it is what the plan's evidence section cites.
It is recorded here because it is what every milestone's design rests
on, and because a number that moves under a later milestone is a
finding rather than a typo.

**Per-entity sites.** A kind is spelled at 14 to 19 places across 12 to
14 files: provider 18 sites in 13 files, mcp-server 19 in 14,
prompt-fragment 15 in 12, agent 15 in 12, agent-defaults 14 in 12. The
three non-fragment surfaces (devices, default-agent, secrets) run 13 to
16 sites each. The issue's estimate of 11 was low, and adding
`prompt_fragments`, the most recent kind, hand-edited 13 config-surface
files.

**Three store mappings a `model_dump()` round trip cannot express.**
`_mcp_from_row` omits keys per six distinct per-column rules so that
`McpServerConfig`'s transport validator can read `model_fields_set`;
`_layer_values` and `_layer_data` encode the tri-state where None
inherits and an empty list opts out, and re-emit each MCP grant in the
form it was written so that an older reader keeps reading the column;
`_provider_from_row` and `_provider_values` split the declared fields
from the `options` extras of an `extra="allow"` model.
`models.domain_fields` exists precisely to avoid a dump round trip and
says so in its docstring.

**The five commanded kinds are not uniform.** `agent-defaults` is a
singleton with no delete anywhere (no route, no subparser, no
sentence) and a constant acknowledgement. `prompt-fragment` and `agent`
write inline dicts with no `_values` helper. `prompt-fragment` checks
its name before parsing, for a documented ordering reason.
`mcp-server` runs `check_mcp_entry_names` inside its write. `provider`
alone runs the URL-credential checks, and alone is addressed by two
path segments.

**docgen already had three tiers.** Seven `ENTITIES` (five commanded,
two nested with `command=None`), two `SETTINGS` rendered by a different
function, plus secrets described in prose only. That is the shape the
descriptor registry took.

**The CLI must not import FastAPI.** `writes.py`'s docstring records
that having the CLI import the API was rejected, so that
`config schema` and `config reference` never pay for FastAPI's imports;
`test_config_docgen.py` and the CI docs lane enforce it. The response
models M3 moves therefore cannot stay in `api.py` for the CLI's
purposes.

**Deliberate non-derivations that must survive.** `views.provider_record`
is built key by key so that a new model field is absent from every
record until somebody decides it belongs, and `device/session.py`
consumes it. `RawBody` plus `openapi_extra` exists because FastAPI's
validation echoes rejected input, which may hold a pasted credential.

**The dispatch.** One `--local` gate plus 14 per-command
`if args.local:` branches (15 occurrences of the test in `cli.py`). The
notice constants have been unified since #134 but are still chosen at
two call sites per act, with two single-sided nuances: the API alone
computes `binding_notice(unloaded)`, and the CLI alone maps
`secret_notice(kind)` where the API splits the same choice across four
routes.

**The shape bridge M4 deletes.** Five predicate constants at
`cli.py:159-190`, nine predicate functions, four renderers, and
`tests/unit/test_config_cli_shapes.py`, whose docstring says it exists
to be deleted wholesale by this issue.

**Command strings.** Seven independent encodings: the loader's
`MOVED_KEY_COMMANDS`, docgen's entity table, `config.example.yaml`, the
example fragment headers, the examples README, the deploy script and
the smoke seed script, plus about 20 occurrences in `README.md`. The
loader/docgen pair is byte-identical placeholder text held together by
nothing, which is the pair a descriptor can own; the other five are
concrete invocations a person copies and are drift-checked by the tests
that execute them.

**The contract surfaces.** `tests/unit/test_config_cli.py` (2305 lines,
101 tests) and the examples-driven tests are the acceptance files. The
two generated references have CI drift steps of their own. The refusal
text amounts to 20 symbols in `writes.py`, 21 strings landing in the
OpenAPI document, and dozens of raise-site sentences across store, cli,
models, secrets and loader.

## Milestone 1: the descriptors exist and docgen consumes them

`samtal-server/samtal_server/config/entities.py` (new, 463 lines)
declares the three tiers and their registries: five `EntityDescriptor`
entries (provider, mcp-server, prompt-fragment, agent, agent-defaults),
two `NestedShape` entries (mcp-grant, filler) and two `Setting` entries
(devices, default_agent). It imports the models and the standard
library and nothing else, which is what keeps a descriptor readable by
the documentation renderer on a machine with no database, no key and no
FastAPI.

`samtal-server/samtal_server/config/docgen.py` keeps its renderers and
loses its data: the `Entity` dataclass, the seven-entry `ENTITIES`
tuple and the four-tuple `SETTINGS` are gone, and what remains is
`ENTITIES: tuple[DocumentedShape, ...] = (*COMMANDED, *NESTED)`, the
order the document has always had.

`samtal-server/tests/unit/test_config_entities.py` (new, 63 lines, 4
tests) holds the registry to the surfaces it describes.

Three commits, plus the one that records the milestone:

1. `bbd0da5` Describe every domain entity in one registry
2. `75577da` Render the reference from the descriptors
3. `9fab044` Hold the registry to the surfaces it describes

### What is filled, and what is only declared

The plan's rule was that a fact group is filled by the milestone that
wires its consumer, so that M1 invents no value nothing validates. M1's
only consumer is docgen, so the documentation facts (name, title,
location, model, purpose, command, examples, notes) are filled from
what docgen already held, byte for byte.

Filled anyway, because they are static identity facts rather than
guesses: `route` and `addressing` (the API path prefix and the
parameters under it, which are also the CLI's positional argument names
and are the same names for the same reason, so a provider's two are
data rather than a special case), `has_delete` (False for
agent-defaults alone), `secret_slots` (provider and mcp-server alone),
and `moved_key`, which is the `DOMAIN_KEYS` member whose
`MOVED_KEY_COMMANDS` entry is this kind's `command`.

Declared with their defaults and named for the milestone that fills
them: the store facts (`table`, `from_row`, `to_row`, `before_parse`,
`inside_write`) and the view `body` for M2; `endpoints` and `missing`
for M3; `summary`, `wrote`, `deleted` and `notice` for M4. The hook
type is deliberately loose (`Callable[..., object] | None`), because
each group's signature is settled beside the code that calls it and
naming one now would be a guess dressed up as a contract.

### The byte-identical proof

The core proof of a move is that neither generated reference changes.
Both were regenerated exactly as the CI drift steps run them, from
`samtal-server/`, and diffed against the committed copies:

```
$ uv run samtal-server config reference > "$RUNNER_TEMP/domain-config.md"
$ diff -u ../docs/reference/domain-config.md "$RUNNER_TEMP/domain-config.md"
(no output from diff -u: the files are identical)
$ uv run samtal-server config openapi > "$RUNNER_TEMP/api-openapi.json"
$ diff -u ../docs/reference/api-openapi.json "$RUNNER_TEMP/api-openapi.json"
(no output from diff -u: the files are identical)
```

Both diffs print nothing and exit 0, which is the whole of the check.
Neither committed reference is touched by this branch, which is the
other half of the claim: there is no regeneration commit to point at.

### Who imports the retired names, and how they stayed unmodified

Two importers, both unmodified.

`tests/unit/test_config_docgen.py` iterates `docgen.ENTITIES` twice
(the field-coverage test at line 95 and `_claims`, which the
example-bijection pair both read, at line 139), names it in three
failure messages, and calls `docgen.entity("filler").command`. Keeping
`docgen.ENTITIES` as the composed tuple of the two tiers, with the same
seven members in the same order and the same attribute names on each,
kept the file byte-unchanged. No port was needed, and none is
recorded.

`samtal_server/config/api.py` imports `API_OPTIONS_NOTE` from
`docgen`. The provider-options contract moved to `entities.py` with the
descriptor whose note quotes it, and `docgen` re-exports the name,
listed in `__all__` so the re-export is deliberate rather than an
unused import ruff would flag. `api.py` is untouched, which the plan
wanted: it belongs to M3.

### Deviations from the plan

1. **`docgen.ENTITIES` survives as a composed name.** The plan says
   docgen's own `Entity`/`SETTINGS` definitions retire in favour of the
   registry, which they did; what did not retire is the *name*
   `ENTITIES`, because the document renders two tiers as one sequence
   of sections. It is now `(*entities.ENTITIES, *entities.NESTED)`,
   which is what kept the docgen tests unmodified.
2. **`route` is filled at M1, not M3.** The plan lists the route prefix
   among the API facts M3 wires. It is the same static addressing fact
   as the path parameters beside it, and splitting a URL across two
   milestones would have left the descriptor unable to say where a kind
   lives. What M3 still owns is everything about the endpoints
   themselves.
3. **`secret_slots` holds a plain string.** The plan calls it a
   descriptor fact carrying the `secrets.EntityKind` member. This
   module imports the models and the standard library only, and
   `secrets.py` sits above them, so the fact is a `str | None` and
   `test_config_entities.py` pins the set of values to `get_args(EntityKind)`
   instead. The two members are still exactly two.
4. **`endpoints` is declared as `tuple[object, ...]`.** The plan's
   review round grew the descriptor's API facts to carry each
   endpoint's operation identity, description, response and status
   declarations and parameter signature. The group is named here so
   that M3 does not invent a second descriptor to hold it, but its
   element type is M3's to settle beside the route factory that
   installs it, and inventing that shape now would have been a design
   nothing could check.
5. **`fields_in_help` stops being a field.** It was declared on
   `docgen.Entity`, written on all seven entries, and read by nothing
   (grep across the package and the suites finds three occurrences, all
   of them writes). What it states is that a nested shape has no
   command to list its fields on, which is true of the tier rather than
   of an entry, so each tier states it once as a `ClassVar`.
6. **`EXAMPLES` and `CONFIG_FILE` moved too.** They are the locations
   the descriptors' `examples` and the agent's note are written
   against, so they moved with the prose and docgen imports them back.
7. **A registry-coherence test was added.** The plan named no test for
   M1. Four assertions were cheap and are load-bearing while the
   consumers are still being moved over: every descriptor names a real
   `DOMAIN_KEYS` member, its `command` is byte for byte the loader's
   `MOVED_KEY_COMMANDS` entry for that key, the documented shapes are
   exactly the two tiers with unique names, and stored secrets hang on
   exactly the two kinds `EntityKind` admits. The unit count rises by
   four, which the plan's never-lower rule allows.
8. **The `shown_values` finding is filed as issue #171.** The plan
   has M1 file it as its own issue; the body recorded below is what
   was posted, kept here as the record of what M1 found and why the
   contract forbade fixing it in place.

No other deviation. No behavior changed, no sentence moved, and no
consumer other than docgen reads a descriptor yet.

### Verification

From `samtal-server/`, with `PYTHONDONTWRITEBYTECODE=1` outside pytest:

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `2927 passed, 16 skipped in 307.50s`.
  The lane collected 2939 before this milestone and 2943 after, the
  difference being the four coherence tests; the skips are the same 16.
- `uv run pytest tests/integration -q`: `55 passed in 159.41s`,
  collection unchanged at 55.
- Both generated references regenerate byte-identical, as above, with
  no regeneration commit anywhere on the branch.
- `tests/unit/test_config_docgen.py`, `tests/unit/test_config_cli.py`
  and `tests/unit/test_config_examples.py` are byte-unchanged.

### The filed follow-up: issue #171

The inventory found one real surface question inside the walker family,
and the no-behavior-change contract is exactly why it must not be
answered here: masking one more value would change what a read prints.
It is filed as issue #171 with the body recorded below; the plan's
walker section cross-references it.

````
Title: The nested-value masking gap in an MCP server's env and headers

An MCP server's `env` and `headers` are masked flat, while a provider's options are masked at every depth. The two display paths make opposite assumptions about whether the models can be relied on, and only one of them says so.

**The asymmetry**

`views.masked_option` (`samtal-server/samtal_server/config/views.py:237-255`) walks mappings and lists and masks a secret-shaped key wherever it finds one. Its docstring states the reason as a rule rather than an accident: it "does not rely on" the model's refusal, because it "is the last thing standing between a row that got its contents another way and a caller, so it fails closed on its own".

`views.shown_values` (`samtal-server/samtal_server/config/views.py:329-336`) masks only the top level: `{key: mask(value) if is_mcp_secret_key(key) else value for key, value in values.items()}`. Its docstring makes the opposite bet: "The model already requires a $VAR for the secret-bearing keys, so this changes nothing for a valid entry". It is called for both of an MCP server's value maps, at `views.py:265` (`env`) and `views.py:269` (`headers`).

The validators match their views: `models.check_no_inline_secrets` (`samtal-server/samtal_server/config/models.py:766-801`) recurses into provider options at every depth, and `McpServerConfig._secret_problems` (`samtal-server/samtal_server/config/models.py:1150-1165`) checks the two maps one level deep.

**Evidence**

Built without validation, which is what "a value that got in another way" means concretely, and is the shape `tests/unit/test_config_reads.py:215-223` already uses to pin the provider side. Run as written except that `model_construct` wants the remaining declared fields spelled out, which the `...` stands in for:

```python
p = ProviderConfig.model_construct(type="mock", api_key_env=None, egress=None,
                                   options={"connection": {"api_key": "sk-live"}})
views.provider_body(p)
# {'type': 'mock', 'options': {'connection': {'api_key': '********'}}}

m = McpServerConfig.model_construct(transport="stdio", command="x", headers={},
                                    env={"A": {"api_key": "sk-live"}}, ...)
views.mcp_server_body(m)
# {'transport': 'stdio', 'command': 'x', 'env': {'A': {'api_key': 'sk-live'}}, ...}
```

**How reachable it is today**

Not, through the normal read path, and this is the honest half of the report. `env` and `headers` are typed `dict[str, str]` (`models.py:969` and `models.py:986`), `_mcp_from_row` validates every row it reads, and a mapping in a value fails that validation with `Input should be a valid string`, so the whole domain read raises `StorageError` rather than showing anything. The gap is latent: it is the difference between a display path that fails closed on its own and one that is safe only for as long as a type annotation two modules away holds. Widening those maps to carry structured values, or any future path that builds an entry without validating it, turns it into a leak with no test in the way.

**Why #139 must not fix it silently**

#139 is a refactoring issue under a no-behavior-change contract, whose proof is that both generated references regenerate byte-identical and every refusal, notice and rendered value is unchanged. Making `shown_values` recursive changes what a read prints for an input that reaches it, which is a behavior change however small the set of such inputs is, and it would be indistinguishable in review from the move #139 is. #139's plan therefore records the finding and leaves `shown_values` exactly as it is.

**What the fix would weigh**

Small in code, and the weight is in the decisions: whether the recursive walker is `masked_option` reused (its provider-flavoured `is_secret_option` differs from `is_mcp_secret_key`, which also matches `auth`) or a third walker with the MCP predicate; whether the same depth rule should apply to `McpServerConfig._secret_problems` so that write-time refusal and read-time masking keep saying the same thing; and whether the URL-credential stripping that `recorded_option` applies to providers belongs on an MCP server's `url` too, which is a neighbouring gap this issue does not claim. Two tests, mirroring the provider fail-closed pins that already exist.
````

## Milestone 2: store and views generalize over the descriptors

The repository stopped naming its five kinds at every method. What a
kind is rowed in, how one of its rows becomes a model and back, what to
refuse when an entry is not there, and which checks its write runs are
descriptor facts now, and `store.py` has one read, one write and one
delete over all of them. `views.py` reads which builder shows a kind
from the same registry. Neither module changed a byte of what it says
to anybody.

Eleven commits, plus the one that records the milestone:

1. `32e702f` Say on the descriptor what is not there
2. `a4b4240` Read a stored row through the kind's own mapping
3. `9e09dd2` Write a row through the kind's own mapping
4. `cbf577d` Refuse a write where the kind says to
5. `e7ba1ba` Read, write and delete one kind generically
6. `1ff5b25` Address a stored secret through its holder
7. `3ff9a6e` Walk a value once for both refusals
8. `511081a` Show an entity through its kind's descriptor
9. `7308897` Say in the registry which table a kind is rowed in
10. `34fdfa2` Ask one function which builder shows a kind
11. `4b730cd` Say in the registry which facts it writes down itself

### What each fact group turned out to be

`table` is data, so it is written in the registry beside `route` and
`moved_key` and the repository resolves it against the schema. That is
what makes a kind whose model says everything about its row cost
nothing outside the registry, which is the acceptance criterion this
milestone can already demonstrate for its half: a new such kind is a
model, a table with its migration, and one descriptor entry.

The row mapping's default path is `model_validate` over the columns
named by `model.model_fields`, and `model_dump()` back. Exactly one
kind takes it: `prompt-fragment`, whose hand-written reader and whose
inline `{"text": entry.text}` are both gone. The other four keep the
mappings the inventory proved a dump cannot express, moved unchanged:

- `_provider_from_row` and `_provider_values`, the split between the
  declared fields and the `options` extras of an `extra="allow"` model.
- `_mcp_from_row`, all six per-column omission rules, so that
  `McpServerConfig`'s transport validator keeps reading
  `model_fields_set`, and `_mcp_values` beside it.
- `_layer_data` and `_layer_values`, the tri-state where None inherits
  and an empty list opts out, including the per-element
  `mcp_entry_fragment` call and the comment saying why it re-emits each
  grant as it was written.
- `_agent_from_row` and `_agent_values`, which are the layer's pair plus
  the prompt. The agent's inline write dict became the named
  `_agent_values`; the singleton's inline read became the named
  `_defaults_from_row`.

The checks became two slots, because a write has two moments for one:
`before_parse` for `prompt-fragment`'s name rule, which must be able to
refuse a name before anything has looked at the body, and `inside_write`
for `mcp-server`'s entry-name check and `provider`'s URL-credential
check, which are about the parsed entry. The paragraph explaining the
fragment's ordering moved onto the check it explains, which is what a
reader of the write is now pointed at.

`missing` is the refusal builder from the plan's review round. Four
kinds share one shape (`<section>.<identity>: no such <noun>`), the
fragments answer the fixed `NO_SUCH_FRAGMENT` that does not repeat what
it was given, and the singleton carries none, which is how "there is no
missing case" is said. The store's read, its delete and its
stored-secret slot check all ask the descriptor, so those sentences
exist once each.

### The port table

Empty, and the reason is worth recording rather than asserting: exactly
one test in the suite reaches into `store.py`'s privates, and the name
it reaches for survives.

| Test | What it pins | What happened |
| --- | --- | --- |
| `tests/unit/test_config_store.py::test_two_concurrent_writers_serialize` | Monkeypatches `store_module._read_domain` to pace two writers and prove BEGIN IMMEDIATE serializes them | No port. `_read_domain` keeps its name, and every caller still resolves it as a module global rather than through a captured reference or a descriptor hook, so the patch reaches the generic read exactly as it reached the five hand-written ones. The test is byte-unchanged and passes. |

No other test named a private helper of `store.py` or `views.py`: the
grep for `_provider_values`, `_mcp_values`, `_layer_values`,
`_provider_from_row`, `_mcp_from_row`, `_layer_data`,
`_fragment_from_row`, `_agent_from_row`, `_nonfinite`,
`_untransportable`, `_stored_slots`, `_shadowed`, `_secret_row`,
`_check_no_url_credentials`, `_delete_row`, `_upsert`,
`_readable_domain` and `_read_domain` across `tests/` finds only that
one test's two lines, everything else it matches being a name of its
own elsewhere in the server (`resolve_mcp_values`, the MCP tool
manager's own `_shadowed`, the `mcp_tool_shadowed` event). The
tri-state and as-written-grant store tests, the fragment refusal tests
and the whole `test_config_reads.py` view suite pass unmodified, which
is what the hooks moving rather than being rewritten is for.

No test was added either, so the collected count is exactly equal
before and after. That is the strongest available statement that this
milestone was a move.

### Deviations from the plan

1. **A code-valued fact is filled by the module that owns the code.**
   The plan says the hooks "live beside the descriptor". They live
   beside the code they are written in, and reach the descriptor
   through one documented call, `entities.fill`, at the owning module's
   import. The alternative was not available: `entities.py` imports the
   models and the standard library only, which is what lets `docgen`
   render the reference on a machine with no database or key, and the
   row mappings are written in terms of the repository's own row
   helpers while the body builders are written in terms of the masking
   rules. Importing either from the registry is a cycle, since both
   import the registry. The other alternative, a per-consumer table
   keyed by kind, is two copies of a kind's facts that can come to
   disagree, which is the drift the issue exists to end. `fill` refuses
   a fact that is not declared and a fact that is already filled, so
   filling stays once each.
2. **The hook signatures were settled here, as M1 said they would be.**
   `from_row(row)` answers the model and computes its own location, so
   the three moved readers are unchanged; `to_row(entry)` answers the
   columns; `before_parse(*identity)`; `inside_write(location,
   identity, entry)`; `missing(*identity)` answers the sentence. The
   two write checks share one signature so that the write can call
   whichever its kind names without knowing which it got, and the
   provider's ignores the identity, which the leading underscore says.
3. **`missing` is filled at M2, not M3.** The plan lists it among the
   API facts. Its first three callers are all in the store (the read,
   the delete, and the entity half of the slot check), so it is filled
   where those are. M1's `route` deviation is the precedent: a fact is
   filled by the milestone that first has a consumer for it.
4. **`NO_SUCH_FRAGMENT` moved to `entities.py`.** The fragment's
   `missing` is that constant, and the descriptor cannot import the
   store. It moved with its comment intact and `store.py` re-exports
   it, so both test files that import it from there are unchanged. This
   is M1's `API_OPTIONS_NOTE` move in the other direction.
5. **`_check_slot` keeps its two branches.** Only the entity half
   generalized: which kind holds the slot, whether the entity exists,
   and the sentence when it does not. The slot-shape rules stayed two
   rules, because a provider's slot is a secret-shaped option name and
   an MCP server's is a dotted `env`/`headers` path, which is not one
   rule with a parameter. The provider's `_stage` check stayed exactly
   where it was, so a location naming a stage that is not one still
   meets the sentence a caller's typo meets everywhere else rather than
   an `AttributeError`.
6. **`_shadowed` is not a descriptor hook.** It dispatches on the model
   type, the descriptor declares no fact for it, and inventing one was
   not in the plan's list. What did generalize around it is the
   plumbing the plan named: `stored_secrets`, `_read_secrets`,
   `_secret_row` and the slot check all read `secret_slots` now, and
   `EntityKind` is untouched at two members.
7. **The merged walker returns a composed sentence in both modes.** The
   plan asked for one walker with a mode flag and byte-identical
   messages. The row mode used to answer a location that `_stored`
   formatted into `_NOT_FINITE`; it now answers the formatted sentence,
   and `_stored` wraps it in the same words, so the bytes are identical
   and the two modes mean the same thing by their return value. The row
   mode keeps having no cycle rule and refusing nothing but numbers,
   which is the requirement that made this merge worth checking rather
   than assuming: a cyclic input still recurses to a `RecursionError`
   there exactly as before, and a `set` or a date in a row is still
   passed over.
8. **The `Setting` tier keeps its hand-written store code.** Devices and
   the default agent are written with their own verbs (`bind`, `claim`,
   `delete`, `set`, `clear`), the tier declares no store facts at all,
   and the plan's descriptor section says so. Nothing about them moved.

No other deviation. `views.provider_record` is untouched, docstring and
key-by-key construction intact, and now says so where the body builders
are registered. `masked_option`, `recorded_option`, `shown_values` and
`check_no_inline_secrets` are untouched, and the `shown_values` finding
stays with issue #171, where M1 filed it.

### The differential check on the walker merge

The one change where "byte-identical" needed more than the suite, since
the merge crosses two callers with different rules. Both replaced
walkers were reimplemented verbatim in a scratch script and compared
against the merged one over 182 shapes: every leaf kind JSON has and
does not have, nested one to three deep in mappings, lists and tuples,
with non-string keys, an anchor shared by two keys, and NaN and the
infinities at every depth. Zero mismatches in either mode, and a
self-referential list still raises `RecursionError` in the row mode
under both implementations while both fragment modes name it.

### Verification

From `samtal-server/`, with `PYTHONDONTWRITEBYTECODE=1` outside pytest:

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `2927 passed, 16 skipped in 306.98s`.
  The lane collected 2943 before this milestone and 2943 after: no test
  was added, removed or split, and the skips are the same 16.
- `uv run pytest tests/integration -q`: `55 passed in 183.91s`,
  collection unchanged at 55.
- Both generated references regenerate byte-identical, run exactly as
  the CI drift steps run them, with no regeneration commit anywhere on
  the branch:

```
$ uv run samtal-server config reference > "$RUNNER_TEMP/domain-config.md"
$ diff -u ../docs/reference/domain-config.md "$RUNNER_TEMP/domain-config.md"
(no output from diff -u: the files are identical)
$ uv run samtal-server config openapi > "$RUNNER_TEMP/api-openapi.json"
$ diff -u ../docs/reference/api-openapi.json "$RUNNER_TEMP/api-openapi.json"
(no output from diff -u: the files are identical)
```

- `git diff --stat` against the milestone's base over `tests/` and
  `docs/reference/` prints nothing at all. Not only the two acceptance
  files and the two committed references: no test file in the
  repository changed, which is what an empty port table means when it
  is true.

### PR review round

External review of PR #173 (diff main...be49dea) by codex 0.147.0
(model gpt-5.6-sol), 2026-08-17, posted to the PR by the review run
itself. Verdict: mergeable as is, no findings. The empty port
table, the equal counts, and the differential walker proof
pre-answered the lenses a store-and-views move touches.

## Milestone 3: the response models move and api.py generalizes

The five commanded kinds stopped having twenty-two hand-written route
handlers. `api.py` walks the registry and installs each endpoint a
descriptor declares, and the pydantic shapes it answers with moved one
import below FastAPI so that a reader who may not have FastAPI can have
them. The committed OpenAPI document did not move a byte, which is the
whole of the claim: every operation id, summary, description, parameter
and status in it now comes from the registry.

Three commits, plus the one that records the milestone:

1. `1eae4d7` Keep the API's shapes one import below FastAPI
2. `14ee7e3` Say in the registry what routes a kind has
3. `a951387` Build a kind's routes from its descriptor

`samtal-server/samtal_server/config/responses.py` (new, 462 lines) holds
the twelve response models and the three request models, byte for byte
what they were. `api.py` went from 2142 lines to 1713 and names none of
the five entity models any more. `entities.py` grew the `Endpoint` shape
and 22 endpoint literals.

### What the endpoint facts turned out to be

An `Endpoint` says what the committed document says about one route and
nothing else: the verb, the operation's name, the description exactly as
the document carries it (already dedented, since that is what FastAPI
does to a docstring on its way into a description), the response model,
and the statuses it declares. Those are the bytes a factory cannot
compose, because they come from what a hand-written handler happened to
be called and what its docstring happened to say: the operation id is the
function's name followed by its path, the summary is that name with its
underscores turned to spaces and title-cased. A summary assembled out of
a kind's `title` would read almost like the committed one, and almost is
a drift check that fails.

What is deliberately not on an `Endpoint` is what the verb settles the
same way for all five kinds: the HTTP method, the path under the kind's
route, the handler's parameters, the request body (a write of the kind
carries the kind's own model, a secret write carries the credential
body), and which repository call is made. Those are one rule with no
exception across the five, and writing them out per endpoint would have
been five chances to disagree. The verbs are six because the surface is
six, and a kind with fewer says so by listing fewer endpoints: the
singleton has no collection and no delete, and three of the five hold no
secret.

The descriptions were extracted from the handlers' own docstrings by a
script rather than retyped, and each is written in the registry as one
string per line of it, so the diff that ever changes one shows which
line moved.

The handler itself is built and then told what it is. FastAPI reads
three things off a route function that the document then carries: its
`__name__`, its `__doc__`, and its signature, in the order the
parameters are declared. All three are installed from the descriptor.
The generated function stays a plain `def`, so FastAPI still runs it on
the threadpool and the synchronous repository never blocks the event
loop.

### The byte-identical proof

Both generated references were regenerated exactly as the CI drift steps
run them, from `samtal-server/`, and diffed against the committed
copies:

```
$ uv run samtal-server config reference > "$RUNNER_TEMP/domain-config.md"
$ diff -u ../docs/reference/domain-config.md "$RUNNER_TEMP/domain-config.md"
(no output from diff -u: the files are identical)
$ uv run samtal-server config openapi > "$RUNNER_TEMP/api-openapi.json"
$ diff -u ../docs/reference/api-openapi.json "$RUNNER_TEMP/api-openapi.json"
(no output from diff -u: the files are identical)
```

Both diffs print nothing and exit 0. Neither committed reference is
touched by this branch: there is no regeneration commit to point at.

Three things about the document turned out to be load-bearing and are
recorded so the next reader does not have to rediscover them. The paths
appear in the order they are first registered and the operations under
one path in the order they were added to it, so the factory is called
once from `_reads` and once from `_writes` rather than once per kind:
every read of every kind is registered before any write, which is the
order the committed document has. The schemas FastAPI collects from
routes are sorted, but the entity and request models `_entity_schemas`
injects are not, so `ENTITY_MODELS` deriving from the registry has to
keep the registry's order, and it does. And the two-segment kind's
listing is keyed twice, which is why `_collection` asks how many
parameters address a kind rather than which kind it is.

### Who imports the moved models, and how they kept working

Five importers, all unmodified.

`tests/unit/test_config_cli_shapes.py` reads `api.PendingDevice` and
`api.McpServerStatus` through `from samtal_server.config import api`.
`tests/unit/test_config_api_runtime.py`, `test_config_api_pending.py`,
`test_config_api.py` and `test_api_openapi.py` import constants and
`build_api` from `api`, which did not move. The fifteen model names are
re-exported from `api.py` and listed in its `__all__`, which is what
makes the re-export deliberate rather than an unused import ruff would
flag, and is M1's `API_OPTIONS_NOTE` move again.

The port table is empty. No test file in the repository changed, and
`git diff --stat` over `tests/` against the milestone's base prints
nothing.

### Deviations from the plan

1. **`entities.py` imports `responses.py` as well as the models.** The
   module docstring's rule was "the models and the standard library, and
   nothing else", and the rule behind the rule is that `docgen` renders
   the reference on a machine with no database, no key and no FastAPI.
   `responses.py` is pydantic and nothing else, so the rule behind it
   holds; the docstring now says so. The alternative was an endpoint
   that names its response model as a string for `api.py` to resolve,
   which is a reference nothing checks.
2. **`wrote`, `deleted` and `notice` are filled at M3, not M4.** The
   plan lists them among the CLI's writes facts. Their first consumer is
   the write factory here, and M1's `route` and M2's `missing` are the
   precedent: a fact is filled by the milestone that first has a
   consumer for it. M4 finds them filled and reads them.
3. **`notice` is a string, not a hook.** M1 declared it as a `Hook`.
   When a write of a kind takes effect does not depend on what was
   written: an MCP server and the credentials stored on it are exactly
   what a reload re-reads, and everything else waits for the restart.
   That is one sentence per kind, which is the same rule
   `writes.secret_notice` states for the two kinds that hold secrets. The
   two acts whose notice does depend on what was written (a device
   binding, whose agent may not be loaded) belong to the `Setting` tier
   and compute theirs at the call site as they always have.
4. **Three store-verb facts the plan did not name.** `read`, `write` and
   `delete` are filled by `store.py` with its own per-kind methods,
   unbound. The plan assumed the generic CRUD M2 built would be what the
   API calls, and it is, one method further out: a kind's own method is
   where its identity is normalized, and `read_provider` makes the stage
   canonical before it addresses anything, so a generic read that
   reached past it would answer `providers.LLM.x: no such provider`
   where today it answers `providers.llm.x`. Naming the methods on the
   descriptor also avoids reaching one through a name built out of the
   kind's own, which is a reference nothing checks.
5. **`Setting.endpoints` is removed rather than shaped.** M1 declared
   the group on both tiers. A setting's routes are precisely the ones the
   entity tier's six verbs cannot describe (bind by MAC, bind by
   activation code, unbind, set, clear, each with its own
   argument-shaped body and a notice computed per request), and the plan
   says the non-entity routes stay hand-written. Declaring a group that
   will never be filled would be an invitation to force them into it.
   This is M2's deviation 8 in the API's half.
6. **`writes.wrote_agent_defaults()` is new.** The singleton's
   acknowledgement is the constant `WROTE_AGENT_DEFAULTS`, and the
   descriptor's `wrote` is called the way every other kind's is. The
   function returns the constant, which stays where it was and keeps its
   name, so no sentence is written twice and none changed.
7. **The routes are installed with `add_api_route` rather than the
   method decorators.** `api.get(path, ...)` is a decorator that calls
   `api.add_api_route(path, endpoint, methods=["GET"], ...)` with the
   same defaults, and a factory has the function in hand rather than
   under a decorator. Same call, one layer down.

No other deviation. `RawBody` is unchanged and still on every write
route (it moved up the file, beside the factory that reads it, and
nothing about it changed); `_request_body`, `_resolve_body_schemas`,
`_entity_schemas` and the schema hoisting are untouched; the bearer
gate, the sanitized-errors middleware and `store_dependency` were not
touched at all. `views.provider` and its four siblings, and
`views.mcp_servers` and its two, stay: the CLI's own read path calls
them, which is M4's to look at.

### Verification

From `samtal-server/`, with `PYTHONDONTWRITEBYTECODE=1` outside pytest:

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `2927 passed, 16 skipped in 311.73s`.
  The lane collected 2943 before this milestone and 2943 after: no test
  was added, removed or split, and the skips are the same 16. No
  coherence test was added either, deliberately: what would pin an
  endpoint fact is the OpenAPI drift check, which already refuses every
  byte of every operation, and an equal count is the strongest available
  statement that this milestone was a move.
- `uv run pytest tests/integration -q`: `55 passed in 159.97s`,
  collection unchanged at 55.
- Both generated references regenerate byte-identical, as above, with no
  regeneration commit anywhere on the branch. The other two committed
  references, the conversation store's schema and the event schema,
  regenerate identical as well.
- `git diff --stat` against the milestone's base over `tests/` and
  `docs/reference/` prints nothing at all: no test file in the
  repository changed, and no committed reference did.
