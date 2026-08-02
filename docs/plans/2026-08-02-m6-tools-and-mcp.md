# M6 Tools and MCP: implementation plan

This file details milestone M6 of the
[samtal-server v1 plan](2026-08-02-samtal-server-v1.md). It was agreed in
conversation on 2026-08-02 and is written so a fresh session can implement
it from the repository alone. Implementation notes do not go in a companion
to this file: they go in the v1 plan's own
[implementation doc](2026-08-02-samtal-server-v1-implementation.md), as a
new M6 section appended in the same change that ticks the milestone in the
v1 plan's checklist.

Scope from the v1 plan: server-side MCP servers per agent plus the
device's own MCP tools, merged into one tool list and round-tripped
through LLM tool calling. Accept: a simulator conversation triggers a
mock MCP tool and the reply reflects its result. This milestone is also
what makes the root README's "Tools via MCP, on both sides" feature line
true.

## State at the start of M6

M0 to M5 are merged (PRs #1 to #4, #6, and #7), `main` is clean, no open
PRs. Per-device agent resolution, per-agent personas, and the explicit
active agent all work. What M6 builds on:

- `session.py` parses `McpMessage` but logs it as "ignored until M6";
  `_activate_agent` already enforces the device's bound agent list and
  raises `AgentNotAllowed` for anything else, with a docstring pointing
  at M6's switch_agent tool.
- `LlmProvider.stream(system, turns)` yields text deltas only; the
  session speaks sentences as they complete and keeps history in
  `_turns`, a flat list of text `Turn`s.
- Both the real firmware and xiaozhi-sdk advertise `features: {"mcp":
  true}` in the device hello; the device side of MCP is JSON-RPC 2.0
  inside `{"type": "mcp", "payload": ...}` messages (upstream
  `docs/mcp-protocol.md`: `initialize`, then paginated `tools/list`,
  then `tools/call`; device-initiated `notifications/*` carry no id).

## Decisions already made (do not reopen)

Agreed with Rafael before this plan was written:

- **The session owns the tool loop; providers stay translators.**
  `stream` grows a `tools` parameter and yields typed events (text
  delta or tool call); the session executes tools, appends results, and
  calls `stream` again. The loop, caps, timeouts, and logging then live
  once, and switch_agent is even possible: after a switch the next LLM
  call goes to a different provider instance, which a provider-internal
  loop could never do.
- **History stays text-only; tool exchanges are ephemeral.** `_turns`
  keeps plain user/assistant text as today. The structured tool-call
  turns exist only in a working copy inside one reply; what survives is
  the text that was actually spoken. The model can therefore not recall
  an earlier reply's raw tool results, only what it said aloud, which
  is acceptable for a voice assistant and keeps history portable across
  providers and agents.
- **On a successful switch, the new agent greets.** The old agent's
  stream ends at the tool call; the session activates the new agent and
  starts a fresh LLM turn as it, so the greeting arrives in the new
  prompt and the new voice. That is what the M5 receptionist scenario
  wants: choose the poet, hear the poet. A refused switch
  (`AgentNotAllowed`) becomes an error tool result, and the current
  agent phrases the refusal in its own voice and language.
- **History carries over on a switch.** The new agent sees the whole
  conversation so far, which is what makes "switch to the tutor and
  explain what we just discussed" and the receptionist handoff work.
  Since history is text-only, nothing provider-specific leaks across.
- **The device hears silence while a tool runs, bounded by a timeout.**
  No synthetic filler: any preamble the model streams before the tool
  call ("Let me check.") is already spoken sentence by sentence, and
  the example prompts encourage that. A configured filler sentence
  would live in one fixed language, the reply-language trap again.
- **One tool namespace, structural prefixes, collisions impossible by
  construction.** Builtins (`switch_agent`, `remember`) are bare;
  server MCP tools are prefixed with their config entry name
  (`ha__turn_on_light`); device tools keep upstream's `self.` prefix,
  sanitized for the LLM APIs. Validation forbids entry names that
  collide with builtins or `self`, so no merge-time collision handling
  exists to get wrong.
- **MCP servers are named top-level entries; agents reference them.** A
  top-level `mcp_servers` section mirrors how providers are named
  entries; agents carry `mcp: [names]`, `agent_defaults` too, with
  replace-not-merge override semantics, consistent with the stage
  fields. Secrets in `env`/`headers` use a `$VAR` convention resolved
  from the server's environment at boot.
- **A tool failure or timeout becomes an error tool result.** The model
  explains in its own words, voice, and language; a canned apology
  would be fixed-language and would throw away whatever the model could
  still salvage.
- **A dead MCP server does not fail the boot.** Startup connects with a
  timeout and logs a warning for any server that fails; its tools are
  simply absent, and a session for an agent referencing a down server
  kicks off a background reconnect. Configuration errors (unknown
  names, malformed entries, unset `$VAR`) still fail the boot the way
  bad provider config does; only runtime liveness is forgiven.
- **Memory is keyed per agent, not per agent and device.** The persona
  is one entity across rooms: "remember I'm vegetarian" told in the
  kitchen holds in the bedroom. Separating people on a shared device is
  v3's voiceprint problem; keying by device now would fragment memory
  without solving it.
- **Memory reaches the model by injection, not a recall tool.**
  `remember(text)` appends to the agent's file; the file is injected
  into the system prompt each reply. For small bounded memory this is
  the standard pattern (retrieval-on-demand is for memory too large to
  inject), it costs zero lookup latency where a recall round trip is
  spoken silence, and it does not rely on a small local model deciding
  to call recall. The cost is one prompt-cache miss per remembered
  fact, which is rare. This deviates from the M5 handover's
  "remember/recall tools" wording deliberately. **Revisit at v2/v3**:
  when memory outgrows the prompt (real conversation store, users),
  the shape becomes two-tier, a small injected core plus a search
  tool, not recall-only.
- **switch_agent is offered only when the device is bound to more than
  one agent.** A single-bound device has nowhere to switch, so it gets
  no dead tool. Multi-bound devices get the tool with the full allowed
  list in its schema, which is how "who can I talk to?" is answered.
- **CI exercises a real stdio MCP server.** Tests ship a tiny MCP
  server spawned as a subprocess over stdio: no network, deterministic,
  and the actual client transport code is what runs. Device tools ride
  xiaozhi-sdk's `set_mcp_tool`; the mock LLM gains a scripted tool
  call so the acceptance conversation is fully deterministic.

## Design

### 1. Provider interface: tools and typed events

`providers/base.py` gains the neutral tool model, shared by session,
tools package, and providers:

```python
@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict  # JSON Schema, as MCP already speaks

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict

@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False

@dataclass(frozen=True)
class TextDelta:
    text: str

LlmEvent = TextDelta | ToolCall
```

`Turn` grows two optional fields with empty defaults, so every existing
construction site stays valid: `tool_calls: tuple[ToolCall, ...] = ()`
(assistant turns that requested tools) and `tool_results:
tuple[ToolResult, ...] = ()` (a turn with role `"tool"`). Persistent
history never contains either; they appear only in the working list
inside one reply.

The stream signature becomes:

```python
def stream(
    self,
    system: str,
    turns: Sequence[Turn],
    tools: Sequence[ToolDef] = (),
    tool_choice: Literal["auto", "none"] = "auto",
) -> AsyncIterator[LlmEvent]: ...
```

yielding `TextDelta` where it yielded `str`. `tool_choice="none"` keeps
the tool definitions in the request but forbids calling them; both APIs
support it natively, and the session uses it on the final allowed round
so a reply always ends in speech. All three LLM providers (anthropic,
openai_compatible, mock) change in step; there is no compatibility
shim.

Wire mapping, per provider:

- **Anthropic**: `tools` entries as `{name, description, input_schema}`,
  `tool_choice` as `{"type": "auto"|"none"}`. Text deltas from the
  SDK stream as today; tool calls read from the final message's
  `tool_use` blocks after the stream ends. An assistant turn with
  `tool_calls` renders as content blocks (text, then `tool_use`); a
  `tool` turn renders as a user message of `tool_result` blocks with
  `is_error` carried through.
- **OpenAI-compatible**: `tools` entries as `{"type": "function",
  "function": {...}}`, `tool_choice` as `"auto"|"none"`. Streamed
  `tool_calls` deltas are accumulated by index (id, name, argument
  fragments) and yielded as `ToolCall`s when the stream ends; a `tool`
  turn renders as `{"role": "tool", "tool_call_id", "content"}`.
  Malformed argument JSON from the model becomes an error tool result
  rather than an exception.
- **Mock** (`providers/mock.py`): new options `tool_when` (substring of
  the last user turn that triggers a call), `tool_name`, and
  `tool_arguments` (a mapping option, added to `OptionsReader`). When
  triggered and no tool result is in the turns yet, the mock yields one
  `ToolCall`; otherwise it formats its reply template, which gains a
  `{tool_result}` placeholder alongside `{text}` and `{system}`.
  Backward compatible: without the new options it behaves as today.

### 2. The tool loop in the session

`_speak_reply` becomes the loop. Per reply: snapshot the tool list
(section 6), copy `_turns` into a working list, then repeat up to
`MAX_TOOL_ROUNDS = 4` times: stream events, speak `TextDelta` sentences
exactly as today, collect `ToolCall`s. If the stream ends with no tool
calls, done. Otherwise append the assistant turn (spoken text plus
`tool_calls`), execute the calls (concurrently, since device and server
tools are independent), append a `tool` turn with the results, and
stream again. The last permitted round passes `tool_choice="none"`.
The `spoken` bookkeeping is unchanged: what was said aloud is what
lands in `_turns` as the assistant turn, and the closing `tts stop` is
sent in `_reply`'s `finally` as today, so aborts and failures still
release the device.

Every execution path produces a `ToolResult`, never an exception into
the loop: unknown tool name, transport error, `isError` from the far
side, and timeout all become `is_error=True` results whose text names
the problem, and the model phrases what to tell the user. Per-call
timeout: the server entry's `tool_timeout_s` (default 15) for server
tools, the same default as a module constant for device tools and
builtins.

**switch_agent** executes inside the loop but ends it: on success the
session records the text spoken so far as the old agent's assistant
turn in `_turns`, calls `_activate_agent(name)` (whose enforcement and
`AgentNotAllowed` refusal exist since M5), then starts a fresh stream
as the new agent with a new tool snapshot, a new TTS resampler, and the
carried-over `_turns` plus one **ephemeral** user turn, not recorded in
history, telling the model it was just switched in and should greet.
The ephemeral turn keeps the message list ending on a user turn, which
both APIs require for a fresh completion; injecting it as a fake user
utterance into `_turns` would falsify the transcript. The new agent's
speech is recorded as its own assistant turn. A refused switch stays in
the old agent's loop as an error tool result carrying the
`AgentNotAllowed` message, which already lists the allowed agents. At
most one switch is honoured per reply; a second `switch_agent` in the
same reply gets an error result, so two agents cannot ping-pong.

The tool definition is built per session from `self._agents`:

```json
{"name": "switch_agent",
 "description": "Switch this conversation to another assistant. Available: ...",
 "input_schema": {"properties": {"agent": {"enum": ["poet", "tutor"]}}}}
```

The enum plus description carry the device's full bound list, which is
what lets the active agent answer "who can I talk to?" without any
extra mechanism.

### 3. The device MCP channel

New `tools/device.py`: a per-session `DeviceToolClient` owning the
JSON-RPC conversation with the device. The session creates it after the
hello when the device hello carried `features.mcp: true`, and routes
every parsed `McpMessage` to it, replacing the "ignored until M6"
no-op.

- **Handshake**, as a background task so the conversation never blocks
  on it: send `initialize` (with a `capabilities.vision` stanza of
  empty strings: xiaozhi-sdk indexes `params.capabilities.vision.url`
  unconditionally and crashes without it; the real firmware treats
  vision as optional, and samtal has no vision endpoint, a v1
  non-goal), then the `notifications/initialized` notification per the
  MCP spec, then `tools/list`, following `nextCursor` pagination until
  exhausted. Request ids from a session counter; responses matched to
  pending futures by id. Each request gets a 10 s timeout; on timeout
  or error the client logs a warning and the device contributes no
  tools, which is also the natural behaviour for devices that never
  answer (the sdk silently ignores `tools/call` for unknown tool
  names, so calls need the timeout too).
- **Name sanitization**: device tool names contain dots
  (`self.audio_speaker.set_volume`), which the LLM APIs' `^[a-zA-Z0-9_-]+$`
  name rule forbids. Every character outside that set becomes `_`, a
  reverse map routes calls back to the original name, and a sanitized
  name that collides with one already mapped is dropped with a warning
  (deterministic: first listed wins). Names longer than 64 characters
  after sanitization are dropped with a warning too, since both APIs
  cap tool names there.
- **Calls**: `tools/call` with the original name, the result's text
  content items joined as the `ToolResult` text (non-text items noted
  as unsupported), `isError` carried through. Device `notifications/*`
  (no id) are logged at debug and not replied to.

Protocol support lands in `protocol/messages.py` or a sibling: builders
for the three requests and the notification, and a parser for response
payloads (result/error, tools list pages), unit-tested against the
shapes in upstream's `mcp-protocol.md`.

### 4. Server MCP: configuration and manager

Config (`config/models.py`), with `config.example.yaml` updated in the
same change per repository rule:

```yaml
mcp_servers:
  ha:
    transport: stdio            # stdio | streamable_http
    command: mcp-proxy
    args: ["http://ha.local/mcp_server/sse"]
    env:
      API_ACCESS_TOKEN: $HA_TOKEN    # resolved from the server's env
    tool_timeout_s: 15               # optional, default 15
  weather:
    transport: streamable_http
    url: http://localhost:8000/mcp
    headers:
      Authorization: $WEATHER_TOKEN

agent_defaults:
  mcp: [weather]
agents:
  home:
    prompt: ...
    mcp: [ha, weather]   # replaces the default list, like the stage fields
```

- `McpServerConfig`: `transport` selects which fields are required
  (`command`/`args`/`env` for stdio, `url`/`headers` for
  streamable_http); naming a field of the other transport is an error.
  Entry names must match `^[A-Za-z0-9_-]+$` (they become tool-name
  prefixes) and must not be `self` or any builtin tool name, which is
  what makes namespace collisions unrepresentable.
- **`$VAR` values** in `env` and `headers` are resolved from the
  server's environment at boot; an unset variable fails the boot,
  naming the entry, exactly like `api_key_env`. Keys matching the M1
  secret fragments (`token`, `api_key`, ...) **must** use the `$VAR`
  form; a literal value there is rejected by the same guard that
  rejects inline secrets today. Literal values for non-secret keys
  pass through unchanged; a value that must start with a literal `$`
  is not supported, and the config reference says so.
- `AgentDefaults` gains `mcp: list[NonBlankStr] | None = None` (`None`
  means inherit; an agent naming `mcp: []` explicitly opts out).
  Cross-reference validation checks each layer's own list where it is
  written, like the stage fields since M5.

New `tools/mcp.py`: an `McpServerManager` per referenced entry (only
entries some agent references are built, like providers), running on
the official `mcp` Python SDK, which becomes a **core dependency** (it
is pure-Python and light; the GPL rule is untouched). Lifecycle:

- **Startup**, from the FastAPI lifespan: all managers connect
  concurrently, each with a 10 s connect timeout, then list their
  tools. Success logs the tool names; failure logs a warning naming
  the entry and the server is marked down. The boot proceeds either
  way: config errors fail the boot, liveness does not.
- **Reconnect**: when a session opens, any down server referenced by
  any of the device's bound agents gets a background reconnect task
  (skipped if one is already running), so a server that came back is
  picked up without a restart. A transport error during a call marks
  the server down (after producing its error tool result) so the same
  path revives it.
- **Shutdown**: managers close cleanly via the lifespan's exit
  (`AsyncExitStack`), so stdio child processes do not outlive the
  server.

### 5. Builtins and memory

New `tools/builtin.py` (definitions and executors for `switch_agent`
and `remember`) and `tools/memory.py` (the store). Config:

```yaml
memory:
  dir: ./memory
```

Optional top-level section; absent means no memory: no `remember`
tool, no injection. Present, `dir` is required, created on first
write. One file per agent, `<agent>.md` (agent-name characters outside
`[A-Za-z0-9_-]` become `_` in the filename), holding one `- fact` line
per remembered item.

- `remember(text)` appends a line and answers a short confirmation
  result. Appends go through a per-agent `asyncio.Lock` and an atomic
  write (temp file, rename), since two sessions can share an agent.
- **Injection**: each reply reads the active agent's file (small by
  construction) and appends it to the system prompt under a fixed
  heading ("You remember these facts about past conversations:").
  Reading per reply means a fact remembered in one session is known to
  a concurrent one on its next reply.
