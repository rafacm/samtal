# Split tools/mcp.py and seat one ToolSource under the three sources: implementation

Companion to [`2026-08-17-mcp-split.md`](2026-08-17-mcp-split.md). One
section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out.

## The inventory, taken fresh at main@51bf990

The issue's evidence is pinned to main@8dd1a5f, before the #155 emitter
migration and the reload-token work. The figures below were taken at
51bf990, which is the commit this branch is based on, and they are what
the plan's evidence section cites. Recorded here because every
milestone's design rests on them, and because a number that moves under
a later milestone is a finding rather than a typo.

**2,338 lines, one file.** `McpServerManager` spans lines 307 to 1183,
877 of them, of which the #122 prompts and guidance capture is 313
(lines 769 to 1081, the section comment included). `McpServers` spans
1725 to 2338. The remainder is 45 module constants, four public
exception types, one private one, and thirteen module-level helpers.

**Eight emit sites on one pinned channel.** `ServerEvents(__name__)` at
mcp.py:77, and the sites are `McpServerManager._run` (three:
`mcp_connected`, the failure `mcp_down`, the stopped `mcp_down`),
`McpServerManager._mark_down` (two: `mcp_call_dropped` and its
`mcp_down`), `McpServers._reachable` (`mcp_tool_shadowed`),
`McpServers._refused` and `McpServers._apply` (the two `mcp_reload`
outcomes). The plan's prose says six of them are the manager's; five
are, and the eighth is the registry's `_reachable`. Corrected here
rather than left to be rediscovered.

Their destinations, which is what the sidecar update is:

| Site at 51bf990 | Destination |
| --- | --- |
| `McpServerManager._run` #1..#3 | `…tools.mcp.manager` |
| `McpServerManager._mark_down` #1..#2 | `…tools.mcp.manager` |
| `McpServers._reachable` #1 | `…tools.mcp.registry` |
| `McpServers._refused` #1 | `…tools.mcp.reload`, as `_refused` |
| `McpServers._apply` #1 | `…tools.mcp.reload`, as `_apply` |

**The channel is a compatibility surface four ways**: the retained
records' `logger` field (the 2026-08-04 ADR), five hardcoded pins in
`test_server_event_pins.py`, #155's `SERVER_CHANNELS` literal and eight
`EventVariant.channel` values, and six test files filtering caplog by
`MANAGER_LOGGER = "samtal_server.tools.mcp"`
(`test_tools_mcp.py`, `_http`, `_prompts`, `_reload`,
`test_mcp_status_reflection.py`, `tests/support/tools_mcp.py`).

**The test surface, by import.** Nine files import `McpServers`;
`test_tools_mcp.py` imports 14 names, `test_tools_mcp_reload.py` 16,
`test_tools_mcp_prompts.py` 19, `test_tools_mcp_http.py` 6,
`test_config_api_runtime.py` 6, `test_mcp_status_reflection.py` 3,
`test_config_cli_rendering.py` 2, `test_secret_resolution.py` 1 plus
`transport` after M1. `test_config_cli_transport.py` reads three
timeout constants off the module and sums them. The plan brief's
figures (16/17/20) were one or two high per file; the union is what
matters and it is 74 re-exported names.

**Module-attribute reaches, and which survive a re-export.** Nine
sites: `mcp_module.stdio_client` (read and patched),
`CONNECT_TIMEOUT_S` (patched in `_http`, read in `_transport`),
`STOP_TIMEOUT_S` and `CANCEL_TIMEOUT_S` (read in `_transport`,
`CANCEL_TIMEOUT_S` patched in `_reload`), `PROMPT_CALL_TIMEOUT_S` and
`PROMPT_DISCOVERY_TIMEOUT_S` (patched), `_rendered` and `TOO_LONG`
(read), `_abandoned` (read three times). A re-export copies a binding,
so it serves reads and imports and never a rebinding: the five reads
are untouched by M1 and the four patches move.

**Instance reaches at methods that stop being methods.** Four, which
the plan's inventory did not name: `manager._resolve("env")`
(`test_tools_mcp.py:369`),
`McpServerManager("weather", config)._resolve("headers")`
(`test_secret_resolution.py:305`), `manager._discovered(...)`
(`test_tools_mcp_prompts.py:331`) and `manager._injectable(...)`
(`test_tools_mcp_prompts.py:557`). All four are ports.

**The private reaches M4 inherits:** `_managers` (7 sites plus the
guidance integration test), `_config` (5), `_session` (4), `_reloading`
(1), `_task` (2), and `StubbornManager`/`SlowStopManager` subclassing
`_run`, `_became`, `_settled` and `_stop`
(`test_tools_mcp_reload.py:688-770`). M1 adds none and retires one: the
resolver port took `servers._managers["tools"]` out of
`test_tools_mcp.py`, leaving six unit sites and the integration one.

## M1: the package with the full split

Three commits, each leaving both lanes green and the package
importable: the conformance machinery amendment first, on its own and a
no-op for the single-module world it was written for; the split itself,
which is where the sidecar, the value sources and the test ports move
with the code; and the log quieting, separable because the split kept
the import-time statements verbatim so that this commit could be read
as the one behavioral change it is. Four more landed in the PR review
round, recorded at the end of this section.

### The package, and the two logger rules

`tools/mcp.py` is `tools/mcp/__init__.py`, which IS the module
`samtal_server.tools.mcp`. It builds `ServerEvents(__name__)`, so the
channel string is unchanged by construction rather than by care, keeps
`logger = logging.getLogger(__name__)` beside it (the package logger,
which is the same object a submodule's `logging.getLogger(__package__)`
answers with), and re-exports **74 names**: `__all__` holds 76, the two
others being `events` and `logger` themselves. Its docstring states the two
rules the split rests on: events go through the package emitter, which
a submodule takes with `from . import events` and no other way; ordinary
prose records go through the package logger, `__package__` and never
`__name__`.

The submodule imports sit after the emitter rather than above it, since
each of them takes `events` from the package, and carry `# noqa: E402`
with the comment that says why.

| Module | Lines | Responsibility |
| --- | --- | --- |
| `__init__.py` | 238 | the emitter, the 74 re-exports, the two rules |
| `transport.py` | 285 | bringing a connection up, and classifying failure |
| `prompts.py` | 471 | the #122 capture, under its bounds |
| `manager.py` | 777 | one server's lifecycle, and the abandonment plumbing |
| `slice.py` | 201 | the configuration a registry was built from |
| `reload.py` | 423 | the two phases, as functions over the registry |
| `registry.py` | 455 | what needs the managers and the slice together |

