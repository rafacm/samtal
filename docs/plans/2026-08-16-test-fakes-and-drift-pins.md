# Shared test fakes and drift-pinning tests

## Goal

Implement issue #144: the test suite (42,052 lines under `tests/unit`
alone at plan time) has no shared fake library, couples test files
through helper imports, and lacks the cross-checking tests that would
have caught the drift the 2026-08-14 review found. Give it a fakes
package under `tests/support`, remove every unit-test import of
another test module, and add the named drift pins: the example
configuration's coverage of `ServerConfig`, the docgen entities'
example filenames against `examples/`, and the CLI's response-shape
predicates against the API's pydantic models.

The companion implementation doc,
[`2026-08-16-test-fakes-and-drift-pins-implementation.md`](2026-08-16-test-fakes-and-drift-pins-implementation.md),
records what each milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by issue #144 and not re-litigated here:

1. **A fakes package under `tests/support`**: SDK stream fakes for
   both LLM dialects, the scripted device and socket fakes, the
   scripted providers. Test modules import from it instead of from
   each other. The boundary-contract pattern (`StubRuntime` and
   `FakeDevice` from `test_boundary_contract.py`) is promoted there
   as the template for seam-level testing.
2. **Test files stop importing helpers from other test files**;
   shared helpers move to support or a conftest.
3. **Drift-pinning tests where two encodings must agree** and no
   refactor deletes the duplication: `config.example.yaml` covers
   every `ServerConfig` field, `docgen.ENTITIES` example filenames
   match `examples/`, and, until #139 lands, the CLI/API
   response-shape cross-checks.
4. **The `test_config_cli.py` split coordinates with #139** rather
   than preceding it: the split scheme is decided in this plan (see
   below) so #139 can inherit it, and the physical split is #139's
   to execute along the file boundaries it produces.
5. **No assertion weakening anywhere**: this issue moves and adds
   tests, it does not simplify them. Test count does not decrease.

## Evidence, re-verified at plan time

The issue's evidence is pinned to main@8dd1a5f; re-verified against
main@c410af8 (after #138 and #120 landed, both of which added test
files), the coupling has grown, which is the point of doing this now:

- 84 cross-module import statements in 32 test files reach into 26
  distinct test modules. The authoritative inventory is
  `grep -rn "from tests\.unit\.test_\|import tests\.unit\.test_"
  tests/ --include="*.py"`, re-run at the start of every milestone;
  the counts above are its output at c410af8, not a list to trust
  later.
- The hubs, by import count: `test_session.py` (19 importing
  statements), `test_session_tools.py` (12), `test_tools_mcp.py`
  (7), `test_session_events.py` (7), `test_ota.py` (6),
  `test_device_bindings.py` (4), and twenty more with one to three.
- The SDK fake block sits at `test_providers_llm_tools.py:118-390`
  and is 14 classes at c410af8 (the issue's 13 was its 8dd1a5f
  pin): seven fake the anthropic streaming dialect (`FakeBlock`
  through `FakeMessages`), seven the openai one (`FakeFunction`
  through `FakeCompletions`).
- Literal duplicates the grep for `^class` finds: a `Falsey`
  truthiness-probe client is hand-rolled four times
  (`test_providers_llm.py:117`, `test_providers_elevenlabs.py:438`,
  `test_providers_openai_tts.py:491`,
  `test_providers_openai_asr.py:814`); `BrokenTts` is defined twice
  (`test_session_filler.py:326`, `test_session_record.py:576`).
- Of the drift pins the issue names, one has landed since the
  issue was written: #134's fix added
  `test_a_local_write_says_what_the_api_says_for_the_same_act`
  (`test_config_cli.py:2070`), which pins every `--local` write
  notice against the API constant for the same act. That pin is
  recorded here as already satisfied and is not duplicated. The
  CLI/API shape predicates, the example-config coverage, and the
  docgen examples remain unpinned, verified by grep: no test
  imports `PENDING_FIELDS`, `STATUS_FIELDS`, `STATUS_STATES`,
  `RELOAD_OUTCOMES`, or `PROMPT_BLOCK_FIELDS`, and
  `test_config_examples.py` installs the fragments but never walks
  `ServerConfig`'s fields.

