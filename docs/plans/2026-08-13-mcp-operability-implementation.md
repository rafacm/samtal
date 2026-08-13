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
existing `_printable` bound, and `_status_entries` refuses a body that
does not carry the five fields with the same `UNRECOGNIZED_ANSWER`
sentence the other readers use. No `--local`: a database has no runtime
state to report.

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
