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

**When assembly runs, stated exactly.** Assembly is a pure function
over memory-resident state, evaluated at the start of each reply leg,
beside the tool snapshot the leg already takes: once when a reply
begins answering an utterance, and once more when an agent switch
starts the new agent's leg, which is the "at agent switch" case the
issue names. Nothing is assembled, and nothing is ever fetched, while
the model is streaming or between tool rounds; every network fetch
(connect, `initialize`, the prompt fetches, reload) happened before
assembly runs. That is this plan's reading of the issue's settled
boundary, and it is a reading rather than a repeal: what the decision
exists to forbid is fetch-on-demand through the speaker's dead air,
and a string concatenation of resident pieces costs none. The
alternative, caching one prompt at session open, was considered and
rejected because it cannot coexist with the rest of the system: #121
deliberately made the tool snapshot per-reply so that reloads and
reconnects reach running sessions on the next utterance, so a prompt
frozen at activation would describe tools that have since moved,
which is the inert-config trap rebuilt inside one session. Per-leg
assembly is what keeps the guidance and the tool list a reply is
built on describing the same world.

This changes one visible timing, and the plan says so rather than
hiding it behind an equality claim: today `_system_prompt()` is
evaluated on every LLM round, so a fact remembered in round one of a
multi-round reply appears in round two's prompt; assembled per leg,
it appears at the next leg instead. The change is deliberate (a
prompt that shifts between the rounds of one reply is a prompt the
tool loop cannot reason about) and small (one reply's tail, only when
the model called `remember` mid-reply). Cross-session freshness is
unchanged: memory is still read at every assembly, so a fact
remembered in a concurrent session is known to this one on its next
reply, exactly as today. The assembler's output for a configuration
with no guidance and no fragments is pinned byte-equal to today's
`with_memory` output per invocation, and the timing itself is pinned
by an integration test that holds one session across a memory write,
a reload, a reconnect and an agent switch and asserts when each
becomes visible.

The assembler lives in `samtal_server/runtime/prompt.py`: prompt
assembly fails the "would this exist if the backend were a telephone
call" test, so it is runtime code, and `builtin.with_memory` folds
into it rather than surviving as a second place where prompt text is
glued.

### Budget pressure: counted on the inspection surface, not the MCP one

Every injected block competes with memory for the prompt budget on
small local models, and the issue asks for the sizes to be visible
somewhere. The answer is the assembled-prompt surface (milestone 4):
it reports each block with its provenance and its size in characters,
plus the total, which is the number an operator tunes against. It is
not the MCP status surface, although the issue names it as a
candidate: that surface reports MCP runtime state, fragments are not
MCP, and a prompt question ("what is the model receiving and how big
is it") deserves one answer in one place rather than half an answer on
a surface about connections. Beside the surface, agent activation
logs a `prompt_assembled` event carrying per-source character counts,
which is the decision-site rule applied to prompt size: when a small
model degrades in the field, the retained logs say what the prompt
held without anyone reproducing the session.

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
  server-published prompts to inject, by prompt name. At connect,
  inside the existing `CONNECT_TIMEOUT_S` envelope and never during a
  turn, the manager fetches each named prompt with `prompts/get` and
  no arguments, extracts its text content, and holds it beside the
  published tools; the blocks are injected in the order the operator
  listed them, after the entry's other guidance. Selection is
  operator-explicit rather than wholesale, because the specification
  defines prompts as user-controlled templates and a server may
  publish dozens: the operator, who read the server's documentation,
  names the ones that are standing guidance rather than invocable
  templates. A named prompt the server does not publish, one that
  requires arguments (a template cannot be rendered without them),
  or one whose rendered content is not text, is skipped with a
  warning naming the operator-written name and the rule it failed,
  never the server's bytes: the same visible-mismatch shape as a
  grant allow list naming an unpublished tool. Prompts are refetched
  on every reconnect, like the tool list, and `inject_prompts`
  participates in connection identity (below): editing it changes
  what the connect fetches, so it restarts the connection, unlike
  the two fields that configure only injection.

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
  a log record; the connect log and the skip warnings carry the entry
  name, the operator-written prompt name where one exists, and the
  size only. The bytes reach exactly two
  places, both deliberate: the model's system prompt, which is what
  the opt-in means, and the assembled-prompt surface, which exists to
  show what the model receives and marks the block's provenance so an
  operator can see whose words they are reading. The CLI strips
  control characters when rendering any block, so a hostile server
  cannot drive a terminal through an inspection command.

### Guidance and per-tool grants: whole-entry, and injected next to tools

Per-server guidance does not narrow with a grant's allow list; it is
whole-entry, as the issue expects ("probably fine to ignore at first"),
and the field's generated description says that guidance describing a
withheld tool is noise the operator avoids by writing guidance about
the granted surface. What the resolution adds is the injection
condition: an entry's guidance (operator-written and server-shipped
alike) is injected exactly when the entry contributes at least one
tool to the reply's snapshot, after liveness and the allow-list
filter. A down server contributes neither tools nor guidance, because
"prefer the search tool" with no search tool present is an instruction
to fail; an `mcp: []` agent never sees any of it; and an agent whose
allow list filtered an entry's offer to nothing gets no orphaned
guidance either. This is the issue's "next to that server's tools"
made literal: the guidance appears when and only when the tools do.

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
non-blank string on the entry, operator-written guidance injected for
every granted agent. Ordinary domain field: stored in a nullable
`instructions` column, written through the existing generic
`set mcp-server` CLI and REST routes with no route changes, shown by
reads (it is not a secret), covered by the generated reference.

