# Untangle onboarding.py and ota.py

## Goal

Implement issue #143: `onboarding.py` (767 lines) and `ota.py`
(608 lines) are circularly coupled (onboarding imports ota at
module scope to register its handlers by reference; ota imports
onboarding lazily through three function-body imports, each with
an apology comment), each holds two unrelated halves, and the
decision about what an unbound device receives is split across
three files that must agree. Split both along their real seams as
packages that keep their module identity, give the unbound-device
decision one home, unify origin assembly, and leave the wire
byte-identical, so the hardware-verified #40 ceremony needs no
re-verification.

The companion implementation doc,
[`2026-08-18-onboarding-ota-split-implementation.md`](2026-08-18-onboarding-ota-split-implementation.md),
records what each milestone actually did, deviations, and
discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by issue #143 and not re-litigated here:

1. **Split by responsibility so the cycle disappears
   structurally**: the pending-device table, the
   key-derivation/routing surface, and the OTA reply/activation
   handlers become separate modules (exact names below); routers
   compose them without back-references.
2. **The unbound-device decision gets one home** that the three
   current sites call, so one file answers what a device with no
   agent gets.
3. **Origin assembly unifies into one helper** serving the
   request-derived and config-derived cases.
4. **The wire behavior of every route is byte-identical**: both
   slash spellings, the activation ceremony, the 404 with its
   log-only hint, and the banner. Load-bearing for the
   hardware-verified #40 ceremony.
5. **Feeds but does not implement #96** (observed device facts)
   and the #40 last-seen signal; clean seams are left where those
   attach.

