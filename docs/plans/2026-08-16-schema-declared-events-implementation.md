# Declare every event's schema and enforce it: implementation

Companion to
[`2026-08-16-schema-declared-events.md`](2026-08-16-schema-declared-events.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out.

## Milestone 1: the registry exists and is statically conformant

`samtal-server/samtal_server/events_schema.py` (new, 2332 lines)
declares **58 events in 99 variants**: the 57 with ordinary emit sites,
in 85 variants, plus the internal `schema_violation` recovery event M2
will emit, whose 14 variants are one per channel. It imports the
standard library and nothing else, and nothing imports it yet except
its own conformance test.

`samtal-server/tests/unit/test_event_schema_conformance.py` (new, 1423
lines) walks all **81 emit sites** and holds the declarations to them
both ways. `samtal-server/tests/unit/test_conversations_event_pins.py`
(new, 309 lines) pins the five conversation-store paths that neither
contract suite reached. Four decision sites gained an event-only
bounded copy of a far-side string, with
`samtal-server/tests/unit/test_event_descriptor_sanitization.py` (new,
518 lines) behind them.

Nine commits, in the order the milestone was built in, plus the one
that records it:

1. `9a25d69` Bound what a device says about itself
2. `e65eec1` Bound the board and firmware ota_check retains
3. `24c2409` Bound the client id ota_check retains
4. `2e4816e` Bound the client id session_open renders
5. `ffa7dc1` Name only a device the capacity refusal knows
6. `021f000` Declare every event's schema as data
7. `379eda1` Pin the conversation store's five event paths
8. `7402d2a` Tie the registry to the surface it describes
9. `a062c5b` Make a variant's field table required

### The inventory, re-verified

The AST scan confirms the plan's evidence at this branch's base
(`main@af9e4d4`): **81 emit sites, 57 distinct event names, one session
channel and 13 server channels**. The conformance test asserts all
three numbers rather than leaving them as a claim in prose, so a site
that stops being found is a failure rather than a smaller silent pass.

The nine spread builders were read rather than guessed, and what is
read is not their keys but their BRANCHES: one complete key set per
path through the builder, parsed by walking its statements. The PR #167
review's first finding is why (see the review round below); the table
records the shapes as they stand after it.

| spread | alternatives it can produce |
| --- | --- |
| `openai_asr:OpenAiAsr._echo_fields` | `outcome duration_s host`, and the same with `retry_ms` |
| `ota:check_version.fields` | the six of `device client board firmware agents unloaded`, and the same with `code` |
| `ota:_version_two.refusal` | `device code` |
| `pipeline:PipelineRuntime._reply.language_fields` | nothing, `language`, `language_confidence`, both: two independent conditions |
| `pipeline:provider_fields` | `stage`; `stage provider type`; and that with `host`, with `model`, or with both |
| `pipeline:PipelineRuntime._llm_round_done.tokens` | every subset of `input_tokens output_tokens first_token_ms`: eight |
| `pipeline:PipelineRuntime._provider_failed.fields` | delegates to `provider_fields` |
| `pipeline:PipelineRuntime._run_one.fields` | delegates to `_tool_named` |
| `pipeline:_tool_named` | `tool`, or `entry`, or neither |
| `pipeline:PipelineRuntime._speaking_ms_field` | nothing, or `speaking_ms` |

The rules the extraction rests on, each pinned by a planted-source
test: a dict literal creates the shapes, a subscript assignment adds a
key to every shape there is, a branch is the union of both sides, a
return takes its shapes out of the flow, and shapes start empty so a
builder assembled inside a conditional picks up no phantom shape from
the path where it was never built. That last rule is what makes
`provider` and `type` atomic: the early return in `provider_fields` is
a shape of its own, and no path carries one of the pair without the
other. It is ten rows for nine events because `_run_one`'s local and
the builder behind it are two identities.

Two calls reach only some of their builder's branches, because the
condition selecting the branch is the condition selecting the call:
`ota_check`'s `code` is written exactly when an activation was offered,
which is exactly the branch that emits the first of the four sentences,
and `_echo_fields` is handed a retry time by every outcome except the
skip. Those nine calls carry an inventory of their own, which may only
NARROW: the test asserts each is a subset of the builder's own
alternatives and that the calls sharing a builder cover all of them
between them.

**Decision 4 is satisfied by declaration, verified by reading rather
than assumed.** `provider_fields` carries `model` and the `tokens`
builder carries `input_tokens` and `output_tokens`; #120's M5 (PR #160)
had already done the rename, so this milestone declares those fields
and there is no rename left to do.

### The string-field provenance inventory

Every string-valued field was classified by where its value comes from,
and the kind follows the classification rather than the other way
round. The plan's rule: `ID`-with-syntax where the decision site
normalizes to a form, `DESCRIPTOR` only where it does not and the
surface deliberately retains what the far side said.

**Operator-configured or server-chosen (`IDENTIFIER`).** A name the
operator wrote in the configuration, or one this server composed from
configuration: `agent`, `agents`, `unloaded`, `from_agent`, `to_agent`,
`entry`, `owner`, `tool`, `stage`, `provider`, `type`, `host`, `model`,
`origin`, `path`, `revision`. None of these can carry a byte a device or
a far side chose: `host` is the hostname out of a configured
`base_url`, `tool` is only ever a builtin's name (a device tool's name
is the board's vocabulary and an unknown one is the model's invention,
and `_tool_named` names neither), and `path` is a configured directory.

**Server-minted or normalized (`ID`, with a per-field syntax).**
`event` (`event_name`), `session` (`session_id`), `device` (`mac`),
`code` (`activation_code`), `language` (`language`), and
`capture_pruned.sessions` (an `ID_LIST` of `session_id`). Each syntax is
a named entry in `SYNTAXES` rather than a generic bound, and the message
arguments reuse them: `ota_check`'s first argument is `reported_mac`,
the Device-Id header in the spelling the firmware sent, which only
reaches that sentence because `normalize_mac` accepted it.

