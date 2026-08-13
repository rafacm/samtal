# Move the MCP streamable_http transport off the deprecated client

## Goal

Implement issue #98: the pinned MCP SDK marks
`mcp.client.streamable_http.streamablehttp_client` as `@deprecated`,
so the transport samtal uses for every HTTP MCP server is one SDK
release away from breaking. Switch `tools/mcp.py` to the SDK's
replacement client, drop the per-test `DeprecationWarning` filter
that PR #97's review round left in
`tests/unit/test_secret_resolution.py`, and give the transport real
coverage beyond that one header-delivery test.

The companion implementation doc,
[`2026-08-13-mcp-streamable-http-client-implementation.md`](2026-08-13-mcp-streamable-http-client-implementation.md),
records what the milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by issue #98 and not re-litigated here:

1. **Switch to the SDK's replacement client.** The deprecation
   notice on `streamablehttp_client` names it:
   `streamable_http_client`, in the same module.
2. **Drop the per-test filter.** The
   `@pytest.mark.filterwarnings("ignore::DeprecationWarning")` on
   `test_a_stored_header_reaches_a_real_request`, and the comment
   explaining it, exist only because the server still calls the
   deprecated function. Both go.
3. **Add coverage for the transport beyond the header test.** Every
   other MCP manager test runs over stdio; the streamable_http side
   of `_connect` is exercised by exactly one test today.

## How the two clients differ, for reference

Verified against the pinned SDK (1.28.1):

- The deprecated `streamablehttp_client(url, headers=..., ...)`
  builds its own `httpx.AsyncClient` through the SDK's factory with
  `follow_redirects=True` and `httpx.Timeout(30, read=300)`, manages
  its lifecycle, and delegates to the replacement.
- The replacement `streamable_http_client(url, *,
  http_client=None, terminate_on_close=True)` takes a pre-built
  `httpx.AsyncClient` instead of header and timeout parameters. A
  provided client is caller-managed: the transport does not close
  it. Both yield the same `(read, write, get_session_id)` tuple.

So the switch moves header delivery (and with it the timeout and
redirect policy) from the SDK's hands into `_connect`'s.

## Decisions this plan makes

### `_connect` builds the httpx client itself, not via the SDK's factory

The SDK's `create_mcp_http_client` factory would reproduce the old
defaults exactly, but it lives in `mcp.shared._httpx_utils`, a
private module; adopting it trades one fragile SDK dependency for
another, which is the shape of problem this issue exists to remove.
`_connect` constructs `httpx.AsyncClient` directly. httpx is already
a direct dependency (the cloud providers speak their APIs over it),
so no dependency is added.

The client is entered on the same `AsyncExitStack` as the transport
and the session, before the transport, so unwinding closes the
transport first and the client after it, in one task, preserving the
one-task lifecycle rule the module header documents.

### The old wrapper's HTTP policy is preserved explicitly

