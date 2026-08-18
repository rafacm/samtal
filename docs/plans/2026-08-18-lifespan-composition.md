# Own construction in the lifespan and type the composition state

## Goal

Implement issue #142: `create_app` builds every resource
synchronously and hangs thirteen untyped attributes on `app.state`,
the lifespan constructs nothing (so an app that never enters it
leaks the bindings engine), the config API opens a database engine
and re-derives the master key on every request, and the
shutdown-drain task is created with no reference held. Give the
composition one typed object, make the lifespan own construction
and release, give the config API a lifespan-owned engine, own the
drain task, and let `agent_fillers` distinguish pending from
absent, all under the issue's no-behavior-change contract for the
wire surfaces.

The companion implementation doc,
[`2026-08-18-lifespan-composition-implementation.md`](2026-08-18-lifespan-composition-implementation.md),
records what each milestone actually did, deviations, and
discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by issue #142 and not re-litigated here:

1. **One typed composition object** (a dataclass; name and exact
   contents in this plan) built and torn down by the lifespan;
   `app.state` carries that one object. `ws.py`, `ota.py`,
   `onboarding.py`, and the API read typed fields.
2. **The config API gets a lifespan-owned engine**; migrations run
   once at startup; the per-request `load_keys()` re-parse goes
   with it. The documented property that motivated per-request
   opening is re-examined in this plan and either preserved
   deliberately or retired with a recorded reason (resolved
   below).
3. **The shutdown-drain task is owned and awaited with a bound.**
4. **`agent_fillers` becomes a container that can distinguish
   pending from absent**; fire-time behavior unchanged (a
   not-yet-ready clip stands down exactly as an unconfigured one
   does today).
5. **`uvicorn samtal_server.app:app` keeps working.**
6. **The API token stays out of the state bag** (the deliberate
   exception, resolved once in `create_app` and passed into
   `build_api`).

The no-behavior-change contract: all integration tests pass
unmodified except where they constructed apps without entering a
lifespan (those move to the lifespan-aware fixture, and the issue
grants the same for unit tests); the OTA, onboarding, and
websocket surfaces behave identically on the wire.

## Evidence, re-verified at plan time

