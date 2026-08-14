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
  memory states; the per-block accounting; an empty memory read
  returning the cached half unchanged, by identity. The review round
  below added the equality that makes the accounting worth reading:
  the prompt is the blocks joined and nothing else.
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

*Superseded by review finding 1 below.* The paragraph above is half
right and the half it got wrong is the one that mattered: the strip was
applied to the joined text only, so the blocks kept bytes the model
never received and the surface reported them. The trim now applies to
the blocks as well.

**The strip is also why the persona is always a block.** The assembler
joins the blocks and strips only when something was appended to the
persona, which is what reproduces the old behavior exactly for a
whitespace-only prompt with no memory. Dropping an empty persona from
the block list instead would have been simpler and would have failed
that case.

*Superseded by review finding 1 below.* A blank persona is now dropped
when there is anything else to say, and the one-block prompt is what
carries the compatibility case instead.

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

### PR #130 review round

One external review of the milestone's diff: codex CLI, model
gpt-5.6-sol, read-only, 2026-08-14, with CI green on all three lanes.
Verdict: mergeable after the listed fixes. Findings as received,
condensed; each carries the commit that addressed it.

1. **P1: prompt inspection reports bytes the model never receives.**
   `runtime/prompt.py` strips the assembled prompt but retains
   unstripped `Block.text` and counts, so leading persona whitespace and
   trailing instruction blank lines appear in inspection and are absent
   from the model prompt, against the plan's byte-exact and
   no-disagreement requirements. The tests evade it by adding memory
   after guidance and by collapsing whitespace. Suggested: preserve
   boundary whitespace for the new guidance assemblies and keep the
   legacy stripping only for the no-guidance compatibility path, then
   assert model against inspection exactly, with leading persona
   whitespace and a final guidance block ending in blank lines.
   *Resolution*: adopted in 3f9be29, by the other of the two ways it can
   be closed. The trim now applies to the blocks as well, so the prompt
   is the blocks joined by blank lines and nothing else: a blank block
   contributes nothing and is not reported, the first block loses its
   leading whitespace and the last its trailing, and every byte inside a
   block is left as written. A prompt of one block, a persona standing
   alone, is still handed over untouched, which is what keeps the
   no-guidance byte-equality pin exact for every input including a
   whitespace-only prompt.
   The suggested split was weighed and not taken. It makes the same
   persona value produce different bytes depending on whether an
   unrelated entry happens to carry guidance, which is a rule an
   operator cannot hold in their head; and it leaves the legacy path's
   blocks disagreeing with its own text unless they are trimmed there
   too, which is the whole of this fix anyway. What the taken route
   costs is whitespace at the two outer ends of a guidance block when it
   sits first or last, and the block reports exactly what it sent, so
   nothing disagrees. The field description, the README and the response
   model say what is trimmed instead of promising bytes nothing sends.
   The tests are the ones the finding asked for: six awkward inputs
   (leading persona whitespace, guidance ending in blank lines, a blank
   persona, facts with a trailing newline) crossed with both halves,
   asserting the blocks joined equal the text and the total equals the
   sum plus the separators; a session-level test comparing the exact
   string the provider was handed against the blocks the surface would
   report; and the API and integration reads asserting the same equality
   on the answer itself, which is what the whitespace-collapsing spoken
   reply cannot check.
2. **P1: a malformed memory file leaks a library traceback through the
   new prompt-read path.** `tools/memory.py` catches `OSError` but not
   `UnicodeDecodeError`, and the threaded read now reaches the reply's
   own handler, which logs with `exc_info` and records the traceback.
   Suggested: contain decode and prompt-preparation failures with a
   fixed sanitized record and an empty-memory fallback, never `exc_info`
   or the exception message, with a corrupt-memory sentinel test over
   every log record.
   *Resolution*: adopted in b5f773e. Decode failures are caught beside
   the filesystem ones at the source, which is where the containment
   belongs (everything above it is pure string joining), and the record
   carries the class of the failure and nothing else, the rule the MCP
   layer's reason tokens already follow: a `UnicodeDecodeError` quotes
   the byte it tripped on and an `OSError` carries the path, and neither
   belongs in a record about a prompt. An unreadable file means the
   agent remembers nothing this round and the reply happens. Appending
   reads through the same containment, so the next remembered fact
   leaves a readable file behind, which loses nothing a model could have
   been given and keeps `remember` working rather than failing for as
   long as those bytes sit there. Four tests: the read, the sentinel
   over every record's message, args and attributes with `exc_info`
   asserted absent, the append, and a whole reply held over a corrupt
   file.