**Deviation: `manager.py` is 777 lines, not "under roughly 500".** The
plan's own arithmetic implies it: the class is 885 lines at plan time
and 307 of those are the capture, which leaves 578 for the class alone,
before its module's imports, the four timeout and status constants, the
three exception types it raises and the task-abandonment plumbing the
plan puts here. Nothing was left in it that had somewhere else to be:
`_resolve` and `_connect` went to `transport`, the whole capture except
its two call sites went to `prompts`, and the only way further down is a
seventh module for the stop and its abandonment, which is a boundary
this plan did not draw and M1 is not the place to invent. Recorded as
the one criterion M1 misses, and closed by the PR review round below,
which accepted the file at this size with no seventh module asked for.

**Deviation: `_managers_for` lives in `manager.py`, not `registry.py`.**
The plan's brief lists it under the registry. It builds
`McpServerManager`s, and putting it there is also what keeps the import
graph acyclic with every import at module level:
`transport → prompts → slice → manager → reload → registry`. With
`_managers_for` in the registry, `reload._prepared` would import the
registry and the registry would import the reload, which costs a
deferred import inside a method to break. The plan's own sentence
attributes the egress check to `build` "via `_managers_for`", which
still holds.

**Deviation: `_capture` stayed on the manager.** The plan lists it among
`prompts.py`'s contents. It is 42 lines and it writes two manager fields
either side of an await: `self._instructions` before the prompt
discovery runs and `self._prompts` after it. A `capture()` returning
both would land them together, which is a different world for anything
reading `shipped_instructions` during a background reconnect's discovery
phase, and the plan's harder rule is that nothing behaves differently.
So `_capture` is the manager's 42-line orchestrator and everything it
calls (`_redactor`, `_injectable`, `_discovered` and the eleven
functions under them, 249 lines) is in `prompts.py`, called at the same
point with the same arguments. `_announce_shipped` stayed with it, for
the same reason: it reads the two fields.

**Shape change, sanctioned by the brief: `_install`.** `_apply`'s
five-field atomic swap is one registry method now, performing the five
assignments with no await, with the swap comment as its docstring plus a
sentence saying why it is a method: the apply that calls it is in
another file, and a no-await contract resting on the order of somebody
else's statements is a contract nobody can check.

**Shape change: three signatures take the entry instead of `self`.**
`_resolve(name, config, secrets, group)`,
`_connect(name, config, secrets, stack, reached)` and the prompt
functions take the entry's name and configuration as arguments, which is
what the plan asked for. `_discover`'s loop variable `name` was renamed
`listed_name`, since `name` is the entry's there now.

### The #155 machinery amendment

A channel is owned by a module or by a package. `unowned()` states the
whole rule in one place, so the suite and the planted cases run the same
decision: a module owns its channel by building `ServerEvents(__name__)`
in it; a package owns one the same way in its `__init__`, whose
`__name__` IS the package name, and its submodules emit on it by taking
that emitter with `from . import events`. A relative import of depth one
names the module's own package by construction, which is what makes
"only its own package's emitter" a check by path rather than by a name
any module could write.

The walk moved with it: `_Walk.visit_Module` resolves the channel from
the module's own imports before any call is looked at, so a submodule's
sites are recorded under the owning package's channel while `Site.module`
stays the file the source is in; `module_name()` normalizes a package's
`__init__.py` to the package, so the name is what `__name__` holds and
what the emitter's channel says; and `module_source()` finds the text of
a name that is a package.

Five planted cases, accepted and rejected alike:

- a module that builds its own emitter owns that channel, and does not
  own its package's;
- a submodule may emit on the channel its own package owns;
- three rejected shapes in one test: an absolute import of another
  package's emitter, a package that builds none, and a submodule that
  takes its package's emitter and emits on a third name;
- the walk records a submodule's emission on its package's channel and
  a self-built one on its own;
- a package's `__init__` is named for the package, in `module_name` and
  in `module_source` both.

The sidecar moved the eight identities per the table above.
`TOKEN_SOURCES` moved with them: `_down_reason`, `STOPPED` and
`CALL_FAILED` to `transport`, `APPLIED`, `REFUSED`, `_refusal` and
`_refused` to `reload`. The phase marker is **two** entries now, which
is a finding: `reached(INITIALIZE_FAILED)` is inside `_connect` and
`reached(DISCOVERY_FAILED)` inside `_run`, and those are in different
files now, so one `Decides` cannot see both. `_refused`'s entry lost its
`scope="McpServers"` because it is a module-level function.

`SERVER_CHANNELS`, the `EventVariant` channels in `events_schema.py`,
`test_server_event_pins.py` and `test_event_surface_pins.py` are
byte-unchanged, proven by an empty `git diff --stat` over the three
files.

### The log quieting

`quiet_sdk_loggers()` in `transport.py`, same four filters, same
`propagate = False`, same rationale comment now inside the function.

It was first called from `McpServerManager.start` and from
`McpServers.build`, which the review's first finding showed to be the
wrong boundary: `ensure_reconnecting` comes straight to `_begin`, so a
process whose first connect is a background reconnect connected with
`mcp` still propagating. It is called from `_begin` now, which is the
one place a connection is ever begun, and the registry's call went with
the move because there is nothing left for it to cover. Nothing asserts
on the state at import time, so nothing needed porting for it; the
regression that pins the boundary is described in the round below.

### The port table

Every modified test line, with the reason. Nothing else in these files
moved.

