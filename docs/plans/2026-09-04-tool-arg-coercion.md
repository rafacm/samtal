# Coerce lossless tool-call arguments at dispatch

Plan for [#383](https://github.com/rafacm/vinga/issues/383).
Implementation notes land in the companion
`2026-09-04-tool-arg-coercion-implementation.md`, one section per
milestone, appended in the change that ticks the milestone here.

## Goal

Small local models routinely emit tool-call arguments with the wrong
JSON type: `{"volume": "100"}` where the schema declares `integer`.
The measurement in the issue puts the floor at one wrong call in
eight for the best model on the reference machine, and half of them
for `llama3.2:3b`. The far side then refuses the call (the device
firmware validates its own tools; a strict MCP server may too), the
model flails across rounds, and the device tools are decorative on
exactly the stack the README tells people to build. This plan adds
one bounded accommodation: an argument whose string form converts
losslessly and unambiguously to the declared type is converted at
the dispatch boundary, once, for every tool source alike. Anything
lossy or ambiguous is left exactly as the model sent it and fails
exactly as it does today.

## The issue's decisions, restated

- Coerce only where the round trip is exact: `"100"` to `100` is;
  `"one hundred"` and `"100.5"` to `integer` are not.
- When coercion does not apply, the call is refused the same way as
  today; nothing is guessed at.
- The coercion happens once, at one site, not in each tool.
- A test per declared type, with the rejected shapes beside the
  accepted ones.

## Where the facts already live

There is no server-side validation site to attach to; that is a
discovery this plan records rather than the situation the issue
assumed. The 8 ms rejection in the issue's trace was the device
firmware refusing the call over the LAN: `DeviceToolClient.call`
(`tools/device.py`) forwards `arguments` verbatim, `McpTools`
forwards them verbatim to the MCP session
(`tools/mcp/manager.py`), and the builtins guard ad hoc
(`_numbered`, `_fact_scope` in `tools/builtin.py`). So the plan
does not move an existing check; it introduces the one site.

The flow the site slots into: `_tool_loop`
(`runtime/pipeline.py:1513`) takes the tool snapshot as a local,
`tools = self._tool_snapshot()` (line 1536), collects the model's
calls, reserves them (`_reserve_tools`, line 1597), and
`_run_tools` fans out to `_run_one` (line 2125), which reads the
reserved claim back and hands it to `_dispatch` (line 2195), where
the three `ToolSource`s answer `owns`/`dispatch`. The snapshot is
the only place the declared `input_schema` exists at reply time,
and it is not visible from `_run_one` or `_dispatch` today. Device
and MCP schemas arrive through `publish()` (`tools/publish.py`),
which admits an empty dict for a non-dict schema, and a device that
sent no schema gets `{"type": "object"}` (`protocol/mcp.py`), so
the coercion function must tolerate an empty schema, a missing
`properties`, and non-dict property entries.

## Open questions, resolved

**The site is dispatch, and the record keeps the model's bytes.**
Two candidate sites exist. Rewriting `calls` in `_tool_loop` before
reservation would make every downstream reader see coerced
arguments: the conversation record, the API's `ToolInvocation`
body, and the history re-sent to the model on the next round. But
the record's column comment and the API description both promise
"What the model passed" (`conversations/schema.py`,
`config/responses.py`), both feed generated references, and the
promise is worth keeping: the model emitting a string where the
schema says integer is the very fact an operator diagnosing a
marginal model needs to see, and it is the fact #383 itself was
diagnosed from. So the coercion happens on the way out instead,
and at the one point every execution path shares: after
`_reserve_tools` has filed the originals and the working-history
append has captured them, and before `_run_tools` branches into
the move tools (`_moves`/`_move`, which never reach `_run_one`)
and ordinary dispatch, `_tool_loop` derives execution-only
`ToolCall` copies (`dataclasses.replace`, arguments run through
the coercion against the snapshot's schema for that published
name) and hands those to `_run_tools`. `_run_one` keeps its
`(call, slot)` signature, so the direct-call reload-routing pin at
`test_session_tools.py:963` stays green; at dispatch it replaces
only the reserved claim's arguments with the execution copy's
(`dataclasses.replace` on the frozen record) while the
reservation, the record, the events and the working history keep
the original. The surfaces then mean: record, API and history show
what the model said; the wire to the device, the MCP server or the
builtin, and the move tools' own reads, receive what the schema
declares. The new module's docstring states that split, because it
is the one fact a reader of either surface needs.

**One leaf module, `tools/arguments.py`, and `ToolSource` stays
four members wide.** The design guide holds the four-member
`ToolSource` up as the worked example of interface width
(`docs/architecture/design-guide.md`), so the schema does not
travel through `dispatch`. The pipeline, which owns the snapshot,
passes the mapping down its own private call chain. The coercion
itself is a pure function in a new leaf module beside `publish.py`:
`conformed(arguments, schema)` returns a new dict, never raises,
and never mutates its input. The deletion test: inlining it into
`_run_one` would put a domain rule with its own per-type test
matrix inside a method whose job is timing and dispatch, and the
rule already has a sibling precedent worth keeping beside it
(`_numbered` in `builtin.py` accepts a digit string for a fact id,
with a docstring making this issue's argument in miniature). What
callers stop having to know: how JSON Schema's type vocabulary maps
onto the string-typed habits of small quantized models.

**The lossless set, exactly: an ASCII grammar plus an equivalence
check, never a bare parser.** Python's `float()` and `int()` are
parsers with their own dialect (`"+1"`, `".5"`, `"1."`, `"1_0"`,
Unicode digits, `"inf"`), and `float()` silently rounds
(`"9007199254740993"` becomes `9007199254740992.0`) and
underflows (`"1e-4000"` becomes `0.0`), so neither defines
"lossless". The rule is stated in two halves, and both must hold.
Per top-level property whose schema entry declares a single
`type` string:

- `integer`: a `str` that, stripped, matches the ASCII grammar
  `-?[0-9]+` (compiled with `re.ASCII`) becomes its `int`
  (`"100"`, `" 100 "`, `"-7"`, and `"05"`, whose value is exact
  even though its spelling is not canonical); the conversion runs
  inside the totality guard below, which also covers Python's
  integer digit-conversion limit. A `float` whose `is_integer()`
  holds becomes its `int` (models emit `100.0`); `bool` is never
  touched (JSON `true` is not `1` declared).
- `number`: a `str` that, stripped, matches the ASCII JSON number
  grammar `-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?` is converted and
  then equivalence-checked: the result must be finite and
  `Decimal(text) == Decimal(result)` must hold, which rejects
  precision loss, overflow and underflow rather than assuming a
  finite parse was exact. An `int` already satisfies `number` and
  passes untouched.
- `boolean`: exactly the strings `"true"` and `"false"` become the
  booleans. Not `"True"`, not `"1"`, not `"yes"`: the mistake being
  undone is quoting, and anything beyond the JSON literals is
  spelling, where guessing begins.

Everything else passes through unchanged: other declared types, a
`type` given as a list (union schemas stay strict rather than
half-guessed), properties the schema does not declare, schemas with
no `properties`, and nested structure below the top level. The
bound is deliberate: device tools are flat, the measured failure is
flat, and every widening can arrive with its own evidence.

**The coercion is a vinga-owned decision, so it emits an event.**
The decision-reason guideline is the governing rule: a decision
vinga owns exposes a closed reason on the structured surface, and
the conversation record cannot substitute for it (it is a
content-class surface, its `arguments` is null under text-off, it
retains no schema, and an original string beside a success does
not prove a coercion happened; a type correction can even surface
a second constraint such as a `maximum` or an `enum`, so the
outcome alone says nothing). A new declaration,
`tool_arguments_coerced` on the session channel, one variant,
emitted where the execution copy is derived and only when it
differs from the original, carrying: the tool identified under the
same policy `tool_call` uses (`_tool_fragment`, which names
builtins in this server's vocabulary and identifies a far side's
tool without repeating its bytes), and the count of coerced
arguments. No argument values, no peer-authored property names.
The full catalog discipline lands with it: the declaration, the
baseline driver, the `CARRIED` row, `events.md` regenerated
through its generator, and the README event index row.

**Malformed calls stay malformed.** A claim whose `arguments` is
`None` (the model streamed non-JSON) is answered before dispatch
today and stays that way; coercion runs only on a dict.

## Module layout

- `tools/arguments.py` (new): `conformed(arguments, schema)`, pure,
  tolerant of every degenerate schema shape `publish()` can let
  through. Imports nothing beyond the stdlib.
- `runtime/pipeline.py`: `_tool_loop` builds
  `{tool.name: tool.input_schema}` from the snapshot it already
  takes and derives the execution-only call copies at the one
  shared point described above; `_run_tools` and `_run_one` keep
  their signatures, and `_run_one` dispatches the reserved claim
  with the execution copy's arguments. No new field on
  `PipelineRuntime`; the mapping lives and dies with the loop the
  snapshot lives and dies with.

## Tests

- `tests/unit/test_tool_arguments.py` (new): the per-type matrix,
  accepted and rejected shapes side by side, exactly as the issue
  asks: the integer, number and boolean coercions above, each
  refusal (`"one hundred"`, `"100.5"` against `integer`, `"True"`,
  `"1"` against `boolean`, `true` against `integer`, non-finite
  spellings against `number`), the grammar's own edge (parser-only
  spellings `"+1"`, `".5"`, `"1."`, `"1_0"` and a Unicode-digit
  string, all refused), the equivalence check's edge (a large
  inexact integer string such as `"9007199254740993"` and an
  underflowing exponent such as `"1e-4000"` against `number`, both
  refused), and the tolerance cases (empty schema, no
  `properties`, a property with no `type`, `type` as a list, an
  undeclared property), plus input non-mutation.
