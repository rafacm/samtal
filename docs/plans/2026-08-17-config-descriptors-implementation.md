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
`cli.py:159-190`, of which four are frozensets naming a body's fields
or a state's vocabulary and the fifth is the reload's ordered outcomes;
ten predicate functions; four renderers; and
`tests/unit/test_config_cli_shapes.py`, whose docstring says it exists
to be deleted wholesale by this issue. This inventory said nine
functions when it was taken, and the count is corrected here rather
than left to be rediscovered: M4 enumerated them on the way out and
there are ten, which is the preamble's own rule that a number moving
under a later milestone is a finding rather than a typo.

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
   contract forbade fixing it in place. Answered since, on
   2026-08-19, together with #176 and under one ratified policy: the
   display fails open, masked, and the masking is one walk that does
   not stop. See
   [the display sweep](../features/2026-08-19-display-fails-open.md).

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

### PR review round

External review of PR #174 (diff main...23db60e) by codex 0.147.0
(model gpt-5.6-sol), 2026-08-17, posted to the PR by the review run
itself. Verdict: mergeable as is, no findings. The byte-identical
references and the empty test diff pre-answered the lenses a
models-move-plus-route-factory touches.

## Milestone 4: the CLI unifies and renders from response models

A command stopped being two implementations of one act. The fourteen
`if args.local:` branches are one dispatcher over a table of rows, and
what a body has to be for the CLI to read it is the model the API
declares it answers with rather than a frozenset of field names kept by
hand beside it.

`cli.py` grew, from 2135 lines to 2288, and the growth is the honest
part of the result: a row states what an act is where the branches
performed it, and the reasons that were spread over twenty function
bodies are written once beside the field that carries them. What got
smaller is the number of places a fact is written. A kind's route, the
arguments that address one entry, the location a fragment refusal
names, the sentence a delete answers with and the notice under it were
each written twice, once per path, and are now read off the descriptor;
the fifteen command functions of the five commanded kinds are three
dict comprehensions; and the module names a kind by hand in two places,
its grammar and the summary tree's sections, which are the two places a
person reads them in.

Five commits, plus the one that records the milestone:

1. `a8600d0` Read an answer as the shape it was promised
2. `852b86d` Say in one row what a command does with an entity
3. `6f7487e` Give the settings, the secrets and the reads rows
4. `629e632` Summarize an entry through its kind's descriptor
5. `533fa5b` Prove each act says one thing on both paths

### The dispatch table's shape

An `Act` is a frozen dataclass with seven fields: the HTTP method, a
path builder taking the parsed arguments, an optional body builder, the
read timeout (the default for everything but the reload), the response
shape the answer is read as with the refusal a body that is not one
meets, the renderer, and an optional local fallback. `_act(args)` is the
only reader of a row: it either calls the fallback or makes the one
request and reads the answer, and hands whatever came back to the
renderer. A subparser carries its row on the namespace (`run=_act,
act=<row>`), which is where `run=<function>` used to point.

The three groups of entity rows are built from the registry rather than
written per kind: `route` and `addressing` give the path, `moved_key`
gives the configuration-document location a fragment refusal names,
`read` and `delete` are the repository's own verbs for the kind,
`deleted` and `notice` are what a local delete answers, and
`has_delete` is why the singleton has a write and a show row and no
delete row. The rows that are not an entity's are written out: the
device and default-agent settings, whose verbs are their own, and the
two secret rows, whose slot is addressed under an entity rather than as
one.

The two single-sided nuances the plan named are row facts. The local
device delete answers `BINDING_NOTICE` plainly, because the sentence
the API answers a device write with depends on whether it has the named
agent loaded and this path has no loaded server to ask. The two secret
rows call `secret_notice(kind)` where the API splits the same choice
across four statically-worded routes.

### The response-validation helper

`_understood(shape, answer, refusal)` is the one place an HTTP answer
becomes something a renderer may print. It runs the answer through
`_declared`, which drops what the shape does not declare, validates
what is left with a `TypeAdapter` in strict mode, and dumps the
validated result back to plain mappings, which is what lets one
renderer take a value from either path and what leaves the extras
behind.

Three properties, each deliberate:

- **Strict.** Nothing is coerced: a body is free to put `true` where a
  size belongs, and a renderer printing the coercion would print
  something nobody sent. This is what the deleted `_is_count` did, and
  strict mode refuses `bool` for `int` for the same reason it named.
