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

## Plan review round

External review: claude backend (codex quota exhausted), claude CLI,
model claude-opus-5, read-only tool set, 2026-08-22, of commit
85d8352. Findings condensed but faithful; resolutions follow each.

**1 (P1). The `StreamStarted` arm is pinned by nothing; the plan's
central verification claim is false.** Every producer yields
`StreamStarted` once, in first position, and `_watchdog_stream`
swallows a first-position one (`pipeline.py:815`), so no suite ever
delivers the event to the loop; the support fakes never yield it at
all. Deleting the arm keeps all six named suites green while a
mid-stream `StreamStarted` would land in `calls`, defeat
`if not calls: break`, and reach `_reserve_tools`. The arm is
contract (`providers/base.py:282`). The plan must name it as the one
arm with no pin and add one: a fake yielding a second
`StreamStarted` mid-stream, asserting the reply still speaks and no
phantom tool call is recorded; "No new tests" and "no test files
change" fall with it.

**2 (P2). The whitespace-only timing guard is unpinned and the plan
never forbids the spelling that breaks it.** The guard
`if first_token_at is None and event.text.strip():` sits above a
`splitter.push(event.text)` that runs unconditionally, whitespace
included. The obvious case-guard spelling
(`case TextDelta(text=text) if text.strip():`) would route
whitespace-only deltas into `case _:` and ship green, since no test
yields one. The plan must spell the arm as
`case TextDelta(text=text):` with the strip check remaining an inner
`if`, and cover the whitespace-only delta in the finding-1 pin.

**3 (P2). The stated reason for skipping the changelog is
contradicted by the changelog.** `CHANGELOG.md` records
behavior-preserving refactors under `### Changed` as a matter of
course, with "nothing an operator sees changed" as the entry's own
point; AGENTS.md exempts nothing. Add the entry or argue a real
premise.

**4 (P3). The "closed sets" lens claims a type-level property the
code does not have.** `_watchdog_stream` is typed
`AsyncIterator[Any]`, so neither the chain nor the `match` gets any
exhaustiveness help and `case _:` accepts a fifth `LlmEvent` member
into `calls` silently. Say the catch-all gain is a reading gain, or
take the cheap win and annotate the stream `AsyncIterator[LlmEvent]`.

**5 (P3). Both listed risks are impossible; the two real ones are
absent.** The four event types are unrelated frozen dataclasses with
no base, so no subject can match two arms; the real risks are
findings 1 and 2. Replace the section.

**6 (P3). The inventory grep misses the sibling dispatch in the same
file.** `isinstance(first, StreamStarted)` at `pipeline.py:815` is
the other dispatch on the same type; correctly out of scope, but the
plan should name it and why it stays rather than rely on a grep that
cannot see it.

**7 (P3). The milestone's footprint omits the documentation work
AGENTS.md requires.** The implementation-doc section and the ticked
checklist entry are milestone work in the same change; the plan
references the doc but never lists the work.

Verdict: ready after the P1/P2 amendments.