- **Cap**: 8 KiB or 200 lines per file, whichever trips first; an
  append that overflows drops oldest lines. The cap is what keeps
  injection cheap and is why no recall/search tool exists in v1 (see
  the decision above, revisit at v2/v3).

### 6. Assembling the tool list

Per reply (and re-done after a switch), the session snapshots:

1. builtins: `switch_agent` when the device is bound to more than one
   agent; `remember` when memory is configured;
2. the device's sanitized tools, when the device advertised MCP and
   discovery has completed (a first utterance racing discovery simply
   runs without device tools);
3. for each entry in the active agent's effective `mcp` list, the
   server's tools as `<entry>__<original name>`, skipping servers
   currently down.

Execution routes by the same structure: builtins by name, `self_`-view
names through the device client's reverse map, prefixed names to their
manager with the prefix stripped. The session logs each call and
result (name, duration, is_error) at info level, the M7 structured
logging groundwork.

### 7. Tests

Unit (no network, as today):

- protocol: MCP envelope builders and response parsing, pagination,
  the vision stanza in `initialize`;
- name sanitization and reverse mapping, including collision and
  length drops;
- config: `mcp_servers` shapes, transport-specific field validation,
  `$VAR` resolution and the secret-key enforcement, reserved entry
  names, `mcp` list inheritance and explicit `[]` override, `memory`
  section;
