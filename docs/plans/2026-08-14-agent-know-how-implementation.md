# Give agents know-how: what each milestone did

Companion to
[`2026-08-14-agent-know-how.md`](2026-08-14-agent-know-how.md). One
section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out.

## Milestone 1: Per-server guidance, with the inspection surface

An `mcp_servers` entry can now carry the operator's own guidance about
using its tools, injected into the system prompt of every agent the
entry is granted to. The system prompt is assembled in one place, with
per-block accounting, and an operator can read back what a session
opening now would be sent.

### What landed

**`samtal_server/config/models.py`.** A `VerbatimStr` type (a plain
string with an `AfterValidator` that checks a stripped copy for
non-blankness and returns the original untouched) and `instructions` on
`McpServerConfig`, typed with it. Deliberately not `NonBlankStr`, which
strips: the field is promised verbatim, and leading indentation and a
trailing newline are somebody's own formatting of a prompt. The refusal
for a blank value names the rule and never the value, the boundary's
rule everywhere else. `Config.prompt_for_agent(agent)` joins
`mcp_for_agent` and `filler_for_agent` as the one source of an agent's
persona.

**`samtal_server/db/`.** `schema.py` gains a nullable `instructions`
column on `mcp_servers`, and migration `0002_mcp_server_instructions`
adds it. Nullable because NULL is the unset the model already means, so
a row written before the migration loads as an entry with no guidance.

**`samtal_server/config/store.py` and `views.py`.** The column is
written and read as it was given, and a read shows it unmasked and
write-shaped: it is prompt text the operator wrote, not a credential
slot.

**`samtal_server/runtime/prompt.py`** (new). The assembler, and the one
place prompt text is joined. `tools/builtin.py`'s `with_memory` and
`MEMORY_HEADING` folded into it: prompt assembly fails the "would this
exist if the backend were a telephone call" test, so it is runtime
code, and a second joiner beside it is how a pipeline and an inspection
surface come to disagree. The module carries the block shapes
(`Guidance`, `Block`, `Assembled`), the provenance tokens (`persona`,
`instructions:<entry>`, `memory`), the heading a guidance block sits
under (`Guidance for using the tools whose names begin with home__:`,
which names the prefix rather than the entry because the prefix is what
the model can act on), and two functions: `know_how(persona, guidance)`
for the half a caller caches per activation, and `with_memory(half,
facts)` for the prompt one round is sent. Both answer an `Assembled`,
which carries the ordered blocks and the text, so the prompt and its
accounting are produced together.

**`samtal_server/tools/mcp.py`.** `McpSlice` carries each entry's
`instructions` and answers `guidance_for(agent)`; `McpServers` grows
`guidance_for_agent(agent)` over it, asked by agent for the reason
`tools_for_agent` is, since a reload swaps the slice. The condition is
the effective grant and nothing else: present while the entry is down
and while an allow list filters its offer to nothing, absent for an
agent granted nothing. `same_as` now compares through
`_connection_identity`, which drops the entry's prompt-only fields, so
an instructions-only edit keeps the live connection and reports
`unchanged`.

