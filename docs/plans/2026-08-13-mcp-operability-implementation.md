# Round out MCP operability: status, reload, per-tool grants

Companion to
[`2026-08-13-mcp-operability.md`](2026-08-13-mcp-operability.md). One
section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out.

## Milestone 1: Status visibility

A running server can now be asked what its MCP servers are doing, over
the configuration API and through a CLI command that prints it. Nothing
about the conversation path changed.

### What landed

**`samtal_server/tools/mcp.py`.** Three additions, in the order they
depend on each other.

Each `McpServerManager` records what it is doing: `state` (the module
constants `CONNECTED` and `DOWN`), `reason` (the token of the last
failure, or `None`), and `since` (a float, the instant that condition
started). They are written where `_run` and `_mark_down` already decide
those things: on a successful connect, in the failure branch, in the
`finally` (which only records a reasonless down when the manager was
connected, so a failure's own reason and the dropped-after-a-call token
are not overwritten by the unwind), and in `_mark_down`. The reason
vocabulary is the existing `_reason` type names plus one new fixed
token, `DROPPED_AFTER_FAILED_CALL` (`"DroppedAfterFailedCall"`), for the
one way down that has no exception left to name. The warning line the
failure branch prints now interpolates the recorded reason rather than
recomputing it, so the line and the surface cannot disagree.

`McpSlice` is the configuration slice an `McpServers` was built from: a
sorted tuple of every configured entry name, referenced or not, and a
mapping from agent name to the entries it may reach, built by
`McpSlice.of(config)` from `config.mcp_servers` and
`config.mcp_for_agent`. `McpServers.__init__` takes it (defaulting to an
empty slice, which is what the two tests that construct
`McpServers({})` directly get) and records its own construction instant,
which is the `since` an entry with no manager reports.

`McpServers.status()` answers one entry per configured entry, keyed by
name: `state` (adding `UNUSED` for an entry with no manager),
`reason`, `since` as an ISO-8601 UTC string, `tools` as the list of
published names, and `grants` as a mapping from agent name to `None`.
It reads the slice and the managers and nothing else.

**`samtal_server/config/api.py`.** A `McpServerStatus` response model
(with `state` as a `Literal` of the three values, so the document
carries a real enum), a `_mcp_servers` dependency taking the registry
off `app.state` the way `_pending` does, and `_runtime(api)` registering
`GET /runtime/mcp-servers`. The handler is `async def` for the reason
the plan gives, and answers `{}` when the application was built without
a server. `build_api` grows an optional `mcp_servers` argument beside
`pending`. The API description gains a paragraph on the `/runtime`
namespace and why it is separate. The `McpServers` import is under
`TYPE_CHECKING`, like `PendingDevices` and for a stronger version of the
same reason: `tools/mcp.py` pulls in the SDK's clients and the provider
layer, and `config openapi` must load none of it.

**`samtal_server/app.py`.** `McpServers.build(...)` moves from below the
providers to just above `build_api`, which is then handed the same
object. The API token is resolved into a local first, so a deployment
that forgot the variable still reads that refusal before any other, and
the boot contract is otherwise unchanged (a bad entry still fails the
boot there, unreachability still does not); what changed is only that
an MCP configuration error is now raised before a provider one.

**`samtal_server/config/cli.py`.** `samtal-server config status`, a
client of the new endpoint. It renders a block per entry (a header line
with the state, the instant and the reason in parentheses, then `tools:`
and `agents:` lines) rather than the pending listing's columns, because
two of the fields are lists. Everything printed goes through the
existing `_printable` bound, and `_status_entries` refuses a body it
cannot recognize with the same `UNRECOGNIZED_ANSWER` sentence the other
readers use (checking the five fields when this landed, and every
field's type and `state`'s vocabulary after the review round below). No
`--local`: a database has no runtime state to report.

**`docs/reference/api-openapi.json`.** Regenerated with
`uv run samtal-server config openapi`, never hand-edited. One new path,
one new schema, and the description paragraph.

**`samtal-server/README.md`.** A new subsection under Tools, "What the
MCP servers are doing", with sample output, the three states, why
`unused` exists, why there is no `--local`, and why the tool lists carry
published names only. The configuration API section gains the
`/runtime` route beside the entity routes and a pointer to that
subsection.

**`CHANGELOG.md`.** One entry under the existing `## 2026-08-13`, in a
new `### Added` group.

**Tests.** Twenty-four new ones, all in the unit lane.

- `tests/unit/test_tools_mcp.py`: six on the manager's own state (never
  connected, connected, dead with a token that matches a type-name
  pattern and quotes no message, dropped after a failed call with the
  fixed token, stopped on purpose with no reason, and the instant
  moving on a reconnect), and seven on the status view (a connected
  server's published names and nothing a server chose, a dead server
  down with its reason and no tools, an unreferenced entry `unused`,
  every configured entry reported once, grants naming every agent that
  may reach a server, instants that parse as UTC, and the view reading
  the slice rather than a configuration mutated since).
- `tests/unit/test_config_api_runtime.py` (new): the gate (no token and
  a wrong token), the empty answer for an application built without a
  server, the read against a real started registry, a dead server
  reported down, the wiring through a `create_app` mount, and the
  regression the plan's review round asked for: an entry named `status`
  written, read, listed and deleted as an entity while the runtime
  route answers beside it.
- `tests/unit/test_config_cli.py`: the empty-configuration sentence,
  the rendering of `down` and `unused` with tools and agents, and a
  body the client refuses to read as a listing.
- `tests/unit/test_mcp_status_reflection.py` (new) and
  `tests/support/mcp_reflecting_server.py` (new): the two reflection
  sentinels, below.

### The reflection sentinels, and where the plan contradicts itself

The plan asks (Tests, milestone 1) for "a stdio and an HTTP test server
whose tool metadata (names, descriptions) carries a credential
sentinel, asserted absent from the status response, the CLI output and
every log record". Its own gap 2 section, and finding 2's resolution,
say the opposite about names: published names are "the one
server-derived thing already accepted on the observability surface (the
connect log prints them, through the publishing rule)", they are what
`tools` carries, and they are what an operator writes a grant with. A
sentinel inside a publishable name would therefore appear in the status
response and in the connect log *by design*, and a test asserting its
absence could only pass by removing the surface the plan specifies.

Resolved in favour of the gap 2 sections, which are the ones that argue
the case: the sentinel is placed in every server-chosen field that must
not cross, and the tests additionally assert that the published names
do arrive.

- The stdio server (`tests/support/mcp_reflecting_server.py`) reads the
  value out of its environment, which is the delivery path a real
  entry's `env:` uses, so the child process genuinely holds what it
  reflects. It writes it into a tool description, into an argument
  description inside an input schema, and into the description of a
  tool whose name is too long once prefixed, so publication drops it.
  Its names carry nothing.
- The HTTP server is an in-process `FastMCP` served by the uvicorn
  helper extracted from `test_tools_mcp_http.py`, with the sentinel in
  a tool description and configured as the entry's `Authorization`
  header. Its reflection is arranged rather than observed (a `FastMCP`
  instance cannot vary a description per connection), which the test
  says out loud.