| File | Line at 51bf990 | Was | Is | Why |
| --- | --- | --- | --- | --- |
| `test_mcp_status_reflection.py` | 38 | `import samtal_server.tools.mcp as mcp_module` | `…import CONNECTED, REDACTED, McpServers, transport` | the only two uses of `mcp_module` are the two below |
| `test_mcp_status_reflection.py` | 279 | `spawning = mcp_module.stdio_client` | `spawning = transport.stdio_client` | `stdio_client` is called from `transport._connect` |
| `test_mcp_status_reflection.py` | 285 | `setattr(mcp_module, "stdio_client", …)` | `setattr(transport, …)` | the contract's one exercised allowance; the test still asserts exactly one sink was spawned |
| `test_tools_mcp_http.py` | 28 | `import samtal_server.tools.mcp as mcp_module` | `manager as manager_module` in the existing block | same, one use |
| `test_tools_mcp_http.py` | 440 | `setattr(mcp_module, "CONNECT_TIMEOUT_S", 0.3)` | `setattr(manager_module, …)` | the bound is read in `manager._run`; a cross-module import copies the binding as a re-export does |
| `test_tools_mcp_reload.py` | 28 | — | `+ from …mcp import manager as manager_module` | for the line below |
| `test_tools_mcp_reload.py` | 720 | `setattr(mcp_module, "CANCEL_TIMEOUT_S", 0.05)` | `setattr(manager_module, …)` | read in `manager.stop` |
| `test_tools_mcp_prompts.py` | 29 | — | `+ from …mcp import prompts` | for the four lines below |
| `test_tools_mcp_prompts.py` | 575-576 | `setattr(mcp_module, "PROMPT_CALL_TIMEOUT_S"/"PROMPT_DISCOVERY_TIMEOUT_S", …)` | `setattr(prompts, …)` | read in `prompts._bounded` and `prompts._discover` |
| `test_tools_mcp_prompts.py` | 328-333 | `manager = McpServerManager(…); await manager._discovered(session, capabilities, …)` | `await prompts._discovered("tools", stdio_entry(**overrides), session, capabilities, …)` | `_discovered` takes the entry rather than reading a manager; the helper's docstring says "entry" where it said "manager" |
| `test_tools_mcp_prompts.py` | 553-557 | `manager._injectable(huge, "instructions")` | `prompts._injectable("tools", huge, "instructions")` | same; the manager construction went with it, and the warning still names `mcp server tools` |
| `test_tools_mcp.py` | 25, 365-369 | `servers._managers["tools"]`, then `manager._resolve("env")` | `McpServers.build(config)` unbound, then `transport._resolve("tools", config.mcp_servers["tools"], None, "env")` | asks the resolver the connection asks, over the entry the manager was built from; the literals rather than the manager's fields, so the port introduces no reach M4 has to map |
| `test_secret_resolution.py` | 32, 305 | `McpServerManager("weather", config)._resolve("headers")` | the construction, then `transport._resolve("weather", config, None, "headers")` | the construction is half of what the test is about (an unset reference fails the boot below it), so it stayed |

Reads left alone because a re-export carries them:
`mcp_module._abandoned` (three sites, the same set object, mutated in
place), `mcp_module._rendered` and `mcp_module.TOO_LONG`, and
`test_config_cli_transport.py`'s sum of `CONNECT_TIMEOUT_S`,
`STOP_TIMEOUT_S` and `CANCEL_TIMEOUT_S`. `servers._reloading` is an
instance attribute and was never affected.

### Verification

From `samtal-server/`, `uv` throughout, `PYTHONDONTWRITEBYTECODE=1`
outside pytest.

- `uv run ruff check .`: **All checks passed!**
- `uv run pytest tests/unit -q`: **2945 passed, 16 skipped in
  312.01s**. Collected 2961 against 2954 at the base commit, and the
  seven are all tests M1 wrote: the five planted conformance cases, the
  sixth planted by the review round, and the reconnect regression. A
  move adds no test, and the ports rewrote lines inside tests that
  already existed.
- `uv run pytest tests/integration -q`: **55 passed in 158.19s**,
  unchanged in count.
  `test_mcp_reload.py`'s black-box reload proof needed nothing, as the
  plan said it would not.
- The reflection sentinels
  (`test_mcp_status_reflection.py`, 8 tests) pass, including
  `test_a_child_that_writes_where_it_likes_reaches_no_operator_surface`,
  which is the proof that the quieting is in force before the first
  real subprocess connect on the start path, beside the reconnect
  regression that proves the same of the other one.
- `uv run samtal-server events reference` diffs clean against
  `docs/reference/events.md`, and byte-identically against the same
  document generated at the base commit: the channel did not move.
- `git diff --stat` over `test_server_event_pins.py`,
  `test_event_surface_pins.py` and `events_schema.py` is empty.
- `wc -l` per module is the table above.

### The PR review round

External review of PR #178 by the pipeline's reviewer, 2026-08-17.
Three findings, all valid, one commit each; the verification above was
re-run whole afterwards and is the result recorded. The reviewer
accepted `manager.py` at its size with no seventh module asked for,
which closes the flagged deviation above.

1. **P1: the public reconnect path bypasses the SDK quieting.**
   `quiet_sdk_loggers()` was the first act of `McpServerManager.start`,
   but `ensure_reconnecting` comes straight to `_begin`, so a process
   whose first connect is a background reconnect connects with `mcp`
   still propagating and the SDK's records reaching every handler. The
   CHANGELOG sentence claiming every path was covered was false with
   it.

   *Resolution* (`cd88749`): the call moves to `_begin`, the one place
   a connection is ever begun, since both public entries come through
   it and neither calls the other. `McpServers.build`'s call goes with
   it rather than staying a second guard: with the boundary there,
   nothing is left for it to cover. The CHANGELOG sentence and
   `quiet_sdk_loggers`' own docstring are corrected to name the
   boundary that holds. The regression,
   `test_a_background_reconnect_quiets_the_sdk_before_it_connects`,
   drives a manager through `ensure_reconnecting` having never started
   it, over an `unquieted` fixture that puts the process-wide state
   back to a process that has quieted nothing, and asserts both halves:
   propagation already off before anything has been awaited, which is
   the ordering, and no `mcp.*` record in capture across a real connect
   and a line written on each SDK logger. On the previous code it fails
   at the ordering assertion (`assert not True`).

2. **P2: the package form accepted a borrowed emitter after a
   rebinding.** The rule asked whether `from . import events` appeared
   anywhere, so a module that took its package's emitter and then
   rebound the name to a foreign one passed while emitting on the
   foreign channel.

   *Resolution* (`066a8ea`): `emitter_bindings` collects every
   top-level statement that binds the name, in source order, and both
   accepted forms require exactly one, by the statement the form
   claims; nested scopes are left alone, since a name rebound inside a
   function is that function's own. The module form is narrowed by the
   same rule, because build-then-rebind is the same hole from the other
   side. Planted in both forms as
   `test_an_emitter_rebound_after_a_lawful_one_owns_nothing`, with the
   walk's channel attribution asserted beside the rule so the two
   halves cannot drift; on the previous rule the case returns the empty
   string, the rule saying the module owns the channel it borrowed.

3. **P3: the resolver port added private reaches M4 does not map.**
   The port read `_name`, `_config` and `_secrets` off the manager,
   which contradicted this section's own claim that no reach was added,
   and `_secrets` is a seam M4 never planned.

   *Resolution* (`cceadb7`): the literals answer the same question, so
   the call is `transport._resolve("tools", config.mcp_servers["tools"],
   None, "env")`. `McpServers.build(config)` stays, unbound, because
   building is what resolves at construction; its `_managers` reach
   went with the change, leaving six unit sites where the inventory
   counted seven.