3. **P2: the generated prompt-route contract advertises the wrong,
   input-reflecting error shape.** The route omits an explicit 422, so
   the document carries `HTTPValidationError` with its `input` field
   while runtime validation is globally replaced by the sanitized
   `Problem`; and its 503 description says the reads in this namespace
   answer emptily, although this one returns 503. Suggested: document
   the sanitized 422 or remove the unreachable one, add a
   prompt-specific 503 description, regenerate and pin both.
   *Resolution*: adopted in 4448188. The 422 is declared like every
   other refusal, with a sentence saying it is the request that could
   not be read and that nothing sent is quoted back; `HTTPValidationError`
   is now referenced by no path in the document. The 503 gets a sentence
   of its own saying why this read refuses where the status read answers
   emptily: an empty block list would say a session opening now is sent
   nothing. The reload keeps the shared sentence, since that one is
   about actions, and the test pins all four schemas as `Problem` and
   both descriptions against the shared ones.
4. **P2: reload documentation still contradicts instructions-only reload
   semantics.** The API description claims live conversations pick the
   entire result up on their next utterance, and `unchanged` is defined
   as the entries nothing changed about, while an instructions-only edit
   returns `unchanged`, changes the entry's text, and reaches
   conversations at their next activation. Suggested: state that
   `unchanged` describes connection identity, distinguish per-reply tool
   and grant pickup from activation-cached guidance, regenerate and pin
   the wording.
   *Resolution*: adopted in b48d681. The description, the route's own
   prose and the `unchanged` field now say which half of an entry a
   conversation meets when: tools and grants on the next utterance,
   because they are snapshotted per reply, and guidance at the next
   activation, because prompt text is assembled there and cached, with
   the inspection surface named as what previews it meanwhile.
   `unchanged` says it is a statement about the connection and names the
   instructions case explicitly. The test pins the field's wording and
   the presence of both clocks in the two pieces of prose.

### Verification after the review round

Same commands, from `samtal-server/`, on the tree at b48d681.

```
uv run ruff check .
All checks passed!

uv run pytest tests/unit -q
1674 passed, 15 skipped in 150.10s

uv run pytest tests/integration -q
51 passed in 147.91s

uv run samtal-server config reference | diff against the committed copy
reference current

uv run samtal-server config openapi | diff against the committed copy
openapi current
```

Nineteen more unit tests than before the round, which is what was
added: twelve parametrized assembler equalities, one session-level
model-against-inspection comparison, four corrupt-memory tests and the
two contract pins. The installed-wheel migration check still runs in CI
only.

## Milestone 2: Shared prompt fragments

Know-how that belongs to no single agent now has a home of its own. A
`prompt_fragments` section holds named blocks of prompt text, a
`prompt_includes` list on an agent or on the agent defaults says which
of them that layer's prompt carries, and the assembler injects them
between the persona and the per-server guidance.

### What landed

**`samtal_server/config/models.py`.** `PromptFragmentConfig`, a mapping
with one `VerbatimStr` field, `text`: the entity travels the ordinary
path (a stored row, a read envelope, a written fragment), all three of
which want a mapping, and one field leaves room for a second later
without changing what a client writes. `check_prompt_fragment_names`
applies the safe charset an MCP entry name is held to, and deliberately
not that check's sentence: a name that fails the charset is exactly the
string that must not be echoed, so the refusal names the section and
the rule. The reserved tool names do not apply, since a fragment is in
no tool list. `prompt_includes` joins `AgentDefaults`, so both layers
carry it, with duplicates refused by position; `check_references`
resolves every include against the section and reports an unresolved
one by layer, position and rule, never by value, which is the one
reference refusal in that function that does not quote what it could
not resolve. `Config.fragments_for_agent` resolves the effective list
(own or inherited, a list replacing rather than extending) into the
blocks the assembler takes.

**`samtal_server/db/`.** A `prompt_fragments` table (name primary key,
text) and a nullable `prompt_includes` JSON column on `agents` and
`agent_defaults`, added by migration `0003_prompt_fragments`. Additive
throughout: an empty table and a NULL column are what "no fragments,
nothing included" already means.

**`samtal_server/config/store.py` and `views.py`.** Fragment rows read,
written and deleted, `prompt_includes` carried on both layer rows in
the form it was written in, and a delete that meets the reference pass
every other delete runs, so a fragment an agent still includes cannot
be taken away underneath it. A fragment read is `{"text": ...}`,
unmasked and byte for byte: it is prompt text for the model to read,
not a credential slot.