The no-behavior-change contract: all onboarding and OTA unit and
integration tests pass unmodified (they already test through
HTTP or the table's own interface), and the hardware-facing
behaviors recorded in docs/xiaozhi-notes.md are untouched.

## Evidence, re-verified at plan time

The full inventory was retaken at main@8c20596 (recorded in the
implementation doc's preamble at M1); the issue's anchors are
from main@8dd1a5f and have moved. The load-bearing facts, and
where they correct or extend the issue:

- **The cycle's edges today**: onboarding.py:72 imports ota at
  module scope; `build_router` (onboarding.py:678-706) registers
  `ota.check_version`/`describe`/`activate` by reference and uses
  `ota.spellings`/`ACTIVATE_SEGMENT`; ota's three function-body
  imports are now at ota.py:427 (`activation_object`), 514
  (`ACTIVATION_ALGORITHMS`), 564 (`portal_url_line`).
- **The cycle is wider than the issue states.** `ota.py:57`
  imports `WEBSOCKET_PATH` from `ws`, and `ws.py:29-35` defers
  `Composition` under `TYPE_CHECKING` naming the exact chain
  (composition, onboarding, ota, ws, composition). `config/api.py`
  defers `PendingDevices` twice (the TYPE_CHECKING block at
  120-129, `_empty_pending` at 680-689) and string-annotates
  `ApiRuntime.pending`, and `config/cli.py` defers `Origin` (103)
  and imports onboarding inside `_onboarding_url` (801), all with
  comments blaming the same coupling: importing the table or the
  origin helpers pulls in a whole conversation's machinery. A
  split that gives the table and the origin helpers
  device-machinery-free homes retires every one of these.
- **The two halves are real and marked.** onboarding.py's own
  docstring declares both halves (40-46), and a section comment
  at 362-379 marks the pending-table seam. The one cross-half
  function is `activation_object` (656-675): it needs
  `public_origin` and `PendingDevice`, and is called only from
  `ota._activation`.
- **The unbound-device decision's three sites, re-anchored**:
  `DeviceAgents.authoritative` (device/bindings.py:58-88, the
  provenance flag with its rationale at 70-84);
  `ota._activation` (ota.py:376-444: onboarding enabled, agents
  or unloaded, authoritative, then `observe`, then
  `activation_object`); `PendingDevices.observe`
  (onboarding.py:468-505: expire, re-display, capacity, budget,
  mint). `activate` re-reads the bindings independently and
  deliberately ignores `authoritative` (bindings.py:74-81 says
  the distinction matters to exactly one caller); that asymmetry
  is part of the behavior to preserve.
- **Origin assembly**: `ota.websocket_url_for` (108-115,
  request-derived, raw netloc, trusts no forwarded headers,
  cannot fail) versus `onboarding.public_origin` (230-267,
  config-derived, rebuilt host and port so credentials cannot
  ride into a log, IPv6 re-bracketing, provenance tracked).
  Callers: the reply body and `describe` for the former; the
  banner, `portal_url_line`, `activation_object`, and
  `config/cli.py`'s `ota-url`/`doctor` for the latter.
- **Events pin the module names.** Both files construct
  `ServerEvents(__name__)`: channels `samtal_server.onboarding`
  (4 emit sites) and `samtal_server.ota` (12 emit sites), both in
  `SERVER_CHANNELS`, with `test_server_event_pins.py` asserting
  the literal `"samtal_server.ota"` logger twelve times. Channels
  are logger names, so they move with code unless the #140
  package pattern holds them: the package's `__init__` constructs
  the events instance under the package name and submodules
  import it (the #155 conformance rule for package-owned
  channels). The conformance suite's location-keyed maps (two
  spread builders, the `CALL_ALTERNATIVES` entries, five
  `TOKEN_SOURCES` entries including the cross-module
  `activation_not_offered` reason split between `_activation` and
  `PendingDevices.observe`, and the sixteen sidecar identities)
  update mechanically to the defining submodules, named by the
  suite's own exhaustive failures, exactly as in #140 and #141.
- **"Tests pass unmodified" pins a re-export surface.** Test
  modules import at least fourteen names from
  `samtal_server.onboarding` (constants, `onboarding_key`,
  `onboarding_path`, `PendingDevices`, `BUDGET_SPENT`, ...) and
  seven modules import `OTA_PATH` and siblings from
  `samtal_server.ota`; `tests/support/config_cli.py` imports
  `PendingDevices` at module scope. Both names must remain
  import-compatible facades.
- **Misplaced-for-cycle-reasons items the split re-homes**:
  `spellings` lives in ota.py explicitly "because both need it
  and this is the module the other imports" (208-211);
  `ACTIVATION_TIMEOUT_MS` and `ACTIVATION_ALGORITHMS` are
  OTA-reply constants sitting in onboarding.py; `WEBSOCKET_PATH`
  is a wire constant living in the websocket edge module.
- **The wire surface and its pins**: six OTA-path routes (both
  spellings of check, describe, activate), six short-alias routes
  (keyed or keyless), the guard as a closure over the same
  handler objects (registration by reference is why the split
  must preserve handler identity, not re-implement); the
  body-equality tests in `test_onboarding.py` /
  `test_onboarding_activation.py` / `test_ota.py` pin
  byte-identity, and docs/xiaozhi-notes.md's ceremony section
  (190-254) records the hardware-verified behaviors, including
  the redirect intolerance that `spellings` exists for.
- **#96/#40 seams**: `DeviceFacts.record` at ota.py:248 and the
  facts handed to `observe` at 288/430 are the same observation
  made twice; `PendingDevice.first_seen`/`last_seen` exist only
  for unbound devices, in RAM. The natural attachment point for
  #96's record and #40's last-seen is a single observed-facts
  call site in the new unbound-decision home, beside the
  composition's existing `device_facts` field.
- `#142` already typed both modules' state reads
  (`comp: Composition` bindings), so the issue's untyped-state
  evidence is resolved; nothing in this plan touches the
  composition's shape.

## Design

### Package layout

Both modules become packages that keep their import identity, the
#140 pattern:

```
samtal_server/onboarding/
    __init__.py      # docstring (both halves' story becomes the
                     # package's map), events =
                     # ServerEvents("samtal_server.onboarding"),
                     # re-exports of the whole current public
                     # surface
    keys.py          # KEY_LABEL..._TYPO_ATTEMPT_RE, derive_key,
                     # onboarding_key, onboarding_path, Handler,
                     # _guarded, _log_mismatch
    origin.py        # Origin, public_origin, _origin_of,
                     # _bracketed, portal_url_line, log_banner,
                     # and the unified request-derived helper
                     # (below)
    pending.py       # FACT_LENGTH, _fact, PendingDevice, Offer,
                     # Claim, CAPACITY_REACHED, BUDGET_SPENT,
                     # PendingDevices, _drawn; reads the tunable
                     # bounds late through the package (below)
    unbound.py       # the one home of the unbound-device
                     # decision (below), plus activation_object
                     # and ACTIVATION_TIMEOUT_MS /
                     # ACTIVATION_ALGORITHMS beside the reply
                     # section they shape

samtal_server/ota/
    __init__.py      # docstring, events =
                     # ServerEvents("samtal_server.ota"),
                     # re-exports (OTA_PATH, ACTIVATE_SEGMENT,
                     # DEVICE_ID_PROBLEM, UNKNOWN_VERSION,
                     # ACTIVATION_VERSION_HEADER, spellings,
                     # websocket_url_for, token_for,
                     # check_version, describe, activate,
                     # build_router, ...)
    reply.py         # check_version, describe, token_for,
                     # reported_version/board,
                     # timezone_offset_minutes, _bad_request,
                     # _read_json_object, _json_object
    poll.py          # activate, _version_two
    router.py        # spellings, build_router (the OTA-path
                     # router), build_alias_router (the short
                     # /x/ router, moved here from
                     # onboarding.build_router)
```

Dependency direction, all module-scope, acyclic:
`onboarding.keys`/`origin`/`pending` import nothing device-facing
(the CLI constraint: `config/cli.py` and `config/api.py` must be
able to import them without loading providers, MCP, or audio;
the split is what guarantees it). `onboarding.unbound` imports
`origin` and `pending` plus the bindings resolution type.
`ota.reply` and `ota.poll` import `samtal_server.onboarding`
submodules at module scope (the three apology imports become
ordinary imports); `ota.router` imports the handlers and
`onboarding.keys._guarded`. Nothing in `onboarding/` imports
`ota` any more: the short-alias router moves to `ota/router.py`,
because it is a router over ota's handlers and the guard is just
a dependency, which is what "routers compose them without
back-references" means once the guard and the handlers have
separate homes. `app.py` registers both routers from
`ota.router`; `main.py` keeps `onboarding.log_banner`.

**The tunable-bounds write-through.** Tests monkeypatch bounds on
the module they always lived on (`samtal_server.onboarding
.MINT_BUDGET` in four tests, and siblings like
`PENDING_CAPACITY` and `CODE_TTL_S` elsewhere), and a re-exported
integer is a snapshot: rebinding the package attribute would
leave the submodule's global, and the decision it feeds,
unchanged. So the tunable bounds stay DEFINED at the top of
`onboarding/__init__.py`, before the submodule imports, and
`pending.py` binds the package module once at import
(`import samtal_server.onboarding as onboarding`, safe because
the constants precede the submodule imports during
initialization) and reads each bound at decision time
(`onboarding.MINT_BUDGET` in `_affordable`, and likewise for
capacity, TTL, mint window, and released grace), with a comment
naming the monkeypatch contract as the reason. The four existing
tests are the verification and stay unmodified.

Two knock-on re-homes that make the direction clean:

- `WEBSOCKET_PATH` moves to `device/boundary.py` (a wire
  constant on the device-facing seam); `ws.py` re-exports it and
  can then import `Composition` at module scope, retiring the
  four-module TYPE_CHECKING chain its comment describes.
  `ota/reply.py` takes it from the boundary.
- `config/api.py`'s two pending deferrals and string annotation
  become direct imports of `onboarding.pending`;
  `config/cli.py`'s `Origin` deferral and `_onboarding_url`'s
  function-body import become direct imports of
  `onboarding.origin`/`keys`. The apology comments go with them.

### The events story

Channels do not move: each package `__init__` constructs the
`ServerEvents` under the package's name (its own `__name__`), the
sole binding, and submodules import `events` from the package,
per the #155 package-owned-channel conformance rule. The pins
suite's twelve literal `samtal_server.ota` logger assertions and
every event's schema entry stay untouched; the generated events
reference must diff empty. What does move: the conformance
suite's location-keyed maps (sidecar identities, the two spread
builders, `CALL_ALTERNATIVES`, five `TOKEN_SOURCES` entries)
update to the defining submodules, driven by the suite's own
exhaustive failures. The grammar table's two citation strings
(`ORIGIN_PROVENANCE`, and `ALSO_BOUND_TO`/`AGENT_LIST` citing
`samtal_server.ota:check_version`) are checked and updated the
same way if the walk resolves them by location.

