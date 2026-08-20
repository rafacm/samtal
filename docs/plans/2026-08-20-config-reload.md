# Apply domain configuration to a running server without a restart

## Goal

Implement issue #191: generalize the MCP reload's prepare-then-swap
pattern so that a stored domain-configuration change can be applied
to a running server through one explicit reload endpoint, slice by
slice in the issue's own ladder: agent prompt text and prompt
fragments first, then fillers with clip reuse, then providers (a
teardown lifecycle, generational binding per session, and
unchanged-entry reuse), then the agent set and `agent_defaults`.
The immutable whole-validated snapshot stays load-bearing
everywhere: an apply builds a complete new world in a prepare phase
that can only refuse, then swaps what new work binds to, and
everything in flight keeps the generation it bound at its last
convergence point. The file half (port, database directory,
`local_only`, capture, conversations, auth, memory) stays
restart-only, deliberately; `reload_domain_config` never re-reads
the file and that boundary does not move.

The companion implementation doc,
[`2026-08-20-config-reload-implementation.md`](2026-08-20-config-reload-implementation.md),
records what each milestone actually did, deviations from this
plan, and discoveries; a milestone with no deviations says so
explicitly.

## The issue's decisions, restated

Settled by issue #191 and not re-litigated here:

1. **Generalize the reload's pattern, not `Config`.** No live
   mutable configuration object: a prepare phase that can only
   refuse (the read half exists: `reload_domain_config` re-runs
   boot steps 2 to 5, secrets verification and whole-snapshot
   validation included), then a swap of what new work binds to.