**`prompt_fragments`** (milestone 2): a new domain section mapping
fragment names to instruction blocks. Names match the MCP entry-name
pattern (`[A-Za-z0-9_-]+`): they appear in refusals, logs and the
inspection surface, so the same safe-charset rule applies, enforced at
parse time like `check_mcp_entry_names`. Bodies are non-blank text
with no server-imposed length cap; the counting surface is the guard,
and the operator is trusted with their own prompt budget. A new
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
unset the models already mean. The manager
captures `InitializeResult.instructions` and fetches the named
prompts at connect, holds them beside the published tools (cleared
when the connection drops, like them), and applies the cap at
capture.

### Where the pieces live at runtime

Per-server guidance follows the managers and the slice, because it is
what a reload swaps: `McpSlice` carries each entry's operator
`instructions` and its opt-in flag, the manager carries what the
server shipped, and `McpServers` grows `guidance_for_agent(agent)`,
answering the ordered guidance blocks for the entries that currently
contribute tools to that agent, the same per-reply question
`tools_for_agent` already answers and swapped by the same reload.

Fragments follow the boot `Config`, which the pipeline already holds:
`Config.prompt_for_agent(agent)` resolves the persona plus the
effective include list (own or inherited) against `prompt_fragments`.
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
receive. It is explicitly a new-session preview: sessions hold no
cached prompt to read back (each leg assembles afresh), so there is
no "what did session X get" answer to give, and the surface says so
in its description rather than implying one. The response carries:
the ordered blocks, each with its provenance (`persona`,
`fragment:<name>`, `instructions:<entry>`, `server_instructions:<entry>`,
`memory`), its character count, and its text, plus the total count.
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

## Module layout