**Far-side, sanitized at the decision site (`DESCRIPTOR`).** Exactly
four fields, and they are the four the plan predicted:

| field | bound | why not an `ID` |
| --- | --- | --- |
| `ota_check.board` | 64, printable | A board type is a vendor string with no form this server can hold it to, and the operator workflow leans on reading it |
| `ota_check.firmware` | 32, printable | A version string is echoed back to the firmware verbatim, so the site cannot normalize it to a version syntax without changing the reply |
| `ota_check.client` | 64, printable | The device UUID a token is signed for. Lawful values are UUIDs, but the site does not require one, and a registry claiming a tighter form than the site guarantees would turn lawful traffic into violations |
| `session_open.client` | 64, printable | The same header on the other side of the same device's arrival, and the same reason |

The bounds are restated in the registry (which imports the standard
library only) and defined at the decision sites in
`config/models.py`; the conformance test asserts the two statements
equal, so "restated" cannot become "drifted".

**Class names (`CLASS_NAME`).** `failure` and `error`, which are type
names and never messages.

**Lists against counts, read from each site rather than assumed.** The
plan asks for this explicitly because the two spellings share names.
`agents` and `unloaded` are lists everywhere they appear (`ota_check`,
`activation_complete`, `activation_pending`, `session_open`), declared
`IDENTIFIER_LIST`. `sessions` is both: `capture_pruned.sessions` is the
ids themselves, an `ID_LIST` of `session_id`, while
`conversations_pruned.sessions` and the three `drain_*` events' are
`len(...)`, so they are `COUNT`. `tools` on `mcp_connected` is a count
and never a list, which is the README's own note and is now a
registry-owned one.

**Tokens (`TOKEN`).** Sixteen `(event, field)` pairs and six argument
positions, each with a named decision site the conformance test
resolves and compares against. The
cross-module case the plan calls out is real:
`activation_not_offered.reason` is emitted in `ota.py` and produced by
`onboarding.py`'s `PendingDevices.observe`, so its entry names both.

### The four sanitization fixes

Each is its own commit, each is adversarial-input-only, and each
normalizes an **event-only copy**: what the site answers elsewhere is
untouched. `bounded_descriptor` lives in `config/models.py` beside
`normalize_mac`, which is this codebase's other answer to "a device
sent this and the server owns what it becomes"; it keeps printable
characters only, trims, and cuts to the caller's limit. Unprintables go
by class rather than by list, because the two that matter are a newline
(one retained record would become two) and a terminal escape.

1. **`ota.py`'s `reported_board` and `reported_version`** (`e65eec1`).
   Both stripped whitespace and nothing else, on an unauthenticated
   endpoint, and both reached the log twice, as payload fields and as
   sentence arguments. The bounded copies feed the four `ota_check`
   templates and the `fields` dict; the reply still echoes the reported
   version verbatim (the firmware compares it to decide whether it is up
   to date) and `DeviceFacts` still records what the device sent.
2. **`ota_check`'s `client`** (`24c2409`). The stripped but otherwise
   unbounded Client-Id header. The bounded copy is the event's alone:
   the token is still signed for the header exactly as it arrived, which
   a test asserts by verifying the issued token against the unbounded
   value. Null rather than the empty string where nothing printable
   survived.
3. **`session_open`'s client rendering** (`2e4816e`). The same header,
   bounded for the payload field and for the sentence's argument. The
   capture manifest and the conversation store are built from the header
   itself; a test opens a session with a hostile Client-Id and reads the
   manifest back off disk to prove it.
4. **`ws.py`'s capacity rejection** (`ffa7dc1`). "Past the refusal above
   the token verified against this header" holds only where there is a
   token to verify: with device auth off, `refusal_reason` returns
   before reading anything. The path now normalizes the MAC or emits
   null with the fixed `an unidentified device` argument. A real
   Device-Id reads exactly as it did before, which is why the contract
   pin does not move.

The adversarial tests follow the plan's two-class sentinel model, which
round 4 corrected: an **admissible** credential-shaped value appears in
exactly its declared field and its declared argument position, on the
record, in both shipped formats and on an attached tap, and in no other
field of any record; a **rejected** value appears nowhere at all. The
`carrying()` helper asserts the first as an exact set of
`(event, field)` pairs rather than as one field and a hope.

### The conformance test

Five claims, in the order they build on each other. The first three
sentences of the first two, and the whole of the kinds claim, are as the
PR #167 review left them; the round below records what moved and why.

**Every emit site maps into the registry, shape for shape.** Conformance
is keyed by source call, and by SET EQUALITY: the payload shapes a call
can produce and the shapes its variants admit are the same set. A
variant's admitted shapes are its required fields times every subset of
its optional ones. Equality in both directions is the point, because
containment in either alone misses half of it: a variant carrying two
mutually exclusive fields is contained in the union of everything the
spreads can say, and a call branch nothing declares is contained in
nothing at all. That is what forbids a `tool_call` variant carrying both
`tool` and `entry`, or an `llm_retry` variant carrying `provider`
without `type`.

**Every kind agrees with what produces it.** A field name and an arity
say nothing about a kind, so the producing expression is read:
`bounded_descriptor(board, BOARD_LIMIT)` is a descriptor of exactly that
length, `normalize_mac(...)` is an id in the MAC form,
`type(exc).__name__` is a class name, `len(...)` is a count, `round(x)`
is an integer the registry may call INT or COUNT, `X or None` is
nullable and `X or "a fixed word"` is not. One step through a function
the same module defines is followed, which is what makes `ws.py`'s
`_known_device` readable as a MAC or nothing. The classifier is
deliberately partial, since a bare attribute read carries no evidence at
all; what keeps the silence honest is that the number of positions it
can speak about is pinned at 72 fields and 49 arguments, so a classifier
that stopped reading fails rather than passing over an empty set.

