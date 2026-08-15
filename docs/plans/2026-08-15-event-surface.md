# Deepen the event surface into one emitter with an explicit tap

## Goal

Implement issue #138: the structured JSON events are the declared
observability surface (ADR 2026-08-04), but the machinery serving it
is used by exactly one subsystem. `SessionEvents` lives in
`device/events.py` and is called only by the pipeline and the device
session; `ota.py`, `onboarding.py`, `capture.py`, `ws.py`, `app.py`,
`registry.py`, `filler.py`, `device/bindings.py`, `tools/memory.py`
and `config/api.py` hand-build `extra={...}` dicts; the MCP
subsystem emits no structured events at all; providers invented a
private `_echo_event`. Move the emitter to neutral ground, give the
server scope an emitter of its own, give the MCP lifecycle its
events, and make the consumer fan-out an explicit tap interface, so
#120's conversation store attaches as one more consumer.

The companion implementation doc,
[`2026-08-15-event-surface-implementation.md`](2026-08-15-event-surface-implementation.md),
records what each milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by issue #138 and not re-litigated here:

1. **The emitter moves out of `device/`** to a home neither the
   device edge nor runtimes own (name decided here). Session-scoped
   events keep the pinned `samtal_server.session` logger name and
   their existing names and fields byte-for-byte: they are a
   compatibility surface and this change must be invisible in the
   retained logs.
2. **A server-scoped emitter** (no session identity; names the host
   or entry instead) for MCP, OTA, onboarding, providers, and
   capture, replacing hand-built `extra` dicts.
3. **The MCP lifecycle gets structured events** (connect, down with
   reason token, drop after failed call, reload outcome,
   shadowed-tool drop). These are new compatibility surface: names
   chosen here, recorded in the README event table, changed only as
   breaking changes thereafter.
4. **Consumer fan-out becomes an explicit tap interface** on the
   emitter (JSON log, capture decision track, future consumers), so
   #120's store sink and #66/#67 exporters attach without touching
   emit sites. Designing the tap is in scope; implementing any new
   consumer is not.
5. **The invariant "an event that is logged is an event that is
   recorded" survives the move.**

Sequencing: this lands before #120 by agreed order.

Evidence re-verified at main@0ab83cc: `SessionEvents` is imported by
`runtime/pipeline.py:53`, `device/session.py:71`, and
`device/boundary.py:31` (the `RuntimeFactory` type); the hand-built
`extra={` sites count ota 8, onboarding 4, capture 8, registry 3,
ws 2, app 2, bindings 2, filler 1, memory 1, config/api 2 (the
config/models.py hit is a pydantic `json_schema_extra`, not an
event); `tools/mcp.py` has 13 plain `logger.*` calls and no
structured event; `openai_asr.py`'s `_echo_event` builds the
`asr_prompt_echo` payload privately. The README's event table sits
at README.md:~1735 and already documents `asr_prompt_echo` as
carrying no session (providers serve them all), which is the
precedent decision 2 generalizes.

## Decisions this plan makes

### The home is `samtal_server/events.py`

A top-level module, the same neutral altitude `egress.py` took in
#136: `device/`, `runtime/`, and every server subsystem import
downward into it and it imports none of them. The issue's word
"package" is read as "a location owned by neither side"; one module
holds the tap protocol and both emitters today, and becoming a
directory later is a mechanical move behind the same import path is
not. `device/events.py` is deleted, not forwarded: its three
importers are all first-party and updated in the same change, and a
compatibility shim would be exactly the half-move the issue exists
to end.

### The tap is the consumer interface, and the log is its first implementation

```python
@dataclass(frozen=True)
class Emission:
    """One event, complete: everything any consumer needs.

    `payload` is the finished structured dict; `at` is monotonic
    time (whose clock is the emitter's, below); `level` is the
    numeric logging level; `message` and `args` are the human
    sentence exactly as a logging call would receive them."""

    payload: dict[str, Any]
    at: float
    level: int
    message: str
    args: tuple[Any, ...]


class EventTap(Protocol):
    """One consumer of the structured events."""

    def emit(self, emission: Emission) -> None: ...
```