- `tests/unit/test_session_tools.py` gains the through-the-loop
  case: a device tool declared with an `integer` property (the
  fixture in `tests/support/device_tools.py` grows a typed tool
  beside `STATUS`, which has no schema), called by the scripted
  model with a string value; the assertion reads the wire payload
  the fake device received (`40`, an `int`) and the turn record's
  claim (`"40"`, the `str`), pinning both halves of the split in
  one test. A companion case drives a value coercion cannot help
  (`"a lot"`) and asserts the dispatch and refusal are exactly
  today's.
- Existing pins stay green by construction and are the negative
  proof: `tests/unit/test_tools_device.py` pins the wire below the
  site, `tests/unit/test_session_record.py` pins the record's
  `arguments`, and `tests/unit/test_providers_llm_tools.py` pins
  the decode layer, none of which this plan touches.

## Risks

- **A frozen-record replace that drifts.** `dataclasses.replace` on
  `ToolInvocation` copies every field; a future field is copied by
  construction. The session-level test pinning record-original
  versus wire-coerced is the tripwire if the replace ever moves
  before the reservation.
- **Schema shapes from the wild.** MCP servers may declare unions,
  `anyOf`, or nested objects. The function's tolerance cases pin
  that every such shape passes arguments through untouched rather
  than raising mid-reply.