**`samtal_server/runtime/pipeline.py`.** `_activate_agent` assembles the
know-how half and caches it on `self._know_how`, at session open and at
an agent switch and never per reply, and logs `prompt_assembled` with
the half's per-source character counts. `_system_prompt` became `async
def`: it appends the memory block to the cached half, reading the store
through `asyncio.to_thread`, and the round resolves it before building
its stream request. The memory clock is unchanged, which is the whole
point of the split.

**`samtal_server/providers/registry.py`.** `AgentProviders.prompt` is
gone; what is left is the four providers the class is named for.

**`samtal_server/config/api.py` and `app.py`.**
`GET /runtime/agents/{name}/prompt`, with `PromptBlock` and
`AssembledPrompt` response models, an `agent_prompt` hook on
`build_api` beside the MCP ones, and the composition root closing over
the three pieces an assembly needs (the loaded configuration, the MCP
registry, the memory store). The handler is `async def` so the slice is
read on the loop that owns it; the memory read is a file read and runs
in a worker thread. An agent this server did not load answers 404 with
a sentence naming the restart and never quoting the name; an
application built without a server answers 503. `create_app` builds the
memory store before the API rather than after it, since the read
reports what a session would be sent.

**`samtal_server/config/cli.py`.** `samtal-server config prompt
<agent>`, a client of that route. It renders through a full-block
sanitizer of its own rather than through `_printable`: newlines and
tabs pass, every other unprintable character is replaced rather than
dropped, and nothing is truncated, ever. The counts printed are the
server's, counting what is stored, so a replacement never falsifies the
accounting. The answer is shape-checked all the way down, like the
status document, with the same `UNRECOGNIZED_ANSWER` sentence.

**`.github/workflows/samtal-server.yml`.** The installed-wheel database
check learns the column: a wheel carrying only the baseline script now
fails there rather than at the first write on a deployment.

**Documentation.** `docs/reference/domain-config.md` and
`docs/reference/api-openapi.json` regenerated in the commits that moved
them, never hand-edited. Both MCP examples gain an `instructions`
block. The server README gains a paragraph on guidance beside the MCP
servers it is about and a new section, "What the model is actually
sent", with the fixed order, the counts, the preview semantics and the
route. `CHANGELOG.md` gains a `## 2026-08-14` section.

**Tests.** Everything the plan's Tests section assigns to this
milestone.

- `tests/unit/test_runtime_prompt.py` (new): the order pinned exactly,
  headings and blank lines included; the byte-equality pin, which
  transcribes the old `with_memory` and compares the two over an empty
  persona, a whitespace-only one and an indented one crossed with three
  memory states; the per-block accounting; the empty persona still
  being a block; an empty memory read returning the cached half
  unchanged, by identity.
- `tests/unit/test_config_tools.py`, `test_config_store.py`,
  `test_config_reads.py`: the field parses, a blank one is refused by
  the rule and not by its value, and the text round trips byte for byte
  through the column, the load, the entity read and the view, with a
  body that carries leading indentation and a trailing newline.
- `tests/unit/test_db_open.py`: the seeded upgrade proof the plan's
  review round asked for. It builds a real 0001 schema with Alembic,
  seeds a nonempty row in every table with SQL against the baseline
  columns, opens the database the way a server does, and loads the
  result through `ConfigStore`, asserting every seeded value preserved
  and `instructions` unset.
- `tests/unit/test_tools_mcp.py`: guidance by the effective grant, with
  the entry down, with an allow list that offers nothing, with
  `mcp: []`, for an agent the slice does not know, and verbatim through
  the slice.
- `tests/unit/test_tools_mcp_reload.py`: an instructions-only edit
  keeps the same manager object and reports `unchanged` while the slice
  carries the new text; adding guidance to an entry that had none does
  the same; an edit beside the guidance still restarts the entry, so
  the exclusion is one field and not a general softening.
- `tests/unit/test_session_prompt.py` (new): the half assembled once per
  activation and not per reply, proven against a counting registry
  because an assembled half looks the same however many times it was
  built; a switch re-assembling; the guidance reaching the model; a
  fact remembered between two replies appearing in the second while the
  cached half does not carry it; the memory read running on another
  thread, proven by thread identity; the `prompt_assembled` event with
  memory explicitly absent from it, one record per activation and none
  per round.
- `tests/unit/test_config_api_runtime.py`: the route gated, 503 without
  a server, 404 with the restart sentence and without the name, the
  blocks and the total agreeing with the assembler, and the wiring
  through a `create_app` mount.
- `tests/unit/test_config_cli.py`: the rendering, a block far longer
  than `GLIMPSE_LENGTH` whose tail survives, newlines and tabs passing
  while control characters are replaced, the refusals for a body of the
  wrong shape with nothing of it printed.
- `tests/integration/test_agent_guidance.py` (new): the three end-to-end
  proofs, described under Discoveries below.

### Deviations from the plan