**Every declaration is evidenced.** Declared non-base field names are
asserted EQUAL to the names the sites produce, not merely contained in
them, so a surplus declared field cannot sit unused. Token sets resolve
to the function or constant that closes them, through five modes
(a constant, a function's returns, a keyword at a call, a positional
argument at a call, and a pydantic `Literal` annotation for
`transport`), and the values that object can produce are compared with
the declaration. Each site's own literal is read besides, from its
keywords and from the arguments a builder takes its tokens from, and
held to the token set of every variant that matches it, so two variants
of one event cannot swap their singleton reasons behind a union that
does not move.

**Every path is pinned.** The sidecar `PINNED_BY` maps each site's
stable identity to the pytest node IDs pinning it, and the walk's
identities are asserted equal to the sidecar's keys both ways.

**The registry is coherent**, and the walk sees what it claims to:
planted-source tests cover the concatenated template, the
method-derived level, the two scopes, the per-function ordinal, both
spellings of a spread, the plain logging call that names no event, and
each of the five branch-walk rules.

Twelve mutations have been observed failing and reverted, since a guard
nobody has seen fail is a guard nobody has seen. Four from the original
milestone:

| mutation | what failed |
| --- | --- |
| a reworded `speaking_started` template | the site's own parametrized case, and `test_every_declared_variant_is_produced_by_a_site` |
| a surplus `invented` field on a variant | the site's case, and `test_every_declared_field_is_produced_somewhere` |
| `CLOSE_REASONS` short of `error` | `test_every_token_set_matches_its_decision_site[session_closed.reason]` |
| an extra argument on `barge_in_merged` | the site's case |

Four from the review's first finding:

| mutation | what failed |
| --- | --- |
| `tool` and `entry` on one `tool_call` variant | `test_every_emit_site_matches_declared_variants[...PipelineRuntime._run_one #1 (tool_call)]` |
| `provider` without `type` on `llm_retry` | `...[...PipelineRuntime._watchdog_stream #1 (llm_retry)]` |
| `code` on the `ota_check` branch that offers none | `...[samtal_server.ota:check_version #4 (ota_check)]` |
| two `barge_in_suppressed` reasons swapped | `test_every_token_a_site_writes_into_a_field_is_declared` on both affected sites |

And four from the second:

| mutation | what failed |
| --- | --- |
| `ota_check.board` from DESCRIPTOR to IDENTIFIER | `test_every_field_kind_agrees_with_what_produces_it[samtal_server.ota:check_version #1 (ota_check)]` |
| `FIRMWARE_BOUNDS` moved to 48 | the same test on all four `ota_check` sites, and the argument test besides |
| `activation_complete.device` given the session-id syntax | `...[samtal_server.ota:activate #1 (activation_complete)]` |
| `session_open.client` made non-nullable | `...[samtal_server.device.session:DeviceSession.run #4 (session_open)]` |

### The sidecar's shape, and a correction to the plan's arithmetic

`PINNED_BY: dict[tuple[str, str, int], tuple[str, ...]]`, keyed by
`(module, enclosing function, call ordinal within it)` and never by a
line number, with 81 entries.

The plan says the two contract files' 76 expectations plus the five
conversation paths cover the 81. **They do not, and the arithmetic hid
it**: the mapping is not one-to-one in either direction. `tool_call` is
one site with four pins, one per classification, and `barge_in` is two
sites sharing two pins, so the 76 expectations cover **73** paths, not
76. 73 plus the 5 new store pins is 78, and the remaining **three** are
MCP paths the contract suites never reached:

| path | pinned by |
| --- | --- |
| `tools.mcp:McpServerManager._run` #3 (`mcp_down`, `stopped`) | `test_tools_mcp.py::test_a_server_stopped_on_purpose_is_down_at_info_with_no_duration` |
| `tools.mcp:McpServerManager._mark_down` #2 (`mcp_down`, `call_failed`) | `test_tools_mcp.py::test_a_failed_call_drops_the_call_and_then_the_connection` |
| `tools.mcp:McpServers._refused` #1 (`mcp_reload`, refused) | `test_tools_mcp_reload.py::test_a_refused_reload_says_which_kind_of_refusal_it_was` |

All three assert the exact field set and the level, which is weaker
than a contract pin (no template, no arguments) but is not a silence.
They are named in the sidecar rather than left out, `test_every_path_names_at_least_one_pin`
refuses an entry with no pin at all, and
`test_the_two_contract_files_carry_the_pins_they_are_credited_with`
asserts the 73/5/3 split so the correction cannot quietly rot back into
the plan's 76/5. Strengthening the three to full pins is M2's to
consider under strict enforcement, and is recorded here rather than
done, since a milestone that widened its own test scope to fix the
plan's arithmetic would be harder to review than one that says so.

### Deviations from the plan

Five, all recorded rather than silent.

1. **`revision` is an `IDENTIFIER`, not an `ID`.** The plan's registry
   section lists "revision string" as an `ID` example. `build_info.revision()`
   answers with `SAMTAL_REVISION` as the operator's build set it, or with
   `git describe --always --dirty`, or with `unknown`; none of those is
   normalized to a syntax, and all of them are values the operator or
   this server chose, which is exactly what `IDENTIFIER` is for. Giving
   it an `ID` syntax would have claimed a form the site does not
   guarantee, which the plan's own rule forbids.
2. **`CLASS_NAME` gained a per-field `joined` refinement.**
   `mcp_call_dropped.error` is `_reason(exc)`, which answers with a
   single class name, or with the sorted names of an exception group
   joined by `", "`. The plan anticipates comma-joined class names (the
   round-2 review names them) and resolves them for ARGUMENTS through
   `COMPOSED`, but this is also a field. Rather than widen `CLASS_NAME`
   for every field, or invent a kind, the field and its argument declare
   `joined=True`, in the same way `ID` carries a per-field syntax and
   `DESCRIPTOR` per-field bounds. Only that one field and its argument
   position use it.