Each test asserts the sentinel is in what the servers published (so the
absences below are not vacuous), then that it is absent from
`json.dumps(status())`, from what `config status` printed on either
stream, and from every captured log record rendered through
`logs.JsonFormatter`, the way PR #123's malformed-handshake test does.
The log capture requires at least one record from
`samtal_server.tools.mcp`, so an empty capture cannot pass. Both tests
were checked for teeth: with `status()` interpolating descriptions into
its tool list, both fail; the source was restored from a copy and
touched, not with `git checkout`.

The capture is at `INFO` rather than `DEBUG`, deliberately. At `DEBUG`,
`httpcore` prints the headers of every response any httpx client in the
process receives, which is the pre-existing whole-server property
recorded in
[`2026-08-13-mcp-streamable-http-client-implementation.md`](2026-08-13-mcp-streamable-http-client-implementation.md),
not anything this surface decides.

### Deviations from the plan

Six, none changing what the milestone does.

1. **The sentinel is not put in tool names**, for the reason above. The
   plan's two gap 2 passages and its test bullet cannot both be
   satisfied.
2. **`grants` is typed `dict[str, list[str] | None]` in the response
   model, not `dict[str, None]`.** Every value the code produces today
   is `null`, which is what the plan specifies. The declared value type
   is the wider one because the plan's stated reason for the mapping
   shape is that milestone 3 can put an allow list where the `null` is
   "without breaking the shape it just shipped", and a `null`-only
   schema would have guaranteed exactly that break. The field
   description says what is true now.
3. **`since` moves when the reason moves, not only when the state
   does.** The plan says "when the state last changed". A server that
   goes on being down for a new reason has failed again, and an instant
   that stayed put would date the new reason to the old failure. The
   OpenAPI description says so.
4. **`_instant` is a second one-line helper in `tools/mcp.py`**, rather
   than shared with the identical one in `config/api.py`. The tools
   layer must not import the configuration API, and the alternative was
   a new shared module for one line.
5. **`McpServers.build` moved up in `app.py`.** The API is handed the
   registry, so the registry has to exist first. The API token is
   resolved into a local ahead of it, so the one precedence that is a
   promise to an operator (the admin surface's own credential is
   reported before anything the configuration references) is kept; the
   only observable change left is which of two boot failures is raised
   first when both a provider and an MCP entry are wrong. Corrected
   after the review round found this paragraph claimed less than the
   change did; see the round section below.
6. **The status view returns plain dictionaries from `tools/mcp.py`**,
   with the transport shapes declared as pydantic models in
   `config/api.py`. That is the arrangement `views.py` and the response
   models already have: the view decides what may be shown, the model
   describes it.

`config.example.yaml` needed no change: this milestone touches no
server-section schema and adds no configuration key. No entity or
domain schema moved either, so `docs/reference/domain-config.md` is
unchanged and only the OpenAPI document was regenerated.

### Discoveries

**`publish()` quotes a server's raw listed name in its drop warnings.**
When publication drops a tool (no usable name, too long once prefixed,
or a collision), the warning names the original as the server listed it
and the published form beside it. A server that put a credential in a
name it lists would therefore put it in that log line. This predates the
milestone and is not narrowed here: the plan's own accepted remainder
already has published names reaching the log under the publishing rule,
and the only identification an operator has of a dropped tool is its
name, so removing it would cost more than it buys. The reflecting test
server covers the half that does matter, a dropped tool whose
*description* holds the sentinel. Worth a decision of its own if #122's
work on the grant edge reopens it.

**`up` and `state` are deliberately two properties.** `up` stays "there
is a session", which is what `ensure_reconnecting` and `call` ask; the
recorded state is what the surface reports. They are written together
at every transition, and the transition tests assert both.

**A `TestClient` call inside an async test reads managers across
loops.** The portal runs the ASGI app on its own loop, so a test that
starts managers on the pytest loop and then drives the API through
`TestClient` is reading them from another thread. That is safe here
because `status()` is a synchronous read of plain attributes with no
await in it, which is also why the plan's real requirement, that the
handler be `async def` so the read happens on the loop that mutates the
managers, is about the server rather than about the tests.

### Verification

All from `samtal-server/`, on the tree at the documentation commit.

- `uv run ruff check .`: "All checks passed!".
- `uv run pytest tests/unit -q`: 1458 passed, 15 skipped in 132.56s.
  Twenty-four more than before the milestone, which is exactly the
  count listed above; the drift checks for the OpenAPI document and the
  generated reference run in this lane and pass.
- `uv run pytest tests/integration -q`: 44 passed in 80.32s. No
  integration test was added: milestone 1's surface is a read, and the
  plan's integration proofs are milestone 2's and 3's.
- Sentinel teeth: with the status view interpolating tool descriptions
  into its tool list, both reflection tests fail; restored from a copy
  and touched, both pass again.

Not verified, and not claimed: nothing was run against hardware or a
deployment. `config status` was exercised through its real entry point
against the real application over Starlette's TestClient, which is how
every other command in that suite is covered, but not over a socket
against a running server.

### PR #125 review round

One external review of the milestone's diff (main...ef4ac79): codex CLI
0.147.0, model gpt-5.6-sol, read-only, 2026-08-13. Verdict: mergeable
after the listed fixes. Findings as received, condensed; each carries
the commit that addressed it.

1. **P1: reflected credentials still escape through tool names.**
   `tools/publish.py` logs the raw name a server listed when it drops a
   tool, and `tools/mcp.py` logs and returns published names, so a
   credential made of tool-name characters reaches the log, the API and
   the CLI. The sentinel test avoids names, which the plan's test bullet
   asked for and the implementation doc admits. Suggested: never log
   rejected names, and stop a delivered credential from becoming an
   observable identifier at all, through application-owned aliases or
   credential-aware rejection during publication.
   *Resolution*: adopted in part, in 635cdd5. The half about rejected
   names is taken whole: all three drop warnings stop quoting what the
   server listed and identify the tool by its position in the listing,
   the length case says how long the name would have been, the
   empty-name case has nothing to print (every character but none at
   all survives sanitizing), and the collision case names the published
   name it clashes with, which is the one name a drop may print because
   an earlier tool already published under it and it is on the connect
   line already. The reflecting test servers now list a tool whose name
   is the credential and is too long to publish, so both sentinel tests
   cover a rejected name across logs, response and CLI, and the
   publishing suite pins the rule directly.
   The other half is refused, and this is the finding's real subject.
   Published names crossing is not an oversight: it is what the plan
   settled in gap 2 and in its resolution to plan finding 2, which the
   review did not have in front of it. The connect log has always
   printed them under the publishing rule, the model must be given them
   to call anything, and an operator must be able to read one in
   `config status` and write the same word into a grant, which is the
   whole design of milestone 3's allow lists. An application-owned alias
   would put a name the far side never chose in front of the model and
   in the operator's grant, breaking the correspondence the grant model
   rests on; credential-aware rejection would have this server pattern
   matching its own secrets against every string a third party sends,
   which fails open on any credential it does not recognize and turns a
   publishing rule into a scanner. The plan states the remainder and its
   limit out loud: a deployment that distrusts a server's choice of
   names has no business granting that server. Reopening it is a plan
   decision rather than a review fix.
