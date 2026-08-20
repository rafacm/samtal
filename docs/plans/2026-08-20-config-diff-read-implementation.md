# Serve the stored-vs-running configuration diff: implementation

Companion to
[`2026-08-20-config-diff-read.md`](2026-08-20-config-diff-read.md). One
section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the whole comparison, unexposed

### What was done

Five commits. Nothing is exposed: no route, no response model in the
OpenAPI document, no behavior change, and the four committed reference
documents are byte-unchanged, which the drift checks under Verification
prove.

**The comparison.** `vinga-server/src/vinga_server/config/diff.py` is
the new module. It holds the `Applies` token enum (`restart`, `reload`,
`check-in`), the regime map `APPLIES` as data beside the comparison that
reads it, the typed result (`ConfigDiff` and its per-kind frozen
dataclasses), and `config_diff(running, stored, mcp)`, which is pure:
both sides arrive composed, with the secrets loaded beside them, so its
tests build two worlds from the support factories and no case opens a
database.

Providers are addressed as `stage.name` and compared by model equality
plus `SecretStore.fingerprint`; prompt fragments by model equality;
agents and `agent_defaults` by model equality with the `mcp` field
excluded, so a grants-only edit is never claimed pending-restart. The
two live-labeled kinds answer with their label and carry no comparison
at all, which is what `LiveKind` says by having one field.

**The MCP half.** `McpSlice`, which is the object `McpServers._install`
swaps, now carries one opaque comparison identity per configured entry,
referenced or not, computed by `McpSlice.of(config, secrets)` from the
connection identity the reload's `same_as` already uses, the prompt-only
fields that identity leaves out, and the entry's stored-secret
fingerprint, digested. `McpServers.pending_against(config, secrets)` is
the new public read: it composes the candidate into the slice a reload
would install and compares the two as configuration, answering entries
added, removed and changed plus the agents whose effective grants would
move. No manager is built, nothing connects, and a caller learns entry
names and agent names and nothing else.

Grants are compared for every agent of the running generation, through
`Config.mcp_for_agent` on both sides, so an agent the candidate no
longer knows compares as the empty grant set and its pending revocation
stays reported until a reload applies it; an agent only the candidate
knows is not in the grants answer, since it rides the agents' own added
row.

**The generation mark.** `McpServers.generation` is advanced by
`_install` and by nothing else, so a refused reload does not move it.
M2's route captures it either side of its worker-thread load to prove it
composed one world.

**Tests.** `tests/unit/test_config_diff.py` (15 cases) and
`tests/unit/test_mcp_pending.py` (12 cases), both at the public
interfaces only: no new test reaches for an underscore name. The
completeness pin holds both the regime map's keys and `ConfigDiff`'s own
fields equal to `DOMAIN_KEYS`.

### Deviations from the plan

Five, all recorded here because each moved something the plan named.

**1. The comparison identities and the entry-and-grant comparison live
in `tools/mcp/slice.py`, not in `registry.py`.** The plan's module
layout put "the per-entry comparison identities retained at install ...
and the pending-against-stored read" in `registry.py`, and lists
`slice.py` as touched only "as far as the install path needs to carry
the identities". Putting them on the slice turned out to be that path
and more: the slice IS what `_install` assigns, so "swapped atomically
with the world they describe" stops being a promise and becomes
structural; `McpSlice.of` is the one composition both the boot and a
reload already go through, so "one derivation for both sides" falls out
instead of being arranged; the grants comparison reuses
`McpSlice.grants_for`, whose documented empty answer for an unknown
agent is exactly the deleted-agent rule the plan's review finding 5
asked for; and nothing had to be threaded through `_Preparation` and
`_apply` to reach the install. The public read is still
`McpServers.pending_against`, exactly as the plan says, and it is the
only way in: callers learn nothing of connection identity, secret marks
or slice anatomy.