One envelope, not a payload-only signature (the review round's
finding 1): `LogTap` needs the level, the template and the args to
produce today's records, so the protocol carries them for every
consumer, and a consumer that only wants the payload reads one
field. `SessionEvents` stops being a payload factory the call
sites log around and becomes the thing that emits: sites call
`events.info("heard %r", text, event="heard", agent=..., ...)`
(plus `.debug`, `.warning`, `.error`), the emitter builds the
payload exactly as `event()` builds it today (`event`, `session`,
`device`, then the fields), wraps it in an `Emission`, and hands
it to every attached tap. The payload-only
`SessionEvents.event()` does NOT survive: dispatching it would
invoke `LogTap` (a double log), not dispatching it would bypass
the capture, so the call-site migration is atomic and the method
is deleted with it, `SessionEvents.log` with it if nothing is left
using the handle. Two implementations ship:

- `LogTap`: logs on the pinned `samtal_server.session` channel with
  `emission.message`, `emission.args`, `emission.level`, and the
  payload as `extra=`, which is byte-for-byte what the call sites
  do today: same logger name, same human sentence, same level, same
  JSON fields. Attached at construction, always.
- `CaptureTap`: wraps the attached `SessionCapture`, calling its
  existing `event(payload, at)` with the emission's payload and
  time; attached when the capture opens and detached before it
  closes, exactly where `attach_capture`/`detach_capture` sit
  today (the methods keep their names; they wrap the generic
  `attach`/`detach` the store and exporters will use).

Dispatch order and failure behavior are fixed, not incidental (the
review round's finding 2). Today the capture records inside
`event()` before the log call returns, so the guarantee runs
capture-first; the emitter therefore dispatches non-log taps in
attachment order FIRST and `LogTap` LAST, preserving "every logged
event was first offered to the attached capture" exactly. A tap
that raises does not starve the taps after it: the emitter runs
each tap under its own guard, and a tap failure is reported once
on the emitter's own channel (a plain sentence, not an event, so a
broken tap cannot recurse into itself) while the remaining taps,
the log above all, still see the emission. The contract test
covers all of it with a capture-shaped spy (same `event(payload,
at)` surface as `SessionCapture`): the spy receives exactly the
payload dicts the log emitted, receives them before the log record
is created, keeps receiving when another tap raises, and stops on
detach while the log continues.

The tap contract is events only. `vad()` and `dropped()` remain
capture-specific side channels on `SessionEvents` (they feed the
capture's VAD and drop tracks, which no other consumer has a
meaning for), delegating to the attached capture as today; the
docstring says they are outside the tap contract and why.

Emit sites change shape and nothing else changes: the channel, the
sentences, the levels, the payloads are identical, which the pin
suite (below) proves by passing unmodified through the reshape.

### The server scope gets `ServerEvents`, one per subsystem channel

`ServerEvents(channel: str)` wraps `logging.getLogger(channel)` and
offers `.debug/.info/.warning/.error(msg, *args, event=...,
**fields)`, building `{"event": ..., **fields}` with no session or
device defaults: a server-scoped event names what it is about
explicitly (`device=`, `entry=`, `host=`, `path=`), which is what
every hand-built site already does. `.debug` is required, not a
nicety: `device_bindings_snapshot_only` is a structured
`logger.debug` event today, and its level is part of the retained
surface, which its migration test asserts (the review round's
finding 4).

The clock is an explicit dependency, not an assumption (finding 3):
`SessionEvents` binds the session loop's clock at construction
(the capture's tracks are aligned by it, and a session only exists
inside the loop), while `ServerEvents` uses `time.monotonic`,
because server events fire before any loop runs (`create_app`'s
capture events, the onboarding banner `main()` logs before serving)
and `asyncio.get_running_loop()` would raise there. The contract
test emits a server event outside any running loop and a session
event inside one. Each subsystem constructs
its emitter on its existing module logger name, so the `logger`
field of every retained record is unchanged, and every existing
event name and field moves byte-identically; the diff at each site
is mechanical (`logger.info(msg, extra={...})` becomes
`events.info(msg, event=..., ...)`). Server-scope consumers need one attachment point, not a hunt
through module privates (the review round's finding 8): every
`ServerEvents` registers itself with a module-level hub in
`events.py` at construction, and `attach_server_tap(tap)` /
`detach_server_tap(tap)` on the hub reach every emitter, including
ones constructed after the attach (the hub holds the tap set;
emitters read it at emit time rather than copying it). Nothing
attaches in this issue; the hub, its lifecycle, and a contract
case (attach, emit from an emitter created later, detach) land so
#66/#67 attach without touching emitters.

The migrated sites are every structured-event `extra={` in
production code: ota.py, onboarding.py, capture.py, ws.py, app.py,
registry.py, filler.py, device/bindings.py, tools/memory.py,
config/api.py, and openai_asr.py's `_echo_event` (the private
builder dissolves into a module-level `ServerEvents` for the
provider channel; the `asr_prompt_echo` event keeps its name,
fields, and logger). Capture attaching to session taps while its
own module emits server events is not a cycle: `capture.py`
imports `events.py` for `ServerEvents`, and `events.py` imports
nothing from `capture.py` because the capture arrives as a tap
object, which is the point of the tap.

