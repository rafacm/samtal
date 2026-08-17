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
as the one behavioral change it is.

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
| `transport.py` | 283 | bringing a connection up, and classifying failure |
| `prompts.py` | 471 | the #122 capture, under its bounds |
| `manager.py` | 768 | one server's lifecycle, and the abandonment plumbing |
| `slice.py` | 201 | the configuration a registry was built from |
| `reload.py` | 423 | the two phases, as functions over the registry |
| `registry.py` | 459 | what needs the managers and the slice together |

**Deviation: `manager.py` is 768 lines, not "under roughly 500".** The
plan's own arithmetic implies it: the class is 885 lines at plan time
and 307 of those are the capture, which leaves 578 for the class alone,
before its module's imports, the four timeout and status constants, the
three exception types it raises and the task-abandonment plumbing the
plan puts here. Nothing was left in it that had somewhere else to be:
`_resolve` and `_connect` went to `transport`, the whole capture except
its two call sites went to `prompts`, and the only way further down is a
seventh module for the stop and its abandonment, which is a boundary
this plan did not draw and M1 is not the place to invent. Recorded as
the one criterion M1 misses.

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
Called as the first act of `McpServerManager.start`, because several
suites (the HTTP no-leak proof among them) build a manager and start it
without a registry, and again from `McpServers.build`, which costs
nothing: `Logger.addFilter` does not install a filter twice and turning
propagation off twice is turning it off. Nothing asserts on the state at
import time, so nothing needed porting for it.

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
- `uv run pytest tests/unit -q`: **2943 passed, 16 skipped**. Collected
  2959 against 2954 at the base commit, and the five are the planted
  conformance cases and nothing else: a move adds no test and the ports
  rewrote lines inside tests that already existed.
- `uv run pytest tests/integration -q`: **55 passed**, unchanged.
  `test_mcp_reload.py`'s black-box reload proof needed nothing, as the
  plan said it would not.
- The reflection sentinels
  (`test_mcp_status_reflection.py`, 8 tests) pass, including
  `test_a_child_that_writes_where_it_likes_reaches_no_operator_surface`,
  which is the proof that the quieting is in force before the first
  real subprocess connect on the manager-start path.
- `uv run samtal-server events reference` diffs clean against
  `docs/reference/events.md`, and byte-identically against the same
  document generated at the base commit: the channel did not move.
- `git diff --stat` over `test_server_event_pins.py`,
  `test_event_surface_pins.py` and `events_schema.py` is empty.
- `wc -l` per module is the table above.
