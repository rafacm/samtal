# Move the MCP streamable_http transport off the deprecated client implementation

Companion to
[`2026-08-13-mcp-streamable-http-client.md`](2026-08-13-mcp-streamable-http-client.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out.

## Milestone 1: switch the streamable_http transport to the replacement client and cover it

`_connect` no longer calls the SDK's deprecated HTTP client. It builds
its own `httpx.AsyncClient`, hands it to the replacement transport, and
the transport now has tests of its own, including tests of the HTTP
policy the swap made samtal's responsibility.

### What landed

**`samtal_server/tools/mcp.py`.** The import moves from
`streamablehttp_client` to `streamable_http_client`, `httpx` joins the
imports, and the `streamable_http` arm of `_connect` builds the client:
the resolved headers (or `None` when there are none, as before),
`follow_redirects=True`, and `httpx.Timeout(30.0, read=300.0)`. The
client is entered on the manager's `AsyncExitStack` before the
transport, so unwinding closes the transport first and the client after
it, in the one task the module header's lifecycle rule requires. The
comment above it says the values come from the SDK's
`create_mcp_http_client`, explains that the long read timeout is the
old wrapper's `sse_read_timeout` and is deliberately longer than
`CONNECT_TIMEOUT_S`, and says why the client is entered where it is.
Nothing else in the module moved: the stdio arm, the timeouts, the
publishing and the reconnection are untouched.

**`samtal-server/pyproject.toml` and `uv.lock`.** `mcp>=1.2` becomes
`mcp>=1.24,<2`, with a comment giving both halves their reason. `uv
lock` was rerun and the lock committed with the edit; the delta is one
line of project metadata (`{ name = "mcp", specifier = ">=1.24,<2" }`)
and no resolved version moved, mcp included, which stays 1.28.1.

**`tests/unit/test_secret_resolution.py`.** The
`@pytest.mark.filterwarnings("ignore::DeprecationWarning")` on
`test_a_stored_header_reaches_a_real_request` and the six-line comment
above it explaining why it was there are gone. The test body is
untouched and still passes, which is the first evidence that the new
client delivers the configured headers on the wire.

**`tests/unit/test_tools_mcp_http.py`.** New. A function-scoped fixture
builds a fresh `FastMCP`, registers the two module-level tool functions
on it, and serves `streamable_http_app()` from a `uvicorn.Server` on
`127.0.0.1` port 0, started as an asyncio task, awaited until
`server.started` (and failing fast if the serve task finishes first),
with the port read from the bound socket. Teardown sets `should_exit`
and awaits the task. Seven tests use it or stand beside it:

- the started manager offers `tools__secret_word` and `tools__add`
  under its entry name, with the description and schema intact;
- a call answers with its text, for both tools;
- a URL nobody listens on (a port bound and released, so it was free a
  moment ago) leaves the manager down, tool-less and raising
  `McpServerDown`, without failing the start;
- a `ThreadingHTTPServer` in front of the real server answering every
  method with a 307 to it still ends with the manager listing tools;
- a capturing subclass of `httpx.AsyncClient`, monkeypatched over the
  real one, records the constructor arguments and delegates, and the
  test asserts `follow_redirects is True` and
  `timeout == httpx.Timeout(30.0, read=300.0)`;
- the same capture asserts `is_closed` on the one constructed client
  after a normal `stop()`;
- and again after a connect that fails past client construction, where
  a stub answers the handshake POST with a 404.

**`CHANGELOG.md`.** One entry under the existing `## 2026-08-13`,
`### Changed`, covering the swap, the unchanged HTTP policy, the
retired per-test filter and the dependency constraint.

### Deviations from the plan

Three, all small, none changing what the milestone does.

1. **The origin comment names `create_mcp_http_client`, not the
   deprecated symbol.** The plan asks for a comment naming where the
   literals come from, and its acceptance asks that
   `grep -rn streamablehttp_client` over `samtal_server` and `tests`
   find nothing. Spelling the deprecated function in the comment
   satisfies the first and breaks the second. The comment therefore
   names the SDK factory the deprecated wrapper built its client with,
   which is the actual source of the values, and says it is what the
   wrapper this replaced used. Both the intent and the acceptance
   command hold.
2. **Six described cases are seven test functions.** The plan's last
   bullet describes closure as one case asserted after a normal stop
   and after a failed connect. Those are two different servers and two
   different lifecycles, so they are two tests sharing the capture
   helper the plan allows, rather than one test with a second server
   bolted into it.
3. **The fixture does two things the plan did not anticipate**, both
   forced by the lane rather than chosen: it clears and restores
   `sse_starlette`'s process-wide shutdown flag, and the module ignores
   one `ResourceWarning`. Both are discoveries and are written up
   below.

### Discoveries

**A process-wide shutdown flag in `sse_starlette` makes this suite
order-dependent.** The new tests passed on their own and five of the
seven failed in the full unit lane, with the server logging `ASGI
callable returned without completing response` and the manager
reporting an unhandled TaskGroup error. The cause is not in samtal's
client. `sse_starlette` monkeypatches `uvicorn.Server.handle_exit` at
import to set a class attribute, `AppStatus.should_exit`, and nothing
ever clears it. `tests/unit/test_drain.py` calls `handle_exit` directly
on its own `uvicorn.Server` subclass to test the drain, that call
chains to the patched base, and the flag stays set for the rest of the
pytest process. While it is set, every `EventSourceResponse` in the
process returns immediately without sending anything, and an SSE
response is how the MCP server answers a POST, so the test server
accepted connections and completed no request. The fixture now saves
the flag, clears it for the length of the test, and puts it back, so
this module neither depends on suite order nor changes the flag for
anybody else. Reproducing it takes only the two modules together:
`uv run pytest tests/unit/test_drain.py tests/unit/test_tools_mcp_http.py`,
which failed five tests before the fix and passes 21 after it. None of
this reaches a running deployment: samtal serves no SSE of its own, and
setting the flag when a real signal arrives is precisely what
sse_starlette means to do.

**The SDK's server transport leaks a memory stream per request, and
the lane turns that into an error.** `_handle_post_request` and
`_handle_get_request` in `mcp/server/streamable_http.py` create an SSE
`anyio` memory object stream, hand the reading end to the response, and
never close it, so anyio's finalizer emits `ResourceWarning: Unclosed
<MemoryObjectReceiveStream ...>`. Under the lane's `filterwarnings`,
which starts with `"error"`, that becomes an unraisable exception
inside `__del__`, which pytest re-raises as a
`PytestUnraisableExceptionWarning`, failing whichever test happens to
be running when the garbage collector gets there. This was confirmed by
instrumenting `anyio.create_memory_object_stream` in a scratch script
and printing the creation stack of every stream still unclosed at
finalization: every one of them was created on the server side, inside
the SDK, and none in the client path. The narrowest honest treatment is
in the new module and nowhere else: a `pytestmark` ignoring that one
`ResourceWarning` message, plus a `gc.collect()` at the end of the
fixture so the finalizers run inside this module's filters rather than
inside someone else's test. It is not a deprecation filter, and it is
not about the client under test, whose closure the module asserts
directly. It is also load-bearing rather than defensive: the module's
first run, written without it, failed five of its seven tests on
exactly these warnings, and with the ignore swapped for a filter that
reports them instead of erroring, one pass counts 17 of them.

**The client swap needed no change to the header test.** The
header-delivery test in `test_secret_resolution.py` passed unmodified
once the filter came off, which is the wire-level confirmation that the
headers `_connect` now puts on its own client arrive exactly as the
SDK's factory used to deliver them.

### Verification

All commands from `samtal-server/`, on the milestone's final tree.

- `uv run ruff check .`: "All checks passed!".
- `uv run pytest tests/unit -q`: 1432 passed, 15 skipped in 126.49s, of
  which the seven new tests are this milestone's addition.
- `uv run pytest tests/integration -q`: 44 passed in 102.24s.
- `uv lock --check`: "Resolved 104 packages", no error, so the
  committed lock agrees with the edited requirement and a frozen build
  resolves.
- `grep -rn streamablehttp_client samtal_server tests`: no matches
  (exit 1). The check is scoped to first-party code because the synced
  venv necessarily contains the SDK's own definition of the deprecated
  wrapper.
- `grep -rn filterwarnings tests`: one match, the new module's
  `ignore:Unclosed <MemoryObject:ResourceWarning`. No
  `DeprecationWarning` filter remains anywhere in the suite, and
  `grep -rn DeprecationWarning samtal_server tests pyproject.toml`
  finds nothing.
- The two policy tests were checked for teeth rather than assumed:
  with `follow_redirects=False` and `httpx.Timeout(5.0)` substituted in
  `_connect`, the redirect test and the capture test both fail and the
  other five pass. The source was then restored from a copy taken
  before the edit, not with `git checkout`.
- CI was not run from here. It runs the same lint, unit and integration
  steps on the PR.

The numbers above are the state at the PR's first push. The review round
below adds two tests, strengthens a third, and changes what the
manager's warning prints; its own verification is recorded with it.

### PR #123 review round

One external review of the pull request's diff (`main...41e7b78`):
codex CLI 0.147.0, model gpt-5.6-sol, read-only, 2026-08-13. Two
findings, one P1 and one P2, each fixed in its own commit. Findings as
received, condensed, each with its resolution.

1. **P1: the MCP SDK can put server-controlled values and tracebacks
   into the logs.** The call at `mcp.py:205` delegates to the pinned
   transport, whose module logs the session id a server chose
   (`streamable_http.py:181`), the raw result of an initialization
   response that will not parse (`:195-198`), and a parsing failure
   through `logger.exception` (`:393-395`). `logs.py:54` serializes a
   traceback into the structured log, and a malformed response makes
   Pydantic quote the rejected bytes inside the exception, so a
   malicious or broken MCP server can write to the observability
   surface. The manager's own `_run` interpolates the exception object
   as well (`mcp.py:153`). Suggested: suppress or sanitize the SDK
   client's records, replace the interpolation with application-owned
   reason codes, and add malicious-handshake tests asserting the
   sentinel bytes, the session id, the traceback, `exc_info` and the
   exception chain are all absent.
   *Resolution*: adopted, in the narrow form, in 8482178. The mechanism
   is older than this PR, and the finding says so: the deprecated
   wrapper delegated to the same module and the same logger, and the
   interpolation predates the swap. It is fixed here because this is
   the transport's review moment and JSON log events are a public
   surface by ADR. The SDK client's logger takes a filter that passes
   nothing, installed in `tools/mcp.py` where the transport is used, so
   its records stop before any handler of ours is reached; this is the
   reasoning that already keeps uvicorn's access log off in `main.py`.
   The unavailability warning keeps its sentence and prints a reason
   token built from exception types, unwrapping the group the transport
   raises through, so a malformed handshake now reads "mcp server
   weather is unavailable, its tools are absent: ValidationError". The
   test drives a real connect against a stub that answers the handshake
   with a well-formed envelope around a result that is not an
   `InitializeResult`, echoing the request id so the client accepts it,
   and asserts the sentinel appears in neither `caplog.text` nor the
   `JsonFormatter` rendering of every record (which is where a
   traceback would land), that no record from the SDK client's logger
   survives, that no record carries `exc_info`, and that our own
   warning still names the server. A unit test pins the reason token.
   Three parts of the prescription were not taken, each for a reason
   worth recording. Sanitizing the SDK's records was rejected in favour
   of dropping them: the messages are already interpolated f-strings,
   so sanitizing means pattern-matching another library's prose, and
   what an operator needs from those lines this module already writes.
   The guard names `mcp.client.streamable_http` only, rather than the
   `mcp` namespace or every third-party logger, because a guard that
   silences loggers nobody has read is a guess. And no new structured
   field or event was added, since event names and fields are a
   compatibility surface by the same ADR and the finding needs none.
2. **P2: the dead-server test asserted only manager state.** The plan
   promised a URL nobody answers would leave the manager logging and
   staying down, and the test checked `up`, the empty tool list and the
   refusal to call, none of which can tell a warning from silence.
   Suggested: capture logs and assert one stable application warning,
   pinned to our logger and level rather than to the wording, with no
   exception object and no `exc_info`.
   *Resolution*: adopted in 8ab0a18. The test captures across the start
   and requires exactly one record from `samtal_server.tools.mcp` at
   `WARNING`, naming the entry that went down, with `exc_info` unset.
   The sentence itself is not asserted, so wording stays free.

**What the round turned up and this PR did not fix.** At `DEBUG`,
`httpcore` logs the headers of every response any `httpx` client in the
process receives, MCP servers and the cloud LLM providers alike, which
puts a session id (and whatever else a response header carries) in the
log of a deployment that turns debug logging on. That is a property of
the debug level across the whole server rather than anything this
transport decides, it predates this PR, and narrowing it would take a
decision about how much of a debugging tool to keep. It is recorded
here rather than fixed, and the leak test scopes its capture to the SDK
client's logger for exactly this reason, so that it asserts what this
change guarantees and not more.

### Verification after the review round

Same commands, from `samtal-server/`, on the tree at 8ab0a18.

- `uv run ruff check .`: "All checks passed!".
- `uv run pytest tests/unit -q`: 1434 passed, 15 skipped in 127.70s.
  Two more than before the round: the malformed-handshake test and the
  reason-token test. The dead-server test was strengthened rather than
  added.
- `uv run pytest tests/integration -q`: 44 passed in 103.01s.
- `uv lock --check`: "Resolved 104 packages", no error. No dependency
  moved in this round.
- `grep -rn streamablehttp_client samtal_server tests`: still no
  matches (exit 1).
- The leak guard was checked for teeth: with the filter line removed
  and the exception interpolated again, the malformed-handshake test
  fails and the other eight pass. The source was restored from a copy,
  not with `git checkout`.