### The unbound-device decision's one home

`onboarding/unbound.py` owns the question end to end:

```python
async def activation_for(
    pending: PendingDevices,
    server: ServerConfig,
    resolution: DeviceAgents,
    mac: str, client_id: str | None,
    board: str | None, firmware: str | None,
) -> dict | None
```

The signature above is amended by the review round: the return
type is not `dict | None` but a small typed result declared in
`unbound.py` that distinguishes every outcome the caller must
tell apart:

```python
@dataclass(frozen=True)
class Unbound:
    activation: dict | None      # the offer, when one is made
    outcome: Literal[
        "offered",         # a code (new or re-displayed)
        "not_applicable",  # onboarding off, or device bound/unloaded
        "unreadable",      # resolution not authoritative
        "refused",         # the table said no
    ]
    refusal: str | None          # CAPACITY_REACHED / BUDGET_SPENT
```

The body is today's `_activation` sequence moved whole: the
onboarding-enabled gate, the agents-or-unloaded emptiness test,
the `authoritative` provenance check, the `observe` call, and
`activation_object` (which moves into the same module, being the
one cross-half function), each branch returning its tagged
outcome instead of warning in place. **The two
`activation_not_offered` warnings stay in the ota package**: a
thin `_activation` wrapper in `ota/reply.py` calls
`activation_for`, emits the `unreadable` and `refused` warnings
through ota's own `events` (fields and messages byte-identical,
fed from the result's tags), and hands `check_version` the
activation dict or None exactly as today. This is settled now,
not deferred: the conformance walk permits only a module's own
emitter or `from . import events` within its own package (it has
a planted test rejecting an absolute import of another package's
emitter), and a cross-package emitter import would also recreate
the onboarding-to-ota edge this issue exists to remove.
`bindings.DeviceAgents.authoritative` stays the input it is,
`observe` stays the table's mint mechanics, and exactly one
function answers what an unbound device gets; `activate`'s
deliberate authoritative-blind re-read is untouched and its
rationale comment gains a pointer to the new home.

