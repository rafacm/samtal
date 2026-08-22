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
   uses and making the catch-all arm explicit (`case _:`). The
   surrounding comments move into the arms unchanged. The text arm
   is spelled `case TextDelta(text=text):` with the first-token
   check remaining an inner `if first_token_at is None and
   text.strip():`, never a case guard: `splitter.push(text)` runs
   unconditionally, whitespace included, and a guard spelling
   (`case TextDelta(text=text) if text.strip():`) would route a
   whitespace-only delta into `case _:` as a phantom tool call. The
   pin under the lenses section covers exactly that delta.
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
- **A changelog entry under `### Changed`**, in the house form the
  existing refactor entries use: what moved, and that nothing an
  operator sees changed. The repository's convention records
  behavior-preserving refactors as a matter of course, and this plan
  follows it rather than inventing an exemption.

## The standing review lenses, pre-answered

- **No-leak.** No error text, event field, log line, or API body is
  touched.
- **Pin before reshaping.** Three of the four arms are pinned by the
  session suites (`tests/unit/test_session.py`,
  `test_session_tools.py`, `test_session_events.py`,
  `test_session_record.py`, `test_session_characterization.py`,
  `test_session_watchdog.py`), which drive the loop through fake
  providers yielding `TextDelta`, `Usage`, and tool-call events: a
  `TextDelta` routed to `calls` breaks the spoken-sentence
  assertions, a lost `Usage` breaks the round-event pins, a
  swallowed tool call breaks `test_session_tools.py`. The
  `StreamStarted` arm is the one arm with no pin anywhere: every
  producer yields it once in first position, `_watchdog_stream`
  swallows exactly that one (`pipeline.py:815`), and the support
  fakes never yield it at all, so the arm could be deleted green
  while the contract (`providers/base.py:282`, consumers "must
  nevertheless tolerate and ignore it") breaks on the first
  mid-stream occurrence. The milestone therefore adds the missing
  pin BEFORE the conversion, committed green against the chain: a
  scripted stream that yields a second `StreamStarted` mid-stream
  and a whitespace-only `TextDelta`, asserting the reply still
  speaks every scripted sentence, the whitespace still reaches the
  splitter, no phantom tool call is recorded, and no first-token
  time is taken from whitespace. The existing suites stay
  byte-unchanged through the conversion; the new pin is the one test
  change.
- **Closed sets.** The event union (`LlmEvent`) is the closed set,
  declared where the events are; the `match` restates the same arms
  over the same types with the same catch-all, adding and removing
  none. Honestly stated: with `_watchdog_stream` typed
  `AsyncIterator[Any]`, neither spelling gets exhaustiveness help
  and the explicit `case _:` is a reading gain, not a checked one.
  The conversion commit therefore also annotates `_watchdog_stream`
  (its parameter and return) as `AsyncIterator[LlmEvent]`, which is
  what its docstring and single caller already say it is; no checker
  in CI enforces it today, and the annotation is the seam stated
  truthfully for the one that will.
- **Honest seams.** No seam changes; the loop consumes the same
  `LlmEvent` stream from the same watchdog wrapper.
- **Inventories by tooling.** One site converts; the inventory is
  the issue's own rejected-sites list, re-checked before the commit
  by grepping for `isinstance(` over `runtime/pipeline.py` whole,
  not just `isinstance(event`. That wider sweep is what finds the
  sibling dispatch the narrow one cannot:
  `isinstance(first, StreamStarted)` at `pipeline.py:815`, the
  watchdog's own single negated check on the same type. It stays as
  it is; a one-arm boolean is not a dispatch, and converting it
  would be the guard-clause mistake the issue's rejected list
  already names for `parse_message`.

## Module layout

Unchanged. One file touched in `src` (`runtime/pipeline.py`), one
test file gains the pin the review found missing.

## Tests

The existing session suites are the characterization for three arms
and stay byte-unchanged. One new pin is added before the conversion
(the review's finding 1): the mid-stream `StreamStarted` plus
whitespace-only `TextDelta` stream described under the pin lens,
which is a behavior pin on the loop's contract, not a restatement of
the dispatch idiom.

## Risks and mitigations

The four event types are unrelated frozen dataclasses with no shared
base, so arm ordering is not load-bearing and no subject can match
two arms; the real risks are the two the review named:

- **The `StreamStarted` arm converts wrong or gets dropped**, which
  no existing suite would catch. Mitigated by the new pin, committed
  green against the chain before the conversion touches the loop.
- **The text arm converts to a case guard**, silently rerouting
  whitespace-only deltas into the tool-call arm. Mitigated by the
  explicit spelling in the decisions section and the whitespace-only
  delta in the same pin.

## Milestones

- [ ] **M1: Pin the unpinned arms, then convert the loop to
  `match`.** Two code commits (the pin, then the conversion with its
  changelog entry), plus the implementation-doc section and the
  ticked checklist entry in the change that completes the milestone,
  as AGENTS.md requires. Design footprint: no interface changes
  anywhere; the loop's reader stops having to parse an `if`/`elif`
  chain to see that four event shapes have four fates, and the
  device edge and the runtime edge read identically. No new modules,
  no new seams.

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

*Resolution*: accepted in full. The pin lens, module layout, and
tests sections now state the `StreamStarted` arm as the one unpinned
arm and add the pin (a mid-stream `StreamStarted` plus a
whitespace-only `TextDelta`, folding finding 2's coverage in),
committed green against the chain before the conversion. The
milestone becomes two commits: the pin, then the conversion.

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

*Resolution*: accepted. The decisions section now spells the arm
explicitly, forbids the case-guard spelling with the reason, and the
finding-1 pin covers the whitespace-only delta.

**3 (P2). The stated reason for skipping the changelog is
contradicted by the changelog.** `CHANGELOG.md` records
behavior-preserving refactors under `### Changed` as a matter of
course, with "nothing an operator sees changed" as the entry's own
point; AGENTS.md exempts nothing. Add the entry or argue a real
premise.

*Resolution*: accepted; the entry is added. The false premise is
dropped rather than replaced: the repository's own convention
answers the question, and arguing a size bar against five recorded
counterexamples would be the same mistake with more words.

**4 (P3). The "closed sets" lens claims a type-level property the
code does not have.** `_watchdog_stream` is typed
`AsyncIterator[Any]`, so neither the chain nor the `match` gets any
exhaustiveness help and `case _:` accepts a fifth `LlmEvent` member
into `calls` silently. Say the catch-all gain is a reading gain, or
take the cheap win and annotate the stream `AsyncIterator[LlmEvent]`.

*Resolution*: accepted, both halves. The lens now states the
reading-gain honestly, and the conversion commit annotates
`_watchdog_stream` with `AsyncIterator[LlmEvent]`, verified truthful
against its docstring and its single caller at `pipeline.py:1266`.

**5 (P3). Both listed risks are impossible; the two real ones are
absent.** The four event types are unrelated frozen dataclasses with
no base, so no subject can match two arms; the real risks are
findings 1 and 2. Replace the section.

*Resolution*: accepted; the section is replaced with the two real
risks and their mitigations, and the impossibility of the old two is
stated once where the ordering question would next be asked.

**6 (P3). The inventory grep misses the sibling dispatch in the same
file.** `isinstance(first, StreamStarted)` at `pipeline.py:815` is
the other dispatch on the same type; correctly out of scope, but the
plan should name it and why it stays rather than rely on a grep that
cannot see it.

*Resolution*: accepted; the inventory lens now names line 815, why
it stays, and widens the grep so the sweep itself would have found
it.

**7 (P3). The milestone's footprint omits the documentation work
AGENTS.md requires.** The implementation-doc section and the ticked
checklist entry are milestone work in the same change; the plan
references the doc but never lists the work.

*Resolution*: accepted; folded into the milestone rewrite the
finding-1 amendment made, which now lists the implementation-doc
section and the ticked checklist entry as the milestone's own work.

Verdict: ready after the P1/P2 amendments.
