# Serve the stored-vs-running configuration diff: implementation

Companion to
[`2026-08-20-config-diff-read.md`](2026-08-20-config-diff-read.md). One
section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the whole comparison, unexposed

### What was done

Five commits. Nothing is exposed: no route, no response model in the
OpenAPI document, no behavior change, and the four committed reference
documents are byte-unchanged, which the drift checks under Verification
prove.

**The comparison.** `vinga-server/src/vinga_server/config/diff.py` is
the new module. It holds the `Applies` token enum (`restart`, `reload`,
`check-in`), the regime map `APPLIES` as data beside the comparison that
reads it, the typed result (`ConfigDiff` and its per-kind frozen
dataclasses), and `config_diff(running, stored, mcp)`, which is pure:
both sides arrive composed, with the secrets loaded beside them, so its
tests build two worlds from the support factories and no case opens a
database.

Providers are addressed as `stage.name` and compared by model equality
plus `SecretStore.fingerprint`; prompt fragments by model equality;
agents and `agent_defaults` by model equality with the `mcp` field
excluded, so a grants-only edit is never claimed pending-restart. The
two live-labeled kinds answer with their label and carry no comparison
at all, which is what `LiveKind` says by having one field.

**The MCP half.** `McpSlice`, which is the object `McpServers._install`
swaps, now carries one opaque comparison identity per configured entry,
referenced or not, computed by `McpSlice.of(config, secrets)` from the
connection identity the reload's `same_as` already uses, the prompt-only
fields that identity leaves out, and the entry's stored-secret
fingerprint, digested. `McpServers.pending_against(config, secrets)` is
the new public read: it composes the candidate into the slice a reload
would install and compares the two as configuration, answering entries
added, removed and changed plus the agents whose effective grants would
move. No manager is built, nothing connects, and a caller learns entry
names and agent names and nothing else.

Grants are compared for every agent of the running generation, through
`Config.mcp_for_agent` on both sides, so an agent the candidate no
longer knows compares as the empty grant set and its pending revocation
stays reported until a reload applies it; an agent only the candidate
knows is not in the grants answer, since it rides the agents' own added
row.

**The generation mark.** `McpServers.generation` is advanced by
`_install` and by nothing else, so a refused reload does not move it.
M2's route captures it either side of its worker-thread load to prove it
composed one world.

**Tests.** `tests/unit/test_config_diff.py` (15 cases) and
`tests/unit/test_mcp_pending.py` (12 cases), both at the public
interfaces only: no new test reaches for an underscore name. The
completeness pin holds both the regime map's keys and `ConfigDiff`'s own
fields equal to `DOMAIN_KEYS`.

### Deviations from the plan

Five, all recorded here because each moved something the plan named.

**1. The comparison identities and the entry-and-grant comparison live
in `tools/mcp/slice.py`, not in `registry.py`.** The plan's module
layout put "the per-entry comparison identities retained at install ...
and the pending-against-stored read" in `registry.py`, and lists
`slice.py` as touched only "as far as the install path needs to carry
the identities". Putting them on the slice turned out to be that path
and more: the slice IS what `_install` assigns, so "swapped atomically
with the world they describe" stops being a promise and becomes
structural; `McpSlice.of` is the one composition both the boot and a
reload already go through, so "one derivation for both sides" falls out
instead of being arranged; the grants comparison reuses
`McpSlice.grants_for`, whose documented empty answer for an unknown
agent is exactly the deleted-agent rule the plan's review finding 5
asked for; and nothing had to be threaded through `_Preparation` and
`_apply` to reach the install. The public read is still
`McpServers.pending_against`, exactly as the plan says, and it is the
only way in: callers learn nothing of connection identity, secret marks
or slice anatomy.

**2. `secrets.provider_identity` was added.** The plan says providers
are addressed as `stage.name`, "the identity the store and every refusal
already use", but that string had exactly one home and it was inside
`SecretLocation.provider`. Spelling it a second time in the diff would
have been the worst kind of wrong: a drifted second spelling asks the
store about an entity nothing ever wrote to, and the empty answer that
comes back looks exactly like nothing stored, so provider secret changes
would silently stop being reported. One function, two callers.

**3. The typed result is frozen dataclasses in `config/diff.py`.**
Explicitly permitted by the milestone brief, since M1 publishes no
schema: M2 maps or moves them into `config/responses.py` when the
schema publishes, complete and exactly once. `McpPending` is declared
there too and imported by the MCP package, which is the direction
`McpReloadResult` and `McpServerStatus` already travel.

**4. The two sides are a `Loaded` protocol rather than a concrete
pair.** `config.boot.BootConfig` is what both sides really are at a
running server, and importing that module would pull the database driver
and the migrations into a module whose whole job is comparing two
configurations, which is the line `config/__init__.py` already holds.
The protocol states the two reads and nothing else; `BootConfig`
satisfies it structurally and is what the tests pass.

**5. Effective grants are compared as the ordered tuple
`mcp_for_agent` answers with, not as a set.** The plan says "effective
set" throughout. Order is observable and reload-applied: the guidance
blocks an agent's activation is given are injected in grant order, so
comparing sets would hide a pending change, which is the false negative
the plan's review finding 2 rejected. The plan's own named case, moving
a grant between `agent_defaults` and the agent without changing what the
agent reaches, reports nothing either way.

### Discoveries

**An entry's comparison identity is the whole entry plus its stored
mark.** `_connection_identity` is the entry with `_PROMPT_ONLY_FIELDS`
cleared, so those two halves together are exactly `McpServerConfig`. The
derivation was kept as the plan's two named halves rather than collapsed
to a dump of the model, because the two names are what say why each half
matters, and because keeping the link means an entry field that becomes
prompt-only later moves in the reload's rule and in this one at the same
moment.

**Most of the MCP cases need no subprocess.** The entries the design
turns on are the ones no agent references, which build no manager at
all, and a reload whose entries are unchanged keeps every connection, so
it starts and stops nothing. Ten of the twelve MCP cases are therefore
pure configuration and the whole file runs in under half a second; the
one whose claim is about a connection standing stands the real stdio
server up.

**Finding 5's rule was already written down.** `McpSlice.grants_for`
answers nothing for an agent it does not know, and its docstring already
says why: a session is holding the agent it was built with, and a reload
can have applied a configuration that agent was deleted from. The
deleted-agent rule needed no new code, only to be read for what it
means on the other side of the comparison.

### No CHANGELOG entry

Deliberately none in this milestone. Nothing operator-visible changed:
there is no route, no schema, no acknowledgement text and no behavior
difference, so an entry here would announce a surface a deployment
cannot reach. It lands with M2's route.

### Verification

Run from `vinga-server/`, at the last commit of the milestone.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files. Its scope
  is the events package, which this milestone does not touch.
- `uv run pytest tests/unit -q`: 2,667 passed, 16 skipped. (Baseline
  before the milestone: 2,640 passed, 16 skipped, counted by collecting
  the suite with this milestone's two files ignored; the 27 new tests
  are those files.)
- `uv run pytest tests/integration -q`: 60 passed.
- The four documentation drift checks, regenerated and diffed against
  `../docs/reference/`: `config reference`, `conversations schema`,
  `events reference` and `config openapi` are all clean, and no file
  under `docs/reference/` appears in this milestone's commits, which is
  the "unexposed" claim's proof.

Not verified here, and not claimed: the container image and the smoke
lane, which no part of this milestone touches.