The full inventory was retaken at main@4ec765d (recorded in the
implementation doc's preamble at M1); the issue's anchors are from
main@8dd1a5f and have moved. The load-bearing facts:

- **`create_app` is app.py:141-358** (370-line file). It builds, in
  a comment-documented order: config via `load_boot_config` when
  none is passed (opens, migrates, and disposes the domain DB),
  `device_auth`, the bindings engine (`DeviceBindings.open`,
  app.py:185, the pool an unentered app leaks), `PendingDevices`,
  the API token (the deliberate exception, app.py:192-201),
  `McpServers.build` (no subprocess until `start_all`),
  `MemoryStore`, `build_api(...)` (closures over token, pending,
  MCP, memory), `SessionRegistry`, `build_agent_providers`
  (heavy and blocking: Whisper and Silero model loads),
  the empty `agent_fillers` dict, `ConversationStore` (opens the
  file and runs Alembic in the constructor), the runtime factory
  closure, `DeviceFacts`, `CaptureStore`, and the routers. The
  lifespan (app.py:30-76) only fills the fillers dict, starts and
  stops MCP and the store's writer thread, and disposes the
  bindings engine.
- **Thirteen `app.state` attributes**, read by `ws.py` (seven in
  the conversation handler), `ota.py` (five), `main.py`'s
  `DrainingServer` (`sessions`), and twenty-six test sites. The
  mounted config API keeps its own separate state bag on the
  sub-app (`config/api.py:480-492`), read back by its dependencies
  and by `conversations/api.py:609`.
- **Mounted sub-apps get no lifespan.** Starlette does not run a
  mounted app's lifespan, so the config API's lifespan-owned
  engine must be created by the parent lifespan and attached to
  the sub-app's state; the many suites that run `build_api(...)`
  as a top-level `TestClient` app are a second path to keep
  consistent.
- **The config API opens per request** at
  `config/api.py:545-565` (`store_dependency`: `open_database`,
  which runs `upgrade_to_head`, then `ConfigStore(engine,
  load_keys())`, then dispose), not api.py:793-813 as pinned. The
  documented rationale is docs/plans/2026-08-11-rest-api.md's "The
  API opens the database per request" section: (a) boot's contract
  that nothing after boot reads the database, (b) no lifespan
  disposal wiring existed, (c) decisively, eager engines made
  every `create_app` in the suites open a database. There is no
  crash-recovery property attached to the config store; the
  fresh-engine-per-request property is documented for the
  conversations reader (`conversations/api.py:572-579`, a store
  purged or restored under a running server is met as it is now).
- **Busy/contention tests** exist at three levels: real held-lock
  tests for the write path and for the open-and-migrate phase
  (`test_config_refusals.py`), API-level 409s
  (`test_config_api_reads.py:471`, `test_config_api_writes.py:623`),
  and mapping-level fakes. The open-phase test pins contention in
  a phase that moves to startup under this issue.
- **The drain task**: `main.py:84` creates it with the return
  value discarded; `_drain` awaits `sessions.drain(drain_s)` and
  calls `super().handle_exit` in a `finally`. The registry's drain
  already bounds itself (`CLOSE_MARGIN_S`/`_FRACTION`).
- **Fillers**: the dict is built empty (app.py:245), filled in
  place by the lifespan (app.py:62-64), and held by reference
  through `bespoke_runtime_factory` into `FillerRunner`, whose
  only reads are membership, `[name]`, and `.get(...)`; absent and
  not-yet-synthesized are indistinguishable today, which is the
  fire-time behavior to preserve while making the states
  distinguishable to a caller that asks.
- **Test terrain**: 177 `TestClient(...)` constructions, 53 of
  them without `with` (no lifespan) across 21 files, plus 30
  bare `create_app(...)` sites in 15 files; the union needing the
  lifespan-aware fixture is 29 files, roughly two-thirds of them
  config-API suites that build `build_api(...)` directly rather
  than whole apps. There is no shared app fixture under
  `tests/support/` today; the integration lane has its own
  serving helpers that do run the lifespan.
- **Two capture events live in `create_app`**
  (`capture_enabled`/`capture_disabled`, app.py:304-327), keyed in
  the conformance suite as `("samtal_server.app", "create_app",
  1|2)` on channel `samtal_server.app`. Construction moving into
  the lifespan moves these sites: the enclosing-function halves of
  their identity keys change, and the channel must not (the build
  stays in `app.py`).
- **Injection seams**: `create_app(config, secrets)` is the whole
  seam; two tests inject post hoc by overwriting `app.state`
  (`test_boundary_contract.py:58-68` replaces `runtime_factory`
  before entering the client; `test_conversations_boot.py:217`
  replaces the store) and one monkeypatches
  `app_module.build_agent_fillers`. Production callers of
  `create_app` are `main.py` and the module `__getattr__` only;
  no `--factory` usage anywhere.

## Design

### The composition object

A frozen-by-convention (mutable, but written only by the lifespan
and two named tests) dataclass in a new module
`samtal_server/composition.py`:

```python
@dataclass
class Composition:
    config: Config
    device_auth: DeviceAuth | None
    bindings: DeviceBindings
    pending: PendingDevices
    mcp_servers: McpServers
    memory: MemoryStore | None
    sessions: SessionRegistry
    agent_providers: dict[str, AgentProviders]
    agent_fillers: AgentFillers
    conversations: ConversationStore | None
    runtime_factory: RuntimeFactory
    device_facts: DeviceFacts
    capture: CaptureStore | None
    api: ApiRuntime
```

`ApiRuntime` is the config API's request-time dependencies as one
typed dataclass (declared beside `build_api` in `config/api.py`):
the store handle (the lifespan-owned engine plus the once-derived
keys, yielding a `ConfigStore` per request), `conversations`,
`loaded_agents`, `pending`, `mcp_servers`, `mcp_reload`, and
`agent_prompt`. The mounted sub-app's state carries exactly one
attribute, `api_runtime`, replacing today's seven-field bag; the
sub-app dependencies and `conversations/api.py`'s state read go
through its typed fields. So the whole application carries two
typed state objects, `composition` on the app and `api_runtime`
on the mounted API, and the acceptance grep covers both with no
exemption.

The module imports only downward (config, providers, registry,
bindings, onboarding's `PendingDevices`, capture, conversations,
tools.mcp, filler, device boundary types) and never `ws`, `ota`,
or `app`, so the readers can import the type without a cycle
(`ws.py` and `ota.py` import it for annotations; the existing
deferred-import precedents in `config/api.py:500` and
`registry.py:18` set the discipline). `app.state.composition` is
the one attribute; the token exception stays as it is (never on
state). Readers bind `comp = request.app.state.composition` (or
the websocket equivalent) once and use typed fields; the
"composition root" docstring references across the codebase move
with the object where they name `create_app`'s wiring.

### Two phases: describe in `create_app`, build in the lifespan

`create_app(config, secrets)` keeps its signature and becomes the
describe phase: resolve enforcement (unchanged, first), resolve
config via `load_boot_config` when none was passed (it disposes
what it opens), resolve the API token, validate `device_auth`
(fail-fast configuration errors stay at `create_app` time: both
are pure config reads that open nothing), register the routers
and the health endpoint, mount the API shell, and stash the
build inputs on the app (a small private `_CompositionSeed`:
config, secrets, token). The lifespan becomes the build phase:
construct everything else in today's documented order, set
`app.state.composition`, attach the config API's runtime pieces
to the mounted sub-app (below), start the store writer and MCP,
emit the capture events, yield, then tear down in reverse
(stop MCP, dispose the config-API engine, dispose bindings, stop
the store) with the same try/finally nesting discipline the
current lifespan documents.

Two consequences the inventory forces:

- **Routers before resources.** `ota.build_router` and
  `onboarding.build_router` need only config values;
  `build_api(...)` today captures live objects (pending, MCP,
  memory) in closures at mount time. The API is therefore built
  in two steps: `create_app` mounts the FastAPI shell (routes
  registered, token dependency armed), and the lifespan attaches
  the live objects to the sub-app's existing state bag (the same
  attributes `config/api.py:480-492` sets today, set from the
  parent lifespan instead). The sub-app's dependencies already
  read everything from its state, so request-time behavior is
  unchanged; `build_api` keeps working standalone for the
  config-API test suites, which pass live objects exactly as
  today.