### The MCP lifecycle events, named

New compatibility surface, added to the README event table in the
same change, all on the `samtal_server.tools.mcp` channel, all
naming the entry (the operator's configured name, a trusted
identifier) and never carrying far-side bytes:

| event | when | fields |
| --- | --- | --- |
| `mcp_connected` | an entry's connect envelope completes and its tools are published | `entry`, `transport`, `tools` (an integer count, never a list), `duration_ms` |
| `mcp_down` | an entry fails to come up or its connection is given up | `entry`, `reason` (closed token set below), `duration_ms` where a duration exists |
| `mcp_call_dropped` | a call failed and the connection is dropped because of it | `entry`, `tool` |
| `mcp_reload` | a reload request completes | `outcome` (`applied` or `refused`); applied carries the counts `started`, `restarted`, `stopped`, `unchanged` and `duration_ms`; refused carries `reason` from a closed set |
| `mcp_tool_shadowed` | a published tool is dropped because another entry shadows it | `entry`, `position` (the tool's position in the far side's listing), `owner` (the shadowing entry, a trusted configured name). Deliberately NO tool name: a shadowed tool never reached the publishing rule, its name is far-side bytes, and the existing code already logs the position instead of the name for exactly this reason |

The `mcp_down` reason tokens map one-to-one onto the decision
sites, which requires the manager to know which phase of `_run`'s
envelope it is in (the review round's finding 6: transport,
initialization, and `list_tools()` currently sit behind one broad
catch, so the milestone adds explicit phase tracking, a local
marker advanced between the stack entries, before classification
can be honest):