The #96/#40 seam: `activation_for`'s call site in
`check_version` is immediately after today's
`device_facts.record` line; the plan leaves them adjacent with a
short comment naming #96 (one observation, two writers today,
one recorder later). No new machinery.

### Origin assembly, unified

`origin.py` gains ONE assembly helper that both existing names
become compatibility wrappers over, neither assembling an
address on its own:

```python
def assemble(scheme: str, netloc: NetLoc, path: str = "") -> str
```

where `NetLoc` is either the raw request netloc taken verbatim
(the wire mode: the reply's `websocket.url` must keep its exact
bytes, forwarded headers stay untrusted) or the parsed-and-rebuilt
hostname/port pair with IPv6 re-bracketing (the retained mode:
credentials can never ride into a log, provenance carried on the
`Origin` the wrapper builds). `websocket_url_for(config, request)`
resolves the configured override or calls `assemble` in wire mode
with `WEBSOCKET_PATH`; `public_origin(server)` resolves its
three-step priority and calls `assemble` in retained mode,
keeping `Origin.provenance` exactly as today. Both wrappers'
outputs are byte-identical to today's, which the wire baseline
(below) and the banner/CLI tests prove. `ota.__init__` re-exports
`websocket_url_for` for compatibility. One assembler, two
documented modes, one module answering "what address does the
outside world use".

### Considered and declined

- Moving `PendingDevices` to `device/` (it is about devices):
  declined; the config API is its second reader and the CLI
  constraint wants it beside keys and origin in the
  device-machinery-free half.
- A FastAPI dependency for the key guard instead of the closure:
  declined; registration by reference over the same handler
  objects is what the byte-identity contract leans on, and the
  guard's 404 must stay indistinguishable from an unserved path.
- Implementing the #96 record or last-seen while the seam is
  open: declined, the issue says feeds, not implements.

## The standing review lenses, answered

- **No-leak.** No message text changes and no new retained
  surface. The key guard's discipline is preserved verbatim: the
  404 carries no hint, the mismatch warnings quote neither key,
  and `public_origin`'s rebuilt-netloc rule (credentials cannot
  ride into a log) moves unmodified. The sentinel and
  sanitization suites pass untouched.
