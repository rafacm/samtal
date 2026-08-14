# Give agents know-how: per-server guidance, shared fragments, MCP instructions

## Goal

Implement issue #122: an agent's know-how today has exactly one home,
the flat `prompt` string, and that shape cannot express knowledge that
belongs to a capability (how to use an MCP server's tools) or knowledge
shared across agents (household facts, spoken-style rules) without
copying it into every persona prompt and letting the copies drift. This
plan gives the `mcp_servers` entry an `instructions` field injected
beside its tools for every granted agent, adds a `prompt_fragments`
domain section composed per agent through `prompt_includes`, consumes
the guidance a connected MCP server ships about itself behind a
per-entry opt-in, and makes the assembled system prompt inspectable, so
an operator can see what the model actually receives.

The companion implementation doc,
[`2026-08-14-agent-know-how-implementation.md`](2026-08-14-agent-know-how-implementation.md),
records what each milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by issue #122 and not re-litigated here:

1. **No skill subsystem.** No on-demand loading, no samtal-specific
   instruction packaging format. If prompts outgrow the context budget,
   the answer is the two-tier shape #83 is establishing for memory
   (small injected core plus a lookup tool), reused rather than
   invented here.
2. **Assembly happens at session open and at agent switch, never
   mid-turn.** The voice latency budget rules out fetch-on-demand: a
   "load the instructions" round trip mid-turn is dead air through the
   speaker.
3. **All of it lives in the domain database**, edited through the CLI
   and REST API in the #101 shape: write-time validation, generated
   docs, sanitized refusals.
4. **The scope is the three deliverables.** Per-server `instructions`
   on `mcp_servers` entries; named shared fragments with
   `agent_defaults` participation including the `[]` opt-out; the MCP
   server's own shipped guidance surfaced behind a per-entry opt-in.
   Resources wait until non-text tool results do (the #121 gap 6
   revisit condition).

## The issue's open questions, resolved

### Assembly order: one fixed, documented order

The assembled system prompt is, in order: the persona prompt, the
fragments in `prompt_includes` order, the per-server guidance in grant
order (for each entry, the operator's `instructions`, then the
server's shipped instructions, then its named prompts in
`inject_prompts` order, each where opted in), and the remembered
facts last
under the existing memory heading. Blocks are separated by blank
lines. Fragments are injected verbatim with no added heading: they are
prompt text the operator wrote, and a heading would editorialize.
Each entry's guidance sits under a one-line heading naming the entry's
tool prefix, so the model ties the guidance to the prefixed tool names
it can call. Memory keeps its existing heading and its existing tail
position.

The reasons, in the same order. Identity comes first because the
persona defines who is speaking and everything after it is read in
that voice. Shared fragments come next because they are standing
context the persona speaks within. Capability guidance comes third,
grouped per entry, because it is about the tools rather than about the
speaker. Memory stays last because it is the most dynamic block, it is
what today's behavior appends last (so the refactor is provably a
reordering of nothing), and #83 is about to restructure memory's
operator surface, which the tail position leaves free to move.

There is deliberately no configurable order: one documented,
deterministic ordering beats a configurable one, and when #83's
precedence question lands it composes against a fixed base rather than
against a per-deployment permutation.

**When assembly runs, stated exactly.** The prompt has two halves
with two clocks, and the split is what lets the issue's decision hold
without breaking a contract today's code documents. The know-how half
(persona, fragments, per-server guidance) is assembled once in
`_activate_agent`, at session open and again at agent switch, which
is the decision's own timing verbatim, and cached on the runtime for
the life of that activation; nothing about it is recomputed per
reply, and nothing is ever fetched at assembly time (connect,
`initialize`, the prompt fetches and reload all happened before).
The memory block keeps the clock it already has: read on every LLM
round and appended to the cached half, because that read predates
this issue, its per-reply freshness is a documented contract in
today's `with_memory` ("a fact remembered in one session is known to
a concurrent one on its next reply"), and its restructuring belongs
to #83, not here. The one change to that read is where it runs, not
when (finding 16 below): it moves off the event loop through
`asyncio.to_thread`, resolved before the round's stream request is
built, and the assembler stays a pure function handed the text.

The consequence for a reload is stated rather than hidden: guidance
applied by a reload reaches new sessions and switched-in agents, not
the replies of a session already running, while the tool list keeps
its #121 per-reply pickup. A live session can therefore briefly hold
yesterday's guidance beside today's tools, and that mismatch is
accepted the way the issue itself accepts guidance about withheld
tools: initial noise, bounded by session length (minutes), gone at
the next activation. The alternative, re-assembling guidance per
reply, was round one's design and is retired: it re-reads the
settled decision's explicit timing, and the second review round was
right that nothing in the deliverables needs it.

This design changes no existing timing at all. The cached half for a
configuration with no guidance and no fragments is exactly the
persona string `AgentProviders.prompt` holds today, and the memory
append per round is unchanged, so the assembler's output is pinned
byte-equal to today's `with_memory` output per invocation with no
caveat. An integration test holds one session across a memory write,
an MCP reload, a reconnect and an agent switch, asserting the memory
write visible on the next reply (as today), the reload's guidance
invisible until the switch, and the switch re-assembling.

The assembler lives in `samtal_server/runtime/prompt.py`: prompt
assembly fails the "would this exist if the backend were a telephone
call" test, so it is runtime code, and `builtin.with_memory` folds
into it rather than surviving as a second place where prompt text is
glued.

### Budget pressure: counted on the inspection surface, not the MCP one