2. **Writes stay inert; applying is one explicit generalized
   reload endpoint, extending the existing one.** A UI edit is
   several writes, and applying after each one would validate and
   swap against half-edited intent. "Save, then apply" pairs with
   the stored-vs-running diff (#193), which is now merged and is
   the read an operator reviews before pressing apply.
3. **The milestone ladder is the issue's, in ascending
   difficulty**: prompts and fragments at activation; fillers with
   clip reuse; providers (teardown, generational binding,
   unchanged-entry reuse so a prompt edit never reloads a local
   model); the agent set and `agent_defaults` riding on the above.
4. **Convergence points, not interruption**: next activation for
   prompt text, next utterance for tools, next session for
   providers; a deleted agent's live sessions finish on the old
   generation.
5. **The file half stays restart-only.**
6. **The non-code costs are part of each slice**: the boot-snapshot
   sentence is load-bearing in the API description, every write's
   `notice`, the committed OpenAPI document, and the generated
   references; each slice that goes live rewrites its
   acknowledgement to its actual convergence point under the
   existing drift checks, and the per-slice "when does a live
   conversation see this" prose that exists for the MCP reload gets
   written for prompts, fillers, and providers.

Sequencing context, decided 2026-08-19 and adjusted since: #193
landed first (PRs #227/#228) and this plan arrives into that world;
the CLI rebuild (#194) is postponed behind #223, so this plan's CLI
surface is the minimal retarget of the existing reload subcommand,
not new grammar.

## Prerequisite decision: #195, resolved here as the issue asks

#195 asks two questions together: whether snapshot-authoritative
device bindings still earn their keep, and whether the unit lane
should migrate onto seeded databases (roughly 118 tests across 19
files lean on snapshot mode, plus the shared factories).

**Decision: snapshot-authoritative mode is kept, and its meaning is
sharpened from "the boot snapshot is the whole truth" to "the
current generation is the whole truth". The unit lane does not
migrate.** Reasons:

- The mode exists for deployments with no database file behind the
  configuration: the test lane and an embedded caller. That reason
  survives generations unchanged; what changes is only which
  configuration object it serves. Once `DeviceBindings` reads the
  agent set and the fallback bindings from the current generation
  (milestone 4) instead of a construction-time `Config`, snapshot
  mode is simply the degenerate case where the current generation
  is the only generation there will ever be. No test's meaning
  changes: with no reload in play, the current generation is the
  boot.
- The hazard #195 records (a fresh-directory embedded caller gets
  snapshot bindings until its first restart, then live ones) stays
  production-unreachable for the reasons PR #189 pinned: both
  production entry points migrate the directory before the build.
  This plan does not touch `DeviceBindings.open`'s decision.
- Migrating the unit lane onto seeded databases would change what
  the lane means (in-memory configuration becomes stored state) for
  no property this plan needs.

#195 is closed when this plan's review round settles, citing this
section; milestone 4 carries the `DeviceBindings` change that makes
the decision real.

## The issue's open questions, and the ones the code map raises

**Where does the generation live?** A new top-level module,
`generation.py`, beside `composition.py`: a frozen `Generation`
(the composed `Config`, its `SecretStore`, and, as the milestones
widen it, the built `AgentProviders` mapping and the filler cache),
and a `Generations` holder whose `current()` is what every
convergence point reads and whose swap is a single assignment with
no await, the `McpServers._install` shape. The holder also owns the
one operator-visible generation counter: #193's diff guard reads it
(the mark moves here from `McpServers`, whose own counter becomes
an internal detail or is dropped), and every apply advances it
exactly once, after its last swap point. `Composition` carries the
holder; the closures that today capture the boot `Config`
(`bespoke_runtime_factory`, `_prompt_preview`,
`config_diff_reader`) capture the holder instead and read
`current()` at their existing convergence points.

**One endpoint, and what happens to the MCP route?** The
generalized endpoint is `POST /runtime/config/reload`, and it
subsumes `POST /runtime/mcp-servers/reload` in milestone 1: the
MCP half's prepare and apply run inside the generalized reload
unchanged (same exclusion, same shielded phases, same
started/restarted/stopped/unchanged answer for entries), and the
old route is removed rather than aliased, pre-release, with the
CLI's existing `reload` subcommand retargeted in the same change.
One apply must not race another: the MCP reload's exclusion
(refused, not queued, held past a cancelled caller) widens to the
whole generalized reload.

**What does the result look like?** A `ConfigReloadResult` in
`config/responses.py` with one section per kind, published complete
for the kinds that are live at that milestone and extended per
milestone (a response model gaining a field is additive for
clients, unlike #193's diff where the closed shape had to publish
once; the OpenAPI byte pin makes each extension a reviewed diff).
The MCP section keeps the four-outcome vocabulary with its
documented meaning (a statement about the connection, not the
entry's text). Other kinds get the vocabulary that is true for
them: prompts and fragments converge by re-assembly, so their
section reports the agents whose assembled know-how inputs changed;
fillers report agents re-synthesized and agents reusing clips;
providers report entries built, reused, and retired. The
`RELOAD_OUTCOMES`-style CLI rendering introspects `list[str]`
fields, so each new section either keeps that shape or brings its
own rendering in the same change; a differently-shaped field that
silently drops out of the CLI is the named failure to test for.

**How do the two swap points relate?** An apply has two: the
generation assignment and the MCP `_install` (which stops and
starts managers around it and cannot be awaitless). They happen in
one apply under one exclusion, so no second apply interleaves, but
a session activating between them sees the new generation's
prompts with the old MCP world for at most one utterance. That is
the same per-half convergence the MCP reload already documents
(guidance at activation, tools at the next utterance), stated in
the endpoint's description rather than fought with a lock across
awaits.

**What does the diff (#193) do as slices go live?** Each milestone
moves its kind's running side from the boot snapshot to
`current()`, flips the kind's `Applies` token from `restart` to
`reload` in the regime map (the completeness pin holds the map to
`DOMAIN_KEYS` throughout), and for the agent kind grows the same
split the grants already have: in milestone 1 the prompt half
(`prompt`, `prompt_includes`) leaves the restart-bound comparison
and is compared against the current generation under the reload
label, exactly as `mcp` grants are today. The diff's care point is
unchanged: a change a reload already applied reports nothing.

**Fillers: what is the reuse key, which voice synthesizes, and
when do sessions see the change?** The reuse key is the pair the
synthesis actually depends on: the agent's effective filler section
(`filler_for_agent`) and the identity of the agent's effective TTS
provider entry (entry model plus its secret fingerprint, the
comparison #193 built). In milestone 2 providers are not yet
generational, so synthesis always uses the running TTS providers;
once milestone 3 lands, it uses the candidate generation's. A
filler synthesis failure during an apply degrades exactly as boot
does (the agent runs with the feature off, an event says so) and
does not refuse the apply: fillers are a latency mask, and a
posture where a TTS hiccup blocks a prompt fix would invert their
importance. The fill-once assert in `AgentFillers` goes: the cache
becomes a value the generation carries, sessions bind it at
construction through the factory, and convergence is the next
session, the same clock as providers, rather than mid-session
timer changes.

**Providers: what is the lifecycle?** Three parts, the issue's own
list. (a) `Provider` gains an async `close()`, default no-op,
overridden where a real resource is held: the long-lived HTTP
clients (ElevenLabs, the OpenAI and Anthropic SDK clients) close
their pools; the local models (faster-whisper, Piper, Silero) drop
their engine references, with the honest caveat recorded that a
library like CTranslate2 frees memory on its own schedule, so
release is best-effort and double residency during a swap is the
accepted cost either way. (b) Generational binding: a session binds
`current()` once (the factory hands the runtime its generation, in
place of the shared dict), holds it for its life, and the
generation is retired when its last session ends; the
`SessionRegistry`, the only object that knows the whole session
set, gains the disposal hook (sessions are counted per generation,
and the registry disposes a generation that is neither current nor
held by any session). (c) Unchanged-entry reuse: the prepare phase
builds the candidate `AgentProviders` mapping reusing the running
generation's provider object for every entry whose model and
secret fingerprint are unchanged (the `same_provider` comparison
the diff already has), so an apply that only edited a prompt never
reloads Whisper. Reuse transfers the object into the new
generation; disposal closes only providers owned by the retiring
generation and not adopted by a live or current one, so a shared
instance is never closed under a generation still using it. The
egress check cannot run without constructing the provider
(`check_provider` validates the built instance's class), so the
prepare phase for providers pays the model load before it can
refuse; that is the double residency the issue prices in, and it
buys the property that a refused apply has touched nothing
running.

**The agent set: what moves in milestone 4?** The four snapshots
of "which agents can this server serve" move onto the generation:
`ApiRuntime.loaded_agents` (which chooses between the binding
notice and the restart notice), `McpServers`' servable-agent set
(whose "read once, never replaced" contract #193 documented in
three places, all rewritten here), `DeviceBindings`' loaded set
and fallback configuration (the #195 decision above), and the
prompt preview's 404 branch. A new agent is part of the next
generation and servable from the swap; a deleted agent's live
sessions finish on the generation they bound. The per-session
bound-agent list stays per-session, correctly: it is the device's
binding, not the server's capability.

**Where does #88 fit?** Typed option models would refuse provider
option typos at write time and narrow the unchanged-entry
comparison to declared fields; the comparison works today on model
equality plus secret fingerprints, exactly as the diff's does, so
#88 stays an enhancer and nothing here waits for it.

## Module layout

- `generation.py` (new, M1, widened each milestone): `Generation`
  and `Generations`. The one sentence callers stop having to know:
  which world a piece of new work should bind, and when an old
  world may be disposed. Deletion test: inlined into `app.py` the
  composition root would own swap-and-retire rules; inlined into
  `pipeline.py` the runtime would own a server-wide lifecycle.
- `config/reload.py` (new, M1): the generalized apply: the
  exclusion, the shielded prepare and apply phases, the composed
  `ConfigReloadResult`. It orchestrates `reload_domain_config`,
  the MCP half (delegating to the mcp package's existing prepare
  and apply, which keep their homes), and, per milestone, filler
  synthesis and provider builds. Deletion test: inlined into
  `api.py` the route would learn what a reload is made of; inlined
  into `app.py` the composition root would grow a second
  responsibility beyond wiring.
- `config/api.py` (M1): the route swap in `_runtime`
  (`POST /runtime/config/reload` in, the MCP route out), the
  `API_DESCRIPTION` rewrite, `ApiRuntime`'s reload field retyped
  to the generalized callable.
- `providers/base.py` and the concrete providers (M3): `close()`.
- `registry.py` (M3): the per-generation count and the disposal
  hook.
- `filler.py` (M2): the cache becomes a generation value; the
  fill-once assert and `ready` go with the seam that needed them.
- `device/bindings.py`, `config/entities.py` notices,
  `config/diff.py` regime map and running side, `config/models.py`
  field descriptions, `config/writes.py` notice choosers: per
  milestone as each slice goes live.
- Docs regenerated under their pins per milestone:
  `api-openapi.json`, `domain-config.md`; plus the README's
  boot-snapshot sites and `CHANGELOG.md`.

## Milestones

Every merge leaves `main` releasable: lint, both suites, and the
four drift checks green, with the generated references regenerated
in the same change as the prose that feeds them.

- [ ] **M1: the generation seam and the generalized reload, with
  the prompt slice live.** `generation.py` holding config and
  secrets; the holder's counter replaces `McpServers.generation`
  in the diff guard; `config/reload.py` with the widened
  exclusion, prepare (the re-read, whole-snapshot validation, MCP
  candidate preparation) and apply (generation swap, MCP install);
  `POST /runtime/config/reload` replaces the MCP route, CLI
  subcommand retargeted, `ConfigReloadResult` with the MCP section
  and the prompts section; `_activate_agent`, `_prompt_preview`
  and the diff's running side read prompt text and fragments from
  `current()`; the prompt-fragment descriptor's notice becomes the
  reload notice, the agent kind's diff comparison grows its
  reload-labeled prompt half, `models.py`'s `prompt_includes`
  description and the `docgen` contract prose rewritten, both
  generated references regenerated, README sites swept. Design
  footprint: adds the generation seam; deepens the reload from an
  MCP verb into the configuration verb whose callers stop knowing
  which halves converge where. Branch `feature/config-reload`,
  PR TBD.
- [ ] **M2: fillers, re-synthesized with clip reuse.** The filler
  cache becomes a generation value bound per session; prepare
  re-synthesizes only agents whose reuse key (effective filler
  section plus effective TTS entry identity) changed, reusing
  clips otherwise; synthesis failure degrades as boot does and is
  reported in the result's fillers section; the fill-once assert
  retires with the lifespan seam. Branch `feature/config-reload-m2`
  stacked on M1, PR TBD.
- [ ] **M3: providers, the hard slice.** `Provider.close()` and
  the concrete overrides; generational binding (the factory hands
  each runtime its generation; the registry counts sessions per
  generation and disposes a retired generation's unadopted
  providers when its last session ends, and immediately when it
  retires with no sessions); unchanged-entry reuse by model
  equality plus secret fingerprint with ownership transfer; the
  provider descriptor's and provider-secret writes' notices become
  reload notices; the diff's provider kind flips to `reload` and
  its running side reads `current()`; the result gains the
  built/reused/retired providers section; the session manifest
  reads (`device/session.py`'s provider lookups) bind the
  session's generation. Branch `feature/config-reload-m3` stacked
  on M2, PR TBD.
- [ ] **M4: the agent set and agent_defaults.** The four agent-set
  snapshots move to the generation (`ApiRuntime.loaded_agents`
  becomes a read-through, `McpServers`' servable set swaps with
  the world and its three "never replaced" prose sites are
  rewritten, `DeviceBindings` reads the current generation per the
  #195 decision, the prompt preview's 404 follows); `binding_notice`
  answers from the current generation; the agent and agent-defaults
  kinds flip to `reload` in the regime map and their notices
  follow; a deleted agent's sessions finish on their generation
  (already structural after M3); the diff's remaining restart
  claims for the domain half are gone, and the API description's
  boot-snapshot paragraph is rewritten into the generation
  paragraph. Branch `feature/config-reload-m4` stacked on M3,
  PR TBD.

## Test strategy

Reused assets: `tests/support/configs.py` factories,
`tests/support/apps.py`, the MCP support servers and
`test_tools_mcp_reload.py`'s real-registry harness, the diff
suites from #193 (which pin the care point this plan must keep
true), the wire/integration harness of `test_mcp_reload.py` (one
session across a reload), and the drift-check pins.

- **M1**: unit tests for the holder (swap is one assignment, the
  counter advances once per apply); reload transport tests
  migrated from the MCP route with the same 401/409/422/500/503
  contract; activation reads `current()` (a session activated
  before the apply keeps its know-how, one activated after
  assembles the new text, driven through the public runtime
  harness); the prompt preview and the diff agree with activation
  after an apply (the free-win claim, pinned); integration: edit a
  fragment, apply, same socket, next activation speaks the new
  text; the diff empties for the prompt half after apply.
- **M2**: clip reuse pinned by object identity across an apply
  that edits only a prompt; a phrase edit re-synthesizes only that
  agent; synthesis failure degrades and reports; a session's
  filler timing does not change mid-session.
- **M3**: unchanged-entry reuse pinned by provider object identity
  (a prompt-only apply reuses every provider); a changed entry
  builds anew and the old provider closes when the last bound
  session ends, pinned with the registry hook and a fake provider
  whose `close()` records; a shared reused instance is not closed
  while any generation holds it; egress refusal in prepare touches
  nothing running (the running generation's objects are identical
  after a refused apply); local-model double residency is not
  asserted numerically, but the close-called-once property is.
- **M4**: bind a device to an agent added by apply and see it
  served at the next check-in with the binding notice, no restart;
  delete an agent with a live session, apply, the session finishes
  and new sessions cannot reach it; the diff reports nothing for
  an applied agent-set change.
- **Concurrency, every milestone**: the widened exclusion refuses
  a second apply; the #193 barrier test keeps passing with the
  holder's counter as the mark.
- **No-leak**: the reload result carries entry and agent names and
  closed outcome tokens only; the sentinel pattern from #193's
  route (plant, force the wrong-key refusal, assert absence in
  body and logs) reruns against the generalized endpoint in M1.

## The standing review lenses, pre-answered

**No-leak.** The prepare phase refuses through the same typed
errors the boot and the MCP reload refuse through, with the #228
lesson applied from the start: stored-side refusal sentences that
can embed stored bytes are replaced at the endpoint boundary with
fixed sentences, types preserved, chains cut. The result model is
names and closed tokens.

**Pin before reshaping.** The MCP route's behavior moves, not
changes: its transport suite migrates to the new path in the same
change, and the MCP section of the result keeps its shape and
vocabulary, byte-pinned through the OpenAPI document. The
activation path's behavior for a server that never applies is
pinned by the existing suites, which must pass unmodified in M1
except where they name the retired route or the moved counter.

**Closed sets mapped to decision sites.** Result outcomes per
section are declared closed sets chosen where the apply actually
classifies (reuse decision, manager diff, synthesis decision);
the diff's `Applies` map keeps its completeness pin and each flip
is one reviewed line.

**Honest seams.** The generation holder is required, not optional,
in the composition; optional seams that remain (`ApiRuntime`
fields) keep `is not None`. The registry's disposal hook gets its
own pins since sessions cannot prove it.

**Inventories by tooling.** The boot-snapshot prose sites are
enumerated by grep in each milestone's brief (the README alone has
eight); the closure inventory (the four config closures that must
read the holder) is verified by grep before M1 merges and the
implementation doc records the count; the agent-set snapshot
inventory (four sites) likewise before M4.

## Risks and mitigations

- **M1 is the widest milestone** (endpoint swap, holder, diff
  rewire, doc sweep). Mitigation: the MCP behavior moves verbatim
  behind the new path with its migrated suite as the proof; the
  prompt slice itself is small; if the diff rewire grows past
  review size, the milestone may land the holder counter first and
  the brief says so, but the route swap and the prompt slice stay
  one PR so `main` never serves two reload routes.
- **Provider disposal races a session's last read.** The registry
  removes the session before disposing, and disposal only runs for
  generations that are not current and have zero sessions; a
  session holds its generation object, not a name, so a disposed
  generation is unreachable rather than emptied under a reader.
- **Double residency on constrained hosts.** Accepted by the
  issue; the endpoint description says an apply that changes a
  local model briefly holds two, and the refusal path is priced to
  touch nothing running, so the worst case of a failed apply is
  the old world plus a garbage-collected candidate.
- **CTranslate2 and friends free lazily.** `close()` drops the
  references and the docstring says release is the library's
  schedule; the tests pin close-called, never RSS.
- **The two swap points leave a one-utterance mixed window.**
  Documented as the per-half convergence the MCP reload already
  has; no lock spans awaits.
- **The retired MCP route breaks an operator's muscle memory.**
  Pre-release and the CLI subcommand moves in the same change; the
  CHANGELOG entry names the replacement.
- **`DeviceSession`'s manifest reads go stale under generations.**
  Named in M3's scope (the session binds its generation for
  manifests), so the conversation record names the providers that
  actually served it.

## Plan review round (2026-08-20)

External review: codex exec, model gpt-5.6-sol, read-only against
commit 04579e44. Verdict: not ready; a second round runs against
the amended plan before implementation. Findings condensed but
faithful; each carries its resolution.

**1 (P1). A stored `Config` is not the serving generation during
the staged rollout.** Putting the whole freshly loaded `Config`
into `Generation` lets M1 apply restart-bound changes through
inheritance (`fragments_for_agent` reads
`agent_defaults.prompt_includes`; provider builds read effective
defaults), lets an activation index an agent deleted in the store,
and leaves the diff claiming `agent_defaults` pending after its
effective fields already changed. The generation's config must be
an overlay of only that milestone's live slices onto the previous
generation, preserving the servable agent set and restart-bound
defaults until M4, with field-level regime splits in the diff and
tests for the inheritance paths.

**2 (P1). The replacement counter cannot protect the diff across
the first swap.** Advancing the counter only after the last swap
leaves a window where a diff reads stored A, resumes after the
generation assignment to B, sees an unmoved mark, and composes
worlds that never coexisted. The holder must read unstable from
before the first serving-state change until after the last swap,
the diff must answer the retryable 409 on instability, and the
barrier tests must sit before the assignment, between the two
swaps, and after the install.

**3 (P1). M2 synthesizes fillers against a provider identity that
is not running.** Keying reuse by the candidate TTS identity while
synthesizing with the running voice reports a filler applied whose
voice is still restart-bound. Before M3 the reuse and synthesis
key is the running TTS binding; provider-only edits neither
invalidate nor replace clips and stay visible in the diff; M3
switches the key to the candidate identity.

**4 (P1). Provider preparation and shutdown have no complete
resource owner.** The plan covered retirement of installed
generations but not a mid-build failure (later constructions leak
the earlier ones), a shielded preparation finishing after its
caller is gone, or process shutdown of current and retired
providers; "garbage-collected candidate" is incompatible with an
explicit lifecycle. A provider-world builder owns every unique
newly constructed provider until installation transfers ownership
and closes partial or abandoned candidates exactly once, and
`Generations` gains a lifespan shutdown operation.

**5 (P1). Session generation accounting cannot attach where the
plan says.** The registry admits a `DeviceSession` before `run()`,
the runtime factory runs only after MAC validation and an awaited
bindings lookup, and many admitted sessions never construct a
runtime. One explicit generation-binding event is needed, with
removal handling admitted-but-never-bound sessions.

**6 (P1). "Byte-equivalent MCP behavior" conflicts with the
result and the no-leak claim.** `McpReloadResult` carries the
whole `servers` status document, not only the four outcome lists;
keeping it falsifies "names and closed tokens only", dropping it
breaks the one-round-trip contract and the CLI rendering. The MCP
section must be exactly the existing `McpReloadResult`, `servers`
included, and the no-leak suite must keep the reflected-metadata
sentinels and the #228-style stored-value cases.

**7 (P2). Provider build refusals have no declared HTTP
taxonomy.** `ProviderError` is not a `ConfigError` and only
`BOOT_FAILURES` handles it; an HTTP apply needs a typed refusal
with a declared 422 contract, fixed sanitized sentence, empty
chain, and OpenAPI plus no-leak pins.

**8 (P2). The filler result cannot report the degraded outcome it
promises.** The vocabulary needs a closed third outcome for an
agent whose synthesis failed, with the generation still applying,
the CLI rendering it, and the response tested directly.

**9 (P2). The #195 resolution retains a falsely-live write path
in snapshot mode.** With no engine, a device-binding or
default-agent write can still acknowledge next-check-in
convergence that snapshot mode cannot see. The plan must define
runtime-write behavior in snapshot mode and add a mounted
snapshot-mode test across write, check-in, reload, and restart.

**10 (P2). Duplicate provider and filler ownership surfaces in
`Composition`.** Widening `Generation` to own providers and
fillers while `Composition` keeps `agent_providers` and
`agent_fillers` makes two structures that must agree. The
standalone fields go or become read-through derivations with no
stored duplicate.