3. **The `session_id` syntax is the bounded machine form, not
   `uuid4().hex`.** Production session ids are 32 hex characters, but
   the capture and store suites drive sessions of their own naming
   (`s1`, `alpha`), and M2 runs those lanes under strict enforcement. A
   session id is never far-side bytes whoever chose it, so the syntax is
   `[0-9A-Za-z_-]{1,64}`. Declaring the tighter form would have made the
   registry describe a surface the lanes do not have.
4. **`bounded_descriptor` and the three limits live in
   `config/models.py`.** The plan lists `ota.py` and `device/session.py`
   as the conditionally modified files. Both fixes bound the same header
   from two modules, and `ws.py` cannot import `ota.py` (that direction
   is a cycle: `ota` imports `ws`), so one definition beside
   `normalize_mac`, which all three already import, is what keeps the
   bound single. `config/models.py` therefore joins the touched list. No
   `ServerConfig` field was added, so the #144 example-config pin is
   unaffected.
5. **The declarations are grouped by subsystem in the order a request
   meets them**, not in the README table's order. The plan asks for the
   table's order; the table interleaves subsystems (its session rows sit
   between the OTA row and the pipeline's), and a registry that
   interleaved them would put `session_limit` four screens from
   `session_open`. It opens on `ota_check`, as the table does, and then
   follows the device's arrival: the check-in, the handshake gate, the
   session edge, the pipeline inside it, the providers behind that, and
   the server's own lifecycle surfaces. M3 generates the reference from
   the registry, so this is the order that document will carry, and the
   README table becomes a name-and-when index whose order is its own
   business.

Two things the work turned up and did not change:

- **`ota_check`'s first message argument is the raw Device-Id header**,
  not the normalized MAC the field carries. It is safe as it stands:
  `check_version` returns 400 before this line unless `normalize_mac`
  accepted the header, so the only values that reach the sentence are
  seventeen characters of hex pairs in either separator and either case.
  It is declared as an `ID` with a `reported_mac` syntax that says
  exactly that, rather than being quietly widened to an identifier.
- **`test_event_surface_pins.py` and `test_server_event_pins.py` are
  byte-unchanged**, which `git diff --stat` against the branch base
  reports as an empty diff. The lawful values they plant pass through
  every new bound unaltered, which is the whole design of an
  adversarial-input-only fix.

### Verification

Run from `samtal-server/`, with `PYTHONDONTWRITEBYTECODE=1` for
everything outside pytest.

Re-run after the PR review round, which is where these numbers come
from; the milestone's own first run is in the round's record below.

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `2769 passed, 16 skipped in 296.92s`
- `uv run pytest tests/integration -q`: `55 passed in 157.28s`
- `uv run pytest tests/unit --collect-only -q | tail -1`: **2288
  before**, **2785 after**. The rise of 497 is exactly the new tests: 15
  descriptor-sanitization tests, 6 conversation-store pins, and 476
  conformance cases. Four of the conformance families are parametrized
  per site, which is 324 of them over the 81 sites; 22 more are the
  per-token-set family and 9 the lawful-configured-name ones.
- `uv run pytest tests/integration --collect-only -q | tail -1`: **55
  before**, **55 after**.
- `git diff --stat af9e4d4 -- tests/unit/test_event_surface_pins.py tests/unit/test_server_event_pins.py`:
  empty, which is the pin suites' byte-unchanged contract, and still
  empty after the review round's three commits.

### PR review round

The milestone's own verification, before the round below, was
`All checks passed!`, `2508 passed, 16 skipped` on the unit lane, `55
passed` on the integration lane, and a collected count of 2524 against
2288 at the branch base. The section above carries the numbers after
the round, which is the state this branch is in.

External review of PR #167 (diff `main...31ada64`), 2026-08-17. Three
findings, two P1 and one P2, all valid and all accepted. As received,
condensed but faithful, each with its resolution:

1. **P1: spread correlations are flattened, so impossible variants
   count as produced.** The conformance reduced each spread to
   `always | sometimes` and considered a variant produced by channel,
   level and template alone, so a `tool_call` variant carrying both
   `tool` and `entry`, or an `ota_check` non-activation variant
   carrying `code`, would pass. Token checking unioned values across
   variants, so branch-specific token sets could swap undetected.
   Extract the exact correlated alternatives per source call, compare
   them for set equality with the complete variants, and add the named
   mutations.

   *Resolution*: accepted, `e0af8bf`. Each builder is read as a list of
   ALTERNATIVES, one complete key set per path through it, by walking
   its statements rather than collecting its keys, and the comparison
   with the registry is set equality between what a call can produce
   and what its variants admit. The two calls that reach only some of
   their builder's branches are inventoried per call, and an override
   may only narrow. Each site's own token literals are read from its
   keywords and from the arguments a builder takes its tokens from. The
   four named mutations are in the table above.

2. **P1: field and argument kinds are not tied to their producers.**
   The per-site check covered field names and arity only, and coherence
   asked merely that an ID name some syntax and a DESCRIPTOR carry some
   bounds, so flipping `ota_check.board` to IDENTIFIER, changing
   nullability, or substituting a wrong syntax or bound all passed.

   *Resolution*: accepted, `2866a97`. The producing expression is read
   and the declaration is held to it, for the kind, the nullability, an
   ID's syntax and a DESCRIPTOR's bounds, and for every argument
   position besides. The classifier is partial by design and its reach
   is pinned at 72 fields and 49 arguments so the silence cannot
   spread. The four named mutations are in the table above.

