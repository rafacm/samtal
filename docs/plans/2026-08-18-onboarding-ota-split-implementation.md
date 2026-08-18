# Untangle onboarding.py and ota.py: implementation

Companion to
[`2026-08-18-onboarding-ota-split.md`](2026-08-18-onboarding-ota-split.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## The inventory, retaken at main@8c20596

Issue #143's anchors are pinned to main@8dd1a5f and have moved. The
figures below were retaken at 8c20596, the commit this plan's branch is
based on, and they are what the plan's evidence section cites. Recorded
here because both milestones' designs rest on them, and because a
number that moves under a later milestone is a finding rather than a
typo.

**767 lines in `onboarding.py`, 608 in `ota.py`.** Both figures are the
issue's, unchanged.

**The cycle's edges.** `onboarding.py:72` imports `ota` at module
scope; `build_router` (678-706) registers `ota.check_version`,
`ota.describe` and `ota.activate` by reference and uses `ota.spellings`
and `ota.ACTIVATE_SEGMENT`. `ota.py`'s three function-body imports sit
at 427 (`activation_object`), 514 (`ACTIVATION_ALGORITHMS`) and 564
(`portal_url_line`), each with its apology comment. `ota.py:57` takes
`WEBSOCKET_PATH` from `ws`, and `ws.py:29-34` defers `Composition`
under `TYPE_CHECKING` naming the chain. `config/api.py` defers the
pending table twice (the `TYPE_CHECKING` block at 120-129, and
`_empty_pending` at 681-691) and `config/cli.py` defers `Origin` (103)
and the module itself inside `_onboarding_url` (801).

**The two halves are marked in the file itself**: the docstring
declares both at 40-46, and a section comment at 362-379 marks the
pending-table seam. `activation_object` (656-675) is the one
cross-half function.

**The unbound decision's three sites**: `DeviceAgents.authoritative`
(`device/bindings.py:58-88`), `ota._activation` (376-444) and
`PendingDevices.observe` (468-505).

**Origin assembly**: `ota.websocket_url_for` (108-115) against
`onboarding.public_origin` (230-267).

**Four emit sites on `samtal_server.onboarding` and twelve on
`samtal_server.ota`**, counted as `event=` keywords in each file, which
is what the sixteen sidecar identities in the conformance suite are.

**The re-export surface, by tooling.** An AST walk over
`samtal_server/` and `tests/` for `from samtal_server.onboarding import
...` and `from samtal_server import onboarding` answers with twenty
names plus the module itself. The test suites alone import fifteen of
them (`ACTIVATION_TIMEOUT_MS`, `BUDGET_SPENT`, `CAPACITY_REACHED`,
`CODE_DIGITS`, `CODE_TTL_S`, `FACT_LENGTH`, `KEY_LENGTH`,
`MINT_BUDGET`, `MINT_WINDOW_S`, `PENDING_CAPACITY`, `PendingDevices`,
`RELEASED_GRACE_S`, `derive_key`, `onboarding_key`, `onboarding_path`)
and bind the module itself in three more; the plan's "at least
fourteen" is the floor it said it was.

## M1: the onboarding package

`samtal_server/onboarding.py` became
`samtal_server/onboarding/`: `__init__.py` (223 lines), `keys.py`
(183), `origin.py` (177), `pending.py` (320) and `unbound.py` (149).
Everything the plan lists for each file went to that file, and the code
and its comments moved verbatim except where this section says
otherwise.

The facade builds the emitter under the package's own name and the four
submodules that emit take it with `from . import events`, which is the
#155 package-owned-channel rule and what keeps the channel, and the
`logger` field of every record, exactly what it was. The tunable bounds
(`CODE_DIGITS`, `_CODE_CEILING`, `CODE_TTL_S`, `PENDING_CAPACITY`,
`MINT_BUDGET`, `MINT_WINDOW_S`, `RELEASED_GRACE_S`) are defined in the
facade above the submodule imports; `pending.py` binds the package once
(`import samtal_server.onboarding as onboarding`) and reads every one
of them through it at the moment it decides by one. `build_router`
stays in the facade, with a docstring paragraph naming it as the one
surviving edge to `ota` and M2 as its retirement.

`unbound.py` is new work rather than moved code: `activation_for` with
the typed `Unbound` result (`activation`, a four-outcome `outcome`
literal, `refusal`), holding today's `_activation` sequence with each
branch returning its tagged outcome instead of warning in place.
Nothing calls it this milestone; `ota` still calls `activation_object`
and reads `ACTIVATION_ALGORITHMS` through the facade.

### Deviations from the plan

Three, all small, none of them behavioral.

1. **`events_schema.py` was touched, and the events reference gained
   one line.** The plan foresaw this conditionally ("checked and
   updated the same way if the walk resolves them by location") and the
   walk does resolve it by location:
   `test_every_composed_argument_names_a_grammar_and_its_builder` runs
   `scope_of` over every composed grammar's builders, and
   `ORIGIN_PROVENANCE`'s `samtal_server.onboarding:Origin.provenance`
   fails the moment `Origin` is defined in a submodule. The citation is
   now `samtal_server.onboarding.origin:Origin.provenance`. That string
   is rendered into the generated reference, so
   `docs/reference/events.md` changed by exactly that one table cell.
   No registry entry, channel, template, field or level changed, which
   is what the rest of the empty diff shows. The other citation the
   plan named (`ALSO_BOUND_TO`/`AGENT_LIST`, at
   `samtal_server.ota:check_version`) is M2's, and untouched here.

2. **`activation_for`'s `client_id`, `board` and `firmware` are `str`,
   not `str | None`.** The plan's signature sketch marks the three
   optional. `PendingDevices.observe` takes them as `str` and hands
   each to `_fact`, which calls `.strip()`, so a None would be a
   crash rather than a case; today's `_activation` passes a required
   header and two reporters that fall back to fixed strings, so no
   caller has one to pass. Declaring an optional the body cannot
   survive would be a promise the seam does not keep.

3. **`activation_for` is `async` with nothing awaited in it.** The
   plan declares it `async def` and M2's caller is an async handler, so
   the shape is settled rather than discovered; it is recorded here
   because a reader meets the `async` before they meet the caller.

### Discoveries

- **`pending.py`'s early binding of the package is safe, and tested by
  the module's own text.** `CAPACITY_REACHED` and `BUDGET_SPENT` are
  f-strings evaluated at import, and they read
  `onboarding.PENDING_CAPACITY`, `onboarding.MINT_BUDGET` and
  `onboarding.MINT_WINDOW_S` while the package is still initializing.
  That is exactly the ordering the plan's write-through design asks
  for, and it exercises the partial-import path at every start rather
  than only under a test.
- **The write-through is a live property, not an inherited one.** Both
  sentences those constants render are also the words two warnings
  print, and they are snapshots of the bounds as configured, as they
  were before: a monkeypatched `MINT_BUDGET` changes what is refused
  and not what the refusal says. Unchanged behavior, worth saying,
  because the two live one line apart now.
- **`unbound.py` imports `device/bindings.py` for `DeviceAgents`**, so
  the onboarding package now reaches SQLAlchemy and the configuration
  store through that one module. It reaches neither `ota`, `ws` nor the
  providers package by that route, so M2's import-weight check is not
  compromised; but the plan's "imports nothing device-facing" holds of
  `keys`, `origin` and `pending` only, which is what the CLI and the
  configuration API actually read.
- **`CALL_ALTERNATIVES` needed no change.** The plan expected entries
  there to move; all of its keys name `samtal_server.ota`, so they are
  M2's.

### Verification

- `uv run ruff check .`: clean.
- `uv run pytest tests/unit -q`: 2988 passed, 16 skipped, which is what
  the same command answered on the branch base before any of this.
- `uv run pytest tests/integration -q`: 58 passed.
- The four generated references, regenerated and diffed against the
  committed copies: all four clean. The committed
  `docs/reference/events.md` carries the one grammar-citation cell of
  deviation 1; `domain-config.md`, `conversations-schema.md` and
  `api-openapi.json` are byte-identical to what main holds.
- **No test file changed except the conformance suite.** `git diff
  --stat` against the branch base lists
  `tests/unit/test_event_schema_conformance.py` and nothing else under
  `tests/`. What changed in it is what its own exhaustive failures
  named: four sidecar identities (`log_banner` 1 and 2 to
  `samtal_server.onboarding.origin`, `_log_mismatch` 1 and 2 to
  `samtal_server.onboarding.keys`) and three token decision sites (the
  `Offer:refused` half of `activation_not_offered.reason`, all of
  `activation_not_offered.arg:1`, and
  `onboarding_banner.origin_source`), plus the one comment that named
  the old file.

The three greps the plan asks M1 to record:

**(a) The re-export surface, shown importable.** Every name any module
or suite imports from `samtal_server.onboarding`, in one statement:

```console
$ python -c "
from samtal_server import onboarding
from samtal_server.onboarding import (
    ACTIVATION_ALGORITHMS, ACTIVATION_TIMEOUT_MS, BUDGET_SPENT, CAPACITY_REACHED,
    CODE_DIGITS, CODE_TTL_S, FACT_LENGTH, KEY_LENGTH, MINT_BUDGET, MINT_WINDOW_S,
    PENDING_CAPACITY, RELEASED_GRACE_S, Origin, PendingDevice, PendingDevices,
    activation_object, derive_key, onboarding_key, onboarding_path, portal_url_line,
)
for name in ('log_banner', 'build_router', 'events', 'MINT_BUDGET'):
    assert hasattr(onboarding, name), name
print('20 imported names plus the module itself: OK')
"
20 imported names plus the module itself: OK
```

**(b) The one facade edge.**

```console
$ grep -rn "import ota" samtal_server/onboarding/
samtal_server/onboarding/__init__.py:47:from samtal_server import ota
```

**(c) No test imports `build_router`.**

```console
$ grep -rn "build_router" tests/
$
```

Nothing under `tests/` names it at all, in either module's spelling,
which is the proof the plan's fourth review finding asked M1 for: M2
may retire the symbol when `app.py` switches to the alias router
without touching a suite.

### The PR review round (M1)

External review of PR #197. One finding, P2, accepted.

**`activation_for`'s four-outcome contract had no test coverage.** Only
its definition existed: nothing called it, so a wrong outcome tag or a
dropped refusal string would have passed CI and surfaced in M2 as a
warning naming the wrong reason, or as none at all. The typed result
was written precisely so the wrapper's two emissions could be driven
from it, and an untested tag is a contract in name only.

*Fix*: `tests/unit/test_unbound.py`, seven cases driving
`activation_for` directly with a real `PendingDevices` on the support
clock and hand-built `DeviceAgents` resolutions, in the
direct-construction style of `test_onboarding_pending.py`. One offer
(the whole reply section compared as a dict against the minted entry),
the three not-applicable gates (onboarding off, bound, waiting on a
restart), the unreadable resolution, and both refusals asserted as the
table's own sentences (`CAPACITY_REACHED`, `BUDGET_SPENT`), which is
what the caller renders into `activation_not_offered.reason`. Every
case that is not an offer also asserts the table was left alone, which
is the half of the contract the return value does not state. Checked to
bite rather than assumed to: with the `unreadable` tag flipped to
`not_applicable` and `offer.refused` dropped from the refusal, three of
the seven fail.

### Left for M2

The `config/api.py` and `config/cli.py` deferrals and their apology
comments stay, as review finding 3 settled: importing any onboarding
submodule executes the facade, and the facade still imports `ota` for
`build_router`. `ota.py` is still one module, `websocket_url_for` is
still in it, `WEBSOCKET_PATH` is still in `ws.py`, and the unified
`assemble` helper is not built.

## M2: the ota package and the cycle's end

`samtal_server/ota.py` became `samtal_server/ota/`: `__init__.py` (119
lines), `reply.py` (393), `poll.py` (134) and `router.py` (111). Four
commits carry the change, in the order a reader can follow (three more
correct comments that this milestone made stale or that claimed more
than they deliver):

1. **`WEBSOCKET_PATH` re-homes to `device/boundary.py`**, with `ws.py`
   importing it back so every suite and `tests/support/wire.py` keep
   reading it where they always have.
2. **The split and the cycle's end.** The handlers get a file each, the
   short alias router moves to `ota/router.py` beside them with
   `spellings`, `onboarding.build_router` and the
   `from samtal_server import ota` edge go, `app.py` calls
   `ota.build_alias_router`, the three function-body imports become
   module-scope ones, and `_activation` becomes the wrapper over
   `activation_for`. The conformance suite's ota-keyed maps and two
   grammar citations follow the code.
3. **One assembler, two modes** in `onboarding/origin.py`.
4. **The four lightweight-import deferrals retire**, with the
   subprocess import-weight check as their replacement.

The facade builds the emitter under the package's own name and the two
submodules that emit take it with `from . import events`, which is the
#155 package-owned-channel rule and what keeps the channel, and the
`logger` field of all twelve records, exactly what it was. The
constants went to the module that uses them (`UNKNOWN_VERSION` and
`DEVICE_ID_PROBLEM` to `reply`, `ACTIVATION_VERSION_HEADER` to `poll`,
`OTA_PATH` and `ACTIVATE_SEGMENT` to `router`) and the facade re-exports
them, so `events` is the only name the `__init__` binds, which is what
the plan asks of it. There is no tunable-bounds problem here, unlike
the onboarding facade: nothing under `tests/` assigns an attribute of
`samtal_server.ota`, so no constant needed the write-through the
onboarding bounds have.

`_activation` is the one piece of new writing. It calls
`activation_for` and matches on the outcome literal, emitting the
`unreadable` and `refused` warnings with the sentences, arguments and
fields they always had, and returning `unbound.activation`. The match
is exhaustive over the four outcomes with `offered` and
`not_applicable` sharing an explicit do-nothing arm, so a fifth outcome
added in `unbound.py` cannot go silent here. It takes the composition
rather than the request, since the wrapper no longer needs anything of
the request that the caller has not already read.

### Deviations from the plan

Two, one of them load-bearing.

1. **`device/boundary.py`'s `ToolDef` import moved under
   `TYPE_CHECKING`, with the one annotation quoted.** The plan asks for
   two things that turn out to be in tension: `WEBSOCKET_PATH` re-homes
   to `device/boundary.py`, and `websocket_url_for` (which needs it)
   moves into `onboarding/origin.py`, which `samtal-server config` and
   the configuration API's `document()` import and which therefore may
   not load the provider layer. `boundary.py` imported
   `samtal_server.providers.base` at module scope for one annotation,
   and `providers/__init__` re-exports the registry, so reaching
   `providers.base` (which imports nothing but the standard library)
   executes every configured engine's client on the way. Both plan
   items are kept by making the boundary as cheap as its own docstring
   claims it is: it is a vocabulary of device terms, and a vocabulary
   should not cost a provider layer to read. Recorded as a deviation
   because M2's theme is retiring deferrals and this adds one; the
   difference is that this one is a type-only reference in a module of
   type-only references, where the four retired ones were a whole
   subsystem being kept out of a CLI.

   The alternative considered and declined was putting `WEBSOCKET_PATH`
   in `config/models.py` beside `ONBOARDING_MOUNT_PATH`, which both
   readers already import cheaply. Declined because the plan names
   `device/boundary.py` and the reason it gives holds: the path is a
   term of the device-facing seam, and neither the module that assembles
   the URL nor the module that answers on it owns the other.

2. **`Composition` is imported at module scope in `ota/reply.py` and
   `ota/poll.py`**, and the `TYPE_CHECKING` block that used to hold it,
   `PendingDevice` and `PendingDevices` is gone from the package
   entirely. The plan says this of `ws.py` and is silent about `ota`.
   It is the same fix for the same dead reason: the block's comment
   said a module-scope import "would not load", which stopped being
   true the moment the cycle closed, and a deferral whose stated reason
   is gone is exactly the apology comment this milestone retires.

Not a deviation, but worth saying because a reader will look for it:
`activate` is byte-for-byte the function it was, including its
deliberate blindness to `resolution.authoritative`. The pointer the
plan asks for went into `DeviceAgents.authoritative`'s own comment in
`device/bindings.py`, which is where the rationale lives.

### Discoveries

- **The wire baseline is worth more than the plan hoped, and cheaply.**
  Fixing the dynamics turned out to need exactly three patches, all of
  them location-independent and so immune to the very refactor they are
  checking: `time.time` and `secrets.randbelow` on their own modules
  (not on any importing module's globals), and `SAMTAL_REVISION` in the
  environment. That made it cheap to capture 38 shapes rather than the
  four the plan names, including both slash spellings, all four
  version-2 poll bodies, the short alias path with a right and a wrong
  key, an unserved path for comparison, and five `public_origin`
  resolutions covering both IPv6 arms of the new assembler.
- **The bindings view resolves from the database whenever there is a
  database.** The baseline's first run answered "no agent" for the
  bound MAC, because `entered_client` migrates a configuration database
  into the directory the config names and `DeviceBindings.open` then
  reads it rather than the boot snapshot, and nothing had written a
  `devices` row into it. The suites do not meet this because their
  autouse fixture points the default at a directory that does not
  exist; the baseline script does the same now, per app. Nothing about
  the split, but it is the difference between capturing a bound-device
  shape and capturing a second unbound one.
- **`ota` never needed `WEBSOCKET_PATH` after all.** The plan re-homes
  it so that `ws.py` can import `Composition` at module scope, and says
  `ota/reply.py` takes the path from the boundary. Once
  `websocket_url_for` moved to `origin.py`, the OTA package stopped
  naming the constant at all: the only reader outside `ws.py` is the
  origin module. The move is still right, and its justification is now
  the origin module rather than the OTA one.
- **`CALL_ALTERNATIVES` did move, contrary to M1's note.** M1 recorded
  that it needed no change because all of its keys name
  `samtal_server.ota`; that made them M2's, and all four moved to
  `samtal_server.ota.reply`, along with the two-key assertion in
  `test_the_narrowings_are_the_two_the_source_hides`.

### Verification

- `uv run ruff check .`: clean.
- `uv run pytest tests/unit -q`: 2990 passed, 16 skipped. Two more than
  M1's 2988, which are the two import-weight tests added below; no
  other count moved.
- `uv run pytest tests/integration -q`: 58 passed, the same as M1.
- The four generated references, regenerated and diffed against the
  committed copies: `domain-config.md`, `conversations-schema.md` and
  `api-openapi.json` clean. `docs/reference/events.md` changed by
  exactly two table cells, both in the grammar table's "Built by"
  column: `also_bound_to` and `agent_list` now cite
  `samtal_server.ota.reply:check_version` instead of
  `samtal_server.ota:check_version`. That is the citation the plan
  predicted for M2, resolved by location by the same walk that forced
  M1's `ORIGIN_PROVENANCE`. No registry entry, channel, template, field
  or level changed.
- **No test file changed except the conformance suite, and one new
  file.** `git diff --stat` against the branch base (`8c20596`) lists
  `tests/unit/test_event_schema_conformance.py` and
  `tests/unit/test_onboarding_import_weight.py` under `tests/` and
  nothing else. Every onboarding and OTA suite passes byte-unmodified.
- `test_server_event_pins.py`'s twelve literal `"samtal_server.ota"`
  logger assertions are untouched and pass, which is the check that the
  channel did not move with the code.

What moved in the conformance suite, named by its own exhaustive
failures:

| Map | Key | Now |
| --- | --- | --- |
| `SPREAD_INVENTORY` | `ota:check_version.fields` | `ota.reply:` |
| `SPREAD_INVENTORY` | `ota:_version_two.refusal` | `ota.poll:` |
| `CALL_ALTERNATIVES` | `(ota, check_version, 1..4)` | `ota.reply` |
| the narrowing assertion | `(ota, check_version)` | `ota.reply` |
| `TOKEN_SOURCES` | `activation_not_offered.reason`, ota half | `ota.reply`, scope `_activation` |
| `TOKEN_SOURCES` | `activation_refused.reason` | `ota.poll`, scope `_version_two` |
| `TOKEN_SOURCES` | `ota_request_rejected.arg:0` | `ota.reply` |
| the sidecar | `check_version` 1-4, `_activation` 1-2, `_bad_request` 1 | `ota.reply` |
| the sidecar | `activate` 1-2, `_version_two` 1-3 | `ota.poll` |

Twelve sidecar identities, which is the twelve emit sites. The
`activation_not_offered.reason` entry keeps both of its halves: the
`Offer:refused` half stayed in `onboarding.pending` where M1 put it, and
the ota half followed `_activation` into the reply wrapper.

#### The greps

**(a) No import of `ota` anywhere under `samtal_server/onboarding/`.**

```console
$ grep -rn "import ota\|samtal_server\.ota" samtal_server/onboarding/
$
```

**(b) No function-body import in either package, in either direction.**

```console
$ grep -rn "^    from \|^        from \|^    import \|^        import " \
    samtal_server/onboarding/ samtal_server/ota/
$
```

Both are empty. The three that died are `activation_object`,
`ACTIVATION_ALGORITHMS` and `portal_url_line`; the fourth, in
`config/cli.py`'s `_onboarding_url`, died with the deferrals.

**(c) The re-export surface, shown importable.** Every name any module
or suite imports from `samtal_server.ota`, in one statement:

```console
$ python -c "
from samtal_server import ota
from samtal_server.ota import (
    ACTIVATE_SEGMENT, ACTIVATION_VERSION_HEADER, DEVICE_ID_PROBLEM, OTA_PATH,
    UNKNOWN_VERSION, activate, build_alias_router, build_router, check_version,
    describe, events, reported_board, reported_version, spellings,
    timezone_offset_minutes, token_for, websocket_url_for,
)
for name in ('_activation','_bad_request','_json_object','_read_json_object','_version_two'):
    assert hasattr(ota, name), name
print('22 names on samtal_server.ota plus the module itself: OK')
"
22 names on samtal_server.ota plus the module itself: OK
```

What the suites actually import is three of them (`OTA_PATH`,
`ACTIVATE_SEGMENT`, `DEVICE_ID_PROBLEM`, across ten modules including
`tests/support/checkin.py` and `tests/support/registry.py`); the rest
are re-exported because they were importable before the split and
nothing about them changed.

#### The import-weight check

`tests/unit/test_onboarding_import_weight.py`, two tests, both green: a
subprocess that imports `samtal_server.onboarding`, and one that imports
`samtal_server.config.cli`, each asserting that `samtal_server.ota`,
`samtal_server.ws` and `samtal_server.providers` are absent from
`sys.modules` afterwards. The CLI is included because it is the reader
the deferrals existed for, and holding it as well as the package means
an edge added inside the package fails the test that names the caller.

The test's docstring says out loud what is NOT asserted: SQLAlchemy is
loaded, through `onboarding.unbound` importing `device.bindings` for the
`DeviceAgents` type it answers about (M1's discovery). That is a real
dependency of the decision this package makes, and the plan's "imports
nothing device-facing" always held of `keys`, `origin` and `pending`
alone, which are the three modules the CLI and the configuration API
read.

#### The wire baseline

`scratchpad/wire-baseline-143.py` (kept in the session scratchpad, not
committed) drives an entered app over 38 shapes with the dynamics fixed:
`time.time` and `secrets.randbelow` patched on their own modules, a
pinned `SAMTAL_REVISION`, the suites' own auth and API secrets, a pinned
`timezone_offset_minutes`, and the fixed `SYSTEM_INFO`/`HEADERS` from
`tests/support/checkin.py`. For each it records the status, the raw
`response.content`, and the content-type, content-length and location
headers; the five `public_origin` cases record the URL and its
provenance instead.

Run once on the pre-split tip (`70a061b`, M1's tip, which changed none
of `ota.py`) and once on the post-split tip. **Verdict: byte-identical.**
The two files have the same SHA-256,
`d9f3090a081a21063481715f98cc957beafe7a21e738e5bc4662503174b75915`, and
`diff` reports no difference at all.

The 38 shapes: describe on the OTA path and on its slashless spelling;
the check for an unbound device (the offer), the same device again (the
re-display), and on the slashless spelling; the check for a bound device
(the token); four refusals (no Device-Id, no Client-Id, a Device-Id that
is not a MAC, a body that is not a JSON object); the poll at 202 on both
spellings, at 200 for a bound device, and its two refusals; four
version-2 polls (unreadable body, unknown algorithm, wrong challenge,
and a well-formed one); the short alias path answering describe on both
spellings, the check and the poll; the wrong-key 404 on GET and POST and
an unserved path beside it for comparison; the check and describe with
onboarding off; the check with device auth off; the check and describe
with `server.websocket_url` configured; the check and describe with
`server.public_url` configured; and five `public_origin` resolutions
(the listen address, a `websocket_url` with a port, a `public_url`, an
IPv6 listen address, an IPv6 `websocket_url`).

The five the plan names, verbatim from both runs:

```text
### describe on the OTA path | 200 | text/plain; charset=utf-8 | 404 bytes
samtal-server 0.1.0 (revision wire-baseline-143) OTA endpoint.
Devices are sent to ws://testserver/xiaozhi/v1/ (protocol version 1).
Type this into the device's captive portal: http://0.0.0.0:8003/xiaozhi/ota/ (guessed from the listen address (server.host and server.port), 0.0.0.0 is where the server listens rather than a name a device can reach; set server.public_url to name this deployment exactly)

### check for an unbound device (the offer) | 200 | application/json | 382 bytes
{"activation":{"message":"http://0.0.0.0:8003\n424242","code":"424242","challenge":"aa:bb:cc:dd:ee:ff","timeout_ms":30000},"server_time":{"timestamp":1700000000000,"timezone_offset":60},"firmware":{"version":"2.4.0","url":""},"server":{"name":"samtal-server","version":"0.1.0","revision":"wire-baseline-143"},"websocket":{"url":"ws://testserver/xiaozhi/v1/","token":"","version":1}}

### check for a bound device (the token) | 200 | application/json | 314 bytes
{"server_time":{"timestamp":1700000000000,"timezone_offset":60},"firmware":{"version":"2.4.0","url":""},"server":{"name":"samtal-server","version":"0.1.0","revision":"wire-baseline-143"},"websocket":{"url":"ws://testserver/xiaozhi/v1/","token":"SRkaZ7U9xq9I70OseUrhg0fSpk15aVQUwRxnFiDJpMo.1700000000","version":1}}

### poll for a device still waiting (202) | 202 | (no content-type) | 0 bytes

### poll for a bound device (200) | 200 | (no content-type) | 0 bytes
```

The token is a real HMAC over a pinned secret and a pinned clock, which
is why it is reproducible and why its equality is evidence: the reply
that carries it is assembled by a different module than it was.

### The acceptance sweep

Issue #143's five criteria, each against what proves it.

1. **Split by responsibility so the cycle disappears structurally.**
   Two packages of four and five modules; grep (a) shows no import of
   `ota` under `onboarding/` and grep (b) no function-body import in
   either package in either direction. The cycle cannot come back by
   accident: the alias router is where the handlers are, and the guard
   is a dependency of it.
2. **The unbound-device decision gets one home.**
   `onboarding/unbound.py:activation_for`, called by exactly one caller,
   the `ota/reply.py` wrapper whose whole body is the two warnings.
   `PendingDevices.observe` stays the table's mint mechanics and
   `DeviceAgents.authoritative` stays an input, both by argument rather
   than by import.
3. **Origin assembly unifies into one helper.**
   `onboarding/origin.py:assemble` is the only place in the package that
   writes `scheme://authority`; `websocket_url_for` and `public_origin`
   are wrappers that pass a raw netloc or a rebuilt pair and assemble
   nothing themselves. The wire baseline's `websocket.url` values and
   its five `public_origin` resolutions, both IPv6 arms included, are
   identical across the split.
4. **The wire behavior of every route is byte-identical.** The wire
   baseline, 38 shapes, same SHA-256 before and after; both slash
   spellings, the activation ceremony, the 404 with its log-only hint,
   and the banner among them. The unmodified body-equality and
   no-redirect suites are the standing net afterwards.
5. **Feeds but does not implement #96 and the #40 last-seen.** The two
   seams stayed two: `device_facts.record` still runs for every valid
   check-in in `check_version`, with a comment naming #96 and pointing
   at the other seam, and the unbound-only `last_seen` refresh stays
   inside `activation_for`/`observe`. No recorder was added and no
   machinery was built.