- **Extras dropped, not refused.** Every model in `responses.py`
  forbids extras, because the document it generates is a contract about
  what the API sends; this client reads it from the other side, where
  an unknown key means a newer server. `_declared` is guided by the
  shape rather than by a list of names, so an entry nested inside a
  listing is treated exactly like one that arrived alone, which is what
  the status entries inside a reload's answer need.
- **Sanitized.** `ValidationError` is caught without being bound to a
  name, the refusal is built inside the handler and raised after it, so
  the fixed sentence leaves with `__cause__` and `__context__` both
  empty. `ValidationError.errors()` retains the input it rejected, and
  on this surface that can be a pasted credential.

Deleted with it: the four predicate frozensets (`PENDING_FIELDS`,
`STATUS_FIELDS`, `STATUS_STATES`, the state vocabulary among them, and
`PROMPT_BLOCK_FIELDS`) and ten predicate functions (`_envelope`,
`_document`, `_pending_entries`, `_status_entries`, `_is_status_entry`,
`_is_name_list`, `_assembled_prompt`, `_is_prompt_block`, `_is_count`,
`_reload_outcome`), plus `_mapping` and `_wrote`, which were shape
checks by another name. `PENDING_COLUMNS` stays: its members are
headings a person reads. `_names` and `_sequence` stay too, because
they are rendering rules (bound it, make it printable, treat a null
grant as a list of nothing) rather than claims about the shape.

### The `RELOAD_OUTCOMES` choice, and its evidence

The plan has M4 delete five predicate constants. Four of them, the
frozensets above, are gone. `RELOAD_OUTCOMES` is kept, derived, and
re-exported, because deleting it would have modified the contract
file:

```
$ grep -rn 'RELOAD_OUTCOMES' tests/
tests/unit/test_config_cli.py:929:        dict.fromkeys(cli.RELOAD_OUTCOMES, [])
tests/unit/test_config_cli.py:941:            {outcome: [] for outcome in cli.RELOAD_OUTCOMES}, id="servers-missing"
tests/unit/test_config_cli.py:990:    empty = dict.fromkeys(cli.RELOAD_OUTCOMES, []) | {"servers": {}}
```

All three are in `tests/unit/test_config_cli.py`, which the issue's
contract forbids modifying, and all three read `cli.RELOAD_OUTCOMES` as
a name on the module: the acceptance suite builds a reload answer out
of it. So it lives in `responses.py` now, beside the result it
describes, read off that model's own list-of-names fields:

```python
RELOAD_OUTCOMES = tuple(
    name
    for name, field in McpReloadResult.model_fields.items()
    if get_origin(field.annotation) is list and get_args(field.annotation) == (str,)
)
```

That is the relation the deleted shape test asserted, built in rather
than pinned, and `cli.py` imports it, so `cli.RELOAD_OUTCOMES` resolves
exactly as before and the contract file is byte-unchanged. It is not a
deprecated alias in the end: it is presentation, which is why it is an
ordered tuple and not a set, and the renderer still reads it for the
order the four lines are printed in. `servers` is the only field the
rule leaves out, which is correct, since it is the status mapping
carried beside the outcomes rather than an outcome.

### What the models made stricter, and why that is the point

Three acceptance rules narrowed, all of them where the CLI was
tolerating less than the API promises. They are recorded rather than
avoided, because "the CLI renders from the same models the API answers
with" is the decision, and a hand-kept subset of a model is the second
encoding the issue exists to delete.

1. **A pending listing must carry the whole `PendingDevice`.** The old
   check required the four fields the columns render and ignored the
   types. `client_id`, `first_seen` and `last_seen` are now required
   too, and every field has to be a string. The API declares
   `dict[str, PendingDevice]` on that route, so an answer missing one
   is not this API's.
2. **A write acknowledgement must carry its notice.** `_wrote` used to
   print the restart sentence when a body carried `wrote` and no
   readable `notice`. `Acknowledgement` declares both, every write
   route answers with it, and a body carrying one and not the other now
   meets the same refusal a body carrying neither always did.