Found while fixing the above, and fixed as its own commit
(`dcba261`): **the split dropped one line of prose.** The comment over
`DROPPED_AFTER_FAILED_CALL` lost its first sentence to an off-by-one in
the line range it was copied by, so the block began mid-sentence. It
was found by auditing every line of the file at 51bf990 against the
package rather than by reading, which is the check that should have run
before the split was committed; it is the only prose lost in the move.
The two blocks that changed shape rather than place are word-identical
and were verified so: the SDK propagation comment, re-wrapped to sit
inside `quiet_sdk_loggers` at four spaces, and the atomic-swap
contract, which is `_install`'s docstring now.

## M2: the typed status and reload surface

Three commits: the decoder unification first, which touches neither of
the other two files and is the one piece of M2 that is about the wire
rather than about types; then the typed views at the source, which
nothing consumes yet; then the Protocol and the two dependencies, which
is where the API stops saying it was handed `Any`.

### The adapter, and what deliberately did not move

`McpServers.status()` is byte-unchanged and still answers
`dict[str, dict[str, Any]]`. The reflection sentinel indexes those
mappings and `json.dumps`es the whole of one, and every consumer inside
this server reads them as mappings, which is finding 2 of the plan
review and the reason the typed surface is an adapter rather than a
reshaped internal.

`McpServers.typed_status()` is that adapter: one dict comprehension
over `status()`, `McpServerStatus.model_validate` per entry.
Validating rather than constructing is the point of it, and it is
recorded in the method: the models forbid extra keys, so a field this
registry starts answering with and the document does not declare fails
in this server's own unit lane rather than on a client.

`reload.reload_result(servers, read)` composes the endpoint's whole
answer where the two phases live: it awaits `reload(servers, read)`,
then reads `servers.typed_status()` with no await between the two,
which is exactly the invariant the handler used to hold, moved with
the construction it belongs to and stated in the function's docstring.
Refusals are not caught there: the exception types the two phases
raise ARE the contract with the API (409, 422, 500), and they travel
out untouched. `McpServers.reload_result(read)` is the one-line
delegation the composition root hands the API, beside the `reload()`
the suites drive.

**`McpServers.reload()` keeps returning `McpReload`.** Its four tuples
are what `test_tools_mcp_reload.py` asserts on, tuple by tuple, and
`McpReloadResult`'s fields are lists, so folding the two would have
rewritten a suite the contract says not to touch. The registry
therefore has two reload entry points, which is the honest shape:
`McpReload` is this application's own vocabulary and `McpReloadResult`
is what the API sends.

The two files grew by what those views are worth: `registry.py` 455 to
496 lines and `reload.py` 423 to 462, both still at the plan's
roughly-500 criterion. (The 459 first recorded here was read before M1's
own review round, whose fixes moved it.)

### The Protocol

In `config/responses.py`, beside the models it answers in, out of
`typing` and `collections.abc` and those models:

```python
class McpStatusSource(Protocol):
    def typed_status(self) -> dict[str, McpServerStatus]: ...


type McpReloader = Callable[[], Awaitable[McpReloadResult]]
```

`api.py`'s two dependencies are
`Annotated[McpStatusSource | None, Depends(_mcp_servers)]` and
`Annotated[McpReloader | None, Depends(_mcp_reload)]`, the None being
the honest shape both dependency functions already returned for an
application built without a server. `build_api`'s two parameters take
the same types, `app.py`'s `_mcp_reloader` returns `McpReloader`, and
the `TYPE_CHECKING` import of `McpServers` in `api.py` is gone: the
comment that carried its rationale stays and now records that the
constraint holds by construction, since nothing in the module names
the registry at all.

**Deviation: the Protocol's method is `typed_status`, not `status`.**
The plan review's finding 2 says "the `McpStatusSource` Protocol's
`status()` returns `dict[str, McpServerStatus]`", and the amended plan
section names `typed_status()` on the registry as what the API
dependency consumes. Taken literally the first would leave `McpServers`
not satisfying the protocol it is passed as, since its `status()`
answers mappings; the protocol declares the method the registry
actually offers instead, so the conformance is structural and true.

### The reload route keeps both dependencies

`reload_mcp_servers` takes `servers` and `reload` and refuses with 503
if either is None, as it did before M2. The composing left the handler
and the registry parameter did not: it is read for one thing, whether
there is one.

This is the correction of a deviation this milestone made and the PR
review round took back (finding 1 below). The parameter was dropped on
the argument that no composition passes a reload without a registry,
which was true of `app.py` and of every test, and beside the point: a
guard is the endpoint's behavior rather than a note about its callers,
and this endpoint is the one that changes state. With the dependency
gone, `build_api(mcp_reload=..., mcp_servers=None)` applied a
configuration and answered 200 for a reload whose other half nothing
could report on. `test_half_a_runtime_refuses_to_reload_from_either_side`
now pins both directions.

**Discovery: a handler's docstring is committed bytes.** The paragraph
explaining why the composition moved was first written into
`reload_mcp_servers`'s docstring, which FastAPI renders as the
endpoint's `description`; the OpenAPI drift check refused it on the
spot. It is a comment inside the handler now, which says so in its
first sentence so the next person does not learn it the same way.

### The decoders

`protocol/mcp.py` gains `spoken_content(content)`: it takes a
normalized sequence of a content type and the text that type carries
(`None` for content that carries none) and answers the join, with
`[unsupported {type} content]` for the None entries. It lives there
because that module owns the wire shapes and imports nothing of
`tools/`, so `transport.py` imports downward as the rest of the layer
does.

Both callers keep everything that is theirs, which is the five
differences the inventory catalogued: `parse_tool_result` keeps its
dict tolerance (an item that is not an object skipped, an absent
`type` reading `unknown`, a missing `text` reading empty) and its
`tuple[str, bool]`; `_result_text` keeps its typed iteration and its
`str`. Two of them needed care rather than copying. `item.get("type",
"unknown")` defaults only on an ABSENT key, so a `{"type": null}` item
still renders `[unsupported None content]`; the normalization passes
`str(kind)`, which is what an f-string did with the same value. And
the text sentinel is `None` rather than a falsy check, so a text item
whose text is the empty string is still text and still contributes its
empty line. `test_protocol_mcp.py`'s
`test_content_a_voice_assistant_cannot_speak_is_named`, the one pinned
sentence, is unmodified.