**2. `secrets.provider_identity` was added.** The plan says providers
are addressed as `stage.name`, "the identity the store and every refusal
already use", but that string had exactly one home and it was inside
`SecretLocation.provider`. Spelling it a second time in the diff would
have been the worst kind of wrong: a drifted second spelling asks the
store about an entity nothing ever wrote to, and the empty answer that
comes back looks exactly like nothing stored, so provider secret changes
would silently stop being reported. One function, two callers.

**3. The typed result is frozen dataclasses in `config/diff.py`.**
Explicitly permitted by the milestone brief, since M1 publishes no
schema: M2 maps or moves them into `config/responses.py` when the
schema publishes, complete and exactly once. `McpPending` is declared
there too and imported by the MCP package, which is the direction
`McpReloadResult` and `McpServerStatus` already travel.

**4. The two sides are a `Loaded` protocol rather than a concrete
pair.** `config.boot.BootConfig` is what both sides really are at a
running server, and importing that module would pull the database driver
and the migrations into a module whose whole job is comparing two
configurations, which is the line `config/__init__.py` already holds.
The protocol states the two reads and nothing else; `BootConfig`
satisfies it structurally and is what the tests pass.

**5. Effective grants are compared as the ordered tuple
`mcp_for_agent` answers with, not as a set.** The plan says "effective
set" throughout. Order is observable and reload-applied: the guidance
blocks an agent's activation is given are injected in grant order, so
comparing sets would hide a pending change, which is the false negative
the plan's review finding 2 rejected. The plan's own named case, moving
a grant between `agent_defaults` and the agent without changing what the
agent reaches, reports nothing either way.

### Discoveries

**An entry's comparison identity is the whole entry plus its stored
mark.** `_connection_identity` is the entry with `_PROMPT_ONLY_FIELDS`
cleared, so those two halves together are exactly `McpServerConfig`. The
derivation was kept as the plan's two named halves rather than collapsed
to a dump of the model, because the two names are what say why each half
matters, and because keeping the link means an entry field that becomes
prompt-only later moves in the reload's rule and in this one at the same
moment.

**Most of the MCP cases need no subprocess.** The entries the design
turns on are the ones no agent references, which build no manager at
all, and a reload whose entries are unchanged keeps every connection, so
it starts and stops nothing. Ten of the twelve MCP cases are therefore
pure configuration and the whole file runs in under half a second; the
one whose claim is about a connection standing stands the real stdio
server up.

**Finding 5's rule was already written down.** `McpSlice.grants_for`
answers nothing for an agent it does not know, and its docstring already
says why: a session is holding the agent it was built with, and a reload
can have applied a configuration that agent was deleted from. The
deleted-agent rule needed no new code, only to be read for what it
means on the other side of the comparison.

### No CHANGELOG entry

Deliberately none in this milestone. Nothing operator-visible changed:
there is no route, no schema, no acknowledgement text and no behavior
difference, so an entry here would announce a surface a deployment
cannot reach. It lands with M2's route.

### Verification

Run from `vinga-server/`, at the last commit of the milestone.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files. Its scope
  is the events package, which this milestone does not touch.
- `uv run pytest tests/unit -q`: 2,667 passed, 16 skipped. (Baseline
  before the milestone: 2,640 passed, 16 skipped, counted by collecting
  the suite with this milestone's two files ignored; the 27 new tests
  are those files.)
- `uv run pytest tests/integration -q`: 60 passed.
- The four documentation drift checks, regenerated and diffed against
  `../docs/reference/`: `config reference`, `conversations schema`,
  `events reference` and `config openapi` are all clean, and no file
  under `docs/reference/` appears in this milestone's commits, which is
  the "unexposed" claim's proof.

Not verified here, and not claimed: the container image and the smoke
lane, which no part of this milestone touches.

### PR review round (2026-08-20)

External review of PR #227: codex exec, model gpt-5.6-sol, read-only
against `main...9f1991e5`. Verdict: mergeable after fixes. Findings
condensed but faithful; each carries its resolution and the commit that
made it.