- **Pin before reshaping.** The review round showed the existing
  tests are weaker than they look for THIS purpose: `_stable`
  compares two routes through the same (possibly changed)
  handler, the poll tests assert status codes, and describe
  asserts substrings; only the wrong-key 404 comparison is
  byte-exact. So M2 adds a wire baseline, run as a verification
  step rather than a committed test: a script (kept in the
  scratchpad, its outputs recorded in the implementation doc)
  drives the check, activation offer, poll, and describe routes
  against an entered app with the dynamics fixed (the support
  clock, a seeded code, a pinned token secret, a pinned
  firmware/revision), captured once on the pre-split commit and
  once after, comparing status, raw `response.content`, and the
  relevant headers. Existing tests stay unmodified; the
  body-equality and no-redirect tests remain the regression
  net afterwards.
- **Closed sets.** No reason token changes and no decision site
  changes meaning; the `TOKEN_SOURCES` relocations re-point the
  same closed sets at the same decisions in their new homes, and
  the suite's two-way exhaustive assertions are the check.
- **Honest seams.** `activation_for` takes its collaborators as
  arguments (no import of the composition, no reach into state);
  optional comparisons move verbatim (`is None` throughout).
- **Inventories by tooling.** The re-export surfaces are
  enumerated by grepping test imports of the two module names
  (the M1/M2 verification re-runs the grep and records it); the
  conformance and pins suites are the emit-site inventory; the
  no-cycle criterion is proven by `grep -rn "from samtal_server
  import ota\|import ota$" samtal_server/onboarding/` and the
  function-body-import grep over both packages, recorded in the
  implementation doc.

## Risks and mitigations

- **Package `__init__` import order.** The facades import
  submodules; a submodule importing the package facade back
  (rather than the specific sibling or the `events` name) would
  cycle at init. Rule, stated in each `__init__`: submodules
  import siblings directly and take `events` from the package;
  only `__init__` aggregates. Same rule tools/mcp already lives
  by.
- **Handler identity.** The alias router must wrap the very same
  handler functions; the body-equality and no-redirect tests
  catch a re-implementation, and the router move is
  reference-preserving by construction.
- **The typed `Unbound` result must not drift from the
  emissions it feeds.** The wrapper's two warnings are driven by
  the result's tags, so a new outcome without a matching arm
  would go silent; the conformance suite's closed-set assertions
  on `activation_not_offered.reason` and the pins are the check,
  and the wrapper's match is exhaustive over the literal.
- **Facade completeness.** A missed re-export fails the
  unmodified test suites immediately (imports are the first thing
  a suite does), so the failure mode is loud and cheap.

## Milestones

- [ ] **M1: the onboarding package.** Convert `onboarding.py` to
  the four-module package plus facade `__init__` (keys, origin,
  pending, unbound with `activation_for` not yet called by ota;
  the tunable bounds defined in the facade ahead of the submodule
  imports), move `ACTIVATION_TIMEOUT_MS`/`ACTIVATION_ALGORITHMS`
  into `unbound.py`, keep `build_router` defined in the facade as
  the ONE surviving onboarding-to-ota edge (a construction-only
  factory `app.py` still calls, named as temporary in its
  comment), update the conformance location keys the suite names,
  regenerate the events reference expecting an empty diff, verify
  every onboarding suite passes unmodified, CHANGELOG entry,
  implementation-doc section with the re-export grep recorded.
  The `config/api.py` and `config/cli.py` deferrals stay in M1:
  importing any onboarding submodule executes the facade, which
  still aggregates the router edge, so retiring them now would
  make `config openapi` and the CLI pull the conversation stack.
- [ ] **M2: the ota package and the cycle's end.** Convert
  `ota.py` to the three-module package plus facade, move the
  short-alias router to `ota/router.py` and `spellings` with it,
  point the reply wrapper at `activation_for` (the three
  function-body imports die with `_activation`), retire
  `onboarding.build_router` when `app.py` switches to
  `ota.router.build_alias_router` (the facade's public-surface
  promise deliberately excludes this construction-only factory;
  no test imports it, verified by grep in M1), unify
  `websocket_url_for` into `onboarding/origin.py`, re-home
  `WEBSOCKET_PATH` to `device/boundary.py` and let `ws.py` import
  `Composition` at module scope, retire the `config/api.py` and
  `config/cli.py` deferrals and their apology comments now that
  the onboarding package has no path to ota, with an
  import-weight check (a subprocess imports
  `samtal_server.onboarding` and asserts `samtal_server.ota`,
  `samtal_server.ws`, and the providers package are absent from
  `sys.modules`), update `app.py`'s router registration and the
  conformance location keys, regenerate the events reference
  expecting an empty diff, run the no-cycle and
  no-function-body-import greps, capture the pre/post wire
  baseline (below), verify every onboarding and OTA suite (unit
  and integration) passes unmodified, CHANGELOG entry,
  implementation-doc section with the acceptance sweep (all five
  criteria mapped).