3. **A reload answer's status half is read as part of the reload's own
   shape.** An unreadable entry under `servers` used to reach the
   status listing and meet its sentence; it now meets the reload's,
   which is the act that was run. Both sentences are byte for byte what
   they were, and the acceptance suite's `servers-invalid` case asserts
   the fixed `UNRECOGNIZED_ANSWER` both carry.

Nothing loosened. Extras are tolerated exactly where they were, which
`_is_status_entry`'s docstring was the only place to say out loud.

### Deviations from the plan

1. **`RELOAD_OUTCOMES` survives, derived.** As above, with its
   evidence. The plan lists five constants to delete; deleting the
   fifth would have edited the contract file.
2. **The four listings read their own answer; the other rows carry the
   shape.** The plan has the CLI render every HTTP answer through the
   helper, which it does, but not all from the same place.
   `_status_listing` and `_prompt_listing` are handed a body directly by
   the acceptance suite (`test_config_cli.py:687` and `:863`, which
   assert the refusal carries nothing of the body), so the reading is
   theirs; the pending and reload listings keep the same shape as their
   neighbours. Every other act's row names the shape and the dispatcher
   reads it, which is what the show family needs, since its local
   fallback produces the value directly and is not validated, exactly
   as before.
3. **`summary` is filled here, and the `Setting` hooks are removed.**
   M1 named `summary` for this milestone, so the summary tree asks a
   kind how one of its entries reads after its name (` (anthropic)`,
   ` (12 characters)`, `: tts=voice`). The `Setting` tier's four
   never-filled hooks (`summary`, `wrote`, `deleted`, `notice`) are
   removed instead, on M3's reasoning for removing `Setting.endpoints`:
   a binding's line is the agents a MAC points at, its acknowledgement
   and notice are computed per request because the notice depends on
   whether the server has the named agent loaded, and declaring a group
   nothing will fill is an invitation to force a shape.
4. **Five commands are not rows.** `ota-url`, `doctor`, `schema`,
   `reference` and `openapi` reach no API: two are about onboarding a
   board before there is anything to configure, and three render the
   models and the routes with no database, server or key. They keep
   their own functions under a section header that says so.
5. **`_report` is now `_acknowledged` and takes the acknowledgement.**
   The printer used to take a sentence and a notice with the restart
   sentence as its default, which is a choice made at a call site. It
   takes the acknowledgement both paths produce, and the default is
   gone: every row says which notice its act carries.

No other deviation. `LOCAL_NOTICE`, `LOCAL_SUBSET`, the `--local` gate
in `main` and the per-subparser `local_ok` wiring are untouched, the
grammar is unchanged, and `writes.py` was not edited at all.

### The renderer golden, and how byte-identity was checked

Every renderer was captured before the milestone began and diffed after
each commit: the summary tree and the whole-configuration document (a
populated one and an empty one), one entity with stored secrets and one
without, the pending listing full and empty, the status block with a
down entry, an escape sequence in a name, a partial grant and an unused
entry, the assembled prompt with a published prompt's name and control
characters in a block, the reload full and empty, and the seven
refusals with their `__cause__` and `__context__`. The diff is empty at
every commit, which covers the paths the acceptance suite exercises
with substring assertions rather than whole outputs.

The per-act proofs were checked against deliberate divergences rather
than assumed to bite: swapping the local device delete's notice for the
restart sentence fails exactly one case, and showing a provider through
another kind's view fails two.

### Verification

From `samtal-server/`, with `PYTHONDONTWRITEBYTECODE=1` outside pytest:

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `2938 passed, 16 skipped in 312.07s`.
  The lane collected 2943 before this milestone and 2954 after: minus
  the six tests of `test_config_cli_shapes.py`, which this milestone
  deletes wholesale as its docstring said it would, plus the seventeen
  in `tests/unit/test_config_cli_local.py` (2943 - 6 + 17 = 2954). The
  skips are the same 16.
- `uv run pytest tests/integration -q`: `55 passed in 159.70s`,
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

- `git diff --stat` against the milestone's base over `tests/` shows
  the deletion and the new file and nothing else:

```
 samtal-server/tests/unit/test_config_cli_local.py  | 249 +++++++++++++++++++++
 samtal-server/tests/unit/test_config_cli_shapes.py |  92 --------
```

  `test_config_cli.py` and both lanes of `test_config_examples.py` are
  byte-unchanged, and `git diff --stat` over `docs/reference/` prints
  nothing.