The client is built with `follow_redirects=True` and
`httpx.Timeout(30.0, read=300.0)`, the deprecated wrapper's
defaults, stated as literals with a comment saying where they come
from. Nothing in samtal has ever chosen these values; they are what
every existing deployment has been running, and this change is a
client swap, not a policy change. The 300 s read timeout is the SSE
read timeout the wrapper exposed as `sse_read_timeout`; a
streamable_http server may hold a GET stream open long-term, which
is why it is deliberately longer than `CONNECT_TIMEOUT_S` (the
connect-and-list budget, which stays the module's own and unchanged).

### The dependency constraint becomes `mcp>=1.24,<2`

The current floor (`mcp>=1.2`) predates the replacement client
entirely: probing PyPI releases shows `streamable_http_client`
absent in 1.23 and present in 1.24. A floor that admits an SDK
without the symbol would make the boot fail on import for anyone
resolving fresh with an older pin.

The range is capped below 2: MCP 2.0 is published as stable, its
own metadata recommends `<2` for unmigrated v1 clients, it requires
httpx 2, and its transport yields two streams where `_connect`
unpacks three, so an uncapped range would let a fresh resolve of
the published requirements pick an SDK the code cannot import
against while CI stays green on the lock. Migrating to MCP 2 is
separate work with its own issue when the project chooses to take
it; this change is about leaving the deprecated 1.x client, not the
1.x line.

`uv.lock` moves with the requirement: the lock records the
project's requirement strings in its metadata, and the frozen image
build treats the lock as authoritative, so a pyproject edit the
lock does not reflect would leave the two disagreeing about what
the project requires. `uv lock` is rerun
and the lock committed in the same change; the resolved `mcp`
version stays 1.28.1, so the delta is metadata, not a version jump.
Verification includes `uv lock --check`.

### New coverage runs the shipping transport in-process

The stdio tests spawn `tests/support/mcp_stdio_server.py` as a
subprocess because that transport is a child process by nature.
streamable_http's nature is a TCP socket, and a subprocess would add
port-collision and readiness-polling machinery without making the
wire any more real. The new tests host a small `FastMCP` server
in-process: uvicorn on `127.0.0.1` port 0 (the OS picks a free
port), serving `streamable_http_app()`, started as an asyncio task
and awaited until `server.started`, with the bound port read from
the server's socket. uvicorn is already a dependency and runs the
app's lifespan, which the streamable_http session manager requires.
The manager under test connects to `http://127.0.0.1:<port>/mcp`
exactly as it would to a real server.

The tools the test server publishes are defined in the test module
itself, small and local; the stdio support server exists as a file
because a subprocess needs a file to run, and an in-process app
needs no file.

### What the new tests cover

A new module, `tests/unit/test_tools_mcp_http.py`, holding the
uvicorn fixture and mirroring the stdio suite's transport-facing
cases:

- A started server offers its tools under its entry name, over
  streamable_http. This is the test that fails if the replacement
  client stops working.
- A tool call answers with its text.
- A URL nobody listens on does not fail the start: the manager logs
  and stays down, the way a dead stdio command does.

Logic that is transport-independent (name sanitization, timeouts,
registry routing, mark-down on a failed call) stays covered once,
over stdio, where it already lives. The header-delivery test in
`test_secret_resolution.py` keeps its current home and stub-server
shape, minus the filter: it asserts what arrives on the wire, which
the in-process FastMCP server cannot see.

### One milestone, one PR

The swap and its coverage land together: splitting them would leave
either the deprecated call alive after its filter is gone (the unit
lane turns the warning into an error at the next SDK bump) or new
tests asserting a client the server does not yet use. The diff is
one function's body, one dependency line, one test file added, one
filter removed. `main` stays releasable at the merge, as the image
publish on push requires.

## Files touched

```
samtal-server/samtal_server/tools/mcp.py        _connect, imports
samtal-server/pyproject.toml                    mcp 1.2 -> >=1.24,<2
samtal-server/uv.lock                           relocked, same versions
samtal-server/tests/unit/test_tools_mcp_http.py new
samtal-server/tests/unit/test_secret_resolution.py  filter and comment removed
CHANGELOG.md                                    2026-08-13 entry
docs/plans/2026-08-13-mcp-streamable-http-client.md
docs/plans/2026-08-13-mcp-streamable-http-client-implementation.md
```

`config.example.yaml` is untouched: no configuration key changes
shape or meaning.

## Verification

- `uv run ruff check .`, `uv run pytest tests/unit -q`,
  `uv run pytest tests/integration -q`, all from `samtal-server/`.
- `uv lock --check` passes: the committed lock agrees with the
  edited requirement, so the frozen image build resolves.
- `grep -rn streamablehttp_client samtal-server` finds nothing: no
  call site and no lingering import of the deprecated name.
- The unit lane's `filterwarnings = ["error", ...]` is itself the
  standing regression guard: with the per-test filter gone, any
  future deprecation of the replacement client fails the new
  connect test instead of hiding until the SDK removes the symbol.

## Risks and mitigations

- **Policy drift in the swap.** Passing our own client means the
  SDK's defaults no longer apply; forgetting `follow_redirects` or
  the long read timeout would change behavior for deployments with
  redirecting proxies or long-lived streams. Mitigation: the values
  are stated as literals copied from the deprecated wrapper, with
  the comment naming the source, and the header test plus the new
  connect tests exercise the built client end to end.
- **In-process uvicorn flakiness.** A test HTTP server brings
  lifecycle and port questions into the unit lane. Mitigation: port
  0 delegates the free-port choice to the OS with no probe-then-bind
  race; startup awaits `server.started` with a timeout; teardown
  sets `should_exit` and awaits the serve task, so no socket
  outlives a test. The lane already runs a threaded HTTP stub (the
  header test), so a local socket in unit tests is established
  practice.
- **The floor bump breaks a stale environment.** An environment
  resolved against an old `mcp` cannot import the replacement.
  Mitigation: `uv.lock` already pins 1.28.1, `uv sync` is the
  documented setup path, and the floor now states the true
  requirement instead of understating it.

## Plan review round

One external review of the plan as first committed (30fed2d): codex
CLI 0.147.0, model gpt-5.6-sol, read-only against this repository
with the pinned SDK synced into the worktree venv and the issue #98
body supplied, 2026-08-13. Verdict: ready after the P1/P2
amendments. Findings as received, condensed; each carries its
resolution once the amendment addressing it lands.

1. **P1: the proposed dependency range admits incompatible MCP
   2.x.** `mcp>=1.24` has no upper bound, and MCP 2.0 is already
   published as stable: it recommends `<2` for unmigrated v1
   clients, requires httpx 2, and its transport yields two streams
   where `_connect` unpacks three, so a fresh resolve of the
   published requirements could pick 2.x and fail at startup while
   CI stays green on the lock. Require `mcp>=1.24,<2` and record
   that MCP 2 migration is separate work.
   *Resolution*: adopted. The dependency decision is now
   `mcp>=1.24,<2`, with the cap's reasons stated (stable 2.0,
   `<2` recommended by its own metadata, httpx 2, a two-stream
   transport), and MCP 2 migration named as separate work; the
   files-touched list and the milestone carry the capped range.
