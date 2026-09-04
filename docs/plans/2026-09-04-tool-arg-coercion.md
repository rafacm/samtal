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
diagnosed from. So the coercion happens on the way out instead:
`_tool_loop` builds a published-name-to-schema mapping from the
snapshot it already holds, threads it through `_run_tools` into
`_run_one`, and `_run_one` dispatches a claim carrying the
conformed arguments (`dataclasses.replace` on the frozen record)
while the reservation, the record, the events and the working
history keep the original. The surfaces then mean: record, API and
history show what the model said; the wire to the device, the MCP
server or the builtin receives what the schema declares. The new
module's docstring states that split, because it is the one fact a
reader of either surface needs.

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

**The lossless set, exactly.** Per top-level property whose schema
entry declares a single `type` string:

- `integer`: a `str` that, stripped, matches `-?\d+` becomes its
  `int` (`"100"`, `" 100 "`, `"-7"`, and `"05"`, whose value is
  exact even though its spelling is not canonical); a `float` whose
  `is_integer()` holds becomes its `int` (models emit `100.0`);
  `bool` is never touched (JSON `true` is not `1` declared).
- `number`: a `str` that parses as a finite `float` becomes it
  (`"1.5"`, `"100"`); `nan`/`inf` spellings are refused as not
  being JSON numbers at all. An `int` already satisfies `number`
  and passes untouched.
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

**No new event, and the reason recorded.** A coercion never turns
a failure into a different failure; it turns a refusal the model
cannot act on into the call the model meant, deterministically.
The fact that the model needed it stays fully visible where the
operator already looks: the conversation record's `arguments`
carries the original string. An event variant would buy a second
copy of that fact at the price of the whole catalog surface
(declaration, driver, baseline row, regenerated `events.md`). If
field experience later shows the record is not enough, the event
can arrive with that evidence.

**Malformed calls stay malformed.** A claim whose `arguments` is
`None` (the model streamed non-JSON) is answered before dispatch
today and stays that way; coercion runs only on a dict.

## Module layout

- `tools/arguments.py` (new): `conformed(arguments, schema)`, pure,
  tolerant of every degenerate schema shape `publish()` can let
  through. Imports nothing beyond the stdlib.
- `runtime/pipeline.py`: `_tool_loop` builds
  `{tool.name: tool.input_schema}` from the snapshot it already
  takes; `_run_tools` and `_run_one` carry the mapping; `_run_one`
  dispatches the conformed claim. No new field on
  `PipelineRuntime`; the mapping lives and dies with the loop the
  snapshot lives and dies with.

## Tests

- `tests/unit/test_tool_arguments.py` (new): the per-type matrix,
  accepted and rejected shapes side by side, exactly as the issue
  asks: the integer, number and boolean coercions above, each
  refusal (`"one hundred"`, `"100.5"` against `integer`, `"True"`,
  `"1"` against `boolean`, `true` against `integer`, non-finite
  spellings against `number`), and the tolerance cases (empty
  schema, no `properties`, a property with no `type`, `type` as a
  list, an undeclared property), plus input non-mutation.
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
  descriptions stay true by design, and no generated reference is
  staled because no event, schema or API shape changes; CHANGELOG
  only.
