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
class EventTap(Protocol):
    """One consumer of the structured events. `payload` is the
    finished event dict; `at` is the loop clock, which is what the
    capture's tracks are aligned by."""

    def event(self, payload: dict[str, Any], at: float) -> None: ...
```

`SessionEvents` stops being a payload factory the call sites log
around and becomes the thing that emits: sites call
`events.info("heard %r", text, event="heard", agent=..., ...)`
(and `.warning`, `.error`), the emitter builds the payload exactly
as `event()` builds it today (`event`, `session`, `device`, then
the fields), and hands it to every attached tap along with the
level, message and args. Two implementations ship:

- `LogTap`: logs on the pinned `samtal_server.session` channel with
  the payload as `extra=`, which is byte-for-byte what the call
  sites do today: same logger name, same human sentence, same JSON
  fields. Attached at construction, always.
- `CaptureTap`: the existing `SessionCapture.event(payload, at)`
  call, attached when the capture opens and detached before it
  closes, exactly where `attach_capture`/`detach_capture` sit
  today (the methods keep their names; they wrap the generic
  `attach`/`detach` the store and exporters will use).

The invariant is now structural instead of positional: every tap
sees every emit, so an event that is logged is an event that is
recorded because both are taps on the same call. The contract test
covers it: a fake tap attached alongside the log receives exactly
the payloads the log emitted, with the same dicts, and detaching
stops the flow without stopping the log.

The tap contract is events only. `vad()` and `dropped()` remain
capture-specific side channels on `SessionEvents` (they feed the
capture's VAD and drop tracks, which no other consumer has a
meaning for), delegating to the attached capture as today; the
docstring says they are outside the tap contract and why.

Emit sites change shape (18 in pipeline.py, 1 in
device/session.py), and nothing else changes: the channel, the
sentences, the levels, the payloads are identical, which the
existing event-assertion suites prove by passing unmodified.
`SessionEvents.event()` itself remains, unchanged in behavior, for
the two sites that need a payload without a log line (none known;
if the migration finds one, the implementation doc records it), and
`SessionEvents.log` remains the channel handle while migration
proceeds; if nothing uses either at the end of milestone 1, they
go.

### The server scope gets `ServerEvents`, one per subsystem channel

`ServerEvents(channel: str)` wraps `logging.getLogger(channel)` and
offers the same `.info/.warning/.error(msg, *args, event=...,
**fields)` shape, building `{"event": ..., **fields}` with no
session or device defaults: a server-scoped event names what it is
about explicitly (`device=`, `entry=`, `host=`, `path=`), which is
what every hand-built site already does. Each subsystem constructs
its emitter on its existing module logger name, so the `logger`
field of every retained record is unchanged, and every existing
event name and field moves byte-identically; the diff at each site
is mechanical (`logger.info(msg, extra={...})` becomes
`events.info(msg, event=..., ...)`). `ServerEvents` carries the
same tap list (`attach`/`detach`) so #66/#67 exporters can consume
server events later; nothing attaches to it in this issue.

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
| `mcp_connected` | an entry's connect envelope completes and its tools are published | `entry`, `transport`, `tools`, `duration_ms` |
| `mcp_down` | an entry fails to connect or its connection is given up | `entry`, `reason` (a token from our own classification: `connect_failed`, `connect_timeout`, `handshake_invalid`, `stopped`), `duration_ms` where a duration exists |
| `mcp_call_dropped` | the connection is dropped after a failed call | `entry`, `tool` |
| `mcp_reloaded` | a reload finishes | `started`, `stopped`, `unchanged`, `failed` (counts), `duration_ms` |
| `mcp_tool_shadowed` | a published tool is dropped because another entry shadows it | `entry`, `tool` |

Reason tokens are chosen where the failure is classified, from the
exception's type and our own state, never from far-side text; the
existing sanitization tests (the MCP suites' no-leak assertions)
are extended to plant a sentinel in a connect failure and assert
its absence from the event fields and the log. The exact call
sites are the 13 `logger.*` calls plus the reload path; the
milestone maps each to an event or deliberately leaves it a plain
sentence (a line that narrates progress rather than records an
outcome stays prose; the implementation doc lists both columns).
`tool` names published tool identifiers, which are already
sanitized by the publishing rule (#121's `names` module), so the
fields stay value-free.

### What this issue does not do

No new consumer is implemented: the store sink is #120's, the
exporters are #66/#67's. No event name or field of the existing
surface changes. `config/api.py`'s two `api_error` events migrate
mechanically but the config domain's wider single-sourcing stays
#139's. The pipeline's own emit sites move to the new import and
shape but the pipeline is otherwise untouched (#141's territory).

### Three milestones, three PRs, stacked

1. **The emitter moves and the tap exists.** `events.py` lands
   with `EventTap`, `LogTap`, `CaptureTap`, and the moved
   `SessionEvents`; pipeline.py, device/session.py, and
   device/boundary.py move their imports; emit sites take the new
   shape; `device/events.py` is deleted; the contract test lands.
   Accept: every existing event-assertion test passes unmodified;
   `grep -rn "device.events" samtal_server` is empty.
2. **The server scope emits through it.** `ServerEvents` plus the
   mechanical migration of every hand-built structured `extra=`
   site; `_echo_event` dissolves. Accept: no hand-built
   `extra={"event"` remains in production code; every migrated
   event byte-identical (existing tests for those events pass
   unmodified; where no test pinned a site, the implementation doc
   shows before/after records for one example per subsystem).
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
- Milestone 1: the event-assertion suites (`test_session_events.py`,
  `test_session_filler.py`, `test_session_barge_in.py`,
  `test_capture_session.py`, and every other test asserting on
  `caplog` records from the session channel) pass with no diff;
  `grep -rn "from samtal_server.device.events" samtal_server tests`
  empty.
- Milestone 2: `grep -rn 'extra={"event"' samtal_server` empty
  (production code); the per-subsystem before/after record pairs in
  the implementation doc.
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
  capture records inside `event()` before the log call; with taps
  the order log-then-capture or capture-then-log must be fixed and
  stated. Mitigation: the tap list preserves attachment order with
  `LogTap` first, and the contract test pins the order; the
  capture's decision track carries its own clock reading as today.
- **MCP reason tokens leak by future edit.** A reason token built
  from an exception message would carry far-side bytes. Mitigation:
  tokens are literals chosen at classification sites, the
  sanitization test plants a sentinel, and the README rows document
  the closed token set.
- **The chain sequencing.** This is the first link of
  #138 -> #120 -> #139 -> #140 -> #141; milestone 2 touches files
  #139 and #141 will move again. That is the accepted cost of the
  agreed order; the runbook forbids running them concurrently.

## Milestones

- [ ] **The emitter moves and the tap exists** (PR TBD): as decided
  above. Accept: event-assertion suites unmodified and green; no
  `device.events` import anywhere; the contract test covers
  attach, fan-out, detach, and ordering.
- [ ] **The server scope emits through it** (PR TBD): as decided
  above. Accept: no hand-built structured `extra=` in production
  code; logger names, event names, and fields byte-identical;
  before/after pairs recorded.
- [ ] **The MCP lifecycle speaks** (PR TBD): as decided above.
  Accept: five events tested through real managers; sentinel
  sanitization; README rows exact; CHANGELOG entry.
