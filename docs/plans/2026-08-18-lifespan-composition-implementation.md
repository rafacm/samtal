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
names neither `ws`, `ota` nor `app`; its class docstring names the one
sanctioned writer outside the composition root,
`tests/unit/test_boundary_contract.py`'s runtime-factory injection
(finding 8), which is why the dataclass is plain rather than frozen.

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
