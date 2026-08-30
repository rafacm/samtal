# Memory scopes and editing: conversation, agent, device

Plan for [#83](https://github.com/rafacm/vinga/issues/83), the
behavior half of the memory work whose storage foundation #314 laid.
Implementation notes land in the companion
`2026-08-30-memory-scopes-implementation.md`, one section per
milestone, appended in the change that ticks the milestone here.

## Goal

Memory grows from one append-only list per agent into three scopes
with editing. An agent keeps facts about the user (agent scope, what
exists today), facts about the place and the household (device scope,
shared by every agent bound to that device), and a keyed ledger of
what is currently true in this conversation (conversation state,
sharing its thread's lifecycle). The single `remember` tool becomes a
small family: add with a returned id, update and forget by id, restore
of the last thing forgotten, lookup over what is not injected, and
set/clear for conversation state. The injected block becomes a labeled
per-scope rendering that states its own precedence. An operator gets a
scope-addressed REST surface with the CLI as its client, and per-agent
memory control arrives in the domain configuration. Rename stays
#356's; the storage foundation and its chain stay #314's, extended
here by ordinary forward migrations.

## The issue's decisions, restated

Settled in the issue body (2026-08-30 interview) and not re-litigated:

- **Three scopes: `conversation`, `agent`, `device`.** The session
  tier is gone; what the issue once called session scope is rebased
  whole onto #190's first-class conversation threads, keyed by the
  thread's uuid hex. Live state survives disconnects because the rows
  are keyed by the thread, and re-attaches on explicit resumption.
  Consequence to document: on a deployment without conversation text
  storage every thread starts clean, so agents must promote durable
  outcomes to agent scope.
- **Precedence is `conversation > agent > device`, stated in the
  injected text itself.** Most current wins; the rendering labels each
  scope's block and names the order.
- **Identity: model-chosen keys with upsert semantics for
  conversation state; short numeric ids for agent and device facts,**
  returned on add and shown by lookup. Fuzzy matching on text is
  rejected.
- **Deletion is soft, spoken, and reversible, and nothing is called a
  tombstone.** The delete tool answers with the removed text and the
  agent is expected to speak it. Restore is "the last thing you
  forgot" first, with the id door also open. Permanent forgetting
  erases immediately. The held area purges itself when the
  conversation ends.
- **Device scope is steered and documented, not enforced.** Tool
  descriptions say what belongs to the device; the docs say what
  accumulates where; the operator API is the audit door. Scope keys
  are designed so a user dimension can arrive by ordinary migration.
- **Lookup lands only where growth is.** Conversation state and
  device facts inject whole under small per-scope caps; agent scope
  becomes the two-tier shape: a small injected core plus a lookup
  tool over the remainder.
- **Rename stays out.** Name keys and the documented orphaning
  continue; the transactional rename is #356, sequenced after this.
- **The storage foundation is #314's.** Everything here arrives as
  forward migrations on the `2001_agent_memory` chain, and the
  per-agent memory control is this issue's to deliver.
- **Conversation state shares its conversation's lifecycle.** Thread
  deletion and retention pruning both take the thread's keyed state
  with them, and this plan owes the cross-store ordering that keeps
  that true. Inspection goes through the same scope-addressed
  operator surface as the other scopes, with the thread's uuid hex as
  the owner segment; the injected block leans toward being called
  state, a ledger of what is currently true.
- **The scope rule of thumb** the tool descriptions are written from:
  if losing the thread should lose it, it is conversation state; if
  not, it belongs to the agent. A chess coach's board position and an
  RPG's scene and hit points are pure conversation state. A tutor
  splits: per-quiz tracking is conversation state, durable learner
  progress is promoted to agent memory. Promotion is load-bearing
  because retention prunes idle threads whole.
- **Operator half:** facts in the database (done, #314), memory over
  the REST API with the CLI as client in the #101 shape, and memory
  enablement as a config write rather than a YAML edit.
- **No migration** of anything pre-release beyond what the schema
  migration itself carries forward: the agent-scope rows #314's
  cutover accrued upgrade in place and become `scope = 'agent'` rows;
  nothing else exists to convert.

## Open questions, resolved

**The schema grows by one migration, `2002_memory_scopes`, on the
memory chain.** `facts` renames `agent` to `owner`, gains
`scope` (text, not null, `'agent'` for existing rows), and gains the
held-for-undo pair `forgotten_at` and `forgotten_in` (both nullable
text: when it was forgotten, and the thread it was forgotten in). The
index becomes `ix_facts_scope` on `(scope, owner, id)`, the one
access path there is (one owner's rows in insertion order, walked by
the ordered read, the prune, and the lookup filter). A new table
`state` carries `(conversation, key, value, updated_at)` with primary
key `(conversation, key)`: upsert-by-key is the whole of its
semantics, so the key is the identity and there is no id column. The
two kinds are two tables rather than one because they answer
different questions with different shapes: a fact list is ordered and
id-addressed with a held area; a ledger is keyed and current-only
with neither. `owner` stays plain text (an agent name, a MAC, a
thread hex, depending on `scope`), which is exactly the shape a
future `user` column extends by ordinary migration, per the roadmap
constraint. The floor consequence is nil: `2001_agent_memory` remains
the promised baseline and this is the forward migration the promise
says every schema change arrives as.

**One store call reads the whole prompt's memory.**
`MemoryStore.read(agent)` deepens into
`read_for_prompt(agent, device, conversation)`, one worker-thread
round trip on the read engine answering all three scopes as one
structured value (a frozen dataclass of three rendered blocks), so
the reply path keeps exactly one off-loop hop per round instead of
three. `read(agent)` itself survives unchanged through M2 and M3
callers' translation and retires only when nothing calls it; the
per-round freshness contract and the never-raise containment
translate to the new read whole (a scope that cannot be read renders
empty and emits `memory_unreadable`).

**The rendering keeps today's agent block byte-identical and adds
labeled siblings, precedence stated where the reader needs it.**
Blocks are appended to the know-how half in the order state, agent,
device. The agent block keeps `MEMORY_HEADING` and its exact
rendering, so a deployment whose facts are all agent-scope (every
deployment, the day M2 lands) sends byte-identical prompts and the
byte-equality pins hold. The state block's heading names the rule
from the top: "The current state of this conversation. When anything
below disagrees with this, this is current:". The device block's
heading names its rank from below: "Notes about this device and its
household. The conversation and the remembered facts above take
precedence:". Each block carries its own provenance token (`state`,
`memory`, `device`), because `Assembled.sizes()` is keyed by
provenance and three blocks under one token would collapse in the
event and API accounting. `with_memory(half, facts)` deepens into
`with_scopes(half, scopes)` while keeping the two invariants the
suite pins: identity on all-empty (the same `Assembled` object back),
and pure function of the text it is handed.

**Caps are per scope, enforced where each scope's pressure is.**
Agent scope: the storage cap grows to `MAX_LINES = 1000` and
`MAX_BYTES = 65536` (the same names, so the cap suites keep their
monkeypatch shape), pruned oldest-first at write exactly as today;
the injected core is the newest `CORE_LINES = 40` rendered lines
within `CORE_BYTES = 4096`, chosen newest-first because a fact worth
keeping tends to get said again, and the remainder is the lookup
tool's territory. Device scope: `DEVICE_LINES = 30` within
`DEVICE_BYTES = 2048`, injected whole, pruned oldest-first at write
(the same trade `remember` has always made, on a scope the issue
calls few). Conversation state: `STATE_KEYS = 50` and
`STATE_BYTES = 4096` of rendered ledger; a write that would leave
the ledger past either cap is refused with a fixed sentence naming
the cap, whether it is a new key or an overwrite grown too large,
because a ledger that silently drops keys is a ledger the model
cannot trust, and one that silently grows is not capped. All
constants stay module-level and read at call time.

One transactional invariant covers every mutation, and held rows
sit outside it. Held facts are excluded from all cap arithmetic and
are never pruned: they share their thread's lifecycle, and a prune
that took one would break the undo the softness exists for. Every
mutating transaction (add, update, restore, and the operator PUT)
leaves the scope's active rows within its caps by dropping oldest
active facts, so an update that grows a fact, or a restore into a
scope that refilled meanwhile, succeeds and re-prunes rather than
failing or silently exceeding; and any single fact or state value
whose rendered line alone exceeds its scope's byte cap is refused
with a fixed sentence, since pruning everything else could not make
it fit.

**The tool family is seven, named from the user's own verbs.**
`remember(text, scope)` (scope an enum of `agent` and `device`,
default `agent`), answering `Remembered [id]: text`; `update_memory
(id, text)`, same normalization, id stable, `at` refreshed;
`forget(id, permanently)`, answering `Forgot [id]: text` so the agent
can speak what it removed, soft by default, erasing immediately when
`permanently` is true; `restore_memory(id?)`, no id meaning the last
thing forgotten in this conversation; `recall(query)`,
case-insensitive substring match over all of the current agent's and
device's active facts, injected or not, answering matches newest
first with their ids, bounded by `RECALL_LINES = 20` within
`RECALL_BYTES = 2048` and ending with a fixed
more-matched-refine-the-query sentence when truncated (the injected
core shows no ids, so recall is also how the model finds the id of
any fact it needs to update or forget, core facts included); `set_state(key, value)`
and `clear_state(key)` for the conversation ledger. State needs no
undo: it is a current-truth ledger with overwrite semantics, not a
record of the user's words, and the soft-deletion decision is about
facts. Every id-addressed operation is bounded by ownership in its
WHERE clause, not by the model's good behavior: `update_memory`,
`forget` and `restore_memory` reach exactly the rows whose
`(scope, owner)` is the current agent's or the current device's, and
a restore by id additionally requires `forgotten_in` to be the
current conversation, matching the no-id path's meaning. A missing
id and an inaccessible one are indistinguishable on purpose,
answered by one fixed sentence, because a distinguishable refusal
would confirm that another owner's ids exist. All seven join
`BUILTIN_TOOL_NAMES`, which reserves them as
MCP entry names; the one-tuple hinge propagates ownership, turn
classification and the reservation rule, and the reservation growth
is a flagged compatibility note (a deployment with an MCP entry
already named `recall` refuses the boot with the existing rule
sentence naming the reserved set).

**Scope steering lives in the tool descriptions, written from the
issue's worked examples.** `remember`'s description says what
belongs to the device (the place, the household, hardware quirks)
and that personal facts belong with the persona; `set_state`'s says
the rule of thumb (if losing the thread should lose it, it is state)
and names promotion plainly: state dies with its conversation, so
anything that should outlive it must be remembered upward, which is
the RPG agent's save-the-game and the tutor's mastered-Iberia. The
descriptions are the enforcement the issue chose (steered and
documented, not enforced).

**Held facts share the lifecycle of the thread that forgot them.**
"The held area purges itself when the conversation ends" translates
under #190 to: a held fact lives until the thread named by its
`forgotten_in` is erased or pruned, at which point the coupling below
takes it, and permanent forgetting never enters the held area at all.
No session-close hook, because a session's close is not a thread's
end (the thread is resumable, and an undo after an explicit resume is
the reversibility decision working). The consequence is stated in the
docs: the undo window is the thread's lifetime, not the connection's.

**The cross-store deletion is one transaction under a fixed
ascending lock order, and thread-keyed memory writes join the
erasure-order protocol.** Thread erasure and retention pruning
delete the record rows and the thread's memory (state rows, and held
facts whose `forgotten_in` names them) in the same database
transaction: the schemas share one database, so atomicity is
available and taken, and there is never a moment when the thread is
gone while its state remains, nor counts that a later failure could
falsify. The memory deletes are issued by a purge callable the
memory store provides (it owns the SQL; the caller owns the
transaction), invoked by `threads.erase_conversations`'s and
`threads.prune`'s callers on the connection they already hold. A
transaction that writes both stores takes both chains' advisory
locks in ascending key order: the record chain's (key 2) from its
write engine's begin listener, then the memory chain's (key 3)
explicitly before the first memory statement. The ascending rule is
documented beside `advisory_key` and in the erasure-order comment,
and it is what keeps `db`'s no-deadlock statement true.
Resurrection by an in-flight writer is closed the way the
conversation writer already closes it: every thread-keyed memory
write (`set_state`, `clear_state`, a soft `forget`, a restore) takes
`erasure_order()` outside its own transaction and consults the
dead-thread set the `erased()` fan-out publishes, to which the
memory store now subscribes beside the conversation writers. A
write that began before the erasure completes before it, and its
rows are then deleted inside the erasure transaction; one that
begins after meets the dead set and refuses with a fixed sentence.
Retention publishes through the same fan-out, which is why
`threads.prune` and `Pruned` gain the taken thread ids. The boot
sweep remains, narrowed to healing what no transaction covers:
state and held rows whose thread has no row in
`record.conversations` and whose latest write is older than a
one-day grace period, which is pre-upgrade leftovers, threads that
never landed a first turn, and recording-off deployments, where no
thread rows land at all. The grace period exists because threads
materialize lazily at the first turn, so fresh state can briefly
precede its thread's row. The sweep's anti-join reads
`record.conversations` through the chain-agnostic read engine; the
memory store importing `conversations.schema` for the table is the
honest statement that the deletion promise crosses schemas.

**The runtime hands tools and prompt reads a session context, as a
type.** `BuiltinTools` gains a zero-argument callable answering a
small frozen `MemoryContext` (device MAC, current thread hex), closed
over the events object in `bespoke_runtime_factory`, which is where
both already live (`SessionEvents.device`, `.conversation`). The
pipeline gains a `_device` property beside `_agent` and
`_conversation`, and `_system_prompt` calls `read_for_prompt` with
all three. The `ToolSource` protocol does not widen: the context is a
constructor concern of the one source that needs it, not a question
every source must answer.

**The operator surface is a scope-addressed `/memory` namespace, in
the conversations-API shape.** Routes, registered from a new
`memory/api.py` with `routes(api, problems)` exactly as
`conversations/api.py` is:

- `GET /memory/agents`, `GET /memory/devices`,
  `GET /memory/conversations`: owners with row counts, the audit
  door (orphaned owners included, which is the point).
- `GET /memory/agents/{name}/facts` and
  `GET /memory/devices/{mac}/facts`: the facts with ids and
  timestamps, held ones marked, id-cursor pagination in the
  `LIMIT_DEFAULT`/`LIMIT_MAX` shape.
- `PUT /memory/agents/{name}/facts/{id}` (and the device twin):
  correct one fact's text in place.
- `DELETE .../facts/{id}` and `DELETE .../facts`: erase one fact or
  the owner's whole scope, hard deletes both, because the operator
  door is correction and audit, not the spoken-undo flow.
- `GET /memory/conversations/{conversation}/state`,
  `DELETE .../state/{key}`, `DELETE .../state`.

Reads answer empty shapes for an owner with no rows (the #283
contract: a store with no rows is not a 404); the addressed deletes
404 on a missing fact or key through `UnknownEntityError` with fixed
sentences that never quote the id back. Reads use per-request
`db.read_engine`, writes per-request `db.write_engine` on
`MEMORY_CHAIN`, both in the conversations API's dependency shape.

**The CLI noun is `memory`, singular, core verbs only, address in
the URL's own order.** `vinga memory list agent poet`,
`vinga memory set agent poet 7 "the user is not vegetarian"`,
`vinga memory delete agent poet 7` (destroys, so it prompts and
takes `--force`), `vinga memory delete device aa:bb:... --all` for
the whole-scope erase (a flag rather than an absent id, so a
mistyped id can never mean everything), `vinga memory list
conversation <hex>` and `vinga memory delete conversation <hex>
<key>` for state. Three address segments (scope, owner, id) is the
guide's ceiling and stays under it; `set` is the core verb for
correction (no `correct` synonym); owner listings are
`vinga memory list agent` with no owner, the same words one level
up. Every route the CLI does not cover goes into
`test_api_contract.py`'s `EXCLUDED` with a reason, and the goal is
none.

**Per-agent control is a nested `memory` section on the agent, in
the `filler` shape.** `MemoryPolicy` with `enabled: bool = True`,
declared on `AgentDefaults` and `AgentConfig`, inheriting like every
nested section (naming it replaces it wholly, so
`memory: {enabled: false}` opts an agent out). Disabled means the
whole thing for that agent: no memory or state tools offered, no
scope blocks injected, so a switched-off agent cannot read the
device facts its siblings accrue either; the OFF state is honest
rather than partial. No domain migration (the agent row is a JSON
body), but the `mcp` field description that currently asserts
"remember is offered to every agent" moves in the same change, and
the README's grant-model paragraph, which names #83 as the decider,
is rewritten to describe the control that now exists. Body-parse
fixtures gain the new field's sparse and written forms.

**The two memory events gain a scope, and lifecycle cleanup gets
one agent-free variant.** `memory_unreadable` and
`memory_unwritable` keep their names, channel and level, and their
`agent` field keeps meaning the acting agent; each gains a `scope`
field from the closed scope set (the decision site is the store
call that failed, which knows it). The deletions with no acting
agent (the boot sweep, the session-close purge) cannot borrow those
variants honestly, so a third arrives on the same channel:
`memory_cleanup_failed`, WARNING, carrying only the failure's class
name, its decision site the one `except` arm in the sweep-and-purge
path. Erasure and retention need no failure event of their own: the
atomic transaction means their memory counts are as truthful as
their record counts, and `ThreadErasure` and `Erasure` both gain
the `state` and `held_facts` counts from the same transaction that
produced the others. Operator writes through the API are
refusals-or-acknowledgements in the #101 shape and emit nothing
new, like the conversations API's erasures. The catalog change
regenerates the events reference, adds the README index rows, and
extends the driver set (the two extended drivers, one new one).

**The preview stays agent-keyed and says so.** `GET
/runtime/agents/{name}/prompt` renders the know-how half plus the
agent core exactly as a fresh session with no device and no thread
would see it; its route description states that the device and
conversation blocks are per-session and absent here. A preview that
invented a device to show its facts would be a second prompt
assembler pretending to be the first.

**No generated reference for the memory schema, still.** The #314
resolution holds: nobody but the server reads the raw tables, and
the operator surface this plan builds lands in `api-openapi.json`
and `cli.md` through their own generators, which are the documents
that earn it. The observability question memory now raises is
answered on the surfaces page instead: memory becomes a named
content-bearing surface with its retention stated (facts until
corrected, capped by scope; state and held facts until their thread
ends; the operator API as the deletion door).

## Design footprint

- `memory/store.py` deepens in place: the same store, more
  sentences (`read_for_prompt`, scoped `add` returning the id,
  `update`, `forget`, `restore`, `recall`, `set_state`,
  `clear_state`, `purge_threads`, `sweep`), same two engines, same
  containment, same close discipline. Callers still never see SQL,
  scopes' table shapes, or the held-area mechanics.
- `memory/api.py` is a new module in the conversations-API shape and
  passes the deletion test: inlined into `config/api.py` it would
  hand the whole admin surface the memory schema's vocabulary; the
  registration inversion (`routes(api, problems)`) already exists as
  a seam and carries a second namespace at no cost.
- `runtime/prompt.py` deepens: `with_scopes` subsumes `with_memory`,
  and the per-scope headings live beside `MEMORY_HEADING` where the
  assembly owns them.
- `tools/builtin.py` grows the tool family beside the four builtins
  it has; `BuiltinTools` gains the `MemoryContext` callable and stays
  the one `ToolSource` that runs memory; the protocol is untouched.
- `conversations/threads.py` and `store.py` deepen by carrying ids
  they already compute (`Pruned.threads`) and invoking a purge
  callable inside the transactions they already own; the memory
  store subscribes to the `erased()` fan-out that already exists.
- `config/models.py` gains `MemoryPolicy`, one more nested shape in
  the `filler` mold; `entities.NESTED` lists it.

## Documentation footprint

Named by role, homes confirmed against the authority taxonomy:

- **M2/M3** (agent-visible behavior): `docs/concepts.md`'s Memory
  section rewritten whole: the scope names become conversation,
  agent, device (the page still says session); the two decided
  directions it marks stay marked (the user key, the shared
  profile); the Device section's "context, never memory" paragraph
  is revised to say the device scope holds the place's and
  household's facts while everything personal stays with the
  persona, and "replacing hardware loses nothing" becomes "loses
  only the device's own notes". `docs/glossary.md` gains the Memory
  entry the scope vocabulary now demands. `docs/system-overview.md`
  step 7's "its memory of the conversation" is reworded now that
  the phrase names a real thing. `vinga-server/README.md`: the
  builtins list (both spellings), the memory paragraph and the
  block-order prose, the preview sample output. Generated pages
  through generators only (`events.md`, `api-openapi.json` route
  descriptions). `CHANGELOG.md` per milestone.
- **M4** (operator surface): `vinga-server/README.md`'s `vinga_ro`
  sentence, which reserves exactly this design space, is cashed in;
  `docs/architecture/observability-surfaces.md` gains memory as a
  named surface with its retention answer; `docs/README.md`'s
  reference bullet set stays true (no new generated page, stated).
  `cli.md` and `api-openapi.json` through their generators.
- **M5** (control): `docs/concepts.md`'s "per-agent control arrives
  with the scopes" paragraph becomes current; the README grant-model
  paragraph; `docs/reference/domain-config.md` through `docgen`; the
  agent entity note in `config/entities.py` (rename orphaning now
  spans scopes and the control field).
- Milestones M1 stales no hand-maintained page beyond the changelog
  and says so; the command-spellings census is re-run after every
  README edit per standing rule.

## Tests

- **Pins that hold unchanged, run green before and after**: the
  prompt byte-equality family (`test_runtime_prompt.py`'s
  `previously` transcription, the order test, identity-on-empty);
  the two-clock family (`test_session_prompt.py`: half cached per
  activation, memory read off-loop by thread identity, fact between
  replies); the store's close/admission and sanitized-failure
  families; the schema suite's identity and never-reissued-id pins,
  which the id addressing now leans on; the conversations erasure
  and retention suites' existing assertions.
- **Pins that translate, named one by one in the implementation
  doc**: the offered-tools ordered literals in `test_session_tools`
  and the integration `DUE_BUILTINS` set grow the family; the
  `Remembered:` confirmation gains its id; the cap suites keep their
  monkeypatch shape against the same constant names with the new
  values; `test_memory_is_keyed_by_agent_not_by_device` becomes the
  scope-separation pin (an agent fact is not a device fact and
  neither leaks into the other's block); the reserved-entry-name
  parametrization grows with the tuple.
- **New, named for the acceptance criteria**:
  - *Store*: add returns an id that update and forget address;
    forget hides from every read and recall, restore by id and by
    last-forgotten-in-this-thread brings back the exact original
    bytes; permanent forget survives no restore; state upserts by
    key and refuses a new key past the key cap, an overwrite past
    the byte cap, and a single oversized value, each with its fixed
    sentence; the cap invariant across mutations (an oversized add
    and an oversized update refused; an update that grows a fact
    re-prunes the oldest active; a restore after the released
    capacity refilled succeeds and re-prunes; held rows present at
    both caps are untouched by every prune and still restorable);
    per-scope caps prune independently (an agent at cap does not
    touch device rows and conversely, proven by exact survivor
    sets); the core/remainder boundary from both sides (the oldest
    fact beyond the core is absent from the injected block and
    found by recall; a fact inside the core is also found by
    recall, with its id); recall's bound and its fixed continuation
    sentence at an exact boundary; `read_for_prompt` is one
    connection (asserted by an engine that counts checkouts).
  - *Coupling*: erasing a thread through the API takes its state
    and its held facts and answers the new counts; retention prune
    takes the same; the boot sweep takes an orphan older than grace
    and leaves a younger one; a live `remember` during an erasure
    serializes correctly (the two-writer arrangement from #314,
    reused); every transaction that takes both chains' locks takes
    them in ascending key order (asserted by a test walking the
    erasure and retention paths); and a thread-keyed memory write
    forced to straddle an erasure is either deleted with the thread
    or refused by the dead set, both interleavings forced with the
    #314 gate technique rather than reasoned about.
  - *Tools and rendering*: each of the seven tools offered,
    executed, refusing bad arguments in the ValueError shape;
    forget's result carries the removed text; a fact forgotten in
    round n is out of round n+1's system prompt within the same
    reply (the working-copy clock); the full three-block rendering
    with the precedence sentences, and the agent-only rendering
    byte-identical to today's; the ownership negatives (another
    agent's fact id, another device's, a restore against a fact
    forgotten in another conversation), each answered by the one
    fixed refusal and each proven to change nothing in the store.
  - *Operator surface*: the conversations-API property set applied
    to `/memory`: nothing a caller sends quoted back (sentinel hunt
    through bodies, both log formats, process output), cursors
    exact, empty shapes for no rows, 404 fixed sentences, the
    whole-scope delete requiring its flag on the CLI, destroys
    prompting and `--force`; `test_api_contract.py` covering every
    route through a CLI act.
  - *Control*: an agent with `memory: {enabled: false}` is offered
    no memory tools and gets no scope blocks while its sibling on
    the same device keeps both; the body fixtures parse; the
    generated references regenerate to the committed bytes.
  - *No-leak sentinels*: a credential-shaped fact and a
    credential-shaped state value driven through every failure path
    and asserted absent from events (equality against declared
    constants, not substring), both log formats, API problem bodies
    and exception chains.
- The event drivers extend for the scope field; the migration suite
  pins `2002_memory_scopes` as head, the renamed column, the new
  table, and the metadata-drift comparison; the CI wheel step's
  memory block updates its head literal and inventory.

## Risks and mitigations

- **Seven more tool names in front of small local models.** The
  descriptions are short and disjoint, state tools name their scope
  in the name, and the per-agent control (M5) plus MCP-style
  granting later are the doors if field use shows confusion; the
  count is accepted knowingly as the issue's own operations list.
- **Reserved-name growth can refuse an existing boot.** An MCP entry
  named `recall` (or any of the seven) meets the existing
  reservation refusal after upgrade. Flagged in the changelog under
  Changed with the rename-the-entry fix; the refusal sentence
  already names the reserved set.
- **The injected prompt changes shape for every deployment the
  moment state or device facts exist.** The agent-only rendering is
  byte-pinned unchanged, so nothing changes until an agent actually
  uses the new scopes, which is the gentlest cutover available.
- **Cross-chain coupling invites deadlocks.** The rule is one
  ascending lock order for any transaction that writes both stores,
  written beside `advisory_key` and asserted by test; erasure and
  retention are atomic so no crash window exists on those paths, and
  the sweep heals only what no transaction covers.
- **State can precede its thread's row** (threads land at first
  turn). The sweep's grace period covers it; the erasure path
  cannot race it because erasure addresses threads that have rows.
