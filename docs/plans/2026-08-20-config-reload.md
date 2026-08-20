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

One consequence must be said rather than inherited (round 1's
finding 9, corrected by round 2's finding 2): in snapshot mode
there is no engine behind the bindings, and the database the
mounted API creates beside them is sparse, holding only what was
written since. A reload from that store would load a
binding-and-nothing world as the complete domain half, and the
first amendment's promise that snapshot-mode writes converge at
the reload was therefore impossible as written. The honest design
is refusal, not a seeded baseline: a server composed from a
supplied snapshot has no store that describes its world, so in
snapshot mode the generalized reload and the stored-vs-running
diff both refuse with one fixed typed sentence saying exactly
that, a sibling in the closed refusal set mapped to 409. This
also closes a latent #193 edge the correction exposed: in
snapshot mode today, the diff read would compare the sparse store
against the served snapshot and claim the whole world pending,
which is the care point's failure mode in an
unreachable-in-production configuration; from milestone 1 it
refuses instead. Device and default-agent writes in snapshot mode
are acknowledged with their own fixed sentence, that the write is
stored and takes effect when a server boots from this store,
which is the one true sentence available: nothing this process
serves reads it. The mode is one fact on `ApiRuntime` beside
`loaded_agents`, chosen where `binding_notice` already chooses.
The unit lane keeps its meaning untouched: no seeding, no delta
semantics, no lane migration; the mounted snapshot-mode tests pin
the write acknowledgement, the diff refusal, and the reload
refusal.

#195 is closed when milestone 4 lands, not when the plan settles,
citing this section: the decision (snapshot mode kept, the lane
stays, the runtime surfaces refuse honestly) is made here, and M4
is where `DeviceBindings` reading the current generation makes it
real.

## The issue's open questions, and the ones the code map raises

**Where does the generation live?** A new top-level module,
`generation.py`, beside `composition.py`: a frozen `Generation`
(its serving `Config`, its `SecretStore`, and, as the milestones
widen it, the built `AgentProviders` mapping and the filler cache),
and a `Generations` holder whose `current()` is what every
convergence point reads and whose swap is a single assignment with
no await, the `McpServers._install` shape.

**What is a generation's config? An overlay, never the raw stored
snapshot** (the review's finding 1). Until milestone 4, the freshly
loaded `Config` describes a world this process must not fully
serve: `agent_defaults` and the agent set are restart-bound, and
the effective-value helpers inherit through them
(`fragments_for_agent` falls back to
`agent_defaults.prompt_includes`, the provider and filler lookups
fall back the same way), so serving the stored snapshot whole would
apply restart-bound changes by inheritance, and an activation could
index an agent the store has deleted. The prepare phase therefore
composes the candidate generation's config as an overlay: the
previous generation's config with exactly the live slices replaced
from the store. Per milestone, the live slices are: M1, the
`prompt_fragments` kind and each retained agent's own `prompt` and
`prompt_includes` fields; M2, additionally each retained agent's
own `filler` section; M3, additionally the `providers` kind (the
entries themselves; which entry an agent names remains
restart-bound); M4, everything in the domain half, at which point
the overlay becomes the identity function and retires. The overlay
covers the `SecretStore` by the same regimes (round 2's finding
1): the generation's secrets carry the freshly loaded MCP-server
secrets, that half being live, and retain the previous
generation's provider secrets until M3 installs candidate
providers, so a provider-secret rotation stays pending in the diff
through M1 and M2 applies and empties only when the M3 rebuild
actually uses the new secret, which the tests pin. The overlay
is validated whole, by the same `check_completeness` and
`check_references` every composition passes, and an apply whose
overlay does not compose refuses with the standard sentences:
deleting a fragment in the store while a restart-bound
`agent_defaults.prompt_includes` still names it is an apply that
must wait for the restart, said plainly. The diff mirrors the same
field-level regimes so an applied live-slice change reports
nothing and a restart-bound change keeps reporting: the agent
kind's reload-labeled half grows per milestone (prompt fields at
M1, the filler section at M2) beside the grants it already has,
and `agent_defaults` stays restart-labeled whole until M4. The
agent write's acknowledgement stages with the same fields (round
2's finding 8): the agent descriptor's notice becomes mixed-regime
prose per milestone (M1: prompt text and includes apply at a
reload's next activation, everything else at the next start; M2
adds the filler sentence at the next session; M4 collapses it to
the general reload notice), pinned across the API acknowledgement,
the CLI rendering, and the generated references in the same change
as each stage.

**The holder's mark, and its instability window.** The holder owns
the mark #193's diff guard reads (moved here from `McpServers`,
whose own counter becomes an internal detail or is dropped), and
the mark is not a bare counter, because an apply has more than one
swap point and a counter advanced only at the end would leave a
window in which the diff sees a moved world under an unmoved mark
(the review's finding 2). The holder reads as unstable from before
the apply's first serving-state change until after its last, and
the settled mark advances once per apply: the diff guard treats
"unstable at either sample" and "mark moved between samples"
identically, re-reading within its bound and then answering the
retryable 409 it already has. `Composition` carries the holder;
the closures that today capture the boot `Config`
(`bespoke_runtime_factory`, `_prompt_preview`,
`config_diff_reader`) capture the holder instead and read
`current()` at their existing convergence points. What the
generation comes to own, `Composition` stops owning (round 1's
finding 10, widened by round 2's finding 9): the full `config`
snapshot goes in M1, replaced by the restart-only server half the
device paths actually need (`ws.py` and the OTA paths read limits
and file-half settings; their domain reads, the manifest provider
lookups included, move to the holder or the session's bound
generation, and the grep-backed closure inventory counts
`composition.config` reads before M1 merges); the standalone
`agent_fillers` field goes in M2 and `agent_providers` in M3,
each becoming a read of the holder where a caller genuinely needs
the current world and a generation read where it needs its own,
so no stored duplicate can disagree with the world being
served.

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
The MCP section is exactly the existing `McpReloadResult`, whole
and unchanged, nested inside the new result (the review's finding
6): not only the four outcome lists but the `servers` status
document they are answered beside, because that pairing is the
route's documented one-round-trip contract and the CLI renders it.
The result is therefore not "names and closed tokens only": it
carries the MCP status document, whose own no-leak properties
(redacted reflected metadata, typed status tokens) are already
pinned and whose suites migrate whole. Other kinds get the vocabulary that is true for
them: prompts and fragments converge by re-assembly, so their
section reports the agents whose assembled know-how inputs changed;
fillers report agents re-synthesized, agents reusing clips, and
agents whose synthesis failed, a closed three-outcome set whose
third member means the generation applied with no clip for that
agent (the review's finding 8), rendered by the CLI like its
siblings;
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
synthesis actually depends on, and before milestone 3 that pair is
read from the running world on both sides (the review's finding
3): the agent's filler section from the candidate overlay, and the
identity of the agent's TTS binding as it is actually running,
because that is the voice milestone 2 synthesizes with. A
provider or provider-secret edit therefore neither invalidates nor
replaces a clip in M2: the voice it describes is still
restart-bound, the clips that exist are the running voice's, and
the change stays visible in the diff instead of being reported
applied. Once milestone 3 makes providers generational, the key's
provider half becomes the candidate generation's effective TTS
entry identity (entry model plus secret fingerprint, the
comparison #193 built) and synthesis uses the candidate's
provider. A
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
their pools; faster-whisper and Piper drop their held engine and
voice references, with the honest caveat recorded that a library
like CTranslate2 frees memory on its own schedule, so release is
best-effort and double residency during a swap is the accepted
cost either way. `SileroVad` keeps the default no-op, corrected
by the review (round 2's finding 11): it holds tuning values
only, and each session's endpointer owns its detector and is gone
with the session before any generation disposal, which its
docstring says. (b) Generational binding, with one explicit
binding event (the review's finding 5): the registry admits a
`DeviceSession` before `run()`, the runtime is constructed only
after MAC validation and the awaited bindings lookup, and many
admitted sessions never construct one, so "the factory hands out
the generation" is not by itself an accounting point. The runtime
factory reads `current()` when it builds the runtime, and the
session reports that binding to the registry in the same
synchronous step, so admission and binding are two recorded
states: `remove` releases the generation for a bound session and
is a no-op on that axis for an admitted-but-never-bound one. From
M4 the handoff is pinned end to end (round 2's finding 4): the
session captures one generation on the event loop after the
awaited bindings resolve, filters the resolved agent list against
that exact generation, and passes the same object into runtime
construction and registry accounting, so a reload that deletes an
agent between the binding read and the factory call cannot
produce a list the serving generation cannot serve; the barrier
test that forces exactly that interleaving is named in M4's
tests. The
`SessionRegistry`, the only object that knows the whole session
set, counts bound sessions per generation and disposes a
generation that is neither current nor held by any bound session,
immediately when it retires with none. (c) Unchanged-entry reuse: the prepare phase
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

Ownership is total, not best-effort (round 1's finding 4, made
airtight by round 2's finding 6), and it begins the instant an
allocation succeeds, not when a finished provider is handed over:
option validation runs to completion before anything is
constructed, so a trailing unknown option can no longer refuse
after a model loaded, and the post-construction egress check runs
inside the owner that already holds the object, so an egress
refusal closes what it just built; the same ordering applies to
boot construction, which shares the builder. From there the
provider-world builder owns every unique provider it constructs
until installation transfers ownership to the new generation, and
on any exit that is not an install (a later entry's build fails, a
shielded preparation finishes after its caller has gone, the apply
is refused downstream) it closes every constructed-and-unadopted
provider exactly once; nothing is left to the garbage collector,
which an explicit lifecycle has just declared insufficient.
`Generations` also owns process shutdown: the lifespan registers a
close operation on the exit stack that, after sessions have
drained, closes the current generation's providers and any retired
generation still held, so the close a provider gained in this
milestone runs at every end a provider can meet.

Teardown never refuses (round 2's finding 7): a raising `close()`
cannot fail an apply whose serving state already changed, strand
the holder unstable, or put third-party prose anywhere. Disposal
awaits its cleanup within a bound, classifies a failure by
exception class only with the prose suppressed, and the apply
always finishes its install, settles the mark, and releases the
exclusion whatever teardown did; the close-raises sentinel test
proves the apply succeeds and neither the response nor the logs
carry the planted text.

A provider refusal also needs an HTTP identity it does not have
today (the review's finding 7): `ProviderError` is not a
`ConfigError`, and only the boot-failure handler knows it. The
apply translates provider preparation failures into one typed
refusal in the configuration vocabulary, mapped to 422 in
`REFUSAL_STATUS`, answering with a fixed sanitized sentence built
in the except arm and raised after it (chains cut, the #228
shape), carried in the OpenAPI document's declared problem
statuses and pinned by the no-leak suite; the original failure's
class is what the log records, never the response.

**The agent set: what moves in milestone 4?** The four snapshots
of "which agents can this server serve" move onto the generation:
`ApiRuntime.loaded_agents` (which chooses between the binding
notice and the restart notice), `McpServers`' servable-agent set
(whose "read once, never replaced" contract #193 documented in
three places, all rewritten here), `DeviceBindings`' loaded set
and fallback configuration (the #195 decision above), and the
prompt preview's 404 branch. A new agent is part of the next
generation and servable from the swap; a deleted agent's live
sessions finish on the generation they bound, and the activation
rule makes that survivable at every activation, not only across
utterances (round 2's finding 5): `_activate_agent` reads the
current generation when it holds the agent and falls back to the
session's bound generation when it does not, so a mid-session
handover to a deleted-but-session-bound agent keeps that agent's
last-served prompt world instead of indexing a missing entry.
Before M4 the fallback is unreachable, because the overlay
retains every previous agent; the rule is stated in M1's
docstring and becomes load-bearing at M4, tested with a live
multi-agent session switching to the deleted agent after the
apply. The per-session
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
  after an apply (the free-win claim, pinned); the overlay's
  inheritance paths: an `agent_defaults.prompt_includes` edit does
  not reach an inheriting agent's activation and stays pending in
  the diff, an agent's own prompt edit does and empties, an agent
  added or deleted in the store changes no activation before M4,
  and an overlay that no longer composes (fragment deleted, a
  restart-bound reference remains) refuses the apply whole;
  integration: edit a fragment, apply, same socket, next
  activation speaks the new text; the diff empties for the prompt
  half after apply.
- **M2**: clip reuse pinned by object identity across an apply
  that edits only a prompt; a phrase edit re-synthesizes only that
  agent; a provider-only or provider-secret-only edit preserves
  clip identity and stays pending in the diff (finding 3's case);
  synthesis failure applies the generation with no clip and the
  response names the agent under the disabled outcome, asserted on
  the response body itself and in the CLI rendering, not only on
  the existing event (finding 8); a session's filler timing does
  not change mid-session.
- **M3**: unchanged-entry reuse pinned by provider object identity
  (a prompt-only apply reuses every provider); a changed entry
  builds anew and the old provider closes when the last bound
  session ends, pinned with the registry hook and a fake provider
  whose `close()` records; a shared reused instance is not closed
  while any generation holds it; egress refusal in prepare touches
  nothing running (the running generation's objects are identical
  after a refused apply); finding 4's ownership cases: a
  later-entry build failure closes the earlier constructions
  exactly once, a preparation cancelled by its caller still closes
  its candidates, a generation retired with zero sessions disposes
  immediately, a provider reused across several generations closes
  only when the last of them lets go, and application shutdown
  closes current and retired providers after the drain; finding
  6's same-entry cases: a trailing unknown option refuses before
  anything is constructed, and an egress refusal after
  construction closes the object it just built; finding 11's
  concrete teardowns, proven on the real classes and not only the
  fake: an injected HTTP client and an injected SDK client are
  closed by their providers' `close()`, and faster-whisper and
  Piper release their held engine and voice references; local-model
  double residency is not asserted numerically, but the
  close-called-once property is; finding 5's binding-event cases:
  a rejected device id, a device with no binding, a disconnect
  before hello (admitted, never bound, removed cleanly), and a
  reload between admission and runtime construction (the session
  binds the generation the factory read, and the count follows
  it).
- **M4**: bind a device to an agent added by apply and see it
  served at the next check-in with the binding notice, no restart;
  delete an agent with a live session, apply, the session finishes
  and new sessions cannot reach it; the diff reports nothing for
  an applied agent-set change; the pinned-handoff barrier test
  (finding 4): a reload deletes the resolved agent between the
  binding read and the factory call, and the session either serves
  the generation it captured or is turned away cleanly, never an
  index error; the snapshot-mode acknowledgement, diff refusal,
  and reload refusal from the #195 section.
- **Concurrency, every milestone**: the widened exclusion refuses
  a second apply; the #193 barrier test moves onto the holder's
  mark and gains the three positions finding 2 names: the barrier
  before the generation assignment, between the assignment and the
  MCP install, and after the install, each answering one world or
  the retryable 409, never a mixture.
- **No-leak**: the new sections carry entry and agent names and
  closed outcome tokens; the MCP section carries the status
  document whose reflected-metadata sentinels migrate whole with
  the route. The sentinel pattern from #193's route reruns against
  the generalized endpoint in M1 with the #228 lesson's full
  breadth: an invalid stored scalar (the stored-value case, not
  only the wrong key), both running and stored ciphertexts and
  fingerprints, an environment-reference name, asserted absent
  from response bodies and log records, with empty exception
  chains on the refusal paths.

## The standing review lenses, pre-answered

**No-leak.** The prepare phase refuses through the same typed
errors the boot and the MCP reload refuse through, with the #228
lesson applied from the start: stored-side refusal sentences that
can embed stored bytes are replaced at the endpoint boundary with
fixed sentences, types preserved, chains cut. The new sections
carry names and closed tokens; the nested MCP section carries the
status document, whose own pinned no-leak properties travel with
it, which is the one standing exception to the names-and-tokens
sentence.

**Pin before reshaping.** The MCP move has exactly three
intentional transport deltas, listed so nothing else may change
(round 2's finding 10): the path, the success body's nesting
under the new result's MCP section, and the stored-side 422
detail becoming a fixed sentence. Everything else is invariant
and pinned as such: the serialized MCP section of a successful
generalized reload equals the former route's successful body, the
lifecycle outcomes and status document unchanged, the status
codes unchanged, proven by the migrated transport suite plus one
equality test over the nested section, byte-pinned through the
OpenAPI document. The activation path's behavior for a server
that never applies is pinned by the existing suites, which must
pass unmodified in M1 except where they name the retired route or
the moved counter.

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
  rewire, doc sweep). Mitigation: the MCP move is bounded by the
  three named deltas with the nested-equality pin as the proof;
  the
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
  the old world plus a candidate the builder has already closed.
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

*Resolution.* Adopted. The new "What is a generation's config"
decision defines the overlay, its per-milestone live slices, its
whole re-validation with the honest refusal for slice
interactions, its retirement at M4, and the matching field-level
regimes in the diff; the M1 test bullet names the inheritance,
added-agent, deleted-agent, and overlay-refusal cases.

**2 (P1). The replacement counter cannot protect the diff across
the first swap.** Advancing the counter only after the last swap
leaves a window where a diff reads stored A, resumes after the
generation assignment to B, sees an unmoved mark, and composes
worlds that never coexisted. The holder must read unstable from
before the first serving-state change until after the last swap,
the diff must answer the retryable 409 on instability, and the
barrier tests must sit before the assignment, between the two
swaps, and after the install.

*Resolution.* Adopted. The holder's mark is now defined with an
instability window spanning first to last swap point, the diff
guard treats instability and movement identically, and the
concurrency bullet names the three barrier positions.

**3 (P1). M2 synthesizes fillers against a provider identity that
is not running.** Keying reuse by the candidate TTS identity while
synthesizing with the running voice reports a filler applied whose
voice is still restart-bound. Before M3 the reuse and synthesis
key is the running TTS binding; provider-only edits neither
invalidate nor replace clips and stay visible in the diff; M3
switches the key to the candidate identity.

*Resolution.* Adopted. The fillers decision now keys reuse and
synthesis by the running TTS binding before M3, provider edits
neither invalidate nor replace clips and stay pending in the diff,
the M2 test bullet carries the named case, and M3 moves the key to
the candidate identity.

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

*Resolution.* Adopted. The providers decision now states total
ownership (builder owns until install transfers; every non-install
exit closes constructed-and-unadopted providers exactly once;
nothing left to the garbage collector), `Generations` owns the
exit-stack shutdown after the drain, the risk bullet no longer
speaks of a garbage-collected candidate, and the M3 test bullet
names all five ownership cases.

**5 (P1). Session generation accounting cannot attach where the
plan says.** The registry admits a `DeviceSession` before `run()`,
the runtime factory runs only after MAC validation and an awaited
bindings lookup, and many admitted sessions never construct a
runtime. One explicit generation-binding event is needed, with
removal handling admitted-but-never-bound sessions.

*Resolution.* Adopted, the second offered shape: the session
reports its binding to the registry in the same synchronous step
the factory reads `current()`, admission and binding are two
recorded states, `remove` handles the never-bound session, and the
M3 test bullet names the four cases (bad device id, no binding,
disconnect before hello, reload between admission and
construction).

**6 (P1). "Byte-equivalent MCP behavior" conflicts with the
result and the no-leak claim.** `McpReloadResult` carries the
whole `servers` status document, not only the four outcome lists;
keeping it falsifies "names and closed tokens only", dropping it
breaks the one-round-trip contract and the CLI rendering. The MCP
section must be exactly the existing `McpReloadResult`, `servers`
included, and the no-leak suite must keep the reflected-metadata
sentinels and the #228-style stored-value cases.

*Resolution.* Adopted. The result decision now nests the existing
`McpReloadResult` whole, `servers` included, and withdraws the
"names and closed tokens only" claim for the result as a whole;
the no-leak bullet carries the full #228 breadth including the
invalid-stored-scalar case.

**7 (P2). Provider build refusals have no declared HTTP
taxonomy.** `ProviderError` is not a `ConfigError` and only
`BOOT_FAILURES` handles it; an HTTP apply needs a typed refusal
with a declared 422 contract, fixed sanitized sentence, empty
chain, and OpenAPI plus no-leak pins.

*Resolution.* Adopted. The providers decision now declares the
typed 422 refusal with its fixed sanitized sentence, cut chains,
OpenAPI declaration and no-leak pins, the original class recorded
in the log and never the response.

**8 (P2). The filler result cannot report the degraded outcome it
promises.** The vocabulary needs a closed third outcome for an
agent whose synthesis failed, with the generation still applying,
the CLI rendering it, and the response tested directly.

*Resolution.* Adopted. The result vocabulary is a closed
three-outcome set whose third member means the generation applied
with no clip for that agent, the CLI renders it, and the M2 test
bullet asserts the response body and the rendering directly.

**9 (P2). The #195 resolution retains a falsely-live write path
in snapshot mode.** With no engine, a device-binding or
default-agent write can still acknowledge next-check-in
convergence that snapshot mode cannot see. The plan must define
runtime-write behavior in snapshot mode and add a mounted
snapshot-mode test across write, check-in, reload, and restart.

*Resolution.* Adopted, the third offered shape: snapshot-mode
device and default-agent writes acknowledge the reload notice,
which under this plan is exactly when they take effect; the mode
is one fact on `ApiRuntime` chosen where `binding_notice` chooses,
the diff's `check-in` label follows the same fact in M4, and the
mounted snapshot-mode loop test is named in the #195 section.
(Superseded: round 2's finding 2 showed this shape impossible;
see its resolution.)

**10 (P2). Duplicate provider and filler ownership surfaces in
`Composition`.** Widening `Generation` to own providers and
fillers while `Composition` keeps `agent_providers` and
`agent_fillers` makes two structures that must agree. The
standalone fields go or become read-through derivations with no
stored duplicate.

*Resolution.* Adopted. The holder decision now states that what
the generation comes to own `Composition` stops owning:
`agent_fillers` goes in M2 and `agent_providers` in M3, with
callers moved to the holder or their bound generation.

## Plan review round 2 (2026-08-20)

External review: codex exec, model gpt-5.6-sol, read-only against
commit e0017a68, judging whether round 1's resolutions close their
findings. Verdict: not ready; a third round runs after these
amendments. Findings condensed but faithful; each carries its
resolution.

**1 (P1). The staged overlay omits the secret half of provider
identity.** `Generation` receives a `SecretStore`, the diff
compares provider secrets through the running side's fingerprint,
and installing freshly loaded secrets in M1 would report a
provider-secret rotation applied while the provider is
restart-bound. Provider-secret state must follow the provider
regime: previous identities through M1 and M2, candidate secrets
at M3, with the rotation-stays-pending tests. An unclosed part of
round 1's finding 1.

*Resolution.* Adopted. The overlay decision now covers the
`SecretStore` by the same regimes: fresh MCP-server secrets,
previous provider secrets until M3, with the
rotation-pending-through-M1-and-M2 test named.

**2 (P1). The snapshot-mode resolution cannot reload from the
sparse database it creates.** Snapshot mode is chosen before the
mounted API creates its database, so a binding-only write creates
a store holding that binding and nothing else; a reload would load
it as the complete domain half and either fail validation or
replace the in-memory world. The promised write-reload-converge
loop is impossible without a seeded baseline or delta semantics,
and #195 must not close on it. Round 1's finding 9 unresolved.

*Resolution.* Adopted, by refusal rather than a baseline: in
snapshot mode the generalized reload and the diff both refuse with
one fixed typed 409 sentence (there is no store describing this
server's world), snapshot-mode device and default-agent writes
acknowledge that the write is stored and takes effect when a
server boots from this store, no seeding and no lane migration,
and #195 closes at M4 rather than at plan settle. The #195
section records the latent #193 snapshot-mode diff edge this
fixes.

**3 (P1). The snapshot-mode diff cannot represent the pending
bindings round 1's resolution introduced.** `LiveKind` has no
comparison payload, so a snapshot-mode write pending a reload has
no honest response shape under the published schema.

*Resolution.* Resolved by finding 2's shape: snapshot mode has no
pending-bindings answer to represent because the diff refuses
there; the published schema is untouched and the label-only live
regime remains the database mode's answer.

**4 (P1). Agent binding and generation binding race at M4.** The
session resolves bindings, then separately the factory reads
`current()`; a reload deleting an agent between them produces a
resolved list the factory's generation cannot serve. One pinned
handoff is needed: capture one generation on the loop, filter
against it, and pass that exact generation to construction and
accounting, with a barrier test.

*Resolution.* Adopted, the pinned-handoff shape: one generation
captured on the loop after the bindings resolve, the filter, the
construction, and the accounting all take that exact object, and
M4's tests carry the deletion-interleaving barrier case.

**5 (P1). A deleted agent cannot survive a later activation as
promised.** `_activate_agent` reads `current()` from M1 and also
serves mid-session handovers; after an M4 deletion the current
world has no such agent and a switch to it would index a missing
entry. The deletion rule must be explicit: current configuration
when the agent exists there, the session's bound generation when
it does not, tested with a live switch to a
deleted-but-session-bound agent.

*Resolution.* Adopted, exactly that rule: the current generation
when it holds the agent, the session's bound generation when it
does not, unreachable before M4 because the overlay retains every
agent, with the mid-session switch test named in the agent-set
decision.

**6 (P1). Total ownership still misses failures inside one
provider build.** A factory can allocate before options validation
finishes, and `build_provider` can allocate then refuse at the
egress check; in both cases the object never reaches the world
builder. Ownership must begin the instant allocation succeeds:
options validated before construction, post-construction checks
run by an owner already holding the object, applied to boot too,
with same-entry tests for a trailing unknown option and an egress
refusal after construction. Round 1's finding 4 not fully closed.

*Resolution.* Adopted. Ownership now begins at allocation: options
validate before construction, the egress check runs inside the
owner already holding the object, the boot path shares the
builder, and the two same-entry test cases are named in M3's
bullet.

**7 (P2). Provider close failures have no safe post-swap policy.**
Disposal can run after the generation assignment; a raising
`close()` must not fail the apply, strand the mark unstable, or
leak third-party prose. Teardown is non-refusing and bounded,
failures classified by class only, and the apply always finishes
install, settlement, and release, with a close-raises-sentinel
test.

*Resolution.* Adopted verbatim: the teardown-never-refuses
paragraph in the providers decision states the bound, the
class-only classification, the guaranteed
install-settle-release, and the sentinel test.

**8 (P2). M1 and M2 leave agent-write acknowledgements falsely
restart-only.** An agent write carries the now-live `prompt`,
`prompt_includes`, and later `filler` fields while the descriptor
keeps the restart notice. The agent notice becomes staged
mixed-regime prose per milestone, collapsed at M4, pinned across
API, CLI, and the generated references.

*Resolution.* Adopted. The overlay decision now stages the agent
notice with the fields, milestone by milestone, collapsed at M4,
pinned across all three surfaces in the same change as each
stage.

**9 (P2). `Composition.config` remains a second, stale serving
configuration.** Removing only the provider and filler fields
leaves a public full `Config` that disagrees with the generation
after M1. The full domain snapshot leaves `Composition`; callers
keep the restart-only server half and read the domain through the
holder or their bound generation; `composition.config` reads join
the grep-backed closure inventory.

*Resolution.* Adopted. The holder decision now removes the full
`config` snapshot from `Composition` in M1, keeps the restart-only
server half for the device paths, moves domain reads to the holder
or the bound generation, and adds `composition.config` reads to
the pre-M1 closure inventory.

**10 (P2). The byte-equivalent MCP claim contradicts the
intentional transport deltas.** The path, the nesting, and the
sanitized 422 detail all change by design. The plan must list the
deltas and pin the invariant that the nested MCP section equals
the former successful result, keeping the MCP-status exception in
the no-leak section. An incomplete resolution of round 1's
finding 6.

*Resolution.* Adopted. The pin-before-reshaping lens now lists
the three intentional deltas (path, nesting, sanitized 422
detail) and pins the nested-section equality with unchanged
outcomes, status document, and status codes; the no-leak lens
keeps the MCP-status exception explicitly; the M1 risk bullet
stops claiming a verbatim move.

**11 (P3). The provider tests do not prove the concrete
teardowns, and Silero is misdescribed.** A fake-provider suite
passes if every real provider inherits the no-op; injected HTTP
and SDK clients must be proven closed and faster-whisper and
Piper proven to release their engines, while `SileroVad` holds
only tuning values (each session's endpointer owns its detector)
and keeps the default no-op, documented.

*Resolution.* Adopted. The lifecycle decision corrects Silero to
the default no-op with the session-owned-detector fact in its
docstring, and M3's tests prove the injected HTTP and SDK clients
close and that faster-whisper and Piper release their references,
on the real classes.

## Plan review round 3 (2026-08-20)

External review: codex exec, model gpt-5.6-sol, read-only against
commit 8a6273d6, judging whether round 2's resolutions close their
findings. Verdict: ready after the P1/P2 amendments, which follow
with their resolutions.

**1 (P1). Provider disposal can race still-running worker-thread
calls.** Cancelling an awaiting coroutine does not stop the
`asyncio.to_thread` worker inside a transcription or synthesis
call, so removal-triggered disposal can clear an engine a worker
is about to read. Provider calls need an operation lease held
until the underlying thread actually finishes, disposal waits for
the leases, and a blocking-engine test proves the engine stays
reachable until the worker exits.

**2 (P1). The snapshot-mode resolution leaves M1 through M3
unreleasable.** The refusals were promised "from milestone 1" but
scheduled nowhere before M4, and every milestone publishes. The
mode fact, both typed refusals, the snapshot-mode
acknowledgements, the OpenAPI changes, and the mounted tests move
into M1; M4 keeps the `DeviceBindings` conversion and the #195
closure. A failed closure of round 2's finding 2.

**3 (P1). The session-to-generation handoff has no implementable
interface.** The factory returns only a `SessionInput`, the
session has no registry collaborator, and `DeviceBindings`
classifies names against its own snapshot before returning. The
interface must be exact: raw bound and default names come back
unclassified, the session captures one generation after the
await, classifies against it, constructs from the same object,
and synchronously registers its lease before yielding, with the
release owned explicitly and both an addition and a deletion
tested at the barrier. Round 2's finding 4 partially resolved.

**4 (P1). Immediate ownership is not achievable through the
current provider builder.** `build_provider` constructs then
egress-checks inside one synchronous function under one
`to_thread` call, which cannot await an async close on refusal.
M3 names the refactor: options parsed and finished before
allocation, one provider constructed at a time off-loop and
transferred immediately into an async candidate owner, the egress
check run after the transfer, the owner awaiting cleanup on every
non-install exit, boot and reload sharing the path. Round 2's
finding 6 not closed by prose alone.

**5 (P2). The staged secret overlay has no safe `SecretStore`
operation.** Envelopes and keys are deliberately private, so the
overlay as written would need reach-in or exposure. `SecretStore`
gains a deep derivation that composes two stores by entity-kind
regime internally, returning none of envelopes, keys, plaintext,
or fingerprints, named in M1 and tested for provider rotation
pending through M1 and M2, MCP rotation applying in M1, and
planted envelope bytes absent everywhere.

**6 (P2). The reload response is neither additive nor complete
across milestones.** The API models forbid extras, so gaining
fields is a breaking change for a generated client, and M4's
vocabulary was undefined. The final `ConfigReloadResult` schema
publishes in M1 with the later sections declared optional and
null until their milestone implements them; the M4 outcomes are
defined now (agents added and removed by the apply, and whether
`agent_defaults` changed), and device and default-agent writes
are explicitly not reload outcomes, being check-in live.

**7 (P2). The disconnect-before-hello test asserts the opposite
of the control flow.** The runtime is constructed, and binds,
before `_receive_hello`, so that case is bound, not never-bound.
It is reclassified; the never-bound cases are an invalid MAC, no
binding, and cancellation during binding resolution; and a test
proves the pre-hello runtime's generation is released although
the conversation cleanup block never ran.