## Decisions this plan makes

### What moves is decided by a rule, not a taste

Four categories move to `tests/support`, and nothing else:

1. **Every name imported across a test-module boundary today**, as
   listed by the inventory grep. This is the minimum that makes the
   no-cross-import criterion true. The boundary-contract pair
   (`StubRuntime` and the boundary `FakeDevice`), promoted by the
   issue's decision 1, moves under this category even though it is
   single-module today.
2. **The SDK fake block**, named by the issue even though it is
   single-module today: it is the template duplication the next
   provider suite would copy.
3. **Literal duplicates**: a fake hand-rolled to the same purpose in
   two or more modules (`Falsey` x4, `BrokenTts` x2) is consolidated
   into one support definition, with the strongest of the duplicate
   definitions kept. Consolidation replaces definitions, never
   assertions; if two duplicates genuinely differ in behavior the
   difference is preserved as two named classes rather than merged.
4. **The minimal module-local dependency closure of every moved
   root.** A moved helper's body is not edited, so whatever
   module-level names its body reads move with it (or already live
   in support): `Gate` brings `TIMEOUT_S`
   (`test_conversations_store.py:52`), `corrupt` brings `CORRUPT`
   with its no-leak `STORED` sentinel verbatim
   (`test_tools_memory.py:132-140`), `StubRuntime` and the boundary
   `FakeDevice` bring `OUTPUT_RATE`, `FRAME_BYTES`, and `REPLY_PCM`
   (`test_boundary_contract.py:63-68`). The closure is computed per
   milestone by AST inspection of each moved definition's free
   names, not by memory, and the closure list goes in the PR body.
   Sentinel constants move byte-identical: they are load-bearing
   for the no-leak tests that plant them.

Two boundaries of the rule:

- A fake defined and used in one module stays in that module.
  Locality is a feature; the support package is for what is already
  shared, not a museum for everything fake-shaped.
- A name that crosses the boundary but is defined in
  `samtal_server` (a production re-export, like `OpusEncoder`
  imported via `test_session.py`) is not a helper: the importing
  site's import is redirected to the production module, and nothing
  is copied into support.

### The support package layout

`tests/support` today holds three real MCP subprocess servers plus
`mcp_stdio_server.py`, imported as `tests.support.*` with no
`__init__.py`, which works because the repository root is on the
test path. The new modules follow the same convention (no
`__init__.py`, module docstrings say what belongs in each). Modules
are named for the seam they serve, not the file they came from:

- `llm_sdk.py`: the SDK stream fakes for both LLM dialects, in two
  clearly headed sections (anthropic dialect: `FakeBlock`,
  `FakeUsage`, `FakeMessage`, `FakeTextDelta`, `FakeStreamEvent`,
  `FakeStream`, `FakeMessages`; openai dialect: `FakeFunction`,
  `FakeFragment`, `FakeDelta`, `FakeChoice`, `FakeChunkUsage`,
  `FakeChunk`, `FakeCompletions`), plus the consolidated `Falsey`
  client probe (the honest-seams check that a falsey injected
  client is still used, never replaced by truthiness). Three of the
  four `Falsey` definitions are nested inside test functions, so
  consolidating them is necessarily a test-function edit; those
  three deletions (`test_providers_elevenlabs.py:433-451`,
  `test_providers_openai_tts.py:486-497`,
  `test_providers_openai_asr.py:809-820`) are the only permitted
  edits inside test functions in the whole issue, each replacing a
  nested class with the imported one and changing nothing else in
  the function. And because the seam those tests probe is
  falsiness itself, a support probe that accidentally became
  truthy would leave every identity assertion passing while
  testing nothing: M1 adds `tests/unit/test_support_fakes.py` with
  a contract test asserting `bool(Falsey()) is False`, so the
  premise is pinned where the fake lives.