**`samtal_server/config/api.py`, `writes.py` and `cli.py`.**
`GET /prompt-fragments`, `GET/PUT/DELETE /prompt-fragments/{name}` in
the entity namespace, `wrote_prompt_fragment` and
`deleted_prompt_fragment` as the sentences both write paths answer
with, and `config set|show|delete prompt-fragment` with show and delete
inside the `--local` recovery subset. The acknowledgement carries
`RESTART_NOTICE` rather than the reload's: what a reload re-reads is
the MCP entries, their secrets and the grant lists, and a fragment is
prompt text an agent composes with at its next activation on a server
that read it at boot. The summary tree gains the section, giving each
fragment's size rather than its text.

**`samtal_server/config/loader.py`.** `prompt_fragments` joins
`MOVED_KEY_COMMANDS`, so a stale YAML section and a
`SAMTAL_PROMPT_FRAGMENTS` override are each refused naming the command
that writes it. A new test pins the table against `DOMAIN_KEYS`, since
a section without an entry there would meet a `KeyError` out of the
boot path rather than that sentence.

**`samtal_server/runtime/prompt.py`.** A `Fragment` block shape beside
`Guidance`, the `fragment:<name>` provenance, and a `fragments`
argument on `know_how` between the persona and the guidance. A fragment
is injected with nothing over it, which is the one way it differs from
a guidance block: it is prompt text the operator composed, and a
heading would editorialize, while guidance is about a set of tools and
has to say which.

**`samtal_server/runtime/pipeline.py` and `app.py`.** Both read
`Config.fragments_for_agent`, so the running session and the inspection
surface resolve the same list through the same method.

**Documentation.** `docs/reference/domain-config.md` and
`docs/reference/api-openapi.json` regenerated in the commits that moved
them. `examples/prompt-fragment.yaml` is new, both layer examples carry
`prompt_includes`, `examples/README.md` lists the file, and
`config.example.yaml` names the section and its command. The server
README's assembly section gains the fragment slot in the order, a
fragment block in the `config prompt` output and a paragraph on writing
one. `CHANGELOG.md`'s `## 2026-08-14` section gains the entry.

**Tests.** Everything the plan's Tests section assigns to this
milestone.

- `tests/unit/test_config_fragments.py` (new): the section parses and
  keeps its text verbatim; the mapping shape is required; a blank body
  and an unusable name are refused by the rule and never by the value;
  the include semantics (inherit, replace, `[]` opt-out, listed order)
  through `fragments_for_agent`; duplicates refused by position; an
  unresolved include refused at write-shaped validation and at boot,
  with the credential sentinel asserted absent from the whole exception
  chain.
- `tests/unit/test_config_store.py`, `test_config_reads.py`: the
  byte-exact round trip through the column, the load and the view; the
  exact read representation; both layers' lists write-shaped; a
  pre-upgrade row with no `prompt_includes` loading unchanged; the
  delete refused while an agent includes it; the sentinels on both
  write paths.
- `tests/unit/test_config_api_reads.py`, `test_config_api_writes.py`:
  the four routes over HTTP, the exact body a write carries and a read
  answers, the restart notice, and the two refusals with the sentinel
  absent from the response, its headers and everything this server
  logged.
- `tests/unit/test_config_cli.py`: the exact input a person types (a
  `text:` literal block with the indentation indicator), the rendering,
  the listing, the `--local` reads and deletes, and the refusals with
  nothing of the sentinel on either stream.
- `tests/unit/test_config.py`: the stale section and the stale
  environment override, and the moved-key table against the domain
  keys.
- `tests/unit/test_db_open.py`: the seeded 0001-to-head proof grown
  with the 0003 case, and the expected table and column sets.
- `tests/unit/test_runtime_prompt.py`, `test_session_prompt.py`,
  `test_config_api_runtime.py`: the order with fragments between the
  persona and the guidance, injection byte for byte, the fragment
  reaching the model, the opt-out, and the `fragment:<name>` provenance
  on both the inspection surface and the `prompt_assembled` event.
- `tests/integration/test_agent_guidance.py`: one fragment written
  once, spoken by two agents and not by the third, read back on the
  inspection surface over the same socket, rewritten through the API,
  invisible to the running server and carried by both agents after the
  restart.

### Deviations from the plan

**One name-in-a-path sentinel assertion is narrowed to this server's own
log records.** The plan asks for the invalid-fragment-name sentinel to
be absent from every log record. A fragment name is addressed in a URL
path, so the HTTP client that made the request holds it by construction
and writes it into its own request line; the test asserts the absence
over the records `samtal_server` emitted, and says why in its
docstring. Every other sentinel assertion is over everything.

