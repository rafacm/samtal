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
baseline for the MCP half is the registry's current generation,
which requires retained state and a new read on `McpServers` (the
design decision below). The endpoint does not ship in halves: a
label-only interim answer was considered and rejected by the review
(finding 2), because it would hide stored MCP and grant changes
that are genuinely pending, a false negative rather than a safe
under-claim. Milestone 1 builds the whole comparison unexposed,
with no route and no published schema, and milestone 2 exposes the
complete read.

**Where does the route live?** `GET /runtime/config/diff`. The
`/runtime` namespace exists precisely because it can never collide
with an entity name, and the two-segment path leaves
`/runtime/config/...` as the natural home for #191's generalized
reload verb, so the operator loop's read and its future apply sit
side by side. The alternatives (`/runtime/config-diff`,
`/runtime/pending`) name the same thing less compositionally.

**What is compared?** Typed models and secret fingerprints, never
rendered documents. Both sides hold the same entity models (the
boot and the re-read compose through one set of models, which is
the store's own design), so "changed" is model inequality; pydantic
equality is
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
change for the same reason. What "changed" means, and what the API
description says, is that the stored state differs from the
comparison baseline: an edit changed back before anyone looked
produces no diff, and a rewritten stored secret counts as different
because its ciphertext fingerprint changes even when the plaintext
may not have. State comparison, not write history, with the one
place the two blur documented rather than fought.

**How is the agent kind kept honest?** An agent entry spans two
regimes: its `mcp` grants are applied by the MCP reload (the
registry's slice derives grants from the whole candidate
configuration), while everything else about it waits for a restart.
The restart-bound comparison therefore takes agents and
`agent_defaults` with the `mcp` field excluded, so a grants-only
edit that a reload already applied is never claimed
pending-restart. The grant comparison under the reload label
covers every agent of the current generation, not only the agents
both worlds hold (the review's finding 5): a boot-loaded agent
deleted from storage keeps talking until a restart, while a reload
would revoke its grants now, so its stored side compares as the
empty grant set and the pending revocation stays in the grants
list until a reload applies it. An agent only the stored side
knows rides its own added row: its grants describe a world that
begins at the restart that adds it.

**How does the read stay one-world?** The route is `async def` for
the reason the runtime status read is: the MCP world is read on the
loop that owns it. But being on the loop is not enough, because the
stored side is loaded in a worker thread and that await is itself
the race window (the review's finding 3): a concurrent reload can
read the store after the diff did and install its world before the
diff composes, leaving stored generation A compared against a
runtime that installed B, two states that never coexisted. The
registry therefore exposes the cheap generation mark its install
advances (a fact milestone 1 adds anyway); the route captures it on
the loop before the worker-thread load, re-checks it on the loop
after, and on a mismatch loads again, twice at most, then refuses
with a retryable 409 from the typed refusal set rather than
composing a mixed world. Whether `ReloadInProgressError`'s sentence
honestly covers "the running world moved while the diff read" or a
sibling refusal joins the closed set is decided with the code in
front of the milestone; the status is 409 and the mapping lives in
`REFUSAL_STATUS` either way. After the last mark check the
composition runs with no await of its own, which is the
`reload_result` one-world rule applied to a read.

## Design decisions this plan makes

**A new module owns the comparison: `config/diff.py`.** The one
sentence its callers stop having to know: which configuration kind
converges at which boundary, and how equality between two
configuration worlds is judged. It exports one function taking the
running side and the stored side, each a composed `Config` with its
`SecretStore` (the boot's, and the re-read's), and returning the
typed result; it is pure, so its tests build both sides from the
support factories and never touch a database. The regime map (which
kind carries which `applies` token) lives here as data, beside the
comparison that uses it. Deletion test:
inlined into `app.py` the composition root would own comparison
rules; inlined into `api.py` it would break that module's standing
contract that the API never learns what configuration means. Both
callers get harder to read, so the module stands.

**The API learns nothing; the composition root wires a closure,
and the stored side is the reload's own re-read.** `ApiRuntime`
gains one optional field beside `mcp_reload` and `agent_prompt`: an
async callable producing the diff response, `None` for an
application built without a server around it, answered by the route
with the honest 503 the prompt read already answers, and compared
`is not None`, never by truthiness. `app.py` builds the closure
beside `_mcp_reloader` and `_prompt_preview`, where the boot
configuration and its secrets are already in hand. The settled
decision says the stored side is the same re-read the MCP reload
uses, and the reload's re-read is `reload_domain_config`, not a raw
`ConfigStore.load` (the review's finding 4): it opens and migrates
the database, verifies that every stored secret opens under the
configured keys, and composes and validates the whole snapshot. The
closure runs exactly that in a worker thread, the way
`_mcp_reloader`'s read half does, so a stored half whose secrets do
not open, or one that is model-valid but fails whole-snapshot
validation, refuses through the same typed errors the reload
refuses through, mapped by `REFUSAL_STATUS`. The cost is the
reload's cost, the database write lock held for the read's
duration, priced in for an operator inspection read. The route
stays ignorant of all of it: it awaits the callable and answers.

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
                           "changed": [],
                           "grants": {"applies": "reload", "changed": []}},
      "agent_defaults":   {"applies": "restart", "changed": false},
      "mcp_servers":      {"applies": "reload", "added": [], "removed": [],
                           "changed": []},
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
`check-in`. The schema is published exactly once, complete, when
milestone 2 lands the route: the response models forbid extra keys
like every model in `responses.py`, so a client generated from a
smaller interim schema would reject a grown response, and no
interim schema therefore ever exists (the review's finding 6).

**Completeness is pinned, not remembered.** The regime map must
cover the domain, and a domain kind added next year must not
silently fall out of the diff: a unit test asserts the map's keys
equal `DOMAIN_KEYS` exactly, so the sixth kind arrives with a
failing test naming this module. This is the two-structures rule
applied to the plan's own new data.

**The MCP generation retains a comparison identity for every
entry, and `McpServers` is deepened rather than exported.** The
world the registry swaps today cannot answer the diff's question
for every entry: managers exist only for the entries some agent
references, and the slice keeps names, grants and prompt fields,
so an unused entry has no retained connection fields and no secret
mark to compare (the review's finding 1). The install that swaps a
generation in, at boot and at reload alike, therefore also retains
one opaque comparison identity per configured entry, every entry
and not only the referenced ones, computed from the connection
identity the reload's `same_as` already uses, the prompt-only
fields (`instructions` and `use_server_instructions`, which are
the two that identity excludes; `inject_prompts` is deliberately
not one of them, because editing it changes what a connect
fetches and so restarts the connection, and it is therefore
already inside the connection identity), and the entry's
stored-secret fingerprint. The
identities are swapped atomically with the world they describe,
and one derivation computes them for the running side at install
and for the stored candidate at diff time, so the two sides cannot
disagree about what identity means. The new public read on
`McpServers` takes the stored candidate and answers in the diff's
vocabulary (added, removed, changed) by comparing identities; no
manager is ever built, and callers never learn connection
identity, secret marks, or slice anatomy. The alternative,
exposing the generation's entry configurations for
`config/diff.py` to compare, would teach the diff module exactly
those facts, which are the MCP package's to know. Grant derivation
for the stored side reuses the one derivation that exists
(`mcp_for_agent`'s defaults-then-own rule) rather than restating
it; the milestone either calls it through a composed view of the
stored domain or factors the rule to a function both `Config` and
the diff path call, whichever the diff in front of the implementer
keeps smaller.

## Module layout

- `config/diff.py` (new, M1): the comparison and the regime map.
- `tools/mcp/registry.py` (M1): the per-entry comparison
  identities retained at install, the generation mark, and the
  pending-against-stored read; `slice.py`, `manager.py` and
  `reload.py` as far as the install path needs to carry the
  identities, factoring an existing private helper only where that
  is smaller than calling through the registry.
- `config/responses.py` (M2): `ConfigDiff` and its per-kind
  models, the `applies` token enum.
- `config/api.py` (M2): the `ApiRuntime` field, the dependency
  resolver, the `GET /runtime/config/diff` route in `_runtime`,
  and the `API_DESCRIPTION` paragraph that rewrites the "boot-time
  snapshot" story to mention the read.
- `app.py` (M2): the closure builder beside `_mcp_reloader`.
- Docs: `docs/reference/api-openapi.json` regenerated (drift
  checked); `vinga-server/README.md`'s `/runtime` route block;
  `CHANGELOG.md`. `docs/reference/domain-config.md` is untouched
  (no schema change), and no write acknowledgement changes, so no
  notice text moves.

## Milestones

Every merge leaves `main` releasable: the image publishes on every
push, so each milestone ends with lint, both suites, and the doc
drift checks green.

- [x] **[M1: the whole comparison, unexposed](2026-08-20-config-diff-read-implementation.md#m1-the-whole-comparison-unexposed).** (PR #227) `config/diff.py`
  with the regime map, the `DOMAIN_KEYS` completeness pin, and the
  model-plus-fingerprint comparison for the restart-bound kinds
  (providers as `stage.name`, prompt fragments, agents and
  `agent_defaults` with `mcp` excluded); the MCP generation's
  retained per-entry comparison identities computed at install, the
  generation mark the install advances, and the public `McpServers`
  read answering added/removed/changed plus effective-grant changes
  against a stored candidate; unit tests at both interfaces,
  including every named MCP case in the test strategy. No route, no
  response models in the OpenAPI document, no behavior change; the
  committed reference documents are untouched and the drift checks
  prove it. Design footprint: adds the one module that knows
  convergence boundaries, and deepens `McpServers` with the
  reload's diff question in read-only form; callers never learn
  connection identity, secret marks, or slice anatomy. Branch
  `feature/config-diff`, PR #227.
- [ ] **M2: the route, complete.** The typed response models,
  published once with the `mcp_servers` lists and the
  `agents.grants` entry included; the `ApiRuntime` field, the
  composition-root closure running the reload's re-read in a worker
  thread with the generation-mark retry; the async route with its
  503 and its 409; the OpenAPI regeneration with the route
  inventory pin; the transport, sentinel, concurrency, and
  integration tests; the `API_DESCRIPTION` paragraph, the README
  `/runtime` block, and the CHANGELOG entry. Design footprint: adds
  the one seam (an async diff callable on `ApiRuntime`) and deepens
  the composition root by one closure built where the boot world is
  already in hand. Branch `feature/config-diff-m2` stacked on M1,
  PR TBD.

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
- **The MCP cases that decide the design** (unit, at the registry's
  new read and the diff function): an unused entry's
  connection-field edit reports changed; an unused entry's
  stored-secret rotation reports changed; a prompt-only edit
  (`instructions`, `use_server_instructions`) reports changed
  without any connection difference, and an `inject_prompts` edit
  reports changed and does restart the connection, since that
  field is part of the connection identity; effective
  grants compare through the defaults-then-own rule, so moving a
  grant between `agent_defaults` and the agent without changing the
  effective set reports nothing; a deleted boot-loaded agent's
  grants report changed before a reload and nothing after it.
- **Stored-side failure paths**: a wrong encryption key (stored
  secrets that do not open) and a stored domain that is model-valid
  but fails whole-snapshot validation both answer the same typed
  refusals the MCP reload answers, proven at the route.
- **Concurrency**: a barrier-driven test forces a reload's install
  between the diff's stored load and its composition, and asserts
  the answer is a re-read of one world or the retryable 409, never
  a mixture; a second case proves the retry succeeds when the world
  holds still.
- **No-leak sentinels**: distinct sentinels for each thing that
  must not travel: the planted plaintext, the stored ciphertext
  envelope as the database holds it, the entity's fingerprint hex,
  and a syntactically valid environment-reference name. Each is
  asserted absent from the successful diff response, from a forced
  refusal body (the stored half made unreadable by a wrong
  encryption key, so the problem path is a real one rather than an
  unspecified one), and from the log records both paths emit, in
  both log formats.
- **Integration** (`tests/integration/test_config_api.py`): boot a
  real server, write a provider through the API, read the diff and
  see it pending; bind a device and see no pending claim; make one
  connection-changing MCP edit and one prompt-only MCP edit, see
  both pending, reload, and see the diff empty again, which is the
  end-to-end proof of the care point.

## The standing review lenses, pre-answered

**No-leak.** The response is names and closed tokens by
construction: no entity bodies, no values, no masks, no `shadows`
names. The comparison reads models and opaque fingerprints, and
the sentinel suite above pins all four forms a secret's presence
takes (plaintext, ciphertext, fingerprint, environment-reference
name) as absent from the success body, a forced refusal body, and
the logs of both. Refusals ride the existing sanitized problem
path (`REFUSAL_STATUS`); the route adds no exception text of its
own.

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

- **A reload runs while the diff reads.** Handled structurally by
  the generation mark, the bounded re-load, and the retryable 409
  (the one-world question above), and pinned by a barrier-driven
  concurrency test that forces an install between the diff's stored
  load and its composition. The rule is stated in the route
  docstring the way `reload_result` states it.
- **The stored half fails to load** (unreadable database, secrets
  that do not open, a stored domain that is model-valid but fails
  whole-snapshot validation). The diff route meets all of it
  exactly as the MCP reload does, because it runs the reload's own
  re-read: the typed refusals map through `REFUSAL_STATUS`, and no
  new failure vocabulary is invented.
- **Fingerprint semantics surprise an operator** (a re-set of the
  same value reports changed). Documented in the API description
  sentence for the read: changed means the stored state differs
  from the baseline, and a rewritten secret counts as different.
- **M1 lands machinery no route reaches yet.** Accepted for one
  milestone by design: publishing a partial schema was the worse
  trade (findings 2 and 6), the new interfaces are exercised by
  their own unit suites in the same PR, and the route is the next
  PR in the stack.
- **Scope creep toward #191.** The generation story stays
  MCP-only, exactly as it is today; nothing here retains new state
  for other kinds, so #191 inherits a read to re-baseline, not a
  parallel mechanism to unwind.

## Plan review round (2026-08-20)

External review: codex exec, model gpt-5.6-sol, read-only against
commit 3d89181c. Verdict: ready after the P1/P2 amendments. Findings
condensed but faithful; each carries its resolution.

**1 (P1). The registry does not retain enough state to diff unused
MCP entries.** The slice keeps only entry names, grants,
instructions and `use_server_instructions`; managers keep
configuration and secret marks but exist only for referenced
entries, so an unused entry has no retained connection fields or
fingerprint to compare against.

*Resolution.* Adopted. The design decision "The MCP generation
retains a comparison identity for every entry" now states it: the
install retains one opaque identity per configured entry,
referenced or not, computed at boot and reload from connection
identity, prompt-only fields, and the secret fingerprint, swapped
atomically with the world, derived by one function for both sides,
and consumed by the public registry diff read.

**2 (P1). M1 ships a false-negative diff for unapplied MCP and
grant changes.** Answering `{"applies": "reload"}` alone hides
stored MCP and grant changes that are still pending; the issue
permits labeling or excluding changes that are already live, not
hiding pending ones. Keep M1 internal with no route, or combine the
milestones.

*Resolution.* Adopted, as the first offered cut: milestone 1 builds
the whole comparison unexposed (no route, no published schema) and
milestone 2 exposes the complete read. The open-questions section,
the milestones, the module layout, and the risk that replaced the
label-only gap now say so.

**3 (P1). The claimed one-world read can combine states that never
coexisted.** The await around the stored load is itself the race
window: a diff can load stored generation A, then observe runtime
generation B after a concurrent reload. Add a generation token
captured before the load and re-checked after, with a bounded retry
and a retryable refusal, plus a barrier-driven concurrency test.

*Resolution.* Adopted. The one-world question now states the
mechanism: the mark is captured on the loop before the worker-thread
load and re-checked after, a mismatch re-loads at most twice and
then refuses with a retryable 409 through `REFUSAL_STATUS`, and the
barrier-driven test (install forced between load and composition)
is named in the test strategy and the risk table.

**4 (P1). `ConfigStore.load()` is not the re-read the MCP reload
uses.** The reload goes through `reload_domain_config`, which also
verifies every stored secret and composes and validates the whole
snapshot; the plan's claim that a stored half that does not open
fails like the reload is false as written. Use
`reload_domain_config` in the worker, and test a wrong encryption
key and a stored domain that is model-valid but fails
whole-snapshot validation.

*Resolution.* Adopted. The closure decision now says the stored
side runs `reload_domain_config` in a worker thread, the way
`_mcp_reloader`'s read half does, with the write-lock cost named;
the diff function takes two composed `Config`s with their
`SecretStore`s and stays pure; both named failure tests are in the
test strategy and the risk table.

**5 (P1). Restricting grant comparison to agents present on both
sides hides live revocation for removed agents.** A boot-loaded
agent deleted from storage keeps talking until restart while a
reload would revoke its grants now; comparing only shared agents
omits that pending revocation. Compare effective grants for every
current-generation agent, treating absence from the stored
candidate as an empty grant set, with the before- and after-reload
test.

*Resolution.* Adopted. The agent-honesty decision now compares
effective grants for every current-generation agent, a deleted
agent's stored side comparing as the empty grant set so its pending
revocation stays reported until a reload applies it; the deleted
agent's before- and after-reload case is named in the MCP test
list.

**6 (P2). M2 is a breaking response-schema change, not an additive
extension.** Response models forbid extra keys, so a client
generated from M1's schema can reject M2's response. Publish the
final schema exactly once.

*Resolution.* Adopted through finding 2's recut: no interim schema
ever exists. The response-shape decision now states that the models
forbid extra keys and are published complete when milestone 2 lands
the route, with the sketch showing the final shape.

**7 (P2). The no-leak sentinel proves only plaintext absence.** One
planted plaintext would not catch serialization of the ciphertext
envelope, the fingerprint, or an environment-variable name, and the
problem-path coverage is unspecified. Use distinct sentinels and a
forced stored-state failure.

*Resolution.* Adopted. The sentinel bullet now plants all four
forms and asserts each absent from the success body, a wrong-key
refusal body, and the log records of both paths in both formats;
the no-leak lens paragraph matches.

**8 (P2). The tests do not exercise the MCP cases that decide
whether the design works.** One referenced-entry edit plus reload
would pass even if unused entries, prompt-only fields, secret
rotation, grant inheritance, removed agents, or the reload race
were all broken. Name those tests.

*Resolution.* Adopted. The test strategy now names each case: the
unused entry's connection edit and secret rotation, the prompt-only
edit, inheritance through the defaults-then-own rule including the
no-effective-change move, the deleted agent before and after
reload, the barrier-driven race, and an integration pass carrying
both a connection-changing and a prompt-only edit through a reload.

**9 (P2). "Changed means written since boot" is false.** Model
equality reports state difference, not write history: an edit
changed back produces no diff, and only re-encrypted secrets retain
history, accidentally. Say that changed means the stored state
differs from the baseline.

*Resolution.* Adopted, with the reviewer's sentence: changed means
the stored state differs from the comparison baseline, and a
rewritten stored secret counts as different because its ciphertext
fingerprint changes even when the plaintext may not have. The
comparison decision and the risk table now say exactly that.