### The port table

| File | Was | Is | Why |
| --- | --- | --- | --- |
| `test_config_api_runtime.py` | `outcome(**fields) -> McpReload` | `outcome(servers, **fields) -> McpReloadResult`, the four lists off the dataclass plus `servers.typed_status()` | the stub stands in for the callable the API is handed, whose answer is now the whole reply |
| `test_config_api_runtime.py` | `answering`/`refusing` annotated `McpReload` | annotated `McpReloadResult` | same, in the two stub factories |
| `test_config_api_runtime.py` | `outcome(started=…)` at the one call site | `outcome(servers, started=…)` | the registry the status half is taken from |
| `test_config_api_runtime.py` | `async def reload() -> McpReload: return await servers.reload(read)` | `-> McpReloadResult: … servers.reload_result(read)` | the sanitizing test drives the real path, which is now the result-composing one |
| `test_config_cli_rendering.py` | `_applied(**outcome)` answering `McpReload` | `_applied(servers, **outcome)` answering `McpReloadResult` | same reason: it is a stub of the same callable, and the CLI renders both halves |

Both files gained a `dataclasses.asdict` import and
`test_config_cli_rendering.py` a `McpReloadResult` one; the reload
test's one call site binds the registry to a name to pass it. Nothing
else in either file moved, and no other test file is touched by M2:
`test_tools_mcp_reload.py`, the reflection sentinels and the reload
integration proof are byte-identical to their M1 state.

### Verification

From `samtal-server/`, `uv` throughout, `PYTHONDONTWRITEBYTECODE=1`
outside pytest.

- `uv run ruff check .`: **All checks passed!**
- `uv run pytest tests/unit -q`: **2947 passed, 16 skipped in
  306.04s**, 2963 collected against M1's 2961. The typed views and the
  ports added no test of their own, since a port rewrites lines inside
  a test that already exists; the two are the review round's, the
  reload route's regression and the planted rebinding cases.
- `uv run pytest tests/integration -q`: **55 passed in 157.06s**,
  unchanged in count.
- `uv run samtal-server config openapi` diffs clean against
  `docs/reference/api-openapi.json`, which is the milestone's core
  proof: the wire shapes, the descriptions and the schemas are the
  same bytes with the flattening gone and the dependencies typed.
- `uv run samtal-server events reference` diffs clean against
  `docs/reference/events.md`.
- The reflection sentinels (`test_mcp_status_reflection.py`) and the
  reload integration proof (`tests/integration/test_mcp_reload.py`)
  pass unmodified.

### The PR review round

External review of PR #179 by the pipeline's reviewer, 2026-08-18.
Three findings, all valid, one commit each; the verification above was
re-run whole afterwards and is the result recorded.

1. **P1: an application with half a runtime could execute a reload.**
   `build_api` takes `mcp_servers` and `mcp_reload` independently, and
   the route dropped the registry dependency and guarded only the
   callable, so `mcp_servers=None` with a reloader applied a
   configuration and answered 200 where the code before M2 answered
   503. This section's deviation 2 had argued that no composition
   passes one without the other, which was true and beside the point: a
   guard on a state-changing route is the endpoint's behavior, not a
   note about its callers.

   *Resolution* (`a8725cc`): `servers: McpServersDep` is back on the
   route, read for one thing, whether there is one, and the guard is
   `reload is None or servers is None` again with the same sentence.
   The regression,
   `test_half_a_runtime_refuses_to_reload_from_either_side`, drives
   both mismatched compositions through the mount and asserts 503 and
   the sentence for each; on the previous code the first of them
   answers 200. The deviation above is rewritten as the correction it
   became. The document does not move a byte: a dependency is not part
   of the contract.

2. **P2: the ownership rule missed the bindings that hide.**
   `emitter_bindings` read plain assignments and imports at a module's
   top level only, so `events, other = foreign.events, value` and a
   rebinding under a module-level `if` or `try` all passed. Each runs
   in the module's own scope, before the first emit, and leaves the
   emit sites reaching a foreign emitter; `unowned` answered the empty
   string for all three.

   *Resolution* (`59fb728`): `module_scope` yields the statements that
   run in the module's own scope, descending through control flow and
   never into a function or a class body, and `binds_the_emitter`
   recurses an assignment target through tuples, lists and starred
   elements. Every form that binds a name in that scope is read now:
   assignment of every arity, annotated and augmented, an import, a
   `for` target and a `with ... as`. Planted as
   `test_an_emitter_rebound_out_of_plain_sight_owns_nothing_either`,
   which refuses the three shapes, asserts the walk attributes each to
   its own module rather than the borrowed channel, and keeps the
   honest look-alikes accepted: a tuple that binds other names, and a
   module-level `if` that binds none.

3. **P3: two line counts had gone stale.** This section said
   `registry.py` went 459 to 500 lines, and the plan's M1 tick said
   `manager.py` lands at 768; both were read before the M1 review
   round's fixes grew the files under them, and the M1 table in this
   document already said 455 and 777.

   *Resolution* (`74d09d4`): 455 to 496 here, 777 in the tick, with
   the tick's parenthetical otherwise as it was. The figures are what
   the one recorded criterion miss is measured against, so a stale one
   is a claim about a criterion.

## M3: the `ToolSource` seam

Two commits: `tools/source.py` first, stating the interface and the
three implementations while nothing consumes them; then the runtime,
where three methods that knew each source by heart become three loops
over the same tuple. The seam is 225 lines and the runtime lost 54 to
gain 50, which is what a straight replacement of four conventions by
one looks like.

### The claim is the reservation itself

The plan's review finding 1 required a per-call claim carrying the
reserved MCP entry through `owns`, `dispatch` and `timeout_for`. The
type is `conversations.records.ToolInvocation`, unchanged and not
wrapped: it already carries everything routing reads (the name asked
for, the arguments asked with, the entry that owned the name when the
call was classified), it is what `_classified` already returns and what
`TurnUnderway.reserved` already answers with, and it is what the
`tool_call` event and the turn's row are built from, so there is
exactly one classification per call and no second structure that could
disagree with it.

`tools/source.py` names it structurally rather than importing it:

```python
class ToolClaim(Protocol):
    @property
    def name(self) -> str | None: ...
    @property
    def arguments(self) -> dict[str, Any] | None: ...
    @property
    def entry(self) -> str | None: ...
```

Three read-only members, which is the whole of what a source is told,
and the tool layer does not import the conversation record layer to say
it. Both `None`s are honest rather than defensive: `name` is optional
on `ToolInvocation`, and `arguments` is `None` exactly when the model's
arguments never parsed, which is the one case the runtime answers
before any source is asked.