3. **P2: valid configured names fall outside the declared composed
   grammars.** `NonBlankStr` admits any stripped non-empty string,
   quotes, control characters and unbounded length included, and the
   emitters interpolate those names; the grammars rejected all three,
   so an agent called `secondary"agent` would become a schema violation
   the moment M2 enforces, mangling lawful traffic under forgiving
   mode.

   *Resolution*: accepted, with the decision recorded rather than
   improvised, `e48d138`. The registry must describe the surface that
   exists, so the identifier kind and the composed grammars now carry
   the configuration's own domain. Where a fragment still needs a bound
   to mean anything it is bounded by STRUCTURE, the quoting or the
   parenthesized tail, never by a character class or a length nobody
   promised. Three lawful names, quoted, control-bearing and overlong,
   are asserted through the configuration type first and then through
   every affected grammar, and a rule test forbids any grammar over
   configured names from claiming a length or a character class again.
   Reverting the patterns fails all four of those tests.

   The narrowing belongs at configuration semantics, where a refusal
   reaches the operator who can fix it rather than a log line nobody
   asked for, so the follow-up below is filed to propose it there.

#### Follow-up to file

Not filed from this worktree, which has no GitHub access. The body
below is written unwrapped so it can be pasted into an issue without
its sentences shattering, since GitHub renders an issue body with the
`breaks` extension.

Title: **Give configured names a bounded safe-name rule, at configuration semantics**

```markdown
`NonBlankStr`, the type behind an agent name, a provider entry name, a provider type and several more, is `StringConstraints(strip_whitespace=True, min_length=1)`: any non-empty string once stripped. It admits quotes, control characters, newlines and any length at all.

That domain reaches further than the configuration file. These names are interpolated into the event surface's sentences and carried in its fields (`agent`, `agents`, `provider`, `type`, `entry`, `from_agent`, `to_agent`, `origin`), and #155's registry had to widen its grammars to match, because a registry claiming a tighter domain than configuration guarantees would turn a lawful deployment's every `session_open` into a schema violation the moment enforcement lands (PR #167 review, finding 3).

Widening was the right call there: the registry describes the surface that exists. But the surface it describes is wider than anybody wants. An agent called `secondary"agent` renders a sentence whose quoting means nothing; one carrying a newline splits a retained record in two; one four thousand characters long is a log line nobody can read. None of those is a leak, and all of them are avoidable.

**Proposal.** A `SafeName` type beside `NonBlankStr`, applied to the names that reach the event surface:

- non-empty once stripped, as now;
- printable throughout (`str.isprintable()`), which is the rule `bounded_descriptor` already applies to device-reported values and for the same two reasons: a newline splits one retained record into two, and a terminal escape paints an operator's screen;
- a stated maximum length, so a log line has a bound;
- refused at parse time with a sentence naming the key and the rule, like every other configuration refusal.

**Which keys.** At least the ones the events interpolate: `agents` keys, `providers.<stage>` keys, `ProviderConfig.type`, the provider references in `agent_defaults` and `AgentConfig`, and `prompt_fragments` keys. MCP entry names already have a tighter rule (`check_mcp_entry_names`, `[A-Za-z0-9_-]+`), which is the precedent for this and the reason `from_entry` is the one fragment whose looseness is a floor rather than the whole truth.

**Migration.** A name a running deployment already uses and the new rule refuses is a boot refusal, so this is a breaking change: it needs its changelog entry, a way to find offending keys before an upgrade, and a decision on whether the first release warns rather than refuses.