**1 (P1). An entry's identity was taken through an operation that is
not total over the models.** The connection half was asked for as JSON
text, and `command`, `url`, an `args` element and an `env` value are
plain strings, so an unpaired surrogate validates and then has no UTF-8
encoding: `model_dump_json` raises `PydanticSerializationError`. An
identity is taken for every configured entry at every boot, unreferenced
entries included, and that exception is not one the startup path
classifies, so an operator would meet a library traceback out of uvicorn
rather than a refusal naming the entry.

*Resolution.* Adopted (`066544aa`). The digest is built from a
Python-mode dump through one canonical encoder that escapes every
non-ASCII character, so an unpaired surrogate becomes its own escape and
compares as itself. The regression is at the public read: an entry
holding one is built and compared, which is where the old code raised.

**2 (P1). The identity moved with mapping insertion order.** Two loads
of one row can build the same `env` or `headers` pairs in a different
order. The models are equal, which is what the reload's own comparison
says of them when it decides a connection stands, but the digest walked
the mapping as it was built, so the pending read could report a change
nobody made.

*Resolution.* Adopted (`4d80f61e`), folded into finding 1's encoder as
the review suggested: the keys are sorted, which is the rule the stored
secrets' own fingerprint already follows one module over. Reordered
`env` on stdio and reordered `headers` on streamable HTTP each report
unchanged, and the `env` case also asserts that a pair which really
moved is still reported, so it cannot pass by comparing nothing.

**3 (P1). The grants were compared over the wrong population.** The
comparison walked whichever agents the current slice held, and that set
is not the one the question is about: a restart is what loads an agent,
so a reload can install grants for an agent no session can be built for
and can drop an agent this server is still talking as. Both directions
were wrong, and both need a reload to reach. Delete a boot-loaded agent,
reload, write the identical agent back: genuinely pending, and no longer
reported. Add an agent, reload, edit its grants: reported, though
nothing this server can do would change before the restart.

*Resolution.* Adopted (`977d0079`), with one difference from the shape
the finding proposed. The agent names are not passed into `McpServers`
from `build`'s configuration; they are read once in `__init__` off the
slice it was constructed with, which for `build` is exactly
`sorted(config.agents)` and for every other construction is the world
that caller handed over. The property the finding asked for is the same
and is now structural rather than arranged: there is no second parameter
to forget, and `_install` cannot reach the field. `McpSlice.pending_against`
takes the population as an argument, because neither slice holds the
right answer. Both reload-history sequences are pinned at the public
read.

**4 (P2). The plan misclassified `inject_prompts` as prompt-only.**
`transport.py` excludes exactly `instructions` and
`use_server_instructions` from an entry's connection identity, because
editing `inject_prompts` changes what a connect fetches and therefore
restarts the connection.

*Resolution.* Adopted (`533dce9a`). Verified rather than assumed, and
the code needed no change: the derivation reads `_PROMPT_ONLY_FIELDS`
instead of restating a list, so `inject_prompts` was already inside the
connection half. What was missing was anywhere saying so. The plan is
corrected in both places, the derivation's docstring names the
distinction and why it reads the list, and the tests stop covering the
question with one field: the prompt-only case is taken for both
prompt-only fields, each asserting the reload keeps the live connection,
and `inject_prompts` has a case of its own asserting the opposite, that
it is pending just the same and that applying it makes the connection
again.

**5 (P3). `provider_identity` was not yet the single home this document
claimed.** The secret write and delete routes, the CLI's masked-body
index and its summary tree, and the whole-configuration view each still
built `<stage>.<name>` by hand, so the new function was a fifth spelling
rather than the only one.

*Resolution.* Adopted (`012f11c7`). All four call it, and nothing about
the strings changes, which is the point: the byte-pinned suites (the
committed OpenAPI document, the CLI rendering, the whole-config read)
pass unmodified. `store._identified` is deliberately left alone: it
joins the addressing parameters of whichever descriptor it was handed,
and a provider is only the kind that has two of them, so it is a generic
join that happens to agree rather than this spelling written again.

