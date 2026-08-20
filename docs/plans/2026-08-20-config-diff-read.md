# Serve the stored-vs-running configuration diff as a runtime read

## Goal

Implement issue #193 scoped to the API read alone: one `GET` under the
existing `/runtime` namespace answering "what have I changed that is
not yet in effect", as a per-entity summary of the domain half the
server booted with versus what the database holds now. The CLI `diff`
subcommand deliberately does not land here: its seat is reserved by
the CLI rebuild (#194), which is postponed behind #223, and the
issue's own relations section blesses landing the read alone with the
subcommand to follow.

The companion implementation doc,
[`2026-08-20-config-diff-read-implementation.md`](2026-08-20-config-diff-read-implementation.md),
records what each milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by issue #193 and not re-litigated here:

1. **One read under `/runtime`**, because a running server currently
   has no read that answers the question, the only trace of a pending
   change is the transient `notice` in a write's acknowledgement, and
   the admin UI (#129) needs the read on day one to render any honest
   "pending restart" indicator.
2. **The booted side comes from the snapshot the process already
   holds; the stored side is the same re-read the MCP reload uses.**
   Nothing new touches the database layer.
3. **The live exceptions are labeled in effect or excluded**: device
   bindings, the default agent, and the MCP half must never cause the
   diff to claim a pending change that is not pending.
4. **Secrets are reported by slot presence only, never by value.**
5. **The shape of the answer is a per-entity summary**: added,
   removed, changed.
6. **This read is #191's companion surface, not its dependent.** It
   is useful under today's restart semantics; once apply-without-
   restart lands, #191 makes it report against the current generation
   instead of the boot. That migration is #191's work, not this
   plan's.

## The issue's open questions, resolved

**Exclude the live entities, or label them?** Label. The admin UI
wants one read that covers the whole domain configuration, and an
excluded kind forces every consumer to hard-code the knowledge of
why it is missing; a label is one token from a closed set and keeps
the knowledge in the server. Device bindings and the default agent
answer with the label alone and no diff lists: they are read per
check-in, so the store is authoritative within seconds of a write
and a computed diff would assert a lag that does not meaningfully
exist.

**How is the MCP half kept honest?** It is the one kind whose
running world can differ from the boot: `POST
/runtime/mcp-servers/reload` swaps the registry's configuration
generation while the process runs. Diffing MCP entries against the
boot snapshot would therefore claim pending changes a reload already
applied, which is exactly what the issue forbids. The honest
baseline for the MCP half is the registry's current generation, and
reading that requires a small new read on `McpServers`, so the work
is split: milestone 1 labels the kind as reload-governed and claims
nothing pending for it (allowed by the issue: excluded or labeled),
and milestone 2 adds the real added/removed/changed answer computed
against the current generation. Under-reporting between the
milestones is the safe direction to be wrong in; over-claiming is
the direction the issue names as the failure.

**Where does the route live?** `GET /runtime/config/diff`. The
`/runtime` namespace exists precisely because it can never collide
with an entity name, and the two-segment path leaves
`/runtime/config/...` as the natural home for #191's generalized
reload verb, so the operator loop's read and its future apply sit
side by side. The alternatives (`/runtime/config-diff`,
`/runtime/pending`) name the same thing less compositionally.

**What is compared?** Typed models and secret fingerprints, never
rendered documents. Both sides hold the same entity models (the
boot `Config`'s domain fields and the stored `Snapshot.domain` are
validated through one set of models, which is the store's own
design), so "changed" is model inequality; pydantic equality is
exact and needs no masking because nothing rendered ever enters the
comparison. Stored secrets are compared through
`SecretStore.fingerprint(kind, identity)`, the public opaque mark
that exists for exactly this question and can be asked without a
key. The response carries entity names and closed-set tokens only:
no entity bodies, no values, no masks, so the leak surface is
structurally empty rather than carefully filtered.

A consequence to document rather than fight: a Fernet token carries
a timestamp and a fresh IV, so re-setting a slot to the same
plaintext still changes the fingerprint, and the diff reports the
entity as changed. That is the store's own documented posture
("rebuilding is the safe direction to be wrong in") and it matches
the MCP reload, whose `same_as` treats a rewritten secret as a
change for the same reason. "Changed" therefore means "written
since boot", which is the operationally useful sentence anyway.

**How is the agent kind kept honest?** An agent entry spans two
regimes: its `mcp` grants are applied by the MCP reload (the
registry's slice derives grants from the whole candidate
configuration), while everything else about it waits for a restart.
Milestone 1 therefore compares agents and `agent_defaults` with the
`mcp` field excluded, so a grants-only edit that a reload already
applied is never claimed pending-restart. Milestone 2 adds the
grant comparison under the reload label, computed against the
current slice for the agents both worlds hold: grants of an agent
only one side knows ride that agent's own added or removed row.

**Sync or async handler?** Async, from milestone 1. The existing
runtime status read is `async def` deliberately, to read the MCP
managers on the loop that owns them; milestone 2 needs the same
guarantee for the diff (a thread-pool read could see a reload's
world half-installed). Landing the route async from the start means
milestone 2 changes what the handler composes, never its execution
model. The stored side loads through `asyncio.to_thread` around the
same `ConfigStore.load` every configuration read uses, and the
composition of the answer runs after that await with no await of
its own, the `reload_result` one-world rule applied to a read.

## Design decisions this plan makes

**A new module owns the comparison: `config/diff.py`.** The one
sentence its callers stop having to know: which configuration kind
converges at which boundary, and how equality between two
configuration worlds is judged. It exports one function taking the
running side (the boot `Config` and its `SecretStore`) and the
stored side (a `Snapshot`) and returning the typed response model.
The regime map (which kind carries which `applies` token) lives
here as data, beside the comparison that uses it. Deletion test:
inlined into `app.py` the composition root would own comparison
rules; inlined into `api.py` it would break that module's standing
contract that the API never learns what configuration means. Both
callers get harder to read, so the module stands.

**The API learns nothing; the composition root wires a closure.**
`ApiRuntime` gains one optional field beside `mcp_reload` and
`agent_prompt`: a callable from a stored `Snapshot` to the diff
response, `None` for an application built without a server around
it, answered by the route with the honest 503 the prompt read
already answers. `app.py` builds the closure beside `_mcp_reloader`
and `_prompt_preview`, where the boot configuration and its secrets
are already in hand; the route reads the stored snapshot through
its own store dependency and hands it over. The seam is compared
`is not None`, never by truthiness.

**The response is typed in `config/responses.py`,** the way
`McpReloadResult` is, and kind-keyed the way the whole-config
document is, so a consumer walks the same field names it already
knows from `GET /config`:

    {
      "providers":        {"applies": "restart", "added": ["llm.local"],
                           "removed": [], "changed": []},
      "prompt_fragments": {"applies": "restart", "added": [], "removed": [],
                           "changed": []},
      "agents":           {"applies": "restart", "added": [], "removed": [],
                           "changed": []},
      "agent_defaults":   {"applies": "restart", "changed": false},
      "mcp_servers":      {"applies": "reload"},
      "devices":          {"applies": "check-in"},
      "default_agent":    {"applies": "check-in"}
    }

Providers are addressed as `stage.name`, the identity the store and
every refusal already use. `agent_defaults` is the singleton, so it
answers with a boolean rather than name lists. The two live-labeled
settings answer with the label alone and carry no comparison: what
is stored for either is already served by the entity reads and is
in effect by the next check-in, so a `changed` flag there would
dress a non-pending fact as a diff. `applies` values come from a
closed `StrEnum` declared with the models: `restart`, `reload`,
`check-in`. Milestone 2 extends `mcp_servers` with the
added/removed/changed lists and gives `agents` a nested
reload-labeled `grants` entry listing agents whose effective grants
differ from the current generation.

**Completeness is pinned, not remembered.** The regime map must
cover the domain, and a domain kind added next year must not
silently fall out of the diff: a unit test asserts the map's keys
equal `DOMAIN_KEYS` exactly, so the sixth kind arrives with a
failing test naming this module. This is the two-structures rule
applied to the plan's own new data.

**Milestone 2 deepens `McpServers` instead of exporting its
anatomy.** The new read answers "which stored entries differ from
the world this registry is running", taking the stored candidate
(entries and secrets) and answering with names in the diff's
vocabulary. It is the reload's prepare-phase question asked without
doing anything: presence from the current slice's entries,
connection identity through the same `_connection_identity` the
reload's `same_as` uses, stored secrets through the same
fingerprint, prompt-only fields (instructions,
`use_server_instructions`) through the slice, and no manager is
ever built. The alternative, exposing the current generation's
entry configurations for `config/diff.py` to compare, would teach
the diff module connection identity and secret marks, two facts
that are the MCP package's to know. Grant derivation for the stored
side reuses the one derivation that exists (`mcp_for_agent`'s
defaults-then-own rule) rather than restating it; the milestone
either calls it through a composed view of the stored domain or
factors the rule to a function both `Config` and the diff path
call, whichever the diff in front of the implementer keeps
smaller.

## Module layout

- `config/diff.py` (new): the comparison and the regime map.
- `config/responses.py`: `ConfigDiff` and its per-kind models, the
  `applies` token enum.
- `config/api.py`: the `ApiRuntime` field, the dependency resolver,
  the `GET /runtime/config/diff` route in `_runtime`, and the
  `API_DESCRIPTION` paragraph that rewrites the "boot-time
  snapshot" story to mention the read.
- `app.py`: the closure builder beside `_mcp_reloader`.
- `tools/mcp/registry.py` (M2): the pending-against-stored read;
  `slice.py` and `manager.py` only if factoring an existing private
  helper for it is smaller than calling through the registry.
- Docs: `docs/reference/api-openapi.json` regenerated (drift
  checked); `vinga-server/README.md`'s `/runtime` route block;
  `CHANGELOG.md`. `docs/reference/domain-config.md` is untouched
  (no schema change), and no write acknowledgement changes, so no
  notice text moves.

## Milestones

Every merge leaves `main` releasable: the image publishes on every
push, so each milestone ends with lint, both suites, and the doc
drift checks green.

- [ ] **M1: the diff read, honest about what it cannot yet see.**
  `config/diff.py` with the regime map and the model-plus-
  fingerprint comparison for the restart-bound kinds (providers,
  prompt fragments, agents and `agent_defaults` with `mcp`
  excluded); devices and `default_agent` labeled `check-in` with no
  lists; `mcp_servers` labeled `reload` with no lists; the typed
  response models; the `ApiRuntime` field and 503; the async route;
  the OpenAPI regeneration with the route inventory pin; the README
  block and CHANGELOG entry. Design footprint: adds the one seam
  (a `Snapshot`-to-diff callable) and the one module that knows
  convergence boundaries; deepens the composition root by one
  closure built where the boot world is already in hand. Branch
  `feature/config-diff`, PR TBD.
- [ ] **M2: the MCP half against the current generation.**
  The `McpServers` read described above; `config/diff.py` composes
  it into `mcp_servers.added/removed/changed` and the reload-labeled
  `agents.grants` list; the route's composition stays await-free
  after the store load; OpenAPI regenerated; the README sentence
  that says the MCP half reports against the running generation.
  Design footprint: deepens `McpServers` with the reload's diff
  question in read-only form; callers never learn connection
  identity, secret marks, or slice anatomy. Branch
  `feature/config-diff-m2` stacked on M1, PR TBD.

## Test strategy

Reused assets: `tests/support/configs.py` factories for the running
side, `tests/support/stores.py` for stored snapshots,
`tests/support/apps.py` (`entered_client`) and
`tests/support/problems.py` for the transport suite, the committed
OpenAPI document's byte pin, and the integration lane's real-server
harness. New assets are one unit module for the comparison and the
transport cases beside the existing `/runtime` suite.

- **Comparison semantics** (`tests/unit/test_config_diff.py`):
  added, removed, and changed per kind; provider identity as
  `stage.name`; the singleton boolean; a stored-secret fingerprint
  change reported as changed with no value anywhere in the result;
  the agent `mcp`-exclusion rule (a grants-only edit is not claimed
  pending-restart); the regime-map-covers-`DOMAIN_KEYS` pin.
- **Transport** (`tests/unit/test_config_api_runtime.py`): the gate
  answers 401; a runtime-less application answers the problem shape
  503; the happy path returns the typed shape; the route joins the
  pinned inventory in `test_api_openapi.py` and the committed
  document is regenerated in the same change.
- **No-leak sentinel**: plant a credential-shaped stored secret,
  change it, and assert the serialized diff response carries the
  entity's name and no fragment of the planted value, in the body
  and in the problem paths.
- **Integration** (`tests/integration/test_config_api.py`): boot a
  real server, write a provider through the API, read the diff and
  see it pending; bind a device and see no pending claim; M2: edit
  an MCP server, see it pending, reload, and see the diff empty
  again, which is the end-to-end proof of the care point.

## The standing review lenses, pre-answered

**No-leak.** The response is names and closed tokens by
construction: no entity bodies, no values, no masks, no `shadows`
names. The comparison reads models and opaque fingerprints, and the
sentinel test above pins it. Refusals ride the existing sanitized
problem path (`REFUSAL_STATUS`); the route adds no exception text
of its own.

**Pin before reshaping.** Nothing existing is reshaped: the change
is additive (one dataclass field, one route, one module). The one
byte-pinned surface it touches, the OpenAPI document, changes
because the API changes, and the new bytes are committed and
reviewed in the same diff, which is that pin's designed workflow.

**Closed sets mapped to decision sites.** The `applies` tokens are
a declared `StrEnum`; the one decision site is the regime map in
`config/diff.py`, which is data, and the completeness pin holds it
to `DOMAIN_KEYS`. No token is ever chosen from message text.

**Honest seams.** The `ApiRuntime` field is compared
`is not None`; a serverless application answers 503, matching the
prompt and reload routes. The closure takes the snapshot as an
argument rather than reading the store itself, so the route's tests
can drive it with a snapshot they built.

**Inventories by tooling.** The kind coverage claim is the
`DOMAIN_KEYS` pin, not a list in prose; the M2 claim that grants
derivation is not duplicated is checked by there being exactly one
definition (grep for the defaults-then-own rule cited in review).

## Risks and mitigations

- **A reload runs while the diff composes (M2).** The registry
  swaps its world atomically on the loop, and the diff reads it on
  the loop with no await between the read and the response, so the
  answer is one world or the other, never a mix. The rule is stated
  in the route docstring the way `reload_result` states it.
- **The stored half fails to load** (unreadable database, secrets
  that do not open). The diff route meets it exactly as `GET
  /config` does: the typed refusals map through `REFUSAL_STATUS`,
  and no new failure vocabulary is invented.
- **Fingerprint semantics surprise an operator** (a re-set of the
  same value reports changed). Documented in the API description
  sentence for the read: changed means written since boot.
- **The M1 gap around MCP pending changes is misread as "nothing
  pending".** The `mcp_servers` entry is never empty lists in M1;
  it is the label alone, a shape that says "not answered here yet",
  and the README sentence says the reload surface owns that answer
  until M2 lands in the same release train.
- **Scope creep toward #191.** The generation story stays
  MCP-only, exactly as it is today; nothing here retains new state
  for other kinds, so #191 inherits a read to re-baseline, not a
  parallel mechanism to unwind.