- `providers.py`: the scripted pipeline-stage providers that cross
  module boundaries: `ScriptedLlm`, `StallingLlm` (with `STALL_S`),
  `LockingAsr`, `Unreachable`, and the two broken-TTS fakes, which
  are not duplicates and stay two classes: `BrokenTts` (the filler
  one, raising synchronously when `synthesize()` is called, with
  its class-level sample rate) and `BrokenStreamingTts` (the record
  one, an async generator declaring `egress = False`, initializing
  its rate, raising during iteration). The record module imports it
  as `from tests.support.providers import BrokenStreamingTts as
  BrokenTts`, so its test bodies stay byte-identical.
- `sockets.py`: the scripted device-socket fakes: `RecordingSocket`,
  `LoopingSocket`, `QuietSocket`.
- `boundary.py`: the promoted seam-testing template: `StubRuntime`
  and the boundary `FakeDevice` from `test_boundary_contract.py`,
  with that module's docstring argument (drive each side of a seam
  from a scripted far side, so the assertions see one side alone)
  carried along as the package's stated pattern.
- `device_tools.py`: the device MCP tool-client `FakeDevice` from
  `test_tools_device.py` and its `STATUS` vocabulary.
- `configs.py`: the shared `Config` builders and the constants they
  share: `config_with_agent`, `two_persona_config`, `base_config`,
  `watchdog_config`, `masked_config`, `recording_config`,
  `config_with` (from `test_config_tools.py`),
  `load_config_from_data`, the MAC/UUID constants, `DEVICE_HELLO`,
  and the audio-shape constants (`SAMPLE_RATE`, `FRAME_BYTES`, and
  siblings).
- `wire.py`: driving a session over the real websocket: `connect`,
  `token_for`, `shake_hands`, `speech_pcm`, `send_pcm`,
  `endpoint_silence`, `collect_until`, `collect_reply`,
  `say_something`, the reply predicates and extractors
  (`is_reply_start`, `is_reply_end`, `is_transcript`, `sentences`,
  `heard_ms`, `audio_ms`, `assert_endpointed_speech`), and the
  ws-auth helpers `device_headers` and `handshake`.
- `sessions.py`: driving a `DeviceSession` in process:
  `device_session`, `session_for`, `run_reply`, `drive_reply`,
  `start_reply`, `open_session`, `reply_with`, `call`, and the
  conversation-session drivers `until` and `Gate`.
- `events.py`: reading the structured log in tests: `events`,
  `only` (from `test_session_events.py`), `fields_of`, `one_event`
  (from `test_tools_mcp.py`).
- `checkin.py`: the OTA/onboarding scaffolding: `SYSTEM_INFO`,
  `MOCK_AGENT`, `MOCK_PROVIDERS`, `client_for`, the activation
  helpers from `test_onboarding_activation.py`, and `Clock` from
  `test_onboarding_pending.py`.
- `mcp.py`: the MCP entry builders that cross modules:
  `stdio_entry`, `entry_data`, `config_granting`, `running`,
  `started`, `reading`, `config_with` (reload variant, renamed
  apart from the config one at move time), `serving`,
  `MANAGER_LOGGER`, `SHADOWED_POSITION`.
- `stores.py`: capture and conversation-store scaffolding:
  `MANIFEST`, `store`, `tone` (capture), `rows` and the
  conversations `MANIFEST` (renamed apart at move time), and
  `_corrupt` (public as `corrupt` once shared).
- `registry.py`: `FakeSession` and `registry_with` from
  `test_drain.py`, plus `booted`, `check_in`, and the binding
  constants from `test_device_bindings.py`.

Exact placement of a handful of small names may shift a module
within this set during implementation; the implementation doc
records each such deviation. What may not shift: the rule for what
moves, the no-cross-import end state, and assertion bodies.

Where two moved names collide (`config_with` twice, `MANIFEST`
twice, `DEVICE_MAC` with different values in `test_session.py` and
`test_ota.py`), the support definitions are renamed apart with
names that say which seam they belong to, and every importing site
keeps its current local name through an import alias
(`from tests.support.stores import CONVERSATIONS_MANIFEST as
MANIFEST`), so call sites and assertions stay byte-identical.
Renames live in import lines only; edits of assertions are out of
scope everywhere.
The two classes named `FakeDevice` remain two classes: they fake
different seams (the device edge as a runtime sees it, and the
device MCP tool channel as the tool client sees it), live in
different modules, and merging them would invent a fake with two
jobs.