**`Config.prompt_for_agent` did not grow the fragments; a second
resolver did.** The plan's module layout says `prompt_for_agent`
resolves the persona plus the effective include list. It stayed the
persona's one source, as the same plan says elsewhere, and
`fragments_for_agent` answers the resolved blocks beside it. The
assembler takes the pieces separately, so one method returning both
would have had to return a pair and both call sites would have unpacked
it; the two callers still read one method each and cannot disagree,
which is what the single-source rule was for.

No other deviation. The section's shape, the name rule and its refusal,
the include semantics and their refusals, the migration, the routes,
the CLI verbs, the moved-key command, the assembly slot, the provenance
and the examples all landed as written.

### Discoveries

**The configuration layer imports the block shape from the assembler.**
`Config.fragments_for_agent` answers `runtime.prompt.Fragment`, the way
`tools/mcp.py` answers `runtime.prompt.Guidance`. It reverses the usual
direction (runtime reads configuration), and the alternative was worse:
either a second shape in `models.py` that the assembler would have to
know about anyway, or an ordered mapping standing in for a list. No
cycle: `runtime/prompt.py` imports `tools/names`, which is a leaf by
design, and `runtime/__init__.py` is a docstring.

**`know_how`'s guidance argument moved behind the new one.** The
signature is now `know_how(persona, fragments, guidance)`, in the order
the blocks are assembled, so the existing positional calls in the
milestone 1 tests became keyword ones. Worth the small churn: a
signature whose order contradicts the assembly order is a comment
waiting to be wrong.

**A verbatim body needs YAML's indentation indicator to be written at
all.** A fragment whose first line is indented cannot be written as a
plain literal block, because YAML takes the first line's indentation as
the block's own and strips it. `text: |2` with everything indented two
further spaces is what preserves it, and the CLI test pins exactly that
input, since it is what an operator has to type.