### Verification, after the review round

Run from `vinga-server/`, at `012f11c7`.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files.
- `uv run pytest tests/unit -q`: 2,674 passed, 16 skipped (2,667 at the
  end of M1; the 7 new tests are this round's).
- `uv run pytest tests/integration -q`: 60 passed.
- The four documentation drift checks: all clean, and no file under
  `docs/reference/` is touched by this round, which finding 5's fix had
  to leave true and does.

The image and the smoke lane remain unverified here, for the reason
given above.

## M2: the route, complete

### What was done

Five commits, and the surface is whole: `GET /runtime/config/diff`
answers the comparison milestone 1 built, the schema is published once
with every kind and every list in it, and the committed OpenAPI document
carries the route in the same change.

**The shape.** The result types moved from `config/diff.py` into
`config/responses.py` as pydantic models, and the `applies` token enum
moved with them, so the closed set a client reads out of the document and
the one the regime map is written in are one declaration. `config_diff`
returns the model the route sends, which is the direction `McpReloadResult`
already travels: the module that knows what an answer is made of composes
it, and the handler awaits and answers.

**The route.** `ApiRuntime` gains one optional async callable beside
`mcp_reload` and `agent_prompt`, compared `is not None`, resolved by a
dependency like its siblings, and answered with the prompt read's 503
when it is absent. The route is `async def` for the reason the status
read is, declares 401, 409, 422, 500 and 503, and gives three of them
descriptions of their own, because the shared sentences are about
addressing, about locks, and about reads that can answer emptily, and
none of the three is true here.

**The composition root.** `config_diff_reader` is built where both
worlds are in hand. Its stored side is `reload_domain_config`, the
reload's own re-read, run in a worker thread; its running side is the
boot snapshot with the credentials loaded beside it; and its one-world
rule is the registry's generation mark, taken on the loop before the
read and again after it, with the comparison running only if the mark
held and with no await of its own after the check. Three reads at most,
then the retryable refusal.

**Tests.** The transport cases join `tests/unit/test_config_api_runtime.py`
(the gate, the 503, the whole shape on the wire, each refusal's status,
and the document's own descriptions), the route joins the pinned
inventory in `test_api_openapi.py`, and the composition root's own
behavior is `tests/unit/test_config_diff_read.py`: the two stored-side
refusals against a real database and a real key, the four no-leak
sentinels over both paths and both log formats, the two race cases, and
the wiring through the mount. The integration lane carries the care point
end to end.

### Deviations from the plan

Five, and the first is the one the plan asked to be decided with the code
in front of it.

**1. The 409 is a new refusal, `RunningConfigMovedError`, not
`ReloadInProgressError` reused.** The plan left the choice open and the
code decided it: `ReloadInProgressError` says that a reload was asked for
while one was already running and that the second request changed
nothing, which is a sentence about a second reload. What this read met is
not that. It asked for nothing to be changed, and the reload that moved
the world under it may have finished before the refusal is composed. The
two share a status and share the whole of their advice, which is to ask
again, so the sibling sits in `loader.py` beside it and maps to 409 in
`REFUSAL_STATUS` exactly as the plan said it would either way.

**2. The result types are pydantic models in `config/responses.py`, and
`Applies` went with them.** Milestone 1 recorded this as its own
deviation 3 and left the choice of "map or move" to this milestone. Moved,
because mapping would have been two structures that must agree: a
dataclass field list and a model field list, with a converter between
them and nothing holding the two together. `responses.py` still imports
pydantic and the standard library and nothing else, and `diff.py` imports
it, which is the direction `registry.py` and `reload.py` already import
their result shapes from. Two lines of milestone 1's test moved with the
shapes: the two live kinds are constructed by keyword, and the
completeness pin reads `ConfigDiff.model_fields` rather than
`dataclasses.fields`, which now holds the published shape to
`DOMAIN_KEYS` as well as the map.

**3. No field of a model type carries a description.** The prose the plan
implies for each kind is there, but in the docstring of the model it
points at rather than beside the reference to it. pydantic renders a
described model-typed field as a `$ref` with a sibling key, and the
committed document had exactly zero of those before this change: the
codebase's own note on the subject (`_resolve_body_schemas` in `api.py`)
says a `$ref` with a sibling is at best ignored, and a document that a
TypeScript client is generated from (#210) is not the place to introduce
the pattern. The per-kind facts that had nowhere else to go, the
provider's `stage.name` identity and the MCP half's running baseline,
are in `ConfigDiff`'s own description.

**4. The composition root's builder is public and takes its read as an
argument.** The plan says "the closure builder beside `_mcp_reloader`",
and the two around it are private and do their own reads. This one is
`config_diff_reader(running, servers, read)`, which is the shape
`McpServers.reload(read)` already has and for the same stated reason:
opening a database belongs to the layer that owns one, and what this
function owns is where that read runs and what makes its answer one
world. It is also what makes the rule checkable. The race the plan asks
to be pinned is a reload landing between the stored read and the
composition, and forcing that deterministically means gating the read;
a builder that performed its own read could only be tested by patching a
module global, and a test that reaches for a private name is a design
flag rather than a test problem.

**5. The closure catches nothing.** The milestone brief asked for
sanitized errors built in an except arm and raised after the block. There
is no except arm, deliberately: every failure `reload_domain_config` has
is a `ConfigError` that `REFUSAL_STATUS` already maps, and anything else
is a bug, which the API's last-resort middleware answers as a sanitized
500 while recording the exception's class in one fixed line. Catching it
here to raise a `StorageError` with a sentence of this closure's would
answer the same status with less information: the class recorded would be
the replacement's, not the failure's. The no-leak property is the same
either way and the sentinel suite asserts it over the refusal path that
exists.

### Discoveries

**The committed document had no `$ref` siblings at all.** Checked
mechanically rather than assumed, over the document as it stood before
this change, which is what turned a stylistic question into deviation 3.

**Milestone 1 left nothing for the MCP package to do.**
`McpServers.pending_against` and `McpServers.generation` were exactly the
two reads this milestone needed, and no file under `tools/mcp/` is
touched by it.

**An unreferenced entry makes the race cheap to force.** A reload of
entries no agent grants builds no manager, starts nothing and stops
nothing, and still advances the generation, because the mark counts
installs rather than lifecycle work. Both concurrency cases therefore run
in milliseconds and spawn no processes, which is the same discovery
milestone 1 made about its own MCP cases arriving from the other side.

**The two boundaries are visible in one run.** The integration case
writes providers and an agent, sees them pending, writes two MCP entries,
reloads, and sees the MCP half go quiet while the providers stay pending.
That contrast is the whole reason the labels exist, and it took no
scaffolding beyond the pipeline the file already had.

### Verification

Run from `vinga-server/`, at the last commit of the milestone.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files. Its scope is
  the events package, which this milestone does not touch.
- `uv run pytest tests/unit -q`: 2,682 passed, 16 skipped. (Milestone 1
  left 2,667 passed; the 15 new cases are the nine beside the other
  `/runtime` routes and the six in `test_config_diff_read.py`.)
- `uv run pytest tests/integration -q`: 61 passed, one more than
  milestone 1's 60, which is the diff's own end-to-end case.
- The four documentation drift checks, regenerated and diffed against
  `../docs/reference/`: `config reference`, `conversations schema`,
  `events reference` and `config openapi` are all clean.
  `api-openapi.json` is the only reference document this milestone
  touches, and it is committed with the route that changed it; the other
  three are byte-identical to what milestone 1 left.

Not verified here, and not claimed: the container image and the smoke
lane, which no part of this milestone touches, and the read against a
real device, which it has nothing to do with.
