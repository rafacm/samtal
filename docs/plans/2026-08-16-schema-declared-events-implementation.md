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

The nine spread builders were read rather than guessed, and their key
sets are now parsed out of the builders by the conformance test rather
than described beside them:

| spread | always | sometimes |
| --- | --- | --- |
| `openai_asr:OpenAiAsr._echo_fields` | `outcome`, `duration_s`, `host` | `retry_ms` |
| `ota:check_version.fields` | `device`, `client`, `board`, `firmware`, `agents`, `unloaded` | `code` |
| `ota:_version_two.refusal` | `device`, `code` | |
| `pipeline:PipelineRuntime._reply.language_fields` | | `language`, `language_confidence` |
| `pipeline:provider_fields` | `stage` | `provider`, `type`, `host`, `model` |
| `pipeline:PipelineRuntime._llm_round_done.tokens` | | `input_tokens`, `output_tokens`, `first_token_ms` |
| `pipeline:PipelineRuntime._provider_failed.fields` | delegates to `provider_fields` | |
| `pipeline:PipelineRuntime._run_one.fields` | delegates to `_tool_named` | |
| `pipeline:_tool_named` | | `tool`, `entry` |
| `pipeline:PipelineRuntime._speaking_ms_field` | | `speaking_ms` |

The rule the extraction rests on, and which a planted-source test
pins: a key in the dict literal is produced on every call, a key added
by a subscript assignment is conditional, because an unconditional one
would have been written into the literal. It is ten rows for nine
events because `_run_one`'s local and the builder behind it are two
identities.

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

Four claims, in the order they build on each other.

**Every emit site maps into the registry.** Conformance is keyed by
source call. Each site is matched to the exact SET of declared variants
that could have produced it, by channel, method-derived level and
byte-exact template, and every member of that set is held to the site's
arity, its static keywords and what its spreads can produce. A set
rather than a single variant, because one call can select among shapes:
`tool_call`'s classification picks between mutually exclusive `tool`,
`entry` and neither, and `provider_fields` emits `provider` and `type`
atomically with `host` and `model` independently conditional. The
correlations are retained in the variants; the flattening the round-4
finding warned about would have admitted shapes no call can produce.

**Every declaration is evidenced.** Declared non-base field names are
asserted EQUAL to the names the sites produce, not merely contained in
them, so a surplus declared field cannot sit unused. Token sets resolve
to the function or constant that closes them, through five modes
(a constant, a function's returns, a keyword at a call, a positional
argument at a call, and a pydantic `Literal` annotation for
`transport`), and the values that object can produce are compared with
the declaration.

**Every path is pinned.** The sidecar `PINNED_BY` maps each site's
stable identity to the pytest node IDs pinning it, and the walk's
identities are asserted equal to the sidecar's keys both ways.

**The registry is coherent**, and the walk sees what it claims to: six
planted-source tests cover the concatenated template, the method-derived
level, the two scopes, the per-function ordinal, both spellings of a
spread, the plain logging call that names no event, and the two
key-extraction rules.

Four mutations were observed failing and reverted, since a guard nobody
has seen fail is a guard nobody has seen:

| mutation | what failed |
| --- | --- |
| a reworded `speaking_started` template | the site's own parametrized case, and `test_every_declared_variant_is_produced_by_a_site` |
| a surplus `invented` field on a variant | the site's case, and `test_every_declared_field_is_produced_somewhere` |
| `CLOSE_REASONS` short of `error` | `test_every_token_set_matches_its_decision_site[session_closed.reason]` |
| an extra argument on `barge_in_merged` | the site's case |

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

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `2508 passed, 16 skipped`
- `uv run pytest tests/integration -q`: `55 passed`
- `uv run pytest tests/unit --collect-only -q | tail -1`: **2288
  before**, **2524 after**. The rise of 236 is exactly the new tests: 15
  descriptor-sanitization tests, 6 conversation-store pins, and 215
  conformance cases, 162 of which are the two parametrized-per-site
  families over the 81 sites and 22 the per-token-set family.
- `uv run pytest tests/integration --collect-only -q | tail -1`: **55
  before**, **55 after**.
- `git diff --stat af9e4d4 -- tests/unit/test_event_surface_pins.py tests/unit/test_server_event_pins.py`:
  empty, which is the pin suites' byte-unchanged contract.