- **Milestone releasability.** M1 ships a migrated schema plus
  dormant store depth (the #314 M1 shape); M2 and M3 each ship one
  agent-visible behavior change alone; M4 is additive surface; M5
  is one config field. Every merge leaves `main` releasable.
- **The `remember` confirmation changes wording** (gains the id).
  A translated pin, named in the implementation doc, not a silent
  drift.

## Milestones

- [ ] **M1: the scoped schema and the store's new sentences.**
  Migration `2002_memory_scopes` (rename, scope, held pair, the
  `state` table, the reshaped index), autogen-produced; the store's
  scoped operations (`add`, `update`, `forget`, `restore`,
  `recall`, `set_state`, `clear_state`, `read_for_prompt`,
  `purge_threads`, `sweep`) implemented and tested at store level
  behind the unchanged `read` and `remember`; per-scope cap
  constants; the wheel step and lane fixtures updated; changelog.
  No caller changes; behavior is #314's exactly. Design footprint:
  the store deepens in place, callers still see no database
  vocabulary. Documentation footprint: none beyond the changelog,
  stated.
- [ ] **M2: conversation state end to end.** The state tools, the
  state block and the three-block rendering (device block present
  and empty), `MemoryContext` plumbing, `read_for_prompt` on the
  reply path, the lifecycle coupling (erasure purge inside the
  window, `Pruned.threads`, the purge callable, the boot sweep),
  `ThreadErasure`'s new counts and the CLI erasure row, the events'
  scope field, concepts and README updates this milestone
  falsifies, changelog. Design footprint: the coupling is ids
  handed where they are computed plus one callable seam; no module
  learns another's SQL. One behavior change alone in review: agents
  gain a ledger.
- [ ] **M3: scoped facts and the editing tools.** `remember` with
  scope and id, `update_memory`, `forget`/`restore_memory`,
  `recall`, the agent core/remainder split, the device block live,
  the steering descriptions, the offered-list and reserved-name
  translations, concepts' Device reconciliation and the glossary
  entry, changelog. Design footprint: `tools/builtin.py` grows
  beside its four; the two-tier shape the old docstring predicted
  arrives.
- [ ] **M4: the operator surface.** `memory/api.py` routes, the
  responses shapes, the `memory` CLI noun with its acts and
  commands, `api-openapi.json` and `cli.md` regenerated,
  `test_api_contract.py` coverage, the surfaces-page memory row and
  the README's `vinga_ro` sentence cashed in, changelog.
- [ ] **M5: per-agent memory control.** `MemoryPolicy` on the agent
  model, tool and injection gating, the preview honoring it, the
  `mcp` description and grant-paragraph rewrites, `domain-config.md`
  regenerated, body fixtures, concepts' control paragraph brought
  current, changelog.

## Plan review round

External review of commit 24863b14: backend codex (codex-cli
0.151.0), model gpt-5.6-sol, sandbox read-only, 2026-08-30, runtime
11m33s. Verdict as received: ready after the P1/P2 amendments.
Findings condensed but faithful; each is amended below with its
resolution.

1. **P1: sequential cross-chain deletion can resurrect erased
   memory.** The purge runs after the record transaction commits,
   but memory writes participate in neither `erasure_order()` nor
   the `erased()` fan-out, so an in-flight `set_state` or soft
   `forget` can commit after the one-shot purge and recreate rows
   for the erased thread; the planned "live `remember`" test cannot
   catch it because agent and device remembering is not
   thread-lifecycle data. Require an ordering protocol covering
   every thread-keyed memory write across erasure and retention,
   with tests forcing writes on both sides.

   *Resolution*: adopted, atomically rather than by outbox: erasure
   and retention delete both stores in one transaction under a fixed
   ascending lock order, thread-keyed memory writes take
   `erasure_order()` and consult the dead set the `erased()` fan-out
   publishes (the memory store subscribes beside the conversation
   writers), and the straddling-write interleavings are forced with
   the #314 gate technique in the coupling test family.

2. **P1: a cleanup failure after the record commit has no truthful
   outcome.** With the record deletion committed and the memory
   purge still pending, a retry finds the conversation missing while
   its state remains, the erasure response has already answered
   counts, and the event vocabulary cannot report the failure
   honestly because both memory events require an acting agent that
   retention and the sweep do not have. Prefer atomic deletion;
   otherwise define partial-completion semantics, durable retry, and
   an agent-free lifecycle event, with truthful counts on both
   `Erasure` and `ThreadErasure`.

   *Resolution*: dissolved for erasure and retention by finding 1's
   atomicity (there is no cleanup after the commit; the counts ride
   the deleting transaction, and both `Erasure` and `ThreadErasure`
   gain `state` and `held_facts` counts), and adopted for the paths
   that remain: the boot sweep and the session-close purge get the
   agent-free `memory_cleanup_failed` variant, WARNING, class name
   only, with its own driver.

3. **P1: numeric fact operations lack an ownership boundary.** Ids
   are global and guessable, and the plan never states that update,
   forget, permanent erase, or restore constrain the row to the
   current agent or device, so an agent could mutate another agent's
   or device's fact. Specify the predicates for every id-addressed
   operation, one fixed refusal for missing and inaccessible alike,
   and cross-agent, cross-device, cross-conversation negative tests.

   *Resolution*: adopted whole: the predicates are stated as WHERE
   clauses on the current agent, current device, and (for restore by
   id) the current conversation; missing and inaccessible share one
   fixed sentence so a refusal confirms nothing; the three negative
   families are named in the tools tests, each proven to change
   nothing in the store.

4. **P1: recall excludes the facts whose ids editing needs.** The
   injected core shows no ids and recall searches only what is not
   injected, so on a later turn the model cannot obtain the id of a
   core fact it needs to correct; the fact-41 boundary test would
   preserve the defect. Recall must search all active facts
   reachable by the current agent and device, with a result bound,
   deterministic ordering, and a fixed refine-the-query
   continuation.

   *Resolution*: adopted whole: recall searches every active fact of
   the current agent and device, injected or not, newest first,
   bounded by `RECALL_LINES`/`RECALL_BYTES` with the fixed
   continuation sentence, and the boundary test is rewritten to
   prove both sides (a beyond-core fact reachable, a core fact's id
   reachable).

5. **P1: the cap rules do not preserve either bounds or undo.** The
   plan does not say whether held rows are counted or prunable
   (pruning them breaks undo; excluding them without a rule lets
   restore overflow), updates can enlarge a scope past its cap, and
   always-allowed state overwrites can violate `STATE_BYTES`.
   Define one transactional invariant across add, update, soft
   forget, restore, operator PUT, and state upsert, with oversized
   items refused by a fixed sentence and the named boundary tests.

   *Resolution*: adopted whole: held rows are outside all cap
   arithmetic and never pruned; every mutating transaction restores
   the active-row bound by dropping oldest actives, so grown updates
   and restores into refilled scopes re-prune rather than overflow
   or fail; a single item over its scope's byte cap is refused, and
   state refuses any write past either of its caps, overwrites
   included; the reviewer's four boundary tests are named in the
   store family.

6. **P2: recording-off deployments are cleaned only when they
   reboot.** With recording off no thread rows land and no
   retention runs, so unreachable state and held facts accumulate
   until a restart; a boot-only sweep does not bound a long-running
   process. Purge the connection's thread ids at session close
   where threads cannot be resumed, with a test that proves rows
   disappear without a restart.

7. **P2: the proposed construction and shutdown order is currently
   impossible.** The conversation store is constructed and started
   before memory is opened, and its exit callback is registered
   first, so the exit stack closes memory before the writer drains
   and a teardown retention purge would call a closed store. M2
   must name the composition reorder and the startup-failure and
   shutdown-drain tests.

8. **P1: the operator grammar places content and potential secrets
   in argv and URLs.** Corrected fact text as a CLI positional
   reaches shell history and process listings, and model-chosen
   state keys in URL paths reach proxy and access logs; the CLI
   guide prohibits exactly this for credentials and the
   configuration boundary treats caller-chosen keys as possible
   credential locations. Move fact text to file or stdin input,
   carry state keys in request bodies, and extend the no-leak
   tests to argv and access-log request targets.

9. **P2: the schema omits the constraints and indexes its lifecycle
   requires.** `scope` is unconstrained text, nothing ties
   `forgotten_at` and `forgotten_in` together, and restore,
   erasure, retention and sweep all query `forgotten_in` with no
   index, scanning all facts under the writer lock. Add the check
   constraints, a partial lifecycle index, and one declared scope
   vocabulary the store, events and tools derive from.

10. **P2: owner-list routes are unbounded.** The three owner
    listings return every owner with counts and no pagination
    contract, while conversation owners grow at thread-creation
    pace and the conversations API bounds every page. Paginate all
    owner collections with keyset cursors under the existing limit
    discipline.

11. **P2: memory enablement has no atomic reload clock.** The tool
    snapshot is per reply while injection is per round, so a reload
    mid-reply can let one reply observe half of a policy change.
    Resolve one effective policy per reply and carry it to both
    decisions, with a reload interleaving test.

12. **P2: the upgrade test does not prove the promised 2001-to-2002
    migration.** The named migration tests cover head, columns and
    drift on fresh databases only, so lost rows in the rename would
    pass; and the rename breaks any older process still serving
    during a rolling upgrade. Seed a database at `2001_agent_memory`
    with exact rows, migrate, and assert preserved bytes, scope,
    null held fields and identity continuation; state the upgrade as
    stop-then-start or design expand-and-contract.

13. **P2: the `server.local_only` question remains incorrectly
    answered.** The footprint never states memory egress: injected
    and recalled content follows the active LLM provider, and
    device scope sends household-wide facts to every sibling
    agent's provider. Document that storage is local, egress
    follows the provider, and `local_only` is the existing guard.

14. **P2: the central resume and pre-first-turn behavior has no
    end-to-end test.** Nothing drives state written before the
    first turn commits through disconnect and explicit resume, the
    exact case the grace period exists for. Name the end-to-end
    resume test, the fresh-activation-is-clean case, the text-off
    case, and same-reply visibility of state mutations without a
    know-how rebuild.
