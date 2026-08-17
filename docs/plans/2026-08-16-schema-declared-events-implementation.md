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