- **The unit lane must stay database-free.** Everything here runs
  against the scripted session fixtures; nothing touches the
  store's behavior, only reads what it already records.

## Milestones

- [ ] **M1: the coercion, wired and tested.** `tools/arguments.py`
  with `conformed`; the snapshot mapping threaded
  `_tool_loop` to `_run_tools` to `_run_one`; the conformed claim
  dispatched with the reservation left original; the typed device
  tool fixture; the test matrix and the session-level split pin; a
  CHANGELOG entry; the implementation-doc section. Design
  footprint: one new leaf module (callers stop having to know how
  JSON Schema types map onto small-model argument habits), and
  `_run_one` deepened; `ToolSource` stays four members and no
  pass-through layer appears. Documentation footprint: no
  hand-maintained page claims arguments pass through verbatim
  (checked: `concepts.md`, `glossary.md`, `system-overview.md`
  describe the tool loop at a higher altitude), the record and API
  descriptions stay true by design, `events.md` and the README
  event index regenerate and update for the new declaration, and
  the CHANGELOG entry names both the coercion and the
  `forget`-permanence consequence recorded below.

## Plan review round

Backend codex (codex-cli 0.153.0), model `gpt-5.6-sol`, sandbox
read-only, 2026-09-04, against commit `20ab4d0e`; the reviewer ran
about 12 minutes. Verdict: ready after the P1/P2 amendments.