**The integration lane needed a second start that seeds nothing.**
`serve_app_in` seeds the database from the test's `Config` before
serving, which would have overwritten the very write the restart is
supposed to pick up, and a config that leaves the fragment out cannot
be built at all (its agents' includes would not resolve). A
`restart_in` fixture serves the database as it stands, which is what a
restart reads.

**The README's block counts were illustrative rather than real.** The
`config prompt` example carried numbers that did not match the text
beside them or add up to its total. Repaired while the fragment block
was added to it, since a counting surface documented with wrong counts
is the one place a reader checks the arithmetic.

### Rebase onto the PR #130 review round

This milestone was written on the milestone 1 branch as it stood before
its review round, and rebased onto b678f3d afterwards. Finding 1's fix
changed the assembler's contract underneath it: the prompt is now
exactly the blocks joined by blank lines, a block that would hold
nothing contributes nothing and is reported nowhere, the first block
loses its leading whitespace and the last its trailing, and only a
prompt of one block is handed over untouched. The fragments landed
inside that contract rather than beside it, and five of the conflicts
were semantic rather than textual.

- `know_how` keeps the review round's docstring and gains one sentence
  of its own: a fragment is a block like every other, so what the ends
  trim it trims here too, and the absence of a heading is the only way
  it differs.
- `AssembledPrompt.blocks` is the review round's description with the
  fragment slot written into its order, rather than either version
  standing alone.
- `test_a_fragment_is_injected_byte_for_byte` now asserts the interior
  where a fragment is neither end of the prompt, because a fragment
  that is the last block loses its trailing newline exactly as any
  other last block does. Beside it, a new parametrized test puts
  fragments through the review round's own two equalities over awkward
  bodies (a trailing blank line, leading indentation, a whitespace-only
  neighbour), so the new block type is inside the contract rather than
  exempt from it.
- Milestone 1's positional `know_how(persona, guidance)` calls became
  keyword ones, since the second positional argument is now the
  fragments.
- The fragment's own descriptions, the field, the generated reference,
  the README, the example and the changelog entry, say what
  `instructions` now says: as written, its indentation and its own
  blank lines included, with the two ends of the whole assembled prompt
  the only bytes trimmed and the surface reporting what was sent.

### Verification

Run from `samtal-server/` with `PYTHONDONTWRITEBYTECODE=1` exported for
everything that is not pytest.

```
uv run ruff check .
All checks passed!

uv run pytest tests/unit -q
1747 passed, 15 skipped in 152.41s

uv run pytest tests/integration -q
52 passed in 157.06s
```

Run again after the rebase onto the review round, which is where these
numbers come from: twenty-three more unit tests than milestone 1 left,
which is what this milestone added, four of them the parametrized
fragment equalities the rebase called for.

The two doc drift checks pass inside the unit lane
(`test_the_committed_reference_matches_the_models` and the OpenAPI pair
in `test_api_openapi.py`), and both artifacts were regenerated with
`uv run samtal-server config reference` and `uv run samtal-server config
openapi` in the commits that moved them.

Not verified locally, and stated rather than claimed: the
installed-wheel migration check, which builds a wheel and migrates a
fresh database from it, runs in CI only. It learned the new table and
the two new columns in the same change as the migration.

### PR #131 review round

One external review of the milestone's diff: codex CLI, model
gpt-5.6-sol, read-only, 2026-08-14, with CI green on all three lanes.
Verdict: mergeable after the listed fixes. Findings as received,
condensed; each carries the commit that addressed it.

1. **P1: invalid fragment names leak when the body is also invalid.**
   `set_prompt_fragment` parses the body at the location
   `prompt_fragments.<name>` before checking the name's charset, so a
   `PUT /prompt-fragments/<credential>.pasted` with a body the models
   refuse answers with a sentence carrying the rejected name. The tests
   used only a valid body, which is why they missed the ordering.
   Suggested: validate the name with a name-only helper before parsing,
   and add invalid-name-plus-invalid-body sentinel tests across HTTP,
   the CLI streams, logs, headers and the exception chain.
   *Resolution*: adopted in efa668a. The charset check runs first,
   through `is_valid_fragment_name`, a predicate that needs nothing of
   the body, and the rule it refuses with is one sentence in one place
   (`PROMPT_FRAGMENT_NAME_RULE`), which is what let the check move in
   front of the parse at all. The store, HTTP and CLI cases are
   parametrized over the bodies a mistake produces (blank, mistyped,
   over-keyed, absent, not a mapping), with the sentinel looked for on
   both streams, in the response headers, in this server's log records
   and through the whole exception chain.

2. **P1: missing-fragment reads and deletes reflect the rejected
   identifier.** `read_prompt_fragment` and `delete_prompt_fragment`
   interpolate an unknown path value into `UnknownEntityError`, which
   reaches a 404 body and the CLI's stderr, and a test pinned that
   behavior rather than catching it. Suggested: a fixed sentence, plus
   credential-shaped missing-name tests for the remote and `--local`
   reads and deletes.
   *Resolution*: adopted in a3dd6b8. Both answer `NO_SUCH_FRAGMENT`,
   one fixed sentence naming the section and the fact: a name that
   addresses no fragment arrived in a URL path or on a command line and
   was validated by nothing here, and the operator can see what they
   typed without this server repeating it. The tests drive
   credential-shaped names through the read and the delete on both
   paths.

   The boundary is worth stating, because the same interpolation is how
   every other section answers a miss (`providers.<stage>.<name>: no
   such provider`, `mcp_servers.<name>`, `agents.<name>`,
   `devices.<mac>`, and the two secret-slot refusals). Those were not
   rewritten here: each has its own validation story, several of those
   names are checked before they are used in ways a fragment name is
   not, and a milestone that widened the change would be changing
   sentences its own tests are not about. The fragment case is the one
   this milestone introduced, and it is the one fixed. Whether the rest
   should follow is a question worth its own issue rather than a
   silent decision here.

3. **P2: the invalid-name exception-chain test does not test its stated
   claim.** The parametrized names were all checked against one
   unrelated long sentinel, so for a name like `household.facts`
   pydantic's `ValidationError` renders the rejected mapping, keys
   included, while the test passes. Suggested: assert each parametrized
   name is absent, use a short sentinel that truncation cannot hide,
   and make sure raw pydantic input cannot appear in the escaping
   exception.
   *Resolution*: adopted in b88ef4b. The test asks the rule itself
   rather than a model, and looks for each name it was given rather
   than for one it was not; a short sentinel joins the long one, since
   a long value can be lost to something truncating a representation
   instead of to anything keeping it out. Beside it, a new test pins
   the claim where it has to hold: the write, the boot composition and
   a row that reached the table some other way each answer with this
   repository's own refusal, built from the error's locations and
   messages and raised after the handler, so there is no cause and no
   context to walk back to, and `input_value` appears in none of them.
   No production change was needed for this one, which is the point of
   pinning it.

4. **P3: generated and operator documentation omit prompt fragments
   from existing exhaustive lists.** The envelope's `secrets` field
   names the kinds that hold no stored secret without naming fragments,
   and the changelog's provenance list omits `fragment:<name>`.
   *Resolution*: adopted in 7768304. Both lists gained the member, and
   the OpenAPI document was regenerated from the description rather
   than edited. A reader checks a list like that rather than reading
   past it, so a missing member reads as a statement.

### Verification after the review round

Same commands, from `samtal-server/`, on the tree at 7768304.

```
uv run ruff check .
All checks passed!

uv run pytest tests/unit -q
1782 passed, 15 skipped in 153.18s

uv run pytest tests/integration -q
52 passed in 156.42s

uv run samtal-server config reference | diff against the committed copy
reference current

uv run samtal-server config openapi | diff against the committed copy
openapi current
```

Thirty-five more unit tests than before the round, which is what the
parametrization added: the bodies an unusable name can arrive with on
three paths, the missing-name reads and deletes on two, and the two
name tests the round rewrote. The installed-wheel migration check still
runs in CI only.

## Milestone 3: Server-shipped guidance opt-ins

An MCP server can now tell the agents that use it how to use it, in its
own words, through both channels the specification gives it: the
`instructions` of its handshake, and the prompts it publishes. Neither
reaches a prompt until the entry says so, channel by channel, because
neither is the operator's own writing.

### What landed

**`samtal_server/config/models.py`.** Two fields on `McpServerConfig`.
`use_server_instructions` is a boolean defaulting to false;
`inject_prompts` is an optional list of non-blank names, refused when
one is listed twice, by its positions and never by its value. Both
descriptions carry the trust sentence, because the generated reference
is where an operator reads what a field means and the thing to say
about these two is whose words they let in.

**`samtal_server/db/`.** `schema.py` gains the two columns and
migration `0004_server_guidance_opt_ins` adds them. The boolean's column
is NOT NULL with a database-level default of false rather than a
nullable one rescued in Python: a row written before the migration then
reads its opt-in as closed from the database itself, which is what a
trust decision has to mean on an upgraded deployment. The prompt list is
nullable, where NULL is the "none" the model already means.

**`samtal_server/config/store.py` and `views.py`.** Both columns are
written and read. A read shows the flag in either state, like the
timeout beside it, since what a read answers about a trust decision is
the decision; the prompt names are shown only when the operator wrote
some, and this read body and the inspection response are the only two
places a server-chosen name appears at all, both JSON-encoded.

**`samtal_server/runtime/prompt.py`.** `ServerInstructions` and
`ServerPrompt` join `Guidance` as block shapes, under the union
`GuidanceBlock`, with the provenances `server_instructions:<entry>` and
`server_prompt:<entry>:<position>`. Separate shapes rather than a flag
on `Guidance`, because they carry separate provenance and separate
headings and a boolean would leave every reader to remember which way
round it went. `Block` gains an optional `name`, which only a
`server_prompt` carries. Each server-shipped block sits under a heading
of its own saying the server is the one talking (see Discoveries).

**`samtal_server/tools/mcp.py`.** `_connect` returns the initialization
result it used to discard, and `_run` captures its `instructions` on
every connect whatever the entry's opt-in says, which is what makes a
false-to-true reload work on a connection nobody restarted. The prompts
an entry names are discovered listing first: the full paginated
`prompts/list` is walked and every configured name is judged against it
before anything is fetched, so no skip decision rests on interpreting an
untrusted server's error. The rules are `NoPromptsCapability`,
`NotListed`, `RequiresArguments`, `NonTextContent`, `NothingToInject`,
`DiscoveryDeadline` and `PageCap`, each logged with the entry, the
positions in `inject_prompts` counted from one, and the rule. A block
past the 4000-character cap is skipped whole with a warning naming the
entry, the channel and the size. `_PROMPT_ONLY_FIELDS` gains
`use_server_instructions` and deliberately not `inject_prompts`.
`McpSlice.guidance_for` takes a `shipped` callable so that one loop
produces an entry's blocks in order, with the registry supplying what
its managers captured and a slice on its own answering the operator's
blocks alone.

**Bounds.** Discovery runs after the connect envelope has closed and the
tools are published, in the same task, so a raised exception cannot mark
the manager down and take a working tool list away. Each call gets
`PROMPT_CALL_TIMEOUT_S` (5 s), the whole phase gets
`PROMPT_DISCOVERY_TIMEOUT_S` (equal to `CONNECT_TIMEOUT_S`), and the
listing walk gets `PROMPT_PAGE_CAP` (20 pages) as the backstop against a
cursor that repeats itself. Which bound expired decides what happens:
a per-call expiry skips that prompt, the phase's expiry skips every
position that is left with one warning.

**`samtal_server/config/api.py` and `cli.py`.** `PromptBlock` gains
`name`, the provenance description learns the two new tokens, and the
`blocks` description learns the order inside one entry. The CLI prints
the name beside the provenance and the size, through the same full-block
sanitizer, and its shape check learns the field. The reload timeout's
comment in `cli.py` now says the server's envelope is one connect
timeout plus one discovery deadline.

**Documentation.** `docs/reference/domain-config.md` and
`docs/reference/api-openapi.json` regenerated.
`examples/mcp-server-streamable-http.yaml` carries both fields with
their trust comments. The README gains the trust paragraph and the
server block in the assembly order and in the `config prompt` example,
with the counts recomputed. `CHANGELOG.md`'s `## 2026-08-14` section
gains the entry.

**Tests.** Everything the plan's Tests section assigns to this
milestone.

- `tests/support/mcp_stdio_server.py`: the shared stdio server now
  declares `instructions` (overridable from its environment, see
  Discoveries) and publishes four prompts: one message, two messages,
  one that declares a required argument, and one that never answers.
- `tests/unit/test_tools_mcp_prompts.py` (new): the default entry
  ignoring both channels and fetching nothing; the opt-in injecting in
  the plan's order; the rendering pinned over the multi-message prompt;
  the required-argument and unlisted-name skips; both clearing paths;
  and, against a stub session, the pagination walk, the missing
  capability, a non-text prompt, an empty rendering, the cap on both
  channels, a stalled fetch, the phase deadline, a repeating cursor, a
  listing that fails, and a configured name holding a credential and a
  terminal escape asserted absent from every record. The envelope
  itself is measured twice: as arithmetic against the CLI's reload
  timeout, and as elapsed boot and reload against a real server whose
  prompt does not answer.
- `tests/unit/test_tools_mcp_reload.py`: the flag toggled in both
  directions on the same manager object, and an `inject_prompts` edit
  restarting the connection.
- `tests/unit/test_runtime_prompt.py`: the order inside one entry, the
  headings, the position-not-name rule, and the two new block kinds
  inside the trim contract's own parametrized equalities.
- `tests/unit/test_session_prompt.py`: the event carrying both new
  provenances and none of the bytes, and the shipped block reaching the
  model under its heading.
- `tests/unit/test_config_api_runtime.py`, `test_config_cli.py`: the two
  provenances on the surface with the configured name beside the
  `server_prompt` block, and the CLI sanitizing that name.
- `tests/unit/test_mcp_status_reflection.py`: the reflecting server now
  reflects its credential through both guidance channels and through a
  prompt name with a terminal escape in it, asserted absent from the
  status response, the `config status` output and every log record, with
  the entry opted out and opted in.
- `tests/unit/test_db_open.py`, `test_config_store.py`,
  `test_config_reads.py`, `test_config_tools.py`: the fields parse,
  round trip, read back write-shaped, and are unset on a seeded
  pre-upgrade row, with the boolean asserted false in the row itself.
- `tests/integration/test_agent_guidance.py`: two entries backed by the
  same server differing by the opt-in alone, and the held session grown
  with the reconnect.

### Deviations from the plan

**The document regeneration rode one commit late.** The plan's risk
section says regeneration rides in the same commit as the change that
moves it. The reference did; the OpenAPI document did not, because the
entry model is a request and response schema and that was noticed when
the suite ran rather than when the fields were written. It is its own
commit immediately after, with nothing else in it.

**The hostile shapes are proven against a stub session rather than
against a mock server.** The plan names the FastMCP-on-uvicorn fixture
as reusable, and the capture, the listing-first discovery, the
rendering, the required-argument rule and the containment envelope are
all proven against the real stdio server. A listing that never ends, a
cursor that repeats, a fetch that stalls and a message carrying an image
are not: a server cooperative enough to be scripted into those shapes
would be a second implementation of the SDK's server half, and the test
would be about that. They are proven against a stub session instead,
which is also where they belong, since each of them is a rule this
client applies rather than anything on the wire.

**The finer-grained bound tests run at scaled-down bounds.** The plan
asks for elapsed boot and reload measured against the stated envelope,
and one test does exactly that, against a real server publishing a
prompt that never answers, at the bounds that ship. The stub-session
tests that ask which bound expired scale both constants down, because
what they are about is the classification and not the number of
seconds, and four sleeping prompts at the shipped bounds would cost the
unit lane forty. The shipped arithmetic is pinned on its own beside
them, against the CLI's reload read timeout.

**The integration reconnect reaches for the manager.** A reconnect is
not something an operator asks for, so there is no request that causes
one; the test stops the entry's manager through
`app.state.mcp_servers._managers` and lets it come back, which is the
same private reach the unit suite's reload tests already make for
`manager_of`. Everything else about that test stays on the socket.

**The stdio example was left alone.** The plan says the opt-ins go in
the streamable_http example "and stdio if it reads naturally". It does
not: the stdio example is a proxy to a Home Assistant, and neither
channel has anything to say through an mcp-proxy bridge, so the two
fields would have been a paragraph about a thing that example is not
about.

No deviation from the plan's design decisions. The capture path, the
flag's exclusion from connection identity and `inject_prompts`'
inclusion in it, the listing-first discovery and its skip rules, the
rendering, the two bounds and the page cap, the injection order, the
provenances, the publishing rule for logs and the sentinels all landed
as written.

### Discoveries

**Where `_settled.set()` sits is the whole envelope, and it sits after
discovery.** `start()` waits for `_settled`, so the choice is between a
manager that settles once its tools are up and finishes fetching in the
background, and one that settles when its whole answer is in place. The
second is what the plan's arithmetic describes ("a manager start is now
bounded by one connect timeout plus one discovery deadline ... and the
reload's whole envelope grows by the same one deadline"), and it is the
better of the two anyway: a reload that answered before its prompts had
arrived would tell an operator the new configuration was applied while
the entry was still assembling what an activation reads, which is the
inert-configuration ambiguity the reload surface exists to remove. What
it costs is one discovery deadline on the boot and on a reload, and both
stay well inside the CLI's 60 s reload timeout, which is asserted rather
than asserted about.

**A server-shipped block needed a heading of its own.** The plan fixes
the heading for an entry's guidance and says nothing about the two new
kinds, and repeating the operator's heading three times would have been
the literal reading. It is the wrong one: the model is the only reader
of the prompt that cannot see the provenance every other surface
reports, and the point of an opt-in is knowing whose words are steering
the agent. So each server-shipped kind sits under a heading saying the
server is the one talking. They were shortened once written, in a commit
of their own: the first version spelled the prefix rule out again, which
is eighty characters of every prompt carrying one, and the operator's
heading above it has already said it.

**"Shipped nothing" and "shipped whitespace" are one answer.** A server
that sends an empty `instructions`, or a prompt that renders to nothing,
would otherwise contribute a heading with no words under it, which is
not guidance. Both are treated as nothing shipped; for a configured
prompt it is a visible skip (`NothingToInject`) because a position the
operator wrote deserves an answer, and for the handshake it is silent
because nobody asked for it.

**The block's name could not go in the provenance.** The plan puts the
configured name on the inspection response as data, and the reason
becomes concrete in the code: `Assembled.sizes()` is keyed by
provenance and goes straight into the `prompt_assembled` event, so a
provenance carrying the name would have put a server-chosen string into
the JSON log by the shortest possible path. `Block.name` is a field
beside it, null for every other kind, and the CLI sanitizes it like the
text.

**One loop, and the slice does not own half of it.** An entry
contributes the operator's block and then whatever its connection
captured, and only the registry knows the second half. Rather than
splitting the loop between `McpSlice.guidance_for` and
`McpServers.guidance_for_agent`, the slice takes a `shipped` callable
whose default answers nothing. A slice asked on its own still answers
what the configuration says, which is what the milestone 1 tests ask it,
and there is one place that decides the order.

**A reconnect can only capture something new if the far side changes.**
Proving that a reconnect's capture does not reach a running session
needs two different captures from one entry, and nothing about the entry
moves during a reconnect. What does is the environment its `env`
references resolve from, which `_resolve` reads per connection: the
shared stdio server reads its `instructions` from a variable, the held
session's entry passes that variable through as a `$VAR`, and moving the
value between two connections is enough. It is also the honest shape of
the risk this rule exists for, a server whose shipped guidance changes
under a running deployment.

### Verification

Run from `samtal-server/` with `PYTHONDONTWRITEBYTECODE=1` exported for
everything that is not pytest.

```
uv run ruff check .
All checks passed!

uv run pytest tests/unit -q
1798 passed, 15 skipped in 166.85s

uv run pytest tests/integration -q
53 passed in 152.14s
```

Fifty-one more unit tests than milestone 2 left and one more integration
test, which is what this milestone added. The unit lane is about twelve
seconds slower, almost all of it the one test that watches a real
server's prompt fail to answer inside the bounds that ship.

The two doc drift checks pass inside the unit lane
(`test_the_committed_reference_matches_the_models` and the OpenAPI pair
in `test_api_openapi.py`), and both artifacts were regenerated with
`uv run samtal-server config reference` and `uv run samtal-server config
openapi`, the reference in the commit that moved it and the document in
the commit immediately after.

Not verified locally, and stated rather than claimed: the
installed-wheel migration check, which builds a wheel and migrates a
fresh database from it, runs in CI only. It learned the two new columns
in the same change as the migration.