### Plain helpers move to support; fixtures stay fixtures

Almost everything imported across module boundaries today is a
plain callable, class, or constant, which is why the import web
works at all (pytest fixtures cannot be imported and used as
fixtures). Anything moved keeps exactly its current calling
convention. If implementation finds a shared name that is a fixture
in disguise (used via `pytest.fixture` registration somewhere), it
moves to a new `tests/unit/conftest.py` instead of support, and the
implementation doc says so. No fixture is converted to a helper or
vice versa in this issue.

### The import guard makes the criterion self-enforcing

A new `tests/unit/test_support_boundaries.py` walks every
`tests/**/*.py` file with `ast` and asserts:

- no test module (`test_*.py`) imports another test module, at
  module level or inside a function (the current web includes a
  function-level `from tests.unit.test_ota import SYSTEM_INFO`);
- no `tests/support` module imports from any test module, so the
  dependency arrow points one way.

Integration and smoke tests importing their own `conftest` remain
allowed (decision 2 permits conftest), and the guard says so in its
docstring. This is the same move `test_event_surface_guard.py`
already made for event emission sites: the acceptance criterion
becomes a test, so the next session cannot regress it silently.

### The drift pins, each with its true relation

Each pin states the relation the two encodings actually hold, read
from the code at c410af8, not an imagined equality. Each is proven
by mutation during development (change one side, watch the pin
fail, restore it) and the mutation is recorded in the milestone PR.

1. **`config.example.yaml` covers every `ServerConfig` field**
   (in `test_config_examples.py`, whose docstring already owns the
   "a fragment nobody can install is worse than no fragment"
   argument). The test walks `ServerConfig.model_fields`
   recursively into nested `BaseModel` sections (`onboarding`,
   `auth`, `api`, `limits`, `database`, `capture`, `conversations`,
   and future siblings, discovered from annotations rather than
   listed). For each leaf field path it asserts the example file
   mentions the key: either as a live YAML key at the right
   nesting, or as a commented-out `# key:` line, because the file's
   own convention is that a field whose default is right for a
   plain LAN deployment appears as a commented line with its
   reasoning (`# websocket_url:`, `# public_url:`). The comment
   scan requires the `key:` form, so prose mentioning a word does
   not count as coverage.
2. **`docgen.ENTITIES` example filenames match `examples/`**, both
   directions (in `test_config_docgen.py`): every filename in every
   `Entity.examples` tuple exists under `examples/`, and every
   `*.yaml` under `examples/` is claimed by exactly one entity. At
   plan time both sides hold 13 files; the pin is what keeps a
   14th example from being added to one side only. The existing
   README listing test (`test_config_examples.py:117`) checks
   `examples/README.md`, a third encoding; it stays as is.