### The order, and why it settles nothing

`PipelineRuntime._sources` is a fixed tuple, built once in `__init__`:
`BuiltinTools`, `DeviceTools`, `McpTools`. That is the order
`_tool_snapshot` merged in and the order `_dispatch` tested in, and it
is not a tie-break: `names` makes the three namespaces disjoint by
construction (builtins bare, the device's carrying its `self` prefix,
an MCP server's carrying its entry, and an entry may take neither of
the other two groups' names), so no two sources can own one name.

The one place today's dispatch ordering was load-bearing is the
no-memory `remember`, and it is the plan's names-not-outcomes
resolution: `BuiltinTools.owns` answers by name membership whether or
not the feature is configured, and its `dispatch` returns the exact
unknown-tool sentence, byte for byte, instead of the call falling
through to the device scan the way it used to. The two are
indistinguishable from outside because a device tool cannot be called
`remember` and an MCP entry cannot either.

`switch_agent` never reaches the loop: `_run_tools` splits it out and
`_refuse_handover` answers it, both untouched. `BuiltinTools.dispatch`
still answers it if it ever arrives, as the builtin that cannot run,
which is what the runtime did with it before.

### What stayed in the runtime

The malformed-arguments branch, with its length-not-value warning
verbatim; the unknown-tool sentence for a name no source claims; the
`DEFAULT_TOOL_TIMEOUT_S` constant and its comment. The constant is
handed to each source at construction rather than read by them, for two
reasons: how long a builtin or a device tool may take is the runtime's
policy, and `test_session_tools.py` rebinds
`pipeline_module.DEFAULT_TOOL_TIMEOUT_S` before the session it patches
it for is built, which a source reading its own copy of the name would
not have seen (the M1 lesson about assignment-based patches, from the
other side).

`_timeout_for` now asks the owning source and keeps the module default
for a call nobody owns, which is the same answer it gave before: the
fork was `entry is not None`, and only an `mcp` claim carries an entry.

### Deviations

**The MCP source is a thin adapter, not `McpServers` itself.** The plan
says the registry implements the protocol directly because "its
existing methods already match". They do not:
`McpServers.timeout_for(entry: str) -> float | None` answers about an
entry, the protocol's `timeout_for(claim) -> float` answers about a
call, and the entry form is pinned by six assertions the
no-behavior-change contract forbids porting
(`test_tools_mcp.py:399,407,408,849`,
`test_tools_mcp_reload.py:139,146`). A protocol member of that name
taking a claim would break them, so `McpTools` in `tools/source.py`
holds the registry and translates: `snapshot` to `tools_for_agent`,
`owns` to the claim's entry, `dispatch` to
`call(..., expected=claim.entry)` with the re-resolution-is-wrong
rationale moved into it, and `timeout_for` to the entry's timeout or
the default. `tools/mcp/` is untouched by M3 as a result, which is also
what keeps M1's and M2's byte-identical proofs out of this milestone's
way.

**`tools/source.py` imports more than `providers.ToolDef` and stdlib.**
It holds the three implementations beside the interface, so it also
imports `device.boundary.DeviceOutput`, `tools.builtin`, `tools.names`,
`tools.memory.MemoryStore` and `tools.mcp.McpServers`. The constraint
the plan wrote that sentence for holds: every one of those is a module
`runtime/pipeline.py` already imported, so the pipeline still imports
downward and nothing new is on the path of anything that imports the
tool layer. Keeping the interface pure and scattering three ten-line
adapters across three layers (a class in the device boundary, whose
docstring says it holds two protocols and nothing else) was the
alternative, and one file named for the seam reads better than three
homes for one idea.

### Ports

None. No test file is modified by M3. The three renamed things are
docstrings; `_tool_snapshot`, `_dispatch`, `_timeout_for`,
`_classified`, `_reserve_tools` and `_run_one` all keep their names and
their signatures, which is why `test_session_tools.py` drives the new
loop unmodified, including the reservation-semantics test that reloads
a registry between `_reserve_tools` and `_run_one` and the one that
asserts `_timeout_for(_classified(...)) == 7.5`.

The conformance sidecar is unchanged too: the `tool_call` emit site
never left `_run_one`, so both of its entries
(`…pipeline:PipelineRuntime._run_one.fields` and the
`("samtal_server.runtime.pipeline", "PipelineRuntime._run_one", 1)`
value source) still name the enclosing function they always named, and
no other event is emitted from anything this milestone moved.

### Verification

From `samtal-server/`, `uv` throughout, `PYTHONDONTWRITEBYTECODE=1`
outside pytest.

- `uv run ruff check .`: **All checks passed!**
- `uv run pytest tests/unit -q`: **2947 passed, 16 skipped**, 2963
  collected on the cascaded branch (the two additions are M2's review
  round's regression and planted cases, which this branch carries):
  the seam adds no test of its own because the tests that drove the
  four conventions drive
  the one interface unmodified.
- `uv run pytest tests/integration -q`: **55 passed**, unchanged.
- `uv run samtal-server config openapi` and
  `uv run samtal-server events reference` both diff clean against
  `docs/reference/api-openapi.json` and `docs/reference/events.md`.
- `git diff --stat` over `test_server_event_pins.py` and
  `test_event_surface_pins.py`: empty, the two contract pin suites
  byte-unchanged.

### The PR review round (M3)

External review of PR #180 (diff main...d8ad2eb) by codex 0.147.0
(model gpt-5.6-sol), 2026-08-18. Three findings, all documentation:
the rebase had left M3 listed both unchecked and checked in the
plan (the artifact deleted); the M3 verification block recorded the
pre-cascade counts as unchanged (rerun and corrected to 2947 passed
and 2963 collected on the cascaded branch); and the never-resolves-
twice claim in the module docstring and CHANGELOG overstated the
device source's deliberate live-scan (both narrowed to MCP routing
with the edge behavior named). Verdict as posted: mergeable after
the listed fixes.

## M4: tests through public seams

Six commits, one per seam with the ports it makes possible: the
registry's two reads, the manager's session, the abandoned set's name,
the four managers whose configuration was being rewritten under them,
the manager protocol with the stand-in it makes possible, and the stop
bound's subclass. No production behaviour moves in any of them: what
changes is what a test is allowed to know.

### The seams, reach by reach

| Reach, and where | Seam | The port |
| --- | --- | --- |
| `servers._managers[entry]` (`test_tools_mcp.py:1284`, `test_tools_mcp_reload.py:96` in its own `manager_of` helper, `:390`, `:397`, `:549`, `test_agent_guidance.py:466`) | `McpServers.manager_of(entry)`, KeyError for an entry with no manager | the helper collapses onto it, its rationale moving into the method and into the section comment above the diff tests |
| `servers._reloading` (`test_tools_mcp_reload.py:759`) | `McpServers.reloading`, read-only | `while servers.reloading:` in the helper that waits out an apply whose caller went away |
| `manager._session` patched (`test_tools_mcp.py:257`, `:1170`, `:1223`, `test_tools_mcp_prompts.py:206`) | `McpServerManager.session`, read-only | `monkeypatch.setattr(manager.session, "call_tool", refuse)`, the same patch of the same live object |
| `mcp_module._abandoned` (`test_tools_mcp_reload.py:739`, `:744`, `:800`) | `manager.abandoned`, the module-level name renamed and re-exported | `manager_module.abandoned` |
| `manager._config = …` (`test_tools_mcp.py:116`, `:120`, `:293`, `:307`, `:312`) and the read at `test_tools_mcp_reload.py:549` | none: the world moves instead | `command_arrives` in `tests/support/tools_mcp.py`, and one entry whose path is empty and then is not |
| `StubbornManager(McpServerManager)` overriding `_run` and writing `_became`, `_settled`, `_stop`, plus `manager._task` (`test_tools_mcp_reload.py:690-744`) | `transport.stdio_client` (M1's sanctioned patch target) and `manager.abandoned` | `stubbornly_unwinding`, and the task read out of the abandoned set |
| `SlowStopManager(McpServerManager)` overriding `_run`, plus `going._task` (`test_tools_mcp_reload.py:762-799`) | `McpManager`, the protocol, and construction with a mapping | a stand-in answering the protocol, asserted on the bound it was stopped under and on the stop having finished |

### The protocol

`McpManager` in `manager.py`, above the class that answers it, taken
from what `registry.py` and `reload.py` actually call and nothing else:
`state`, `reason`, `since`, `tool_timeout_s`, `shipped_instructions`
and `shipped_prompts` as read-only properties; `tools()`,
`listed_at(published)`, `expect(allowed)`, `same_as(other)` and
`ensure_reconnecting()`; and `start()`, `stop(timeout=None)` and
`call(published, arguments)` as coroutines. Fourteen members.

Three names the manager has that are deliberately not in it: `name`,
`up` and the new `session`. Nothing in the registry or the reload reads
them, and a member nobody calls is one a stand-in has to answer for
nothing.

`same_as` takes `Any` rather than the protocol, which is the one place
the interface is deliberately loose: what two managers are compared by
is knowledge an implementation has about itself (its entry fragment and
the fingerprint of the secrets behind it), and the candidate on the
other side of the comparison is always one the reload just built.

`McpServers.__init__`, `manager_of`, `_install`, `_shadowed` and the
reload's three lists are typed against it. `_managers_for` still
answers concrete managers, because building them is exactly what it
does.

### Deviations

**The read-only `config` property was not added.** The plan names "the
six `_config` reads"; five of the six are WRITES
(`manager._config = stdio_entry(...)`) and the sixth is the read half
of a write. A read-only property serves none of them, and a settable
one would be a public way to do the thing no caller does: the only
thing that ever changes a running manager's configuration is a reload,
and a reload does it by replacing the manager. So the reaches are
retired rather than re-expressed. What each of those tests was really
about is a server that was down and is not any more, and that is now
what they do: the entry names a path with nothing at it, and
`command_arrives` puts this suite's stdio server there, which is a
stronger test of the same sentence (`ensure_reconnecting` really
reconnects to a server that really arrived). The fourth wanted two
failures of different kinds under one entry and gets them from the same
path: nothing there, and then something there this process may not
execute (`FileNotFoundError`, then `PermissionError`).

**No manager factory parameter.** The plan pairs the protocol with "a
MANAGER FACTORY parameter on `McpServers` construction (and/or
`build`)". Construction already takes the mapping of managers, which
the plan itself calls a de-facto seam, and that is what the stand-in is
injected through, so a factory would have had no caller: it would have
had to thread through `_managers_for` and the reload's `_prepared` for
a test that wants the arriving candidates to be real anyway. The
protocol landed; the factory did not.

**The stubborn stop is not a protocol stand-in, and could not be.**
The plan turns both subclasses into stand-ins answering the protocol.
That works for `SlowStopManager`, whose test is about what the reload
does around a slow stop. It cannot work for `StubbornManager`: that
test's subject IS `McpServerManager.stop`, whose bound, cancellation
and abandonment are all inside it, so a stand-in would replace the
subject with its own stop and then assert on that. What the test needs
is a task that swallows its cancellation and goes on running, and
suppressing a cancellation is something code inside the task does: no
configuration, and no unhelpful far side, can produce one (a hostile
child process cannot, because the cancel is delivered in this process
and anyio honours it).

So what stands in is the transport rather than the manager.
`stubbornly_unwinding` wraps the stdio client that ships in an exit
which swallows its cancellation and sleeps again, patched over
`transport.stdio_client`, which M1's port table already established as
the patch target for that name. The manager under test is then the one
that ships, with its real run, its real exit stack and a real child
process, against a way out that will not close, and the assertions get
stronger rather than weaker: the task is read out of `abandoned` (so
exactly one was left behind, which the old assertion did not say), it
is still running at the bound, and the set is empty once it ends.
Checked by reverting `abandoned.add(task)` in `_abandon`, on which the
test fails at the unpacking.

**`manager.py` is 886 lines**, up from the 777 M1 recorded as the one
criterion it missed. The protocol is 109 of them. It sits with its one
implementation on purpose: a seventh module holding fifty lines of
interface would put the contract a file away from the class that
answers it, and this package's modules are named for
responsibilities rather than for kinds of declaration. `registry.py`
goes 496 to 526.

**Found while ticking: the plan's milestone list had a broken item.**
M3's tick left the first four lines of its own unticked entry above the
new one, so the list carried a fifth item made of half of M3's
description and the tail of M2's parenthetical. Removed here, since a
milestone list a fresh session resumes from is exactly the thing that
must not be read twice.

**The fifth `_session` patch stays.** `test_server_event_pins.py:1838`
patches `manager._session` the way the four ported sites did. Both the
plan and this milestone's brief require the contract pin suites
byte-unchanged, and a pin suite that moves with the code it pins is not
a pin, so it is left exactly as it is and recorded here rather than
found by a later grep.

### The port table

Every modified test line, with the reason. Line numbers are the ones
at M3's tip (`d8ad2eb`).

| File | Line | Was | Is | Why |
| --- | --- | --- | --- | --- |
| `test_tools_mcp.py` | 11 | `import socket` | — | the socket probe went with the second failure's port |
| `test_tools_mcp.py` | 47-54 | the support import | `+ command_arrives` | the three reconnect ports |
| `test_tools_mcp.py` | 114-121 | `_config` written twice around a start | an entry naming an empty path, then `command_arrives` | the world moves rather than the manager's configuration; a docstring says so |
| `test_tools_mcp.py` | 257 | `monkeypatch.setattr(manager._session, …)` | `manager.session` | the seam |
| `test_tools_mcp.py` | 277-296 | a nonexistent command, then an HTTP URL on a free port assigned to `_config` | one path: absent, then present and not executable | two failures of different kinds under the entry the manager was built with |
| `test_tools_mcp.py` | 305-313 | `_config` written twice | the same path port | as above |
| `test_tools_mcp.py` | 1170, 1223 | `manager._session` patched | `manager.session` | the seam |
| `test_tools_mcp.py` | 1284 | `servers._managers["home"].tools()` | `servers.manager_of("home").tools()` | the seam |
| `test_tools_mcp_prompts.py` | 206 | `manager._session` patched | `manager.session` | the seam |
| `test_tools_mcp_reload.py` | 28 | `import samtal_server.tools.mcp as mcp_module` | — | its only use was `_abandoned` |
| `test_tools_mcp_reload.py` | 88-96 | the `manager_of(servers, entry)` helper | `managers_in(servers)`, over `manager_of` | the helper's own rationale moves to the registry method; the new one answers the mapping `unchanged_by` compares |
| `test_tools_mcp_reload.py` | 24 sites | `manager_of(servers, "x")` | `servers.manager_of("x")` | the seam |
| `test_tools_mcp_reload.py` | 390, 397 | `dict(servers._managers)` and its comparison | `managers_in(servers)`, plus `len(servers) == len(kept)` | equal per entry and stronger by the count, which is the one way the mapping could agree while the set had moved |
| `test_tools_mcp_reload.py` | 528-549 | `"extra"` at `/nonexistent/mcp`, revived by assigning `_config` | `"extra"` at an empty path, revived by `command_arrives` | the box comes back rather than the entry being rewritten |
| `test_tools_mcp_reload.py` | 690-711 | `class StubbornManager(McpServerManager)` | `stubbornly_unwinding(holding_s)` | the deviation above |
| `test_tools_mcp_reload.py` | 725-744 | `task = manager._task`, `task in _abandoned` | `(left,) = manager_module.abandoned` | one private reach fewer and one assertion more |
| `test_tools_mcp_reload.py` | 759 | `while servers._reloading:` | `while servers.reloading:` | the seam |
| `test_tools_mcp_reload.py` | 762-770 | `class SlowStopManager(McpServerManager)` | a stand-in answering `McpManager` | the protocol seam |
| `test_tools_mcp_reload.py` | 778, 1004 | `SlowStopManager("tools", stdio_entry())` | `SlowStopManager()` | it has no entry to be built from |
| `test_tools_mcp_reload.py` | 799 | `going._task is None or going._task.done()` | `going.stops == [STOP_TIMEOUT_S]` and `going.finished` | what the reload asked, and that it ran to its end behind the shield |
| `test_agent_guidance.py` | 466 | `app.state.mcp_servers._managers[ENTRY]` | `.manager_of(ENTRY)` | the seam |
| `tests/support/tools_mcp.py` | — | — | `+ command_arrives` | the three reconnect ports and the reload's |

`test_tools_mcp_http.py`, the reflection sentinels, the reload
integration proof, both contract pin suites and the conformance suite
are byte-unchanged.

### The acceptance criterion, as a grep

The issue asks that tests reach managers and reload through public
seams and that the `StubbornManager`-style private subclassing is gone.
From `samtal-server/`:

```
grep -rn "_managers\b\|_abandoned\|\._session\|\._reloading\|\._task\b\|\._config\b\|\._became\|\._settled\|\._stop\b\|\._run\b\|(McpServerManager)" tests/
```

Seven lines answer, and none of them is a reach:

- `test_tools_device.py:125` and `test_session_limits.py:253`, test
  names with the word `abandoned` in them, about other subsystems;
- `test_tools_mcp_reload.py:618`, a test name with `managers` in it;
- `test_event_schema_conformance.py:1411`, `:1414`, `:1417`, the
  sidecar's value-source keys, which name `McpServerManager._run` as
  the function three emit sites are in: strings about where the code
  is, not calls into it;
- `test_server_event_pins.py:1838`, the fifth `_session` patch, left
  byte-unchanged on purpose (above).

### Verification

From `samtal-server/`, `uv` throughout, `PYTHONDONTWRITEBYTECODE=1`
outside pytest.

- `uv run ruff check .`: **All checks passed!**
- `uv run pytest tests/unit -q`: **2947 passed, 16 skipped in
  306.82s**, 2963 collected, against 2963 before the milestone. A port
  rewrites lines inside a test that already exists, and this milestone
  wrote no new one. (The before figure is measured at `d8ad2eb`; M3's
  section records 2961, which is M2's count from before M2's own
  review round added two tests. The lane moved under neither M3 nor
  M4.)
- `uv run pytest tests/integration -q`: **55 passed in 159.31s**,
  unchanged in count.
- `uv run samtal-server config openapi` and
  `uv run samtal-server events reference` both diff clean against
  `docs/reference/api-openapi.json` and `docs/reference/events.md`.
- `git diff --stat` over `test_server_event_pins.py`,
  `test_event_surface_pins.py`, `test_event_schema_conformance.py`,
  `test_mcp_status_reflection.py` and
  `tests/integration/test_mcp_reload.py`: empty across the milestone.
- The abandonment regression check above: with `abandoned.add(task)`
  removed from `_abandon`, the stop-bound test fails at the unpacking,
  and the file was restored and `touch`ed afterwards.

### The PR review round (M4)

External review of PR #181 (diff main...5e051f5) by codex 0.147.0
(model gpt-5.6-sol), 2026-08-18. One finding: the protocol-backed
constructor took a dict, which is invariant, rejecting both the
production call and pre-typed protocol dictionaries. Fixed by
taking and holding a Mapping, never copied, which the reload's
whole-mapping rebind and every read site already fit. Verdict as
posted: mergeable after the listed fix.