Three, all in the tests rather than in the behavior.

**The held session covers the reconnect nowhere.** The plan's
integration bullet holds one session across "a memory write, an MCP
reload, a server reconnect and an agent switch". This milestone's
session covers the memory write, two reloads and two switches, and not
the reconnect, because in milestone 1 a reconnect changes nothing a
prompt is made of: what a reconnect captures is the server-shipped
instructions and prompts, which milestone 3 adds. The assertion the
plan wants from it ("the reconnect's captures leave the running
session's cached half untouched") has nothing to be about yet, and the
same held-session test grows it in milestone 3.

**The switch is driven by a chain of handovers rather than by a script
that switches once.** The mock LLM decides to call a tool by matching a
substring of the last user turn, and the transcript of a lane
conversation is the same string every utterance, so an agent either
hands over on every utterance spoken to it or on none. A session's
activations are therefore a fixed chain. The test uses that rather than
fighting it: alpha hands over to beta, beta to gamma, and gamma answers
for itself, so gamma is the agent that replies twice, once when it is
switched in and once with the half it cached then. That is what makes
one session show both clocks.

**The CI wheel check learns a column and not a table.** The plan's
module layout says the check "learns the new table and columns";
milestone 1 adds no table, so it learned `mcp_servers.instructions`
only. Milestone 2's table joins it there.

No deviation from the plan's design decisions. The assembly order, the
grant-edge condition, the connection-identity exclusion, the activation
cache, the memory read moving off the loop, the removal of
`AgentProviders.prompt`, the surface and its CLI client, and the event's
exclusion of memory all landed as written.

### Discoveries

**The whole prompt is stripped at its two ends, and that is inherited
rather than chosen.** `with_memory` ended with `.strip()`, so an agent
with no prompt of its own was not sent a leading blank line. Keeping
byte-equality with no caveat means keeping that strip, and it applies
to the last block as well: an `instructions` value ending in a blank
line loses that blank line when it is the last block of the prompt.
Every other byte of it survives, including its indentation and its
inner blank lines, and the block's reported size is the size of what
was stored either way. Recorded rather than repaired: the pin is worth
more than a trailing newline nothing reads, and repairing it would have
made the byte-equality claim conditional.

**The strip is also why the persona is always a block.** The assembler
joins the blocks and strips only when something was appended to the
persona, which is what reproduces the old behavior exactly for a
whitespace-only prompt with no memory. Dropping an empty persona from
the block list instead would have been simpler and would have failed
that case.

**`tools/mcp.py` imports the block shape from `runtime/prompt.py`.**
The MCP layer knows which entries an agent may reach and what their
operator wrote about them, and nothing about headings or block order,
so `Guidance` comes from the assembler rather than being restated. No
cycle: the assembler imports `tools/names` for the separator, and
`tools/names` is a leaf by design.

**The memory store had to move up in `create_app`.** It was built after
the API, and the API's prompt read reports what a session would be
sent, memory included. Moving its construction above `build_api` is the
whole change; nothing else reads it earlier.

**An `async def _system_prompt` is a new await point inside the round.**
It resolves before the stream request is built, so a barge-in landing
there cancels the reply the way it cancels one landing anywhere else in
the loop. The existing session suites cover it, and three of them
needed the call awaited.

### Verification

Run from `samtal-server/` with `PYTHONDONTWRITEBYTECODE=1` exported for
everything that is not pytest.

```
uv run ruff check .
All checks passed!

uv run pytest tests/unit -q
1655 passed, 15 skipped in 149.02s

uv run pytest tests/integration -q
51 passed, 146.85s
```

The two doc drift checks pass inside the unit lane
(`test_the_committed_reference_matches_the_models` and the OpenAPI
pair in `test_api_openapi.py`), and both artifacts were regenerated
with `uv run samtal-server config reference` and `uv run samtal-server
config openapi` in the commits that moved them.

Not verified locally, and stated rather than claimed: the
installed-wheel migration check, which builds a wheel and migrates a
fresh database from it, runs in CI only.