```
samtal_server/runtime/prompt.py       the assembler: block order, headings,
                                      per-block accounting; subsumes
                                      builtin.with_memory
samtal_server/runtime/pipeline.py     per-leg assembly beside the tool
                                      snapshot; the prompt_assembled event
samtal_server/tools/builtin.py        with_memory retired into the assembler;
                                      MEMORY_HEADING moves with it
samtal_server/tools/mcp.py            slice carries instructions and opt-ins;
                                      manager captures initialize
                                      instructions under the cap;
                                      guidance_for_agent; connection
                                      identity excludes the prompt fields
samtal_server/config/models.py        instructions and use_server_instructions
                                      on McpServerConfig; prompt_fragments
                                      section and name rule; prompt_includes
                                      on both layers; reference checks
samtal_server/db/schema.py            the new columns and table
samtal_server/db/migrations/versions/ 0002 (instructions), 0003 (fragments,
                                      includes), 0004 (use_server_instructions)
samtal_server/config/store.py         fragment rows; the new columns in
                                      entry and layer rows
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
  guidance exists; guidance injected for a
  granted agent whose entry contributes tools, absent for `mcp: []`,
  absent while the entry is down, absent when the allow list filtered
  the offer to nothing; an instructions-only reload keeps the
  connection (same manager object), reports `unchanged`, and the next
  assembly carries the new text; pre-upgrade rows without the column
  load unchanged.
- Unit, milestone 2: fragment name and body validation (bad charset,
  blank body, both refused with the position-not-value rule where a
  value would be echoed); store, views, API and CLI round-trips;
  `prompt_includes` semantics (inherit, replace, `[]` opt-out,
  duplicates refused, unknown fragment refused at write time and at
  boot, `agent_defaults` parity); the unresolved-include refusal
  names layer, position and rule only, with a credential-sentinel
  include asserted absent from the HTTP response, CLI stdout and
  stderr, every log record and the full exception chain, for agent
  and defaults writes and for boot validation; assembly order with
  fragments in include order; migration loads pre-upgrade agent
  rows; docgen and examples drift checks.
- Unit, milestone 3: default-off ignores shipped instructions
  entirely and fetches no prompts; opted-in captures and injects
  after the operator's block, prompts in `inject_prompts` order after
  the shipped instructions; a named prompt that is unpublished,
  requires arguments, or renders non-text content is skipped with the
  warning naming the operator-written name and the rule, never the
  server's bytes; the cap skips wholesale per block with a warning
  naming entry, channel and size; the reflection sentinel: a mock
  server shipping a credential sentinel in its instructions and in a
  prompt's rendered text, asserted absent from every log record and
  from the status surface, with or without the opt-ins; cleared when
  the connection drops; an `inject_prompts` edit restarts the
  connection on reload, and the two injection-only fields do not.
- Unit, milestone 4: the route (bearer-gated, 404 for an unloaded
  agent with the restart sentence, 503 serverless); the block shapes
  and totals agree with the assembler; CLI rendering strips control
  characters; OpenAPI drift.
- Integration: the issue's verification steps. Two agents granted the
  same entry both speak its guidance through `{system}`, and an
  `mcp: []` agent does not; a fragment written through the API changes
  the assembled prompt of every including agent after a restart (a
  second app instance on the same database); a server shipping
  instructions has them surfaced only when the entry opts in; one
  session held open across a memory write, an MCP reload, a server
  reconnect and an agent switch, asserting through `{system}` when
  each becomes visible (the next leg, never mid-reply); the
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
  fragments, and the one timing change (per-leg rather than
  per-round evaluation) is stated in the assembly section and pinned
  by the held-session integration test, so milestone 1's effect on
  existing deployments is exactly the documented one.
- **Prompt budget on small local models.** No automatic trimming, by
  decision; the mitigation is visibility (the surface, the event) and
  the documented two-tier fallback owned by #83.
- **Third-party bytes steering the agent.** Opt-in per entry, the
  size cap, the publishing rule for logs, provenance on the surface,
  control characters stripped at the CLI.
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

7. **P2: "injected verbatim" conflicts with `NonBlankStr`.** The
   repository's nonblank type strips surrounding whitespace, so
   using it for fragment bodies and instructions would alter
   deliberate leading and trailing newlines, and no test pins
   preservation. Use a plain string validated nonblank on a stripped
   copy but returned unmodified, with byte-exact tests through
   store, API, CLI and assembly.

8. **P2: the CLI inspection test would not catch silent
   truncation.** The CLI's response renderer strips and truncates
   every value to `GLIMPSE_LENGTH`, so reusing it would make
   `config prompt` conceal most realistic prompts while appearing
   successful, and the planned test covers only control characters.
   Use a dedicated full-block sanitizer that replaces nonprintables
   without stripping or truncating, and test a block longer than
   `GLIMPSE_LENGTH`.

9. **P3: two persona sources are left standing.** The plan has
   `Config.prompt_for_agent` resolving the persona while
   `AgentProviders.prompt` still carries a boot-time copy, and
   `providers/registry.py` is absent from the module layout. Decide
   which source survives and make the pipeline and the inspection
   hook consume the same one.

## Milestones

Stacked branches, one PR each, every merge leaving `main` releasable:
milestone 1 is additive (no guidance configured means byte-identical
prompts), milestone 2 is a new optional section, milestone 3 is a
default-off flag, milestone 4 is a new read surface.

- [ ] **Per-server guidance**: the `instructions` field, migration
  0002, the assembler in `runtime/prompt.py` subsuming `with_memory`,
  per-leg assembly beside the tool snapshot, `guidance_for_agent` on
  the slice, the connection-identity exclusion, examples, README,
  reference and OpenAPI regen, CHANGELOG. Accept: lint and both lanes
  green; the two-agents/opt-out proof and the instructions-only
  reload proof in tests; the no-guidance byte-equality pin; drift
  checks pass.
- [ ] **Shared prompt fragments**: the `prompt_fragments` section and
  `prompt_includes` on both layers, migration 0003, store, views, API
  routes, CLI verbs, write sentences, reference checks, assembly
  slot, `examples/prompt-fragment.yaml` and the two agent examples,
  README, docs regen, CHANGELOG. Accept: lint and both lanes green;
  the write, read-back, boot, assembled loop proven; unknown and
  duplicate includes refused; pre-upgrade rows load; drift checks
  pass.
- [ ] **Server-shipped guidance opt-ins**: `use_server_instructions`
  and `inject_prompts`, migration 0004, capture at `initialize` and
  the prompt fetches at connect under the cap, injection after the
  operator's block, the skip rules for unusable named prompts, the
  reflection sentinel, the trust paragraph in README and the
  generated reference, examples, regen, CHANGELOG. Accept: lint and
  both lanes green; default-off proven silent and fetch-free;
  opted-in proven injected, ordered and capped; unusable named
  prompts skipped visibly; no server bytes in any log record; drift
  checks pass.
- [ ] **The assembled-prompt surface**: `GET
  /runtime/agents/{name}/prompt`, `config prompt <agent>`, the
  `prompt_assembled` event, the assembly-order documentation, OpenAPI
  regen, CHANGELOG. Accept: lint and both lanes green; the surface
  answers all four provenances with sizes and totals over a real
  socket; 404 and 503 honest; control characters stripped at the CLI;
  drift checks pass.
