# Own construction in the lifespan and type the composition state: implementation

Companion to
[`2026-08-18-lifespan-composition.md`](2026-08-18-lifespan-composition.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out.

## The inventory, taken fresh at main@4ec765d

Issue #142's anchors are pinned to main@8dd1a5f and have moved. The
plan's evidence section cites figures retaken at 4ec765d ("Record the
PR #186 review round"), which is the commit this branch's plan was
written against and the one the milestone branches descend from. They
are recorded here rather than only in the plan because every
milestone's design rests on them, and because a number that moves under
a later milestone is a finding rather than a typo.

The load-bearing ones, as the plan states them: `create_app` is
app.py:141-358 in a 370-line file; thirteen `app.state` attributes, read
by `ws.py` (seven), `ota.py` (five), `main.py`'s `DrainingServer` (one)
and twenty-six test sites; the mounted configuration API's own
seven-attribute state bag at `config/api.py:480-492`, read back by its
six dependencies and by `conversations/api.py:609`; the per-request
open at `config/api.py:545-565`; the unreferenced drain task at
`main.py:84`; the two capture events at app.py:304-327, keyed in the
conformance suite as `("samtal_server.app", "create_app", 1|2)`; and
177 `TestClient(...)` constructions, 53 without `with`, plus 30 bare
`create_app(...)` sites.

## M1: the typed composition object

Four commits, each green on both lanes: the filler cache first, since
nothing depended on it yet; the configuration API's typed runtime
second, which is self-contained behind its own state bag; the
composition itself with its readers and the test relocation third; and
the changelog and this document last.

Construction stays synchronous in `create_app` this milestone, as the
plan's milestone says. What moved is where the built objects are put and
how they are read back.

### What was written

**`samtal_server/composition.py`**: the `Composition` dataclass, the
fourteen fields the plan's Design section lists, `api: ApiRuntime`
included (the plan review's finding 2). It imports only downward and
names neither `ws`, `ota` nor `app`; its class docstring names the
sanctioned writers outside the composition root, starting with
`tests/unit/test_boundary_contract.py`'s runtime-factory injection
(finding 8), which is why the dataclass is plain rather than frozen. The
PR review round corrected that claim: it said "the one sanctioned
exception" while `test_conversations_boot.py` still replaces
`conversations` on a built composition in M1, so the docstring now names
both and marks the second as standing only until M2 converts it to the
constructor patch plan review finding 8 asks for.

**`ApiRuntime`**, declared beside `build_api` in `config/api.py`: the
seven request-time dependencies as typed fields, filled by `build_api`
exactly as the seven loose attributes were, and carried by the
sub-application as the single attribute `state.api_runtime`. All six
dependencies in `config/api.py` and the reader in
`conversations/api.py` take a field of it. The store handle is
unchanged this milestone: still `store_dependency(directory)`, still a
per-request open, migrate and dispose. That lifetime is M3's.

**`AgentFillers`** in `filler.py`: `__contains__`, `__getitem__`,
`get`, the `ready` property and `fill`, which asserts it is called
once. Before the fill it answers exactly as the empty dictionary
`create_app` used to hand out; after it, exactly as the filled one.
`create_app` builds it in place of the bare dict and the lifespan's
`update(...)` became `fill(...)`.

**`create_app`** builds what it built, in the same order and with the
same comments, into locals, and assembles one `Composition` set on
`app.state.composition` as the only state write. The API token exception
is preserved verbatim: resolved into a local, passed into the gate,
stored nowhere. The two capture events stay in `create_app`, so no
conformance key moves in M1.

**The lifespan** was rewritten in the same milestone, as finding 1
requires: it binds `comp = app.state.composition` once and reads the six
fields it used to read off `app.state`. It still constructs nothing.

**The readers**: `ws.py` binds the composition once and reads six typed
fields; `ota.py` binds it once per handler (three of its four handlers
read more than one field); `main.py`'s `_drain` reads
`composition.sessions`. Both `ws.py` and `ota.py` name the type under
`TYPE_CHECKING` only, for the reason `ota.py` already defers
`PendingDevices`: the composition names the pending table, whose module
imports `ota`, which imports `ws`, so a module-scope import in that
direction would not load.

### Deviations from the plan

- **`FillerRunner`'s constructor takes a protocol, and so do the two
  annotations that carry the cache to it.** The plan left the choice to
  implementation as long as `tests/support/sessions.py` keeps passing
  plain dictionaries. It does: `FillerCache` in `filler_runner.py`
  declares the three reads the runner makes, and `PipelineRuntime`'s
  `fillers` parameter and `bespoke_runtime_factory`'s widen to it too,
  because the composition root now hands them an `AgentFillers` and an
  annotation saying `dict[str, FillerClips]` would have been false.
- **Four test lines were reflowed rather than only re-pointed, at three
  sites.** The relocation is mechanical, but `.state.` grew twelve
  characters and four lines went past the 100-column limit; two sites
  bind a local (`composition = restarted.state.composition` in
  `test_config_api.py`, `bindings = app.state.composition.bindings` in
  the integration `test_device_bindings.py`) and one hoists
  `device_auth` out of a header dictionary in
  `test_event_descriptor_sanitization.py`. No assertion changed.
- **41 state-read sites moved, across 17 files, not 26.** The plan's
  evidence cites the count the issue recorded at main@8dd1a5f. The grep
  at branch time finds 41 reads, all of them the same mechanical
  `.state.X` to `.state.composition.X`, plus the one sub-app read in
  `test_config_api.py` (`api.state.store` to
  `api.state.api_runtime.store`). The file list is the plan's, with no
  file outside it.
- **`tests/unit/test_drain.py`'s fake app was not in the plan's list and
  had to move too.** It builds a two-line stand-in for `app.state` with
  a `sessions` attribute, which no grep for `.state.` finds because the
  attribute is a dictionary key in a `type(...)` call. It now carries a
  composition stub with the registry on it, and its one test failed
  loudly in the first full run rather than silently, which is what the
  drain reading through the composition is worth.

### Discoveries

- **The composition cannot be assembled before `build_api` returns**,
  because its `api` field is the `ApiRuntime` the factory builds. It is
  read back as `api.state.api_runtime`, which keeps `build_api` working
  standalone for the configuration-API suites exactly as before. M2's
  describe/build split will have to keep that ordering or hand the
  runtime out of `build_api` some other way.
- **`test_conversations_boot`'s store injection still works in M1.** It
  replaces the store before entering the lifespan, and in M1 the
  composition exists at `create_app` return, so
  `app.state.composition.conversations = Failing()` is the same test it
  was. Finding 8's constructor patch is M2's, when the store is built
  inside the lifespan.
- **Ruff prefers the unquoted annotation** on the `comp:` locals in
  `ws.py` and `ota.py` (UP037). A local variable's annotation is never
  evaluated at runtime, so the deferred `TYPE_CHECKING` import is enough
  and the quotes were removed.

### Verification

From `samtal-server/`:

- `uv run ruff check .`: all checks passed.
- `uv run pytest tests/unit -q`: 2,972 passed, 16 skipped. Five of the
  passes are the new `test_filler_cache.py`, which pins the cache
  answering as an empty dictionary before the fill and as the filled one
  after.
- `uv run pytest tests/integration -q`: 55 passed.
- The four generated references CI diffs (domain configuration,
  conversations schema, events, API OpenAPI): regenerated and diffed
  clean. No event moves in M1, which is what the events reference being
  byte-identical says.
- The acceptance grep,
  `grep -rn '\.state\.' samtal-server/samtal_server`: only
  `state.composition` (the write in `create_app`, the lifespan's bind,
  the three readers) and `state.api_runtime` (the write in `build_api`,
  the six dependencies and the conversations reader), both typed, with
  no exemption.

### The PR review round (M1)

External review of PR #187 (codex-cli 0.147.0, gpt-5.6-sol,
2026-08-18, diff main...3d47b62): three findings, verdict
"mergeable after the listed fixes". All three fixed.

1. **P2: state reads still typed as Any.** Starlette's
   `State.__getattr__` defeats the typed-fields requirement
   unless every boundary binds an annotated local. Fixed in
   d0cae9c: `comp: Composition` / `runtime: ApiRuntime` bound at
   every read site, with `TYPE_CHECKING` imports where needed;
   the drain's binding sits inside its `try` so a broken state
   still reaches `super().handle_exit`.
2. **P3: the composition docstring denied M1's second writer.**
   Fixed in 9b0e7e6: both writers named, the conversations one
   marked temporary until M2's constructor-patch conversion.
3. **P3: the lifespan-to-cache wiring was untested.** Fixed in
   367bf43 with a pin that fails if the build result is
   discarded instead of filled (verified by mutation before
   committing the passing form).

## M2: the lifespan owns construction

Four commits. The describe/build split with the test-lane migration that
has to land with it; the leak tests; the shutdown signal guard pulled
forward from M4; and the exit-code fix the real-uvicorn verification
turned up.

### What was written

**The describe phase.** `create_app(config, secrets, on_started=None)`
resolves enforcement, resolves the configuration when none was passed,
reads the device-auth secret and throws the issuer away (the refusal is
what stays at describe time, not the object), resolves the API token
into a local and passes it into the gate, builds the API shell with
`build_api(token, database_dir)`, registers `/healthz` and the routers,
mounts the API, and stashes a `_CompositionSeed`. It opens nothing.

**The build phase.** `lifespan` binds the seed, opens an
`AsyncExitStack`, and calls `_build_composition`, which builds what
`create_app` used to build, in the same order and with the same
comments, registering each release as it acquires: `bindings.dispose`
the moment the pool is open, `conversations.stop` the moment the store
is constructed and before it is started, `mcp_servers.stop_all` in front
of `start_all`. `build_agent_providers` runs under `asyncio.to_thread`.
Then the API's live pieces are installed on the mounted sub-application,
the composition is set on `app.state`, the writer is started, the
fillers are synthesized and filled, the MCP servers are connected,
`on_started()` is called, and the generator yields.

**The failure bridge.** `BOOT_FAILURES` is `(ConfigError,
EventEnforcementError, ProviderError)`; the database busy refusal is a
`ConfigError` subclass, so the tuple is the whole taxonomy the plan
names. The lifespan catches it, records the sentence on the seed, and
raises `StartupFailed(sentence)` **outside** the `except` block, which
leaves the replacement with `__cause__` and `__context__` both None
rather than merely suppressed. `startup_failure(app)` is how `main()`
asks; it prints and exits 1.

**`build_api_runtime`** in `config/api.py` assembles the `ApiRuntime`
that `build_api` used to assemble inline. `build_api` calls it with what
it was given, so the standalone path is unchanged; the parent lifespan
calls it with the live objects and installs the result on the mounted
application's state. The store handle is still `store_dependency`, still
per request: that lifetime is M3's.

**`tests/support/apps.py`**: `entered_app(config, secrets, **options)`
yielding `(app, client)`, and `entered_client` for the common case.

### Deviations from the plan

- **The seed carries the mounted API application, and the device auth
  issuer is built in the lifespan rather than carried.** The plan says
  the seed holds "config and secrets only", which is finding 10's rule
  about the token; it also requires the lifespan to attach the API's
  live pieces to the mounted sub-application, and a lifespan that
  receives only `app` has no other way to reach it. So the seed is
  config, secrets, the mounted application, `on_started` and the
  recorded failure sentence. It holds no credential: the token is inside
  the gate middleware, where `create_app` passed it. `device_auth` is
  built where everything else is, and `create_app` calls
  `build_device_auth` only for the refusal.
- **The capture events' identity keys are
  `("samtal_server.app", "_build_composition", 1|2)`, not
  `(..., "lifespan", 1|2)`.** The build is a named function rather than
  200 lines inside an async generator with a `try`/`except`/`yield`
  around them; the channel is unchanged, which is what the plan's
  constraint was about. The conformance suite's exhaustive map named
  both halves of the change and was updated to match.
- **The signal guard was pulled forward from M4**, as the milestone
  brief asked: `handle_exit` delegates straight through when
  `app.state` has no composition. Without it this merge would be
  releasable with a shutdown path that raises inside a signal handler
  during any startup long enough to be signalled, which is exactly the
  startup this milestone makes long. The task ownership and the bounded
  settle stay M4's.
- **Teardown order between the bindings pool and the conversation store
  is reversed from today's.** Today the lifespan disposes the bindings
  inside the inner `finally` and stops the store in the outer one; under
  the exit stack the store is released before the pool, because it was
  acquired after it. Nothing depends on the order: the store's writer
  drains its own queue and the pool serves device lookups, and every
  session has stopped before either runs.
- **A failed MCP `start_all` now releases everything.** Today it would
  leave the bindings pool open and the MCP managers unstopped, because
  `start_all` sits outside the inner `try`. The exit stack has no such
  gap. `start_all` is documented never to raise, so this is a latent
  case rather than an observed one.
- **`serve()` swallows `SystemExit` on a recorded boot failure.** See
  the discovery below; without it the plan's "serve() returns and
  main() prints" does not happen at all.
- **The sanitized sentence appears twice on a refused startup, not
  once.** Uvicorn logs the whole formatted traceback of a failed
  lifespan (Starlette sends it as the `lifespan.startup.failed`
  message), and that traceback ends with `StartupFailed: <sentence>`;
  `main()` then prints the sentence as its own line. The plan's
  no-leak requirement holds exactly: the traceback has no chain to walk
  into, and the integration test hunts the sentinel through both
  streams. The traceback itself is uvicorn's operator surface, which
  the plan already accepted for the module entry point.

### Discoveries

- **Uvicorn ends the process itself when a lifespan startup fails.**
  `uvicorn.Server.startup` calls `sys.exit(STARTUP_FAILURE)`, which is
  3, from inside the coroutine `Server.run` is driving. `serve()`
  therefore never returned and `main()`'s reporting never ran: the exit
  code was 3 and the only surface was uvicorn's traceback. `serve()`
  now catches `SystemExit` and swallows it exactly when the lifespan
  recorded a boot failure, so the entry point's own sentence and exit
  code of 1 survive; any other `SystemExit` from in there propagates.
  This is the one thing in this milestone that a test entering an
  application by hand could not have found, which is what the plan's
  real-uvicorn verification existed for.
- **`test_conversations_boot`'s store injections both had to move to
  the constructor**, not just the one finding 8 names. The store is
  built inside the lifespan now, so a test that wants to hold the
  instance the build made has to catch it at
  `monkeypatch.setattr(app_module, "ConversationStore", ...)`; a test
  that wants the writer's start to fail replaces the class with one that
  refuses. `test_the_file_is_open_before_the_lifespan_runs` inverted
  into `test_nothing_is_opened_before_the_lifespan_runs`, which is the
  leak claim in the suite that owns the store.
- **Three `test_config_api_runtime` tests documented "no lifespan, so
  nothing is connected"** and used that to assert a `DOWN` MCP status
  through the mount. Entering the lifespan connects the stdio server
  they configure, so they now configure a command that does not exist:
  the managers are down for a reason the test states rather than for the
  absence of a startup, and the assertions are unchanged.
- **The remaining unentered `TestClient` sites are honest ones.** After
  the migration, 36 no-`with` constructions remain: 33 are the
  standalone `build_api(...)` suites the plan defers to M3, and three
  drive routes that read nothing the lifespan builds (two on `/healthz`,
  one on the docs 404).

### The fixture migration inventory

Taken by grep at branch time, then by running the lanes. The plan's
figures (53 no-`with` `TestClient` sites across 21 files, 30 bare
`create_app` sites in 15 files, a union of 29 files) were the inventory
of what *could* need the lifespan. What actually needed it is what
failed: **116 unit tests across 14 files, and 2 integration tests across
2 files**, all of them the same `AttributeError: 'State' object has no
attribute 'composition'`.

Sixteen files changed, plus the new support module:

| File | Sites | Shape of the change |
| --- | --- | --- |
| `tests/support/apps.py` | new | `entered_app`, `entered_client` |
| `tests/support/checkin.py` | 2 | `ota_client`/`activation_client` became context managers |
| `tests/unit/test_ota.py` | 24 | every check-in through the entered client |
| `tests/unit/test_onboarding_activation.py` | 30 | the ceremony inside one entered client per test |
| `tests/unit/test_onboarding.py` | 19 | local `client_for` became a context manager |
| `tests/unit/test_server_event_pins.py` | 17 | the OTA and onboarding pins, and the two capture pins |
| `tests/unit/test_event_descriptor_sanitization.py` | 13 | local `ota_client` became a context manager |
| `tests/unit/test_config_api_runtime.py` | 4 | the three mounted-API wiring tests |
| `tests/unit/test_app.py` | 3 | the two composition reads |
| `tests/unit/test_auth_boot.py` | 3 | the two issuer reads; the refusals stayed bare |
| `tests/unit/test_onboarding_banner.py` | 3 | the two describe-line reads |
| `tests/unit/test_ota_tokens.py` | 3 | the two token issuers |
| `tests/unit/test_registry.py` | 2 | the cap read |
| `tests/unit/test_boundary_contract.py` | 3 | the factory injection moved inside the entered context |
| `tests/unit/test_conversations_boot.py` | 3 | the store injections moved to the constructor |
| `tests/integration/test_config_api.py` | 1 | the restart's composition read moved inside its client |
| `tests/integration/test_activation.py` | 1 | the same, for the derived onboarding key |

Two new test files: `tests/unit/test_app_lifespan.py` (the leak,
teardown, partial-startup, bridge and banner claims) and
`tests/integration/test_startup_failure.py` (the real entry point in a
process of its own, with the credential-shaped sentinel).
`tests/unit/test_drain.py` gained the blocked-startup signal test, and
`tests/unit/test_event_schema_conformance.py` the two moved keys.

### Verification

From `samtal-server/`:

- `uv run ruff check .`: all checks passed.
- `uv run pytest tests/unit -q`: 2,980 passed, 16 skipped (2,972 before
  this milestone; the eight are the seven in `test_app_lifespan.py` and
  the drain's signal test).
- `uv run pytest tests/integration -q`: 57 passed (55 before; the two
  are the startup-failure pair).
- The four generated references CI diffs (domain configuration,
  conversations schema, events, API OpenAPI): regenerated and diffed
  clean. The capture events moved function without changing channel,
  message, level or fields, which is what the events reference being
  byte-identical says.
- The conformance suite specifically
  (`tests/unit/test_event_schema_conformance.py`): 485 passed. Its
  exhaustive sidecar named the two moved paths in both directions and
  was updated to `("samtal_server.app", "_build_composition", 1|2)`.
- The acceptance grep, `grep -rn '\.state\.'
  samtal-server/samtal_server`: `state.composition` (the lifespan's
  write, the three readers), `state.api_runtime` (the write in
  `build_api`, the lifespan's install, the six dependencies and the
  conversations reader) and `state.seed` (the describe phase's write,
  the lifespan's read, and `startup_failure`'s). All three are declared
  dataclasses; the third is the private build seed the split requires,
  which no handler reads.
