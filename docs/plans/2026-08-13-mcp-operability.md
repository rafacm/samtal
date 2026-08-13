# Round out MCP operability: status, reload, per-tool grants

## Goal

Implement issue #121: the M6 tools-and-MCP plumbing works end to end,
but running MCP servers in anger surfaces operability gaps. This plan
gives the deployment a runtime status surface for its MCP servers, a
deliberate reload path so a configuration edit does not cost a restart
and every live session, a per-tool grant model for agents that should
not get everything a server publishes, and explicit documented
decisions for the three gaps that resolve as documentation: builtins
staying structural, SSE-only servers riding the stdio bridge, and
non-text tool results staying named placeholders.

The companion implementation doc,
[`2026-08-13-mcp-operability-implementation.md`](2026-08-13-mcp-operability-implementation.md),
records what each milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by issue #121 and not re-litigated here:

1. **The scope is the six gaps, in one issue.** They touch the same
   seams (`tools/mcp.py`, `runtime/pipeline.py`, the config API), and
   none carries an issue alone.
2. **Visibility lands before reload.** A reload path without a status
   surface reproduces the silent-inert-config trap it exists to fix.
3. **Gaps 3 and 4 are one design conversation about the grant model**,
   drawn up aware of #122, which hangs per-server guidance on the same
   agent-to-server grant edge and notes that guidance describing a
   tool withheld by an allow list is noise.
4. **Gaps 5 and 6 may resolve as documentation.** This plan resolves
   both that way, with the reasons below.