- `transport_failed`: the stdio spawn or HTTP connection raised
- `initialize_failed`: the MCP initialize exchange raised
- `discovery_failed`: `list_tools()` or publication raised
- `connect_timeout`: the connect envelope's own bound expired
- `call_failed`: `_mark_down` gave the connection up after a
  failed call; this down emits BOTH `mcp_call_dropped` (the call's
  story) and `mcp_down` with this token (the connection's story),
  stated here so the pairing is contract rather than accident
- `stopped`: an intentional stop (shutdown or reload)

Classification is by exception type, recursing into
`ExceptionGroup`s, never by message text.

`mcp_reload` matches the reload that exists rather than one that
does not (finding 7): `McpReload` counts `started`, `restarted`,
`stopped`, `unchanged`, deliberately has no failure count (an
unreachable new manager is an applied reload that will report
`mcp_down` on its own), preparation refusals raise before `_apply`
(outcome `refused`, reason from the refusal classification, a
closed set fixed at implementation from the refusal types that
exist), and a caller-cancelled apply continues behind its shield,
so the event is emitted exactly once at apply or refusal
completion, whether or not the requesting client is still
connected.

Reason tokens are literals chosen where the failure is classified;
the existing sanitization tests (the MCP suites' no-leak
assertions) are extended to plant a sentinel in a connect failure
and assert its absence from event fields and the log, and the
shadowed-tool test plants a valid, credential-shaped tool name and
asserts it appears nowhere. The exact call sites are the 13
`logger.*` calls plus the reload path; the milestone maps each to
an event or deliberately leaves it a plain sentence (a line that
narrates progress rather than records an outcome stays prose; the
implementation doc lists both columns).

### What this issue does not do

No new consumer is implemented: the store sink is #120's, the
exporters are #66/#67's. No event name or field of the existing
surface changes. `config/api.py`'s two `api_error` events migrate
mechanically but the config domain's wider single-sourcing stays
#139's. The pipeline's own emit sites move to the new import and
shape but the pipeline is otherwise untouched (#141's territory).

### Three milestones, three PRs, stacked

1. **The emitter moves and the tap exists.** The milestone's FIRST
   commit is the pin suite (the review round's finding 10): a
   characterization module driving every structured session emit
   path and pinning `record.name`, `levelno`, `getMessage()`, and
   the exact nonstandard field key set and values (dynamic
   identifiers and durations normalized), committed green against
   the unrefactored code and unchanged thereafter. Then
   `events.py` lands with `Emission`, `EventTap`, `LogTap`,
   `CaptureTap`, the server hub, and the moved `SessionEvents`;
   pipeline.py (17 sites), device/session.py (8 sites, its private
   `_event` helper deleted), device/boundary.py, and
   `test_boundary_contract.py`'s import move (finding 9: 26
   production sites total, one test import); `device/events.py` is
   deleted; the contract test lands. Accept: the pin suite and
   every existing event-assertion test pass unmodified;
   `grep -rn "device.events" samtal_server tests` is empty.
2. **The server scope emits through it.** The pin suite first
   extends to every structured server emit path (same fields, same
   normalization, green pre-migration); then `ServerEvents` plus
   the mechanical migration of every hand-built structured
   `extra=` site; `_echo_event` dissolves. Accept: the extended
   pin suite passes unmodified through the migration; the AST
   guard (finding 11) passes: a test walking the production tree's
   AST rejects any `logging` call carrying an `extra=` keyword
   outside `events.py` itself (deliberate exceptions, if any
   survive, enumerated in the test with reasons), and asserts
   `_echo_event` and `device.events` appear nowhere.
3. **The MCP lifecycle speaks.** The five events at their call
   sites, the README table rows, the sanitization extension, the
   CHANGELOG entry for the whole issue. Accept: each event covered
   by a test through the real manager against the stdio/http test
   servers where the existing MCP suites already run them.

Milestones 2 and 3 both leave `main` releasable: each adds or
migrates events without changing any consumer's view of the
existing surface.

## Files touched

```
samtal-server/samtal_server/events.py            new: EventTap, LogTap, CaptureTap, SessionEvents, ServerEvents
samtal-server/samtal_server/device/events.py     deleted (milestone 1)
samtal-server/samtal_server/runtime/pipeline.py  imports and emit-site shape (m1)
samtal-server/samtal_server/device/session.py    imports and emit-site shape (m1)
samtal-server/samtal_server/device/boundary.py   the RuntimeFactory type's import (m1)
samtal-server/samtal_server/{ota,onboarding,capture,ws,app,registry,filler}.py  ServerEvents (m2)
samtal-server/samtal_server/device/bindings.py   ServerEvents (m2)
samtal-server/samtal_server/tools/memory.py      ServerEvents (m2)
samtal-server/samtal_server/config/api.py        ServerEvents (m2)
samtal-server/samtal_server/providers/openai_asr.py  _echo_event dissolves (m2)
samtal-server/samtal_server/tools/mcp.py         lifecycle events (m3)
samtal-server/README.md                          event table rows (m3)
samtal-server/tests/unit/test_events.py          new: the contract test (m1)
samtal-server/tests/unit/ (MCP suites)           lifecycle event tests, sanitization extension (m3)
CHANGELOG.md                                     entry (m3)
docs/plans/2026-08-15-event-surface.md
docs/plans/2026-08-15-event-surface-implementation.md
```

`config.example.yaml` is untouched: no configuration key changes.

## Verification

- Per milestone: `uv run ruff check .`, `uv run pytest tests/unit
  -q`, `uv run pytest tests/integration -q` from `samtal-server/`.
- Milestone 1: the pin suite, committed green before the reshape
  and unchanged after it, plus the event-assertion suites
  (`test_session_events.py`, `test_session_filler.py`,
  `test_session_barge_in.py`, `test_capture_session.py`, and every
  other test asserting on `caplog` records from the session
  channel) pass with no diff;
  `grep -rn "from samtal_server.device.events" samtal_server tests`
  empty.
- Milestone 2: the extended pin suite unchanged and green; the AST
  guard test green (no production logging call carries `extra=`
  outside `events.py`; `_echo_event` and `device.events` absent).
- Milestone 3: each lifecycle event asserted through a real manager
  run; the sentinel sanitization test; the README table rows match
  the emitted fields exactly.

## Risks and mitigations

- **Byte-compat drift while reshaping 19 emit sites.** The
  sentences and payloads move by copy, but a missed `%` arg or a
  renamed field would corrupt the retained surface. Mitigation: the
  event-assertion suites are the contract and pass unmodified;
  milestone 1 is reviewed as a rename-shaped diff.
- **The tap changes event timing for the capture.** Today the
  capture records inside `event()` before the log call; the order
  is now fixed as non-log taps first, `LogTap` last, per-tap
  guards, and the contract test pins it; the capture's decision
  track carries its own clock reading as today.
- **MCP reason tokens leak by future edit.** A reason token built
  from an exception message would carry far-side bytes. Mitigation:
  tokens are literals chosen at classification sites, the
  sanitization test plants a sentinel, and the README rows document
  the closed token set.
- **The chain sequencing.** This is the first link of
  #138 -> #120 -> #139 -> #140 -> #141; milestone 2 touches files
  #139 and #141 will move again. That is the accepted cost of the
  agreed order; the runbook forbids running them concurrently.

## Plan review round

One external review of the plan as first committed (c6d0f51): codex
CLI, model gpt-5.6-sol, read-only against this repository with the
issue #138 body supplied, 2026-08-15. Verdict: ready after the
P1/P2 amendments. Findings as received, condensed; each carries its
resolution once the amendment addressing it lands.

1. **P1: the tap signature cannot implement LogTap.** The protocol
   carried only `(payload, at)` while the text promised taps the
   level, message and args; and keeping a payload-only `event()`
   is impossible (dispatching it invokes LogTap, not dispatching
   bypasses capture). Define one exact envelope carrying payload,
   monotonic time, numeric level, message template, and args;
   LogTap consumes it; the payload-only `event()` goes after the
   atomic migration.
   *Resolution*: adopted. The tap decision now defines the
   `Emission` envelope (payload, at, level, message, args),
   `LogTap` consumes it, and `SessionEvents.event()` and `.log`
   are deleted with the atomic migration rather than retained.
2. **P1: LogTap first reverses the capture guarantee.** Capture
   currently records before the log call returns; sequential
   fan-out with LogTap first can log an event capture never saw if
   a tap raises. Dispatch capture before logging, define per-tap
   failure behavior, and test ordering and detach with a
   capture-shaped spy; the contract must show every logged session
   event was first offered to the attached capture.
   *Resolution*: adopted. Non-log taps dispatch first and `LogTap`
   last, each tap under its own guard with a one-line non-event
   report on tap failure, and the contract test uses a
   capture-shaped spy pinning order, isolation, and detach.
3. **P1: a mandatory loop-clock timestamp breaks synchronous
   server events.** `create_app` and `onboarding.log_banner` emit
   before any loop runs; `asyncio.get_running_loop().time()`
   raises there. Make the clock an explicit dependency: session
   emitters bind the session loop clock (capture alignment),
   server emitters use a synchronous monotonic clock; contract
   test emits outside a running loop.
   *Resolution*: adopted. The ServerEvents decision binds the loop
   clock to `SessionEvents` at construction and `time.monotonic`
   to `ServerEvents`, with the outside-a-loop contract case named.
4. **P1: ServerEvents lacks the required debug level.**
   `device_bindings_snapshot_only` is a structured `logger.debug`
   event. Include `.debug`, and assert the event stays DEBUG.
   *Resolution*: adopted. `.debug` joins the API and the bindings
   event's level is asserted by its migration test.
5. **P1: `mcp_tool_shadowed.tool` leaks bytes current code
   deliberately withholds.** Name sanitization only replaces
   illegal characters, an alphanumeric credential survives, and
   the existing code logs the position rather than the name for
   exactly this reason. Fields become `entry`, `position`, trusted
   `owner`, no tool name; sentinel test with a credential-shaped
   shadowed name; `mcp_connected.tools` specified as an integer
   count.
   *Resolution*: adopted. The table row carries `entry`,
   `position`, `owner` and states the no-name reason; the count is
   an integer; the credential-shaped sentinel test is named.
6. **P1: the `mcp_down` reason set does not cover the actual down
   transitions.** `_run` puts transport, initialization and
   `list_tools()` behind one broad catch, and `_mark_down` gives a
   connection up after a failed call with no reason token in the
   proposed set. Give a decision-site mapping for transport,
   initialization, tool discovery, failed call, timeout, and
   intentional stop; state whether a failed call emits both
   `mcp_call_dropped` and `mcp_down` (with a `call_failed` token
   if so); classify exception groups recursively without reading
   messages.
   *Resolution*: adopted. The section now maps six tokens to their
   decision sites, adds the phase tracking `_run` needs before the
   classification can be honest, states the call-failure pairing
   as contract, and pins recursive type-only classification.
7. **P1: the reload event schema does not describe the reload
   implementation.** `McpReload` counts `started`, `restarted`,
   `stopped`, `unchanged`, deliberately has no failure count, and
   treats an unreachable new manager as applied; refusals raise
   before `_apply`; a cancelled apply continues behind a shield.
   Define `outcome` as `applied`/`refused`, keep all four applied
   counts including `restarted`, use a closed refusal reason
   instead of a `failed` count, and emit exactly once at apply or
   refusal completion even when the requesting client
   disconnected.
   *Resolution*: adopted. The event is now `mcp_reload` with
   `outcome` applied/refused, the four applied counts including
   `restarted`, a closed refusal reason instead of a failure
   count, and exactly-once emission through the shield.
8. **P2: per-instance server tap lists give no global attachment
   point.** A future exporter would have to discover and mutate
   every module's private emitter. Define a shared hub (or a
   common injected tap set) with an attachment lifecycle covering
   emitters constructed before and after a consumer attaches.
   *Resolution*: adopted. A module-level hub in `events.py` holds
   the server tap set; emitters register at construction and read
   the set at emit time, so attach order does not matter; the
   contract test covers the created-later case.
9. **P2: the migration inventory is stale.** `device/session.py`
   has eight `extra=self._event(...)` sites (a private `_event`
   helper), so milestone 1 reshapes 26 sites, not 19; and
   `test_boundary_contract.py:40` imports the moving module. Count
   26, remove the helper, and add the test file to milestone 1.
   *Resolution*: adopted. Milestone 1 counts 17+8+1 sites, deletes
   the `_event` helper, and carries `test_boundary_contract.py`.
10. **P2: existing tests do not prove the claimed byte-compatible
    surface.** The suites pin sentences for two events, loggers
    for six, and selected fields without exact key sets or levels.
    Add pre-refactor characterization coverage pinning
    `record.name`, `levelno`, `getMessage()`, and the exact
    nonstandard field set per structured emit path (dynamic values
    normalized), kept unchanged through the reshape.
    *Resolution*: adopted. The pin suite is milestone 1's first
    commit for the session scope and milestone 2's first for the
    server scope, green pre-refactor and unchanged after.
11. **P2: the milestone-2 grep cannot detect most missed
    migrations.** Multiline dicts, `extra={**record, ...}`, and
    quoting variants all evade it. Add an AST-based test rejecting
    production logging calls with an `extra=` keyword, deliberate
    exceptions enumerated, plus absence assertions for
    `_echo_event` and `device.events`.
    *Resolution*: adopted. Milestone 2's acceptance replaces the
    grep with the AST guard test, exceptions enumerated in the
    test with reasons.

## Milestones

- [x] [**The emitter moves and the tap exists**](2026-08-15-event-surface-implementation.md#milestone-1-the-emitter-moves-and-the-tap-exists)
  (PR #152): as decided above. Accept: event-assertion suites
  unmodified and green; no `device.events` import anywhere; the
  contract test covers attach, fan-out, detach, and ordering.
- [x] [**The server scope emits through it**](2026-08-15-event-surface-implementation.md#milestone-2-the-server-scope-emits-through-it)
  (PR #153): as decided above. Accept: no hand-built structured
  `extra=` in production code; logger names, event names, and fields
  byte-identical; before/after pairs recorded.
- [x] [**The MCP lifecycle speaks**](2026-08-15-event-surface-implementation.md#milestone-3-the-mcp-lifecycle-speaks)
  (PR #154): as decided above. Accept: five events tested through real
  managers; sentinel sanitization; README rows exact; CHANGELOG entry.