**Then the registry tightens from this side.** `samtal_server/events_schema.py`'s composed grammars (`also_bound_to`, `agent_list`, `quoted_tool_name`, `from_entry`, `quoted_provider`, `reaching_host`, `origin_provenance`) and its `IDENTIFIER` domain are bounded by structure alone today, with a comment pointing here. Once configuration guarantees more, they can claim more, and `test_no_composed_grammar_claims_a_length_or_a_character_class` in `tests/unit/test_event_schema_conformance.py` is the test that would then be relaxed deliberately rather than by accident.
```

  empty, which is the pin suites' byte-unchanged contract.

## Milestone 2: the emitters enforce

`samtal-server/samtal_server/events.py` grew a 590-line enforcement
section between the dispatch and the two emitters, and both `_emit`
paths now go through it. Nothing else about the emitters moved: the
taps, the ordering, the deep copies and the two clocks are exactly what
M1 left.

Eight commits, in the order the milestone was built in, plus the one
that records it (hashes as rebased onto merged main after PR #167):

1. `bca36b6` Activate an agent in the stub runtime
2. `d6ec9b1` Judge every emission against its declaration
3. `83b5ab5` Resolve the enforcement mode where a server is built
4. `10dfc40` Give the mechanics suite a registry of its own
5. `655f216` Drive every violation class through both modes
6. `b3dd7f4` Plant a credential through every diagnostic branch
7. `932ba9a` Run the entrypoint's mode resolution for real
8. `a34a627` Follow the registry's own domain for a name

### What the enforcement does, in the order it does it

Two ordered steps, and the order is the whole of the anti-spoofing
argument. The caller's `**fields` are compared with the emitter's base
keys BEFORE the merge, so a session caller supplying `event`, `session`
or `device` is a `base_key_collision` rather than a key that replaced
the emitter's identity and typechecked; the merge then lets the base win
whatever the caller passed. A server emitter's base is `event` alone, so
`session` and `device` stay ordinary declarable fields there.

The finished payload is then matched WHOLE. Variant selection uses
registry-owned dimensions in one fixed order, the emitter's channel,
then the declared templates, then the level, and answers which dimension
failed where nothing survives, which is what lets a refusal say
`wrong_channel` or `wrong_template` without naming anything the caller
supplied. Among the survivors, each variant is scored by its full fault
list (arguments by position, then declared fields in declaration order,
then the count of what was not declared at all) and the fewest-fault
candidate is what the diagnostics describe. Everything is an explicit
condition that raises; there is no `assert` statement in the section,
and a subprocess under `-O` proves it.

Kinds are held as M1 declared them: `bool` is rejected for the numeric
kinds and checked FIRST, since `True` is an `int` to `isinstance`;
floats must be finite; an `int` satisfies `FLOAT`; `DESCRIPTOR` is
re-bounded here whatever its decision site did; `ID` and `ID_LIST` are
matched against the named syntax; `SOURCES` keys are matched against the
know-how grammar, so `memory` fails like any unknown prefix.

### The forgiving algorithm, and the sentence that always goes

Forgiving mode selects the variant the same way, drops the caller's
message and arguments (always, see below), rebuilds the payload field by
field against the variant keeping only what validates, and then holds the
REBUILT PAYLOAD to that variant's field table again. That final check is
what gates dispatch: a rebuild that leaves a required field missing
becomes the declared `schema_violation` event, built fresh from the fixed
token, the emitter's own base identity, the fixed sentence and no
arguments. So does an undeclared event, an event that is not a name, an
unknown template, an unknown channel, a wrong level and an ambiguous
selection. What passes the gate rides as its own event, carrying its
declared fields, `SAFE_MESSAGE` and no arguments.

**Deviation 1, corrected by the PR #169 review.** The milestone first
read the plan's "ANY invalid emission has its message and args replaced
by the fixed safe sentence" as "any emission still invalid AFTER the
rebuild", on the argument that a successful rebuild implies every
retained field and every argument validated, so the surviving sentence
could only render lawful values. That argument is wrong, and the review
found the case that breaks it: the two halves are not independent. One
credential can be an undeclared field key, that field's value, AND a
perfectly lawful `IDENTIFIER` or `PATHLIKE` argument of the same call.
The payload half dropped it for being undeclared while the sentence half
rendered it for being a valid identifier, so one emission both refused
the value and printed it. The plan's literal rule stands: EVERY invalid
emission loses the caller's message and arguments.

What that would have made redundant, on the original reading, is
answered by splitting the final check rather than by softening the rule.
The gate asks about the variant's FIELD table alone, because recovery's
own sentence is recovery's rendering and not a template the caller
chose. So the plan's three named conditions stay distinct (a required
field missing, an ambiguous selection, a template the registry does not
know), the rebuild is not dead code, and the plan's risk section still
gets what it asked for: a field only production reaches is a complaint
line and a slightly quieter event, never an outage.

Behind all of it sits one `try`, wrapping candidate selection,
validation and rebuild alike, so a bug in this module degrades an
emission rather than raising on a reply path. It does not degrade the
caller's payload, because the caller's payload is precisely what could
not be judged: it builds the replacement fresh and reports the failure
by exception class name alone. Strict has no guard, deliberately: a lane
has no reply to lose, and a swallowed bug there is a bug nobody finds.

### What a diagnostic may say

One vocabulary serves both modes: `EventSchemaError`'s single `args`
entry and the forgiving complaint's two logged arguments are the same
two halves, a label and a summary, so the surfaces cannot drift apart.
The label is the declared event name or the fixed `an undeclared event`;
the summary is `"; ".join` of fixed codes with registry-owned details
(a declared field name, a count, an argument position). Sixteen codes,
all module constants.

The complaint is a plain `log.error` on the emitter's own channel, not
an event, for the reason a failed tap's report is not one: a complaint
that went back through validation could recurse. ERROR rather than
WARNING because `log_level` admits roots above WARNING; a test asserts
its survival under a root of ERROR, and a root of CRITICAL suppresses it
along with every other ERROR-class diagnostic, which is that operator's
choice.

### The mode, and where it is resolved

`SAMTAL_EVENTS_ENFORCEMENT`, a module flag with `set_enforcement` under
it and `resolve_enforcement` over it. The module default is strict.
`create_app` resolves first, before anything that can emit, because a
production process may import it and serve under an ASGI runner without
reaching `main()`; `main()` resolves too, after `load_dotenv` and after
the `config` and `conversations` early exits, inside the same `try` that
prints every other boot refusal to stderr and exits 1. Unset means
forgiving at both. Anything else refuses, naming the variable and the
two values and never echoing the rejected spelling, which a test with a
credential-shaped value pins.

`tests/conftest.py` SETS the variable rather than defaulting it, unlike
the two secrets beside it: the module default already makes the lanes
strict, and what the line buys is a lane that stays strict when it
builds an app, which an ambient `forgiving` on a CI runner would
otherwise relax. The Dockerfile's `ENV` block gains
`SAMTAL_EVENTS_ENFORCEMENT=forgiving`, redundant with the resolver's own
default on purpose.

### The seam

`events.declared_events()` and `set_declared_events()` are the injectable
module state; `tests/support/schema.py`'s `scratch_registry` is the
test-scoped context manager over them. `test_events.py` installs a
scratch registry through an autouse fixture, budgeted for everything it
emits: the four synthetic names, and `heard`, `ota_check` and
`capture_enabled` in the shapes that suite gives them. Declaring them
turned out to be a second reading of the file rather than a formality:
two shapes only a line-by-line read finds are in there (a
session-channel `one` whose whole template is `a`, and an `ota_check`
emitted without its `device` field), and a permissive stub registry
would have hidden both. An emission this suite adds and does not declare
now fails rather than passing quietly.

The two contract pin files are byte-unchanged, which `git diff --stat`
against the branch base reports as an empty diff.

### What strict enforcement found

**No declaration was wrong.** The whole unit lane and the whole
integration lane run every production emission through the validator,
and the registry M1 declared accepted all of them unaltered. That is the
milestone's core proof and it needed no fixes to the registry, no
changes to an emit site, and no weakened pin.

**Deviation 2: one test double was wrong, and it is fixed rather than
declared around.** Three `test_boundary_contract.py` cases failed on
`session_open` with `not_nullable (agent)`. `StubRuntime`, the boundary
suite's stand-in conversation runtime, never wrote `events.agent`, so
the edge emitted `session_open` with a null agent and rendered `None`
into the sentence. The real runtime's constructor activates the first
agent (which is why the edge stopped doing it by hand), and a device
with no agent is turned away before the session opens, so no production
path can produce that shape. Declaring `agent` nullable would have made
the registry describe a surface this server does not have. The stub
gained the one line the real runtime already had, and the contract
test's assertion moved with it: it now says the value is the runtime's
own choice rather than anything the edge wrote. This is a test-support
change, not an emit-site change, and neither pin suite moved.

### The test matrix, as it came out

`tests/unit/test_event_enforcement.py` (81 cases) drives every violation
class through both modes against a scratch registry of its own, keyed to
this suite rather than to production events so that the matrix does not
change whenever the surface does, on real channels so the recovery
event's own declaration covers them. Undeclared event, undeclared field,
wrong kind, boolean-for-number, null in a non-nullable field, unlisted
token, missing required field, wrong level, wrong channel, wrong
template, wrong arity, wrong argument kind, base-key collision per key,
bad list element, bad `ID_LIST` element, every allowed `SOURCES` form,
an empty and a populated mapping, `memory` and two other bad keys, a bad
provenance value, the two argument-only kinds (`PATHLIKE` as a `Path`
and as a `str`, `COMPOSED` accepted in the four shapes its structure
admits and refused in the four it does not), a configured name in four
exotic but lawful spellings and three blank ones, the complaint's
survival under an ERROR root, the multi-fault single complaint, the
guard, and the two internal producers of `schema_violation` told apart
by what they say.

`tests/unit/test_event_enforcement_sentinels.py` (28 cases) is the
no-leak half. One credential-shaped spelling, dotted so that it
satisfies no `ID` syntax and no token set while remaining an ordinary
printable string, drives all NINE distinct diagnostic branches in both
modes: wrong-kind field value, undeclared event name, undeclared spread
key, the message itself, a wrong-kind template argument, an unlisted
token, a malformed `ID`, a bad list element, a bad provenance key. Each
strict case asserts `EventSchemaError.args` by equality (and `str` and
`repr`, which are built from it) and that nothing was written or
dispatched at all; each forgiving case asserts `record.msg` and
`record.args` by equality, then hunts the sentinel through both shipped
log formats, the arguments behind them, `Emission.args` and an attached
tap. Beside the nine: one wholly hostile call (key, value, message and
arguments together) through the ordinary forgiving path with nothing
injected, and the guard matrix, a validator raising a sentinel-bearing
exception crossed with five hostile shapes. The descriptor proofs are
the two-class model round 4 settled: an admissible credential-shaped
board appears in exactly `("checked", "board")` and its declared
argument position, on the record, in both formats and on the tap, and a
rejected one (a newline inside it) appears nowhere at all.

`tests/unit/test_event_enforcement_mode.py` (21 cases) covers the
resolver in process and the entrypoint in subprocesses: unset,
`forgiving`, `strict`, an unknown value refusing with its message on
stderr and exit code 1, a `.env` file carrying the mode, a real variable
still beating that file, `config --help` unblocked by an unusable value,
and the `-O` proof (`__debug__` false in the child, and an invalid
strict-mode emission still refused).

### Deviations from the plan

Four, all recorded rather than silent, and the first is a deviation
withdrawn rather than one taken.

1. **None on the forgiving rule, in the end.** The milestone read "any
   invalid emission has its message and args replaced" as "any emission
   still invalid after the rebuild"; the PR #169 review showed the
   aliasing case that breaks the reasoning, and the plan's literal rule
   is what shipped. What the reading was trying to protect (the rebuild
   not being dead code, the plan's three final-check conditions staying
   distinct) is answered by the final gate asking about the variant's
   field table alone. Recorded rather than quietly rewritten, because
   the wrong reading was in this document for a round and somebody may
   have read it.
2. **A test double changed, and one assertion with it**
   (`tests/support/boundary.py`, `test_boundary_contract.py`). Strict
   enforcement refused a shape no production path produces; the double
   was what produced it. No emit site, declaration or pin moved.
3. **The complaint and the exception share one sentence.** The plan
   describes them separately (a fixed complaint, a raising exception).
   They are built from one `REFUSAL_MESSAGE` template and one label and
   summary pair here, the complaint logging the two halves unrendered
   and the exception carrying them rendered, so the two surfaces cannot
   say different things about the same violation and a test can assert
   both exactly.
4. **A configured name is validated as configuration defines it, which
   is a rebase-time reconciliation rather than a choice** (`a34a627`).
   M1's PR #167 review (finding 3, commit `3d67940` "Claim of a
   configured name only what config claims") established that the registry may not claim a tighter
   domain than configuration semantics guarantee: `NonBlankStr` is
   `StringConstraints(strip_whitespace=True, min_length=1)` and nothing
   else, so an agent called `secondary"agent`, one carrying a control
   character and one four thousand characters long are all lawful
   configuration today. `IDENTIFIER_LIMIT` was removed with the
   character-class and length claims in the composed grammars, and a
   rule test (`test_no_composed_grammar_claims_a_length_or_a_character_class`)
   keeps that honest. Rebasing M2 onto it was conflict-free in the text
   and semantically conflicting underneath: the enforcement had been
   written against the narrower registry, so the lane failed collection
   with 65 `ImportError`s. Reconciled by following the registry:
   `_identifier_fault` now asks only what `NonBlankStr` asks (a string,
   non-empty once stripped), which the list kinds inherit, and the
   `PATHLIKE` argument check drops its printability claim for the same
   reason, a configured directory being the operator's word too. Every
   other kind keeps its exact checks: `DESCRIPTOR` bounds, `ID` and
   `ID_LIST` syntaxes, `CLASS_NAME`, token sets, the numeric rules and
   the `SOURCES` grammar are untouched, because none of them describes a
   configured name. On the test side, the composed-fragment case that
   expected a newline to be refused flipped to expecting it accepted,
   beside a quoted and an overlong fragment, and `IDENTIFIER` gained
   both halves of its new domain explicitly: four exotic-but-lawful
   names pass, three blank ones do not. **One diagnostic branch was
   removed**, `test_no_declared_grammar_admits_a_control_character`,
   which asserted over every registered grammar exactly the claim #167
   deleted; the conformance suite's rule test now owns that ground from
   the correct side, as a prohibition on making the claim rather than an
   assertion that it holds. Tightening returns through #168, at
   configuration semantics, where a refusal reaches the operator who can
   fix it rather than a log line nobody asked for.