2. **P1: `config status` prints arbitrary values from a malformed
   response.** The client checked that five keys existed and then
   stringified what was under them, so a body answering with an object
   where `state`, `since` or a tool name belongs would have printed that
   object. Suggested: validate the whole shape and vocabulary before
   rendering, raise the fixed refusal outside any exception context, and
   test every malformed field type.
   *Resolution*: adopted in 7d8e087. Every field is checked for its type
   and `state` for its vocabulary before anything renders; extra keys
   stay tolerated so a newer server is readable; the refusal is the
   existing sentence raised from plain predicates with no `try`, so
   nothing walking a chain finds the body. Ten parametrized bodies each
   carry a credential-shaped value where a printed one belongs, with a
   control asserting the shape they were built from is accepted and a
   test asserting the raised error's whole chain holds none of it. The
   first run found the bug the finding implies: a membership test on an
   unhashable value raises rather than answering False, so the type is
   checked before the vocabulary.
3. **P2: the boot-order change is broader than recorded.** The API token
   was resolved after the MCP managers, so a deployment missing
   `SAMTAL_API_SECRET` and also holding a bad MCP entry now reads about
   the entry.
   *Resolution*: adopted in 6b8e1f5. The token is resolved into a local
   ahead of the registry and passed to `build_api` from there. A test
   drives `create_app` with both faults and asserts the refusal names
   the variable; it fails under the old order. Deviation 5 above is
   corrected in the same commit.
4. **P3: the documented output and vocabulary cannot match the
   command.** The README sample listed agents unsorted where the command
   sorts them, and the help named two of the three states.
   *Resolution*: adopted in a6182be, with a test for each so neither can
   drift again.
5. **P3: the "a new reason moves the instant" contract is untested.**
   Every assertion in the suite would have passed with the instant tied
   to the state alone.
   *Resolution*: adopted in df94714. A manager that fails to spawn a
   command and then fails to reach a socket is down throughout for two
   different reasons, and the test asserts both the reason and the
   instant moved. Checked for teeth against an instant tied to the
   state.

### Verification after the review round

Same commands, from `samtal-server/`, on the tree at df94714.

- `uv run ruff check .`: "All checks passed!".
- `uv run pytest tests/unit -q`: 1476 passed, 15 skipped in 129.78s.
  Eighteen more than before the round, which is exactly what was added:
  two publishing rules, ten malformed bodies with their control and the
  refusal-chain check, the boot precedence, the agent order, the help
  vocabulary and the new-reason instant.
- `uv run pytest tests/integration -q`: 44 passed in 78.71s.
- Teeth, both restored from a copy and touched rather than with `git
  checkout`: the boot precedence test fails with the token resolved
  inside the `build_api` call, and the new-reason test fails with the
  instant tied to the state alone.

## Milestone 2: Reload without restart

An MCP entry written, a grant given and a credential rotated now reach a
running server on request, without a restart and without dropping a
conversation. Everything else about the configuration is still a
boot-time snapshot.

### What landed

**`samtal_server/config/secrets.py`.** `SecretStore.fingerprint(kind,
identity)`: a SHA-256 over the entity's slot names and the ciphertext in
them, each length-prefixed so that no two different sets of slots can
digest alike. It answers one question, whether two loads hold the same
stored secrets for one entity, and carries neither plaintext nor
envelope to whoever asks; it needs no key, which is what lets the diff
compare rotations on a store it cannot open. Setting a slot again to the
same plaintext changes the mark, since a Fernet token carries a
timestamp and a fresh IV, and rebuilding then is the safe direction to
be wrong in.

**`samtal_server/config/boot.py`.** The boot's steps 2 to 5 (open the
database, load, verify the stored secrets, compose and validate the
whole snapshot) become `_with_domain_half`, shared by `load_boot_config`
and the new `reload_domain_config(running)`. The reload composes the
stored domain half onto the running process's `server` and `memory`
sections rather than reading the file again, so entry names, references
and `server.local_only` are judged by the code that judged them at
startup while a changed file still means a restart.

**`samtal_server/config/loader.py`.** `ReloadInProgressError`, beside
`DatabaseBusyError` and for its reason: the raiser is the MCP registry,
on the conversation side of the process, and the answerer is the
configuration API, which loads none of that.

**`samtal_server/tools/mcp.py`.** The milestone's centre.

