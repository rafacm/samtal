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
8. **The `shown_values` finding is drafted, not filed.** The plan has
   M1 file it as its own issue. The issue body is written below, ready
   to post as it stands; filing it is the maintainer's step, and the
   plan's cross-reference gains its number then.

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

### Follow-up to file

The inventory found one real surface question inside the walker family,
and the no-behavior-change contract is exactly why it must not be
answered here: masking one more value would change what a read prints.
The issue body below is ready to file as its own issue, and the plan's
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