One thing the work turned up and did not change: the emitters keep
validating on the `vad`/`dropped` side channels' behalf by not touching
them at all. They are outside the tap contract and outside the registry,
and they remain outside the enforcement, which is what keeps validation
per decision rather than per frame.

### Verification

Run from `samtal-server/`, with `PYTHONDONTWRITEBYTECODE=1` for
everything outside pytest.

Re-run after the rebase onto merged main (PR #167) and again after the
review round below, which is what the numbers here are.

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `2901 passed, 16 skipped`
- `uv run pytest tests/integration -q`: `55 passed`
- `uv run pytest tests/unit --collect-only -q | tail -1`: **2785
  before**, **2917 after**. The rise of 132 is the new tests: 81
  enforcement cases, 28 sentinel cases, 21 mode cases, and the round's
  two registry-coherence additions to the conformance suite.
- `uv run pytest tests/integration --collect-only -q | tail -1`: **55
  before**, **55 after**.
- `git diff --stat origin/main -- tests/unit/test_event_surface_pins.py tests/unit/test_server_event_pins.py`:
  empty. Both contract suites pass unmodified under strict enforcement,
  which is the milestone's core proof.

### PR review round

External review of PR #169, 2026-08-17. Four findings, two P1 and two
P2, all valid and all accepted. As received, condensed but faithful,
each with its resolution and the commit that carries it:

1. **P1: forgiving recovery can leak rejected input through a
   separately valid argument.** Recovery kept the caller's message and
   arguments whenever the arguments validated on their own, so a
   credential used both as an undeclared field key or value AND as a
   lawful `IDENTIFIER` or `PATHLIKE` argument was dropped from the
   payload and rendered into the log and every tap.

   *Resolution*: accepted (`4396bcf`), and it overturns this section's
   first deviation, corrected above. The two halves are not independent,
   so the plan's literal rule stands: every invalid emission loses the
   caller's message and arguments. The final gate now asks about the
   variant's FIELD table alone, since recovery's own sentence is
   recovery's rendering rather than a template the caller chose, which
   keeps the rebuild meaningful without keeping the leak. An emission
   that passes rides as its own event with `SAFE_MESSAGE` and no
   arguments; the recovery event keeps its own declared sentence, so
   two outcomes read differently in the retained log. The aliasing case
   is planted in both suites and reverting the return line fails both.

2. **P1: the last-resort guard can itself raise and cost the reply.**
   The guard's handler called `log.error` outside any guard, and
   `_offer`'s tap-failure report had the same shape, which is worse: the
   tap that failed is usually the log tap, so the report goes back onto
   the channel that just threw.

   *Resolution*: accepted (`6e7a794`). Every report this module makes
   from inside a guard now goes through one helper that suppresses
   whatever the channel does. A logging call is not inert: `handle` and
   `filter` are called unwrapped and a formatter meets whatever the
   record carries, all of it code somebody else installed. Suppressing
   blind is right here and only here, because what is protected is a
   reply and what is lost is one diagnostic line about a diagnostic
   line. Four tests with a handler that raises where `logging` does not
   catch it (`emit` is swallowed by `handleError`, `handle` is not)
   cover the refusal report, the guard's report, the tap report and the
   helper alone; removing the suppression fails all four.

3. **P2: strict enforcement accepts ambiguous full-variant matches.**
   The matcher scored candidates and took the fewest-faults one, so two
   variants an emission satisfied completely were resolved by
   declaration order; and the conformance suite compared variants by
   `(channel, level, message)`, so a duplicated declaration stayed
   green.

   *Resolution*: accepted (`de096a7`). Matching is exactly one full
   match; more than one is a violation with its own fixed code, strict
   raising and forgiving demoting to `schema_violation` since there is
   nothing to rebuild towards. The declaration is refused where it can
   be fixed too: the conformance suite now compares the payload SHAPES
   two variants admit, within each group the emitter selects by, and a
   planted registry proves the check sees both spellings (the same
   variant twice, and one whose optional field makes its shapes a
   superset of another's). The real registry is already clean, so this
   is a guard rather than a fix. The four events with several variants
   behind one template (`tool_call`, `llm_round`, `llm_retry`,
   `provider_failed`) stay lawful precisely because their shapes are
   disjoint.

4. **P2: a non-string event bypasses the safe error mapping.**
   `registry.get(event)` ran before anything asked whether the event was
   a name, so an unhashable one raised a raw `TypeError` out of the
   validator: in strict mode not the error a caller is told to expect,
   and in forgiving mode a fall through to the last-resort guard, which
   saved the reply but reported a broken validator rather than a bad
   call.

   *Resolution*: accepted (`10e3c9c`). The type is checked before the
   lookup and reported as an ordinary fault through the base field's own
   name, never by rendering the value: a caller that passed a list
   passed its contents too. Six cases, hashable and not, plus a sentinel
   in the event slot asserted absent from every record and every tap.