Each milestone is a stacked branch off the previous one
(`feature/onboarding-ota-split-m1` off this plan's branch, `-m2`
off `-m1`), merged to `main` by rebase via its own PR after its
own external review round; both are behavior-preserving with the
suites passing unmodified, so every merge leaves `main`
releasable.

## Plan review round

External review: codex-cli 0.147.0, model gpt-5.6-sol, 2026-08-18,
against commit 67d52a6. Seven findings; verdict "ready after the
P1/P2 amendments". All seven adopted.

1. **P1: the chosen cross-package emitter is explicitly
   forbidden.** The conformance walk permits only a module's own
   emitter or `from . import events` within its own package, with
   a planted test rejecting an absolute import of another
   package's emitter; the plan's primary choice would also
   recreate the onboarding-to-ota edge, and its fallback is
   unimplementable against a `dict | None` return that cannot
   distinguish refusal shapes. The ota-owned wrapper must be
   chosen now, with `activation_for` returning a result that
   distinguishes offered, not-applicable, unreadable, and
   pending-table refusal, and `ota.reply` owning both emissions.

   *Resolution*: the unbound-decision section now declares the
   typed `Unbound` result with its four-outcome literal, settles
   the ota-owned wrapper as the only shape, and the risk bullet
   about the cross-package emitter is replaced by a
   result-to-emission drift check.

2. **P1: the facade breaks the `MINT_BUDGET` monkeypatch
   contract.** Four unmodified tests assign
   `samtal_server.onboarding.MINT_BUDGET`; a re-exported integer
   snapshot leaves the submodule global unchanged and the tests
   exercising the wrong bound. Write-through compatibility must
   be preserved, with the existing four tests as the
   verification.

   *Resolution*: the design gains a tunable-bounds write-through
   section: bounds defined in the facade before submodule
   imports, `pending.py` reading them at decision time through
   the package module, the four tests unchanged as proof.

3. **P2: M1 retires the lightweight-import deferrals too
   early.** Importing any onboarding submodule executes the
   facade, which still aggregates the temporary router edge to
   ota in M1, so `config openapi` and the CLI would pull the
   conversation stack. The config API and CLI deferrals stay
   through M1 and retire in M2, with an import-weight check.

   *Resolution*: M1 now keeps both deferrals and says why; M2
   retires them with the subprocess import-weight check.

4. **P2: `onboarding.build_router` has no transitional
   contract.** M1 must name its temporary home, and M2 must
   retire the construction-only symbol when `app.py` switches to
   the alias router; the whole-current-public-surface promise
   must exclude this deliberately moved factory.

   *Resolution*: M1 names the facade as `build_router`'s
   temporary home and the one surviving edge; M2 retires the
   symbol when `app.py` switches, with a grep in M1 proving no
   test imports it.

5. **P2: the origin design does not implement the settled
   single-helper decision.** Sharing only a scheme fragment
   leaves two assemblers. One URL-assembly helper with two modes
   (raw request netloc for the wire; parsed and rebuilt
   hostname/port with provenance for retained output) is
   required, with both existing names as compatibility wrappers
   that do not assemble independently.

   *Resolution*: the origin section now declares the one
   `assemble` helper with its wire and retained modes, and both
   names become wrappers that never assemble independently.

6. **P2: the cited tests cannot prove pre-split byte
   identity.** `_stable` compares two routes through the same
   new handler and drops `server_time`; polls assert status,
   describe asserts substrings; only the 404 comparison is
   byte-exact. A pre-move baseline (dynamics fixed; status, raw
   content, and relevant headers captured for check, offer,
   poll, and describe) diffed against the post-split responses
   is required; existing tests stay unmodified.

   *Resolution*: the pin-before-reshaping lens now specifies the
   baseline script, its fixed dynamics, and the recorded
   pre/post capture, referenced from M2's checklist.

7. **P3: the facts seam conflates all-device facts with
   unbound-only state.** Two seams, not one: observed facts stay
   at `check_version` for every device; pending `last_seen`
   stays at `activation_for`/`observe` for unbound ones. No new
   recorder.