3. **The CLI's response-shape predicates against the API's models**
   (new file `tests/unit/test_config_cli_shapes.py`, one file so
   that #139, which deletes the predicates entirely, deletes the
   bridge with them; the file's docstring says it exists to be
   deleted by #139):
   - `cli.PENDING_FIELDS` is a subset of
     `api.PendingDevice.model_fields` (the predicate names what the
     CLI requires to read a listing; the model also carries
     `client_id`, `first_seen`, `last_seen` the listing does not
     render).
   - `cli.STATUS_FIELDS` equals `api.McpServerStatus.model_fields`
     as sets.
   - `cli.STATUS_STATES` equals the `Literal` arguments of
     `McpServerStatus.state`, via `typing.get_args`.
   - `set(cli.RELOAD_OUTCOMES)` equals the `list[str]` outcome
     fields of `api.McpReloadResult` (every field except
     `servers`), and the tuple carries no duplicates.
   - `cli.PROMPT_BLOCK_FIELDS` equals the required fields of
     `api.PromptBlock` (`{name for name, field in
     PromptBlock.model_fields.items() if field.is_required()}`):
     the model also carries an optional `name` the CLI does not
     require to read a block, and the pin records that `name` stays
     optional, type-checked only when present.
   `cli.PENDING_COLUMNS` is not pinned: it is a rendering choice
   (`code`, `device`, `expires` are presentation names, not field
   names), and pinning presentation to field names would invent an
   equality the code does not hold.
4. **The `--local` notices**: already pinned by #134
   (`test_a_local_write_says_what_the_api_says_for_the_same_act`,
   plus the recovery-subset membership tests around
   `test_config_cli.py:2119-2237`). Nothing to add; recorded here
   so the issue's list is answered item by item.

The mutation proofs cover every relation each pin claims, one
mutation per branch, each applied, observed failing, and reverted,
with the failure output recorded in the M4 PR body:

- Example-config coverage: delete one live top-level key (`port`),
  and separately one commented key inside a nested section (picked
  from the file at implementation time, so the proof exercises the
  comment scan at depth, not only at the top level where
  `# websocket_url:` sits). Each deletion must fail the pin,
  proving the live-key walk and the comment scan separately.
- Docgen examples, all three branches: add an unclaimed `*.yaml`
  under `examples/`; remove a file an entity claims; claim one
  existing file from a second entity. Each must fail with a
  message naming the file.
- CLI/API shapes, one mutation per relation: drop a member from
  each CLI frozenset in turn (`PENDING_FIELDS`, `STATUS_FIELDS`,
  `STATUS_STATES`, `PROMPT_BLOCK_FIELDS`); duplicate an entry in
  `RELOAD_OUTCOMES` (the no-duplicates clause); and for the
  required-versus-optional distinction, flip `PromptBlock.name` to
  required in a scratch copy of the check and watch the equality
  break, since that distinction is exactly what the pin encodes.

### The `test_config_cli.py` split, decided here for #139

`test_config_cli.py` (2,305 lines, 101 tests) mirrors the source
monolith. The issue settles that the physical split follows the
file boundaries #139 produces; what #139 inherits from this plan is
the bucket boundaries, each anchored to a production concern #139's
issue body names, so the split demonstrably follows #139's
structure rather than this plan's taste:

- the acceptance spine (the empty-database-to-working-configuration
  walk and per-entity write/show/list behavior) stays in
  `test_config_cli.py`, anchored to the descriptor-driven entity
  handling (#139: "one descriptor per entity ... consumed by
  store, views, api, cli, and docgen");
- transport and client behavior (URL/token/TLS resolution, refusal
  of unreadable or credentialed URLs, timeouts,
  unreachable-server reporting), anchored to the CLI-as-API-client
  seam (#139: "the CLI renders API responses from the same
  pydantic response models api.py declares");
- rendering of status, reload, pending and prompt answers,
  anchored to the same response-model rendering concern, and the
  natural new home of whatever survives of the shape checks once
  the frozensets are deleted;
- the `--local` recovery subset, anchored to the unified dispatch
  (#139: "local and HTTP branches unify behind one dispatch so
  acknowledgements and notices come from one place");
- secrets entry, masking, refusals and key failures, anchored to
  the secrets write path (#139 keeps `--local` at "exactly the
  current four commands", two of which are secret commands);
- parser grammar, help and exit codes, anchored to the parser
  wiring #139's descriptor generates.

Exact filenames are #139's to fix when its production split is
final; the buckets above are the inherited decision, and a #139
production split that merges or divides a concern moves the bucket
with it. This issue does not move any of those tests; the
sentinel-hunting no-leak tests in that file stay word-for-word
where they are until #139 relocates them with the code they pin.

### Four milestones, four PRs, stacked

Tests-only changes keep `main` releasable at every merge, so the
milestone cut is about review focus, not release safety:

1. **M1, the fakes package is born**: `llm_sdk.py` with both SDK
   dialect fake families and the consolidated `Falsey` probe;
   the five provider test modules it touches
   (`test_providers_llm_tools.py`, `test_providers_llm.py`,
   `test_providers_elevenlabs.py`, `test_providers_openai_tts.py`,
   `test_providers_openai_asr.py`) import from it. Small, and it
   establishes the package conventions the bigger moves follow.
2. **M2, the session family decouples**: `providers.py`,
   `sockets.py`, `boundary.py`, `device_tools.py`, `configs.py`,
   `wire.py`, `sessions.py`, `events.py`; every `test_session*`
   hub, `test_tools_device.py`, and `test_boundary_contract.py`
   stops being imported by anyone.
3. **M3, the feature suites decouple and the guard lands**:
   `checkin.py`, `mcp.py`, `stores.py`, `registry.py`; the
   remaining hubs (`test_ota`, `test_ws_auth`, onboarding, config,
   capture, bindings, drain, MCP, memory, conversations) stop being
   imported; `test_support_boundaries.py` lands and passes, making
   the zero-cross-import state enforced.
4. **M4, the drift pins**: the three pin families above, each with
   its recorded mutation proof, plus the CHANGELOG entry for the
   issue as a whole.

Each of M1-M3 is a move: the proof of behavior preservation is
that `uv run pytest tests/unit -q` and the integration lane pass
before and after, the collected test count
(`uv run pytest tests/unit -q --collect-only | tail -1`) does not
decrease, and the diff to any test function is import paths and
helper renames only. M4 adds tests and may only raise the count.

## Files touched

New: `tests/support/llm_sdk.py`, `providers.py`, `sockets.py`,
`boundary.py`, `device_tools.py`, `configs.py`, `wire.py`,
`sessions.py`, `events.py`, `checkin.py`, `mcp.py`, `stores.py`,
`registry.py`, `tests/unit/test_support_boundaries.py`,
`tests/unit/test_support_fakes.py`,
`tests/unit/test_config_cli_shapes.py`, this plan's implementation
doc.

Modified: the five M1 provider test modules named in the milestone
list (none of which are in the cross-import web, which is why the
inventory grep does not list them), the 32 importing test files
from the inventory grep, the
26 hub modules they import from (definitions removed, imports
added), `tests/unit/test_config_examples.py`,
`tests/unit/test_config_docgen.py`, `CHANGELOG.md`.

Untouched on purpose: everything under `samtal_server/` (this
issue changes no source), the three MCP subprocess servers in
`tests/support`, `tests/integration` and `tests/smoke` beyond
whatever the inventory grep shows importing unit test modules
(at c410af8: nothing), `config.example.yaml` and `examples/`
themselves unless a pin catches real drift, in which case the fix
is its own commit explained in the PR.

## Tests

The moved tests are the test of the move: both lanes green at every
milestone, count non-decreasing, assertions byte-identical. The new
tests are the guard and the pins, listed above with their relations
and mutation proofs. No new test asset duplicates an existing one:
the pins reuse `ServerConfig`, `ENTITIES`, and the API models as
imported objects rather than restating their contents.

## Verification

From `samtal-server/`, per milestone:

- `uv run ruff check .`
- `uv run pytest tests/unit -q` and
  `uv run pytest tests/integration -q`
- `uv run pytest tests/unit -q --collect-only | tail -1` recorded
  before and after in the PR body; never lower after.
- The inventory grep re-run; its line count reaches zero for unit
  tests at M3 and stays there (the guard test enforces it).
- `git diff` inspected for assertion edits: outside import lines,
  the only permitted diffs inside test functions are the three
  nested `Falsey` deletions enumerated in the layout section, each
  replacing a nested class with the imported probe and nothing
  else.
- Every relocated definition compared against its origin by
  normalized AST (`ast.dump` of the parsed def, names aside where
  a rename was decided), recorded in the PR body as a pass/fail
  per moved root, so "the body did not change in flight" is a
  checked claim rather than a reviewed impression.
- For M4, each pin's mutation proof recorded in the PR body: the
  mutation applied, the failure message observed, the restore.
- `PYTHONDONTWRITEBYTECODE=1` on every run outside pytest, per
  AGENTS.md.

## Risks and mitigations

- **A moved name is secretly a fixture or context-manager bound to
  module state.** Mitigation: classify every moved name at
  milestone start by reading its definition, not its import; the
  fixture escape hatch is `tests/unit/conftest.py`, and the
  implementation doc records any use of it.
- **Import-time side effects change collection order behavior.**
  `tests/conftest.py` sets the auth secret and bytecode settings at
  import time before any test module loads; support modules import
  `samtal_server` the same way the existing MCP server scripts do,
  and no support module runs work at import time beyond constant
  construction. The full-suite runs at each milestone are the
  check.
- **Circular imports inside support.** The modules are layered:
  `configs.py` imports only `samtal_server`; `wire.py`,
  `sessions.py`, and the rest may import `configs.py`; nothing in
  support imports test modules (guard-enforced). Any cycle is a
  layering mistake to fix, not to shim.
- **The comment-scan half of the example-config pin passes on
  prose.** The scan requires the literal `key:` form in a comment
  line; the mutation matrix in the drift-pins section (a live key
  and a nested commented key, deleted separately) is the check
  that the scan is neither too loose nor too tight.
- **Two definitions consolidated as duplicates turn out to
  differ.** The consolidation rule keeps behavioral differences as
  two named classes; the one known case, the two `BrokenTts`
  variants, is decided above (two classes, `BrokenTts` and
  `BrokenStreamingTts`, an import alias preserving the record
  module's local name). Any further case found mid-move follows the
  same rule and is recorded in the implementation doc.
- **Conflict with #155/#139 work.** The batch runs one issue at a
  time (tracking issue #146), so no concurrent edits; #139 inherits
  the split scheme and the `test_config_cli_shapes.py` deletion
  marker from this plan.

## Milestones

- [x] [**M1: the fakes package is born**](2026-08-16-test-fakes-and-drift-pins-implementation.md#milestone-1-the-fakes-package-is-born)
      (PR TBD): `tests/support/llm_sdk.py` holds both SDK dialect fake
      families and the consolidated `Falsey` probe; the provider test
      modules import from it; `test_support_fakes.py` pins the probe's
      falsiness; both lanes green, count non-decreasing.
- [ ] M2: the session family decouples: the eight session-side
      support modules exist, no test module imports any
      `test_session*`, `test_tools_device`, or
      `test_boundary_contract` module; both lanes green, count
      non-decreasing.
- [ ] M3: the feature suites decouple and the guard lands: the
      remaining four support modules exist, the unit-lane
      cross-import count is zero, `test_support_boundaries.py`
      enforces it; both lanes green, count non-decreasing.
- [ ] M4: the drift pins land with recorded mutation proofs:
      example-config coverage of `ServerConfig`, docgen examples
      against `examples/` both ways, the CLI/API shape bridge in
      `test_config_cli_shapes.py`; CHANGELOG entry; both lanes
      green.

## Plan review round

External review of commit df813ed by codex 0.147.0 (model
gpt-5.6-sol), 2026-08-16, prompted with this plan, the issue body,
the hub and importer test files, the drift-pin substrate, and the
prior plans. Findings as received, condensed but faithful:

1. **P1: the `PROMPT_BLOCK_FIELDS` equality is false.** The CLI
   deliberately requires only `provenance`, `characters`, `text`
   (`cli.py:178-179`), while `PromptBlock` also has an optional
   `name` (`api.py:715-755`). M4 as written fails immediately. Pin
   equality against the required fields of `PromptBlock`, and
   record that `name` stays optional.

   *Resolution*: accepted. The pin now reads equality against the
   required fields of `PromptBlock` via `field.is_required()`, with
   the optional `name` recorded as such. Amended in the drift-pins
   section.

2. **P1: the three-category move rule cannot produce working
   support modules.** Moved roots have module-local dependencies
   the rule excludes: `Gate` needs `TIMEOUT_S`
   (`test_conversations_store.py:52`), `_corrupt` needs `CORRUPT`
   with its no-leak `STORED` sentinel (`test_tools_memory.py:132`),
   `StubRuntime`/`FakeDevice` need `OUTPUT_RATE`, `FRAME_BYTES`,
   `REPLY_PCM` (`test_boundary_contract.py:63-68`); `OpusEncoder`
   crossing the boundary is a production re-export, not a helper.
   Moving only grep-named symbols yields NameErrors, support
   importing test modules, or silently edited helper bodies. Define
   the move set as every shared root plus its minimal module-local
   dependency closure (AST-generated), keep sentinel constants
   verbatim, and redirect production re-exports to their owners.

   *Resolution*: accepted. The move rule gains a fourth category,
   the AST-computed module-local dependency closure of every moved
   root, with the reviewer's three examples named, sentinel
   constants moving byte-identical, and production re-exports
   redirected to their owning module instead of copied. The
   boundary-fake pair is now explicitly inside category 1. Amended
   in "What moves is decided by a rule, not a taste".

3. **P2: the two `BrokenTts` classes are not duplicates.** The
   filler one raises synchronously in `synthesize()` with a
   class-level rate; the record one is an async generator, declares
   `egress = False`, initializes its rate, raises during iteration.
   Decide now: two separately named fakes, behavior unchanged,
   import aliases keeping the local name so test bodies stay
   untouched.

   *Resolution*: accepted. Decided in the layout section: the
   filler variant keeps the name `BrokenTts`, the record variant
   becomes `BrokenStreamingTts` with its exception timing,
   `egress = False`, and initialization unchanged, and
   `test_session_record.py` imports it under its old local name via
   an alias so no test body changes. The risks section's deferral
   now points at this decision instead of postponing it.

4. **P2: green lanes and unchanged assertions do not prove the
   fake premises survived the move.** Three `Falsey` definitions
   are nested inside test functions, so consolidating them is
   already a test-function edit the plan forbids; and if the
   centralized fake accidentally became truthy, the identity
   assertions would still pass while no longer testing the
   falsey-client seam. Add a support-level contract test asserting
   the consolidated client is falsey, enumerate the three permitted
   nested-class deletions, compare relocated definitions by
   normalized AST, and use import aliases for collision renames so
   call sites stay byte-identical.

   *Resolution*: accepted in full. The layout section now
   enumerates the three nested `Falsey` deletions as the only
   permitted test-function edits and adds
   `tests/unit/test_support_fakes.py` in M1 pinning
   `bool(Falsey()) is False`; the verification section requires a
   normalized-AST comparison of every relocated definition,
   recorded per root in the PR body; collision renames now live in
   import aliases so call sites stay byte-identical.

5. **P2: the mutation plan does not exercise every relation the
   pins claim.** Only the commented `websocket_url` deletion is
   named. Enumerate mutations per branch: a live top-level key and
   a commented nested key for the example config; an unclaimed
   file, a missing claimed file, and a doubly claimed file for
   docgen; each CLI predicate relation separately, including
   duplicate `RELOAD_OUTCOMES` and the required-versus-optional
   `PromptBlock` distinction.

   *Resolution*: accepted. The drift-pins section now carries the
   full mutation matrix: live key and nested commented key for the
   example config, all three docgen branches, one mutation per CLI
   relation including the duplicated outcome and the
   required-versus-optional flip, each applied, observed failing,
   reverted, and recorded in the M4 PR body. The risks section's
   single-example mention stands corrected by that matrix.

6. **P2: the proposed `test_config_cli.py` split is not anchored
   to #139's production boundaries.** Six behavioral buckets are
   named but not mapped to #139's production concerns, so the
   scheme cannot demonstrate compliance with the coordination
   rule. Map each bucket to a #139 concern, or defer exact
   filenames to #139 instead of claiming them decided.

   *Resolution*: accepted, both halves. Each bucket is now anchored
   to a concern quoted from #139's issue body (descriptor-driven
   entity handling, response-model rendering, unified dispatch, the
   secrets write path, generated parser wiring), and exact
   filenames are explicitly deferred to #139's final production
   split, with the buckets moving if #139 merges or divides a
   concern. What this plan hands #139 is the bucket boundaries, no
   more.

7. **P3: the SDK and M1 inventories are numerically wrong.** The
   block is seven anthropic plus seven openai classes, 14 not 13;
   M1 touches five provider test modules (the block's owner plus
   the four falsey-client modules), and those five are absent from
   the files-touched list.

   *Resolution*: accepted. The evidence section now records 14
   classes (seven per dialect) at c410af8, noting the issue's 13
   was its 8dd1a5f pin; M1 names its five provider modules
   explicitly, and the files-touched list carries them.

Verdict: ready after the P1/P2 amendments.
