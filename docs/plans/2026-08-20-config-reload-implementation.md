# Apply domain configuration without a restart: implementation

Companion to [`2026-08-20-config-reload.md`](2026-08-20-config-reload.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the generation seam and the generalized reload, with the prompt slice live

### What was done

**The seam.** `vinga-server/src/vinga_server/generation.py` is the new
top-level module beside `composition.py`. `Generation` is a frozen pair
of the configuration to serve and the `SecretStore` loaded with it;
`Generations` is the holder, with `current()` for every convergence
point, `mark` for anything composing an answer across an await, and
`applying()` for the one door a swap goes through. The swap is yielded
by the context manager rather than offered as a method, which makes
"nothing is installed outside an apply's instability window" a property
of the type rather than a rule to remember. `mark` answers `None` for
the whole of an apply and a settled count otherwise, so a reader
compares `is not None` as well as equality: two unstable samples are two
moments inside two different applies, not one steady world.

**The secret half.** `SecretStore.composed(previous, live)` is the deep
two-store derivation. It takes the previous generation's store and the
entity kinds whose stored credentials this server actually applies, and
returns a store; nothing else crosses the boundary, which is the whole
reason it is a method. `LIVE_SECRETS` in `config/reload.py` holds the
regime as data, and for this milestone it is `{"mcp_server"}`: an MCP
server's credentials are read as its connection is made and a reload
makes it again, while a provider's are read as the provider is built,
which is still the start.

**The apply.** `config/reload.py` holds `ConfigReload`: the exclusion
(one at a time, refused rather than queued, held past a cancelled
caller), the shielded prepare and apply phases, and the composed result.
Prepare re-reads the stored half in a worker thread, composes the
candidate generation as an overlay, validates it whole, and asks the MCP
package for its candidate. Apply enters the holder's window, installs
the generation, and runs the MCP install inside the same window, so the
mark settles once for both swaps.

The overlay is the previous generation's configuration with the
`prompt_fragments` kind replaced from the store and, for each agent both
worlds hold, that agent's own `prompt` and `prompt_includes`. Everything
else is the previous generation's, `agent_defaults` and the agent set
included, because the effective-value helpers inherit through exactly
those. It is validated by `check_completeness` and `check_references`,
the two functions `Config`'s own validator calls, so a combination of
applied and start-bound slices that does not add up is refused whole:
deleting a fragment that a start-bound `agent_defaults.prompt_includes`
still names is the case, and it refuses in the sentence a boot would
print.

**The MCP package.** `tools/mcp/reload.py` keeps its two phases and
loses everything around them. `prepare(config, secrets)` composes the
slice and builds the managers; `apply(servers, candidate, began)` does
the diff, the lifecycles, the swap and the status read with no await
between the last two, and returns the whole `McpReloadResult`. The
exclusion, the shields, the task ownership and the re-read moved to
`ConfigReload`, and `McpServers` lost `_reloading`, `_applying`,
`_preparing`, `_hold_until`, `_release`, `reloading`, `reload`,
`reload_result` and `generation` with them. The `mcp_reload` event is
unchanged and is emitted by `refused(exc)` and by the apply, which is
what keeps `docs/reference/events.md` byte-identical.

**The route.** `POST /runtime/config/reload` replaces
`POST /runtime/mcp-servers/reload`, which is removed rather than
aliased. `ApiRuntime.mcp_reload` became `ApiRuntime.reload`, typed
`ConfigReloader`. The CLI's `reload` subcommand retargets and renders
the new result. The three intentional transport deltas are the path, the
nesting of the former body under the result's `mcp` section, and the
fixed sentence a refused stored half now carries; the nesting is pinned
by an equality test against a `McpReloadResult` built by hand, and the
rest of the contract by the migrated transport suite.

**The result.** `ConfigReloadResult` in `config/responses.py` publishes
the whole schema at once: `mcp` is the existing `McpReloadResult` nested
whole, `prompts` reports the agents whose own prompt or resolved
fragments moved, and `fillers`, `providers` and `agents` are declared
and answer null until their milestones. `outcomes()` and `flags()` read
a section's shape off its model and `RELOAD_SECTIONS` reads the sections
off the result, so the CLI renders what the models declare and a field
shaped like neither fails a test rather than disappearing from the
output.

**The prompt slice live.** `_activate_agent` and `_prompt_preview` read
`current()`, and the comparison read's running side is the holder rather
than a captured snapshot. The diff's agent kind grew `prompt` beside
`grants`, both labelled `reload`, the restart-bound agent comparison
excludes `prompt` and `prompt_includes` as well as `mcp`, and
`prompt_fragments` flipped to `reload` in the regime map.

**The composition.** `Composition.config` is gone. The file half is
`Composition.server` and the domain half is `Composition.generations`.
The inventory below records every reader and where it went.

**Snapshot mode.** `DeviceBindings.snapshot_authoritative` is the one
place the mode is decided (it already was, at the open), `ApiRuntime`
carries it as `snapshot_only`, and the two surfaces that span both sides
refuse with `SnapshotOnlyError` and one fixed sentence mapped to 409.
Device and default-agent writes answer `SNAPSHOT_NOTICE`.

**Notices and prose.** `MCP_RELOAD_NOTICE` became `RELOAD_NOTICE` and
covers the prompt fragments as well as the MCP entries; `AGENT_NOTICE`
is the new mixed-regime sentence; `SNAPSHOT_NOTICE` is the fourth. The
`prompt_includes` field description, the docgen contract prose, the
`API_DESCRIPTION` paragraphs and the two READMEs were rewritten, and
both generated references were regenerated in the same change.

### The composition-config reader inventory

Taken by `git grep -n "composition\.config\|comp\.config"` against the
commit this milestone started from, which found **11 sites**: three in
`src/` and eight in the suites. Every one is accounted for.

| Site | Read | Disposition |
| --- | --- | --- |
| `src/vinga_server/ota/reply.py:141` (`check_version`) | `server.timezone_offset_minutes`, `server.protocol_version`, `websocket_url_for`, `_activation` | `comp.server`; `websocket_url_for` and `timezone_offset_minutes` now take a `ServerConfig` |
| `src/vinga_server/ota/reply.py:346` (`describe`) | `server.protocol_version`, `websocket_url_for`, `portal_url_line` | `comp.server` |
| `src/vinga_server/ws.py:144` | the whole `Config`, handed to `DeviceSession` | `comp.generations`; the session takes the holder |
| `tests/integration/test_activation.py:92,145,160` | `.config.server` for the onboarding key | `.composition.server` |
| `tests/integration/test_config_api.py:101,103` | `agents_for_device`, `prompt_for_agent` | `.composition.generations.current().config` |
| `tests/unit/test_app.py:13,25` | identity of the whole configuration | split: `.server is config.server` and `.generations.current().config is config` |
| `tests/unit/test_onboarding_activation.py:60` | `.config.server` for the onboarding key | `.composition.server` |

The four config-holding closures and factories the plan also names were
converted in the same change: `bespoke_runtime_factory`,
`_prompt_preview`, `config_diff_reader` and the reload closure (which
`_mcp_reloader` became) all take the holder now, and
`PipelineRuntime.__init__` and `DeviceSession.__init__` take it instead
of a `Config`. Each of the two session-side classes keeps
`generations.current().config.server` in a field at construction, which
is honest because a generation never replaces the file half: a reload
composes the stored domain half onto this process's own server section,
so every generation carries the same one.

### Deviations from the plan

Seven, each recorded because it moved something the plan named.

**1. The MCP reload's exclusion did not widen; it moved.** The plan says
the exclusion "widens to the whole generalized reload", subsuming
`McpServers._reloading`. Implemented as a move rather than a widening:
the flag, the two held tasks, `_hold_until` and `_release` are now
`ConfigReload`'s, and `McpServers` has none of them. Keeping a second
copy on the registry would have been two structures that must agree, and
the registry's own reload entry points had no caller left once the route
was the generalized one. The cost is real and is the next deviation.

**2. `test_tools_mcp_reload.py` drives the generalized apply.** It was
the MCP reload's own suite and called `servers.reload(read)` at about
forty sites. Since that method is gone, the file now drives
`ConfigReload` through a support helper, `tests/support/tools_mcp.py`'s
`Applying`, which holds the stored half so a test can move it between
calls and shares one apply object across the calls the exclusion cases
need. Nothing about what the suite asserts changed except the shape:
`applied` is now the result's `mcp` section, and the four outcomes are
lists rather than tuples because the section is the API model rather
than the internal dataclass. `McpReload`, the internal four-tuple, is
gone with the only function that returned it.

**3. The reload's stored-side refusals are sanitized, and the
comparison's prose changed with them.** The plan's third transport delta
is "the stored-side 422 detail becoming a fixed sentence", and the
composition-root closure mirrors the comparison read's shape exactly:
three fixed sentences, the store's types preserved, chains cut. The
consequence the plan did not state is that `DIFF_REFUSED` could no
longer point at `vinga-server config reload` as the place that names the
location, because it no longer does. Both sentences now point at a
server started from the same store, which refuses on the same state and
prints the location it refused on, and that is the one place the
location is still available. `ReloadInProgressError` passes through as
itself: it is about this server's own exclusion and was composed over
nothing stored.

**4. The `mcp_reload` event stayed the MCP package's, and is emitted
from outside it.** One reload is one event, and after the move the
refusals a reload can end at are mostly the generalized phase's: the
re-read, the overlay composition and the whole-snapshot validation are
all outside `tools/mcp/`. Renaming the event would have moved
`docs/reference/events.md`, which this milestone must leave byte-
identical, so `tools/mcp/reload.py` exposes `refused(exc)` and
`config/reload.py` calls it. The event's name is a committed surface and
widening it is its own change.

**5. `DeviceBindings`' mode reads as `snapshot_authoritative`, not
`snapshot_only`.** The class already had a `snapshot_only` classmethod
constructor, so a property of that name would have been shadowed by a
bound method that is always truthy, which is exactly the bug the first
draft had. The property is named for what it asserts, which is that the
snapshot is authoritative here; `ApiRuntime` keeps `snapshot_only`,
which is what the API-side fact is called.

**6. The prompts section counts an agent's own inputs, not every input
of an assembly.** The plan says the section "reports the agents whose
assembled know-how inputs changed". An assembly has three inputs and the
third is the MCP guidance, which the `mcp` section already reports entry
by entry, with the connection-versus-text distinction that section
exists to make. Counting it twice would have had one apply report the
same change in two vocabularies. The field's description says so.

**7. The comparison's `prompt` half compares only agents both sides
hold.** The plan mirrors the grants rule, which compares every agent of
the running generation. The two are not the same question: a reload
revokes a deleted agent's grants immediately, so its pending revocation
is real, while a reload keeps a deleted agent's prompt exactly as it is,
so there is nothing pending to report. An agent only one side holds
rides the added or removed lists instead.

### Discoveries

**The MCP half is not overlaid, and that is what keeps it byte-for-byte
the same.** The overlay governs what the generation serves, which is the
prompt world; the MCP candidate is still composed from the freshly read
stored configuration, exactly as it always was. Once that separation was
written down the whole MCP suite passed unchanged apart from the shape
of the value it reads.

**The instability window cannot be tested by holding a manager open.**
The barrier position between the two swaps is a state of the holder, and
the honest way to put a reader in front of it is to enter the holder's
own window, which is what the apply does. Driving a slow manager to
reach the same state would be asserting on the manager.

**Three reads, not two.** The comparison read's retry bound is exactly
three, and a reader that meets the window spends all three: its second
sample is unstable, and the attempt after it starts inside the window
too, so its first sample is unstable as well. The third runs in a world
that is holding still. That is the bound doing what it is for rather
than a coincidence, and the test asserts `read.reads == DIFF_LOADS`.

**`model_copy` plus the two checks is the honest re-validation.** Every
value in an overlay came out of a model that has already been validated
field by field, so what is left to check is the whole. Running
`check_completeness` and `check_references` is what `Config`'s own
validator does, and it avoids a dump-and-revalidate round trip whose
failure modes would have been the round trip's rather than the
configuration's.

### Verification

Run from `vinga-server/`, at the last commit of the milestone.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files. Its scope is
  the events package, which this milestone does not touch.
- `uv run pytest tests/unit -q`: 2,735 passed, 16 skipped. (2,692 at the
  end of #193's review round; the 43 new cases are the holder's 7, the
  secret derivation's 5, the apply's 19, snapshot mode's 5, and the
  seven added to existing suites for the nested-section equality, the
  reload's own 409 description, the snapshot refusal at the route, the
  fragment kind's regime, the include half of an agent entry, and the
  barrier position between an apply's two swaps.)
- `uv run pytest tests/integration -q`: 61 passed, the same count as
  #193 left: the two cases this milestone changes were already there and
  were rewritten rather than added to.
- The four documentation drift checks, regenerated and diffed against
  `../docs/reference/`: all four clean. `api-openapi.json` and
  `domain-config.md` change deliberately and are committed in this
  milestone; `events.md` and `conversations-schema.md` are byte-untouched
  and are absent from this milestone's commits, which is what says no
  event and no conversation column moved.

Not verified here, and not claimed: the container image, the smoke lane,
and anything against a real device, none of which this milestone
touches.

### PR review round (2026-08-20)

External review of PR #229: codex exec, model gpt-5.6-sol, read-only
against `main...f1fce4ba`. Verdict: mergeable after fixes. Three
findings, all P2, condensed but faithful; each carries its resolution
and the commit that made it.

**1 (P2). The agent write's notice put the MCP grants on the wrong side
of the line.** `AGENT_NOTICE` named `prompt` and `prompt_includes` as
the half a reload applies and swept the rest into "everything else",
while the reload applies an agent's effective `mcp` grants too: the
candidate slice derives them from the stored configuration, the route's
own description says so, and the comparison read has reported them under
a `reload` label since #193. An operator acting on the sentence would
restart a server for a grant edit a request applies.

*Resolution.* Adopted (`0207941a`). The sentence names all three fields
and states the two clocks the applied half has, since prompt text is
assembled once per activation while the tools an agent may reach are
snapshotted per reply. Pinned whole rather than by substring on both
surfaces that print it, the API acknowledgement and the CLI's stderr
line, because what an operator reads is the sentence rather than the
fields it happens to contain.

**2 (P2). The prompt boundary was documented wrongly in both
directions.** `prompt_includes` is declared on `AgentDefaults` and
inherited by `AgentConfig`, so one description covered two layers in two
regimes: it claimed the defaults' list reloads, which this milestone's
own apply test proves start-bound, and the prompt-fragment handler's
docstring and the README's operational-trap paragraph still sent an
operator to restart for a change a request applies. The drift checks
could not see any of it, because they hold the committed document to the
models and a description naming the wrong boundary passes them exactly.

*Resolution.* Adopted (`b0b4992f`). `AgentConfig` re-declares the field
for its description alone, same type, same default and the same
validator, which is bound by name and inherited; the handler and the
README say the boundary that holds; both references were regenerated.
Two semantic assertions join the drift check, in its spirit and doing
what a diff cannot: they read the two rows out of the generated
reference and pin the sentence each has to mean. Proved to bite by
putting the old shared description back on the agent, which fails one of
them while the byte-for-byte check stays green.

**3 (P2). Nothing proved that an agent's own include list is applied.**
The overlay takes two fields off a retained agent and only one was
pinned: removing `"prompt_includes": fresh.prompt_includes` left the
suite green, because the fragment cases moved a fragment's text, which
the fragment kind's own wholesale replacement applies, and the prompt
cases moved an agent's prompt, which the other half of the same line
applies.

*Resolution.* Adopted (`7ee907e2`). An apply-level case whose answer
nothing else can produce: two fragments exist in both worlds with the
same text, so replacing the fragment kind replaces it with a copy of
itself, and the agent's own list moving between them is the only
difference an apply can read. It asserts the installed generation's
names and resolved text, the result's `prompts.changed`, what the next
activation sends the model, and that the comparison clears. Proved to
bite by removing that line from the overlay and running the reload,
comparison, API and holder suites: one failure, this case, with 321
other tests still passing.

### Verification, after the review round

Run from `vinga-server/`, at the last commit of the round.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files.
- `uv run pytest tests/unit -q`: 2,738 passed, 16 skipped. The three new
  cases are this round's: the agent's own include list at the apply, and
  the two semantic pins on the generated reference.
- `uv run pytest tests/integration -q`: 61 passed, unchanged.
- The four documentation drift checks: all clean.
  `api-openapi.json` and `domain-config.md` moved with the descriptions
  and are committed with them; `events.md` and `conversations-schema.md`
  are byte-untouched by this round as by the milestone.

The image and the smoke lane remain unverified here, for the reason
given above.

## M2: fillers, re-synthesized with clip reuse

### What was done

**The cache became a value.** `Generation` carries
`fillers: Mapping[str, FillerClips]` beside its configuration and its
secrets, for the reason the other two travel together: a clip is a
configured phrase spoken by a configured voice, so it is a consequence
of one world and holding it anywhere else would be a second place that
has to agree. `AgentFillers` is gone, and with it the fill-once assert,
the `ready` property and the lifespan seam they existed for.
`Composition.agent_fillers` is gone too, which is round 1's finding 10
for this milestone.

**A session binds its clips at construction.** `bespoke_runtime_factory`
lost its `fillers` parameter and reads `generations.current().fillers`
inside `build`, which is the one moment a runtime is made. That is the
convergence point stated as control flow rather than as a rule: a
conversation goes on masking with what it opened on, and the next one
gets what the apply installed.

**The synthesis learned reuse.** `build_agent_fillers` takes the
previous world as an optional third argument and answers a `Fillers`
value: the clips, and the three closed outcomes, sorted. The reuse key
is `_voiced_by`, a pair of the agent's effective filler section and the
agent's running TTS provider, computed for both sides from the same
running providers mapping. Equal key plus an existing clip means the
object itself is carried over; anything else is synthesized, and a
failure leaves no clip and names the agent under `disabled`. An agent
that configures no filled pause is in none of the three.

**The overlay grew a field, and the field list grew a home.**
`config/diff.py` now declares `OVERLAID_AGENT_FIELDS`, which is
`("prompt", "prompt_includes", "filler")`, and `config/reload.py`'s
`_composed` copies exactly those from the stored entry of every retained
agent. The restart-bound comparison's exclusion mapping is derived from
that declaration plus `mcp`, taking each field's own default off the
model, so the three facts that used to be written separately are one.

**The comparison grew a third half.** `AgentsDiff` carries `filler`
beside `grants` and `prompt`, each labelled `reload`, and
`agent_defaults` stays restart-labelled whole, since a candidate
generation keeps the previous layer and the effective-value helpers
inherit through it.

**The result's fillers section is real.** `FillersReload` was declared
in M1 and answered null; it answers now, with the same three fields and
no schema change, which is what the round 3 finding 6 shape was for. The
CLI renders it through `RELOAD_SECTIONS` and `outcomes` with no new
rendering code.

**Notices and prose.** `AGENT_NOTICE` says three things where it said
two, the third being that the filler section applies at the next
conversation. The `Applies` docstring, the `API_DESCRIPTION` diff and
reload paragraphs, the docgen contract prose, the `FillerConfig`
docstring and both READMEs were rewritten, and both generated references
were regenerated in the same change.

### The shared-cache reader inventory

Taken by `git grep -n "agent_fillers\|AgentFillers"` against the commit
this milestone started from (`f1fce4ba`), which found **38 lines across
14 files**. The five source sites are what mattered; every one is
accounted for.

| Site | Held or read | Disposition |
| --- | --- | --- |
| `src/vinga_server/filler.py` (`AgentFillers`) | the mutable cache and its fill-once assert | deleted; the mapping is `Generation.fillers` |
| `src/vinga_server/app.py:257,302,417,433` | constructed it, handed it to the factory, put it on the composition, filled it at the end of the boot | one `build_agent_fillers` call before the holder is built, whose clips go into the first `Generation` |
| `src/vinga_server/composition.py:82` | the `agent_fillers` field | removed |
| `src/vinga_server/runtime/pipeline.py` (`bespoke_runtime_factory`) | the closed-over cache | gone; `build` reads `generations.current().fillers` |
| `src/vinga_server/runtime/filler_runner.py` (`FillerCache`) | the three reads a runner needs | unchanged as a protocol, since a mapping satisfies it; its prose stopped naming the deleted class |

The nine test files went the same way: `build_agent_fillers(...)` reads
`.clips` where a mapping was wanted, `session_for(fillers=...)` puts them
in the world the session binds, and `test_filler_cache.py` went from six
cases about a mutable object to two about the mapping and the wiring.

### Deviations from the plan

Eight, each recorded because it moved something the plan named.

**1. The reuse key's provider half is the running provider object, not
an identity string.** The plan says "the identity of the agent's TTS
binding as it is actually running". Implemented as the object itself,
because that is what "as it is actually running" can mean before
providers are generational: an identity string would be a description of
the entry the object was built from, and two objects built from one
entry are exactly what M3 has to tell apart. Both sides of the
comparison read the same running mapping, so the half is equal by
construction here; writing it down anyway is what makes M3's change one
line, and it is what makes the provider-edit and rotated-credential
cases pass for the stated reason rather than by accident.

**2. `ConfigReload` takes the running providers as a constructor
argument.** The plan puts the built `AgentProviders` mapping on the
generation at M3, which leaves M2 needing the running one from
somewhere. It is handed in beside the stored read, required rather than
optional, because every server has one; M3 is where the field goes away
in favour of a generation read.

**3. The fillers section is never null again, including for a server
with nothing to do.** M1's description said an empty three-way answer
would claim that every agent had been considered and nothing needed
doing. That claim is now true, so the section answers `[] [] []` rather
than null for a deployment where no agent masks its latency, and the
integration pin says so. Only `providers` and `agents` are still null.

**4. An agent that masks nothing is in none of the three outcomes, and
so is one that just switched masking off.** The closed set is the plan's
and does not grow, so a filler section switched off drops its clip and
is reported nowhere. Said out loud in the model's docstring rather than
answered with a fourth outcome: a clip that is gone because an operator
asked for it to be gone is not a decision a reload made.

**5. `AgentsDiff` grew a third entry rather than widening `prompt`.**
The plan says the agent kind's reload-labeled half "grows"; a `filler`
section reported under a field called `prompt` would be the wrong
sentence, and the two converge at different moments (an activation and a
session), which is exactly what the separate label is for. The published
diff schema therefore gains a field, which is the growth the plan
allowed for the diff and deliberately not for the reload result.

**6. Which agent fields a reload applies became one declaration in
`config/diff.py`.** The plan does not say where the overlay's field list
lives, and writing it a second time in `config/reload.py` would have
been two structures that must agree. `diff.py` is the module whose
docstring already owns "which configuration kind takes effect at which
boundary", and it is the direction the imports already run, so the
declaration is there and the overlay reads it. The restart-bound
exclusion mapping is derived from it rather than listed, taking each
field's default off `AgentConfig`.

**7. The agent notice merged with PR #229's review round rather than
replacing it.** This milestone was built on the commit before that
round, which had just widened `AGENT_NOTICE` to name the `mcp` grants
and the two clocks the applied half had. Rebasing onto it made the
sentence carry four applied fields and three clocks: the prompt fields
at the next activation, the grants at the next utterance, the filler
section at the next conversation, and everything else at the next start.
The round's own pin asserts the sentence whole, so it was widened in the
same breath, and the same merge runs through `Acknowledgement.notice`,
the `API_DESCRIPTION`, the docgen prose and the four README sites that
name the halves.

**8. The boot's order moved.** Synthesis now runs before the generation
is built rather than after everything else, which put it in front of the
conversation writer's start. `test_conversations_boot.py` proves that a
boot failing after the writer started still stops it, and filler
synthesis was the failure it used, so the writer now starts immediately
after the store is constructed, in front of everything a boot can still
fail in, and that test keeps its subject. The stop was already on the
exit stack at construction, so the earlier start is strictly safer.

### Discoveries

**A protocol was the way to hand the previous world to synthesis.**
`filler.py` cannot import `generation.py`, which imports it. `Served`
declares the two reads reuse needs (`config` and `fillers`), `Generation`
satisfies it structurally, and a test supplies a configuration and a
mapping without building a world.

**`_composed` had to stop returning a `Generation`.** The overlay is
synchronous and the synthesis is not, so the composition of the
candidate is now three statements in `_prepare`: the overlay, the
synthesis against it, and the `Generation` built from both plus the
composed secrets. That reads better than it did, because the one line
that was doing three things is now three lines that each do one.

**Deriving the exclusion mapping from the model's defaults works
exactly.** Every field a reload applies (`mcp`, `prompt`,
`prompt_includes`, `filler`) has a declared default, and each default is
the "nothing here" value the mapping was listing by hand.

**A delay-only edit re-synthesizes.** The key is the whole filler
section, so changing `delay_ms` alone makes clips whose bytes are
identical. Following the plan's wording rather than narrowing the key to
the phrases: the section is one value, rebuilding is the safe direction
to be wrong in (the store's own posture), and a key that covered part of
a section would be a second rule about what a clip depends on.

### Verification

Run from `vinga-server/`, at the last commit of the milestone.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files. Its scope is
  the events package, which this milestone does not touch.
- `uv run pytest tests/unit -q`: 2,744 passed, 16 skipped. (2,738 at the
  end of PR #229's review round, which this milestone was rebased onto;
  the net six are eight new cases in `test_config_reload.py` and two in
  `test_config_diff.py`, less the four `test_filler_cache.py` cases that
  were about a mutable object that no longer exists.)
- `uv run pytest tests/integration -q`: 61 passed, the same count as M1
  left: the one case this milestone changes was already there and was
  amended rather than added to.
- The four documentation drift checks, regenerated and diffed against
  `../docs/reference/`: all four clean. `api-openapi.json` and
  `domain-config.md` change deliberately and are committed in this
  milestone; `events.md` and `conversations-schema.md` are byte-untouched
  and are absent from this milestone's commits, which is what says no
  event and no conversation column moved.

Not verified here, and not claimed: the container image, the smoke lane,
and anything against a real device, none of which this milestone
touches.