1. **P1: the proposed `number` conversion is not lossless.**
   `float()` converts `"9007199254740993"` to `9007199254740992.0`
   and `"1e-4000"` to `0.0`, and accepts non-JSON spellings
   (`"1_0"`, `"+1"`, `".5"`, `"1."`); `\d` admits Unicode digits.
   The plan should define an explicit ASCII numeric grammar and a
   post-conversion equivalence check rejecting precision changes,
   overflow and underflow, and the matrix must include a large
   inexact integer, an underflowing exponent, parser-only
   spellings and Unicode digits.

   *Resolution*: accepted in full. The lossless set is restated as
   an ASCII grammar plus a Decimal equivalence check for `number`
   (finite, and `Decimal(text) == Decimal(result)`), the integer
   grammar is `re.ASCII`, and the matrix gains the large inexact
   integer, the underflowing exponent, the parser-only spellings
   and the Unicode-digit case, all refused.

2. **P2: the dispatch seam misses the move tools and breaks an
   existing pin.** `_run_tools` executes move tools through
   `_moves`/`_move` before `_run_one` is reached, so coercing only
   in `_run_one` is not "once, for every source alike"; and adding
   a mapping parameter breaks the direct `_run_one(call, slot)`
   reload-routing pin at `test_session_tools.py:963`. The plan
   should derive execution-only `ToolCall` copies after reserving
   and appending the originals to working history, before
   `_run_tools` branches; `_run_one` then replaces only the
   reserved claim's arguments for dispatch, without gaining a
   schema parameter.

   *Resolution*: accepted in full; the site moves exactly there.
   Execution-only copies are derived after the reservation and the
   working-history append and before `_run_tools` branches, so the
   move tools are covered, `_run_one` keeps its `(call, slot)`
   signature, the direct-call pin stays green, and the dispatch
   replaces only the reserved claim's arguments.

3. **P2: rejecting all observability contradicts the
   decision-reason rule.** A vinga-owned decision exposes a closed
   reason on the structured surface; the conversation record is a
   content-class surface whose `arguments` is null under text-off,
   and it retains no schema, so an original string beside a
   success does not prove a coercion. The rationale that coercion
   cannot produce a different failure is also false when a type
   correction exposes a second constraint (`maximum`, `enum`). Add
   metadata-only observability recording that coercion occurred
   with target type/count and no argument values or peer-authored
   names, with the catalog, driver, baseline, generated-reference
   and no-leak work named.

   *Resolution*: accepted in full; the no-event decision is
   reversed. The plan now declares `tool_arguments_coerced` with
   the `_tool_fragment` naming policy and a coerced-argument
   count, emitted where the execution copy differs, and the
   milestone names the catalog, driver, baseline row, `events.md`
   and README index work.

4. **P2: the never-raises contract is untested against the inputs
   most likely to escape and leak.** The matrix omits an arbitrary
   non-numeric string for `number`, an integer beyond Python's
   digit-conversion limit, a non-mapping `properties`, and a
   non-mapping property entry; a conversion exception escaping
   into `_run_one` interpolates its message, which for `float()`
   includes the rejected string, into a stored tool result.
   Require totality tests for those shapes including a
   secret-shaped invalid numeric value asserted absent from every
   exception-derived and retained surface.

5. **P2: the rejected through-loop test cannot observe a refusal
   with the current fake device.** `FakeDevice` answers success
   for every unscripted call, so sending `"a lot"` produces
   success, not the firmware-style refusal the test claims to pin.
   Script a strict far-side error and assert both the unchanged
   wire value and the error reaching the model.

6. **P2: the record/history half of the split is under-pinned.**
   The proposed session test reads the wire and the in-flight
   turn, not the durable record or the next round. Use the
   recording-session infrastructure, assert the completed
   `TurnRecord` carries `"40"`, and assert the second scripted
   round receives the original `ToolCall` unchanged.

7. **P2: the typed fixture proposal duplicates an existing schema
   fixture.** The exact integer-typed volume tool already exists
   as `VOLUME` in `test_tools_device.py`; move it into
   `tests/support/device_tools.py` and reuse it in both places.

8. **P2: boolean coercion crosses an irreversible-operation guard
   without a safety test.** `forget` permanently erases only when
   `permanently is True`; after the upgrade the string `"true"`
   deliberately changes from recoverable removal to permanent
   erasure. Acknowledge the upgrade-visible change and add a
   through-pipeline test proving only exact `"true"` erases
   permanently while `"false"`, `"True"` and `"1"` stay
   recoverable.

9. **P3: `conformed` promises more than the function returns.**
   Its result is not necessarily schema-conformant; name it for
   what it guarantees (`with_lossless_coercions`), and say
   "original values" rather than "the model's bytes", since the
   provider adapters already decoded the JSON.
