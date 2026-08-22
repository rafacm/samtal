# Dispatch the LLM event stream with structural pattern matching

## Goal

Implement issue #235: convert the LLM event loop in
`runtime/pipeline.py` from an `isinstance` chain to a `match`
statement, so both ends of the pipeline dispatch typed events in the
same idiom the device edge already uses (`device/session.py`). The
change is behavior-preserving by construction and by the existing
pins; nothing observable moves.

The companion implementation doc,
[`2026-08-22-match-dispatch-implementation.md`](2026-08-22-match-dispatch-implementation.md),
records what the milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by #235 and not re-litigated here:

1. **The LLM event loop converts.** The chain over `StreamStarted`,
   `TextDelta`, `Usage`, and the everything-else tool-call arm
   becomes `match event:`, destructuring the one field the text arm
   uses (`case TextDelta(text=text):`) and making the catch-all arm
   explicit (`case _:`). The surrounding comments move into the arms
   unchanged.
2. **The rejected sites stay rejected**: `parse_message`'s guard
   clauses, the boolean-condition chains in `ota/reply.py` and the
   LLM turn builders, the two-arm shape ternaries, and the recursive
   secret walkers. The issue records why; no code there changes.

## The issue's open questions, resolved

**Does `_held` in `config/store.py` convert too?** No. The issue
calls it optional and names the criterion (skip if it reads worse).
The one semantic gain, a sequence pattern excluding `str` and
`bytes` automatically, does not apply cleanly here: the `BaseModel`
arm keeps its internal conditional either way, the `Mapping` arm is
a class pattern of no depth, and the sequence arm still needs its
`isinstance(segment, int)` guard, so the conversion trades one
enumeration for one guard and gains a `case` ladder around logic
that stays conditional inside. The chain as written reads top to
bottom in the shape vocabulary its docstring uses. Recording the
decision here is the issue's "judgment call" answered.

## Design decisions this plan makes

- **No new modules, no new names.** The change is confined to the
  loop body inside the reply round in `runtime/pipeline.py`; no
  helper is extracted (a dispatch of four arms inlined at its only
  site passes the deletion test in the inline direction).
- **The `StreamStarted` arm keeps its skip semantics** (`continue`
  under the chain) whatever the `match` arm's spelling is; the arm
  exists to state indifference, and its comment stays.
- **No changelog entry.** The changelog records notable changes;
  a refactor with no observable behavior is not one, and saying so
  here is the record.

## The standing review lenses, pre-answered

- **No-leak.** No error text, event field, log line, or API body is
  touched.
- **Pin before reshaping.** The pins exist: the session suites
  (`tests/unit/test_session.py`, `test_session_tools.py`,
  `test_session_events.py`, `test_session_record.py`,
  `test_session_characterization.py`, `test_session_watchdog.py`)
  drive the loop through fake providers yielding `TextDelta`,
  `Usage`, and tool-call events, and assert spoken sentences,
  usage on the round event, and dispatched calls. They are committed
  green before the change and must be byte-unchanged after; the
  milestone's verification names the subset that fails when any arm
  is mis-mapped (a `TextDelta` routed to `calls` breaks the
  spoken-sentence assertions; a lost `Usage` breaks the round-event
  pins; a swallowed tool call breaks `test_session_tools.py`).
- **Closed sets.** The event union is the closed set, declared where
  the events are; the `match` restates the same arms over the same
  types with the same catch-all, adding and removing none.
- **Honest seams.** No seam changes; the loop consumes the same
  `LlmEvent` stream from the same watchdog wrapper.
- **Inventories by tooling.** One site converts; the inventory is
  the issue's own rejected-sites list, re-checked by grepping for
  `isinstance(event` under `runtime/` before the commit.

## Module layout

Unchanged. One file touched in `src` (`runtime/pipeline.py`), no
test files change.

## Tests

No new tests: the change is behavior-preserving and the existing
session suites are the characterization. A new test restating the
dispatch would pin the idiom, not the behavior.

## Risks and mitigations

- **A subtle arm-ordering change**: under `match`, an event matching
  two class patterns takes the first, same as the `isinstance`
  chain; the arms are mutually exclusive types, and the suites catch
  a mis-route as above.
- **`Usage` instances also matching a wider pattern** (if the event
  types share a base): mitigated by keeping the arm order identical
  to the chain's and by the pins.

## Milestones

- [ ] **M1: Convert the LLM event loop to `match`.** One commit.
  Design footprint: no interface changes anywhere; the loop's reader
  stops having to parse an `if`/`elif` chain to see that four event
  shapes have four fates, and the device edge and the runtime edge
  read identically. No new modules, no new seams.
