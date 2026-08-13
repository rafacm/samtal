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