5. **Non-goals**: memory's operator surface (#83), new tools or
   providers (#12, #82), instructions and prompt fragments (#122). The
   deprecated streamable_http client (#98) was retired first, as the
   issue suggested, in PR #123.

## The issue's open questions, resolved

### Gap 1: the reload path is an explicit endpoint, not session-open pickup

`POST /runtime/mcp-servers/reload` on the configuration API, with
`samtal-server config reload` as its CLI client. The alternative the
issue names, re-reading domain config when a session opens, loses on
three counts:

- **Deliberateness.** An operator edits several entries and grant
  lists, then applies them once. Session-open pickup applies a
  half-finished edit at whatever moment a device happens to connect,
  which is the inert-config trap's mirror image: config that changed
  under you rather than config that did not change when you thought.
- **Attribution.** An explicit reload has a caller to answer. The
  response carries what changed and the resulting status, so the
  ConfigMap-shaped trap (believing a write took effect when it did
  not) is closed by the same request that applies the change.
- **Cost control.** Reconnecting MCP servers is not free (stdio spawn,
  handshake, tools/list). Doing it on session open would put that on
  the conversation path; an endpoint keeps it on the admin path.

**What a reload applies, exactly**: the `mcp_servers` entries, the
stored secrets riding on them, and the agents' effective `mcp` grant
lists (`agents.<name>.mcp` and `agent_defaults.mcp`), re-read from the
configuration database. Nothing else. Agents' providers, prompts,
memory, filler, devices and the server section keep the boot-time
snapshot contract; a new agent still waits for the restart that builds
its providers. This is the same shape of exception device bindings
already are: a running server re-reads a named, bounded slice, and the
contract stays "boot-time snapshot" everywhere else.

**Mechanics.** The reload re-reads the domain half from the database
(the file half is the running server's own; a changed file still means
restart), verifies the stored secrets open, composes it with the
running server section and validates the whole snapshot exactly as
boot does, so entry-name rules, reference checks and `server.local_only`
egress declarations are enforced by the same code.

**The apply is two-phase, and only the second phase touches anything
running.** Model validation is not the last thing that can fail:
building a manager resolves `$VAR` references from the environment,
decrypts stored credentials and enforces the egress declarations, any
of which can refuse after the snapshot itself validated. So the
prepare phase does all of it: validate the composed snapshot, verify
the stored secrets open, construct every candidate manager the new
world needs (which is where `$VAR` resolution, decryption and the
`local_only` check already live, at construction), and only when
every candidate stands does the apply phase stop, start and swap. A
failure anywhere in preparation refuses the reload with the same
sanitized sentence shape the write routes use and leaves managers and
grants exactly as they were. Being unreachable is not a preparation
failure: a candidate that connects to nothing still applies as a
`down` manager with its reason on the status surface, eligible for
the existing revival, which is the boot behaviour (config errors fail,
liveness does not) carried over. The diff against what is running:

- an entry that is new, or newly referenced, is built and started;
- an entry whose fragment or whose stored secrets changed is stopped,
  rebuilt with the fresh `SecretStore`, and started (secret rotation
  therefore applies on reload, not just entry edits). "Changed" is
  decided by a comparison primitive the store owns: `SecretStore`
  grows an opaque per-entity fingerprint (a digest over the entity's
  slot names and ciphertext envelopes), and the diff compares
  fingerprints, so neither envelopes nor plaintext ever reach the
  reload code, its response, its logs or an exception message;
- an entry that is gone, or no longer referenced by any agent, is
  stopped and dropped;
- an unchanged entry keeps its live connection untouched.

Grant lists are swapped atomically with the manager set, so the next
tool snapshot any session takes sees the new world. One reload runs at
a time; a concurrent one is refused with 409, like the database's busy
write lock. The handler is `async def`, unlike the plain-`def` CRUD
routes, because it awaits manager lifecycles on the event loop that
owns them, and that puts a duty on it the plain routes discharge by
being plain: `ConfigStore.load()` is synchronous and takes
`BEGIN IMMEDIATE`, so run on the loop it would stall every live
conversation for up to SQLite's busy timeout. The whole synchronous
half (open the database, load, verify secrets, compose, validate)
therefore runs in `asyncio.to_thread`, touching no manager state; the
diff, candidate construction, manager stop/start and the atomic swap
happen on the event loop, which is the task that owns them. The
status handler is `async def` for the matching reason: it reads the
managers and the slice on the loop that mutates them, so a read
cannot interleave with a swap and report half of one world.

**The reload answers within a stated bound, in a typed shape.** The
apply phase does its lifecycle work concurrently, the way
`start_all` already connects at boot: stops together, then starts
together, each start under the existing `CONNECT_TIMEOUT_S` and each
stop under a short fixed bound of its own with task cancellation as
the backstop, so the endpoint's whole envelope is one connect timeout
plus small change rather than a sum over managers. A manager that
cannot finish connecting inside its bound is not a hung request: it
is reported `down` with its reason, revivable as ever. The response
is a declared model, not an ad-hoc dictionary: the four diff outcomes
by entry name (`started`, `restarted`, `stopped`, `unchanged`) plus
the full status document as `GET /runtime/mcp-servers` would answer
it, so one round trip both applies and verifies. Refusals map like
the rest of the API: 409 for a reload already running or a busy
database, 422 for a preparation refusal in the sanitized sentence
shape, 500 for unreadable stored state, 503 for an application built
without a server. All of it is pinned in the committed OpenAPI
document and exercised over a real socket. The CLI's `reload`
command sends with an endpoint-specific read timeout comfortably
above the server-side envelope (60 s against a bound well under
half that), so the client cannot time out on a reload the server
then applies, which would recreate the exact ambiguity this feature
exists to remove.

**Live sessions.** The pipeline already snapshots tools per reply, so
a running conversation picks up the new grant list and the new tool
set on its next utterance, with no session drop. A call in flight on a
manager the reload stopped fails into the existing error-tool-result
path, which is the same behaviour as a server dropping mid-call today.

**Write notices.** Every MCP mutation the API serves stops claiming a
restart is needed and instead names the reload: the entry writes and
deletes (`wrote_mcp_server`, `deleted_mcp_server`) and equally the
MCP secret slot writes and clears, which today answer with the
generic restart sentence even though rotation is exactly what the
ciphertext half of the reload diff applies. Provider writes and
provider secrets keep the restart sentence, and so does the CLI's
`--local` recovery path even for MCP entities: it runs where there
may be no server to reload, and the sentence it prints must not
promise a request it cannot make. Agent
writes keep the restart sentence: an agent fragment mixes reloadable
(`mcp`) and non-reloadable fields (prompt, providers, filler), and a
notice that is right about one field and wrong about the rest is worse
than the conservative sentence. The README and the reload response
document that a reload also re-reads grant lists, and the issue's
first verification step (new entry, granted, usable without restart)
exercises exactly that pair of writes plus one reload.

### Gap 2: the status surface is a gated read of runtime state

`GET /runtime/mcp-servers` on the configuration API, with
`samtal-server config status` as its CLI client. Precedent:
`/devices/pending` already serves the running server's own state from
the same mounted application, shared object rather than database read.
Unlike `pending`, whose path segment cannot collide with a MAC, a
route inside `/mcp-servers/` would shadow a legal entry name (`status`
passes `tools/names.py`, and an existing database may already hold
it), so the runtime surfaces live in a namespace of their own:
`/runtime/mcp-servers` for the status read, and the reload action
beside it as `/runtime/mcp-servers/reload`. The entity namespace stays
purely CRUD, so no future runtime route has to fight an entry name
either, and an upgrade regression test proves an entry named `status`
is still read, written and deleted as an entity.

One entry per configured `mcp_servers` entry, keyed by name:

- `state`: `connected`, `down`, or `unused` (configured but referenced
  by no agent, so no manager exists; a likely answer to "why does the
  agent not have the tool" and invisible today).
- `reason`: why a `down` server is down, the application-owned type
  token the unavailability warning already prints (`_reason`), or the
  fixed token for a connection dropped after a failed call; `null`
  when not down. Never a third party's message bytes, per the PR #123
  round.
- `since`: when the state last changed, ISO-8601 UTC, the `_instant`
  shape the pending listing uses.
- `tools`: what a connected server published, as the list of names
  the model sees (prefixed, sanitized), empty while down. Nothing
  else a server chose crosses this surface: descriptions and the
  original listed names are server-provided bytes, and a server that
  received a credential through its env or headers could reflect it
  in either, which would make a gated read into the secret-readback
  path the whole API refuses to be. Published names are the one
  server-derived thing already accepted on the observability surface
  (the connect log prints them, through the publishing rule), and
  they are what an operator needs to write a grant.
- `grants`: which agents may reach this server, as a mapping from
  agent name to `null`, meaning the whole server. Shaped as a mapping
  now so milestone 3 can put the allow list where the `null` is
  without breaking the shape it just shipped.

The manager grows the small amount of state this needs: its current
state, the reason token of the last failure, and the instant of the
last transition, written where `_run` and `_mark_down` already decide
those things. `McpServers` keeps the configuration slice it was built
from (entries and grants), so the status view has one source and the
reload has one thing to swap.

An API application built without a server around it (the committed
OpenAPI document's rendering, tests) has no runtime to report:
`status` answers an empty object and `reload` refuses with 503, the
same honesty `loaded_agents = ()` already has. `build_api` grows the
optional runtime hooks the same way it grew `pending`.

### Gap 3: per-tool grants are an allow list on the grant edge

An agent's `mcp` list keeps its replace-not-merge semantics and its
string entries, and gains an object form:

```yaml
agents:
  kids:
    mcp:
      - weather                 # the whole server, as today
      - server: home
        tools: [turn_on_light, turn_off_light]
```

- `server` names the `mcp_servers` entry, checked by the same
  reference validation as the string form. `tools` lists the tools
  the agent may reach, by the published name without its entry prefix
  (`turn_on_light` grants `home__turn_on_light`), matched exactly.
  That identifier is application-owned: it has been through the
  sanitize rule, it is what the status surface shows and what the
  model calls, so the operator reads a name in `config status` and
  writes the same name in the grant, and the raw server-listed
  original never has to appear on any samtal surface. Omitting
  `tools` means the whole server, same as the string form; an
  explicit empty list is refused at validation, because "granted,
  nothing allowed" is a confusing spelling of not granting
  (`mcp: []` is how an agent opts out).
- **Allow list only, no deny list.** A deny list fails open: a
  kitchen-sink server adding a tool silently grants it to every agent
  that denied the old ones, and the shared-family-device story (gap
  3's own example: `home__lights` yes, `home__unlock_door` no) is
  exactly where failing open is wrong. One way to say a thing.
- `agent_defaults.mcp` takes the same entry forms, with the same
  override semantics as today.
- **Names that match nothing are visible, not silent.** An allow list
  cannot be validated at write time against a tool list only a live
  connection knows. So when a server's tools come out of the
  publishing rule (connect or reload), every grant naming that server
  is checked against the final `PublishedTools` mapping, after
  sanitization, collision drops and length drops, never against the
  raw `tools/list` answer: a tool the server listed but publication
  dropped is exactly as unreachable as one it never listed, and a
  warning that consulted the raw listing would stay silent about it.
  The warning names the allowed tools that did not publish; the
  status surface shows the allow list under `grants`, beside the
  published tool list, so the mismatch is answerable in one read.

Internally the grant edge becomes a small value (`entry name` plus
`allowed tool names or None`), `Config.mcp_for_agent` returns it, and
`McpServers` filters a server's published tools through the allow list
by the published name's unprefixed half before handing them to the
tool snapshot. Execution
needs no second gate beyond the snapshot filter for the model's sake,
but gets one anyway: a call to a granted-away tool is refused in
`McpServers.call`'s routing by the same grant value, so the property
"the agent cannot reach it" does not rest on the model only calling
what it was shown. This is the edge #122 will hang guidance on, and
the object form is where a future per-grant field lands without
another schema change.

**Both entry forms round-trip as themselves.** The store serializes
agent rows into JSON and the views echo the row shape back, so the
grant needs a canonical serialization on both paths: a string entry
is stored and read back as its string, an object entry as
`{server, tools}` with no invented keys, which keeps a read exactly
the fragment a write of it accepts (the envelope contract) and keeps
pre-upgrade rows, which are all plain string lists, valid without a
migration. `config/store.py` (row serialization on write and load)
and `config/views.py` (what a read shows) are part of the milestone,
and the suite proves the loop: write both forms through the API,
read them back byte-shaped, reload and see them applied, and load a
pre-upgrade string row unchanged.

### Gap 4: builtins stay structural, and the rule is documented

`switch_agent` and `remember` do not join the grant model. The
structural conditions are the design, now documented rather than
implicit:

- `switch_agent`'s condition is device-shaped, not agent-shaped: it
  exists exactly when the device is bound to more than one agent.
  Withholding it per agent would strand a conversation on whichever
  agent lacks the way back, breaking the receptionist handoff that
  motivated the tool.
- `remember`'s condition is deployment-shaped: memory configured.
  Memory injection into the system prompt is unconditional when
  memory exists, so an agent with `remember` withheld would still
  recall but never learn, a half-state nobody asked for; and #83 is
  about to restructure memory's operator surface, which is the wrong
  moment to bolt a grant onto its tool.
- Two builtins exist. A grant model for two tools with sound
  structural rules is machinery ahead of need; the moment a builtin
  arrives whose availability is genuinely per-agent policy, the grant
  edge from gap 3 is where it lands, and #122's work on that edge
  will have shaken the design out further by then.

Documented in the server README's tools section and in the `mcp`
field's generated reference description, which is what an operator
reading the config schema sees.

### Gap 5: SSE-only servers ride the documented stdio bridge

No native SSE transport. The MCP ecosystem has moved its HTTP story to
streamable HTTP and deprecated SSE; a third transport arm would be
permanent maintenance for a shrinking population, bought exactly when
the project just paid to leave a deprecated client (#98, PR #123). The
bridge is one configuration line (`mcp-proxy` over stdio), it is what
`examples/mcp-server-stdio.yaml` already demonstrates, and the fix is
prominence: the README's tools section gets an explicit "SSE-only
servers" paragraph naming the bridge, the streamable-http example
points at it for SSE-only endpoints, and the generated `transport`
field description says the same, so the answer is wherever the
operator is looking.

### Gap 6: non-text results stay named placeholders, and the contract is stated

`_result_text` keeps rendering non-text content as
`[unsupported <type> content]`. The decision, now explicit: the tool
loop's contract is speakable text, because the pipeline's output is a
voice and its history is text-only by design (M6 decision). Naming the
type rather than dropping the content is deliberate, so the model can
say what it received instead of appearing to ignore it. The revisit
condition is named where the decision is documented: when the display
path can render more than speech, results can start carrying
structured content to the device, and that work belongs beside the
display protocol, not inside the tool loop. Documented in the README's
tools section and the `mcp_servers` generated reference.

## Module layout

```
samtal_server/tools/mcp.py         manager state/reason/since; McpServers
                                   keeps entries+grants; status view;
                                   reload diff-and-apply; grant filtering
                                   and the call-time grant check
samtal_server/runtime/pipeline.py  _tool_snapshot and revive ask
                                   McpServers by agent instead of
                                   carrying config-resolved entry lists
samtal_server/config/models.py     the grant value; mcp lists accept
                                   string-or-object entries; validation
samtal_server/config/store.py      grant serialization in agent rows,
                                   both forms canonical
samtal_server/config/views.py      reads echo the written form
samtal_server/config/api.py        GET /runtime/mcp-servers,
                                   POST /runtime/mcp-servers/reload,
                                   runtime hooks on build_api
samtal_server/config/writes.py     the mcp reload notice
samtal_server/config/cli.py        config status, config reload
samtal_server/config/boot.py       the domain-slice re-read the reload
                                   shares with boot
samtal_server/config/secrets.py    the opaque per-entity fingerprint
                                   the reload diff compares
samtal_server/app.py               wiring the hooks
samtal_server/config/docgen.py     the generated preamble's boot-time
                                   sentence learns the MCP reload
                                   exception
config.example.yaml                its "applies at restart" wording
                                   learns the same exception; domain
                                   entities stay out of it, so no grant
                                   example here
examples/agent.yaml                a per-tool grant example
examples/agent-defaults.yaml       the same, on the defaults layer
docs/reference/domain-config.md    regenerated
docs/reference/api-openapi.json    regenerated
samtal-server/README.md            status, reload, grants, builtins,
                                   SSE bridge, non-text results
CHANGELOG.md                       per-milestone entries
```

## Tests

Reuse of existing assets, not restatement: the stdio mock server in
`tests/support/mcp_stdio_server.py`, the in-process FastMCP-on-uvicorn
fixture pattern from `tests/unit/test_tools_mcp_http.py` (including
its one-shot session-manager rule), the API TestClient patterns from
the REST API suites, and the scripted mock LLM for integration
conversations.

- Unit, milestone 1: manager state transitions (connected, down with
  reason token, dropped-after-failed-call, `unused` for unreferenced
  entries); the status view's shape and instants; the API route
  (gated, empty for a serverless application); an entry named
  `status` still read, written and deleted as an entity while the
  runtime route answers beside it; CLI rendering; the reflection
  sentinels: a stdio and an HTTP test server whose tool metadata
  (names, descriptions) carries a credential sentinel, asserted
  absent from the status response, the CLI output and every log
  record, the way the malformed-handshake test already asserts.
- Unit, milestone 2: the diff (new, changed fragment, changed stored
  ciphertext, removed, de-referenced, unchanged-untouched); refusal
  applies nothing, proven for each way preparation can fail (a
  snapshot that will not validate, an unset `$VAR`, a stored secret
  that will not decrypt, a `local_only` egress refusal), with the
  running managers and grants asserted untouched after each; a
  candidate that merely cannot connect applies as `down` and is
  revivable; concurrent reload answers 409; grants swap visible to
  the next tool snapshot; write notices.
- Unit, milestone 3: config shapes (string form, object form, both in
  one list, `tools: []` refused, duplicate and blank names refused,
  reference checks on the object form, `agent_defaults` parity);
  filtering by the unprefixed published name, including tools whose
  listed names needed sanitizing; the call-time grant refusal; the
  unpublished-allowed-name warning, including a grant naming a tool
  the server listed but publication dropped; the status surface
  carrying allow lists.
- Integration: the issue's verification steps. A new entry written and
  granted through the API becomes usable in a live conversation after
  one reload, no restart, and status shows it connected with its
  tools. That test holds one WebSocket and one session across the
  whole proof, because the promise under test is per-reply pickup in
  a running session and the existing `converse` helper opens, speaks
  once and closes, which would pass by reconnecting: first utterance
  before the write and reload (the tool absent), the write and the
  reload through the API while the socket stays open, then a second
  utterance on the same session that reaches the new tool; a
  deliberately dead server shows `down` with a reason; an
  agent restricted to a subset sees exactly that subset in its merged
  tool list. The subset claim is proven from what the model was
  actually offered, not from which calls happened: `MockLlm.stream()`
  ignores its `tools` argument today, so a scripted conversation
  would pass even with a forbidden tool on offer. The mock gains a
  `{tools}` reply placeholder rendering the offered tool names, the
  same trick the `{system}` placeholder already plays for prompt
  injection, and the test asserts the spoken list is exactly the
  granted subset; the call-time refusal of a granted-away tool is a
  unit test on `McpServers.call`, separate from the offer proof.
- Doc drift: the committed OpenAPI document and generated reference
  are regenerated in the same change as each schema or route change,
  which the existing byte-for-byte drift tests enforce.

## Risks and mitigations

- **Reload races the managers' one-task lifecycle rule.** The SDK's
  clients break when entered and exited in different tasks. The reload
  only ever uses the managers' own `start`/`stop`, which already keep
  the whole lifecycle inside the manager's task; the reload task
  awaits, it never enters transports itself. The 409 lock keeps two
  reloads from interleaving stop/start on one manager.
- **A reload mid-conversation.** Per-reply snapshots mean the worst
  case is one reply built on the old world or one in-flight call
  failing into the error-result path the model already phrases.
  Accepted and documented; no session is dropped.
- **Schema widening breaks existing fragments.** The `mcp` list must
  keep accepting plain string lists: every stored agent row and every
  example uses them. The object form is additive; the unit suite pins
  the string form explicitly.
- **The status surface becomes a second config read that drifts.**
  Status reads only what `McpServers` holds (the slice it was built or
  reloaded with, and its managers), never the database, so it cannot
  disagree with what is actually running, which is the point of a
  runtime surface.
- **Committed contracts drift.** OpenAPI and reference regeneration
  ride in the same commit as the change that moves them; CI's drift
  checks are the net.

## Plan review round

One external review of the plan as first committed (9c773d2): codex
CLI 0.147.0, model gpt-5.6-sol, read-only against this repository
with the issue #121 body supplied, 2026-08-13. Verdict: ready after
the P1/P2 amendments. Findings as received, condensed; each carries
its resolution once the amendment addressing it lands.

1. **P1: the status route shadows a valid MCP server named
   `status`.** `GET /mcp-servers/status` registered before
   `/mcp-servers/{name}` would answer runtime status where
   `config show mcp-server status` used to answer that entry's
   configuration: `status` is a legal entry name under
   `tools/names.py`, and existing databases may already hold it. Put
   runtime status outside the entity namespace (such as
   `GET /runtime/mcp-servers`) rather than reserving the name, and
   add an upgrade regression test with an entry named `status`.
   *Resolution*: adopted. The status read is `GET /runtime/mcp-servers`
   and the reload moves beside it as `POST /runtime/mcp-servers/reload`,
   so the entity namespace stays purely CRUD and no future runtime
   route fights an entry name; the gap 2 section says why, and
   milestone 1's tests and acceptance carry the `status`-named-entry
   regression.

2. **P1: the status payload violates the no-secret-readback
   contract.** `listed_as` and `description` are server-provided
   bytes; a server that received a credential through its env or
   headers can reflect it in either field, making the API and CLI a
   secret-readback path, and sanitizing only `reason` does not cover
   it. Expose only names that have been through the publishing rule,
   omit raw descriptions and original names, define allow lists
   against a non-reflective identifier, and add sentinel tests
   asserting a credential reflected in tool metadata reaches neither
   responses nor CLI output nor logs.
   *Resolution*: adopted. The status `tools` field is now a list of
   published names only, with the gap 2 section stating why nothing
   else a server chose crosses the surface; allow lists are defined
   against the unprefixed published name, so the operator reads a
   name in `config status` and writes the same name in the grant and
   the raw original never appears anywhere; milestone 1's tests gain
   the two reflection sentinels over responses, CLI output and logs.
   One deliberate remainder: published names are still server-chosen
   strings inside a safe charset, accepted here because the connect
   log already prints them under the publishing rule and the model
   must see them anyway; a deployment that distrusts even that has no
   business granting the server at all.

3. **P1: reload would perform blocking SQLite work on the
   conversation event loop.** `ConfigStore.load()` is synchronous and
   takes `BEGIN IMMEDIATE`, and the existing database-backed handlers
   are plain `def` precisely so FastAPI runs them on a worker thread;
   an `async def` reload doing the same load would stall every live
   conversation for up to the busy timeout. Run the load, secret
   verification and composition in a worker thread with no manager
   state touched there, keep manager construction and swapping on the
   owning event loop, and define the status route's synchronization.
   *Resolution*: adopted. The reload mechanics now state that the
   synchronous half (open, load, verify, compose, validate) runs in
   `asyncio.to_thread` touching no manager state, while the diff,
   candidate construction, lifecycle work and the swap stay on the
   event loop; the status handler is `async def` so its read runs on
   the loop that mutates the managers and cannot see half a swap.

4. **P1: "invalid reload applies nothing" does not cover failures
   after model validation.** Manager construction resolves `$VAR`
   values, decrypts credentials and enforces `local_only` after the
   snapshot validates, with no stated ordering or rollback. Specify a
   two-phase apply: prepare everything (validation, secret
   verification, reference resolution, egress checks, candidate
   construction) before stopping or replacing anything, any
   preparation failure leaving managers and grants unchanged, and
   connection failure distinctly defined as an applied-but-down
   manager eligible for revival. Test unset variables, undecryptable
   secrets and `local_only` rejection, not only validation errors.
   *Resolution*: adopted. The mechanics section now specifies the
   two-phase apply in exactly this shape: preparation (validate,
   verify, construct every candidate, which is where `$VAR`
   resolution, decryption and the egress check already live) touches
   nothing running and any failure there refuses with the sanitized
   sentence shape and changes nothing; unreachability is applied as a
   `down`, revivable manager, the boot rule carried over. Milestone
   2's tests enumerate each preparation failure and assert the
   running state untouched after each.

5. **P1: object-form grants cannot round-trip through the current
   database and view paths.** `config/store.py` serializes
   `list(entry.mcp)` into row JSON and `config/views.py` echoes the
   same shape, and neither file is in the plan's layout; pydantic
   grant objects are not valid values there. Add both files to the
   milestone, define canonical serialization for both entry forms and
   the exact read representation, and require write, restart or
   reload, read-back tests covering pre-upgrade string rows.
   *Resolution*: adopted. Gap 3 gains a round-trip decision: each
   entry form serializes as itself (string as string, object as
   `{server, tools}`), which keeps reads write-shaped per the
   envelope contract and pre-upgrade string rows valid without
   migration; `config/store.py` and `config/views.py` join the module
   layout, and the milestone's tests close the write, read-back,
   reload, pre-upgrade loop.

6. **P2: secret-rotation diffing has no safe implementation
   primitive.** The diff compares stored ciphertexts, but
   `SecretStore` exposes locations, slots and decryption only, and
   `config/secrets.py` is absent from the layout. Add a store
   operation returning an opaque comparison fingerprint (or comparing
   envelopes internally) that never exposes envelopes or plaintext,
   and test that unchanged secrets preserve managers while rotated
   ciphertext rebuilds only the affected ones.
   *Resolution*: adopted. The diff bullet now names the primitive: an
   opaque per-entity fingerprint on `SecretStore`, a digest over slot
   names and ciphertext envelopes, compared and never exposed;
   `config/secrets.py` joins the module layout, and milestone 2's
   diff tests already carry the unchanged-preserves and
   rotated-rebuilds-only-affected cases.

7. **P2: MCP secret writes would still falsely instruct operators to
   restart.** The plan changes notices for entry writes and deletes
   only; MCP secret PUT and DELETE use the generic secret
   acknowledgements, whose default notice requires restart. Apply the
   reload notice to API and CLI writes and clears of MCP secret
   slots, keep restart notices for provider secrets and the `--local`
   recovery path, and test the notice on every MCP mutation path.
   *Resolution*: adopted. The write-notices paragraph now covers MCP
   secret slot writes and clears alongside entry writes and deletes,
   keeps the restart sentence for providers, provider secrets and the
   whole `--local` path (which runs where there may be no server to
   ask), and milestone 2's tests pin the notice on every MCP mutation
   path.

8. **P2: the documentation work targets the wrong source and would
   leave contradictory upgrade guidance.** `config.example.yaml`
   holds the file half only and says domain entities in it prevent
   boot, so the object grant form cannot be demonstrated there; its
   general wording and `config/docgen.py`'s generated preamble both
   claim every configuration command takes effect at restart, which
   the reload makes false; `examples/agent-defaults.yaml` uses the
   grant form and is omitted. Put object examples in
   `examples/agent.yaml` and `examples/agent-defaults.yaml`, update
   `config/docgen.py`, the acknowledgement descriptions and
   `config.example.yaml`'s wording for the reload exception, and
   regenerate rather than hand-edit the committed references.
   *Resolution*: adopted. The module layout now carries
   `config/docgen.py`, the corrected role of `config.example.yaml`
   (wording only, no domain examples) and
   `examples/agent-defaults.yaml` beside `examples/agent.yaml`;
   milestone 2 updates the two boot-time sentences in the same change
   that makes them false and regenerates the reference; milestone 3's
   grant examples live in the two example files. Committed references
   are regenerated, never hand-edited, which the drift checks
   enforce.

9. **P2: reload has neither a bounded completion contract nor a
   defined response schema.** No concurrency bound, total deadline,
   timeout outcome, JSON model or HTTP error set is defined, and the
   CLI's fixed 30 s read timeout could expire while the server later
   applies the reload, recreating the ambiguity the feature removes.
   Define bounded, concurrent lifecycle work, an endpoint-specific
   CLI timeout longer than the server bound, a typed response
   (started, restarted, stopped, unchanged, plus final status), and
   specified responses for validation refusal, preparation failure
   and timeout, pinned in OpenAPI and real-socket tests.
   *Resolution*: adopted. A new mechanics paragraph fixes the
   contract: concurrent stops then concurrent starts, each start
   under `CONNECT_TIMEOUT_S` and each stop under a short bound with
   cancellation as backstop, an envelope of one connect timeout plus
   small change, a slow manager reported `down` rather than awaited;
   a declared response model carrying the four diff outcomes plus
   the full status document; refusal statuses 409, 422, 500 and 503
   mapped like the rest of the API; and a 60 s endpoint-specific CLI
   read timeout above the server bound. Pinned in OpenAPI and tested
   over a real socket.

10. **P2: the proposed conversation test cannot prove which tools the
    model received.** `MockLlm.stream()` ignores its `tools`
    argument, so a scripted conversation passes even when a forbidden
    tool was offered. Capture the exact `tools` argument with a
    recording test LLM (or make the mock refuse calls absent from
    it), assert the offered names exactly, and test call-time
    authorization separately.
    *Resolution*: adopted. The mock LLM gains a `{tools}` reply
    placeholder rendering the offered tool names, the trick the
    `{system}` placeholder already plays, so the conversation itself
    carries the offer; the integration test asserts the spoken list
    is exactly the granted subset, and the call-time refusal is its
    own unit test on `McpServers.call`.

11. **P2: the "live conversation" test does not require reload during
    the same session.** The integration helper opens a socket, sends
    one utterance and closes, so reopening after reload would pass
    without testing the per-reply pickup promise. Require one
    WebSocket and session across two utterances: one before the write
    and reload, the reload through the API while the socket stays
    open, and a second utterance that observes the new tool without
    disconnecting.
    *Resolution*: adopted. The integration test now specifies exactly
    that sequence on one held WebSocket and session: tool absent in
    the first reply, write and reload mid-session, tool reached in
    the second reply, no reconnect anywhere.

12. **P3: allow-list warnings must be based on successfully published
    tools, not raw `list_tools` output.** Publication can drop a tool
    for a sanitized-name collision or length, and a warning compared
    against the raw listing would stay silent about a listed but
    unusable tool. Compare grants against the final `PublishedTools`
    mapping, and test a grant naming a tool that publication dropped.
    *Resolution*: adopted. The warning rule now names the final
    `PublishedTools` mapping as its comparison base, with the reason
    (a listed-but-dropped tool is exactly as unreachable as an
    unlisted one), and milestone 3's unit tests carry the
    dropped-tool grant case.

## Milestones

Stacked branches, one PR each, every merge leaving `main` releasable:
the status surface alone is additive; reload without grants-shape
changes is additive; the grant schema is backward compatible; the docs
milestone is docs.

- [x] **[Status visibility](2026-08-13-mcp-operability-implementation.md#milestone-1-status-visibility)**
  (PR #125): manager state, reason and since;
  `McpServers` keeps its slice; `GET /runtime/mcp-servers`;
  `config status`; OpenAPI regen; README section; CHANGELOG. Accept:
  lint and both lanes green; a dead server shows `down` with a
  reason and an unreferenced entry shows `unused`, both proven in
  tests; an entry named `status` still behaves as an entity; the
  drift checks pass.
- [x] **[Reload without restart](2026-08-13-mcp-operability-implementation.md#milestone-2-reload-without-restart)**
  (PR #126): the domain-slice re-read shared with
  boot; diff-and-apply on `McpServers`; grants behind the swap;
  pipeline asks by agent; `POST /runtime/mcp-servers/reload`;
  `config reload`; the MCP write and secret notices; the boot-time
  sentence in `config/docgen.py`'s preamble and `config.example.yaml`
  learns the reload exception, reference regenerated; OpenAPI regen;
  README; CHANGELOG. Accept: lint and both lanes green; the
  integration proof that a written-and-granted entry is usable after
  one reload with no restart, and that an invalid snapshot refuses
  and applies nothing.
- [x] **[Per-tool grants](2026-08-13-mcp-operability-implementation.md#milestone-3-per-tool-grants)**
  (PR #127): the grant value and object entry form;
  snapshot filtering and the call-time check; the
  unpublished-allowed-name warning; status carries allow lists;
  `examples/agent.yaml` and `examples/agent-defaults.yaml`, reference
  and OpenAPI regen; README; CHANGELOG. Accept: lint and both lanes green; the
  integration proof that a restricted agent sees exactly its subset;
  string-form fragments still validate byte-identically.
- [x] **[Documented decisions](2026-08-13-mcp-operability-implementation.md#milestone-4-documented-decisions)**
  (PR TBD): builtins structural rule, the SSE
  bridge paragraph and example pointer, the non-text results
  contract, in README, examples and generated reference; CHANGELOG.
  Accept: lint green; drift checks pass; the docs say what gaps 4, 5
  and 6 decided and why.