- The grep proof that no name of the deleted family survives in the
  source: `grep -rn 'PENDING_FIELDS\|STATUS_FIELDS\|STATUS_STATES\|PROMPT_BLOCK_FIELDS'`
  over `samtal-server/` and `docs/` matches only `docs/plans/`, which
  is history: this plan's own verification clause, and the plan and
  implementation doc of #144, which built the bridge for this milestone
  to delete.

### PR review round

External review of PR #175 by codex (model gpt-5.6-sol), 2026-08-17.
Two findings, both accepted, each fixed in its own commit:

1. **P2: the loader's command map is still handwritten.** The plan's
   command-strings section assigns `loader.py` to this milestone: the
   descriptor's `command` becomes the one source for the two code
   encodings of a command string, and the milestone's brief omitted it,
   so `MOVED_KEY_COMMANDS` still held seven strings byte-identical to
   the seven the reference prints. `test_config_entities.py` said out
   loud that the duplication should survive only until M4.

   *Resolution*: fixed in `add8368`. `MOVED_KEY_COMMANDS` is a dict
   comprehension over `entities.ENTITIES` keyed by `moved_key`, merged
   with one over `entities.SETTINGS` keyed by `name`, which are the two
   halves of `DOMAIN_KEYS`. The `Setting` tier already carried
   `command` from M1, so nothing had to be filled. `loader.py` now
   imports `entities`, which imports the models and `responses.py` and
   nothing else, so no cycle and no new dependency: `entities` sits
   below `loader` exactly as the models do. The map is byte for byte
   what it was, checked field by field against the handwritten literal
   before the commit, and both refusal builders are untouched.

   What happened to the coherence test: it would have become a
   comparison of the derivation against itself, so it moved down a
   level rather than staying vacuous.
   `test_the_registry_carries_the_loaders_moved_key_commands` is now
   `test_the_loader_quotes_each_kinds_command_in_full`, which writes a
   file carrying each moved section in turn, loads it through
   `load_file_config`, and asserts the whole command string appears in
   the refusal under `write it with: `. That is the claim the table
   comparison was making, stated where it cannot be tautological: what
   the derivation has to preserve is the sentence an operator reads.
   `test_config.py`'s `set(MOVED_KEY_COMMANDS) == set(DOMAIN_KEYS)` is
   the other half and needed no change, since a descriptor with a wrong
   `moved_key` fails it. The new test was checked against a deliberate
   divergence: truncating the quoted command to twenty characters in
   `_check_moved_keys` fails it. The collected count is unchanged at
   2954, one test replacing one test.

2. **P3: the predicate-removal counts disagree.** The changelog said
   five frozensets plus a state vocabulary, which counts
   `STATUS_STATES` twice and one constant too many, and the
   implementation doc said nine predicate functions in the inherited
   inventory while enumerating ten in the M4 section.

   *Resolution*: fixed in `fdd2626`. Both documents now say four
   predicate frozensets and ten predicate functions deleted, with
   `RELOAD_OUTCOMES` named as the fifth constant that stayed, derived.
   The inventory's nine is corrected where it is written, under that
   section's own rule that a number moving under a later milestone is a
   finding rather than a typo. The 2026-08-16 changelog entry
   describing #144's pins carries the same loose "five frozensets" and
   was left alone: it is a dated record of what was written then.

Re-run after both commits, from `samtal-server/`:

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `2938 passed, 16 skipped in 305.63s`,
  collection unchanged at 2954.
- Both generated references regenerate byte-identical, which is the
  proof that matters for the first finding: the reference prints the
  same `command` the loader now quotes, and it did not move.

## Milestone 5: the test split and the cost demonstration

The acceptance file stopped being one file because the module it drives
stopped being one module. `tests/unit/test_config_cli.py` was 2,305
lines and 101 tests, and it mirrored `cli.py` exactly: everything the
command group did, in one place, because everything the command group
did was in one place. The four milestones before this one produced the
boundaries, and #144's plan had already decided which buckets to cut
along, each anchored to a concern this issue's body names. This
milestone cuts them.

Six commits, plus the one that records the milestone:

1. `5632010` Give the config CLI suites one runner
2. `42eccc6` Move the client's own behavior to its own file
3. `2d1a11c` Move the four rendered answers to their own file
4. `41a75e5` Move a credential's whole life to its own file
5. `ca236aa` Move the grammar and its exit codes to their own file
6. `c8dfb3f` Move the break-glass path into the file M4 started