- **Providers off the event loop.** `build_agent_providers` loads
  models for seconds to minutes; in the async lifespan it runs
  under `asyncio.to_thread`, matching `build_agent_fillers`'
  existing await. Same work, same order.
- **The sanitized startup-failure bridge.** Today `create_app`
  raises before `serve`, inside `main()`'s except arm, which
  prints one sanitized sentence and exits 1; after the move,
  construction fails inside the lifespan, `main()`'s arm ends
  before `serve()`, and uvicorn renders lifespan exceptions as
  tracebacks, which loses the sentence and would render a
  provider exception's chain onto stderr. The bridge: the
  lifespan catches the boot failure taxonomy (`ConfigError`,
  `ProviderError`, `EventEnforcementError`, the database busy
  refusal), records the already-sanitized one-line sentence on
  the seed, and re-raises `StartupFailed(sentence)` **`from
  None`**, so the only traceback uvicorn can render carries the
  sanitized sentence and no chain; `serve()` returns, `main()`
  sees the recorded failure and prints the sentence to stderr and
  exits 1, exactly today's surface. Anything outside the taxonomy
  propagates as the bug it is, unchanged. Verified against real
  uvicorn startup (the integration lane's serving helper) with a
  provider failure whose exception chain carries a
  credential-shaped sentinel: the sentence appears once, the
  sentinel appears nowhere in stderr or the logs, and the process
  exits 1. The module entry point (`uvicorn samtal_server.app:app`)
  gets the same `from None` discipline; uvicorn's own startup
  failure handling is its operator surface there.

The capture events move into the lifespan function: their sidecar
identities become `("samtal_server.app", "lifespan", 1|2)`; the
channel stays `samtal_server.app` because the function stays in
`app.py`. The conformance suite's exhaustive maps are the
inventory, as in #141.

`__getattr__` keeps building the module-level `app`, unchanged:
construction now happens at server startup rather than import,
which is the direction the issue wants.

### The config API's engine and keys

The engine has exactly one owner per process, and which lifespan
owns it depends on how the API runs:

- **Mounted (production and whole-app tests)**: the parent
  lifespan opens it via `open_database` (so migrations run once
  at startup; see the fresh-deployment note below), derives the
  Fernet keys once via `load_keys()`, installs the store handle
  into the sub-app's `api_runtime`, and disposes it at teardown.
  Starlette never runs a mounted app's lifespan, so there is no
  double-open.
- **Standalone (`build_api(...)` as the top-level app, the
  config-API suites)**: the API app gets its own lifespan doing
  the same open-install-dispose against the directory `build_api`
  was given. A standalone client that never enters the lifespan
  has no engine, which is part of the fixture migration those
  suites undergo anyway.

`store_dependency` becomes: take the installed store handle from
`api_runtime`, yield `ConfigStore(engine, keys)`, no dispose; a
missing handle is a programming error and raises, never a silent
per-request open. `BEGIN IMMEDIATE` is per transaction
(`ConfigStore._transaction`) and does not move, so write
contention behavior is unchanged; the busy-refusal mapping stays
at the same decision sites.

The fresh-deployment note: the open MUST be `open_database`, not
a bare `write_engine`, because the API-first path builds
`create_app(Config(...))` over a directory nothing has migrated
(the integration conftest does exactly this, and today the first
request's per-request open creates the schema). `upgrade_to_head`
is idempotent and cheap when current, so production pays one
no-op check after boot's own migration; nothing may assume
`load_boot_config` ran.

The documented property, re-examined as the issue requires:
reason (a) (boot's "nothing after boot reads the database") was
already retired by #86/#101 themselves, which is why the section
documents the per-request open rather than a prohibition; reason
(b) is exactly what this issue builds; reason (c) (eager engines
in test suites) is dissolved by the lifespan fixture: an
unentered app opens nothing at all now, which is strictly better
than today. Retired, with this paragraph recorded in
docs/plans/2026-08-11-rest-api.md's section as a dated amendment
note pointing here. The conversations reader's documented
fresh-engine property (`conversations/api.py:572-579`) is NOT
touched: that dependency keeps opening per request deliberately,
and the plan records it as out of scope.

Contention coverage: the write-path and read-path 409 tests pass
unchanged (the lock is taken per transaction, as today). The
open-phase test
(`test_config_refusals.py::test_an_open_that_cannot_take_the_lock_is_a_busy_error`)
pins a phase that now happens once at startup: it is re-scoped to
assert that a locked database at lifespan enter surfaces as the
same typed refusal out of startup (the sentence unchanged), which
is the honest equivalent of what it pins today, and the
implementation doc records the re-scope.

### The drain task

`DrainingServer` keeps the task: `_start_drain` stores it
(`self._drain_task = loop.create_task(...)`), and `serve()` gains
a bounded settle after uvicorn returns: if a drain task exists
and is not done, await it under `asyncio.wait_for` with
`drain_s + CLOSE_MARGIN_S` as the bound and log a warning if the
bound fires (today the task is fire-and-forget into uvicorn's own
shutdown; the bound makes the ownership honest without letting a
hung drain wedge the exit). `handle_exit`'s
second-signal-passes-through behavior is untouched. The unit
drain suite gains one test: the task reference exists and is
awaited; no unreferenced `create_task` remains in `main.py`
(grep-verified in the milestone).

### AgentFillers

A small class in `samtal_server/filler.py` beside the loader:

```python
class AgentFillers:
    def __contains__(self, name) -> bool
    def __getitem__(self, name) -> FillerClips
    def get(self, name, default=None) -> FillerClips | None
    @property
    def ready(self) -> bool          # synthesis has run
    def fill(self, clips: dict[str, FillerClips]) -> None  # once, by the lifespan
```

Before `fill`, lookups behave exactly as the empty dict does
today (nothing reachable, the runner stands down); after, exactly
as the filled dict. `ready` is what distinguishes pending from
absent for a caller that asks (nothing asks yet; the issue wants
the distinction expressible, not consumed). `FillerRunner`'s
three read forms work unchanged; its constructor annotation
widens to the small protocol it actually uses (or takes
`AgentFillers`; decided at implementation to whichever keeps
`tests/support/sessions.py` builders passing plain dicts,
likely a `Mapping[str, FillerClips]`-shaped protocol with the
dict still accepted).

### The lifespan-aware test fixture

One new module `tests/support/apps.py`: an `entered_app(config,
secrets=None)` context manager yielding `(app, TestClient)` with
the lifespan entered, and a thinner `entered_client` for the
common case. The 29 affected files move mechanically: `with
TestClient(app)` sites just switch constructor; no-`with` sites
gain the context; bare `create_app` sites that only inspect
construction failures keep calling `create_app` (failure surfaces
that stay in the describe phase: enforcement, auth config, token)
or enter the lifespan when they inspect built state. The
config-API suites that run `build_api(...)` standalone are
untouched except where they lacked `with` and read state the
lifespan now attaches. The two post-hoc injection tests move
their overwrite inside the entered context
(`composition.runtime_factory = ...` on the dataclass instance),
which the composition object's mutability exists for, and the
class docstring names those two tests as the sanctioned writers.

### The leak-checking test

The acceptance criterion's proof: a unit test builds
`create_app(config)` with a config pointing at a temp directory,
never enters the lifespan, and asserts no engine or pool was
opened: `DeviceBindings.open` not called (patched sentinel),
no `ConfigStore` engine created, no conversation store file
descriptor open, and no provider construction ran (the mock
providers record instantiation). A second test enters and exits
the lifespan and asserts every disposal ran (bindings disposed,
engines disposed, writer stopped, MCP stopped), using the
existing fakes. A third covers partial startup: the build
registers each acquisition on an `AsyncExitStack` the moment it
is acquired (bindings before providers, the config engine before
the store), so a failure anywhere later unwinds everything
already opened; the test acquires the bindings engine, fails the
provider build, and asserts the engine was disposed.

### Considered and declined

- A separate composition module owning the build function:
  declined; the build stays in `app.py`'s lifespan so the two
  capture events keep their channel, and `composition.py` holds
  only the dataclass and stays import-light.
- Making the conversations API share the lifespan-owned engine:
  declined, out of scope; its per-request open carries a
  documented property of its own.
- An `AgentFillers` state enum or events: declined; the issue
  asks for the distinction to exist, not for new surface.

## The standing review lenses, answered

- **No-leak.** No new retained surface: the composition object is
  never logged or rendered; the token exception is preserved
  verbatim; startup failures keep their existing sanitized
  sentences (`ConfigError`/`ProviderError`/`EventEnforcementError`
  through `main()`'s except arm, unchanged). The keys derived
  once at startup live on the sub-app state exactly as the store
  they configure did; nothing renders them, and the existing
  secret-sentinel suites pass unmodified.
- **Pin before reshaping.** The wire surfaces are pinned by the
  boundary contract, the OTA/onboarding/websocket suites, and the
  server-event pins; the two capture-event pins prove the moved
  emissions byte-identical (identity keys move, assertions do
  not). The refusal sentences the config API returns are pinned
  by the refusals suite and must not change.
- **Closed sets.** No reason token or event field changes; the
  busy-refusal classification sites do not move (per-transaction,
  in the store), only the engine's lifetime does.
- **Honest seams.** Every optional field on `Composition` compares
  `is not None` at its readers exactly as the state attributes do
  today; `store_dependency`'s replacement takes the engine from
  state and never falls back to opening one (a missing engine is
  a programming error and raises, never a silent per-request
  open); `AgentFillers.fill` is called once and asserts it.
- **Inventories by tooling.** The `app.state.` migration is
  verified by `grep -rn "app\.state\." samtal-server/samtal_server`
  finding only `state.composition` and the sub-app's
  `state.api_runtime`, both typed, and
  `grep -rn "state\.composition"` for the readers; the test-file
  migration list (29 files) is re-verified by grepping
  `TestClient(` without `with` and bare `create_app(` at each
  milestone; the conformance suite names the moved event keys.

## Risks and mitigations

- **Startup-order regressions.** The build keeps today's
  comment-documented order inside the lifespan, and the comments
  move with it; the integration boot tests and the banner test
  pin observable order.
- **Mounted sub-app state attachment races a request.** Uvicorn
  serves no request before the lifespan yields, and `TestClient`
  enters the lifespan before the first request; the sub-app
  attributes are set before yield, so no request can see a
  half-attached API. The standalone `build_api` path sets them at
  build time as today.
- **`to_thread` provider construction changes failure timing.**
  Handled by the sanitized startup-failure bridge above, with its
  real-uvicorn sentinel verification; a unit test additionally
  drives lifespan entry with a failing provider config and
  asserts `StartupFailed` carries the sentence and no
  `__cause__`/`__context__`.
- **The 29-file fixture migration is wide but shallow.** It is
  mechanical (context-manager adoption), split across milestones
  so each PR's test diff stays reviewable, and the grep
  inventories keep it honest.
- **Unentered apps that read state.** Three integration helpers
  read `app.state` on apps never served; they move to the
  entered fixture in the same milestone that moves their file,
  and the composition attribute simply does not exist before
  entry (attribute error is the honest signal, and the leak test
  pins that nothing else exists either).

## Milestones

- [ ] **M1: the typed composition object.** Add
  `samtal_server/composition.py` (the dataclass) and
  `AgentFillers` in `filler.py`; `create_app` builds exactly what
  it builds today but hangs the one `Composition` on
  `app.state` (fillers wrapped in `AgentFillers`); `ws.py`,
  `ota.py`, `main.py` read typed fields; the twenty-six test
  state-reads and the two post-hoc injectors move to the
  composition attribute; the lifespan is rewritten in the same
  milestone to bind `comp = app.state.composition` and read its
  fields (it reads six attributes M1 deletes, so leaving it
  untouched would fail at startup); grep proves `app.state.` is
  only the two typed objects; CHANGELOG; implementation-doc
  section. Construction still synchronous in `create_app` this
  milestone, so no fixture migration yet.
- [ ] **M2: the lifespan owns construction.** Move the build into
  the lifespan (describe/build split, `_CompositionSeed`,
  providers under `to_thread`, capture events with their identity
  keys, teardown order), add `tests/support/apps.py` and migrate
  the affected unit and integration files, add the leak-checking
  and full-teardown tests, verify the `ProviderError` startup
  path, CHANGELOG; implementation-doc section with the fixture
  migration inventory.
- [ ] **M3: the config API's lifespan-owned engine.** Parent
  lifespan opens the engine once (no per-request migrations) and
  derives keys once; `store_dependency` serves from state;
  re-scope the open-phase contention test to startup; dated
  amendment note in docs/plans/2026-08-11-rest-api.md recording
  the retirement of the per-request rationale; the conversations
  reader's per-request property recorded as deliberately kept;
  CHANGELOG; implementation-doc section.
- [ ] **M4: the drain task is owned.** `DrainingServer` keeps the
  task reference, `serve()` settles it under the bound, the new
  drain test, the no-unreferenced-`create_task` grep in the
  milestone verification; CHANGELOG; implementation-doc section
  and the closing acceptance sweep (all six criteria mapped).

Each milestone is a stacked branch off the previous one
(`feature/lifespan-composition-m1` off this plan's branch, and so
on), merged to `main` by rebase via its own PR after its own
external review round; every merge leaves `main` releasable: M1
is a pure re-homing, M2 moves construction without changing what
is constructed, M3 changes an engine's lifetime behind unchanged
refusal mapping, M4 tightens shutdown ownership.

## Plan review round

External review: codex-cli 0.147.0, model gpt-5.6-sol, 2026-08-18,
against commit 75e5f08. Eleven findings; verdict "ready after the
P1/P2 amendments". All eleven adopted.

1. **P1: M1 removes state its unchanged lifespan still reads.**
   The current lifespan reads six attributes M1 deletes; M1 as
   written fails at startup. M1 must also rewrite the lifespan to
   read composition fields while construction stays synchronous.

   *Resolution*: the M1 milestone now names the lifespan rewrite
   explicitly, with construction staying synchronous until M2.

2. **P1: the config API is exempted from the settled decision.**
   The plan kept the sub-app's seven-field untyped bag and
   exempted it from the acceptance grep, while the issue requires
   the API to read typed fields. The API runtime dependencies
   must become a typed object carried by the composition and
   attached to the sub-app, and the grep exemption goes.

   *Resolution*: `ApiRuntime` (declared beside `build_api`) is now
   a field of `Composition` and the single `api_runtime` attribute
   on the sub-app; the lens paragraph's grep covers both typed
   objects with no exemption.

3. **P1: the standalone `build_api` path has no engine owner.**
   The config-API suites run `build_api(...)` as a top-level app
   with no parent lifespan; the replacement dependency would find
   no engine. The standalone path needs its own lifespan that
   opens, migrates, installs, and disposes; mounted operation
   uses the parent-owned instance.

   *Resolution*: the engine section now defines both ownership
   paths (parent lifespan when mounted, the API's own lifespan
   when standalone, never both because mounted lifespans do not
   run), and the standalone suites' `with`-adoption is folded
   into the fixture migration.

4. **P1: lifespan failures bypass the sanitized startup
   boundary.** `main()` calls `serve()` outside its except arm,
   and uvicorn renders lifespan exceptions as tracebacks, which
   both loses the one-line sentence and can leak provider
   exception chains. A sanitized lifespan-to-entrypoint bridge is
   required, verified against real uvicorn startup with a
   sentinel-bearing chained failure.

   *Resolution*: the design now specifies the bridge: the
   lifespan catches the boot taxonomy, records the sanitized
   sentence on the seed, raises `StartupFailed(sentence)` from
   None, and `main()` prints the recorded sentence and exits 1
   after `serve()` returns; verified with real uvicorn and a
   sentinel-bearing chain in the milestone.

5. **P1: the no-migration engine breaks API-first fresh
   deployments.** The integration API-first path builds
   `create_app(Config(...))` over a fresh directory and relies on
   the first request's `open_database` to create the schema. The
   lifespan-owned open must run migrations once at startup; the
   plan cannot assume `load_boot_config` ran.

   *Resolution*: the engine section's fresh-deployment note now
   mandates `open_database` (migrate once at startup, idempotent)
   for both ownership paths and forbids assuming boot ran.

6. **P2: the teardown proof misses partial-startup leaks.**
   Cleanup must be registered per acquisition (exit-stack
   discipline), with a startup-failure test proving an engine
   acquired before a later failure is disposed.

   *Resolution*: the build now registers every acquisition on an
   `AsyncExitStack` as it happens, and the leak-test section
   gains the partial-startup case (bindings acquired, providers
   fail, engine disposed).

7. **P2: the proposed startup contention re-scope cannot reach a
   lock.** A bare `write_engine` touches no lock at entry. With
   amendment 5 the lifespan opens via `open_database`, and the
   existing open-phase held-lock test stays exactly as it is,
   pinning `open_database` directly; no re-scope.

8. **P2: moving the conversation-store injection after entry
   destroys its test.** `test_conversations_boot` replaces the
   store before entry so `start()` fails inside the lifespan.
   That test patches the constructor instead; only the
   runtime-factory injection moves inside the entered context.

9. **P2: shutdown has no path before the composition exists.** A
   SIGTERM during a minutes-long provider build reaches missing
   state. A signal before composition installation bypasses the
   drain and delegates straight to uvicorn shutdown, with a test
   driving a signal while startup is blocked.

10. **P2: `_CompositionSeed` retains a second copy of the API
    token.** The token is resolved and consumed in the describe
    phase (passed into the API gate) and never stored on the
    seed.

11. **P2: the CLI banner would announce a server that later fails
    startup.** The banner becomes a post-startup emission: main
    passes it as an `on_started` callback the lifespan invokes
    after a successful build, keeping it CLI-only; the banner
    tests migrate with it.