`McpServerManager` gains the secrets mark it was built with, `same_as`
(the same entry fragment and the same mark, which is the whole of the
diff's question), and a bounded `stop(timeout)` whose backstop is
cancelling the manager's own task, never touching a transport from
outside it.

`McpServers.reload(read)` is the two-phase apply. `read` is the
synchronous re-read, handed in rather than done here, and run in
`asyncio.to_thread` because it takes the database's write lock while
this coroutine is on the loop every conversation is on. `_prepared`
builds every manager the new world needs through `_managers_for`, the
function `build` now shares, which is where the egress check, `$VAR`
resolution and decryption already live; any failure there is recorded
and re-raised outside the handler as a `ConfigError` led by "the reload
was refused and nothing was changed". `_apply` diffs candidates against
what is running, stops the departing managers concurrently under
`STOP_TIMEOUT_S` (5 s, new), starts the arriving ones concurrently under
the existing `CONNECT_TIMEOUT_S`, and then swaps the manager dict and
the slice with no await between them. One reload at a time, on a plain
flag rather than a lock, because a second is refused rather than queued.

`tools_for_agent(agent)` and `revive_for_agents(agents)` ask the slice
that is running rather than a configuration a session is holding;
`McpSlice.entries_for` answers nothing for an agent it does not know.
`McpReload` is the frozen four-tuple of outcomes.

**`samtal_server/runtime/pipeline.py`.** The revive at connect and
`_tool_snapshot` call those two methods, so the grants swap with the
managers they name.

**`samtal_server/config/api.py`.** `POST /runtime/mcp-servers/reload`,
`async def` for the reason the status read is; the `McpReloadResult`
response model (the four outcome lists plus `servers`, the whole status
document, taken with no await after the reload returns); `NoRuntimeError`
mapped to 503 with its own problem description, `ReloadInProgressError`
mapped to 409 beside the busy database; `build_api` grows the
`mcp_reload` hook beside `mcp_servers`, and the API description gains a
paragraph on the second exception to the boot-time snapshot.

**`samtal_server/app.py`.** `_mcp_reloader(config, servers)` closes over
the booted configuration and the running registry and hands the registry
a plain `read` function, which is what keeps the tools layer clear of the
database.

**`samtal_server/config/writes.py` and the routes.** `MCP_RELOAD_NOTICE`,
answered by the four MCP mutations: the entry PUT and DELETE and the
secret slot PUT and DELETE. Providers, provider secrets, agents and the
whole `--local` path keep the restart sentence.

**`samtal_server/config/cli.py`.** `samtal-server config reload`, which
prints the four outcomes and then the status block underneath, since an
entry that started is not thereby connected. `RELOAD_READ_TIMEOUT_S` is
60 s against a server envelope of one connect timeout plus a stop bound,
and `_call` takes the endpoint's read timeout as an argument.

**Documents.** `config/docgen.py`'s preamble and `config.example.yaml`'s
note now name two exceptions and say what separates them (a binding is
noticed, a reload is asked for); `docs/reference/domain-config.md` and
`docs/reference/api-openapi.json` were regenerated with the commands,
never hand-edited. The server README gains "Applying an MCP change
without a restart" beside the status section, and its configuration and
API sections learn the third notice and the 503. One `CHANGELOG.md`
entry under the existing `## 2026-08-13`.

### Deviations from the plan

Four, none changing what the milestone does.

1. **The re-read is handed to `McpServers` as a callable rather than
   called by it.** The plan says the synchronous half runs in
   `asyncio.to_thread` and touches no manager state, which is exactly
   what happens; what differs is who imports whom. `tools/mcp.py`
   calling `config/boot.py` would put the database, SQLAlchemy and
   Alembic on the tools layer's import path, so the composition root
   passes a `read` function and the registry decides where it runs. The
   unit suite gets the same seam for free: the diff tests supply a
   configuration without a database.
2. **A candidate is constructed for every referenced entry, including
   the unchanged ones**, and the ones that turn out unchanged are
   thrown away. The plan asks for "every candidate manager the new world
   needs", and this is the literal reading; it also makes preparation
   total, so an entry whose stored secret stopped decrypting refuses the
   reload rather than being quietly kept. The cost is one env and header
   resolution per entry per reload.
3. **The CLI's endpoint-specific timeout is set on the client rather
   than passed with the request.** httpx would take it per request, and
   that is what was written first; Starlette's `TestClient` raises
   `StarletteDeprecationWarning` on a request-level timeout, and the
   whole CLI acceptance suite runs through that seam. Each `_call`
   builds a client, makes one request and closes it, so the two are the
   same thing here, and the comment says why.
4. **The reload response's status field is named `servers`.** The plan
   says "the full status document exactly as `GET /runtime/mcp-servers`
   would answer it" without naming the key; `status` would have read as
   `status.status` for every entry inside it.

### Discoveries

**`TestClient` carries a different httpx.** Starlette's test client is
built on a vendored `httpx2`, so assigning an `httpx.Timeout` to it
produces a `Timeout` whose `.read` is that object rather than a number.
Nothing in production touches it (the real client is `httpx.Client`),
but a test that asserted the reload's timeout through the fixture's
TestClient would have been asserting nonsense. The test that pins it
drives a real `httpx.Client` over `httpx.MockTransport` instead, and
says so.

**A reload cannot be driven across event loops.** Milestone 1 recorded
that a `TestClient` call inside an async test reads managers from
another loop, and that `status()` is safe because it is a synchronous
read. The reload is not: it stops and starts tasks. So the API-level
tests hand the route a stub reload and an unstarted registry, and the
real two-phase apply is exercised against real servers in
`test_tools_mcp_reload.py` and over a real socket in the integration
lane. This is the same one-task rule the module's own docstring warns
about, met from the test side.

**An unchanged entry needs an identity assertion, not a state one.** A
manager that was stopped and started again reports the same state, the
same tools and (within a second) a similar instant, so "unchanged" is
proven by the manager object being the same one and by its published
`ToolDef` objects being the very same objects, which a re-listing would
have replaced.

**The device sdk re-arms its own listening.** `XiaoZhiWebsocket` sends
`listen start` when a reply's `tts stop` arrives in auto mode, which is
what lets the integration test speak twice on one connection without
reimplementing the device's half of the protocol.

### Verification

All from `samtal-server/`, on the tree at the documentation commit.

- `uv run ruff check .`: "All checks passed!".
- `uv run pytest tests/unit -q`: 1504 passed, 15 skipped in 136.60s.
  Forty-six more than milestone 1, which is what the files above added;
  the drift checks for the OpenAPI document and the generated reference
  run in this lane and pass.
- `uv run pytest tests/integration -q`: 46 passed in 108.54s. Two more:
  the single-socket reload proof, and the reload's answer and its 422
  refusal over a real socket.
- Teeth: the integration proof fails at its second utterance if the
  reload is not made (the reply keeps carrying the tool loop's "no tool
  called" refusal), and every refusal test asserts the running managers
  and grants are the same objects afterwards.

Not verified, and not claimed: nothing was run against hardware or a
deployment, and no reload was made against a server holding a real MCP
server over the network. The 60 s client timeout and the 5 s stop bound
are asserted against the constants they have to outlast rather than
measured under a slow server.

### PR #126 review round

One external review of the milestone's diff: codex CLI 0.147.0, model
gpt-5.6-sol, read-only, 2026-08-14. Verdict: mergeable after the listed
fixes. Findings as received, condensed; each carries the commit that
addressed it, or the reason it did not.

1. **P1: a reloaded HTTP MCP server can leak a credential through debug
   logs.** With the root logger at `DEBUG`, httpcore prints the headers
   of every response any httpx client in the process receives, so a
   server that received a rotated credential and reflects it in a
   response header would put it in the log during the reload's connect.
   Suggested: suppress those records around the reload's connections.
   *Resolution*: refused, and refused deliberately. The mechanism is
   real and is not this milestone's: the same records flow at boot, at
   every background reconnect, and for the cloud LLM and TTS providers,
   which are HTTP clients in the same process. It is
   [#124](https://github.com/rafacm/samtal/issues/124), filed after PR
   #123's round recorded exactly this and put it out of that PR's scope
   for the same reason, and it is recorded again in
   [`2026-08-13-mcp-streamable-http-client-implementation.md`](2026-08-13-mcp-streamable-http-client-implementation.md)
   as a whole-process property. What it needs is a decision about how
   much of a debugging tool `DEBUG` stays, taken once and applied to
   every third-party client this server holds. Suppressing it around
   the reload only would be worse than leaving it: an operator's `DEBUG`
   would then show one thing for a connection made at boot and another
   for the same connection made ten seconds later by a reload, and the
   half that still leaks would be the half nobody had been warned
   about. Nothing about the reload widens the exposure either, since a
   rotated credential reaches the same client through the same
   transport whichever request opened it.
2. **P1: the stop bound cancels and then waits without a bound.**
   `stop(timeout)` cancelled the manager's task at `STOP_TIMEOUT_S` and
   awaited it unbounded afterwards, so a cleanup handler that suppresses
   its cancellation defeats the endpoint's envelope and can outlast the
   CLI's 60 s. Suggested: a finite deadline on the post-cancellation
   wait, and one warning plus abandonment to a background consumer if it
   expires.
   *Resolution*: adopted in 6dde230. `CANCEL_TIMEOUT_S` (2 s) bounds the
   wait after the cancellation, so the whole stop is bounded at 7 s and
   the endpoint's envelope is `CONNECT_TIMEOUT_S` plus that. A task that
   is still unwinding then is left to finish: one warning naming the
   entry, and the task held in a module-level set until it ends, because
   the loop keeps only a weak reference to a task nobody awaits and one
   ending in an exception nobody retrieved prints about it at
   interpreter shutdown. Abandoning does not break the one-task rule,
   which is about entering a transport in one task and exiting it in
   another; the comment says so. The test builds a manager whose cleanup
   swallows its cancellation and holds out for longer than either bound,
   and asserts the stop returns inside the envelope, that the task is
   held rather than dropped, and that nothing of it is held once it
   ends. Checked for teeth against the unbounded wait, restored from a
   copy and touched. The CLI's timeout test now takes the third constant
   into its envelope, so the 60 s stays asserted against what it has to
   outlast.
3. **P1: a cancelled request can leave half a world behind.** A client
   disconnecting cancels the handler awaiting the reload, and a
   cancellation landing inside `_apply` would leave stopped managers in
   the live set and started candidates reachable by nobody, with
   `_reloading` cleared as though the reload were done. Suggested: make
   the mutating phase commit to completion, hold the exclusion until it
   finishes either way, and test cancellation in both halves.
   *Resolution*: adopted in 5f61923. The apply runs in a task of its own
   and the caller awaits it behind `asyncio.shield`, so cancelling the
   request cancels the waiting rather than the work; the exclusion is
   released by a done callback when the caller is already gone, which is
   also where an outcome nobody is left to take is consumed. Two tests
   cancel inside the stops and inside the starts and then assert on the
   world rather than on the outcome the cancelled caller never receives:
   one coherent manager set, the slice swapped with it, the unchanged
   entry still the same object, nothing abandoned, and the next reload
   answered rather than refused. Both fail without the shield.
4. **P2: a read-stage refusal carried no guarantee.** A snapshot that
   will not compose refused with the composition's own sentence, while a
   candidate that would not build refused with that sentence led by "the
   reload was refused and nothing was changed". Suggested: give the read
   stage the same lead, raised outside the handling context, while the
   two refusals whose type is the answer keep theirs.
   *Resolution*: adopted in dc75667. `_read` wraps a `ConfigError` from
   the re-read in the same sentence and re-raises it outside the
   handler, so the chain is empty; `DatabaseBusyError` and
   `StorageError` travel out as themselves, because a busy database is
   retryable (409) and unreadable stored state is not the caller's fault
   (500), and wrapping either would make both 422. Three tests: the
   exact message, the two types preserved with their own sentences, and
   the empty chain.
5. **P2: the reload's 422 was described by a sentence about
   addressing.** The shared problem text names a stage that is not a
   stage and a MAC that is not one, and this endpoint addresses nothing
   and carries no body. Suggested: a route-specific description,
   regenerated into the committed document.
   *Resolution*: adopted in 0c7e389. `_problems` takes a per-route
   override, the reload uses one saying what its 422 does mean and that
   nothing was stopped, started or swapped, and
   `docs/reference/api-openapi.json` was regenerated with the command. A
   test pins both halves, the reload's own sentence and an entity write
   still carrying the shared one, so the override cannot quietly become
   a global edit.
6. **P3: the real-socket refusal test had nothing to lose.** It ran
   against a deployment with nothing configured, so "nothing was
   applied" was true of an empty registry whatever happened to it.
   Suggested: seed it with a connected server and a live grant.
   *Resolution*: adopted in 413f6ec. The proof moves to the integration
   lane's reload module, against a server holding the stdio support
   server connected, an agent granted it, and a device mid-conversation
   using it. The invalid snapshot is provoked through the API by the one
   route pair that leaves it open, and the assertions are that the
   status document is identical instant for instant across the refusal,
   that the conversation still reaches the tool, and that the repair
   reloads the entry as `unchanged` rather than `restarted`. What stays
   in the API suite is the answer's shape over a real connection.

### What the round turned up

**The only invalid snapshot this API can be talked into is one.** Every
write route refuses a fragment that would leave a reference dangling and
every delete refuses while something still names its subject, and
`check_completeness` checks exactly one rule beyond that: a deployment
with agents has to be reachable, by a bound device or by a default
agent. So unbinding the board and then clearing the default agent is the
whole of what an operator can do through the API to make the stored
configuration uncomposable, which is what finding 6's test does. The
milestone's own boot test asserted a message it described as a pipeline
check and which was always this rule; its wording is corrected in the
same commit as this section.

### Verification after the review round

Same commands, from `samtal-server/`, on the tree at the documentation
commit.

- `uv run ruff check .`: "All checks passed!".
- `uv run pytest tests/unit -q`: 1533 passed, 15 skipped in 141.35s.
  Seven more than the tree this round started from, which is what the
  round added: the stubborn stop, the two cancellations, the read-stage
  message, the two read refusals that keep their type, the empty chain,
  and the route-specific 422. The absolute figure is not the
  milestone's own above plus seven, because the branch was rebased onto
  a `main` carrying the merged milestone 1 and its review round in
  between.
- `uv run pytest tests/integration -q`: 47 passed in 89.35s. One more:
  the seeded refusal, beside the single-socket proof and the answer's
  shape over a real socket.
- Teeth, both restored from a copy and touched rather than with `git
  checkout`: the stubborn-stop test fails with the post-cancellation
  wait unbounded, and both cancellation tests fail with the apply
  awaited directly instead of shielded.

Not verified, and not claimed: nothing was run against hardware or a
deployment. The bounds are asserted against the constants they have to
outlast rather than measured against a slow server, and the abandonment
path is exercised against a manager written to be stubborn rather than
against a real transport that would not close.

## Milestone 3: Per-tool grants

An agent can now be granted part of an MCP server rather than all of it.
The list that says which servers an agent talks to says which of their
tools as well, in an entry form the string form's readers still read.

### What landed

**`samtal_server/config/models.py`.** `McpGrant` is one `mcp` list entry
in its object form: a `server`, and a `tools` list or nothing. The field
is `list[NonBlankStr | McpGrant] | None`, so the string form is
untouched and the object form is additive. Validation refuses an empty
`tools` with the plain way to say it (`mcp: []`), a tool named twice, a
blank name (`NonBlankStr`), and a server named twice in one list
whichever forms the two entries take. `Config.mcp_for_agent` answers
grants rather than names, `referenced_mcp_servers` reads `server` off
them (an allow list narrows the tool list, never whether the connection
is made), and `check_references` checks both forms in the one place it
checked one. `mcp_entry_fragment` is the canonical serialization both
the row writer and the view use.

**`samtal_server/config/store.py` and `config/views.py`.** Both went
through `list(entry.mcp)`, which a pydantic model is not a valid value
for and which would have normalized the two forms into one. Both now map
each entry through `mcp_entry_fragment`: a string is stored and shown as
its string, so every row written before the object form existed loads
and is written back byte-identically and no migration is owed, and an
object is stored and shown as `{server, tools}` with `tools` absent when
it was never written.

**`samtal_server/tools/names.py`.** `unqualified(entry, published)`, the
half of a published name a grant names it by. It strips the entry it was
qualified with rather than splitting on the separator, because an entry
name may legally contain `__` itself.

**`samtal_server/tools/mcp.py`.** The milestone's centre.

`McpSlice.grants` holds grants rather than entry names. `grants_for`
answers an agent's, `entries_for` is the server names off them (what a
revive needs), `allows(agent, entry, published)` is the call-time
question, `allowed_by_agent(entry)` is the status surface's mapping from
agent to allow list or `None`, and `allowed_names(entry)` is every tool
name some grant allows of one entry, which is what a publication is
checked against.

`tools_for_agent` filters each granted server's published tools through
that grant's allow list, matched by the unprefixed published name.
`McpServers.call` takes the speaking agent and refuses a tool the grants
do not name with `McpToolNotGranted`, which reaches the session as the
error result an unknown tool produces.

Each `McpServerManager` carries the names some grant allows of its
entry, and `_warn_about_unpublished` says which of them the server did
not publish. It runs where the manager publishes (so a boot, a reload
and a background reconnect all check) and again when `expect` hands a
connected manager a new allow list. The comparison is against the
published mapping, never the raw listing, and only names the operator
wrote are printed.

The reload composes its `McpSlice` once and hands it to both phases, so
the world the candidates were built for is the world that is installed,
and a kept manager is told its new allow list on the way through.

**`samtal_server/runtime/pipeline.py`.** `_dispatch` passes the active
agent to `McpServers.call`.

**`samtal_server/config/api.py` and `config/cli.py`.** The `grants`
field's description says what the value now means; `config status`
prints an agent on its own when it may reach the whole server and with
its allowed tools in parentheses when it may not.

**`samtal_server/providers/mock.py`.** A `{tools}` reply-template
placeholder rendering the offered tool names, the trick `{system}`
already plays for the prompt. Templates without it are unaffected.

**`samtal_server/config/docgen.py`.** An "MCP grant" entity section
beside the filler's, since the `mcp` field's type now names a shape a
reader has to be able to look up.

**Documents.** `examples/agent.yaml` carries the object form live and
`examples/agent-defaults.yaml` carries its shape and the prose for the
defaults layer; the server README's tools section gains the per-tool
grants passage and its status subsection explains the parentheses;
`docs/reference/domain-config.md` and `docs/reference/api-openapi.json`
were regenerated with the commands, never hand-edited. One
`CHANGELOG.md` entry under the existing `## 2026-08-13`.
`config.example.yaml` holds no domain entities, so it did not change.

**Tests.** Thirty-four new ones in the unit lane and one in the
integration lane.

- `tests/unit/test_config_tools.py`: the shapes (object form, object
  form without tools, both forms in one list, empty `tools` refused
  naming `mcp: []`, a tool named twice, a blank name, a server named
  twice in each of the three ways two entries can say it,
  `agent_defaults` parity with replace-not-merge), the reference check
  on both forms in both layers, and each form serializing as itself.
- `tests/unit/test_config_store.py`: both forms through a row with the
  raw column asserted, a pre-upgrade string row loading and being
  written back unchanged, and a grant on an unknown server refused at
  the write.
- `tests/unit/test_config_api_writes.py`: an `mcp` list reading back in
  the form it was written, on an agent and on the defaults, and a grant
  naming an unknown server refused with 422.
- `tests/unit/test_tools_mcp.py`: a whole-server grant offering
  everything, an allow list offering only what it names, a grant written
  against the sanitized published name reaching the tool while the raw
  listed name reaches nothing, two agents getting their own subsets, the
  call-time refusal (and the same call from an agent granted the whole
  server running), a call from an agent with no grant at all, the three
  warning cases (a tool the server never listed, a tool publication
  dropped for length, and a whole-server grant warned about nothing),
  and the status surface carrying allow lists beside published tools.
- `tests/unit/test_tools_mcp_reload.py`: a narrowed allow list applying
  without touching the connection, and a grant added to a connected
  server checked on the reload rather than at the next connect.
- `tests/unit/test_config_cli.py`: `config status` printing how much of
  a server each agent gets.
- `tests/unit/test_providers_mock_tools.py`: the `{tools}` placeholder,
  and a template without it unaffected.
- `tests/integration/test_tools.py`: the issue's third verification
  step, proven from the offer rather than from which calls happened.

### Deviations from the plan

Five, none changing what the milestone does.

1. **The object form is parsed by a `mode="before"` validator rather
   than left to the field's union.** Pydantic reports every branch of a
   union, so the first thing an operator read about a grant with an
   empty `tools` was that a mapping is not a valid string, and the
   sentence about the actual mistake came second. The entry is parsed as
   an `McpGrant` before the union sees it and the failure is re-raised as
   that grant's own sentence, naming the entry's index and its server.
   The annotation is unchanged, so the schema and the reference still
   describe both forms.
2. **`config/docgen.py` gains an "MCP grant" entity section.** The plan
   asks only that the `mcp` field's description describe both forms,
   which it does; but the field's rendered type is now
   `list[str | McpGrant] | null`, and a reader who meets that name has
   nowhere to look it up. It is a nested shape with no command of its
   own, the arrangement `FillerConfig` already has.
3. **`config status` renders the allow lists too.** The plan puts them
   on the status surface; the CLI is that surface's other client, and an
   operator reading `agents: kids` there would have had to ask the API
   directly to see that `kids` gets two tools of the server rather than
   all of them.
4. **The agent reaches the call router as an argument of
   `McpServers.call`.** The plan left the seam to the implementation and
   asked for the choice to be recorded. The alternatives were a grant
   value carried from the tool snapshot to the call (which would have
   made the check use a world one reply old, since a reload can land
   between them) and a per-session view object (a second object to keep
   in step with the swap). One registry serves every session and the
   grants swap inside it, so the session passes the one thing it owns,
   its agent's name, and the registry answers from the world running
   now. It is a required argument rather than an optional one, so a call
   site cannot forget it.
5. **A kept manager is told its new allow list on a reload.** The plan
   checks grants "when a server's tools come out of the publishing rule
   (connect or reload)", and an entry the reload leaves alone publishes
   nothing. Deferring its check to the next connect would leave an
   operator who adds a grant and reloads with no answer until something
   restarts, which may be days. `expect` therefore re-checks a connected
   manager against the tools it already published.

### Discoveries

**The test server's awkward names were already the two cases that
mattered.** `tests/support/mcp_stdio_server.py` lists
`weather.today/v2`, which publishes as `tools__weather_today_v2`, and a
sixty-character name that publication drops because the entry prefix
pushes it over the limit. The first is the sanitizing case (a grant
naming the published half reaches it, one naming the raw listed name
reaches nothing) and the second is the plan's dropped-tool warning case,
neither needing a new fixture.

**The offer proof needs an agent with no builtins.** `_tool_snapshot`
merges `switch_agent` when the device is bound to more than one agent
and `remember` when memory is configured, so the reply's rendered list
is exactly the granted subset only when the test binds each agent to a
device of its own, configures no memory, and registers no device tools.
The two agents in that test therefore sit on two MACs rather than on one
bound to both.

**A pre-upgrade row is proven by writing it back, not by loading it.**
Loading a plain string list would pass whatever the serializer did with
it; the assertion that matters is on the column after a write through
the same path the API writes with, which is where a normalization into
objects would have shown up.

**Teeth.** The integration proof fails when the restricted agent's grant
is widened to the whole server (the spoken list then carries the
sibling's tools); the file was restored from a copy and touched, not
with `git checkout`. Each warning test asserts the tool's absence from
the published list first, so the warning is about something genuinely
unreachable rather than about a name that arrived anyway.

### Verification

All from `samtal-server/`. The figures first recorded here were the
branch's before it was rebased, twice: onto the merged milestone 1 with
its review round, and onto the merged milestone 2 with its review round.
Both lanes were rerun on the rebased tree, after the PR #127 round
below, and these are that tree's.

- `uv run ruff check .`: "All checks passed!".
- `uv run pytest tests/unit -q`: 1585 passed, 15 skipped in 145.99s.
  The arithmetic the rebase makes checkable: milestone 2's round left
  1533 on `main`, this milestone adds the thirty-four listed above, and
  the round below adds eighteen. The drift checks for the OpenAPI
  document and the generated reference run in this lane and pass.
- `uv run pytest tests/integration -q`: 48 passed in 96.39s. One more
  than milestone 2's round left: the offer proof. The round below added
  no integration test.

Not verified, and not claimed: nothing was run against hardware or a
deployment, and no grant was written against a real third-party MCP
server. The unpublished-name warning was exercised against the stdio
test server only, and the reflection sentinels milestone 1 added were
not re-run against the new warning line, which prints operator-written
names rather than anything a server chose.

### PR #127 review round

One external review of the milestone's diff: codex CLI 0.147.0, model
gpt-5.6-sol, read-only, 2026-08-14. Verdict: mergeable after the listed
fixes. Findings as received, condensed; each carries the commit that
addressed it, or the reason it did not.

1. **P1: an object grant's refusals quote what they rejected.** The
   duplicate tool name, the server value of a failing entry and the
   locations pydantic reports (which for a key the model does not
   declare is that key) were interpolated into sentences that travel
   through the store into CLI errors and HTTP 422 bodies, so a
   credential pasted into a malformed grant came back out. Suggested:
   make them location-and-rule only, keeping them actionable, and add
   credential sentinels over the response, the CLI, the logs and the
   exception chain.
   *Resolution*: adopted in 56cda3c. A repeated name is named by the
   positions that repeat it, an entry by its position in the list, and a
   location inside a grant only when it is a field this repository
   declared; anything else prints as "an unrecognized key". The empty
   allow list still points at `mcp: []`, which is what the plan asked
   that sentence to do. Thirteen sentinel cases: seven malformed grants
   over HTTP with the body, the headers and every log record checked,
   the same shapes through the repository with the whole exception chain
   checked, one over the CLI on both streams, and the repeated-server
   refusal. All of them fail against the sentences they replace.
   One thing the finding's subject touches that this did not change, and
   deliberately: `check_references` still answers an unresolved
   reference with `unknown MCP server "<name>"`, and its provider
   neighbour with the same shape. That sentence predates this milestone,
   is uniform across every reference kind, and names what the fragment
   asked the repository to resolve rather than a value it rejected on
   sight. It is still a body value reaching a 422, so it is worth
   deciding on; the decision is repository-wide (both branches, several
   suites pinning the wording) and does not belong in a milestone's
   review round. The sentinel cases above are built so that none of them
   depends on it.
2. **P1: the call-time gate embeds the refused tool name in the
   exception, the error result and the `tool_call` log event.**
   Suggested: deny without the value.
   *Resolution*: refused, and refused deliberately. The name in a
   refused call is model-generated content, not a server's bytes and not
   an operator's secret. The model's words are already this server's
   structured transcript by design
   ([`json logs are the observability surface`](../adr/2026-08-04-json-logs-are-the-observability-surface.md)):
   the same reply is logged under `heard` and `said`, every `tool_call`
   event has carried `call.name` since M6, and the pre-existing
   unknown-tool path answers `there is no tool called "<name>"` with it.
   A value-free denial would therefore remove the one datum an operator
   debugging a grant needs, which tool was denied, while leaving the
   same name in the transcript lines beside it, and it would make the
   granted-away case say less than the unknown-tool case it is
   deliberately shaped like. If model-generated content is to stop
   crossing into logs, that is a decision about the transcript and every
   line that carries it, not about this gate.
3. **P2: an entry name may contain the separator, and the split at the
   first one gets it wrong.** `home__inside` is a legal entry, and
   `split_qualified` hands `home__inside__turn_on` to a server called
   `home`: unreachable when there is none, and somebody else's tool when
   there is. The grant gate made the wrong answer more visible, since it
   asked the same split which server to check. Suggested: resolve
   published names through a registry-owned mapping with the longest
   matching entry-name prefix winning, drop cross-manager collisions
   deterministically with a warning, and keep the pipeline on the same
   resolution.
   *Resolution*: adopted in db3015d. `names.owner_of(published,
   entries)` is the one resolution: the longest entry that qualifies the
   name owns it, which depends on the configuration and on nothing a
   server published, so two callers asking about one name get one
   answer. The registry's offer, status, timeout lookup, grant check and
   routing ask it, and so do the session's dispatch and per-call
   timeout. Where two entries do publish one name the more specific
   keeps it and the other's tool is dropped rather than offered under a
   name that would run somebody else's, which is `publish`'s own
   first-wins drop between managers instead of within one. Two
   departures from the finding's letter, both for determinism: the
   winner is the more specific entry rather than the first listed,
   because "first" across managers means whichever connected first and
   would flip when one reconnects, while the entry names cannot; and the
   drop is decided when the tools are read rather than when they are
   published, because a reload that adds the inner entry changes the
   answer for the outer one without reconnecting anything. The warning
   is printed once per manager set, by position and by the entry that
   owns the name, never by the name, per the rule PR #125's round
   settled for dropped tools. `split_qualified` had no caller left and
   is gone. Five tests, each failing against the first-split rule: the
   resolution itself, an entry holding the separator end to end through
   the registry (offer, timeout, gate, execution), the same through the
   session's dispatch and timeout, the collision drop with its warning
   and its status, and the once-per-world reporting. The stdio test
   server grew a tool whose listed name holds the separator, so one
   entry genuinely collides with another and each answers differently,
   which is what lets a test say which one a call reached.
4. **P2: the schema declares `tools` as an unconstrained array while
   the model refuses an empty one and a repeated name.** Suggested:
   express both in the JSON schema, regenerate, and assert them in the
   contract test.
   *Resolution*: adopted in 84881b4. The array carries `minItems: 1` and
   `uniqueItems: true` on the type, where a generator and a schema
   validator read them, declared rather than enforced by pydantic
   because a constraint would answer the empty list with its own
   sentence instead of the one that says how to grant nothing. Both
   committed documents were regenerated with their commands; the
   reference did not move, since its tables carry types and descriptions
   rather than constraints. The contract test asserts both bounds beside
   the empty-secret case it already pinned, and the write suite
   exercises the refusals over HTTP.
5. **P3: the milestone's verification figures are the pre-rebase
   tree's and are arithmetically inconsistent with this one.**
   Suggested: rerun and replace them.
   *Resolution*: adopted in this commit. Both lanes were rerun on the
   rebased tree and the milestone's Verification section carries those
   figures, with the rebase said out loud and the arithmetic shown:
   1533 on `main` after milestone 2's round, plus this milestone's
   thirty-four, plus this round's eighteen.

### Verification after the review round

Same commands, from `samtal-server/`, on the tree at the documentation
commit.

- `uv run ruff check .`: "All checks passed!".
- `uv run pytest tests/unit -q`: 1585 passed, 15 skipped in 145.99s.
  Eighteen more than the tree this round started from, which is what the
  round added: thirteen sentinel cases for the refusals and five for the
  namespace resolution.
- `uv run pytest tests/integration -q`: 48 passed in 96.39s. Unchanged:
  the round is about what a refusal says and which server a name belongs
  to, and both are proven where they are decided.
- Teeth, all restored from a copy and touched rather than with `git
  checkout`: every sentinel case fails against the sentences that quoted
  the body, and all five namespace tests fail against the first-split
  resolution.

Not verified, and not claimed: nothing was run against hardware or a
deployment. The colliding namespace was exercised against two entries
pointed at the same stdio test server rather than against two real
third-party servers, and no schema validator outside this repository was
run against the regenerated document.

## Milestone 4: Documented decisions

The three gaps that resolve as documentation are written down, where an
operator meets each of them rather than only in the plan. No behaviour
changed and no code path moved; what moved is four field descriptions
and the prose around them.

### What landed

**Gap 4, the builtins stay structural.** The server README's tools
section gains a paragraph where `switch_agent` and `remember` are
described, saying that neither is granted the way an MCP server is and
why the conditions are not agent-shaped: `switch_agent`'s belongs to the
device and withholding it per agent would strand a conversation on
whichever agent has no way back, `remember`'s belongs to the deployment
and the prompt injection is unconditional wherever memory exists, so a
withheld tool would leave an agent recalling for ever and never
learning. It ends where a future policy-shaped builtin would land: the
grant edge the `mcp` list already carries. The `mcp` field's description
in `config/models.py` gains one sentence saying the builtins are outside
the grant model, since the schema is where the other reader is.

**Gap 5, SSE rides the stdio bridge.** An "SSE-only servers" paragraph
in the same section, directly under the two transports, with the
configuration that answers it (`mcp-proxy` in front of the endpoint,
configured as the stdio server it then is), why there is no native arm
(the specification moved its HTTP story to streamable HTTP and left SSE
deprecated, so a third arm would be permanent maintenance for a
shrinking population, bought straight after this server paid to leave a
deprecated client behind), and that nothing else about the entry
changes. `examples/mcp-server-streamable-http.yaml` says it is the only
HTTP transport there is and points at the stdio example for an SSE
endpoint; `examples/mcp-server-stdio.yaml` labels its `mcp-proxy` line
as that bridge, which is what it silently was. The `transport` field's
description carries the same sentence.

**Gap 6, non-text results stay named placeholders.** A paragraph beside
the README's error-result one: the tool loop's contract is speakable
text because the pipeline's output is a voice and its history is text
throughout, other content is rendered as a named placeholder
(`[unsupported image content]`) rather than dropped so the model can say
what it was given instead of reading as though it ignored the tool, and
the revisit condition is the display path, at which point structured
content to the board belongs beside the display protocol rather than
inside the tool loop. The `mcp_servers` entry in `DOMAIN_DESCRIPTIONS`
carries the short form.

**Generated documents.** `docs/reference/domain-config.md` and
`docs/reference/api-openapi.json` were regenerated with their commands
in the commits that moved the descriptions, never hand-edited. One
`CHANGELOG.md` entry under the existing `## 2026-08-13`, under Changed:
nothing was added to the server, and a documentation-shaped entry is
what that section already holds this date. No test changed, and none
needed to: the two drift checks are the automated half of this
milestone.

### Deviations from the plan

Two, neither changing what the milestone documents.

1. **The stdio example is annotated as well as the streamable-http
   one.** The plan asks the streamable-http example to point at the
   bridge. The example it points at demonstrated the bridge without
   naming it, so an operator following the pointer arrived at a Home
   Assistant proxy and had to infer that this was the SSE answer. Three
   comment lines on the `command` say so.
2. **The README's SSE paragraph shows the three lines rather than
   describing them.** The plan asks for a paragraph naming the bridge.
   The claim being made is that the answer is one line of configuration,
   and printing it is what makes the claim checkable.

`config.example.yaml` needed no change: it holds the file half and no
domain entities, and nothing in the server section moved.

### Discoveries

**A description change can move one committed contract, both, or
neither.** `transport` and `mcp` are fields of models the configuration
API serves, so their descriptions are in `docs/reference/api-openapi.json`
as well as in the generated reference; `DOMAIN_DESCRIPTIONS["mcp_servers"]`
describes a whole section of the composed configuration, which the API's
per-entity schemas do not carry, so gap 6's sentence moved the reference
alone. Regenerating both after every description change is the only rule
that does not require knowing which case you are in, and the first two
commits of this milestone were written the other way (reference only)
and caught by the OpenAPI drift test in the unit lane before they were
final.

**The tools section had no natural home for a transport paragraph.** Its
order is servers, grants, secrets, device tools, builtins, error
results. The SSE paragraph went directly under the two-transport example
because that is the sentence it contradicts ("both transports the
specification defines are supported"), and the non-text paragraph went
under the error-result one because both are about what comes back from a
call rather than about what is configured.

### Verification

All from `samtal-server/`. The figures first recorded here were the
branch's before it was rebased onto the merged milestone 3 with its
review round; both lanes were rerun on the rebased tree, and these are
that tree's.

- `uv run ruff check .`: "All checks passed!".
- `uv run pytest tests/unit -q`: 1585 passed, 15 skipped in 145.81s.
  Exactly what milestone 3's round left on `main`, this milestone adding
  no test; the drift checks for the OpenAPI document and the generated
  reference run in this lane and pass, which is what proves the two
  committed documents match the descriptions as edited.
- `uv run pytest tests/integration -q`: 48 passed in 96.62s. Unchanged
  from what milestone 3's round left, as a documentation milestone
  should leave it.

Not verified, and not claimed: no SSE-only server was put behind
`mcp-proxy` and reached from this server, so the bridge is documented
from the specification's deprecation and from the example that already
shipped, not from a run. Nothing was run against hardware or a
deployment, and the placeholder rendering was not re-exercised, no code
having changed.