### The split

| Bucket, as #144 named it | File | Tests | Cases |
| --- | --- | --- | --- |
| The acceptance spine: the empty-database-to-working-configuration walk and per-kind write, show, list and delete | `test_config_cli.py` | 29 | 47 |
| Transport and client: URL, token and TLS resolution, the refusals of an unreadable or credentialed URL, the timeouts, an unreachable server | `test_config_cli_transport.py` | 19 | 23 |
| Rendering: the status, reload, pending and prompt answers | `test_config_cli_rendering.py` | 23 | 42 |
| Secrets: entry, masking, refusals, key failures | `test_config_cli_secrets.py` | 13 | 13 |
| Parser grammar, help and exit codes | `test_config_cli_grammar.py` | 5 | 5 |
| The `--local` recovery subset | `test_config_cli_local.py` | 12 | 20 |
| | **total** | **101** | **150** |

The counts are equal by construction rather than by luck: 101 test
functions in, 101 out, and the 150 cases they parametrize to are the 150
the file collected before. `test_config_cli_local.py` holds three more
tests and seventeen more cases than the table's row, which are M4's own
and are not part of this move; the unit lane's total is unchanged at
2,954.

The three judgment calls the bucket list does not settle, recorded
because a reader will wonder:

- **The two help-text assertions are grammar, not rendering.** One names
  every state a `status` can print and one names which of the two ways
  to bind a board takes a MAC; both read `cli._parser().format_help()`,
  and the help is part of the grammar rather than a rendering of an
  answer.
- **`add-device` is spine, the pending listing is rendering.** Claiming
  a board writes a device row, which is per-kind write behavior; listing
  what is waiting renders an answer from the running server. They share
  the helper that puts a device in the pending table, which is why that
  helper is in support.
- **A database directory that cannot be opened is spine; a config file
  that is not there is transport.** The first is the reading commands'
  own reporting rule, beside the row that cannot be read; the second is
  the resolution that decides where a command is addressed, which is
  what the transport file is about.

### The support module, and why it exists

`tests/support/config_cli.py` (new, 166 lines) holds what six suites
need before they can run anything: `runner`, which is the 80-line
fixture body the acceptance file had, plus the sentinels, the fragment
constants and the three helpers (`chain`, `document`, `showing`) that
more than one bucket reads.

It is a factory rather than a fixture for two reasons. A fixture cannot
be imported and used as a fixture, which is why #144's decision 2 says
"support or a conftest"; and a `run` fixture in `tests/unit/conftest.py`
would be a name visible to every module in the lane, most of which have
nothing to do with the CLI. Each suite spends four lines wrapping the
factory instead, and the six wrappers are byte-identical to each other.

The three helpers are imported under their original private spellings
(`from tests.support.config_cli import document as _document`), which is
the convention `tests/support/stores.py` already set, and which is what
kept every moved test body byte-identical rather than renaming a call
inside an assertion. `tests/unit/test_support_boundaries.py` stays
green: no test module imports another, and nothing under support imports
a test module.

### One file or two for `--local`, decided here

M4 wrote `test_config_cli_local.py` for the per-act proof that a local
run prints what the API prints. The bucket #144 named is the `--local`
recovery subset, which was still in the acceptance file. They are the
same concern from two sides, so this milestone merges them: two files
called `test_config_cli_local.py` and `test_config_cli_recovery.py`
would be a distinction nobody could hold, and they would have carried
two copies of the preamble constant, two `run` fixtures, two sets of
seed helpers and two nine-row tables of the same nine acts.

The merge is where this milestone is not a pure move, and the deviation
is recorded below rather than smoothed over.

### The port table

Empty in the sense M2 and M3 meant it: no test was ported, rewritten,
weakened or strengthened. What changed is stated exactly by the
comparison below.

### How byte-identity was checked

The M2-era method from #144, applied to this shape: every top-level
`def` and every module-level assignment in the pre-split file and in the
six files it became was parsed out with `ast.get_source_segment` and
paired by name. The result:

```
identical: 122
changed:   ['run (test_config_cli_local.py)', '_a_provider (test_config_cli_local.py)',
            '_a_prompt_fragment (test_config_cli_local.py)']
gone:      ['SECRET', 'OTHER_SECRET', 'API_SECRET_ENV', 'TOKEN', '_chain', '_document',
            '_showing', 'FRAGMENT_TEXT', 'FRAGMENT_INPUT', 'LOCAL_MUTATIONS']
new:       ['MUTATIONS (test_config_cli_local.py)']
```

Every name in `changed` and `gone` is a fixture, a helper or a constant.
Not one `test_` function appears in either, which is the claim: all 101
are byte for byte what they were. The `gone` names are the nine that
moved to support and `LOCAL_MUTATIONS`, which the merge renamed. A
second pass compared the support module against the originals: the
factory's body is the old fixture's body line for line, the three
helpers' bodies are unchanged under their new names, and all six
constants are identical.

### Deviations from the plan

1. **The `--local` bucket merged into M4's file rather than getting one
   of its own.** As above. The plan says each bucket gets its file; the
   recovery subset's file already existed.
2. **Two seed helpers were reconciled instead of moved.** The two files
   had seven helpers under the same names, five byte-identical and two
   not: the acceptance file's `_a_provider` writes `type` and `model`
   where M4's also writes `api_key_env`, and its `_a_prompt_fragment`
   writes the elaborate `FRAGMENT_INPUT` where M4's writes one short
   line. One module can hold one of each, and the survivor was chosen
   per helper by what the file's own tests compare: `_a_provider`
   keeps M4's `api_key_env` variant, because M4's reads compare the
   shadowing note the environment reference produces, while
   `_a_prompt_fragment` keeps M4's short line, because no test in the
   file reads the fragment's text back and the elaborate
   `FRAGMENT_INPUT` belongs to the acceptance spine's round-trip
   tests, which kept it. The moved acts that use these seeds are
   deletes and secret writes whose compared sentence mentions neither
   the reference nor the fragment's text. The moved test's own body is unchanged; what
   changed is which seed its parametrize table names.
3. **The two nine-row tables became one.** They listed the same nine
   acts, the acceptance file's carrying a third column saying whether a
   running server applies the act by reloading. The merged `MUTATIONS`
   carries the column, the moved proof reads it, and M4's proof
   parametrizes over the pairs derived from it. Both tests' ids are the
   strings they always were.
4. **A support module was added, which the plan's file list did not
   name.** It names the six bucket files and this doc; the scaffolding
   had to go somewhere, and #144's rule says where.
5. **The acceptance file's docstring was rewritten.** It described "the
   acceptance suite for the whole write path", which after the split is
   five other files' description too. It now says what the spine keeps
   and names the five neighbours, which is what a reader landing in one
   of them needs.

No other deviation. Both committed references are untouched, and no
production file was edited by this milestone at all.

### The cost demonstration

The issue's first acceptance criterion: adding a new field to an entity
touches the model, the schema and its migration, and at most the
descriptor. Measured rather than argued, by adding a scratch field
(`note: str | None = None`) to `PromptFragmentConfig`, a nullable `note`
column to the `prompt_fragments` table and a migration adding it, then
running the surfaces and the whole unit lane against it and reverting.

**What the descriptor world required.** Three files, all three of them
the ones the criterion admits:

| File | What it needed |
| --- | --- |
| `samtal_server/config/models.py` | The field, with its description. |
| `samtal_server/db/schema.py` | The column. |
| `samtal_server/db/migrations/versions/0005_*.py` | The migration adding it. |

Nothing else was edited, and the descriptor was not touched at all:
`entities.py` is byte-unchanged in the experiment. What worked without
being asked:

- **The write.** `config set prompt-fragment household -f -` with `note`
  in the body was accepted, answered with its usual acknowledgement and
  notice, and the value reached the column: the row read back
  `('household', 'The bins go out.', 'a scratch note')`. That is the
  default `model_validate`/`model_dump` row path M2 built, which this
  kind takes.
- **The API.** No route, request model or response model was touched.
  The write route carries the kind's own model, so the new field is part
  of the body it accepts by construction.
- **Both generated references.** `docs/reference/domain-config.md` gained
  one row in the fragment's field table, and
  `docs/reference/api-openapi.json` gained one property in the fragment
  schema, both derived from the model and both produced by the
  regeneration command rather than by an editor.