2. **P2: `uv.lock` must change with the dependency constraint.**
   The plan says the lockfile does not change, but the lock's
   project metadata records the requirement string (`mcp>=1.2`
   today), and frozen image builds treat the lock as authoritative.
   Regenerate and commit `uv.lock` (still resolving mcp 1.28.1)
   and add `uv lock --check` or equivalent to verification.
   *Resolution*: adopted. The dependency decision now says the
   lock is relocked and committed in the same change (same
   resolved versions, metadata delta only), the files-touched
   list carries `uv.lock`, and verification gains
   `uv lock --check`.
3. **P2: the tests do not verify the new caller-owned client
   responsibilities.** A supplied client is not managed by the
   replacement transport; the plan takes over closure, redirects,
   and the timeout values, yet its three tests would all pass with
   `follow_redirects=False`, default httpx timeouts, or a client
   never closed, contradicting the claimed mitigation. Add a
   redirecting handshake test, assertions on the constructed
   client's timeout values, and lifecycle assertions that the
   client closes after both a normal stop and a failed connect.
4. **P2: the FastMCP fixture must account for its one-shot session
   manager.** `streamable_http_app()` memoizes one session manager
   on the `FastMCP` instance, and that manager's `run()` may be
   entered only once, so a shared server instance breaks the
   second live-server test. Create a fresh `FastMCP` instance and
   app inside each function-scoped fixture invocation, or scope
   one server with an explicitly compatible event-loop scope.
5. **P2: the deprecated-name acceptance command cannot succeed.**
   `grep -rn streamablehttp_client samtal-server` traverses
   `.venv`, where the pinned SDK necessarily defines the deprecated
   wrapper. Scope the check to first-party code
   (`samtal_server` and `tests`), expecting no matches.

## Milestones

- [ ] **Switch the streamable_http transport to the replacement
  client and cover it** (PR TBD): `_connect` builds the
  caller-managed `httpx.AsyncClient` (headers, the wrapper's
  timeout and redirect defaults, with the comment naming their
  origin) on the manager's exit stack and hands it to
  `streamable_http_client`; the `mcp` constraint moves to
  `>=1.24,<2`; the
  per-test `DeprecationWarning` filter and its comment leave
  `test_secret_resolution.py`; `tests/unit/test_tools_mcp_http.py`
  arrives with the in-process uvicorn fixture and the three
  transport cases; CHANGELOG entry under 2026-08-13; the
  implementation doc section written in the change that ticks this
  box. Accept: lint and both lanes green; the deprecated name
  absent from the tree; no `DeprecationWarning` filter anywhere in
  the suite.