Every injected block competes with memory for the prompt budget on
small local models, and the issue asks for the sizes to be visible
somewhere. The answer is the assembled-prompt surface, which lands
with the first injection milestone and grows a provenance with each
later one, so no injected block ever ships before the surface that
counts it:
it reports each block with its provenance and its size in characters,
plus the total, which is the number an operator tunes against. It is
not the MCP status surface, although the issue names it as a
candidate: that surface reports MCP runtime state, fragments are not
MCP, and a prompt question ("what is the model receiving and how big
is it") deserves one answer in one place rather than half an answer on
a surface about connections. Beside the surface, agent activation
logs a `prompt_assembled` event carrying the know-how half's
per-source character counts, which is the decision-site rule applied
to prompt size: when a small model degrades in the field, the
retained logs say what the prompt held without anyone reproducing
the session. Memory is deliberately outside the event: the event
fires where the know-how half is assembled, `_activate_agent`, which
is synchronous, while memory is read per round off the event loop,
and emitting per round would double the round's log volume for a
number that moves slowly when `llm_round` already carries per-round
token counts. The inspection surface, which reads memory fresh,
is where memory's size is answered.

There is no automatic trimming and no budget enforcement: the
deployment's operator is the one who knows what their model tolerates,
and a server that silently drops instruction blocks is worse than one
that visibly reports what it injected. That is the two-tier fallback's
job if it is ever needed, per the issue's settled decision.

### Trust boundary: two server channels, both behind per-entry opt-ins

Deliverable 3 is resolved through both channels a server can ship
guidance in, each behind its own explicit opt-in on the entry:

- **`use_server_instructions`** (default false) injects
  `InitializeResult.instructions`, the specification's field
  "describing how to use the server and its features". It is captured
  during the `initialize` call every manager already makes, so
  consuming it costs no extra round trip.
- **`inject_prompts`** (default unset, meaning none) names the
  server-published prompts to inject, by prompt name. Selection is
  operator-explicit rather than wholesale, because the specification
  defines prompts as user-controlled templates and a server may
  publish dozens: the operator, who read the server's documentation,
  names the ones that are standing guidance rather than invocable
  templates. Discovery is listing-first, so no skip decision ever
  rests on interpreting an untrusted server error: when the field
  names anything, the manager walks the full paginated
  `prompts/list`, cursor by cursor, and validates every configured
  name against the listing before any fetch. Each way a name can be
  unusable is a distinct, sanitized skip warning naming the entry,
  the name's position in `inject_prompts` counted from one, and the
  rule, never the configured value: an MCP prompt name is a
  server-chosen identifier the operator copies, so nothing bounds
  what it holds, and a warning that printed it could carry a pasted
  credential or a terminal escape. The rules: the server does not advertise
  the prompts capability at all (one warning for the entry, every
  name skipped), the name is absent from the listing, or the listed
  prompt declares required arguments (a template cannot be rendered
  without them). Only names the listing proves eligible are fetched
  with `prompts/get` and no arguments. Prompts are refetched on
  every reconnect, like the tool list, and `inject_prompts`
  participates in connection identity (below): editing it changes
  what the connect fetches, so it restarts the connection, unlike
  the two fields that configure only injection. The blocks are
  injected in the order the operator listed them, after the entry's
  other guidance.

What stays out, with its revisit conditions documented where the
decision is: prompt templates with arguments become worth consuming
when samtal grows a user-invocable surface for them, and resources
wait until non-text tool results land (#121 gap 6 names that
condition already).

Operator-written guidance is trusted; a third party's published
instructions and prompts are that server steering the agent, so the
operator opts in explicitly, per entry and per channel, and the
generated reference says exactly that on both fields. Two bounds
apply to every server-shipped block, whichever channel it came in
on:

- **A size cap.** A server-shipped block longer than a fixed cap
  (4000 characters) is skipped wholesale with a warning naming the
  entry, the channel and the size, never truncated: a truncated
  instruction block is half an instruction nobody reviewed, and an
  unbounded one is a third party filling the prompt budget.
- **The publishing rule for logs.** The shipped bytes never appear in
  a log record, and neither does a configured prompt name; the
  connect log and the skip warnings carry the entry name, the
  `inject_prompts` position where one applies, and the size only.
  The configured name itself appears only where operator-written
  configuration is already echoed write-shaped: entity reads and the
  inspection response body, both JSON-encoded, with the CLI's
  full-block sanitizer covering provenance strings as well as text.

**How a prompt renders, exactly.** A `prompts/get` result is an
ordered list of messages with roles and typed content blocks, and
what this feature injects is one system-guidance block, so the
rendering is defined rather than left to an implementation: the text
of each message, in message order, joined by blank lines, roles
dropped. The block is standing guidance the operator chose to
inject, not a dialog to replay, and a prompt that only makes sense
as a dialog is a template this feature is not for. A prompt any of
whose messages carries a non-text content block is skipped as
unusable, the same visible rule as required arguments; the size cap
applies to the final rendered block. Each injected prompt carries
the stable provenance `server_prompt:<entry>:<position>`, built from
the entry name and the name's position in `inject_prompts`, both
safe to print by construction where the configured name is not; the
inspection response body sets the configured name beside the token
as data, where reads already echo what the operator wrote. The
rendering itself is pinned exactly in milestone 3's tests, including
a multi-message prompt.

**Prompt retrieval cannot take the tools down.** The tool connection
is the entry's load-bearing half, and optional guidance must not be
able to cost it: inside the `CONNECT_TIMEOUT_S` envelope a raised
exception marks the manager down and removes every tool, so prompt
discovery and fetching run after the envelope has closed and the
tools are published, in the same task, before the manager settles
into its wait. Two layers of bound apply, because per-call bounds
alone do not bound the phase: each listing page and each
`prompts/get` runs under its own short per-call bound, and the whole
discovery phase runs under one aggregate deadline equal to
`CONNECT_TIMEOUT_S`, with a fixed page cap on the listing walk as
the backstop against a repeating cursor. A per-call failure or
timeout skips that prompt (or, when the listing itself fails, all
configured names); reaching the aggregate deadline or the page cap
skips everything remaining, with one warning carrying the entry, the
`inject_prompts` positions concerned and the reason token. The
connection, the published tools and the already-captured blocks stay
up throughout. The envelope arithmetic is stated because #121 pinned
it: a manager start is now bounded by one connect timeout plus one
discovery deadline plus small change (about 20 s), and the reload's
whole envelope grows by the same one deadline, staying comfortably
inside the CLI's 60 s reload read timeout. Milestone 3 tests a
stalled prompt, a repeating pagination cursor, a listing whose pages
exceed the cap, and elapsed boot and reload completion against the
stated envelope, asserting the tools survive every case. The bytes reach exactly two
  places, both deliberate: the model's system prompt, which is what
  the opt-in means, and the assembled-prompt surface, which exists to
  show what the model receives and marks the block's provenance so an
  operator can see whose words they are reading. The CLI renders
  blocks through a sanitizer of their own (below), so a hostile
  server cannot drive a terminal through an inspection command.

### Guidance and per-tool grants: the effective grant is the condition

Per-server guidance does not narrow with a grant's allow list; it is
whole-entry, as the issue expects ("probably fine to ignore at
first"), and the field's generated description says that guidance
describing a withheld tool is noise the operator avoids by writing
guidance about the granted surface. The injection condition is the
effective grant and nothing else, which is deliverable 1 read
literally ("injected into the system prompt of any agent granted
that server"): operator instructions are injected for every granted
agent, whether the server is connected and whatever its filtered
tool count, and captured server-shipped blocks are injected for a
granted server whenever a capture exists, tools or none, which is
what keeps a prompt-only MCP server (deliverable 3's own case) from
being silently excluded. An `mcp: []` agent never sees any of it.
Guidance for a server that is down, or whose granted tools were all
filtered away, is the same accepted noise as guidance naming a
withheld tool: the issue tolerates it initially, the status and
inspection surfaces make it visible, and tying injection to the
mutable per-reply tool offer was rejected in review round two
because it both violates the grant-edge deliverable and cannot
coexist with activation-time assembly.

## The smaller decisions, decided

### An instructions edit does not restart the connection

The reload's diff keeps a manager when its entry fragment and stored
secrets are unchanged. `instructions` and `use_server_instructions`
are excluded from that comparison: they configure prompt text the
connection never sees, so restarting a live connection (dropping
mid-call tools, respawning a stdio child) to apply a guidance typo fix
is churn without cause. `inject_prompts` is deliberately not excluded:
editing it changes what the connect fetches from the server, so it
restarts the connection, which is the honest cost of a new fetch. An instructions-only edit therefore applies on
reload as `unchanged` in the reload report, which is honest about the
connection, and the new guidance is visible on the assembled-prompt
surface and in the next reply. The README and the reload response
documentation say that `unchanged` speaks about the connection, not
the entry's text. `MCP_RELOAD_NOTICE` is already what entry writes
answer, so the write path needs no new sentence.

### Fragments follow the boot-time snapshot, not the reload

`prompt_fragments` and `prompt_includes` apply at the next server
start. The reload's contract is a named, bounded slice (the MCP
entries, their secrets, the grant lists), and #121 already decided
that agent writes keep the restart sentence because an agent fragment
mixes reloadable and non-reloadable fields; `prompt_includes` is one
more non-reloadable field on that row, and fragment bodies sit beside
the personas they compose with. The issue's verification ("after the
restart or reload path applies") names restart as sufficient. So:
fragment writes and deletes answer `RESTART_NOTICE`, everything
MCP-shaped reloads, everything else restarts, and the boundary stays
one sentence long.

### The new shapes

**`mcp_servers.<name>.instructions`** (milestone 1): an optional
string on the entry, operator-written guidance injected for every
granted agent. Ordinary domain field: stored in a nullable
`instructions` column, written through the existing generic
`set mcp-server` CLI and REST routes with no route changes, shown by
reads (it is not a secret), covered by the generated reference.

**`prompt_fragments`** (milestone 2): a new domain section mapping
fragment names to a `PromptFragmentConfig` entity with a single
verbatim `text` field, not to bare strings: the store's entity
parsing, the API's `Envelope.entity` and the CLI's fragment handling
all require mapping-shaped entities, so the fragment travels the
ordinary path as `{text: ...}`, in the PUT body, the read envelope
and the CLI fragment alike, and milestone 2 pins the exact CLI input
and API read representation in tests. The one-field mapping also
leaves room for a later per-fragment field without a shape change,
the same reason grants took their object form. Names match the MCP entry-name
pattern (`[A-Za-z0-9_-]+`): they appear in refusals, logs and the
inspection surface, so the same safe-charset rule applies, enforced
at parse time. The refusal sentence is not borrowed with the rule:
`check_mcp_entry_names` interpolates the rejected name, and a string
that fails the charset is exactly the string that must not be
echoed, so an invalid fragment name is refused naming the section
and the rule only, and a valid name is the only kind any samtal
surface ever prints. Bodies have no
server-imposed length cap; the counting surface is the guard, and the
operator is trusted with their own prompt budget.

Fragment bodies and the `instructions` field share one type
decision, because both are promised verbatim: not `NonBlankStr`,
which strips surrounding whitespace and would silently alter
deliberate leading indentation and trailing newlines, but a plain
string whose validator checks a stripped copy for non-blankness and
returns the original untouched. Byte-exact preservation is pinned
through the store, the API, the CLI and the assembled prompt, with a
body that carries leading indentation and trailing blank lines. A new
`prompt_fragments` table (name primary key, text) with its own
Alembic migration; `DOMAIN_DESCRIPTIONS`/`DOMAIN_KEYS` gain the
section; store, views, API routes (`GET/PUT/DELETE
/prompt-fragments[/{name}]`), CLI verbs (`config set prompt-fragment`,
`show`, `delete`, and the `list`/`show config` composites), docgen and
the committed reference all learn it in the same milestone.

**`prompt_includes`** (milestone 2): an optional list of fragment
names on `agents.<name>` and on `agent_defaults`, with exactly the
`mcp` field's semantics: unset inherits, a list replaces rather than
extends, `[]` opts out. Duplicate names in one list are refused by
position; a name that matches no fragment is refused at write time
and at boot by `check_references` (unlike a grant's tool allow list,
the referent is in the same database, so there is no reason to defer
the check to a live connection). Both refusals follow the rule the
#121 round fixed for grants, not the older reference sentences that
quote the unresolved value: an unresolved include is reported by the
layer, the list position and the rule, never by what was written,
because a rejected fragment may hold a pasted credential and the
sentence travels out as a CLI line, an HTTP 422 body and a boot log.
The safe charset does not close this on its own (a credential can
match `[A-Za-z0-9_-]+`), so the sentinel tests below assert the
value's absence from HTTP responses, CLI stdout and stderr, every
log record and the whole exception chain. A nullable
`prompt_includes` JSON column on both layer tables, in the same
migration.

**`mcp_servers.<name>.use_server_instructions`** and
**`mcp_servers.<name>.inject_prompts`** (milestone 3): a boolean,
default false, and an optional list of prompt names (non-blank,
duplicates refused by position), each stored in its own column, with
the trust sentence in both generated descriptions. The boolean's
column is NOT NULL with a database-level default of false, so a row
written before the migration reads false from the database itself
rather than through a Python-side rescue of NULL; `inject_prompts`
and milestone 1's `instructions` are nullable, where NULL is the
unset the models already mean. The capture path is
named exactly, because today's code discards what it needs:
`initialize()` is awaited inside `_connect`, which returns only the
session, so `_connect` grows the initialization result in its return
value and `_run` does the capturing. Shipped instructions are
captured whenever the server sends them, regardless of the current
opt-in, and the slice's flag decides injection and inspection only:
the flag is excluded from connection identity, so a false-to-true
reload must be able to expose instructions on a connection that
never restarts, which only works if they were captured while the
flag was still false. The named prompts are fetched only when
`inject_prompts` names them, since fetching costs round trips and
the field participates in connection identity anyway. Both captures
are held beside the published tools, cleared wherever they are (the
normal unwind and `_mark_down` alike), and capped at capture.

### Where the pieces live at runtime

Per-server guidance follows the managers and the slice, because it is
what a reload swaps: `McpSlice` carries each entry's operator
`instructions` and its opt-in flag, the manager carries what the
server shipped, and `McpServers` grows `guidance_for_agent(agent)`,
answering the ordered guidance blocks the agent's effective grants
name, whatever each server's liveness or filtered tool count. It is
read at activation, when the know-how half is assembled and cached,
so a reload's swap reaches new sessions and switched-in agents.

Fragments follow the boot `Config`, which the pipeline already holds:
`Config.prompt_for_agent(agent)` resolves the persona plus the
effective include list (own or inherited) against `prompt_fragments`,
and it is the persona's only source. `AgentProviders.prompt` is
removed rather than left standing beside it: it is a boot-time copy
of the same `agents.<name>.prompt` field, two sources for one string
is how the pipeline and the inspection surface come to disagree, and
what remains of `AgentProviders` is exactly what its name says, the
four providers. The registry's builder and its tests drop the field
in milestone 1, and the pipeline and the inspection hook both read
`Config.prompt_for_agent`.
The assembler takes the resolved pieces (persona, fragment blocks,
guidance blocks, memory text) and produces the prompt and the
per-block accounting in one place, so the pipeline, the
`prompt_assembled` event and the inspection surface cannot disagree
about what was assembled.

### The inspection surface

`GET /runtime/agents/{name}/prompt`, with `samtal-server config
prompt <agent>` as its CLI client. It lives in the `/runtime`
namespace #121 established (the entity namespace stays purely CRUD),
and it answers what a session opening now as that agent would
receive. It is explicitly a new-session preview, and the surface
says so in its description: a running session holds the know-how
half it cached at its own activation, which may predate a reload,
and per-session readback is deliberately not offered; what an
operator audits is what the configuration produces now. The response
carries:
the ordered blocks, each with its provenance (`persona`,
`fragment:<name>`, `instructions:<entry>`,
`server_instructions:<entry>`, `server_prompt:<entry>:<position>`,
`memory`), its character count, and its text, plus the total count;
a `server_prompt` block also carries the configured name as data,
where reads already echo what the operator wrote.
It is a runtime read, not a database read: it reflects the loaded
agents, the running slice and managers, and the memory store, so it
cannot disagree with what a session would get, which is the point. An
agent the running server did not load answers 404 with a sentence
naming the restart; an API application built without a server answers
503, like the reload. The handler is `async def` and reads manager
state on the loop that mutates it, the status handler's rule; the
memory read is a file read and runs in a worker thread. `build_api`
grows one more optional runtime hook, the way it grew `pending` and
the MCP hooks.

The blocks carry text deliberately, including the server-shipped
block when an entry opted in: an inspection surface that hides part of
the prompt fails its purpose, the opt-in is the trust decision, and
provenance marking is what tells the operator whose words they are
reading. This does not reopen the #121 no-server-bytes rule for the
status surface, which is about surfaces that did not ask for those
bytes; here the operator explicitly opted the bytes into the model's
prompt, and this surface is the audit of exactly that.

`config prompt` does not render through the CLI's existing response
renderer: `_printable` strips every value and truncates it to
`GLIMPSE_LENGTH`, which is right for acknowledgements and fatally
wrong for an inspection command, where a concealed tail is exactly
what the operator came to see. The command gets a full-block terminal
sanitizer of its own: newlines and tabs pass, every other
non-printable character is replaced rather than stripped, and nothing
is truncated, ever, silently or otherwise; the block's reported
character count is the count of what was stored, so a replacement
never falsifies the accounting. The test renders a block longer than
`GLIMPSE_LENGTH` and asserts the tail survives, beside the
control-character case.

## Module layout

```
samtal_server/runtime/prompt.py       the assembler: block order, headings,
                                      per-block accounting; subsumes
                                      builtin.with_memory
samtal_server/runtime/pipeline.py     the cached know-how half assembled
                                      in _activate_agent; the per-round
                                      memory append moves to a worker
                                      thread; the prompt_assembled event
samtal_server/tools/builtin.py        with_memory retired into the assembler;
                                      MEMORY_HEADING moves with it
samtal_server/tools/mcp.py            slice carries instructions and opt-ins;
                                      manager captures initialize
                                      instructions under the cap;
                                      guidance_for_agent; connection
                                      identity excludes the prompt fields
samtal_server/config/models.py        instructions, use_server_instructions
                                      and inject_prompts on McpServerConfig;
                                      prompt_fragments section and name rule;
                                      prompt_includes on both layers;
                                      reference checks; prompt_for_agent
samtal_server/providers/registry.py   AgentProviders loses its prompt copy;
                                      the four providers remain
samtal_server/db/schema.py            the new columns and table
samtal_server/db/migrations/versions/ 0002 (instructions), 0003 (fragments,
                                      includes), 0004 (the two
                                      server-shipped opt-in fields)
samtal_server/config/loader.py        prompt_fragments joins the moved-key
                                      commands, so a stale YAML section or
                                      environment variable is refused with
                                      the command that writes it
samtal_server/config/store.py         fragment rows; the new columns in
                                      entry and layer rows
config.example.yaml                   the domain-section wording learns
                                      prompt_fragments and its command
samtal_server/config/views.py         fragment reads; the new fields echoed
                                      write-shaped
samtal_server/config/api.py           prompt-fragment CRUD routes;
                                      GET /runtime/agents/{name}/prompt;
                                      the prompt hook on build_api
samtal_server/config/writes.py        the fragment write sentences
samtal_server/config/cli.py           set/show/delete prompt-fragment;
                                      config prompt <agent>
samtal_server/config/docgen.py        the new section and fields in the
                                      generated reference
samtal_server/app.py                  wiring the prompt hook
examples/mcp-server-stdio.yaml        an instructions line
examples/mcp-server-streamable-http.yaml  the same, plus the opt-in with
                                      its trust comment
examples/prompt-fragment.yaml         a fragment example
examples/agent.yaml                   prompt_includes beside mcp
examples/agent-defaults.yaml          the same, on the defaults layer
examples/README.md                    the new example listed, which the
                                      example suite enforces
tests/unit/test_config_examples.py    prompt-fragment joins the creation
                                      order before the layers that
                                      include it; the listing assertions
tests/unit/test_db_open.py            the expected-table set gains
                                      prompt_fragments; the seeded
                                      0001-to-head upgrade proof
.github/workflows/samtal-server.yml   the installed-wheel database check
                                      learns the new table and columns
docs/reference/domain-config.md       regenerated
docs/reference/api-openapi.json       regenerated
samtal-server/README.md               assembly order, guidance, fragments,
                                      the opt-in trust paragraph, the
                                      inspection surface
CHANGELOG.md                          per-milestone entries
```

## Tests

Reuse of existing assets, not restatement: the stdio mock server in
`tests/support/mcp_stdio_server.py` and the in-process
FastMCP-on-uvicorn fixture from `test_tools_mcp_http.py` (both can
publish `instructions` from `initialize`), the API TestClient patterns
from the REST suites, the scripted `MockLlm` whose `{system}` reply
placeholder renders the assembled prompt into the spoken reply and
whose `{tools}` placeholder proves the offer, and the byte-for-byte
doc drift checks.

- Unit, milestone 1: the field parses, round-trips through store and
  views write-shaped, and appears in the generated reference; the
  assembler's order pinned (persona, guidance, memory) and, per
  invocation, byte-equal to today's `with_memory` output when no
  guidance exists; guidance injected for every granted agent by the
  effective grant, present while the entry is down and while the
  allow list filters its offer to nothing, absent for `mcp: []`; an
  instructions-only reload keeps the connection (same manager
  object), reports `unchanged`, and the next activation carries the
  new text while a running session's cached half does not; the
  activation cache itself (assembled in `_activate_agent`, not per
  reply, re-assembled on switch); the per-round memory read running
  off the event loop; pre-upgrade rows without the column load
  unchanged; the inspection surface over persona, guidance and
  memory provenance, and the `prompt_assembled` event over the
  know-how half only, memory explicitly absent from it.
- Unit, milestone 2: fragment name and body validation (bad charset,
  blank body, both refused naming section and rule only), with a
  credential-sentinel fragment name asserted absent from the HTTP
  response, CLI stdout and stderr, every log record and the full
  exception chain, beside the unknown-include sentinel; store,
  views, API and CLI round-trips;
  `prompt_includes` semantics (inherit, replace, `[]` opt-out,
  duplicates refused, unknown fragment refused at write time and at
  boot, `agent_defaults` parity); the unresolved-include refusal
  names layer, position and rule only, with a credential-sentinel
  include asserted absent from the HTTP response, CLI stdout and
  stderr, every log record and the full exception chain, for agent
  and defaults writes and for boot validation; assembly order with
  fragments in include order; migration loads pre-upgrade agent
  rows; the moved-key path: a YAML file holding a `prompt_fragments`
  section and a `SAMTAL_PROMPT_FRAGMENTS` environment variable are
  each refused naming the command that writes the section, which is
  the loader's rule for every domain key; docgen and examples drift
  checks.
- Unit, milestone 3: default-off ignores shipped instructions
  entirely and fetches no prompts; opted-in captures and injects
  after the operator's block, prompts in `inject_prompts` order after
  the shipped instructions; discovery is listing-first and proven: a
  listing walked across more than one page, a server without the
  prompts capability skipping every configured name with one
  entry-level warning, and an unlisted name, a required-arguments
  prompt and a non-text-content prompt each skipped with its
  `inject_prompts` position and rule named, never the configured
  value or the server's bytes; the rendering pinned exactly, a
  multi-message prompt joining its messages' text with blank lines
  and roles dropped, under the `server_prompt:<entry>:<position>`
  provenance; the cap skips wholesale per block with a warning
  naming entry, channel and size; the reflection sentinels: a mock
  server shipping a credential sentinel in its instructions and in a
  prompt's rendered text, and a configured prompt name holding a
  credential sentinel and a terminal escape, each asserted absent
  from every log record, every CLI stream, every refusal sentence
  and the status surface, with or without the opt-ins; cleared when
  the connection drops, on the normal unwind and on `_mark_down`
  both; an `inject_prompts` edit restarts the connection on reload,
  and the two injection-only fields do not, proven in both toggle
  directions on the same manager object: false-to-true exposes the
  already captured instructions without a reconnect, true-to-false
  stops injecting while the connection stands; containment proven
  with a stalled prompt fetch, a repeating pagination cursor, a
  listing past the page cap, and elapsed boot and reload completion
  measured against the stated envelope, the connection and tools
  surviving every case.
- Unit, milestone 1, the inspection surface: the route
  (bearer-gated, 404 for an unloaded agent with the restart
  sentence, 503 serverless); the block shapes and totals agree with
  the assembler; the CLI's full-block sanitizer replaces
  non-printables without stripping and truncates nothing, proven
  with a block longer than `GLIMPSE_LENGTH` whose tail survives;
  OpenAPI drift. Milestones 2 and 3 extend these assertions to each
  provenance they add.
- Integration: the issue's verification steps. Two agents granted the
  same entry both speak its guidance through `{system}`, and an
  `mcp: []` agent does not; a fragment written through the API changes
  the assembled prompt of every including agent after a restart (a
  second app instance on the same database); a server shipping
  instructions has them surfaced only when the entry opts in; one
  session held open across a memory write, an MCP reload, a server
  reconnect and an agent switch, asserting through `{system}` that
  the memory write appears on the next reply as today, the reload's
  guidance stays invisible until the switch, and the switch
  re-assembles the know-how half; the
  assembled prompt of a live deployment is read back through
  `GET /runtime/agents/{name}/prompt` over a real socket and matches
  what `{system}` shows the model receiving.
- Upgrade, all three migration milestones: a test that stamps and
  builds the 0001 schema, seeds nonempty provider, MCP entry,
  defaults, agent and device rows, upgrades to head through 0002,
  0003 and 0004, and loads the result through `ConfigStore`,
  asserting every seeded value preserved, `use_server_instructions`
  false and the nullable additions unset. Grown in each milestone as
  its migration lands, so the chain is proven at every merge, not
  only at the end. The expected-table set in `test_db_open.py` and
  the installed-wheel check in the CI workflow move in the same
  change as each migration.
- Doc drift: reference and OpenAPI regenerated in the same change as
  each schema or route move, enforced by the existing byte-for-byte
  checks.

## Risks and mitigations

- **The with_memory refactor touches every reply.** The assembler
  lands with a pinned test that its output is, per invocation,
  byte-equal to today's for a configuration with no guidance and no
  fragments, the memory clock is deliberately unchanged (per-round,
  now off the event loop), and the held-session integration test
  pins the activation-cache semantics, so milestone 1's effect on
  existing deployments is exactly the documented one.
- **Prompt budget on small local models.** No automatic trimming, by
  decision; the mitigation is visibility (the surface, the event) and
  the documented two-tier fallback owned by #83.
- **Third-party bytes steering the agent.** Opt-in per entry, the
  size cap, the publishing rule for logs, provenance on the surface,
  the CLI's full-block sanitizer.
- **Schema widening breaks existing rows.** Every column is nullable
  or defaulted, the table is new, and the unit suites pin pre-upgrade
  rows loading unchanged; migrations are additive and per-milestone,
  so each merge upgrades a fresh volume to a releasable schema.
- **Reload and boot diverge on the new fields.** Both build the slice
  and managers through the same `_managers_for`/`McpSlice.of` path,
  the #121 property carried over; the connection-identity exclusion
  is one comparison in one place (`same_as`), tested from both sides
  (a config edit restarts, a prompt-field edit does not).
- **Committed contracts drift.** Regeneration rides in the same
  commit as the change that moves it; CI's drift checks are the net.

## Plan review round

One external review of the plan as first committed (dec79b2): codex
CLI 0.147.0, model gpt-5.6-sol, read-only against this repository
with the issue #122 body supplied, 2026-08-14. Verdict: not ready,
on findings 1 to 3. Findings as received, condensed; each carries
its resolution once the amendment addressing it lands.

1. **P1: the plan drops the required MCP prompts primitive.**
   Deliverable 3 explicitly requires consuming server-published MCP
   prompts; the plan substitutes `InitializeResult.instructions` and
   defers prompts, which replaces a settled deliverable with a
   different protocol field. Define how `prompts/list` and
   `prompts/get` are consumed at connect or reload, never during a
   turn, with handling for required arguments, selection, size caps,
   reconnects and opt-in; `InitializeResult.instructions` may be
   supported additionally, but cannot replace the deliverable.
   Resources may stay deferred as the issue permits.
   *Resolution*: adopted. The trust-boundary section now consumes
   both channels behind per-entry opt-ins: `use_server_instructions`
   for `InitializeResult.instructions`, and `inject_prompts`, a list
   of operator-named prompts fetched with `prompts/get` and no
   arguments at connect inside the existing `CONNECT_TIMEOUT_S`
   envelope, never during a turn, refetched on every reconnect.
   Selection is operator-explicit because the specification defines
   prompts as user-controlled templates and a server may publish
   dozens; a named prompt that is unpublished, requires arguments or
   renders non-text is skipped with a warning naming the
   operator-written name only, the grant-allow-list mismatch shape.
   `inject_prompts` participates in connection identity, since
   editing it changes what the connect fetches. Templates with
   arguments keep a documented revisit condition (a user-invocable
   surface); resources stay deferred. Milestone 3 and its tests
   carry both fields.

2. **P1: per-reply assembly contradicts the settled session-open
   boundary, and the byte-equality claim is false.** The plan
   restates assembly at session open, then specifies per-leg
   assembly with reload pickup on the next utterance; the
   guidance-iff-tools-contribute rule cannot coexist with a
   session-open cache, since tools change per reply after
   activation. And today `_system_prompt()` re-reads memory on every
   LLM round, so moving to per-leg assembly changes when newly
   remembered facts appear; a static byte-equality test would not
   detect that. Either cache at activation and base guidance on the
   grant edge, or say explicitly what per-leg assembly means for the
   settled decision; document the memory-visibility change and test
   a persistent session across memory writes, reloads, reconnects
   and an agent switch; make the inspection endpoint's
   new-session-preview nature explicit.
   *Resolution*: adopted in substance, with the per-leg design kept
   and the contradiction resolved by argument rather than by a
   cache. The assembly section now states exactly when assembly runs
   (the start of each reply leg, which is the utterance boundary and
   the issue's own agent-switch case), why a session-open cache was
   rejected (it cannot coexist with #121's per-reply tool snapshot:
   a frozen prompt would describe tools that have since moved, the
   inert-config trap rebuilt inside one session), and that what the
   settled decision forbids is fetch-on-demand, which per-leg
   assembly never does. The false compatibility claim is replaced by
   a stated timing change (a mid-reply `remember` becomes visible at
   the next leg rather than the next round, cross-session freshness
   unchanged), pinned per invocation by the byte-equality test and
   in time by a new held-session integration test spanning a memory
   write, a reload, a reconnect and an agent switch. The inspection
   surface now says it is a new-session preview and why no cached
   per-session answer exists.

3. **P1: unknown `prompt_includes` can become a secret-reflection
   path.** The plan delegates unknown includes to
   `check_references`, whose existing refusals quote the unresolved
   value, and the #121 round already recorded that rejected
   fragments may hold pasted credentials; the `[A-Za-z0-9_-]+` rule
   does not close the leak, since a credential can match it.
   Unresolved includes must be reported by list position and rule
   only, never by value, with credential-sentinel tests over HTTP
   responses, CLI stdout and stderr, every log record and the full
   exception chain, for agent and defaults writes and boot
   validation.
   *Resolution*: adopted. The `prompt_includes` decision now states
   that unresolved includes are refused by layer, list position and
   rule only, never by value, following the rule the #121 round
   fixed for grants rather than the older quoting reference
   sentences, and says why the charset rule alone does not close the
   leak. Milestone 2's unit tests gain the credential-sentinel
   include asserted absent from HTTP responses, CLI stdout and
   stderr, every log record and the whole exception chain, on both
   write paths and at boot.

4. **P2: the new example breaks the existing example suite.**
   `test_config_examples.py` recognizes a fixed creation order and
   requires every example listed in `examples/README.md`; adding
   `examples/prompt-fragment.yaml` fails `ORDER.index(...)`, and
   neither the test nor the README is named by the plan.
   *Resolution*: adopted. The module layout now carries
   `examples/README.md` and `tests/unit/test_config_examples.py`,
   with `prompt-fragment` joining the creation order before the
   layers that include it, and the listing assertions updated in the
   same milestone 2 change that adds the example.

5. **P2: the upgrade verification does not prove real 0001
   databases survive.** The database tests migrate a fresh database
   to head rather than seeding a 0001 schema and advancing it; the
   expected-table sets in `test_db_open.py` and the installed-wheel
   CI check omit `prompt_fragments`; and a boolean added with only a
   Python default can leave existing rows `NULL`. Seed a real 0001
   database with nonempty rows, upgrade through all three
   migrations, load through `ConfigStore` and assert preservation
   plus `use_server_instructions == false`; specify the boolean's
   database-level default; update both expected-table sets.
   *Resolution*: adopted. The boolean's column is NOT NULL with a
   database-level default of false, stated where the shapes are; a
   new upgrade test stamps and seeds a real 0001 schema with
   nonempty rows, upgrades through all three migrations and loads
   through `ConfigStore`, grown in each milestone as its migration
   lands so the chain is proven at every merge; `test_db_open.py`'s
   expected-table set and the installed-wheel CI check join the
   module layout and move with each migration.

6. **P2: server-instruction capture is attached to the wrong code
   path, and flag toggles are unproven.** `initialize()` is awaited
   in `_connect()`, which discards its result, not in `_run`; and
   with the opt-in excluded from connection identity, a
   false-to-true reload cannot expose instructions that were never
   captured. Capture capped instructions regardless of the opt-in,
   use the flag only to control injection and inspection, and test
   both toggle directions on the same manager object plus clearing
   on `_mark_down` and normal unwind.
   *Resolution*: adopted. The capture decision now names the real
   path: `_connect` returns the initialization result it currently
   discards, `_run` captures shipped instructions regardless of the
   opt-in, and the flag decides injection and inspection only, which
   is what makes a false-to-true reload work on a connection that
   never restarts. Prompts are fetched only when named, since the
   field participates in connection identity. Milestone 3's tests
   carry both toggle directions on the same manager object and
   clearing on both unwind paths.

7. **P2: "injected verbatim" conflicts with `NonBlankStr`.** The
   repository's nonblank type strips surrounding whitespace, so
   using it for fragment bodies and instructions would alter
   deliberate leading and trailing newlines, and no test pins
   preservation. Use a plain string validated nonblank on a stripped
   copy but returned unmodified, with byte-exact tests through
   store, API, CLI and assembly.
   *Resolution*: adopted. Fragment bodies and `instructions` share
   the stated type decision: a plain string validated non-blank on a
   stripped copy and returned untouched, with byte-exact
   preservation pinned through store, API, CLI and the assembled
   prompt using a body carrying leading indentation and trailing
   blank lines.

8. **P2: the CLI inspection test would not catch silent
   truncation.** The CLI's response renderer strips and truncates
   every value to `GLIMPSE_LENGTH`, so reusing it would make
   `config prompt` conceal most realistic prompts while appearing
   successful, and the planned test covers only control characters.
   Use a dedicated full-block sanitizer that replaces nonprintables
   without stripping or truncating, and test a block longer than
   `GLIMPSE_LENGTH`.
   *Resolution*: adopted. The inspection section now states that
   `config prompt` bypasses `_printable` and renders through a
   full-block sanitizer that passes newlines and tabs, replaces
   rather than strips other non-printables, and never truncates,
   with the reported counts always counting what was stored; the
   milestone 4 tests carry the longer-than-`GLIMPSE_LENGTH` block
   whose tail must survive.

9. **P3: two persona sources are left standing.** The plan has
   `Config.prompt_for_agent` resolving the persona while
   `AgentProviders.prompt` still carries a boot-time copy, and
   `providers/registry.py` is absent from the module layout. Decide
   which source survives and make the pipeline and the inspection
   hook consume the same one.
   *Resolution*: adopted. `Config.prompt_for_agent` is the persona's
   only source; `AgentProviders.prompt` is removed in milestone 1,
   its builder and tests updated, and `providers/registry.py` joins
   the module layout. The pipeline and the inspection hook read the
   same helper, so they cannot disagree.

## Plan review round, second pass

A second review of the amended plan (6b81a68), same reviewer setup,
2026-08-14, asked to verify the first round's resolutions and to
find what the amendments introduced. Findings 3 to 9 of round one
were confirmed resolved. Verdict: not ready, on findings 1, 2 and
10. Findings as received, condensed, with resolutions appended as
the amendments land.

1. **P1: prompt discovery is underspecified and cannot enforce its
   skip rules.** The plan calls only `prompts/get`, so the client
   cannot tell an unpublished prompt from one requiring arguments
   without interpreting an untrusted server error. Require paginated
   `prompts/list`, validate configured names and required arguments
   against the listing, call `prompts/get` only for eligible
   prompts, define behavior when the server lacks the prompts
   capability, and test pagination, capability absence, unpublished
   names and required arguments.
   *Resolution*: adopted. The trust-boundary section now specifies
   discovery: the full paginated `prompts/list` is walked first,
   each configured name is validated against the listing (absent
   name, required arguments, no listing capability at all are each
   a distinct skip with its own warning), and `prompts/get` is
   called only for eligible names. A server without the prompts
   capability skips every configured name with one warning naming
   the entry. Milestone 3's tests cover pagination, capability
   absence, unpublished names and required-argument prompts.

2. **P1: the session-open decision is still contradicted, not
   resolved.** Reinterpreting the decision as forbidding only
   network fetches changes its explicit timing; cache the assembled
   prompt at activation and rebuild only on agent switch, base
   guidance on the grant edge rather than the mutable per-reply
   offer, or change the issue decision before implementing.
   *Resolution*: adopted, jointly with finding 10, and the design is
   better for it. The know-how half of the prompt (persona,
   fragments, guidance) is assembled once in `_activate_agent`, at
   session open and again at agent switch, exactly as the decision
   says, and cached on the runtime for the session's life; nothing
   about it is recomputed per reply. Memory keeps its existing
   per-round read appended to the cached half: that read predates
   this issue, its freshness contract is documented in today's code
   and owned by #83, and leaving it untouched means this plan
   changes no existing timing at all, which retires round one's
   finding 2 completely rather than arguing with it. A reload's
   guidance changes reach new sessions and switched-in agents, not
   replies in flight, the boot-snapshot contract's shape.

10. **P1: conditioning guidance on at least one offered tool
   violates the grant-edge deliverables and excludes prompt-only
   servers.** Deliverable 1 requires guidance for any granted
   agent, deliverable 3 concerns servers exposing prompts rather
   than tools, and the issue already tolerates guidance about
   withheld tools. Inject operator guidance by effective grant,
   irrespective of liveness or the filtered tool count; make
   shipped blocks eligible for a granted, connected server that
   publishes no tools.
   *Resolution*: adopted. The injection condition is now the
   effective grant and nothing else: operator instructions are
   injected for every granted agent, connected or not, tools or
   none, and captured server-shipped blocks are injected for a
   granted server whenever they have been captured, tools or none.
   The contribute-a-tool condition is gone, and the section says
   what replaced its rationale: the issue's own decision to
   tolerate guidance-tool mismatch as initial noise.

11. **P2: the new domain section omits the moved-key loader and the
   canonical example.** `config/loader.py` indexes
   `MOVED_KEY_COMMANDS` for every domain key, so a stale
   `prompt_fragments` YAML section or environment variable would
   raise `KeyError`, and AGENTS.md requires `config.example.yaml`
   to move with every schema change. Name both files, add the
   moved-key command and stale-file and environment tests, and
   update the example's domain-section list.
   *Resolution*: adopted. Both files join the module layout;
   milestone 2 adds the `prompt_fragments` moved-key command, tests
   a stale YAML section and a `SAMTAL_PROMPT_FRAGMENTS` environment
   variable both refused with the command named, and updates
   `config.example.yaml`'s domain-section wording.

12. **P2: an MCP prompt has no defined, auditable rendering.**
   Prompt results are ordered messages with roles and typed content
   blocks, and the provenance list omits named prompts. Define the
   rendering, assign stable provenance, apply the cap to the
   rendered block, and prove the rendering in tests.
   *Resolution*: adopted. A named prompt renders as the text of its
   messages in order, joined by blank lines, roles dropped: the
   block is standing guidance spoken by the operator's choice, not
   a dialog replay, and the plan says so. A prompt whose messages
   carry any non-text content block is skipped as unusable, the
   same rule as required arguments. The cap applies to the rendered
   block. Provenance is `server_prompt:<entry>:<name>`, the
   operator-written name, safe to print by construction. Milestone
   3 pins the exact rendering, including a multi-message prompt.

13. **P2: optional prompt retrieval can take the tool server
   down.** Every `prompts/get` inside the aggregate connect
   envelope means one slow prompt can mark the manager down and
   remove healthy tools. Specify bounded per-prompt failure
   containment with sanitized diagnostics, keeping the initialized
   connection usable; test a stalled prompt and aggregate duration
   past the old envelope.
   *Resolution*: adopted. Prompt discovery and fetching move after
   the connect envelope closes and the tools are published: each
   listing page and each `prompts/get` runs under its own short
   per-call bound, a failure or timeout skips that prompt (or, for
   the listing, all named prompts) with a warning carrying the
   entry, the operator-written name and the reason token, and the
   connection and tools stay up throughout. Milestone 3 tests a
   stalled prompt and a sequence of fetches whose sum exceeds
   `CONNECT_TIMEOUT_S`, asserting the tools survive both.

14. **P2: the fragment-name validator can reflect a rejected
   secret.** `check_mcp_entry_names` interpolates the rejected name
   into its refusal, and the sentinel tests cover unknown includes
   only. Refuse invalid fragment names by field and rule, never by
   value, with sentinel tests on every surface.
   *Resolution*: adopted. The name rule keeps the entry-name
   charset but not its refusal sentence: an invalid fragment name
   is refused naming the section and the rule only, and milestone
   2's sentinel tests add the invalid-name case beside the
   unknown-include one, over HTTP, CLI streams, logs and the
   exception chain.

15. **P2: inspection and budget visibility land after three
   releasable injection milestones.** The counting surface is the
   stated mitigation but ships last. Land the endpoint, the
   accounting and the event with milestone 1 and extend their
   provenance coverage per milestone.
   *Resolution*: adopted. The plan is restructured from four
   milestones to three: the inspection surface, the CLI command and
   the `prompt_assembled` event land in milestone 1 covering
   persona, guidance and memory, and milestones 2 and 3 extend the
   provenance to fragments and server-shipped blocks in the same
   change that adds each block type. No injection milestone merges
   without the surface that counts it.

16. **P2: the pipeline-side memory read remains blocking and
   contradicts resident-only assembly.** `MemoryStore.read()` is
   synchronous filesystem I/O reached from the event loop, and only
   the inspection handler was assigned a worker thread. Read memory
   through `asyncio.to_thread` in the runtime path too, passing the
   text into the pure assembler.
   *Resolution*: adopted. The plan now names the refactor: the
   per-round memory read moves off the event loop through
   `asyncio.to_thread`, resolved before the round's stream request
   is built, and the assembler stays a pure function handed the
   text. Today's code performs this read synchronously on the loop,
   so this is a small repair shipped with milestone 1, tested by
   the existing session suites.

## Plan review round, third pass

A third review of the twice-amended plan (fe8fac8), same reviewer
setup, 2026-08-14, asked to verify the second round's resolutions.
Round two's findings were confirmed resolved as amended, finding 13
partially (see finding 2 below). Verdict: ready after the P1/P2
amendments. Findings as received, condensed, with resolutions
appended as the amendments land.

1. **P1: raw prompt names create log-leak and terminal-control
   paths.** `inject_prompts` entries are only non-blank strings, and
   an MCP prompt name is a server-chosen identifier the operator
   copies, so it can hold anything; printing it in skip warnings and
   provenance makes "safe to print by construction" false. Identify
   prompts in logs, warnings and structured events by entry and
   `inject_prompts` position, never by value; sanitize provenance at
   the CLI like block text; add configured-name credential and
   control-character sentinels across every surface.
   *Resolution*: adopted. Every log line, warning and structured
   event now identifies a configured prompt by the entry and its
   position in `inject_prompts`, counted from one; the provenance
   token becomes `server_prompt:<entry>:<position>`. The configured
   name itself appears only where operator-written configuration is
   already echoed write-shaped (entity reads and the inspection
   response body, both JSON-encoded), and the CLI's full-block
   sanitizer covers provenance strings as well as text. Milestone
   3's sentinels gain a credential-shaped and a control-character
   prompt name asserted absent from logs, CLI streams and refusal
   sentences.

2. **P1: prompt discovery destroys the bounded startup and reload
   contract.** Per-call bounds alone let an unlimited listing or a
   repeating pagination cursor hold `start()` and the reload
   indefinitely while each request stays within its bound, against
   #121's one-connect-timeout envelope and the CLI's 60 s read
   timeout. Give discovery an aggregate deadline and bounded
   pagination, skip everything remaining when either is reached,
   state the worst-case start and reload envelope below the CLI
   timeout, and test elapsed completion, not just tool survival.
   *Resolution*: adopted. The whole discovery phase (listing pages
   and fetches together) runs under one aggregate deadline equal to
   `CONNECT_TIMEOUT_S`, with a fixed page cap as the cursor-loop
   backstop; reaching either skips all remaining prompts with one
   positional warning. The worst case for a manager start is
   therefore one connect timeout plus one discovery deadline plus
   small change, about 20 s, and the reload envelope stays where
   #121 pinned it plus that same deadline, comfortably inside the
   60 s CLI read timeout; the plan states both numbers. Milestone
   3's tests measure elapsed boot and reload completion against the
   envelope, and cover a repeating cursor and a listing whose pages
   exceed the cap.

3. **P2: an activation-time event cannot account for per-round
   memory.** `_activate_agent` is synchronous and memory is read per
   round, so the event cannot carry a memory count; either emit per
   round after the memory read, or keep it activation-only and
   exclude memory explicitly.
   *Resolution*: adopted, the second branch. `prompt_assembled`
   stays at activation, where the know-how half is actually
   assembled, and carries that half's block sizes only; memory is
   explicitly out of the event, because a per-round emission would
   double the round's log volume for a number that moves slowly and
   the existing `llm_round` event already carries per-round token
   counts, while the inspection surface reads memory fresh and
   answers its size on demand. The claims and milestone 1 tests are
   reworded to know-how provenance.

4. **P2: the fragment CRUD representation is incompatible with the
   existing substrate.** Entity parsing, `Envelope.entity` and the
   CLI all require mapping-shaped entities, and the plan never
   defines the PUT body. Define a concrete entity model with one
   verbatim text field and use the ordinary mapping path.
   *Resolution*: adopted. `prompt_fragments` maps names to a
   `PromptFragmentConfig` entity with a single verbatim `text`
   field; the PUT body, the read envelope and the CLI fragment all
   carry `{text: ...}` through the ordinary entity path, and
   milestone 2 pins the exact CLI input and API read representation.

5. **P2: reload and reconnect cache semantics still contain
   contradictory promises.** The instructions-edit section says new
   guidance appears in the next reply, against the cache rule; and a
   reconnect capture cannot update a running session's cached half,
   since activation precedes revival. Say "next activation" in both
   places, document the reconnect case beside the reload, and make
   the held-session test prove the reconnect does not mutate the
   cached half.
   *Resolution*: adopted. The instructions-edit section now says the
   next activation, the trust section documents that a reconnect's
   captures reach the inspection preview and later activations only,
   and the held-session integration test asserts the reconnect
   leaves the running session's cached half untouched while a
   subsequent switch or new session sees the capture.

## Milestones

Stacked branches, one PR each, every merge leaving `main` releasable:
milestone 1 is additive (no guidance configured means byte-identical
prompts) and carries the inspection surface from the start, milestone
2 is a new optional section, milestone 3 is a default-off flag; no
injection milestone merges without the surface that counts it.

- [ ] **Per-server guidance, with the inspection surface**: the
  `instructions` field, migration 0002, the assembler in
  `runtime/prompt.py` subsuming `with_memory`, the know-how half
  cached in `_activate_agent`, the per-round memory read off the
  event loop, `guidance_for_agent` by effective grant on the slice,
  the connection-identity exclusion, `Config.prompt_for_agent` as
  the persona's one source with `AgentProviders.prompt` removed,
  `GET /runtime/agents/{name}/prompt` with `config prompt <agent>`
  and its full-block sanitizer, the `prompt_assembled` event,
  examples, README, reference and OpenAPI regen, CHANGELOG. Accept:
  lint and both lanes green; the two-agents/opt-out proof and the
  instructions-only reload proof in tests; the no-guidance
  byte-equality pin; the surface answering persona, guidance and
  memory provenance with sizes and totals over a real socket, 404
  and 503 honest, the sanitizer's long-block tail surviving; drift
  checks pass.
- [ ] **Shared prompt fragments**: the `prompt_fragments` section and
  `prompt_includes` on both layers, migration 0003, store, views, API
  routes, CLI verbs, write sentences, reference checks, the
  moved-key command, assembly slot, `fragment:<name>` provenance on
  the inspection surface, `examples/prompt-fragment.yaml` and the
  two agent examples, `config.example.yaml` wording, README, docs
  regen, CHANGELOG. Accept: lint and both lanes green; the write,
  read-back, boot, assembled loop proven; unknown and duplicate
  includes refused by position, the sentinels clean; pre-upgrade
  rows load; the surface counting fragments; drift checks pass.
- [ ] **Server-shipped guidance opt-ins**: `use_server_instructions`
  and `inject_prompts`, migration 0004, capture at `initialize`,
  listing-first discovery and the contained prompt fetches, the
  defined rendering, injection after the operator's block, the skip
  rules, the reflection sentinel, the two server provenances on the
  inspection surface, the trust paragraph in README and the
  generated reference, examples, regen, CHANGELOG. Accept: lint and
  both lanes green; default-off proven silent and fetch-free;
  opted-in proven injected, ordered, rendered as defined and capped;
  unusable named prompts skipped visibly; the tools surviving a
  stalled fetch; no server bytes in any log record; the surface
  counting both server block kinds; drift checks pass.