- **Everything else the suite pins.** The whole unit lane was run against
  the scratch field: `2 failed, 2936 passed, 16 skipped`, and the two
  failures are exactly the two drift pins that compare the committed
  references against the freshly generated ones. Nothing else asked for
  a hand edit: not `config.example.yaml`, not the example fragment file
  under `examples/`, not the docgen bijection tests, not the CLI.

**What the pre-descriptor world required.** Read off the branch's base
commit, `a1d5dd2`, where the same field would have needed five files:
the same three, plus

| File | Why | Anchor at a1d5dd2 |
| --- | --- | --- |
| `samtal_server/config/store.py` | Two hand-written mappings naming the field list: the write's inline `{"text": entry.text}` and the reader's `{"text": row.text}` | `store.py:469` and `store.py:938` |
| `samtal_server/config/views.py` | The body builder, key by key | `views.py:294` |

Both store sites are gone: M2 replaced them with the default row path,
which is why a fragment's row costs nothing outside its model now. The
entity-level baseline this doc's preamble records, 13 hand-edited files
to add `prompt_fragments`, is the number for a whole new kind rather
than for a field; it is not what this experiment measured and is not
claimed as measured here.

**Verdict, and the surface the demonstration surfaced.** The criterion
holds for the store, the API, the CLI and both generated references: a
field is its model, its column and its migration, and the descriptor
does not have to hear about it. It does not hold for the read view. The
scratch field never appeared in `config show prompt-fragment household`
or in the whole-configuration document, because
`views.prompt_fragment_body` returns `{"text": entry.text}` key by key,
as all four of its siblings do for their kinds. No test failed for it,
which is part of the finding.

That is a fourth file (`views.py`, one line) whenever the new field is
meant to be displayed, and it is not obviously a defect: it is exactly
the rule `views.provider_record`'s docstring states and defends, that a
new model field is absent from a record until somebody decides it
belongs. The difference is that `provider_record` says so and the five
body builders do not, so on the display path the same behavior reads as
an oversight rather than as a decision. Recorded as a finding and not
widened into: making a body builder derive from the model would change
what a read prints the moment a model gains a field, which is a
behavior change this issue's contract forbids, and deciding whether the
display path should fail open or closed is now issue #176, filed
from this walkthrough, the same question family as issue #171
already holds open for the masking path beside it.

**Answered since.** Both were decided together on 2026-08-19: the
display fails open, masked. The five builders derive from the model, so
the fourth file this walkthrough counted is no longer one, and the
masking is a single walk applied at every depth. `provider_record` is
untouched and still fails closed, which is now stated as the split it
is rather than as a local preference. See
[the display sweep](../features/2026-08-19-display-fails-open.md).

### Verification

From `samtal-server/`, with `PYTHONDONTWRITEBYTECODE=1` outside pytest:

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `2938 passed, 16 skipped`. The lane
  collected 2954 before this milestone and 2954 after: the split adds
  and removes nothing, and the skips are the same 16.
- `uv run pytest tests/integration -q`: `55 passed`, collection
  unchanged at 55.
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

  Nothing in this milestone touches a production module, so the two
  references could not have moved; they were regenerated anyway, because
  a proof that was not run is not a proof.
- `tests/unit/test_support_boundaries.py` passes: no test module imports
  another, and nothing under `tests/support` imports a test module.
- `git diff --stat` against the milestone's base over `samtal_server/`
  and `docs/reference/` prints nothing at all: no production file and no
  committed reference changed.
- The normalized-comparison proof above, which is the strong form of
  "the moved tests are unchanged".

### PR review round

External review of PR #177 (diff main...2e9d5ed) by codex 0.147.0
(model gpt-5.6-sol), 2026-08-17, posted to the PR by the review run
itself. One finding:

1. **P3: the seed-helper reconciliation record did not match the
   surviving helper.** The deviation entry said the richer pair
   won, but `_a_prompt_fragment` in the file keeps M4's one-line
   fragment.

   *Resolution*: accepted; the record was wrong, not the code. The
   entry now states the per-helper choice as it was actually made:
   the provider helper kept the richer `api_key_env` variant
   because the file's reads compare what it produces, and the
   fragment helper kept the short line because nothing in the file
   reads the text back, the elaborate input staying with the
   acceptance spine that does.

Verdict as posted: mergeable after the listed fix.