- provider wire mapping: turns-with-tools to Anthropic blocks and
  OpenAI messages as pure functions, argument-fragment accumulation,
  malformed-argument handling, `tool_choice` pass-through;
- memory store: append, cap truncation, filename sanitization,
  concurrent appends;
- session loop against scripted fake providers: rounds cap with
  `tool_choice="none"` on the last round, error results for unknown
  tools and timeouts, switch success (provider swap, ephemeral turn
  not in `_turns`), switch refusal, one-switch-per-reply.

Integration (xiaozhi-sdk against a running server, mock providers,
still keyless and network-free):

- **the milestone acceptance**: a conversation whose mock LLM is
  scripted to call a tool on the stdio mock MCP server
  (`tests/support/mcp_stdio_server.py`, official `mcp` SDK, launched
  via `sys.executable` so CI needs nothing installed), with the reply
  template quoting `{tool_result}`; the received reply reflects the
  tool's answer;
- device tools: a simulator registering a custom tool via
  `set_mcp_tool`, the scripted LLM calling it through the sanitized
  name, the reply reflecting its result (this also proves the
  initialize/tools-list handshake against the sdk's implementation);
- switch_agent: a device bound to two agents; the first agent's mock
  LLM triggers the switch; the post-switch audio carries the second
  agent's `tone_hz` and its reply text derives from the second agent's
  own prompt (`{system}` marker), the M5 assertions reused across a
  switch. Also the refusal: a scripted switch to an unbound name is
  answered by the first agent, whose reply quotes the error result;
- memory: one conversation remembers a fact (file content asserted),
  a second conversation's reply proves injection via the `{system}`
  placeholder carrying the remembered line;
- resilience: a config referencing a nonexistent stdio command boots,
  warns, and holds a normal conversation without tools; a tool that
  sleeps past a short configured `tool_timeout_s` produces a spoken
  reply (error result path) and a clean `tts stop`.

Local lane (opt-in, `SAMTAL_LOCAL_LANE=1`, never CI): one conversation
where a real local model calls the same stdio mock server ("ask the
tool for the secret word") through Ollama. The pre-flight check must
name a tool-capable model, since not every Ollama model supports tool
calling; keep the model configurable in the test's pre-flight the way
M4's lane names its requirements.

No device checkpoint is required for M6 by the v1 plan. The board is
on the desk, and asking the assistant to lower its own volume (a real
`self.audio_speaker.set_volume` call) is the natural test if one is
run; record it in the implementation notes either way.

### 8. Documentation

- `config.example.yaml`: `mcp_servers`, an agent `mcp` list, and the
  `memory` section, in the same change as the schema (repository
  rule).
- `CHANGELOG.md` under the date of the change, Keep a Changelog
  sections.
- `samtal-server/README.md`: a tools section (attaching MCP servers,
  device tools, memory, the `$VAR` convention).
- The root README's MCP feature line needs no edit; M6 is what makes
  it true.

## Commit breakdown

Small commits, one logical change each, imperative titles of roughly 50
characters with bodies explaining what and why:

1. MCP JSON-RPC protocol builders and parsers, unit tests
2. Neutral tool model and event-yielding `stream` signature; mock LLM
   scripted tool calls; unit tests
3. Anthropic provider tool calling, unit tests
4. OpenAI-compatible provider tool calling, unit tests
5. Config: `mcp_servers`, agent `mcp` lists, `memory` section, `$VAR`
   resolution, validation; `config.example.yaml`; unit tests
6. `tools/mcp.py` server manager (lifecycle, reconnect, timeouts),
   stdio test server in `tests/support`, unit tests
7. `tools/device.py` device tool client and session MCP routing, unit
   tests
8. Memory store, `remember` builtin, prompt injection, unit tests
9. Session tool loop with switch_agent and the greeting flow, unit
   tests
10. Integration tests (acceptance, device tools, switch, memory,
    resilience)
11. Local lane tool-calling test
12. `CHANGELOG.md` and README updates
13. Plan checklist tick and implementation doc M6 section (rides in
    the PR that completes the milestone)

## Process constraints

- Branch `feature/tools-and-mcp` off `main`; verify `main` is current
  first (`git pull --rebase`). Never commit code to `main`.
- Run from `samtal-server/`: `uv run pytest tests/unit -q`,
  `uv run pytest tests/integration -q`, `uv run ruff check .` before
  every push. CI runs the same.
- Open the PR when done (title per the repository convention:
  imperative verb plus deliverables, never a bare "M6:" prefix; body
  with a Verification task list, boxes checked only for steps actually
  carried out, and no hard-wrapped lines in the PR body). **Do not
  merge it.**
- The implementation doc section records deviations from this plan, or
  states explicitly that there were none.
- No em-dashes anywhere: docs, commit messages, code comments.
